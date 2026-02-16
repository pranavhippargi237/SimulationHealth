"""
Patient routing logic based on ESI acuity levels.

This module provides intelligent routing decisions based on:
- Patient acuity (ESI 1-5)
- Current node in patient journey
- Clinical pathways (critical, standard, fast track)

Routing Rules:
- ESI 1-2 (Critical): Skip registration, go directly to bed → provider
- ESI 3 (Standard): Full pathway including registration
- ESI 4-5 (Fast Track): Use fast track pathway, skip diagnostics/treatment if minor
"""

from __future__ import annotations
from typing import Optional, Dict, List
from enum import Enum
import yaml
from pathlib import Path

from ..core.enums import NodeType, Acuity


class RoutingDecision(Enum):
    """Routing decision outcomes."""
    CONTINUE = "continue"  # Continue to next node in pathway
    SKIP = "skip"          # Skip this node
    END = "end"            # End of pathway (go to disposition)
    FAST_TRACK = "fast_track"  # Route to fast track


class RoutingEngine:
    """
    Determines patient routing through ED based on acuity and current node.
    
    Usage:
        >>> router = RoutingEngine()
        >>> next_node = router.get_next_node(
        ...     current_node=NodeType.TRIAGE,
        ...     acuity=Acuity.ESI_1
        ... )
        >>> assert next_node == NodeType.BED_ASSIGNMENT
    """
    
    def __init__(self, pathways_config: Optional[Dict] = None):
        """
        Initialize the routing engine.
        
        Args:
            pathways_config: Optional custom pathways configuration dict.
                            If None, uses default pathways.
        """
        # Define pathways for each acuity level
        if pathways_config:
            self._pathways = self._load_pathways_from_config(pathways_config)
        else:
            self._pathways = self._build_pathways()
    
    def _build_pathways(self) -> Dict[Acuity, list[NodeType]]:
        """
        Build default routing pathways for each acuity level.
        
        Returns:
            Dict mapping acuity to ordered list of nodes
        """
        return {
            # ESI 1-2: Critical pathway - skip registration
            Acuity.ESI_1: [
                NodeType.TRIAGE,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ],
            Acuity.ESI_2: [
                NodeType.TRIAGE,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ],
            # ESI 3: Standard pathway - full flow
            Acuity.ESI_3: [
                NodeType.TRIAGE,
                NodeType.REGISTRATION,
                NodeType.BED_ASSIGNMENT,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DIAGNOSTICS,
                NodeType.TREATMENT,
                NodeType.DISPOSITION,
            ],
            # ESI 4-5: Fast track pathway
            Acuity.ESI_4: [
                NodeType.TRIAGE,
                NodeType.FAST_TRACK,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DISPOSITION,
            ],
            Acuity.ESI_5: [
                NodeType.TRIAGE,
                NodeType.FAST_TRACK,
                NodeType.PROVIDER_ASSESSMENT,
                NodeType.DISPOSITION,
            ],
        }
    
    def _load_pathways_from_config(self, pathways_config: Dict) -> Dict[Acuity, list[NodeType]]:
        """
        Load pathways from configuration dictionary.
        
        Args:
            pathways_config: Dictionary with pathways configuration
                           Format: {"pathways": {"ESI_1": {"nodes": [...]}, ...}}
        
        Returns:
            Dict mapping acuity to ordered list of nodes
        """
        pathways = {}
        
        # Get pathways section
        pathways_data = pathways_config.get("pathways", {})
        
        # Map each ESI level
        for esi_str, pathway_data in pathways_data.items():
            try:
                # Convert "ESI_1" to Acuity.ESI_1
                acuity = Acuity[esi_str]
                
                # Get node list
                node_names = pathway_data.get("nodes", [])
                
                # Convert node name strings to NodeType enums
                node_list = []
                for node_name in node_names:
                    try:
                        node_type = NodeType[node_name]
                        node_list.append(node_type)
                    except KeyError:
                        # Skip invalid node names
                        continue
                
                if node_list:
                    pathways[acuity] = node_list
            except KeyError:
                # Skip invalid ESI levels
                continue
        
        # Fill in any missing pathways with defaults
        default_pathways = self._build_pathways()
        for acuity in Acuity:
            if acuity not in pathways:
                pathways[acuity] = default_pathways.get(acuity, default_pathways[Acuity.ESI_3])
        
        return pathways
    
    def load_pathways_from_yaml(self, yaml_path: str) -> None:
        """
        Load pathways from a YAML file.
        
        Args:
            yaml_path: Path to YAML file containing pathways configuration
        """
        with open(yaml_path, 'r') as f:
            config = yaml.safe_load(f)
        self._pathways = self._load_pathways_from_config(config)
    
    def get_pathways_config(self) -> Dict:
        """
        Get current pathways as a configuration dictionary.
        
        Returns:
            Dictionary in YAML-serializable format
        """
        pathways_dict = {}
        for acuity, node_list in self._pathways.items():
            pathways_dict[acuity.name] = {
                "name": self.get_pathway_name(acuity),
                "nodes": [node.name for node in node_list]
            }
        
        return {"pathways": pathways_dict}
    
    def save_pathways_to_yaml(self, yaml_path: str) -> None:
        """
        Save current pathways to a YAML file.
        
        Args:
            yaml_path: Path where to save the YAML file
        """
        config = self.get_pathways_config()
        with open(yaml_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    def update_pathway(self, acuity: Acuity, nodes: List[NodeType]) -> None:
        """
        Update the pathway for a specific acuity level.
        
        Args:
            acuity: Acuity level to update
            nodes: Ordered list of nodes for this pathway
        """
        if not nodes:
            raise ValueError("Pathway must contain at least one node")
        
        # Validate that TRIAGE is first
        if nodes[0] != NodeType.TRIAGE:
            raise ValueError("Pathway must start with TRIAGE")
        
        # Validate that DISPOSITION is last
        if nodes[-1] != NodeType.DISPOSITION:
            raise ValueError("Pathway must end with DISPOSITION")
        
        self._pathways[acuity] = nodes.copy()
    
    def get_pathway(self, acuity: Acuity) -> list[NodeType]:
        """
        Get the complete pathway for a given acuity level.
        
        Args:
            acuity: Patient acuity level
            
        Returns:
            Ordered list of nodes in the pathway
        """
        return self._pathways.get(acuity, self._pathways[Acuity.ESI_3]).copy()
    
    def get_next_node(
        self,
        current_node: NodeType,
        acuity: Acuity,
        visited_nodes: Optional[set[NodeType]] = None
    ) -> Optional[NodeType]:
        """
        Determine the next node for a patient based on current position and acuity.
        
        Args:
            current_node: The node the patient just completed
            acuity: Patient acuity level
            visited_nodes: Optional set of nodes already visited (for validation)
            
        Returns:
            Next NodeType in the pathway, or None if at end of pathway
        """
        pathway = self.get_pathway(acuity)
        
        # Find current node in pathway
        try:
            current_index = pathway.index(current_node)
        except ValueError:
            # Current node not in pathway - this shouldn't happen in normal flow
            # Return None to indicate error/end
            return None
        
        # Check if we're at the end
        if current_index >= len(pathway) - 1:
            return None  # End of pathway
        
        # Return next node
        next_node = pathway[current_index + 1]
        
        # Validate that we haven't already visited it (safety check)
        if visited_nodes and next_node in visited_nodes:
            # This shouldn't happen, but handle gracefully
            return None
        
        return next_node
    
    def next_node(
        self,
        current_node: NodeType,
        acuity: Acuity
    ) -> Optional[NodeType]:
        """
        Alias for get_next_node() for convenience.
        
        Determine the next node for a patient based on current position and acuity.
        
        Args:
            current_node: The node the patient just completed
            acuity: Patient acuity level
            
        Returns:
            Next NodeType in the pathway, or None if at end of pathway
        """
        return self.get_next_node(current_node, acuity)
    
    def should_skip_node(self, node: NodeType, acuity: Acuity) -> bool:
        """
        Check if a node should be skipped for a given acuity.
        
        Args:
            node: Node to check
            acuity: Patient acuity level
            
        Returns:
            True if node should be skipped
        """
        pathway = self.get_pathway(acuity)
        return node not in pathway
    
    def is_fast_track_patient(self, acuity: Acuity) -> bool:
        """
        Check if patient should use fast track pathway.
        
        Args:
            acuity: Patient acuity level
            
        Returns:
            True if patient should use fast track
        """
        return acuity in (Acuity.ESI_4, Acuity.ESI_5)
    
    def get_pathway_name(self, acuity: Acuity) -> str:
        """
        Get human-readable pathway name for an acuity level.
        
        Args:
            acuity: Patient acuity level
            
        Returns:
            Pathway name (e.g., "Critical", "Standard", "Fast Track")
        """
        if acuity in (Acuity.ESI_1, Acuity.ESI_2):
            return "Critical"
        elif acuity == Acuity.ESI_3:
            return "Standard"
        else:
            return "Fast Track"

