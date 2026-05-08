---
name: dc-opf-dispatch-with-spinning-reserves
description: Solve MATPOWER-format grid dispatch tasks by building a DC optimal power flow with generator energy dispatch, spinning reserve allocation, transmission constraints, and structured JSON reporting.
tools:
  - python3
  - python3-pip
  - python3-venv
  - numpy
  - scipy
  - cvxpy
  - clarabel
  - jq
  - rg
tags:
  - power-systems
  - dc-opf
  - matpower
  - optimization
  - json
  - reporting
---

# DC-OPF Dispatch with Spinning Reserves from MATPOWER JSON

This skill is for tasks where you are given a MATPOWER-style network snapshot in JSON and must produce an economically optimal generator dispatch that satisfies:

- DC power balance at each bus
- Generator output limits
- Branch flow limits
- System-wide spinning reserve requirement
- Standard reserve-energy coupling, typically `Pg + Rg <= Pmax`

It also covers how to generate the expected `report.json` schema and how to verify feasibility before finalizing.

The most reliable path is:

1. Inspect the MATPOWER structure.
2. Parse buses, generators, and branches into numeric arrays.
3. Build a **DC-OPF with reserve variables** using `cvxpy`.
4. Solve with `CLARABEL` or another installed convex solver.
5. Post-process branch loadings and operating margin.
6. Validate all constraints numerically.
7. Write exactly the requested JSON schema.

---

## When to Use This Skill

Use this skill when:

- Input is a power network snapshot in MATPOWER-like JSON.
- Output requires active generation dispatch and spinning reserve allocation.
- The task expects globally optimal or near-exact linear/quadratic economic dispatch.
- The validator checks feasibility, consistency, and cost optimality.

Do **not** use ad hoc balancing heuristics or proportional allocation. These will usually fail optimality checks.

---

## Assumptions and Domain Conventions

Most MATPOWER-format cases follow these conventions:

- `baseMVA`: scalar system base.
- `bus`: matrix with bus records.
- `gen`: matrix with generator records.
- `branch`: matrix with transmission branch records.
- `gencost`: generator cost data.

Typical important column meanings:

### Bus columns
MATPOWER bus matrix commonly includes:

- column 0: `bus_i`
- column 1: `type`
- column 2: `Pd` in MW
- column 3: `Qd`
- column 7: `Vm`
- column 8: `Va`

For DC-OPF, only `bus_i` and `Pd` are usually required.

### Generator columns
Common generator matrix columns:

- column 0: `bus`
- column 1: `Pg`
- column 7: `status`
- column 8: `Pmax`
- column 9: `Pmin`

Only online generators (`status > 0`) should be dispatchable.

### Branch columns
Common branch matrix columns:

- column 0: `fbus`
- column 1: `tbus`
- column 3: `x` (reactance)
- column 5: `rateA`
- column 10: `status`
- column 8: `ratio` (tap), if present and nonzero

For DC power flow:
- susceptance `b = 1 / x`
- if transformer tap exists, use adjusted flow relation
- angle difference limits are often ignored unless explicitly required

### Generator cost
Most tasks use polynomial costs with MATPOWER `gencost` rows:

- column 0: model (`2` means polynomial)
- column 3: number of coefficients `n`
- remaining columns: coefficients from highest order to constant

For DC dispatch, use the active power cost only.

---

## High-Level Workflow

### 1) Inspect the input structure before coding
Why: MATPOWER JSON can vary in size and indexing, and a quick inspection prevents column misinterpretation.

Check:
- Are `bus`, `gen`, `branch`, `gencost`, `baseMVA` present?
- Are IDs contiguous or arbitrary?
- How many online generators and active branches exist?
- Is reserve requirement explicitly included in custom fields, or must it be inferred from available data/instructions?

```python
import json
from pathlib import Path

def inspect_network(path: str) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    required = ["baseMVA", "bus", "gen", "branch", "gencost"]
    missing = [k for k in required if k not in data]
    if missing:
        raise KeyError(f"Missing required MATPOWER keys: {missing}")

    print("baseMVA:", data["baseMVA"])
    print("n_bus:", len(data["bus"]))
    print("n_gen:", len(data["gen"]))
    print("n_branch:", len(data["branch"]))
    print("n_gencost:", len(data["gencost"]))

    bus_ids = [row[0] for row in data["bus"]]
    print("first_bus_ids:", bus_ids[:10])

# Example:
# inspect_network("network.json")
```

---

### 2) Normalize the network into indexed arrays
Why: Bus numbers in MATPOWER are often non-consecutive. You must map bus IDs to compact indices for matrix construction.

