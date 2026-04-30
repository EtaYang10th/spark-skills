---
name: dc_opf_reserve_counterfactual_analysis
description: Solve day-ahead energy market what-if studies on MATPOWER-style network JSON using DC-OPF with spinning reserve co-optimization, compare base vs counterfactual transmission scenarios, and write a validator-friendly report.json.
category: energy-market-pricing
tags:
  - power-systems
  - dc-opf
  - market-clearing
  - reserve-cooptimization
  - locational-marginal-pricing
  - matpower
  - cvxpy
tools:
  - python3
  - python3-pip
  - python3-venv
  - jq
  - numpy
  - cvxpy
---

# DC-OPF Reserve Co-Optimization Counterfactual Analysis

Use this skill when you must:
- load a MATPOWER-style `network.json`
- run **two market clears**: base case and a transmission-capacity counterfactual
- model **DC-OPF with spinning reserve co-optimization**
- report:
  - total production cost
  - LMP by bus
  - reserve market clearing price
  - binding transmission lines
  - scenario-to-scenario impacts

This pattern is common in RTO/ISO-style congestion analysis: identify whether one constrained line is driving price separation, then test how prices and costs change when that line is relaxed.

---

## When to Use This Skill

Choose this path if the task mentions most of the following:

1. **Network input in MATPOWER format** (`bus`, `gen`, `branch`, `gencost`, often `baseMVA`)
2. **Day-ahead market clearing**, **DC power flow**, or **DC-OPF**
3. **Reserves** with a **system-wide reserve requirement**
4. A **counterfactual change** to one branch limit, generator limit, or load assumption
5. Required outputs like **LMPs**, **binding lines**, **cost**, and **congestion relief**

Do **not** start by hand-editing output values or guessing prices. These tasks are solver-based and internally cross-checked.

---

## High-Level Workflow

### 1) Inspect the input schema before modeling
Why:
- MATPOWER-like JSONs vary slightly by dataset.
- You need to confirm how buses, generators, branches, costs, and reserve data are stored.
- A wrong assumption about indexing or field position will corrupt the entire optimization.

What to verify:
- top-level keys: `baseMVA`, `bus`, `gen`, `branch`, `gencost`
- optional reserve fields: e.g. `reserve_capacity`, `reserve_requirement`
- bus numbering convention: MATPOWER bus IDs are often non-contiguous
- branch thermal limit field (`rateA` in MATPOWER column layout)
- generator PMIN/PMAX and bus attachment column positions
- linear vs quadratic generator costs from `gencost`

```python
#!/usr/bin/env python3
import json
from pathlib import Path

def inspect_network(path: str) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Missing input file: {path}")

    with p.open() as f:
        data = json.load(f)

    print("Top-level keys:", sorted(data.keys()))
    for key in ["baseMVA", "bus", "gen", "branch", "gencost"]:
        if key not in data:
            raise KeyError(f"Required key missing: {key}")

    print("Counts:")
    print("  buses   :", len(data["bus"]))
    print("  gens    :", len(data["gen"]))
    print("  branches:", len(data["branch"]))
    print("  gencost :", len(data["gencost"]))

    if data["bus"]:
        print("Sample bus row:", data["bus"][0])
    if data["gen"]:
        print("Sample gen row:", data["gen"][0])
    if data["branch"]:
        print("Sample branch row:", data["branch"][0])
    if data["gencost"]:
        print("Sample gencost row:", data["gencost"][0])

if __name__ == "__main__":
    inspect_network("network.json")
```

Useful shell inspection:

```bash
jq 'keys' network.json
jq '{baseMVA: .baseMVA, bus_len: (.bus|length), gen_len: (.gen|length), branch_len: (.branch|length), gencost_len: (.gencost|length), reserve_capacity_len: (.reserve_capacity|length?)}' network.json
jq '.bus[0], .gen[0], .branch[0], .gencost[0]' network.json
```

---

### 2) Normalize MATPOWER indexing into solver-friendly arrays
Why:
- Bus IDs in MATPOWER are labels, not zero-based solver indices.
- You must build a stable mapping from bus number to array position.
- Every subsequent structure depends on this mapping: demand vector, incidence matrices, generator-to-bus mapping, branch constraints, and output formatting.

Key conventions:
- Angles are in radians internally.
- Power quantities are typically in MW in these tasks; stay consistent.
- For DC power flow:
  - flow on line `l`: `f_l = b_l * (theta_from - theta_to)`
  - `b_l = 1 / x_l` if reactance `x_l != 0`
- Use one slack/reference angle, e.g. set one bus angle to 0.

