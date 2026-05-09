---
title: "HVAC Temperature Controller: Calibration, System ID, PID Tuning, and Closed-Loop Control"
category: hvac-control
tags:
  - pid-control
  - system-identification
  - first-order-systems
  - temperature-control
  - lambda-tuning
  - calibration
domain: control-systems
difficulty: intermediate
prerequisites:
  - numpy
  - scipy
  - json
  - python3
---

# HVAC Temperature Controller Skill

Implement a complete HVAC temperature control pipeline: open-loop calibration → first-order system identification → PID gain tuning → closed-loop control → metrics computation. The target is to bring a room from ~18°C to a 22°C setpoint while meeting strict performance constraints (steady-state error, settling time, overshoot, safety limits).

---

## 1. High-Level Workflow

1. **Inspect the simulator** — Read `hvac_simulator.py` and any config files (`room_config.json`) to understand the API, the system dynamics equation, hidden parameters, ambient temperature, noise characteristics, and the `step()` / `reset()` interface.
2. **Run an open-loop calibration test** — Apply a constant heater power (e.g., 50%) for ≥30 seconds, collecting ≥20 data points. Log every `(time, temperature, heater_power)` tuple. Save as `calibration_log.json`.
3. **Estimate system parameters** — Fit the calibration data to a first-order step-response model to extract the static gain `K` and time constant `tau`. Validate with R² ≥ 0.90. Save as `estimated_params.json`.
4. **Calculate PID gains** — Use Lambda tuning (or IMC tuning) with the estimated `K` and `tau` to compute `Kp`, `Ki`, and optionally `Kd`. Save as `tuned_gains.json`.
5. **Run closed-loop PID control** — Reset the simulator, run for ≥150 seconds with the PID controller, clamp heater output to [0, 100]%. Log every step. Save as `control_log.json`.
6. **Compute and save performance metrics** — Calculate rise time, overshoot, settling time, steady-state error, and max temperature from the control log. Save as `metrics.json`.
7. **Verify all outputs** — Cross-check every metric against the pass criteria before finalizing.

---

## 2. Understanding the Simulator

The typical HVAC simulator exposes a class like:

```python
# Typical API (read from hvac_simulator.py to confirm exact names)
from hvac_simulator import HVACSimulator

sim = HVACSimulator()          # or HVACSimulator(config_path="room_config.json")
state = sim.reset()            # returns dict: {"time": 0.0, "temperature": T0}
state = sim.step(heater_power) # heater_power in [0, 100], returns {"time": t, "temperature": T}
```

The underlying dynamics are typically a first-order system with sensor noise:

```
dT/dt = (1/tau) * (K * u + T_ambient - T) + noise
```

Where:
- `K` = static gain (°C per % heater power at steady state, typically 0.08–0.15)
- `tau` = time constant (seconds, typically 20–60)
- `T_ambient` = ambient/outdoor temperature (typically ~15°C)
- `u` = heater power (0–100%)
- noise = sensor noise (±1–2°C)

**Key**: Always read the simulator source to confirm the exact equation, the `step()` timestep (`dt`), noise amplitude, and whether `reset()` returns a randomized initial temperature.

---

## 3. Calibration Phase

### Why calibrate?
You need real system response data to estimate `K` and `tau`. A step response (constant input) is the simplest and most reliable excitation signal for a first-order system.

### Calibration procedure

```python
import json
from hvac_simulator import HVACSimulator

sim = HVACSimulator()
state = sim.reset()

CALIBRATION_POWER = 50.0   # Use 50% — strong enough signal, won't saturate
CALIBRATION_DURATION = 60.0 # 60s gives ~1.5 time constants for tau~40

data = [{"time": state["time"], "temperature": state["temperature"], "heater_power": 0.0}]

while state["time"] < CALIBRATION_DURATION:
    state = sim.step(CALIBRATION_POWER)
    data.append({
        "time": round(state["time"], 4),
        "temperature": round(state["temperature"], 4),
        "heater_power": CALIBRATION_POWER
    })

calibration_log = {
    "phase": "calibration",
    "heater_power_test": CALIBRATION_POWER,
    "data": data
}

with open("calibration_log.json", "w") as f:
    json.dump(calibration_log, f, indent=2)

print(f"Calibration: {len(data)} points over {data[-1]['time']:.1f}s")
```

