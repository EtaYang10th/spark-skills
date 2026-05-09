---
title: "AC Optimal Power Flow (ACOPF) with CasADi + IPOPT"
category: energy-ac-optimal-power-flow
tags:
  - power-systems
  - optimization
  - nonlinear-programming
  - CasADi
  - IPOPT
  - ACOPF
  - MATPOWER
domain: energy
objective: "Solve AC Optimal Power Flow for MATPOWER-format networks using CasADi/IPOPT in polar coordinates, producing a structured JSON report."
environment:
  runtime: python3
  packages:
    - casadi
    - numpy
  system: ubuntu
---

# AC Optimal Power Flow (ACOPF) with CasADi + IPOPT

## Overview

AC Optimal Power Flow (ACOPF) finds the least-cost generator dispatch that satisfies the full nonlinear AC power balance equations, voltage limits, generator limits, and branch thermal limits. This skill covers the complete pipeline: parsing MATPOWER-format JSON data, formulating the NLP in polar coordinates, solving with IPOPT via CasADi, computing branch flows using the π-model, and producing a validated JSON report.

---

## High-Level Workflow

1. **Parse the network data** — Load the MATPOWER-format JSON (or `.m` file converted to JSON). Extract bus, generator, branch, and base MVA data. Build index maps from external bus IDs to internal 0-based indices.

2. **Read the math model** — Check for a `math-model.md` or equivalent specification. Identify the exact formulation: polar vs rectangular, cost function form (polynomial), branch flow model (π-model with tap/shift), and which constraints are required.

3. **Install dependencies** — Ensure `casadi` and `numpy` are available. Use `pip install --break-system-packages casadi numpy` on system Python (Ubuntu 24.04+).

4. **Build the NLP in CasADi (polar coordinates)**:
   - Decision variables: `Vm[i]` (voltage magnitude), `Va[i]` (voltage angle), `Pg[g]` (active generation), `Qg[g]` (reactive generation).
   - Objective: minimize total generation cost (typically quadratic polynomial).
   - Equality constraints: nodal active and reactive power balance (P and Q injections = 0 at every bus).
   - Inequality constraints: generator P/Q limits, voltage magnitude limits, branch apparent power flow limits (both ends).

5. **Compute the bus admittance matrix (Ybus)** — Build from branch parameters (r, x, b, tap ratio, shift angle) using the standard π-model. This is the core of the AC power flow equations.

6. **Set warm-start initial values** — Use flat start (Vm=1.0, Va=0.0) with generators at midpoint of their P/Q ranges. Good initialization helps IPOPT converge.

7. **Solve with IPOPT** — Call `casadi.nlpsol` with IPOPT. Key IPOPT options: `max_iter=5000`, `tol=1e-6`, `linear_solver=mumps`.

8. **Extract results and compute branch flows** — Pull optimal Vm, Va, Pg, Qg from the solution. Recompute branch flows using the π-model equations to get `flow_from_MVA` and `flow_to_MVA`.

9. **Compute feasibility metrics** — Check power balance mismatches, voltage violations, and branch overloads against the solved values.

10. **Generate `report.json`** — Assemble the JSON with sections: `summary`, `generators`, `buses`, `most_loaded_branches`, `feasibility_check`. All power in MW/MVAr, angles in degrees, voltages in per-unit.

11. **Self-validate** — Before finishing, load the report and verify schema completeness, numerical consistency (losses = generation − load), and feasibility tolerances.

---

## Step 1: Parse MATPOWER-Format Network Data

MATPOWER JSON files typically have top-level keys: `bus`, `gen`, `branch`, `gencost`, `baseMVA`. Each is a list of lists (rows of the MATPOWER case matrices).

