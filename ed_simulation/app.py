#!/usr/bin/env python3
"""
ED Digital Twin MVP - Streamlit Application

A configurable ED throughput simulation tool with scenario support.

Usage:
    streamlit run app.py
"""

import sys
import os

# Add the repository root to Python path for imports
# This ensures ed_simulation package can be found
# Handle multiple deployment scenarios:
# 1. app.py at repo root (local development)
# 2. app.py in ed_simulation subdirectory (Streamlit Cloud)
# 3. Repository cloned to different paths

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

# Strategy: Add both current and parent directories to path
# This covers both local and cloud deployment scenarios
paths_to_add = []
if current_dir not in sys.path:
    paths_to_add.append(current_dir)
if parent_dir not in sys.path and parent_dir != current_dir:
    paths_to_add.append(parent_dir)

# Also check if we're in a mount/src structure (Streamlit Cloud)
# and add the mount/src directory if it exists
if '/mount/src/' in current_dir:
    mount_src = current_dir.split('/mount/src/')[0] + '/mount/src'
    if mount_src not in sys.path and os.path.exists(mount_src):
        paths_to_add.append(mount_src)

for path in paths_to_add:
    sys.path.insert(0, path)

import random
from typing import Dict, Generator, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import simpy
import streamlit as st
import yaml

from ed_simulation.core.enums import Acuity, NodeType
from ed_simulation.core.patient import Patient
from ed_simulation.core.node import Node, NodeConfig
from ed_simulation.processes.arrivals import ArrivalGenerator, ArrivalConfig
from ed_simulation.processes.routing import RoutingEngine
from ed_simulation.processes.historical_arrivals import (
    HistoricalArrivalGenerator,
    parse_historical_csv,
)
from ed_simulation.simulation.metrics import MetricsCollector
from ed_simulation.simulation.staffing_optimizer import (
    StaffingOptimizer,
    OptimizationGoal,
    StaffingSuggestion,
)
from ed_simulation.simulation.replications import (
    run_replications,
    AggregatedMetrics,
    ReplicationResult,
)
from ed_simulation.scenarios import (
    BoardingScenario,
    VerticalTrackScenario,
    StaffingAdjustmentScenario,
    SurgeScenario,
)
from ed_simulation.config.loader import EDConfig, load_config, save_config, merge_with_defaults
from ed_simulation.config.defaults import (
    NODE_CAPACITY_DEFAULTS,
    SERVICE_TIME_DEFAULTS,
    ACUITY_DISTRIBUTION,
    ARRIVAL_DEFAULTS,
    LWBS_DEFAULTS,
)
from ed_simulation.simulation.animation import (
    get_all_patient_positions_at_time,
    PatientPosition,
    PatientState,
)


# Scenario options for dropdown
SCENARIO_OPTIONS = {
    "None (Baseline)": None,
    "Boarding": "boarding",
    "Vertical/Fast Track": "vertical_track",
    "Staffing Adjustment": "staffing_adjustment",
    "Surge": "surge",
}


class EDSimulationWithScenarios:
    """
    ED simulation orchestrator with scenario support.
    """

    def __init__(
        self,
        env: simpy.Environment,
        seed: int = 42,
        arrival_rate: float = 3.0,
        scenario_key: Optional[str] = None,
        scenario_params: Optional[Dict] = None,
        custom_capacities: Optional[Dict[NodeType, int]] = None,
        ed_config: Optional[EDConfig] = None,
    ):
        """
        Initialize the ED simulation.

        Args:
            env: SimPy environment
            seed: Random seed for reproducibility
            arrival_rate: Patients per hour
            scenario_key: Scenario to apply (None for baseline)
            scenario_params: Parameters for the selected scenario
        """
        self._env = env
        self._rng = random.Random(seed)
        self._scenario_key = scenario_key
        self._scenario = None
        self._scenario_description = "Baseline (no scenario)"
        self._scenario_params = scenario_params or {}

        # Create nodes with default configurations (or custom capacities/config)
        self._nodes: Dict[NodeType, Node] = {}
        
        # Merge custom config with defaults if provided
        if ed_config:
            merged_config = merge_with_defaults(ed_config)
        else:
            merged_config = None
        
        for node_type in NodeType:
            config = NodeConfig.from_defaults(node_type)
            
            # Apply custom capacity (from config or direct parameter)
            if merged_config and node_type.name in merged_config.node_capacities:
                config.capacity = merged_config.node_capacities[node_type.name]
            elif custom_capacities and node_type in custom_capacities:
                config.capacity = custom_capacities[node_type]
            
            # Apply custom service times if provided
            if merged_config and node_type.name in merged_config.service_times:
                from ed_simulation.core.node import ServiceTimeConfig
                service_times = merged_config.service_times[node_type.name]
                params_by_acuity = {}
                for acuity_val, params in service_times.items():
                    try:
                        acuity = Acuity(acuity_val)
                        params_by_acuity[acuity] = params
                    except ValueError:
                        pass
                config.service_time_config = ServiceTimeConfig(
                    distribution="lognormal",
                    params_by_acuity=params_by_acuity,
                )
            
            config.enable_lwbs_monitoring = True
            self._nodes[node_type] = Node(env, config, rng=random.Random(seed))

        # Create routing engine with custom pathways if provided
        if ed_config and hasattr(ed_config, 'pathways') and ed_config.pathways:
            self._routing_engine = RoutingEngine(pathways_config=ed_config.pathways)
        else:
            self._routing_engine = RoutingEngine()

        # Apply scenario if specified
        effective_arrival_rate = arrival_rate
        if scenario_key:
            effective_arrival_rate = self._apply_scenario(scenario_key, arrival_rate)

        # Create arrival generator
        arrival_config = ArrivalConfig(
            mean_arrival_rate=effective_arrival_rate,
            use_time_variation=False,
        )
        self._arrivals = ArrivalGenerator(env, arrival_config, rng=self._rng)

        # Track patients
        self._patients: List[Patient] = []
        self._completed: List[Patient] = []
        self._lwbs: List[Patient] = []
        
        # Snapshot collection for step-by-step analysis
        self._snapshots: List[Dict] = []

    def _apply_scenario(self, scenario_key: str, base_arrival_rate: float) -> float:
        """
        Apply the selected scenario with parameters from UI.

        Args:
            scenario_key: Key identifying the scenario
            base_arrival_rate: Base arrival rate before scenario

        Returns:
            Adjusted arrival rate (may be modified by surge scenario)
        """
        arrival_rate = base_arrival_rate
        params = self._scenario_params

        if scenario_key == "boarding":
            boarding_pct = params.get("boarding_pct", 0.40)
            self._scenario = BoardingScenario(boarding_percentage=boarding_pct)
            self._scenario.apply(self._nodes)
            self._scenario_description = f"Boarding: {int(boarding_pct * 100)}% beds occupied"

        elif scenario_key == "vertical_track":
            ft_providers = params.get("fast_track_providers", 2)
            self._scenario = VerticalTrackScenario(fast_track_providers=ft_providers)
            self._scenario.apply(self._nodes, env=self._env)
            self._scenario_description = f"Fast Track: {ft_providers} providers for ESI 4-5"

        elif scenario_key == "staffing_adjustment":
            # New time-window staffing mode
            time_window_start = params.get("time_window_start", 15)
            time_window_end = params.get("time_window_end", 19)
            target_node = params.get("target_node", NodeType.PROVIDER_ASSESSMENT)
            additional_staff = params.get("additional_staff", 1)
            
            self._scenario = StaffingAdjustmentScenario(
                time_window_start=time_window_start,
                time_window_end=time_window_end,
                target_node=target_node,
                additional_staff=additional_staff,
            )
            self._scenario.apply(self._nodes)
            
            node_name = target_node.name.replace("_", " ").title()
            start_str = f"{time_window_start:02d}:00"
            end_str = f"{time_window_end:02d}:00"
            self._scenario_description = f"Time-Window Staffing: +{additional_staff} {node_name} ({start_str}-{end_str})"

        elif scenario_key == "surge":
            surge_pct = params.get("surge_pct", 0.30)
            self._scenario = SurgeScenario(surge_percentage=surge_pct)
            arrival_rate = self._scenario.apply_to_rate(base_arrival_rate)
            self._scenario_description = f"Surge: +{int(surge_pct * 100)}% arrivals ({arrival_rate:.1f}/hr)"

        return arrival_rate

    @property
    def scenario_description(self) -> str:
        """Description of active scenario."""
        return self._scenario_description

    @property
    def patients(self) -> List[Patient]:
        """All patients that arrived."""
        return self._patients

    @property
    def nodes(self) -> Dict[NodeType, Node]:
        """All simulation nodes."""
        return self._nodes

    @property
    def arrivals(self) -> ArrivalGenerator:
        """Arrival generator."""
        return self._arrivals

    def patient_journey(self, patient: Patient) -> Generator:
        """
        Process a patient through their ED journey using conditional routing.

        Args:
            patient: The arriving patient

        Yields:
            SimPy events
        """
        self._patients.append(patient)

        # Start with triage (all patients go through triage)
        current_node = NodeType.TRIAGE

        while current_node is not None:
            # Get appropriate node (may be fast track node for vertical track scenario)
            node = self._get_node_for_patient(patient, current_node)

            # Process at this node
            completed = yield from node.process_patient(patient)

            if not completed:
                # Patient left (LWBS)
                self._lwbs.append(patient)
                return

            # Determine next node based on routing engine
            current_node = self._routing_engine.get_next_node(
                current_node=current_node,
                acuity=patient.acuity
            )

        # Successfully completed - discharge
        patient.discharge()
        self._completed.append(patient)

    def _get_pathway(self, patient: Patient) -> List[NodeType]:
        """
        Get the node pathway for a patient (used by vertical track scenario).

        Args:
            patient: Patient to route

        Returns:
            List of nodes to visit
        """
        # Get pathway from routing engine
        pathway = self._routing_engine.get_pathway(patient.acuity)

        # Let vertical track scenario modify pathway if active
        if isinstance(self._scenario, VerticalTrackScenario):
            return self._scenario.get_pathway(patient, pathway)

        return pathway

    def _get_node_for_patient(self, patient: Patient, node_type: NodeType) -> Node:
        """
        Get the appropriate node for a patient.

        Args:
            patient: Patient being processed
            node_type: Type of node needed

        Returns:
            Appropriate Node for this patient
        """
        # Let vertical track scenario provide fast track node if appropriate
        if isinstance(self._scenario, VerticalTrackScenario):
            return self._scenario.get_node_for_patient(patient, node_type, self._nodes)

        return self._nodes[node_type]

    def run(self, duration_minutes: float, warmup_minutes: float = 0.0) -> None:
        """
        Run the simulation.

        Args:
            duration_minutes: How long to simulate (in minutes)
            warmup_minutes: Warmup period in minutes. Statistics are reset after this.
        """
        # Start arrival process
        self._env.process(
            self._arrivals.run(
                on_arrival=self.patient_journey,
                until=duration_minutes,
            )
        )
        
        # Start time-based staffing updates if scenario supports it
        if self._scenario and hasattr(self._scenario, 'update_for_time'):
            self._env.process(self._monitor_staffing_updates(duration_minutes))
        
        # Start periodic snapshot collection (every 15 minutes)
        self._env.process(self._periodic_snapshots(interval=15.0, duration=duration_minutes))
        
        # Start warmup reset process if warmup period is specified
        if warmup_minutes > 0:
            self._env.process(self._reset_after_warmup(warmup_minutes))

        # Run simulation
        self._env.run(until=duration_minutes + 180)
    
    def _reset_after_warmup(self, warmup_minutes: float) -> Generator:
        """
        Reset node statistics after warmup period.
        
        Args:
            warmup_minutes: When to reset statistics
        """
        yield self._env.timeout(warmup_minutes)
        # Reset statistics for all nodes
        for node in self._nodes.values():
            node.statistics.reset()
    
    def _monitor_staffing_updates(self, duration_minutes: float) -> Generator:
        """Monitor simulation time and update staffing capacities."""
        check_interval = 60.0  # Check every hour
        while self._env.now < duration_minutes:
            yield self._env.timeout(check_interval)
            if self._scenario and hasattr(self._scenario, 'update_for_time'):
                self._scenario.update_for_time(self._env.now)
    
    def _periodic_snapshots(self, interval: float, duration: float) -> Generator:
        """Collect periodic snapshots of system state."""
        while self._env.now < duration:
            yield self._env.timeout(interval)
            self._record_snapshot()
    
    def _record_snapshot(self) -> None:
        """Record a snapshot of the current system state."""
        snapshot = {
            'time': self._env.now,
            'queues': {},
            'utilization': {},
            'active_patients': 0,
            'in_queue': 0,
            'in_service': 0,
            'lwbs_so_far': len(self._lwbs),
            'completed_so_far': len(self._completed),
        }
        
        # Collect queue lengths and utilization per node
        for node_type, node in self._nodes.items():
            # Get current queue length from statistics
            queue_samples = node.statistics.queue_length_samples
            if queue_samples:
                # Get most recent queue length
                snapshot['queues'][node_type.name] = queue_samples[-1][1] if queue_samples else 0
            else:
                snapshot['queues'][node_type.name] = 0
            
            # Calculate utilization (patients in service / capacity)
            # Use the node's internal _patients_in_service list
            in_service = len(node._patients_in_service)
            capacity = node._config.capacity
            snapshot['utilization'][node_type.name] = (in_service / capacity) if capacity > 0 else 0.0
        
        # Count active patients
        current_time = self._env.now
        for patient in self._patients:
            if patient.arrival_time <= current_time:
                if patient._departure_time and current_time >= patient._departure_time:
                    continue  # Patient has left
                snapshot['active_patients'] += 1
                
                # Check if in queue or in service
                if patient._current_node and patient._current_queue_enter_time:
                    if current_time >= patient._current_queue_enter_time:
                        if patient._current_node in patient.timestamps:
                            ts = patient.timestamps[patient._current_node]
                            if ts.service_start <= current_time < ts.service_end:
                                snapshot['in_service'] += 1
                            else:
                                snapshot['in_queue'] += 1
                        else:
                            snapshot['in_queue'] += 1
        
        self._snapshots.append(snapshot)
    
    @property
    def snapshots(self) -> List[Dict]:
        """Get all collected snapshots."""
        return self._snapshots


