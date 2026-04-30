---
name: r2r-mpc-control
description: Derive an exact discrete linearized model from a roll-to-roll simulator, design an MPC-style controller with strong tracking performance, run closed-loop simulation, and export validator-ready JSON artifacts.
version: 1.0.0
category: control
tags:
  - mpc
  - lqr
  - model-linearization
  - state-space
  - roll-to-roll
  - simulator-integration
  - json-outputs
tools:
  - python3
  - numpy
  - scipy
  - rg
  - sed
dependencies:
  - numpy==1.26.4
  - scipy==1.13.0
---

# R2R MPC Control Skill

This skill covers tasks where you must:

1. inspect a provided roll-to-roll simulator,
2. derive the **exact linearized discrete-time model** around an operating point,
3. design an MPC or MPC-like tracking controller,
4. run the controller on the **original simulator without modifying it**,
5. export required JSON outputs with the exact schema expected by validators.

This domain is unforgiving in two places:

- **Linearization correctness**: validators often compare your exported `A_matrix` and `B_matrix` against the simulator equations.
- **Performance**: safe but weak linear feedback may produce valid files yet fail transient metrics.

The most reliable pattern is:

- derive the model directly from the simulator equations,
- verify the Jacobians numerically before trusting them,
- use a **nominal trajectory on the nonlinear plant** for the commanded transition,
- add **time-varying or strong local linear tracking feedback** around that nominal trajectory,
- export all required files in the exact schema.

---

# When to Use This Skill

Use this skill when the task involves:

- a state vector composed of tensions and roller velocities,
- a change in tension reference at a known time,
- a provided simulator file such as `r2r_simulator.py`,
- a required `controller_params.json`, `control_log.json`, and `metrics.json`,
- constraints that prohibit changing the simulator implementation.

Typical state convention in these tasks:

- `x = [T1..T6, v1..v6]` â 12 states
- `u = [u1..u6]` â 6 motor torques
- logged tensions are in **Newtons**
- logged velocities are usually in **m/s** or simulator-native speed units
- time is in **seconds**
- control is discrete-time with simulator step `dt`

Always verify the state ordering used by the simulator. A common hidden failure is exporting `A` and `B` in a different ordering than the simulator and tests expect.

---

# High-Level Workflow

## 1. Inspect the simulator and config before designing anything

**What to do:** read the simulator source and the configuration file completely.

**Why:** this task is not âgeneric MPC.â The validator usually expects the linearization of the **actual implemented simulator equations**, not an approximate textbook web-tension model.

**Decisions to make:**
- What is the exact state ordering?
- What is the exact input ordering?
- Is the simulator already discrete-time, or does it integrate a continuous model internally?
- What is the sample time `dt`?
- Where do the initial conditions and reference tensions come from?
- At what time does the step change occur?

**Use this shell workflow first:**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/root}"

rg --files "$ROOT_DIR" | rg 'r2r_simulator\.py|system_config\.json|mpc|AGENTS\.md|README|tests'
echo "---- simulator ----"
sed -n '1,260p' "$ROOT_DIR/r2r_simulator.py"
echo "---- config ----"
sed -n '1,220p' "$ROOT_DIR/system_config.json"
```

**Python helper to summarize config safely:**

```python
#!/usr/bin/env python3
import json
from pathlib import Path

