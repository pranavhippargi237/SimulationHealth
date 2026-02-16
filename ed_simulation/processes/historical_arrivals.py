"""
Historical arrival data replay for backtesting.

This module provides functionality to replay actual historical patient arrivals
through the simulation for validation and comparison purposes.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Generator, Callable, Optional
from datetime import datetime, timedelta

import simpy

from ..core.enums import Acuity
from ..core.patient import Patient, PatientConfig


@dataclass
class HistoricalArrival:
    """Represents a historical patient arrival."""
    arrival_time: float  # Time in minutes from start
    acuity: Acuity
    patient_id: Optional[str] = None


class HistoricalArrivalGenerator:
    """
    Generates patient arrivals from historical CSV data.
    
    Replays actual patient arrivals at their recorded times with their
    actual acuity levels for backtesting and validation.
    
    Usage:
        >>> arrivals = parse_historical_csv(csv_data)
        >>> generator = HistoricalArrivalGenerator(env, arrivals)
        >>> env.process(generator.run(on_arrival=process_patient))
        >>> env.run(until=max_time)
    """
    
    def __init__(
        self,
        env: simpy.Environment,
        arrivals: List[HistoricalArrival],
        patient_config: Optional[PatientConfig] = None,
    ):
        """
        Initialize historical arrival generator.
        
        Args:
            env: SimPy environment
            arrivals: List of historical arrivals (sorted by arrival_time)
            patient_config: Optional patient configuration
        """
        self._env = env
        self._arrivals = sorted(arrivals, key=lambda a: a.arrival_time)
        self._patient_config = patient_config
        self._patients_generated = 0
    
    def run(
        self,
        on_arrival: Callable[[Patient], Generator],
        until: Optional[float] = None,
    ) -> Generator:
        """
        Replay historical arrivals.
        
        Args:
            on_arrival: Callback that takes a Patient and returns a
                       SimPy generator (the patient's journey process)
            until: Optional end time (in minutes). If None, uses max arrival time.
        
        Yields:
            SimPy timeout events
        """
        if not self._arrivals:
            return
        
        # Determine end time
        max_arrival_time = max(a.arrival_time for a in self._arrivals)
        end_time = until if until is not None else max_arrival_time + 60  # Add buffer
        
        # Process each arrival
        for arrival in self._arrivals:
            # Skip arrivals after end time
            if arrival.arrival_time > end_time:
                break
            
            # Wait until arrival time
            if arrival.arrival_time > self._env.now:
                yield self._env.timeout(arrival.arrival_time - self._env.now)
            
            # Create patient at historical arrival time
            patient = Patient(
                env=self._env,
                arrival_time=arrival.arrival_time,
                acuity=arrival.acuity,
                config=self._patient_config,
                patient_id=arrival.patient_id,
            )
            
            # Start patient journey
            self._env.process(on_arrival(patient))
            self._patients_generated += 1
    
    @property
    def patients_generated(self) -> int:
        """Total number of patients generated."""
        return self._patients_generated


def parse_historical_csv(
    csv_content: str,
    arrival_time_column: str = "arrival_time",
    esi_column: str = "esi",
    time_format: str = "minutes"
) -> List[HistoricalArrival]:
    """
    Parse historical arrivals from CSV content.
    
    Expected CSV format:
        arrival_time,esi
        0,3
        15.5,2
        30,4
        ...
    
    Args:
        csv_content: CSV file content as string
        arrival_time_column: Name of column containing arrival times
        esi_column: Name of column containing ESI levels
        time_format: Format of arrival_time - "minutes" (default) or "datetime"
    
    Returns:
        List of HistoricalArrival objects
    
    Raises:
        ValueError: If CSV format is invalid or missing required columns
    """
    import csv
    from io import StringIO
    
    arrivals = []
    reader = csv.DictReader(StringIO(csv_content))
    
    # Validate columns
    if arrival_time_column not in reader.fieldnames:
        raise ValueError(f"CSV missing required column: {arrival_time_column}")
    if esi_column not in reader.fieldnames:
        raise ValueError(f"CSV missing required column: {esi_column}")
    
    # Parse rows
    base_time = None
    for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
        try:
            # Parse arrival time
            if time_format == "datetime":
                # Parse datetime string (e.g., "2024-01-01 08:30:00")
                dt = datetime.strptime(row[arrival_time_column], "%Y-%m-%d %H:%M:%S")
                # Convert to minutes from start (assuming first row is time 0)
                if base_time is None:
                    base_time = dt
                arrival_time = (dt - base_time).total_seconds() / 60.0
            else:
                # Parse as minutes (float)
                arrival_time = float(row[arrival_time_column])
            
            # Parse ESI (can be integer or string like "ESI_3")
            esi_str = str(row[esi_column]).strip()
            if esi_str.startswith("ESI_"):
                esi_value = int(esi_str.split("_")[1])
            else:
                esi_value = int(esi_str)
            
            # Validate ESI
            if esi_value < 1 or esi_value > 5:
                raise ValueError(f"Invalid ESI value: {esi_value} (must be 1-5)")
            
            # Convert to Acuity enum
            acuity = Acuity(esi_value)
            
            # Create historical arrival
            arrivals.append(HistoricalArrival(
                arrival_time=arrival_time,
                acuity=acuity,
                patient_id=f"H-{row_num:06d}"  # Historical patient ID
            ))
            
        except (ValueError, KeyError) as e:
            raise ValueError(f"Error parsing row {row_num}: {e}")
    
    return arrivals

