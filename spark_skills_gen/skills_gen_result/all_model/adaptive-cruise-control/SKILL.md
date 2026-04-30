---
id: adaptive-cruise-control-simulation
title: Adaptive Cruise Control (ACC) Simulation with PID Control
version: 1.0.0
tags: [control-systems, pid, simulation, autonomous-vehicles, python]
domain: vehicle-control
task_pattern: adaptive-cruise-control
success_rate: 1.0
---

# Adaptive Cruise Control Simulation Skill

## Overview

This skill covers implementing a full ACC simulation pipeline: PID controller, ACC logic with mode switching, parameter tuning, and a 150s simulation producing validated CSV output and a report. The system must satisfy hard performance targets (rise time, overshoot, steady-state errors, minimum distance) while respecting physical constraints (acceleration limits, time headway, emergency TTC).

---

## High-Level Workflow

### 1. Understand the Data and Configuration

Before writing any code, read both input files to understand the scenario structure.

```python
import yaml, pandas as pd

with open('vehicle_params.yaml') as f:
    config = yaml.safe_load(f)

# Key fields to extract
set_speed     = config['acc_settings']['set_speed']       # e.g. 30.0 m/s
time_headway  = config['acc_settings']['time_headway']    # e.g. 1.5 s
min_gap       = config['acc_settings']['min_gap']         # e.g. 10.0 m
ttc_threshold = config['acc_settings']['ttc_threshold']   # e.g. 3.0 s
max_accel     = config['vehicle']['max_acceleration']     # e.g. 3.0 m/s²
max_decel     = config['vehicle']['max_deceleration']     # e.g. -8.0 m/s²

df = pd.read_csv('sensor_data.csv')
# Columns: time, ego_speed, lead_speed, distance
# lead_speed and distance are NaN when no lead vehicle is present
print(df.head(10))
print(df[df['lead_speed'].notna()].head(5))  # find when lead vehicle appears
```

Key things to confirm:
- When does the lead vehicle appear? (typically around t=30s)
- Are there NaN rows for lead vehicle columns? (yes — use `pd.isna()` checks)
- Total rows should be 1501 (t=0 to t=150 at dt=0.1s)

---

### 2. Implement `pid_controller.py`

A clean, reusable PID with anti-windup and derivative kick protection.

```python
# pid_controller.py
class PIDController:
    def __init__(self, kp: float, ki: float, kd: float,
                 output_min: float = -float('inf'),
                 output_max: float = float('inf'),
                 integral_limit: float = 50.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.integral_limit = integral_limit
        self.reset()

    def reset(self):
        self._integral = 0.0
        self._prev_error = None  # None signals first call

    def compute(self, error: float, dt: float) -> float:
        if dt <= 0:
            return 0.0

        # Proportional
        p = self.kp * error

        # Integral with anti-windup clamping
        self._integral += error * dt
        self._integral = max(-self.integral_limit,
                             min(self.integral_limit, self._integral))
        i = self.ki * self._integral

        # Derivative — skip on first call to avoid derivative kick
        if self._prev_error is None:
            d = 0.0
        else:
            d = self.kd * (error - self._prev_error) / dt
        self._prev_error = error

        output = p + i + d
        return max(self.output_min, min(self.output_max, output))
```

---

### 3. Implement `acc_system.py`

The ACC has three modes:
- `cruise`: no lead vehicle detected — use speed PID to reach set speed
- `follow`: lead vehicle present — use distance PID to set a speed target, then speed PID to track it
- `emergency`: TTC < threshold — apply maximum deceleration immediately

The critical design decision for `follow` mode: **the distance PID output should directly adjust the desired speed** (not add a small correction on top of lead speed). This ensures the distance error converges to near zero.

