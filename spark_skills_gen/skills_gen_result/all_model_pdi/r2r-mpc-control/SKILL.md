---
title: "Roll-to-Roll MPC/LQR Controller Design for Web Tension Regulation"
category: "r2r-mpc-control"
domain: "control-systems"
tags: ["MPC", "LQR", "roll-to-roll", "web-tension", "linearization", "gain-scheduling", "discrete-control"]
dependencies: ["numpy", "scipy"]
author: "agent"
date: "2025-01-01"
version: "1.0"
---

# Roll-to-Roll MPC/LQR Controller for Web Tension Regulation

## Overview

This skill covers designing a discrete-time LQR/MPC controller for a multi-section Roll-to-Roll (R2R) web handling system. The plant has `N` sections (typically 6), each with a web tension state `T_i` and a roller velocity state `v_i`, giving a 12-dimensional state vector `x = [T1..T6, v1..v6]` and 6 control inputs `u = [u1..u6]` (motor torques). The controller must regulate tensions to reference values, including tracking a step change in one section's reference tension at a known time.

The task requires:
1. Deriving the linearized discrete-time state-space model `(A, B)` from the simulator's nonlinear dynamics.
2. Designing an LQR controller (or MPC with LQR terminal cost).
3. Running the closed-loop simulation for ≥5 seconds.
4. Computing and saving performance metrics.

## High-Level Workflow

### Step 1: Read and Understand the Simulator

Read `r2r_simulator.py` and `system_config.json` thoroughly before writing any code. You need to extract:

- **State ordering**: Typically `x = [T1, T2, ..., T6, v1, v2, ..., v6]`.
- **Dynamics equations**: The simulator's `step()` method contains the nonlinear ODEs. You need the exact formulas for `dT_i/dt` and `dv_i/dt`.
- **Parameters**: Roller radii `r_i`, inertias `J_i`, web cross-section area `A`, elastic modulus `E`, span lengths `L_i`, damping `b_i`, and the timestep `dt`.
- **Reference tensions and velocities**: From `system_config.json`, including which section changes and when.
- **Safety bounds**: `max_safe_tension` and `min_safe_tension` from config.

**Why this matters**: The linearization must match the simulator's dynamics exactly. Even small discrepancies (wrong sign, missing term, different parameter name) cause the LQR gain to be wrong, leading to instability or poor tracking.

```python
import json
import numpy as np

with open("system_config.json", "r") as f:
    config = json.load(f)

num_sections = config["num_sections"]  # typically 6
dt = config["dt"]                      # e.g., 0.01
sim_time = config.get("sim_time", 6.0)
T_refs_initial = config["initial_tensions"]   # e.g., [28, 36, 20, 40, 24, 32]
T_refs_final = config["final_tensions"]       # e.g., [28, 36, 44, 40, 24, 32]
v_ref = config["reference_velocity"]          # e.g., 1.0
step_time = config["step_time"]               # e.g., 0.5

# Physical parameters (names may vary — READ the simulator)
E = config["E"]          # elastic modulus
A_cross = config["A"]    # cross-section area
L = config["L"]          # list of span lengths
r = config["r"]          # list of roller radii
J = config["J"]          # list of roller inertias
b = config["b"]          # list of damping coefficients
```

### Step 2: Derive the Linearized State-Space Model

The typical R2R tension dynamics for section `i` are:

```
dT_i/dt = (E * A / L_i) * (v_{i+1} - v_i) - (v_i / L_i) * T_i
```

And the velocity dynamics for roller `i`:

```
dv_i/dt = (r_i / J_i) * (T_i - T_{i-1}) - (b_i / J_i) * v_i + (r_i / J_i) * u_i
```

(Boundary conditions: `T_0 = 0` for the first roller, `v_{N+1}` may be fixed or have special handling.)

**Critical**: Read the simulator's actual equations. The formulas above are the standard form but the simulator may use slightly different conventions (e.g., `T_{i+1} - T_i` vs `T_i - T_{i-1}`, different indexing for which velocity drives which tension).

Linearize by computing Jacobians analytically at the reference operating point:

```python
def compute_linearized_matrices(T_ref, v_ref_val, config):
    """
    Compute continuous-time A_c, B_c matrices by analytically differentiating
    the R2R dynamics at the reference operating point.
    
    State: x = [T1..T6, v1..v6]  (12 states)
    Input: u = [u1..u6]           (6 inputs)
    """
    n = config["num_sections"]
    nx = 2 * n  # 12
    nu = n      # 6
    
    E_val = config["E"]
    A_val = config["A"]
    L_arr = config["L"]
    r_arr = config["r"]
    J_arr = config["J"]
    b_arr = config["b"]
    
    A_c = np.zeros((nx, nx))
    B_c = np.zeros((nx, nu))
    
    # Tension dynamics: dT_i/dt = (EA/L_i)(v_{i+1} - v_i) - (v_i/L_i)*T_i
    # Partial derivatives at operating point (T_ref_i, v_ref):
    for i in range(n):
        ti = i          # index of T_i in state vector
        vi = n + i      # index of v_i in state vector
        
        # dT_i/dT_i = -v_ref / L_i
        A_c[ti, ti] = -v_ref_val / L_arr[i]
        
        # dT_i/dv_i = -EA/L_i - T_ref_i/L_i
        A_c[ti, vi] = -E_val * A_val / L_arr[i] - T_ref[i] / L_arr[i]
        
        # dT_i/dv_{i+1} = EA/L_i  (if i+1 < n)
        if i + 1 < n:
            A_c[ti, n + i + 1] = E_val * A_val / L_arr[i]
        # else: v_{n} might be a fixed boundary — check simulator
    
    # Velocity dynamics: dv_i/dt = (r_i/J_i)(T_i - T_{i-1}) - (b_i/J_i)*v_i + (r_i/J_i)*u_i
    for i in range(n):
        vi = n + i
        
        # dv_i/dT_i = r_i / J_i
        A_c[vi, i] = r_arr[i] / J_arr[i]
        
        # dv_i/dT_{i-1} = -r_i / J_i  (if i > 0)
        if i > 0:
            A_c[vi, i - 1] = -r_arr[i] / J_arr[i]
        
        # dv_i/dv_i = -b_i / J_i
        A_c[vi, vi] = -b_arr[i] / J_arr[i]
        
        # dv_i/du_i = r_i / J_i
        B_c[vi, i] = r_arr[i] / J_arr[i]
    
    return A_c, B_c
```

### Step 3: Discretize the System