### Calibration checklist
- Duration ≥ 30 seconds (aim for 60s to capture enough of the exponential curve)
- At least 20 data points (most simulators use dt=0.5s, so 60s → 120+ points)
- Record the initial temperature `T0` before applying power — this is critical for fitting
- Use a moderate power level (40–60%) to get a clear signal without hitting limits

---

## 4. System Identification (Parameter Estimation)

### The model

For a first-order system with step input `u` starting from `T0`:

```
T(t) = T_ss - (T_ss - T0) * exp(-t / tau)
```

Where `T_ss = T_ambient + K * u` is the steady-state temperature. We don't know `T_ambient` directly, but we can rearrange:

```
T(t) = T0 + (T_ss - T0) * (1 - exp(-t / tau))
```

Let `delta_T_ss = T_ss - T0`. We fit for `delta_T_ss` and `tau`, then derive `K = delta_T_ss / u` (since `T_ss - T0 ≈ K*u + T_ambient - T0`, and for the gain relevant to control, `K ≈ delta_T_ss / u` is the effective gain).

### Fitting with scipy

```python
import json
import numpy as np
from scipy.optimize import curve_fit

with open("calibration_log.json") as f:
    cal = json.load(f)

data = cal["data"]
u_cal = cal["heater_power_test"]

times = np.array([d["time"] for d in data])
temps = np.array([d["temperature"] for d in data])

T0 = temps[0]

def step_response(t, delta_T_ss, tau):
    """First-order step response from T0."""
    return T0 + delta_T_ss * (1 - np.exp(-t / tau))

# Initial guesses: delta_T_ss ~ a few degrees, tau ~ 30s
p0 = [4.0, 30.0]
bounds = ([0.1, 1.0], [50.0, 200.0])

popt, pcov = curve_fit(step_response, times, temps, p0=p0, bounds=bounds, maxfev=10000)
delta_T_ss, tau = popt

# Effective gain: K = delta_T_ss / u_cal
# But be careful: the TRUE system gain K is defined as K in dT/dt = (1/tau)*(K*u + T_amb - T)
# At steady state: T_ss = K*u + T_amb, so T_ss - T_amb = K*u
# We measured T_ss - T0, and T0 ≈ T_amb (room starts near ambient), so K ≈ delta_T_ss / u_cal
K = delta_T_ss / u_cal

# Goodness of fit
T_pred = step_response(times, *popt)
ss_res = np.sum((temps - T_pred) ** 2)
ss_tot = np.sum((temps - np.mean(temps)) ** 2)
r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
fitting_error = np.sqrt(np.mean((temps - T_pred) ** 2))

params = {
    "K": round(K, 4),
    "tau": round(tau, 4),
    "r_squared": round(r_squared, 4),
    "fitting_error": round(fitting_error, 4)
}

with open("estimated_params.json", "w") as f:
    json.dump(params, f, indent=2)

print(f"K={K:.4f}, tau={tau:.2f}, R²={r_squared:.4f}")
```

### Validation criteria
- `R²` should be ≥ 0.90 (typically ≥ 0.99 for clean first-order systems with noise averaging)
- `K` should be in a physically reasonable range (0.05–0.20 for typical HVAC)
- `tau` should be in a reasonable range (10–80 seconds)
- If R² is low, check: did you include the initial zero-power point? Is the calibration duration long enough?

---

## 5. PID Gain Tuning (Lambda Method)

### Why Lambda tuning?

Lambda tuning (also called IMC tuning) is ideal for first-order systems because:
- It has a single tuning parameter `λ` (desired closed-loop time constant)
- It guarantees stability for any `λ > 0`
- It provides a direct tradeoff between speed and robustness
- No derivative term needed (reduces noise sensitivity)

### Lambda tuning formulas

For a first-order-plus-dead-time (FOPDT) model with no dead time:

```
Kp = tau / (K * lambda)
Ki = Kp / tau
Kd = 0  (no derivative for first-order without dead time)
```

Where `λ` (lambda) is the desired closed-loop time constant:
- `λ = tau` → moderate response (good default)
- `λ = tau / 2` → aggressive (faster but more overshoot)
- `λ = 2 * tau` → conservative (slower but very smooth)