```python
# acc_system.py
import math
from pid_controller import PIDController

class AdaptiveCruiseControl:
    def __init__(self, config: dict):
        acc = config['acc_settings']
        veh = config['vehicle']

        self.set_speed     = acc['set_speed']
        self.time_headway  = acc['time_headway']
        self.min_gap       = acc['min_gap']
        self.ttc_threshold = acc['ttc_threshold']
        self.max_accel     = veh['max_acceleration']
        self.max_decel     = veh['max_deceleration']

        # PIDs are created externally and injected, or created here with defaults
        # They will be replaced by tuned gains loaded from tuning_results.yaml
        self.pid_speed    = PIDController(kp=1.0, ki=0.1, kd=0.05,
                                          output_min=self.max_decel,
                                          output_max=self.max_accel)
        self.pid_distance = PIDController(kp=0.5, ki=0.05, kd=0.1)

    def set_pid_gains(self, speed_gains: dict, distance_gains: dict):
        """Replace PID gains after loading from tuning_results.yaml."""
        self.pid_speed = PIDController(
            kp=speed_gains['kp'], ki=speed_gains['ki'], kd=speed_gains['kd'],
            output_min=self.max_decel, output_max=self.max_accel)
        self.pid_distance = PIDController(
            kp=distance_gains['kp'], ki=distance_gains['ki'],
            kd=distance_gains['kd'])

    def desired_gap(self, ego_speed: float) -> float:
        """Time-headway gap model: d_desired = v * T_h + d_min"""
        return ego_speed * self.time_headway + self.min_gap

    def compute(self, ego_speed: float, lead_speed, distance, dt: float):
        """
        Returns: (acceleration_cmd, mode, distance_error)
        lead_speed and distance are None/NaN when no lead vehicle.
        """
        lead_present = (lead_speed is not None and
                        not (isinstance(lead_speed, float) and math.isnan(lead_speed)) and
                        distance is not None and
                        not (isinstance(distance, float) and math.isnan(distance)))

        if not lead_present:
            # CRUISE mode
            speed_error = self.set_speed - ego_speed
            accel = self.pid_speed.compute(speed_error, dt)
            accel = max(self.max_decel, min(self.max_accel, accel))
            return accel, 'cruise', None

        distance = float(distance)
        lead_speed = float(lead_speed)

        # TTC check for EMERGENCY mode
        relative_speed = ego_speed - lead_speed  # positive = closing
        ttc = distance / relative_speed if relative_speed > 0.01 else float('inf')

        if ttc < self.ttc_threshold:
            self.pid_speed.reset()
            self.pid_distance.reset()
            return self.max_decel, 'emergency', distance - self.desired_gap(ego_speed)

        # FOLLOW mode — distance PID sets desired speed
        d_desired = self.desired_gap(ego_speed)
        distance_error = distance - d_desired

        # Distance PID output is a speed correction around lead_speed
        speed_correction = self.pid_distance.compute(distance_error, dt)
        desired_speed = min(self.set_speed, lead_speed + speed_correction)
        desired_speed = max(0.0, desired_speed)

        speed_error = desired_speed - ego_speed
        accel = self.pid_speed.compute(speed_error, dt)
        accel = max(self.max_decel, min(self.max_accel, accel))

        return accel, 'follow', distance_error
```

---

### 4. Implement `simulation.py`

Key design choices:
- **Load PID gains from `tuning_results.yaml`** — do not embed tuning logic here
- **Propagate gap dynamically**: `gap += (lead_speed - ego_speed) * dt` rather than using raw noisy sensor distance directly. This gives smooth, physically consistent gap evolution.
- **Integrate ego speed** with aerodynamic drag for realism
- **Clamp acceleration** to vehicle limits at every step

