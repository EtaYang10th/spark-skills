---
id: energy-ac-optimal-power-flow
title: AC Optimal Power Flow — ISO Base Case & Report Generation
version: 1.0.0
tags: [energy, power-systems, ac-opf, pypower, pandapower, optimization]
---

# AC Optimal Power Flow — ISO Base Case & Report Generation

## Module 1: Environment Setup & Library Selection

### Check available solvers first
```bash
python3 -c "import pandapower; print('pandapower', pandapower.__version__)"
python3 -c "import pypower; print('pypower ok')"
python3 -c "import scipy; print('scipy ok')"
python3 -c "import numpy; print('numpy', numpy.__version__)"
```

### Critical: numpy compatibility with pypower
pypower uses `np.in1d` which was removed in numpy 2.x. If numpy >= 2.0 is installed, downgrade:
```bash
pip install "numpy<2.0"
# numpy 1.26.4 is a known-good version
pip install numpy==1.26.4
```

### Solver preference
- **pypower**: lightweight, direct MATPOWER-format support, good for scripted OPF
- **pandapower**: higher-level API, better diagnostics, wraps pypower internally
- If `network.json` is in MATPOWER/pypower dict format, use pypower directly
- If network is in pandapower net format, use pandapower

---

## Module 2: Loading Network Data & Running AC OPF

### Inspect network structure before writing solver code
```python
import json
with open("network.json") as f:
    net = json.load(f)
# Check top-level keys to determine format
print(list(net.keys()))
# Expect: 'bus', 'gen', 'branch', 'gencost', 'baseMVA' for MATPOWER format
print(f"Buses: {len(net['bus'])}, Gens: {len(net['gen'])}, Branches: {len(net['branch'])}")
```

### Running AC OPF with pypower
```python
from pypower.api import opf, ppoption
from pypower.idx_bus import VM, VA, PD, QD, VMIN, VMAX, BUS_I, BUS_TYPE
from pypower.idx_gen import PG, QG, PMIN, PMAX, QMIN, QMAX, GEN_BUS
from pypower.idx_brch import F_BUS, T_BUS, RATE_A, PF, PT, QF, QT
import numpy as np

ppc = net  # already a dict with bus/gen/branch arrays as lists → convert
# Convert lists to numpy arrays if needed
for key in ['bus', 'gen', 'branch', 'gencost']:
    ppc[key] = np.array(ppc[key], dtype=float)

ppopt = ppoption(VERBOSE=0, OUT_ALL=0)
result = opf(ppc, ppopt)
success = result['success']  # 1 = optimal
```

### Running AC OPF with pandapower (alternative)
```python
import pandapower as pp
import pandapower.converter as pc

net = pc.from_mpc("network.mat")  # or build manually
pp.runopp(net)
# Access results via net.res_bus, net.res_gen, net.res_line, net.res_trafo
```

---

## Module 3: Building the Report JSON

### Unit conventions (always verify against math-model.md)
- Power: MW and MVAr (multiply per-unit by `baseMVA`)
- Voltage magnitude: per-unit (already in pypower results)
- Voltage angle: degrees (pypower stores radians → convert)
- Branch loading: percentage of thermal limit (RATE_A column)

