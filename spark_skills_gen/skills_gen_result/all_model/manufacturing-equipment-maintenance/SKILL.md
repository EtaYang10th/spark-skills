---
name: reflow-maintenance-profile-analysis
description: Analyze reflow oven maintenance/process-compliance tasks by extracting requirements from a handbook PDF, combining MES logs, thermocouple traces, and defect data, and producing validated JSON outputs for ramp rate, TAL, peak temperature, conveyor feasibility, and best-run selection.
tools: [bash, python, pandas, pdfinfo, pdftotext]
---

# Reflow Oven Handbook + Process Data Analysis

Use this skill when a task asks you to answer manufacturing equipment maintenance or process-compliance questions by combining:

- a **reflow machine handbook PDF**
- **thermocouple trace data**
- **MES run logs**
- **test defect / quality data**
- strict **JSON output schemas**

This pattern appears in root-cause analysis, profile verification, process-window compliance checks, and run selection tasks.

The most important principle is:

> **Do not invent thermal limits or formulas. Extract them from the handbook, then compute values from actual data.**

---

## When to Use This Skill

Use this workflow when the task includes questions like:

- âCalculate maximum preheat ramp rateâ
- âDetermine time above liquidus (TAL)â
- âCheck peak temperature complianceâ
- âCheck whether conveyor speed is feasibleâ
- âSelect best run per board family based on quality and efficiencyâ
- âUse the handbook to determine the applicable window/limit/formulaâ

Typical files:

- `handbook.pdf`
- `mes_log.csv`
- `thermocouples.csv`
- `test_defects.csv`

---

## High-Level Workflow

1. **Inspect available files and instructions first**
   - Confirm the exact filenames, output schemas, and sorting/rounding rules.
   - Check for local `AGENTS.md` / `SKILL.md` instructions before computing anything.
   - Why: many failures come from schema mismatches, wrong sort order, or ignoring local instructions.

2. **Extract handbook text early and search for process definitions**
   - Use `pdfinfo` and `pdftotext` first; use Python PDF readers only if needed.
   - Identify:
     - preheat temperature region
     - ramp-rate equation or wording
     - allowable ramp limit
     - liquidus temperature and TAL window
     - peak temperature requirement
     - conveyor / heated length / dwell / line-speed formulas
     - any product-family or machine-model tables
   - Why: the numeric rules must come from the handbook, not from generic SMT assumptions.

3. **Load CSVs and inspect schema before writing logic**
   - Print columns, dtypes, sample rows, and distinct IDs.
   - Detect how thermocouple traces are structured:
     - long format (`run_id`, `tc_id`, `time_s`, `temp_c`)
     - wide format (`time_s`, `TC1`, `TC2`, ...)
     - one file per run or combined file
   - Why: wrong assumptions about layout cause silent miscalculations.

4. **Normalize thermocouple traces**
   - Convert to a canonical long format with:
     - `run_id`
     - `tc_id`
     - `time_s`
     - `temp_c`
   - Sort by `run_id`, `tc_id`, `time_s`
   - Drop duplicates and invalid rows safely
   - Why: every thermal metric depends on ordered time-temperature sequences.

5. **Compute preheat ramp correctly**
   - Use the handbook-defined **preheat region**.
   - If the handbook implies a region like `100-150Â°C`, compute slope using segments that **intersect** that window, not only points strictly inside it.
   - Interpolate boundary crossings if needed.
   - Select the **most representative thermocouple** according to handbook intent; for compliance, this is often the **worst-case** thermocouple for that metric.
   - Why: a common failure is using only adjacent points already inside the region, which underestimates peak ramp.

6. **Compute TAL conservatively and per thermocouple**
   - Use the handbook's liquidus threshold.
   - Measure total time spent above liquidus, handling threshold crossings by interpolation.
   - For run-level compliance, use the thermocouple that reflects the most conservative interpretation required by the task/handbook. In many SMT tasks, that means **minimum TAL across TCs**.
   - Why: TAL is sensitive to crossing interpolation and TC-selection policy.

7. **Compute peak-temperature compliance**
   - Determine each thermocouple's peak temperature.
   - For a run-level requirement like âall assemblies must meet minimum peak,â the relevant run value is typically the **minimum peak among TCs**.
   - If a run has no thermocouple data, mark it failing if the prompt says so.
   - Why: using max peak instead of min peak can incorrectly pass cold spots.

8. **Compute conveyor feasibility from board geometry and thermal dwell rules**
   - Extract handbook formula for line speed / throughput feasibility.
   - Use board dimensions and loading-factor / dwell assumptions exactly as described.
   - If the handbook provides oven heated lengths by model, map MES model names to those lengths explicitly.
   - Why: tasks often expect machine-specific speed calculations rather than generic guesses.

9. **Rank best run per board family using compliance first, then quality, then efficiency**
   - Build a ranking that prioritizes process-compliant runs unless the task says otherwise.
   - Then use defect/quality metrics and MES efficiency metrics as tie-breakers.
   - Keep ranking deterministic.
   - Why: âbest runâ failures often come from using yield alone and ignoring compliance or operational signals.

10. **Validate output formatting before finalizing**
    - Round floats to 2 decimals
    - Sort arrays and dictionary keys by the required identifiers
    - Use `null` where necessary
    - Ensure exact JSON shape
    - Why: many tasks fail despite correct calculations because of formatting.

---

## Practical Handbook Extraction Workflow

Start with system tools. They are fast and usually enough.

```bash
set -euo pipefail

PDF="/app/data/handbook.pdf"

pdfinfo "$PDF" || true
pdftotext "$PDF" - | sed -n '1,240p'
```

Search for likely keywords:

```bash
set -euo pipefail

PDF="/app/data/handbook.pdf"
TXT="$(mktemp)"
pdftotext "$PDF" "$TXT"

grep -Ein "preheat|ramp|liquidus|time above liquidus|TAL|peak|conveyor|speed|heated length|board length|dwell|loading factor|zone|profile" "$TXT" | sed -n '1,200p'
```

If `pdftotext` misses text or tables, use Python with `pypdf`.