```python
import json
import numpy as np

def load_network(path):
    with open(path) as f:
        data = json.load(f)

    baseMVA = float(data['baseMVA'])

    # Bus data: [bus_i, type, Pd, Qd, Gs, Bs, area, Vm, Va, baseKV, zone, Vmax, Vmin]
    buses = []
    for row in data['bus']:
        buses.append({
            'bus_i': int(row[0]),
            'type':  int(row[1]),
            'Pd':    float(row[2]),
            'Qd':    float(row[3]),
            'Gs':    float(row[4]),
            'Bs':    float(row[5]),
            'Vm':    float(row[7]),
            'Va':    float(row[8]),
            'Vmax':  float(row[11]),
            'Vmin':  float(row[12]),
        })

    # Generator data: [bus, Pg, Qg, Qmax, Qmin, Vg, mBase, status, Pmax, Pmin, ...]
    gens = []
    for row in data['gen']:
        gens.append({
            'bus':   int(row[0]),
            'Pg':    float(row[1]),
            'Qg':    float(row[2]),
            'Qmax':  float(row[3]),
            'Qmin':  float(row[4]),
            'Vg':    float(row[5]),
            'status': int(row[7]),
            'Pmax':  float(row[8]),
            'Pmin':  float(row[9]),
        })

    # Branch data: [fbus, tbus, r, x, b, rateA, rateB, rateC, ratio, angle, status, angmin, angmax]
    branches = []
    for row in data['branch']:
        branches.append({
            'fbus':   int(row[0]),
            'tbus':   int(row[1]),
            'r':      float(row[2]),
            'x':      float(row[3]),
            'b':      float(row[4]),
            'rateA':  float(row[5]),
            'ratio':  float(row[8]),
            'angle':  float(row[9]),
            'status': int(row[10]),
        })

    # Generator cost: [model, startup, shutdown, ncost, c2, c1, c0] for polynomial model=2
    gencosts = []
    for row in data['gencost']:
        model = int(row[0])
        ncost = int(row[3])
        coeffs = [float(c) for c in row[4:4+ncost]]
        gencosts.append({'model': model, 'ncost': ncost, 'coeffs': coeffs})

    return baseMVA, buses, gens, branches, gencosts
```

**Critical notes on parsing:**
- Bus IDs are external (e.g., 1, 2, ..., 9999). Build a map: `bus_id_to_idx = {b['bus_i']: i for i, b in enumerate(buses)}`.
- Generator cost coefficients for `ncost=3` are `[c2, c1, c0]` where cost = `c2*Pg² + c1*Pg + c0`. The Pg here is in MW (not per-unit) — the cost function operates in natural units.
- Branch `ratio=0` means ratio=1.0 (no tap). Always normalize: `tap = ratio if ratio != 0 else 1.0`.
- Branch `rateA=0` means unlimited — skip the flow constraint for that branch or set a very large limit (e.g., 99999 MVA).
- Filter out offline generators (`status=0`) and offline branches (`status=0`).

---

## Step 2: Build the Bus Admittance Matrix (Ybus)

The Ybus is built from the π-model of each branch. For a branch between bus f and bus t with series impedance `z = r + jx`, total charging susceptance `b_ch`, tap ratio `τ`, and phase shift `θ_shift`:

```python
def build_ybus(buses, branches, bus_id_to_idx):
    n = len(buses)
    Ybus = np.zeros((n, n), dtype=complex)

    for br in branches:
        if br['status'] == 0:
            continue
        f = bus_id_to_idx[br['fbus']]
        t = bus_id_to_idx[br['tbus']]
        r, x = br['r'], br['x']
        b_ch = br['b']
        tap = br['ratio'] if br['ratio'] != 0.0 else 1.0
        shift = np.radians(br['angle'])

        ys = 1.0 / complex(r, x)           # series admittance
        tap_complex = tap * np.exp(1j * shift)

        # π-model entries
        Ybus[f, f] += ys / (tap * tap) + 1j * b_ch / 2.0 / (tap * tap)
        Ybus[t, t] += ys + 1j * b_ch / 2.0
        Ybus[f, t] -= ys / np.conj(tap_complex)
        Ybus[t, f] -= ys / tap_complex

    # Add bus shunt admittance (Gs + jBs) — these are in MW/MVAr at 1.0 pu voltage
    # They must be converted to per-unit: divide by baseMVA
    # BUT: Ybus is in per-unit, and Gs/Bs from MATPOWER are already in MW/MVAr
    # so shunt admittance in pu = (Gs + jBs) / baseMVA
    # This is added OUTSIDE this function or inside with baseMVA as parameter.

    return Ybus
```

**Critical: Bus shunts (Gs, Bs) must be included in Ybus diagonal.** They represent shunt conductance and susceptance at the bus. In MATPOWER format, Gs and Bs are in MW and MVAr at V=1.0 pu, so divide by baseMVA to get per-unit admittance:

