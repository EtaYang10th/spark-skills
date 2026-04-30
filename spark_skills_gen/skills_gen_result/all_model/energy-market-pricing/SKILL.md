---
name: dc_opf_reserve_counterfactual_market_analysis
description: Solve day-ahead market counterfactual pricing tasks on MATPOWER-style network JSON using DC-OPF with spinning reserve co-optimization, extract LMPs and reserve MCP from duals, compare a base and modified transmission scenario, and write a validator-friendly report.json.
tools:
  - python3
  - python3-venv
  - pip
  - jq
  - build-essential
  - curl
  - cvxpy
tags:
  - energy-market-pricing
  - dc-opf
  - lmp
  - reserve
  - matpower
  - counterfactual
  - optimization
---

# DC-OPF + Reserve Counterfactual Market Analysis

This skill is for tasks where you must:

1. Load a power system snapshot from `network.json` in MATPOWER-like JSON format.
2. Solve a **base market-clearing** problem.
3. Modify one or more network parameters, then solve a **counterfactual** market-clearing problem.
4. Report:
   - total production cost,
   - LMP at every bus,
   - reserve market clearing price,
   - binding lines,
   - comparative impact metrics.

The most reliable path is to implement a **linear DC-OPF with reserve co-optimization** in Python using `cvxpy`, then extract economics from **dual variables**.

---

## When to Use This Skill

Use this workflow when the task explicitly says:

- the model is **DC-OPF**,
- reserves are **co-optimized** with energy,
- the input is MATPOWER-style bus/gen/branch data,
- the output requires **bus-level LMPs** and **reserve MCP**,
- a transmission line limit is changed in a counterfactual.

Do **not** use an AC power flow or nonlinear OPF workflow unless the task explicitly requires it.

---

## High-Level Workflow

### 1) Inspect the input file and runtime environment
Why:
- MATPOWER JSON schemas vary slightly across tasks.
- `python` may not exist, while `python3` usually does.
- `cvxpy` may not be preinstalled.

What to decide:
- Confirm top-level keys.
- Confirm bus/gen/branch arrays exist.
- Check whether generator cost and reserve fields are embedded in standard locations or custom fields.

```bash
set -euo pipefail

test -f network.json

jq 'keys' network.json
python3 --version
python3 -m pip --version || true
```

If `cvxpy` is missing, create a virtual environment instead of polluting the base image:

```bash
set -euo pipefail

python3 -m venv .venv_market
. .venv_market/bin/activate
python -m pip install --upgrade pip
python -m pip install cvxpy
python - <<'PY'
import cvxpy as cp
print("cvxpy version:", cp.__version__)
PY
```

Critical note:
- Prefer `python3` or `.venv_market/bin/python`; do not assume `python` exists on PATH.

---

### 2) Parse MATPOWER-style JSON into normalized tables
Why:
- You need stable indexing to formulate optimization matrices.
- MATPOWER bus IDs are not guaranteed to be contiguous.
- Generator and branch status flags must be honored.

What to decide:
- Filter out offline generators and branches.
- Build a `bus_id -> dense index` map.
- Convert power values to a consistent unit, typically MW and $/MWh.

```python
import json
from typing import Dict, List, Tuple, Any

def load_network(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        data = json.load(f)
    required = ["bus", "gen", "branch"]
    missing = [k for k in required if k not in data]
    if missing:
        raise KeyError(f"Missing required keys: {missing}")
    return data

def normalize_records(data: Dict[str, Any]) -> Tuple[List[dict], List[dict], List[dict], Dict[int, int]]:
    buses = [b for b in data["bus"] if int(b.get("bus_i", b.get("BUS_I", -1))) >= 0]
    if not buses:
        raise ValueError("No bus records found")

    buses = sorted(buses, key=lambda b: int(b.get("bus_i", b.get("BUS_I"))))
    bus_ids = [int(b.get("bus_i", b.get("BUS_I"))) for b in buses]
    bus_index = {bus_id: i for i, bus_id in enumerate(bus_ids)}

    gens = []
    for g in data["gen"]:
        status = int(g.get("gen_status", g.get("GEN_STATUS", 1)))
        bus_id = int(g.get("bus", g.get("GEN_BUS", -1)))
        if status <= 0:
            continue
        if bus_id not in bus_index:
            continue
        gens.append(g)
    if not gens:
        raise ValueError("No online generators found")

    branches = []
    for br in data["branch"]:
        status = int(br.get("br_status", br.get("BR_STATUS", 1)))
        fbus = int(br.get("f_bus", br.get("F_BUS", -1)))
        tbus = int(br.get("t_bus", br.get("T_BUS", -1)))
        if status <= 0:
            continue
        if fbus not in bus_index or tbus not in bus_index:
            continue
        branches.append(br)
    if not branches:
        raise ValueError("No online branches found")

    return buses, gens, branches, bus_index
```

Critical note:
- Never assume bus numbers are `1..N`; always map to dense internal indices.

---

### 3) Extract load, branch susceptance, line limits, generator bounds, energy bids, and reserve parameters
Why:
- The optimization model depends on these arrays.
- Reserve tasks often hide parameters in nonstandard JSON fields.

What to decide:
- Where energy bids come from:
  - `gencost`,
  - per-generator custom fields,
  - or a supplied linear cost term.
- Where reserve offers and reserve requirements come from.
- Whether branch reactance is `x`, `br_x`, or `BR_X`.