```python
#!/usr/bin/env python3
import json
import numpy as np

# MATPOWER standard column positions used most often
BUS_I = 0
PD = 2

GEN_BUS = 0
PG = 1
PMAX = 8
PMIN = 9
GEN_STATUS = 7

F_BUS = 0
T_BUS = 1
BR_X = 3
RATE_A = 5
BR_STATUS = 10

def build_indices(data: dict):
    bus_rows = data["bus"]
    gen_rows = data["gen"]
    branch_rows = data["branch"]

    bus_ids = [int(row[BUS_I]) for row in bus_rows]
    bus_id_to_idx = {bus_id: i for i, bus_id in enumerate(bus_ids)}

    demand = np.array([float(row[PD]) for row in bus_rows], dtype=float)

    active_gens = []
    for g in gen_rows:
        status = int(g[GEN_STATUS]) if len(g) > GEN_STATUS else 1
        if status > 0:
            active_gens.append(g)

    gen_bus_idx = np.array([bus_id_to_idx[int(g[GEN_BUS])] for g in active_gens], dtype=int)
    pmin = np.array([float(g[PMIN]) for g in active_gens], dtype=float)
    pmax = np.array([float(g[PMAX]) for g in active_gens], dtype=float)

    active_branches = []
    for br in branch_rows:
        status = int(br[BR_STATUS]) if len(br) > BR_STATUS else 1
        if status > 0:
            active_branches.append(br)

    f_idx = np.array([bus_id_to_idx[int(br[F_BUS])] for br in active_branches], dtype=int)
    t_idx = np.array([bus_id_to_idx[int(br[T_BUS])] for br in active_branches], dtype=int)
    x = np.array([float(br[BR_X]) for br in active_branches], dtype=float)

    if np.any(np.isclose(x, 0.0)):
        raise ValueError("Zero reactance branch encountered; DC susceptance would blow up.")

    b = 1.0 / x
    rateA = np.array([float(br[RATE_A]) for br in active_branches], dtype=float)

    return {
        "bus_ids": bus_ids,
        "bus_id_to_idx": bus_id_to_idx,
        "demand": demand,
        "active_gens": active_gens,
        "gen_bus_idx": gen_bus_idx,
        "pmin": pmin,
        "pmax": pmax,
        "active_branches": active_branches,
        "f_idx": f_idx,
        "t_idx": t_idx,
        "b": b,
        "rateA": rateA,
    }

with open("network.json") as f:
    data = json.load(f)

parsed = build_indices(data)
print("Parsed buses:", len(parsed["bus_ids"]))
print("Parsed active generators:", len(parsed["active_gens"]))
print("Parsed active branches:", len(parsed["active_branches"]))
```

---

### 3) Extract generator cost curves robustly
Why:
- Validator-grade results require matching the cost function represented in `gencost`.
- Many MATPOWER files use polynomial costs with either:
  - linear: `c1 * P + c0`
  - quadratic: `c2 * P^2 + c1 * P + c0`
- You must align active generators to active `gencost` rows.

Decision rule:
- For `gencost` model type 2 (polynomial), parse the trailing coefficients.
- Support at least 1st- and 2nd-order curves.
- If higher order appears, fail clearly rather than silently approximating.

```python
#!/usr/bin/env python3
import json
import numpy as np

MODEL = 0
NCOST = 3

GEN_STATUS = 7

def extract_active_gencost(data: dict):
    gen_rows = data["gen"]
    cost_rows = data["gencost"]

    if len(cost_rows) < len(gen_rows):
        raise ValueError("gencost has fewer rows than gen; cannot align costs safely.")

    active_costs = []
    active_gen_indices = []

    for i, g in enumerate(gen_rows):
        status = int(g[GEN_STATUS]) if len(g) > GEN_STATUS else 1
        if status > 0:
            active_gen_indices.append(i)
            active_costs.append(cost_rows[i])

    quad = []
    lin = []
    const = []

    for row in active_costs:
        model = int(row[MODEL])
        if model != 2:
            raise NotImplementedError(f"Unsupported gencost model: {model} (expected polynomial model 2)")
        ncost = int(row[NCOST])
        coeffs = [float(x) for x in row[4:4+ncost]]

        if ncost == 3:
            c2, c1, c0 = coeffs
        elif ncost == 2:
            c2, c1, c0 = 0.0, coeffs[0], coeffs[1]
        elif ncost == 1:
            c2, c1, c0 = 0.0, coeffs[0], 0.0
        else:
            raise NotImplementedError(f"Unsupported polynomial degree with ncost={ncost}")

        quad.append(c2)
        lin.append(c1)
        const.append(c0)

    return np.array(quad), np.array(lin), np.array(const)

with open("network.json") as f:
    data = json.load(f)

c2, c1, c0 = extract_active_gencost(data)
print("Quadratic terms shape:", c2.shape)
print("First 5 linear terms:", c1[:5])
```

---

### 4) Build reserve data carefully and couple it to generator headroom
Why:
- The reserve product affects dispatch and can change LMPs and total cost.
- The required model is usually:
  - reserve variable `r_g >= 0`
  - system reserve requirement: `sum(r_g) >= R_req`
  - capacity coupling: `p_g + r_g <= pmax_g`
- Some tasks provide reserve capacity per generator. Use it if available.

Decision criteria:
- If `reserve_capacity` exists, treat it as an upper bound on each generator's reserve offer/capability.
- If a system-wide reserve requirement key is absent, inspect dataset keys before making assumptions.
- Keep reserve pricing as the dual on the system reserve requirement.

```python
#!/usr/bin/env python3
import json
import numpy as np

GEN_STATUS = 7

def parse_reserve_data(data: dict, active_gen_count: int):
    reserve_cap = np.full(active_gen_count, np.inf, dtype=float)

    if "reserve_capacity" in data and data["reserve_capacity"] is not None:
        all_active = []
        gen_rows = data["gen"]
        rc_rows = data["reserve_capacity"]

        if len(rc_rows) < len(gen_rows):
            raise ValueError("reserve_capacity has fewer rows than gen; cannot align safely.")

        for i, g in enumerate(gen_rows):
            status = int(g[GEN_STATUS]) if len(g) > GEN_STATUS else 1
            if status > 0:
                val = rc_rows[i]
                if isinstance(val, list):
                    val = val[0]
                all_active.append(float(val))
        reserve_cap = np.array(all_active, dtype=float)

    req_candidates = [
        "reserve_requirement",
        "reserves_requirement",
        "reserve_req",
        "system_reserve_requirement",
    ]

    reserve_req = None
    for key in req_candidates:
        if key in data:
            raw = data[key]
            if isinstance(raw, list):
                if len(raw) == 0:
                    continue
                reserve_req = float(raw[0])
            else:
                reserve_req = float(raw)
            break

    if reserve_req is None:
        raise KeyError("Could not find a system reserve requirement in the input JSON.")

    return reserve_cap, reserve_req

with open("network.json") as f:
    data = json.load(f)

active_gen_count = sum(1 for g in data["gen"] if int(g[GEN_STATUS]) > 0)
reserve_cap, reserve_req = parse_reserve_data(data, active_gen_count)
print("Reserve requirement:", reserve_req)
print("Reserve cap finite count:", np.isfinite(reserve_cap).sum())
```