def main():
    cfg_path = Path("/root/system_config.json")
    if not cfg_path.exists():
        raise FileNotFoundError(f"Missing config: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    print("Top-level keys:", sorted(cfg.keys()))
    for key in ["dt", "time_step", "initial_state", "reference_tensions", "tensions_ref"]:
        if key in cfg:
            print(f"{key}: {cfg[key]}")

if __name__ == "__main__":
    main()
```

**Critical note:** do not assume the control acts instantly on tension. In many roll-to-roll simulators, torque affects roller velocity, which then affects tension through coupled dynamics. That means the useful control effect on tension may be one-step delayed or effectively second-order.

---

## 2. Recover the exact state update map used by the simulator

**What to do:** identify or reconstruct a callable discrete map:

\[
x_{k+1} = f(x_k, u_k)
\]

using the simulator's real equations.

**Why:** `A_matrix` and `B_matrix` must reflect the simulator's implemented dynamics at the operating point. If you derive the wrong model, you may pass file-format checks but fail `test_linearization_correctness`.

**Decision criteria:**
- If the simulator already exposes a `step` function returning next state, wrap that.
- If internal state includes extra fields/noise/logging, isolate only the physical 12-state transition used in tests.
- If the simulator stores tensions and velocities separately, concatenate them in the exact expected order.

**Pattern for safely wrapping the simulator:**

```python
#!/usr/bin/env python3
import copy
import json
import numpy as np
from pathlib import Path

from r2r_simulator import R2RSimulator

class PlantWrapper:
    def __init__(self, config_path="/root/system_config.json"):
        self.config_path = str(config_path)
        self.sim = R2RSimulator(self.config_path)
        self.nx = 12
        self.nu = 6

    def reset(self):
        if hasattr(self.sim, "reset"):
            obs = self.sim.reset()
            return self._extract_state(obs)
        raise AttributeError("Simulator does not expose reset()")

    def _extract_state(self, obs=None):
        """
        Convert simulator observation/internal state into x = [T1..T6, v1..v6].
        Adapt this if simulator uses a different field layout.
        """
        # Preferred: observation dict
        if isinstance(obs, dict):
            tensions = obs.get("tensions")
            velocities = obs.get("velocities")
            if tensions is not None and velocities is not None:
                x = np.asarray(list(tensions) + list(velocities), dtype=float)
                if x.shape == (12,):
                    return x

        # Fallback: simulator internal arrays
        for t_name in ["tensions", "T", "web_tensions"]:
            for v_name in ["velocities", "v", "roller_velocities"]:
                if hasattr(self.sim, t_name) and hasattr(self.sim, v_name):
                    t = np.asarray(getattr(self.sim, t_name), dtype=float).reshape(-1)
                    v = np.asarray(getattr(self.sim, v_name), dtype=float).reshape(-1)
                    x = np.concatenate([t, v])
                    if x.shape == (12,):
                        return x

        raise RuntimeError("Unable to extract 12-state vector [T1..T6, v1..v6]")

    def step_from_state(self, x, u):
        """
        Compute x_next from a given state and input using the unmodified simulator.
        This often requires deep-copying the simulator and writing the internal state.
        """
        x = np.asarray(x, dtype=float).reshape(self.nx)
        u = np.asarray(u, dtype=float).reshape(self.nu)

        sim_copy = copy.deepcopy(self.sim)

        # Write state into the copied simulator.
        # Adapt field names to match the actual simulator.
        if hasattr(sim_copy, "tensions") and hasattr(sim_copy, "velocities"):
            sim_copy.tensions = x[:6].copy()
            sim_copy.velocities = x[6:].copy()
        elif hasattr(sim_copy, "T") and hasattr(sim_copy, "v"):
            sim_copy.T = x[:6].copy()
            sim_copy.v = x[6:].copy()
        else:
            raise RuntimeError("Simulator state fields not recognized")

        # Step the copied simulator
        if hasattr(sim_copy, "step"):
            obs = sim_copy.step(u)
            return self._extract_state(obs)

        raise AttributeError("Simulator does not expose step(u)")
```

**Critical note:** do not linearize an imagined continuous-time model unless the tests explicitly require that. Most validators expect the discrete transition used by the simulator.

---

## 3. Identify the operating point exactly

**What to do:** compute the linearization around the initial reference operating point, not around the post-step target unless explicitly required.

**Why:** tasks often say âderive the linearized state-space model at the initial reference operating point.â Hidden tests can check exactly that point.

**Decision criteria:**
- Initial state usually comes from `reset()` or config.
- Initial reference is typically the nominal pre-step tension/velocity operating point.
- If the simulator begins exactly at equilibrium, use that state and corresponding steady input.
- If the simulator provides nominal torques or a baseline input, use it. Otherwise solve for an input that satisfies \(f(x^\*, u^\*) \approx x^\*\).

**Code to estimate equilibrium input from simulator dynamics:**

```python
#!/usr/bin/env python3
import numpy as np
from scipy.optimize import least_squares

def find_equilibrium_input(plant, x_ref, u_init=None, lb=None, ub=None):
    x_ref = np.asarray(x_ref, dtype=float).reshape(12)
    if u_init is None:
        u_init = np.zeros(6, dtype=float)
    else:
        u_init = np.asarray(u_init, dtype=float).reshape(6)

    if lb is None:
        lb = -np.inf * np.ones(6)
    if ub is None:
        ub = np.inf * np.ones(6)

    def residual(u):
        x_next = plant.step_from_state(x_ref, u)
        return x_next - x_ref

    result = least_squares(residual, u_init, bounds=(lb, ub), xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=500)
    if not result.success:
        raise RuntimeError(f"Equilibrium solve failed: {result.message}")

    return result.x, np.linalg.norm(result.fun, ord=np.inf)
```

**Critical note:** if the simulator/config already contains a nominal operating torque, prefer that and only refine it if needed.

---

## 4. Compute exact local Jacobians `A` and `B`

**What to do:** linearize the discrete map numerically at \((x^\*, u^\*)\) using central differences.

**Why:** repeated failures in this domain come from approximate, symbolic, or incorrectly ordered linearization. Central differences against the actual simulator transition are robust and usually match the validator.

**Decision criteria:**
- Use small perturbations, but not so small that floating point noise dominates.
- Check consistency by comparing linear prediction against true next states for random local perturbations.
- Export matrices in the exact shapes:
  - `A_matrix`: 12x12
  - `B_matrix`: 12x6

**Reliable Jacobian implementation:**

```python
#!/usr/bin/env python3
import numpy as np

def linearize_discrete_dynamics(plant, x_star, u_star, eps_x=1e-6, eps_u=1e-6):
    x_star = np.asarray(x_star, dtype=float).reshape(12)
    u_star = np.asarray(u_star, dtype=float).reshape(6)

    nx, nu = 12, 6
    A = np.zeros((nx, nx), dtype=float)
    B = np.zeros((nx, nu), dtype=float)

    # State Jacobian
    for i in range(nx):
        dx = np.zeros(nx, dtype=float)
        dx[i] = eps_x
        xp = plant.step_from_state(x_star + dx, u_star)
        xm = plant.step_from_state(x_star - dx, u_star)
        A[:, i] = (xp - xm) / (2.0 * eps_x)

    # Input Jacobian
    for j in range(nu):
        du = np.zeros(nu, dtype=float)
        du[j] = eps_u
        xp = plant.step_from_state(x_star, u_star + du)
        xm = plant.step_from_state(x_star, u_star - du)
        B[:, j] = (xp - xm) / (2.0 * eps_u)

    return A, B

def verify_linearization(plant, x_star, u_star, A, B, trials=20, scale_x=1e-5, scale_u=1e-5, seed=0):
    rng = np.random.default_rng(seed)
    worst = 0.0

    f_star = plant.step_from_state(x_star, u_star)

    for _ in range(trials):
        dx = rng.normal(size=12) * scale_x
        du = rng.normal(size=6) * scale_u

        f_true = plant.step_from_state(x_star + dx, u_star + du)
        f_lin = f_star + A @ dx + B @ du
        err = np.max(np.abs(f_true - f_lin))
        worst = max(worst, err)

    return worst
```

**Acceptance guideline:** local max error should be extremely small for tiny perturbations. If it is not, suspect:
- wrong state ordering,
- wrong operating point,
- incorrect simulator state write-back,
- one-sided differences with poor step size,
- hidden internal states not included in your wrapper.

---

## 5. Build a controller that is stronger than a plain fixed LQR

**What to do:** use the exact linear model for local feedback, but rely on a **nominal trajectory on the nonlinear plant** for the actual reference transition.

**Why:** many failed attempts use a generic LQR or weak MPC directly around the initial equilibrium. That often stabilizes the system but does not meet settling-time or steady-state performance targets after a tension step.

**Recommended structure:**
1. compute a nominal open-loop state/input trajectory on the nonlinear plant from initial state to desired post-step operating region,
2. design tracking feedback around that trajectory,
3. run receding-horizon or trajectory-tracking control on the original simulator.

This still qualifies as MPC-style control if you optimize over a finite horizon and reapply only the first control or track a finite-horizon nominal.

**Decision criteria:**
- If writing a full constrained QP solver is overkill, a nominal-trajectory + TVLQR tracker is usually sufficient.
- If constraints are mild and the simulator is smooth, direct optimization with `scipy.optimize.minimize` or `least_squares` can work well.

---

## 6. Generate a nominal trajectory using the nonlinear simulator

**What to do:** optimize a sequence of control inputs that drives the system from the initial operating point to the target reference after the step event.

**Why:** the plant is coupled and may have delayed tension response. A nominal plan âteachesâ the controller how to move the system quickly without blind gain sweeps.

**Design pattern:**
- choose horizon length long enough to capture the transient,
- penalize terminal tracking error strongly,
- penalize control effort and rate smoothly,
- roll out using the actual nonlinear simulator transition,
- optionally hold the final input or blend to equilibrium torque.

**Example direct trajectory optimizer:**

```python
#!/usr/bin/env python3
import numpy as np
from scipy.optimize import minimize

class NominalOptimizer:
    def __init__(self, plant, dt):
        self.plant = plant
        self.dt = float(dt)
        self.nx = 12
        self.nu = 6

    def rollout(self, x0, U):
        x = np.asarray(x0, dtype=float).reshape(self.nx)
        U = np.asarray(U, dtype=float).reshape(-1, self.nu)
        X = [x.copy()]
        for k in range(U.shape[0]):
            x = self.plant.step_from_state(x, U[k])
            X.append(x.copy())
        return np.asarray(X), U

    def optimize(self, x0, x_goal, N=40, u_init=None, u_eq=None, effort_weight=1e-2, rate_weight=1e-1):
        x0 = np.asarray(x0, dtype=float).reshape(self.nx)
        x_goal = np.asarray(x_goal, dtype=float).reshape(self.nx)

        if u_eq is None:
            u_eq = np.zeros(self.nu, dtype=float)
        else:
            u_eq = np.asarray(u_eq, dtype=float).reshape(self.nu)

        if u_init is None:
            U0 = np.tile(u_eq, (N, 1))
        else:
            U0 = np.asarray(u_init, dtype=float).reshape(N, self.nu)

        # State cost: emphasize tensions more than velocities
        q = np.array([100, 100, 100, 100, 100, 100, 1, 1, 1, 1, 1, 1], dtype=float)
        qf = 20.0 * q
        r = effort_weight * np.ones(self.nu, dtype=float)
        rd = rate_weight * np.ones(self.nu, dtype=float)

        def unpack(z):
            return z.reshape(N, self.nu)

        def objective(z):
            U = unpack(z)
            X, _ = self.rollout(x0, U)
            cost = 0.0

            for k in range(N):
                e = X[k] - x_goal
                cost += np.dot(q * e, e)
                du = U[k] - u_eq
                cost += np.dot(r * du, du)
                if k > 0:
                    dU = U[k] - U[k - 1]
                    cost += np.dot(rd * dU, dU)

            eN = X[-1] - x_goal
            cost += np.dot(qf * eN, eN)
            return float(cost)

        result = minimize(
            objective,
            U0.reshape(-1),
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-12, "maxls": 50},
        )
        if not result.success:
            raise RuntimeError(f"Nominal optimization failed: {result.message}")

        U_opt = unpack(result.x)
        X_opt, _ = self.rollout(x0, U_opt)
        return X_opt, U_opt