```python
import math
import numpy as np

def get_bus_load_mw(bus: dict) -> float:
    return float(bus.get("pd", bus.get("PD", 0.0)))

def get_gen_pmax(g: dict) -> float:
    return float(g.get("pmax", g.get("PMAX", 0.0)))

def get_gen_pmin(g: dict) -> float:
    return float(g.get("pmin", g.get("PMIN", 0.0)))

def get_gen_bus(g: dict) -> int:
    return int(g.get("bus", g.get("GEN_BUS")))

def get_branch_endpoints(br: dict) -> tuple[int, int]:
    return int(br.get("f_bus", br.get("F_BUS"))), int(br.get("t_bus", br.get("T_BUS")))

def get_branch_x(br: dict) -> float:
    x = float(br.get("br_x", br.get("x", br.get("BR_X", 0.0))))
    if abs(x) < 1e-9:
        raise ValueError(f"Branch has zero/near-zero reactance: {br}")
    return x

def get_branch_limit(br: dict) -> float:
    rate = float(br.get("rate_a", br.get("RATE_A", 0.0)))
    if rate <= 0:
        raise ValueError(f"Branch missing positive thermal limit RATE_A: {br}")
    return rate

def build_arrays(buses, gens, branches, bus_index):
    nb = len(buses)
    ng = len(gens)
    nl = len(branches)

    load = np.array([get_bus_load_mw(b) for b in buses], dtype=float)

    gen_bus_idx = np.array([bus_index[get_gen_bus(g)] for g in gens], dtype=int)
    pmin = np.array([get_gen_pmin(g) for g in gens], dtype=float)
    pmax = np.array([get_gen_pmax(g) for g in gens], dtype=float)

    f_idx = np.zeros(nl, dtype=int)
    t_idx = np.zeros(nl, dtype=int)
    b_series = np.zeros(nl, dtype=float)
    rate_a = np.zeros(nl, dtype=float)

    for i, br in enumerate(branches):
        fbus, tbus = get_branch_endpoints(br)
        f_idx[i] = bus_index[fbus]
        t_idx[i] = bus_index[tbus]
        b_series[i] = 1.0 / get_branch_x(br)
        rate_a[i] = get_branch_limit(br)

    return {
        "nb": nb,
        "ng": ng,
        "nl": nl,
        "load": load,
        "gen_bus_idx": gen_bus_idx,
        "pmin": pmin,
        "pmax": pmax,
        "f_idx": f_idx,
        "t_idx": t_idx,
        "b_series": b_series,
        "rate_a": rate_a,
    }
```

Critical notes:
- In DC-OPF, flow is usually `b * (theta_f - theta_t)`.
- Keep everything in MW and radians.
- A positive `RATE_A` is required for thermal constraints if binding-line analysis is required.

---

### 4) Build a robust cost / reserve data extractor
Why:
- Tasks often use linear bids, but the exact schema varies.
- The solver should fail loudly if reserve prices or requirements cannot be found.

What to decide:
- Reserve requirement may be:
  - system-wide scalar,
  - explicit field in JSON,
  - sum of zonal requirements,
  - or custom extension.
- Reserve offer may be:
  - per-generator custom field,
  - or inferred from another section.

```python
def extract_linear_energy_costs(data: dict, gens: list[dict]) -> np.ndarray:
    import numpy as np

    # Preferred: MATPOWER gencost aligned with gen rows
    gencost = data.get("gencost", [])
    if gencost and len(gencost) == len(data["gen"]):
        costs = []
        filtered_index = 0
        for raw_g, gc in zip(data["gen"], gencost):
            status = int(raw_g.get("gen_status", raw_g.get("GEN_STATUS", 1)))
            if status <= 0:
                continue
            model = int(gc.get("model", gc.get("MODEL", 2)))
            ncost = int(gc.get("ncost", gc.get("NCOST", 0)))
            coeffs = gc.get("cost", gc.get("COST", []))
            if model != 2 or ncost < 2 or len(coeffs) < 2:
                raise ValueError(f"Unsupported gencost row for linear extraction: {gc}")
            # For linear cost, MATPOWER polynomial with n=2 has [c1, c0]
            c1 = float(coeffs[-2])
            costs.append(c1)
            filtered_index += 1
        if len(costs) == len(gens):
            return np.array(costs, dtype=float)

    # Fallback: custom per-generator fields
    candidate_fields = ["energy_cost", "offer_price", "cost_per_mwh", "marginal_cost"]
    costs = []
    for g in gens:
        value = None
        for field in candidate_fields:
            if field in g:
                value = g[field]
                break
        if value is None:
            raise ValueError("Unable to extract linear energy cost from gencost or generator custom fields")
        costs.append(float(value))
    return np.array(costs, dtype=float)

def extract_reserve_prices(gens: list[dict]) -> np.ndarray:
    import numpy as np
    candidate_fields = ["reserve_cost", "reserve_offer", "reserve_price", "spin_offer"]
    values = []
    for g in gens:
        found = None
        for field in candidate_fields:
            if field in g:
                found = g[field]
                break
        if found is None:
            raise ValueError("Unable to find per-generator reserve offer field")
        values.append(float(found))
    return np.array(values, dtype=float)

def extract_system_reserve_requirement(data: dict) -> float:
    candidate_paths = [
        ("reserve_requirement",),
        ("spin_requirement",),
        ("reserves", "requirement"),
        ("ancillary", "spin_requirement"),
        ("system", "reserve_requirement"),
    ]
    for path in candidate_paths:
        cur = data
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok:
            return float(cur)
    raise ValueError("Unable to locate system reserve requirement in input JSON")
```