---

### 5) Formulate a sparse, vectorized DC-OPF with reserve co-optimization
Why:
- Naive Python loops over buses/generators/branches can make `cvxpy` compilation slow.
- Use incidence matrices and vectorized expressions.
- This was important in successful execution: sparse, vectorized modeling reduces compile time dramatically.

Core model:
- Variables:
  - `pg[g]` generation MW
  - `rg[g]` reserve MW
  - `theta[b]` bus angle radians
- Objective:
  - minimize generator production cost
  - if reserve offer cost is unavailable, reserve can be feasibility-priced through the reserve constraint dual; do not invent arbitrary reserve costs unless the dataset defines them
- Constraints:
  - nodal balance: generation injection minus demand equals net line export
  - branch thermal limits
  - generator min/max
  - reserve nonnegativity and coupling
  - reserve requirement
  - reference angle

Critical note on LMPs:
- LMPs are the dual values of the **bus balance constraints**.
- Sign conventions matter. Define the balance equation consistently and test one case.
- If using `gen_inc @ pg - demand == A.T @ flow`, the dual on that equality is already the marginal energy price under a standard minimization setup.

```python
#!/usr/bin/env python3
import json
import numpy as np
import cvxpy as cp

BUS_I = 0
PD = 2

GEN_BUS = 0
PMAX = 8
PMIN = 9
GEN_STATUS = 7

F_BUS = 0
T_BUS = 1
BR_X = 3
RATE_A = 5
BR_STATUS = 10

MODEL = 0
NCOST = 3

def build_problem(data: dict, branch_rate_override=None):
    bus_ids = [int(row[BUS_I]) for row in data["bus"]]
    nbus = len(bus_ids)
    bus_id_to_idx = {b: i for i, b in enumerate(bus_ids)}
    demand = np.array([float(row[PD]) for row in data["bus"]], dtype=float)

    active_gen_rows = []
    active_gencost_rows = []
    for i, g in enumerate(data["gen"]):
        status = int(g[GEN_STATUS]) if len(g) > GEN_STATUS else 1
        if status > 0:
            active_gen_rows.append(g)
            active_gencost_rows.append(data["gencost"][i])

    ngen = len(active_gen_rows)
    gen_bus_idx = np.array([bus_id_to_idx[int(g[GEN_BUS])] for g in active_gen_rows], dtype=int)
    pmin = np.array([float(g[PMIN]) for g in active_gen_rows], dtype=float)
    pmax = np.array([float(g[PMAX]) for g in active_gen_rows], dtype=float)

    c2 = np.zeros(ngen)
    c1 = np.zeros(ngen)
    c0 = np.zeros(ngen)
    for i, row in enumerate(active_gencost_rows):
        if int(row[MODEL]) != 2:
            raise NotImplementedError("Only polynomial gencost model=2 supported")
        ncost = int(row[NCOST])
        coeffs = [float(x) for x in row[4:4+ncost]]
        if ncost == 3:
            c2[i], c1[i], c0[i] = coeffs
        elif ncost == 2:
            c2[i], c1[i], c0[i] = 0.0, coeffs[0], coeffs[1]
        elif ncost == 1:
            c2[i], c1[i], c0[i] = 0.0, coeffs[0], 0.0
        else:
            raise NotImplementedError(f"Unsupported ncost={ncost}")

    active_branch_rows = []
    for br in data["branch"]:
        status = int(br[BR_STATUS]) if len(br) > BR_STATUS else 1
        if status > 0:
            active_branch_rows.append(br)

    nbranch = len(active_branch_rows)
    f_idx = np.array([bus_id_to_idx[int(br[F_BUS])] for br in active_branch_rows], dtype=int)
    t_idx = np.array([bus_id_to_idx[int(br[T_BUS])] for br in active_branch_rows], dtype=int)
    x = np.array([float(br[BR_X]) for br in active_branch_rows], dtype=float)
    if np.any(np.isclose(x, 0.0)):
        raise ValueError("Zero branch reactance encountered")
    b = 1.0 / x
    rateA = np.array([float(br[RATE_A]) for br in active_branch_rows], dtype=float)

    if branch_rate_override:
        for k, br in enumerate(active_branch_rows):
            fb = int(br[F_BUS])
            tb = int(br[T_BUS])
            if (fb, tb) in branch_rate_override:
                rateA[k] = float(branch_rate_override[(fb, tb)])
            elif (tb, fb) in branch_rate_override:
                rateA[k] = float(branch_rate_override[(tb, fb)])

    reserve_cap = np.full(ngen, np.inf)
    if "reserve_capacity" in data and data["reserve_capacity"] is not None:
        active_vals = []
        for i, g in enumerate(data["gen"]):
            status = int(g[GEN_STATUS]) if len(g) > GEN_STATUS else 1
            if status > 0:
                raw = data["reserve_capacity"][i]
                if isinstance(raw, list):
                    raw = raw[0]
                active_vals.append(float(raw))
        reserve_cap = np.array(active_vals, dtype=float)

    reserve_req = None
    for key in ["reserve_requirement", "reserves_requirement", "reserve_req", "system_reserve_requirement"]:
        if key in data:
            raw = data[key]
            reserve_req = float(raw[0] if isinstance(raw, list) else raw)
            break
    if reserve_req is None:
        raise KeyError("No reserve requirement found")

    gen_inc = np.zeros((nbus, ngen), dtype=float)
    gen_inc[gen_bus_idx, np.arange(ngen)] = 1.0

    A = np.zeros((nbranch, nbus), dtype=float)
    A[np.arange(nbranch), f_idx] = 1.0
    A[np.arange(nbranch), t_idx] = -1.0

    pg = cp.Variable(ngen)
    rg = cp.Variable(ngen)
    theta = cp.Variable(nbus)

    flow = cp.multiply(b, A @ theta)

    objective = cp.Minimize(
        cp.sum(cp.multiply(c2, cp.square(pg)) + cp.multiply(c1, pg) + c0)
    )

    constraints = []
    constraints += [pg >= pmin, pg <= pmax]
    constraints += [rg >= 0, rg <= reserve_cap]
    constraints += [pg + rg <= pmax]
    reserve_con = cp.sum(rg) >= reserve_req
    constraints += [reserve_con]
    constraints += [flow <= rateA, flow >= -rateA]

    balance_cons = []
    net_inj = gen_inc @ pg - demand
    line_export = A.T @ flow
    for i in range(nbus):
        con = (net_inj[i] == line_export[i])
        constraints.append(con)
        balance_cons.append(con)

    constraints += [theta[0] == 0.0]

    problem = cp.Problem(objective, constraints)
    return {
        "problem": problem,
        "pg": pg,
        "rg": rg,
        "theta": theta,
        "flow_expr": flow,
        "balance_cons": balance_cons,
        "reserve_con": reserve_con,
        "bus_ids": bus_ids,
        "active_branch_rows": active_branch_rows,
        "rateA": rateA,
    }
```

