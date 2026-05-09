---
title: Adaptive Cruise Control (ACC) Simulation with PID Control
category: adaptive-cruise-control
domain: control-systems
tags:
  - pid-control
  - vehicle-simulation
  - adaptive-cruise-control
  - time-series
  - control-tuning
dependencies:
  - numpy==1.26.4
  - pandas==2.2.2
  - pyyaml==6.0.1
  - matplotlib==3.8.4
---

# Adaptive Cruise Control (ACC) Simulation with PID Control

## Overview

This skill covers implementing a complete Adaptive Cruise Control system: a PID controller with anti-windup, a multi-mode ACC state machine (cruise / follow / emergency), a kinematic vehicle simulation driven by real sensor data, PID gain tuning, and report generation. The pattern applies to any task that asks you to maintain a set speed when the road is clear and a safe following distance when a lead vehicle is present.

---

## 1. High-Level Workflow

1. **Inspect inputs** — Read `vehicle_params.yaml` for physical limits and ACC settings. Read `sensor_data.csv` to understand lead-vehicle behavior (when it appears/disappears, speed profile, emergency braking events). This step is critical: the data dictates which modes will be exercised and where edge cases live.

2. **Implement `PIDController`** — A reusable class with proportional, integral, derivative terms, output clamping, and **back-calculation anti-windup**. Anti-windup is the single most important detail; without it the integral term saturates during the long ramp-up and causes massive overshoot.

3. **Implement `AdaptiveCruiseControl`** — A state machine that selects among three modes:
   - **cruise** — no lead vehicle detected; use speed PID to track set speed.
   - **follow** — lead vehicle present; use distance PID to maintain safe gap.
   - **emergency** — time-to-collision (TTC) below threshold; apply maximum braking.

4. **Implement `simulation.py`** — Loads tuned gains from `tuning_results.yaml`, reads sensor data for lead-vehicle speed, simulates ego-vehicle kinematics at 0.1 s timestep for 150 s, writes `simulation_results.csv`.

5. **Tune PID gains** — Sweep or manually adjust gains for the speed and distance controllers. Evaluate against the six performance targets. Save final gains to `tuning_results.yaml`.

6. **Validate** — Check all performance metrics programmatically before finalizing. Generate `acc_report.md`.

---

## 2. Inspecting Inputs

```python
import yaml
import pandas as pd

# --- vehicle_params.yaml ---
with open('vehicle_params.yaml', 'r') as f:
    config = yaml.safe_load(f)

vehicle = config['vehicle']
acc = config['acc_settings']

MAX_ACCEL  = vehicle['max_acceleration']   # e.g. 3.0 m/s²
MAX_DECEL  = vehicle['max_deceleration']   # e.g. -8.0 m/s²
SET_SPEED  = acc['set_speed']              # e.g. 30.0 m/s
TIME_HEADWAY = acc['time_headway']         # e.g. 1.5 s
MIN_GAP    = acc['minimum_gap']            # e.g. 10.0 m
TTC_THRESH = acc['emergency_ttc_threshold'] # e.g. 3.0 s

print(f"Accel limits: [{MAX_DECEL}, {MAX_ACCEL}]")
print(f"Set speed: {SET_SPEED}, headway: {TIME_HEADWAY}, min gap: {MIN_GAP}")

# --- sensor_data.csv ---
sensor = pd.read_csv('sensor_data.csv')
print(f"Rows: {len(sensor)}, Columns: {list(sensor.columns)}")
print(f"Time range: {sensor['time'].iloc[0]} – {sensor['time'].iloc[-1]}")

# Identify when lead vehicle is present (non-null lead_speed)
lead_present = sensor.dropna(subset=['lead_speed'])
if len(lead_present) > 0:
    t_start = lead_present['time'].iloc[0]
    t_end   = lead_present['time'].iloc[-1]
    print(f"Lead vehicle present: t={t_start} – {t_end}")
    print(f"Lead speed range: {lead_present['lead_speed'].min():.1f} – "
          f"{lead_present['lead_speed'].max():.1f}")
    print(f"Distance range: {lead_present['distance'].min():.1f} – "
          f"{lead_present['distance'].max():.1f}")
```

**Why this matters:** The sensor data typically shows three phases — free driving (cruise), car-following, and an emergency braking event. Knowing the exact time boundaries lets you verify that all three modes activate during simulation.

---

## 3. PID Controller with Anti-Windup

