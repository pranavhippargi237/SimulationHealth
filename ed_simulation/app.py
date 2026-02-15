#!/usr/bin/env python3
"""
ED Digital Twin MVP - Streamlit Application

A configurable ED throughput simulation tool with scenario support.

Usage:
    streamlit run ed_simulation/app.py
"""

import random
from typing import Dict, Generator, List, Optional, Tuple

import pandas as pd
import simpy
import streamlit as st

from ed_simulation.core.enums import Acuity, NodeType
from ed_simulation.core.patient import Patient
from ed_simulation.core.node import Node, NodeConfig
from ed_simulation.processes.arrivals import ArrivalGenerator, ArrivalConfig
from ed_simulation.simulation.metrics import MetricsCollector
from ed_simulation.scenarios import (
    BoardingScenario,
    VerticalTrackScenario,
    StaffingAdjustmentScenario,
    SurgeScenario,
)


# Scenario options for dropdown
SCENARIO_OPTIONS = {
    "None (Baseline)": None,
    "Boarding (40% beds occupied)": "boarding",
    "Vertical/Fast Track (ESI 4-5)": "vertical_track",
    "Staffing Adjustment (Peak Hours)": "staffing_adjustment",
    "Surge (+30% Arrivals)": "surge",
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
    ):
        """
        Initialize the ED simulation.

        Args:
            env: SimPy environment
            seed: Random seed for reproducibility
            arrival_rate: Patients per hour
            scenario_key: Scenario to apply (None for baseline)
        """
        self._env = env
        self._rng = random.Random(seed)
        self._scenario_key = scenario_key
        self._scenario = None
        self._scenario_description = "Baseline (no scenario)"

        # Create nodes with default configurations
        self._nodes: Dict[NodeType, Node] = {}
        for node_type in NodeType:
            config = NodeConfig.from_defaults(node_type)
            config.enable_lwbs_monitoring = True
            self._nodes[node_type] = Node(env, config, rng=random.Random(seed))

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

    def _apply_scenario(self, scenario_key: str, base_arrival_rate: float) -> float:
        """
        Apply the selected scenario.

        Args:
            scenario_key: Key identifying the scenario
            base_arrival_rate: Base arrival rate before scenario

        Returns:
            Adjusted arrival rate (may be modified by surge scenario)
        """
        arrival_rate = base_arrival_rate

        if scenario_key == "boarding":
            self._scenario = BoardingScenario(boarding_percentage=0.40)
            self._scenario.apply(self._nodes)
            self._scenario_description = self._scenario.get_description()

        elif scenario_key == "vertical_track":
            self._scenario = VerticalTrackScenario(fast_track_providers=2)
            self._scenario.apply(self._nodes, env=self._env)
            self._scenario_description = self._scenario.get_description()

        elif scenario_key == "staffing_adjustment":
            self._scenario = StaffingAdjustmentScenario(peak_provider_increase=2)
            self._scenario.apply(self._nodes)
            self._scenario_description = self._scenario.get_description()

        elif scenario_key == "surge":
            self._scenario = SurgeScenario(surge_percentage=0.30)
            arrival_rate = self._scenario.apply_to_rate(base_arrival_rate)
            self._scenario_description = self._scenario.get_description()

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
        Process a patient through their ED journey.

        Args:
            patient: The arriving patient

        Yields:
            SimPy events
        """
        self._patients.append(patient)

        # Get pathway (may be modified by scenario)
        pathway = self._get_pathway(patient)

        for node_type in pathway:
            # Get appropriate node (may be fast track node)
            node = self._get_node_for_patient(patient, node_type)

            # Process at this node
            completed = yield from node.process_patient(patient)

            if not completed:
                # Patient left (LWBS)
                self._lwbs.append(patient)
                return

        # Successfully completed - discharge
        patient.discharge()
        self._completed.append(patient)

    def _get_pathway(self, patient: Patient) -> List[NodeType]:
        """
        Get the node pathway for a patient.

        Args:
            patient: Patient to route

        Returns:
            List of nodes to visit
        """
        # Default pathways by acuity
        if patient.acuity in (Acuity.ESI_1, Acuity.ESI_2):
            default_pathway = [
                NodeType.TRIAGE,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ]
        elif patient.acuity == Acuity.ESI_3:
            default_pathway = [
                NodeType.TRIAGE,
                NodeType.REGISTRATION,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ]
        else:
            default_pathway = [
                NodeType.TRIAGE,
                NodeType.REGISTRATION,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ]

        # Let vertical track scenario modify pathway if active
        if isinstance(self._scenario, VerticalTrackScenario):
            return self._scenario.get_pathway(patient, default_pathway)

        return default_pathway

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

    def run(self, duration_minutes: float) -> None:
        """
        Run the simulation.

        Args:
            duration_minutes: How long to simulate (in minutes)
        """
        # Start arrival process
        self._env.process(
            self._arrivals.run(
                on_arrival=self.patient_journey,
                until=duration_minutes,
            )
        )

        # Run simulation
        self._env.run(until=duration_minutes + 180)


def run_simulation(
    arrival_rate: float,
    scenario_key: Optional[str] = None,
    duration_hours: int = 24,
    seed: int = 42
) -> Tuple:
    """
    Run ED simulation with given parameters.

    Args:
        arrival_rate: Patients per hour
        scenario_key: Scenario to apply
        duration_hours: Simulation duration in hours
        seed: Random seed for reproducibility

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
    )
    sim.run(duration_minutes=duration_hours * 60)

    collector = MetricsCollector()
    collector.add_patients(sim.patients)
    metrics = collector.calculate()

    return sim, metrics, sim.scenario_description


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

    # Sidebar configuration
    st.sidebar.header("Simulation Parameters")

    arrival_rate = st.sidebar.slider(
        "Arrival Rate (patients/hour)",
        min_value=1.0,
        max_value=10.0,
        value=3.0,
        step=0.5,
        help="Average number of patients arriving per hour (Poisson process)",
    )

    st.sidebar.markdown("---")

    # Scenario selection
    st.sidebar.header("Scenario")
    scenario_label = st.sidebar.selectbox(
        "Select Scenario",
        options=list(SCENARIO_OPTIONS.keys()),
        index=0,
        help="Apply a pre-configured scenario to test ED performance",
    )
    scenario_key = SCENARIO_OPTIONS[scenario_label]

    # Scenario descriptions
    scenario_descriptions = {
        "boarding": "Reduces bed capacity by 40% to simulate admitted patients boarding in ED.",
        "vertical_track": "Adds a fast-track pathway for ESI 4-5 patients with dedicated providers.",
        "staffing_adjustment": "Increases provider staffing during peak hours (2pm-10pm).",
        "surge": "Increases arrival rate by 30% to simulate high-volume periods.",
    }

    if scenario_key:
        st.sidebar.info(scenario_descriptions.get(scenario_key, ""))

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
    if st.button("Run 24-Hour Simulation", type="primary"):
        with st.spinner("Running simulation..."):
            sim, metrics, scenario_desc = run_simulation(
                arrival_rate=arrival_rate,
                scenario_key=scenario_key,
            )

        st.success(f"Simulation complete! {metrics.total_patients} patients processed.")

        # Display scenario in header
        st.header(f"Results: {scenario_desc}")

        # Display metrics report
        st.subheader("National ED Metrics Report")
        st.markdown(f"```\n{metrics.print_report()}\n```")

        # Patient journeys table
        st.subheader("Sample Patient Journeys (Top 3 Completed)")
        journeys_df = get_patient_journeys_df(sim.patients, limit=3)
        if not journeys_df.empty:
            st.dataframe(journeys_df, use_container_width=True, hide_index=True)
        else:
            st.info("No completed patients to display.")


if __name__ == "__main__":
    main()
