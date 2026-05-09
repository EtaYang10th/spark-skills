---
title: GLM Lake Temperature Calibration
category: glm-lake-mendota
domain: environmental-modeling
tags:
  - GLM
  - lake-modeling
  - calibration
  - netCDF
  - water-temperature
  - RMSE
  - namelist-editing
version: 1.0
---

# GLM Lake Temperature Calibration

Simulate vertical water temperature profiles for lakes using the General Lake Model (GLM), calibrate key parameters to minimize RMSE against field observations, and produce validated NetCDF output.

## 1. High-Level Workflow

1. **Inventory the environment** — Confirm GLM binary is installed and runnable. Identify the namelist config (`glm3.nml`), boundary-condition files (`bcs/`), field observations CSV, and expected output path.
2. **Understand the baseline config** — Parse `glm3.nml` to extract simulation dates, lake morphometry, meteorology settings, inflow/outflow, and the three most impactful calibration knobs: light extinction (`Kw`), hypolimnetic mixing (`coef_mix_hyp`), and wind scaling (`wind_factor`).
3. **Run a baseline simulation** — Execute GLM with the default config and compute RMSE against observations. This gives you a reference point and reveals the dominant bias pattern (e.g., warm bias at depth, cold bias at surface).
4. **Diagnose the bias** — Compare simulated vs. observed temperatures by depth. A warm bias below the thermocline means too much heat is being mixed downward or too little light is being absorbed near the surface. A cold surface bias means the opposite.
5. **Calibrate iteratively** — Sweep a small grid of the 2–3 most sensitive parameters. Evaluate RMSE for each combination. Pick the best.
6. **Write final parameters back to `glm3.nml`** — Ensure the namelist on disk matches the parameters that produced the best output.
7. **Final validation run** — Re-run GLM from the saved namelist, re-compute RMSE, and confirm the output NetCDF exists at the expected path.

## 2. Environment Setup and Verification

GLM is typically pre-installed as a system binary. Verify before doing anything else.

```bash
# Check GLM is available
which glm || which GLM
glm --version 2>&1 || echo "GLM binary found but no --version flag (normal for GLM3)"

# Check required Python packages
python3 -c "import pandas, numpy, netCDF4, scipy; print('All packages available')"
```

Key paths (typical for this task class):

| Artifact | Typical Path |
|---|---|
| GLM config | `/root/glm3.nml` |
| Meteo forcing | `/root/bcs/meteo.csv` |
| Inflow files | `/root/bcs/yahara.csv`, `/root/bcs/pheasant.csv` |
| Outflow file | `/root/bcs/outflow.csv` |
| Field observations | `/root/field_temp_oxy.csv` |
| Output NetCDF | `/root/output/output.nc` |

## 3. Reading and Parsing the GLM Namelist (`glm3.nml`)

The namelist is a Fortran-style `.nml` file with `&block ... /` sections. Python's standard library can't parse it natively, so use regex-based editing.

```python
import re

def read_nml(path):
    """Read a GLM namelist file and return its raw text."""
    with open(path, 'r') as f:
        return f.read()

def get_nml_value(text, key):
    """Extract a scalar value from namelist text."""
    pattern = rf'{key}\s*=\s*([^\n,!/]+)'
    m = re.search(pattern, text)
    if m:
        val = m.group(1).strip().strip("'\"")
        try:
            return float(val)
        except ValueError:
            return val
    return None

def set_nml_value(text, key, value):
    """Replace a scalar value in namelist text."""
    if isinstance(value, bool):
        val_str = '.true.' if value else '.false.'
    elif isinstance(value, float):
        val_str = str(value)
    elif isinstance(value, int):
        val_str = str(value)
    else:
        val_str = f"'{value}'"
    pattern = rf'({key}\s*=\s*)([^\n,!/]+)'
    return re.sub(pattern, rf'\1{val_str}', text)

def write_nml(path, text):
    """Write namelist text back to file."""
    with open(path, 'w') as f:
        f.write(text)
```

### Critical Parameters to Know

