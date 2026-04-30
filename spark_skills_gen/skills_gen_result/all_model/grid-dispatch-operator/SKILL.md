---
id: grid-dispatch-operator
title: Grid Dispatch Operator — DC-OPF with Spinning Reserve
version: 1.0.0
tags: [power-systems, optimization, dc-opf, linear-programming, matpower]
---

## Overview

Solve a DC Optimal Power Flow (DC-OPF) problem given a MATPOWER-format network snapshot. Produce an economically optimal dispatch that satisfies power balance, generator/line limits, and spinning reserve requirements. Output a structured `report.json`.

---

## Module 1: Parse the Network Snapshot

MATPOWER JSON typically contains these top-level keys: `bus`, `gen`, `branch`, `gencost`, `baseMVA`.

```python
import json, numpy as np

with open("network.json") as f:
    net = json.load(f)

baseMVA = net["baseMVA"]
bus     = np.array(net["bus"])     # [bus_id, type, Pd, Qd, ...]
gen     = np.array(net["gen"])     # [bus, Pg, Qg, Qmax, Qmin, Vg, mBase, status, Pmax, Pmin, ...]
branch  = np.array(net["branch"])  # [fbus, tbus, r, x, b, rateA, ...]
gencost = np.array(net["gencost"]) # [type, startup, shutdown, n, c2, c1, c0, ...]
```

Key column indices (0-based, standard MATPOWER ordering):

| Array   | Column | Meaning          |
|---------|--------|------------------|
| bus     | 0      | bus ID           |
| bus     | 2      | Pd (MW load)     |
| gen     | 0      | connected bus    |
| gen     | 7      | status (1=on)    |
| gen     | 8      | Pmax (MW)        |
| gen     | 9      | Pmin (MW)        |
| branch  | 0/1    | from/to bus      |
| branch  | 3      | x (reactance)    |
| branch  | 5      | rateA (MW limit) |
| gencost | 4/5/6  | c2, c1, c0       |

Filter active generators: `gen[gen[:, 7] == 1]`. Filter active branches: `branch[branch[:, 10] == 1]` (status column 10).

---

## Module 2: Formulate and Solve the DC-OPF LP

### Build the B-matrix and incidence matrices

```python
from scipy.sparse import csr_matrix, lil_matrix

nb = len(bus)
ng = len(active_gen)
nl = len(active_branch)

# Map bus IDs to 0-based indices
bus_ids = bus[:, 0].astype(int)
bus_idx = {bid: i for i, bid in enumerate(bus_ids)}

# Branch susceptances
x = active_branch[:, 3]
b = 1.0 / x  # susceptance

# Branch-bus incidence (Bf) and bus-bus susceptance matrix (Bbus)
Bf = lil_matrix((nl, nb))
for k, br in enumerate(active_branch):
    f = bus_idx[int(br[0])]
    t = bus_idx[int(br[1])]
    Bf[k, f] =  b[k]
    Bf[k, t] = -b[k]
Bf = Bf.tocsr()
Bbus = Bf.T @ Bf  # nb x nb
```

### LP variable layout

Variables: `x = [p (ng), r (ng), theta (nb)]`

```python
from scipy.optimize import linprog

# Objective: minimize sum of linear generation cost (c1 * p + c0 ignored for dispatch)
c1 = gencost_active[:, 5]  # linear cost coefficients
c_obj = np.concatenate([c1, np.zeros(ng), np.zeros(nb)])
```

### Constraints

1. **DC power balance** (equality): `Cg @ p - Bbus @ theta = Pd`
2. **Capacity coupling** (inequality): `p + r <= Pmax`
3. **Reserve requirement** (inequality): `-sum(r) <= -reserve_req`
4. **Line flow limits** (inequality): `|Bf @ theta| <= rateA`

```python
# Reserve requirement: typically a fraction of total load or largest unit
reserve_req = 0.05 * total_load  # 5% of load, or use domain-specific rule

# Equality: power balance
# A_eq @ x = b_eq
# [Cg | 0 | -Bbus] @ [p; r; theta] = Pd_vec

# Bounds
bounds = (
    [(pmin[i], pmax[i]) for i in range(ng)] +   # p bounds
    [(0, pmax[i]) for i in range(ng)] +           # r bounds
    [(None, None)] * nb                           # theta (angle) free
)
# Fix slack bus angle
bounds[nb_slack_idx + 2*ng] = (0, 0)
```

