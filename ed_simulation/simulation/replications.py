"""
Multiple Replications Support for Statistical Confidence.

This module provides functionality to run multiple simulation replications
and aggregate statistics with confidence intervals.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Callable, Any
from statistics import mean, median, stdev
import math

from ..simulation.metrics import EDMetrics, MetricsCollector


@dataclass
class ReplicationResult:
    """Results from a single simulation replication."""
    metrics: EDMetrics
    sim: Any  # EDSimulationWithScenarios object
    description: str
    seed: int
    replication_number: int


@dataclass
class AggregatedMetrics:
    """
    Aggregated metrics across multiple replications.
    
    For each metric, provides:
    - Mean (average across replications)
    - Median
    - Standard deviation
    - Min/Max
    - 95% confidence interval (if >= 3 replications)
    """
    # Volume metrics
    total_patients_mean: float = 0.0
    total_patients_std: float = 0.0
    total_patients_ci_lower: Optional[float] = None
    total_patients_ci_upper: Optional[float] = None
    
    completed_patients_mean: float = 0.0
    lwbs_count_mean: float = 0.0
    lwbs_rate_mean: float = 0.0
    lwbs_rate_std: float = 0.0
    lwbs_rate_ci_lower: Optional[float] = None
    lwbs_rate_ci_upper: Optional[float] = None
    
    # LOS metrics
    median_los_mean: Optional[float] = None
    median_los_std: Optional[float] = None
    median_los_ci_lower: Optional[float] = None
    median_los_ci_upper: Optional[float] = None
    
    mean_los_mean: Optional[float] = None
    mean_los_std: Optional[float] = None
    
    # Door-to-Provider metrics
    median_door_to_provider_mean: Optional[float] = None
    median_door_to_provider_std: Optional[float] = None
    median_door_to_provider_ci_lower: Optional[float] = None
    median_door_to_provider_ci_upper: Optional[float] = None
    
    mean_door_to_provider_mean: Optional[float] = None
    
    # Number of replications
    num_replications: int = 0
    
    # Raw values for each replication
    raw_metrics: List[EDMetrics] = field(default_factory=list)


def calculate_confidence_interval(
    values: List[float],
    confidence_level: float = 0.95
) -> Tuple[Optional[float], Optional[float]]:
    """
    Calculate confidence interval for a list of values.
    
    Args:
        values: List of numeric values
        confidence_level: Confidence level (default: 0.95 for 95% CI)
    
    Returns:
        Tuple of (lower_bound, upper_bound) or (None, None) if insufficient data
    """
    if len(values) < 3:
        return None, None
    
    n = len(values)
    sample_mean = mean(values)
    sample_std = stdev(values)
    
    # Use t-distribution for small samples, normal for large
    # For 95% CI, t-value approaches 1.96 for large n
    if n < 30:
        # Approximate t-value for 95% CI (simplified)
        t_value = 2.0  # Conservative estimate
    else:
        t_value = 1.96  # Normal distribution
    
    margin_of_error = t_value * (sample_std / math.sqrt(n))
    
    lower = sample_mean - margin_of_error
    upper = sample_mean + margin_of_error
    
    return lower, upper


def aggregate_metrics(replication_results: List[ReplicationResult]) -> AggregatedMetrics:
    """
    Aggregate metrics across multiple replications.
    
    Args:
        replication_results: List of replication results
    
    Returns:
        AggregatedMetrics with mean, std dev, and confidence intervals
    """
    if not replication_results:
        return AggregatedMetrics()
    
    aggregated = AggregatedMetrics()
    aggregated.num_replications = len(replication_results)
    aggregated.raw_metrics = [r.metrics for r in replication_results]
    
    # Extract values for aggregation
    total_patients_vals = [r.metrics.total_patients for r in replication_results]
    completed_patients_vals = [r.metrics.completed_patients for r in replication_results]
    lwbs_count_vals = [r.metrics.lwbs_count for r in replication_results]
    lwbs_rate_vals = [r.metrics.lwbs_rate for r in replication_results]
    
    median_los_vals = [r.metrics.median_los for r in replication_results if r.metrics.median_los is not None]
    mean_los_vals = [r.metrics.mean_los for r in replication_results if r.metrics.mean_los is not None]
    
    median_d2p_vals = [r.metrics.median_door_to_provider for r in replication_results if r.metrics.median_door_to_provider is not None]
    mean_d2p_vals = [r.metrics.mean_door_to_provider for r in replication_results if r.metrics.mean_door_to_provider is not None]
    
    # Calculate means
    aggregated.total_patients_mean = mean(total_patients_vals)
    aggregated.completed_patients_mean = mean(completed_patients_vals)
    aggregated.lwbs_count_mean = mean(lwbs_count_vals)
    aggregated.lwbs_rate_mean = mean(lwbs_rate_vals)
    
    if median_los_vals:
        aggregated.median_los_mean = mean(median_los_vals)
    if mean_los_vals:
        aggregated.mean_los_mean = mean(mean_los_vals)
    if median_d2p_vals:
        aggregated.median_door_to_provider_mean = mean(median_d2p_vals)
    if mean_d2p_vals:
        aggregated.mean_door_to_provider_mean = mean(mean_d2p_vals)
    
    # Calculate standard deviations
    if len(total_patients_vals) > 1:
        aggregated.total_patients_std = stdev(total_patients_vals)
    if len(lwbs_rate_vals) > 1:
        aggregated.lwbs_rate_std = stdev(lwbs_rate_vals)
    if len(median_los_vals) > 1:
        aggregated.median_los_std = stdev(median_los_vals)
    if len(median_d2p_vals) > 1:
        aggregated.median_door_to_provider_std = stdev(median_d2p_vals)
    
    # Calculate confidence intervals
    if len(total_patients_vals) >= 3:
        aggregated.total_patients_ci_lower, aggregated.total_patients_ci_upper = calculate_confidence_interval(total_patients_vals)
    
    if len(lwbs_rate_vals) >= 3:
        aggregated.lwbs_rate_ci_lower, aggregated.lwbs_rate_ci_upper = calculate_confidence_interval(lwbs_rate_vals)
    
    if len(median_los_vals) >= 3:
        aggregated.median_los_ci_lower, aggregated.median_los_ci_upper = calculate_confidence_interval(median_los_vals)
    
    if len(median_d2p_vals) >= 3:
        aggregated.median_door_to_provider_ci_lower, aggregated.median_door_to_provider_ci_upper = calculate_confidence_interval(median_d2p_vals)
    
    return aggregated


def run_replications(
    simulation_func: Callable,
    num_replications: int,
    base_seed: int = 42,
    **simulation_kwargs
) -> Tuple[List[ReplicationResult], AggregatedMetrics]:
    """
    Run multiple simulation replications.
    
    Args:
        simulation_func: Function that runs a single simulation and returns (sim, metrics, description)
        num_replications: Number of replications to run
        base_seed: Base random seed (each replication uses base_seed + replication_number)
        **simulation_kwargs: Additional arguments to pass to simulation_func
    
    Returns:
        Tuple of (list of ReplicationResult, AggregatedMetrics)
    """
    results = []
    
    for i in range(num_replications):
        seed = base_seed + i
        sim, metrics, description = simulation_func(seed=seed, **simulation_kwargs)
        
        result = ReplicationResult(
            metrics=metrics,
            sim=sim,
            description=description,
            seed=seed,
            replication_number=i + 1
        )
        results.append(result)
    
    aggregated = aggregate_metrics(results)
    
    return results, aggregated