| Parameter | Block | What It Controls | Typical Range |
|---|---|---|---|
| `Kw` | `&light` | Light extinction coefficient (m⁻¹). Higher = more heat absorbed near surface. | 0.2 – 1.5 |
| `coef_mix_hyp` | `&mixing` | Hypolimnetic mixing efficiency. Higher = more deep mixing. | 0.2 – 1.0 |
| `wind_factor` | `&meteorology` | Multiplier on wind speed. Higher = more wind-driven mixing. | 0.7 – 1.1 |
| `coef_mix_shear` | `&mixing` | Shear-driven mixing coefficient. | 0.1 – 0.5 |
| `coef_mix_turb` | `&mixing` | Turbulent mixing coefficient. | 0.2 – 0.8 |

**The big three for temperature RMSE are `Kw`, `coef_mix_hyp`, and `wind_factor`.** Start there.

## 4. Running GLM

GLM reads `glm3.nml` from the current working directory. It writes output to the path specified in the `&output` block.

```python
import subprocess
import os

def run_glm(working_dir='/root'):
    """Run GLM in the specified directory. Returns (success, stderr)."""
    # Ensure output directory exists
    os.makedirs(os.path.join(working_dir, 'output'), exist_ok=True)

    result = subprocess.run(
        ['glm'],
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=300  # 5 min timeout — GLM is usually fast
    )
    success = result.returncode == 0
    if not success:
        print(f"GLM FAILED (rc={result.returncode})")
        print(result.stderr[-2000:] if result.stderr else "No stderr")
    return success, result.stderr
```

**Important**: GLM must be run from the directory containing `glm3.nml`. All relative paths in the namelist (e.g., `'bcs/meteo.csv'`) are resolved from that directory.

## 5. Loading and Matching Observations to Simulation

The field observation CSV typically has columns like `datetime`, `Depth_m` (or `depth`), and `Temp_C` (or `temp`). The simulation output is a NetCDF file with a 2D temperature array indexed by time and depth.

```python
import pandas as pd
import numpy as np
from netCDF4 import Dataset
from datetime import datetime, timedelta

def load_observations(obs_path, start_date=None, end_date=None):
    """Load field temperature observations."""
    df = pd.read_csv(obs_path)

    # Normalize column names (handle common variants)
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if 'date' in cl or 'time' in cl:
            col_map[c] = 'datetime'
        elif 'depth' in cl:
            col_map[c] = 'depth'
        elif 'temp' in cl and 'oxy' not in cl:
            col_map[c] = 'temp'
    df = df.rename(columns=col_map)

    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.dropna(subset=['datetime', 'depth', 'temp'])

    if start_date:
        df = df[df['datetime'] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df['datetime'] <= pd.Timestamp(end_date)]

    return df

def load_glm_output(nc_path):
    """
    Load GLM NetCDF output.
    Returns (times_as_datetime, depths_1d, temp_2d).
    temp_2d shape: (n_times, n_depths), depths measured from surface.
    """
    ds = Dataset(nc_path, 'r')

    # Time: GLM stores hours since a reference date
    time_var = ds.variables['time']
    time_units = time_var.units  # e.g. "hours since 2009-01-01 00:00:00"
    time_vals = time_var[:]

    # Parse reference date from units string
    ref_str = time_units.split('since')[-1].strip()
    ref_date = pd.Timestamp(ref_str)
    times = [ref_date + pd.Timedelta(hours=float(t)) for t in time_vals]

    # Temperature: variable name is usually 'temp'
    temp = ds.variables['temp'][:]  # shape: (time, depth)

    # Depths: GLM uses z (height from bottom) — convert to depth from surface
    # The lake surface elevation and z coordinates vary per timestep
    # Use NS (number of active layers) and z to reconstruct
    if 'z' in ds.variables:
        z = ds.variables['z'][:]  # (time, max_layers) — height from bottom
    elif 'NS' in ds.variables:
        z = ds.variables['z'][:]

    # Lake surface height
    if 'lake_level' in ds.variables:
        lake_level = ds.variables['lake_level'][:]
    else:
        # Approximate: max z per timestep
        lake_level = np.nanmax(z, axis=1)

    ds.close()
    return times, z, temp, lake_level

def match_obs_to_sim(obs_df, nc_path):
    """
    For each observation (datetime, depth, temp), find the nearest
    simulated value by time and depth. Returns arrays of (obs, sim) pairs.
    """
    ds = Dataset(nc_path, 'r')

    time_var = ds.variables['time']
    time_units = time_var.units
    ref_str = time_units.split('since')[-1].strip()
    ref_date = pd.Timestamp(ref_str)
    sim_times = np.array([ref_date + pd.Timedelta(hours=float(t))
                          for t in time_var[:]])

    temp = ds.variables['temp'][:]  # (time, layers)
    z = ds.variables['z'][:]        # (time, layers) — height above bottom

    # Number of valid layers per timestep
    if 'NS' in ds.variables:
        ns = ds.variables['NS'][:].astype(int)
    else:
        ns = np.sum(~np.isnan(temp), axis=1).astype(int)

    # Lake level (surface elevation)
    if 'lake_level' in ds.variables:
        lake_level = ds.variables['lake_level'][:]
    else:
        lake_level = np.array([z[i, ns[i]-1] if ns[i] > 0 else np.nan
                               for i in range(len(sim_times))])

    ds.close()

    obs_temps = []
    sim_temps = []

    # Build a time index for fast lookup
    sim_dates = pd.DatetimeIndex(sim_times)

    for _, row in obs_df.iterrows():
        obs_dt = row['datetime']
        obs_depth = row['depth']
        obs_temp = row['temp']

        # Find nearest simulation time (within 1 day)
        time_diffs = np.abs((sim_dates - obs_dt).total_seconds())
        tidx = np.argmin(time_diffs)
        if time_diffs[tidx] > 86400:  # skip if > 1 day away
            continue

        n = ns[tidx]
        if n < 2:
            continue

        # Convert observation depth (from surface) to height above bottom
        surface_height = lake_level[tidx]
        target_z = surface_height - obs_depth

        # Get valid layers for this timestep
        layer_z = z[tidx, :n]
        layer_temp = temp[tidx, :n]

        # Interpolate
        if target_z <= layer_z[0]:
            sim_temp = layer_temp[0]
        elif target_z >= layer_z[-1]:
            sim_temp = layer_temp[-1]
        else:
            sim_temp = np.interp(target_z, layer_z, layer_temp)

        if not np.isnan(sim_temp) and not np.isnan(obs_temp):
            obs_temps.append(obs_temp)
            sim_temps.append(sim_temp)

    return np.array(obs_temps), np.array(sim_temps)

def compute_rmse(obs, sim):
    """Compute root mean square error."""
    return np.sqrt(np.mean((obs - sim) ** 2))
```