Key decisions:
- Keep only online generators.
- Keep only in-service branches.
- Decide how to handle zero or missing branch limits:
  - if `rateA > 0`, enforce it
  - if `rateA <= 0`, treat as unbounded unless task says otherwise

```python
import json
import numpy as np

def load_matpower_case(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    base_mva = float(data["baseMVA"])
    bus = np.array(data["bus"], dtype=float)
    gen = np.array(data["gen"], dtype=float)
    branch = np.array(data["branch"], dtype=float)
    gencost = np.array(data["gencost"], dtype=float)

    if bus.ndim != 2 or gen.ndim != 2 or branch.ndim != 2 or gencost.ndim != 2:
        raise ValueError("Expected 2D matrices for bus/gen/branch/gencost")

    bus_ids = bus[:, 0].astype(int).tolist()
    bus_index = {bus_id: i for i, bus_id in enumerate(bus_ids)}

    online_gen_mask = gen[:, 7] > 0
    active_branch_mask = branch[:, 10] > 0

    gen_on = gen[online_gen_mask].copy()
    branch_on = branch[active_branch_mask].copy()
    gencost_on = gencost[online_gen_mask].copy()

    if len(gen_on) == 0:
        raise ValueError("No online generators available")

    return {
        "baseMVA": base_mva,
        "bus": bus,
        "gen": gen_on,
        "branch": branch_on,
        "gencost": gencost_on,
        "bus_ids": bus_ids,
        "bus_index": bus_index,
    }
```

---

### 3) Determine the reserve requirement explicitly and consistently
Why: Reserve requirements are frequently task-specific. The optimization and the output must use the exact same number.

Good practice:
- First look for a custom field in the JSON, such as `reserve_requirement_MW`.
- If no such field exists, use the task instruction or supplied metadata.
- Never invent a heuristic unless the task explicitly prescribes it.

```python
def get_reserve_requirement(case_data: dict, raw_data: dict, fallback_mw: float | None = None) -> float:
    candidate_keys = [
        "reserve_requirement_MW",
        "reserveRequirementMW",
        "spinning_reserve_MW",
        "spinningReserveMW",
        "reserve_MW",
    ]
    for key in candidate_keys:
        if key in raw_data:
            value = float(raw_data[key])
            if value < 0:
                raise ValueError(f"Negative reserve requirement in key {key}")
            return value

    if fallback_mw is not None:
        if fallback_mw < 0:
            raise ValueError("Fallback reserve requirement cannot be negative")
        return float(fallback_mw)

    raise KeyError(
        "Reserve requirement not found in network data. Supply it from task instructions or metadata."
    )
```

---

### 4) Build the DC network model carefully
Why: The most common technical errors come from wrong bus indexing, wrong branch reactance handling, or forgetting transformer taps.

For each active branch:
- from bus index `i`
- to bus index `j`
- reactance `x`
- tap `tap = ratio if ratio != 0 else 1.0`
- DC flow approximation from `i` to `j`:
  `f = (theta_i - theta_j) / (x * tap)`

If `x` is zero or nearly zero, you must guard against division by zero.

```python
import numpy as np

def build_branch_data(case_data: dict) -> list[dict]:
    branch = case_data["branch"]
    bus_index = case_data["bus_index"]

    branch_data = []
    for k, row in enumerate(branch):
        fbus = int(row[0])
        tbus = int(row[1])
        x = float(row[3])
        rate_a = float(row[5]) if row.shape[0] > 5 else 0.0
        ratio = float(row[8]) if row.shape[0] > 8 else 0.0

        if fbus not in bus_index or tbus not in bus_index:
            raise KeyError(f"Branch references unknown bus: {fbus} -> {tbus}")

        if abs(x) < 1e-12:
            raise ValueError(f"Branch {k} has zero/near-zero reactance; DC model undefined")

        tap = ratio if abs(ratio) > 1e-12 else 1.0

        branch_data.append({
            "from_bus_id": fbus,
            "to_bus_id": tbus,
            "from_idx": bus_index[fbus],
            "to_idx": bus_index[tbus],
            "x": x,
            "tap": tap,
            "rateA_MW": rate_a,
        })

    return branch_data
```

---

### 5) Build generator cost functions from MATPOWER `gencost`
Why: Cost optimality tests usually compare against the exact objective form. If you simplify a quadratic cost to linear, you may fail.

Use:
- polynomial model only (`model == 2`)
- if coefficients are quadratic: `c2*Pg^2 + c1*Pg + c0`
- if linear: `c1*Pg + c0`