```bash
python - <<'PY'
import sys
from pathlib import Path

pdf_path = Path("/app/data/handbook.pdf")
try:
    from pypdf import PdfReader
except ImportError:
    print("Installing pypdf...", file=sys.stderr)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pypdf"])
    from pypdf import PdfReader

reader = PdfReader(str(pdf_path))
for i, page in enumerate(reader.pages[:20]):
    text = page.extract_text() or ""
    if any(k in text.lower() for k in [
        "preheat", "ramp", "liquidus", "peak", "conveyor", "heated length", "dwell"
    ]):
        print(f"\n--- PAGE {i+1} ---\n")
        print(text[:4000])
PY
```

### What to extract from the handbook

Create a structured note immediately after reading:

```python
handbook_rules = {
    "preheat_region_c": [100.0, 150.0],   # example; replace with actual handbook values
    "ramp_limit_c_per_s": 2.0,
    "liquidus_temp_c": 217.0,
    "tal_window_s": [30.0, 60.0],
    "peak_rule": "liquidus_plus_delta",
    "peak_delta_c": 20.0,
    "heated_lengths_cm_by_model": {
        # fill from handbook table
    },
    "speed_formula_notes": "minimum line speed = boards_per_min * board_length / loading_factor",
}
print(handbook_rules)
```

Do **not** keep placeholder values in final work. Replace everything with handbook-derived values.

---

## Inspect and Normalize Input Data

Always inspect schemas before assumptions.

```bash
python - <<'PY'
import pandas as pd
from pathlib import Path

for path in sorted(Path("/app/data").glob("*.csv")):
    print(f"\n=== {path.name} ===")
    df = pd.read_csv(path)
    print("shape:", df.shape)
    print("columns:", list(df.columns))
    print(df.head(3).to_string(index=False))
PY
```

### Canonical thermocouple normalization

This function handles both long and wide formats.

```python
import pandas as pd
import numpy as np

def normalize_thermocouples(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return canonical columns: run_id, tc_id, time_s, temp_c
    """
    cols_lower = {c.lower(): c for c in df.columns}
    out = None

    # Case 1: already long format
    required_variants = {
        "run_id": ["run_id", "run", "recipe_run_id"],
        "tc_id": ["tc_id", "thermocouple", "tc", "sensor_id"],
        "time_s": ["time_s", "time_sec", "seconds", "t_s", "elapsed_s"],
        "temp_c": ["temp_c", "temperature_c", "temp", "temperature", "tc_temp_c"],
    }

    def find_col(candidates):
        for cand in candidates:
            for actual in df.columns:
                if actual.lower() == cand:
                    return actual
        return None

    mapped = {k: find_col(v) for k, v in required_variants.items()}
    if all(mapped.values()):
        out = df[[mapped["run_id"], mapped["tc_id"], mapped["time_s"], mapped["temp_c"]]].copy()
        out.columns = ["run_id", "tc_id", "time_s", "temp_c"]

    # Case 2: wide format with TC columns
    if out is None:
        run_col = find_col(["run_id", "run", "recipe_run_id"])
        time_col = find_col(["time_s", "time_sec", "seconds", "t_s", "elapsed_s"])
        if run_col and time_col:
            tc_cols = [c for c in df.columns if c not in {run_col, time_col}]
            numeric_tc_cols = []
            for c in tc_cols:
                try:
                    pd.to_numeric(df[c], errors="raise")
                    numeric_tc_cols.append(c)
                except Exception:
                    pass
            if numeric_tc_cols:
                out = df.melt(
                    id_vars=[run_col, time_col],
                    value_vars=numeric_tc_cols,
                    var_name="tc_id",
                    value_name="temp_c",
                )
                out = out.rename(columns={run_col: "run_id", time_col: "time_s"})

    if out is None:
        raise ValueError("Unable to normalize thermocouple data: unsupported schema")

    out["run_id"] = out["run_id"].astype(str)
    out["tc_id"] = out["tc_id"].astype(str)
    out["time_s"] = pd.to_numeric(out["time_s"], errors="coerce")
    out["temp_c"] = pd.to_numeric(out["temp_c"], errors="coerce")
    out = out.dropna(subset=["run_id", "tc_id", "time_s", "temp_c"]).copy()
    out = out.sort_values(["run_id", "tc_id", "time_s"], kind="stable")
    out = out.drop_duplicates(subset=["run_id", "tc_id", "time_s"], keep="last")
    return out.reset_index(drop=True)
```

---

## Preheat Ramp Rate Computation

### Correct interpretation

When the handbook defines preheat over a temperature window such as `100-150Â°C`, do **not** only compute slopes between points where both temperatures already lie inside that band.

Instead:

- consider every segment that **intersects** the preheat region
- clip/interpolate the segment to the window boundaries
- compute slope as `ÎT / Ît`
- take the maximum slope within that region

This is the key fix for common underestimation errors.

### Segment clipping and ramp computation

```python
from typing import Optional
import math
import pandas as pd

def interpolate_time_at_temp(t1, y1, t2, y2, target_temp) -> Optional[float]:
    if any(pd.isna(v) for v in [t1, y1, t2, y2, target_temp]):
        return None
    if t2 == t1:
        return None
    if y1 == y2:
        if y1 == target_temp:
            return float(t1)
        return None
    lo, hi = sorted([y1, y2])
    if not (lo <= target_temp <= hi):
        return None
    frac = (target_temp - y1) / (y2 - y1)
    return float(t1 + frac * (t2 - t1))

def max_ramp_in_temp_window(trace: pd.DataFrame, low_c: float, high_c: float) -> Optional[float]:
    """
    trace columns: time_s, temp_c
    Returns max dT/dt within the temperature window, using segment clipping.
    """
    if trace.empty or len(trace) < 2:
        return None

    trace = trace.sort_values("time_s", kind="stable")
    max_slope = None

    times = trace["time_s"].to_list()
    temps = trace["temp_c"].to_list()

    for i in range(len(trace) - 1):
        t1, t2 = float(times[i]), float(times[i + 1])
        y1, y2 = float(temps[i]), float(temps[i + 1])

        if t2 <= t1:
            continue

        seg_low = min(y1, y2)
        seg_high = max(y1, y2)
        if seg_high < low_c or seg_low > high_c:
            continue  # no intersection with preheat window

        # Determine clipped endpoints in temperature-space
        start_temp = max(low_c, seg_low if seg_low >= low_c else low_c)
        end_temp = min(high_c, seg_high if seg_high <= high_c else high_c)

        # For monotonic segments, slope magnitude is constant on the segment.
        # We still ensure some actual overlap with the target window.
        t_start = interpolate_time_at_temp(t1, y1, t2, y2, low_c) if seg_low < low_c else min(t1, t2)
        t_end = interpolate_time_at_temp(t1, y1, t2, y2, high_c) if seg_high > high_c else max(t1, t2)

        # Fallback if boundary interpolation is not applicable
        if t_start is None:
            t_start = t1
        if t_end is None:
            t_end = t2

        dt = t2 - t1
        if dt <= 0:
            continue

        slope = (y2 - y1) / dt
        if max_slope is None or slope > max_slope:
            max_slope = slope

    return None if max_slope is None else float(max_slope)
```

