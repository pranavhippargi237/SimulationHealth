"""
Draggable, clickable ED diagram using vis-network via HTML component.

Provides a truly interactive node graph where nodes can be dragged and clicked.
"""

from __future__ import annotations
from typing import Dict, Optional, Tuple, List
import streamlit.components.v1 as components
import os

from ed_simulation.core.enums import NodeType
from ed_simulation.core.node import Node
from ed_simulation.processes.routing import RoutingEngine


def render_draggable_ed_diagram(
    nodes_dict: Optional[Dict[NodeType, Node]] = None,
    capacities: Optional[Dict[str, int]] = None,
    ed_name: str = "My ED",
    routing_engine: Optional[RoutingEngine] = None,
    saved_positions: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Dict[str, float]]]]:
    """
    Render a draggable, clickable ED diagram.
    
    Args:
        nodes_dict: Dictionary of Node objects (for real-time stats)
        capacities: Dictionary of node capacities (node name -> capacity)
        ed_name: Name of the ED
        routing_engine: Routing engine for pathways
        saved_positions: Previously saved node positions
    
    Returns:
        Tuple of (clicked_node_id, updated_positions)
    """
    if routing_engine is None:
        routing_engine = RoutingEngine()
    
    if capacities is None:
        from ed_simulation.config.defaults import NODE_CAPACITY_DEFAULTS
        capacities = NODE_CAPACITY_DEFAULTS.copy()
    
    # Default node positions (will be overridden by saved positions)
    default_positions: Dict[NodeType, Tuple[float, float]] = {
        NodeType.TRIAGE: (0, 0),
        NodeType.REGISTRATION: (100, -100),
        NodeType.BED_ASSIGNMENT: (200, 0),
        NodeType.FAST_TRACK: (100, 100),
        NodeType.PROVIDER_ASSESSMENT: (400, 0),
        NodeType.DIAGNOSTICS: (500, -100),
        NodeType.TREATMENT: (500, 100),
        NodeType.DISPOSITION: (700, 0),
    }
    
    # Define pathways with colors
    pathways = {
        'Critical (ESI 1-2)': {
            'path': [
                NodeType.TRIAGE,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ],
            'color': '#dc3545',  # Red
        },
        'Standard (ESI 3)': {
            'path': [
                NodeType.TRIAGE,
                NodeType.REGISTRATION,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ],
            'color': '#007bff',  # Blue
        },
        'Fast Track (ESI 4-5)': {
            'path': [
                NodeType.TRIAGE,
                NodeType.FAST_TRACK,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DISPOSITION,
            ],
            'color': '#28a745',  # Green
        },
    }
    
    # Build nodes array
    vis_nodes = []
    node_stats = {}
    
    for node_type in NodeType:
        node_name = node_type.name.replace("_", " ").title()
        node_id = node_type.name
        cap = capacities.get(node_type.name, 1)
        
        # Get real-time stats if available
        current_queue = 0
        utilization = 0.0
        in_service = 0
        avg_wait_time = 0.0
        lwbs_count = 0
        
        if nodes_dict and node_type in nodes_dict:
            node = nodes_dict[node_type]
            current_queue = node.queue_length
            utilization = node.utilization
            in_service = len(node._patients_in_service)
            avg_wait_time = node.statistics.average_wait_time
            lwbs_count = node.statistics.patients_lwbs
        
        # Store stats for display
        node_stats[node_id] = {
            'name': node_name,
            'capacity': cap,
            'queue': current_queue,
            'utilization': utilization,
            'in_service': in_service,
            'avg_wait': avg_wait_time,
            'lwbs': lwbs_count,
        }
        
        # Choose box color based on utilization
        if nodes_dict and node_type in nodes_dict:
            util = nodes_dict[node_type].utilization
            if util > 0.8:
                box_color = '#ff4444'  # Red
            elif util > 0.6:
                box_color = '#ffaa00'  # Orange
            else:
                box_color = '#44ff44'  # Green
        else:
            box_color = '#88ccff'  # Light blue
        
        # Get position (use saved if available, otherwise default)
        if saved_positions and node_id in saved_positions:
            x = saved_positions[node_id]['x']
            y = saved_positions[node_id]['y']
        else:
            x, y = default_positions.get(node_type, (0, 0))
        
        # Create node label
        label = f"{node_name}\nCap: {cap}\nQ: {current_queue}"
        
        vis_nodes.append({
            'id': node_id,
            'label': label,
            'color': {
                'background': box_color,
                'border': '#333333',
                'highlight': {
                    'background': box_color,
                    'border': '#000000'
                }
            },
            'x': x,
            'y': y,
            'fixed': False,  # Allow dragging
        })
    
    # Build edges array
    vis_edges = []
    edge_id = 0
    
    for pathway_name, pathway_info in pathways.items():
        path = pathway_info['path']
        color = pathway_info['color']
        
        # Create edges between consecutive nodes in pathway
        for i in range(len(path) - 1):
            from_node = path[i].name
            to_node = path[i + 1].name
            
            # Check if edge already exists (multiple pathways might share edges)
            existing_edge = next(
                (e for e in vis_edges if e['from'] == from_node and e['to'] == to_node),
                None
            )
            
            if existing_edge:
                # Edge exists, but we want to show all pathway colors
                # Use the first color encountered (or we could make it multi-colored)
                continue
            else:
                vis_edges.append({
                    'id': f"edge_{edge_id}",
                    'from': from_node,
                    'to': to_node,
                    'color': {
                        'color': color,
                        'highlight': color,
                    },
                    'width': 3,
                    'smooth': {
                        'type': 'continuous',
                        'roundness': 0.5
                    }
                })
                edge_id += 1
    
    # Prepare data for HTML component
    diagram_data = {
        'nodes': vis_nodes,
        'edges': vis_edges,
        'nodeStats': node_stats,
        'savedPositions': saved_positions or {},
    }
    
    # Read HTML template
    html_file = os.path.join(os.path.dirname(__file__), 'draggable_diagram.html')
    with open(html_file, 'r') as f:
        html_template = f.read()
    
    # Convert data to JSON string for injection
    import json
    data_json = json.dumps(diagram_data, indent=2)
    
    # Inject data into HTML script tag
    html_with_data = html_template.replace(
        '<!-- Data will be injected here by Python -->',
        data_json
    )
    
    # Render component
    # Note: components.html() doesn't return values directly
    # We'll use a workaround with session state and JavaScript communication
    components.html(
        html_with_data,
        height=720,
        width=None,
    )
    
    # For now, return None for clicked node and current saved positions
    # The actual interaction will be handled via the node selector in app.py
    # Positions are saved via the session state update mechanism
    clicked_node = None
    updated_positions = saved_positions.copy() if saved_positions else {}
    
    return clicked_node, updated_positions