```

**Critical note:** do not waste time on random gain sweeps if performance fails. Inspect the logged transient and optimize the planned tension transition directly.

---

## 7. Add local tracking feedback with LQR or TVLQR

**What to do:** stabilize tracking errors around the nominal trajectory using Riccati-based gains.

**Why:** the nominal plan alone is fragile; feedback is needed for disturbances, numerical mismatch, and simulator coupling.

**Decision criteria:**
- Use constant LQR around the initial operating point if the system is mild.
- Use time-varying LQR around the nominal trajectory if the transition is aggressive or highly nonlinear.
- Export `K_lqr` as a 6x12 gain matrix. If using TVLQR internally, export a representative local gain, usually the initial gain.

**Code for discrete LQR and TVLQR:**

```python
#!/usr/bin/env python3
import numpy as np
from scipy.linalg import solve_discrete_are

def dlqr(A, B, Q, R):
    A = np.asarray(A, dtype=float)
    B = np.asarray(B, dtype=float)
    Q = np.asarray(Q, dtype=float)
    R = np.asarray(R, dtype=float)

    P = solve_discrete_are(A, B, Q, R)
    M = R + B.T @ P @ B
    K = np.linalg.solve(M, B.T @ P @ A)
    return K, P

def tvlqr(plant, X_nom, U_nom, Q, R, Qf, eps_x=1e-6, eps_u=1e-6):
    X_nom = np.asarray(X_nom, dtype=float)
    U_nom = np.asarray(U_nom, dtype=float)
    N = U_nom.shape[0]
    nx, nu = 12, 6

    A_list = []
    B_list = []
    for k in range(N):
        A_k, B_k = linearize_discrete_dynamics(plant, X_nom[k], U_nom[k], eps_x=eps_x, eps_u=eps_u)
        A_list.append(A_k)
        B_list.append(B_k)

    P = np.asarray(Qf, dtype=float).copy()
    K_list = [None] * N

    for k in reversed(range(N)):
        A_k = A_list[k]
        B_k = B_list[k]
        S = R + B_k.T @ P @ B_k
        K_k = np.linalg.solve(S, B_k.T @ P @ A_k)
        K_list[k] = K_k
        P = Q + A_k.T @ P @ (A_k - B_k @ K_k)

    return K_list, A_list, B_list