def run_simulation(
    arrival_rate: float,
    scenario_key: Optional[str] = None,
    scenario_params: Optional[Dict] = None,
    duration_hours: int = 24,
    seed: int = 42,
    ed_config: Optional[EDConfig] = None,
    warmup_minutes: float = 120.0
) -> Tuple:
    """
    Run ED simulation with given parameters.

    Args:
        arrival_rate: Patients per hour
        scenario_key: Scenario to apply
        scenario_params: Parameters for the scenario
        duration_hours: Simulation duration in hours
        seed: Random seed for reproducibility
        ed_config: Optional ED configuration (uses defaults if None)
        warmup_minutes: Warmup period in minutes (default: 120 = 2 hours)

    Returns:
        Tuple of (EDSimulation, EDMetrics, scenario_description)
    """
    # Ensure warmup_minutes is a float
    warmup_minutes = float(warmup_minutes) if warmup_minutes is not None else 120.0
    
    Patient.reset_counter()
    env = simpy.Environment()
    sim = EDSimulationWithScenarios(
        env,
        seed=seed,
        arrival_rate=arrival_rate,
        scenario_key=scenario_key,
        scenario_params=scenario_params,
        ed_config=ed_config,
    )
    sim.run(duration_minutes=duration_hours * 60, warmup_minutes=warmup_minutes)

    collector = MetricsCollector()
    collector.add_patients(sim.patients)
    metrics = collector.calculate(warmup_minutes=warmup_minutes)

    return sim, metrics, sim.scenario_description


def run_simulation_with_custom_capacities(
    arrival_rate: float,
    custom_capacities: Dict[NodeType, int],
    duration_hours: int = 24,
    seed: int = 42,
    warmup_minutes: float = 120.0
) -> Tuple:
    """
    Run ED simulation with custom node capacities.
    
    Args:
        arrival_rate: Patients per hour
        custom_capacities: Dictionary mapping NodeType to capacity
        duration_hours: Simulation duration in hours
        seed: Random seed for reproducibility
        warmup_minutes: Warmup period in minutes (default: 120 = 2 hours)
        
    Returns:
        Tuple of (EDSimulation, EDMetrics, description)
    """
    # Ensure warmup_minutes is a float
    warmup_minutes = float(warmup_minutes) if warmup_minutes is not None else 120.0
    
    Patient.reset_counter()
    env = simpy.Environment()
    sim = EDSimulationWithScenarios(
        env,
        seed=seed,
        arrival_rate=arrival_rate,
        scenario_key=None,  # No scenario for baseline comparison
        scenario_params=None,
        custom_capacities=custom_capacities,
    )
    sim.run(duration_minutes=duration_hours * 60, warmup_minutes=warmup_minutes)
    
    collector = MetricsCollector()
    collector.add_patients(sim.patients)
    metrics = collector.calculate(warmup_minutes=warmup_minutes)
    
    return sim, metrics, "Custom Staffing Configuration"


def evaluate_staffing_suggestion(
    suggestion: StaffingSuggestion,
    arrival_rate: float,
    duration_hours: int = 24,
    seed: int = 42,
    warmup_minutes: float = 120.0
) -> StaffingSuggestion:
    """
    Evaluate a staffing suggestion by running simulation.
    
    Args:
        suggestion: Staffing suggestion to evaluate
        arrival_rate: Patients per hour
        duration_hours: Simulation duration
        seed: Random seed
        warmup_minutes: Warmup period in minutes
        
    Returns:
        Updated suggestion with expected metrics
    """
    sim, metrics, _ = run_simulation_with_custom_capacities(
        arrival_rate=arrival_rate,
        custom_capacities=suggestion.node_capacities,
        duration_hours=duration_hours,
        seed=seed,
        warmup_minutes=warmup_minutes,
    )
    
    # Calculate utilization (average across key nodes)
    key_nodes = [NodeType.PROVIDER_ASSESSMENT, NodeType.TRIAGE, NodeType.DIAGNOSTICS]
    utilizations = []
    for node_type in key_nodes:
        if node_type in sim.nodes:
            node = sim.nodes[node_type]
            if node.statistics.patients_completed > 0:
                # Approximate utilization from service time and capacity
                avg_service_time = sum(node.statistics.service_times) / len(node.statistics.service_times) if node.statistics.service_times else 0
                total_time = duration_hours * 60
                utilization = (node.statistics.patients_completed * avg_service_time) / (suggestion.node_capacities.get(node_type, 1) * total_time)
                utilizations.append(min(utilization, 1.0))
    
    avg_utilization = sum(utilizations) / len(utilizations) if utilizations else 0.0
    
    # Update suggestion with actual metrics
    suggestion.expected_lwbs_rate = metrics.lwbs_rate
    suggestion.expected_los_mean = metrics.mean_los
    suggestion.expected_utilization = avg_utilization
    
    # Update score based on goal (will be set by optimizer)
    return suggestion


def run_simulation_with_historical_arrivals(
    historical_arrivals: List,
    scenario_key: Optional[str] = None,
    scenario_params: Optional[Dict] = None,
    seed: int = 42,
    warmup_minutes: float = 120.0
) -> Tuple:
    # Ensure warmup_minutes is a float
    warmup_minutes = float(warmup_minutes) if warmup_minutes is not None else 120.0
    """
    Run ED simulation with historical arrival data.

    Args:
        historical_arrivals: List of HistoricalArrival objects
        scenario_key: Scenario to apply
        scenario_params: Parameters for the scenario
        seed: Random seed for reproducibility
        warmup_minutes: Warmup period in minutes

    Returns:
        Tuple of (EDSimulation, EDMetrics, scenario_description)
    """
    Patient.reset_counter()
    env = simpy.Environment()
    sim = EDSimulationWithScenarios(
        env,
        seed=seed,
        arrival_rate=0.0,  # Not used for historical arrivals
        scenario_key=scenario_key,
        scenario_params=scenario_params,
    )
    
    # Create historical arrival generator
    historical_gen = HistoricalArrivalGenerator(
        env,
        historical_arrivals
    )
    
    # Start historical arrival process
    env.process(
        historical_gen.run(
            on_arrival=sim.patient_journey,
            until=None  # Run until all arrivals processed
        )
    )
    
    # Calculate max time from arrivals (add buffer for patients to complete)
    if historical_arrivals:
        max_arrival_time = max(a.arrival_time for a in historical_arrivals)
        max_time = max_arrival_time + 480  # Add 8 hours buffer
    else:
        max_time = 1440  # 24 hours default
    
    # Start warmup reset process if warmup period is specified
    if warmup_minutes > 0:
        env.process(sim._reset_after_warmup(warmup_minutes))
    
    # Run simulation
    env.run(until=max_time)

    collector = MetricsCollector()
    collector.add_patients(sim.patients)
    metrics = collector.calculate(warmup_minutes=warmup_minutes)

    return sim, metrics, sim.scenario_description