```python
import json

with open("estimated_params.json") as f:
    params = json.load(f)

K = params["K"]
tau = params["tau"]

# Lambda = tau gives a good balance of speed and stability
lam = tau  # Can adjust: smaller = faster but more overshoot

Kp = tau / (K * lam)
Ki = Kp / tau
Kd = 0.0

gains = {
    "Kp": round(Kp, 4),
    "Ki": round(Ki, 4),
    "Kd": round(Kd, 4),
    "lambda": round(lam, 4)
}

with open("tuned_gains.json", "w") as f:
    json.dump(gains, f, indent=2)

print(f"Kp={Kp:.4f}, Ki={Ki:.4f}, Kd={Kd:.4f}, λ={lam:.2f}")
```

### Gain sanity checks
- `Kp` should typically be in range 2–20 for HVAC systems
- `Ki` should be small (0.05–0.5) to avoid integral windup
- If `Kp` is very large (>50), your `K` estimate is probably too small — recheck calibration
- If `Ki` is very large (>1.0), your `tau` estimate is probably too small

---

## 6. Closed-Loop PID Control

### PID controller implementation

```python
import json
import numpy as np
from hvac_simulator import HVACSimulator

with open("tuned_gains.json") as f:
    gains = json.load(f)

Kp = gains["Kp"]
Ki = gains["Ki"]
Kd = gains["Kd"]

SETPOINT = 22.0
CONTROL_DURATION = 200.0  # Must be >= 150s

sim = HVACSimulator()
state = sim.reset()

# PID state
integral = 0.0
prev_error = SETPOINT - state["temperature"]
dt = 0.5  # Read from simulator source to confirm

data = []

while state["time"] < CONTROL_DURATION:
    temp = state["temperature"]
    error = SETPOINT - temp

    # Proportional
    P = Kp * error

    # Integral with anti-windup clamp
    integral += error * dt
    integral = np.clip(integral, -100.0 / max(Ki, 1e-6), 100.0 / max(Ki, 1e-6))
    I = Ki * integral

    # Derivative (on error; use on measurement for less noise)
    D = Kd * (error - prev_error) / dt if dt > 0 else 0.0
    prev_error = error

    # Compute and clamp output
    output = P + I + D
    heater_power = float(np.clip(output, 0.0, 100.0))

    # Step simulator
    state = sim.step(heater_power)

    data.append({
        "time": round(state["time"], 4),
        "temperature": round(state["temperature"], 4),
        "setpoint": SETPOINT,
        "heater_power": round(heater_power, 4),
        "error": round(SETPOINT - state["temperature"], 4)
    })

control_log = {
    "phase": "control",
    "setpoint": SETPOINT,
    "data": data
}

with open("control_log.json", "w") as f:
    json.dump(control_log, f, indent=2)

print(f"Control: {len(data)} points over {data[-1]['time']:.1f}s")
```

### Critical implementation details

1. **Anti-windup**: Always clamp the integral term. Without it, the integral can grow unbounded when the heater is saturated (0% or 100%), causing massive overshoot when the error changes sign.

2. **Output clamping**: Heater power must be in [0, 100]. Clamp AFTER summing P+I+D.

3. **dt value**: Read from the simulator source. Typically 0.5s. Using the wrong dt will make Ki and Kd behave incorrectly.

4. **Duration**: Must be ≥ 150s. Use 200s to have margin. The verifier checks `times[-1] - times[0]`.

5. **Initial error**: Initialize `prev_error` to the initial error, not zero, to avoid a derivative spike on the first step.

---

## 7. Metrics Computation

The verifier computes metrics with specific definitions. Match them exactly:

```python
import json
import numpy as np

with open("control_log.json") as f:
    clog = json.load(f)

data = clog["data"]
setpoint = clog["setpoint"]

times = np.array([d["time"] for d in data])
temps = np.array([d["temperature"] for d in data])

T_initial = temps[0]

# --- Max temperature ---
max_temp = float(np.max(temps))

# --- Overshoot ---
# Defined as fraction: (max_temp - setpoint) / (setpoint - T_initial)
# Only counts if max_temp > setpoint
if max_temp > setpoint and setpoint != T_initial:
    overshoot = (max_temp - setpoint) / (setpoint - T_initial)
else:
    overshoot = 0.0

# --- Rise time ---
# Time to first reach 90% of the way from T_initial to setpoint
target_90 = T_initial + 0.9 * (setpoint - T_initial)
rise_time = None
for i in range(len(temps)):
    if temps[i] >= target_90:
        rise_time = times[i] - times[0]
        break
if rise_time is None:
    rise_time = times[-1] - times[0]  # Never reached

# --- Settling time ---
# Last time the temperature was outside ±1.0°C of setpoint
# Verifier scans backward from end
settling_band = 1.0
settling_time = None
for i in range(len(temps) - 1, -1, -1):
    if abs(temps[i] - setpoint) > settling_band:
        if i < len(temps) - 1:
            settling_time = times[i + 1] - times[0]
        else:
            settling_time = times[-1] - times[0]
        break
if settling_time is None:
    settling_time = 0.0  # Always within band

# --- Steady-state error ---
# Mean of last 20% of temperature readings vs setpoint
last_portion = max(1, int(len(temps) * 0.2))
ss_error = float(abs(np.mean(temps[-last_portion:]) - setpoint))

# --- Duration ---
duration = times[-1] - times[0]

metrics = {
    "rise_time": round(float(rise_time), 2),
    "overshoot": round(float(overshoot), 4),
    "settling_time": round(float(settling_time), 2),
    "steady_state_error": round(float(ss_error), 4),
    "max_temp": round(float(max_temp), 4)
}

with open("metrics.json", "w") as f:
    json.dump(metrics, f, indent=2)

# Verify against targets
print("=== Performance Check ===")
checks = [
    ("steady_state_error", ss_error, "<", 0.5),
    ("settling_time", settling_time, "<", 120),
    ("overshoot", overshoot, "<", 0.10),
    ("max_temp", max_temp, "<", 30.0),
    ("duration", duration, ">=", 150.0),
]
all_pass = True
for name, val, op, target in checks:
    if op == "<":
        ok = val < target
    else:
        ok = val >= target
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  {name}: {val:.4f} {op} {target} -> {status}")

print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
```

### Target criteria summary

| Metric | Condition | Typical Good Value |
|---|---|---|
| Steady-state error | < 0.5°C | 0.01–0.10°C |
| Settling time | < 120s | 40–80s |
| Overshoot | < 10% (0.10) | 3–8% |
| Max temperature | < 30°C | 22–23°C |
| Control duration | ≥ 150s | 200s |

---

## 8. Common Pitfalls

### Pitfall 1: Wrong gain definition
The system gain `K` in the ODE `dT/dt = (1/tau)*(K*u + T_amb - T)` means that at steady state, `T_ss = K*u + T_amb`. The effective gain for control is `K` itself (°C per % power), NOT `K/tau` or `K*tau`. When you fit `delta_T_ss` from calibration, `K = delta_T_ss / u_cal` only if `T0 ≈ T_amb`. If the initial temperature is far from ambient, you need to account for the offset.

### Pitfall 2: Forgetting anti-windup
Without integral clamping, the integral term accumulates during the initial large-error phase when the heater is saturated at 100%. When the temperature approaches the setpoint, the bloated integral causes massive overshoot (sometimes >30°C). Always clamp the integral.

### Pitfall 3: Insufficient calibration duration
If calibration is too short (e.g., 10s for a system with tau=40s), the curve barely rises and `curve_fit` cannot distinguish between different `(K, tau)` combinations. Use at least 1.5× the expected tau. When in doubt, run for 60s.

### Pitfall 4: Metrics definition mismatch
The verifier uses specific definitions:
- **Overshoot** is `(max_temp - setpoint) / (setpoint - T_initial)`, NOT `(max_temp - setpoint) / setpoint`
- **Settling time** scans backward from the end, finding the last point outside ±1.0°C
- **SS error** uses the last 20% of data points, not just the final point
Match these exactly or your metrics will disagree with the verifier.

### Pitfall 5: Not reading the simulator source
Different task instances may have different `dt`, noise levels, ambient temperatures, or even different ODE formulations. Always read `hvac_simulator.py` before coding. The 5 minutes spent reading saves hours of debugging.

