---
title: "Reflow Oven Profile Analysis & Manufacturing Equipment Maintenance"
category: "manufacturing-equipment-maintenance"
domain: "electronics-manufacturing"
tags:
  - reflow-soldering
  - thermocouple-analysis
  - MES-log-processing
  - quality-compliance
  - thermal-profiling
applicability:
  task_pattern: "Analyze thermocouple sensor data, MES logs, and test defect records against a reflow oven handbook to answer compliance questions about preheat ramp rate, time above liquidus (TAL), peak temperature, conveyor speed, and best-run selection."
  data_files:
    - "handbook.pdf — reflow machine operating manual with profile specs"
    - "thermocouples.csv — time-series temperature readings per run/TC"
    - "mes_log.csv — run metadata (solder paste, conveyor speed, board info)"
    - "test_defects.csv — defect counts and yield per run"
  output_format: "JSON files with specific schemas per question"
  environment: "Python 3.11, pandas, pytest"
---

# Reflow Oven Profile Analysis & Manufacturing Equipment Maintenance

## 1. Domain Context

Reflow soldering passes PCBs through a multi-zone oven. The thermal profile has four phases:

1. **Preheat** — gradual ramp from ambient to a soak temperature (typically 100–150 °C)
2. **Soak / Thermal Soak** — hold at a plateau to equalize temperature across the board
3. **Reflow** — rapid ramp above the solder liquidus temperature; solder melts and wets
4. **Cooling** — controlled cool-down to solidify joints

Key metrics extracted from thermocouple (TC) data:
- **Preheat ramp rate** (°C/s) — must stay below a limit to avoid thermal shock
- **Time Above Liquidus (TAL)** — seconds the solder is molten; must be within a window
- **Peak temperature** — must exceed liquidus by a margin but not damage components
- **Conveyor speed** — must be fast enough to meet throughput requirements

Each run has multiple TCs placed at different board locations (e.g., `largest_mass`, `smallest_component`, `board_edge`). The handbook specifies which TC represents the worst case for each metric.

## 2. High-Level Workflow

### Step 1: Extract Handbook Specifications

Read the handbook PDF to find numeric specifications. Key values to extract:

| Parameter | Typical Handbook Language | Example Value |
|---|---|---|
| Preheat temperature band | "preheat region 100 °C to 150 °C" | 100–150 °C |
| Preheat ramp rate limit | "shall not exceed 2 °C/s" | < 2.0 °C/s |
| TAL window | "wetting time 30–60 seconds" | 30–60 s |
| Peak temp margin | "exceeded by approximately 20 °C" | liquidus + 20 °C |
| Conveyor speed formula | "LINE SPEED = boards_per_min × board_length_cm / loading_factor" | computed per run |

**Critical**: The handbook may use indirect language. "Wetting time" = TAL. "Thermal shock limit" = ramp rate limit. Read carefully and map terminology.

### Step 2: Load and Understand All Data Files

```python
import pandas as pd
import json, math

tc = pd.read_csv("/app/data/thermocouples.csv")
mes = pd.read_csv("/app/data/mes_log.csv")
defects = pd.read_csv("/app/data/test_defects.csv")

# Inspect columns and shapes
print(tc.columns.tolist(), tc.shape)
print(mes.columns.tolist(), mes.shape)
print(defects.columns.tolist(), defects.shape)

# Typical columns:
# tc: run_id, tc_id, tc_location, time_s, temperature_c
# mes: run_id, board_family, board_id, solder_paste, solder_liquidus_c,
#      conveyor_speed_cm_min, boards_per_min, board_length_cm, loading_factor
# defects: run_id, board_id, defect_type, defect_count, fp_yield_pct
```

### Step 3: Identify All Runs (Including Those Without TC Data)

```python
all_runs_mes = sorted(mes["run_id"].unique())
all_runs_tc = sorted(tc["run_id"].unique()) if not tc.empty else []
runs_without_tc = sorted(set(all_runs_mes) - set(all_runs_tc))
```

Runs without TC data must still appear in outputs — they get `null` values for TC-derived metrics and are automatically **failing/non-compliant**.

### Step 4: Compute Per-Run TC Metrics

For each run, group TC data by `tc_id` and compute:
- Max preheat ramp rate
- TAL (time above liquidus)
- Peak temperature

### Step 5: Select the Correct TC Per Run Per Metric

