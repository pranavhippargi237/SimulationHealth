"""
Node class representing an ED processing station.

Each Node wraps a SimPy PriorityResource and handles:
- Priority queuing by patient acuity
- Service time generation from configurable distributions
- Capacity management
- Statistics collection
- LWBS monitoring

Design Principles:
- Configurable capacity and service times
- Priority-based queuing (acuity-aware)
- Modular service time distributions
- Comprehensive metrics collection
"""

from __future__ import annotations
import math
import random
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Generator, Any, Tuple

import simpy
from simpy import Environment, PriorityResource

from .enums import NodeType, Acuity
from .patient import Patient
from ..config.defaults import SERVICE_TIME_DEFAULTS, NODE_CAPACITY_DEFAULTS, LWBS_DEFAULTS


# =============================================================================
# Service Time Configuration
# =============================================================================

@dataclass
class ServiceTimeConfig:
    """
    Configuration for service time distributions.

    Supports different distributions:
    - exponential: mean only
    - lognormal: mean and std_dev
    - triangular: min, mode (mean), max
    - uniform: min, max
    - constant: fixed value (mean)
    """
    distribution: str = "lognormal"

    # Parameters by acuity level (in minutes)
    # Keys are Acuity enum values, values are distribution parameters
    params_by_acuity: Dict[Acuity, Dict[str, float]] = field(default_factory=dict)

    # Default parameters if acuity-specific not provided
    default_params: Dict[str, float] = field(default_factory=lambda: {
        "mean": 15.0,
        "std_dev": 5.0,
        "min": 5.0,
        "max": 30.0,
    })


class ServiceTimeGenerator:
    """
    Generates service times from configured distributions.

    Supports acuity-specific service times with fallback to defaults.
    """

    # Minimum and maximum service times to avoid edge cases
    MIN_SERVICE_TIME = 0.1   # minutes
    MAX_SERVICE_TIME = 480.0  # 8 hours

    def __init__(
        self,
        config: ServiceTimeConfig,
        rng: Optional[random.Random] = None
    ):
        """
        Initialize the service time generator.

        Args:
            config: Distribution configuration
            rng: Random number generator (uses new instance if None)
        """
        self._config = config
        self._rng = rng or random.Random()

    def generate(self, acuity: Acuity) -> float:
        """
        Generate a service time for given acuity level.

        Args:
            acuity: Patient acuity level

        Returns:
            Service time in simulation time units (minutes)
        """
        # Get acuity-specific params or defaults
        params = self._config.params_by_acuity.get(
            acuity,
            self._config.default_params
        )

        # Generate based on distribution type
        dist = self._config.distribution.lower()

        if dist == "exponential":
            service_time = self._exponential(params)
        elif dist == "lognormal":
            service_time = self._lognormal(params)
        elif dist == "triangular":
            service_time = self._triangular(params)
        elif dist == "uniform":
            service_time = self._uniform(params)
        elif dist == "constant":
            service_time = self._constant(params)
        else:
            # Default to lognormal
            service_time = self._lognormal(params)

        # Clamp to valid range
        return max(
            self.MIN_SERVICE_TIME,
            min(self.MAX_SERVICE_TIME, service_time)
        )

    def _exponential(self, params: Dict[str, float]) -> float:
        """Exponential distribution with given mean."""
        mean = params.get("mean", 15.0)
        if mean <= 0:
            return self.MIN_SERVICE_TIME
        return self._rng.expovariate(1.0 / mean)

    def _lognormal(self, params: Dict[str, float]) -> float:
        """Lognormal distribution with given mean and std_dev."""
        mean = params.get("mean", 15.0)
        std_dev = params.get("std_dev", 5.0)

        if mean <= 0:
            return self.MIN_SERVICE_TIME
        if std_dev <= 0:
            return mean

        # Convert to lognormal parameters
        # mu and sigma are parameters of underlying normal distribution
        variance = std_dev ** 2
        mu = math.log(mean ** 2 / math.sqrt(variance + mean ** 2))
        sigma = math.sqrt(math.log(1 + variance / mean ** 2))

        return self._rng.lognormvariate(mu, sigma)

    def _triangular(self, params: Dict[str, float]) -> float:
        """Triangular distribution with min, mode (mean), and max."""
        low = params.get("min", 5.0)
        high = params.get("max", 30.0)
        mode = params.get("mean", (low + high) / 2)
        return self._rng.triangular(low, high, mode)

    def _uniform(self, params: Dict[str, float]) -> float:
        """Uniform distribution between min and max."""
        return self._rng.uniform(
            params.get("min", 5.0),
            params.get("max", 30.0)
        )

    def _constant(self, params: Dict[str, float]) -> float:
        """Constant (deterministic) service time."""
        return params.get("mean", 15.0)