Use Euler discretization (matching the simulator's integration method):

```python
def discretize_euler(A_c, B_c, dt):
    """Forward Euler: A_d = I + dt*A_c, B_d = dt*B_c"""
    nx = A_c.shape[0]
    A_d = np.eye(nx) + dt * A_c
    B_d = dt * B_c
    return A_d, B_d
```

**Why Euler and not ZOH**: The simulator uses Euler integration internally. If you use `scipy.signal.cont2discrete` with ZOH, the `A` and `B` matrices will NOT match the simulator's actual discrete dynamics, and the test's linearization correctness check will fail. Always match the simulator's integration method.

### Step 4: Compute LQR Gain via DARE

```python
from scipy.linalg import solve_discrete_are

def compute_lqr_gain(A_d, B_d, Q, R):
    """
    Solve the Discrete Algebraic Riccati Equation and return the LQR gain K.
    Control law: u = -K @ (x - x_ref) + u_ref
    """
    P = solve_discrete_are(A_d, B_d, Q, R)
    K = np.linalg.solve(R + B_d.T @ P @ B_d, B_d.T @ P @ A_d)
    return K, P
```

**Tuning Q and R**:
- Weight tension states heavily: `Q_T = 100.0` per tension state.
- Weight velocity states lightly: `Q_v = 0.1` per velocity state.
- Moderate control penalty: `R_i = 1.0` per input.
- These values provide good tracking without excessive control effort.

```python
nx = 2 * num_sections
nu = num_sections

Q = np.diag([100.0] * num_sections + [0.1] * num_sections)
R = np.diag([1.0] * num_sections)

K_init, _ = compute_lqr_gain(A_d_init, B_d_init, Q, R)
K_final, _ = compute_lqr_gain(A_d_final, B_d_final, Q, R)
```

### Step 5: Compute Equilibrium (Feedforward) Inputs

At steady state, `x_{k+1} = x_k = x_ref`, so:

```
x_ref = A_d @ x_ref + B_d @ u_ref
=> (I - A_d) @ x_ref = B_d @ u_ref
=> u_ref = pinv(B_d) @ (I - A_d) @ x_ref
```

Or solve from the continuous-time equilibrium: `0 = A_c @ x_ref + B_c @ u_ref` → `u_ref = -pinv(B_c) @ (A_c @ x_ref)`.

```python
def compute_equilibrium_input(A_c, B_c, x_ref):
    """Compute feedforward input u_ref such that A_c @ x_ref + B_c @ u_ref = 0"""
    rhs = -A_c @ x_ref
    u_ref, _, _, _ = np.linalg.lstsq(B_c, rhs, rcond=None)
    return u_ref
```

### Step 6: Gain-Scheduled Control Loop

The key insight: compute separate LQR gains and feedforward inputs for the initial and final operating points. Switch at the step time.

```python
def run_control_loop(sim, config, K_init, K_final, u_ref_init, u_ref_final,
                     x_ref_init, x_ref_final, step_time):
    """
    Run the closed-loop simulation with gain-scheduled LQR.
    """
    dt = config["dt"]
    total_time = max(config.get("sim_time", 6.0), 5.5)
    num_steps = int(total_time / dt)
    
    log_data = []
    
    for step_idx in range(num_steps):
        t = (step_idx + 1) * dt
        
        # Get current state from simulator
        tensions = sim.get_tensions()      # array of 6
        velocities = sim.get_velocities()  # array of 6
        x = np.concatenate([tensions, velocities])
        
        # Select operating point based on time
        if t < step_time:
            K = K_init
            x_ref = x_ref_init
            u_ref = u_ref_init
        else:
            K = K_final
            x_ref = x_ref_final
            u_ref = u_ref_final
        
        # LQR control law: u = u_ref - K @ (x - x_ref)
        dx = x - x_ref
        u = u_ref - K @ dx
        
        # Apply control and step simulator
        sim.step(u)
        
        # Log
        log_data.append({
            "time": round(t, 4),
            "tensions": tensions.tolist(),
            "velocities": velocities.tolist(),
            "control_inputs": u.tolist(),
            "references": x_ref.tolist()
        })
    
    return log_data
```

### Step 7: Compute Performance Metrics

The test verifier recomputes metrics from `control_log.json` and checks them against `metrics.json`. Your saved metrics must be consistent.

```python
def compute_metrics(log_data, x_ref_final, step_time, dt, settling_threshold=2.0):
    """
    Compute performance metrics from logged data.
    
    - steady_state_error: mean absolute tension error in last 1 second
    - settling_time: time after step when all tensions stay within threshold of reference
    - max_tension / min_tension: global extremes across entire run
    """
    T_ref = np.array(x_ref_final[:6])  # final reference tensions (after step)
    
    all_tensions = np.array([d["tensions"] for d in log_data])
    all_times = np.array([d["time"] for d in log_data])
    
    # Steady-state error: mean over last 1 second
    last_second_mask = all_times >= (all_times[-1] - 1.0)
    last_tensions = all_tensions[last_second_mask]
    sse = np.mean(np.abs(last_tensions - T_ref))
    
    # Settling time: find last time any tension is outside threshold of reference
    # (measured from step_time)
    errors = np.abs(all_tensions - T_ref)
    max_errors = np.max(errors, axis=1)  # max across sections at each timestep
    
    post_step_mask = all_times >= step_time
    post_step_times = all_times[post_step_mask]
    post_step_errors = max_errors[post_step_mask]
    
    settled_mask = post_step_errors < settling_threshold
    if np.all(settled_mask):
        settling_time = 0.0
    elif np.all(~settled_mask):
        settling_time = post_step_times[-1] - step_time
    else:
        # Last time it was NOT settled
        last_unsettled_idx = np.where(~settled_mask)[0][-1]
        if last_unsettled_idx + 1 < len(post_step_times):
            settling_time = post_step_times[last_unsettled_idx + 1] - step_time
        else:
            settling_time = post_step_times[-1] - step_time
    
    max_tension = float(np.max(all_tensions))
    min_tension = float(np.min(all_tensions))
    
    return {
        "steady_state_error": round(float(sse), 4),
        "settling_time": round(float(settling_time), 4),
        "max_tension": round(max_tension, 4),
        "min_tension": round(min_tension, 4)
    }
```

### Step 8: Save Output Files

Three JSON files are required:

```python
def save_outputs(A_d, B_d, K, Q, R, horizon_N, log_data, metrics):
    # controller_params.json
    params = {
        "horizon_N": horizon_N,
        "Q_diag": np.diag(Q).tolist(),
        "R_diag": np.diag(R).tolist(),
        "K_lqr": K.tolist(),
        "A_matrix": A_d.tolist(),
        "B_matrix": B_d.tolist()
    }
    with open("controller_params.json", "w") as f:
        json.dump(params, f, indent=2)
    
    # control_log.json
    control_log = {
        "phase": "control",
        "data": log_data
    }
    with open("control_log.json", "w") as f:
        json.dump(control_log, f, indent=2)
    
    # metrics.json
    with open("metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
```

## Common Pitfalls

### 1. Linearization Mismatch (Most Critical)

The test includes a `test_linearization_correctness` check that computes the ground-truth `A` and `B` matrices by numerically differentiating the simulator's dynamics and compares them to your `A_matrix` and `B_matrix`. If you use ZOH discretization instead of Euler, or if your analytical Jacobian has a sign error or missing term, this test fails.

**Fix**: Always use Euler discretization (`A_d = I + dt*A_c`, `B_d = dt*B_c`). Double-check every partial derivative against the simulator's actual code.

### 2. Integral Action / Filters Causing Instability

Adding integral action or low-pass filters to the LQR controller often causes instability in this simulator. The pure LQR with gain scheduling is sufficient and much more robust.

**Fix**: Start with pure LQR. Only add complexity if the basic controller doesn't meet performance targets.

### 3. Wrong Reference State Construction

The reference state must include both tensions AND velocities: `x_ref = [T1_ref, ..., T6_ref, v_ref, ..., v_ref]`. Forgetting to include velocity references or using zeros for velocities causes large errors.

### 4. Forgetting to Switch Feedforward Input

When the reference changes at `step_time`, you must also switch the feedforward input `u_ref`. Using the initial `u_ref` with the final `x_ref` creates a persistent offset that the LQR must fight against, degrading steady-state error.

### 5. Metrics Computation Inconsistency

The test verifier recomputes metrics from your `control_log.json`. If your `metrics.json` values don't match what the verifier computes, the test fails. Always compute metrics from the same logged data you save.

### 6. Simulation Duration Too Short

The log must span at least 5 seconds. Run for 5.5–6.0 seconds to have margin. The test checks `data[-1]["time"] - data[0]["time"] >= 5.0`.

### 7. Aggressive Q/R Tuning

Very large Q values (e.g., 10000) with very small R values (e.g., 0.001) can produce enormous LQR gains that cause numerical issues or actuator saturation. Start with `Q_T=100, Q_v=0.1, R=1.0` — this is a well-tested starting point.

### 8. Not Reading the Simulator's API

The simulator may use specific method names (`step()`, `get_state()`, `reset()`, etc.) and may return states in a specific format (separate arrays vs. single vector). Read the simulator code to understand the exact API before writing the controller.

### 9. Horizon_N Out of Range

The `horizon_N` parameter must be in `[3, 30]`. Use 10 as a safe default.

## Reference Implementation

This is the complete, end-to-end solution. Copy, adapt parameter names to match your simulator, and run.

```python
#!/usr/bin/env python3
"""
Complete R2R MPC/LQR Controller Implementation
================================================
Gain-scheduled LQR controller for a 6-section Roll-to-Roll web handling system.
Handles a step change in one section's reference tension at a known time.

Usage: python3 run_controller.py
Requires: r2r_simulator.py and system_config.json in the working directory.
Outputs: controller_params.json, control_log.json, metrics.json
"""

import json
import numpy as np
from scipy.linalg import solve_discrete_are

# ─────────────────────────────────────────────
# 1. Load configuration
# ─────────────────────────────────────────────
with open("system_config.json", "r") as f:
    config = json.load(f)

num_sections = config["num_sections"]
dt = config["dt"]
sim_time = config.get("sim_time", 6.0)
if sim_time < 5.5:
    sim_time = 6.0  # ensure at least 5s of data

step_time = config["step_time"]
T_ref_init = np.array(config["initial_tensions"], dtype=float)
T_ref_final = np.array(config["final_tensions"], dtype=float)
v_ref_val = float(config["reference_velocity"])

E_val = float(config["E"])
A_cross = float(config["A"])
L_arr = np.array(config["L"], dtype=float)
r_arr = np.array(config["r"], dtype=float)
J_arr = np.array(config["J"], dtype=float)
b_arr = np.array(config["b"], dtype=float)

nx = 2 * num_sections  # 12
nu = num_sections       # 6

# ─────────────────────────────────────────────
# 2. Build reference states
# ─────────────────────────────────────────────
v_refs = np.full(num_sections, v_ref_val)
x_ref_init = np.concatenate([T_ref_init, v_refs])
x_ref_final = np.concatenate([T_ref_final, v_refs])

# ─────────────────────────────────────────────
# 3. Linearize at an operating point
# ─────────────────────────────────────────────
def linearize_continuous(T_ref, v_ref_scalar):
    """
    Compute continuous-time Jacobians A_c, B_c at operating point.
    
    Tension dynamics (section i, 0-indexed):
        dT_i/dt = (EA/L_i) * (v_{i+1} - v_i) - (v_i / L_i) * T_i
    
    Velocity dynamics (roller i, 0-indexed):
        dv_i/dt = (r_i/J_i) * (T_i - T_{i-1}) - (b_i/J_i) * v_i + (r_i/J_i) * u_i
    
    NOTE: Adapt these equations to match YOUR simulator exactly.
    Read r2r_simulator.py and verify every term.
    """
    n = num_sections
    A_c = np.zeros((nx, nx))
    B_c = np.zeros((nx, nu))
    
    for i in range(n):
        ti = i       # tension state index
        vi = n + i   # velocity state index
        
        # --- Tension row ---
        # dT_i/dT_i
        A_c[ti, ti] = -v_ref_scalar / L_arr[i]
        
        # dT_i/dv_i
        A_c[ti, vi] = -E_val * A_cross / L_arr[i] - T_ref[i] / L_arr[i]
        
        # dT_i/dv_{i+1}
        if i + 1 < n:
            A_c[ti, n + i + 1] = E_val * A_cross / L_arr[i]
        # If i+1 == n, check if simulator has a boundary velocity term
        
        # --- Velocity row ---
        # dv_i/dT_i
        A_c[vi, i] = r_arr[i] / J_arr[i]
        
        # dv_i/dT_{i-1}
        if i > 0:
            A_c[vi, i - 1] = -r_arr[i] / J_arr[i]
        
        # dv_i/dv_i
        A_c[vi, vi] = -b_arr[i] / J_arr[i]
        
        # --- Input ---
        # dv_i/du_i
        B_c[vi, i] = r_arr[i] / J_arr[i]
    
    return A_c, B_c


def discretize_euler(A_c, B_c, dt_val):
    """Forward Euler discretization to match the simulator's integration."""
    A_d = np.eye(nx) + dt_val * A_c
    B_d = dt_val * B_c
    return A_d, B_d


def compute_equilibrium_input(A_c, B_c, x_ref):
    """Solve for u_ref such that A_c @ x_ref + B_c @ u_ref = 0."""
    rhs = -A_c @ x_ref
    u_ref, _, _, _ = np.linalg.lstsq(B_c, rhs, rcond=None)
    return u_ref


def compute_lqr_gain(A_d, B_d, Q, R):
    """Solve DARE and return LQR gain K."""
    P = solve_discrete_are(A_d, B_d, Q, R)
    K = np.linalg.solve(R + B_d.T @ P @ B_d, B_d.T @ P @ A_d)
    return K

# ─────────────────────────────────────────────
# 4. Compute gains for both operating points
# ─────────────────────────────────────────────
# Tuning: tension states weighted heavily, velocity states lightly
Q = np.diag([100.0] * num_sections + [0.1] * num_sections)
R = np.diag([1.0] * num_sections)

# Initial operating point
A_c_init, B_c_init = linearize_continuous(T_ref_init, v_ref_val)
A_d_init, B_d_init = discretize_euler(A_c_init, B_c_init, dt)
u_ref_init = compute_equilibrium_input(A_c_init, B_c_init, x_ref_init)
K_init = compute_lqr_gain(A_d_init, B_d_init, Q, R)

# Final operating point (after step change)
A_c_final, B_c_final = linearize_continuous(T_ref_final, v_ref_val)
A_d_final, B_d_final = discretize_euler(A_c_final, B_c_final, dt)
u_ref_final = compute_equilibrium_input(A_c_final, B_c_final, x_ref_final)
K_final = compute_lqr_gain(A_d_final, B_d_final, Q, R)

# Verify closed-loop stability
for label, A_d, B_d, K in [("init", A_d_init, B_d_init, K_init),
                             ("final", A_d_final, B_d_final, K_final)]:
    A_cl = A_d - B_d @ K
    eigs = np.abs(np.linalg.eigvals(A_cl))
    max_eig = np.max(eigs)
    print(f"Closed-loop max eigenvalue ({label}): {max_eig:.6f}")
    assert max_eig < 1.0, f"Unstable closed-loop system at {label} operating point!"

# ─────────────────────────────────────────────
# 5. Run closed-loop simulation
# ─────────────────────────────────────────────
# Import the simulator — adapt this to match the actual module/class name
from r2r_simulator import R2RSimulator  # or whatever the class is named

sim = R2RSimulator(config)  # adapt constructor arguments as needed

num_steps = int(sim_time / dt)
log_data = []

for step_idx in range(num_steps):
    t = round((step_idx + 1) * dt, 6)
    
    # Read current state — adapt method names to match simulator API
    tensions = np.array(sim.get_tensions(), dtype=float)
    velocities = np.array(sim.get_velocities(), dtype=float)
    x = np.concatenate([tensions, velocities])
    
    # Gain scheduling: switch at step_time
    if t < step_time:
        K = K_init
        x_ref = x_ref_init
        u_ref = u_ref_init
    else:
        K = K_final
        x_ref = x_ref_final
        u_ref = u_ref_final
    
    # LQR control law
    dx = x - x_ref
    u = u_ref - K @ dx
    
    # Step the simulator
    sim.step(u)
    
    # Log entry
    log_data.append({
        "time": round(t, 4),
        "tensions": tensions.tolist(),
        "velocities": velocities.tolist(),
        "control_inputs": u.tolist(),
        "references": x_ref.tolist()
    })

print(f"Simulation complete: {len(log_data)} steps, "
      f"duration {log_data[-1]['time'] - log_data[0]['time']:.2f}s")

# ─────────────────────────────────────────────
# 6. Compute performance metrics
# ─────────────────────────────────────────────
all_tensions = np.array([d["tensions"] for d in log_data])
all_times = np.array([d["time"] for d in log_data])

# Steady-state error: mean absolute error in last 1 second
last_second_mask = all_times >= (all_times[-1] - 1.0)
last_tensions = all_tensions[last_second_mask]
T_ref_target = T_ref_final  # final reference is the target
sse = float(np.mean(np.abs(last_tensions - T_ref_target)))

# Settling time: time after step when max tension error stays below threshold
settling_threshold = 2.0
errors = np.abs(all_tensions - T_ref_target)
max_errors_per_step = np.max(errors, axis=1)

post_step_mask = all_times >= step_time
post_step_times = all_times[post_step_mask]
post_step_errors = max_errors_per_step[post_step_mask]

settled = post_step_errors < settling_threshold
if np.all(settled):
    settling_time = 0.0
elif np.all(~settled):
    settling_time = float(post_step_times[-1] - step_time)
else:
    last_unsettled = np.where(~settled)[0][-1]
    if last_unsettled + 1 < len(post_step_times):
        settling_time = float(post_step_times[last_unsettled + 1] - step_time)
    else:
        settling_time = float(post_step_times[-1] - step_time)

max_tension = float(np.max(all_tensions))
min_tension = float(np.min(all_tensions))

metrics = {
    "steady_state