Critical note:
- Do not silently set reserve requirement to zero; that can pass feasibility but fail optimality and economics checks.

---

### 5) Formulate the DC-OPF with reserve co-optimization
Why:
- This is the core model.
- You need duals for bus balance and reserve requirement.

Model structure:
- Variables:
  - generator energy `pg[g]`,
  - generator spinning reserve `r[g]`,
  - bus voltage angles `theta[i]`,
  - line flows `f[l]`.
- Constraints:
  - bus balance at every bus,
  - DC flow equations,
  - line thermal limits,
  - generator bounds,
  - standard capacity coupling `pg + r <= pmax`,
  - reserve requirement `sum(r) >= R`,
  - reference angle `theta[ref] = 0`.
- Objective:
  - minimize `sum(c_energy * pg + c_reserve * r)`.

```python
import cvxpy as cp
import numpy as np

def solve_market_case(model_data: dict, energy_cost: np.ndarray, reserve_cost: np.ndarray, reserve_req: float):
    nb = model_data["nb"]
    ng = model_data["ng"]
    nl = model_data["nl"]

    load = model_data["load"]
    gen_bus_idx = model_data["gen_bus_idx"]
    pmin = model_data["pmin"]
    pmax = model_data["pmax"]
    f_idx = model_data["f_idx"]
    t_idx = model_data["t_idx"]
    b_series = model_data["b_series"]
    rate_a = model_data["rate_a"]

    pg = cp.Variable(ng)
    rg = cp.Variable(ng, nonneg=True)
    theta = cp.Variable(nb)
    flow = cp.Variable(nl)

    constraints = []

    # Generator bounds and capacity coupling
    constraints += [pg >= pmin, pg <= pmax]
    constraints += [pg + rg <= pmax]

    # Line DC equations and thermal limits
    for l in range(nl):
        constraints.append(flow[l] == b_series[l] * (theta[f_idx[l]] - theta[t_idx[l]]))
        constraints.append(flow[l] <= rate_a[l])
        constraints.append(flow[l] >= -rate_a[l])

    # Slack/reference bus
    constraints.append(theta[0] == 0.0)

    # Bus balance constraints
    balance_constraints = []
    for i in range(nb):
        gen_at_bus = cp.sum(pg[np.where(gen_bus_idx == i)[0]]) if np.any(gen_bus_idx == i) else 0.0
        inflow_terms = []
        outflow_terms = []
        for l in range(nl):
            if t_idx[l] == i:
                inflow_terms.append(flow[l])
            if f_idx[l] == i:
                outflow_terms.append(flow[l])
        inflow = cp.sum(cp.hstack(inflow_terms)) if inflow_terms else 0.0
        outflow = cp.sum(cp.hstack(outflow_terms)) if outflow_terms else 0.0
        c = (gen_at_bus + inflow - outflow == load[i])
        constraints.append(c)
        balance_constraints.append(c)

    reserve_constraint = (cp.sum(rg) >= reserve_req)
    constraints.append(reserve_constraint)

    objective = cp.Minimize(energy_cost @ pg + reserve_cost @ rg)
    problem = cp.Problem(objective, constraints)

    # Try several conic/LP solvers in order
    installed = cp.installed_solvers()
    preferred = ["GUROBI", "CPLEX", "MOSEK", "CLARABEL", "ECOS", "SCS", "OSQP"]
    last_err = None
    for solver in preferred:
        if solver not in installed:
            continue
        try:
            solve_kwargs = {"solver": solver, "verbose": False}
            if solver in {"SCS"}:
                solve_kwargs.update({"eps": 1e-6, "max_iters": 20000})
            if solver in {"OSQP"}:
                solve_kwargs.update({"eps_abs": 1e-6, "eps_rel": 1e-6, "max_iter": 200000})
            problem.solve(**solve_kwargs)
            if problem.status in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
                break
        except Exception as e:
            last_err = e
            continue

    if problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(f"Optimization failed; status={problem.status}; last_err={last_err}")

    # Economics from duals
    # Depending on sign convention, bus-balance duals may need negation.
    balance_duals = np.array([c.dual_value for c in balance_constraints], dtype=float)
    lmp = -balance_duals

    reserve_mcp = float(reserve_constraint.dual_value)

    return {
        "objective": float(problem.value),
        "pg": np.array(pg.value, dtype=float),
        "rg": np.array(rg.value, dtype=float),
        "theta": np.array(theta.value, dtype=float),
        "flow": np.array(flow.value, dtype=float),
        "lmp": lmp,
        "reserve_mcp": reserve_mcp,
        "status": problem.status,
    }
```

Critical note:
- For equality constraints, `cvxpy` dual sign conventions commonly imply `LMP = -dual(balance)`. Verify the sign by checking whether prices are economically sensible.

---

### 6) Apply the counterfactual by modifying the target line limit
Why:
- The problem statement often specifies a single branch `(from_bus, to_bus)` and a percentage increase.
- Branch direction in data may be `(to, from)` instead of `(from, to)`.

What to decide:
- Match the branch regardless of order.
- Only modify the intended branch or branches.
- Copy arrays before mutation.