```python
# simulation.py
import yaml
import pandas as pd
import numpy as np
import math
from acc_system import AdaptiveCruiseControl

def run_simulation():
    # Load config
    with open('vehicle_params.yaml') as f:
        config = yaml.safe_load(f)

    with open('tuning_results.yaml') as f:
        gains = yaml.safe_load(f)

    # Load sensor data
    sensor_df = pd.read_csv('sensor_data.csv')

    # Build ACC system and inject tuned gains
    acc = AdaptiveCruiseControl(config)
    acc.set_pid_gains(gains['pid_speed'], gains['pid_distance'])

    veh = config['vehicle']
    max_accel = veh['max_acceleration']
    max_decel = veh['max_deceleration']
    drag_coeff = veh.get('drag_coefficient', 0.0)  # optional aero drag

    dt = 0.1
    ego_speed = 0.0
    sim_gap = None  # will be initialized when lead vehicle first appears

    results = []

    for idx, row in sensor_df.iterrows():
        t = row['time']
        lead_speed_raw = row['lead_speed']
        distance_raw   = row['distance']

        lead_present = not (pd.isna(lead_speed_raw) or pd.isna(distance_raw))

        if lead_present:
            lead_speed = float(lead_speed_raw)
            if sim_gap is None:
                sim_gap = float(distance_raw)  # initialize from sensor on first appearance
            else:
                sim_gap += (lead_speed - ego_speed) * dt
            sim_gap = max(0.1, sim_gap)  # physical floor
            distance_for_acc = sim_gap
        else:
            lead_speed = None
            distance_for_acc = None
            sim_gap = None  # reset when lead disappears

        accel_cmd, mode, dist_error = acc.compute(
            ego_speed, lead_speed, distance_for_acc, dt)

        # Clamp acceleration
        accel_cmd = max(max_decel, min(max_accel, accel_cmd))

        # Compute TTC for output
        if lead_present and (ego_speed - lead_speed) > 0.01:
            ttc = sim_gap / (ego_speed - lead_speed)
        else:
            ttc = None

        results.append({
            'time': round(t, 1),
            'ego_speed': round(ego_speed, 4),
            'acceleration_cmd': round(accel_cmd, 4),
            'mode': mode,
            'distance_error': round(dist_error, 4) if dist_error is not None else '',
            'distance': round(sim_gap, 4) if sim_gap is not None else '',
            'ttc': round(ttc, 4) if ttc is not None else '',
        })

        # Integrate ego speed (Euler, with optional drag)
        drag = drag_coeff * ego_speed ** 2
        ego_speed += (accel_cmd - drag) * dt
        ego_speed = max(0.0, ego_speed)

    # Write CSV — exact column order required
    out_df = pd.DataFrame(results, columns=[
        'time', 'ego_speed', 'acceleration_cmd', 'mode',
        'distance_error', 'distance', 'ttc'])
    out_df.to_csv('simulation_results.csv', index=False)
    print(f"Wrote {len(out_df)} rows to simulation_results.csv")
    return out_df

if __name__ == '__main__':
    run_simulation()
```

---

### 5. Tune PID Gains and Save `tuning_results.yaml`

Use a two-stage grid search: coarse first, then fine around the best candidate. Evaluate against the hard performance targets.

