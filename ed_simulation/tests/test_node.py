"""
Unit tests for the Node class.

Tests cover:
- Node creation and configuration
- Patient processing
- Priority queuing
- Statistics collection
- Service time generation
"""

import pytest
import simpy
import random

from ed_simulation.core.node import (
    Node,
    NodeConfig,
    ServiceTimeConfig,
    ServiceTimeGenerator,
    NodeStatistics,
)
from ed_simulation.core.patient import Patient
from ed_simulation.core.enums import Acuity, NodeType


class TestNodeCreation:
    """Test node initialization."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_node_creation_basic(self):
        """Node is created with correct configuration."""
        env = simpy.Environment()
        config = NodeConfig(node_type=NodeType.TRIAGE, capacity=2)
        node = Node(env, config)

        assert node.node_type == NodeType.TRIAGE
        assert node.capacity == 2
        assert node.queue_length == 0
        assert node.utilization == 0.0

    def test_node_from_defaults(self):
        """Node can be created from default configuration."""
        config = NodeConfig.from_defaults(NodeType.TRIAGE)

        assert config.node_type == NodeType.TRIAGE
        assert config.capacity == 2  # Default triage capacity
        assert config.service_time_config is not None

    def test_node_display_name(self):
        """Node has correct display name."""
        env = simpy.Environment()

        # Default name from node type
        config1 = NodeConfig(node_type=NodeType.BED_ASSIGNMENT)
        node1 = Node(env, config1)
        assert node1.name == "Bed Assignment"

        # Custom name
        config2 = NodeConfig(
            node_type=NodeType.TRIAGE,
            display_name="Fast Track Triage"
        )
        node2 = Node(env, config2)
        assert node2.name == "Fast Track Triage"


class TestServiceTimeGenerator:
    """Test service time generation."""

    def test_constant_distribution(self):
        """Constant distribution returns fixed value."""
        config = ServiceTimeConfig(
            distribution="constant",
            default_params={"mean": 10.0}
        )
        generator = ServiceTimeGenerator(config, rng=random.Random(42))

        times = [generator.generate(Acuity.ESI_3) for _ in range(10)]

        assert all(t == 10.0 for t in times)

    def test_uniform_distribution(self):
        """Uniform distribution returns values in range."""
        config = ServiceTimeConfig(
            distribution="uniform",
            default_params={"min": 5.0, "max": 15.0}
        )
        generator = ServiceTimeGenerator(config, rng=random.Random(42))

        times = [generator.generate(Acuity.ESI_3) for _ in range(100)]

        assert all(5.0 <= t <= 15.0 for t in times)

    def test_lognormal_distribution(self):
        """Lognormal distribution returns positive values."""
        config = ServiceTimeConfig(
            distribution="lognormal",
            default_params={"mean": 15.0, "std_dev": 5.0}
        )
        generator = ServiceTimeGenerator(config, rng=random.Random(42))

        times = [generator.generate(Acuity.ESI_3) for _ in range(100)]

        assert all(t > 0 for t in times)
        # Mean should be approximately correct
        avg = sum(times) / len(times)
        assert 10 < avg < 25  # Rough range

    def test_acuity_specific_params(self):
        """Generator uses acuity-specific parameters."""
        config = ServiceTimeConfig(
            distribution="constant",
            params_by_acuity={
                Acuity.ESI_1: {"mean": 5.0},
                Acuity.ESI_5: {"mean": 20.0},
            },
            default_params={"mean": 10.0}
        )
        generator = ServiceTimeGenerator(config, rng=random.Random(42))

        assert generator.generate(Acuity.ESI_1) == 5.0
        assert generator.generate(Acuity.ESI_5) == 20.0
        assert generator.generate(Acuity.ESI_3) == 10.0  # Uses default

    def test_service_time_clamping(self):
        """Service times are clamped to valid range."""
        config = ServiceTimeConfig(
            distribution="constant",
            default_params={"mean": -5.0}  # Invalid
        )
        generator = ServiceTimeGenerator(config)

        time = generator.generate(Acuity.ESI_3)

        assert time >= ServiceTimeGenerator.MIN_SERVICE_TIME


class TestNodeProcessing:
    """Test patient processing through nodes."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_single_patient_processed(self):
        """Single patient is processed successfully."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=1,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 5.0}
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)

        def process():
            result = yield from node.process_patient(patient)
            assert result is True

        env.process(process())
        env.run()

        assert node.statistics.patients_completed == 1
        assert node.statistics.patients_entered == 1

    def test_priority_ordering(self):
        """Higher acuity patients are processed first."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=1,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 10.0}
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        # Create patients with different acuities
        p5 = Patient(env, arrival_time=0.0, acuity=Acuity.ESI_5)
        p3 = Patient(env, arrival_time=0.0, acuity=Acuity.ESI_3)
        p1 = Patient(env, arrival_time=0.0, acuity=Acuity.ESI_1)

        completed_order = []
        
        # Block the node's resource initially to ensure all requests are queued
        blocker_patient = Patient(env, arrival_time=-1.0, acuity=Acuity.ESI_3)
        
        def blocker_process():
            # Hold the resource to ensure all other requests are queued
            # This allows PriorityResource to properly order by priority
            yield from node.process_patient(blocker_patient)
        
        def process(patient):
            result = yield from node.process_patient(patient)
            completed_order.append(patient.acuity)

        # Start blocker first to occupy the resource
        env.process(blocker_process())
        # Wait for blocker to get the resource (it will hold it for 10 minutes)
        # But we'll start the other processes immediately - they'll queue
        
        # Start all other processes - they should all queue behind the blocker
        env.process(process(p5))
        env.process(process(p3))
        env.process(process(p1))
        
        # Run simulation
        env.run()

        # Blocker completes first (after 10 minutes), then others by priority
        # The three test patients should be processed in priority order:
        # ESI 1 should be first (highest priority, priority value 1)
        # ESI 3 should be second (priority value 3)
        # ESI 5 should be last (priority value 5)
        assert len(completed_order) == 3, f"Expected 3 test patient completions, got {len(completed_order)}"
        assert completed_order[0] == Acuity.ESI_1, f"Expected ESI_1 first, got {completed_order}"
        assert completed_order[1] == Acuity.ESI_3, f"Expected ESI_3 second, got {completed_order}"
        assert completed_order[2] == Acuity.ESI_5, f"Expected ESI_5 third, got {completed_order}"

    def test_capacity_limits_concurrent_service(self):
        """Only capacity patients can be in service simultaneously."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=2,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 100.0}  # Long service time
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        # Start 5 patients
        for i in range(5):
            patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
            env.process(node.process_patient(patient))

        # Run for a short time (not enough to complete any)
        env.run(until=10)

        # Only 2 should be in service (capacity)
        assert len(node._patients_in_service) == 2
        # Remaining 3 should be waiting
        assert node.queue_length == 3


class TestNodeStatistics:
    """Test statistics collection."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_statistics_accumulate(self):
        """Statistics are collected over multiple patients."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=2,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 5.0}
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        def run_patients():
            for i in range(10):
                patient = Patient(env, arrival_time=env.now, acuity=Acuity.ESI_3)
                yield from node.process_patient(patient)

        env.process(run_patients())
        env.run()

        stats = node.statistics
        assert stats.patients_completed == 10
        assert len(stats.wait_times) == 10
        assert len(stats.service_times) == 10
        assert stats.average_service_time == pytest.approx(5.0, rel=0.01)

    def test_statistics_reset(self):
        """Statistics can be reset."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=1,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 5.0}
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        # Process one patient
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        env.process(node.process_patient(patient))
        env.run()

        assert node.statistics.patients_completed == 1

        # Reset
        node.reset_statistics()

        assert node.statistics.patients_completed == 0
        assert len(node.statistics.wait_times) == 0

    def test_queue_length_tracking(self):
        """Max queue length is tracked."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=1,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 50.0}  # Long service
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        # Start 5 patients at once
        for i in range(5):
            patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
            env.process(node.process_patient(patient))

        env.run(until=10)  # Let them queue up

        # Max queue should be 5 (all 5 patients were in queue at some point)
        # At time 0, all 5 enter the queue, then one gets into service
        assert node.statistics.max_queue_length == 5


class TestNodeCallbacks:
    """Test event callbacks."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_on_enter_callback(self):
        """on_patient_enter callback is called."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=1,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 5.0}
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        entered_patients = []
        node.on_patient_enter(lambda p: entered_patients.append(p))

        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        env.process(node.process_patient(patient))
        env.run()

        assert len(entered_patients) == 1
        assert entered_patients[0] == patient

    def test_on_complete_callback(self):
        """on_patient_complete callback is called."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=1,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 5.0}
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        completed_patients = []
        node.on_patient_complete(lambda p: completed_patients.append(p))

        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        env.process(node.process_patient(patient))
        env.run()

        assert len(completed_patients) == 1
        assert completed_patients[0] == patient


