"""
Surge Scenario

Simulates a surge in patient arrivals, such as during:
- Flu season
- Mass casualty incidents
- Community events
- Post-holiday periods

This scenario increases the arrival rate by a configurable
percentage to stress-test ED capacity.
"""

from dataclasses import dataclass
from typing import Optional

from ..config.defaults import ARRIVAL_DEFAULTS


@dataclass
class SurgeScenarioConfig:
    """Configuration for surge scenario."""

    # Percentage increase in arrival rate (0.0 to 1.0+)
    surge_percentage: float = 0.30  # 30% increase

    # Optional: surge only during certain hours
    surge_start_hour: Optional[int] = None
    surge_end_hour: Optional[int] = None

    # Whether to also increase high-acuity percentage
    increase_acuity: bool = False
    acuity_shift: float = 0.05  # Shift 5% from ESI 4-5 to ESI 2-3


class SurgeScenario:
    """
    Surge Scenario: Increases patient arrival rate.

    Simulates increased patient volume by multiplying the
    base arrival rate by a surge factor. This tests how
    the ED handles periods of high demand.

    Usage:
        >>> scenario = SurgeScenario(surge_percentage=0.30)
        >>> adjusted_rate = scenario.apply_to_rate(base_rate)  # Returns rate * 1.3
    """

    name = "Surge"
    description = "Increases arrival rate to simulate high-volume periods"

    def __init__(
        self,
        surge_percentage: float = 0.30,
        config: Optional[SurgeScenarioConfig] = None,
    ):
        """
        Initialize surge scenario.

        Args:
            surge_percentage: Fractional increase (0.30 = 30% increase)
            config: Full configuration (overrides if provided)
        """
        if config:
            self._config = config
        else:
            self._config = SurgeScenarioConfig(
                surge_percentage=surge_percentage
            )

    @property
    def surge_percentage(self) -> float:
        """Percentage increase in arrivals."""
        return self._config.surge_percentage

    @property
    def surge_multiplier(self) -> float:
        """Multiplier to apply to arrival rate."""
        return 1.0 + self._config.surge_percentage

    def apply_to_rate(self, base_rate: float) -> float:
        """
        Apply surge to a base arrival rate.

        Args:
            base_rate: Base arrival rate (patients per hour)

        Returns:
            Surged arrival rate
        """
        return base_rate * self.surge_multiplier

    def get_rate_for_hour(self, base_rate: float, hour: int) -> float:
        """
        Get arrival rate for a specific hour.

        Applies surge only during configured hours if set.

        Args:
            base_rate: Base arrival rate
            hour: Hour of day (0-23)

        Returns:
            Adjusted arrival rate
        """
        # If no time restriction, apply surge always
        if (self._config.surge_start_hour is None or
            self._config.surge_end_hour is None):
            return self.apply_to_rate(base_rate)

        # Check if current hour is in surge window
        start = self._config.surge_start_hour
        end = self._config.surge_end_hour

        if start <= end:
            in_window = start <= hour < end
        else:
            # Wraps around midnight
            in_window = hour >= start or hour < end

        if in_window:
            return self.apply_to_rate(base_rate)
        return base_rate

    def get_adjusted_acuity_distribution(self) -> dict:
        """
        Get adjusted acuity distribution if acuity shift is enabled.

        During surges, there may be more high-acuity patients.

        Returns:
            Adjusted acuity distribution
        """
        from ..config.defaults import ACUITY_DISTRIBUTION

        if not self._config.increase_acuity:
            return ACUITY_DISTRIBUTION.copy()

        # Shift some probability from low to high acuity
        shift = self._config.acuity_shift
        return {
            1: ACUITY_DISTRIBUTION[1] + shift * 0.2,  # Slight increase in ESI 1
            2: ACUITY_DISTRIBUTION[2] + shift * 0.8,  # More ESI 2
            3: ACUITY_DISTRIBUTION[3] + shift * 1.0,  # More ESI 3
            4: ACUITY_DISTRIBUTION[4] - shift * 1.0,  # Less ESI 4
            5: ACUITY_DISTRIBUTION[5] - shift * 1.0,  # Less ESI 5
        }

    def apply(self, nodes=None) -> None:
        """
        Apply surge scenario.

        For surge, the main effect is on arrival rate, not nodes.
        This method is here for interface consistency.

        Args:
            nodes: Not used for surge scenario
        """
        pass  # Surge modifies arrival rate, not nodes

    def get_description(self) -> str:
        """Get human-readable description of scenario settings."""
        pct = int(self._config.surge_percentage * 100)
        return f"Surge: +{pct}% arrival rate"
