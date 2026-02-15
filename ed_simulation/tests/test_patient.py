"""
Unit tests for the Patient class.

Tests cover:
- Patient creation and initialization
- State transitions through nodes
- LWBS probability calculation
- Priority ordering
"""

import pytest
import simpy

from ed_simulation.core.patient import Patient, PatientConfig
from ed_simulation.core.enums import Acuity, NodeType, PatientStatus, DispositionType


class TestPatientCreation:
    """Test patient initialization."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_patient_creation_basic(self):
        """Patient is created with correct initial state."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=10.0, acuity=Acuity.ESI_3)

        assert patient.arrival_time == 10.0
        assert patient.acuity == Acuity.ESI_3
        assert patient.status == PatientStatus.ARRIVING
        assert patient.is_lwbs is False
        assert patient.disposition is None
        assert patient.current_node is None

    def test_patient_priority_reflects_acuity(self):
        """Lower acuity number = higher priority (lower value)."""
        env = simpy.Environment()
        p1 = Patient(env, arrival_time=0, acuity=Acuity.ESI_1)
        p5 = Patient(env, arrival_time=0, acuity=Acuity.ESI_5)

        assert p1.priority < p5.priority
        assert p1.priority == 1
        assert p5.priority == 5

    def test_patient_comparison_for_sorting(self):
        """Patients can be sorted by priority (acuity then arrival)."""
        env = simpy.Environment()
        p1 = Patient(env, arrival_time=5, acuity=Acuity.ESI_1)
        p3_early = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        p3_late = Patient(env, arrival_time=10, acuity=Acuity.ESI_3)
        p5 = Patient(env, arrival_time=0, acuity=Acuity.ESI_5)

        # ESI 1 < ESI 3 (regardless of arrival)
        assert p1 < p3_early
        assert p1 < p3_late

        # Same acuity: earlier arrival < later arrival
        assert p3_early < p3_late

        # ESI 3 < ESI 5
        assert p3_early < p5

    def test_patient_unique_ids(self):
        """Each patient gets a unique ID."""
        env = simpy.Environment()
        patients = [
            Patient(env, arrival_time=i, acuity=Acuity.ESI_3)
            for i in range(100)
        ]
        ids = [p.id for p in patients]

        assert len(set(ids)) == 100

    def test_patient_custom_id(self):
        """Patient can be created with custom ID."""
        env = simpy.Environment()
        patient = Patient(
            env,
            arrival_time=0,
            acuity=Acuity.ESI_3,
            patient_id="CUSTOM-001"
        )

        assert patient.id == "CUSTOM-001"