def calculate_actual_metrics_from_csv(
    csv_df: pd.DataFrame,
    los_column: Optional[str] = None,
    lwbs_column: Optional[str] = None,
    door_to_provider_column: Optional[str] = None,
) -> Dict:
    """
    Calculate actual metrics from CSV data if available.
    
    Args:
        csv_df: DataFrame with historical data
        los_column: Column name for length of stay (minutes)
        lwbs_column: Column name for LWBS indicator (0/1 or True/False)
        door_to_provider_column: Column name for door-to-provider time (minutes)
    
    Returns:
        Dict with actual metrics
    """
    metrics = {
        "median_los": None,
        "mean_los": None,
        "lwbs_rate": None,
        "median_door_to_provider": None,
        "mean_door_to_provider": None,
    }
    
    if los_column and los_column in csv_df.columns:
        los_values = csv_df[los_column].dropna()
        if len(los_values) > 0:
            metrics["median_los"] = los_values.median()
            metrics["mean_los"] = los_values.mean()
    
    if lwbs_column and lwbs_column in csv_df.columns:
        lwbs_values = csv_df[lwbs_column].dropna()
        if len(lwbs_values) > 0:
            metrics["lwbs_rate"] = lwbs_values.mean() if lwbs_values.dtype in ['int64', 'float64', 'bool'] else None
    
    if door_to_provider_column and door_to_provider_column in csv_df.columns:
        d2p_values = csv_df[door_to_provider_column].dropna()
        if len(d2p_values) > 0:
            metrics["median_door_to_provider"] = d2p_values.median()
            metrics["mean_door_to_provider"] = d2p_values.mean()
    
    return metrics


def plot_los_distribution(
    simulated_patients: List[Patient],
    actual_los: Optional[List[float]] = None,
) -> go.Figure:
    """
    Create LOS distribution density plot.
    
    Args:
        simulated_patients: List of simulated patients
        actual_los: Optional list of actual LOS values from CSV
    
    Returns:
        Plotly figure
    """
    fig = go.Figure()
    
    # Simulated LOS
    sim_los = [p.length_of_stay for p in simulated_patients if p.length_of_stay is not None]
    
    if sim_los:
        fig.add_trace(go.Histogram(
            x=sim_los,
            name="Simulated",
            opacity=0.7,
            nbinsx=30,
            histnorm='probability density',
            marker_color='#1f77b4',
        ))
    
    # Actual LOS if available
    if actual_los and len(actual_los) > 0:
        fig.add_trace(go.Histogram(
            x=actual_los,
            name="Actual",
            opacity=0.7,
            nbinsx=30,
            histnorm='probability density',
            marker_color='#ff7f0e',
        ))
    
    fig.update_layout(
        title="Length of Stay Distribution",
        xaxis_title="Length of Stay (minutes)",
        yaxis_title="Density",
        hovermode='x unified',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )
    
    return fig


def plot_key_metrics_comparison(
    simulated_metrics,
    actual_metrics: Optional[Dict] = None,
    baseline_metrics: Optional = None,
) -> go.Figure:
    """
    Create bar chart comparing key metrics.
    
    Args:
        simulated_metrics: EDMetrics object with simulated values
        actual_metrics: Optional dict with actual metrics (for backtesting)
        baseline_metrics: Optional EDMetrics for baseline comparison
    
    Returns:
        Plotly figure
    """
    metrics_data = []
    
    # LOS Mean
    if simulated_metrics.mean_los is not None:
        metrics_data.append({
            "Metric": "LOS Mean (min)",
            "Value": simulated_metrics.mean_los,
            "Type": "Simulated" if actual_metrics is None else "Simulated",
        })
        if actual_metrics and actual_metrics.get('mean_los') is not None:
            metrics_data.append({
                "Metric": "LOS Mean (min)",
                "Value": actual_metrics['mean_los'],
                "Type": "Actual",
            })
        elif baseline_metrics and baseline_metrics.mean_los is not None:
            metrics_data.append({
                "Metric": "LOS Mean (min)",
                "Value": baseline_metrics.mean_los,
                "Type": "Baseline",
            })
    
    # LWBS Rate
    metrics_data.append({
        "Metric": "LWBS Rate (%)",
        "Value": simulated_metrics.lwbs_rate * 100,
        "Type": "Simulated" if actual_metrics is None else "Simulated",
    })
    if actual_metrics and actual_metrics.get('lwbs_rate') is not None:
        metrics_data.append({
            "Metric": "LWBS Rate (%)",
            "Value": actual_metrics['lwbs_rate'] * 100,
            "Type": "Actual",
        })
    elif baseline_metrics:
        metrics_data.append({
            "Metric": "LWBS Rate (%)",
            "Value": baseline_metrics.lwbs_rate * 100,
            "Type": "Baseline",
        })
    
    # Door-to-Provider Median
    if simulated_metrics.median_door_to_provider is not None:
        metrics_data.append({
            "Metric": "Door-to-Provider (min)",
            "Value": simulated_metrics.median_door_to_provider,
            "Type": "Simulated" if actual_metrics is None else "Simulated",
        })
        if actual_metrics and actual_metrics.get('median_door_to_provider') is not None:
            metrics_data.append({
                "Metric": "Door-to-Provider (min)",
                "Value": actual_metrics['median_door_to_provider'],
                "Type": "Actual",
            })
        elif baseline_metrics and baseline_metrics.median_door_to_provider is not None:
            metrics_data.append({
                "Metric": "Door-to-Provider (min)",
                "Value": baseline_metrics.median_door_to_provider,
                "Type": "Baseline",
            })
    
    if not metrics_data:
        # Return empty figure if no data
        fig = go.Figure()
        fig.add_annotation(text="No metrics data available", showarrow=False)
        return fig
    
    df = pd.DataFrame(metrics_data)
    
    fig = px.bar(
        df,
        x="Metric",
        y="Value",
        color="Type",
        barmode="group",
        title="Key Metrics Comparison",
        color_discrete_map={
            "Simulated": "#1f77b4",
            "Actual": "#ff7f0e",
            "Baseline": "#2ca02c",
        },
    )
    
    fig.update_layout(
        xaxis_title="",
        yaxis_title="Value",
        hovermode='x unified',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )
    
    return fig