```

**Suggested weighting pattern:**
- tension states: high weight
- velocity states: moderate or low weight
- torque effort: small positive weight, never zero
- torque rate: add regularization in nominal optimization

This pattern often works better than equal weighting because the validator scores tension quality directly.

---

## 8. Run the controller on the original simulator for at least 5 seconds

**What to do:** run a closed-loop simulation that logs time, tensions, velocities, torques, and references at every timestep.

**Why:** validators inspect the output schema and performance metrics. Logging must cover at least 5.0 seconds.

**Decision criteria:**
- Use the simulator's own `dt`.
- If the step change happens at `t = 0.5`, update references exactly at or after that time.
- Ensure the final logged time is at least 5.0 seconds.
- Clamp or regularize control inputs only if the simulator/config implies limits.

**Robust logging controller loop:**

```python
#!/usr/bin/env python3
import json
import numpy as np
from pathlib import Path

def run_tracking_controller(sim, x0, dt, total_time, step_time, x_ref_pre, x_ref_post,
                            X_nom, U_nom, K_list, u_min=None, u_max=None):
    x = np.asarray(x0, dtype=float).reshape(12)
    x_ref_pre = np.asarray(x_ref_pre, dtype=float).reshape(12)
    x_ref_post = np.asarray(x_ref_post, dtype=float).reshape(12)

    if u_min is None:
        u_min = -np.inf * np.ones(6)
    if u_max is None:
        u_max = np.inf * np.ones(6)

    log = {"phase": "control", "data": []}
    n_steps = int(np.ceil(total_time / dt))
    sim.reset()

    # Optionally force the simulator state to x0 if needed
    if hasattr(sim, "tensions") and hasattr(sim, "velocities"):
        sim.tensions = x[:6].copy()
        sim.velocities = x[6:].copy()

    for k in range(n_steps):
        t = round((k + 1) * dt, 10)
        x_ref = x_ref_pre if t < step_time else x_ref_post

        # Nominal index saturates at end of planned trajectory
        idx = min(k, len(U_nom) - 1)
        x_nom = X_nom[min(idx, len(X_nom) - 1)]
        u_nom = U_nom[idx]
        K = K_list[idx] if idx < len(K_list) else K_list[-1]

        # Tracking feedback
        u = u_nom - K @ (x - x_nom)

        # Safety clamp
        u = np.minimum(np.maximum(u, u_min), u_max)

        obs = sim.step(u)

        tensions = np.asarray(obs["tensions"], dtype=float).reshape(6)
        velocities = np.asarray(obs["velocities"], dtype=float).reshape(6)
        x = np.concatenate([tensions, velocities])

        log["data"].append({
            "time": float(t),
            "tensions": [float(v) for v in tensions],
            "velocities": [float(v) for v in velocities],
            "control_inputs": [float(v) for v in u],
            "references": [float(v) for v in x_ref],
        })

    return log