# =============================================================================
# Node Statistics
# =============================================================================

@dataclass
class NodeStatistics:
    """
    Real-time statistics for a node.

    Tracks queue lengths, utilization, wait times, etc.
    """
    patients_entered: int = 0
    patients_completed: int = 0
    patients_lwbs: int = 0

    total_wait_time: float = 0.0
    total_service_time: float = 0.0

    max_queue_length: int = 0
    queue_length_samples: List[Tuple[float, int]] = field(default_factory=list)

    wait_times: List[float] = field(default_factory=list)
    service_times: List[float] = field(default_factory=list)

    @property
    def average_wait_time(self) -> float:
        """Average wait time across all completed patients."""
        if not self.wait_times:
            return 0.0
        return sum(self.wait_times) / len(self.wait_times)

    @property
    def average_service_time(self) -> float:
        """Average service time across all completed patients."""
        if not self.service_times:
            return 0.0
        return sum(self.service_times) / len(self.service_times)

    @property
    def lwbs_rate(self) -> float:
        """Proportion of patients who left without being seen."""
        total = self.patients_completed + self.patients_lwbs
        if total == 0:
            return 0.0
        return self.patients_lwbs / total

    def reset(self) -> None:
        """Reset all statistics. Useful for warmup period."""
        self.patients_entered = 0
        self.patients_completed = 0
        self.patients_lwbs = 0
        self.total_wait_time = 0.0
        self.total_service_time = 0.0
        self.max_queue_length = 0
        self.queue_length_samples.clear()
        self.wait_times.clear()
        self.service_times.clear()


# =============================================================================
# Node Configuration
# =============================================================================

@dataclass
class NodeConfig:
    """
    Configuration for a Node instance.

    Allows external configuration of all node parameters.
    """
    node_type: NodeType
    capacity: int = 1
    service_time_config: Optional[ServiceTimeConfig] = None

    # Priority settings
    use_priority_queue: bool = True  # If False, uses FIFO

    # LWBS monitoring
    enable_lwbs_monitoring: bool = True
    lwbs_check_interval: float = LWBS_DEFAULTS["check_interval"]

    # Display name for UI
    display_name: Optional[str] = None

    @property
    def name(self) -> str:
        """Get display name or derive from node type."""
        return self.display_name or self.node_type.name.replace("_", " ").title()

    @classmethod
    def from_defaults(cls, node_type: NodeType) -> "NodeConfig":
        """
        Create a NodeConfig using default values.

        Args:
            node_type: The type of node to configure

        Returns:
            NodeConfig with default capacity and service times
        """
        # Get default capacity
        capacity = NODE_CAPACITY_DEFAULTS.get(node_type.name, 1)

        # Build service time config from defaults
        node_service_times = SERVICE_TIME_DEFAULTS.get(node_type.name, {})
        params_by_acuity = {}
        for acuity_val, params in node_service_times.items():
            try:
                acuity = Acuity(acuity_val)
                params_by_acuity[acuity] = params
            except ValueError:
                pass

        service_config = ServiceTimeConfig(
            distribution="lognormal",
            params_by_acuity=params_by_acuity,
        )

        return cls(
            node_type=node_type,
            capacity=capacity,
            service_time_config=service_config,
        )


# =============================================================================
# Node Class
# =============================================================================