**This is the most critical decision and the #1 source of failures.**

| Metric | TC Selection Strategy | Rationale |
|---|---|---|
| Preheat ramp rate (Q1) | TC with **maximum** ramp rate | Worst case for thermal shock |
| TAL (Q2) | TC with **minimum** TAL | Worst case for solder wetting — coldest spot spends least time above liquidus |
| Peak temperature (Q3) | TC with **minimum** peak | Worst case — coldest spot may not reach required temperature |

**Common Pitfall**: Using the same TC selection for all metrics. Each metric has its own worst-case TC. The "min peak" TC is NOT necessarily the "min TAL" TC.

### Step 6: Compute Conveyor Speed Feasibility

```python
# From MES log, per run:
required_min_speed = boards_per_min * board_length_cm / loading_factor
meets = actual_speed >= required_min_speed
```

### Step 7: Select Best Run Per Board Family

Rank runs within each board family by:
1. Highest `fp_yield_pct` (first-pass yield — higher is better)
2. Lowest total defect count (tiebreaker)
3. Highest `boards_per_min` (throughput tiebreaker)

### Step 8: Write JSON Outputs and Validate

Write each output file, then run the test suite to verify.

## 3. Critical Computations

### 3.1 Preheat Ramp Rate

The ramp rate is the maximum slope of temperature vs. time **within the preheat band**.

```python
def max_preheat_ramp(times, temps, preheat_min, preheat_max):
    """
    Compute maximum temperature ramp rate (°C/s) within the preheat band.
    Only considers segments where BOTH endpoints are within [preheat_min, preheat_max].
    """
    max_ramp = 0.0
    for i in range(1, len(times)):
        t0, t1 = temps[i - 1], temps[i]
        dt = times[i] - times[i - 1]
        if dt <= 0:
            continue
        # Both endpoints must be within the preheat band
        if preheat_min <= t0 <= preheat_max and preheat_min <= t1 <= preheat_max:
            ramp = abs(t1 - t0) / dt
            if ramp > max_ramp:
                max_ramp = ramp
    return round(max_ramp, 2) if max_ramp > 0 else None
```

**Key detail**: Use `abs()` for the ramp — the handbook cares about the magnitude of temperature change rate, not direction. However, during preheat the temperature is rising, so `t1 - t0` is typically positive. Using `abs()` is a safe guard.

### 3.2 Time Above Liquidus (TAL) with Linear Interpolation

TAL requires precise crossing-point detection. Do NOT simply count samples above the threshold — this introduces quantization error proportional to the sampling interval.

```python
def compute_tal(times, temps, liquidus):
    """
    Compute time above liquidus using linear interpolation at crossings.
    Returns TAL in seconds.
    """
    crossings = []  # list of (time, direction) where direction is 'up' or 'down'

    for i in range(1, len(times)):
        t0, t1 = temps[i - 1], temps[i]
        # Detect upward crossing
        if t0 <= liquidus < t1:
            # Linear interpolation: find exact time of crossing
            frac = (liquidus - t0) / (t1 - t0)
            cross_time = times[i - 1] + frac * (times[i] - times[i - 1])
            crossings.append((cross_time, "up"))
        # Detect downward crossing
        elif t0 > liquidus >= t1:
            frac = (t0 - liquidus) / (t0 - t1)
            cross_time = times[i - 1] + frac * (times[i] - times[i - 1])
            crossings.append((cross_time, "down"))

    # Sum all above-liquidus intervals
    tal = 0.0
    i = 0
    while i < len(crossings) - 1:
        if crossings[i][1] == "up" and crossings[i + 1][1] == "down":
            tal += crossings[i + 1][0] - crossings[i][0]
            i += 2
        else:
            i += 1

    return round(tal, 2)
```

**Critical**: The linear interpolation at crossing points is essential. Simple sample-counting (`sum(temps > liquidus) * dt`) will be off by up to one full sampling interval per crossing, which can be 1–3 seconds — enough to flip compliance status.

### 3.3 Peak Temperature

```python
def peak_temp(temps):
    return round(max(temps), 2)
```

Simple, but the TC selection (minimum peak across all TCs in a run) is what matters.

### 3.4 Conveyor Speed

```python
def required_min_speed(row):
    """row is a MES log row with boards_per_min, board_length_cm, loading_factor"""
    return round(row["boards_per_min"] * row["board_length_cm"] / row["loading_factor"], 2)
```