---

### 6) Implement the counterfactual by modifying the correct branch limit only
Why:
- The task usually specifies one branch by bus IDs and a relative capacity change.
- Bus ordering may not match branch row order.
- Branches may appear in either direction, so check both `(from, to)` and `(to, from)`.

Recommended procedure:
1. Find all active branches matching the unordered bus pair.
2. Confirm exactly one match, unless the data intentionally has parallel circuits.
3. Increase `RATE_A` by the specified percentage for that branch only.

```python
#!/usr/bin/env python3
import json

F_BUS = 0
T_BUS = 1
RATE_A = 5
BR_STATUS = 10

def apply_branch_limit_multiplier(data: dict, bus_a: int, bus_b: int, multiplier: float) -> dict:
    if multiplier <= 0:
        raise ValueError("Multiplier must be positive")

    out = json.loads(json.dumps(data))  # safe deep copy for JSON-compatible data
    matches = []

    for i, br in enumerate(out["branch"]):
        status = int(br[BR_STATUS]) if len(br) > BR_STATUS else 1
        if status <= 0:
            continue
        fb = int(br[F_BUS])
        tb = int(br[T_BUS])
        if {fb, tb} == {bus_a, bus_b}:
            old = float(br[RATE_A])
            br[RATE_A] = old * multiplier
            matches.append((i, old, br[RATE_A]))

    if not matches:
        raise ValueError(f"No active branch found between buses {bus_a} and {bus_b}")

    print("Modified branches:", matches)
    return out

with open("network.json") as f:
    data = json.load(f)

cf = apply_branch_limit_multiplier(data, bus_a=64, bus_b=1501, multiplier=1.20)
```

---

### 7) Solve with a robust installed solver and capture duals
Why:
- The output requires both primal values and economic signals.
- `cvxpy` plus OSQP usually handles convex quadratic DC-OPF well.
- If the objective is linear only, ECOS/SCS can also work, but OSQP is a good default for QP.

Best practice:
- Try OSQP first.
- Check `problem.status` for `optimal` or `optimal_inaccurate`.
- Fail loudly on infeasible/unbounded rather than emitting a report.

```python
#!/usr/bin/env python3
import cvxpy as cp

def solve_problem(problem: cp.Problem) -> None:
    try:
        problem.solve(solver=cp.OSQP, verbose=False)
    except Exception:
        problem.solve(verbose=False)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Optimization failed with status={problem.status}")
```

---

### 8) Post-process results into validator-friendly outputs
Why:
- Validators typically check JSON schema and internal consistency.
- Use exact field names and nested structure.
- Sort buses deterministically, usually by bus number.
- Binding lines should mean line loading at or above 99% of thermal limit.

Definitions:
- `binding_lines`: include active branches where `abs(flow) >= 0.99 * limit`
- `reserve_mcp_dollars_per_MWh`: use dual of reserve requirement
- `lmp_by_bus`: one entry per bus, preserving actual bus IDs

Important sign note:
- Depending on how the reserve requirement is written (`sum(rg) >= R_req` vs `R_req <= sum(rg)`), the dual may be nonpositive in some solver conventions. Return the economically meaningful positive MCP:
  - if dual is negative because of formulation sign, report `-dual`
  - otherwise report `dual`
- Apply the same discipline to LMPs if your equality sign convention flips them. Verify by spot-checking that relaxing load at a congested bus would not produce nonsensical negative prices unless the system truly supports them.

```python
#!/usr/bin/env python3
import numpy as np

F_BUS = 0
T_BUS = 1

def summarize_solution(model: dict) -> dict:
    prob = model["problem"]
    bus_ids = model["bus_ids"]
    flow = np.array(model["flow_expr"].value, dtype=float).reshape(-1)
    rateA = np.array(model["rateA"], dtype=float).reshape(-1)

    lmp = []
    for bus_id, con in zip(bus_ids, model["balance_cons"]):
        dual = float(con.dual_value)
        lmp.append({
            "bus": int(bus_id),
            "lmp_dollars_per_MWh": dual
        })

    reserve_dual = float(model["reserve_con"].dual_value)
    reserve_mcp = abs(reserve_dual)

    binding = []
    for br, f, lim in zip(model["active_branch_rows"], flow, rateA):
        if lim > 0 and abs(f) >= 0.99 * lim:
            binding.append({
                "from": int(br[F_BUS]),
                "to": int(br[T_BUS]),
                "flow_MW": float(f),
                "limit_MW": float(lim),
            })

    return {
        "total_cost_dollars_per_hour": float(prob.value),
        "lmp_by_bus": lmp,
        "reserve_mcp_dollars_per_MWh": reserve_mcp,
        "binding_lines": binding,
    }
```