Constant terms can be included or omitted if only relative cost matters, but include them if final total cost is validated numerically.

```python
import cvxpy as cp

def build_generator_cost_expression(pg_var, gencost: np.ndarray):
    if len(gencost) != pg_var.shape[0]:
        raise ValueError("gencost row count must match number of online generators")

    total_cost = 0
    for i in range(pg_var.shape[0]):
        row = gencost[i]
        model = int(row[0])
        ncost = int(row[3])

        if model != 2:
            raise NotImplementedError(f"Only polynomial gencost model=2 is supported, got {model}")

        coeffs = row[4:4 + ncost]
        if len(coeffs) != ncost:
            raise ValueError(f"Malformed gencost row at generator {i}")

        expr = 0
        power = ncost - 1
        for c in coeffs:
            expr += float(c) * cp.power(pg_var[i], power)
            power -= 1
        total_cost += expr

    return total_cost
```

---

### 6) Formulate the DC-OPF with reserve variables
Why: The required task is a joint energy-and-reserve optimization.

Typical decision variables:
- `pg[g]`: active generation MW
- `rg[g]`: spinning reserve MW
- `theta[b]`: bus angles radians-like DC state
- optionally `flow[l]`: branch flow MW (or represent as expressions)

Core constraints:
- `Pmin <= pg <= Pmax`
- `rg >= 0`
- `pg + rg <= Pmax`
- `sum(rg) >= reserve_requirement`
- bus-by-bus nodal balance
- `-rateA <= flow <= rateA` for bounded lines
- one reference angle, e.g. `theta[slack] == 0`

```python
import cvxpy as cp
import numpy as np

def build_and_solve_dc_opf(case_data: dict, reserve_requirement_mw: float):
    bus = case_data["bus"]
    gen = case_data["gen"]
    gencost = case_data["gencost"]
    branch_data = build_branch_data(case_data)

    n_bus = bus.shape[0]
    n_gen = gen.shape[0]

    pd = bus[:, 2].astype(float)
    gen_bus_idx = np.array([case_data["bus_index"][int(b)] for b in gen[:, 0]], dtype=int)
    pmax = gen[:, 8].astype(float)
    pmin = gen[:, 9].astype(float)

    pg = cp.Variable(n_gen)
    rg = cp.Variable(n_gen)
    theta = cp.Variable(n_bus)

    constraints = []

    constraints += [pg >= pmin, pg <= pmax]
    constraints += [rg >= 0]
    constraints += [pg + rg <= pmax]
    constraints += [cp.sum(rg) >= reserve_requirement_mw]

    flow_exprs = []
    for br in branch_data:
        i = br["from_idx"]
        j = br["to_idx"]
        x = br["x"]
        tap = br["tap"]
        f = (theta[i] - theta[j]) / (x * tap)
        flow_exprs.append(f)

        rate = br["rateA_MW"]
        if rate > 0:
            constraints += [f <= rate, f >= -rate]

    # nodal balance: generation - demand = net outgoing flow
    for b in range(n_bus):
        gen_inj = cp.sum(pg[np.where(gen_bus_idx == b)[0]]) if np.any(gen_bus_idx == b) else 0.0
        net_out = 0
        for idx, br in enumerate(branch_data):
            if br["from_idx"] == b:
                net_out += flow_exprs[idx]
            elif br["to_idx"] == b:
                net_out -= flow_exprs[idx]
        constraints += [gen_inj - pd[b] == net_out]

    slack_bus = 0
    constraints += [theta[slack_bus] == 0]

    objective = cp.Minimize(build_generator_cost_expression(pg, gencost))
    problem = cp.Problem(objective, constraints)

    try:
        problem.solve(solver=cp.CLARABEL, verbose=False)
    except Exception:
        problem.solve(verbose=False)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Optimization failed with status: {problem.status}")

    pg_val = np.asarray(pg.value, dtype=float)
    rg_val = np.asarray(rg.value, dtype=float)
    theta_val = np.asarray(theta.value, dtype=float)
    flow_val = np.asarray([float(f.value) for f in flow_exprs], dtype=float)

    return {
        "status": problem.status,
        "objective": float(problem.value),
        "pg": pg_val,
        "rg": rg_val,
        "theta": theta_val,
        "flows": flow_val,
        "branch_data": branch_data,
        "pd": pd,
        "pmax": pmax,
        "pmin": pmin,
        "gen_bus_idx": gen_bus_idx,
    }
```

---

### 7) Compute the report fields exactly as requested
Why: Passing the optimizer is not enough. Schema mismatches fail even with perfect dispatch.

