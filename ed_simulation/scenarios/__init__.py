"""
ED Simulation Scenarios

High-value scenario presets for common ED operational challenges.
Each scenario modifies the base simulation configuration to model
specific situations.
"""

from .boarding import BoardingScenario
from .vertical_track import VerticalTrackScenario
from .staffing_adjustment import StaffingAdjustmentScenario
from .surge import SurgeScenario

__all__ = [
    "BoardingScenario",
    "VerticalTrackScenario",
    "StaffingAdjustmentScenario",
    "SurgeScenario",
]

# Scenario registry for easy access
SCENARIOS = {
    "boarding": BoardingScenario,
    "vertical_track": VerticalTrackScenario,
    "staffing_adjustment": StaffingAdjustmentScenario,
    "surge": SurgeScenario,
}