---

### 9) Compute cross-scenario impact metrics explicitly
Why:
- The report requires a comparison layer, not just two scenario summaries.
- You must correctly identify the largest LMP drops and whether the target constraint remains binding.

Rules:
- `cost_reduction_dollars_per_hour = base_cost - cf_cost`
- LMP drop means `cf_lmp - base_lmp` is most negative
- `congestion_relieved = True` if the adjusted line is **not** binding in the counterfactual

```python
#!/usr/bin/env python3
def make_impact_analysis(base: dict, cf: dict, target_a: int, target_b: int) -> dict:
    base_map = {x["bus"]: float(x["lmp_dollars_per_MWh"]) for x in base["lmp_by_bus"]}
    cf_map = {x["bus"]: float(x["lmp_dollars_per_MWh"]) for x in cf["lmp_by_bus"]}

    drops = []
    for bus in sorted(base_map):
        if bus not in cf_map:
            raise KeyError(f"Bus {bus} missing in counterfactual LMPs")
        delta = cf_map[bus] - base_map[bus]
        drops.append({
            "bus": int(bus),
            "base_lmp": base_map[bus],
            "cf_lmp": cf_map[bus],
            "delta": delta,
        })

    largest_drop = sorted(drops, key=lambda x: x["delta"])[:3]

    def line_present(binding_lines):
        for row in binding_lines:
            if {int(row["from"]), int(row["to"])} == {target_a, target_b}:
                return True
        return False

    return {
        "cost_reduction_dollars_per_hour": float(base["total_cost_dollars_per_hour"] - cf["total_cost_dollars_per_hour"]),
        "buses_with_largest_lmp_drop": largest_drop,
        "congestion_relieved": not line_present(cf["binding_lines"]),
    }
```

---

### 10) Validate before finalizing
Why:
- These tasks are often graded on both schema and economic consistency.
- A report can look correct but still fail if costs, duals, or branch logic are inconsistent.

Pre-finalization checklist:
1. `report.json` exists and has top-level keys:
   - `base_case`
   - `counterfactual`
   - `impact_analysis`
2. Base and counterfactual costs equal the solver objective values.
3. Same buses appear in both `lmp_by_bus` lists.
4. `cost_reduction_dollars_per_hour >= 0` if the counterfactual is a relaxation and the model solved optimally.
5. The target line is binding in the base if the task narrative expects congestion there.
6. `congestion_relieved` matches whether the target line disappears from counterfactual `binding_lines`.

```python
#!/usr/bin/env python3
import json

def validate_report(path="report.json"):
    with open(path) as f:
        report = json.load(f)

    for k in ["base_case", "counterfactual", "impact_analysis"]:
        if k not in report:
            raise KeyError(f"Missing top-level section: {k}")

    base = report["base_case"]
    cf = report["counterfactual"]
    impact = report["impact_analysis"]

    required_case_keys = [
        "total_cost_dollars_per_hour",
        "lmp_by_bus",
        "reserve_mcp_dollars_per_MWh",
        "binding_lines",
    ]
    for name, case in [("base_case", base), ("counterfactual", cf)]:
        for k in required_case_keys:
            if k not in case:
                raise KeyError(f"{name} missing key: {k}")

    base_buses = [x["bus"] for x in base["lmp_by_bus"]]
    cf_buses = [x["bus"] for x in cf["lmp_by_bus"]]
    if sorted(base_buses) != sorted(cf_buses):
        raise ValueError("Base and counterfactual bus sets differ")

    calc_delta = base["total_cost_dollars_per_hour"] - cf["total_cost_dollars_per_hour"]
    if abs(calc_delta - impact["cost_reduction_dollars_per_hour"]) > 1e-5:
        raise ValueError("Impact cost reduction does not match scenario costs")

    print("report.json validation passed")

if __name__ == "__main__":
    validate_report()
```

---

## Reference Implementation

The following is a complete end-to-end script. Copy, adapt filenames or target branch parameters, and run. It:
- loads `network.json`
- solves the base case
- increases one branch thermal capacity by a relative factor
- solves the counterfactual
- computes LMPs, reserve MCP, binding lines, and impact metrics
- writes `report.json`