### 3.5 Best Run Selection

```python
def rank_runs(defects_df, mes_df):
    """
    Rank runs within each board family.
    Returns dict: board_family -> [run_id_best, run_id_2nd, ...]
    """
    # Aggregate defects per run
    run_defects = defects_df.groupby("run_id").agg(
        total_defects=("defect_count", "sum"),
        fp_yield_pct=("fp_yield_pct", "first")  # same for all rows of a run
    ).reset_index()

    # Merge with MES for board_family and boards_per_min
    merged = run_defects.merge(
        mes_df[["run_id", "board_family", "boards_per_min"]].drop_duplicates(),
        on="run_id"
    )

    # Sort: highest yield, then lowest defects, then highest throughput
    merged = merged.sort_values(
        by=["board_family", "fp_yield_pct", "total_defects", "boards_per_min"],
        ascending=[True, False, True, False]
    )

    result = []
    for family, group in merged.groupby("board_family", sort=True):
        runs = group["run_id"].tolist()
        result.append({
            "board_family": family,
            "best_run_id": runs[0],
            "runner_up_run_ids": sorted(runs[1:])
        })
    return result
```

## 4. TC Selection Strategy — The Make-or-Break Decision

This deserves its own section because it is the single most common failure point.

### Why Different Metrics Need Different TCs

A reflow oven has temperature gradients across the board. A large thermal mass (e.g., a big connector) heats slowly and cools slowly. A small component heats quickly.

| Metric | Worst Case | TC to Select |
|---|---|---|
| Ramp rate | Fastest-heating spot → thermal shock risk | **Max ramp** across TCs |
| TAL | Coldest spot → least time above liquidus → poor wetting | **Min TAL** across TCs |
| Peak temp | Coldest spot → may not reach required peak | **Min peak** across TCs |

### Implementation Pattern

```python
def select_tc_for_metric(run_group, metric_fn, select="min"):
    """
    run_group: DataFrame for one run, with tc_id, time_s, temperature_c
    metric_fn: function(times, temps) -> float
    select: "min" or "max"
    Returns: (tc_id, metric_value)
    """
    results = []
    for tc_id, tc_data in run_group.groupby("tc_id"):
        tc_data = tc_data.sort_values("time_s")
        value = metric_fn(tc_data["time_s"].values, tc_data["temperature_c"].values)
        if value is not None:
            results.append((tc_id, value))

    if not results:
        return (None, None)

    if select == "min":
        return min(results, key=lambda x: x[1])
    else:
        return max(results, key=lambda x: x[1])
```

## 5. Common Pitfalls

### Pitfall 1: Using the Same TC for All Metrics
**Symptom**: Q1 passes but Q2 fails, or vice versa.
**Fix**: Each metric has its own worst-case TC selection. See Section 4.

### Pitfall 2: Using "Min Peak TC" for TAL
**Symptom**: Q2 TAL values are close but wrong for some runs.
**Explanation**: The TC with the minimum peak temperature is NOT always the TC with the minimum TAL. A TC on a large mass may have a lower peak but spend more time above liquidus due to slow cooling. Use **min TAL** TC for Q2.

### Pitfall 3: Counting Samples Instead of Interpolating for TAL
**Symptom**: TAL values are off by 1–3 seconds.
**Fix**: Use linear interpolation at liquidus crossings (Section 3.2).

### Pitfall 4: Forgetting Runs Without TC Data
**Symptom**: Missing runs in output, or test fails on schema validation.
**Fix**: Enumerate all runs from MES log. Runs without TC data get `null` for TC-derived values and are automatically failing/non-compliant.

### Pitfall 5: Wrong Preheat Band Filter
**Symptom**: Ramp rates are too high or too low.
**Fix**: Only compute ramp for segments where **both** endpoints are within the preheat band [100, 150] °C. Segments that cross the band boundary include non-preheat behavior.

### Pitfall 6: Handbook Terminology Mismatch
**Symptom**: Can't find TAL specification in handbook.
**Fix**: The handbook may call TAL "wetting time" or "time above melting point." The preheat ramp limit may be called "thermal shock limit." Read the full handbook and map terminology.