### Key Design Decisions

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| Anti-windup method | Back-calculation | Prevents integral saturation during actuator limits; simple to implement |
| Derivative filtering | Not required for 0.1 s timestep | Sensor noise is manageable at this sample rate |
| Integral clamping | Belt-and-suspenders with back-calc | Extra safety against runaway integral |

### Implementation

```python
# pid_controller.py
class PIDController:
    """PID controller with back-calculation anti-windup."""

    def __init__(self, kp: float, ki: float, kd: float,
                 output_min: float = float('-inf'),
                 output_max: float = float('inf'),
                 anti_windup_gain: float = 1.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.anti_windup_gain = anti_windup_gain

        self._integral = 0.0
        self._prev_error = None

    def reset(self):
        self._integral = 0.0
        self._prev_error = None

    def compute(self, error: float, dt: float) -> float:
        if dt <= 0:
            return 0.0

        # Proportional
        p_term = self.kp * error

        # Integral (accumulated BEFORE clamping — corrected after)
        self._integral += error * dt
        i_term = self.ki * self._integral

        # Derivative
        if self._prev_error is not None:
            d_term = self.kd * (error - self._prev_error) / dt
        else:
            d_term = 0.0
        self._prev_error = error

        # Raw output
        output_raw = p_term + i_term + d_term

        # Clamp
        output_clamped = max(self.output_min, min(self.output_max, output_raw))

        # Back-calculation anti-windup: reduce integral by the saturation amount
        saturation_error = output_clamped - output_raw
        if self.ki != 0:
            self._integral += (self.anti_windup_gain * saturation_error) / self.ki

        return output_clamped
```

**Critical note on anti-windup:** Without back-calculation, the integral term grows unchecked while acceleration is clamped at 3.0 m/s² during the 0→30 m/s ramp. When the ego vehicle finally reaches set speed, the bloated integral causes 10-20% overshoot — far above the 5% target. The back-calculation line `self._integral += (anti_windup_gain * saturation_error) / self.ki` is what keeps overshoot under control.

---

## 4. ACC System (Mode Selection + Dual PID)

### Mode Logic

```
if lead_speed is None or NaN:
    mode = 'cruise'
elif TTC < emergency_ttc_threshold and closing:
    mode = 'emergency'
else:
    mode = 'follow'
```

### Desired Distance Formula

```
desired_distance = max(min_gap, time_headway * ego_speed)
```

This ensures the gap grows with speed (headway-based) but never drops below the absolute minimum.

### Implementation

```python
# acc_system.py
import math
from pid_controller import PIDController


class AdaptiveCruiseControl:
    def __init__(self, config: dict):
        acc = config['acc_settings']
        veh = config['vehicle']

        self.set_speed    = acc['set_speed']
        self.time_headway = acc['time_headway']
        self.min_gap      = acc['minimum_gap']
        self.ttc_thresh   = acc['emergency_ttc_threshold']
        self.max_accel    = veh['max_acceleration']
        self.max_decel    = veh['max_deceleration']

        # PID controllers — gains set externally via set_gains()
        self.speed_pid = PIDController(
            kp=1.0, ki=0.1, kd=0.05,
            output_min=self.max_decel, output_max=self.max_accel
        )
        self.distance_pid = PIDController(
            kp=0.5, ki=0.02, kd=0.3,
            output_min=self.max_decel, output_max=self.max_accel
        )

    def set_speed_gains(self, kp, ki, kd):
        self.speed_pid.kp = kp
        self.speed_pid.ki = ki
        self.speed_pid.kd = kd

    def set_distance_gains(self, kp, ki, kd):
        self.distance_pid.kp = kp
        self.distance_pid.ki = ki
        self.distance_pid.kd = kd

    def compute(self, ego_speed: float, lead_speed, distance, dt: float):
        """
        Returns: (acceleration_cmd, mode, distance_error)
        - acceleration_cmd: clamped to [max_decel, max_accel]
        - mode: 'cruise' | 'follow' | 'emergency'
        - distance_error: float or None (None in cruise mode)
        """
        # --- Determine if lead vehicle is present ---
        lead_present = (lead_speed is not None and
                        not (isinstance(lead_speed, float) and math.isnan(lead_speed)))

        if not lead_present:
            # CRUISE MODE
            speed_error = self.set_speed - ego_speed
            accel = self.speed_pid.compute(speed_error, dt)
            accel = max(self.max_decel, min(self.max_accel, accel))
            # Reset distance PID so it doesn't accumulate stale integral
            self.distance_pid.reset()
            return accel, 'cruise', None

        # --- Lead vehicle present ---
        # Compute TTC
        relative_speed = ego_speed - lead_speed  # positive = closing
        ttc = float('inf')
        if relative_speed > 0.01 and distance > 0:
            ttc = distance / relative_speed

        # EMERGENCY MODE
        if ttc < self.ttc_thresh and relative_speed > 0.01:
            self.speed_pid.reset()
            self.distance_pid.reset()
            desired_dist = max(self.min_gap, self.time_headway * ego_speed)
            dist_error = distance - desired_dist
            return self.max_decel, 'emergency', dist_error

        # FOLLOW MODE
        desired_dist = max(self.min_gap, self.time_headway * ego_speed)
        dist_error = distance - desired_dist  # positive = too far, negative = too close

        # Distance PID output
        dist_accel = self.distance_pid.compute(dist_error, dt)

        # Also compute speed PID targeting lead speed (don't exceed set speed)
        target_speed = min(lead_speed, self.set_speed)
        speed_error = target_speed - ego_speed
        speed_accel = self.speed_pid.compute(speed_error, dt)

        # Take the more conservative (lower) of the two commands
        accel = min(dist_accel, speed_accel)
        accel = max(self.max_decel, min(self.max_accel, accel))

        return accel, 'follow', dist_error
```

