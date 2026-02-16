# End-to-End Capabilities - ED Digital Twin MVP

This document outlines all the complete workflows you can perform with the application.

## 🚀 How to Start

```bash
# Activate virtual environment
source venv/bin/activate

# Launch Streamlit app
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`

---

## 📋 Complete End-to-End Workflows

### 1. **Baseline ED Simulation** ✅

**What you can do:**
- Run a 24-hour ED simulation with configurable arrival rates
- See patient flow through all nodes (Triage → Registration → Bed → Provider → Diagnostics → Treatment → Disposition)
- View comprehensive metrics including:
  - Length of Stay (LOS) statistics
  - Left Without Being Seen (LWBS) rate
  - Door-to-Provider times
  - Node-level statistics (utilization, queue lengths, wait times)
- View interactive visualizations:
  - LOS distribution density plot
  - Key metrics bar chart
  - Queue length over time line chart

**Steps:**
1. Set arrival rate in sidebar (default: 3.0 patients/hour)
2. Select "None (Baseline)" scenario
3. Click "Run 24-Hour Simulation"
4. Review metrics, charts, and patient journey details

**Output:**
- Summary metrics table
- Patient journey sample
- Three interactive Plotly charts
- Node-level statistics

---

### 2. **Scenario Testing & Comparison** ✅

**What you can do:**
Test different operational scenarios and compare their impact on ED performance.

**Available Scenarios:**

#### a) **Boarding Scenario**
- Simulates admitted patients occupying ED beds
- Configurable: 10-80% of beds occupied (default: 40%)
- Impact: Reduces available bed capacity, increases wait times

#### b) **Vertical/Fast Track Scenario**
- Dedicates providers for low-acuity (ESI 4-5) patients
- Configurable: 1-5 fast track providers (default: 2)
- Impact: Faster throughput for low-acuity patients, reduces overall LOS

#### c) **Staffing Adjustment Scenario**
- Adds extra providers during peak hours (2pm-10pm)
- Configurable: 0-5 additional peak providers (default: 2)
- Impact: Reduces wait times during busy periods

#### d) **Surge Scenario**
- Simulates increased patient arrivals
- Configurable: 10-100% arrival rate increase (default: 30%)
- Impact: Tests ED capacity under stress

**Steps:**
1. Select a scenario from dropdown
2. Adjust scenario-specific parameters (sliders appear automatically)
3. Click "Run 24-Hour Simulation"
4. Compare results with baseline

**Output:**
- Scenario name and parameters displayed
- Metrics showing impact of scenario
- Visualizations comparing scenario vs baseline
- Detailed node statistics

---

### 3. **Historical Data Backtesting** ✅

**What you can do:**
- Upload historical patient arrival data (CSV format)
- Replay the exact arrival pattern through simulation
- Compare simulated results with actual historical outcomes
- Identify gaps between model predictions and reality

**CSV Format Required:**
```csv
arrival_time,esi
0,3
15,2
30,4
45,1
...
```

**Optional columns for comparison:**
- `los` - Length of stay (minutes)
- `lwbs` - Left without being seen (0 or 1)
- `door_to_provider` - Door-to-provider time (minutes)

**Steps:**
1. Prepare CSV with `arrival_time` (minutes from start) and `esi` (1-5) columns
2. Upload CSV using file uploader
3. Preview data to verify format
4. Click "🔄 Replay & Compare"
5. Review comparison table showing:
   - Actual vs Simulated metrics
   - Delta (difference) for each metric
6. View visualizations:
   - LOS distribution (simulated vs actual overlay)
   - Key metrics comparison (actual vs simulated bars)
   - Queue length over time

**Output:**
- Comparison table with deltas
- Visual overlays showing model accuracy
- Identification of model calibration needs

---

### 4. **Staffing Optimization** ✅

**What you can do:**
- Get data-driven staffing recommendations within FTE budget constraints
- Optimize for different goals:
  - **Minimize LWBS**: Reduce left-without-being-seen rate
  - **Maximize Utilization**: Improve staff efficiency
  - **Cost Neutral**: Reallocate within existing budget
- See expected impact of each suggestion before implementation

**Steps:**
1. In sidebar, set "Max Total FTE" (5.0-30.0, default: 15.0)
2. Select "Priority Goal" from dropdown
3. Click "💡 Suggest 2–3 Options"
4. Wait for simulations to complete (each suggestion is evaluated)
5. Review ranked suggestions:
   - Best option expanded by default
   - Each shows: FTE, expected LWBS rate, LOS, utilization
   - Capacity changes from baseline
6. Compare all options in summary table

**Output:**
- 2-3 ranked staffing suggestions
- Expected metrics for each option
- Capacity change details
- Comparison table for easy decision-making

**Example Use Cases:**
- "What's the best way to reduce LWBS with 15 FTE budget?"
- "How can I improve utilization without increasing costs?"
- "What staffing changes give the best ROI?"

---

### 5. **Acuity-Based Patient Routing** ✅