```python
import numpy as np

def scale_branch_limit(branches: list[dict], from_bus: int, to_bus: int, factor: float) -> list[dict]:
    if factor <= 0:
        raise ValueError("Scale factor must be positive")
    updated = []
    hits = 0
    for br in branches:
        f = int(br.get("f_bus", br.get("F_BUS")))
        t = int(br.get("t_bus", br.get("T_BUS")))
        new_br = dict(br)
        if (f == from_bus and t == to_bus) or (f == to_bus and t == from_bus):
            old_limit = float(br.get("rate_a", br.get("RATE_A", 0.0)))
            if old_limit <= 0:
                raise ValueError(f"Target branch has non-positive RATE_A: {br}")
            new_limit = old_limit * factor
            if "rate_a" in new_br:
                new_br["rate_a"] = new_limit
            else:
                new_br["RATE_A"] = new_limit
            hits += 1
        updated.append(new_br)
    if hits == 0:
        raise ValueError(f"No branch matched endpoints ({from_bus}, {to_bus})")
    return updated
```

Critical note:
- Search both `(from, to)` and `(to, from)`. Missing this is a common reason counterfactual results appear unchanged.

---

### 7) Identify binding or near-binding lines
Why:
- The report requires lines with loading level at or above a threshold, typically `>= 99%`.

What to decide:
- Use absolute flow magnitude.
- Report original bus IDs, actual flow, and limit.
- Use a small tolerance for numerical noise.

```python
def find_binding_lines(branches: list[dict], flow: np.ndarray, threshold: float = 0.99) -> list[dict]:
    bindings = []
    for br, f in zip(branches, flow):
        limit = float(br.get("rate_a", br.get("RATE_A")))
        if limit <= 0:
            continue
        loading = abs(float(f)) / limit if limit > 0 else 0.0
        if loading >= threshold - 1e-9:
            fbus = int(br.get("f_bus", br.get("F_BUS")))
            tbus = int(br.get("t_bus", br.get("T_BUS")))
            bindings.append({
                "from": fbus,
                "to": tbus,
                "flow_MW": float(f),
                "limit_MW": float(limit),
            })
    return bindings
```

Critical note:
- Use `abs(flow) / limit`, not `flow / limit`, or you will miss negatively loaded but binding lines.

---

### 8) Build the exact report schema and compute impact metrics
Why:
- Validators often enforce field presence, nesting, and list structure strictly.
- `lmp_by_bus` should usually be sorted by bus number and contain every bus exactly once.

What to decide:
- Cost reduction = base cost â counterfactual cost.
- Largest LMP drops = most negative `cf_lmp - base_lmp` or equivalently smallest delta.
- Congestion relieved = target line is not binding in the counterfactual.

```python
import json

def format_lmp_by_bus(buses: list[dict], lmp: np.ndarray) -> list[dict]:
    pairs = []
    for bus, price in zip(buses, lmp):
        bus_id = int(bus.get("bus_i", bus.get("BUS_I")))
        pairs.append({"bus": bus_id, "lmp_dollars_per_MWh": float(price)})
    pairs.sort(key=lambda x: x["bus"])
    return pairs

def top_lmp_drops(buses: list[dict], base_lmp: np.ndarray, cf_lmp: np.ndarray, k: int = 3) -> list[dict]:
    items = []
    for bus, bp, cp in zip(buses, base_lmp, cf_lmp):
        bus_id = int(bus.get("bus_i", bus.get("BUS_I")))
        delta = float(cp - bp)
        items.append({
            "bus": bus_id,
            "base_lmp": float(bp),
            "cf_lmp": float(cp),
            "delta": float(delta),
        })
    items.sort(key=lambda x: x["delta"])  # most negative first
    return items[:k]

def target_line_not_binding(binding_lines: list[dict], from_bus: int, to_bus: int) -> bool:
    for item in binding_lines:
        f = int(item["from"])
        t = int(item["to"])
        if (f == from_bus and t == to_bus) or (f == to_bus and t == from_bus):
            return False
    return True

def write_report(path: str, report: dict):
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
```

Exact expected structure:

```json
{
  "base_case": {
    "total_cost_dollars_per_hour": 0.0,
    "lmp_by_bus": [
      {"bus": 1, "lmp_dollars_per_MWh": 0.0}
    ],
    "reserve_mcp_dollars_per_MWh": 0.0,
    "binding_lines": [
      {"from": 1, "to": 2, "flow_MW": 0.0, "limit_MW": 0.0}
    ]
  },
  "counterfactual": {
    "total_cost_dollars_per_hour": 0.0,
    "lmp_by_bus": [
      {"bus": 1, "lmp_dollars_per_MWh": 0.0}
    ],
    "reserve_mcp_dollars_per_MWh": 0.0,
    "binding_lines": []
  },
  "impact_analysis": {
    "cost_reduction_dollars_per_hour": 0.0,
    "buses_with_largest_lmp_drop": [
      {"bus": 1, "base_lmp": 0.0, "cf_lmp": 0.0, "delta": -1.0}
    ],
    "congestion_relieved": true
  }
}
```

---

### 9) Verify internal consistency before finalizing
Why:
- Passing optimization does not guarantee passing the validator.
- Small schema issues or unsorted buses can fail tests.

Checklist:
- `lmp_by_bus` includes every bus exactly once.
- Bus list is sorted ascending.
- `cost_reduction_dollars_per_hour >= 0` if the counterfactual is a relaxation.
- Target line is binding in base if the narrative implies it, but only report what the solve shows.
- Reserve MCP is numeric, not null.
- Binding line threshold uses `>= 99%`.

