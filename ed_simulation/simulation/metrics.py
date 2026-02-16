"""
Standard ED Metrics and KPI Calculation.

This module provides calculation of nationally-tracked ED metrics
aligned with CMS, TJC (The Joint Commission), and other reporting
standards.

Key Metrics Tracked:
- ED-1b: Median Time from ED Arrival to ED Departure for Discharged Patients
- ED-2b: Median Time from ED Arrival to ED Provider Contact
- LWBS Rate: Left Without Being Seen percentage
- LBTC Rate: Left Before Treatment Complete percentage
- Door-to-Bed Time
- Provider-to-Disposition Time

All times are in minutes unless otherwise specified.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from statistics import median, mean, stdev, quantiles

from ..core.enums import Acuity, NodeType, DispositionType
from ..core.patient import Patient


def safe_median(values: List[float]) -> Optional[float]:
    """Calculate median, returning None for empty lists."""
    if not values:
        return None
    return median(values)


def safe_mean(values: List[float]) -> Optional[float]:
    """Calculate mean, returning None for empty lists."""
    if not values:
        return None
    return mean(values)


def safe_percentile(values: List[float], p: int) -> Optional[float]:
    """
    Calculate percentile, returning None for insufficient data.

    Args:
        values: List of values
        p: Percentile (0-100)

    Returns:
        Percentile value or None
    """
    if len(values) < 2:
        return values[0] if values else None
    # quantiles returns n-1 cut points for n quantiles
    # For 90th percentile, we need 10 quantiles and take the 9th cut point
    try:
        n = 100 // (100 - p) if p < 100 else 100
        q = quantiles(values, n=n)
        return q[-1] if q else None
    except Exception:
        return max(values) if values else None


@dataclass
class EDMetrics:
    """
    Standard ED performance metrics.

    All times are in minutes. Rates are decimals (0.0 to 1.0).

    Attributes:
        total_patients: Total patients who arrived
        completed_patients: Patients who completed treatment and were discharged
        lwbs_count: Patients who left without being seen
        lbtc_count: Patients who left before treatment complete
        still_in_ed: Patients still being treated

        median_los: Median length of stay (ED-1b equivalent)
        percentile_90_los: 90th percentile LOS
        mean_los: Average length of stay

        median_door_to_provider: Median time to see provider (ED-2b)
        percentile_90_door_to_provider: 90th percentile door-to-provider
        mean_door_to_provider: Average door-to-provider

        median_door_to_bed: Median time to get a bed
        mean_door_to_bed: Average door-to-bed

        lwbs_rate: LWBS percentage (0.0-1.0)
        lbtc_rate: LBTC percentage (0.0-1.0)

        metrics_by_acuity: Breakdown of key metrics by ESI level
    """
    # Volume metrics
    total_patients: int = 0
    completed_patients: int = 0
    lwbs_count: int = 0
    lbtc_count: int = 0
    still_in_ed: int = 0

    # Length of Stay (ED-1b related)
    median_los: Optional[float] = None
    percentile_90_los: Optional[float] = None
    mean_los: Optional[float] = None
    min_los: Optional[float] = None
    max_los: Optional[float] = None

    # Door-to-Provider (ED-2b)
    median_door_to_provider: Optional[float] = None
    percentile_90_door_to_provider: Optional[float] = None
    mean_door_to_provider: Optional[float] = None

    # Door-to-Bed (ED-1b related)
    median_door_to_bed: Optional[float] = None
    percentile_90_door_to_bed: Optional[float] = None
    mean_door_to_bed: Optional[float] = None

    # Door-to-Triage
    median_door_to_triage: Optional[float] = None
    mean_door_to_triage: Optional[float] = None

    # Provider-to-Disposition
    median_provider_to_disposition: Optional[float] = None
    mean_provider_to_disposition: Optional[float] = None

    # Rates
    lwbs_rate: float = 0.0
    lbtc_rate: float = 0.0
    completion_rate: float = 0.0

    # Breakdown by acuity
    metrics_by_acuity: Dict[str, Dict[str, Optional[float]]] = field(
        default_factory=dict
    )

    # Raw data for further analysis
    los_values: List[float] = field(default_factory=list)
    door_to_provider_values: List[float] = field(default_factory=list)
    door_to_bed_values: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert metrics to dictionary for export/display."""
        return {
            "volume": {
                "total_patients": self.total_patients,
                "completed": self.completed_patients,
                "lwbs": self.lwbs_count,
                "lbtc": self.lbtc_count,
                "still_in_ed": self.still_in_ed,
            },
            "length_of_stay_minutes": {
                "median": self.median_los,
                "90th_percentile": self.percentile_90_los,
                "mean": self.mean_los,
                "min": self.min_los,
                "max": self.max_los,
            },
            "door_to_provider_minutes": {
                "median": self.median_door_to_provider,
                "90th_percentile": self.percentile_90_door_to_provider,
                "mean": self.mean_door_to_provider,
            },
            "door_to_bed_minutes": {
                "median": self.median_door_to_bed,
                "90th_percentile": self.percentile_90_door_to_bed,
                "mean": self.mean_door_to_bed,
            },
            "door_to_triage_minutes": {
                "median": self.median_door_to_triage,
                "mean": self.mean_door_to_triage,
            },
            "rates": {
                "lwbs_rate": self.lwbs_rate,
                "lbtc_rate": self.lbtc_rate,
                "completion_rate": self.completion_rate,
            },
            "by_acuity": self.metrics_by_acuity,
        }

    def print_report(self) -> str:
        """Generate a formatted report string."""
        lines = []
        lines.append("=" * 70)
        lines.append("NATIONAL ED METRICS REPORT")
        lines.append("=" * 70)

        # Volume
        lines.append("\nPATIENT VOLUME:")
        lines.append(f"  Total Arrivals:        {self.total_patients}")
        lines.append(f"  Completed/Discharged:  {self.completed_patients}")
        lines.append(f"  LWBS:                  {self.lwbs_count}")
        lines.append(f"  LBTC:                  {self.lbtc_count}")
        lines.append(f"  Still in ED:           {self.still_in_ed}")

        # Key rates
        lines.append("\nKEY RATES:")
        lines.append(f"  Completion Rate:       {self.completion_rate * 100:.1f}%")
        lines.append(f"  LWBS Rate:             {self.lwbs_rate * 100:.1f}%")
        lines.append(f"  LBTC Rate:             {self.lbtc_rate * 100:.1f}%")

        # ED-2b: Door-to-Provider
        lines.append("\nDOOR-TO-PROVIDER (ED-2b):")
        if self.median_door_to_provider is not None:
            lines.append(f"  Median:                {self.median_door_to_provider:.1f} min")
            lines.append(f"  90th Percentile:       {self.percentile_90_door_to_provider:.1f} min" if self.percentile_90_door_to_provider else "  90th Percentile:       N/A")
            lines.append(f"  Mean:                  {self.mean_door_to_provider:.1f} min")
        else:
            lines.append("  No data available")

        # Door-to-Bed
        lines.append("\nDOOR-TO-BED:")
        if self.median_door_to_bed is not None:
            lines.append(f"  Median:                {self.median_door_to_bed:.1f} min")
            lines.append(f"  90th Percentile:       {self.percentile_90_door_to_bed:.1f} min" if self.percentile_90_door_to_bed else "  90th Percentile:       N/A")
            lines.append(f"  Mean:                  {self.mean_door_to_bed:.1f} min")
        else:
            lines.append("  No data available")

        # ED-1b: Length of Stay
        lines.append("\nLENGTH OF STAY (ED-1b):")
        if self.median_los is not None:
            lines.append(f"  Median:                {self.median_los:.1f} min ({self.median_los/60:.1f} hrs)")
            lines.append(f"  90th Percentile:       {self.percentile_90_los:.1f} min ({self.percentile_90_los/60:.1f} hrs)" if self.percentile_90_los else "  90th Percentile:       N/A")
            lines.append(f"  Mean:                  {self.mean_los:.1f} min ({self.mean_los/60:.1f} hrs)")
            lines.append(f"  Range:                 {self.min_los:.1f} - {self.max_los:.1f} min")
        else:
            lines.append("  No data available")

        # By acuity
        if self.metrics_by_acuity:
            lines.append("\nMETRICS BY ACUITY:")
            lines.append("-" * 70)
            lines.append(f"{'Acuity':<10} {'Count':>8} {'D2P Med':>10} {'D2B Med':>10} {'LOS Med':>10} {'LWBS%':>8}")
            lines.append("-" * 70)
            for acuity_name, metrics in self.metrics_by_acuity.items():
                count = metrics.get('count', 0)
                d2p = metrics.get('median_door_to_provider')
                d2b = metrics.get('median_door_to_bed')
                los = metrics.get('median_los')
                lwbs = metrics.get('lwbs_rate', 0) * 100

                d2p_str = f"{d2p:.1f}" if d2p is not None else "N/A"
                d2b_str = f"{d2b:.1f}" if d2b is not None else "N/A"
                los_str = f"{los:.1f}" if los is not None else "N/A"

                lines.append(f"{acuity_name:<10} {count:>8} {d2p_str:>10} {d2b_str:>10} {los_str:>10} {lwbs:>7.1f}%")

        lines.append("=" * 70)
        return "\n".join(lines)