```python
# tune_pid.py
import yaml
import numpy as np
import pandas as pd
import math
from acc_system import AdaptiveCruiseControl

def simulate_with_gains(config, kp_s, ki_s, kd_s, kp_d, ki_d, kd_d, sensor_df):
    acc = AdaptiveCruiseControl(config)
    acc.set_pid_gains(
        {'kp': kp_s, 'ki': ki_s, 'kd': kd_s},
        {'kp': kp_d, 'ki': ki_d, 'kd': kd_d})

    veh = config['vehicle']
    max_accel = veh['max_acceleration']
    max_decel = veh['max_deceleration']

    dt = 0.1
    ego_speed = 0.0
    sim_gap = None
    set_speed = config['acc_settings']['set_speed']

    speeds, modes, dist_errors, distances = [], [], [], []

    for _, row in sensor_df.iterrows():
        lead_speed_raw = row['lead_speed']
        distance_raw   = row['distance']
        lead_present = not (pd.isna(lead_speed_raw) or pd.isna(distance_raw))

        if lead_present:
            lead_speed = float(lead_speed_raw)
            if sim_gap is None:
                sim_gap = float(distance_raw)
            else:
                sim_gap += (lead_speed - ego_speed) * dt
            sim_gap = max(0.1, sim_gap)
        else:
            lead_speed = None
            sim_gap = None

        accel_cmd, mode, dist_error = acc.compute(
            ego_speed, lead_speed, sim_gap, dt)
        accel_cmd = max(max_decel, min(max_accel, accel_cmd))

        speeds.append(ego_speed)
        modes.append(mode)
        dist_errors.append(dist_error)
        distances.append(sim_gap)

        ego_speed += accel_cmd * dt
        ego_speed = max(0.0, ego_speed)

    speeds = np.array(speeds)
    times  = np.arange(len(speeds)) * dt

    # --- Metrics ---
    # Rise time: time to first reach 90% of set_speed
    rise_time = None
    for i, s in enumerate(speeds):
        if s >= 0.9 * set_speed:
            rise_time = times[i]
            break
    if rise_time is None:
        rise_time = 999.0

    # Overshoot (cruise phase only)
    cruise_mask = np.array(modes) == 'cruise'
    cruise_speeds = speeds[cruise_mask]
    overshoot = max(0.0, (cruise_speeds.max() - set_speed) / set_speed * 100) if len(cruise_speeds) else 999.0

    # Steady-state speed error (last 10s of cruise before lead appears)
    # Find first follow transition
    first_follow = next((i for i, m in enumerate(modes) if m == 'follow'), len(modes))
    pre_follow_cruise = [s for i, s in enumerate(speeds) if modes[i] == 'cruise' and i < first_follow]
    ss_speed_error = abs(np.mean(pre_follow_cruise[-50:]) - set_speed) if len(pre_follow_cruise) >= 50 else 999.0

    # Steady-state distance error (last 20s of follow phase)
    follow_dist_errors = [e for e, m in zip(dist_errors, modes) if m == 'follow' and e is not None]
    ss_dist_error = abs(np.mean(follow_dist_errors[-200:])) if len(follow_dist_errors) >= 200 else 999.0

    # Minimum distance
    follow_distances = [d for d, m in zip(distances, modes) if m == 'follow' and d is not None]
    min_dist = min(follow_distances) if follow_distances else 999.0

    return {
        'rise_time': rise_time,
        'overshoot': overshoot,
        'ss_speed_error': ss_speed_error,
        'ss_dist_error': ss_dist_error,
        'min_dist': min_dist,
    }

def score(metrics):
    """Lower is better. Returns inf if any hard constraint is violated."""
    if (metrics['rise_time'] > 10.0 or
        metrics['overshoot'] > 5.0 or
        metrics['ss_speed_error'] > 0.5 or
        metrics['ss_dist_error'] > 2.0 or
        metrics['min_dist'] < 5.0):
        return float('inf')
    # Weighted sum of normalized metrics
    return (metrics['rise_time'] / 10.0 +
            metrics['overshoot'] / 5.0 +
            metrics['ss_speed_error'] / 0.5 +
            metrics['ss_dist_error'] / 2.0)

def grid_search(config, sensor_df, speed_grid, dist_grid):
    best_score = float('inf')
    best_gains = None
    best_metrics = None

    for kp_s in speed_grid['kp']:
        for ki_s in speed_grid['ki']:
            for kd_s in speed_grid['kd']:
                for kp_d in dist_grid['kp']:
                    for ki_d in dist_grid['ki']:
                        for kd_d in dist_grid['kd']:
                            m = simulate_with_gains(
                                config, kp_s, ki_s, kd_s,
                                kp_d, ki_d, kd_d, sensor_df)
                            s = score(m)
                            if s < best_score:
                                best_score = s
                                best_gains = (kp_s, ki_s, kd_s, kp_d, ki_d, kd_d)
                                best_metrics = m

    return best_gains, best_metrics, best_score

if __name__ == '__main__':
    with open('vehicle_params.yaml') as f:
        config = yaml.safe_load(f)
    sensor_df = pd.read_csv('sensor_data.csv')

    # Stage 1: coarse search
    coarse_speed = {
        'kp': [0.5, 1.0, 2.0, 3.0, 5.0],
        'ki': [0.0, 0.1, 0.3],
        'kd': [0.0, 0.05, 0.1, 0.2],
    }
    coarse_dist = {
        'kp': [0.3, 0.5, 1.0, 1.5],
        'ki': [0.0, 0.05, 0.1],
        'kd': [0.0, 0.05, 0.1],
    }
    best_gains, best_metrics, best_score = grid_search(config, sensor_df, coarse_speed, coarse_dist)
    print(f"Coarse best: {best_gains}, score={best_score:.4f}, metrics={best_metrics}")

    if best_gains:
        kp_s, ki_s, kd_s, kp_d, ki_d, kd_d = best_gains
        # Stage 2: fine search around best
        fine_speed = {
            'kp': [max(0.1, kp_s * f) for f in [0.7, 0.85, 1.0, 1.15, 1.3]],
            'ki': [max(0.0, ki_s + d) for d in [-0.05, 0.0, 0.05]],
            'kd': [max(0.0, kd_s + d) for d in [-0.02, 0.0, 0.02, 0.05]],
        }
        fine_dist = {
            'kp': [max(0.1, kp_d * f) for f in [0.7, 0.85, 1.0, 1.15, 1.3]],
            'ki': [max(0.0, ki_d + d) for d in [-0.02, 0.0, 0.02]],
            'kd': [max(0.0, kd_d + d) for d in [-0.02, 0.0, 0.02]],
        }
        best_gains, best_metrics, best_score = grid_search(config, sensor_df, fine_speed, fine_dist)
        print(f"Fine best: {best_gains}, score={best_score:.4f}, metrics={best_metrics}")

    kp_s, ki_s, kd_s, kp_d, ki_d, kd_d = best_gains
    result = {
        'pid_speed':    {'kp': round(kp_s, 4), 'ki': round(ki_s, 4), 'kd': round(kd_s, 4)},
        'pid_distance': {'kp': round(kp_d, 4), 'ki': round(ki_d, 4), 'kd': round(kd_d, 4)},
    }
    with open('tuning_results.yaml', 'w') as f:
        yaml.dump(result, f, default_flow_style=False)
    print("Saved tuning_results.yaml:", result)
```