**Design note — `min(dist_accel, speed_accel)` in follow mode:** Taking the minimum of the distance and speed commands ensures the ego vehicle never accelerates faster than the distance controller allows, even if the speed controller wants more. This is a standard ACC safety pattern.

---

## 5. Simulation

### Architecture

The simulation does NOT replay the ego speed from `sensor_data.csv`. It only uses the sensor file for **lead vehicle** information (`lead_speed`, initial `distance`). The ego vehicle's speed and position are computed from first principles:

```
ego_speed[t+1] = max(0, ego_speed[t] + accel * dt)
distance[t+1]  = distance[t] + (lead_speed[t] - ego_speed[t+1]) * dt
```

This is essential — if you replay the sensor ego speed, you're not simulating anything and the controller has no effect.

### Implementation

```python
# simulation.py
import math
import yaml
import pandas as pd
from acc_system import AdaptiveCruiseControl


def run_simulation():
    # --- Load config ---
    with open('vehicle_params.yaml', 'r') as f:
        config = yaml.safe_load(f)

    # --- Load tuned gains ---
    with open('tuning_results.yaml', 'r') as f:
        tuning = yaml.safe_load(f)

    # --- Load sensor data (for lead vehicle only) ---
    sensor = pd.read_csv('sensor_data.csv')

    # --- Initialize ACC ---
    acc = AdaptiveCruiseControl(config)
    sp = tuning['pid_speed']
    dp = tuning['pid_distance']
    acc.set_speed_gains(sp['kp'], sp['ki'], sp['kd'])
    acc.set_distance_gains(dp['kp'], dp['ki'], dp['kd'])

    dt = 0.1
    max_accel = config['vehicle']['max_acceleration']
    max_decel = config['vehicle']['max_deceleration']

    # --- State ---
    ego_speed = 0.0
    # Initial distance from first valid sensor reading
    initial_dist_rows = sensor.dropna(subset=['distance'])
    if len(initial_dist_rows) > 0:
        initial_distance = initial_dist_rows['distance'].iloc[0]
    else:
        initial_distance = 200.0
    current_distance = initial_distance

    results = []

    for i in range(len(sensor)):
        row = sensor.iloc[i]
        t = row['time']

        # Lead vehicle info from sensor
        lead_speed_val = row.get('lead_speed', None)
        if pd.isna(lead_speed_val):
            lead_speed_val = None

        # For distance: use simulated distance when lead is present,
        # reset from sensor when lead first appears
        dist_for_controller = None
        if lead_speed_val is not None:
            dist_for_controller = current_distance

        # --- ACC compute ---
        accel_cmd, mode, dist_error = acc.compute(
            ego_speed, lead_speed_val, dist_for_controller, dt
        )

        # Clamp acceleration
        accel_cmd = max(max_decel, min(max_accel, accel_cmd))

        # --- Compute TTC for logging ---
        ttc_val = None
        if lead_speed_val is not None and current_distance is not None:
            rel_speed = ego_speed - lead_speed_val
            if rel_speed > 0.01:
                ttc_val = current_distance / rel_speed

        # --- Record ---
        results.append({
            'time': round(t, 1),
            'ego_speed': round(ego_speed, 6),
            'acceleration_cmd': round(accel_cmd, 6),
            'mode': mode,
            'distance_error': round(dist_error, 6) if dist_error is not None else '',
            'distance': round(current_distance, 6) if lead_speed_val is not None else '',
            'ttc': round(ttc_val, 6) if ttc_val is not None else '',
        })

        # --- Update ego state ---
        ego_speed = max(0.0, ego_speed + accel_cmd * dt)

        # --- Update distance (only when lead present) ---
        if lead_speed_val is not None:
            current_distance += (lead_speed_val - ego_speed) * dt
            current_distance = max(0.0, current_distance)
        else:
            # Reset distance for when lead appears next
            # Look ahead to see if lead appears in next step
            if i + 1 < len(sensor):
                next_lead = sensor.iloc[i + 1].get('lead_speed', None)
                next_dist = sensor.iloc[i + 1].get('distance', None)
                if not pd.isna(next_lead) and not pd.isna(next_dist):
                    current_distance = next_dist

    # --- Write CSV ---
    df = pd.DataFrame(results)
    df.to_csv('simulation_results.csv', index=False)
    print(f"Wrote {len(df)} rows to simulation_results.csv")

    return df


if __name__ == '__main__':
    df = run_simulation()

    # --- Quick metrics ---
    cruise = df[df['mode'] == 'cruise']
    follow = df[df['mode'] == 'follow']
    emergency = df[df['mode'] == 'emergency']
    print(f"Modes: cruise={len(cruise)}, follow={len(follow)}, emergency={len(emergency)}")

    speeds = df['ego_speed'].values
    set_speed = 30.0

    # Rise time: first time ego_speed >= 0.9 * set_speed
    rise_idx = next((i for i, s in enumerate(speeds) if s >= 0.9 * set_speed), None)
    if rise_idx:
        print(f"Rise time: {df['time'].iloc[rise_idx]:.1f}s")

    # Overshoot
    max_speed = max(speeds)
    overshoot_pct = (max_speed - set_speed) / set_speed * 100
    print(f"Max speed: {max_speed:.2f}, Overshoot: {overshoot_pct:.2f}%")

    # SS speed error (last 20s of cruise before lead appears)
    cruise_late = df[(df['mode'] == 'cruise') & (df['time'] >= 20) & (df['time'] <= 29)]
    if len(cruise_late) > 0:
        ss_err = abs(cruise_late['ego_speed'].mean() - set_speed)
        print(f"SS speed error: {ss_err:.4f} m/s")

    # Min distance
    dist_vals = pd.to_numeric(df['distance'], errors='coerce').dropna()
    if len(dist_vals) > 0:
        print(f"Min distance: {dist_vals.min():.2f}m")
```