Required fields from the task pattern:
- `generator_dispatch`
- `totals`
- `most_loaded_lines`
- `operating_margin_MW`

Important definitions:
- `generation_MW = sum(pg)`
- `reserve_MW = sum(rg)`
- `load_MW = sum(Pd)`
- `operating_margin_MW = sum(max(0, Pmax - Pg - Rg))`
- line loading percentage:
  - if `rateA > 0`: `100 * abs(flow) / rateA`
  - if `rateA <= 0`: usually omit from âmost loadedâ ranking or treat as not rate-limited

```python
import json
import numpy as np

def build_report(case_data: dict, solution: dict) -> dict:
    gen = case_data["gen"]
    branch_data = solution["branch_data"]
    pg = solution["pg"]
    rg = solution["rg"]
    flows = solution["flows"]
    pd = solution["pd"]
    pmax = solution["pmax"]
    objective = solution["objective"]

    generator_dispatch = []
    for i in range(gen.shape[0]):
        generator_dispatch.append({
            "id": i + 1,
            "bus": int(gen[i, 0]),
            "output_MW": float(pg[i]),
            "reserve_MW": float(rg[i]),
            "pmax_MW": float(pmax[i]),
        })

    loadings = []
    for br, f in zip(branch_data, flows):
        rate = br["rateA_MW"]
        if rate > 0:
            loading_pct = 100.0 * abs(float(f)) / rate
            loadings.append({
                "from": br["from_bus_id"],
                "to": br["to_bus_id"],
                "loading_pct": float(loading_pct),
            })

    loadings.sort(key=lambda x: x["loading_pct"], reverse=True)

    operating_margin = float(np.sum(np.maximum(0.0, pmax - pg - rg)))

    report = {
        "generator_dispatch": generator_dispatch,
        "totals": {
            "cost_dollars_per_hour": float(objective),
            "load_MW": float(np.sum(pd)),
            "generation_MW": float(np.sum(pg)),
            "reserve_MW": float(np.sum(rg)),
        },
        "most_loaded_lines": loadings[:3],
        "operating_margin_MW": operating_margin,
    }
    return report

def save_report(report: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
```

---

### 8) Validate numerically before finalizing
Why: Solvers return floating-point results. Small residuals are normal, but you should check they are tiny.

Recommended validation checks:
- `abs(sum(pg) - sum(Pd))` near zero
- no `pg > pmax + tol`
- no `pg < pmin - tol`
- no `pg + rg > pmax + tol`
- `sum(rg) >= reserve_requirement - tol`
- bus balance residuals tiny
- no line exceeds rate by more than tolerance
- report totals match variable sums

```python
import numpy as np

def validate_solution(case_data: dict, solution: dict, reserve_requirement_mw: float, tol: float = 1e-5) -> dict:
    gen = case_data["gen"]
    pg = solution["pg"]
    rg = solution["rg"]
    pd = solution["pd"]
    pmax = solution["pmax"]
    pmin = solution["pmin"]
    gen_bus_idx = solution["gen_bus_idx"]
    flows = solution["flows"]
    branch_data = solution["branch_data"]

    checks = {}

    checks["gen_matches_load"] = float(abs(np.sum(pg) - np.sum(pd)))
    checks["reserve_shortfall"] = float(max(0.0, reserve_requirement_mw - np.sum(rg)))
    checks["reserve_cap_violation"] = float(max(0.0, np.max(rg - np.maximum(0.0, pmax - pg))))
    checks["capacity_coupling_violation"] = float(max(0.0, np.max(pg + rg - pmax)))
    checks["pmax_violation"] = float(max(0.0, np.max(pg - pmax)))
    checks["pmin_violation"] = float(max(0.0, np.max(pmin - pg)))

    n_bus = case_data["bus"].shape[0]
    max_balance = 0.0
    for b in range(n_bus):
        gen_inj = np.sum(pg[gen_bus_idx == b]) if np.any(gen_bus_idx == b) else 0.0
        net_out = 0.0
        for idx, br in enumerate(branch_data):
            if br["from_idx"] == b:
                net_out += flows[idx]
            elif br["to_idx"] == b:
                net_out -= flows[idx]
        residual = abs(gen_inj - pd[b] - net_out)
        max_balance = max(max_balance, residual)
    checks["max_balance_residual_MW"] = float(max_balance)

    max_line_over = 0.0
    for br, f in zip(branch_data, flows):
        rate = br["rateA_MW"]
        if rate > 0:
            max_line_over = max(max_line_over, abs(f) - rate)
    checks["max_line_limit_excess_MW"] = float(max(0.0, max_line_over))

    for key, value in checks.items():
        if key in ("reserve_shortfall", "reserve_cap_violation", "capacity_coupling_violation",
                   "pmax_violation", "pmin_violation", "max_balance_residual_MW", "max_line_limit_excess_MW"):
            if value > tol:
                raise AssertionError(f"Validation failed: {key} = {value} > {tol}")

    return checks
```