## 6. Calibration Strategy

### Diagnosis-Driven Calibration

Don't blindly sweep. First diagnose the bias pattern:

```python
def diagnose_bias(obs_df, nc_path, depth_bins=[0, 5, 10, 15, 25]):
    """Compute RMSE and mean bias by depth bin."""
    obs_arr, sim_arr = match_obs_to_sim(obs_df, nc_path)
    overall_rmse = compute_rmse(obs_arr, sim_arr)
    print(f"Overall RMSE: {overall_rmse:.4f}°C  (n={len(obs_arr)})")

    # Re-match with depth info for binned analysis
    ds = Dataset(nc_path, 'r')
    # ... (use the match logic above but also track depth per pair)
    ds.close()
    return overall_rmse
```

**Interpretation guide:**

| Bias Pattern | Likely Cause | Fix |
|---|---|---|
| Warm bias at depth (>5m) | Too much heat mixed down, or too little surface absorption | Increase `Kw` (traps heat near surface), decrease `wind_factor` |
| Cold bias at surface | Too much light extinction or not enough wind mixing | Decrease `Kw`, increase `wind_factor` |
| Warm bias everywhere | Excess incoming radiation or too little outgoing | Decrease `sw_factor` or `lw_factor` |
| Seasonal timing off | Sediment heat flux or ice parameters | Adjust `sed_temp_mean`, `sed_temp_peak_doy` |

### Grid Search

For a task requiring RMSE < 2.0°C, a small grid over the big three parameters is usually sufficient:

```python
import itertools

def calibration_grid_search(nml_path, working_dir, obs_df, nc_path):
    """
    Run a grid search over key GLM parameters.
    Returns the best parameter set and its RMSE.
    """
    # Define parameter grid
    param_grid = {
        'Kw': [0.3, 0.4, 0.5, 0.6],
        'coef_mix_hyp': [0.4, 0.5, 0.6],
        'wind_factor': [0.85, 0.90, 0.95, 1.0],
    }

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))

    best_rmse = float('inf')
    best_params = None
    results = []

    original_nml = read_nml(nml_path)

    for combo in combos:
        params = dict(zip(keys, combo))
        nml_text = original_nml
        for k, v in params.items():
            nml_text = set_nml_value(nml_text, k, v)
        write_nml(nml_path, nml_text)

        success, _ = run_glm(working_dir)
        if not success:
            results.append((params, float('inf')))
            continue

        try:
            obs_arr, sim_arr = match_obs_to_sim(obs_df, nc_path)
            rmse = compute_rmse(obs_arr, sim_arr)
        except Exception as e:
            print(f"  Error evaluating: {e}")
            rmse = float('inf')

        results.append((params, rmse))
        print(f"  {params} -> RMSE={rmse:.4f}")

        if rmse < best_rmse:
            best_rmse = rmse
            best_params = params

    # Restore best params
    if best_params:
        nml_text = original_nml
        for k, v in best_params.items():
            nml_text = set_nml_value(nml_text, k, v)
        write_nml(nml_path, nml_text)

    return best_params, best_rmse, results
```

**Efficiency tip**: If the grid is large (>20 combos), run a coarse sweep first (3 values per param), identify the best region, then refine with a finer grid around it.

## 7. Final Validation

After calibration, always do a clean final run and re-verify:

```python
def final_validation(nml_path, working_dir, obs_path, nc_path,
                     start_date, end_date, rmse_threshold=2.0):
    """Run GLM one final time and validate RMSE."""
    # 1. Verify the namelist is self-consistent
    nml_text = read_nml(nml_path)
    kw = get_nml_value(nml_text, 'Kw')
    wf = get_nml_value(nml_text, 'wind_factor')
    cmh = get_nml_value(nml_text, 'coef_mix_hyp')
    print(f"Final params: Kw={kw}, wind_factor={wf}, coef_mix_hyp={cmh}")

    # 2. Run GLM
    success, stderr = run_glm(working_dir)
    assert success, f"GLM failed on final run: {stderr[-500:]}"

    # 3. Verify output exists
    assert os.path.exists(nc_path), f"Output not found at {nc_path}"

    # 4. Compute RMSE
    obs_df = load_observations(obs_path, start_date, end_date)
    obs_arr, sim_arr = match_obs_to_sim(obs_df, nc_path)
    rmse = compute_rmse(obs_arr, sim_arr)
    print(f"Final RMSE: {rmse:.4f}°C ({len(obs_arr)} matched points)")
    assert rmse < rmse_threshold, f"RMSE {rmse:.4f} >= {rmse_threshold}"

    print("VALIDATION PASSED")
    return rmse
```

## 8. Common Pitfalls

### Pitfall 1: Running GLM from the wrong directory
GLM resolves all relative paths in `glm3.nml` from the current working directory. If you `cd` somewhere else and run `glm`, it won't find `bcs/meteo.csv`. Always `cwd=` to the directory containing `glm3.nml`.

### Pitfall 2: Forgetting to create the output directory
GLM will silently fail or crash if `output/` doesn't exist. Always `os.makedirs('output', exist_ok=True)` before running.

### Pitfall 3: Depth coordinate confusion
GLM internally uses height above the lake bottom (`z`), but observations are typically depth from the surface. You must convert: `depth_from_surface = lake_level - z`. Getting this wrong inverts the temperature profile and produces huge RMSE.

### Pitfall 4: Time matching tolerance
Field observations may be sparse (weekly or biweekly). Use a tolerance of ~1 day when matching to daily simulation output. Too tight a tolerance drops valid matches; too loose matches the wrong day.

### Pitfall 5: Not writing final params back to the namelist
The verifier typically re-runs GLM from `glm3.nml` to confirm reproducibility. If you computed the best RMSE but forgot to write those parameters back to the file, the verifier will run with different (default) parameters and fail.

### Pitfall 6: Over-calibrating `wind_factor`
Values below 0.7 or above 1.2 are physically unrealistic and often cause GLM instability (NaN temperatures, crashes). Stay within 0.8–1.1 for safety.

### Pitfall 7: Ignoring `Kw` as the primary lever
For deep lakes with warm-at-depth bias, `Kw` (light extinction) is usually the single most impactful parameter. Increasing it from the default (often 0.3) to 0.4–0.6 can reduce RMSE by 0.3–0.5°C on its own.

