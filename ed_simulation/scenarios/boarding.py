"""
Boarding Scenario

Simulates the impact of admitted patients boarding in the ED,
which reduces effective bed capacity for new ED patients.

Boarding is one of the most significant contributors to ED crowding
and is associated with increased mortality, longer wait times,
and higher LWBS rates.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from ..core.enums import NodeType
from ..core.node import Node
from ..config.defaults import NODE_CAPACITY_DEFAULTS


@dataclass
class BoardingScenarioConfig:
    """Configuration for boarding scenario."""

    # Percentage of beds occupied by boarding patients (0.0 to 1.0)
    boarding_percentage: float = 0.40  # 40% default

    # Which node represents beds (typically BED_ASSIGNMENT)
    bed_node: NodeType = NodeType.BED_ASSIGNMENT

    # Also affects treatment capacity (patients treated in beds)
    affects_treatment: bool = True


class BoardingScenario:
    """
    Boarding Scenario: Reduces effective bed capacity.

    When admitted patients board in the ED waiting for inpatient beds,
    they occupy ED beds that would otherwise be available for new patients.
    This scenario reduces the BED_ASSIGNMENT capacity by a configurable
    percentage to simulate this effect.

    Usage:
        >>> scenario = BoardingScenario(boarding_percentage=0.40)
        >>> scenario.apply(nodes)  # Reduces bed capacity by 40%
    """

    name = "Boarding"
    description = "Reduces bed capacity to simulate admitted patients boarding in ED"

    def __init__(
        self,
        boarding_percentage: float = 0.40,
        config: Optional[BoardingScenarioConfig] = None,
    ):
        """
        Initialize boarding scenario.

        Args:
            boarding_percentage: Fraction of beds occupied by boarding (0.0-1.0)
            config: Full configuration (overrides boarding_percentage if provided)
        """
        if config:
            self._config = config
        else:
            self._config = BoardingScenarioConfig(
                boarding_percentage=boarding_percentage
            )

    @property
    def boarding_percentage(self) -> float:
        """Percentage of beds occupied by boarding patients."""
        return self._config.boarding_percentage

    def apply(self, nodes: Dict[NodeType, Node]) -> Dict[NodeType, Node]:
        """
        Apply boarding scenario to nodes.

        Reduces the capacity of bed-related nodes.

        Args:
            nodes: Dictionary of NodeType to Node

        Returns:
            Modified nodes dictionary
        """
        # Calculate reduced capacity
        base_bed_capacity = NODE_CAPACITY_DEFAULTS.get("BED_ASSIGNMENT", 20)
        available_fraction = 1.0 - self._config.boarding_percentage
        new_capacity = max(1, int(base_bed_capacity * available_fraction))

        # Update bed assignment node
        if self._config.bed_node in nodes:
            node = nodes[self._config.bed_node]
            # Create new resource with reduced capacity
            node._resource = node._resource.__class__(
                node._env,
                capacity=new_capacity
            )
            node._config.capacity = new_capacity

        # Also reduce treatment capacity if configured
        if self._config.affects_treatment and NodeType.TREATMENT in nodes:
            treatment_node = nodes[NodeType.TREATMENT]
            treatment_node._resource = treatment_node._resource.__class__(
                treatment_node._env,
                capacity=new_capacity
            )
            treatment_node._config.capacity = new_capacity

        return nodes

    def get_description(self) -> str:
        """Get human-readable description of scenario settings."""
        pct = int(self._config.boarding_percentage * 100)
        return f"Boarding: {pct}% of beds occupied by admitted patients"