def plot_animated_ed_diagram(
    ed_config: Optional[EDConfig] = None,
    routing_engine: Optional[RoutingEngine] = None,
    patients: Optional[List[Patient]] = None,
    current_time: float = 0.0,
    node_positions: Optional[Dict[NodeType, Tuple[float, float]]] = None,
) -> go.Figure:
    """
    Create an animated, interactive ED diagram showing patients moving through the system.
    
    Args:
        ed_config: ED configuration
        routing_engine: Routing engine
        patients: List of patients (for animation)
        current_time: Current simulation time to display
        node_positions: Node position mapping
    
    Returns:
        Plotly figure with animated diagram
    """
    if routing_engine is None:
        routing_engine = RoutingEngine()
    
    # Get node capacities
    if ed_config:
        merged_config = merge_with_defaults(ed_config)
        capacities = merged_config.node_capacities
    else:
        capacities = NODE_CAPACITY_DEFAULTS.copy()
    
    # Define node positions (layout)
    if node_positions is None:
        node_positions = {
            NodeType.TRIAGE: (0, 3),
            NodeType.REGISTRATION: (1, 2),
            NodeType.BED_ASSIGNMENT: (2, 3),
            NodeType.FAST_TRACK: (1, 4),
            NodeType.PROVIDER_ASSESSMENT: (3, 3),
            NodeType.DIAGNOSTICS: (4, 2),
            NodeType.TREATMENT: (4, 4),
            NodeType.DISPOSITION: (5, 3),
        }
    
    # Create figure
    fig = go.Figure()
    
    # Define pathway colors
    pathway_colors = {
        "Critical": "#dc3545",      # Red for ESI 1-2
        "Standard": "#007bff",      # Blue for ESI 3
        "Fast Track": "#28a745",    # Green for ESI 4-5
    }
    
    # Draw pathways
    pathways_to_draw = [
        (Acuity.ESI_1, "Critical"),
        (Acuity.ESI_3, "Standard"),
        (Acuity.ESI_4, "Fast Track"),
    ]
    
    for acuity, pathway_name in pathways_to_draw:
        pathway = routing_engine.get_pathway(acuity)
        color = pathway_colors[pathway_name]
        
        # Draw arrows between nodes
        for i in range(len(pathway) - 1):
            start_node = pathway[i]
            end_node = pathway[i + 1]
            
            if start_node in node_positions and end_node in node_positions:
                x0, y0 = node_positions[start_node]
                x1, y1 = node_positions[end_node]
                
                # Calculate arrow direction
                dx = x1 - x0
                dy = y1 - y0
                dist = (dx**2 + dy**2)**0.5
                
                # Shorten line to avoid overlapping with nodes
                shorten = 0.15
                x0_adj = x0 + (dx * shorten / dist) if dist > 0 else x0
                y0_adj = y0 + (dy * shorten / dist) if dist > 0 else y0
                x1_adj = x1 - (dx * shorten / dist) if dist > 0 else x1
                y1_adj = y1 - (dy * shorten / dist) if dist > 0 else y1
                
                # Draw arrow line
                fig.add_trace(go.Scatter(
                    x=[x0_adj, x1_adj],
                    y=[y0_adj, y1_adj],
                    mode='lines',
                    line=dict(color=color, width=2, dash='dot'),
                    showlegend=False,
                    hoverinfo='skip',
                    opacity=0.4,
                ))
    
    # Draw nodes
    for node_type, (x, y) in node_positions.items():
        capacity = capacities.get(node_type.name, 1)
        node_name = node_type.name.replace("_", "\n")
        
        # Add node shape
        fig.add_shape(
            type="rect",
            x0=x-0.4, y0=y-0.3,
            x1=x+0.4, y1=y+0.3,
            fillcolor="lightblue",
            line=dict(color="darkblue", width=2),
            opacity=0.9,
        )
        
        # Add node text
        fig.add_annotation(
            x=x,
            y=y,
            text=f"<b>{node_name}</b>",
            showarrow=False,
            font=dict(size=10, color='black'),
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="darkblue",
            borderwidth=1,
        )
        
        # Add capacity label
        fig.add_annotation(
            x=x,
            y=y - 0.4,
            text=f"Cap: {capacity}",
            showarrow=False,
            font=dict(size=8, color='darkblue'),
        )
        
        # Show queue length if we have patient data
        if patients:
            # Count patients at this node using position data
            queue_count = 0
            in_service_count = 0
            patient_positions = get_all_patient_positions_at_time(patients, current_time, node_positions)
            
            for pos in patient_positions:
                if pos.node == node_type:
                    if pos.status == PatientStatus.WAITING:
                        queue_count += 1
                    elif pos.status == PatientStatus.IN_SERVICE:
                        in_service_count += 1
            
            if queue_count > 0 or in_service_count > 0:
                fig.add_annotation(
                    x=x,
                    y=y + 0.4,
                    text=f"Queue: {queue_count} | In Service: {in_service_count}",
                    showarrow=False,
                    font=dict(size=8, color='red' if queue_count > capacity else 'green'),
                )
    
    # Draw patients as moving dots
    if patients:
        patient_positions = get_all_patient_positions_at_time(patients, current_time, node_positions)
        
        # Group by status for different colors
        status_colors = {
            PatientStatus.ARRIVING: "#ffa500",      # Orange
            PatientStatus.WAITING: "#ff0000",       # Red (in queue)
            PatientStatus.IN_SERVICE: "#00ff00",     # Green (being served)
            PatientStatus.DISCHARGED: "#888888",    # Gray
            PatientStatus.LWBS: "#ff00ff",          # Magenta
        }
        
        for status in PatientStatus:
            status_patients = [p for p in patient_positions if p.status == status]
            if status_patients:
                # Get color based on acuity for in-service patients
                x_vals = []
                y_vals = []
                colors = []
                texts = []
                
                for pos in status_patients:
                    x_vals.append(pos.x)
                    y_vals.append(pos.y)
                    
                    # Color by acuity
                    acuity_colors = {
                        Acuity.ESI_1: "#8b0000",  # Dark red
                        Acuity.ESI_2: "#dc3545",  # Red
                        Acuity.ESI_3: "#007bff",  # Blue
                        Acuity.ESI_4: "#28a745",  # Green
                        Acuity.ESI_5: "#ffc107",  # Yellow
                    }
                    colors.append(acuity_colors.get(pos.acuity, "#000000"))
                    texts.append(f"P{pos.patient_id[-3:]}<br>ESI {pos.acuity.value}")
                
                fig.add_trace(go.Scatter(
                    x=x_vals,
                    y=y_vals,
                    mode='markers+text',
                    marker=dict(
                        size=15,
                        color=colors,
                        line=dict(width=2, color='white'),
                        opacity=0.9,
                    ),
                    text=[f"ESI{pos.acuity.value}" for pos in status_patients],
                    textposition="middle center",
                    textfont=dict(size=8, color='white'),
                    name=f"{status.name.replace('_', ' ').title()} ({len(status_patients)})",
                    hovertemplate="<b>Patient %{text}</b><br>Status: %{fullData.name}<extra></extra>",
                    customdata=[pos.patient_id for pos in status_patients],
                ))
    
    # Add title
    ed_name = ed_config.ed_name if ed_config else "Default ED"
    time_str = f"{current_time/60:.1f} hrs" if current_time > 0 else "0 min"
    fig.update_layout(
        title=f"🕹️ Interactive ED: {ed_name} | Time: {time_str}",
        xaxis=dict(
            showgrid=False,
            showticklabels=False,
            range=[-1, 6.5],
        ),
        yaxis=dict(
            showgrid=False,
            showticklabels=False,
            range=[0.5, 5],
        ),
        plot_bgcolor='white',
        width=1000,
        height=700,
        showlegend=True,
        legend=dict(
            x=0.02,
            y=0.98,
            bgcolor='rgba(255,255,255,0.8)',
        ),
    )
    
    return fig


def plot_ed_diagram_interactive(
    ed_config: Optional[EDConfig] = None,
    routing_engine: Optional[RoutingEngine] = None,
    nodes_dict: Optional[Dict[NodeType, Node]] = None,
) -> go.Figure:
    """
    Create an interactive ED diagram with click/hover events.
    
    Args:
        ed_config: ED configuration
        routing_engine: Routing engine
        nodes_dict: Optional nodes dict for showing real-time stats
    
    Returns:
        Interactive Plotly figure
    """
    if routing_engine is None:
        routing_engine = RoutingEngine()
    
    # Get node capacities
    if ed_config:
        merged_config = merge_with_defaults(ed_config)
        capacities = merged_config.node_capacities
    else:
        capacities = NODE_CAPACITY_DEFAULTS.copy()
    
    # Node positions (more spread out for better interactivity)
    positions = {
        NodeType.TRIAGE: (0, 4),
        NodeType.REGISTRATION: (2, 4),
        NodeType.BED_ASSIGNMENT: (4, 4),
        NodeType.FAST_TRACK: (4, 2),
        NodeType.PROVIDER_ASSESSMENT: (6, 3),
        NodeType.DIAGNOSTICS: (8, 4),
        NodeType.TREATMENT: (10, 4),
        NodeType.DISPOSITION: (12, 4),
    }
    
    fig = go.Figure()
    
    # Draw pathways with different colors
    pathways = {
        'Critical (ESI 1-2)': [
            positions[NodeType.TRIAGE],
            positions[NodeType.BED_ASSIGNMENT],
            positions[NodeType.PROVIDER_ASSESSMENT],
            positions[NodeType.DIAGNOSTICS],
            positions[NodeType.TREATMENT],
            positions[NodeType.DISPOSITION],
        ],
        'Standard (ESI 3)': [
            positions[NodeType.TRIAGE],
            positions[NodeType.REGISTRATION],
            positions[NodeType.BED_ASSIGNMENT],
            positions[NodeType.PROVIDER_ASSESSMENT],
            positions[NodeType.DIAGNOSTICS],
            positions[NodeType.TREATMENT],
            positions[NodeType.DISPOSITION],
        ],
        'Fast Track (ESI 4-5)': [
            positions[NodeType.TRIAGE],
            positions[NodeType.FAST_TRACK],
            positions[NodeType.PROVIDER_ASSESSMENT],
            positions[NodeType.DISPOSITION],
        ],
    }
    
    colors = ['#dc3545', '#007bff', '#28a745']
    for i, (label, path) in enumerate(pathways.items()):
        xs, ys = zip(*path)
        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode='lines+markers',
            line=dict(width=3, color=colors[i], dash='dot'),
            marker=dict(size=8, color=colors[i]),
            name=label,
            hoverinfo='name',
            showlegend=True,
        ))
    
    # Draw nodes with interactive markers
    for node_type, (x, y) in positions.items():
        cap = capacities.get(node_type.name, 1)
        node_name = node_type.name.replace("_", " ")
        
        # Get real-time stats if available
        queue_info = ""
        if nodes_dict and node_type in nodes_dict:
            node = nodes_dict[node_type]
            queue_samples = node.statistics.queue_length_samples
            if queue_samples:
                current_queue = queue_samples[-1][1] if queue_samples else 0
                in_service = node.statistics.patients_in_service
                queue_info = f"<br>Queue: {current_queue} | In Service: {in_service}"
        
        hover_text = f"<b>{node_name}</b><br>Capacity: {cap}{queue_info}<br>Click for details"
        
        fig.add_trace(go.Scatter(
            x=[x], y=[y],
            mode='markers+text',
            marker=dict(size=50, color='lightblue', line=dict(width=3, color='darkblue')),
            text=[f"{node_name}<br>{cap}"],
            textposition='middle center',
            textfont=dict(size=10, color='black'),
            hoverinfo='text',
            hovertext=hover_text,
            name=node_name,
            customdata=[node_type.name],  # Store node name for click handling
        ))
    
    ed_name = ed_config.ed_name if ed_config else "Default ED"
    fig.update_layout(
        title=f"ED Flow Diagram – {ed_name} (Click nodes for details)",
        showlegend=True,
        hovermode='closest',
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-1, 13]),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 5]),
        height=600,
        margin=dict(l=20, r=20, t=80, b=20),
        plot_bgcolor='white',
    )
    
    return fig