---

## Reference Implementation

The following is a complete end-to-end script. It is designed to be copied, adapted, and run directly.

It:
- loads `network.json`
- detects the MATPOWER structure
- solves the DC-OPF with spinning reserves
- validates feasibility
- writes the required `report.json`

> If the reserve requirement is not embedded in the JSON, pass it with `--reserve-mw`.

```python
#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import cvxpy as cp
import numpy as np


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def load_matpower_case(path: str) -> dict:
    raw = load_json(path)

    required = ["baseMVA", "bus", "gen", "branch", "gencost"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise KeyError(f"Missing required keys: {missing}")

    base_mva = float(raw["baseMVA"])
    bus = np.array(raw["bus"], dtype=float)
    gen = np.array(raw["gen"], dtype=float)
    branch = np.array(raw["branch"], dtype=float)
    gencost = np.array(raw["gencost"], dtype=float)

    if bus.ndim != 2 or gen.ndim != 2 or branch.ndim != 2 or gencost.ndim != 2:
        raise ValueError("bus/gen/branch/gencost must all be 2D arrays")

    bus_ids = [int(x) for x in bus[:, 0].tolist()]
    bus_index = {bus_id: i for i, bus_id in enumerate(bus_ids)}

    online_gen_mask = gen[:, 7] > 0
    active_branch_mask = branch[:, 10] > 0

    gen_on = gen[online_gen_mask].copy()
    branch_on = branch[active_branch_mask].copy()
    gencost_on = gencost[online_gen_mask].copy()

    if gen_on.shape[0] == 0:
        raise ValueError("No online generators found")
    if gencost_on.shape[0] != gen_on.shape[0]:
        raise ValueError("gencost row count does not match online generator count")

    return {
        "raw": raw,
        "baseMVA": base_mva,
        "bus": bus,
        "gen": gen_on,
        "branch": branch_on,
        "gencost": gencost_on,
        "bus_ids": bus_ids,
        "bus_index": bus_index,
    }


def infer_reserve_requirement(raw: dict, cli_value: float | None) -> float:
    keys = [
        "reserve_requirement_MW",
        "reserveRequirementMW",
        "spinning_reserve_MW",
        "spinningReserveMW",
        "reserve_MW",
    ]
    for key in keys:
        if key in raw:
            value = float(raw[key])
            if value < 0:
                raise ValueError(f"Reserve requirement cannot be negative: {key}={value}")
            return value

    if cli_value is None:
        raise KeyError(
            "Reserve requirement not found in input JSON. "
            "Pass --reserve-mw or adapt this script to your task metadata."
        )

    if cli_value < 0:
        raise ValueError("--reserve-mw must be nonnegative")
    return float(cli_value)


def build_branch_data(case_data: dict) -> list[dict]:
    branch = case_data["branch"]
    bus_index = case_data["bus_index"]

    out = []
    for k, row in enumerate(branch):
        fbus = int(row[0])
        tbus = int(row[1])
        x = float(row[3])
        rate_a = float(row[5]) if row.shape[0] > 5 else 0.0
        ratio = float(row[8]) if row.shape[0] > 8 else 0.0

        if fbus not in bus_index or tbus not in bus_index:
            raise KeyError(f"Branch {k} references unknown buses: {fbus}, {tbus}")
        if abs(x) < 1e-12:
            raise ValueError(f"Branch {k} has zero/near-zero reactance")

        tap = ratio if abs(ratio) > 1e-12 else 1.0

        out.append({
            "from_bus_id": fbus,
            "to_bus_id": tbus,
            "from_idx": bus_index[fbus],
            "to_idx": bus_index[tbus],
            "x": x,
            "tap": tap,
            "rateA_MW": rate_a,
        })
    return out


def build_cost_expression(pg: cp.Variable, gencost: np.ndarray):
    total_cost = 0
    for i in range(pg.shape[0]):
        row = gencost[i]
        model = int(row[0])
        ncost = int(row[3])

        if model != 2:
            raise NotImplementedError(f"Unsupported gencost model {model}; expected polynomial model 2")
        coeffs = row[4:4 + ncost]
        if len(coeffs) != ncost:
            raise ValueError(f"Malformed gencost row for generator {i}")

        expr = 0
        degree = ncost - 1
        for coeff in coeffs:
            coeff = float(coeff)
            if degree == 2:
                expr += coeff * cp.square(pg[i])
            elif degree == 1:
                expr += coeff * pg[i]
            elif degree == 0:
                expr += coeff
            else:
                expr += coeff * cp.power(pg[i], degree)
            degree -= 1
        total_cost += expr
    return total_cost


def solve_dc_opf(case_data: dict, reserve_requirement_mw: float) -> dict:
    bus = case_data["bus"]
    gen = case_data["gen"]
    gencost = case_data["gencost"]
    branch_data = build_branch_data(case_data)

    n_bus = bus.shape[0]
    n_gen = gen.shape[0]

    pd = bus[:, 2].astype(float)
    gen_bus_idx = np.array([case_data["bus_index"][int(b)] for b in gen[:, 0]], dtype=int)
    pmax = gen[:, 8].astype(float)
    pmin = gen[:, 9].astype(float)

    pg = cp.Variable(n_gen)
    rg = cp.Variable(n_gen)
    theta = cp.Variable(n_bus)

    constraints = []
    constraints += [pg >= pmin]
    constraints += [pg <= pmax]
    constraints += [rg >= 0]
    constraints += [pg + rg <= pmax]
    constraints += [cp.sum(rg) >= reserve_requirement_mw]

    flow_exprs = []
    for br in branch_data:
        i = br["from_idx"]
        j = br["to_idx"]
        x = br["x"]
        tap = br["tap"]
        flow = (theta[i] - theta[j]) / (x * tap)
        flow_exprs.append(flow)

        rate = br["rateA_MW"]
        if rate > 0:
            constraints += [flow <= rate, flow >= -rate]

    # Nodal balance: generation - load = net flow out
    for b in range(n_bus):
        gen_idxs = np.where(gen_bus_idx == b)[0]
        gen_term = cp.sum(pg[gen_idxs]) if len(gen_idxs) > 0 else 0.0

        net_out = 0
        for ell, br in enumerate(branch_data):
            if br["from_idx"] == b:
                net_out += flow_exprs[ell]
            elif br["to_idx"] == b:
                net_out -= flow_exprs[ell]

        constraints += [gen_term - pd[b] == net_out]

    # Reference angle
    constraints += [theta[0] == 0]

    objective = cp.Minimize(build_cost_expression(pg, gencost))
    problem = cp.Problem(objective, constraints)

    try:
        problem.solve(solver=cp.CLARABEL, verbose=False)
    except Exception:
        # Safe fallback in case solver selection differs by environment
        problem.solve(verbose=False)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Solver failed with status: {problem.status}")

    pg_val = np.asarray(pg.value, dtype=float)
    rg_val = np.asarray(rg.value, dtype=float)
    theta_val = np.asarray(theta.value, dtype=float)
    flow_val = np.asarray([float(expr.value) for expr in flow_exprs], dtype=float)

    return {
        "status": problem.status,
        "objective": float(problem.value),
        "pg": pg_val,
        "rg": rg_val,
        "theta": theta_val,
        "flows": flow_val,
        "branch_data": branch_data,
        "pd": pd,
        "pmax": pmax,
        "pmin": pmin,
        "gen_bus_idx": gen_bus_idx,
    }


def validate_solution(case_data: dict, solution: dict, reserve_requirement_mw: float, tol: float = 1e-5) -> dict:
    pg = solution["pg"]
    rg = solution["rg"]
    pd = solution["pd"]
    pmax = solution["pmax"]
    pmin = solution["pmin"]
    gen_bus_idx = solution["gen_bus_idx"]
    flows = solution["flows"]
    branch_data = solution["branch_data"]

    checks = {
        "gen_matches_load": float(abs(np.sum(pg) - np.sum(pd))),
        "reserve_shortfall": float(max(0.0, reserve_requirement_mw - np.sum(rg))),
        "reserve_cap_violation": float(max(0.0, np.max(rg - np.maximum(0.0, pmax - pg)))),
        "capacity_coupling_violation": float(max(0.0, np.max(pg + rg - pmax))),
        "pmax_violation": float(max(0.0, np.max(pg - pmax))),
        "pmin_violation": float(max(0.0, np.max(pmin - pg))),
    }

    n_bus = case_data["bus"].shape[0]
    max_balance = 0.0
    for b in range(n_bus):
        gen_term = np.sum(pg[gen_bus_idx == b]) if np.any(gen_bus_idx == b) else 0.0
        net_out = 0.0
        for ell, br in enumerate(branch_data):
            if br["from_idx"] == b:
                net_out += flows[ell]
            elif br["to_idx"] == b:
                net_out -= flows[ell]
        residual = abs(gen_term - pd[b] - net_out)
        max_balance = max(max_balance, residual)
    checks["max_balance_residual_MW"] = float(max_balance)

    max_line_over = 0.0
    for br, flow in zip(branch_data, flows):
        rate = br["rateA_MW"]
        if rate > 0:
            max_line_over = max(max_line_over, abs(flow) - rate)
    checks["max_line_limit_excess_MW"] = float(max(0.0, max_line_over))

    hard_fail_keys = [
        "reserve_shortfall",
        "reserve_cap_violation",
        "capacity_coupling_violation",
        "pmax_violation",
        "pmin_violation",
        "max_balance_residual_MW",
        "max_line_limit_excess_MW",
    ]
    for key in hard_fail_keys:
        if checks[key] > tol:
            raise AssertionError(f"Validation failed: {key} = {checks[key]} > {tol}")

    return checks


def build_report(case_data: dict, solution: dict) -> dict:
    gen = case_data["gen"]
    pg = solution["pg"]
    rg = solution["rg"]
    pd = solution["pd"]
    pmax = solution["pmax"]
    flows = solution["flows"]
    branch_data = solution["branch_data"]

    generator_dispatch = []
    for i in range(gen.shape[0]):
        generator_dispatch.append({
            "id": i + 1,
            "bus": int(gen[i, 0]),
            "output_MW": float(pg[i]),
            "reserve_MW": float(rg[i]),
            "pmax_MW": float(pmax[i]),
        })

    line_rows = []
    for br, flow in zip(branch_data, flows):
        rate = br["rateA_MW"]
        if rate > 0:
            line_rows.append({
                "from": br["from_bus_id"],
                "to": br["to_bus_id"],
                "loading_pct": float(100.0 * abs(flow) / rate),
            })

    line_rows.sort(key=lambda row: row["loading_pct"], reverse=True)

    operating_margin = float(np.sum(np.maximum(0.0, pmax - pg - rg)))

    report = {
        "generator_dispatch": generator_dispatch,
        "totals": {
            "cost_dollars_per_hour": float(solution["objective"]),
            "load_MW": float(np.sum(pd)),
            "generation_MW": float(np.sum(pg)),
            "reserve_MW": float(np.sum(rg)),
        },
        "most_loaded_lines": line_rows[:3],
        "operating_margin_MW": operating_margin,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Solve DC-OPF with spinning reserves for MATPOWER JSON")
    parser.add_argument("--input", default="network.json", help="Path to MATPOWER JSON input")
    parser.add_argument("--output", default="report.json", help="Path to output report JSON")
    parser.add_argument("--reserve-mw", type=float, default=None, help="Reserve requirement if not embedded in JSON")
    args = parser.parse_args()

    case_data = load_matpower_case(args.input)
    reserve_requirement_mw = infer_reserve_requirement(case_data["raw"], args.reserve_mw)

    solution = solve_dc_opf(case_data, reserve_requirement_mw)
    checks = validate_solution(case_data, solution, reserve_requirement_mw, tol=1e-5)
    report = build_report(case_data, solution)

    # Internal consistency check with report
    report_margin = report["operating_margin_MW"]
    calc_margin = float(np.sum(np.maximum(0.0, solution["pmax"] - solution["pg"] - solution["rg"])))
    if abs(report_margin - calc_margin) > 1e-6:
        raise AssertionError("Operating margin mismatch between report and solution")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps({
        "status": solution["status"],
        "objective": solution["objective"],
        "reserve_requirement_MW": reserve_requirement_mw,
        "validation": checks,
        "output_path": str(Path(args.output).resolve()),
    }, indent=2))


if __name__ == "__main__":
    main()
```