```python
def validate_report(report: dict):
    for section in ["base_case", "counterfactual"]:
        block = report[section]
        assert isinstance(block["total_cost_dollars_per_hour"], (int, float))
        assert isinstance(block["reserve_mcp_dollars_per_MWh"], (int, float))
        buses = [x["bus"] for x in block["lmp_by_bus"]]
        assert buses == sorted(buses), f"{section}: buses must be sorted"
        assert len(buses) == len(set(buses)), f"{section}: duplicate buses found"
        for row in block["binding_lines"]:
            for key in ["from", "to", "flow_MW", "limit_MW"]:
                assert key in row, f"{section}: missing binding line field {key}"
    impact = report["impact_analysis"]
    assert isinstance(impact["congestion_relieved"], bool)
    assert len(impact["buses_with_largest_lmp_drop"]) <= 3
```

---

## Reference Implementation

The following script is a complete end-to-end template: load data, extract parameters, solve base and counterfactual cases, compute LMPs and reserve MCP, identify binding lines, assemble the required JSON, and write `report.json`.

Adapt only the reserve-field extractor and target line parameters if a new task uses different custom JSON field names.

```python
#!/usr/bin/env python3
import json
import math
import sys
from copy import deepcopy
from typing import Any, Dict, List, Tuple

import numpy as np

try:
    import cvxpy as cp
except Exception as e:
    raise SystemExit(
        "cvxpy is required. Install with:\n"
        "  python3 -m venv .venv_market && . .venv_market/bin/activate && pip install cvxpy\n"
        f"Import error: {e}"
    )

INPUT_PATH = "network.json"
OUTPUT_PATH = "report.json"

# Replace these with task-provided values when needed.
TARGET_FROM_BUS = 64
TARGET_TO_BUS = 1501
LINE_LIMIT_MULTIPLIER = 1.20
BINDING_THRESHOLD = 0.99


def load_network(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        data = json.load(f)
    required = ["bus", "gen", "branch"]
    missing = [k for k in required if k not in data]
    if missing:
        raise KeyError(f"Missing required keys: {missing}")
    return data


def normalize_records(data: Dict[str, Any]) -> Tuple[List[dict], List[dict], List[dict], Dict[int, int]]:
    buses = [b for b in data["bus"] if int(b.get("bus_i", b.get("BUS_I", -1))) >= 0]
    if not buses:
        raise ValueError("No bus records found")

    buses = sorted(buses, key=lambda b: int(b.get("bus_i", b.get("BUS_I"))))
    bus_ids = [int(b.get("bus_i", b.get("BUS_I"))) for b in buses]
    bus_index = {bus_id: i for i, bus_id in enumerate(bus_ids)}

    gens = []
    for g in data["gen"]:
        status = int(g.get("gen_status", g.get("GEN_STATUS", 1)))
        bus_id = int(g.get("bus", g.get("GEN_BUS", -1)))
        if status <= 0:
            continue
        if bus_id not in bus_index:
            continue
        gens.append(g)
    if not gens:
        raise ValueError("No online generators found")

    branches = []
    for br in data["branch"]:
        status = int(br.get("br_status", br.get("BR_STATUS", 1)))
        fbus = int(br.get("f_bus", br.get("F_BUS", -1)))
        tbus = int(br.get("t_bus", br.get("T_BUS", -1)))
        if status <= 0:
            continue
        if fbus not in bus_index or tbus not in bus_index:
            continue
        branches.append(br)
    if not branches:
        raise ValueError("No online branches found")

    return buses, gens, branches, bus_index


def get_bus_load_mw(bus: dict) -> float:
    return float(bus.get("pd", bus.get("PD", 0.0)))


def get_gen_pmax(g: dict) -> float:
    return float(g.get("pmax", g.get("PMAX", 0.0)))


def get_gen_pmin(g: dict) -> float:
    return float(g.get("pmin", g.get("PMIN", 0.0)))


def get_gen_bus(g: dict) -> int:
    return int(g.get("bus", g.get("GEN_BUS")))


def get_branch_endpoints(br: dict) -> tuple[int, int]:
    return int(br.get("f_bus", br.get("F_BUS"))), int(br.get("t_bus", br.get("T_BUS")))


def get_branch_x(br: dict) -> float:
    x = float(br.get("br_x", br.get("x", br.get("BR_X", 0.0))))
    if abs(x) < 1e-9:
        raise ValueError(f"Branch has zero/near-zero reactance: {br}")
    return x


def get_branch_limit(br: dict) -> float:
    rate = float(br.get("rate_a", br.get("RATE_A", 0.0)))
    if rate <= 0:
        raise ValueError(f"Branch missing positive thermal limit RATE_A: {br}")
    return rate


def build_arrays(buses, gens, branches, bus_index):
    nb = len(buses)
    ng = len(gens)
    nl = len(branches)

    load = np.array([get_bus_load_mw(b) for b in buses], dtype=float)
    gen_bus_idx = np.array([bus_index[get_gen_bus(g)] for g in gens], dtype=int)
    pmin = np.array([get_gen_pmin(g) for g in gens], dtype=float)
    pmax = np.array([get_gen_pmax(g) for g in gens], dtype=float)

    f_idx = np.zeros(nl, dtype=int)
    t_idx = np.zeros(nl, dtype=int)
    b_series = np.zeros(nl, dtype=float)
    rate_a = np.zeros(nl, dtype=float)

    for i, br in enumerate(branches):
        fbus, tbus = get_branch_endpoints(br)
        f_idx[i] = bus_index[fbus]
        t_idx[i] = bus_index[tbus]
        b_series[i] = 1.0 / get_branch_x(br)
        rate_a[i] = get_branch_limit(br)

    return {
        "nb": nb,
        "ng": ng,
        "nl": nl,
        "load": load,
        "gen_bus_idx": gen_bus_idx,
        "pmin": pmin,
        "pmax": pmax,
        "f_idx": f_idx,
        "t_idx": t_idx,
        "b_series": b_series,
        "rate_a": rate_a,
    }


def extract_linear_energy_costs(data: dict, gens: list[dict]) -> np.ndarray:
    gencost = data.get("gencost", [])
    if gencost and len(gencost) == len(data["gen"]):
        costs = []
        for raw_g, gc in zip(data["gen"], gencost):
            status = int(raw_g.get("gen_status", raw_g.get("GEN_STATUS", 1)))
            if status <= 0:
                continue
            model = int(gc.get("model", gc.get("MODEL", 2)))
            ncost = int(gc.get("ncost", gc.get("NCOST", 0)))
            coeffs = gc.get("cost", gc.get("COST", []))
            if model == 2 and ncost >= 2 and len(coeffs) >= 2:
                c1 = float(coeffs[-2])
                costs.append(c1)
            else:
                raise ValueError(f"Unsupported polynomial gencost row: {gc}")
        if len(costs) == len(gens):
            return np.array(costs, dtype=float)

    candidate_fields = ["energy_cost", "offer_price", "cost_per_mwh", "marginal_cost"]
    values = []
    for g in gens:
        v = None
        for field in candidate_fields:
            if field in g:
                v = g[field]
                break
        if v is None:
            raise ValueError("Unable to extract linear energy cost from gencost or generator fields")
        values.append(float(v))
    return np.array(values, dtype=float)


def extract_reserve_prices(gens: list[dict]) -> np.ndarray:
    candidate_fields = ["reserve_cost", "reserve_offer", "reserve_price", "spin_offer"]
    values = []
    for g in gens:
        v = None
        for field in candidate_fields:
            if field in g:
                v = g[field]
                break
        if v is None:
            raise ValueError("Unable to find per-generator reserve offer field")
        values.append(float(v))
    return np.array(values, dtype=float)


def extract_system_reserve_requirement(data: dict) -> float:
    candidate_paths = [
        ("reserve_requirement",),
        ("spin_requirement",),
        ("reserves", "requirement"),
        ("ancillary", "spin_requirement"),
        ("system", "reserve_requirement"),
    ]
    for path in candidate_paths:
        cur = data
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok:
            return float(cur)

    # Optional extension: infer from custom scalar fields on the root object
    for key, value in data.items():
        if isinstance(key, str) and "reserve" in key.lower() and "require" in key.lower():
            try:
                return float(value)
            except Exception:
                pass

    raise ValueError("Unable to locate system reserve requirement in input JSON")


def solve_market_case(model_data: dict, energy_cost: np.ndarray, reserve_cost: np.ndarray, reserve_req: float):
    nb = model_data["nb"]
    ng = model_data["ng"]
    nl = model_data["nl"]

    load = model_data["load"]
    gen_bus_idx = model_data["gen_bus_idx"]
    pmin = model_data["pmin"]
    pmax = model_data["pmax"]
    f_idx = model_data["f_idx"]
    t_idx = model_data["t_idx"]
    b_series = model_data["b_series"]
    rate_a = model_data["rate_a"]

    pg = cp.Variable(ng)
    rg = cp.Variable(ng, nonneg=True)
    theta = cp.Variable(nb)
    flow = cp.Variable(nl)

    constraints = []
    constraints += [pg >= pmin, pg <= pmax]
    constraints += [pg + rg <= pmax]

    for l in range(nl):
        constraints.append(flow[l] == b_series[l] * (theta[f_idx[l]] - theta[t_idx[l]]))
        constraints.append(flow[l] <= rate_a[l])
        constraints.append(flow[l] >= -rate_a[l])

    constraints.append(theta[0] == 0.0)

    balance_constraints = []
    gen_by_bus = [[] for _ in range(nb)]
    for g in range(ng):
        gen_by_bus[gen_bus_idx[g]].append(g)

    lines_from = [[] for _ in range(nb)]
    lines_to = [[] for _ in range(nb)]
    for l in range(nl):
        lines_from[f_idx[l]].append(l)
        lines_to[t_idx[l]].append(l)

    for i in range(nb):
        gen_term = cp.sum(pg[gen_by_bus[i]]) if gen_by_bus[i] else 0.0
        inflow = cp.sum(flow[lines_to[i]]) if lines_to[i] else 0.0
        outflow = cp.sum(flow[lines_from[i]]) if lines_from[i] else 0.0
        c = (gen_term + inflow - outflow == load[i])
        constraints.append(c)
        balance_constraints.append(c)

    reserve_constraint = (cp.sum(rg) >= reserve_req)
    constraints.append(reserve_constraint)

    objective = cp.Minimize(energy_cost @ pg + reserve_cost @ rg)
    problem = cp.Problem(objective, constraints)

    installed = cp.installed_solvers()
    preferred = ["GUROBI", "CPLEX", "MOSEK", "CLARABEL", "ECOS", "SCS", "OSQP"]
    last_err = None
    for solver in preferred:
        if solver not in installed:
            continue
        try:
            solve_kwargs = {"solver": solver, "verbose": False}
            if solver == "SCS":
                solve_kwargs.update({"eps": 1e-6, "max_iters": 20000})
            elif solver == "OSQP":
                solve_kwargs.update({"eps_abs": 1e-6, "eps_rel": 1e-6, "max_iter": 200000})
            problem.solve(**solve_kwargs)
            if problem.status in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
                break
        except Exception as e:
            last_err = e
            continue

    if problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(f"Optimization failed; status={problem.status}; last_err={last_err}")

    balance_duals = np.array([c.dual_value for c in balance_constraints], dtype=float)
    lmp = -balance_duals
    reserve_mcp = float(reserve_constraint.dual_value)

    return {
        "objective": float(problem.value),
        "pg": np.array(pg.value, dtype=float),
        "rg": np.array(rg.value, dtype=float),
        "theta": np.array(theta.value, dtype=float),
        "flow": np.array(flow.value, dtype=float),
        "lmp": lmp,
        "reserve_mcp": reserve_mcp,
        "status": problem.status,
    }


def scale_branch_limit(branches: list[dict], from_bus: int, to_bus: int, factor: float) -> list[dict]:
    if factor <= 0:
        raise ValueError("Scale factor must be positive")
    updated = []
    hits = 0
    for br in branches:
        f = int(br.get("f_bus", br.get("F_BUS")))
        t = int(br.get("t_bus", br.get("T_BUS")))
        new_br = dict(br)
        if (f == from_bus and t == to_bus) or (f == to_bus and t == from_bus):
            old_limit = float(br.get("rate_a", br.get("RATE_A", 0.0)))
            if old_limit <= 0:
                raise ValueError(f"Target branch has non-positive RATE_A: {br}")
            new_limit = old_limit * factor
            if "rate_a" in new_br:
                new_br["rate_a"] = new_limit
            else:
                new_br["RATE_A"] = new_limit
            hits += 1
        updated.append(new_br)
    if hits == 0:
        raise ValueError(f"No branch matched endpoints ({from_bus}, {to_bus})")
    return updated


def find_binding_lines(branches: list[dict], flow: np.ndarray, threshold: float = 0.99) -> list[dict]:
    bindings = []
    for br, f in zip(branches, flow):
        limit = float(br.get("rate_a", br.get("RATE_A")))
        if limit <= 0:
            continue
        if abs(float(f)) / limit >= threshold - 1e-9:
            bindings.append({
                "from": int(br.get("f_bus", br.get("F_BUS"))),
                "to": int(br.get("t_bus", br.get("T_BUS"))),
                "flow_MW": float(f),
                "limit_MW": float(limit),
            })
    return bindings


def format_lmp_by_bus(buses: list[dict], lmp: np.ndarray) -> list[dict]:
    out = []
    for bus, price in zip(buses, lmp):
        out.append({
            "bus": int(bus.get("bus_i", bus.get("BUS_I"))),
            "lmp_dollars_per_MWh": float(price),
        })
    out.sort(key=lambda x: x["bus"])
    return out


def top_lmp_drops(buses: list[dict], base_lmp: np.ndarray, cf_lmp: np.ndarray, k: int = 3) -> list[dict]:
    items = []
    for bus, bp, cpv in zip(buses, base_lmp, cf_lmp):
        delta = float(cpv - bp)
        items.append({
            "bus": int(bus.get("bus_i", bus.get("BUS_I"))),
            "base_lmp": float(bp),
            "cf_lmp": float(cpv),
            "delta": delta,
        })
    items.sort(key=lambda x: x["delta"])
    return items[:k]


def target_line_not_binding(binding_lines: list[dict], from_bus: int, to_bus: int) -> bool:
    for row in binding_lines:
        f = int(row["from"])
        t = int(row["to"])
        if (f == from_bus and t == to_bus) or (f == to_bus and t == from_bus):
            return False
    return True


def validate_report(report: dict):
    for section in ["base_case", "counterfactual"]:
        block = report[section]
        assert isinstance(block["total_cost_dollars_per_hour"], (int, float))
        assert isinstance(block["reserve_mcp_dollars_per_MWh"], (int, float))
        buses = [x["bus"] for x in block["lmp_by_bus"]]
        assert buses == sorted(buses), f"{section}: buses not sorted"
        assert len(buses) == len(set(buses)), f"{section}: duplicate buses"
        for row in block["binding_lines"]:
            for key in ["from", "to", "flow_MW", "limit_MW"]:
                assert key in row, f"{section}: missing {key}"
    impact = report["impact_analysis"]
    assert isinstance(impact["congestion_relieved"], bool)
    assert len(impact["buses_with_largest_lmp_drop"]) <= 3


def main():
    data = load_network(INPUT_PATH)

    buses, gens, branches, bus_index = normalize_records(data)
    arrays = build_arrays(buses, gens, branches, bus_index)
    energy_cost = extract_linear_energy_costs(data, gens)
    reserve_cost = extract_reserve_prices(gens)
    reserve_req = extract_system_reserve_requirement(data)

    base = solve_market_case(arrays, energy_cost, reserve_cost, reserve_req)
    base_binding = find_binding_lines(branches, base["flow"], threshold=BINDING_THRESHOLD)

    cf_branches = scale_branch_limit(branches, TARGET_FROM_BUS, TARGET_TO_BUS, LINE_LIMIT_MULTIPLIER)
    cf_arrays = build_arrays(buses, gens, cf_branches, bus_index)
    cf = solve_market_case(cf_arrays, energy_cost, reserve_cost, reserve_req)
    cf_binding = find_binding_lines(cf_branches, cf["flow"], threshold=BINDING_THRESHOLD)

    report = {
        "base_case": {
            "total_cost_dollars_per_hour": float(base["objective"]),
            "lmp_by_bus": format_lmp_by_bus(buses, base["lmp"]),
            "reserve_mcp_dollars_per_MWh": float(base["reserve_mcp"]),
            "binding_lines": base_binding,
        },
        "counterfactual": {
            "total_cost_dollars_per_hour": float(cf["objective"]),
            "lmp_by_bus": format_lmp_by_bus(buses, cf["lmp"]),
            "reserve_mcp_dollars_per_MWh": float(cf["reserve_mcp"]),
            "binding_lines": cf_binding,
        },
        "impact_analysis": {
            "cost_reduction_dollars_per_hour": float(base["objective"] - cf["objective"]),
            "buses_with_largest_lmp_drop": top_lmp_drops(buses, base["lmp"], cf["lmp"], k=3),
            "congestion_relieved": target_line_not_binding(cf_binding, TARGET_FROM_BUS, TARGET_TO_BUS),
        },
    }

    validate_report(report)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    print(f"Wrote {OUTPUT_PATH}")
    print("Base objective:", report["base_case"]["total_cost_dollars_per_hour"])
    print("Counterfactual objective:", report["counterfactual"]["total_cost_dollars_per_hour"])
    print("Cost reduction:", report["impact_analysis"]["cost_reduction_dollars_per_hour"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
```