```python
for i, bus in enumerate(buses):
    Ybus[i, i] += complex(bus['Gs'], bus['Bs']) / baseMVA
```

---

## Step 3: Formulate and Solve the NLP with CasADi

```python
import casadi as ca

def solve_acopf(baseMVA, buses, gens, branches, gencosts, bus_id_to_idx):
    nb = len(buses)
    ng = len(gens)

    # Build Ybus (numpy, for extracting G and B matrices)
    Ybus = build_ybus_with_shunts(buses, branches, bus_id_to_idx, baseMVA)
    G = Ybus.real
    B = Ybus.imag

    # Decision variables
    Vm = ca.SX.sym('Vm', nb)
    Va = ca.SX.sym('Va', nb)
    Pg = ca.SX.sym('Pg', ng)
    Qg = ca.SX.sym('Qg', ng)
    x = ca.vertcat(Vm, Va, Pg, Qg)

    # --- Objective: total generation cost ---
    obj = 0
    for g in range(ng):
        c = gencosts[g]['coeffs']
        pg_mw = Pg[g] * baseMVA  # convert to MW for cost
        if len(c) == 3:
            obj += c[0] * pg_mw**2 + c[1] * pg_mw + c[2]
        elif len(c) == 2:
            obj += c[0] * pg_mw + c[1]
        else:
            obj += c[0]

    # --- Power balance constraints ---
    # Map generators to buses
    Pbus = ca.SX.zeros(nb)
    Qbus = ca.SX.zeros(nb)
    for g in range(ng):
        bidx = bus_id_to_idx[gens[g]['bus']]
        Pbus[bidx] += Pg[g]
        Qbus[bidx] += Qg[g]

    # Subtract loads (in per-unit)
    for i, bus in enumerate(buses):
        Pbus[i] -= bus['Pd'] / baseMVA
        Qbus[i] -= bus['Qd'] / baseMVA

    # AC power flow equations
    g_eq = []
    for i in range(nb):
        Pi = 0
        Qi = 0
        for j in range(nb):
            if G[i, j] != 0 or B[i, j] != 0:
                angle_diff = Va[i] - Va[j]
                Pi += Vm[i] * Vm[j] * (G[i, j] * ca.cos(angle_diff) + B[i, j] * ca.sin(angle_diff))
                Qi += Vm[i] * Vm[j] * (G[i, j] * ca.sin(angle_diff) - B[i, j] * ca.cos(angle_diff))
        g_eq.append(Pbus[i] - Pi)
        g_eq.append(Qbus[i] - Qi)

    constraints = ca.vertcat(*g_eq)
    lbg = [0.0] * len(g_eq)
    ubg = [0.0] * len(g_eq)

    # --- Branch flow limits (apparent power, both ends) ---
    for br in branches:
        if br['status'] == 0:
            continue
        rate = br['rateA']
        if rate == 0 or rate > 90000:
            continue  # unconstrained

        f = bus_id_to_idx[br['fbus']]
        t = bus_id_to_idx[br['tbus']]
        r, xb = br['r'], br['x']
        b_ch = br['b']
        tap = br['ratio'] if br['ratio'] != 0 else 1.0
        shift = np.radians(br['angle'])

        ys = 1.0 / complex(r, xb)
        gs, bs = ys.real, ys.imag

        # From-side power
        Pf = (gs / tap**2) * Vm[f]**2 \
             - (1/tap) * Vm[f] * Vm[t] * (gs * ca.cos(Va[f] - Va[t] - shift) + bs * ca.sin(Va[f] - Va[t] - shift))
        Qf = -(bs + b_ch/2) / tap**2 * Vm[f]**2 \
             - (1/tap) * Vm[f] * Vm[t] * (gs * ca.sin(Va[f] - Va[t] - shift) - bs * ca.cos(Va[f] - Va[t] - shift))

        # To-side power
        Pt = gs * Vm[t]**2 \
             - (1/tap) * Vm[f] * Vm[t] * (gs * ca.cos(Va[t] - Va[f] + shift) + bs * ca.sin(Va[t] - Va[f] + shift))
        Qt = -(bs + b_ch/2) * Vm[t]**2 \
             - (1/tap) * Vm[f] * Vm[t] * (gs * ca.sin(Va[t] - Va[f] + shift) - bs * ca.cos(Va[t] - Va[f] + shift))

        Sf2 = Pf**2 + Qf**2
        St2 = Pt**2 + Qt**2
        smax2 = (rate / baseMVA)**2

        constraints = ca.vertcat(constraints, Sf2, St2)
        lbg += [0.0, 0.0]
        ubg += [smax2, smax2]

    # --- Variable bounds ---
    lbx, ubx, x0 = [], [], []

    # Vm bounds
    for bus in buses:
        lbx.append(bus['Vmin'])
        ubx.append(bus['Vmax'])
        x0.append(1.0)  # flat start

    # Va bounds — reference bus angle fixed to 0
    ref_idx = None
    for i, bus in enumerate(buses):
        if bus['type'] == 3:
            ref_idx = i
            break
    if ref_idx is None:
        ref_idx = 0  # fallback

    for i in range(nb):
        if i == ref_idx:
            lbx.append(0.0)
            ubx.append(0.0)
        else:
            lbx.append(-np.pi)
            ubx.append(np.pi)
        x0.append(0.0)

    # Pg bounds (per-unit)
    for g in range(ng):
        lbx.append(gens[g]['Pmin'] / baseMVA)
        ubx.append(gens[g]['Pmax'] / baseMVA)
        x0.append((gens[g]['Pmin'] + gens[g]['Pmax']) / 2.0 / baseMVA)

    # Qg bounds (per-unit)
    for g in range(ng):
        lbx.append(gens[g]['Qmin'] / baseMVA)
        ubx.append(gens[g]['Qmax'] / baseMVA)
        x0.append(0.0)

    # --- Solve ---
    nlp = {'x': x, 'f': obj, 'g': constraints}
    opts = {
        'ipopt.max_iter': 5000,
        'ipopt.tol': 1e-6,
        'ipopt.print_level': 5,
        'print_time': True,
        'ipopt.linear_solver': 'mumps',
        'ipopt.mu_strategy': 'adaptive',
        'ipopt.warm_start_init_point': 'yes',
    }
    solver = ca.nlpsol('acopf', 'ipopt', nlp, opts)
    sol = solver(x0=x0, lbx=lbx, ubx=ubx, lbg=lbg, ubg=ubg)

    stats = solver.stats()
    status = 'optimal' if stats['return_status'] == 'Solve_Succeeded' else stats['return_status']

    xopt = np.array(sol['x']).flatten()
    Vm_sol = xopt[:nb]
    Va_sol = xopt[nb:2*nb]
    Pg_sol = xopt[2*nb:2*nb+ng] * baseMVA  # back to MW
    Qg_sol = xopt[2*nb+ng:2*nb+2*ng] * baseMVA  # back to MVAr

    return Vm_sol, Va_sol, Pg_sol, Qg_sol, status
```