### Run-level selection of representative thermocouple

For compliance tasks, the âmost representativeâ thermocouple is often the one that best captures the **worst-case** risk for that metric.

For preheat ramp-rate checking, that usually means the **maximum** ramp among TCs.

```python
def summarize_preheat_ramp(tc_long: pd.DataFrame, low_c: float, high_c: float) -> dict:
    results = {}
    for run_id, run_df in tc_long.groupby("run_id", sort=True):
        tc_metrics = []
        for tc_id, tc_df in run_df.groupby("tc_id", sort=True):
            val = max_ramp_in_temp_window(tc_df[["time_s", "temp_c"]], low_c, high_c)
            if val is not None:
                tc_metrics.append((str(tc_id), float(val)))
        if tc_metrics:
            tc_id, ramp = max(tc_metrics, key=lambda x: (x[1], x[0]))
            results[str(run_id)] = {
                "tc_id": tc_id,
                "max_preheat_ramp_c_per_s": ramp,
            }
        else:
            results[str(run_id)] = {
                "tc_id": None,
                "max_preheat_ramp_c_per_s": None,
            }
    return dict(sorted(results.items()))
```

---

## Time Above Liquidus (TAL)

### Correct interpretation

Use the handbook's liquidus temperature and TAL compliance window. Compute actual time spent above the threshold by interpolating threshold crossings.

For run-level compliance, choose the thermocouple according to the task's conservative logic. In many real tasks:

- use the **minimum TAL across thermocouples**
- because the coldest location determines whether the board truly spent enough time above liquidus

### TAL computation with interpolation

```python
from typing import Optional
import pandas as pd

def time_above_threshold(trace: pd.DataFrame, threshold_c: float) -> Optional[float]:
    if trace.empty or len(trace) < 2:
        return None

    trace = trace.sort_values("time_s", kind="stable")
    total = 0.0

    times = trace["time_s"].to_list()
    temps = trace["temp_c"].to_list()

    for i in range(len(trace) - 1):
        t1, t2 = float(times[i]), float(times[i + 1])
        y1, y2 = float(temps[i]), float(temps[i + 1])

        if t2 <= t1:
            continue

        above1 = y1 > threshold_c
        above2 = y2 > threshold_c

        if above1 and above2:
            total += (t2 - t1)
            continue

        if (y1 - threshold_c) * (y2 - threshold_c) < 0:
            tcross = interpolate_time_at_temp(t1, y1, t2, y2, threshold_c)
            if tcross is None:
                continue
            if above1 and not above2:
                total += (tcross - t1)
            elif not above1 and above2:
                total += (t2 - tcross)
        elif y1 == threshold_c and above2:
            total += (t2 - t1)
        elif above1 and y2 == threshold_c:
            total += (t2 - t1)

    return float(total)

def summarize_tal(tc_long: pd.DataFrame, liquidus_c: float, tal_min_s: float, tal_max_s: float):
    rows = []
    for run_id, run_df in tc_long.groupby("run_id", sort=True):
        per_tc = []
        for tc_id, tc_df in run_df.groupby("tc_id", sort=True):
            tal = time_above_threshold(tc_df[["time_s", "temp_c"]], liquidus_c)
            if tal is not None:
                per_tc.append((str(tc_id), float(tal)))
        if per_tc:
            # Conservative run-level choice: minimum TAL
            tc_id, tal = min(per_tc, key=lambda x: (x[1], x[0]))
            status = "compliant" if tal_min_s <= tal <= tal_max_s else "non-compliant"
            rows.append({
                "run_id": str(run_id),
                "tc_id": tc_id,
                "tal_s": tal,
                "required_min_tal_s": float(tal_min_s),
                "required_max_tal_s": float(tal_max_s),
                "status": status,
            })
    return rows
```

---

## Peak Temperature Compliance

### Correct interpretation

If a handbook requires a minimum peak temperature for the assembly, the run-level compliance should usually be checked against the **minimum peak across thermocouples**, because the coldest point governs whether the board actually met reflow conditions.

If the prompt says a run with no thermocouple data fails, implement that explicitly.

```python
from typing import Dict, List, Tuple

def summarize_peak(tc_long: pd.DataFrame, required_min_peak_c: float, all_run_ids=None):
    summary = {}
    present_runs = set()

    for run_id, run_df in tc_long.groupby("run_id", sort=True):
        present_runs.add(str(run_id))
        peaks = []
        for tc_id, tc_df in run_df.groupby("tc_id", sort=True):
            peak = pd.to_numeric(tc_df["temp_c"], errors="coerce").max()
            if pd.notna(peak):
                peaks.append((str(tc_id), float(peak)))
        if peaks:
            tc_id, peak = min(peaks, key=lambda x: (x[1], x[0]))
            summary[str(run_id)] = {
                "tc_id": tc_id,
                "peak_temp_c": peak,
                "required_min_peak_c": float(required_min_peak_c),
            }
        else:
            summary[str(run_id)] = {
                "tc_id": None,
                "peak_temp_c": None,
                "required_min_peak_c": float(required_min_peak_c),
            }

    if all_run_ids is not None:
        for run_id in map(str, all_run_ids):
            if run_id not in summary:
                summary[run_id] = {
                    "tc_id": None,
                    "peak_temp_c": None,
                    "required_min_peak_c": float(required_min_peak_c),
                }

    failing_runs = sorted([
        run_id for run_id, rec in summary.items()
        if rec["peak_temp_c"] is None or rec["peak_temp_c"] < required_min_peak_c
    ])
    return {"failing_runs": failing_runs, "min_peak_by_run": dict(sorted(summary.items()))}
```

---

## Conveyor Speed Feasibility

