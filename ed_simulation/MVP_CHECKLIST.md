# ED Simulation MVP - Completion Checklist

## ✅ Currently Implemented

### Core Components
- ✅ Patient class with full journey tracking
- ✅ Node class with priority queuing and LWBS monitoring
- ✅ Arrival generator (Poisson process with time-of-day variation)
- ✅ LWBS probability models (linear, exponential, logistic, piecewise)
- ✅ Service time distributions (exponential, lognormal, triangular, uniform, constant)
- ✅ Priority-based queuing by acuity
- ✅ Statistics collection at node level
- ✅ Basic simulation runner (`main.py`)

### Advanced Features (Partially Implemented)
- ✅ Metrics collector (`simulation/metrics.py`) - calculates ED-1b, ED-2b metrics
- ✅ Scenario framework (`scenarios/`) - boarding, vertical track, staffing, surge
- ✅ Streamlit app framework (`app.py`)

---

## ❌ Missing for Full MVP

### 1. **Patient Routing Logic** (HIGH PRIORITY)
**Current State:** Simple linear flow through all nodes for all patients
**Needed:**
- Conditional pathways based on acuity (ESI 1-2 may skip registration)
- Optional nodes (not all patients need diagnostics)
- Fast track pathway for ESI 4-5 patients
- Bypass logic for critical patients

**Files to Update:**
- `main.py` - `patient_journey()` method needs routing logic
- Consider creating `processes/routing.py` for pathway determination

### 2. **Warmup Period Statistics Filtering** (HIGH PRIORITY)
**Current State:** Statistics include warmup period
**Needed:**
- Filter statistics to only include data after warmup period
- Reset node statistics after warmup
- Filter patient tracking after warmup

**Files to Update:**
- `main.py` - Add warmup filtering in `_print_statistics()`
- `core/node.py` - Add method to reset stats at warmup end

### 3. **Multiple Replications Support** (MEDIUM PRIORITY)
**Current State:** Single run only
**Needed:**
- Run multiple replications with different seeds
- Aggregate statistics across replications
- Calculate confidence intervals

**Files to Create/Update:**
- `simulation/replications.py` - Replication runner
- `main.py` - Add `--replications` argument

### 4. **Streamlit App Dependencies & Integration** (HIGH PRIORITY)
**Current State:** `app.py` exists but may not run
**Needed:**
- Install Streamlit: `pip install streamlit pandas`
- Test and fix any import issues
- Ensure scenarios are properly integrated
- Add visualization (charts, graphs)

**Files to Update:**
- `requirements.txt` - Add streamlit, pandas, matplotlib/plotly
- `app.py` - Test and fix any bugs

### 5. **Configuration File Support** (MEDIUM PRIORITY)
**Current State:** Hardcoded defaults
**Needed:**
- YAML/JSON configuration file support
- Override defaults from config
- Scenario-specific configurations

**Files to Create:**
- `config/loader.py` - Configuration file loader
- `config/example_config.yaml` - Example configuration

### 6. **Enhanced Reporting & Export** (MEDIUM PRIORITY)
**Current State:** Basic console output
**Needed:**
- Export statistics to CSV/JSON
- Generate formatted reports (PDF/HTML)
- Summary dashboards

**Files to Create:**
- `simulation/reporting.py` - Report generation
- `simulation/export.py` - Data export functions

### 7. **Patient Journey Enhancements** (MEDIUM PRIORITY)
**Current State:** Basic properties exist but may need implementation
**Needed:**
- Verify `door_to_provider`, `door_to_bed`, `seen_by_provider` work correctly
- Implement `is_lbtc` (Left Before Treatment Complete) logic
- Track provider contact time accurately

**Files to Update:**
- `core/patient.py` - Verify all metric properties work
- `main.py` - Ensure patient journey tracks these correctly

### 8. **Scenario Integration** (HIGH PRIORITY)
**Current State:** Scenarios exist but may not be fully integrated
**Needed:**
- Test all scenarios work with main simulation
- Ensure scenarios modify node capacities correctly
- Verify vertical track creates separate fast track nodes

**Files to Test/Update:**
- `scenarios/boarding.py`
- `scenarios/vertical_track.py`
- `scenarios/staffing_adjustment.py`
- `scenarios/surge.py`
- `main.py` - Add scenario selection

### 9. **Documentation** (LOW PRIORITY)
**Current State:** No README or user documentation
**Needed:**
- README.md with setup instructions
- Usage examples
- Configuration guide
- API documentation

**Files to Create:**
- `README.md`
- `docs/` directory with detailed docs

### 10. **Requirements File** (HIGH PRIORITY)
**Current State:** No requirements.txt
**Needed:**
- List all dependencies
- Pin versions for reproducibility

**Files to Create:**
- `requirements.txt`

### 11. **Error Handling & Validation** (MEDIUM PRIORITY)
**Current State:** Basic error handling
**Needed:**
- Validate configuration parameters
- Better error messages
- Input validation

**Files to Update:**
- All modules - Add validation

### 12. **Testing Coverage** (MEDIUM PRIORITY)
**Current State:** Good test coverage for core components
**Needed:**
- Integration tests for full simulation
- Scenario tests
- Metrics calculation tests

**Files to Create:**
- `tests/test_simulation.py`
- `tests/test_scenarios.py`
- `tests/test_metrics.py`

---

## 🎯 MVP Priority Ranking

### Must Have (Critical Path):
1. **Patient Routing Logic** - Without this, simulation is unrealistic
2. **Warmup Period Filtering** - Statistics are inaccurate without this
3. **Streamlit App Working** - Main user interface
4. **Requirements.txt** - Dependency management
5. **Scenario Integration** - Core feature for MVP

### Should Have (Important):
6. **Multiple Replications** - Needed for statistical validity
7. **Enhanced Reporting** - Better output for users
8. **Configuration Files** - Easier customization

### Nice to Have:
9. **Documentation** - Can be added incrementally
10. **Error Handling** - Improves robustness
11. **Additional Tests** - Improves confidence

---

## 📋 Quick Start Implementation Order

1. **Create `requirements.txt`** (5 min)
2. **Fix warmup period filtering** (30 min)
3. **Implement patient routing logic** (2-3 hours)
4. **Test and fix Streamlit app** (1-2 hours)
5. **Add scenario selection to main.py** (1 hour)
6. **Create basic README** (30 min)
7. **Add multiple replications** (2-3 hours)
8. **Add export functionality** (1-2 hours)

**Estimated Total Time:** 8-12 hours for full MVP

---

## 🔍 Files That Need Attention

### High Priority:
- `main.py` - Needs routing logic, warmup filtering, scenario support
- `app.py` - Needs testing and potentially fixes
- `requirements.txt` - Needs to be created

### Medium Priority:
- `core/patient.py` - Verify all metric properties work
- `scenarios/*.py` - Test integration
- `simulation/metrics.py` - May need fixes for edge cases

### Low Priority:
- Documentation files
- Additional test files

