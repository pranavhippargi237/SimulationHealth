"""
Interactive ED diagram with clickable box nodes.

Provides a box-based node diagram where nodes can be clicked to show detailed stats.
"""

from __future__ import annotations
from typing import Dict, Optional, Tuple
import plotly.graph_objects as go
import streamlit as st

from ed_simulation.core.enums import NodeType
from ed_simulation.core.node import Node
from ed_simulation.processes.routing import RoutingEngine


def render_interactive_ed_diagram(
    nodes_dict: Optional[Dict[NodeType, Node]] = None,
    capacities: Optional[Dict[str, int]] = None,
    ed_name: str = "My ED",
    routing_engine: Optional[RoutingEngine] = None,
) -> Optional[str]:
    """
    Render an interactive ED diagram with clickable box nodes.
    
    Args:
        nodes_dict: Dictionary of Node objects (for real-time stats)
        capacities: Dictionary of node capacities (node name -> capacity)
        ed_name: Name of the ED
        routing_engine: Routing engine for pathways
    
    Returns:
        Name of clicked node (if any) or None
    """
    if routing_engine is None:
        routing_engine = RoutingEngine()
    
    if capacities is None:
        from ed_simulation.config.defaults import NODE_CAPACITY_DEFAULTS
        capacities = NODE_CAPACITY_DEFAULTS.copy()
    
    # Node positions (x, y) - logical layout, not geographic
    positions: Dict[NodeType, Tuple[float, float]] = {
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
            'color': '#dc3545',
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
            'color': '#007bff',
        },
        'Fast Track (ESI 4-5)': {
            'path': [
                NodeType.TRIAGE,
                NodeType.FAST_TRACK,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DISPOSITION,
            ],
            'color': '#28a745',
        },
    }
    
    # Draw pathway lines first (behind nodes)
    for label, pathway_info in pathways.items():
        path = pathway_info['path']
        color = pathway_info['color']
        
        # Get coordinates for this pathway
        coords_x = []
        coords_y = []
        for node_type in path:
            if node_type in positions:
                x, y = positions[node_type]
                coords_x.append(x)
                coords_y.append(y)
        
        # Draw line
        if len(coords_x) > 1:
            fig.add_trace(go.Scatter(
                x=coords_x,
                y=coords_y,
                mode='lines',
                line=dict(color=color, width=4, dash='dot'),
                name=label,
                showlegend=True,
                hoverinfo='name',
                opacity=0.6,
            ))
    
    # Store node data for click handling
    node_data = {}
    
    # Add nodes as clickable boxes
    for node_type, (x, y) in positions.items():
        node_name = node_type.name.replace("_", " ").title()
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
        
        # Store node data
        node_data[node_type.name] = {
            'name': node_name,
            'capacity': cap,
            'queue': current_queue,
            'utilization': utilization,
            'in_service': in_service,
            'avg_wait': avg_wait_time,
            'lwbs': lwbs_count,
            'node_type': node_type,
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
        
        # Create hover text
        hover_text = (
            f"<b>{node_name}</b><br>"
            f"Capacity: {cap}<br>"
            f"Queue: {current_queue}<br>"
            f"In Service: {in_service}<br>"
            f"Utilization: {utilization*100:.1f}%<br>"
            f"Avg Wait: {avg_wait_time:.1f} min<br>"
            f"<extra></extra>"
        )
        
        # Add node as a box (using scatter with square markers)
        fig.add_trace(go.Scatter(
            x=[x],
            y=[y],
            mode='markers+text',
            marker=dict(
                size=120,
                symbol='square',
                color=box_color,
                line=dict(width=3, color='#333333'),
                opacity=0.9,
            ),
            text=[f"<b>{node_name}</b><br>Cap: {cap}<br>Q: {current_queue}"],
            textposition='middle center',
            textfont=dict(size=10, color='black'),
            name=node_name,
            hovertemplate=hover_text,
            customdata=[node_type.name],  # Store node name for click handling
            showlegend=False,
        ))
    
    # Update layout - disable zoom/pan for fixed view
    fig.update_layout(
        title=f"Interactive ED Diagram – {ed_name} (Click boxes to see details)",
        xaxis=dict(
            showgrid=False,
            showticklabels=False,
            range=[-0.5, 5.5],
            zeroline=False,
            fixedrange=True,  # Disable zoom
        ),
        yaxis=dict(
            showgrid=False,
            showticklabels=False,
            range=[1, 5],
            zeroline=False,
            fixedrange=True,  # Disable zoom
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
        hovermode='closest',
        dragmode=False,  # Disable panning
    )
    
    # Display in Streamlit
    clicked = st.plotly_chart(fig, use_container_width=True, key=f"interactive_diagram_{ed_name}")
    
    # Create interactive node selector
    st.markdown("---")
    st.subheader("📊 Node Details & Flow Information")
    
    # Node selector dropdown
    node_options = {data['name']: name for name, data in node_data.items()}
    selected_node_name = st.selectbox(
        "Select a node to view details:",
        options=list(node_options.keys()),
        index=0,
        key="node_selector",
    )
    
    # Get selected node data
    selected_node_key = node_options[selected_node_name]
    selected_data = node_data[selected_node_key]
    selected_node_type = selected_data['node_type']
    
    # Display node details in columns
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown(f"### {selected_data['name']}")
        st.markdown(f"**Capacity:** {selected_data['capacity']}")
        st.markdown(f"**Current Queue:** {selected_data['queue']}")
        st.markdown(f"**In Service:** {selected_data['in_service']}")
        st.markdown(f"**Utilization:** {selected_data['utilization']*100:.1f}%")
        st.markdown(f"**Average Wait Time:** {selected_data['avg_wait']:.1f} min")
        st.markdown(f"**LWBS Count:** {selected_data['lwbs']}")
    
    with col2:
        st.markdown("### Flow Information")
        
        # Find which pathways use this node
        pathways_using_node = []
        for pathway_name, pathway_info in pathways.items():
            if selected_node_type in pathway_info['path']:
                pathways_using_node.append(pathway_name)
        
        if pathways_using_node:
            st.markdown("**Used in pathways:**")
            for pathway in pathways_using_node:
                st.markdown(f"- {pathway}")
        
        # Find next nodes in pathways
        next_nodes = set()
        for pathway_name, pathway_info in pathways.items():
            path = pathway_info['path']
            if selected_node_type in path:
                try:
                    idx = path.index(selected_node_type)
                    if idx < len(path) - 1:
                        next_nodes.add(path[idx + 1])
                except ValueError:
                    pass
        
        if next_nodes:
            st.markdown("**Next nodes in flow:**")
            for next_node in next_nodes:
                next_name = next_node.name.replace("_", " ").title()
                st.markdown(f"- {next_name}")
        
        # Find previous nodes
        prev_nodes = set()
        for pathway_name, pathway_info in pathways.items():
            path = pathway_info['path']
            if selected_node_type in path:
                try:
                    idx = path.index(selected_node_type)
                    if idx > 0:
                        prev_nodes.add(path[idx - 1])
                except ValueError:
                    pass
        
        if prev_nodes:
            st.markdown("**Previous nodes in flow:**")
            for prev_node in prev_nodes:
                prev_name = prev_node.name.replace("_", " ").title()
                st.markdown(f"- {prev_name}")
    
    return None