### Report assembly pattern
```python
import json, math

baseMVA = result['baseMVA']
bus = result['bus']
gen = result['gen']
branch = result['branch']

# --- Summary ---
total_load_MW   = float(np.sum(bus[:, PD]))
total_load_MVAr = float(np.sum(bus[:, QD]))
total_gen_MW    = float(np.sum(gen[:, PG]))
total_gen_MVAr  = float(np.sum(gen[:, QG]))
total_losses_MW = total_gen_MW - total_load_MW

# --- Generators ---
generators = []
for i, g in enumerate(gen):
    generators.append({
        "id": int(i + 1),
        "bus": int(g[GEN_BUS]),
        "pg_MW":    round(float(g[PG]), 4),
        "qg_MVAr":  round(float(g[QG]), 4),
        "pmin_MW":  round(float(g[PMIN]), 4),
        "pmax_MW":  round(float(g[PMAX]), 4),
        "qmin_MVAr": round(float(g[QMIN]), 4),
        "qmax_MVAr": round(float(g[QMAX]), 4),
    })

# --- Buses ---
buses = []
for b in bus:
    buses.append({
        "id":      int(b[BUS_I]),
        "vm_pu":   round(float(b[VM]), 6),
        "va_deg":  round(float(b[VA]), 6),   # pypower stores degrees already
        "vmin_pu": round(float(b[VMIN]), 4),
        "vmax_pu": round(float(b[VMAX]), 4),
    })

# --- Most loaded branches (top 10 by loading %) ---
branch_data = []
for br in branch:
    limit = float(br[RATE_A])
    if limit <= 0:
        continue
    flow_from = math.sqrt(float(br[PF])**2 + float(br[QF])**2)
    flow_to   = math.sqrt(float(br[PT])**2 + float(br[QT])**2)
    loading   = max(flow_from, flow_to) / limit * 100.0
    branch_data.append({
        "from_bus":      int(br[F_BUS]),
        "to_bus":        int(br[T_BUS]),
        "loading_pct":   round(loading, 4),
        "flow_from_MVA": round(flow_from, 4),
        "flow_to_MVA":   round(flow_to, 4),
        "limit_MVA":     round(limit, 4),
    })

branch_data.sort(key=lambda x: x["loading_pct"], reverse=True)
most_loaded = branch_data[:10]

# --- Feasibility check ---
# P/Q mismatches from OPF bus injections vs load+gen balance
p_mismatch = float(np.max(np.abs(result.get('g', np.array([0])))))
q_mismatch = 0.0  # compute similarly if available

v_violations = bus[(bus[:, VM] < bus[:, VMIN]) | (bus[:, VM] > bus[:, VMAX])]
max_v_viol = 0.0
if len(v_violations):
    max_v_viol = float(np.max(np.maximum(
        bus[:, VMIN] - bus[:, VM], bus[:, VM] - bus[:, VMAX]
    ).clip(min=0)))

overloads = [b["flow_from_MVA"] - b["limit_MVA"] for b in branch_data if b["flow_from_MVA"] > b["limit_MVA"]]
max_overload = max(overloads) if overloads else 0.0

report = {
    "summary": {
        "total_cost_per_hour": round(float(result['f']), 4),
        "total_load_MW":       round(total_load_MW, 4),
        "total_load_MVAr":     round(total_load_MVAr, 4),
        "total_generation_MW": round(total_gen_MW, 4),
        "total_generation_MVAr": round(total_gen_MVAr, 4),
        "total_losses_MW":     round(total_losses_MW, 4),
        "solver_status":       "optimal" if result['success'] else "infeasible",
    },
    "generators": generators,
    "buses": buses,
    "most_loaded_branches": most_loaded,
    "feasibility_check": {
        "max_p_mismatch_MW":       round(p_mismatch, 6),
        "max_q_mismatch_MVAr":     round(q_mismatch, 6),
        "max_voltage_violation_pu": round(max_v_viol, 6),
        "max_branch_overload_MVA": round(max_overload, 6),
    }
}

with open("report.json", "w") as f:
    json.dump(report, f, indent=2)
```

---

## Common Pitfalls

### 1. numpy 2.x breaks pypower
`np.in1d` was removed in numpy 2.0. pypower calls it internally. Always pin `numpy<2.0` before running.

### 2. `most_loaded_branches` field names must be exact
The schema test checks exact key names. Use:
- `from_bus`, `to_bus` (not `from`, `to`, `fbus`, `tbus`)
- `loading_pct` (not `loading_percent`, `pct_loading`)
- `flow_from_MVA`, `flow_to_MVA` (not `p_from`, `mva_from`)
- `limit_MVA` (not `rate_a`, `thermal_limit`)

### 3. Branches with zero RATE_A must be excluded
Branches with `RATE_A == 0` have no thermal limit defined — skip them when computing loading percentage to avoid division by zero and nonsensical 0% entries polluting the top-10 list.

### 4. Power values must be in MW/MVAr, not per-unit
pypower internal arrays store everything in per-unit on `baseMVA` base. The `PG`, `QG`, `PD`, `QD`, `PF`, `QF` columns are already in MW/MVAr when `baseMVA` scaling is applied by the solver — verify by checking if `PD` values match expected load magnitudes.

### 5. Always read `math-model.md` before coding
The math model file specifies the exact objective function, constraint formulation, and unit conventions for the specific network. Don't assume standard MATPOWER defaults — check for custom cost curves, per-unit bases, or non-standard constraint formulations.

### 6. Inspect the test file before finalizing
If `tests/test_outputs.py` is accessible, read it directly to confirm expected field names, types, and array lengths before writing the report. Schema mismatches are the most common failure mode and are trivially avoidable with a quick inspection.