class TestNodeSnapshot:
    """Test node state snapshots."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_get_snapshot(self):
        """get_snapshot returns current state."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=2,
        )
        node = Node(env, config, rng=random.Random(42))

        snapshot = node.get_snapshot()

        assert snapshot["node_type"] == "TRIAGE"
        assert snapshot["capacity"] == 2
        assert snapshot["queue_length"] == 0
        assert snapshot["utilization"] == 0.0
        assert "waiting_by_acuity" in snapshot

    def test_waiting_by_acuity(self):
        """get_waiting_patients_by_acuity groups correctly."""
        env = simpy.Environment()
        config = NodeConfig(
            node_type=NodeType.TRIAGE,
            capacity=1,
            service_time_config=ServiceTimeConfig(
                distribution="constant",
                default_params={"mean": 100.0}
            ),
            enable_lwbs_monitoring=False,
        )
        node = Node(env, config, rng=random.Random(42))

        # Add patients with different acuities
        for acuity in [Acuity.ESI_3, Acuity.ESI_3, Acuity.ESI_5]:
            patient = Patient(env, arrival_time=0, acuity=acuity)
            env.process(node.process_patient(patient))

        env.run(until=5)

        by_acuity = node.get_waiting_patients_by_acuity()

        # 1 in service, 2 waiting
        assert len(by_acuity[Acuity.ESI_3]) == 1  # One ESI_3 waiting
        assert len(by_acuity[Acuity.ESI_5]) == 1  # One ESI_5 waiting