def plot_ed_diagram(
    ed_config: Optional[EDConfig] = None,
    routing_engine: Optional[RoutingEngine] = None,
) -> go.Figure:
    """
    Create a visual diagram of the ED showing nodes, capacities, and patient flow.
    
    Args:
        ed_config: ED configuration (uses defaults if None)
        routing_engine: Routing engine for pathways (creates new if None)
    
    Returns:
        Plotly figure with ED diagram
    """
    if routing_engine is None:
        routing_engine = RoutingEngine()
    
    # Get node capacities
    if ed_config:
        merged_config = merge_with_defaults(ed_config)
        capacities = merged_config.node_capacities
    else:
        capacities = NODE_CAPACITY_DEFAULTS.copy()
    
    # Define node positions (layout)
    node_positions = {
        NodeType.TRIAGE: (0, 3),
        NodeType.REGISTRATION: (1, 2),
        NodeType.BED_ASSIGNMENT: (2, 3),
        NodeType.FAST_TRACK: (1, 4),
        NodeType.PROVIDER_ASSESSMENT: (3, 3),
        NodeType.DIAGNOSTICS: (4, 2),
        NodeType.TREATMENT: (4, 4),
        NodeType.DISPOSITION: (5, 3),
    }
    
    # Create figure
    fig = go.Figure()
    
    # Define pathway colors
    pathway_colors = {
        "Critical": "#dc3545",      # Red for ESI 1-2
        "Standard": "#007bff",      # Blue for ESI 3
        "Fast Track": "#28a745",    # Green for ESI 4-5
    }
    
    # Draw pathways with better visualization
    pathways_to_draw = [
        (Acuity.ESI_1, "Critical"),
        (Acuity.ESI_3, "Standard"),
        (Acuity.ESI_4, "Fast Track"),
    ]
    
    for acuity, pathway_name in pathways_to_draw:
        pathway = routing_engine.get_pathway(acuity)
        color = pathway_colors[pathway_name]
        
        # Draw arrows between nodes
        for i in range(len(pathway) - 1):
            start_node = pathway[i]
            end_node = pathway[i + 1]
            
            if start_node in node_positions and end_node in node_positions:
                x0, y0 = node_positions[start_node]
                x1, y1 = node_positions[end_node]
                
                # Calculate arrow direction
                dx = x1 - x0
                dy = y1 - y0
                dist = (dx**2 + dy**2)**0.5
                
                # Shorten line to avoid overlapping with nodes
                shorten = 0.15
                x0_adj = x0 + (dx * shorten / dist) if dist > 0 else x0
                y0_adj = y0 + (dy * shorten / dist) if dist > 0 else y0
                x1_adj = x1 - (dx * shorten / dist) if dist > 0 else x1
                y1_adj = y1 - (dy * shorten / dist) if dist > 0 else y1
                
                # Draw arrow line
                fig.add_trace(go.Scatter(
                    x=[x0_adj, x1_adj],
                    y=[y0_adj, y1_adj],
                    mode='lines',
                    line=dict(color=color, width=3),
                    showlegend=False,
                    hoverinfo='skip',
                ))
                
                # Add arrowhead
                arrow_angle = 0.3
                arrow_length = 0.15
                # Calculate arrowhead points
                angle = -3.14159/4 if dy < 0 else 3.14159/4
                arrow_x1 = x1_adj - arrow_length * (dx/dist * 0.707 - dy/dist * 0.707)
                arrow_y1 = y1_adj - arrow_length * (dy/dist * 0.707 + dx/dist * 0.707)
                arrow_x2 = x1_adj - arrow_length * (dx/dist * 0.707 + dy/dist * 0.707)
                arrow_y2 = y1_adj - arrow_length * (dy/dist * 0.707 - dx/dist * 0.707)
                
                fig.add_trace(go.Scatter(
                    x=[x1_adj, arrow_x1, x1_adj, arrow_x2],
                    y=[y1_adj, arrow_y1, y1_adj, arrow_y2],
                    mode='lines',
                    line=dict(color=color, width=2),
                    fill='toself',
                    fillcolor=color,
                    showlegend=False,
                    hoverinfo='skip',
                ))
    
    # Draw nodes
    node_x = []
    node_y = []
    node_text = []
    node_capacities = []
    node_names = []
    
    for node_type, (x, y) in node_positions.items():
        node_x.append(x)
        node_y.append(y)
        node_name = node_type.name.replace("_", "\n")
        capacity = capacities.get(node_type.name, 1)
        node_text.append(f"{node_name}\nCapacity: {capacity}")
        node_capacities.append(capacity)
        node_names.append(node_type.name)
    
    # Add node boxes with better styling
    for node_type, (x, y) in node_positions.items():
        capacity = capacities.get(node_type.name, 1)
        node_name = node_type.name.replace("_", "\n")
        
        # Node size based on capacity
        node_size = 50 + capacity * 4
        
        # Add node shape (using annotation for better control)
        fig.add_shape(
            type="rect",
            x0=x-0.4, y0=y-0.3,
            x1=x+0.4, y1=y+0.3,
            fillcolor="lightblue",
            line=dict(color="darkblue", width=2),
            opacity=0.9,
        )
        
        # Add node text
        fig.add_annotation(
            x=x,
            y=y,
            text=f"<b>{node_name}</b>",
            showarrow=False,
            font=dict(size=11, color='black'),
            bgcolor="rgba(255,255,255,0.7)",
            bordercolor="darkblue",
            borderwidth=1,
        )
        
        # Add capacity label
        fig.add_annotation(
            x=x,
            y=y - 0.4,
            text=f"Capacity: {capacity}",
            showarrow=False,
            font=dict(size=9, color='darkblue'),
        )
    
    # Add pathway legend
    legend_y = 4.5
    for pathway_name, color in pathway_colors.items():
        fig.add_trace(go.Scatter(
            x=[-0.5],
            y=[legend_y],
            mode='lines+markers',
            line=dict(color=color, width=3),
            marker=dict(size=0),
            name=f"{pathway_name} Pathway",
            showlegend=True,
            hoverinfo='skip',
        ))
        legend_y -= 0.3
    
    # Add title and labels
    ed_name = ed_config.ed_name if ed_config else "Default ED"
    fig.update_layout(
        title=f"ED Layout Diagram: {ed_name}",
        xaxis=dict(
            showgrid=False,
            showticklabels=False,
            range=[-1, 6],
        ),
        yaxis=dict(
            showgrid=False,
            showticklabels=False,
            range=[1, 5],
        ),
        plot_bgcolor='white',
        width=800,
        height=600,
        showlegend=True,
        legend=dict(
            x=0.02,
            y=0.98,
            bgcolor='rgba(255,255,255,0.8)',
        ),
    )
    
    return fig


def plot_hourly_queue_lengths_by_node(
    nodes: Dict[NodeType, Node],
    selected_nodes: Optional[List[NodeType]] = None,
) -> go.Figure:
    """
    Create line chart showing hour-by-hour average queue length for each node.
    
    Args:
        nodes: Dictionary of all nodes
        selected_nodes: Optional list of nodes to plot (defaults to all)
    
    Returns:
        Plotly figure
    """
    fig = go.Figure()
    
    if selected_nodes is None:
        # Include all nodes
        selected_nodes = list(nodes.keys())
    
    colors = px.colors.qualitative.Set3
    
    for idx, node_type in enumerate(selected_nodes):
        if node_type not in nodes:
            continue
        
        node = nodes[node_type]
        stats = node.statistics
        
        if not stats.queue_length_samples:
            continue
        
        # Group queue lengths by hour
        hourly_queues: Dict[int, List[int]] = {}
        for time_min, queue_len in stats.queue_length_samples:
            hour = int(time_min / 60) % 24
            if hour not in hourly_queues:
                hourly_queues[hour] = []
            hourly_queues[hour].append(queue_len)
        
        # Calculate average queue length per hour
        hours = sorted(hourly_queues.keys())
        avg_queues = [sum(hourly_queues[h]) / len(hourly_queues[h]) for h in hours]
        hour_labels = [f"{h:02d}:00" for h in hours]
        
        fig.add_trace(go.Scatter(
            x=hour_labels,
            y=avg_queues,
            mode='lines+markers',
            name=node.name,
            line=dict(color=colors[idx % len(colors)], width=2),
            marker=dict(size=6),
            hovertemplate=f"<b>{node.name}</b><br>" +
                         "Hour: %{x}<br>" +
                         "Avg Queue Length: %{y:.1f}<extra></extra>",
        ))
    
    fig.update_layout(
        title="Hour-by-Hour Average Queue Length by Node",
        xaxis_title="Hour of Day",
        yaxis_title="Average Queue Length",
        hovermode='x unified',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        xaxis=dict(tickangle=45),
    )
    
    return fig


def plot_wait_times_by_node_comparison(
    baseline_nodes: Dict[NodeType, Node],
    scenario_nodes: Dict[NodeType, Node],
    selected_nodes: Optional[List[NodeType]] = None,
) -> go.Figure:
    """
    Create bar chart comparing average wait times by node (baseline vs scenario).
    
    Args:
        baseline_nodes: Dictionary of baseline nodes
        scenario_nodes: Dictionary of scenario nodes
        selected_nodes: Optional list of nodes to plot (defaults to all)
    
    Returns:
        Plotly figure
    """
    if selected_nodes is None:
        # Include all nodes that exist in both
        selected_nodes = [nt for nt in baseline_nodes.keys() if nt in scenario_nodes]
    
    node_names = []
    baseline_wait_times = []
    scenario_wait_times = []
    deltas = []
    
    for node_type in selected_nodes:
        if node_type not in baseline_nodes or node_type not in scenario_nodes:
            continue
        
        baseline_node = baseline_nodes[node_type]
        scenario_node = scenario_nodes[node_type]
        
        baseline_avg = baseline_node.statistics.average_wait_time
        scenario_avg = scenario_node.statistics.average_wait_time
        
        if baseline_avg > 0 or scenario_avg > 0:
            node_names.append(baseline_node.name)
            baseline_wait_times.append(baseline_avg)
            scenario_wait_times.append(scenario_avg)
            delta = scenario_avg - baseline_avg
            deltas.append(delta)
    
    if not node_names:
        fig = go.Figure()
        fig.add_annotation(text="No wait time data available", showarrow=False)
        return fig
    
    fig = go.Figure()
    
    # Baseline bars
    fig.add_trace(go.Bar(
        name="Baseline",
        x=node_names,
        y=baseline_wait_times,
        marker_color='#1f77b4',
        hovertemplate="<b>Baseline</b><br>%{x}<br>Wait Time: %{y:.1f} min<extra></extra>",
    ))
    
    # Scenario bars
    fig.add_trace(go.Bar(
        name="Scenario",
        x=node_names,
        y=scenario_wait_times,
        marker_color='#ff7f0e',
        hovertemplate="<b>Scenario</b><br>%{x}<br>Wait Time: %{y:.1f} min<extra></extra>",
    ))
    
    # Add delta annotations
    for i, (name, delta) in enumerate(zip(node_names, deltas)):
        if abs(delta) > 0.1:  # Only show significant changes
            color = 'green' if delta < 0 else 'red'
            fig.add_annotation(
                x=i,
                y=max(baseline_wait_times[i], scenario_wait_times[i]) + 1,
                text=f"{delta:+.1f}",
                showarrow=False,
                font=dict(color=color, size=10),
            )
    
    fig.update_layout(
        title="Average Wait Time by Node: Baseline vs Scenario",
        xaxis_title="Node",
        yaxis_title="Average Wait Time (minutes)",
        barmode='group',
        hovermode='x unified',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        xaxis=dict(tickangle=45),
    )
    
    return fig