class MetricsCollector:
    """
    Collects and calculates ED metrics from patient data.

    Usage:
        >>> collector = MetricsCollector()
        >>> for patient in completed_patients:
        ...     collector.add_patient(patient)
        >>> metrics = collector.calculate()
        >>> print(metrics.print_report())
    """

    def __init__(self):
        """Initialize the metrics collector."""
        self._patients: List[Patient] = []

    def add_patient(self, patient: Patient) -> None:
        """
        Add a patient to the metrics collection.

        Args:
            patient: Patient to add
        """
        self._patients.append(patient)

    def add_patients(self, patients: List[Patient]) -> None:
        """
        Add multiple patients to the metrics collection.

        Args:
            patients: List of patients to add
        """
        self._patients.extend(patients)

    def clear(self) -> None:
        """Clear all collected patients."""
        self._patients.clear()

    def calculate(self, warmup_minutes: float = 0.0) -> EDMetrics:
        """
        Calculate all ED metrics from collected patients.

        Args:
            warmup_minutes: Warmup period in minutes. Patients arriving before
                          this time are excluded from metrics.

        Returns:
            EDMetrics dataclass with all calculated values
        """
        metrics = EDMetrics()

        if not self._patients:
            return metrics

        # Filter out patients that arrived during warmup period
        filtered_patients = [
            p for p in self._patients
            if p.arrival_time >= warmup_minutes
        ]

        if not filtered_patients:
            return metrics

        # Categorize patients
        completed = []
        lwbs = []
        lbtc = []
        still_in_ed = []

        for p in filtered_patients:
            if p.disposition == DispositionType.DISCHARGE_HOME:
                completed.append(p)
            elif p.is_lwbs and not p.seen_by_provider:
                lwbs.append(p)
            elif p.is_lbtc:
                lbtc.append(p)
            elif p.disposition is None:
                still_in_ed.append(p)
            else:
                # Other dispositions (AMA, Transfer, etc.) - count as completed
                completed.append(p)

        # Volume metrics (using filtered patients)
        metrics.total_patients = len(filtered_patients)
        metrics.completed_patients = len(completed)
        metrics.lwbs_count = len(lwbs)
        metrics.lbtc_count = len(lbtc)
        metrics.still_in_ed = len(still_in_ed)

        # Rates
        if metrics.total_patients > 0:
            metrics.lwbs_rate = metrics.lwbs_count / metrics.total_patients
            metrics.lbtc_rate = metrics.lbtc_count / metrics.total_patients
            metrics.completion_rate = metrics.completed_patients / metrics.total_patients

        # Collect metric values from completed patients
        los_values = []
        d2p_values = []
        d2b_values = []
        d2t_values = []
        p2d_values = []

        for p in completed:
            if p.length_of_stay is not None:
                los_values.append(p.length_of_stay)
            if p.door_to_provider is not None:
                d2p_values.append(p.door_to_provider)
            if p.door_to_bed is not None:
                d2b_values.append(p.door_to_bed)
            if p.door_to_triage is not None:
                d2t_values.append(p.door_to_triage)
            if p.provider_to_disposition is not None:
                p2d_values.append(p.provider_to_disposition)

        # Store raw values
        metrics.los_values = los_values
        metrics.door_to_provider_values = d2p_values
        metrics.door_to_bed_values = d2b_values

        # Length of Stay
        if los_values:
            metrics.median_los = safe_median(los_values)
            metrics.percentile_90_los = safe_percentile(los_values, 90)
            metrics.mean_los = safe_mean(los_values)
            metrics.min_los = min(los_values)
            metrics.max_los = max(los_values)

        # Door-to-Provider
        if d2p_values:
            metrics.median_door_to_provider = safe_median(d2p_values)
            metrics.percentile_90_door_to_provider = safe_percentile(d2p_values, 90)
            metrics.mean_door_to_provider = safe_mean(d2p_values)

        # Door-to-Bed
        if d2b_values:
            metrics.median_door_to_bed = safe_median(d2b_values)
            metrics.percentile_90_door_to_bed = safe_percentile(d2b_values, 90)
            metrics.mean_door_to_bed = safe_mean(d2b_values)

        # Door-to-Triage
        if d2t_values:
            metrics.median_door_to_triage = safe_median(d2t_values)
            metrics.mean_door_to_triage = safe_mean(d2t_values)

        # Provider-to-Disposition
        if p2d_values:
            metrics.median_provider_to_disposition = safe_median(p2d_values)
            metrics.mean_provider_to_disposition = safe_mean(p2d_values)

        # Calculate metrics by acuity
        metrics.metrics_by_acuity = self._calculate_by_acuity(completed, lwbs)

        return metrics

    def _calculate_by_acuity(
        self,
        completed: List[Patient],
        lwbs: List[Patient]
    ) -> Dict[str, Dict[str, Optional[float]]]:
        """
        Calculate metrics broken down by acuity level.

        Args:
            completed: List of completed patients
            lwbs: List of LWBS patients

        Returns:
            Dict mapping acuity name to metrics dict
        """
        result = {}

        for acuity in Acuity:
            acuity_completed = [p for p in completed if p.acuity == acuity]
            acuity_lwbs = [p for p in lwbs if p.acuity == acuity]
            total = len(acuity_completed) + len(acuity_lwbs)

            if total == 0:
                continue

            # Collect values
            los_vals = [p.length_of_stay for p in acuity_completed if p.length_of_stay]
            d2p_vals = [p.door_to_provider for p in acuity_completed if p.door_to_provider]
            d2b_vals = [p.door_to_bed for p in acuity_completed if p.door_to_bed]

            result[acuity.name] = {
                "count": total,
                "completed": len(acuity_completed),
                "lwbs": len(acuity_lwbs),
                "lwbs_rate": len(acuity_lwbs) / total if total > 0 else 0,
                "median_los": safe_median(los_vals),
                "mean_los": safe_mean(los_vals),
                "median_door_to_provider": safe_median(d2p_vals),
                "median_door_to_bed": safe_median(d2b_vals),
            }

        return result
