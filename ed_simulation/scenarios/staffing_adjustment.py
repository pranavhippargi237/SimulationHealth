"""
Staffing Adjustment Scenario

Simulates changes in staffing levels during specific time windows.
This models common staffing strategies like:
- Adding staff during peak hours
- Reducing staff during low-volume periods
- Shift changes with overlap

Staff adjustments are constrained by total FTE to model realistic
resource limitations.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.enums import NodeType
from ..core.node import Node
from ..config.defaults import NODE_CAPACITY_DEFAULTS


@dataclass
class StaffingWindow:
    """A time window with specific staffing levels."""

    # Start and end hours (0-23)
    start_hour: int
    end_hour: int

    # Capacity adjustments by node type (absolute values)
    # If not specified, uses base capacity
    capacities: Dict[NodeType, int] = field(default_factory=dict)

    def contains_hour(self, hour: int) -> bool:
        """Check if hour falls within this window."""
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        else:
            # Wraps around midnight
            return hour >= self.start_hour or hour < self.end_hour


@dataclass
class StaffingAdjustmentConfig:
    """Configuration for staffing adjustment scenario."""

    # Total FTE constraint (not currently enforced, for reference)
    total_fte: float = 20.0

    # Staffing windows (applied in order, later windows override earlier)
    windows: List[StaffingWindow] = field(default_factory=list)

    # Default: peak staffing 10am-8pm
    def __post_init__(self):
        if not self.windows:
            self.windows = [
                # Night shift (reduced)
                StaffingWindow(
                    start_hour=22,
                    end_hour=6,
                    capacities={
                        NodeType.TRIAGE: 1,
                        NodeType.REGISTRATION: 1,
                        NodeType.PROVIDER_ASSESSMENT: 2,
                    }
                ),
                # Day shift (normal)
                StaffingWindow(
                    start_hour=6,
                    end_hour=14,
                    capacities={
                        NodeType.TRIAGE: 2,
                        NodeType.REGISTRATION: 2,
                        NodeType.PROVIDER_ASSESSMENT: 4,
                    }
                ),
                # Peak hours (increased)
                StaffingWindow(
                    start_hour=14,
                    end_hour=22,
                    capacities={
                        NodeType.TRIAGE: 3,
                        NodeType.REGISTRATION: 2,
                        NodeType.PROVIDER_ASSESSMENT: 5,
                    }
                ),
            ]


class StaffingAdjustmentScenario:
    """
    Staffing Adjustment Scenario: Time-based capacity changes.

    Adjusts node capacities based on time of day to model
    realistic staffing patterns. This can simulate:
    - Peak hour staffing increases
    - Night shift reductions
    - Shift change overlaps

    Note: This scenario works best with time-varying arrival rates.

    Usage:
        >>> scenario = StaffingAdjustmentScenario()
        >>> # Add custom window
        >>> scenario.add_window(StaffingWindow(
        ...     start_hour=10, end_hour=18,
        ...     capacities={NodeType.PROVIDER_ASSESSMENT: 6}
        ... ))
        >>> scenario.apply(nodes)
    """

    name = "Staffing Adjustment"
    description = "Adjusts staff levels by time of day (peak hours staffing)"

    def __init__(
        self,
        config: Optional[StaffingAdjustmentConfig] = None,
        peak_provider_increase: int = 1,
        time_window_start: Optional[int] = None,
        time_window_end: Optional[int] = None,
        target_node: Optional[NodeType] = None,
        additional_staff: int = 0,
    ):
        """
        Initialize staffing adjustment scenario.

        Args:
            config: Full configuration
            peak_provider_increase: Additional providers during peak (14:00-22:00) - legacy
            time_window_start: Start hour for time window (0-23)
            time_window_end: End hour for time window (0-23)
            target_node: Node type to adjust during time window
            additional_staff: Additional staff count to add during window
        """
        self._current_hour: int = 0
        self._nodes: Optional[Dict[NodeType, Node]] = None
        self._hourly_capacities: Dict[int, Dict[NodeType, int]] = {}
        
        # New time-window mode
        if time_window_start is not None and time_window_end is not None and target_node is not None:
            self._time_window_start = time_window_start
            self._time_window_end = time_window_end
            self._target_node = target_node
            self._additional_staff = additional_staff
            self._use_time_window = True
            
            # Create a simple config with one window
            self._config = StaffingAdjustmentConfig()
            self._config.windows = [
                StaffingWindow(
                    start_hour=time_window_start,
                    end_hour=time_window_end,
                    capacities={}
                )
            ]
        else:
            # Legacy mode
            self._use_time_window = False
            if config:
                self._config = config
            else:
                # Create default config with peak increase
                self._config = StaffingAdjustmentConfig()
                # Modify peak window based on parameter
                for window in self._config.windows:
                    if window.start_hour == 14:
                        base = NODE_CAPACITY_DEFAULTS.get("PROVIDER_ASSESSMENT", 4)
                        window.capacities[NodeType.PROVIDER_ASSESSMENT] = base + peak_provider_increase

    def add_window(self, window: StaffingWindow) -> None:
        """Add a staffing window."""
        self._config.windows.append(window)

    def apply(self, nodes: Dict[NodeType, Node]) -> Dict[NodeType, Node]:
        """
        Apply staffing scenario to nodes.

        Stores reference to nodes for dynamic updates.
        Initial application uses hour 0.

        Args:
            nodes: Dictionary of NodeType to Node

        Returns:
            Modified nodes dictionary
        """
        self._nodes = nodes
        self._update_capacities(0)
        return nodes

    def update_for_time(self, simulation_time: float) -> None:
        """
        Update staffing based on current simulation time.

        Call this periodically during simulation to adjust capacities.

        Args:
            simulation_time: Current simulation time in minutes
        """
        hour = int((simulation_time / 60) % 24)
        if hour != self._current_hour:
            self._current_hour = hour
            self._update_capacities(hour)

    def _update_capacities(self, hour: int) -> None:
        """Update node capacities for given hour."""
        if self._nodes is None:
            return

        # Start with defaults
        capacities = {
            NodeType.TRIAGE: NODE_CAPACITY_DEFAULTS.get("TRIAGE", 2),
            NodeType.REGISTRATION: NODE_CAPACITY_DEFAULTS.get("REGISTRATION", 2),
            NodeType.BED_ASSIGNMENT: NODE_CAPACITY_DEFAULTS.get("BED_ASSIGNMENT", 20),
            NodeType.FAST_TRACK: NODE_CAPACITY_DEFAULTS.get("FAST_TRACK", 4),
            NodeType.PROVIDER_ASSESSMENT: NODE_CAPACITY_DEFAULTS.get("PROVIDER_ASSESSMENT", 4),
            NodeType.DIAGNOSTICS: NODE_CAPACITY_DEFAULTS.get("DIAGNOSTICS", 5),
            NodeType.TREATMENT: NODE_CAPACITY_DEFAULTS.get("TREATMENT", 20),
            NodeType.DISPOSITION: NODE_CAPACITY_DEFAULTS.get("DISPOSITION", 4),
        }

        # Apply time-window mode
        if self._use_time_window:
            # Check if hour is in time window
            in_window = False
            if self._time_window_start <= self._time_window_end:
                in_window = self._time_window_start <= hour < self._time_window_end
            else:
                # Wraps around midnight
                in_window = hour >= self._time_window_start or hour < self._time_window_end
            
            if in_window and self._target_node in capacities:
                base_capacity = capacities[self._target_node]
                capacities[self._target_node] = base_capacity + self._additional_staff
        else:
            # Legacy mode: Apply windows (later windows override)
            for window in self._config.windows:
                if window.contains_hour(hour):
                    capacities.update(window.capacities)

        # Store hourly capacity for tracking
        self._hourly_capacities[hour] = capacities.copy()

        # Update nodes - recreate resources for dynamic capacity changes
        for node_type, capacity in capacities.items():
            if node_type in self._nodes:
                node = self._nodes[node_type]
                # Use the new update_capacity method that recreates the resource
                node.update_capacity(capacity)

    def get_capacity_for_hour(self, hour: int) -> Dict[NodeType, int]:
        """
        Get staffing capacities for a specific hour.

        Args:
            hour: Hour of day (0-23)

        Returns:
            Dictionary of node type to capacity
        """
        capacities = {
            NodeType.TRIAGE: NODE_CAPACITY_DEFAULTS.get("TRIAGE", 2),
            NodeType.REGISTRATION: NODE_CAPACITY_DEFAULTS.get("REGISTRATION", 2),
            NodeType.PROVIDER_ASSESSMENT: NODE_CAPACITY_DEFAULTS.get("PROVIDER_ASSESSMENT", 4),
        }

        for window in self._config.windows:
            if window.contains_hour(hour):
                capacities.update(window.capacities)

        return capacities

    def get_description(self) -> str:
        """Get human-readable description of scenario settings."""
        peak_cap = self.get_capacity_for_hour(16)  # 4pm
        night_cap = self.get_capacity_for_hour(2)  # 2am
        return (
            f"Staffing: {peak_cap.get(NodeType.PROVIDER_ASSESSMENT, 4)} providers peak, "
            f"{night_cap.get(NodeType.PROVIDER_ASSESSMENT, 2)} night"
        )