---

## 6. PID Tuning Strategy

### Approach

Tune the speed PID and distance PID **separately**, then verify together.

#### Speed PID (cruise mode)

The ego vehicle starts at 0 m/s and must reach 30 m/s. Constraints:
- Rise time < 10 s → need aggressive enough Kp
- Overshoot < 5% → need anti-windup + moderate Ki
- SS error < 0.5 m/s → need nonzero Ki

**Recommended starting point:** `kp=1.5, ki=0.3, kd=0.1`

Rationale:
- `kp=1.5`: At 30 m/s error, gives 45 m/s² raw → clamped to 3.0 → full throttle during ramp. As error shrinks below 2 m/s, proportional alone gives 3 m/s² which is still the max. The anti-windup prevents the integral from growing during saturation.
- `ki=0.3`: Eliminates steady-state error. With anti-windup, this won't cause overshoot.
- `kd=0.1`: Small damping to reduce oscillation near set point.

#### Distance PID (follow mode)

The distance error is `actual_distance - desired_distance`. Positive = too far (accelerate), negative = too close (brake).

**Recommended starting point:** `kp=0.5, ki=0.02, kd=0.3`

Rationale:
- `kp=0.5`: Moderate response to distance errors. Too high causes oscillation.
- `ki=0.02`: Very small — distance errors are transient and integral can cause instability.
- `kd=0.3`: Important for damping; reacts to rate of change of gap.

### Tuning Search (if manual tuning is insufficient)