def plot_queue_length_over_time(
    nodes: Dict[NodeType, Node],
    selected_nodes: Optional[List[NodeType]] = None,
) -> go.Figure:
    """
    Create line chart showing queue length over time for selected nodes.
    
    Args:
        nodes: Dictionary of all nodes
        selected_nodes: Optional list of nodes to plot (defaults to all)
    
    Returns:
        Plotly figure
    """
    fig = go.Figure()
    
    if selected_nodes is None:
        # Default to key nodes
        selected_nodes = [
            NodeType.TRIAGE,
            NodeType.BED_ASSIGNMENT,
            NodeType.PROVIDER_ASSESSMENT,
        ]
    
    colors = px.colors.qualitative.Set3
    
    for idx, node_type in enumerate(selected_nodes):
        if node_type not in nodes:
            continue
        
        node = nodes[node_type]
        stats = node.statistics
        
        if not stats.queue_length_samples:
            continue
        
        # Extract time and queue length
        times = [sample[0] for sample in stats.queue_length_samples]
        queue_lengths = [sample[1] for sample in stats.queue_length_samples]
        
        fig.add_trace(go.Scatter(
            x=times,
            y=queue_lengths,
            mode='lines',
            name=node.name,
            line=dict(color=colors[idx % len(colors)], width=2),
            hovertemplate=f"<b>{node.name}</b><br>" +
                         "Time: %{x:.1f} min<br>" +
                         "Queue Length: %{y}<extra></extra>",
        ))
    
    fig.update_layout(
        title="Queue Length Over Time",
        xaxis_title="Simulation Time (minutes)",
        yaxis_title="Queue Length",
        hovermode='x unified',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )
    
    return fig


def create_comparison_table(
    actual_metrics: Dict,
    simulated_metrics,
) -> pd.DataFrame:
    """
    Create comparison table of actual vs simulated metrics.
    
    Args:
        actual_metrics: Dict with actual metric values
        simulated_metrics: EDMetrics object with simulated values
    
    Returns:
        DataFrame with comparison
    """
    rows = []
    
    # LOS Mean
    actual_mean_los = actual_metrics.get('mean_los')
    sim_mean_los = simulated_metrics.mean_los
    rows.append({
        "Metric": "Length of Stay (Mean)",
        "Actual": f"{actual_mean_los:.1f}" if actual_mean_los is not None else "N/A",
        "Simulated": f"{sim_mean_los:.1f}" if sim_mean_los is not None else "N/A",
        "Delta": f"{(sim_mean_los - actual_mean_los):.1f}" if sim_mean_los is not None and actual_mean_los is not None else "N/A",
    })
    
    # LOS Median
    actual_median_los = actual_metrics.get('median_los')
    sim_median_los = simulated_metrics.median_los
    rows.append({
        "Metric": "Length of Stay (Median)",
        "Actual": f"{actual_median_los:.1f}" if actual_median_los is not None else "N/A",
        "Simulated": f"{sim_median_los:.1f}" if sim_median_los is not None else "N/A",
        "Delta": f"{(sim_median_los - actual_median_los):.1f}" if sim_median_los is not None and actual_median_los is not None else "N/A",
    })
    
    # LWBS Rate
    actual_lwbs = actual_metrics.get('lwbs_rate')
    if actual_lwbs is not None:
        actual_lwbs_pct = actual_lwbs * 100
    else:
        actual_lwbs_pct = None
    
    sim_lwbs_pct = simulated_metrics.lwbs_rate * 100
    rows.append({
        "Metric": "LWBS Rate (%)",
        "Actual": f"{actual_lwbs_pct:.1f}%" if actual_lwbs_pct is not None else "N/A",
        "Simulated": f"{sim_lwbs_pct:.1f}%",
        "Delta": f"{(sim_lwbs_pct - (actual_lwbs_pct or 0)):.1f}%" if actual_lwbs_pct is not None else "N/A",
    })
    
    # Door-to-Provider Mean
    actual_mean_d2p = actual_metrics.get('mean_door_to_provider')
    sim_mean_d2p = simulated_metrics.mean_door_to_provider
    rows.append({
        "Metric": "Door-to-Provider (Mean)",
        "Actual": f"{actual_mean_d2p:.1f}" if actual_mean_d2p is not None else "N/A",
        "Simulated": f"{sim_mean_d2p:.1f}" if sim_mean_d2p is not None else "N/A",
        "Delta": f"{(sim_mean_d2p - actual_mean_d2p):.1f}" if sim_mean_d2p is not None and actual_mean_d2p is not None else "N/A",
    })
    
    # Door-to-Provider Median
    actual_median_d2p = actual_metrics.get('median_door_to_provider')
    sim_median_d2p = simulated_metrics.median_door_to_provider
    rows.append({
        "Metric": "Door-to-Provider (Median)",
        "Actual": f"{actual_median_d2p:.1f}" if actual_median_d2p is not None else "N/A",
        "Simulated": f"{sim_median_d2p:.1f}" if sim_median_d2p is not None else "N/A",
        "Delta": f"{(sim_median_d2p - actual_median_d2p):.1f}" if sim_median_d2p is not None and actual_median_d2p is not None else "N/A",
    })
    
    return pd.DataFrame(rows)


def compute_node_deltas(
    baseline_nodes: Dict[NodeType, Node],
    scenario_nodes: Dict[NodeType, Node],
) -> Dict[NodeType, Dict[str, float]]:
    """
    Compute per-node deltas between baseline and scenario.
    
    Args:
        baseline_nodes: Dictionary of baseline nodes
        scenario_nodes: Dictionary of scenario nodes
    
    Returns:
        Dictionary mapping NodeType to delta metrics
    """
    deltas = {}
    
    for node_type in baseline_nodes.keys():
        if node_type not in scenario_nodes:
            continue
        
        baseline_node = baseline_nodes[node_type]
        scenario_node = scenario_nodes[node_type]
        
        baseline_stats = baseline_node.statistics
        scenario_stats = scenario_node.statistics
        
        # Calculate average queue length
        baseline_avg_queue = 0.0
        if baseline_stats.queue_length_samples:
            baseline_avg_queue = sum(q[1] for q in baseline_stats.queue_length_samples) / len(baseline_stats.queue_length_samples)
        
        scenario_avg_queue = 0.0
        if scenario_stats.queue_length_samples:
            scenario_avg_queue = sum(q[1] for q in scenario_stats.queue_length_samples) / len(scenario_stats.queue_length_samples)
        
        # Calculate max queue length
        baseline_max_queue = baseline_stats.max_queue_length
        scenario_max_queue = scenario_stats.max_queue_length
        
        # Average wait times
        baseline_avg_wait = baseline_stats.average_wait_time
        scenario_avg_wait = scenario_stats.average_wait_time
        
        # Utilization
        baseline_util = baseline_node.utilization if hasattr(baseline_node, 'utilization') else 0.0
        scenario_util = scenario_node.utilization if hasattr(scenario_node, 'utilization') else 0.0
        
        # Calculate percentage changes
        queue_pct_change = ((scenario_avg_queue - baseline_avg_queue) / baseline_avg_queue * 100) if baseline_avg_queue > 0 else 0.0
        wait_pct_change = ((scenario_avg_wait - baseline_avg_wait) / baseline_avg_wait * 100) if baseline_avg_wait > 0 else 0.0
        util_pct_change = ((scenario_util - baseline_util) / baseline_util * 100) if baseline_util > 0 else 0.0
        
        deltas[node_type] = {
            'avg_queue_delta': scenario_avg_queue - baseline_avg_queue,
            'avg_queue_pct': queue_pct_change,
            'max_queue_delta': scenario_max_queue - baseline_max_queue,
            'avg_wait_delta': scenario_avg_wait - baseline_avg_wait,
            'avg_wait_pct': wait_pct_change,
            'util_delta': scenario_util - baseline_util,
            'util_pct': util_pct_change,
            'baseline_avg_queue': baseline_avg_queue,
            'scenario_avg_queue': scenario_avg_queue,
            'baseline_avg_wait': baseline_avg_wait,
            'scenario_avg_wait': scenario_avg_wait,
        }
    
    return deltas


def create_downstream_changes_summary(
    deltas: Dict[NodeType, Dict[str, float]],
    threshold_pct: float = 5.0,
) -> List[str]:
    """
    Create summary text of key downstream changes.
    
    Args:
        deltas: Dictionary of node deltas from compute_node_deltas
        threshold_pct: Minimum percentage change to include in summary
    
    Returns:
        List of summary strings
    """
    changes = []
    
    for node_type, delta_data in deltas.items():
        node_name = node_type.name.replace("_", " ").title()
        
        # Queue length changes
        queue_pct = delta_data['avg_queue_pct']
        if abs(queue_pct) >= threshold_pct:
            direction = "reduced" if queue_pct < 0 else "increased"
            changes.append(
                f"**{node_name}** queue {direction} {abs(queue_pct):.0f}% "
                f"({delta_data['baseline_avg_queue']:.1f} → {delta_data['scenario_avg_queue']:.1f})"
            )
        
        # Wait time changes
        wait_pct = delta_data['avg_wait_pct']
        if abs(wait_pct) >= threshold_pct:
            direction = "reduced" if wait_pct < 0 else "increased"
            changes.append(
                f"**{node_name}** wait time {direction} {abs(wait_pct):.0f}% "
                f"({delta_data['baseline_avg_wait']:.1f} → {delta_data['scenario_avg_wait']:.1f} min)"
            )
    
    return changes


