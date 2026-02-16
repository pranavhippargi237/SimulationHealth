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
    """Main Streamlit application."""
    st.set_page_config(
        page_title="ED Digital Twin MVP",
        page_icon="🏥",
        layout="wide",
    )

    st.title("ED Digital Twin MVP")
    st.markdown("*Discrete-event simulation of Emergency Department patient flow*")
    
    # Show ED diagram in main area if requested
    if st.session_state.get('show_ed_diagram', False):
        st.header("📊 ED Layout Diagram")
        
        # Get current config
        if 'custom_config' in st.session_state:
            current_config = st.session_state['custom_config']
        else:
            current_config = EDConfig.from_defaults()
        
        # Choose diagram mode
        diagram_mode = st.radio(
            "Diagram Mode",
            options=["🗺️ Clickable Map (Folium)", "📊 Static Plotly", "🕹️ Interactive Animation"],
            horizontal=True,
            key="diagram_mode",
        )
        
        if diagram_mode == "🗺️ Clickable Map (Folium)":
            # Draggable, clickable node diagram
            try:
                # Import with proper path handling
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
                if current_config:
                    merged_config = merge_with_defaults(current_config)
                    capacities = merged_config.node_capacities
                else:
                    capacities = NODE_CAPACITY_DEFAULTS.copy()
                
                ed_name = current_config.ed_name if current_config else "Default ED"
                
                # Get saved positions from session state
                positions_key = f"node_positions_{ed_name}"
                saved_positions = st.session_state.get(positions_key, {})
                
                st.markdown("### 🎯 Draggable ED Diagram")
                st.info("💡 **Drag nodes to reposition them! Click a node to see details in the sidebar.**")
                
                # Create two columns: diagram on left, details on right
                col_diagram, col_details = st.columns([2, 1])
                
                with col_diagram:
                    # Render draggable diagram
                    clicked_node, updated_positions = render_draggable_ed_diagram(
                        nodes_dict=nodes_dict,
                        capacities=capacities,
                        ed_name=ed_name,
                        saved_positions=saved_positions,
                    )
                    
                    # Add a button to save current layout
                    if st.button("💾 Save Current Layout", use_container_width=True):
                        st.info("💡 Drag nodes to reposition them, then click 'Save Current Layout' to persist positions.")
                        # Positions will be saved via the component's communication
                        # For now, we'll rely on the user to manually save via button
                    
                    # Note: Due to Streamlit component limitations, position updates
                    # are handled via localStorage in the JavaScript. To fully persist,
                    # we'd need a custom Streamlit component. For now, positions
                    # persist within the browser session.
                
                with col_details:
                    st.markdown("### 📊 Node Details")
                    
                    # Show clicked node or default to first node
                    if clicked_node:
                        st.session_state['selected_node'] = clicked_node
                    
                    selected_node_id = st.session_state.get('selected_node', NodeType.TRIAGE.name)
                    
                    # Node selector dropdown
                    all_nodes = [nt.name for nt in NodeType]
                    selected_node_id = st.selectbox(
                        "Select Node:",
                        options=all_nodes,
                        index=all_nodes.index(selected_node_id) if selected_node_id in all_nodes else 0,
                        key="node_selector_details",
                    )
                    
                    # Get node stats
                    node_name = selected_node_id.replace("_", " ").title()
                    cap = capacities.get(selected_node_id, 1)
                    
                    # Get real-time stats if available
                    current_queue = 0
                    utilization = 0.0
                    in_service = 0
                    avg_wait_time = 0.0
                    lwbs_count = 0
                    
                    if nodes_dict and NodeType[selected_node_id] in nodes_dict:
                        node = nodes_dict[NodeType[selected_node_id]]
                        current_queue = node.queue_length
                        utilization = node.utilization
                        in_service = len(node._patients_in_service)
                        avg_wait_time = node.statistics.average_wait_time
                        lwbs_count = node.statistics.patients_lwbs
                    
                    # Display metrics
                    st.metric("Capacity", cap)
                    st.metric("Current Queue", current_queue)
                    st.metric("In Service", in_service)
                    st.metric("Utilization", f"{utilization*100:.1f}%")
                    st.metric("Avg Wait Time", f"{avg_wait_time:.1f} min")
                    st.metric("LWBS Count", lwbs_count)
                    
                    # Show flow information
                    st.markdown("---")
                    st.markdown("### 🔄 Flow Information")
                    
                    # Find pathways using this node
                    routing_engine = RoutingEngine()
                    pathways_using = []
                    for acuity in [Acuity.ESI_1, Acuity.ESI_2, Acuity.ESI_3, Acuity.ESI_4, Acuity.ESI_5]:
                        pathway = routing_engine.get_pathway(acuity)
                        if NodeType[selected_node_id] in pathway:
                            if acuity in [Acuity.ESI_1, Acuity.ESI_2]:
                                pathways_using.append("Critical (ESI 1-2)")
                            elif acuity == Acuity.ESI_3:
                                pathways_using.append("Standard (ESI 3)")
                            else:
                                pathways_using.append("Fast Track (ESI 4-5)")
                    
                    pathways_using = list(set(pathways_using))
                    if pathways_using:
                        st.markdown("**Used in pathways:**")
                        for pathway in pathways_using:
                            st.markdown(f"- {pathway}")
                        
            except ImportError as e:
                st.error(f"⚠️ **Error loading draggable diagram:** {str(e)}")
                # Fallback to Plotly
                diagram_fig = plot_ed_diagram(ed_config=current_config)
                st.plotly_chart(diagram_fig, use_container_width=True)
            except Exception as e:
                st.error(f"⚠️ **Error rendering diagram:** {str(e)}")
                import traceback
                st.code(traceback.format_exc())
                # Fallback to Plotly
                diagram_fig = plot_ed_diagram(ed_config=current_config)
                st.plotly_chart(diagram_fig, use_container_width=True)
        
        elif diagram_mode == "📊 Static Plotly":
            # Static Plotly diagram
            diagram_fig = plot_ed_diagram(ed_config=current_config)
            st.plotly_chart(diagram_fig, use_container_width=True)
        else:
            # Interactive animated diagram
            st.markdown("### 🕹️ Interactive ED Animation")
            st.markdown("Watch patients move through your ED in real-time! Use controls to step through time.")
            
            # Check if we have simulation data
            if 'last_simulation' in st.session_state:
                sim_data = st.session_state['last_simulation']
                sim = sim_data['sim']
                patients = sim.patients
                
                # Get time range
                if patients:
                    min_time = min(p.arrival_time for p in patients)
                    max_time = max(
                        (p._departure_time if p._departure_time else p.arrival_time + 480)
                        for p in patients if p.arrival_time
                    ) if patients else 1440
                else:
                    min_time = 0
                    max_time = 1440
                
                # Animation controls
                col_play1, col_play2, col_play3, col_play4 = st.columns(4)
                
                with col_play1:
                    if st.button("⏮️ Start", use_container_width=True, key="anim_start"):
                        st.session_state['anim_time'] = min_time
                        st.rerun()
                
                with col_play2:
                    if st.button("⏪ Step Back", use_container_width=True, key="anim_back"):
                        current_anim_time = st.session_state.get('anim_time', min_time)
                        st.session_state['anim_time'] = max(min_time, current_anim_time - 30)
                        st.rerun()
                
                with col_play3:
                    if st.button("⏩ Step Forward", use_container_width=True, key="anim_forward"):
                        current_anim_time = st.session_state.get('anim_time', min_time)
                        st.session_state['anim_time'] = min(max_time, current_anim_time + 30)
                        st.rerun()
                
                with col_play4:
                    if st.button("⏭️ End", use_container_width=True, key="anim_end"):
                        st.session_state['anim_time'] = max_time
                        st.rerun()
                
                # Time slider
                current_anim_time = st.session_state.get('anim_time', min_time)
                anim_time = st.slider(
                    "Simulation Time (minutes)",
                    min_value=int(min_time),
                    max_value=int(max_time),
                    value=int(current_anim_time),
                    step=5,
                    key="anim_time_slider",
                    help="Drag to see ED state at any point in time",
                )
                st.session_state['anim_time'] = float(anim_time)
                
                # Auto-play toggle
                auto_play = st.checkbox("▶️ Auto-Play (updates every 2 seconds)", value=False, key="auto_play")
                
                if auto_play:
                    import time
                    time.sleep(2)
                    current_anim_time = st.session_state.get('anim_time', min_time)
                    if current_anim_time < max_time:
                        st.session_state['anim_time'] = min(max_time, current_anim_time + 10)
                        st.rerun()
                
                # Create animated diagram
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
                
                anim_fig = plot_animated_ed_diagram(
                    ed_config=current_config,
                    patients=patients,
                    current_time=st.session_state.get('anim_time', min_time),
                    node_positions=node_positions,
                )
                st.plotly_chart(anim_fig, use_container_width=True)
                
                # Statistics at current time
                st.markdown("#### 📊 Current State Statistics")
                col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
                
                current_time = st.session_state.get('anim_time', min_time)
                active_patients = [p for p in patients if p.arrival_time <= current_time and (not p._departure_time or current_time < p._departure_time)]
                in_queue = sum(1 for p in active_patients if p._current_queue_enter_time and current_time >= p._current_queue_enter_time)
                in_service = sum(1 for p in active_patients if any(ts.service_start <= current_time < ts.service_end for ts in p.timestamps.values()))
                completed = sum(1 for p in patients if p._departure_time and current_time >= p._departure_time)
                
                with col_stat1:
                    st.metric("Active Patients", len(active_patients))
                with col_stat2:
                    st.metric("In Queue", in_queue)
                with col_stat3:
                    st.metric("In Service", in_service)
                with col_stat4:
                    st.metric("Completed", completed)
            else:
                st.info("💡 Run a simulation first to see the interactive animation!")
                # Show static diagram as placeholder
                diagram_fig = plot_ed_diagram(ed_config=current_config)
                st.plotly_chart(diagram_fig, use_container_width=True)
        
        # Show pathway details
        with st.expander("📋 Patient Pathways Details"):
            routing_engine = RoutingEngine()
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.markdown("**🔴 Critical Pathway (ESI 1-2)**")
                pathway = routing_engine.get_pathway(Acuity.ESI_1)
                pathway_text = " → ".join([n.name.replace("_", " ").title() for n in pathway])
                st.markdown(f"`{pathway_text}`")
            
            with col2:
                st.markdown("**🔵 Standard Pathway (ESI 3)**")
                pathway = routing_engine.get_pathway(Acuity.ESI_3)
                pathway_text = " → ".join([n.name.replace("_", " ").title() for n in pathway])
                st.markdown(f"`{pathway_text}`")
            
            with col3:
                st.markdown("**🟢 Fast Track Pathway (ESI 4-5)**")
                pathway = routing_engine.get_pathway(Acuity.ESI_4)
                pathway_text = " → ".join([n.name.replace("_", " ").title() for n in pathway])
                st.markdown(f"`{pathway_text}`")
        
        if st.button("❌ Close Diagram"):
            st.session_state['show_ed_diagram'] = False
            st.rerun()
        
        st.markdown("---")

    # Sidebar configuration
    st.sidebar.header("🏥 ED Configuration")
    
    # Configuration mode
    config_mode = st.sidebar.radio(
        "Configuration Mode",
        options=["Use Defaults", "Customize My ED"],
        index=0,
        help="Choose to use default parameters or customize for your ED",
    )
    
    # Initialize session state for custom config
    if 'custom_config' not in st.session_state:
        st.session_state['custom_config'] = EDConfig.from_defaults()
    
    custom_config = None
    arrival_rate = 3.0
    
    if config_mode == "Customize My ED":
        with st.sidebar.expander("⚙️ Customize ED Parameters", expanded=True):
            # ED Name
            ed_name = st.text_input(
                "ED Name",
                value=st.session_state['custom_config'].ed_name,
                help="Name for this ED configuration",
            )
            
            st.markdown("**Node Capacities:**")
            node_capacities = {}
            for node_name in ["TRIAGE", "REGISTRATION", "BED_ASSIGNMENT", "FAST_TRACK", 
                            "PROVIDER_ASSESSMENT", "DIAGNOSTICS", "TREATMENT", "DISPOSITION"]:
                default = NODE_CAPACITY_DEFAULTS.get(node_name, 1)
                current = st.session_state['custom_config'].node_capacities.get(node_name, default)
                capacity = st.number_input(
                    f"{node_name.replace('_', ' ').title()}",
                    min_value=1,
                    max_value=50,
                    value=current,
                    step=1,
                    key=f"capacity_{node_name}",
                )
                node_capacities[node_name] = capacity
            
            st.markdown("**Arrival Rate:**")
            default_arrival = ARRIVAL_DEFAULTS["mean_arrival_rate"]
            current_arrival = st.session_state['custom_config'].arrival_rate or default_arrival
            arrival_rate = st.slider(
                "Patients per Hour",
                min_value=1.0,
                max_value=10.0,
                value=current_arrival,
                step=0.5,
                key="custom_arrival_rate",
            )
            
            st.markdown("**Acuity Distribution:**")
            acuity_dist = {}
            total = 0.0
            for esi in [1, 2, 3, 4, 5]:
                default = ACUITY_DISTRIBUTION.get(esi, 0.0)
                current = st.session_state['custom_config'].acuity_distribution.get(esi, default)
                pct = st.slider(
                    f"ESI {esi} (%)",
                    min_value=0,
                    max_value=100,
                    value=int(current * 100),
                    step=1,
                    key=f"acuity_{esi}",
                )
                acuity_dist[esi] = pct / 100.0
                total += pct / 100.0
            
            if abs(total - 1.0) > 0.01:
                st.warning(f"⚠️ Acuity distribution totals {total*100:.1f}% (should be 100%)")
            
            # Save/Load configuration
            col_save1, col_save2 = st.columns(2)
            with col_save1:
                if st.button("💾 Save Config", use_container_width=True):
                    custom_config = EDConfig(
                        ed_name=ed_name,
                        node_capacities=node_capacities,
                        acuity_distribution=acuity_dist,
                        arrival_rate=arrival_rate,
                    )
                    st.session_state['custom_config'] = custom_config
                    st.success("Configuration saved!")
            
            with col_save2:
                uploaded_config = st.file_uploader(
                    "📁 Load Config",
                    type=['yaml', 'yml'],
                    help="Upload a saved ED configuration file",
                    key="config_uploader",
                )
                if uploaded_config is not None:
                    try:
                        data = yaml.safe_load(uploaded_config)
                        loaded_config = EDConfig.from_dict(data)
                        st.session_state['custom_config'] = loaded_config
                        st.success(f"Loaded: {loaded_config.ed_name}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error loading config: {str(e)}")
            
            # Download current config
            if st.button("📥 Download Config", use_container_width=True):
                config_dict = st.session_state['custom_config'].to_dict()
                yaml_str = yaml.dump(config_dict, default_flow_style=False)
                st.download_button(
                    label="Download YAML",
                    data=yaml_str,
                    file_name=f"{ed_name.replace(' ', '_')}_config.yaml",
                    mime="text/yaml",
                )
            
            # Pathway Editor
            st.markdown("---")
            st.markdown("### 🔄 Edit Patient Flow Pathways")
            st.info("💡 **Rearrange the order of nodes for each ESI acuity level.** All pathways must start with TRIAGE and end with DISPOSITION.")
            
            # Get current pathways or defaults
            routing_engine = RoutingEngine()
            current_config = st.session_state['custom_config']
            if hasattr(current_config, 'pathways') and current_config.pathways and "pathways" in current_config.pathways:
                pathways_config = current_config.pathways["pathways"]
            else:
                pathways_config = routing_engine.get_pathways_config()["pathways"]
            
            # Pathway editor for each ESI level
            pathway_tabs = st.tabs(["ESI 1-2 (Critical)", "ESI 3 (Standard)", "ESI 4-5 (Fast Track)"])
            
            updated_pathways = {}
            
            # ESI 1-2 (Critical)
            with pathway_tabs[0]:
                st.markdown("**Critical Pathway (ESI 1-2)**")
                esi1_config = pathways_config.get("ESI_1", {"nodes": ["TRIAGE", "BED_ASSIGNMENT", "PROVIDER_ASSESSMENT", "DIAGNOSTICS", "TREATMENT", "DISPOSITION"]})
                esi1_nodes = esi1_config.get("nodes", [])
                
                # Node reordering interface
                st.markdown("**Current pathway order:**")
                for i, node_name in enumerate(esi1_nodes):
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.text(f"{i+1}. {node_name.replace('_', ' ').title()}")
                    with col2:
                        if i > 0 and st.button("⬆️", key=f"esi1_up_{i}"):
                            esi1_nodes[i], esi1_nodes[i-1] = esi1_nodes[i-1], esi1_nodes[i]
                            st.rerun()
                    with col3:
                        if i < len(esi1_nodes) - 1 and st.button("⬇️", key=f"esi1_down_{i}"):
                            esi1_nodes[i], esi1_nodes[i+1] = esi1_nodes[i+1], esi1_nodes[i]
                            st.rerun()
                
                # Add/Remove nodes
                col_add1, col_rem1 = st.columns(2)
                with col_add1:
                    available_nodes = [nt.name for nt in NodeType if nt.name not in esi1_nodes]
                    if available_nodes:
                        node_to_add = st.selectbox("Add node:", ["Select..."] + available_nodes, key="add_esi1")
                        if node_to_add != "Select..." and st.button("➕ Add", key="add_btn_esi1"):
                            # Insert before DISPOSITION
                            esi1_nodes.insert(-1, node_to_add)
                            st.rerun()
                
                with col_rem1:
                    if len(esi1_nodes) > 2:  # Must keep TRIAGE and DISPOSITION
                        node_to_remove = st.selectbox(
                            "Remove node:",
                            [n for n in esi1_nodes if n not in ["TRIAGE", "DISPOSITION"]],
                            key="rem_esi1"
                        )
                        if st.button("➖ Remove", key="rem_btn_esi1"):
                            esi1_nodes.remove(node_to_remove)
                            st.rerun()
                
                updated_pathways["ESI_1"] = {"name": "Critical", "nodes": esi1_nodes}
                updated_pathways["ESI_2"] = {"name": "Critical", "nodes": esi1_nodes.copy()}  # Same as ESI_1
            
            # ESI 3 (Standard)
            with pathway_tabs[1]:
                st.markdown("**Standard Pathway (ESI 3)**")
                esi3_config = pathways_config.get("ESI_3", {"nodes": ["TRIAGE", "REGISTRATION", "BED_ASSIGNMENT", "PROVIDER_ASSESSMENT", "DIAGNOSTICS", "TREATMENT", "DISPOSITION"]})
                esi3_nodes = esi3_config.get("nodes", [])
                
                st.markdown("**Current pathway order:**")
                for i, node_name in enumerate(esi3_nodes):
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.text(f"{i+1}. {node_name.replace('_', ' ').title()}")
                    with col2:
                        if i > 0 and st.button("⬆️", key=f"esi3_up_{i}"):
                            esi3_nodes[i], esi3_nodes[i-1] = esi3_nodes[i-1], esi3_nodes[i]
                            st.rerun()
                    with col3:
                        if i < len(esi3_nodes) - 1 and st.button("⬇️", key=f"esi3_down_{i}"):
                            esi3_nodes[i], esi3_nodes[i+1] = esi3_nodes[i+1], esi3_nodes[i]
                            st.rerun()
                
                col_add3, col_rem3 = st.columns(2)
                with col_add3:
                    available_nodes = [nt.name for nt in NodeType if nt.name not in esi3_nodes]
                    if available_nodes:
                        node_to_add = st.selectbox("Add node:", ["Select..."] + available_nodes, key="add_esi3")
                        if node_to_add != "Select..." and st.button("➕ Add", key="add_btn_esi3"):
                            esi3_nodes.insert(-1, node_to_add)
                            st.rerun()
                
                with col_rem3:
                    if len(esi3_nodes) > 2:
                        node_to_remove = st.selectbox(
                            "Remove node:",
                            [n for n in esi3_nodes if n not in ["TRIAGE", "DISPOSITION"]],
                            key="rem_esi3"
                        )
                        if st.button("➖ Remove", key="rem_btn_esi3"):
                            esi3_nodes.remove(node_to_remove)
                            st.rerun()
                
                updated_pathways["ESI_3"] = {"name": "Standard", "nodes": esi3_nodes}
            
            # ESI 4-5 (Fast Track)
            with pathway_tabs[2]:
                st.markdown("**Fast Track Pathway (ESI 4-5)**")
                esi4_config = pathways_config.get("ESI_4", {"nodes": ["TRIAGE", "FAST_TRACK", "PROVIDER_ASSESSMENT", "DISPOSITION"]})
                esi4_nodes = esi4_config.get("nodes", [])
                
                st.markdown("**Current pathway order:**")
                for i, node_name in enumerate(esi4_nodes):
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.text(f"{i+1}. {node_name.replace('_', ' ').title()}")
                    with col2:
                        if i > 0 and st.button("⬆️", key=f"esi4_up_{i}"):
                            esi4_nodes[i], esi4_nodes[i-1] = esi4_nodes[i-1], esi4_nodes[i]
                            st.rerun()
                    with col3:
                        if i < len(esi4_nodes) - 1 and st.button("⬇️", key=f"esi4_down_{i}"):
                            esi4_nodes[i], esi4_nodes[i+1] = esi4_nodes[i+1], esi4_nodes[i]
                            st.rerun()
                
                col_add4, col_rem4 = st.columns(2)
                with col_add4:
                    available_nodes = [nt.name for nt in NodeType if nt.name not in esi4_nodes]
                    if available_nodes:
                        node_to_add = st.selectbox("Add node:", ["Select..."] + available_nodes, key="add_esi4")
                        if node_to_add != "Select..." and st.button("➕ Add", key="add_btn_esi4"):
                            esi4_nodes.insert(-1, node_to_add)
                            st.rerun()
                
                with col_rem4:
                    if len(esi4_nodes) > 2:
                        node_to_remove = st.selectbox(
                            "Remove node:",
                            [n for n in esi4_nodes if n not in ["TRIAGE", "DISPOSITION"]],
                            key="rem_esi4"
                        )
                        if st.button("➖ Remove", key="rem_btn_esi4"):
                            esi4_nodes.remove(node_to_remove)
                            st.rerun()
                
                updated_pathways["ESI_4"] = {"name": "Fast Track", "nodes": esi4_nodes}
                updated_pathways["ESI_5"] = {"name": "Fast Track", "nodes": esi4_nodes.copy()}  # Same as ESI_4
            
            # Save pathways button
            if st.button("💾 Save Pathway Changes", use_container_width=True, type="primary"):
                # Update config with new pathways
                current_config = st.session_state['custom_config']
                current_config.pathways = {"pathways": updated_pathways}
                st.session_state['custom_config'] = current_config
                st.success("✅ Pathways updated! Changes will apply to new simulations.")
            
            # Download pathways as separate YAML
            if st.button("📥 Download Pathways YAML", use_container_width=True):
                pathways_yaml = yaml.dump({"pathways": updated_pathways}, default_flow_style=False)
                st.download_button(
                    label="Download Pathways",
                    data=pathways_yaml,
                    file_name="pathways.yaml",
                    mime="text/yaml",
                )
            
            # Load pathways from file
            uploaded_pathways = st.file_uploader(
                "📁 Load Pathways YAML",
                type=['yaml', 'yml'],
                help="Upload a pathways configuration file",
                key="pathways_uploader",
            )
            if uploaded_pathways is not None:
                try:
                    pathways_data = yaml.safe_load(uploaded_pathways)
                    if "pathways" in pathways_data:
                        current_config = st.session_state['custom_config']
                        current_config.pathways = pathways_data
                        st.session_state['custom_config'] = current_config
                        st.success("✅ Pathways loaded!")
                        st.rerun()
                    else:
                        st.error("Invalid pathways file format. Expected 'pathways' key.")
                except Exception as e:
                    st.error(f"Error loading pathways: {str(e)}")
            
            # Show ED diagram button
            st.markdown("---")
            st.markdown("### 📊 Visual Diagram")
            if st.button("🖼️ Show ED Layout Diagram", use_container_width=True, type="primary"):
                st.session_state['show_ed_diagram'] = True
                st.rerun()
        
        # Use custom config values
        custom_config = st.session_state['custom_config']
        arrival_rate = custom_config.arrival_rate or ARRIVAL_DEFAULTS["mean_arrival_rate"]
    else:
        # Use defaults
    arrival_rate = st.sidebar.slider(
        "Arrival Rate (patients/hour)",
        min_value=1.0,
        max_value=10.0,
        value=3.0,
        step=0.5,
        help="Average number of patients arriving per hour (Poisson process)",
    )

    # Warmup period input
    warmup_minutes = st.sidebar.slider(
        "Warmup Period (minutes)",
        min_value=0,
        max_value=480,
        value=120,
        step=15,
        help="Initial period excluded from statistics. Patients arriving during warmup are discarded. Default: 120 minutes (2 hours).",
    )
    
    # Number of replications
    num_replications = st.sidebar.slider(
        "Number of Runs",
        min_value=1,
        max_value=20,
        value=10,
        step=1,
        help="Run multiple simulations with different random seeds for statistical confidence. 3+ runs enable confidence intervals.",
    )

        # Show diagram button for default mode too
    st.sidebar.markdown("---")
        st.sidebar.markdown("### 📊 Visual Diagram")
        if st.sidebar.button("🖼️ Show ED Layout Diagram", use_container_width=True, type="primary"):
            st.session_state['show_ed_diagram'] = True
            st.rerun()

    st.sidebar.markdown("---")
    
    # Show close button if diagram is open
    if st.session_state.get('show_ed_diagram', False):
        if st.sidebar.button("❌ Close Diagram", use_container_width=True):
            st.session_state['show_ed_diagram'] = False
            st.rerun()

    # Scenario selection
    st.sidebar.header("Scenario")
    scenario_label = st.sidebar.selectbox(
        "Select Scenario",
        options=list(SCENARIO_OPTIONS.keys()),
        index=0,
        help="Apply a pre-configured scenario to test ED performance",
    )
    scenario_key = SCENARIO_OPTIONS[scenario_label]

    # Scenario-specific parameters (conditional sliders)
    scenario_params = {}
    scenario_display_info = {"name": scenario_label, "params": {}}

    if scenario_key == "boarding":
        st.sidebar.markdown("##### Boarding Settings")
        boarding_pct = st.sidebar.slider(
            "Beds Occupied by Boarding (%)",
            min_value=10,
            max_value=80,
            value=40,
            step=5,
            help="Percentage of ED beds occupied by admitted patients waiting for inpatient beds",
        )
        scenario_params["boarding_pct"] = boarding_pct / 100.0
        scenario_display_info["params"]["Beds Occupied"] = f"{boarding_pct}%"
        scenario_display_info["params"]["Available Beds"] = f"{int(20 * (1 - boarding_pct/100))}"
        st.sidebar.caption(f"Reduces bed capacity from 20 to {int(20 * (1 - boarding_pct/100))} beds")

    elif scenario_key == "vertical_track":
        st.sidebar.markdown("##### Fast Track Settings")
        ft_providers = st.sidebar.slider(
            "Fast Track Providers",
            min_value=1,
            max_value=5,
            value=2,
            step=1,
            help="Number of dedicated providers for ESI 4-5 patients",
        )
        scenario_params["fast_track_providers"] = ft_providers
        scenario_display_info["params"]["Fast Track Providers"] = str(ft_providers)
        st.sidebar.caption("ESI 4-5 patients use fast track pathway (skip bed assignment)")

    elif scenario_key == "staffing_adjustment":
        st.sidebar.markdown("##### Time-Window Staffing Settings")
        
        # Time window controls
        col1, col2 = st.sidebar.columns(2)
        with col1:
            start_hour = st.sidebar.slider(
                "Start Time (Hour)",
                min_value=0,
                max_value=23,
                value=15,
                step=1,
                help="Start hour for staffing increase (0-23)",
            )
        with col2:
            end_hour = st.sidebar.slider(
                "End Time (Hour)",
                min_value=0,
                max_value=23,
                value=19,
                step=1,
                help="End hour for staffing increase (0-23)",
            )
        
        # Node selection
        node_options = {
            "Triage": NodeType.TRIAGE,
            "Registration": NodeType.REGISTRATION,
            "Bed Assignment": NodeType.BED_ASSIGNMENT,
            "Fast Track": NodeType.FAST_TRACK,
            "Provider Assessment": NodeType.PROVIDER_ASSESSMENT,
            "Diagnostics": NodeType.DIAGNOSTICS,
            "Treatment": NodeType.TREATMENT,
            "Disposition": NodeType.DISPOSITION,
        }
        
        selected_node_label = st.sidebar.selectbox(
            "Target Node",
            options=list(node_options.keys()),
            index=4,  # Default to Provider Assessment
            help="Node to increase capacity during time window",
        )
        target_node = node_options[selected_node_label]
        
        # Additional staff count
        additional_staff = st.sidebar.slider(
            "Additional Staff Count",
            min_value=0,
            max_value=5,
            value=1,
            step=1,
            help="Number of additional staff to add during time window",
        )
        
        scenario_params["time_window_start"] = start_hour
        scenario_params["time_window_end"] = end_hour
        scenario_params["target_node"] = target_node
        scenario_params["additional_staff"] = additional_staff
        
        start_str = f"{start_hour:02d}:00"
        end_str = f"{end_hour:02d}:00"
        scenario_display_info["params"]["Time Window"] = f"{start_str}-{end_str}"
        scenario_display_info["params"]["Target Node"] = selected_node_label
        scenario_display_info["params"]["Additional Staff"] = str(additional_staff)
        
        st.sidebar.caption(f"Adds {additional_staff} {selected_node_label} staff from {start_str} to {end_str}")

    elif scenario_key == "surge":
        st.sidebar.markdown("##### Surge Settings")
        surge_pct = st.sidebar.slider(
            "Arrival Rate Increase (%)",
            min_value=10,
            max_value=100,
            value=30,
            step=5,
            help="Percentage increase in patient arrival rate",
        )
        scenario_params["surge_pct"] = surge_pct / 100.0
        effective_rate = arrival_rate * (1 + surge_pct / 100)
        scenario_display_info["params"]["Arrival Rate Increase"] = f"{surge_pct}%"
        scenario_display_info["params"]["Effective Rate"] = f"{effective_rate:.1f} patients/hr"
        st.sidebar.caption(f"Effective arrival rate: {effective_rate:.1f} patients/hour")

    st.sidebar.markdown("---")
    
    # Staffing Optimization Section
    st.sidebar.header("🔍 Find Best Staffing Adjustment")
    
    max_fte = st.sidebar.slider(
        "Max Total FTE",
        min_value=5.0,
        max_value=30.0,
        value=15.0,
        step=0.5,
        help="Maximum total Full-Time Equivalent (FTE) staffing budget",
    )
    
    goal_options = {
        "Minimize LWBS": OptimizationGoal.MINIMIZE_LWBS,
        "Maximize Utilization": OptimizationGoal.MAXIMIZE_UTILIZATION,
        "Cost Neutral": OptimizationGoal.COST_NEUTRAL,
    }
    
    goal_label = st.sidebar.selectbox(
        "Priority Goal",
        options=list(goal_options.keys()),
        index=0,
        help="Optimization objective",
    )
    optimization_goal = goal_options[goal_label]
    
    if st.sidebar.button("💡 Suggest 2–3 Options", type="primary"):
        st.session_state['run_optimization'] = True
        st.session_state['optimization_max_fte'] = max_fte
        st.session_state['optimization_goal'] = optimization_goal
        st.session_state['optimization_arrival_rate'] = arrival_rate
    
    st.sidebar.markdown("---")
    
    # Optional: System snapshot slider (for patient trace tab)
    st.sidebar.markdown("### 🔍 Patient Trace Tools")
    show_snapshot = st.sidebar.checkbox(
        "Enable System Snapshot",
        value=False,
        help="Show system snapshot slider in Patient Journey Trace tab"
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        **Simulation Settings:**
        - 24-hour duration
        - ESI acuity distribution
        - LWBS monitoring enabled
        """
    )

    # Main content
    st.header("📊 ED Simulation")
    
    # Staffing Optimization Results
    if st.session_state.get('run_optimization', False):
        st.subheader("💡 Staffing Optimization Suggestions")
        
        max_fte = st.session_state.get('optimization_max_fte', 15.0)
        optimization_goal = st.session_state.get('optimization_goal', OptimizationGoal.MINIMIZE_LWBS)
        opt_arrival_rate = st.session_state.get('optimization_arrival_rate', 3.0)
        
        with st.spinner("Generating staffing suggestions..."):
            # Initialize optimizer
            optimizer = StaffingOptimizer()
            
            # Generate suggestions
            suggestions = optimizer.generate_suggestions(
                max_fte=max_fte,
                goal=optimization_goal,
                num_suggestions=3,
            )
            
            if not suggestions:
                st.warning("No feasible suggestions found within FTE constraint.")
            else:
                st.info(f"Found {len(suggestions)} staffing options within {max_fte:.1f} FTE budget")
                
                # Evaluate each suggestion
                progress_bar = st.progress(0)
                evaluated_suggestions = []
                
                for i, suggestion in enumerate(suggestions):
                    progress_bar.progress((i + 1) / len(suggestions))
                    evaluated = evaluate_staffing_suggestion(
                        suggestion,
                        arrival_rate=opt_arrival_rate,
                        duration_hours=24,
                        seed=42 + i,  # Different seed for each suggestion
                        warmup_minutes=warmup_minutes,
                    )
                    
                    # Recalculate score based on goal and actual metrics
                    if optimization_goal == OptimizationGoal.MINIMIZE_LWBS:
                        # Lower LWBS = higher score
                        evaluated.score = (1.0 - evaluated.expected_lwbs_rate) * 100
                    elif optimization_goal == OptimizationGoal.MAXIMIZE_UTILIZATION:
                        # Higher utilization = higher score
                        evaluated.score = evaluated.expected_utilization * 100
                    else:  # COST_NEUTRAL
                        # Balance: lower LWBS + reasonable utilization
                        evaluated.score = (1.0 - evaluated.expected_lwbs_rate) * 50 + evaluated.expected_utilization * 50
                    
                    evaluated_suggestions.append(evaluated)
                
                progress_bar.empty()
                
                # Re-sort by updated scores
                evaluated_suggestions.sort(key=lambda s: s.score, reverse=True)
                
                # Display ranked suggestions
                st.markdown("### Ranked Suggestions (Best First)")
                
                for rank, suggestion in enumerate(evaluated_suggestions, 1):
                    with st.expander(f"**#{rank}: {suggestion.description}** (Score: {suggestion.score:.1f})", expanded=(rank == 1)):
                        col1, col2, col3 = st.columns(3)
                        
                        with col1:
                            st.metric("Total FTE", f"{suggestion.total_fte:.1f}")
                            st.metric("Expected LWBS Rate", f"{suggestion.expected_lwbs_rate * 100:.2f}%")
                        
                        with col2:
                            los_display = f"{suggestion.expected_los_mean / 60:.1f} hrs" if suggestion.expected_los_mean else "N/A"
                            st.metric("Expected LOS (Mean)", los_display)
                            st.metric("Expected Utilization", f"{suggestion.expected_utilization * 100:.1f}%")
                        
                        with col3:
                            st.metric("Priority Goal", goal_label)
                        
                        # Show capacity changes
                        st.markdown("#### Capacity Changes:")
                        capacity_changes = []
                        for node_type, capacity in suggestion.node_capacities.items():
                            base_capacity = optimizer._base_capacities.get(node_type, 0)
                            if capacity != base_capacity:
                                change = capacity - base_capacity
                                change_str = f"+{change}" if change > 0 else str(change)
                                capacity_changes.append(f"{node_type.name.replace('_', ' ').title()}: {base_capacity} → {capacity} ({change_str})")
                        
                        if capacity_changes:
                            for change in capacity_changes:
                                st.text(f"  • {change}")
                        else:
                            st.text("  • No changes from baseline")
                
                # Comparison table
                st.markdown("### Comparison Summary")
                comparison_data = []
                for rank, suggestion in enumerate(evaluated_suggestions, 1):
                    comparison_data.append({
                        "Rank": rank,
                        "Description": suggestion.description,
                        "Total FTE": f"{suggestion.total_fte:.1f}",
                        "LWBS Rate": f"{suggestion.expected_lwbs_rate * 100:.2f}%",
                        "LOS Mean (hrs)": f"{suggestion.expected_los_mean / 60:.1f}" if suggestion.expected_los_mean else "N/A",
                        "Utilization": f"{suggestion.expected_utilization * 100:.1f}%",
                    })
                
                comparison_df = pd.DataFrame(comparison_data)
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)
        
        # Reset flag
        st.session_state['run_optimization'] = False
    
    st.markdown("---")
    
    # Backtesting section
    st.subheader("Historical Data Backtesting")
    st.markdown("Upload historical arrival data to replay and compare with simulation.")
    
    uploaded_file = st.file_uploader(
        "Upload Historical Arrivals CSV",
        type=['csv'],
        help="CSV should have columns: arrival_time (minutes), esi (1-5). Optional: los, lwbs, door_to_provider"
    )
    
    if uploaded_file is not None:
        try:
            # Read CSV content
            csv_content = uploaded_file.read().decode('utf-8')
            uploaded_file.seek(0)  # Reset file pointer
            
            # Parse as DataFrame for metrics calculation
            csv_df = pd.read_csv(uploaded_file)
            uploaded_file.seek(0)  # Reset again
            
            # Parse historical arrivals
            historical_arrivals = parse_historical_csv(csv_content)
            
            st.success(f"✅ Loaded {len(historical_arrivals)} historical arrivals")
            
            # Show preview
            with st.expander("Preview Historical Data"):
                st.dataframe(csv_df.head(10), use_container_width=True)
            
            # Calculate actual metrics if columns available
            actual_metrics = calculate_actual_metrics_from_csv(
                csv_df,
                los_column="los" if "los" in csv_df.columns else None,
                lwbs_column="lwbs" if "lwbs" in csv_df.columns else None,
                door_to_provider_column="door_to_provider" if "door_to_provider" in csv_df.columns else None,
            )
            
            if st.button("🔄 Replay & Compare", type="primary"):
                with st.spinner("Running simulation with historical arrivals..."):
                    sim, metrics, scenario_desc = run_simulation_with_historical_arrivals(
                        historical_arrivals=historical_arrivals,
                        scenario_key=scenario_key,
                        scenario_params=scenario_params,
                        warmup_minutes=warmup_minutes,
                    )
                
                st.success(f"Simulation complete! {metrics.total_patients} patients processed.")
                
                # Display comparison
                st.subheader("📈 Actual vs Simulated Comparison")
                
                comparison_df = create_comparison_table(actual_metrics, metrics)
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)
                
                # Charts
                st.subheader("📊 Visualizations")
                
                # LOS Distribution
                actual_los = csv_df["los"].dropna().tolist() if "los" in csv_df.columns else None
                los_fig = plot_los_distribution(sim.patients, actual_los)
                st.plotly_chart(los_fig, use_container_width=True)
                
                # Key Metrics Bar Chart
                metrics_fig = plot_key_metrics_comparison(metrics, actual_metrics=actual_metrics)
                st.plotly_chart(metrics_fig, use_container_width=True)
                
                # Queue Length Over Time
                queue_fig = plot_queue_length_over_time(sim.nodes)
                st.plotly_chart(queue_fig, use_container_width=True)
                
                # Display scenario info
                st.info(f"**Scenario:** {scenario_desc}")
                
                # Hour-by-hour metrics for time-window staffing
                if scenario_key == "staffing_adjustment" and sim._scenario:
                    _display_hourly_metrics(sim, scenario_params)
                
                # Display full metrics
                with st.expander("Full Simulated Metrics Report"):
                    st.markdown(f"```\n{metrics.print_report()}\n```")
        
        except Exception as e:
            st.error(f"Error processing CSV: {str(e)}")
            st.info("Expected CSV format:\n- Required columns: `arrival_time` (minutes), `esi` (1-5)\n- Optional columns: `los`, `lwbs`, `door_to_provider`")
    
    st.markdown("---")
    
    # Standard simulation
    st.subheader("Standard Simulation")
    # Show current settings summary
    with st.expander("📋 Current Settings Summary", expanded=False):
        col_sum1, col_sum2 = st.columns(2)
        with col_sum1:
            st.markdown("**Configuration:**")
            if config_mode == "Customize My ED":
                ed_name = st.session_state.get('custom_config', EDConfig.from_defaults()).ed_name
                st.text(f"ED Name: {ed_name}")
                st.text(f"Mode: Custom Configuration")
            else:
                st.text("Mode: Default Configuration")
            st.text(f"Arrival Rate: {arrival_rate:.1f} patients/hour")
            
            if scenario_key:
                st.markdown("**Scenario:**")
                st.text(f"Type: {scenario_label}")
                if scenario_params:
                    for key, value in scenario_params.items():
                        if isinstance(value, NodeType):
                            st.text(f"  {key}: {value.name.replace('_', ' ').title()}")
                        else:
                            st.text(f"  {key}: {value}")
            else:
                st.text("Scenario: None (Baseline)")
        
        with col_sum2:
            st.markdown("**Node Capacities:**")
            if config_mode == "Customize My ED" and 'custom_config' in st.session_state:
                config = st.session_state['custom_config']
                for node_type in NodeType:
                    cap = config.node_capacities.get(node_type.name, NODE_CAPACITY_DEFAULTS.get(node_type.name, 1))
                    st.text(f"{node_type.name.replace('_', ' ').title()}: {cap}")
            else:
                for node_type in NodeType:
                    cap = NODE_CAPACITY_DEFAULTS.get(node_type.name, 1)
                    st.text(f"{node_type.name.replace('_', ' ').title()}: {cap}")
    
    if st.button("Run 24-Hour Simulation", type="primary"):
        # Get custom config if in customize mode
        ed_config = None
        if config_mode == "Customize My ED" and 'custom_config' in st.session_state:
            ed_config = st.session_state['custom_config']
        
        # Run simulation(s) - single or multiple replications
        if num_replications == 1:
            # Single run (original behavior)
        with st.spinner("Running simulation..."):
            sim, metrics, scenario_desc = run_simulation(
                arrival_rate=arrival_rate,
                scenario_key=scenario_key,
                scenario_params=scenario_params,
                    seed=42,  # Same seed for fair comparison with baseline
                    ed_config=ed_config,
                    warmup_minutes=warmup_minutes,
                )
            
            aggregated_metrics = None
            replication_results = None
            
            # Run baseline for comparison if scenario is selected
            baseline_sim = None
            baseline_metrics = None
            baseline_aggregated = None
            if scenario_key is not None:
                with st.spinner("Running baseline simulation for comparison..."):
                    baseline_sim, baseline_metrics, _ = run_simulation(
                        arrival_rate=arrival_rate,
                        scenario_key=None,  # Baseline
                        scenario_params=None,
                        seed=42,  # Same seed as scenario for fair comparison
                        ed_config=ed_config,
                        warmup_minutes=warmup_minutes,
                    )
        else:
            # Multiple replications
            with st.spinner(f"Running {num_replications} simulations for statistical confidence..."):
                progress_bar = st.progress(0)
                
                def run_single_sim(seed: int):
                    return run_simulation(
                        arrival_rate=arrival_rate,
                        scenario_key=scenario_key,
                        scenario_params=scenario_params,
                        seed=seed,
                        ed_config=ed_config,
                        warmup_minutes=warmup_minutes,
                    )
                
                replication_results, aggregated_metrics = run_replications(
                    simulation_func=run_single_sim,
                    num_replications=num_replications,
                    base_seed=42,
                )
                
                # Use first replication's sim for display
                sim = replication_results[0].sim
                scenario_desc = f"Multiple Replications ({num_replications} runs)"
                # Create a synthetic EDMetrics from aggregated for display compatibility
                # We'll display aggregated stats separately
                metrics = replication_results[0].metrics  # Use first run's metrics for compatibility
                
                progress_bar.progress(1.0)
                progress_bar.empty()
            
            # Run baseline replications if scenario is selected
            baseline_sim = None
            baseline_metrics = None
            baseline_aggregated = None
            if scenario_key is not None:
                with st.spinner(f"Running {num_replications} baseline simulations for comparison..."):
                    progress_bar = st.progress(0)
                    
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
                    
                    baseline_metrics = baseline_aggregated
                    progress_bar.progress(1.0)
                    progress_bar.empty()

        if num_replications == 1:
        st.success(f"Simulation complete! {metrics.total_patients} patients processed.")
        else:
            st.success(f"Completed {num_replications} replications! Average: {aggregated_metrics.total_patients_mean:.1f} patients per run.")

        # Store simulation results in session state for tabs
        st.session_state['last_simulation'] = {
            'sim': sim,
            'metrics': metrics,
            'scenario_desc': scenario_desc,
            'baseline_sim': baseline_sim,
            'baseline_metrics': baseline_metrics,
            'num_replications': num_replications,
            'aggregated_metrics': aggregated_metrics if num_replications > 1 else None,
            'replication_results': replication_results if num_replications > 1 else None,
            'baseline_aggregated': baseline_aggregated if (num_replications > 1 and scenario_key) else None,
        }

        # Create tabs for different views
        tab1, tab2, tab3 = st.tabs(["📊 Simulation Results", "🔍 Patient Journey Trace", "⏱️ Step-by-Step Analysis"])
        
        with tab1:
            # Display scenario information prominently
            st.header("📊 Simulation Results")
            
            # Scenario info box
            with st.container():
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.markdown("### Scenario Configuration")
                    st.markdown(f"**{scenario_display_info['name']}**")
                    
                    if scenario_display_info["params"]:
                        st.markdown("**Parameters:**")
                        for param_name, param_value in scenario_display_info["params"].items():
                            st.markdown(f"- {param_name}: `{param_value}`")
                    else:
                        st.markdown("*Baseline simulation (no scenario)*")
                    
                with col2:
                    st.markdown("### Scenario Description")
                    st.info(scenario_desc)
            
            # Key Downstream Changes summary
            if baseline_sim is not None:
                st.markdown("---")
                st.subheader("🔍 Key Downstream Changes")
                
                # Compute node deltas
                node_deltas = compute_node_deltas(baseline_sim.nodes, sim.nodes)
                changes_summary = create_downstream_changes_summary(node_deltas, threshold_pct=5.0)
                
                if changes_summary:
                    with st.container():
                        st.markdown("**Significant changes (>5%) compared to baseline:**")
                        for change in changes_summary:
                            st.markdown(f"- {change}")
                else:
                    st.info("No significant downstream changes detected (>5% threshold).")
                
                # Store deltas for use in charts
                st.session_state['node_deltas'] = node_deltas
                st.session_state['baseline_nodes'] = baseline_sim.nodes
            
            st.markdown("---")

        # Display metrics report
        st.subheader("National ED Metrics Report")
        st.markdown(f"```\n{metrics.print_report()}\n```")

            # Charts
            st.subheader("📊 Visualizations")
            
            # LOS Distribution
            los_fig = plot_los_distribution(sim.patients)
            st.plotly_chart(los_fig, use_container_width=True)
            
            # Key Metrics Bar Chart (with baseline if available)
            if baseline_metrics is not None:
                metrics_fig = plot_key_metrics_comparison(metrics, baseline_metrics=baseline_metrics)
            else:
                metrics_fig = plot_key_metrics_comparison(metrics)
            st.plotly_chart(metrics_fig, use_container_width=True)
            
            # Hour-by-Hour Queue Length by Node
            st.markdown("#### Hour-by-Hour Queue Length by Node")
            hourly_queue_fig = plot_hourly_queue_lengths_by_node(sim.nodes)
            st.plotly_chart(hourly_queue_fig, use_container_width=True)
            
            # Wait Time Comparison (Baseline vs Scenario)
            if baseline_sim is not None:
                st.markdown("#### Wait Time Comparison: Baseline vs Scenario")
                wait_time_fig = plot_wait_times_by_node_comparison(baseline_sim.nodes, sim.nodes)
                st.plotly_chart(wait_time_fig, use_container_width=True)
            
            # Queue Length Over Time (original chart)
            queue_fig = plot_queue_length_over_time(sim.nodes)
            st.plotly_chart(queue_fig, use_container_width=True)
            
            # Hour-by-hour metrics for time-window staffing
            if scenario_key == "staffing_adjustment" and sim._scenario:
                _display_hourly_metrics(sim, scenario_params)

        # Patient journeys table
        st.subheader("Sample Patient Journeys (Top 3 Completed)")
        journeys_df = get_patient_journeys_df(sim.patients, limit=3)
        if not journeys_df.empty:
            st.dataframe(journeys_df, use_container_width=True, hide_index=True)
        else:
            st.info("No completed patients to display.")
        
        with tab2:
            # Patient Journey Trace tab
            st.header("🔍 Patient Journey Trace")
            
            if 'last_simulation' not in st.session_state:
                st.info("Please run a simulation first to view patient traces.")
            else:
                sim_data = st.session_state['last_simulation']
                sim = sim_data['sim']
                
                # Get completed patients
                completed_patients = [p for p in sim.patients if p.disposition and not p.is_lwbs]
                
                if not completed_patients:
                    st.warning("No completed patients available for tracing.")
                else:
                    # Patient selection dropdown
                    patient_options = {f"{p.id} (ESI {p.acuity.value}, LOS: {p.length_of_stay/60:.1f} hrs)" if p.length_of_stay else f"{p.id} (ESI {p.acuity.value})": p for p in completed_patients}
                    
                    selected_patient_label = st.selectbox(
                        "Select Patient ID",
                        options=list(patient_options.keys()),
                        index=0,
                        help="Choose a patient to view their complete journey through the ED",
                    )
                    
                    selected_patient = patient_options[selected_patient_label]
                    
                    # Display patient info
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Patient ID", selected_patient.id)
                    with col2:
                        st.metric("Acuity", f"ESI {selected_patient.acuity.value}")
                    with col3:
                        los_display = f"{selected_patient.length_of_stay/60:.1f} hrs" if selected_patient.length_of_stay else "N/A"
                        st.metric("Length of Stay", los_display)
                    with col4:
                        st.metric("Disposition", selected_patient.disposition.name.replace("_", " ").title() if selected_patient.disposition else "N/A")
                    
                    st.markdown("---")
                    
                    # Build trace
                    trace_df = build_patient_trace(selected_patient)
                    
                    # Filters and controls
                    st.subheader("Chronological Event Trace")
                    
                    col_filter1, col_filter2, col_filter3 = st.columns(3)
                    with col_filter1:
                        # Filter by node
                        all_nodes = ["All"] + sorted(trace_df["Node"].unique().tolist())
                        selected_node = st.selectbox(
                            "Filter by Node",
                            options=all_nodes,
                            index=0,
                            help="Filter events by specific node",
                        )
                    
                    with col_filter2:
                        # Filter by event type
                        all_events = ["All"] + sorted(trace_df["Event"].unique().tolist())
                        selected_event = st.selectbox(
                            "Filter by Event",
                            options=all_events,
                            index=0,
                            help="Filter events by type",
                        )
                    
                    with col_filter3:
                        # Sort options
                        sort_by = st.selectbox(
                            "Sort by",
                            options=["Time (Chronological)", "Wait Time (High to Low)", "Wait Time (Low to High)"],
                            index=0,
                            help="Sort the trace table",
                        )
                    
                    # Apply filters
                    filtered_df = trace_df.copy()
                    
                    if selected_node != "All":
                        filtered_df = filtered_df[filtered_df["Node"] == selected_node]
                    
                    if selected_event != "All":
                        filtered_df = filtered_df[filtered_df["Event"] == selected_event]
                    
                    # Apply sorting
                    if sort_by == "Wait Time (High to Low)":
                        # Extract numeric wait times for sorting
                        filtered_df["Wait Time Numeric"] = filtered_df["Wait Time (min)"].apply(
                            lambda x: float(x.replace("-", "0")) if isinstance(x, str) and x != "-" else 0.0
                        )
                        filtered_df = filtered_df.sort_values("Wait Time Numeric", ascending=False)
                        filtered_df = filtered_df.drop(columns=["Wait Time Numeric"])
                    elif sort_by == "Wait Time (Low to High)":
                        filtered_df["Wait Time Numeric"] = filtered_df["Wait Time (min)"].apply(
                            lambda x: float(x.replace("-", "0")) if isinstance(x, str) and x != "-" else 0.0
                        )
                        filtered_df = filtered_df.sort_values("Wait Time Numeric", ascending=True)
                        filtered_df = filtered_df.drop(columns=["Wait Time Numeric"])
                    # Time (Chronological) is already sorted from build_patient_trace
                    
                    # Highlight rows with long waits (>30 min)
                    def highlight_long_waits(row):
                        wait_time_str = str(row["Wait Time (min)"])
                        if wait_time_str != "-":
                            try:
                                wait_time = float(wait_time_str)
                                if wait_time > 30:
                                    return ['background-color: #ffebee'] * len(row)  # Light red
                            except ValueError:
                                pass
                        return [''] * len(row)
                    
                    # Display filtered and sorted table
                    styled_df = filtered_df.style.apply(highlight_long_waits, axis=1)
                    st.dataframe(styled_df, use_container_width=True, hide_index=True)
                    
                    # Download button for CSV
                    csv_data = filtered_df.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Trace as CSV",
                        data=csv_data,
                        file_name=f"patient_trace_{selected_patient.id}.csv",
                        mime="text/csv",
                        help="Download the filtered trace data as a CSV file",
                    )
                    
                    # Summary statistics
                    st.markdown("---")
                    st.markdown("#### Trace Summary")
                    col_sum1, col_sum2, col_sum3 = st.columns(3)
                    
                    with col_sum1:
                        total_events = len(filtered_df)
                        st.metric("Total Events", total_events)
                    
                    with col_sum2:
                        # Count events with wait times > 30 min
                        long_waits = 0
                        for wait_str in filtered_df["Wait Time (min)"]:
                            if wait_str != "-":
                                try:
                                    if float(wait_str) > 30:
                                        long_waits += 1
                                except ValueError:
                                    pass
                        st.metric("Long Waits (>30 min)", long_waits)
                    
                    with col_sum3:
                        # Average wait time
                        wait_times = []
                        for wait_str in filtered_df["Wait Time (min)"]:
                            if wait_str != "-":
                                try:
                                    wait_times.append(float(wait_str))
                                except ValueError:
                                    pass
                        avg_wait = sum(wait_times) / len(wait_times) if wait_times else 0.0
                        st.metric("Average Wait Time", f"{avg_wait:.1f} min" if wait_times else "N/A")
                    
                    # Optional: Snapshot at specific time
                    st.markdown("---")
                    st.subheader("📸 System Snapshot")
                    st.markdown("View queue lengths at a specific point in time during this patient's journey.")
                    
                    # Get time range from patient
                    min_time = selected_patient.arrival_time
                    # Estimate max time from LOS or use default
                    if selected_patient.length_of_stay:
                        max_time = selected_patient.arrival_time + selected_patient.length_of_stay
                    else:
                        max_time = selected_patient.arrival_time + 480  # Default to 8 hours
                    
                    snapshot_time = st.slider(
                        "Snapshot Time (minutes)",
                        min_value=int(min_time),
                        max_value=int(max_time) if max_time else 1440,
                        value=int(selected_patient.arrival_time),
                        step=1,
                        help="Select a simulation time to view queue lengths at that moment",
                    )
                    
                    # Display snapshot
                    snapshot_df = get_queue_snapshot(sim.nodes, float(snapshot_time))
                    st.markdown(f"**Queue Status at {snapshot_time:.0f} minutes ({snapshot_time/60:.1f} hours):**")
                    st.dataframe(snapshot_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
