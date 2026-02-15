"""
Patient entity class for ED simulation.

The Patient class represents a single patient flowing through the ED.
It tracks all timing information, acuity, status, and outcomes.

Design Principles:
- Immutable ID and arrival characteristics
- Mutable status and timestamps
- Complete audit trail of patient journey
- LWBS-aware with probability tracking
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime

import simpy

from .enums import Acuity, PatientStatus, DispositionType, NodeType, Timestamp
from ..config.defaults import LWBS_DEFAULTS


@dataclass
class PatientConfig:
    """
    Configuration parameters for patient behavior.

    Externally configurable parameters that affect patient decisions
    like LWBS probability.
    """
    # LWBS parameters
    lwbs_base_probability: float = LWBS_DEFAULTS["base_probability"]
    lwbs_time_threshold_minutes: float = LWBS_DEFAULTS["threshold_minutes"]
    lwbs_max_probability: float = LWBS_DEFAULTS["max_probability"]
    lwbs_time_sensitivity: float = 0.005  # Increase per minute over threshold

    # Acuity-specific LWBS multipliers (lower acuity = more likely to leave)
    lwbs_acuity_multipliers: Dict[Acuity, float] = field(default_factory=lambda: {
        Acuity.ESI_1: 0.0,   # Never leave - life threatening
        Acuity.ESI_2: 0.05,  # Very unlikely to leave
        Acuity.ESI_3: 0.4,   # Somewhat unlikely
        Acuity.ESI_4: 1.0,   # Baseline
        Acuity.ESI_5: 1.8,   # More likely to leave
    })


class Patient:
    """
    Represents a patient entity in the ED simulation.

    Attributes:
        id: Unique identifier for the patient
        arrival_time: Simulation time when patient arrived
        acuity: ESI level (1-5)
        status: Current status in the ED
        timestamps: Timing records per node
        is_lwbs: Whether patient left without being seen
        disposition: Final outcome

    Usage:
        >>> env = simpy.Environment()
        >>> patient = Patient(env, arrival_time=10.0, acuity=Acuity.ESI_3)
        >>> patient.enter_queue(NodeType.TRIAGE)
        >>> # ... simulation proceeds ...
        >>> patient.start_service(NodeType.TRIAGE)
        >>> patient.complete_service(NodeType.TRIAGE)
    """

    # Class-level counter for sequential IDs (useful for debugging)
    _id_counter: int = 0

    def __init__(
        self,
        env: simpy.Environment,
        arrival_time: float,
        acuity: Acuity,
        config: Optional[PatientConfig] = None,
        patient_id: Optional[str] = None,
    ):
        """
        Initialize a new patient.

        Args:
            env: SimPy environment for time tracking
            arrival_time: Simulation time of arrival
            acuity: ESI acuity level
            config: Patient behavior configuration
            patient_id: Optional custom ID (auto-generated if None)
        """
        # Increment class counter
        Patient._id_counter += 1

        # Core identity (immutable after creation)
        self._id = patient_id or f"P-{Patient._id_counter:06d}"
        self._arrival_time = arrival_time
        self._acuity = acuity
        self._env = env
        self._config = config or PatientConfig()

        # Mutable state
        self._status = PatientStatus.ARRIVING
        self._current_node: Optional[NodeType] = None
        self._current_queue_enter_time: Optional[float] = None

        # Journey tracking
        self._timestamps: Dict[NodeType, Timestamp] = {}
        self._pending_timestamps: Dict[NodeType, Dict[str, float]] = {}

        # LWBS tracking
        self._lwbs_evaluations: List[Dict[str, Any]] = []
        self._is_lwbs: bool = False

        # Outcome
        self._disposition: Optional[DispositionType] = None
        self._departure_time: Optional[float] = None

        # Metadata for analysis
        self._metadata: Dict[str, Any] = {
            "created_at": datetime.now().isoformat(),
            "sequence_number": Patient._id_counter,
        }

    # =========================================================================
    # Properties (Read-only access to core attributes)
    # =========================================================================

    @property
    def id(self) -> str:
        """Unique patient identifier."""
        return self._id

    @property
    def arrival_time(self) -> float:
        """Simulation time when patient arrived at ED."""
        return self._arrival_time

    @property
    def acuity(self) -> Acuity:
        """ESI acuity level (1-5)."""
        return self._acuity

    @property
    def status(self) -> PatientStatus:
        """Current patient status."""
        return self._status

    @property
    def current_node(self) -> Optional[NodeType]:
        """Node where patient is currently located."""
        return self._current_node

    @property
    def is_lwbs(self) -> bool:
        """Whether patient left without being seen."""
        return self._is_lwbs

    @property
    def disposition(self) -> Optional[DispositionType]:
        """Final disposition (None if still in ED)."""
        return self._disposition

    @property
    def timestamps(self) -> Dict[NodeType, Timestamp]:
        """Completed timestamp records for all visited nodes."""
        return self._timestamps.copy()

    @property
    def priority(self) -> int:
        """
        Priority value for queue ordering.

        Lower values = higher priority (processed first).
        Uses acuity value directly since ESI 1 is most urgent.

        Returns:
            Priority value (1-5, where 1 is highest priority)
        """
        return self._acuity.value

    # =========================================================================
    # Computed Properties (Derived metrics)
    # =========================================================================

    @property
    def total_wait_time(self) -> float:
        """
        Total time spent waiting across all nodes.

        Returns:
            Cumulative wait time in simulation time units (minutes)
        """
        return sum(ts.wait_time for ts in self._timestamps.values())

    @property
    def total_service_time(self) -> float:
        """
        Total time spent in service across all nodes.

        Returns:
            Cumulative service time in simulation time units (minutes)
        """
        return sum(ts.service_time for ts in self._timestamps.values())

    @property
    def length_of_stay(self) -> Optional[float]:
        """
        Total time from arrival to departure.

        Returns:
            LOS in simulation time units (minutes), or None if still in ED
        """
        if self._departure_time is None:
            return None
        return self._departure_time - self._arrival_time

    @property
    def current_wait_time(self) -> float:
        """
        Time spent waiting at current node (if waiting).

        Returns:
            Current wait time in minutes, or 0 if not waiting
        """
        if self._current_queue_enter_time is None:
            return 0.0
        return self._env.now - self._current_queue_enter_time

    @property
    def time_in_ed(self) -> float:
        """
        Time since arrival (regardless of current status).

        Returns:
            Time elapsed since arrival in minutes
        """
        return self._env.now - self._arrival_time

    # =========================================================================
    # National ED Metrics (CMS/TJC Standard KPIs)
    # =========================================================================

    @property
    def door_to_triage(self) -> Optional[float]:
        """
        Time from arrival to triage service start.

        This is typically near-zero in most EDs as triage is the first stop.

        Returns:
            Minutes from arrival to triage start, or None if not yet triaged
        """
        if NodeType.TRIAGE not in self._timestamps:
            return None
        return self._timestamps[NodeType.TRIAGE].service_start - self._arrival_time

    @property
    def door_to_provider(self) -> Optional[float]:
        """
        Time from arrival to provider assessment start (ED-2b metric).

        Also known as "Door-to-Doctor" (D2D). This is one of the most
        important ED metrics tracked nationally by CMS.

        Returns:
            Minutes from arrival to provider assessment start, or None
        """
        if NodeType.PROVIDER_ASSESSMENT not in self._timestamps:
            return None
        return self._timestamps[NodeType.PROVIDER_ASSESSMENT].service_start - self._arrival_time

    # Alias for common terminology
    door_to_doctor = door_to_provider

    @property
    def door_to_bed(self) -> Optional[float]:
        """
        Time from arrival to bed assignment (ED-1b metric).

        Measures how long patients wait before getting an ED bed.

        Returns:
            Minutes from arrival to bed assignment start, or None
        """
        if NodeType.BED_ASSIGNMENT not in self._timestamps:
            return None
        return self._timestamps[NodeType.BED_ASSIGNMENT].service_start - self._arrival_time

    @property
    def provider_to_disposition(self) -> Optional[float]:
        """
        Time from provider assessment to disposition decision.

        Measures the clinical decision-making and treatment time.

        Returns:
            Minutes from provider start to disposition end, or None
        """
        if NodeType.PROVIDER_ASSESSMENT not in self._timestamps:
            return None
        if NodeType.DISPOSITION not in self._timestamps:
            return None
        provider_start = self._timestamps[NodeType.PROVIDER_ASSESSMENT].service_start
        disposition_end = self._timestamps[NodeType.DISPOSITION].service_end
        return disposition_end - provider_start

    @property
    def seen_by_provider(self) -> bool:
        """
        Whether patient was seen by a provider.

        Used to distinguish LWBS (left before provider) from LBTC
        (left after seeing provider but before treatment complete).

        Returns:
            True if provider assessment was started
        """
        return NodeType.PROVIDER_ASSESSMENT in self._timestamps

    @property
    def is_lbtc(self) -> bool:
        """
        Whether patient Left Before Treatment Complete (LBTC).

        LBTC means the patient was seen by a provider but left before
        completing treatment/disposition. This is tracked separately
        from LWBS (Left Without Being Seen).

        Returns:
            True if patient left after seeing provider but before discharge
        """
        # Must have been seen by provider
        if not self.seen_by_provider:
            return False
        # Must have left (not still in ED, not properly discharged)
        if self._disposition is None:
            return False
        # LWBS after seeing provider = LBTC
        return self._disposition == DispositionType.LWBS

    @property
    def treatment_time(self) -> Optional[float]:
        """
        Total time spent in treatment.

        Returns:
            Minutes in treatment node, or None if not treated
        """
        if NodeType.TREATMENT not in self._timestamps:
            return None
        return self._timestamps[NodeType.TREATMENT].total_time

    @property
    def diagnostic_time(self) -> Optional[float]:
        """
        Total time spent in diagnostics (labs, imaging).

        Returns:
            Minutes in diagnostics node, or None if no diagnostics
        """
        if NodeType.DIAGNOSTICS not in self._timestamps:
            return None
        return self._timestamps[NodeType.DIAGNOSTICS].total_time

    # =========================================================================
    # State Transition Methods
    # =========================================================================

    def enter_queue(self, node_type: NodeType) -> None:
        """
        Record patient entering a node's queue.

        Args:
            node_type: The type of node being entered

        Raises:
            ValueError: If patient is already in a queue
        """
        if self._status == PatientStatus.WAITING:
            raise ValueError(
                f"Patient {self._id} already waiting at {self._current_node}"
            )

        self._status = PatientStatus.WAITING
        self._current_node = node_type
        self._current_queue_enter_time = self._env.now

        # Start building timestamp record
        self._pending_timestamps[node_type] = {
            "queue_enter": self._env.now
        }

    def start_service(self, node_type: NodeType) -> None:
        """
        Record patient starting service at a node.

        Args:
            node_type: The type of node (must match current node)

        Raises:
            ValueError: If node_type doesn't match current node
        """
        if node_type != self._current_node:
            raise ValueError(
                f"Patient {self._id} is at {self._current_node}, not {node_type}"
            )

        self._status = PatientStatus.IN_SERVICE

        # Record service start in pending timestamp
        if node_type in self._pending_timestamps:
            self._pending_timestamps[node_type]["service_start"] = self._env.now

        # Clear queue enter time (no longer waiting)
        self._current_queue_enter_time = None

    def complete_service(self, node_type: NodeType) -> None:
        """
        Record patient completing service at a node.

        Args:
            node_type: The type of node (must match current node)

        Raises:
            ValueError: If node_type doesn't match or patient not in service
        """
        if node_type != self._current_node:
            raise ValueError(
                f"Patient {self._id} is at {self._current_node}, not {node_type}"
            )

        if self._status != PatientStatus.IN_SERVICE:
            raise ValueError(
                f"Patient {self._id} is not in service (status: {self._status})"
            )

        # Complete timestamp record
        pending = self._pending_timestamps.pop(node_type, {})
        self._timestamps[node_type] = Timestamp(
            node_type=node_type,
            queue_enter=pending.get("queue_enter", self._env.now),
            service_start=pending.get("service_start", self._env.now),
            service_end=self._env.now,
        )

        # Reset current node (patient is between nodes)
        self._current_node = None
        self._status = PatientStatus.ARRIVING  # Ready for next node

    def set_lwbs(self) -> None:
        """
        Mark patient as Left Without Being Seen.

        This ends the patient's journey through the ED.
        """
        self._is_lwbs = True
        self._status = PatientStatus.LWBS
        self._disposition = DispositionType.LWBS
        self._departure_time = self._env.now

        # Clean up any pending timestamps
        self._pending_timestamps.clear()
        self._current_queue_enter_time = None

    def discharge(
        self, disposition: DispositionType = DispositionType.DISCHARGE_HOME
    ) -> None:
        """
        Mark patient as discharged from ED.

        Args:
            disposition: Type of discharge (default: home)
        """
        self._status = PatientStatus.DISCHARGED
        self._disposition = disposition
        self._departure_time = self._env.now
        self._current_node = None

    # =========================================================================
    # LWBS Probability Calculation
    # =========================================================================

    def calculate_lwbs_probability(self) -> float:
        """
        Calculate probability of patient leaving without being seen.

        The probability is based on:
        1. Base probability (configurable, ~2% default)
        2. Wait time factor (increases after threshold)
        3. Acuity multiplier (lower acuity = higher probability)

        Formula:
            P(LWBS) = min(base_prob + time_factor, max_prob) * acuity_mult

        Where:
            time_factor = max(0, (current_wait - threshold) * sensitivity)

        Returns:
            Probability in range [0, 1]
        """
        # ESI 1 patients never leave
        if self._acuity == Acuity.ESI_1:
            return 0.0

        # Calculate time-based component
        wait_minutes = self.current_wait_time

        if wait_minutes <= self._config.lwbs_time_threshold_minutes:
            time_factor = 0.0
        else:
            excess_wait = wait_minutes - self._config.lwbs_time_threshold_minutes
            time_factor = excess_wait * self._config.lwbs_time_sensitivity

        # Calculate base probability with time factor
        base_prob = self._config.lwbs_base_probability + time_factor
        capped_prob = min(base_prob, self._config.lwbs_max_probability)

        # Apply acuity multiplier
        acuity_mult = self._config.lwbs_acuity_multipliers.get(self._acuity, 1.0)
        final_prob = capped_prob * acuity_mult

        # Ensure probability is in valid range
        return max(0.0, min(1.0, final_prob))

    def evaluate_lwbs(self, random_value: float) -> bool:
        """
        Evaluate whether patient decides to leave.

        This method should be called periodically while patient is waiting.
        It records the evaluation for analysis.

        Args:
            random_value: Random number in [0, 1) for decision

        Returns:
            True if patient decides to leave
        """
        probability = self.calculate_lwbs_probability()
        decision = random_value < probability

        # Record evaluation for analysis
        self._lwbs_evaluations.append({
            "time": self._env.now,
            "wait_time": self.current_wait_time,
            "probability": probability,
            "random_value": random_value,
            "decision": decision,
        })

        return decision

    # =========================================================================
    # Serialization and Analysis
    # =========================================================================

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert patient to dictionary for analysis/export.

        Returns:
            Dict containing all patient data
        """
        return {
            "id": self._id,
            "arrival_time": self._arrival_time,
            "acuity": self._acuity.value,
            "acuity_name": self._acuity.name,
            "status": self._status.name,
            "is_lwbs": self._is_lwbs,
            "is_lbtc": self.is_lbtc,
            "seen_by_provider": self.seen_by_provider,
            "disposition": self._disposition.name if self._disposition else None,
            "departure_time": self._departure_time,
            # National ED Metrics
            "length_of_stay": self.length_of_stay,
            "door_to_triage": self.door_to_triage,
            "door_to_provider": self.door_to_provider,
            "door_to_bed": self.door_to_bed,
            "provider_to_disposition": self.provider_to_disposition,
            # Time breakdowns
            "total_wait_time": self.total_wait_time,
            "total_service_time": self.total_service_time,
            "treatment_time": self.treatment_time,
            "diagnostic_time": self.diagnostic_time,
            "timestamps": {
                node.name: {
                    "wait_time": ts.wait_time,
                    "service_time": ts.service_time,
                    "total_time": ts.total_time,
                }
                for node, ts in self._timestamps.items()
            },
            "lwbs_evaluations_count": len(self._lwbs_evaluations),
            "metadata": self._metadata,
        }

    def __repr__(self) -> str:
        return (
            f"Patient(id={self._id}, acuity={self._acuity.name}, "
            f"status={self._status.name}, node={self._current_node})"
        )

    def __lt__(self, other: "Patient") -> bool:
        """
        Comparison for priority queue ordering.

        Patients are compared by:
        1. Acuity (lower ESI = higher priority)
        2. Arrival time (earlier = higher priority for same acuity)
        """
        if not isinstance(other, Patient):
            return NotImplemented

        if self._acuity != other._acuity:
            return self._acuity.value < other._acuity.value
        return self._arrival_time < other._arrival_time

    @classmethod
    def reset_counter(cls) -> None:
        """Reset the patient ID counter. Useful for testing."""
        cls._id_counter = 0
