#!/usr/bin/env python3
"""
ED Simulation Demo Script

Runs a 4-hour simulation of ED patient flow and displays national ED metrics.

Usage:
    python -m ed_simulation.demo
    # or
    python ed_simulation/demo.py
"""

import random
from typing import Generator, Dict, List

import simpy

from ed_simulation.core.enums import Acuity, NodeType
from ed_simulation.core.patient import Patient
from ed_simulation.core.node import Node, NodeConfig
from ed_simulation.processes.arrivals import ArrivalGenerator, ArrivalConfig
from ed_simulation.simulation.metrics import MetricsCollector


class EDSimulation:
    """
    Simple ED simulation orchestrator.

    Connects patient arrivals to a sequence of processing nodes.
    """

    def __init__(
        self,
        env: simpy.Environment,
        seed: int = 42,
        arrival_rate: float = 3.0,
    ):
        """
        Initialize the ED simulation.

        Args:
            env: SimPy environment
            seed: Random seed for reproducibility
            arrival_rate: Patients per hour
        """
        self._env = env
        self._rng = random.Random(seed)

        # Create nodes with default configurations
        self._nodes: Dict[NodeType, Node] = {}
        for node_type in NodeType:
            config = NodeConfig.from_defaults(node_type)
            config.enable_lwbs_monitoring = True
            self._nodes[node_type] = Node(env, config, rng=random.Random(seed))

        # Create arrival generator
        arrival_config = ArrivalConfig(
            mean_arrival_rate=arrival_rate,
            use_time_variation=False,  # Keep it simple for demo
        )
        self._arrivals = ArrivalGenerator(env, arrival_config, rng=self._rng)

        # Track patients
        self._patients: List[Patient] = []
        self._completed: List[Patient] = []
        self._lwbs: List[Patient] = []

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

        # Define pathway based on acuity
        pathway = self._get_pathway(patient.acuity)

        for node_type in pathway:
            node = self._nodes[node_type]

            # Process at this node
            completed = yield from node.process_patient(patient)

            if not completed:
                # Patient left (LWBS)
                self._lwbs.append(patient)
                return

        # Successfully completed - discharge
        patient.discharge()
        self._completed.append(patient)

    def _get_pathway(self, acuity: Acuity) -> List[NodeType]:
        """
        Get the node pathway based on acuity.

        Args:
            acuity: Patient's ESI level

        Returns:
            List of nodes to visit
        """
        if acuity in (Acuity.ESI_1, Acuity.ESI_2):
            # Critical: Skip registration, fast-track to bed
            return [
                NodeType.TRIAGE,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ]
        elif acuity == Acuity.ESI_3:
            # Urgent: Full pathway
            return [
                NodeType.TRIAGE,
                NodeType.REGISTRATION,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ]
        else:
            # Low acuity: Skip diagnostics
            return [
                NodeType.TRIAGE,
                NodeType.REGISTRATION,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ]

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
        self._env.run(until=duration_minutes + 180)  # Extra time for patients to finish


def print_arrival_stats(sim: EDSimulation) -> None:
    """Print arrival statistics."""
    arrival_stats = sim.arrivals.get_statistics()
    print("\nARRIVAL STATISTICS:")
    print(f"  Total patients arrived: {arrival_stats['total_patients']}")
    print(f"  Simulation duration:    {arrival_stats['duration_hours']:.1f} hours")
    print(f"  Average arrival rate:   {arrival_stats['average_rate_per_hour']:.1f} per hour")

    print(f"\n  Arrivals by acuity:")
    for acuity_name, count in arrival_stats['arrivals_by_acuity'].items():
        pct = arrival_stats['acuity_percentages'][acuity_name]
        print(f"    {acuity_name}: {count} ({pct:.1f}%)")


def print_node_stats(sim: EDSimulation) -> None:
    """Print node statistics."""
    print(f"\nNODE STATISTICS:")
    print("-" * 70)
    print(f"{'Node':<25} {'Processed':>10} {'LWBS':>6} {'Avg Wait':>10} {'Max Queue':>10}")
    print("-" * 70)

    for node_type in NodeType:
        node = sim.nodes[node_type]
        stats = node.statistics
        print(
            f"{node.name:<25} "
            f"{stats.patients_completed:>10} "
            f"{stats.patients_lwbs:>6} "
            f"{stats.average_wait_time:>10.1f} "
            f"{stats.max_queue_length:>10}"
        )
    print("-" * 70)


def main():
    """Run the demo simulation."""
    print("=" * 70)
    print("ED DIGITAL TWIN - SIMULATION DEMO")
    print("=" * 70)
    print("\nSimulating 4 hours of ED operations...")
    print("Arrival rate: 3 patients/hour")

    # Reset patient counter
    Patient.reset_counter()

    # Create environment and simulation
    env = simpy.Environment()
    sim = EDSimulation(env, seed=42, arrival_rate=3.0)

    # Run for 4 hours (240 minutes)
    sim.run(duration_minutes=240)

    # Print arrival statistics
    print_arrival_stats(sim)

    # Calculate national metrics
    collector = MetricsCollector()
    collector.add_patients(sim.patients)
    metrics = collector.calculate()

    # Print the formatted metrics report
    print(metrics.print_report())

    # Print node-level statistics
    print_node_stats(sim)

    # Show sample patient journeys
    print("\nSAMPLE PATIENT JOURNEYS (first 3 completed):")
    print("-" * 70)
    completed = [p for p in sim.patients if p.disposition and not p.is_lwbs][:3]
    for p in completed:
        print(f"\n{p.id} ({p.acuity.name}):")
        print(f"  Door-to-Triage:    {p.door_to_triage:.1f} min" if p.door_to_triage is not None else "  Door-to-Triage:    N/A")
        print(f"  Door-to-Bed:       {p.door_to_bed:.1f} min" if p.door_to_bed is not None else "  Door-to-Bed:       N/A")
        print(f"  Door-to-Provider:  {p.door_to_provider:.1f} min" if p.door_to_provider is not None else "  Door-to-Provider:  N/A")
        print(f"  Length of Stay:    {p.length_of_stay:.1f} min ({p.length_of_stay/60:.1f} hrs)" if p.length_of_stay is not None else "  Length of Stay:    N/A")


if __name__ == "__main__":
    main()
