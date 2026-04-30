---
id: glm-lake-calibration
title: General Lake Model (GLM) Calibration and Simulation
version: 1.0.0
tags: [glm, lake-modeling, hydrology, calibration, netcdf, python]
---

# GLM Lake Simulation and Calibration

## Module 1: Environment Setup and Inspection

Before running anything, audit the available inputs and verify GLM is accessible.

```bash
# Verify GLM binary
which glm || find / -name "glm" -type f 2>/dev/null | head -5

# Inspect forcing data structure
head -5 /root/bcs/*.csv

# Inspect observation file
head -20 /root/field_temp_oxy.csv

# Review full config
cat /root/glm3.nml
```

Key things to confirm:
- Forcing files (meteorological, inflow/outflow) match the filenames referenced in `glm3.nml`
- Observation CSV has columns for date/depth/temperature
- `glm3.nml` `start` and `stop` dates match the required simulation window
- Output path in `glm3.nml` points to the expected directory (e.g., `/root/output/`)

```bash
mkdir -p /root/output
```

---

## Module 2: Running GLM and Evaluating RMSE

Run GLM from the directory containing `glm3.nml`, then immediately evaluate against observations.

```bash
cd /root && glm 2>&1 | tail -30
```

If the process hangs or produces no output, check whether the output file was created:

```bash
ls -lh /root/output/output.nc
```

### RMSE Evaluation Script

```python
import netCDF4 as nc
import pandas as pd
import numpy as np

# Load simulation output
ds = nc.Dataset('/root/output/output.nc')
# Typical GLM output variables: 'temp', 'z', 'time' (check ds.variables.keys())
print(list(ds.variables.keys()))

# Load observations
obs = pd.read_csv('/root/field_temp_oxy.csv', parse_dates=True)
# Align simulated temps to observed depths and timestamps
# Interpolate simulated profile to observed depth at each timestep

# Compute RMSE
rmse = np.sqrt(np.mean((sim_temps - obs_temps) ** 2))
print(f"RMSE: {rmse:.3f} °C")
```

Target: RMSE < 2.0°C. If already met, stop — do not over-tune.

---

## Module 3: Parameter Tuning Strategy

If RMSE ≥ 2.0°C, adjust these parameters in `glm3.nml` in order of impact:

| Parameter | Effect | Typical Adjustment |
|---|---|---|
| `Kw` | Light extinction (affects thermal stratification depth) | Increase to deepen thermocline |
| `sw_factor` | Shortwave radiation scaling | Reduce (e.g. 0.9) if lake runs too warm |
| `coef_wind_stir` | Wind-driven mixing intensity | Increase to reduce stratification bias |
| `min_layer_thick` / `max_layer_thick` | Vertical resolution | Finer layers improve profile accuracy |

Tune iteratively — change one or two parameters, re-run GLM, re-evaluate RMSE:

```python
import subprocess, shutil

def update_nml(param, value, nml_path='/root/glm3.nml'):
    with open(nml_path) as f:
        content = f.read()
    import re
    content = re.sub(rf'({param}\s*=\s*)[\d.]+', rf'\g<1>{value}', content)
    with open(nml_path, 'w') as f:
        f.write(content)

def run_glm():
    result = subprocess.run(['glm'], cwd='/root', capture_output=True, text=True, timeout=300)
    return result.returncode == 0

# Example tuning loop
for kw in [0.30, 0.35, 0.40]:
    update_nml('Kw', kw)
    if run_glm():
        rmse = compute_rmse()  # your evaluation function
        print(f"Kw={kw} → RMSE={rmse:.3f}")
        if rmse < 2.0:
            break
```

---

## Common Pitfalls

- **Wrong working directory**: GLM must be run from the directory containing `glm3.nml`. Running from elsewhere causes it to silently fail or not find forcing files.

- **Output directory missing**: GLM will fail if the output directory doesn't exist. Always `mkdir -p` the output path before running.

- **Date range mismatch**: Ensure `start` and `stop` in `glm3.nml` exactly match the required simulation window. Off-by-one on dates can cause test failures even if RMSE looks fine.

- **Hanging process**: GLM can appear to hang on first run — check if `output.nc` was created before assuming failure. Use a timeout in subprocess calls.

- **Over-tuning**: Once RMSE < 2.0°C, stop. Aggressive tuning can overfit and destabilize the model (negative layer thicknesses, numerical blow-up).

- **Depth interpolation errors**: GLM outputs a variable-layer grid. When comparing to point observations, interpolate the simulated profile to the observed depth at each timestep — don't just take the nearest layer index.

- **Ignoring inflow/outflow files**: If `glm3.nml` references inflow or outflow CSV files, they must exist and be correctly formatted. Missing files cause silent failures or crashes.