### Pitfall 7: Sorting and Rounding
**Symptom**: Tests fail on exact value comparison.
**Fix**: Round all floats to 2 decimal places. Sort all arrays by `run_id` or `board_family` ascending. Use `null` (JSON null) for missing values, not `0` or empty string.

### Pitfall 8: Defect Aggregation for Q5
**Symptom**: Wrong best run selected.
**Fix**: Aggregate `defect_count` across all defect types per run. Use `fp_yield_pct` as the primary sort key (higher is better), total defects as secondary (lower is better), and `boards_per_min` as tertiary (higher is better).

## 6. Verification Checklist

Before submitting outputs, verify:

- [ ] All runs from MES log appear in every output file
- [ ] Runs without TC data have `null` TC-derived values and are marked failing/non-compliant
- [ ] Violating runs in Q1 all have ramp ≥ limit; non-violating have ramp < limit
- [ ] Q2 TAL uses min-TAL TC, not min-peak TC
- [ ] Q3 failing runs all have peak < required minimum; passing runs have peak ≥ required minimum
- [ ] Q4 required_min_speed matches formula: `boards_per_min × board_length_cm / loading_factor`
- [ ] Q5 runner_up_run_ids are sorted ascending
- [ ] All floats rounded to 2 decimal places
- [ ] All arrays sorted by run_id or board_family ascending

```python
# Quick self-test
import json

for qfile in ["q01.json", "q02.json", "q03.json", "q04.json", "q05.json"]:
    with open(f"/app/output/{qfile}") as f:
        data = json.load(f)
    print(f"{qfile}: loaded OK, type={type(data).__name__}")
```

## 7. Reference Implementation

This is the complete, end-to-end solution. Copy, adapt the handbook constants if they differ, and run.