class Node:
    """
    Represents a processing station in the ED.

    Each node has:
    - Configurable capacity (number of servers/beds)
    - Priority queuing based on patient acuity
    - Service time distribution (varies by acuity)
    - LWBS monitoring for waiting patients
    - Statistics collection

    Usage:
        >>> env = simpy.Environment()
        >>> config = NodeConfig(node_type=NodeType.TRIAGE, capacity=2)
        >>> node = Node(env, config)
        >>>
        >>> # In a process:
        >>> yield from node.process_patient(patient)
    """

    def __init__(
        self,
        env: Environment,
        config: NodeConfig,
        rng: Optional[random.Random] = None,
    ):
        """
        Initialize a Node.

        Args:
            env: SimPy environment
            config: Node configuration
            rng: Random number generator for reproducibility
        """
        self._env = env
        self._config = config
        self._rng = rng or random.Random()

        # Create SimPy resource
        self._resource = PriorityResource(env, capacity=config.capacity)

        # Service time generator
        self._service_time_generator = ServiceTimeGenerator(
            config.service_time_config or ServiceTimeConfig(),
            rng=self._rng,
        )

        # Statistics
        self._stats = NodeStatistics()

        # Current state tracking
        self._waiting_patients: List[Patient] = []
        self._patients_in_service: List[Patient] = []

        # Event hooks for extensibility
        self._on_enter_callbacks: List[Callable[[Patient], None]] = []
        self._on_complete_callbacks: List[Callable[[Patient], None]] = []
        self._on_lwbs_callbacks: List[Callable[[Patient], None]] = []

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def node_type(self) -> NodeType:
        """Type of this node."""
        return self._config.node_type

    @property
    def name(self) -> str:
        """Display name of this node."""
        return self._config.name

    @property
    def capacity(self) -> int:
        """Number of servers/beds at this node."""
        return self._config.capacity

    @property
    def queue_length(self) -> int:
        """Current number of patients waiting."""
        return len(self._waiting_patients)

    @property
    def utilization(self) -> float:
        """Current utilization (fraction of capacity in use)."""
        if self._config.capacity == 0:
            return 0.0
        return len(self._patients_in_service) / self._config.capacity

    @property
    def statistics(self) -> NodeStatistics:
        """Get current statistics."""
        return self._stats

    @property
    def is_available(self) -> bool:
        """Whether any capacity is available."""
        return len(self._patients_in_service) < self._config.capacity
    
    def update_capacity(self, new_capacity: int) -> None:
        """
        Update node capacity by recreating the resource.
        
        Note: This will affect new requests but won't interrupt
        patients currently in service.
        
        Args:
            new_capacity: New capacity value (must be >= current patients in service)
        """
        if new_capacity < len(self._patients_in_service):
            # Can't reduce below current usage
            new_capacity = len(self._patients_in_service)
        
        self._config.capacity = new_capacity
        # Recreate resource with new capacity (use same class as original)
        resource_class = type(self._resource)
        self._resource = resource_class(self._env, capacity=new_capacity)

    # =========================================================================
    # Core Process Methods
    # =========================================================================

    def process_patient(
        self, patient: Patient
    ) -> Generator[simpy.Event, Any, bool]:
        """
        Process a patient through this node.

        This is the main entry point for patient processing.
        It handles:
        1. Queuing with priority
        2. LWBS monitoring while waiting
        3. Service time generation and execution
        4. Statistics collection

        Args:
            patient: The patient to process

        Yields:
            SimPy events during processing

        Returns:
            True if patient completed service, False if LWBS
        """
        # Record entry
        self._stats.patients_entered += 1
        patient.enter_queue(self._config.node_type)
        self._waiting_patients.append(patient)
        self._update_queue_stats()

        # Fire entry callbacks
        for callback in self._on_enter_callbacks:
            callback(patient)

        # Request resource with priority
        # Lower priority value = higher priority (ESI 1 = priority 1 = most urgent)
        priority = patient.priority if self._config.use_priority_queue else 0

        with self._resource.request(priority=priority) as request:
            # Wait for resource, checking LWBS periodically
            while True:
                # Create timeout for LWBS check
                lwbs_timeout = self._env.timeout(self._config.lwbs_check_interval)

                # Wait for either resource or timeout
                result = yield request | lwbs_timeout

                if request in result:
                    # Got the resource - break out of wait loop
                    break
                else:
                    # LWBS check timeout - evaluate if patient leaves
                    if self._should_patient_leave(patient):
                        # Patient decides to leave
                        self._handle_lwbs(patient)
                        return False

            # Patient got the resource - start service
            self._waiting_patients.remove(patient)
            self._patients_in_service.append(patient)
            patient.start_service(self._config.node_type)

            # Generate and execute service time
            service_time = self._service_time_generator.generate(patient.acuity)
            yield self._env.timeout(service_time)

            # Complete service
            patient.complete_service(self._config.node_type)
            self._patients_in_service.remove(patient)

            # Record statistics
            ts = patient.timestamps.get(self._config.node_type)
            if ts:
                self._stats.wait_times.append(ts.wait_time)
                self._stats.service_times.append(ts.service_time)
                self._stats.total_wait_time += ts.wait_time
                self._stats.total_service_time += ts.service_time

            self._stats.patients_completed += 1

            # Fire completion callbacks
            for callback in self._on_complete_callbacks:
                callback(patient)

            return True

    def _should_patient_leave(self, patient: Patient) -> bool:
        """
        Determine if a waiting patient should LWBS.

        Args:
            patient: Patient to evaluate

        Returns:
            True if patient decides to leave
        """
        if not self._config.enable_lwbs_monitoring:
            return False

        # Generate random value and let patient evaluate
        random_value = self._rng.random()
        return patient.evaluate_lwbs(random_value)

    def _handle_lwbs(self, patient: Patient) -> None:
        """
        Handle a patient leaving without being seen.

        Args:
            patient: The patient who is leaving
        """
        # Remove from waiting list
        if patient in self._waiting_patients:
            self._waiting_patients.remove(patient)

        # Update patient status
        patient.set_lwbs()

        # Update statistics
        self._stats.patients_lwbs += 1

        # Fire LWBS callbacks
        for callback in self._on_lwbs_callbacks:
            callback(patient)

    def _update_queue_stats(self) -> None:
        """Update queue length statistics."""
        current_length = len(self._waiting_patients)
        self._stats.max_queue_length = max(
            self._stats.max_queue_length,
            current_length
        )
        self._stats.queue_length_samples.append(
            (self._env.now, current_length)
        )

    # =========================================================================
    # Event Hook Registration
    # =========================================================================

    def on_patient_enter(self, callback: Callable[[Patient], None]) -> None:
        """Register callback for when patient enters queue."""
        self._on_enter_callbacks.append(callback)

    def on_patient_complete(self, callback: Callable[[Patient], None]) -> None:
        """Register callback for when patient completes service."""
        self._on_complete_callbacks.append(callback)

    def on_patient_lwbs(self, callback: Callable[[Patient], None]) -> None:
        """Register callback for when patient leaves without being seen."""
        self._on_lwbs_callbacks.append(callback)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_waiting_patients_by_acuity(self) -> Dict[Acuity, List[Patient]]:
        """
        Get waiting patients grouped by acuity.

        Returns:
            Dict mapping acuity to list of waiting patients
        """
        result: Dict[Acuity, List[Patient]] = {a: [] for a in Acuity}
        for patient in self._waiting_patients:
            result[patient.acuity].append(patient)
        return result

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Get current state snapshot for monitoring/UI.

        Returns:
            Dict containing current node state
        """
        return {
            "node_type": self._config.node_type.name,
            "name": self.name,
            "capacity": self.capacity,
            "queue_length": self.queue_length,
            "in_service": len(self._patients_in_service),
            "utilization": self.utilization,
            "avg_wait_time": self._stats.average_wait_time,
            "avg_service_time": self._stats.average_service_time,
            "lwbs_rate": self._stats.lwbs_rate,
            "waiting_by_acuity": {
                a.name: len(patients)
                for a, patients in self.get_waiting_patients_by_acuity().items()
            },
        }

    def reset_statistics(self) -> None:
        """Reset node statistics. Useful after warmup period."""
        self._stats.reset()

    def __repr__(self) -> str:
        return (
            f"Node({self.name}, capacity={self.capacity}, "
            f"queue={self.queue_length}, util={self.utilization:.1%})"
        )