def _display_hourly_metrics(sim, scenario_params: Dict) -> None:
    """
    Display hour-by-hour metrics for time-window staffing scenario.
    
    Args:
        sim: EDSimulationWithScenarios instance
        scenario_params: Scenario parameters including time window info
    """
    st.subheader("⏰ Hour-by-Hour Impact Analysis")
    
    target_node = scenario_params.get("target_node", NodeType.PROVIDER_ASSESSMENT)
    start_hour = scenario_params.get("time_window_start", 15)
    end_hour = scenario_params.get("time_window_end", 19)
    
    if target_node not in sim.nodes:
        return
    
    node = sim.nodes[target_node]
    stats = node.statistics
    
    # Group queue length samples by hour
    hourly_data = {}
    for time_min, queue_len in stats.queue_length_samples:
        hour = int(time_min / 60) % 24
        if hour not in hourly_data:
            hourly_data[hour] = {"queue_lengths": [], "wait_times": []}
        hourly_data[hour]["queue_lengths"].append(queue_len)
    
    # Group wait times by hour (from patient timestamps)
    for patient in sim.patients:
        if target_node in patient.timestamps:
            ts = patient.timestamps[target_node]
            hour = int(ts.service_start / 60) % 24
            if hour not in hourly_data:
                hourly_data[hour] = {"queue_lengths": [], "wait_times": []}
            hourly_data[hour]["wait_times"].append(ts.wait_time)
    
    # Create DataFrame
    rows = []
    for hour in range(24):
        in_window = False
        if start_hour <= end_hour:
            in_window = start_hour <= hour < end_hour
        else:
            in_window = hour >= start_hour or hour < end_hour
        
        data = hourly_data.get(hour, {"queue_lengths": [], "wait_times": []})
        avg_queue = sum(data["queue_lengths"]) / len(data["queue_lengths"]) if data["queue_lengths"] else 0.0
        max_queue = max(data["queue_lengths"]) if data["queue_lengths"] else 0
        avg_wait = sum(data["wait_times"]) / len(data["wait_times"]) if data["wait_times"] else 0.0
        
        # Get capacity for this hour
        if hasattr(sim._scenario, '_hourly_capacities') and hour in sim._scenario._hourly_capacities:
            capacity = sim._scenario._hourly_capacities[hour].get(target_node, node.capacity)
        else:
            capacity = node.capacity
        
        rows.append({
            "Hour": f"{hour:02d}:00",
            "In Window": "✅ Yes" if in_window else "No",
            "Capacity": capacity,
            "Avg Queue Length": f"{avg_queue:.1f}",
            "Max Queue Length": max_queue,
            "Avg Wait Time (min)": f"{avg_wait:.1f}",
            "Samples": len(data["queue_lengths"]) + len(data["wait_times"]),
        })
    
    df = pd.DataFrame(rows)
    
    # Highlight time window rows
    def highlight_window(row):
        if "✅" in str(row["In Window"]):
            return ['background-color: #e8f5e9'] * len(row)
        return [''] * len(row)
    
    st.dataframe(
        df.style.apply(highlight_window, axis=1),
        use_container_width=True,
        hide_index=True
    )
    
    # Summary
    window_rows = df[df["In Window"].str.contains("✅")]
    non_window_rows = df[~df["In Window"].str.contains("✅")]
    
    if not window_rows.empty and not non_window_rows.empty:
        window_avg_queue = pd.to_numeric(window_rows["Avg Queue Length"]).mean()
        non_window_avg_queue = pd.to_numeric(non_window_rows["Avg Queue Length"]).mean()
        window_avg_wait = pd.to_numeric(window_rows["Avg Wait Time (min)"]).mean()
        non_window_avg_wait = pd.to_numeric(non_window_rows["Avg Wait Time (min)"]).mean()
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                "Queue Reduction",
                f"{(non_window_avg_queue - window_avg_queue):.1f}",
                help="Average queue length reduction during time window"
            )
        with col2:
            st.metric(
                "Wait Time Reduction",
                f"{(non_window_avg_wait - window_avg_wait):.1f} min",
                help="Average wait time reduction during time window"
            )
        with col3:
            improvement_pct = ((non_window_avg_wait - window_avg_wait) / non_window_avg_wait * 100) if non_window_avg_wait > 0 else 0
            st.metric(
                "Improvement",
                f"{improvement_pct:.1f}%",
                help="Percentage improvement in wait times"
            )


def build_patient_trace(patient: Patient) -> pd.DataFrame:
    """
    Build chronological event trace for a patient.
    
    Args:
        patient: Patient object to trace
    
    Returns:
        DataFrame with chronological events
    """
    events = []
    
    # Arrival event
    events.append({
        "Simulation Time": patient.arrival_time,
        "Time (min)": f"{patient.arrival_time:.1f}",
        "Node": "ED Entrance",
        "Event": "Arrived",
        "Wait Time (min)": "-",
        "Service Time (min)": "-",
        "Notes": f"ESI {patient.acuity.value}",
    })
    
    # Process timestamps chronologically
    for node_type, timestamp in sorted(patient.timestamps.items(), key=lambda x: x[1].queue_enter):
        node_name = node_type.name.replace("_", " ").title()
        
        # Enter queue event
        events.append({
            "Simulation Time": timestamp.queue_enter,
            "Time (min)": f"{timestamp.queue_enter:.1f}",
            "Node": node_name,
            "Event": "Entered Queue",
            "Wait Time (min)": "-",
            "Service Time (min)": "-",
            "Notes": "Queued, waiting for available capacity",
        })
        
        # Start service event
        events.append({
            "Simulation Time": timestamp.service_start,
            "Time (min)": f"{timestamp.service_start:.1f}",
            "Node": node_name,
            "Event": "Started Service",
            "Wait Time (min)": f"{timestamp.wait_time:.1f}",
            "Service Time (min)": "-",
            "Notes": f"Waited {timestamp.wait_time:.1f} min",
        })
        
        # Complete service event
        events.append({
            "Simulation Time": timestamp.service_end,
            "Time (min)": f"{timestamp.service_end:.1f}",
            "Node": node_name,
            "Event": "Completed Service",
            "Wait Time (min)": f"{timestamp.wait_time:.1f}",
            "Service Time (min)": f"{timestamp.service_time:.1f}",
            "Notes": f"Total time at node: {timestamp.total_time:.1f} min",
        })
    
    # LWBS event (if applicable)
    if patient.is_lwbs:
        # Find the last timestamp to determine when LWBS occurred
        if patient.timestamps:
            last_timestamp = max(patient.timestamps.values(), key=lambda ts: ts.service_end)
            lwbs_time = last_timestamp.service_end + 1.0  # Approximate
        else:
            lwbs_time = patient.arrival_time + 30.0  # Approximate
        
        events.append({
            "Simulation Time": lwbs_time,
            "Time (min)": f"{lwbs_time:.1f}",
            "Node": patient.current_node.name.replace("_", " ").title() if patient.current_node else "Unknown",
            "Event": "Left Without Being Seen",
            "Wait Time (min)": "-",
            "Service Time (min)": "-",
            "Notes": "Patient left before completing treatment",
        })
    
    # Departure event (if discharged)
    if patient.disposition and not patient.is_lwbs:
        if patient._departure_time:
            events.append({
                "Simulation Time": patient._departure_time,
                "Time (min)": f"{patient._departure_time:.1f}",
                "Node": "ED Exit",
                "Event": "Discharged",
                "Wait Time (min)": "-",
                "Service Time (min)": "-",
                "Notes": f"Disposition: {patient.disposition.name.replace('_', ' ').title()}",
            })
    
    # Sort by simulation time
    events.sort(key=lambda x: x["Simulation Time"])
    
    # Remove simulation time from display (keep for sorting)
    for event in events:
        event.pop("Simulation Time", None)
    
    return pd.DataFrame(events)


def get_queue_snapshot(nodes: Dict[NodeType, Node], snapshot_time: float) -> pd.DataFrame:
    """
    Get queue lengths for all nodes at a specific simulation time.
    
    Args:
        nodes: Dictionary of all nodes
        snapshot_time: Simulation time in minutes
    
    Returns:
        DataFrame with node queue lengths at that time
    """
    snapshot_data = []
    
    for node_type, node in nodes.items():
        node_name = node_type.name.replace("_", " ").title()
        
        # Find queue length at snapshot time
        queue_length = 0
        for time_min, q_len in node.statistics.queue_length_samples:
            if time_min <= snapshot_time:
                queue_length = q_len
            else:
                break
        
        # Get capacity
        capacity = node.capacity
        
        snapshot_data.append({
            "Node": node_name,
            "Queue Length": queue_length,
            "Capacity": capacity,
            "Utilization": f"{(queue_length / capacity * 100):.1f}%" if capacity > 0 else "N/A",
        })
    
    return pd.DataFrame(snapshot_data)


def get_patient_journeys_df(patients: List[Patient], limit: int = 3) -> pd.DataFrame:
    """
    Create a DataFrame of patient journeys.

    Args:
        patients: List of Patient objects
        limit: Maximum number of patients to include

    Returns:
        pandas DataFrame
    """
    completed = [p for p in patients if p.disposition and not p.is_lwbs][:limit]

    rows = []
    for p in completed:
        rows.append({
            "Patient ID": p.id,
            "Acuity": p.acuity.name,
            "Door-to-Triage (min)": round(p.door_to_triage, 1) if p.door_to_triage is not None else None,
            "Door-to-Bed (min)": round(p.door_to_bed, 1) if p.door_to_bed is not None else None,
            "Door-to-Provider (min)": round(p.door_to_provider, 1) if p.door_to_provider is not None else None,
            "LOS (min)": round(p.length_of_stay, 1) if p.length_of_stay is not None else None,
            "LOS (hrs)": round(p.length_of_stay / 60, 2) if p.length_of_stay is not None else None,
        })

    return pd.DataFrame(rows)