### Common handbook pattern

These tasks often require combining:

- board geometry from MES
- throughput / loading factor requirements
- machine heated length from handbook tables
- dwell-time requirement from handbook profile guidance

You may see formulas like:

- `minimum_line_speed = boards_per_min Ã board_length / loading_factor`
- or `maximum_line_speed = heated_length / required_dwell_time`
- actual feasibility may require satisfying **both** throughput and thermal dwell constraints

Always implement the formula the handbook actually states.

### Example implementation pattern

```python
import pandas as pd

def compute_conveyor_feasibility(
    mes_df: pd.DataFrame,
    heated_lengths_cm_by_model: dict,
    required_dwell_s_by_family: dict | None = None,
    default_loading_factor: float | None = None,
):
    """
    Example generic function. Adapt column names and formulas to the actual handbook.
    """
    rows = []

    for _, r in mes_df.sort_values("run_id", kind="stable").iterrows():
        run_id = str(r["run_id"])
        model = str(r["oven_model"])
        actual_speed = float(r["actual_speed_cm_min"])

        heated_length_cm = heated_lengths_cm_by_model.get(model)
        if heated_length_cm is None:
            rows.append({
                "run_id": run_id,
                "required_min_speed_cm_min": None,
                "actual_speed_cm_min": actual_speed,
                "meets": False,
            })
            continue

        # Example throughput-driven requirement
        required_min_speed = None
        if {"boards_per_min", "board_length_cm", "loading_factor"}.issubset(mes_df.columns):
            boards_per_min = float(r["boards_per_min"])
            board_length_cm = float(r["board_length_cm"])
            loading_factor = float(r["loading_factor"]) if pd.notna(r["loading_factor"]) else default_loading_factor
            if loading_factor and loading_factor > 0:
                required_min_speed = boards_per_min * board_length_cm / loading_factor

        # Example dwell-driven upper constraint, if needed by task:
        # max_speed_cm_min = heated_length_cm / required_dwell_min
        # Add if handbook requires it.

        meets = (required_min_speed is not None and actual_speed >= required_min_speed)

        rows.append({
            "run_id": run_id,
            "required_min_speed_cm_min": required_min_speed,
            "actual_speed_cm_min": actual_speed,
            "meets": bool(meets),
        })
    return rows
```

### Important

If the handbook gives **heated lengths by machine model**, build an explicit lookup and verify model-name matching carefully.

```python
heated_lengths_cm_by_model = {
    "10Z_Convection": 350.0,   # replace with actual handbook values
    "8Z_Convection": 280.0,
    "8Z_IR+Conv": 260.0,
}
```

Do not guess machine lengths; extract them from the handbook table.

---

## Best Run per Board Family

### Recommended ranking strategy

When selecting âbest runâ from MES + defect data, rank in this order unless the prompt or handbook specifies otherwise:

1. **Compliance first**
   - Prefer runs passing thermal/process checks.
2. **Quality next**
   - Lower defect counts / higher yield wins.
3. **Efficiency next**
   - Better throughput, shorter cycle, or better speed utilization wins.
4. **Deterministic tie-break**
   - earlier run ID or an explicit timestamp ordering

This avoids a common mistake: choosing the highest-yield run even when it is thermally non-compliant.

### Example ranking implementation

```python
import pandas as pd
import numpy as np

def build_run_score_table(mes_df, defects_df, compliance_flags_df):
    """
    Returns one row per run_id with normalized scoring columns.
    Adapt the quality columns to actual data.
    """
    df = mes_df.copy()

    if "run_id" not in df.columns:
        raise ValueError("MES data must include run_id")

    # Aggregate defects if needed
    if "run_id" in defects_df.columns:
        defect_agg = defects_df.groupby("run_id", as_index=False).agg(
            total_defects=("defect_count", "sum") if "defect_count" in defects_df.columns else ("run_id", "size")
        )
        df = df.merge(defect_agg, on="run_id", how="left")
    else:
        df["total_defects"] = np.nan

    df = df.merge(compliance_flags_df, on="run_id", how="left")
    df["process_compliant"] = df["process_compliant"].fillna(False)
    df["total_defects"] = df["total_defects"].fillna(0)

    # Optional quality metrics
    for col in ["fp_yield_pct", "final_yield_pct", "throughput_bph"]:
        if col not in df.columns:
            df[col] = np.nan

    return df

def choose_best_run_per_family(score_df: pd.DataFrame):
    out = []

    needed = {"run_id", "board_family", "process_compliant", "total_defects"}
    missing = needed - set(score_df.columns)
    if missing:
        raise ValueError(f"Missing columns for ranking: {missing}")

    for fam, fam_df in score_df.groupby("board_family", sort=True):
        fam_df = fam_df.copy()

        # Deterministic sort: compliant first, fewer defects, higher yields, higher throughput, lower run_id
        sort_cols = []
        ascending = []

        fam_df["run_id"] = fam_df["run_id"].astype(str)

        sort_cols += ["process_compliant"]
        ascending += [False]

        sort_cols += ["total_defects"]
        ascending += [True]

        if "fp_yield_pct" in fam_df.columns:
            sort_cols += ["fp_yield_pct"]
            ascending += [False]

        if "final_yield_pct" in fam_df.columns:
            sort_cols += ["final_yield_pct"]
            ascending += [False]

        if "throughput_bph" in fam_df.columns:
            sort_cols += ["throughput_bph"]
            ascending += [False]

        sort_cols += ["run_id"]
        ascending += [True]

        ranked = fam_df.sort_values(sort_cols, ascending=ascending, kind="stable")
        best_run_id = str(ranked.iloc[0]["run_id"])
        runner_ups = [str(x) for x in ranked.iloc[1:]["run_id"].tolist()]

        out.append({
            "board_family": str(fam),
            "best_run_id": best_run_id,
            "runner_up_run_ids": runner_ups,
        })

    return out
```

---

## Output Formatting Utilities

Use one formatter across all outputs.

```python
import math

def round_or_none(x, ndigits=2):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass
    return round(float(x), ndigits)

def recursively_round(obj):
    if isinstance(obj, dict):
        return {k: recursively_round(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [recursively_round(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 2)
    return obj
```

Write JSON deterministically:

```python
import json
from pathlib import Path

def write_json(path: str, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recursively_round(data), f, indent=2, ensure_ascii=False)
```