**What you can do:**
The simulation automatically routes patients based on ESI acuity:

- **ESI 1-2 (Critical)**: 
  - Triage → Bed → Provider → Diagnostics → Treatment → Disposition
  - Skips Registration (fast-tracked)
  
- **ESI 3 (Standard)**:
  - Full pathway: Triage → Registration → Bed → Provider → Diagnostics → Treatment → Disposition
  
- **ESI 4-5 (Low Acuity)**:
  - Fast Track pathway: Triage → Fast Track → Provider → Disposition
  - Skips Diagnostics/Treatment for minor cases

**This happens automatically** - no configuration needed. The routing engine ensures realistic patient flow.

---

## 📊 Metrics You Can Track

### Patient-Level Metrics:
- **Length of Stay (LOS)**: Total time in ED
- **Door-to-Triage**: Time from arrival to triage
- **Door-to-Bed**: Time from arrival to bed assignment
- **Door-to-Provider**: Time from arrival to provider assessment
- **Provider-to-Disposition**: Time from provider to discharge decision
- **LWBS Rate**: Percentage who left without being seen

### Node-Level Metrics:
- **Utilization**: Percentage of capacity in use
- **Queue Length**: Number of patients waiting
- **Wait Time**: Average time in queue
- **Service Time**: Average processing time
- **Throughput**: Patients served per hour

---

## 🎯 Typical Workflows

### **Workflow 1: Baseline Assessment**
1. Run baseline simulation
2. Review current performance metrics
3. Identify bottlenecks (high queue lengths, long wait times)
4. Note areas for improvement

### **Workflow 2: Scenario Planning**
1. Run baseline for comparison
2. Test different scenarios (boarding, surge, etc.)
3. Compare metrics across scenarios
4. Use visualizations to see impact
5. Make data-driven decisions

### **Workflow 3: Model Validation**
1. Upload historical data
2. Run backtesting simulation
3. Compare simulated vs actual metrics
4. Identify calibration gaps
5. Adjust model parameters if needed

### **Workflow 4: Staffing Optimization**
1. Define FTE budget constraint
2. Set optimization goal (minimize LWBS, etc.)
3. Get ranked suggestions
4. Review expected impacts
5. Select best option for implementation

### **Workflow 5: What-If Analysis**
1. Test surge scenario with 50% increase
2. See impact on LWBS and LOS
3. Test staffing adjustment to mitigate
4. Compare multiple scenarios side-by-side
5. Plan for capacity management

---

## 🔍 What's NOT Yet Available

These features are planned but not yet implemented:
- Multiple replications with confidence intervals
- Configuration file support (YAML/JSON)
- Export to CSV/PDF reports
- Warmup period filtering (stats include warmup)
- Advanced patient journey tracking (LBTC logic)

---

## 💡 Tips for Best Results

1. **For accurate results**: Run multiple simulations with different seeds (currently manual)
2. **For backtesting**: Ensure CSV has accurate arrival times and ESI levels
3. **For optimization**: Start with realistic FTE budgets based on your actual staffing
4. **For scenarios**: Test one scenario at a time to understand individual impacts
5. **For visualization**: Use the interactive charts to drill into specific time periods or nodes

---

## 🎓 Example Use Cases

### Use Case 1: "We're seeing high LWBS rates"
1. Run baseline simulation
2. Check queue lengths - identify bottlenecks
3. Use staffing optimizer with "Minimize LWBS" goal
4. Review suggestions (likely adding providers or triage capacity)
5. Test suggested configuration
6. Compare before/after metrics

### Use Case 2: "Planning for flu season surge"
1. Run baseline to establish current performance
2. Test "Surge" scenario with 50% increase
3. See impact on LWBS, LOS, queue lengths
4. Test "Staffing Adjustment" scenario to mitigate
5. Compare scenarios to find optimal response

### Use Case 3: "Validating our simulation model"
1. Export historical arrival data to CSV
2. Upload and run backtesting
3. Compare simulated vs actual LOS, LWBS rates
4. If gaps exist, adjust service time parameters
5. Re-run until model matches reality

### Use Case 4: "Optimizing within budget constraints"
1. Set max FTE to your actual budget
2. Choose optimization goal (e.g., "Minimize LWBS")
3. Get ranked suggestions
4. Review expected impacts
5. Test top suggestion in full simulation
6. Implement if results are acceptable

---

## 📈 Next Steps to Enhance

To make this even more powerful, consider:
- Adding multiple replications for statistical confidence
- Implementing warmup period filtering
- Adding export functionality for reports
- Creating configuration files for easy parameter adjustment
- Building a comparison dashboard for multiple scenarios

---

**You now have a fully functional ED simulation tool that can:**
✅ Model realistic patient flow with acuity-based routing
✅ Test operational scenarios
✅ Validate against historical data
✅ Optimize staffing within constraints
✅ Visualize results interactively

**Start exploring with: `streamlit run app.py`**

