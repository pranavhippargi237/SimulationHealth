"""Configuration module for ED simulation parameters."""

from .defaults import (
    ARRIVAL_DEFAULTS,
    ACUITY_DISTRIBUTION,
    SERVICE_TIME_DEFAULTS,
    NODE_CAPACITY_DEFAULTS,
    LWBS_DEFAULTS,
    SIMULATION_DEFAULTS,
)
from .loader import EDConfig, load_config, save_config, merge_with_defaults

__all__ = [
    "ARRIVAL_DEFAULTS",
    "ACUITY_DISTRIBUTION",
    "SERVICE_TIME_DEFAULTS",
    "NODE_CAPACITY_DEFAULTS",
    "LWBS_DEFAULTS",
    "SIMULATION_DEFAULTS",
    "EDConfig",
    "load_config",
    "save_config",
    "merge_with_defaults",
]