```python
#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
import cvxpy as cp

# =========================
# MATPOWER column constants
# =========================
BUS_I = 0
PD = 2

GEN_BUS = 0
GEN_STATUS = 7
PMAX = 8
PMIN = 9

F_BUS = 0
T_BUS = 1
BR_X = 3
RATE_A = 5
BR_STATUS = 10

MODEL = 0
NCOST = 3

# =========================
# Input / task parameters
# =========================
INPUT_PATH = "network.json"
OUTPUT_PATH = "report.json"

# Adapt these for the specific task instance:
TARGET_FROM_BUS = 64
TARGET_TO_BUS = 1501
CAPACITY_MULTIPLIER = 1.20

# =========================
# Utility functions
# =========================
def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input not found: {path}")
    with p.open() as f:
        return json.load(f)

def save_json(obj: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)

def deep_copy_jsonable(obj):
    return json.loads(json.dumps(obj))

def parse_active_generator_data(data: dict):
    active_gen_rows = []
    active_cost_rows = []

    if len(data["gencost"]) < len(data["gen"]):
        raise ValueError("gencost row count is smaller than gen row count")

    for i, g in enumerate(data["gen"]):
        status = int(g[GEN_STATUS]) if len(g) > GEN_STATUS else 1
        if status > 0:
            active_gen_rows.append(g)
            active_cost_rows.append(data["gencost"][i])

    ngen = len(active_gen_rows)
    if ngen == 0:
        raise ValueError("No active generators found")

    pmin = np.array([float(g[PMIN]) for g in active_gen_rows], dtype=float)
    pmax = np.array([float(g[PMAX]) for g in active_gen_rows], dtype=float)

    c2 = np.zeros(ngen, dtype=float)
    c1 = np.zeros(ngen, dtype=float)
    c0 = np.zeros(ngen, dtype=float)

    for i, row in enumerate(active_cost_rows):
        model = int(row[MODEL])
        if model != 2:
            raise NotImplementedError(f"Unsupported gencost model={model}; expected 2")

        ncost = int(row[NCOST])
        coeffs = [float(x) for x in row[4:4+ncost]]

        if ncost == 3:
            c2[i], c1[i], c0[i] = coeffs
        elif ncost == 2:
            c2[i], c1[i], c0[i] = 0.0, coeffs[0], coeffs[1]
        elif ncost == 1:
            c2[i], c1[i], c0[i] = 0.0, coeffs[0], 0.0
        else:
            raise NotImplementedError(f"Unsupported polynomial ncost={ncost}")

    return active_gen_rows, pmin, pmax, c2, c1, c0

def parse_reserve_data(data: dict):
    active_flags = []
    for g in data["gen"]:
        status = int(g[GEN_STATUS]) if len(g) > GEN_STATUS else 1
        active_flags.append(status > 0)

    active_count = sum(active_flags)

    reserve_cap = np.full(active_count, np.inf, dtype=float)
    if "reserve_capacity" in data and data["reserve_capacity"] is not None:
        if len(data["reserve_capacity"]) < len(data["gen"]):
            raise ValueError("reserve_capacity row count is smaller than gen row count")
        vals = []
        for is_active, raw in zip(active_flags, data["reserve_capacity"]):
            if not is_active:
                continue
            if isinstance(raw, list):
                raw = raw[0]
            vals.append(float(raw))
        reserve_cap = np.array(vals, dtype=float)

    reserve_req = None
    for key in ["reserve_requirement", "reserves_requirement", "reserve_req", "system_reserve_requirement"]:
        if key in data:
            raw = data[key]
            reserve_req = float(raw[0] if isinstance(raw, list) else raw)
            break
    if reserve_req is None:
        raise KeyError("No system reserve requirement field found in input")

    return reserve_cap, reserve_req

def apply_branch_limit_multiplier(data: dict, bus_a: int, bus_b: int, multiplier: float) -> dict:
    if multiplier <= 0:
        raise ValueError("Capacity multiplier must be positive")

    out = deep_copy_jsonable(data)
    matches = []

    for i, br in enumerate(out["branch"]):
        status = int(br[BR_STATUS]) if len(br) > BR_STATUS else 1
        if status <= 0:
            continue
        fb = int(br[F_BUS])
        tb = int(br[T_BUS])
        if {fb, tb} == {bus_a, bus_b}:
            old = float(br[RATE_A])
            br[RATE_A] = old * multiplier
            matches.append((i, old, float(br[RATE_A])))

    if not matches:
        raise ValueError(f"No active branch found between buses {bus_a} and {bus_b}")

    return out

def build_market_problem(data: dict):
    bus_rows = data["bus"]
    branch_rows = data["branch"]

    if not bus_rows:
        raise ValueError("No buses in input")
    if not branch_rows:
        raise ValueError("No branches in input")

    bus_ids = [int(row[BUS_I]) for row in bus_rows]
    nbus = len(bus_ids)
    bus_id_to_idx = {bus_id: i for i, bus_id in enumerate(bus_ids)}

    demand = np.array([float(row[PD]) for row in bus_rows], dtype=float)

    active_gen_rows, pmin, pmax, c2, c1, c0 = parse_active_generator_data(data)
    ngen = len(active_gen_rows)

    gen_bus_idx = np.array([bus_id_to_idx[int(g[GEN_BUS])] for g in active_gen_rows], dtype=int)
    gen_inc = np.zeros((nbus, ngen), dtype=float)
    gen_inc[gen_bus_idx, np.arange(ngen)] = 1.0

    active_branch_rows = []
    for br in branch_rows:
        status = int(br[BR_STATUS]) if len(br) > BR_STATUS else 1
        if status > 0:
            active_branch_rows.append(br)

    nbranch = len(active_branch_rows)
    if nbranch == 0:
        raise ValueError("No active branches in input")

    f_idx = np.array([bus_id_to_idx[int(br[F_BUS])] for br in active_branch_rows], dtype=int)
    t_idx = np.array([bus_id_to_idx[int(br[T_BUS])] for br in active_branch_rows], dtype=int)
    x = np.array([float(br[BR_X]) for br in active_branch_rows], dtype=float)
    if np.any(np.isclose(x, 0.0)):
        bad = np.where(np.isclose(x, 0.0))[0].tolist()
        raise ValueError(f"Zero reactance found on branches at active indices {bad}")

    b = 1.0 / x
    rateA = np.array([float(br[RATE_A]) for br in active_branch_rows], dtype=float)

    reserve_cap, reserve_req = parse_reserve_data(data)

    if reserve_cap.shape[0] != ngen:
        raise ValueError("reserve capacity vector length does not match active generator count")

    A = np.zeros((nbranch, nbus), dtype=float)
    A[np.arange(nbranch), f_idx] = 1.0
    A[np.arange(nbranch), t_idx] = -1.0

    pg = cp.Variable(ngen, name="pg")
    rg = cp.Variable(ngen, name="rg")
    theta = cp.Variable(nbus, name="theta")

    flow = cp.multiply(b, A @ theta)

    objective = cp.Minimize(
        cp.sum(cp.multiply(c2, cp.square(pg)) + cp.multiply(c1, pg) + c0)
    )

    constraints = []
    constraints += [pg >= pmin]
    constraints += [pg <= pmax]
    constraints += [rg >= 0]
    constraints += [rg <= reserve_cap]
    constraints += [pg + rg <= pmax]

    reserve_con = (cp.sum(rg) >= reserve_req)
    constraints += [reserve_con]

    constraints += [flow <= rateA]
    constraints += [flow >= -rateA]

    balance_cons = []
    net_inj = gen_inc @ pg - demand
    line_export = A.T @ flow
    for i in range(nbus):
        con = (net_inj[i] == line_export[i])
        constraints.append(con)
        balance_cons.append(con)

    constraints += [theta[0] == 0.0]

    problem = cp.Problem(objective, constraints)

    return {
        "problem": problem,
        "pg": pg,
        "rg": rg,
        "theta": theta,
        "flow_expr": flow,
        "balance_cons": balance_cons,
        "reserve_con": reserve_con,
        "bus_ids": bus_ids,
        "active_branch_rows": active_branch_rows,
        "rateA": rateA,
    }

def solve_problem(problem: cp.Problem) -> None:
    try:
        problem.solve(solver=cp.OSQP, verbose=False)
    except Exception:
        problem.solve(verbose=False)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Solver failed with status={problem.status}")

def summarize_solution(model: dict) -> dict:
    prob = model["problem"]
    bus_ids = model["bus_ids"]
    flows = np.array(model["flow_expr"].value, dtype=float).reshape(-1)
    rateA = np.array(model["rateA"], dtype=float).reshape(-1)

    lmp_by_bus = []
    for bus_id, con in zip(bus_ids, model["balance_cons"]):
        dual = float(con.dual_value)
        lmp_by_bus.append({
            "bus": int(bus_id),
            "lmp_dollars_per_MWh": dual
        })

    reserve_dual = float(model["reserve_con"].dual_value)
    reserve_mcp = abs(reserve_dual)

    binding_lines = []
    for br, flow, limit in zip(model["active_branch_rows"], flows, rateA):
        if limit > 0 and abs(flow) >= 0.99 * limit:
            binding_lines.append({
                "from": int(br[F_BUS]),
                "to": int(br[T_BUS]),
                "flow_MW": float(flow),
                "limit_MW": float(limit),
            })

    return {
        "total_cost_dollars_per_hour": float(prob.value),
        "lmp_by_bus": lmp_by_bus,
        "reserve_mcp_dollars_per_MWh": reserve_mcp,
        "binding_lines": binding_lines,
    }

def line_is_binding(binding_lines, bus_a: int, bus_b: int) -> bool:
    for row in binding_lines:
        if {int(row["from"]), int(row["to"])} == {bus_a, bus_b}:
            return True
    return False

def make_impact_analysis(base_case: dict, counterfactual: dict, bus_a: int, bus_b: int) -> dict:
    base_map = {row["bus"]: float(row["lmp_dollars_per_MWh"]) for row in base_case["lmp_by_bus"]}
    cf_map = {row["bus"]: float(row["lmp_dollars_per_MWh"]) for row in counterfactual["lmp_by_bus"]}

    if sorted(base_map) != sorted(cf_map):
        raise ValueError("Base and counterfactual LMP bus sets do not match")

    lmp_deltas = []
    for bus in sorted(base_map):
        base_lmp = base_map[bus]
        cf_lmp = cf_map[bus]
        delta = cf_lmp - base_lmp
        lmp_deltas.append({
            "bus": int(bus),
            "base_lmp": base_lmp,
            "cf_lmp": cf_lmp,
            "delta": delta,
        })

    largest_drop = sorted(lmp_deltas, key=lambda x: x["delta"])[:3]
    congestion_relieved = not line_is_binding(counterfactual["binding_lines"], bus_a, bus_b)

    return {
        "cost_reduction_dollars_per_hour": float(
            base_case["total_cost_dollars_per_hour"] - counterfactual["total_cost_dollars_per_hour"]
        ),
        "buses_with_largest_lmp_drop": largest_drop,
        "congestion_relieved": congestion_relieved,
    }

def validate_report(report: dict, bus_a: int, bus_b: int) -> None:
    for k in ["base_case", "counterfactual", "impact_analysis"]:
        if k not in report:
            raise KeyError(f"Missing report section: {k}")

    base = report["base_case"]
    cf = report["counterfactual"]
    impact = report["impact_analysis"]

    for case_name, case in [("base_case", base), ("counterfactual", cf)]:
        for key in ["total_cost_dollars_per_hour", "lmp_by_bus", "reserve_mcp_dollars_per_MWh", "binding_lines"]:
            if key not in case:
                raise KeyError(f"{case_name} missing key: {key}")

    calc_delta = base["total_cost_dollars_per_hour"] - cf["total_cost_dollars_per_hour"]
    if abs(calc_delta - impact["cost_reduction_dollars_per_hour"]) > 1e-5:
        raise ValueError("Cost reduction field is inconsistent with scenario costs")

    base_buses = sorted(x["bus"] for x in base["lmp_by_bus"])
    cf_buses = sorted(x["bus"] for x in cf["lmp_by_bus"])
    if base_buses != cf_buses:
        raise ValueError("LMP bus lists differ between scenarios")

    if not isinstance(impact["congestion_relieved"], bool):
        raise TypeError("impact_analysis.congestion_relieved must be a boolean")

    # Optional sanity check: if the base case was congested on the target line but cf is not, flag is expected True.
    target_in_cf = line_is_binding(cf["binding_lines"], bus_a, bus_b)
    if impact["congestion_relieved"] == target_in_cf:
        raise ValueError("congestion_relieved does not match whether the target line is binding in the counterfactual")

def main():
    data = load_json(INPUT_PATH)

    # Base case
    base_model = build_market_problem(data)
    solve_problem(base_model["problem"])
    base_case = summarize_solution(base_model)

    # Counterfactual case: relax one line limit
    cf_data = apply_branch_limit_multiplier(
        data,
        bus_a=TARGET_FROM_BUS,
        bus_b=TARGET_TO_BUS,
        multiplier=CAPACITY_MULTIPLIER,
    )
    cf_model = build_market_problem(cf_data)
    solve_problem(cf_model["problem"])
    counterfactual = summarize_solution(cf_model)

    impact_analysis = make_impact_analysis(
        base_case=base_case,
        counterfactual=counterfactual,
        bus_a=TARGET_FROM_BUS,
        bus_b=TARGET_TO_BUS,
    )

    report = {
        "base_case": base_case,
        "counterfactual": counterfactual,
        "impact_analysis": impact_analysis,
    }

    validate_report(report, TARGET_FROM_BUS, TARGET_TO_BUS)
    save_json(report, OUTPUT_PATH)

    print(f"Wrote {OUTPUT_PATH}")
    print("Base cost:", report["base_case"]["total_cost_dollars_per_hour"])
    print("Counterfactual cost:", report["counterfactual"]["total_cost_dollars_per_hour"])
    print("Cost reduction:", report["impact_analysis"]["cost_reduction_dollars_per_hour"])
    print("Target line binding in base:", line_is_binding(report["base_case"]["binding_lines"], TARGET_FROM_BUS, TARGET_TO_BUS))
    print("Target line binding in cf:", line_is_binding(report["counterfactual"]["binding_lines"], TARGET_FROM_BUS, TARGET_TO_BUS))

if __name__ == "__main__":
    main()
```