---

### 6. Verify Output Format

The CSV must have exactly 1501 rows and this exact column order:

```
time,ego_speed,acceleration_cmd,mode,distance_error,distance,ttc
```

- `distance_error`, `distance`, `ttc` are empty strings (not NaN) when in cruise mode
- `mode` values: exactly `cruise`, `follow`, or `emergency`
- `time` values: `0.0, 0.1, 0.2, ..., 150.0`

```python
# Quick validation
df = pd.read_csv('simulation_results.csv')
assert len(df) == 1501, f"Expected 1501 rows, got {len(df)}"
assert list(df.columns) == ['time', 'ego_speed', 'acceleration_cmd', 'mode',
                             'distance_error', 'distance', 'ttc']
assert set(df['mode'].unique()).issubset({'cruise', 'follow', 'emergency'})
assert abs(df['time'].iloc[-1] - 150.0) < 0.01
print("CSV format OK")
```

---

### 7. Write `acc_report.md`

The report must include these sections (checked by keyword validators):

```markdown
# Adaptive Cruise Control System Report

## System Design

### ACC Architecture
The ACC system operates in three modes:
- **Cruise mode**: No lead vehicle detected. Speed PID tracks set speed.
- **Follow mode**: Lead vehicle present. Distance PID sets desired speed; speed PID tracks it.
- **Emergency mode**: TTC < threshold. Maximum deceleration applied immediately.

### Safety Features
- Acceleration clamped to [{max_decel}, {max_accel}] m/s²
- Minimum gap enforced via time-headway model: d_desired = v × T_h + d_min
- Emergency braking triggered when TTC < {ttc_threshold}s

## PID Tuning Methodology

Two-stage grid search was used:
1. Coarse search over wide parameter ranges
2. Fine search around the best coarse candidate

### Final PID Gains
| Controller | Kp | Ki | Kd |
|---|---|---|---|
| Speed | {kp_s} | {ki_s} | {kd_s} |
| Distance | {kp_d} | {ki_d} | {kd_d} |

## Simulation Results

### Performance Metrics
| Metric | Result | Target | Status |
|---|---|---|---|
| Rise time | X.X s | < 10 s | PASS |
| Overshoot | X.XX % | < 5 % | PASS |
| SS speed error | X.XXX m/s | < 0.5 m/s | PASS |
| SS distance error | X.XXX m | < 2 m | PASS |
| Min distance | XX.X m | > 5 m | PASS |
```

---

## Common Pitfalls

### 1. Distance steady-state error stays large (>5m)

**Cause**: The follow mode uses `desired_speed = lead_speed + small_correction` where the correction is too weak to close the gap error.

**Fix**: Let the distance PID output be a speed correction that can be large enough to actually close the gap:
```python
speed_correction = self.pid_distance.compute(distance_error, dt)
desired_speed = min(self.set_speed, lead_speed + speed_correction)
```
Do NOT blend speed PID and distance PID additively — the distance PID should set the target, and the speed PID should track it.