```python
import itertools
import numpy as np

# Grid search over gain combinations
kp_range = [1.0, 1.5, 2.0]
ki_range = [0.1, 0.3, 0.5]
kd_range = [0.05, 0.1, 0.2]

best_score = float('inf')
best_gains = None

for kp, ki, kd in itertools.product(kp_range, ki_range, kd_range):
    # Run simulation with these gains, compute metrics
    # Score = weighted sum of constraint violations
    metrics = run_with_gains(kp, ki, kd)  # your simulation function
    score = (
        max(0, metrics['rise_time'] - 10) * 10 +
        max(0, metrics['overshoot_pct'] - 5) * 5 +
        max(0, metrics['ss_error'] - 0.5) * 20 +
        max(0, 5 - metrics['min_dist']) * 50
    )
    if score < best_score:
        best_score = score
        best_gains = (kp, ki, kd)
```

### Save Tuning Results

```python
tuning = {
    'pid_speed': {'kp': 1.5, 'ki': 0.3, 'kd': 0.1},
    'pid_distance': {'kp': 0.5, 'ki': 0.02, 'kd': 0.3},
}
with open('tuning_results.yaml', 'w') as f:
    yaml.dump(tuning, f, default_flow_style=False)
```

**Constraint on gains (from validator):**
- `kp` ∈ (0, 10)
- `ki` ∈ [0, 5)
- `kd` ∈ [0, 5)

---

## 7. Output Format Requirements

### simulation_results.csv

Exactly 1501 rows (t = 0.0 to 150.0 at 0.1 s steps). Column order must be exact:

```
time,ego_speed,acceleration_cmd,mode,distance_error,distance,ttc
```

- `time`: float, one decimal place
- `ego_speed`: float, non-negative
- `acceleration_cmd`: float, within [max_decel, max_accel]
- `mode`: one of `cruise`, `follow`, `emergency`
- `distance_error`, `distance`, `ttc`: float when lead present, **empty string** (not NaN, not "nan") when in cruise mode

### tuning_results.yaml

```yaml
pid_speed:
  kp: 1.5
  ki: 0.3
  kd: 0.1
pid_distance:
  kp: 0.5
  ki: 0.02
  kd: 0.3
```

### acc_report.md

Must contain keywords checked by the validator. Include sections on:
- System design / architecture
- PID tuning methodology
- Simulation results with numeric metrics
- Safety features (emergency braking, TTC)

Use terms: "cruise", "follow", "emergency", "PID", "overshoot", "steady-state", "rise time", "TTC", "anti-windup", "safety".

---

## 8. Performance Targets and Verification

```python
def verify_metrics(df, set_speed=30.0):
    """Check all six performance targets. Returns dict of pass/fail."""
    speeds = df['ego_speed'].values
    times = df['time'].values

    # 1. Rise time: first t where speed >= 0.9 * set_speed
    rise_idx = next((i for i, s in enumerate(speeds) if s >= 0.9 * set_speed), None)
    rise_time = times[rise_idx] if rise_idx else float('inf')

    # 2. Overshoot
    max_speed = max(speeds)
    overshoot_pct = max(0, (max_speed - set_speed) / set_speed * 100)

    # 3. SS speed error — average |error| in steady cruise (e.g., t=20-29)
    cruise_mask = (df['mode'] == 'cruise') & (times >= 20)
    if cruise_mask.any():
        ss_error = abs(speeds[cruise_mask] - set_speed).mean()
    else:
        ss_error = float('inf')

    # 4. Distance SS error — average |dist_error| in steady follow
    follow = df[df['mode'] == 'follow']
    if len(follow) > 0:
        dist_errors = pd.to_numeric(follow['distance_error'], errors='coerce').dropna()
        dist_ss_error = abs(dist_errors).mean() if len(dist_errors) > 0 else float('inf')
    else:
        dist_ss_error = float('inf')

    # 5. Min distance
    all_dist = pd.to_numeric(df['distance'], errors='coerce').dropna()
    min_dist = all_dist.min() if len(all_dist) > 0 else float('inf')

    # 6. Duration
    duration = times[-1] - times[0]

    results = {
        'rise_time': (rise_time, rise_time < 10, '<10s'),
        'overshoot': (overshoot_pct, overshoot_pct < 5, '<5%'),
        'ss_speed_error': (ss_error, ss_error < 0.5, '<0.5 m/s'),
        'dist_ss_error': (dist_ss_error, dist_ss_error < 2, '<2m'),
        'min_distance': (min_dist, min_dist > 5, '>5m'),
        'duration': (duration, duration >= 150, '>=150s'),
    }

    for name, (val, passed, target) in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status} {name}: {val:.4f} (target {target})")

    return all(passed for _, passed, _ in results.values())
```