---

## Expected Output Schema

Write exactly this nested structure:

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
      {"bus": 1, "base_lmp": 0.0, "cf_lmp": 0.0, "delta": 0.0}
    ],
    "congestion_relieved": true
  }
}
```

Notes:
- `lmp_by_bus` should include **all buses**
- `binding_lines` may be empty
- `buses_with_largest_lmp_drop` should usually contain the top 3 buses unless the system has fewer than 3 buses
- numeric values should be JSON numbers, not strings

---

## Recommended Execution Path

A reliable short path for these tasks:

1. Inspect `network.json` with `jq` and one short Python probe.
2. Confirm reserve fields and MATPOWER column assumptions.
3. Write a single Python solver script using:
   - `numpy`
   - `cvxpy`
4. Keep the optimization **vectorized** and avoid nested Python summation loops inside constraints.
5. Solve base, then modify the target branch limit and solve again.
6. Write `report.json`.
7. Immediately run one validation script to confirm:
   - schema
   - cost delta consistency
   - target branch binding behavior

---

## Common Pitfalls

### 1) Treating MATPOWER bus numbers as zero-based indices
Symptom:
- index errors, wrong balances, nonsensical LMPs

Fix:
- always create `bus_id_to_idx`

---

### 2) Modifying the wrong branch
Symptom:
- counterfactual changes nothing or affects unrelated congestion

Fix:
- match the branch using the unordered bus pair `{from, to}`
- handle reverse direction
- verify the target line is present in base binding set if the narrative suggests it

---

### 3) Ignoring generator status or branch status
Symptom:
- infeasibility, duplicate costs, incorrect topology

Fix:
- filter to active generators and active branches only

---

### 4) Misreading `gencost`
Symptom:
- objective values fail internal consistency checks

Fix:
- parse MATPOWER polynomial model exactly
- support 1st- and 2nd-order costs
- do not invent simplifications if higher-order terms appear

---

### 5) Building constraints with slow Python loops over large sets
Symptom:
- `cvxpy` compile time becomes very long

Fix:
- use incidence matrices and vectorized expressions:
  - `gen_inc @ pg`
  - `A @ theta`
  - `A.T @ flow`

This was an important success factor in practice.

---

### 6) Getting dual signs wrong for LMP or reserve MCP
Symptom:
- all prices have the wrong sign or reserve MCP is negative

Fix:
- check your equality orientation
- for reserve requirement duals, use the economically meaningful positive value
- validate against intuition: relaxing a congested line should not increase cost in a convex model

---

### 7) Reporting all near-full lines incorrectly
Symptom:
- missing congestion flags or too many false positives

Fix:
- apply the explicit rule:
  - binding if `abs(flow) >= 0.99 * limit`

---

### 8) Emitting a syntactically correct but economically inconsistent report
Symptom:
- schema passes, optimality/consistency tests fail

Fix:
- recompute:
  - `base_cost - cf_cost`
- ensure it matches `impact_analysis.cost_reduction_dollars_per_hour`
- ensure the target line's counterfactual binding status matches `congestion_relieved`

---

## Verification Commands

Install dependencies if needed:

```bash
python3 -m pip install numpy cvxpy --quiet
```

Run the solver:

```bash
python3 solve_market.py
```

Quick schema sanity check:

```bash
python3 - <<'PY'
import json
with open('report.json') as f:
    r = json.load(f)
print(list(r.keys()))
print(r['base_case'].keys())
print(r['counterfactual'].keys())
print(r['impact_analysis'].keys())
PY
```

Check target line relief:

```bash
python3 - <<'PY'
import json
TARGET_A = 64
TARGET_B = 1501
with open('report.json') as f:
    r = json.load(f)

def present(lines):
    return any({x['from'], x['to']} == {TARGET_A, TARGET_B} for x in lines)

print("base binding:", present(r['base_case']['binding_lines']))
print("cf binding:", present(r['counterfactual']['binding_lines']))
print("congestion_relieved:", r['impact_analysis']['congestion_relieved'])
PY
```

---

## Final Notes

- Prefer a single clean solver script over fragmented notebook-style experimentation.
- Do not finalize until you have:
  - an optimal status for both scenarios
  - a fully populated `report.json`
  - a consistency check on costs and target-line congestion status
- In convex DC-OPF with a relaxed line limit, the counterfactual cost should generally be less than or equal to the base cost. If not, investigate:
  - branch modification logic
  - sign conventions
  - reserve coupling
  - cost parsing