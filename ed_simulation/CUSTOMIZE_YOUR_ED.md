# Customize Your ED Configuration

This guide explains how to configure the simulation for your specific Emergency Department.

## Quick Start

1. **In the Streamlit app sidebar**, select "Customize My ED" mode
2. **Set your ED parameters:**
   - ED Name (e.g., "Memorial Hospital ED")
   - Node Capacities (number of staff/beds at each station)
   - Arrival Rate (patients per hour)
   - Acuity Distribution (percentage of ESI 1-5 patients)
3. **Click "💾 Save Config"** to save your settings
4. **Click "📥 Download Config"** to save as YAML file for reuse
5. **Run your simulation** with your custom parameters

## Configuration Options

### Node Capacities
Set the number of resources at each processing station:
- **Triage**: Number of triage nurses
- **Registration**: Number of registration clerks
- **Bed Assignment**: Total number of ED beds
- **Fast Track**: Number of fast track beds/chairs
- **Provider Assessment**: Number of providers (physicians/NPs)
- **Diagnostics**: Combined lab/imaging capacity
- **Treatment**: Treatment capacity (usually same as beds)
- **Disposition**: Providers doing discharge

### Arrival Rate
Average number of patients arriving per hour (Poisson process).

### Acuity Distribution
Percentage breakdown of ESI levels:
- **ESI 1**: Immediate, life-threatening (typically 1-2%)
- **ESI 2**: Emergent (typically 8-12%)
- **ESI 3**: Urgent (typically 30-40%)
- **ESI 4**: Less urgent (typically 30-40%)
- **ESI 5**: Non-urgent (typically 15-25%)

**Note:** Percentages should total 100%.

## Saving and Loading Configurations

### Save Configuration
1. Set all your parameters
2. Click "💾 Save Config"
3. Your settings are saved in the session

### Download Configuration
1. Click "📥 Download Config"
2. A YAML file will be downloaded
3. Share this file with your team or use it later

### Load Configuration
1. Click "📁 Load Config" file uploader
2. Select your saved YAML file
3. Your settings will be loaded automatically

## Example YAML Configuration

```yaml
ed_name: "Memorial Hospital ED"
node_capacities:
  TRIAGE: 3
  REGISTRATION: 2
  BED_ASSIGNMENT: 25
  FAST_TRACK: 6
  PROVIDER_ASSESSMENT: 6
  DIAGNOSTICS: 8
  TREATMENT: 25
  DISPOSITION: 4
arrival_rate: 4.5
acuity_distribution:
  1: 0.02
  2: 0.12
  3: 0.38
  4: 0.32
  5: 0.16
```

## Tips for Accurate Configuration

1. **Use historical data** to set arrival rates and acuity distribution
2. **Count actual resources** in your ED for node capacities
3. **Start with defaults** and adjust based on your ED's characteristics
4. **Save multiple configurations** for different scenarios (day shift, night shift, etc.)
5. **Validate results** by comparing simulated metrics to actual ED performance

## Next Steps

Once configured:
- Run baseline simulations to validate against your actual ED
- Test scenarios (boarding, surge, staffing changes)
- Use staffing optimization to find best configurations
- Export results for analysis

