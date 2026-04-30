---
name: hvac-first-order-pi-control-pipeline
description: Run an HVAC identification-and-control pipeline in a simulator by collecting calibration data, estimating first-order thermal parameters, tuning a conservative PI controller, executing closed-loop control, and emitting validator-friendly JSON artifacts.
category: hvac-control
tags:
  - hvac
  - system-identification
  - pid
  - pi-control
  - simulation
  - json
tools:
  - python3
  - numpy
  - scipy
  - rg
  - sed
  - find
---

# HVAC First-Order Identification and PI Control

This skill is for tasks where you must:
1. inspect an HVAC simulator,
2. run a calibration experiment,
3. estimate room dynamics,
4. tune a controller,
5. execute a closed-loop run,
6. save required JSON artifacts,
7. verify performance against constraints.

It is especially useful when the hidden validator checks:
- exact output file names,
- required JSON field names,
- minimum data durations / point counts,
- control quality metrics,
- safety limits like maximum temperature.

The core pattern is a **first-order thermal room** with **heater power in percent** and a **temperature setpoint**. In these tasks, a **conservative PI controller** usually performs better than an aggressive PID because derivative action amplifies measurement noise and can worsen overshoot.

---

# When to Use This Skill

Use this workflow when:
- the simulator behaves like a single-zone room with thermal lag,
- heater power is bounded in `[0, 100]`,
- calibration can be run open-loop,
- the task asks for artifacts like:
  - `calibration_log.json`
  - `estimated_params.json`
  - `tuned_gains.json`
  - `control_log.json`
  - `metrics.json`

Do **not** start by guessing gains blindly. In this task family, successful solutions usually come from:
1. collecting calibration data,
2. estimating a first-order model,
3. tuning a mild PI loop,
4. using anti-windup and optionally a setpoint ramp.

---

# High-Level Workflow

## 1. Inspect the simulator and discover the callable API

**What to do:**  
Find the simulator file, any wrapper runner scripts, and any instructions such as `AGENTS.md`. Read the simulator source before implementing control logic.

**Why:**  
Small API details matter:
- how to reset state,
- how to step the simulator,
- whether time step `dt` is fixed,
- whether temperature readings include sensor noise,
- whether ambient conditions are exposed,
- whether the simulator already encodes known dynamics.

**Decision criteria:**
- If a runner script already exists, adapt it rather than rewriting everything.
- If the simulator exposes direct state fields, still prefer estimating parameters from calibration data, because validators often expect `estimated_params.json` to reflect measured response, not hidden constants.

### Shell inspection commands

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/root}"

echo "Searching for simulator, runner, and instructions..."
rg --files "$ROOT_DIR" | rg 'hvac_simulator\.py|run_.*controller.*\.py|AGENTS\.md$|room_config\.json$' || true

echo
echo "Previewing simulator..."
SIM_PATH="$(rg --files "$ROOT_DIR" | rg 'hvac_simulator\.py$' | head -n1 || true)"
if [[ -n "${SIM_PATH}" ]]; then
  sed -n '1,260p' "$SIM_PATH"
else
  echo "Simulator not found" >&2
fi

echo
echo "Previewing likely runner..."
RUN_PATH="$(rg --files "$ROOT_DIR" | rg 'run_.*controller.*\.py$' | head -n1 || true)"
if [[ -n "${RUN_PATH}" ]]; then
  sed -n '1,260p' "$RUN_PATH"
fi
```

### Python API probing helper

```python
#!/usr/bin/env python3
import importlib.util
import inspect
import json
import os
import sys