---

## Verification Checklist Before Finalizing

Run all of these checks:

```python
import json
from pathlib import Path

def verify_json_file(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

for path in [
    "/app/output/q01.json",
    "/app/output/q02.json",
    "/app/output/q03.json",
    "/app/output/q04.json",
    "/app/output/q05.json",
]:
    data = verify_json_file(path)
    print(path, "OK", type(data).__name__)
```

Also confirm sorted order explicitly:

```python
import json

q01 = json.load(open("/app/output/q01.json"))
assert list(q01["max_ramp_by_run"].keys()) == sorted(q01["max_ramp_by_run"].keys())

q03 = json.load(open("/app/output/q03.json"))
assert list(q03["min_peak_by_run"].keys()) == sorted(q03["min_peak_by_run"].keys())

q02 = json.load(open("/app/output/q02.json"))
assert [r["run_id"] for r in q02] == sorted(r["run_id"] for r in q02)

q04 = json.load(open("/app/output/q04.json"))
assert [r["run_id"] for r in q04] == sorted(r["run_id"] for r in q04)

q05 = json.load(open("/app/output/q05.json"))
assert [r["board_family"] for r in q05] == sorted(r["board_family"] for r in q05)

print("Sort checks passed")
```

---

## Common Pitfalls

### 1) Wrong preheat ramp logic
**Bad pattern:** compute slopes only for adjacent samples where both temperatures are already inside the preheat window.

**Correct pattern:** consider any segment that intersects the preheat region and interpolate entry/exit at the handbook-defined boundaries.

This was the most important corrected mistake.

---

### 2) Using the wrong thermocouple for run-level reporting
Do not arbitrarily choose `TC1` or average all TCs.

Choose the thermocouple that matches the metric's compliance intent:

- **Ramp rate:** often the **maximum** ramp TC
- **TAL:** often the **minimum** TAL TC
- **Peak requirement:** often the **minimum** peak TC

If the handbook/task says otherwise, follow that.

---

### 3) Guessing SMT defaults instead of extracting from the handbook
Avoid generic assumptions like:

- liquidus is always 217Â°C
- TAL is always 30-60 s
- ramp is always `<2Â°C/s`
- peak is always `liquidus + 20Â°C`

These are common industry values, but the task requires handbook-backed numbers.

---

### 4) Selecting best run from yield alone
âBest runâ often depends on:

- compliance status
- quality/defect rate
- operational efficiency

A run with slightly better yield may still lose if it is non-compliant or operationally worse.

---

### 5) Ignoring runs with missing thermocouple data
If the task says a run with no thermocouple data is failing, encode that explicitly.

Do not silently skip those runs in compliance outputs.

---

### 6) Failing due to formatting, not analytics
Common formatting errors:

- not rounding to 2 decimals
- unsorted arrays/dicts
- using `NaN` instead of `null`
- wrong key names
- wrong scalar types (`"true"` vs `true`)

---

## Reference Implementation

The following script is a complete end-to-end template. It is designed to be copy-pasted and adapted to similar reflow-maintenance tasks. It loads handbook/data files, normalizes traces, computes all five question types, and writes JSON outputs.

> Replace the handbook-extraction placeholders with values actually extracted from the PDF for the specific task.