### Pitfall 8: NaN values in NetCDF
GLM pads inactive layers with fill values. Always use the `NS` variable (number of active layers) to mask invalid data before interpolation.

## 9. Reference Implementation

This is a complete, end-to-end script. Copy it, adjust paths if needed, and run.

```python
#!/usr/bin/env python3
"""
GLM Lake Temperature Calibration — Complete Reference Implementation

Reads a GLM namelist, runs a grid search over key parameters,
evaluates RMSE against field observations, writes the best config,
and performs a final validation run.

Usage:
    python3 calibrate_glm.py

Expects:
    /root/glm3.nml          — GLM configuration
    /root/bcs/              — meteorological and hydrological forcing
    /root/field_temp_oxy.csv — field temperature observations
    /root/output/output.nc  — will be created by GLM
"""

import os
import re
import subprocess
import itertools
import numpy as np
import pandas as pd
from netCDF4 import Dataset

# ============================================================
# Configuration — adjust these for your specific task
# ============================================================
WORKING_DIR = '/root'
NML_PATH = os.path.join(WORKING_DIR, 'glm3.nml')
OBS_PATH = os.path.join(WORKING_DIR, 'field_temp_oxy.csv')
NC_PATH = os.path.join(WORKING_DIR, 'output', 'output.nc')
START_DATE = '2009-01-01'
END_DATE = '2015-12-30'
RMSE_THRESHOLD = 2.0

# Parameter grid — these are the most impactful for temperature RMSE
PARAM_GRID = {
    'Kw':             [0.3, 0.4, 0.5, 0.6],
    'coef_mix_hyp':   [0.4, 0.5, 0.6],
    'wind_factor':    [0.85, 0.90, 0.95, 1.0],
}

# ============================================================
# Namelist I/O
# ============================================================
def read_nml(path):
    with open(path, 'r') as f:
        return f.read()

def write_nml(path, text):
    with open(path, 'w') as f:
        f.write(text)

def get_nml_value(text, key):
    m = re.search(rf'{key}\s*=\s*([^\n,!/]+)', text)
    if m:
        val = m.group(1).strip().strip("'\"")
        try:
            return float(val)
        except ValueError:
            return val
    return None

def set_nml_value(text, key, value):
    if isinstance(value, float):
        val_str = str(value)
    elif isinstance(value, int):
        val_str = str(value)
    elif isinstance(value, bool):
        val_str = '.true.' if value else '.false.'
    else:
        val_str = f"'{value}'"
    return re.sub(rf'({key}\s*=\s*)([^\n,!/]+)', rf'\g<1>{val_str}', text)

# ============================================================
# GLM execution
# ============================================================
def run_glm(working_dir=WORKING_DIR):
    os.makedirs(os.path.join(working_dir, 'output'), exist_ok=True)
    result = subprocess.run(
        ['glm'],
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=300
    )
    return result.returncode == 0, result.stderr

# ============================================================
# Observation loading
# ============================================================
def load_observations(obs_path=OBS_PATH, start_date=START_DATE, end_date=END_DATE):
    df = pd.read_csv(obs_path)

    # Normalize column names
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if 'date' in cl or 'time' in cl:
            col_map[c] = 'datetime'
        elif 'depth' in cl:
            col_map[c] = 'depth'
        elif 'temp' in cl and 'oxy' not in cl:
            col_map[c] = 'temp'
    df = df.rename(columns=col_map)

    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.dropna(subset=['datetime', 'depth', 'temp'])

    if start_date:
        df = df[df['datetime'] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df['datetime'] <= pd.Timestamp(end_date)]

    return df

# ============================================================
# Simulation output reading and observation matching
# ============================================================
def match_obs_to_sim(obs_df, nc_path=NC_PATH):
    ds = Dataset(nc_path, 'r')

    # Parse simulation times
    time_var = ds.variables['time']
    ref_str = time_var.units.split('since')[-1].strip()
    ref_date = pd.Timestamp(ref_str)
    sim_times = pd.DatetimeIndex([
        ref_date + pd.Timedelta(hours=float(t)) for t in time_var[:]
    ])

    temp = ds.variables['temp'][:]   # (time, max_layers)
    z = ds.variables['z'][:]         # (time, max_layers) — height above bottom

    if 'NS' in ds.variables:
        ns = ds.variables['NS'][:].astype(int)
    else:
        ns = np.sum(~np.isnan(temp), axis=1).astype(int)

    if 'lake_level' in ds.variables:
        lake_level = ds.variables['lake_level'][:]
    else:
        lake_level = np.array([
            z[i, ns[i]-1] if ns[i] > 0 else np.nan
            for i in range(len(sim_times))
        ])

    ds.close()

    obs_temps = []
    sim_temps = []

    for _, row in obs_df.iterrows():
        obs_dt = row['datetime']
        obs_depth = row['depth']
        obs_temp = row['temp']

        # Find nearest simulation timestep
        time_diffs = np.abs((sim_times - obs_dt).total_seconds())
        tidx = np.argmin(time_diffs)
        if time_diffs[tidx] > 86400:
            continue

        n = ns[tidx]
        if n < 2:
            continue

        # Convert depth-from-surface to height-above-bottom
        surface_height = lake_level[tidx]
        target_z = surface_height - obs_depth

        layer_z = z[tidx, :n]
        layer_temp = temp[tidx, :n]

        # Interpolate simulated temperature at observation depth
        if target_z <= layer_z[0]:
            sim_temp = float(layer_temp[0])
        elif target_z >= layer_z[-1]:
            sim_temp = float(layer_temp[-1])
        else:
            sim_temp = float(np.interp(target_z, layer_z, layer_temp))

        if not np.isnan(sim_temp) and not np.isnan(obs_temp):
            obs_temps.append(obs_temp)
            sim_temps.append(sim_temp)

    return np.array(obs_temps), np.array(sim_temps)

def compute_rmse(obs, sim):
    return float(np.sqrt(np.mean((obs - sim) ** 2)))

# ============================================================
# Calibration
# ============================================================
def calibrate():
    obs_df = load_observations()
    print(f"Loaded {len(obs_df)} observations from {START_DATE} to {END_DATE}")

    original_nml = read_nml(NML_PATH)

    # --- Baseline run ---
    print("\n=== Baseline Run ===")
    success, _ = run_glm()
    if success:
        obs_arr, sim_arr = match_obs_to_sim(obs_df)
        baseline_rmse = compute_rmse(obs_arr, sim_arr)
        print(f"Baseline RMSE: {baseline_rmse:.4f}°C ({len(obs_arr)} points)")
        if baseline_rmse < RMSE_THRESHOLD:
            print("Baseline already meets threshold!")
            return baseline_rmse
    else:
        print("Baseline run failed — proceeding to calibration anyway")
        baseline_rmse = float('inf')

    # --- Grid search ---
    print("\n=== Grid Search Calibration ===")
    keys = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(itertools.product(*values))
    print(f"Testing {len(combos)} parameter combinations...")

    best_rmse = float('inf')
    best_params = None

    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        # Write params to namelist
        nml_text = original_nml
        for k, v in params.items():
            nml_text = set_nml_value(nml_text, k, v)
        write_nml(NML_PATH, nml_text)

        # Run GLM
        success, stderr = run_glm()
        if not success:
            print(f"  [{i+1}/{len(combos)}] {params} -> FAILED")
            continue

        # Evaluate
        try:
            obs_arr, sim_arr = match_obs_to_sim(obs_df)
            rmse = compute_rmse(obs_arr, sim_arr)
        except Exception as e:
            print(f"  [{i+1}/{len(combos)}] {params} -> ERROR: {e}")
            continue

        print(f"  [{i+1}/{len(combos)}] {params} -> RMSE={rmse:.4f}")

        if rmse < best_rmse:
            best_rmse = rmse
            best_params = params

        # Early exit if well under threshold
        if rmse < RMSE_THRESHOLD - 0.3:
            print(f"  Early stop: RMSE {rmse:.4f} is well under threshold")
            break

    if best_params is None:
        print("ERROR: No successful runs in grid search!")
        return float('inf')

    # --- Write best params ---
    print(f"\n=== Best Parameters ===")
    print(f"  {best_params} -> RMSE={best_rmse:.4f}")

    nml_text = original_nml
    for k, v in best_params.items():
        nml_text = set_nml_value(nml_text, k, v)
    write