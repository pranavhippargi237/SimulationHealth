#!/usr/bin/env python3
"""
Main entry point for ED Simulation.

This script runs a complete ED simulation with:
- Patient arrivals (Poisson process)
- Multiple processing nodes (Triage, Registration, Bed Assignment, etc.)
- Priority-based queuing
- LWBS monitoring
- Statistics collection and reporting
"""

import sys
import os

# Add parent directory to path to allow imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import simpy
import random
from typing import Dict, List

from ed_simulation.core.enums import NodeType, Acuity
from ed_simulation.core.node import Node, NodeConfig
from ed_simulation.core.patient import Patient
from ed_simulation.processes.arrivals import ArrivalGenerator, ArrivalConfig
from ed_simulation.config.defaults import SIMULATION_DEFAULTS


class EDSimulation:
    """Main simulation orchestrator."""
    
    def __init__(
        self,
        duration_hours: float = 24,
        warmup_hours: float = 4,
        random_seed: int = 42,
    ):
        """
        Initialize the ED simulation.
        
        Args:
            duration_hours: Total simulation duration in hours
            warmup_hours: Warmup period (excluded from statistics)
            random_seed: Random seed for reproducibility
        """
        self.env = simpy.Environment()
        self.rng = random.Random(random_seed)
        
        self.duration_minutes = duration_hours * 60
        # Ensure warmup doesn't exceed duration
        self.warmup_minutes = min(warmup_hours * 60, self.duration_minutes * 0.5)
        
        # Create nodes
        self.nodes: Dict[NodeType, Node] = {}
        self._create_nodes()
        
        # Patient tracking
        self.patients: List[Patient] = []
        
        # Statistics
        self.stats_start_time = self.warmup_minutes
        
    def _create_nodes(self):
        """Create all ED processing nodes."""
        node_types = [
            NodeType.TRIAGE,
            NodeType.REGISTRATION,
            NodeType.BED_ASSIGNMENT,
            NodeType.PROVIDER_ASSESSMENT,
            NodeType.DIAGNOSTICS,
            NodeType.TREATMENT,
            NodeType.DISPOSITION,
        ]
        
        for node_type in node_types:
            config = NodeConfig.from_defaults(node_type)
            self.nodes[node_type] = Node(
                self.env,
                config,
                rng=self.rng
            )
    
    def patient_journey(self, patient: Patient):
        """
        Simulate a patient's journey through the ED.
        
        Args:
            patient: The patient to process
        """
        # Simple linear flow through all nodes
        flow = [
            NodeType.TRIAGE,
            NodeType.REGISTRATION,
            NodeType.BED_ASSIGNMENT,
            NodeType.PROVIDER_ASSESSMENT,
            NodeType.DIAGNOSTICS,
            NodeType.TREATMENT,
            NodeType.DISPOSITION,
        ]
        
        for node_type in flow:
            node = self.nodes[node_type]
            completed = yield from node.process_patient(patient)
            
            if not completed:  # Patient LWBS
                return
        
        # Patient completed journey - discharge
        patient.discharge()
    
    def run(self):
        """Run the simulation."""
        # Create arrival generator
        arrival_config = ArrivalConfig(
            mean_arrival_rate=3.0,  # 3 patients per hour
        )
        arrival_gen = ArrivalGenerator(
            self.env,
            arrival_config,
            rng=self.rng
        )
        
        # Start arrival process
        self.env.process(
            arrival_gen.run(
                on_arrival=self.patient_journey,
                until=self.duration_minutes
            )
        )
        
        # Run simulation
        print(f"Starting simulation for {self.duration_minutes/60:.1f} hours...")
        print(f"Warmup period: {self.warmup_minutes/60:.1f} hours")
        print("Running simulation...")
        self.env.run(until=self.duration_minutes)
        print("Simulation complete!")
        
        # Collect statistics
        self._print_statistics()
    
    def _print_statistics(self):
        """Print simulation statistics."""
        print("\n" + "="*60)
        print("SIMULATION STATISTICS")
        print("="*60)
        
        # Node statistics
        print("\nNode Performance:")
        print("-" * 60)
        for node_type, node in self.nodes.items():
            stats = node.statistics
            print(f"\n{node.name}:")
            print(f"  Patients Entered: {stats.patients_entered}")
            print(f"  Patients Completed: {stats.patients_completed}")
            print(f"  Patients LWBS: {stats.patients_lwbs}")
            if stats.patients_completed > 0:
                print(f"  Average Wait Time: {stats.average_wait_time:.2f} min")
                print(f"  Average Service Time: {stats.average_service_time:.2f} min")
            if stats.patients_entered > 0:
                print(f"  LWBS Rate: {stats.lwbs_rate:.2%}")
            print(f"  Max Queue Length: {stats.max_queue_length}")
            print(f"  Current Utilization: {node.utilization:.1%}")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run ED Simulation"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=SIMULATION_DEFAULTS["duration_hours"],
        help="Simulation duration in hours (default: 24)"
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=SIMULATION_DEFAULTS["warmup_hours"],
        help="Warmup period in hours (default: 4)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SIMULATION_DEFAULTS["random_seed"],
        help="Random seed (default: 42)"
    )
    
    args = parser.parse_args()
    
    # Create and run simulation
    sim = EDSimulation(
        duration_hours=args.duration,
        warmup_hours=args.warmup,
        random_seed=args.seed,
    )
    
    sim.run()


if __name__ == "__main__":
    main()