### Pitfall 6: Using Kd with noisy sensors
Sensor noise of ±1–2°C makes the derivative term extremely noisy. For first-order systems without dead time, `Kd = 0` is optimal. If you must use Kd, apply a low-pass filter to the derivative.

### Pitfall 7: Control duration too short
The verifier checks `times[-1] - times[0] >= 150`. If you run exactly 150s, floating-point rounding might make it 149.5s. Use 200s for safety margin.

---

## 9. Reference Implementation

This is a complete, self-contained script that performs all phases. Copy, adapt the import path if needed, and run.

```python
#!/usr/bin/env python3
"""
Complete HVAC temperature control pipeline.
Phases: calibration -> system ID -> PID tuning -> closed-loop control -> metrics.

Requirements: numpy, scipy, hvac_simulator.py in the working directory.
Outputs: calibration_log.json, estimated_params.json, tuned_gains.json,
         control_log.json, metrics.json
"""

import json
import numpy as np
from scipy.optimize import curve_fit

# ─── Import simulator ───────────────────────────────────────────────
# Adjust this import if the simulator is in a different location
from hvac_simulator import HVACSimulator


# ═══════════════════════════════════════════════════════════════════
# PHASE 1: CALIBRATION
# ═══════════════════════════════════════════════════════════════════

def run_calibration(sim, power=50.0, duration=60.0):
    """
    Run an open-loop step response test.
    
    Args:
        sim: HVACSimulator instance (already reset)
        power: constant heater power to apply (%)
        duration: how long to run (seconds)
    
    Returns:
        calibration_log dict
    """
    state = sim.reset()
    
    data = [{
        "time": round(state["time"], 4),
        "temperature": round(state["temperature"], 4),
        "heater_power": 0.0
    }]
    
    while state["time"] < duration:
        state = sim.step(power)
        data.append({
            "time": round(state["time"], 4),
            "temperature": round(state["temperature"], 4),
            "heater_power": power
        })
    
    calibration_log = {
        "phase": "calibration",
        "heater_power_test": power,
        "data": data
    }
    
    print(f"[Calibration] {len(data)} points over {data[-1]['time']:.1f}s")
    print(f"[Calibration] T_start={data[0]['temperature']:.2f}, T_end={data[-1]['temperature']:.2f}")
    
    return calibration_log


# ═══════════════════════════════════════════════════════════════════
# PHASE 2: SYSTEM IDENTIFICATION
# ═══════════════════════════════════════════════════════════════════

def estimate_parameters(calibration_log):
    """
    Fit a first-order step response model to calibration data.
    
    Model: T(t) = T0 + delta_T_ss * (1 - exp(-t / tau))
    
    Returns:
        estimated_params dict with K, tau, r_squared, fitting_error
    """
    data = calibration_log["data"]
    u_cal = calibration_log["heater_power_test"]
    
    times = np.array([d["time"] for d in data])
    temps = np.array([d["temperature"] for d in data])
    
    T0 = temps[0]
    
    def step_response(t, delta_T_ss, tau):
        return T0 + delta_T_ss * (1.0 - np.exp(-t / tau))
    
    # Initial guesses
    observed_rise = temps[-1] - T0
    p0 = [max(observed_rise * 1.5, 2.0), 30.0]
    bounds = ([0.1, 1.0], [50.0, 200.0])
    
    try:
        popt, pcov = curve_fit(
            step_response, times, temps,
            p0=p0, bounds=bounds, maxfev=10000
        )
    except RuntimeError:
        # Fallback: try wider bounds and different initial guess
        p0 = [5.0, 40.0]
        bounds = ([0.01, 0.5], [100.0, 500.0])
        popt, pcov = curve_fit(
            step_response, times, temps,
            p0=p0, bounds=bounds, maxfev=50000
        )
    
    delta_T_ss, tau = popt
    K = delta_T_ss / u_cal
    
    # Goodness of fit
    T_pred = step_response(times, *popt)
    ss_res = np.sum((temps - T_pred) ** 2)
    ss_tot = np.sum((temps - np.mean(temps)) ** 2)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-10 else 0.0
    fitting_error = float(np.sqrt(np.mean((temps - T_pred) ** 2)))
    
    params = {
        "K": round(float(K), 4),
        "tau": round(float(tau), 4),
        "r_squared": round(float(r_squared), 4),
        "fitting_error": round(float(fitting_error), 4)
    }
    
    print(f"[SysID] K={K:.4f}, tau={tau:.2f}, R²={r_squared:.4f}, RMSE={fitting_error:.4f}")
    
    if r_squared < 0.90:
        print(f"[SysID] WARNING: R² is low ({r_squared:.3f}). Consider longer calibration.")
    
    return params


# ═══════════════════════════════════════════════════════════════════
# PHASE 3: PID GAIN TUNING (Lambda / IMC Method)
# ═══════════════════════════════════════════════════════════════════

def compute_gains(params, lambda_factor=1.0):
    """
    Compute PID gains using Lambda (IMC) tuning.
    
    Lambda tuning for first-order system (no dead time):
        Kp = tau / (K * lambda)
        Ki = Kp / tau  (equivalent to 1 / (K * lambda))
        Kd = 0
    
    Args:
        params: dict with K and tau
        lambda_factor: multiplier for tau to get lambda.
                       1.0 = moderate, 0.5 = aggressive, 2.0 = conservative
    
    Returns:
        tuned_gains dict
    """
    K = params["K"]
    tau = params["tau"]
    
    lam = tau * lambda_factor  # Desired closed-loop time constant
    
    Kp = tau / (K * lam)
    Ki = Kp / tau  # = 1 / (K * lam)
    Kd = 0.0       # No derivative for first-order without dead time
    
    gains = {
        "Kp": round(float(Kp), 4),
        "Ki": round(float(Ki), 4),
        "Kd": round(float(Kd), 4),
        "lambda": round(float(lam), 4)
    }
    
    print(f"[Tuning] Kp={Kp:.4f}, Ki={Ki:.4f}, Kd={Kd:.4f}, λ={lam:.2f}")
    
    return gains


# ═══════════════════════════════════════════════════════════════════
# PHASE 4: CLOSED-LOOP PID CONTROL
# ═══════════════════════════════════════════════════════════════════

def run_control(sim, gains, setpoint=22.0, duration=200.0):
    """
    Run closed-loop PID control.
    
    Args:
        sim: HVACSimulator instance (will be reset internally)
        gains: dict with Kp, Ki, Kd
        setpoint: target temperature (°C)
        duration: control duration (seconds, must be >= 150)
    
    Returns:
        control_log dict
    """
    Kp = gains["Kp"]
    Ki = gains["Ki"]
    Kd = gains["Kd"]
    
    state = sim.reset()
    
    # PID state variables
    integral = 0.0
    prev_error = setpoint - state["temperature"]
    
    # Determine dt from simulator (read from source; typical default is 0.5)
    # We'll infer it from the first step
    first_time = state["time"]
    state = sim.step(0.0)  # One dummy step to get dt
    dt = state["time"] - first_time
    if dt <= 0:
        dt = 0.5  # Fallback
    
    # Reset and start fresh
    state = sim.reset()
    prev_error = setpoint - state["temperature"]
    integral = 0.0
    
    data = []
    
    # Anti-windup limit: prevent integral from commanding more than 100% on its own
    integral_limit = 100.0 / max(abs(Ki), 1e-10)
    
    while state["time"] < duration:
        temp = state["temperature"]
        error = setpoint - temp
        
        # --- PID computation ---
        # Proportional
        P = Kp * error
        
        # Integral with anti-windup
        integral += error * dt
        integral = float(np.clip(integral, -integral_limit, integral_limit))
        I = Ki * integral
        
        # Derivative (on error)
        D = Kd * (error - prev_error) / dt if dt > 0 else 0.0
        prev_error = error
        
        # Total output, clamped to actuator limits
        output = P + I + D
        heater_power = float(np.clip(output, 0.0, 100.0))
        
        # Step the simulation
        state = sim.step(heater_power)
        
        data.append({
            "time": round(state["time"], 4),
            "temperature": round(state["temperature"], 4),
            "setpoint": setpoint,
            "heater_power": round(heater_power, 4),
            "error": round(setpoint - state["temperature"], 4)
        })
    
    control_log = {
        "phase": "control",
        "setpoint": setpoint,
        "data": data
    }
    
    print(f"[Control] {len(data)} points over {data[-1]['time']:.1f}s")
    print(f"[Control] Final temp: {data