---

## 9. Common Pitfalls

### Pitfall 1: No Anti-Windup → Massive Overshoot

The ego vehicle starts at 0 m/s with a 30 m/s target. For ~10 seconds, the error is large but acceleration is clamped at 3.0 m/s². A naive PID accumulates `integral ≈ 30 * 10 / 2 = 150` during this period. When the error finally shrinks, the integral term alone commands `ki * 150` which far exceeds the set speed. **Always use back-calculation anti-windup.**

### Pitfall 2: Replaying Sensor Ego Speed Instead of Simulating

The sensor_data.csv contains an `ego_speed` column from real-world data. If you copy this into the output, the PID controller has no effect and the results won't match the expected performance. **Only use sensor data for lead vehicle info.** Simulate ego speed from `v += a * dt`.

### Pitfall 3: Writing NaN Instead of Empty String in CSV

The validator checks for exact column format. When in cruise mode, `distance_error`, `distance`, and `ttc` should be empty strings (`''`), not `NaN`, `nan`, or `None`. Use `''` when building the results dict, and pandas will write empty cells.

### Pitfall 4: Distance Not Updated Kinematically

If you use the raw sensor `distance` column directly, the distance won't respond to the ego vehicle's control actions. Instead, initialize distance from the sensor when the lead vehicle first appears, then update: `distance += (lead_speed - ego_speed) * dt`.

### Pitfall 5: Forgetting to Reset PIDs on Mode Transitions

When switching from follow→cruise or cruise→follow, the inactive PID's integral term is stale. Reset the PID that's not being used to prevent a burst of accumulated error when it becomes active again.

### Pitfall 6: TTC Computed Incorrectly

TTC = distance / relative_speed, where relative_speed = ego_speed - lead_speed. Only valid when relative_speed > 0 (closing). If the ego is slower than the lead, TTC is infinite (safe). Guard against division by zero.

### Pitfall 7: Gain Values Outside Validator Bounds

The validator enforces `kp ∈ (0, 10)`, `ki ∈ [0, 5)`, `kd ∈ [0, 5)`. Gains of exactly 0 for kp will fail. Gains of exactly 5 for ki or kd will fail (strict less-than).

### Pitfall 8: Follow Mode Using Only Distance PID

Using only the distance PID in follow mode can cause the ego vehicle to accelerate beyond the lead vehicle's speed (if the gap is large). **Combine distance PID with a speed PID targeting `min(lead_speed, set_speed)` and take the minimum** of the two commands.

---

## 10. Reference Implementation

This is a complete, self-contained implementation. Copy, adapt file paths if needed, and run.

```python
#!/usr/bin/env python3
"""
Complete Adaptive Cruise Control simulation.
Files produced: pid_controller.py, acc_system.py, simulation.py,
                tuning_results.yaml, simulation_results.csv, acc_report.md
"""

import math
import yaml
import pandas as pd
import os

# ============================================================
# STEP 0: Read configuration and sensor data
# ============================================================
with open('vehicle_params.yaml', 'r') as f:
    config = yaml.safe_load(f)

sensor = pd.read_csv('sensor_data.csv')

vehicle = config['vehicle']
acc_cfg = config['acc_settings']
MAX_ACCEL    = vehicle['max_acceleration']    # 3.0
MAX_DECEL    = vehicle['max_deceleration']    # -8.0
SET_SPEED    = acc_cfg['set_speed']           # 30.0
TIME_HEADWAY = acc_cfg['time_headway']        # 1.5
MIN_GAP      = acc_cfg['minimum_gap']         # 10.0
TTC_THRESH   = acc_cfg['emergency_ttc_threshold']  # 3.0
DT           = 0.1

# ============================================================
# STEP 1: PID Controller
# ============================================================
class PIDController:
    def __init__(self, kp, ki, kd,
                 output_min=float('-inf'), output_max=float('inf'),
                 anti_windup_gain=1.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.anti_windup_gain = anti_windup_gain
        self._integral = 0.0
        self._prev_error = None

    def reset(self):
        self._integral = 0.0
        self._prev_error = None

    def compute(self, error, dt):
        if dt <= 0:
            return 0.0