### 2. Using raw sensor distance instead of propagated gap

**Cause**: `sensor_data.csv` distance values may be noisy or discontinuous. Using them directly causes jitter and incorrect gap dynamics.

**Fix**: Initialize `sim_gap` from sensor on first lead vehicle appearance, then propagate:
```python
sim_gap += (lead_speed - ego_speed) * dt
```

### 3. NaN handling in mode detection

**Cause**: `lead_speed` and `distance` columns are NaN (not Python `None`) when no lead vehicle. Direct `== None` checks fail.

**Fix**: Always use `pd.isna()` or `math.isnan()`:
```python
lead_present = not (pd.isna(lead_speed_raw) or pd.isna(distance_raw))
```

### 4. Derivative kick on PID first call

**Cause**: On the first `compute()` call, `prev_error` is undefined, causing a huge derivative spike.

**Fix**: Initialize `_prev_error = None` and skip derivative on first call:
```python
if self._prev_error is None:
    d = 0.0
else:
    d = self.kd * (error - self._prev_error) / dt
self._prev_error = error
```

### 5. Integral windup during cruise-to-follow transition

**Cause**: The speed PID accumulates large integral during cruise phase. When follow mode starts, the integral causes overshoot or oscillation.

**Fix**: Add integral clamping and consider resetting PIDs on mode transitions (especially entering emergency mode):
```python
self._integral = max(-self.integral_limit, min(self.integral_limit, self._integral))
```

### 6. CSV empty vs NaN for missing values

**Cause**: Validators expect empty string `''` for missing `distance_error`, `distance`, `ttc` in cruise mode — not `NaN` or `None`.

**Fix**: Explicitly write `''`:
```python
'distance_error': round(dist_error, 4) if dist_error is not None else '',
'distance': round(sim_gap, 4) if sim_gap is not None else '',
'ttc': round(ttc, 4) if ttc is not None else '',
```

### 7. Tuning search space too narrow

**Cause**: Starting with a narrow grid misses the optimal region, especially for `kp_speed` which often needs to be in [1.0, 5.0] for fast rise time.

**Fix**: Always start with a coarse search spanning at least one order of magnitude, then refine. Typical working ranges:
- Speed PID: `kp ∈ [0.5, 5.0]`, `ki ∈ [0.0, 0.5]`, `kd ∈ [0.0, 0.3]`
- Distance PID: `kp ∈ [0.3, 2.0]`, `ki ∈ [0.0, 0.2]`, `kd ∈ [0.0, 0.2]`

### 8. `tuning_results.yaml` gain constraints

The validator checks that gains are in valid ranges:
- `kp ∈ (0, 10)` — must be strictly positive
- `ki ∈ [0, 5)` — zero is allowed
- `kd ∈ [0, 5)` — zero is allowed

---

## Performance Targets Reference

| Metric | Target | How to Measure |
|---|---|---|
| Rise time | < 10 s | Time for ego_speed to first reach 90% of set_speed |
| Overshoot | < 5% | `(max_cruise_speed - set_speed) / set_speed * 100` |
| SS speed error | < 0.5 m/s | Mean abs error in last 5s before lead vehicle appears |
| SS distance error | < 2 m | Mean abs distance_error in last 20s of follow phase |
| Min distance | > 5 m | Minimum gap during entire follow phase |

---

## File Checklist

Before finalizing, verify all required outputs exist:

```bash
ls -la pid_controller.py acc_system.py simulation.py \
        tuning_results.yaml simulation_results.csv acc_report.md
```

- `pid_controller.py` — `PIDController` class with `reset()` and `compute(error, dt) -> float`
- `acc_system.py` — `AdaptiveCruiseControl` class with `compute(ego_speed, lead_speed, distance, dt) -> (float, str, float|None)`
- `simulation.py` — reads `tuning_results.yaml`, writes `simulation_results.csv`
- `tuning_results.yaml` — `pid_speed` and `pid_distance` sections with `kp`, `ki`, `kd`
- `simulation_results.csv` — exactly 1501 rows, 7 columns in correct order
- `acc_report.md` — sections on system design, PID tuning, simulation results
