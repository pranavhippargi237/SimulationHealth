"""
Vertical/Fast Track Scenario

Simulates a vertical patient flow or fast-track pathway for
low-acuity patients (ESI 4-5). These patients are seen in a
separate area without requiring a traditional ED bed.

This is a common ED throughput improvement strategy that can
significantly reduce wait times and LOS for low-acuity patients
while freeing up beds for higher-acuity patients.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, TYPE_CHECKING
import random

from ..core.enums import NodeType, Acuity
from ..core.node import Node, NodeConfig, ServiceTimeConfig

if TYPE_CHECKING:
    from ..core.patient import Patient


@dataclass
class VerticalTrackConfig:
    """Configuration for vertical/fast track scenario."""

    # Which acuity levels use fast track
    fast_track_acuities: List[Acuity] = None

    # Fast track provider capacity
    fast_track_providers: int = 2

    # Service time reduction for fast track (multiplier)
    service_time_multiplier: float = 0.7  # 30% faster

    # Skip bed assignment for fast track patients
    skip_bed_assignment: bool = True

    def __post_init__(self):
        if self.fast_track_acuities is None:
            self.fast_track_acuities = [Acuity.ESI_4, Acuity.ESI_5]


class VerticalTrackScenario:
    """
    Vertical/Fast Track Scenario: Separate pathway for low-acuity patients.

    Creates a fast-track pathway where ESI 4-5 patients:
    - Skip traditional bed assignment
    - Are seen by dedicated fast-track providers
    - Have reduced service times
    - Skip diagnostics (most don't need them)

    This frees up main ED beds for higher-acuity patients.

    Usage:
        >>> scenario = VerticalTrackScenario(fast_track_providers=2)
        >>> scenario.apply(nodes)
        >>> pathway = scenario.get_pathway(patient)  # Returns modified pathway
    """

    name = "Vertical/Fast Track"
    description = "Separate fast-track pathway for ESI 4-5 patients"

    def __init__(
        self,
        fast_track_providers: int = 2,
        config: Optional[VerticalTrackConfig] = None,
    ):
        """
        Initialize vertical track scenario.

        Args:
            fast_track_providers: Number of providers in fast track area
            config: Full configuration (overrides if provided)
        """
        if config:
            self._config = config
        else:
            self._config = VerticalTrackConfig(
                fast_track_providers=fast_track_providers
            )

        self._fast_track_node: Optional[Node] = None

    @property
    def fast_track_acuities(self) -> List[Acuity]:
        """Acuity levels that use fast track."""
        return self._config.fast_track_acuities

    def apply(self, nodes: Dict[NodeType, Node], env=None) -> Dict[NodeType, Node]:
        """
        Apply vertical track scenario.

        Creates a fast-track provider node with reduced service times.

        Args:
            nodes: Dictionary of NodeType to Node
            env: SimPy environment (required to create new node)

        Returns:
            Modified nodes dictionary
        """
        if env is None and nodes:
            # Get env from existing node
            env = next(iter(nodes.values()))._env

        # Create fast track provider node with faster service times
        fast_track_config = NodeConfig(
            node_type=NodeType.PROVIDER_ASSESSMENT,
            capacity=self._config.fast_track_providers,
            service_time_config=ServiceTimeConfig(
                distribution="lognormal",
                default_params={
                    "mean": 8.0,  # Faster than regular provider
                    "std_dev": 3.0,
                },
            ),
            display_name="Fast Track Provider",
            enable_lwbs_monitoring=True,
        )

        self._fast_track_node = Node(env, fast_track_config, rng=random.Random())

        return nodes

    def is_fast_track_eligible(self, patient: "Patient") -> bool:
        """
        Check if patient is eligible for fast track.

        Args:
            patient: Patient to check

        Returns:
            True if patient should use fast track
        """
        return patient.acuity in self._config.fast_track_acuities

    def get_pathway(self, patient: "Patient", default_pathway: List[NodeType]) -> List[NodeType]:
        """
        Get the pathway for a patient, potentially using fast track.

        Args:
            patient: Patient to route
            default_pathway: Standard pathway for this acuity

        Returns:
            Modified pathway if fast track eligible, otherwise default
        """
        if not self.is_fast_track_eligible(patient):
            return default_pathway

        # Fast track pathway: Triage -> Registration -> Fast Track Provider -> Treatment -> Disposition
        # Skip bed assignment and diagnostics
        return [
            NodeType.TRIAGE,
            NodeType.REGISTRATION,
            NodeType.PROVIDER_ASSESSMENT,  # Will use fast track node
            NodeType.TREATMENT,
            NodeType.DISPOSITION,
        ]

    def get_node_for_patient(
        self,
        patient: "Patient",
        node_type: NodeType,
        nodes: Dict[NodeType, Node]
    ) -> Node:
        """
        Get the appropriate node for a patient.

        Fast track patients use the fast track provider node.

        Args:
            patient: Patient being processed
            node_type: Type of node needed
            nodes: Available nodes

        Returns:
            Appropriate Node for this patient
        """
        if (self.is_fast_track_eligible(patient)
            and node_type == NodeType.PROVIDER_ASSESSMENT
            and self._fast_track_node is not None):
            return self._fast_track_node

        return nodes[node_type]

    def get_description(self) -> str:
        """Get human-readable description of scenario settings."""
        acuities = ", ".join(a.name for a in self._config.fast_track_acuities)
        return f"Fast Track: {self._config.fast_track_providers} providers for {acuities}"