```python
#!/usr/bin/env python3
"""
Reflow Oven Profile Analysis — Complete Reference Implementation
Generates q01.json through q05.json from thermocouple, MES, and defect data.
"""

import json
import math
import os
import pandas as pd
import numpy as np

# ─── Configuration (extract these from the handbook) ───────────────────────
PREHEAT_MIN_C = 100.0
PREHEAT_MAX_C = 150.0
RAMP_RATE_LIMIT_C_PER_S = 2.0  # handbook: "shall not exceed 2 °C/s"

TAL_MIN_S = 30.0   # handbook: "wetting time" minimum
TAL_MAX_S = 60.0   # handbook: "wetting time" maximum

PEAK_MARGIN_C = 20.0  # handbook: "exceeded by approximately 20 °C"

# ─── Load Data ─────────────────────────────────────────────────────────────
DATA_DIR = "/app/data"
OUTPUT_DIR = "/app/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

tc = pd.read_csv(os.path.join(DATA_DIR, "thermocouples.csv"))
mes = pd.read_csv(os.path.join(DATA_DIR, "mes_log.csv"))
defects = pd.read_csv(os.path.join(DATA_DIR, "test_defects.csv"))

# Ensure consistent types
tc = tc.sort_values(["run_id", "tc_id", "time_s"]).reset_index(drop=True)
all_run_ids = sorted(mes["run_id"].unique())
tc_run_ids = set(tc["run_id"].unique()) if not tc.empty else set()

# Build liquidus lookup from MES log
# Each run has a solder_liquidus_c value
liquidus_map = mes.drop_duplicates("run_id").set_index("run_id")["solder_liquidus_c"].to_dict()


# ─── Helper Functions ──────────────────────────────────────────────────────

def _max_preheat_ramp(times, temps, preheat_min, preheat_max):
    """Max ramp rate (°C/s) within the preheat band."""
    max_ramp = 0.0
    for i in range(1, len(times)):
        t0, t1 = temps[i - 1], temps[i]
        dt = times[i] - times[i - 1]
        if dt <= 0:
            continue
        if preheat_min <= t0 <= preheat_max and preheat_min <= t1 <= preheat_max:
            ramp = abs(t1 - t0) / dt
            if ramp > max_ramp:
                max_ramp = ramp
    return round(max_ramp, 2) if max_ramp > 0 else None


def _tal_seconds(times, temps, liquidus):
    """Time above liquidus using linear interpolation at crossings."""
    crossings = []
    for i in range(1, len(times)):
        t0, t1 = temps[i - 1], temps[i]
        if t0 <= liquidus < t1:
            frac = (liquidus - t0) / (t1 - t0) if (t1 - t0) != 0 else 0
            cross_time = times[i - 1] + frac * (times[i] - times[i - 1])
            crossings.append((cross_time, "up"))
        elif t0 > liquidus >= t1:
            frac = (t0 - liquidus) / (t0 - t1) if (t0 - t1) != 0 else 0
            cross_time = times[i - 1] + frac * (times[i] - times[i - 1])
            crossings.append((cross_time, "down"))

    tal = 0.0
    i = 0
    while i < len(crossings) - 1:
        if crossings[i][1] == "up" and crossings[i + 1][1] == "down":
            tal += crossings[i + 1][0] - crossings[i][0]
            i += 2
        else:
            i += 1
    return round(tal, 2)


def _peak_temp(temps):
    return round(float(max(temps)), 2)


# ─── Q1: Preheat Ramp Rate ────────────────────────────────────────────────

q1_max_ramp_by_run = {}

for run_id in all_run_ids:
    if run_id not in tc_run_ids:
        q1_max_ramp_by_run[run_id] = {"tc_id": None, "max_preheat_ramp_c_per_s": None}
        continue

    run_tc = tc[tc["run_id"] == run_id]
    best_tc, best_ramp = None, -1.0

    for tc_id, grp in run_tc.groupby("tc_id"):
        grp = grp.sort_values("time_s")
        ramp = _max_preheat_ramp(
            grp["time_s"].values, grp["temperature_c"].values,
            PREHEAT_MIN_C, PREHEAT_MAX_C
        )
        if ramp is not None and ramp > best_ramp:
            best_ramp = ramp
            best_tc = tc_id

    if best_tc is not None:
        q1_max_ramp_by_run[run_id] = {
            "tc_id": best_tc,
            "max_preheat_ramp_c_per_s": best_ramp
        }
    else:
        q1_max_ramp_by_run[run_id] = {"tc_id": None, "max_preheat_ramp_c_per_s": None}

violating_runs = sorted([
    rid for rid, v in q1_max_ramp_by_run.items()
    if v["max_preheat_ramp_c_per_s"] is not None
    and v["max_preheat_ramp_c_per_s"] >= RAMP_RATE_LIMIT_C_PER_S
])

q1 = {
    "ramp_rate_limit_c_per_s": RAMP_RATE_LIMIT_C_PER_S,
    "violating_runs": violating_runs,
    "max_ramp_by_run": q1_max_ramp_by_run
}

with open(os.path.join(OUTPUT_DIR, "q01.json"), "w") as f:
    json.dump(q1, f, indent=2)
print("Q1 done:", len(violating_runs), "violating runs")


# ─── Q2: Time Above Liquidus (TAL) ────────────────────────────────────────

q2 = []

for run_id in all_run_ids:
    liquidus = liquidus_map.get(run_id)

    if run_id not in tc_run_ids or liquidus is None:
        q2.append({
            "run_id": run_id,
            "tc_id": None,
            "tal_s": None,
            "required_min_tal_s": TAL_MIN_S,
            "required_max_tal_s": TAL_MAX_S,
            "status": "non-compliant"
        })
        continue

    run_tc = tc[tc["run_id"] == run_id]
    # Select TC with MINIMUM TAL (worst case for wetting)
    best_tc, best_tal = None, float("inf")

    for tc_id, grp in run_tc.groupby("tc_id"):
        grp = grp.sort_values("time_s")
        tal = _tal_seconds(grp["time_s"].values, grp["temperature_c"].values, liquidus)
        if tal < best_tal:
            best_tal = tal
            best_tc = tc_id

    if best_tc is None:
        best_tal = None
        status = "non-compliant"
    else:
        status = "compliant" if TAL_MIN_S <= best_tal <= TAL_MAX_S else "non-compliant"

    q2.append({
        "run_id": run_id,
        "tc_id": best_tc,
        "tal_s": best_tal,
        "required_min_tal_s": TAL_MIN_S,
        "required_max_tal_s": TAL_MAX_S,
        "status": status
    })

q2.sort(key=lambda x: x["run_id"])

with open(os.path.join(OUTPUT_DIR, "q02.json"), "w") as f:
    json.dump(q2, f, indent=2)
print("Q2 done:", sum(1 for x in q2 if x["status"] == "non-compliant"), "non-compliant")


# ─── Q3: Peak Temperature ─────────────────────────────────────────────────

q3_min_peak_by_run = {}
failing_runs = []

for run_id in all_run_ids:
    liquidus = liquidus_map.get(run_id)
    required_min_peak = round(liquidus + PEAK_MARGIN_C, 2) if liquidus is not None else None

    if run_id not in tc_run_ids:
        q3_min_peak_by_run[run_id] = {
            "tc_id": None,
            "peak_temp_c": None,
            "required_min_peak_c": required_min_peak
        }
        failing_runs.append(run_id)
        continue

    run_tc = tc[tc["run_id"] == run_id]
    # Select TC with MINIMUM peak (worst case — coldest spot)
    best_tc, best_peak = None, float("inf")

    for tc_id, grp in run_tc.groupby("tc_id"):
        grp = grp.sort_values("time_s")
        peak = _peak_temp(grp["temperature_c"].values)
        if peak < best_peak:
            best_peak = peak
            best_tc = tc_id

    if best_tc is None:
        q3_min_peak_by_run[run_id] = {
            "tc_id": None,
            "peak_temp_c": None,
            "required_min_peak_c": required_min_peak
        }
        failing_runs.append(run_id)
    else:
        q3_min_peak_by_run[run_id] = {
            "tc_id": best_tc,
            "peak_temp_c": best_peak,
            "required_min_peak_c": required_min_peak
        }
        if required_min_peak is not None and best_peak < required_min_peak:
            failing_runs.append(run_id)

failing_runs = sorted(failing_runs)

q3 = {
    "failing_runs": failing_runs,
    "min_peak_by_run": q3_min_peak_by_run
}

with open(os.path.join(OUTPUT_DIR, "q03.json"), "w") as f:
    json.dump(q3, f, indent=2)
print("Q3 done:", len(failing_runs), "failing runs")


# ─── Q4: Conveyor Speed Feasibility ───────────────────────────────────────

q4 = []

for _, row in mes.drop_duplicates("run_id").sort_values("run_id").iterrows():
    run_id = row["run_id"]
    req_min = round(
        row["boards_per_min"] * row["board_length_cm"] / row["loading_factor"], 2
    )
    actual = round(float(row["conveyor_speed_cm_min"]), 2)
    meets = bool(actual >= req_min)

    q4.append({
        "run_id": run_id,
        "required_min_speed_cm_min": req_min,
        "actual_speed_cm_min": actual,
        "meets": meets
    })

q4.sort(key=lambda x: x["run_id"])

with open(os.path.join(OUTPUT_DIR, "q04.json"), "w") as f:
    json.dump(q4, f, indent=2)
print("Q4 done:", sum(1 for x in q4 if not x["meets"]), "not meeting speed")


# ─── Q5: Best Run Per Board Family ────────────────────────────────────────

# Aggregate defects per run
run_defect_agg = defects.groupby("run_id").agg(
    total_defects=("defect_count", "sum"),
    fp_yield_pct=("fp_yield_pct", "first")
).reset_index()

# Merge with MES for board_family and boards_per_min
mes_dedup = mes[["run_id", "board_family", "boards_per_min"]].drop_duplicates("run_id")
merged = run_defect_agg.merge(mes_dedup, on="run_id", how="left")

# Sort within each family: best yield (desc), fewest defects (asc), highest throughput (desc)
merged = merged.sort_values(
    by=["board_family", "fp_yield_pct", "total_defects", "boards_per_min"],
    ascending=[True, False, True, False]
)

q5 = []
for family, group in merged.groupby("board_family", sort=True):
    runs = group["run_id"].tolist()
    q5.append({
        "board_family": family,
        "best_run_id": runs[0],
        "runner_up_run_ids": sorted(runs[1:])
    })

with open(os.path.join(OUTPUT_DIR, "q05.json"), "w") as f:
    json.dump(q5, f, indent=2)
print("Q5 done:", len(q5), "board families")


# ─── Final Validation ──────────────────────────────────────────────────────

print("\n=== Validation ===")
for qfile in ["q01.json", "q02.json", "q03.json", "q04.json", "q05.json"]:
    path = os.path.join(OUTPUT_DIR, qfile)
    with open(path) as f:
        data = json.load(f)
    print(f"{qfile}: OK ({type(data).__name__})")

# Cross-check: all runs present
for qfile, key in [("q01.json", "max_