class TestPatientStateTransitions:
    """Test patient state changes through nodes."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_enter_queue_updates_status(self):
        """Entering queue changes status to WAITING."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)

        patient.enter_queue(NodeType.TRIAGE)

        assert patient.status == PatientStatus.WAITING
        assert patient.current_node == NodeType.TRIAGE

    def test_cannot_enter_queue_twice(self):
        """Entering queue when already waiting raises error."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        patient.enter_queue(NodeType.TRIAGE)

        with pytest.raises(ValueError, match="already waiting"):
            patient.enter_queue(NodeType.REGISTRATION)

    def test_start_service_updates_status(self):
        """Starting service changes status to IN_SERVICE."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)

        patient.enter_queue(NodeType.TRIAGE)
        patient.start_service(NodeType.TRIAGE)

        assert patient.status == PatientStatus.IN_SERVICE
        assert patient.current_node == NodeType.TRIAGE

    def test_start_service_wrong_node_raises(self):
        """Starting service at wrong node raises error."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        patient.enter_queue(NodeType.TRIAGE)

        with pytest.raises(ValueError, match="not NodeType.REGISTRATION"):
            patient.start_service(NodeType.REGISTRATION)

    def test_complete_service_records_timestamp(self):
        """Completing service records timestamp."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)

        patient.enter_queue(NodeType.TRIAGE)
        env._now = 5  # Simulate wait time
        patient.start_service(NodeType.TRIAGE)
        env._now = 10  # Simulate service time
        patient.complete_service(NodeType.TRIAGE)

        assert NodeType.TRIAGE in patient.timestamps
        ts = patient.timestamps[NodeType.TRIAGE]
        assert ts.wait_time == 5
        assert ts.service_time == 5

    def test_complete_journey_multiple_nodes(self):
        """Patient can complete journey through multiple nodes."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)

        # Triage
        patient.enter_queue(NodeType.TRIAGE)
        env._now = 2
        patient.start_service(NodeType.TRIAGE)
        env._now = 5
        patient.complete_service(NodeType.TRIAGE)

        # Registration
        patient.enter_queue(NodeType.REGISTRATION)
        env._now = 7
        patient.start_service(NodeType.REGISTRATION)
        env._now = 12
        patient.complete_service(NodeType.REGISTRATION)

        assert len(patient.timestamps) == 2
        assert patient.total_wait_time == 4  # 2 + 2
        assert patient.total_service_time == 8  # 3 + 5

    def test_discharge_sets_status(self):
        """Discharge sets correct status and disposition."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        env._now = 100

        patient.discharge()

        assert patient.status == PatientStatus.DISCHARGED
        assert patient.disposition == DispositionType.DISCHARGE_HOME
        assert patient.length_of_stay == 100

    def test_set_lwbs(self):
        """LWBS sets correct status and disposition."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        patient.enter_queue(NodeType.TRIAGE)
        env._now = 60

        patient.set_lwbs()

        assert patient.status == PatientStatus.LWBS
        assert patient.disposition == DispositionType.LWBS
        assert patient.is_lwbs is True
        assert patient.length_of_stay == 60


class TestPatientLWBS:
    """Test LWBS probability calculation."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_esi1_never_leaves(self):
        """ESI 1 patients have zero LWBS probability."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_1)
        patient.enter_queue(NodeType.TRIAGE)
        env._now = 120  # Long wait

        assert patient.calculate_lwbs_probability() == 0.0

    def test_base_probability_at_short_wait(self):
        """LWBS probability is base rate for short waits."""
        env = simpy.Environment()
        config = PatientConfig(
            lwbs_base_probability=0.02,
            lwbs_time_threshold_minutes=30.0,
        )
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_4, config=config)
        patient.enter_queue(NodeType.TRIAGE)
        env._now = 10  # Short wait

        prob = patient.calculate_lwbs_probability()
        # ESI 4 has multiplier of 1.0, so should equal base
        assert prob == pytest.approx(0.02, rel=0.1)

    def test_lwbs_probability_increases_with_wait(self):
        """LWBS probability increases over time after threshold."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_4)
        patient.enter_queue(NodeType.TRIAGE)

        env._now = 10
        prob_10min = patient.calculate_lwbs_probability()

        env._now = 60
        prob_60min = patient.calculate_lwbs_probability()

        env._now = 120
        prob_120min = patient.calculate_lwbs_probability()

        assert prob_60min > prob_10min
        assert prob_120min > prob_60min

    def test_lower_acuity_higher_lwbs(self):
        """Lower acuity (ESI 5) has higher LWBS probability."""
        env = simpy.Environment()

        p3 = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)
        p5 = Patient(env, arrival_time=0, acuity=Acuity.ESI_5)

        p3.enter_queue(NodeType.TRIAGE)
        p5.enter_queue(NodeType.TRIAGE)
        env._now = 60

        assert p5.calculate_lwbs_probability() > p3.calculate_lwbs_probability()

    def test_evaluate_lwbs_records_evaluation(self):
        """evaluate_lwbs records the decision for analysis."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_4)
        patient.enter_queue(NodeType.TRIAGE)
        env._now = 60

        # Evaluate with random value that won't trigger LWBS
        result = patient.evaluate_lwbs(0.99)

        assert result is False
        assert len(patient._lwbs_evaluations) == 1
        assert patient._lwbs_evaluations[0]["random_value"] == 0.99

    def test_evaluate_lwbs_with_low_random_triggers(self):
        """evaluate_lwbs with very low random value can trigger LWBS."""
        env = simpy.Environment()
        config = PatientConfig(
            lwbs_base_probability=0.10,  # High base probability
        )
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_5, config=config)
        patient.enter_queue(NodeType.TRIAGE)
        env._now = 60

        # Very low random value should trigger if probability > 0
        result = patient.evaluate_lwbs(0.001)

        assert result is True


class TestPatientSerialization:
    """Test patient serialization."""

    def setup_method(self):
        """Reset patient counter before each test."""
        Patient.reset_counter()

    def test_to_dict_basic(self):
        """to_dict returns all expected fields."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=10.0, acuity=Acuity.ESI_3)

        data = patient.to_dict()

        assert data["id"] == patient.id
        assert data["arrival_time"] == 10.0
        assert data["acuity"] == 3
        assert data["acuity_name"] == "ESI_3"
        assert data["status"] == "ARRIVING"
        assert data["is_lwbs"] is False

    def test_to_dict_after_journey(self):
        """to_dict includes timestamps after journey."""
        env = simpy.Environment()
        patient = Patient(env, arrival_time=0, acuity=Acuity.ESI_3)

        patient.enter_queue(NodeType.TRIAGE)
        env._now = 5
        patient.start_service(NodeType.TRIAGE)
        env._now = 10
        patient.complete_service(NodeType.TRIAGE)
        patient.discharge()

        data = patient.to_dict()

        assert "TRIAGE" in data["timestamps"]
        assert data["timestamps"]["TRIAGE"]["wait_time"] == 5
        assert data["disposition"] == "DISCHARGE_HOME"
        assert data["length_of_stay"] == 10
