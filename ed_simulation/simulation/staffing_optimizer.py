"""
Staffing optimization for ED simulation.

This module provides constrained optimization to find optimal staffing
configurations within FTE constraints, optimizing for different goals:
- Minimize LWBS rate
- Maximize utilization
- Cost-neutral improvements
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from enum import Enum

from ..core.enums import NodeType
from ..config.defaults import NODE_CAPACITY_DEFAULTS


class OptimizationGoal(Enum):
    """Optimization objectives."""
    MINIMIZE_LWBS = "min_lwbs"
    MAXIMIZE_UTILIZATION = "max_utilization"
    COST_NEUTRAL = "cost_neutral"


@dataclass
class StaffingSuggestion:
    """A staffing configuration suggestion."""
    node_capacities: Dict[NodeType, int]
    expected_lwbs_rate: float
    expected_los_mean: Optional[float]
    expected_utilization: float
    total_fte: int
    score: float  # Higher is better
    description: str


class StaffingOptimizer:
    """
    Optimizes staffing configurations within FTE constraints.
    
    Uses greedy search to find feasible staffing changes that improve
    key metrics while staying within total FTE budget.
    """
    
    def __init__(
        self,
        base_capacities: Optional[Dict[NodeType, int]] = None,
        fte_per_capacity: Optional[Dict[NodeType, float]] = None,
    ):
        """
        Initialize optimizer.
        
        Args:
            base_capacities: Base capacity for each node type
            fte_per_capacity: FTE required per unit capacity for each node
        """
        self._base_capacities = base_capacities or {
            node_type: NODE_CAPACITY_DEFAULTS.get(node_type.name, 1)
            for node_type in NodeType
        }
        
        # Default FTE multipliers (simplified - in reality varies by role)
        self._fte_per_capacity = fte_per_capacity or {
            NodeType.TRIAGE: 0.5,  # Nurses
            NodeType.REGISTRATION: 0.3,  # Clerks
            NodeType.BED_ASSIGNMENT: 0.0,  # Beds (no direct staffing)
            NodeType.FAST_TRACK: 0.5,
            NodeType.PROVIDER_ASSESSMENT: 1.0,  # Physicians/NPs
            NodeType.DIAGNOSTICS: 0.4,  # Techs
            NodeType.TREATMENT: 0.0,  # Uses bed capacity
            NodeType.DISPOSITION: 1.0,  # Providers
        }
    
    def calculate_fte(self, capacities: Dict[NodeType, int]) -> float:
        """
        Calculate total FTE for given capacities.
        
        Args:
            capacities: Node capacities
            
        Returns:
            Total FTE
        """
        total = 0.0
        for node_type, capacity in capacities.items():
            fte_mult = self._fte_per_capacity.get(node_type, 0.5)
            total += capacity * fte_mult
        return total
    
    def generate_suggestions(
        self,
        max_fte: float,
        goal: OptimizationGoal,
        num_suggestions: int = 3,
    ) -> List[StaffingSuggestion]:
        """
        Generate staffing optimization suggestions.
        
        Uses greedy search to find configurations that:
        - Stay within max_fte constraint
        - Optimize for the specified goal
        
        Args:
            max_fte: Maximum total FTE budget
            goal: Optimization objective
            num_suggestions: Number of suggestions to generate
            
        Returns:
            List of staffing suggestions, ranked by score
        """
        suggestions = []
        
        # Strategy 1: Increase high-impact nodes (providers, triage)
        if goal == OptimizationGoal.MINIMIZE_LWBS:
            suggestions.extend(
                self._suggest_for_lwbs_reduction(max_fte, num_suggestions)
            )
        elif goal == OptimizationGoal.MAXIMIZE_UTILIZATION:
            suggestions.extend(
                self._suggest_for_utilization(max_fte, num_suggestions)
            )
        else:  # COST_NEUTRAL
            suggestions.extend(
                self._suggest_cost_neutral(max_fte, num_suggestions)
            )
        
        # Sort by score (higher is better)
        suggestions.sort(key=lambda s: s.score, reverse=True)
        
        return suggestions[:num_suggestions]
    
    def _suggest_for_lwbs_reduction(
        self,
        max_fte: float,
        num_suggestions: int
    ) -> List[StaffingSuggestion]:
        """Generate suggestions focused on reducing LWBS."""
        suggestions = []
        base_fte = self.calculate_fte(self._base_capacities)
        
        # Strategy 1: Add providers (most impact on wait times)
        if base_fte < max_fte:
            for provider_increase in [1, 2, 3]:
                capacities = self._base_capacities.copy()
                capacities[NodeType.PROVIDER_ASSESSMENT] += provider_increase
                
                if self.calculate_fte(capacities) <= max_fte:
                    suggestions.append(StaffingSuggestion(
                        node_capacities=capacities,
                        expected_lwbs_rate=0.0,  # Will be calculated by simulation
                        expected_los_mean=None,
                        expected_utilization=0.0,
                        total_fte=self.calculate_fte(capacities),
                        score=provider_increase * 10,  # Higher provider count = better
                        description=f"+{provider_increase} provider(s) (focus on wait times)"
                    ))
        
        # Strategy 2: Add triage capacity
        for triage_increase in [1, 2]:
            capacities = self._base_capacities.copy()
            capacities[NodeType.TRIAGE] += triage_increase
            
            if self.calculate_fte(capacities) <= max_fte:
                suggestions.append(StaffingSuggestion(
                    node_capacities=capacities,
                    expected_lwbs_rate=0.0,
                    expected_los_mean=None,
                    expected_utilization=0.0,
                    total_fte=self.calculate_fte(capacities),
                    score=triage_increase * 5,
                    description=f"+{triage_increase} triage nurse(s) (reduce initial wait)"
                ))
        
        # Strategy 3: Balanced increase
        capacities = self._base_capacities.copy()
        capacities[NodeType.PROVIDER_ASSESSMENT] += 1
        capacities[NodeType.TRIAGE] += 1
        
        if self.calculate_fte(capacities) <= max_fte:
            suggestions.append(StaffingSuggestion(
                node_capacities=capacities,
                expected_lwbs_rate=0.0,
                expected_los_mean=None,
                expected_utilization=0.0,
                total_fte=self.calculate_fte(capacities),
                score=15,
                description="+1 provider, +1 triage (balanced approach)"
            ))
        
        return suggestions
    
    def _suggest_for_utilization(
        self,
        max_fte: float,
        num_suggestions: int
    ) -> List[StaffingSuggestion]:
        """Generate suggestions focused on maximizing utilization."""
        suggestions = []
        
        # Strategy: Add capacity to bottleneck nodes
        # Provider assessment is often the bottleneck
        for provider_increase in [1, 2]:
            capacities = self._base_capacities.copy()
            capacities[NodeType.PROVIDER_ASSESSMENT] += provider_increase
            
            if self.calculate_fte(capacities) <= max_fte:
                suggestions.append(StaffingSuggestion(
                    node_capacities=capacities,
                    expected_lwbs_rate=0.0,
                    expected_los_mean=None,
                    expected_utilization=0.0,
                    total_fte=self.calculate_fte(capacities),
                    score=provider_increase * 8,
                    description=f"+{provider_increase} provider(s) (reduce bottleneck)"
                ))
        
        # Add diagnostics capacity
        capacities = self._base_capacities.copy()
        capacities[NodeType.DIAGNOSTICS] += 2
        
        if self.calculate_fte(capacities) <= max_fte:
            suggestions.append(StaffingSuggestion(
                node_capacities=capacities,
                expected_lwbs_rate=0.0,
                expected_los_mean=None,
                expected_utilization=0.0,
                total_fte=self.calculate_fte(capacities),
                score=6,
                description="+2 diagnostics capacity (reduce wait for tests)"
            ))
        
        return suggestions
    
    def _suggest_cost_neutral(
        self,
        max_fte: float,
        num_suggestions: int
    ) -> List[StaffingSuggestion]:
        """Generate cost-neutral suggestions (reallocate within budget)."""
        suggestions = []
        base_fte = self.calculate_fte(self._base_capacities)
        
        # Strategy: Reallocate from low-impact to high-impact areas
        # Example: Reduce registration, add to providers
        capacities = self._base_capacities.copy()
        if capacities[NodeType.REGISTRATION] > 1:
            capacities[NodeType.REGISTRATION] -= 1
            capacities[NodeType.PROVIDER_ASSESSMENT] += 1
            
            if self.calculate_fte(capacities) <= max_fte:
                suggestions.append(StaffingSuggestion(
                    node_capacities=capacities,
                    expected_lwbs_rate=0.0,
                    expected_los_mean=None,
                    expected_utilization=0.0,
                    total_fte=self.calculate_fte(capacities),
                    score=12,
                    description="Reallocate: -1 registration, +1 provider (cost-neutral)"
                ))
        
        # Add fast track capacity
        capacities = self._base_capacities.copy()
        capacities[NodeType.FAST_TRACK] += 1
        
        if self.calculate_fte(capacities) <= max_fte:
            suggestions.append(StaffingSuggestion(
                node_capacities=capacities,
                expected_lwbs_rate=0.0,
                expected_los_mean=None,
                expected_utilization=0.0,
                total_fte=self.calculate_fte(capacities),
                score=8,
                description="+1 fast track capacity (low cost, high impact for ESI 4-5)"
            ))
        
        return suggestions

