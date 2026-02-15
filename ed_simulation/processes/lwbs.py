"""
Left Without Being Seen (LWBS) monitoring and handling.

This module provides:
- LWBS probability models based on research literature
- Continuous monitoring of waiting patients
- Event-driven LWBS detection
- Analytics and reporting

Research basis:
- LWBS rates typically 2-5% baseline
- Increases with wait time (especially after 30-60 minutes)
- Lower acuity patients more likely to leave
- Volume-dependent effects

References:
- BMC Emergency Medicine: ML prediction models
- Scientific Reports: Feature importance analysis
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from enum import Enum

import simpy

from ..core.enums import Acuity
from ..core.patient import Patient
from ..config.defaults import LWBS_DEFAULTS


class LWBSModel(Enum):
    """
    Available LWBS probability models.
    """
    LINEAR = "linear"           # Linear increase with wait time
    EXPONENTIAL = "exponential" # Exponential increase
    LOGISTIC = "logistic"       # S-curve (logistic function)
    PIECEWISE = "piecewise"     # Different rates for different thresholds


@dataclass
class LWBSConfig:
    """
    Configuration for LWBS probability calculations.

    All probabilities are per evaluation (not cumulative).
    """
    # Base probability (at zero wait time)
    base_probability: float = LWBS_DEFAULTS["base_probability"]

    # Maximum probability cap
    max_probability: float = LWBS_DEFAULTS["max_probability"]

    # Time parameters (in minutes)
    threshold_minutes: float = LWBS_DEFAULTS["threshold_minutes"]
    half_life_minutes: float = LWBS_DEFAULTS["half_life_minutes"]

    # Model selection
    model: LWBSModel = LWBSModel.LOGISTIC

    # Acuity multipliers
    acuity_multipliers: Dict[Acuity, float] = field(default_factory=lambda: {
        Acuity.ESI_1: 0.0,    # Never leave
        Acuity.ESI_2: 0.05,   # Very unlikely (5% of base)
        Acuity.ESI_3: 0.4,    # Unlikely (40% of base)
        Acuity.ESI_4: 1.0,    # Baseline
        Acuity.ESI_5: 1.8,    # More likely (180% of base)
    })

    # Volume adjustment (optional)
    enable_volume_adjustment: bool = False
    volume_percentile_threshold: float = 0.7  # 70th percentile
    volume_multiplier: float = 1.5  # Applied above threshold

    # Time-of-day adjustment (optional)
    enable_time_of_day_adjustment: bool = False
    evening_multiplier: float = 1.2  # 6pm-midnight
    night_multiplier: float = 0.8    # midnight-6am


class LWBSProbabilityCalculator:
    """
    Calculates LWBS probability based on configured model.

    Implements multiple probability models from research literature:

    1. LINEAR: P = base + rate * max(0, wait - threshold)
    2. EXPONENTIAL: P = base * exp(k * max(0, wait - threshold))
    3. LOGISTIC: P = max_prob / (1 + exp(-k * (wait - midpoint)))
    4. PIECEWISE: Different rates for <30min, 30-60min, >60min
    """

    def __init__(self, config: Optional[LWBSConfig] = None):
        """
        Initialize calculator with configuration.

        Args:
            config: LWBS configuration parameters
        """
        self._config = config or LWBSConfig()

    def calculate(
        self,
        wait_time: float,
        acuity: Acuity,
        current_volume_percentile: Optional[float] = None,
        hour_of_day: Optional[int] = None,
    ) -> float:
        """
        Calculate LWBS probability for given conditions.

        Args:
            wait_time: Current wait time in minutes
            acuity: Patient acuity level
            current_volume_percentile: Optional volume percentile (0-1)
            hour_of_day: Optional hour (0-23) for time adjustment

        Returns:
            Probability in range [0, 1]
        """
        # ESI 1 patients never leave
        if acuity == Acuity.ESI_1:
            return 0.0

        # Calculate base probability from model
        if self._config.model == LWBSModel.LINEAR:
            base_prob = self._linear_model(wait_time)
        elif self._config.model == LWBSModel.EXPONENTIAL:
            base_prob = self._exponential_model(wait_time)
        elif self._config.model == LWBSModel.LOGISTIC:
            base_prob = self._logistic_model(wait_time)
        elif self._config.model == LWBSModel.PIECEWISE:
            base_prob = self._piecewise_model(wait_time)
        else:
            base_prob = self._config.base_probability

        # Apply acuity multiplier
        acuity_mult = self._config.acuity_multipliers.get(acuity, 1.0)
        prob = base_prob * acuity_mult

        # Apply volume adjustment if enabled
        if (self._config.enable_volume_adjustment
                and current_volume_percentile is not None
                and current_volume_percentile > self._config.volume_percentile_threshold):
            prob *= self._config.volume_multiplier

        # Apply time-of-day adjustment if enabled
        if self._config.enable_time_of_day_adjustment and hour_of_day is not None:
            if 18 <= hour_of_day < 24:  # Evening
                prob *= self._config.evening_multiplier
            elif 0 <= hour_of_day < 6:  # Night
                prob *= self._config.night_multiplier

        # Cap probability
        return max(0.0, min(self._config.max_probability, prob))

    def _linear_model(self, wait_time: float) -> float:
        """
        Linear increase model.

        P(LWBS) = base + rate * max(0, wait - threshold)
        """
        if wait_time <= self._config.threshold_minutes:
            return self._config.base_probability

        excess = wait_time - self._config.threshold_minutes
        # Rate calculated to reach max at 60 min after threshold
        rate = (self._config.max_probability - self._config.base_probability) / 60.0
        return self._config.base_probability + rate * excess

    def _exponential_model(self, wait_time: float) -> float:
        """
        Exponential increase model.

        P(LWBS) = base * exp(k * max(0, wait - threshold))
        where k is derived from half_life
        """
        if wait_time <= self._config.threshold_minutes:
            return self._config.base_probability

        excess = wait_time - self._config.threshold_minutes
        k = math.log(2) / self._config.half_life_minutes
        return self._config.base_probability * math.exp(k * excess)

    def _logistic_model(self, wait_time: float) -> float:
        """
        Logistic (S-curve) model.

        P(LWBS) = max_prob / (1 + exp(-k * (wait - midpoint)))

        This model provides:
        - Low probability for short waits
        - Rapid increase around midpoint
        - Saturation at max probability
        """
        midpoint = self._config.threshold_minutes + self._config.half_life_minutes
        k = 0.1  # Steepness parameter

        exponent = -k * (wait_time - midpoint)
        # Clamp exponent to avoid overflow
        exponent = max(-20, min(20, exponent))

        return self._config.max_probability / (1 + math.exp(exponent))

    def _piecewise_model(self, wait_time: float) -> float:
        """
        Piecewise model with different rates for different time ranges.

        Based on research showing LWBS rates increase non-linearly:
        - <30 min: baseline rate
        - 30-60 min: moderate increase
        - >60 min: rapid increase
        """
        if wait_time < 30:
            return self._config.base_probability
        elif wait_time < 60:
            # Linear interpolation from base to 2x base
            t = (wait_time - 30) / 30
            return self._config.base_probability * (1 + t)
        elif wait_time < 120:
            # Steeper increase
            t = (wait_time - 60) / 60
            return self._config.base_probability * (2 + 3 * t)
        else:
            # Near maximum
            return self._config.max_probability * 0.9


@dataclass
class LWBSEvent:
    """
    Record of an LWBS event for analysis.
    """
    patient_id: str
    acuity: Acuity
    wait_time: float
    time_in_ed: float
    node_type: str
    simulation_time: float
    probability_at_decision: float


class LWBSMonitor:
    """
    Monitors waiting patients and tracks LWBS events.

    This class:
    - Maintains count of active patients being monitored
    - Calculates LWBS probabilities
    - Records LWBS events for analysis
    - Provides LWBS rate metrics
    """

    def __init__(
        self,
        env: simpy.Environment,
        config: Optional[LWBSConfig] = None,
    ):
        """
        Initialize LWBS monitor.

        Args:
            env: SimPy environment
            config: LWBS configuration
        """
        self._env = env
        self._config = config or LWBSConfig()
        self._calculator = LWBSProbabilityCalculator(self._config)

        # Event tracking
        self._lwbs_events: List[LWBSEvent] = []
        self._total_patients: int = 0

        # Callbacks
        self._on_lwbs_callbacks: List[Callable[[LWBSEvent], None]] = []

    def calculate_probability(
        self,
        patient: Patient,
        volume_percentile: Optional[float] = None,
    ) -> float:
        """
        Calculate LWBS probability for a patient.

        Args:
            patient: The patient to evaluate
            volume_percentile: Optional current ED volume percentile

        Returns:
            LWBS probability
        """
        # Get hour of day from simulation time (assuming minutes)
        hour = int((self._env.now / 60) % 24)

        return self._calculator.calculate(
            wait_time=patient.current_wait_time,
            acuity=patient.acuity,
            current_volume_percentile=volume_percentile,
            hour_of_day=hour,
        )

    def record_patient(self) -> None:
        """Record that a patient entered the system."""
        self._total_patients += 1

    def record_lwbs(
        self,
        patient: Patient,
        node_type: str,
        probability: float,
    ) -> None:
        """
        Record an LWBS event.

        Args:
            patient: The patient who left
            node_type: Node where patient was waiting
            probability: Probability at time of decision
        """
        event = LWBSEvent(
            patient_id=patient.id,
            acuity=patient.acuity,
            wait_time=patient.current_wait_time,
            time_in_ed=patient.time_in_ed,
            node_type=node_type,
            simulation_time=self._env.now,
            probability_at_decision=probability,
        )

        self._lwbs_events.append(event)

        # Fire callbacks
        for callback in self._on_lwbs_callbacks:
            callback(event)

    @property
    def lwbs_count(self) -> int:
        """Total LWBS events."""
        return len(self._lwbs_events)

    @property
    def lwbs_rate(self) -> float:
        """Overall LWBS rate."""
        if self._total_patients == 0:
            return 0.0
        return len(self._lwbs_events) / self._total_patients

    @property
    def total_patients(self) -> int:
        """Total patients tracked."""
        return self._total_patients

    @property
    def events(self) -> List[LWBSEvent]:
        """Get all LWBS events."""
        return self._lwbs_events.copy()

    def get_lwbs_by_acuity(self) -> Dict[Acuity, int]:
        """Get LWBS count by acuity level."""
        result = {a: 0 for a in Acuity}
        for event in self._lwbs_events:
            result[event.acuity] += 1
        return result

    def get_average_wait_at_lwbs(self) -> float:
        """Get average wait time when patients LWBS."""
        if not self._lwbs_events:
            return 0.0
        return sum(e.wait_time for e in self._lwbs_events) / len(self._lwbs_events)

    def on_lwbs(self, callback: Callable[[LWBSEvent], None]) -> None:
        """Register callback for LWBS events."""
        self._on_lwbs_callbacks.append(callback)

    def reset(self) -> None:
        """Reset all tracking. Useful after warmup period."""
        self._lwbs_events.clear()
        self._total_patients = 0
