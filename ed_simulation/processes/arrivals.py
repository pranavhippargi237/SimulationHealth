"""
Patient arrival generator using Poisson process.

This module provides:
- Poisson-distributed patient arrivals
- Time-of-day variation (hourly multipliers)
- Acuity assignment based on ESI distribution
- Configurable arrival rates

The Poisson process is standard for modeling ED arrivals as it captures:
- Random, independent arrivals
- Memoryless property (next arrival independent of past)
- Realistic variability in inter-arrival times
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Dict, Optional, Generator, Callable, List

import simpy

from ..core.enums import Acuity
from ..core.patient import Patient, PatientConfig
from ..config.defaults import ARRIVAL_DEFAULTS, ACUITY_DISTRIBUTION


@dataclass
class ArrivalConfig:
    """
    Configuration for patient arrival process.

    Attributes:
        mean_arrival_rate: Average arrivals per hour
        acuity_distribution: Probability distribution for ESI levels
        hourly_multipliers: Time-of-day variation factors
        use_time_variation: Whether to apply hourly multipliers
    """
    # Average arrival rate (patients per hour)
    mean_arrival_rate: float = ARRIVAL_DEFAULTS["mean_arrival_rate"]

    # ESI acuity distribution (must sum to 1.0)
    acuity_distribution: Dict[Acuity, float] = field(default_factory=lambda: {
        Acuity.ESI_1: ACUITY_DISTRIBUTION[1],
        Acuity.ESI_2: ACUITY_DISTRIBUTION[2],
        Acuity.ESI_3: ACUITY_DISTRIBUTION[3],
        Acuity.ESI_4: ACUITY_DISTRIBUTION[4],
        Acuity.ESI_5: ACUITY_DISTRIBUTION[5],
    })

    # Hourly multipliers (hour 0-23 -> multiplier)
    hourly_multipliers: Dict[int, float] = field(
        default_factory=lambda: ARRIVAL_DEFAULTS["hourly_multipliers"]
    )

    # Whether to apply time-of-day variation
    use_time_variation: bool = True

    # Patient configuration to pass to created patients
    patient_config: Optional[PatientConfig] = None


class ArrivalGenerator:
    """
    Generates patient arrivals using a Poisson process.

    The generator creates Patient instances at random intervals
    following an exponential distribution (which produces Poisson
    arrivals when counting over fixed intervals).

    Usage:
        >>> env = simpy.Environment()
        >>> config = ArrivalConfig(mean_arrival_rate=3.0)
        >>> generator = ArrivalGenerator(env, config)
        >>> env.process(generator.run(on_arrival=process_patient))
        >>> env.run(until=240)  # Run for 4 hours

    Attributes:
        env: SimPy environment
        config: Arrival configuration
        patients_generated: Count of patients created
    """

    def __init__(
        self,
        env: simpy.Environment,
        config: Optional[ArrivalConfig] = None,
        rng: Optional[random.Random] = None,
    ):
        """
        Initialize the arrival generator.

        Args:
            env: SimPy environment
            config: Arrival configuration
            rng: Random number generator for reproducibility
        """
        self._env = env
        self._config = config or ArrivalConfig()
        self._rng = rng or random.Random()

        # Tracking
        self._patients_generated: int = 0
        self._arrivals_by_hour: Dict[int, int] = {h: 0 for h in range(24)}
        self._arrivals_by_acuity: Dict[Acuity, int] = {a: 0 for a in Acuity}

        # Pre-compute cumulative distribution for acuity selection
        self._acuity_cdf = self._build_acuity_cdf()

    def _build_acuity_cdf(self) -> List[tuple]:
        """Build cumulative distribution function for acuity selection."""
        cdf = []
        cumulative = 0.0
        for acuity in Acuity:
            prob = self._config.acuity_distribution.get(acuity, 0.0)
            cumulative += prob
            cdf.append((cumulative, acuity))
        return cdf

    def _select_acuity(self) -> Acuity:
        """
        Select acuity level based on configured distribution.

        Returns:
            Selected Acuity level
        """
        r = self._rng.random()
        for threshold, acuity in self._acuity_cdf:
            if r <= threshold:
                return acuity
        # Fallback (shouldn't happen if distribution sums to 1)
        return Acuity.ESI_3

    def _get_current_arrival_rate(self) -> float:
        """
        Get arrival rate adjusted for current time of day.

        Returns:
            Adjusted arrival rate (patients per hour)
        """
        base_rate = self._config.mean_arrival_rate

        if not self._config.use_time_variation:
            return base_rate

        # Get current hour (simulation time in minutes)
        hour = int((self._env.now / 60) % 24)
        multiplier = self._config.hourly_multipliers.get(hour, 1.0)

        return base_rate * multiplier

    def _generate_interarrival_time(self) -> float:
        """
        Generate time until next arrival using exponential distribution.

        Returns:
            Inter-arrival time in minutes
        """
        rate = self._get_current_arrival_rate()

        if rate <= 0:
            return float('inf')  # No arrivals

        # Convert rate from per-hour to per-minute
        rate_per_minute = rate / 60.0

        # Exponential distribution: mean = 1/rate
        return self._rng.expovariate(rate_per_minute)

    def create_patient(self) -> Patient:
        """
        Create a new patient with assigned acuity.

        Returns:
            New Patient instance
        """
        acuity = self._select_acuity()

        patient = Patient(
            env=self._env,
            arrival_time=self._env.now,
            acuity=acuity,
            config=self._config.patient_config,
        )

        # Update tracking
        self._patients_generated += 1
        hour = int((self._env.now / 60) % 24)
        self._arrivals_by_hour[hour] += 1
        self._arrivals_by_acuity[acuity] += 1

        return patient

    def run(
        self,
        on_arrival: Callable[[Patient], Generator],
        until: Optional[float] = None,
    ) -> Generator:
        """
        Run the arrival process.

        This is a SimPy generator that creates patients at random
        intervals and calls the on_arrival callback for each.

        Args:
            on_arrival: Callback that takes a Patient and returns a
                       SimPy generator (the patient's journey process)
            until: Optional end time (in minutes). If None, runs forever.

        Yields:
            SimPy timeout events for inter-arrival times
        """
        while True:
            # Check if we should stop
            if until is not None and self._env.now >= until:
                break

            # Generate inter-arrival time
            interarrival = self._generate_interarrival_time()

            # Don't schedule past end time
            if until is not None and self._env.now + interarrival > until:
                break

            # Wait for next arrival
            yield self._env.timeout(interarrival)

            # Create patient and start their journey
            patient = self.create_patient()
            self._env.process(on_arrival(patient))

    @property
    def patients_generated(self) -> int:
        """Total number of patients generated."""
        return self._patients_generated

    @property
    def arrivals_by_hour(self) -> Dict[int, int]:
        """Arrivals grouped by hour of day."""
        return self._arrivals_by_hour.copy()

    @property
    def arrivals_by_acuity(self) -> Dict[Acuity, int]:
        """Arrivals grouped by acuity level."""
        return self._arrivals_by_acuity.copy()

    def get_statistics(self) -> Dict:
        """
        Get arrival statistics.

        Returns:
            Dict with arrival metrics
        """
        total = self._patients_generated
        duration_hours = self._env.now / 60 if self._env.now > 0 else 1

        return {
            "total_patients": total,
            "duration_hours": duration_hours,
            "average_rate_per_hour": total / duration_hours if duration_hours > 0 else 0,
            "arrivals_by_hour": self._arrivals_by_hour,
            "arrivals_by_acuity": {
                a.name: count for a, count in self._arrivals_by_acuity.items()
            },
            "acuity_percentages": {
                a.name: (count / total * 100) if total > 0 else 0
                for a, count in self._arrivals_by_acuity.items()
            },
        }