---

## Quick Execution Recipe

If `cvxpy` is not installed:

```bash
set -euo pipefail
python3 -m venv .venv_market
. .venv_market/bin/activate
pip install --upgrade pip
pip install cvxpy
python solve_market.py
```

If `cvxpy` is already available:

```bash
set -euo pipefail
python3 solve_market.py
jq 'keys' report.json
```

---

## Interpreting Duals Correctly

### LMPs
For the balance equation written as:

```text
generation + inflow - outflow = demand
```

the `cvxpy` dual on this equality is commonly the negative of the economic LMP. So:

```python
lmp = -dual_value
```

Always sanity-check:
- LMPs should generally be in plausible $/MWh ranges.
- If all LMPs are large negative values in a system with positive bids, the sign is probably flipped.

### Reserve MCP
For the reserve requirement:

```text
sum(r) >= R
```

the dual value is typically the system reserve marginal clearing price, assuming your sign conventions are standard.

---

## Output Conventions

Use these conventions consistently:

- **Energy**: MW
- **Flow**: MW
- **Thermal limits**: MW
- **Energy price / LMP**: dollars per MWh
- **Reserve MCP**: dollars per MWh
- **Objective / total cost**: dollars per hour

Sort `lmp_by_bus` by ascending bus number.

Report every bus exactly once.