**Key decisions and why:**
- **Polar coordinates** — natural for power systems; Vm and Va directly appear in the power flow equations. Rectangular formulation works too but polar is more standard for ACOPF.
- **Quadratic branch flow constraints** — use `Pf² + Qf² ≤ Smax²` instead of `sqrt(Pf² + Qf²) ≤ Smax` to avoid non-differentiability at zero.
- **Per-unit internally, MW/MVAr externally** — all NLP variables and constraints are in per-unit. Convert back to MW/MVAr only for the report.
- **Reference bus** — fix Va=0 at the slack bus (type=3). This removes one degree of freedom and helps IPOPT converge.

---

## Step 4: Compute Branch Flows for Reporting

After solving, recompute branch flows in MW/MVA for the report:

```python
def compute_branch_flows(Vm_sol, Va_sol, branches, bus_id_to_idx, baseMVA):
    results = []
    for br in branches:
        if br['status'] == 0:
            continue
        f = bus_id_to_idx[br['fbus']]
        t = bus_id_to_idx[br['tbus']]
        r, x = br['r'], br['x']
        b_ch = br['b']
        tap = br['ratio'] if br['ratio'] != 0 else 1.0
        shift = np.radians(br['angle'])

        ys = 1.0 / complex(r, x)
        gs, bs = ys.real, ys.imag

        vmf, vmt = Vm_sol[f], Vm_sol[t]
        vaf, vat = Va_sol[f], Va_sol[t]

        # From-side
        Pf = (gs/tap**2)*vmf**2 - (1/tap)*vmf*vmt*(gs*np.cos(vaf-vat-shift) + bs*np.sin(vaf-vat-shift))
        Qf = -(bs+b_ch/2)/tap**2*vmf**2 - (1/tap)*vmf*vmt*(gs*np.sin(vaf-vat-shift) - bs*np.cos(vaf-vat-shift))

        # To-side
        Pt = gs*vmt**2 - (1/tap)*vmf*vmt*(gs*np.cos(vat-vaf+shift) + bs*np.sin(vat-vaf+shift))
        Qt = -(bs+b_ch/2)*vmt**2 - (1/tap)*vmf*vmt*(gs*np.sin(vat-vaf+shift) - bs*np.cos(vat-vaf+shift))

        Sf = np.sqrt(Pf**2 + Qf**2) * baseMVA
        St = np.sqrt(Pt**2 + Qt**2) * baseMVA
        rate = br['rateA'] if br['rateA'] > 0 else 99999.0
        loading = max(Sf, St) / rate * 100.0

        results.append({
            'from_bus': br['fbus'],
            'to_bus': br['tbus'],
            'Pf': Pf * baseMVA, 'Qf': Qf * baseMVA,
            'Pt': Pt * baseMVA, 'Qt': Qt * baseMVA,
            'Sf': Sf, 'St': St,
            'rate': rate,
            'loading_pct': loading,
        })
    return results
```