### Solve with HiGHS (via scipy)

```python
result = linprog(
    c_obj,
    A_ub=A_ub, b_ub=b_ub,
    A_eq=A_eq, b_eq=b_eq,
    bounds=bounds,
    method="highs",
    options={"disp": False, "time_limit": 120}
)
assert result.status == 0, f"LP failed: {result.message}"

p_opt     = result.x[:ng]
r_opt     = result.x[ng:2*ng]
theta_opt = result.x[2*ng:]
```

---

## Module 3: Compute Outputs and Write report.json

### Line flows and loading

```python
flows = Bf @ theta_opt  # MW flows on each branch
rateA = active_branch[:, 5]
rateA = np.where(rateA == 0, np.inf, rateA)  # treat 0 as unconstrained
loading_pct = np.abs(flows) / rateA * 100.0

# Top 3 most loaded lines
top3_idx = np.argsort(loading_pct)[-3:][::-1]
most_loaded = [
    {
        "from": int(active_branch[i, 0]),
        "to":   int(active_branch[i, 1]),
        "loading_pct": round(loading_pct[i], 4)
    }
    for i in top3_idx
]
```

### Cost and totals

```python
# Quadratic cost: c2*p^2 + c1*p + c0
c2 = gencost_active[:, 4]
c0 = gencost_active[:, 6]
total_cost = float(np.sum(c2 * p_opt**2 + c1 * p_opt + c0))

total_load       = float(np.sum(bus[:, 2]))   # sum of Pd
total_generation = float(np.sum(p_opt))
total_reserve    = float(np.sum(r_opt))
operating_margin = float(np.sum(pmax - p_opt - r_opt))
```

### Write output

```python
report = {
    "generator_dispatch": [
        {
            "id":        int(i + 1),
            "bus":       int(active_gen[i, 0]),
            "output_MW": round(float(p_opt[i]), 4),
            "reserve_MW":round(float(r_opt[i]), 4),
            "pmax_MW":   round(float(pmax[i]),  4)
        }
        for i in range(ng)
    ],
    "totals": {
        "cost_dollars_per_hour": round(total_cost, 4),
        "load_MW":               round(total_load, 4),
        "generation_MW":         round(total_generation, 4),
        "reserve_MW":            round(total_reserve, 4)
    },
    "most_loaded_lines": most_loaded,
    "operating_margin_MW": round(operating_margin, 4)
}

with open("report.json", "w") as f:
    json.dump(report, f, indent=2)
```

---

## Common Pitfalls

- **Missing scipy/numpy**: Always check availability first with `python3 -c "import numpy, scipy"`. If absent, install with `pip install numpy scipy --break-system-packages` (Ubuntu 24.04 requires the flag).

- **Zero rateA treated as binding**: A `rateA = 0` in MATPOWER means unconstrained, not zero capacity. Replace with `inf` before computing loading percentages or adding line constraints.

- **Bus ID vs. array index mismatch**: Bus IDs in MATPOWER are not necessarily 0-based or contiguous. Always build an explicit `bus_idx` mapping dict before constructing incidence matrices.

- **Inactive generators included**: Filter `gen` by status column (index 7 == 1) before building the LP. Including offline units inflates capacity and distorts cost.

- **Slack bus not fixed**: The DC power flow angle system is rank-deficient without fixing one reference bus angle to zero. Identify the slack bus (type == 3) and pin its theta bound to `(0, 0)`.

- **Reserve requirement scale**: The spinning reserve requirement is typically expressed as a fraction of total load or the capacity of the largest single unit — not a fixed constant. Derive it from the network data rather than hardcoding.

- **Quadratic vs. linear cost in LP**: `linprog` only handles linear objectives. For quadratic `gencost` (type 2 with nonzero `c2`), either linearize via piecewise segments or use `scipy.optimize.minimize` with method `trust-constr`. For pure linear cost (c2 ≈ 0), `linprog` with HiGHS is fast and sufficient.

- **Long-running solvers**: For large networks (500+ generators, 4000+ branches), HiGHS typically solves in under 1 second. If a solve exceeds ~30 seconds, suspect a formulation error (e.g., infeasible equality constraints or unbounded variables).

- **Operating margin sign**: `operating_margin = sum(Pmax - p - r)` must be non-negative. A negative value indicates a constraint violation — debug the LP bounds before writing output.