---

## Common Pitfalls

### 1) Using `python` instead of `python3`
In minimal Ubuntu environments, `python` may not exist.
- Use `python3`.
- If installing dependencies, prefer a virtual environment.

### 2) Forgetting to map noncontiguous bus IDs to dense indices
MATPOWER bus IDs may jump or be sparse.
- Always build `bus_id -> index`.
- Never use bus numbers directly as array offsets.

### 3) Missing the target branch because direction is reversed
The line may be stored as `(1501, 64)` even if the task says `64 to 1501`.
- Match both orders.

### 4) Getting LMP sign wrong
Dual sign conventions matter.
- If prices look inverted, use `lmp = -balance_dual`.

### 5) Ignoring reserve requirement because the schema is unfamiliar
A zero-reserve fallback can produce a feasible but economically wrong result.
- Fail loudly if reserve requirement or reserve offer fields cannot be found.
- Inspect JSON structure first.

### 6) Reporting only âstrictly bindingâ lines
Validators often define binding as `loading >= 99%`, not exactly 100%.
- Use `abs(flow)/limit >= 0.99`.

### 7) Forgetting standard capacity coupling
Reserve co-optimization requires:
```text
pg + rg <= pmax
```
Without this, reserve prices and dispatch can be wrong.

### 8) Returning incomplete bus pricing
Some validators expect one LMP per bus.
- Do not omit loadless or island-like buses unless the model/input explicitly removes them.