---

## Step 5: Generate the Report JSON

```python
def generate_report(Vm_sol, Va_sol, Pg_sol, Qg_sol, status,
                    buses, gens, branches, gencosts, bus_id_to_idx, baseMVA):
    # Totals
    total_gen_MW = float(np.sum(Pg_sol))
    total_gen_MVAr = float(np.sum(Qg_sol))
    total_load_MW = sum(b['Pd'] for b in buses)
    total_load_MVAr = sum(b['Qd'] for b in buses)
    total_losses_MW = total_gen_MW - total_load_MW

    # Total cost
    total_cost = 0.0
    for g in range(len(gens)):
        c = gencosts[g]['coeffs']
        pg = Pg_sol[g]
        if len(c) == 3:
            total_cost += c[0]*pg**2 + c[1]*pg + c[2]
        elif len(c) == 2:
            total_cost += c[0]*pg + c[1]
        else:
            total_cost += c[0]

    # Branch flows
    br_flows = compute_branch_flows(Vm_sol, Va_sol, branches, bus_id_to_idx, baseMVA)

    # Most loaded branches (top 5 or those above 80%)
    br_sorted = sorted(br_flows, key=lambda x: x['loading_pct'], reverse=True)
    most_loaded = []
    for br in br_sorted[:5]:
        most_loaded.append({
            'from_bus': br['from_bus'],
            'to_bus': br['to_bus'],
            'loading_pct': round(br['loading_pct'], 2),
            'flow_from_MVA': round(br['Sf'], 2),
            'flow_to_MVA': round(br['St'], 2),
            'limit_MVA': round(br['rate'], 2),
        })

    # Feasibility check — recompute power mismatches from solution
    Ybus = build_ybus_with_shunts(buses, branches, bus_id_to_idx, baseMVA)
    V = Vm_sol * np.exp(1j * Va_sol)
    S_calc = V * np.conj(Ybus @ V)  # complex power injection at each bus
    P_calc = S_calc.real * baseMVA
    Q_calc = S_calc.imag * baseMVA

    # Net injection at each bus
    P_inj = np.zeros(len(buses))
    Q_inj = np.zeros(len(buses))
    for g in range(len(gens)):
        bidx = bus_id_to_idx[gens[g]['bus']]
        P_inj[bidx] += Pg_sol[g]
        Q_inj[bidx] += Qg_sol[g]
    for i, bus in enumerate(buses):
        P_inj[i] -= bus['Pd']
        Q_inj[i] -= bus['Qd']

    p_mismatch = np.max(np.abs(P_inj - P_calc))
    q_mismatch = np.max(np.abs(Q_inj - Q_calc))

    # Voltage violations
    v_violations = []
    for i, bus in enumerate(buses):
        if Vm_sol[i] < bus['Vmin']:
            v_violations.append(bus['Vmin'] - Vm_sol[i])
        elif Vm_sol[i] > bus['Vmax']:
            v_violations.append(Vm_sol[i] - bus['Vmax'])
    max_v_viol = max(v_violations) if v_violations else 0.0

    # Branch overloads
    overloads = [max(br['Sf'] - br['rate'], br['St'] - br['rate'], 0) for br in br_flows]
    max_overload = max(overloads) if overloads else 0.0

    report = {
        'summary': {
            'total_cost_per_hour': round(total_cost, 2),
            'total_load_MW': round(total_load_MW, 2),
            'total_load_MVAr': round(total_load_MVAr, 2),
            'total_generation_MW': round(total_gen_MW, 2),
            'total_generation_MVAr': round(total_gen_MVAr, 2),
            'total_losses_MW': round(total_losses_MW, 2),
            'solver_status': status,
        },
        'generators': [],
        'buses': [],
        'most_loaded_branches': most_loaded,
        'feasibility_check': {
            'max_p_mismatch_MW': round(float(p_mismatch), 6),
            'max_q_mismatch_MVAr': round(float(q_mismatch), 6),
            'max_voltage_violation_pu': round(float(max_v_viol), 6),
            'max_branch_overload_MVA': round(float(max_overload), 6),
        },
    }

    for g in range(len(gens)):
        report['generators'].append({
            'id': g + 1,
            'bus': gens[g]['bus'],
            'pg_MW': round(float(Pg_sol[g]), 2),
            'qg_MVAr': round(float(Qg_sol[g]), 2),
            'pmin_MW': round(gens[g]['Pmin'], 2),
            'pmax_MW': round(gens[g]['Pmax'], 2),
            'qmin_MVAr': round(gens[g]['Qmin'], 2),
            'qmax_MVAr': round(gens[g]['Qmax'], 2),
        })

    for i, bus in enumerate(buses):
        report['buses'].append({
            'id': bus['bus_i'],
            'vm_pu': round(float(Vm_sol[i]), 6),
            'va_deg': round(float(np.degrees(Va_sol[i])), 4),
            'vmin_pu': bus['Vmin'],
            'vmax_pu': bus['Vmax'],
        })

    return report
```