def save_json(obj, path):
    path = Path(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
```

**Critical note:** references in `control_log.json` should follow the expected 12-state format:
`[T1_ref..T6_ref, v1_ref..v6_ref]`.

Do not log only tension references if the validator expects 12 values.

---

## 9. Compute metrics from logged tensions using the task's exact conventions

**What to do:** compute at least:
- `steady_state_error`
- `settling_time`
- `max_tension`
- `min_tension`

**Why:** hidden tests often recompute these from your log, but some tasks also require a submitted `metrics.json`. Your computation should match the task description as closely as possible.

**Decision criteria:**
- `steady_state_error`: usually mean absolute tension error over the final window.
- `settling_time`: earliest time after the step when all tensions remain within a tolerance band around reference.
- `max_tension` / `min_tension`: over the full logged interval.
- Use the post-step reference tensions from config.

**Reference implementation:**

```python
#!/usr/bin/env python3
import numpy as np

def compute_metrics(control_log, tension_ref_post, step_time, settle_band=2.0, final_window=1.0):
    data = control_log["data"]
    if not data:
        raise ValueError("control_log contains no data")

    times = np.array([row["time"] for row in data], dtype=float)
    tensions = np.array([row["tensions"] for row in data], dtype=float)
    tension_ref_post = np.asarray(tension_ref_post, dtype=float).reshape(6)

    max_tension = float(np.max(tensions))
    min_tension = float(np.min(tensions))

    # Steady-state error on the final window
    t_end = times[-1]
    mask_final = times >= max(step_time, t_end - final_window)
    if not np.any(mask_final):
        mask_final = np.ones_like(times, dtype=bool)

    ss_err = np.mean(np.abs(tensions[mask_final] - tension_ref_post.reshape(1, 6)))
    steady_state_error = float(ss_err)

    # Settling time: first time after step such that all future samples stay in band
    post_idx = np.where(times >= step_time)[0]
    settling_time = float(times[-1] - step_time)

    if post_idx.size > 0:
        for start in post_idx:
            future_ok = np.all(np.abs(tensions[start:] - tension_ref_post.reshape(1, 6)) <= settle_band)
            if future_ok:
                settling_time = float(times[start] - step_time)
                break

    return {
        "steady_state_error": steady_state_error,
        "settling_time": settling_time,
        "max_tension": max_tension,
        "min_tension": min_tension,
    }
```

**Critical note:** if the task says âcompared with the reference tensions from system_config.json,â use that source of truth rather than deriving references from the log.

---

## 10. Export validator-ready JSON exactly

**What to do:** write the three required JSON files with precise field names, matrix shapes, and data types.

**Why:** valid control is useless if the schema is wrong.

**Expected schema:**

- `controller_params.json`
  - `horizon_N`: integer in `[3, 30]`
  - `Q_diag`: list of 12 positive floats
  - `R_diag`: list of 6 positive floats
  - `K_lqr`: 6x12 matrix
  - `A_matrix`: 12x12 matrix
  - `B_matrix`: 12x6 matrix

- `control_log.json`
  - `phase`: `"control"`
  - `data`: array of entries with keys:
    - `time`
    - `tensions` length 6
    - `velocities` length 6
    - `control_inputs` length 6
    - `references` length 12

- `metrics.json`
  - `steady_state_error`
  - `settling_time`
  - `max_tension`
  - `min_tension`

**Exporter implementation:**

```python
#!/usr/bin/env python3
import json
import numpy as np
from pathlib import Path

def to_float_list(x):
    arr = np.asarray(x, dtype=float)
    return arr.tolist()

def export_controller_params(path, horizon_N, Q_diag, R_diag, K_lqr, A, B):
    horizon_N = int(horizon_N)
    if not (3 <= horizon_N <= 30):
        raise ValueError("horizon_N must be in [3, 30]")

    Q_diag = np.asarray(Q_diag, dtype=float).reshape(12)
    R_diag = np.asarray(R_diag, dtype=float).reshape(6)
    K_lqr = np.asarray(K_lqr, dtype=float).reshape(6, 12)
    A = np.asarray(A, dtype=float).reshape(12, 12)
    B = np.asarray(B, dtype=float).reshape(12, 6)

    if np.any(Q_diag <= 0) or np.any(R_diag <= 0):
        raise ValueError("Q_diag and R_diag must contain positive values")

    payload = {
        "horizon_N": horizon_N,
        "Q_diag": to_float_list(Q_diag),
        "R_diag": to_float_list(R_diag),
        "K_lqr": to_float_list(K_lqr),
        "A_matrix": to_float_list(A),
        "B_matrix": to_float_list(B),
    }

    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

def export_metrics(path, metrics):
    required = ["steady_state_error", "settling_time", "max_tension", "min_tension"]
    for key in required:
        if key not in metrics:
            raise KeyError(f"Missing metric: {key}")

    payload = {k: float(metrics[k]) for k in required}
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
```

---

# End-to-End Reference Script Skeleton

Use this when you need to build a full solver quickly and reliably.

```python
#!/usr/bin/env python3
import json
import numpy as np
from pathlib import Path

from r2r_simulator import R2RSimulator

# Reuse implementations from previous sections:
# - PlantWrapper
# - find_equilibrium_input
# - linearize_discrete_dynamics
# - verify_linearization
# - NominalOptimizer
# - dlqr
# - tvlqr
# - run_tracking_controller
# - compute_metrics
# - export_controller_params
# - export_metrics

def load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def extract_dt(cfg):
    for key in ["dt", "time_step", "sample_time"]:
        if key in cfg:
            return float(cfg[key])
    return 0.01  # only as a last-resort fallback if config truly omits dt

def extract_initial_and_reference(cfg, x0_fallback):
    x0 = np.asarray(x0_fallback, dtype=float).reshape(12)

    # Try common config keys
    for key in ["initial_state", "x0"]:
        if key in cfg:
            arr = np.asarray(cfg[key], dtype=float).reshape(-1)
            if arr.size == 12:
                x0 = arr.copy()
                break

    # Tension references
    tref = None
    for key in ["reference_tensions", "tensions_ref", "tension_references"]:
        if key in cfg:
            tref = np.asarray(cfg[key], dtype=float).reshape(6)
            break
    if tref is None:
        tref = x0[:6].copy()

    # Velocity references
    vref = x0[6:].copy()
    x_ref_pre = np.concatenate([x0[:6], vref])
    x_ref_post = np.concatenate([tref, vref])

    return x0, x_ref_pre, x_ref_post

def main():
    root = Path("/root")
    config_path = root / "system_config.json"

    cfg = load_config(config_path)
    sim = R2RSimulator(str(config_path))
    plant = PlantWrapper(str(config_path))

    x0_meas = plant.reset()
    dt = extract_dt(cfg)
    x0, x_ref_pre, x_ref_post = extract_initial_and_reference(cfg, x0_meas)

    # Step timing may be task-specific
    step_time = 0.5
    total_time = 5.0

    # Equilibrium for the initial operating point
    u_star, eq_res = find_equilibrium_input(plant, x_ref_pre, u_init=np.zeros(6))
    if eq_res > 1e-5:
        print(f"Warning: equilibrium residual is {eq_res:.3e}")

    A, B = linearize_discrete_dynamics(plant, x_ref_pre, u_star)
    lin_err = verify_linearization(plant, x_ref_pre, u_star, A, B)
    print(f"Linearization local max error: {lin_err:.3e}")

    # Cost selection
    Q_diag = np.array([100, 100, 100, 100, 100, 100, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1], dtype=float)
    R_diag = np.array([0.033, 0.033, 0.033, 0.033, 0.033, 0.033], dtype=float)
    Q = np.diag(Q_diag)
    R = np.diag(R_diag)

    K_lqr, _ = dlqr(A, B, Q, R)

    # Build a nominal transition
    horizon_N = 9
    nominal_steps = max(30, int(np.ceil(total_time / dt)))
    optimizer = NominalOptimizer(plant, dt)
    X_nom, U_nom = optimizer.optimize(
        x0=x0,
        x_goal=x_ref_post,
        N=nominal_steps,
        u_eq=u_star,
        effort_weight=1e-2,
        rate_weight=1e-1,
    )

    # TVLQR tracking
    Qf = 20.0 * Q
    K_list, _, _ = tvlqr(plant, X_nom, U_nom, Q, R, Qf)

    # Run on original simulator
    sim_run = R2RSimulator(str(config_path))
    log = run_tracking_controller(
        sim=sim_run,
        x0=x0,
        dt=dt,
        total_time=total_time,
        step_time=step_time,
        x_ref_pre=x_ref_pre,
        x_ref_post=x_ref_post,
        X_nom=X_nom,
        U_nom=U_nom,
        K_list=K_list,
    )

    metrics = compute_metrics(log, x_ref_post[:6], step_time=step_time, settle_band=2.0, final_window=1.0)

    export_controller_params(root / "controller_params.json", horizon_N, Q_diag, R_diag, K_lqr, A, B)
    with (root / "control_log.json").open("w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)
    export_metrics(root / "metrics.json", metrics)

    print("metrics:", metrics)

if __name__ == "__main__":
    main()
```

---

# Verification Workflow Before Finalizing

Always run these checks before submitting artifacts.

## A. Validate schema and shapes

```python
#!/usr/bin/env python3
import json
import numpy as np
from pathlib import Path

def check():
    root = Path("/root")

    with (root / "controller_params.json").open("r", encoding="utf-8") as f:
        cp = json.load(f)
    with (root / "control_log.json").open("r", encoding="utf-8") as f:
        log = json.load(f)
    with (root / "metrics.json").open("r", encoding="utf-8") as f:
        metrics = json.load(f)

    assert isinstance(cp["horizon_N"], int)
    assert 3 <= cp["horizon_N"] <= 30
    assert np.asarray(cp["Q_diag"], dtype=float).shape == (12,)
    assert np.asarray(cp["R_diag"], dtype=float).shape == (6,)
    assert np.asarray(cp["K_lqr"], dtype=float).shape == (6, 12)
    assert np.asarray(cp["A_matrix"], dtype=float).shape == (12, 12)
    assert np.asarray(cp["B_matrix"], dtype=float).shape == (12, 6)

    assert log["phase"] == "control"
    assert isinstance(log["data"], list) and len(log["data"]) > 0
    assert log["data"][-1]["time"] >= 5.0
    for row in log["data"][:5] + log["data"][-5:]:
        assert len(row["tensions"]) == 6
        assert len(row["velocities"]) == 6
        assert len(row["control_inputs"]) == 6
        assert len(row["references"]) == 12

    for k in ["steady_state_error", "settling_time", "max_tension", "min_tension"]:
        assert isinstance(metrics[k], (int, float))

    print("Schema checks passed.")

if __name__ == "__main__":
    check()
```

## B. Validate linearization numerically

```python
#!/usr/bin/env python3
import json
import numpy as np
from pathlib import Path

def check_linearization():
    from r2r_simulator import R2RSimulator

    root = Path("/root")
    with (root / "controller_params.json").open("r", encoding="utf-8") as f:
        cp = json.load(f)

    A = np.asarray(cp["A_matrix"], dtype=float)
    B = np.asarray(cp["B_matrix"], dtype=float)

    plant = PlantWrapper(str(root / "system_config.json"))
    x_star = plant.reset()
    u_star = np.zeros(6)  # replace with identified equilibrium if needed

    # If your exported A/B are around a nonzero equilibrium input, use that same u_star.
    # Otherwise this check may be misleading.
    err = verify_linearization(plant, x_star, u_star, A, B, trials=10)
    print(f"Local linearization error: {err:.3e}")

if __name__ == "__main__":
    check_linearization()
```

## C. Re-run controller across multiple seeds if noise exists

```python
#!/usr/bin/env python3
import importlib.util
import numpy as np

def main():
    for seed in [0, 1, 2, 3, 4]:
        np.random.seed(seed)
        spec = importlib.util.spec_from_file_location("solver", "/root/mpc_r2r.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "main"):
            print(f"Running seed {seed}")
            mod.main()

if __name__ == "__main__":
    main()
```

If metrics vary by seed, the controller is too fragile.

---

# Controller Tuning Heuristics That Usually Work

## If settling time is too slow
- Increase tension weights in `Q`.
- Improve the nominal trajectory rather than only increasing feedback gains.
- Penalize terminal error more strongly.
- Check whether the planned transition starts too late relative to the step time.
- Use TVLQR instead of a single fixed gain.

## If steady-state error is too high
- Ensure the post-step reference is actually used after the switch time.
- Improve nominal terminal convergence.
- Compute the correct equilibrium torque for the target operating point.
- Add integral-like augmentation only if necessary and consistent with the simulator.

## If max tension exceeds safety bounds
- Add stronger penalty on overshoot-critical sections in the nominal objective.
- Regularize input rate.
- Shorten aggressive control bursts.
- Inspect whether the feedback is amplifying tracking error due to wrong state ordering.

## If min tension drops too low
- Avoid controllers that âunwindâ neighboring sections excessively while pushing the changed section.
- Weight all tensions, not only the changed one.
- Check for sign mistakes in coupling terms or feedback law.

---

# Common Pitfalls

These are the recurring failure modes in this task family.

## 1. Exporting an approximate or wrong linearization
**Symptom:** schema passes, but linearization correctness fails.

**Causes:**
- linearizing a simplified hand-derived model instead of the simulator's actual update,
- using the wrong operating point,
- state ordering mismatch,
- computing only `B` and setting `A` to identity or near-identity without justification,
- forgetting that the simulator is discrete-time.

**Avoidance:**
- derive `A` and `B` from `step_from_state`,
- verify with random local perturbations,
- keep exact ordering `[T1..T6, v1..v6]` unless simulator says otherwise.

---

## 2. Using a safe but weak fixed linear controller
**Symptom:** performance fails even though the controller is stable and files are valid.

**Causes:**
- plain LQR around the initial equilibrium cannot drive the post-step tension change fast enough,
- no nominal trajectory for the nonlinear transition,
- blind gain sweeps without diagnosing the transient.

**Avoidance:**
- optimize a nominal transition on the nonlinear plant,
- track with TVLQR or strong local feedback,
- inspect `metrics.json` and the tension trajectories to identify the real bottleneck.

---

## 3. Confusing current-state effects with next-state effects
**Symptom:** the designed controller appears theoretically strong but has little real influence on tension.

**Cause:** torque often affects velocity first, then tension through coupled dynamics. If you tune as if torque directly changes tension instantly, the controller underperforms.

**Avoidance:**
- inspect the simulator equations,
- account for indirect coupling by planning over a finite horizon,
- verify actuation pathways numerically from `B`.

---

## 4. Logging the wrong references
**Symptom:** control log exists, but validator or downstream checks reject it.

**Causes:**
- logging only 6 tension references instead of 12 full state references,
- logging the pre-step reference after the step time,
- using simulated state instead of intended reference in the `references` field.

**Avoidance:**
- always log `[T1_ref..T6_ref, v1_ref..v6_ref]`,
- switch references exactly at the specified step time,
- keep reference generation separate from state estimation.

---

## 5. Final time shorter than 5 seconds
**Symptom:** control log format passes locally but hidden test rejects duration.

**Cause:** off-by-one step counting or logging from `t=0` without reaching `t>=5.0`.

**Avoidance:**
- compute `n_steps = ceil(total_time / dt)`,
- verify `log["data"][-1]["time"] >= 5.0`.

---

## 6. Gain sign or ordering mistakes
**Symptom:** tensions diverge, overshoot, or oscillate despite reasonable weights.

**Causes:**
- applying `u = u_nom + K e` instead of `u = u_nom - K e`,
- state vector packed as `[v, T]` while matrices assume `[T, v]`,
- transposed `K`.

**Avoidance:**
- assert shapes explicitly,
- unit-test one closed-loop step,
- print the first control correction and confirm its direction is sensible.

---

## 7. Over-relying on random parameter sweeps
**Symptom:** many trials, little improvement.

**Cause:** performance bottleneck is structural, not just numeric tuning.

**Avoidance:**
- diagnose from the log whether failure is due to delay, overshoot, or offset,
- redesign the controller architecture before sweeping hyperparameters.

---

# Recommended Early Execution Path

When you start a similar task, choose this path immediately:

1. **Read simulator and config.**
2. **Wrap the simulator into a deterministic `f(x,u)` discrete transition.**
3. **Find the exact initial operating point and equilibrium input.**
4. **Compute and numerically verify `A` and `B`.**
5. **Optimize a nominal nonlinear transition to the post-step target.**
6. **Add TVLQR tracking feedback.**
7. **Run 5+ seconds and inspect logged tensions.**
8. **Compute metrics and only then tune.**
9. **Export exact JSON schema.**
10. **Re-verify linearization and performance before finalizing.**

This path consistently outperforms:
- ad hoc hand-derived models,
- identity-`A` placeholders,
- blind gain sweeps,
- pure fixed-gain feedback without a nominal plan.

---

# Final Submission Checklist

Before you stop, confirm all of the following:

- [ ] `r2r_simulator.py` was not modified.
- [ ] `controller_params.json` exists and has the exact required keys.
- [ ] `A_matrix` is 12x12 and `B_matrix` is 12x6.
- [ ] exported linearization matches the simulator locally.
- [ ] `control_log.json` spans at least 5.0 seconds.
- [ ] every log row contains 6 tensions, 6 velocities, 6 controls, 12 references.
- [ ] `metrics.json` exists and contains numeric values.
- [ ] performance targets are met:
  - [ ] mean steady-state error below target,
  - [ ] settling time below target,
  - [ ] max tension below safety limit,
  - [ ] min tension above safety limit.
- [ ] controller behavior is repeatable across reruns.

If any one of these is uncertain, do not finalize yet.