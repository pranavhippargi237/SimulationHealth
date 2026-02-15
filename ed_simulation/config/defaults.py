"""
Default simulation parameters based on literature and industry standards.

These defaults provide a starting point for ED simulation. All values
can be overridden via configuration files or the Streamlit UI.

References:
- ESI Implementation Handbook (5th Edition)
- CMS Hospital Outpatient Quality Reporting metrics
- Published ED discrete-event simulation studies
"""

from typing import Dict, Any


# =============================================================================
# Arrival Parameters
# =============================================================================

ARRIVAL_DEFAULTS: Dict[str, Any] = {
    # Average arrival rate (patients per hour)
    # Typical range: 2-4 for medium ED
    "mean_arrival_rate": 3.0,

    # Arrival process distribution
    "distribution": "poisson",

    # Time-of-day variation (multipliers by hour 0-23)
    "hourly_multipliers": {
        0: 0.5, 1: 0.4, 2: 0.3, 3: 0.3, 4: 0.3, 5: 0.4,
        6: 0.6, 7: 0.8, 8: 1.0, 9: 1.2, 10: 1.3, 11: 1.4,
        12: 1.3, 13: 1.2, 14: 1.2, 15: 1.3, 16: 1.4, 17: 1.5,
        18: 1.4, 19: 1.3, 20: 1.1, 21: 0.9, 22: 0.7, 23: 0.6,
    },
}


# =============================================================================
# Acuity Distribution (ESI Levels)
# =============================================================================

# Keys are ESI levels (1-5), values are probabilities
ACUITY_DISTRIBUTION: Dict[int, float] = {
    1: 0.01,   # 1% - Immediate, life-threatening
    2: 0.10,   # 10% - Emergent
    3: 0.35,   # 35% - Urgent
    4: 0.35,   # 35% - Less urgent
    5: 0.19,   # 19% - Non-urgent
}


# =============================================================================
# Service Time Defaults (in minutes)
# =============================================================================

# Service times by node type and acuity level
# Format: {node_name: {acuity: {"mean": X, "std_dev": Y}}}
SERVICE_TIME_DEFAULTS: Dict[str, Dict[int, Dict[str, float]]] = {
    "TRIAGE": {
        1: {"mean": 2.0, "std_dev": 1.0},   # Rapid triage for critical
        2: {"mean": 3.0, "std_dev": 1.5},
        3: {"mean": 5.0, "std_dev": 2.0},
        4: {"mean": 5.0, "std_dev": 2.0},
        5: {"mean": 4.0, "std_dev": 1.5},
    },
    "REGISTRATION": {
        1: {"mean": 2.0, "std_dev": 1.0},   # Often bypassed for critical
        2: {"mean": 3.0, "std_dev": 1.5},
        3: {"mean": 5.0, "std_dev": 2.0},
        4: {"mean": 5.0, "std_dev": 2.0},
        5: {"mean": 5.0, "std_dev": 2.0},
    },
    "BED_ASSIGNMENT": {
        1: {"mean": 1.0, "std_dev": 0.5},   # Immediate for critical
        2: {"mean": 5.0, "std_dev": 3.0},
        3: {"mean": 15.0, "std_dev": 10.0},
        4: {"mean": 30.0, "std_dev": 20.0},
        5: {"mean": 45.0, "std_dev": 25.0},
    },
    "PROVIDER_ASSESSMENT": {
        1: {"mean": 30.0, "std_dev": 15.0},  # Extended assessment
        2: {"mean": 25.0, "std_dev": 12.0},
        3: {"mean": 20.0, "std_dev": 10.0},
        4: {"mean": 15.0, "std_dev": 7.0},
        5: {"mean": 10.0, "std_dev": 5.0},
    },
    "DIAGNOSTICS": {
        1: {"mean": 45.0, "std_dev": 20.0},  # Multiple tests
        2: {"mean": 40.0, "std_dev": 18.0},
        3: {"mean": 35.0, "std_dev": 15.0},
        4: {"mean": 20.0, "std_dev": 10.0},  # Single test
        5: {"mean": 0.0, "std_dev": 0.0},    # No diagnostics typically
    },
    "TREATMENT": {
        1: {"mean": 60.0, "std_dev": 30.0},  # Complex treatment
        2: {"mean": 45.0, "std_dev": 25.0},
        3: {"mean": 30.0, "std_dev": 15.0},
        4: {"mean": 15.0, "std_dev": 8.0},
        5: {"mean": 10.0, "std_dev": 5.0},
    },
    "DISPOSITION": {
        1: {"mean": 20.0, "std_dev": 10.0},  # Discharge planning
        2: {"mean": 15.0, "std_dev": 8.0},
        3: {"mean": 12.0, "std_dev": 6.0},
        4: {"mean": 8.0, "std_dev": 4.0},
        5: {"mean": 5.0, "std_dev": 3.0},
    },
}


# =============================================================================
# Node Capacity Defaults
# =============================================================================

NODE_CAPACITY_DEFAULTS: Dict[str, int] = {
    "TRIAGE": 2,              # 2 triage nurses
    "REGISTRATION": 2,        # 2 registration clerks
    "BED_ASSIGNMENT": 20,     # 20 ED beds total
    "PROVIDER_ASSESSMENT": 4, # 4 providers (physicians/NPs)
    "DIAGNOSTICS": 5,         # Combined lab/imaging capacity
    "TREATMENT": 20,          # Same as beds (treatment occurs in bed)
    "DISPOSITION": 4,         # Providers doing discharge
}


# =============================================================================
# LWBS (Left Without Being Seen) Defaults
# =============================================================================

LWBS_DEFAULTS: Dict[str, Any] = {
    # Base probability at zero wait time
    "base_probability": 0.02,       # 2%

    # Maximum probability cap
    "max_probability": 0.20,        # 20%

    # Wait time threshold before probability increases (minutes)
    "threshold_minutes": 30.0,

    # For exponential model: time for probability to double
    "half_life_minutes": 60.0,

    # Probability model type
    "model": "logistic",  # Options: linear, exponential, logistic, piecewise

    # Acuity multipliers (applied to base probability)
    # Lower acuity (higher ESI number) = more likely to leave
    "acuity_multipliers": {
        1: 0.0,    # ESI 1: Never leave - life threatening
        2: 0.05,   # ESI 2: Very unlikely to leave
        3: 0.4,    # ESI 3: Unlikely
        4: 1.0,    # ESI 4: Baseline
        5: 1.8,    # ESI 5: More likely to leave
    },

    # How often to check LWBS while waiting (minutes)
    "check_interval": 5.0,
}


# =============================================================================
# Simulation Defaults
# =============================================================================

SIMULATION_DEFAULTS: Dict[str, Any] = {
    "duration_hours": 24,       # Default 24-hour simulation
    "warmup_hours": 4,          # 4-hour warmup period (excluded from stats)
    "replications": 10,         # Number of replications for statistics
    "random_seed": 42,          # For reproducibility
    "time_unit": "minutes",     # Simulation time unit
}