---

## Recommended Execution Path

### 1. Inspect first, solve second
Before writing the optimizer, quickly inspect the file:

```bash
ls -l .
python3 - <<'PY'
import json
with open('network.json','r') as f:
    d=json.load(f)
print(d.keys())
print('baseMVA', d.get('baseMVA'))
print('bus', len(d.get('bus', [])))
print('gen', len(d.get('gen', [])))
print('branch', len(d.get('branch', [])))
print('gencost', len(d.get('gencost', [])))
PY
```

### 2. Install tools only if needed
If `cvxpy` and `clarabel` are unavailable, install them in a venv:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --quiet numpy scipy cvxpy clarabel
```

### 3. Run the solver
If reserve is embedded:

```bash
python3 solve_dispatch.py --input network.json --output report.json
```

If reserve is external:

```bash
python3 solve_dispatch.py --input network.json --output report.json --reserve-mw 500.0
```

### 4. Sanity-check the output schema
```bash
python3 - <<'PY'
import json
with open('report.json','r') as f:
    r=json.load(f)
print(r.keys())
print('n generators:', len(r['generator_dispatch']))
print('totals:', r['totals'])
print('top lines:', r['most_loaded_lines'][:3])
print('margin:', r['operating_margin_MW'])
PY
```

---

## Output Schema Requirements

Match the downstream schema exactly:

```json
{
  "generator_dispatch": [
    {"id": 1, "bus": 1, "output_MW": 100.0, "reserve_MW": 30.0, "pmax_MW": 150.0}
  ],
  "totals": {
    "cost_dollars_per_hour": 8000.0,
    "load_MW": 259.0,
    "generation_MW": 259.0,
    "reserve_MW": 500.0
  },
  "most_loaded_lines": [
    {"from": 1, "to": 2, "loading_pct": 85.0}
  ],
  "operating_margin_MW": 50.0
}
```

Notes:
- `generator_dispatch.id` is usually a sequential output index, not the MATPOWER bus ID.
- `bus` is the generator's bus number from the network.
- `most_loaded_lines` should contain the top 3 by absolute percentage loading among lines with positive thermal limits.
- Keep numeric values as JSON numbers, not strings.

---

## Common Pitfalls

Even when there were no recorded failed attempts, these are the high-risk mistakes for this task class:

1. **Using arbitrary bus IDs directly as array indices**
   - MATPOWER bus numbers are often sparse and non-consecutive.
   - Always build a `bus_id -> compact_index` mapping.

2. **Forgetting to filter offline generators or branches**
   - Only `gen[:,7] > 0` and `branch[:,10] > 0` should be active.

3. **Ignoring reserve-energy coupling**
   - Reserve is not free headroom unless constrained.
   - Enforce `Pg + Rg <= Pmax`.

4. **Allocating reserve without a system-wide reserve constraint**
   - You need `sum(Rg) >= requirement`.

5. **Using heuristic dispatch instead of optimization**
   - Cost-optimality checks usually require solving the actual convex program.

6. **Misreading branch reactance or tap ratio**
   - For DC flows, use branch `x`, not `r`.
   - If `ratio == 0`, interpret tap as `1.0`.

7. **Ranking unlimited lines in âmost_loaded_linesâ**
   - If `rateA <= 0`, loading percentage is undefined for thermal-limit ranking.
   - Exclude them from the top-loaded list unless the task explicitly says otherwise.

8. **Dropping constant or quadratic cost terms incorrectly**
   - If validator checks exact total cost, preserve the original polynomial objective.

9. **Failing to set a slack/reference angle**
   - Without `theta[slack] == 0`, the DC model is singular.

10. **Not validating residuals**
   - Floating-point solutions may be slightly inexact.
   - Check residuals and line excesses against a small tolerance.

11. **Incorrect operating margin**
   - The required definition here is uncommitted capacity beyond energy and reserves:
     `sum(max(0, Pmax - Pg - Rg))`

12. **Mismatched report totals**
   - `generation_MW` must equal `sum(output_MW)` from generator rows.
   - `reserve_MW` must equal `sum(reserve_MW)` from generator rows.

---

## Practical Notes on Solver Choice

- `cvxpy + CLARABEL` is a strong default for convex DC-OPF with quadratic costs.
- If `CLARABEL` is unavailable, `OSQP` may work for QPs, but availability varies.
- Always inspect `problem.status`.
- Accept `optimal` and, if necessary, `optimal_inaccurate` only after running numerical validation.

---

## Minimal Final Checklist

Before submitting:

- [ ] `report.json` exists
- [ ] JSON schema matches exactly
- [ ] `sum(pg)` equals total load within tolerance
- [ ] all `pg` satisfy bounds
- [ ] all `rg >= 0`
- [ ] all `pg + rg <= pmax`
- [ ] `sum(rg)` meets reserve requirement
- [ ] all bounded lines satisfy thermal limits
- [ ] top 3 loaded lines are sorted descending
- [ ] operating margin equals `sum(max(0, pmax - pg - rg))`
- [ ] cost comes from the actual optimization objective

If you follow this workflow, you will usually produce a verifier-passing solution on the first attempt.