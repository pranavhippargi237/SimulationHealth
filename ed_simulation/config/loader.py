"""
Configuration loader and saver for ED simulation.

Supports loading and saving ED configurations from YAML files,
allowing users to customize the simulation for their specific ED.
"""

from __future__ import annotations
import yaml
from typing import Dict, Any, Optional
from pathlib import Path

from .defaults import (
    NODE_CAPACITY_DEFAULTS,
    SERVICE_TIME_DEFAULTS,
    ACUITY_DISTRIBUTION,
    ARRIVAL_DEFAULTS,
    LWBS_DEFAULTS,
    SIMULATION_DEFAULTS,
)


class EDConfig:
    """ED configuration container."""
    
    def __init__(
        self,
        node_capacities: Optional[Dict[str, int]] = None,
        service_times: Optional[Dict[str, Dict[int, Dict[str, float]]]] = None,
        acuity_distribution: Optional[Dict[int, float]] = None,
        arrival_rate: Optional[float] = None,
        lwbs_config: Optional[Dict[str, Any]] = None,
        ed_name: Optional[str] = None,
        pathways: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize ED configuration.
        
        Args:
            node_capacities: Node capacity overrides
            service_times: Service time overrides
            acuity_distribution: Acuity distribution overrides
            arrival_rate: Average arrival rate (patients/hour)
            lwbs_config: LWBS configuration overrides
            ed_name: Name/identifier for this ED
        """
        self.ed_name = ed_name or "Custom ED"
        self.node_capacities = node_capacities or {}
        self.service_times = service_times or {}
        self.acuity_distribution = acuity_distribution or {}
        self.arrival_rate = arrival_rate
        self.lwbs_config = lwbs_config or {}
        self.pathways = pathways or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "ed_name": self.ed_name,
            "node_capacities": self.node_capacities,
            "service_times": self.service_times,
            "acuity_distribution": self.acuity_distribution,
            "arrival_rate": self.arrival_rate,
            "lwbs_config": self.lwbs_config,
            "pathways": self.pathways,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EDConfig":
        """Create configuration from dictionary."""
        return cls(
            ed_name=data.get("ed_name"),
            node_capacities=data.get("node_capacities", {}),
            service_times=data.get("service_times", {}),
            acuity_distribution=data.get("acuity_distribution", {}),
            arrival_rate=data.get("arrival_rate"),
            lwbs_config=data.get("lwbs_config", {}),
            pathways=data.get("pathways", {}),
        )
    
    @classmethod
    def from_defaults(cls) -> "EDConfig":
        """Create configuration from defaults."""
        return cls(
            ed_name="Default ED",
            node_capacities=NODE_CAPACITY_DEFAULTS.copy(),
            service_times=SERVICE_TIME_DEFAULTS.copy(),
            acuity_distribution=ACUITY_DISTRIBUTION.copy(),
            arrival_rate=ARRIVAL_DEFAULTS["mean_arrival_rate"],
            lwbs_config=LWBS_DEFAULTS.copy(),
        )


def load_config(file_path: str) -> EDConfig:
    """
    Load ED configuration from YAML file.
    
    Args:
        file_path: Path to YAML configuration file
    
    Returns:
        EDConfig object
    """
    with open(file_path, 'r') as f:
        data = yaml.safe_load(f)
    
    return EDConfig.from_dict(data)


def save_config(config: EDConfig, file_path: str) -> None:
    """
    Save ED configuration to YAML file.
    
    Args:
        config: EDConfig to save
        file_path: Path where to save the configuration
    """
    data = config.to_dict()
    
    with open(file_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def merge_with_defaults(config: EDConfig) -> EDConfig:
    """
    Merge custom configuration with defaults.
    
    Only overrides specified values, keeps defaults for unspecified ones.
    
    Args:
        config: Custom configuration
    
    Returns:
        Merged configuration
    """
    defaults = EDConfig.from_defaults()
    
    # Merge node capacities
    merged_capacities = defaults.node_capacities.copy()
    merged_capacities.update(config.node_capacities)
    
    # Merge service times (deep merge)
    merged_service_times = defaults.service_times.copy()
    for node, times in config.service_times.items():
        if node in merged_service_times:
            merged_service_times[node].update(times)
        else:
            merged_service_times[node] = times
    
    # Merge acuity distribution
    merged_acuity = defaults.acuity_distribution.copy()
    merged_acuity.update(config.acuity_distribution)
    
    # Merge LWBS config
    merged_lwbs = defaults.lwbs_config.copy()
    merged_lwbs.update(config.lwbs_config)
    
    return EDConfig(
        ed_name=config.ed_name or defaults.ed_name,
        node_capacities=merged_capacities,
        service_times=merged_service_times,
        acuity_distribution=merged_acuity,
        arrival_rate=config.arrival_rate or defaults.arrival_rate,
        lwbs_config=merged_lwbs,
    )

