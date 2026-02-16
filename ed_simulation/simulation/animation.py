"""
Animation and visualization utilities for real-time ED simulation display.

Provides functions to track patient positions and create animated visualizations.
"""

from __future__ import annotations
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

from ..core.enums import NodeType, Acuity, PatientStatus
from ..core.patient import Patient


@dataclass
class PatientPosition:
    """Represents a patient's position at a specific time."""
    patient_id: str
    acuity: Acuity
    node: Optional[NodeType]
    status: PatientStatus
    x: float
    y: float
    time: float


class PatientState(Enum):
    """Patient states for visualization."""
    ARRIVING = "arriving"
    IN_QUEUE = "in_queue"
    IN_SERVICE = "in_service"
    BETWEEN_NODES = "between_nodes"
    DISCHARGED = "discharged"
    LWBS = "lwbs"


def get_patient_position_at_time(
    patient: Patient,
    time: float,
    node_positions: Dict[NodeType, Tuple[float, float]]
) -> Optional[PatientPosition]:
    """
    Determine where a patient is at a specific simulation time.
    
    Args:
        patient: Patient object
        time: Simulation time in minutes
        node_positions: Mapping of NodeType to (x, y) coordinates
    
    Returns:
        PatientPosition or None if patient hasn't arrived yet
    """
    if time < patient.arrival_time:
        return None
    
    # Check if patient has been discharged
    if hasattr(patient, '_departure_time') and patient._departure_time and time >= patient._departure_time:
        return PatientPosition(
            patient_id=patient.id,
            acuity=patient.acuity,
            node=None,
            status=PatientStatus.DISCHARGED if not patient.is_lwbs else PatientStatus.LWBS,
            x=6.0,  # Exit position
            y=3.0,
            time=time,
        )
    
    # Check timestamps to find current node
    # Sort by time to find the most recent node
    sorted_timestamps = sorted(
        patient.timestamps.items(),
        key=lambda x: x[1].queue_enter
    )
    
    for node_type, timestamp in sorted_timestamps:
        if timestamp.queue_enter <= time:
            if time < timestamp.service_end:
                # Patient is at this node
                if timestamp.service_start <= time < timestamp.service_end:
                    # In service
                    status = PatientStatus.IN_SERVICE
                else:
                    # In queue
                    status = PatientStatus.WAITING
                
                if node_type in node_positions:
                    x, y = node_positions[node_type]
                    return PatientPosition(
                        patient_id=patient.id,
                        acuity=patient.acuity,
                        node=node_type,
                        status=status,
                        x=x,
                        y=y,
                        time=time,
                    )
    
    # Check if patient is currently waiting at a node (hasn't started service yet)
    if hasattr(patient, '_current_node') and patient._current_node:
        if hasattr(patient, '_current_queue_enter_time') and patient._current_queue_enter_time:
            if patient._current_queue_enter_time <= time:
                # Check if service has started
                if patient._current_node in patient.timestamps:
                    ts = patient.timestamps[patient._current_node]
                    if time < ts.service_start:
                        # Still in queue
                        if patient._current_node in node_positions:
                            x, y = node_positions[patient._current_node]
                            return PatientPosition(
                                patient_id=patient.id,
                                acuity=patient.acuity,
                                node=patient._current_node,
                                status=PatientStatus.WAITING,
                                x=x,
                                y=y,
                                time=time,
                            )
                else:
                    # In queue but not yet in timestamps
                    if patient._current_node in node_positions:
                        x, y = node_positions[patient._current_node]
                        return PatientPosition(
                            patient_id=patient.id,
                            acuity=patient.acuity,
                            node=patient._current_node,
                            status=PatientStatus.WAITING,
                            x=x,
                            y=y,
                            time=time,
                        )
    
    # Patient is between nodes or at entrance
    if patient.arrival_time <= time:
        # Find the last node the patient completed
        if patient.timestamps:
            # Find the most recent completed node
            completed_nodes = [
                (node, ts) for node, ts in patient.timestamps.items()
                if time >= ts.service_end
            ]
            if completed_nodes:
                last_node, last_ts = max(completed_nodes, key=lambda x: x[1].service_end)
                if last_node in node_positions:
                    x, y = node_positions[last_node]
                    # Offset slightly to show movement to next node
                    return PatientPosition(
                        patient_id=patient.id,
                        acuity=patient.acuity,
                        node=None,
                        status=PatientStatus.ARRIVING,
                        x=x + 0.2,
                        y=y + 0.2,
                        time=time,
                    )
        
        # At entrance (just arrived)
        return PatientPosition(
            patient_id=patient.id,
            acuity=patient.acuity,
            node=None,
            status=PatientStatus.ARRIVING,
            x=-0.5,
            y=3.0,
            time=time,
        )
    
    return None


def get_all_patient_positions_at_time(
    patients: List[Patient],
    time: float,
    node_positions: Dict[NodeType, Tuple[float, float]]
) -> List[PatientPosition]:
    """
    Get positions of all patients at a specific time.
    
    Args:
        patients: List of all patients
        time: Simulation time
        node_positions: Node position mapping
    
    Returns:
        List of PatientPosition objects
    """
    positions = []
    for patient in patients:
        pos = get_patient_position_at_time(patient, time, node_positions)
        if pos:
            positions.append(pos)
    return positions

