"""
Enumerations and type definitions for the ED simulation.

These provide type safety and clear semantics throughout the codebase.
"""

from enum import IntEnum, Enum, auto
from dataclasses import dataclass


class Acuity(IntEnum):
    """
    Emergency Severity Index (ESI) levels.

    Lower values indicate higher acuity (more urgent).
    ESI 1-2: Acuity-based (immediate/emergent)
    ESI 3-5: Resource-based (determined by expected resource usage)

    Reference: ESI Handbook, 5th Edition
    """
    ESI_1 = 1  # Immediate, life-threatening (~1% of patients)
    ESI_2 = 2  # Emergent, high risk (~10% of patients)
    ESI_3 = 3  # Urgent, multiple resources (~35% of patients)
    ESI_4 = 4  # Less urgent, one resource (~35% of patients)
    ESI_5 = 5  # Non-urgent, no resources (~19% of patients)


class NodeType(Enum):
    """
    Types of ED processing nodes.

    Each node represents a distinct station in the ED workflow.
    """
    TRIAGE = auto()               # Initial assessment and ESI assignment
    REGISTRATION = auto()         # Administrative intake
    BED_ASSIGNMENT = auto()       # Waiting for available bed
    PROVIDER_ASSESSMENT = auto()  # Physician/NP evaluation
    DIAGNOSTICS = auto()          # Labs, imaging, etc.
    TREATMENT = auto()            # Procedures, medications
    DISPOSITION = auto()          # Discharge or admission decision


class PatientStatus(Enum):
    """
    Current status of a patient in the ED.
    """
    ARRIVING = auto()     # Just entered the system
    WAITING = auto()      # In queue at a node
    IN_SERVICE = auto()   # Being processed at a node
    LWBS = auto()         # Left without being seen
    DISCHARGED = auto()   # Released from ED
    ADMITTED = auto()     # Transferred to inpatient (out of scope for MVP)


class DispositionType(Enum):
    """
    Final disposition of patient.
    """
    DISCHARGE_HOME = auto()   # Released to go home
    ADMIT_INPATIENT = auto()  # Transferred to inpatient (out of scope for MVP)
    TRANSFER = auto()         # Transfer to another facility
    LWBS = auto()             # Left without being seen
    AMA = auto()              # Against Medical Advice


@dataclass(frozen=True)
class Timestamp:
    """
    Immutable timestamp record for a patient's journey through a node.

    Attributes:
        node_type: The type of node this timestamp is for
        queue_enter: Simulation time when patient entered queue
        service_start: Simulation time when service began
        service_end: Simulation time when service completed
    """
    node_type: NodeType
    queue_enter: float
    service_start: float
    service_end: float

    @property
    def wait_time(self) -> float:
        """Time spent waiting in queue (minutes)."""
        return self.service_start - self.queue_enter

    @property
    def service_time(self) -> float:
        """Time spent in service (minutes)."""
        return self.service_end - self.service_start

    @property
    def total_time(self) -> float:
        """Total time at this node: wait + service (minutes)."""
        return self.service_end - self.queue_enter