```python
#!/usr/bin/env python3
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

DATA_DIR = Path("/app/data")
OUT_DIR = Path("/app/output")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------
# Utility functions
# ----------------------------

def run_cmd(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return p.stdout
    except Exception as e:
        return ""

def round_or_none(x, ndigits=2):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass
    return round(float(x), ndigits)

def recursively_round(obj):
    if isinstance(obj, dict):
        return {k: recursively_round(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [recursively_round(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 2)
    return obj

def write_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recursively_round(data), f, indent=2, ensure_ascii=False)

def find_csv(name_hint: str) -> Path:
    candidates = list(DATA_DIR.glob("*.csv"))
    for p in candidates:
        if name_hint.lower() in p.name.lower():
            return p
    raise FileNotFoundError(f"Could not find CSV matching {name_hint!r} in {DATA_DIR}")

def load_csv(name_hint: str) -> pd.DataFrame:
    path = find_csv(name_hint)
    return pd.read_csv(path)

def extract_handbook_text(pdf_path: Path) -> str:
    txt = run_cmd(["pdftotext", str(pdf_path), "-"])
    if txt.strip():
        return txt

    try:
        from pypdf import PdfReader
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pypdf"])
        from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)

def parse_first_number(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text, flags=re.I | re.S)
    if not m:
        return None
    for g in m.groups():
        if g is not None:
            try:
                return float(g)
            except Exception:
                continue
    return None


# ----------------------------
# Thermocouple normalization
# ----------------------------

def normalize_thermocouples(df: pd.DataFrame) -> pd.DataFrame:
    def find_col(candidates):
        for cand in candidates:
            for actual in df.columns:
                if actual.lower() == cand.lower():
                    return actual
        return None

    out = None
    run_col = find_col(["run_id", "run", "recipe_run_id"])
    tc_col = find_col(["tc_id", "thermocouple", "tc", "sensor_id"])
    time_col = find_col(["time_s", "time_sec", "seconds", "t_s", "elapsed_s"])
    temp_col = find_col(["temp_c", "temperature_c", "temp", "temperature", "tc_temp_c"])

    if run_col and tc_col and time_col and temp_col:
        out = df[[run_col, tc_col, time_col, temp_col]].copy()
        out.columns = ["run_id", "tc_id", "time_s", "temp_c"]

    if out is None and run_col and time_col:
        tc_candidates = [c for c in df.columns if c not in {run_col, time_col}]
        numeric_tc_cols = []
        for c in tc_candidates:
            try:
                pd.to_numeric(df[c], errors="raise")
                numeric_tc_cols.append(c)
            except Exception:
                pass
        if numeric_tc_cols:
            out = df.melt(
                id_vars=[run_col, time_col],
                value_vars=numeric_tc_cols,
                var_name="tc_id",
                value_name="temp_c",
            )
            out = out.rename(columns={run_col: "run_id", time_col: "time_s"})

    if out is None:
        raise ValueError("Unsupported thermocouple schema")

    out["run_id"] = out["run_id"].astype(str)
    out["tc_id"] = out["tc_id"].astype(str)
    out["time_s"] = pd.to_numeric(out["time_s"], errors="coerce")
    out["temp_c"] = pd.to_numeric(out["temp_c"], errors="coerce")
    out = out.dropna(subset=["run_id", "tc_id", "time_s", "temp_c"]).copy()
    out = out.sort_values(["run_id", "tc_id", "time_s"], kind="stable")
    out = out.drop_duplicates(subset=["run_id", "tc_id", "time_s"], keep="last")
    return out.reset_index(drop=True)


# ----------------------------
# Thermal calculations
# ----------------------------

def interpolate_time_at_temp(t1, y1, t2, y2, target_temp) -> Optional[float]:
    if any(pd.isna(v) for v in [t1, y1, t2, y2, target_temp]):
        return None
    if t2 == t1:
        return None
    if y1 == y2:
        if y1 == target_temp:
            return float(t1)
        return None
    lo, hi = sorted([y1, y2])
    if not (lo <= target_temp <= hi):
        return None
    frac = (target_temp - y1) / (y2 - y1)
    return float(t1 + frac * (t2 - t1))

def max_ramp_in_temp_window(trace: pd.DataFrame, low_c: float, high_c: float) -> Optional[float]:
    if trace.empty or len(trace) < 2:
        return None

    trace = trace.sort_values("time_s", kind="stable")
    max_slope = None
    times = trace["time_s"].to_list()
    temps = trace["temp_c"].to_list()

    for i in range(len(trace) - 1):
        t1, t2 = float(times[i]), float(times[i + 1])
        y1, y2 = float(temps[i]), float(temps[i + 1])
        if t2 <= t1:
            continue

        seg_low = min(y1, y2)
        seg_high = max(y1, y2)
        if seg_high < low_c or seg_low > high_c:
            continue

        dt = t2 - t1
        if dt <= 0:
            continue

        slope = (y2 - y1) / dt
        if max_slope is None or slope > max_slope:
            max_slope = slope

    return None if max_slope is None else float(max_slope)

def time_above_threshold(trace: pd.DataFrame, threshold_c: float) -> Optional[float]:
    if trace.empty or len(trace) < 2:
        return None

    trace = trace.sort_values("time_s", kind="stable")
    total = 0.0
    times = trace["time_s"].to_list()
    temps = trace["temp_c"].to_list()

    for i in range(len(trace) - 1):
        t1, t2 = float(times[i]), float(times[i + 1])
        y1, y2 = float(temps[i]), float(temps[i + 1])

        if t2 <= t1:
            continue

        above1 = y1 > threshold_c
        above2 = y2 > threshold_c

        if above1 and above2:
            total += (t2 - t1)
            continue

        if (y1 - threshold_c) * (y2 - threshold_c) < 0:
            tcross = interpolate_time_at_temp(t1, y1, t2, y2, threshold_c)
            if tcross is None:
                continue
            if above1 and not above2:
                total += (tcross - t1)
            elif not above1 and above2:
                total += (t2 - tcross)
        elif y1 == threshold_c and above2:
            total += (t2 - t1)
        elif above1 and y2 == threshold_c:
            total += (t2 - t1)

    return float(total)


# ----------------------------
# Handbook parsing
# ----------------------------

def extract_rules_from_handbook(text: str) -> dict:
    """
    Adapt as needed to the actual handbook wording.
    This function intentionally uses conservative regex heuristics
    and should be manually inspected during task execution.
    """
    rules = {
        "preheat_low_c": None,
        "preheat_high_c": None,
        "ramp_limit_c_per_s": None,
        "liquidus_temp_c": None,
        "tal_min_s": None,
        "tal_max_s": None,
        "required_min_peak_c": None,
        "heated_lengths_cm_by_model": {},
    }

    # Example heuristics. Verify against actual handbook text.
    m = re.search(r"preheat.{0,80}?(\d+(?:\.\d+)?)\s*[--]\s*(\d+(?:\.\d+)?)\s*Â°?\s*C", text, re.I | re.S)
    if m:
        rules["preheat_low_c"] = float(m.group(1))
        rules["preheat_high_c"] = float(m.group(2))

    m = re.search(r"ramp.{0,80}?([<>]?\s*\d+(?:\.\d+)?)\s*Â°?\s*C\s*/\s*s", text, re.I | re.S)
    if m:
        num = re.search(r"(\d+(?:\.\d+)?)", m.group(1))
        if num:
            rules["ramp_limit_c_per_s"] = float(num.group(1))

    m = re.search(r"liquidus.{0,80}?(\d+(?:\.\d+)?)\s*Â°?\s*C", text, re.I | re.S)
    if m:
        rules["liquidus_temp_c"] = float(m.group(1))

    m = re.search(r"time above liquidus|TAL|wetting time", text, re.I)
    if m:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*[--]\s*(\d+(?:\.\d+)?)\s*s", text[m.start():m.start()+500], re.I | re.S)
        if m2:
            rules["tal_min_s"] = float(m2.group(1))
            rules["tal_max_s"] = float(m2.group(2))

    # Peak examples like "liquidus + 20C" or explicit minimum peak
    m = re.search(r"peak.{0,120}?liquidus.{0,20}?\+\s*(\d+(?:\.\d+)?)\s*Â°?\s*C", text, re.I | re.S)
    if m and rules["liquidus_temp_c"] is not None:
        rules["required_min_peak_c"] = rules["liquidus_temp_c"] + float(m.group(1))
    else:
        m = re.search(r"minimum peak.{0,40}?(\d+(?:\.\d+)?)\s*Â°?\s*C", text, re.I | re.S)
        if m:
            rules["required_min_peak_c"] = float(m.group(1))

    # Heated length table parsing can be highly handbook-specific.
    # Add direct regexes for known model strings if present.
    for model in ["10Z_Convection", "8Z_Convection", "8Z_IR+Conv"]:
        mm = re.search(rf"{re.escape(model)}.{0,80}?(\d+(?:\.\d+)?)\s*cm", text, re.I | re.S)
        if mm:
            rules["heated_lengths_cm_by_model"][model] = float(mm.group(1))

    return rules


# ----------------------------
# Question computations
# ----------------------------

def compute_q01(tc_long: pd.DataFrame, rules: dict) -> dict:
    low_c = rules["preheat_low_c"]
    high_c = rules["preheat_high_c"]
    limit = rules["ramp_limit_c_per_s"]
    if None in (low_c, high_c, limit):
        raise ValueError("Missing handbook rules for Q01")

    max_ramp_by_run = {}
    violating_runs = []

    for run_id, run_df in tc_long.groupby("run_id", sort=True):
        tc_metrics = []
        for tc_id, tc_df in run_df.groupby("tc_id", sort=True):
            ramp = max_ramp_in_temp_window(tc_df[["time_s", "temp_c"]], low_c, high_c)
            if ramp is not None:
                tc_metrics.append((str(tc_id), float(ramp)))

        if tc_metrics:
            tc_id, ramp = max(tc_metrics, key=lambda x: (x[1], x[0]))
            max_ramp_by_run[str(run_id)] = {
                "tc_id": tc_id,
                "max_preheat_ramp_c_per_s": ramp,
            }
            if ramp > limit:
                violating_runs.append(str(run_id))
        else:
            max_ramp_by_run[str(run_id)] = {
                "tc_id": None,
                "max_preheat_ramp_c_per_s": None,
            }

    return {
        "ramp_rate_limit_c_per_s": float(limit),
        "violating_runs": sorted(violating_runs),
        "max_ramp_by_run": dict(sorted(max_ramp_by_run.items())),
    }

def compute_q02(tc_long: pd.DataFrame, rules: dict) -> list[dict]:
    liquidus_c = rules["liquidus_temp_c"]
    tal_min_s = rules["tal_min_s"]
    tal_max_s = rules["tal_max_s"]
    if None in (liquidus_c, tal_min_s, tal_max_s):
        raise ValueError("Missing handbook rules for Q02")

    rows = []
    for run_id, run_df in tc_long.groupby("run_id", sort=True):
        per_tc = []
        for tc_id, tc_df in run_df.groupby("tc_id", sort=True):
            tal = time_above_threshold(tc_df[["time_s", "temp_c"]], liquidus_c)
            if tal is not None:
                per_tc.append((str(tc_id), float(tal)))

        if per_tc:
            tc_id, tal = min(per_tc, key=lambda x: (x[1], x[0]))  # conservative
            status = "compliant" if tal_min_s <= tal <= tal_max_s else "non-compliant"
            rows.append({
                "run_id": str(run_id),
                "tc_id": tc_id,
                "tal_s": tal,
                "required_min_tal_s": float(tal_min_s),
                "required_max_tal_s": float(tal_max_s),
                "status": status,
            })

    rows = sorted(rows, key=lambda r: r["run_id"])
    return rows

def compute_q03(tc_long: pd.DataFrame, rules: dict, all_run_ids: list[str]) -> dict:
    required_min_peak_c = rules["required_min_peak_c"]
    if required_min_peak_c is None:
        raise ValueError("Missing handbook rules for Q03")

    min_peak_by_run = {}
    for run_id in sorted(map(str, all_run_ids)):
        run_df = tc_long[tc_long["run_id"] == run_id]
        peaks = []
        for tc_id, tc_df in run_df.groupby("tc_id", sort=True):
            peak = pd.to_numeric(tc_df["temp_c"], errors="coerce").max()
            if pd.notna(peak):
                peaks.append((str(tc_id), float(peak)))
        if peaks:
            tc_id, peak = min(peaks, key=lambda x: (x[1], x[0]))
            min_peak_by_run[run_id] = {
                "tc_id": tc_id,
                "peak_temp_c": peak,
                "required_min_peak_c": float(required_min_peak_c),
            }
        else:
            min_peak_by_run[run_id] = {
                "tc_id": None,
                "peak_temp_c": None,
                "required_min_peak_c": float(required_min_peak_c),
            }

    failing_runs = sorted([
        run_id for run_id, rec in min_peak_by_run.items()
        if rec["peak_temp_c"] is None or rec["peak_temp_c"] < required_min_peak_c
    ])

    return {
        "failing_runs": failing_runs,
        "min_peak_by_run": dict(sorted(min_peak_by_run.items())),
    }

def compute_q04(mes_df: pd.DataFrame, rules: dict) -> list[dict]:
    # Adapt this function to actual handbook + MES schema.
    out = []

    # Flexible column mapping
    cols = {c.lower(): c for c in mes_df.columns}
    run_col = cols.get("run_id")
    model_col = cols.get("oven_model") or cols.get("model")
    actual_speed_col = cols.get("actual_speed_cm_min") or cols.get("conveyor_speed_cm_min") or cols.get("speed_cm_min")
    boards_per_min_col = cols.get("boards_per_min")
    board_length_col = cols.get("board_length_cm")
    loading_factor_col = cols.get("loading_factor")

    if not run_col or not actual_speed_col:
        # Cannot compute; return sorted null rows if run_id exists
        if run_col:
            for run_id in sorted(mes_df[run_col].astype(str).unique()):
                out.append({
                    "run_id": run_id,
                    "required_min_speed_cm_min": None,
                    "actual_speed_cm_min": None,
                    "meets": False,
                })
        return out

    for _, r in mes_df.sort_values(run_col, kind="stable").iterrows():
        run_id = str(r[run_col])
        actual_speed = pd.to_numeric(r[actual_speed_col], errors="coerce")
        required_min_speed = None
        meets = False

        if boards_per_min_col and board_length_col:
            bpm = pd.to_numeric(r[boards_per_min_col], errors="coerce")
            board_len = pd.to_numeric(r[board_length_col], errors="coerce")
            if loading_factor_col:
                loading = pd.to_numeric(r[loading_factor_col], errors="coerce")
            else:
                loading = np.nan

            if pd.notna(bpm) and pd.notna(board_len) and pd.notna(loading) and loading > 0:
                required_min_speed = float(bpm * board_len / loading)

        if pd.notna(actual_speed) and required_min_speed is not None:
            meets = bool(float(actual_speed) >= required_min_speed)

        out.append({
            "run_id": run_id,
            "required_min_speed_cm_min": required_min_speed,
            "actual_speed_cm_min": None if pd.isna(actual_speed) else float(actual_speed),
            "meets": meets,
        })

    return sorted(out, key=lambda r: r["run_id"])

def compute_q05(mes_df: pd.DataFrame, defects_df: pd.DataFrame, q01: dict, q02: list[dict], q03: dict, q04: list[dict]) -> list[dict]:
    if "run_id" not in mes_df.columns or "board_family" not in mes_df.columns:
        raise ValueError("MES data must contain run_id and board_family for Q05")

    score = mes_df.copy()
    score["run_id"] = score["run_id"].astype(str)
    score["board_family"] = score["board_family"].astype(str)

    # Compliance flags
    ramp_ok = {rid: (rid not in set(q01["violating_runs"])) for rid in score["run_id"]}
    tal_ok = {r["run_id"]: (r["status"] == "compliant") for r in q02}
    peak_ok = {rid: (rid not in set(q03["failing_runs"])) for rid in score["run_id"]}
    speed_ok = {r["run_id"]: bool(r["meets"]) for r in q04}

    score["q01_ok"] = score["run_id"].map(ramp_ok).fillna(False)
    score["q02_ok"] = score["run_id"].map(tal_ok).fillna(False)
    score["q03_ok"] = score["run_id"].map(peak_ok).fillna(False)
    score["q04_ok"] = score["run_id"].map(speed_ok).fillna(False)
    score["process_compliant"] = score[["q01_ok", "q02_ok", "q03_ok", "q04_ok"]].all(axis=1)

    # Defect aggregation
    d = defects_df.copy()
    if "run_id" in d.columns:
        d["run_id"] = d["run_id"].astype(str)

        if "defect_count" in d.columns:
            defect_agg = d.groupby("run_id", as_index=False)["defect_count"].sum().rename(columns={"defect_count": "total_defects"})
        else:
            defect_agg = d.groupby("run_id", as_index=False).size().rename(columns={"size": "total_defects"})

        score = score.merge(defect_agg, on="run_id", how="left")
    else:
        score["total_defects"] = np.nan

    score["total_defects"] = score["total_defects"].fillna(0)

    # Optional quality/efficiency metrics
    optional_defaults = {
        "fp_yield_pct": np.nan,
        "final_yield_pct": np.nan,
        "throughput_bph": np.nan,
        "actual_speed_cm_min": np.nan,
    }
    for c, default in optional_defaults.items():
        if c not in score.columns:
            score[c] = default

    result = []
    for fam, fam_df in score.groupby("board_family", sort=True):
        fam_df = fam_df.copy().sort_values(
            by=["process_compliant", "total_defects", "fp_yield_pct", "final_yield_pct", "throughput_bph", "run_id"],
            ascending=[False, True, False, False, False, True],
            kind="stable",
        )
        best = str(fam_df.iloc[0]["run_id"])
        runner_ups = [str(x) for x in fam_df.iloc[1:]["run_id"].tolist()]
        result.append({
            "board_family": str(fam),
            "best_run_id": best,
            "runner_up_run_ids": runner_ups,
        })

    return sorted(result, key=lambda r: r["board_family"])


# ----------------------------
# Main
# ----------------------------

def main():
    handbook_pdf = DATA_DIR / "handbook.pdf"
    handbook_text = extract_handbook_text(handbook_pdf)
    rules = extract_rules_from_handbook(handbook_text)

    # MANUAL REVIEW STEP:
    # Print extracted rules so you can compare them against handbook text.
    print("Extracted handbook rules:")
    print(json.dumps(recursively_round(rules), indent=2))

    thermocouples_df = load_csv("thermocouple")
    mes_df = load_csv("mes")
    defects_df = load_csv("defect")

    tc_long = normalize_thermocouples(thermocouples_df)

    if "run_id" in mes_df.columns:
        all_run_ids = sorted(mes_df["run_id"].astype(str).unique())
    else:
        all_run_ids = sorted(tc_long["run_id"].astype(str).unique())

    q01 = compute_q01(tc_long, rules)
    q02 = compute_q02(tc_long, rules)
    q03 = compute_q03(tc_long, rules, all_run_ids)
    q04 = compute_q04(mes_df, rules)
    q05 = compute_q05(mes_df, defects_df, q01, q02, q03, q04)

    write_json(OUT_DIR / "q01.json", q01)
    write_json(OUT_DIR / "q02.json", q02)
    write_json(OUT_DIR / "q03.json", q03)
    write_json(OUT_DIR / "q04.json", q04)
    write_json(OUT_DIR / "q05.json", q05)

    # Validation
    for path in [OUT_DIR / f"q0{i}.json" for i in range(1, 6)]:
        with open(path, "r", encoding="utf-8") as f:
            _ = json.load(f)
        print(f"Wrote {path}")

    # Sort checks
    with open(OUT_DIR / "q01.json", "r", encoding="utf-8") as f:
        obj = json.load(f)
        assert list(obj["max_ramp_by_run"].keys()) == sorted(obj["max_ramp_by_run"].keys())

    with open(OUT_DIR / "q03.json", "r", encoding="utf-8") as f:
        obj = json.load(f)
        assert list(obj["min_peak_by_run"].keys()) == sorted(obj["min_peak_by_run"].keys())

    with open(OUT_DIR / "q02.json", "r", encoding="utf-8") as f:
        arr = json.load(f)
        assert [r["run_id"] for r in arr] == sorted(r["run_id"] for r in arr)

    with open(OUT_DIR / "q04.json", "r", encoding="utf-8") as f:
        arr = json.load(f)
        assert [r["run_id"] for r in arr] == sorted(r["run_id"] for r in arr)

    with open(OUT_DIR / "q05.json", "r", encoding="utf-8") as f:
        arr = json.load(f)
        assert [r["board_family"] for r in arr] == sorted(r["board_family"] for r in arr)

    print("All outputs written and basic validation passed.")

if __name__ == "__main__":
    main()
```

---

## Fast Execution Path Summary

When time is limited, follow this order:

1. `pdftotext handbook.pdf -` and grep for **preheat / ramp / TAL / liquidus / peak / conveyor / speed**
2. inspect CSV columns
3. normalize thermocouples to long format
4. compute:
   - Q01: **max ramp** in handbook preheat region using segment-intersection logic
   - Q02: **min TAL** across TCs using crossing interpolation
   - Q03: **min peak** across TCs; missing TC data fails if required
   - Q04: handbook formula + MES geometry/model values
   - Q05: compliance first, then quality, then efficiency
5. validate sorting, rounding, null handling, and schema

If you remember only two things from this skill, remember these:

- **Preheat ramp must use the correct handbook-defined window and proper segment/intersection logic.**
- **Best-run selection should not ignore compliance.**