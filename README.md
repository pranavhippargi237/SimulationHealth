# ED Digital Twin MVP

A configurable Emergency Department throughput simulation tool using SimPy and Streamlit.

## Features

- **Discrete-event simulation** of ED patient flow using SimPy
- **National ED metrics**: Door-to-Provider (ED-2b), Door-to-Bed, LOS, LWBS, LBTC
- **ESI acuity levels** (1-5) with priority queuing
- **LWBS modeling** with configurable probability models
- **Scenario presets**:
  - Boarding (reduced bed capacity)
  - Vertical/Fast Track (ESI 4-5 pathway)
  - Staffing Adjustment (time-based capacity)
  - Surge (+30% arrivals)

## Installation

```bash
pip install simpy streamlit pandas pytest
```

## Usage

### Run Streamlit App
```bash
streamlit run ed_simulation/app.py
```

### Run Demo Script
```bash
python -m ed_simulation.demo
```

### Run Tests
```bash
pytest ed_simulation/tests/ -v
```

## Project Structure

```
ed_simulation/
├── app.py                 # Streamlit application
├── demo.py                # CLI demo script
├── config/
│   └── defaults.py        # Default ED parameters
├── core/
│   ├── enums.py           # Acuity, NodeType, PatientStatus
│   ├── patient.py         # Patient class with metrics
│   └── node.py            # Node class (SimPy resource)
├── processes/
│   ├── arrivals.py        # Poisson arrival generator
│   └── lwbs.py            # LWBS probability models
├── scenarios/
│   ├── boarding.py        # Boarding scenario
│   ├── vertical_track.py  # Fast track scenario
│   ├── staffing_adjustment.py
│   └── surge.py           # Surge scenario
├── simulation/
│   └── metrics.py         # National ED metrics
└── tests/
    ├── test_patient.py
    └── test_node.py
```

## Key Metrics

| Metric | Description |
|--------|-------------|
| Door-to-Provider | Time from arrival to provider assessment (ED-2b) |
| Door-to-Bed | Time from arrival to bed assignment |
| Length of Stay | Total time in ED (ED-1b) |
| LWBS Rate | Left Without Being Seen percentage |
| LBTC Rate | Left Before Treatment Complete percentage |

## License

MIT