def load_module(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location("hvac_simulator", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def summarize_module(module):
    out = {
        "module": getattr(module, "__name__", "unknown"),
        "classes": [],
        "functions": [],
    }
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        try:
            if inspect.isclass(obj):
                out["classes"].append({
                    "name": name,
                    "signature": str(inspect.signature(obj)),
                })
            elif inspect.isfunction(obj):
                out["functions"].append({
                    "name": name,
                    "signature": str(inspect.signature(obj)),
                })
        except Exception:
            pass
    return out

if __name__ == "__main__":
    sim_path = sys.argv[1] if len(sys.argv) > 1 else "/root/hvac_simulator.py"
    module = load_module(sim_path)
    print(json.dumps(summarize_module(module), indent=2))
```

---

## 2. Run an open-loop calibration test with enough duration and samples

**What to do:**  
Apply a fixed heater step, usually a moderate power such as 40%-60%, and log temperature over at least 30 seconds with 20+ points.

**Why:**  
A step test is the simplest robust way to identify a first-order thermal model. Moderate power avoids:
- extremely weak excitation,
- excessive heating,
- safety violations,
- highly nonlinear regions if they exist.

**Decision criteria:**
- Use a fixed sampling interval small enough to exceed the minimum point count comfortably.
- Aim for a clean monotonic temperature rise.
- Keep the phase explicitly marked `"calibration"`.

**Expected JSON shape:**
```json
{
  "phase": "calibration",
  "heater_power_test": 50.0,
  "data": [
    {"time": 0.0, "temperature": 18.0, "heater_power": 0.0},
    {"time": 0.5, "temperature": 18.1, "heater_power": 50.0}
  ]
}
```

### Calibration runner

```python
#!/usr/bin/env python3
import json
import math
import os
from typing import Any, Dict, List

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def get_temperature(obs: Any) -> float:
    if isinstance(obs, dict):
        for key in ("temperature", "temp", "room_temperature"):
            if key in obs:
                return float(obs[key])
    if isinstance(obs, (int, float)):
        return float(obs)
    raise ValueError(f"Cannot extract temperature from observation: {obs!r}")

def safe_reset(sim) -> Any:
    if hasattr(sim, "reset"):
        return sim.reset()
    raise AttributeError("Simulator has no reset() method")

def safe_step(sim, heater_power: float) -> Any:
    heater_power = clamp(float(heater_power), 0.0, 100.0)
    if hasattr(sim, "step"):
        return sim.step(heater_power)
    raise AttributeError("Simulator has no step() method")

def run_calibration(
    sim,
    duration_s: float = 30.0,
    dt: float = 0.5,
    heater_power_test: float = 50.0,
    output_path: str = "calibration_log.json",
) -> Dict[str, Any]:
    if duration_s < 30.0:
        raise ValueError("Calibration duration should be at least 30 seconds")
    if dt <= 0:
        raise ValueError("dt must be positive")

    obs = safe_reset(sim)
    t = 0.0
    data: List[Dict[str, float]] = []

    initial_temp = get_temperature(obs)
    data.append({
        "time": round(t, 6),
        "temperature": round(initial_temp, 6),
        "heater_power": 0.0,
    })

    n_steps = int(math.ceil(duration_s / dt))
    for i in range(n_steps):
        result = safe_step(sim, heater_power_test)
        t = round((i + 1) * dt, 10)

        if isinstance(result, tuple):
            obs = result[0]
        else:
            obs = result

        temp = get_temperature(obs)
        data.append({
            "time": round(t, 6),
            "temperature": round(temp, 6),
            "heater_power": float(heater_power_test),
        })

    if len(data) < 21:
        raise RuntimeError(f"Need at least 21 samples, got {len(data)}")
    if data[-1]["time"] < 30.0:
        raise RuntimeError(f"Need at least 30 seconds, got {data[-1]['time']}")

    log = {
        "phase": "calibration",
        "heater_power_test": float(heater_power_test),
        "data": data,
    }
    write_json(output_path, log)
    return log
```

---

## 3. Estimate first-order parameters directly from the calibration response

**What to do:**  
Fit a first-order model from the measured step response:
- static gain `K` in `degC / %heater`,
- time constant `tau` in `seconds`.

Use the actual calibration data, not hidden constants from source code.

**Why:**  
Validators often check that the estimated parameters are consistent with the calibration log. Reading hidden internal constants can fail if:
- the simulator includes sensor noise,
- the room config differs,
- the expected convention is data-derived,
- the test checks field values against calibration-derived trends.

A common thermal model is:

\[
T(t) = T_0 + \Delta T_{\infty}\left(1 - e^{-t/\tau}\right)
\]

where:
- \(\Delta T_{\infty} = K \cdot u\)
- \(u\) is heater power in percent.

**Decision criteria:**
- Estimate `K` from the asymptotic rise implied by the measured response.
- Estimate `tau` from the 63.2% rise time or by linearizing the model.
- Compute fit quality fields like `r_squared` and `fitting_error`.
- Keep field names exactly as required:
  - `K`
  - `tau`
  - `r_squared`
  - `fitting_error`

### Robust parameter estimation from step response

```python
#!/usr/bin/env python3
import json
import math
from typing import Any, Dict, List, Tuple

import numpy as np

def write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def moving_average(x: np.ndarray, window: int = 5) -> np.ndarray:
    if window <= 1 or len(x) < window:
        return x.copy()
    kernel = np.ones(window) / window
    padded = np.pad(x, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")

def compute_first_order_fit(times: np.ndarray, temps: np.ndarray, heater_power: float) -> Dict[str, float]:
    if len(times) < 8:
        raise ValueError("Not enough data points for estimation")
    if heater_power <= 0:
        raise ValueError("heater_power must be positive")

    # Smooth noisy temperature lightly.
    y = moving_average(temps, window=min(5, len(temps)))
    t = times - times[0]
    T0 = float(y[0])

    # Estimate steady-state change from the tail average.
    tail_n = max(3, len(y) // 5)
    y_tail = float(np.mean(y[-tail_n:]))
    delta_inf = y_tail - T0

    if delta_inf <= 1e-6:
        raise ValueError("Calibration did not produce a measurable temperature rise")

    # Tau from 63.2% point.
    target = T0 + 0.6321205588 * delta_inf
    idx = np.where(y >= target)[0]
    if len(idx) == 0:
        # Fallback: infer tau by best fit over a search range.
        tau_candidates = np.linspace(max(1.0, (t[1] - t[0])), max(5.0, t[-1] * 5.0), 500)
        best_tau = None
        best_sse = float("inf")
        for tau in tau_candidates:
            pred = T0 + delta_inf * (1.0 - np.exp(-t / tau))
            sse = float(np.sum((y - pred) ** 2))
            if sse < best_sse:
                best_sse = sse
                best_tau = tau
        tau = float(best_tau)
    else:
        i = int(idx[0])
        if i == 0:
            tau = max(1.0, float(t[0]))
        else:
            # Linear interpolation around crossing.
            t1, t2 = float(t[i - 1]), float(t[i])
            y1, y2 = float(y[i - 1]), float(y[i])
            if abs(y2 - y1) < 1e-12:
                tau = t2
            else:
                frac = (target - y1) / (y2 - y1)
                tau = t1 + frac * (t2 - t1)

    K = delta_inf / heater_power
    pred = T0 + (K * heater_power) * (1.0 - np.exp(-t / tau))

    residuals = y - pred
    sse = float(np.sum(residuals ** 2))
    sst = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - sse / sst if sst > 1e-12 else 1.0
    rmse = float(np.sqrt(np.mean(residuals ** 2)))

    return {
        "K": round(float(K), 4),
        "tau": round(float(tau), 4),
        "r_squared": round(float(max(min(r_squared, 1.0), -1.0)), 4),
        "fitting_error": round(rmse, 4),
    }

def estimate_parameters(
    calibration_path: str = "calibration_log.json",
    output_path: str = "estimated_params.json",
) -> Dict[str, float]:
    log = load_json(calibration_path)
    if log.get("phase") != "calibration":
        raise ValueError("Calibration log has wrong phase")
    heater_power = float(log["heater_power_test"])
    data: List[Dict[str, Any]] = log["data"]
    if len(data) < 21:
        raise ValueError("Calibration log must contain at least 21 points")

    times = np.array([float(row["time"]) for row in data], dtype=float)
    temps = np.array([float(row["temperature"]) for row in data], dtype=float)

    if times[-1] - times[0] < 30.0:
        raise ValueError("Calibration duration must be at least 30 seconds")

    params = compute_first_order_fit(times, temps, heater_power)
    write_json(output_path, params)
    return params
```

**Important convention:**  
Estimate from the **calibration response itself**. Do not replace this with arbitrary formulas or simulator internals if the task explicitly asks for estimation.

---

## 4. Tune a conservative PI controller from the estimated model

**What to do:**  
Use the first-order model to choose PI gains. A good general-purpose tuning for thermal systems is an IMC / lambda-style PI controller.

For a first-order plant:
\[
G(s) = \frac{K}{\tau s + 1}
\]

A robust PI choice is:
\[
K_p = \frac{\tau}{K \lambda}, \quad K_i = \frac{K_p}{\tau}
\]

where `lambda` is the desired closed-loop aggressiveness.  
For noisy, safety-constrained heating tasks, choose **lambda >= tau** or slightly larger.

**Why:**  
Aggressive gains may reduce rise time but often break overshoot and settling constraints. Conservative gains usually satisfy:
- overshoot < 10%,
- settling time < 120 s,
- max temp safety constraint.

**Decision criteria:**
- If overshoot is a concern, increase `lambda`.
- If response is too slow, reduce `lambda` moderately, but not below a safe floor.
- For simple thermal systems, `Kd = 0.0` is often best.

### Gain calculation

```python
#!/usr/bin/env python3
import json
from typing import Any, Dict

def write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def tune_pi_from_first_order(
    estimated_params_path: str = "estimated_params.json",
    output_path: str = "tuned_gains.json",
) -> Dict[str, float]:
    p = load_json(estimated_params_path)

    K = float(p["K"])
    tau = float(p["tau"])

    if K <= 0 or tau <= 0:
        raise ValueError(f"Invalid estimated parameters: K={K}, tau={tau}")

    # Conservative lambda rule for HVAC-like thermal plants.
    lam = max(tau, 30.0)
    lam = min(lam + 5.0, 60.0)

    Kp = tau / (K * lam)
    Ki = Kp / tau
    Kd = 0.0

    gains = {
        "Kp": round(float(Kp), 4),
        "Ki": round(float(Ki), 4),
        "Kd": round(float(Kd), 4),
        "lambda": round(float(lam), 4),
    }
    write_json(output_path, gains)
    return gains
```

**Practical note:**  
In this task family, a **slightly slower** response often wins overall because it improves overshoot and settling metrics.

---

## 5. Execute closed-loop control with anti-windup and optional setpoint ramp

**What to do:**  
Run the control loop for at least the required control duration, often `>= 150 s`. Use:
- PI control,
- actuator clamping to `[0, 100]`,
- integral anti-windup,
- optional setpoint ramp to reduce overshoot,
- explicit logging of time, temperature, setpoint, heater power, error.

**Why:**  
The main causes of failure are:
- overshoot above the limit,
- insufficient run duration,
- missing control log fields,
- heater saturation leading to integral windup.

**Decision criteria:**
- If the plant starts several degrees below setpoint, a ramped setpoint often reduces overshoot.
- If the room is slow, keep the loop duration comfortably above the minimum.
- If sensor noise is present, avoid derivative control unless absolutely necessary.

**Expected JSON shape:**
```json
{
  "phase": "control",
  "setpoint": 22.0,
  "data": [
    {"time": 30.0, "temperature": 18.5, "setpoint": 22.0, "heater_power": 45.0, "error": 3.5}
  ]
}
```

### Closed-loop controller

```python
#!/usr/bin/env python3
import json
import math
from typing import Any, Dict, List

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_temperature(obs: Any) -> float:
    if isinstance(obs, dict):
        for key in ("temperature", "temp", "room_temperature"):
            if key in obs:
                return float(obs[key])
    if isinstance(obs, (int, float)):
        return float(obs)
    raise ValueError(f"Cannot extract temperature from observation: {obs!r}")

def safe_reset(sim) -> Any:
    if hasattr(sim, "reset"):
        return sim.reset()
    raise AttributeError("Simulator has no reset() method")

def safe_step(sim, heater_power: float) -> Any:
    if hasattr(sim, "step"):
        return sim.step(clamp(heater_power, 0.0, 100.0))
    raise AttributeError("Simulator has no step() method")

def setpoint_ramp(t: float, initial_temp: float, target: float, ramp_duration: float) -> float:
    if ramp_duration <= 0:
        return target
    alpha = clamp(t / ramp_duration, 0.0, 1.0)
    return initial_temp + alpha * (target - initial_temp)

def run_control(
    sim,
    gains_path: str = "tuned_gains.json",
    output_path: str = "control_log.json",
    setpoint: float = 22.0,
    duration_s: float = 180.0,
    dt: float = 0.5,
    ramp_duration: float = 30.0,
) -> Dict[str, Any]:
    gains = load_json(gains_path)
    Kp = float(gains["Kp"])
    Ki = float(gains["Ki"])
    Kd = float(gains.get("Kd", 0.0))

    if duration_s < 150.0:
        raise ValueError("Control duration must be at least 150 seconds")
    if dt <= 0:
        raise ValueError("dt must be positive")

    obs = safe_reset(sim)
    initial_temp = get_temperature(obs)

    integral = 0.0
    prev_temp = initial_temp
    prev_error = setpoint - initial_temp

    t = 0.0
    data: List[Dict[str, float]] = []

    n_steps = int(math.ceil(duration_s / dt))
    for step_idx in range(n_steps + 1):
        measured_temp = get_temperature(obs)
        current_setpoint = setpoint_ramp(t, initial_temp, setpoint, ramp_duration)
        error = current_setpoint - measured_temp

        derivative = 0.0
        if step_idx > 0 and dt > 0:
            derivative = -(measured_temp - prev_temp) / dt

        # Candidate integral update.
        trial_integral = integral + error * dt
        u_unsat = Kp * error + Ki * trial_integral + Kd * derivative
        u = clamp(u_unsat, 0.0, 100.0)

        # Anti-windup: only accept the integral update when it helps or not saturated.
        saturated_high = u >= 100.0 - 1e-9
        saturated_low = u <= 0.0 + 1e-9
        if (not saturated_high and not saturated_low) or \
           (saturated_high and error < 0) or \
           (saturated_low and error > 0):
            integral = trial_integral

        # Recompute with accepted integral.
        u_unsat = Kp * error + Ki * integral + Kd * derivative
        u = clamp(u_unsat, 0.0, 100.0)

        data.append({
            "time": round(t, 6),
            "temperature": round(measured_temp, 6),
            "setpoint": round(current_setpoint, 6),
            "heater_power": round(u, 6),
            "error": round(error, 6),
        })

        result = safe_step(sim, u)
        if isinstance(result, tuple):
            obs = result[0]
        else:
            obs = result

        prev_temp = measured_temp
        prev_error = error
        t = round(t + dt, 10)

    log = {
        "phase": "control",
        "setpoint": float(setpoint),
        "data": data,
    }
    write_json(output_path, log)
    return log
```

---

## 6. Compute metrics exactly from the control log

**What to do:**  
Compute:
- rise time,
- overshoot,
- settling time,
- steady-state error,
- max temperature.

**Why:**  
Even if the controller looks good, validators may reject:
- missing metrics fields,
- different field names,
- wrong overshoot convention,
- incorrect settling logic,
- inconsistent units.

**Decision criteria:**
- Use temperature units in `degC`.
- Overshoot should be a fraction relative to setpoint step size unless the task explicitly defines otherwise.
- Settling should use a temperature band around the final setpoint, typically Â±0.5 Â°C when that matches the requirement.

**Expected JSON shape:**
```json
{
  "rise_time": 35.0,
  "overshoot": 0.05,
  "settling_time": 85.0,
  "steady_state_error": 0.15,
  "max_temp": 22.8
}
```

### Metrics computation

```python
#!/usr/bin/env python3
import json
from typing import Any, Dict, List

import numpy as np

def write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def compute_metrics(
    control_log_path: str = "control_log.json",
    output_path: str = "metrics.json",
    target_setpoint: float = 22.0,
    settling_band_c: float = 0.5,
) -> Dict[str, float]:
    log = load_json(control_log_path)
    if log.get("phase") != "control":
        raise ValueError("Control log has wrong phase")

    data: List[Dict[str, Any]] = log["data"]
    if len(data) < 2:
        raise ValueError("Control log must contain at least 2 points")

    t = np.array([float(row["time"]) for row in data], dtype=float)
    y = np.array([float(row["temperature"]) for row in data], dtype=float)

    y0 = float(y[0])
    yf = float(target_setpoint)
    step_mag = max(abs(yf - y0), 1e-9)

    # Rise time: first time reaching 90% of total step.
    rise_threshold = y0 + 0.9 * (yf - y0)
    if yf >= y0:
        idx_rise = np.where(y >= rise_threshold)[0]
    else:
        idx_rise = np.where(y <= rise_threshold)[0]
    rise_time = float(t[idx_rise[0]]) if len(idx_rise) else float(t[-1])

    # Overshoot as fractional excess above target normalized by step magnitude.
    peak = float(np.max(y)) if yf >= y0 else float(np.min(y))
    if yf >= y0:
        overshoot_abs = max(0.0, peak - yf)
    else:
        overshoot_abs = max(0.0, yf - peak)
    overshoot = overshoot_abs / step_mag

    # Settling time: first time after which response remains within band.
    within = np.abs(y - yf) <= settling_band_c
    settling_time = float(t[-1])
    for i in range(len(t)):
        if np.all(within[i:]):
            settling_time = float(t[i])
            break

    # Steady-state error: average absolute error over the final 10% of samples.
    tail_n = max(5, len(y) // 10)
    steady_state_error = float(np.mean(np.abs(y[-tail_n:] - yf)))

    metrics = {
        "rise_time": round(rise_time, 4),
        "overshoot": round(float(overshoot), 4),
        "settling_time": round(settling_time, 4),
        "steady_state_error": round(steady_state_error, 4),
        "max_temp": round(float(np.max(y)), 4),
    }
    write_json(output_path, metrics)
    return metrics
```

---

## 7. Build one end-to-end pipeline script

**What to do:**  
Combine simulator loading, calibration, estimation, tuning, control, and metrics in one reproducible script.

**Why:**  
This reduces mismatch between artifacts and ensures the final run regenerates all files consistently.

### End-to-end pipeline template

```python
#!/usr/bin/env python3
import importlib.util
import json
import os
import sys

from typing import Any

# Assume the helper functions from earlier blocks are copied into this file:
# - run_calibration
# - estimate_parameters
# - tune_pi_from_first_order
# - run_control
# - compute_metrics

def load_module(path: str):
    spec = importlib.util.spec_from_file_location("hvac_simulator", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load simulator from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def instantiate_simulator(module) -> Any:
    # Adapt this logic to the actual simulator source after inspection.
    for cls_name in ("HVACSimulator", "Simulator", "RoomSimulator"):
        if hasattr(module, cls_name):
            cls = getattr(module, cls_name)
            return cls()
    if hasattr(module, "create_simulator"):
        return module.create_simulator()
    raise RuntimeError("Could not instantiate simulator; inspect hvac_simulator.py")

def main():
    sim_path = sys.argv[1] if len(sys.argv) > 1 else "/root/hvac_simulator.py"
    module = load_module(sim_path)
    sim = instantiate_simulator(module)

    run_calibration(
        sim,
        duration_s=30.0,
        dt=0.5,
        heater_power_test=50.0,
        output_path="calibration_log.json",
    )

    estimate_parameters(
        calibration_path="calibration_log.json",
        output_path="estimated_params.json",
    )

    tune_pi_from_first_order(
        estimated_params_path="estimated_params.json",
        output_path="tuned_gains.json",
    )

    # Fresh simulator for closed-loop run.
    sim = instantiate_simulator(module)
    run_control(
        sim,
        gains_path="tuned_gains.json",
        output_path="control_log.json",
        setpoint=22.0,
        duration_s=180.0,
        dt=0.5,
        ramp_duration=30.0,
    )

    compute_metrics(
        control_log_path="control_log.json",
        output_path="metrics.json",
        target_setpoint=22.0,
        settling_band_c=0.5,
    )

    print("Generated:")
    for name in [
        "calibration_log.json",
        "estimated_params.json",
        "tuned_gains.json",
        "control_log.json",
        "metrics.json",
    ]:
        print(" -", os.path.abspath(name))

if __name__ == "__main__":
    main()
```

---

# Verification Checklist Before Finalizing

Always verify the artifacts **after regeneration**, not just by reading code.

## Required file existence and schema

```bash
#!/usr/bin/env bash
set -euo pipefail

for f in calibration_log.json estimated_params.json tuned_gains.json control_log.json metrics.json; do
  [[ -f "$f" ]] || { echo "Missing $f" >&2; exit 1; }
done

python3 - <<'PY'
import json

required = {
    "calibration_log.json": ["phase", "heater_power_test", "data"],
    "estimated_params.json": ["K", "tau", "r_squared", "fitting_error"],
    "tuned_gains.json": ["Kp", "Ki", "Kd", "lambda"],
    "control_log.json": ["phase", "setpoint", "data"],
    "metrics.json": ["rise_time", "overshoot", "settling_time", "steady_state_error", "max_temp"],
}

for path, keys in required.items():
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    missing = [k for k in keys if k not in obj]
    if missing:
        raise SystemExit(f"{path} missing keys: {missing}")

print("Schema check passed")
PY
```

## Duration, sample count, and performance checks

```bash
#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import json

with open("calibration_log.json") as f:
    cal = json.load(f)
with open("control_log.json") as f:
    ctl = json.load(f)
with open("metrics.json") as f:
    met = json.load(f)

assert len(cal["data"]) >= 21, "Calibration needs 20+ data points"
assert cal["data"][-1]["time"] >= 30.0, "Calibration must last at least 30s"

assert len(ctl["data"]) >= 2, "Control log too short"
assert ctl["data"][-1]["time"] >= 150.0, "Control duration must be at least 150s"

assert met["steady_state_error"] < 0.5, "steady_state_error too high"
assert met["settling_time"] < 120.0, "settling_time too high"
assert met["overshoot"] < 0.10, "overshoot too high"
assert met["max_temp"] < 30.0, "max_temp safety violation"

print("Duration and performance checks passed")
PY
```

---

# Domain Conventions and Practical Notes

## Units and coordinate conventions
- Temperature: **degrees Celsius**
- Heater command: **percent power** in `[0, 100]`
- Time: **seconds**
- `K` in `estimated_params.json`: thermal gain in **degC per percent heater**
- `tau`: thermal time constant in **seconds**

## Logging conventions
- Time should be monotonically nondecreasing.
- Use numeric JSON values, not strings.
- Round values for readability, but preserve enough precision for validators.
- Keep the top-level `phase` field exact:
  - `"calibration"`
  - `"control"`

## Control design conventions
- Prefer **PI** over full PID unless derivative is clearly needed.
- Use **anti-windup** whenever actuator saturation is possible.
- Use a **setpoint ramp** if overshoot is close to the limit.
- Run the controller longer than the bare minimum to make metrics stable.

---

# Common Pitfalls

## 1. Estimating parameters from hidden constants instead of calibration data
This is a frequent failure mode. Even if the simulator source exposes `K` and `tau`, the task often expects the values in `estimated_params.json` to be derived from the **measured calibration response**.

**Avoid:**  
- Copying internal simulator constants directly into `estimated_params.json`
- Using formulas inconsistent with the logged step response

**Do instead:**  
Estimate `K` and `tau` from `calibration_log.json` using the first-order step response.

---

## 2. Tweaking tuning logic when the actual problem is the estimator
If only `estimated_params.json` fails validation, do **not** randomly retune the controller first.

**Avoid:**  
- changing control logic, metrics logic, and calibration structure all at once
- broad rewrites when only estimation fields are wrong

**Do instead:**  
Inspect the generated `estimated_params.json` and align only the estimation method to the calibration convention.

---

## 3. Over-aggressive gains causing overshoot and delayed settling
Thermal rooms are slow. Aggressive gains often:
- overshoot the setpoint,
- break the <10% overshoot requirement,
- make settling exceed the time limit.

**Avoid:**  
- very small `lambda`
- derivative-heavy control in noisy environments
- integral action without anti-windup

**Do instead:**  
Use conservative PI tuning and, if needed, lengthen `lambda` or add a setpoint ramp.

---

## 4. Forgetting minimum calibration duration or point count
A short calibration run can fail even if control later looks good.

**Avoid:**  
- exactly minimal sample counts with no margin
- large `dt` that yields too few points

**Do instead:**  
Use something like:
- `duration = 30.0 s`
- `dt = 0.5 s`
which gives 61 samples including the initial point.

---

## 5. Forgetting minimum control duration
Some solutions hit the setpoint quickly but still fail because the log duration is too short.

**Avoid:**  
Stopping the control run right after settling.

**Do instead:**  
Run for a comfortable margin, e.g. `180 s`, unless the task says otherwise.

---

## 6. Wrong metrics definitions
Even a good controller can fail if metrics are computed with the wrong convention.

**Avoid:**  
- reporting overshoot in degrees when the validator expects a fraction
- computing steady-state error from a single noisy final sample
- using inconsistent settling bands

**Do instead:**  
- normalize overshoot by the setpoint step magnitude
- average steady-state error over the final segment
- use the task's tolerance band, commonly Â±0.5 Â°C

---

## 7. Missing or mismatched JSON field names
Validation often checks exact field names and structure.

**Avoid:**  
- renaming `heater_power_test` to `test_power`
- renaming `fitting_error` to `rmse`
- nesting metrics under extra objects

**Do instead:**  
Match the required schema exactly.

---

# Quick Strategy Summary

1. Inspect the simulator and runner first.
2. Run a moderate open-loop heater step for at least 30 seconds.
3. Estimate `K` and `tau` **from the calibration log**.
4. Tune a conservative PI controller with `lambda >= tau`.
5. Add anti-windup and a setpoint ramp if overshoot is tight.
6. Run control for at least 150 seconds, preferably with margin.
7. Compute metrics carefully and verify all artifacts before finishing.

This strategy is robust for first-order HVAC simulator tasks and tends to satisfy both performance and schema validators with minimal unnecessary complexity.