### 9) Mutating the base-case data in place
If you modify branch limits directly on the original object, the base and counterfactual can become identical or contaminated.
- Copy branch records before editing.

### 10) Assuming solver availability without checking
`cvxpy` may be absent in the base image.
- Create a venv and install it.
- Try multiple installed solvers in order.

---

## Minimal Post-Solve Sanity Checks

Run these before finalizing:

```python
import json

with open("report.json") as f:
    r = json.load(f)

for section in ["base_case", "counterfactual"]:
    buses = [x["bus"] for x in r[section]["lmp_by_bus"]]
    print(section, "bus_count", len(buses), "unique", len(buses) == len(set(buses)))
    print(section, "sorted", buses[:5], buses[-5:])

print("impact keys:", list(r["impact_analysis"].keys()))
print("cost reduction:", r["impact_analysis"]["cost_reduction_dollars_per_hour"])
print("congestion relieved:", r["impact_analysis"]["congestion_relieved"])
```

Expected properties:
- buses are unique and sorted,
- the report has `base_case`, `counterfactual`, and `impact_analysis`,
- cost reduction is usually nonnegative for a line-limit relaxation.

---

## Strategy Guidance for Future Agents

Choose this execution path early:

1. **Inspect JSON keys first** instead of guessing schema.
2. **Use Python + cvxpy**, not hand algebra or spreadsheet logic.
3. **Install dependencies in a venv** if missing.
4. **Model the exact stated market design**:
   - DC power balance,
   - generator and line limits,
   - reserve requirement,
   - capacity coupling.
5. **Extract economics from duals**, not from ad hoc heuristics.
6. **Validate the JSON structure before stopping**.

This path is robust, transferable, and aligned with validator expectations for market-pricing counterfactual tasks.