---

## Step 6: Self-Validation

Always validate the report before submitting:

```python
def validate_report(report, num_gens, num_buses):
    # Schema checks
    required_summary = ['total_cost_per_hour', 'total_load_MW', 'total_load_MVAr',
                        'total_generation_MW', 'total_generation_MVAr',
                        'total_losses_MW', 'solver_status']
    for k in required_summary:
        assert k in report['summary'], f"Missing summary key: {k}"

    assert len(report['generators']) == num_gens, f"Expected {num_gens} generators"
    assert len(report['buses']) == num_buses, f"Expected {num_buses} buses"
    assert len(report['most_loaded_branches']) > 0, "Need at least 1 most-loaded branch"

    # Numerical consistency
    losses = report['summary']['total_generation_MW'] - report['summary']['total_load_MW']
    assert abs(losses - report['summary']['total_losses_MW']) < 0.1, "Losses mismatch"

    # Feasibility
    assert report['summary']['solver_status'] == 'optimal'
    assert report['feasibility_check']['max_p_mismatch_MW'] < 1.0
    assert report['feasibility_check']['max_q_mismatch_MVAr'] < 1.0
    assert report['feasibility_check']['max_voltage_violation_pu'] < 0.01
    assert report['feasibility_check']['max_branch_overload_MVA'] < 5.0

    print("All validation checks passed!")
```

---

## Common Pitfalls

1. **Forgetting to normalize tap ratio** — MATPOWER uses `ratio=0` to mean "no tap" (i.e., tap=1.0). If you pass 0 into the π-model formulas, you get division by zero or infinite admittance. Always: `tap = ratio if ratio != 0 else 1.0`.

2. **Omitting bus shunt admittance (Gs, Bs)** — These are easy to overlook but they affect the power balance. Gs/Bs are in MW/MVAr at V=1.0 pu, so divide by baseMVA before adding to Ybus diagonal.

3. **Cost function units** — Generator cost coefficients expect Pg in MW, not per-unit. If your decision variable Pg is in per-unit, multiply by baseMVA before computing cost: `cost = c2 * (Pg * baseMVA)^2 + c1 * (Pg * baseMVA) + c0`.

4. **Branch flow limit of 0 means unlimited** — Don't add a constraint `Sf² ≤ 0`. Skip branches with `rateA == 0`.

5. **Sparse Ybus construction for