def main():
    """Main Streamlit application - Simplified 3-click flow for ED Directors."""
    st.set_page_config(
        page_title="ED Simulation - What If Analysis",
        page_icon="🏥",
        layout="wide",
    )

    st.title("🏥 ED What-If Analysis")
    st.markdown("**Simple 3-click simulation to explore operational changes**")
    
    # Initialize session state
    if 'custom_config' not in st.session_state:
        st.session_state['custom_config'] = EDConfig.from_defaults()
    if 'selected_scenario' not in st.session_state:
        st.session_state['selected_scenario'] = None
    
    # Get current config
    current_config = st.session_state.get('custom_config', EDConfig.from_defaults())
    
    # ===== LANDING PAGE: Always show diagram =====
    st.markdown("---")
    st.header("📊 Your ED Flow Diagram")
    
    # Render draggable diagram (always visible)
    try:
        import sys
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        utils_dir = os.path.join(current_dir, 'utils')
        if utils_dir not in sys.path:
            sys.path.insert(0, utils_dir)
        from draggable_diagram import render_draggable_ed_diagram
        
        # Get nodes dict if simulation has been run
        nodes_dict = None
        if 'last_simulation' in st.session_state:
            sim_data = st.session_state['last_simulation']
            nodes_dict = sim_data['sim'].nodes
        
        # Get capacities
        merged_config = merge_with_defaults(current_config)
        capacities = merged_config.node_capacities
        ed_name = current_config.ed_name if current_config else "Default ED"
        
        # Get saved positions
        positions_key = f"node_positions_{ed_name}"
        saved_positions = st.session_state.get(positions_key, {})
        
        # Render diagram
        clicked_node, updated_positions = render_draggable_ed_diagram(
            nodes_dict=nodes_dict,
            capacities=capacities,
            ed_name=ed_name,
            saved_positions=saved_positions,
        )
        
        if updated_positions:
            st.session_state[positions_key] = updated_positions
            
    except Exception as e:
        # Fallback to Plotly
        diagram_fig = plot_ed_diagram(ed_config=current_config)
        st.plotly_chart(diagram_fig, use_container_width=True)
    
    st.markdown("---")
    
    # ===== 3 BIG SCENARIO BUTTONS =====
    st.header("💡 What If...?")
    st.markdown("**Click any scenario to see the impact (runs 100 simulations automatically)**")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button(
            "What if I add 1 nurse at peak hours (3–7 pm)?",
            use_container_width=True,
            type="primary",
            key="scenario_peak_staffing",
            help="Adds 1 Triage nurse from 15:00-19:00"
        ):
            st.session_state['selected_scenario'] = 'peak_staffing'
            st.rerun()
    
    with col2:
        if st.button(
            "What if boarding takes 40% of beds?",
            use_container_width=True,
            type="primary",
            key="scenario_boarding",
            help="Reduces bed capacity by 40% due to boarding"
        ):
            st.session_state['selected_scenario'] = 'boarding'
            st.rerun()
    
    with col3:
        if st.button(
            "What if we open a fast track for ESI 4-5?",
            use_container_width=True,
            type="primary",
            key="scenario_fast_track",
            help="Opens dedicated fast track pathway for low-acuity patients"
        ):
            st.session_state['selected_scenario'] = 'fast_track'
            st.rerun()
    
    # ===== RESULTS AREA (hidden until scenario clicked) =====
    selected_scenario = st.session_state.get('selected_scenario')
    
    if selected_scenario and st.session_state.get('scenario_results'):
        st.markdown("---")
        st.header("📊 Results: Before vs After")
        
        results = st.session_state['scenario_results']
        baseline_agg = results['baseline_aggregated']
        scenario_agg = results['scenario_aggregated']
        
        # Big summary text
        lwbs_before = baseline_agg.lwbs_rate_mean * 100
        lwbs_after = scenario_agg.lwbs_rate_mean * 100
        lwbs_change = lwbs_after - lwbs_before
        lwbs_change_pct = (lwbs_change / lwbs_before * 100) if lwbs_before > 0 else 0
        
        # Estimate monthly impact (assuming 30 days, 3 patients/hour average)
        monthly_patients = 30 * 24 * 3  # Rough estimate
        patients_saved = monthly_patients * (lwbs_before - lwbs_after) / 100
        
        st.markdown(f"""
        ### 🎯 Impact Summary
        
        **This change would {'reduce' if lwbs_change < 0 else 'increase'} LWBS from {lwbs_before:.2f}% to {lwbs_after:.2f}% 
        ({abs(lwbs_change_pct):.1f}% {'reduction' if lwbs_change < 0 else 'increase'}, saving approximately {abs(patients_saved):.0f} patients/month)**
        """)
        
        # Bar chart: LOS mean, LWBS %, Door-to-Doc
        metrics_data = []
        
        # LOS Mean
        if baseline_agg.mean_los_mean and scenario_agg.mean_los_mean:
            metrics_data.append({"Metric": "LOS Mean (min)", "Baseline": baseline_agg.mean_los_mean, "Scenario": scenario_agg.mean_los_mean})
        
        # LWBS Rate
        metrics_data.append({"Metric": "LWBS Rate (%)", "Baseline": lwbs_before, "Scenario": lwbs_after})
        
        # Door-to-Provider
        if baseline_agg.median_door_to_provider_mean and scenario_agg.median_door_to_provider_mean:
            metrics_data.append({"Metric": "Door-to-Provider (min)", "Baseline": baseline_agg.median_door_to_provider_mean, "Scenario": scenario_agg.median_door_to_provider_mean})
        
        if metrics_data:
            df = pd.DataFrame(metrics_data)
            fig = px.bar(
                df,
                x="Metric",
                y=["Baseline", "Scenario"],
                barmode="group",
                title="Key Metrics: Baseline vs Scenario",
                color_discrete_map={"Baseline": "#2ca02c", "Scenario": "#1f77b4"},
            )
            fig.update_layout(
                xaxis_title="",
                yaxis_title="Value",
                hovermode='x unified',
            )
            st.plotly_chart(fig, use_container_width=True)
        
        # Keep diagram visible (updated if capacity changed)
        if 'last_simulation' in st.session_state:
            sim_data = st.session_state['last_simulation']
            nodes_dict = sim_data['sim'].nodes
            
            # Re-render diagram with updated stats
            try:
                # Get capacities and other config from current_config
                merged_config = merge_with_defaults(current_config)
                diagram_capacities = merged_config.node_capacities
                diagram_ed_name = current_config.ed_name if current_config else "Default ED"
                positions_key = f"node_positions_{diagram_ed_name}"
                diagram_saved_positions = st.session_state.get(positions_key, {})
                
                clicked_node, updated_positions = render_draggable_ed_diagram(
                    nodes_dict=nodes_dict,
                    capacities=diagram_capacities,
                    ed_name=diagram_ed_name,
                    saved_positions=diagram_saved_positions,
                )
                
                if updated_positions:
                    st.session_state[positions_key] = updated_positions
            except Exception as e:
                # Silently fail - diagram will show on next render
                pass
    
    # ===== SIDEBAR: Advanced Settings (collapsed) =====
    with st.sidebar.expander("⚙️ Advanced Settings", expanded=False):
        # Move all advanced features here
        st.markdown("### Configuration")
        config_mode = st.radio(
            "Configuration Mode",
            options=["Use Defaults", "Customize My ED"],
            index=0,
        )
        
        if config_mode == "Customize My ED":
            # All customization options here (abbreviated for space)
            st.info("Full customization options available in advanced mode")
        
        st.markdown("### Simulation Settings")
        warmup_minutes = st.slider("Warmup Period (minutes)", 0, 480, 120)
        # Note: Replications hardcoded to 100 for scenarios, but allow override here
        num_replications_override = st.slider("Number of Runs (override)", 1, 100, 100, help="Default is 100 for scenarios")
        
        st.markdown("### Other Features")
        st.info("Patient trace, step-by-step analysis, and other advanced features available after running simulation")
    
    # ===== SIDEBAR: Backtesting (small section) =====
    st.sidebar.markdown("---")
    st.sidebar.header("📁 Historical Data")
    uploaded_file = st.sidebar.file_uploader(
        "Upload Historical CSV",
        type=['csv'],
        help="CSV with columns: arrival_time (minutes), esi (1-5)"
    )
    
    if uploaded_file is not None:
        try:
            csv_content = uploaded_file.read().decode('utf-8')
            historical_arrivals = parse_historical_csv(csv_content)
            st.sidebar.success(f"✅ Loaded {len(historical_arrivals)} arrivals")
            
            if st.sidebar.button("🔄 Replay & Compare", type="primary"):
                with st.spinner("Running simulation with historical arrivals..."):
                    sim, metrics, scenario_desc = run_simulation_with_historical_arrivals(
                        historical_arrivals=historical_arrivals,
                        scenario_key=None,
                        scenario_params=None,
                        warmup_minutes=120.0,
                    )
                st.success(f"Simulation complete! {metrics.total_patients} patients processed.")
                # Display results (simplified)
                st.subheader("📈 Historical vs Simulated")
                comparison_df = create_comparison_table({}, metrics)
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.sidebar.error(f"Error: {str(e)}")
    
    # ===== AUTO-RUN SCENARIO WHEN BUTTON CLICKED =====
    if selected_scenario and not st.session_state.get('scenario_results'):
        # Determine scenario parameters
        scenario_key = None
        scenario_params = {}
        arrival_rate = current_config.arrival_rate or ARRIVAL_DEFAULTS["mean_arrival_rate"]
        
        if selected_scenario == 'peak_staffing':
            scenario_key = "staffing_adjustment"
            scenario_params = {
                "time_window_start": 15,
                "time_window_end": 19,
                "target_node": NodeType.TRIAGE,
                "additional_staff": 1,
            }
        elif selected_scenario == 'boarding':
            scenario_key = "boarding"
            scenario_params = {"boarding_pct": 0.4}
        elif selected_scenario == 'fast_track':
            scenario_key = "vertical_track"
            scenario_params = {"fast_track_providers": 2}
        
        # Run with 100 replications (hardcoded)
        num_replications = 100
        ed_config = current_config
        warmup_minutes = 120.0
        
        with st.spinner(f"Running {num_replications} simulations for statistical confidence..."):
            progress_bar = st.progress(0)
            
            # Run scenario
            def run_scenario_sim(seed: int):
                return run_simulation(
                    arrival_rate=arrival_rate,
                    scenario_key=scenario_key,
                    scenario_params=scenario_params,
                    seed=seed,
                    ed_config=ed_config,
                    warmup_minutes=warmup_minutes,
                )
            
            scenario_results, scenario_aggregated = run_replications(
                simulation_func=run_scenario_sim,
                num_replications=num_replications,
                base_seed=42,
            )
            
            progress_bar.progress(0.5)
            
            # Run baseline
            def run_baseline_sim(seed: int):
                return run_simulation(
                    arrival_rate=arrival_rate,
                    scenario_key=None,
                    scenario_params=None,
                    seed=seed,
                    ed_config=ed_config,
                    warmup_minutes=warmup_minutes,
                )
            
            baseline_results, baseline_aggregated = run_replications(
                simulation_func=run_baseline_sim,
                num_replications=num_replications,
                base_seed=42,
            )
            
            progress_bar.progress(1.0)
            progress_bar.empty()
        
        # Store results
        st.session_state['scenario_results'] = {
            'baseline_aggregated': baseline_aggregated,
            'scenario_aggregated': scenario_aggregated,
            'baseline_results': baseline_results,
            'scenario_results': scenario_results,
        }
        
        # Store last simulation for diagram updates
        st.session_state['last_simulation'] = {
            'sim': scenario_results[0].sim,
            'metrics': scenario_results[0].metrics,
            'scenario_desc': f"Scenario: {selected_scenario}",
        }
        
        st.rerun()


if __name__ == "__main__":
    main()
