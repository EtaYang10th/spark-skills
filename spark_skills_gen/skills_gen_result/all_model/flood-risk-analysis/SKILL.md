---
name: flood-risk-analysis-usgs-stations-to-csv
description: Analyze USGS-style station time series for a specified date window, count flood days per station using dataset-provided flood thresholds, and write the exact CSV expected by validators.
allowed-tools: [python3, bash, pandas]
tags: [hydrology, usgs, flood, csv, pandas, time-series, validation]
---

# Flood Risk Analysis from USGS Station Records

Use this skill when you need to:

- read a station time-series file such as `michigan_stations.txt`
- identify which stations experienced flooding during a date range
- count the number of flood days per station
- output a CSV with exact required columns, typically:
  - `station_id`
  - `flood_days`

This skill is designed for **similar tasks**, not a single dataset instance. It focuses on choosing the right path early, validating assumptions before coding, and producing output in the exact format tests expect.

## When to Use This Skill

Apply this workflow when the task:

1. names a date window, such as âApril 1-7, 2025â
2. provides a local text/CSV/TSV file containing station records
3. asks for a per-station flood summary
4. expects a CSV written to a specific path
5. keeps only stations with at least one flood day

## Domain Notes

In these tasks, âflood dayâ usually means:

- a station exceeded a flood threshold on that calendar day, where the threshold comes from:
  - a flood-stage column,
  - a flood-flow/discharge column,
  - or task instructions.

Do **not** invent thresholds if they are not present. First inspect the dataset and any verifier-facing requirements.

USGS-related files often contain:

- station identifiers like `04124000`
- dates or datetimes
- streamflow/discharge columns (`flow`, `discharge`, `00060`, `mean_flow`, etc.)
- stage/gage-height columns (`stage`, `gage_height`, `00065`, etc.)
- threshold columns (`flood_stage`, `flood_flow`, `action_stage`, etc.)

## High-Level Workflow

1. **Read the task carefully and extract output requirements**
   - Identify the exact output path.
   - Identify the exact column names and whether headers are required.
   - Identify whether zero-flood stations should be excluded.
   - Why: many tasks fail due to schema mismatch, not analysis.

2. **Inspect the input file structure before writing logic**
   - Determine delimiter, column names, date column type, and whether records are daily or sub-daily.
   - Locate the station identifier column.
   - Locate the measurement column and flood-threshold column.
   - Why: flood logic depends entirely on how the file encodes measurements and thresholds.

3. **Determine the correct flood criterion from available columns**
   - Prefer explicit threshold columns in the data.
   - Match stage-to-stage and flow-to-flow; do not compare flow to flood stage or stage to flood flow.
   - Why: using mismatched units is a common silent error.

4. **Normalize dates and filter to the requested time window**
   - Convert timestamps to timezone-naive calendar dates unless the task explicitly requires time-of-day handling.
   - Include both start and end dates.
   - Why: validators usually count flood *days*, not flood records.

5. **Collapse to daily flood status per station**
   - If data is sub-daily, count a day once if any record that day is in flood.
   - If data is already daily, treat each row as one day after date normalization.
   - Why: otherwise you overcount flood hours/records as flood days.

6. **Aggregate to station-level flood-day counts**
   - Count flood dates per station.
   - Keep only stations with at least one flood day.
   - Why: this is the most common required result shape.

7. **Write the CSV exactly as required**
   - Use the exact header names.
   - Do not add index columns.
   - Keep only requested columns and only qualifying stations.
   - Why: output-format issues are easy to avoid and often fatal.

8. **Self-verify before finalizing**
   - Confirm file exists.
   - Confirm column order.
   - Confirm no duplicates per station.
   - Confirm all `flood_days > 0`.
   - Why: this catches most avoidable failures.

---

## Step 1: Inspect the Input File Safely

Use this first to understand the dataset rather than guessing.

```python
import os
import pandas as pd

INPUT_PATH = "/root/data/stations.txt"  # adapt
CANDIDATE_SEPARATORS = [",", "\t", "|", r"\s+"]

def try_read_preview(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input file not found: {path}")

    print(f"Inspecting: {path}")
    for sep in CANDIDATE_SEPARATORS:
        try:
            df = pd.read_csv(path, sep=sep, engine="python", nrows=5)
            print("\n---")
            print(f"sep={sep!r}")
            print("shape:", df.shape)
            print("columns:", list(df.columns))
            print(df.head())
        except Exception as exc:
            print(f"sep={sep!r} failed: {exc}")

try:
    try_read_preview(INPUT_PATH)
except Exception as e:
    print(f"Inspection failed: {e}")
```

### What to look for

- station column:
  - `station_id`, `site_no`, `site`, `usgs_site_code`, `agency_cd + site_no`
- date column:
  - `date`, `datetime`, `timestamp`, `observation_date`
- measurement columns:
  - flow/discharge: `flow`, `discharge`, `streamflow`, `00060`
  - stage: `stage`, `gage_height`, `gage_ht`, `00065`
- threshold columns:
  - `flood_stage`, `flood_flow`, `flood_discharge`, `minor_flood_stage`

---

## Step 2: Standardize Columns and Detect the Right Fields

This helper finds likely columns without hardcoding one dataset.

```python
import pandas as pd

def find_first_matching_column(columns, candidates):
    lowered = {str(col).strip().lower(): col for col in columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None

def detect_columns(df: pd.DataFrame) -> dict:
    columns = list(df.columns)

    station_col = find_first_matching_column(columns, [
        "station_id", "site_no", "site", "usgs_site_code", "station", "site_id"
    ])

    date_col = find_first_matching_column(columns, [
        "date", "datetime", "timestamp", "observation_date", "time"
    ])

    flow_col = find_first_matching_column(columns, [
        "flow", "discharge", "streamflow", "mean_flow", "00060"
    ])

    stage_col = find_first_matching_column(columns, [
        "stage", "gage_height", "gage_ht", "water_level", "00065"
    ])

    flood_flow_col = find_first_matching_column(columns, [
        "flood_flow", "flood_discharge", "flood_streamflow"
    ])

    flood_stage_col = find_first_matching_column(columns, [
        "flood_stage", "minor_flood_stage", "stage_flood"
    ])

    result = {
        "station_col": station_col,
        "date_col": date_col,
        "flow_col": flow_col,
        "stage_col": stage_col,
        "flood_flow_col": flood_flow_col,
        "flood_stage_col": flood_stage_col,
    }

    if not station_col:
        raise ValueError(f"Could not identify station column from: {columns}")
    if not date_col:
        raise ValueError(f"Could not identify date column from: {columns}")

    if flow_col and flood_flow_col:
        result["measurement_col"] = flow_col
        result["threshold_col"] = flood_flow_col
        result["comparison_type"] = "flow"
    elif stage_col and flood_stage_col:
        result["measurement_col"] = stage_col
        result["threshold_col"] = flood_stage_col
        result["comparison_type"] = "stage"
    else:
        raise ValueError(
            "Could not find a compatible measurement/threshold pair. "
            "Need either flow+flood_flow or stage+flood_stage columns."
        )

    return result
```

### Why this matters

The most important decision is selecting a **compatible pair**:

- `flow` vs `flood_flow`
- `stage` vs `flood_stage`

Never cross them.

---

## Step 3: Load the File Robustly

Once you know the delimiter and columns, load the full file defensively.

```python
import os
import pandas as pd

def load_station_data(path: str, sep: str = ",") -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing input file: {path}")

    try:
        df = pd.read_csv(path, sep=sep, engine="python")
    except Exception as exc:
        raise RuntimeError(f"Failed to read {path} with sep={sep!r}: {exc}") from exc

    if df.empty:
        raise ValueError("Input file loaded but contains no rows")

    # Normalize column names for easier debugging while keeping originals accessible
    df.columns = [str(c).strip() for c in df.columns]
    return df

# Example:
# df = load_station_data("/root/data/michigan_stations.txt", sep=",")
# print(df.shape)
# print(df.columns.tolist())
```

If the delimiter is uncertain, combine inspection and loading:

```python
import pandas as pd

def auto_load_station_data(path: str) -> tuple[pd.DataFrame, str]:
    separators = [",", "\t", "|", r"\s+"]
    best_df = None
    best_sep = None

    for sep in separators:
        try:
            df = pd.read_csv(path, sep=sep, engine="python")
            if df.shape[1] >= 3:
                best_df = df
                best_sep = sep
                break
        except Exception:
            continue

    if best_df is None:
        raise RuntimeError("Could not parse input file with common delimiters")

    best_df.columns = [str(c).strip() for c in best_df.columns]
    return best_df, best_sep
```

---

## Step 4: Normalize Dates and Restrict to the Requested Window

Flood tasks usually require inclusive date filtering.

```python
import pandas as pd

def normalize_and_filter_dates(
    df: pd.DataFrame,
    date_col: str,
    start_date: str,
    end_date: str
) -> pd.DataFrame:
    out = df.copy()

    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out = out.dropna(subset=[date_col])

    if out.empty:
        raise ValueError("No valid dates found after parsing")

    # Convert to calendar day to avoid counting multiple timestamps as multiple days later
    out["__date_only"] = out[date_col].dt.floor("D")

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)

    mask = (out["__date_only"] >= start_ts) & (out["__date_only"] <= end_ts)
    out = out.loc[mask].copy()

    return out

# Example:
# filtered = normalize_and_filter_dates(df, "date", "2025-04-01", "2025-04-07")
```

### Inclusive window rule

If the task says âApril 1-7â, include:

- `2025-04-01`
- `2025-04-02`
- ...
- `2025-04-07`

---

## Step 5: Compute Daily Flood Status

This handles both daily and sub-daily records correctly.

```python
import pandas as pd

def compute_daily_flood_flags(
    df: pd.DataFrame,
    station_col: str,
    measurement_col: str,
    threshold_col: str
) -> pd.DataFrame:
    required = [station_col, "__date_only", measurement_col, threshold_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns for flood logic: {missing}")

    out = df.copy()

    out[measurement_col] = pd.to_numeric(out[measurement_col], errors="coerce")
    out[threshold_col] = pd.to_numeric(out[threshold_col], errors="coerce")
    out = out.dropna(subset=[measurement_col, threshold_col, station_col, "__date_only"])

    if out.empty:
        return pd.DataFrame(columns=[station_col, "__date_only", "is_flood_day"])

    out["is_flood_record"] = out[measurement_col] >= out[threshold_col]

    # Count each station-date once: any flooding during the day => flood day
    daily = (
        out.groupby([station_col, "__date_only"], as_index=False)["is_flood_record"]
        .max()
        .rename(columns={"is_flood_record": "is_flood_day"})
    )

    daily["is_flood_day"] = daily["is_flood_day"].astype(bool)
    return daily
```

### Why `max()`?

For a given station and calendar day:

- if any record exceeds threshold, that day counts as 1 flood day
- multiple flood records on the same day still count as 1 day

---

## Step 6: Aggregate Per Station and Keep Only Positive Counts

This produces the standard validator-friendly result.

```python
import pandas as pd

def summarize_flood_days(
    daily_df: pd.DataFrame,
    station_col: str,
    output_station_col: str = "station_id"
) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=[output_station_col, "flood_days"])

    summary = (
        daily_df.loc[daily_df["is_flood_day"]]
        .groupby(station_col, as_index=False)
        .size()
        .rename(columns={station_col: output_station_col, "size": "flood_days"})
    )

    summary = summary.loc[summary["flood_days"] > 0].copy()

    # Preserve station IDs as strings so leading zeros are not lost
    summary[output_station_col] = summary[output_station_col].astype(str)

    # Deterministic ordering helps reproducibility; adapt if verifier requires another order
    summary = summary.sort_values(
        by=["flood_days", output_station_col],
        ascending=[False, True]
    ).reset_index(drop=True)

    return summary
```

### Important output conventions

- output station IDs as strings
- do not include stations with `0` flood days
- output exactly:
  - `station_id`
  - `flood_days`

unless the task explicitly says otherwise

---

## Step 7: Write the Output CSV Exactly

```python
import os
import pandas as pd

def write_output_csv(df: pd.DataFrame, output_path: str) -> None:
    expected_cols = ["station_id", "flood_days"]
    if list(df.columns) != expected_cols:
        raise ValueError(f"Output columns must be exactly {expected_cols}, got {list(df.columns)}")

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    df.to_csv(output_path, index=False)

    if not os.path.exists(output_path):
        raise RuntimeError(f"Failed to create output file: {output_path}")

# Example:
# write_output_csv(summary, "/root/output/flood_results.csv")
```

---

## Step 8: Verify Before Finalizing

Use a quick validator pass before declaring success.

```python
import os
import pandas as pd

def validate_output_csv(output_path: str) -> None:
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"Output file not found: {output_path}")

    out = pd.read_csv(output_path, dtype={"station_id": str})

    expected_cols = ["station_id", "flood_days"]
    if list(out.columns) != expected_cols:
        raise ValueError(f"Expected columns {expected_cols}, got {list(out.columns)}")

    if out["station_id"].isna().any():
        raise ValueError("station_id contains missing values")

    if out["flood_days"].isna().any():
        raise ValueError("flood_days contains missing values")

    if (out["flood_days"] <= 0).any():
        raise ValueError("Output contains stations with non-positive flood_days")

    if out["station_id"].duplicated().any():
        dupes = out.loc[out["station_id"].duplicated(), "station_id"].tolist()
        raise ValueError(f"Duplicate station_id values found: {dupes}")

    print("Output validation passed.")
    print(out.head())

# Example:
# validate_output_csv("/root/output/flood_results.csv")
```

---

## Reference Implementation

This is the full end-to-end script. Copy, adapt the paths/date range if needed, and run it directly.

```python
#!/usr/bin/env python3
import os
import sys
import pandas as pd


INPUT_PATH = "/root/data/michigan_stations.txt"   # adapt if needed
OUTPUT_PATH = "/root/output/flood_results.csv"
START_DATE = "2025-04-01"
END_DATE = "2025-04-07"


def auto_load_station_data(path: str) -> tuple[pd.DataFrame, str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input file not found: {path}")

    separators = [",", "\t", "|", r"\s+"]
    parse_attempts = []

    for sep in separators:
        try:
            df = pd.read_csv(path, sep=sep, engine="python")
            df.columns = [str(c).strip() for c in df.columns]
            parse_attempts.append((sep, df.shape, list(df.columns)))
            if df.shape[1] >= 3 and len(df.columns) >= 3:
                return df, sep
        except Exception as exc:
            parse_attempts.append((sep, "FAILED", str(exc)))

    message = ["Could not parse input file with common delimiters. Attempts:"]
    for item in parse_attempts:
        message.append(str(item))
    raise RuntimeError("\n".join(message))


def find_first_matching_column(columns, candidates):
    lowered = {str(col).strip().lower(): col for col in columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def detect_columns(df: pd.DataFrame) -> dict:
    columns = list(df.columns)

    station_col = find_first_matching_column(columns, [
        "station_id", "site_no", "site", "usgs_site_code", "station", "site_id"
    ])

    date_col = find_first_matching_column(columns, [
        "date", "datetime", "timestamp", "observation_date", "time"
    ])

    flow_col = find_first_matching_column(columns, [
        "flow", "discharge", "streamflow", "mean_flow", "00060"
    ])

    stage_col = find_first_matching_column(columns, [
        "stage", "gage_height", "gage_ht", "water_level", "00065"
    ])

    flood_flow_col = find_first_matching_column(columns, [
        "flood_flow", "flood_discharge", "flood_streamflow"
    ])

    flood_stage_col = find_first_matching_column(columns, [
        "flood_stage", "minor_flood_stage", "stage_flood"
    ])

    if not station_col:
        raise ValueError(f"Could not identify station column from columns: {columns}")
    if not date_col:
        raise ValueError(f"Could not identify date column from columns: {columns}")

    if flow_col and flood_flow_col:
        measurement_col = flow_col
        threshold_col = flood_flow_col
        comparison_type = "flow"
    elif stage_col and flood_stage_col:
        measurement_col = stage_col
        threshold_col = flood_stage_col
        comparison_type = "stage"
    else:
        raise ValueError(
            "Could not find compatible measurement and threshold columns. "
            "Need flow+flood_flow or stage+flood_stage style fields."
        )

    return {
        "station_col": station_col,
        "date_col": date_col,
        "measurement_col": measurement_col,
        "threshold_col": threshold_col,
        "comparison_type": comparison_type,
    }


def normalize_and_filter_dates(
    df: pd.DataFrame,
    date_col: str,
    start_date: str,
    end_date: str
) -> pd.DataFrame:
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out = out.dropna(subset=[date_col])

    if out.empty:
        raise ValueError("No valid date rows remain after parsing")

    out["__date_only"] = out[date_col].dt.floor("D")

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)

    if end_ts < start_ts:
        raise ValueError(f"End date {end_date} is before start date {start_date}")

    out = out.loc[
        (out["__date_only"] >= start_ts) &
        (out["__date_only"] <= end_ts)
    ].copy()

    return out


def compute_daily_flood_flags(
    df: pd.DataFrame,
    station_col: str,
    measurement_col: str,
    threshold_col: str
) -> pd.DataFrame:
    required = [station_col, "__date_only", measurement_col, threshold_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    out = df.copy()

    # Preserve station IDs as strings
    out[station_col] = out[station_col].astype(str).str.strip()

    out[measurement_col] = pd.to_numeric(out[measurement_col], errors="coerce")
    out[threshold_col] = pd.to_numeric(out[threshold_col], errors="coerce")

    out = out.dropna(subset=[station_col, "__date_only", measurement_col, threshold_col])

    if out.empty:
        return pd.DataFrame(columns=[station_col, "__date_only", "is_flood_day"])

    out["is_flood_record"] = out[measurement_col] >= out[threshold_col]

    daily = (
        out.groupby([station_col, "__date_only"], as_index=False)["is_flood_record"]
        .max()
        .rename(columns={"is_flood_record": "is_flood_day"})
    )

    daily["is_flood_day"] = daily["is_flood_day"].astype(bool)
    return daily


def summarize_flood_days(daily_df: pd.DataFrame, station_col: str) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=["station_id", "flood_days"])

    summary = (
        daily_df.loc[daily_df["is_flood_day"]]
        .groupby(station_col, as_index=False)
        .size()
        .rename(columns={station_col: "station_id", "size": "flood_days"})
    )

    summary["station_id"] = summary["station_id"].astype(str)
    summary["flood_days"] = pd.to_numeric(summary["flood_days"], errors="coerce").fillna(0).astype(int)

    summary = summary.loc[summary["flood_days"] > 0].copy()

    summary = summary.sort_values(
        by=["flood_days", "station_id"],
        ascending=[False, True]
    ).reset_index(drop=True)

    return summary[["station_id", "flood_days"]]


def write_output_csv(df: pd.DataFrame, output_path: str) -> None:
    expected_cols = ["station_id", "flood_days"]
    if list(df.columns) != expected_cols:
        raise ValueError(f"Output columns must be exactly {expected_cols}, got {list(df.columns)}")

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    df.to_csv(output_path, index=False)

    if not os.path.exists(output_path):
        raise RuntimeError(f"Output file was not created: {output_path}")


def validate_output_csv(output_path: str) -> None:
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"Missing output file: {output_path}")

    out = pd.read_csv(output_path, dtype={"station_id": str})

    expected_cols = ["station_id", "flood_days"]
    if list(out.columns) != expected_cols:
        raise ValueError(f"Expected output columns {expected_cols}, got {list(out.columns)}")

    if out["station_id"].isna().any():
        raise ValueError("station_id contains null values")

    if out["flood_days"].isna().any():
        raise ValueError("flood_days contains null values")

    if (out["flood_days"] <= 0).any():
        raise ValueError("Found stations with flood_days <= 0")

    if out["station_id"].duplicated().any():
        duplicates = out.loc[out["station_id"].duplicated(), "station_id"].tolist()
        raise ValueError(f"Duplicate station_id values found: {duplicates}")


def main():
    print(f"Loading input: {INPUT_PATH}")
    df, sep = auto_load_station_data(INPUT_PATH)
    print(f"Parsed with separator: {sep!r}")
    print(f"Input shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")

    cols = detect_columns(df)
    print("Detected columns:", cols)

    filtered = normalize_and_filter_dates(
        df=df,
        date_col=cols["date_col"],
        start_date=START_DATE,
        end_date=END_DATE
    )
    print(f"Rows in requested date range [{START_DATE}, {END_DATE}]: {len(filtered)}")

    daily = compute_daily_flood_flags(
        df=filtered,
        station_col=cols["station_col"],
        measurement_col=cols["measurement_col"],
        threshold_col=cols["threshold_col"]
    )
    print(f"Daily station-date records: {len(daily)}")

    summary = summarize_flood_days(daily, cols["station_col"])
    print(f"Stations with at least one flood day: {len(summary)}")
    print(summary.head(10))

    write_output_csv(summary, OUTPUT_PATH)
    validate_output_csv(OUTPUT_PATH)

    print(f"Success: wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
```

---

## Fast CLI Pattern

If you want a one-shot run from the shell:

```bash
python3 /root/script.py
```

Or inline:

```bash
python3 - <<'PY'
import pandas as pd
# paste the reference implementation here, then run main()
PY
```

---

## Output Format Requirements

Unless the task explicitly says otherwise, use:

```csv
station_id,flood_days
04123456,3
04199999,1
```

Rules:

- header row required
- exactly two columns
- exact column order:
  1. `station_id`
  2. `flood_days`
- no extra index column
- station IDs should remain strings
- include only stations with `flood_days >= 1`

---

## Choosing the Right Execution Path Early

In this task family, the most valuable early decision is:

### 1. Check whether the verifier is schema-sensitive
If tests likely check exact columns and file existence, prioritize:
- exact path
- exact headers
- exact filtering rules

### 2. Inspect the data before building logic
Do not assume:
- delimiter
- flood metric type
- whether records are daily
- whether thresholds are constant or per-row

### 3. Use explicit thresholds from the dataset
Best sources:
- `flood_stage`
- `flood_flow`
- equivalent threshold fields

Do not derive arbitrary thresholds from quantiles or averages unless the task explicitly instructs you to do so.

### 4. Count days, not raw exceedance records
If records are hourly, a station flooding for 8 hourly points on one date still contributes **1 flood day**, not 8.

---

## Common Pitfalls

Even though no failed attempts were recorded in the evidence, these are the most likely failure modes for this task class and should be actively checked.

1. **Writing the wrong CSV schema**
   - Wrong header names
   - Wrong column order
   - Extra columns
   - Saving the pandas index
   - Fix: explicitly enforce `["station_id", "flood_days"]`

2. **Including stations with zero flood days**
   - The task often says to keep only positive cases
   - Fix: filter `flood_days > 0` before writing

3. **Dropping leading zeros from USGS station IDs**
   - Reading station IDs as integers corrupts identifiers
   - Fix: cast/output station IDs as strings

4. **Comparing incompatible units**
   - Example: comparing discharge against flood stage
   - Fix: use flow with flood-flow, or stage with flood-stage

5. **Counting records instead of calendar days**
   - Sub-daily data can greatly inflate counts
   - Fix: normalize timestamps to dates and use max-per-day

6. **Using exclusive end-date filtering**
   - âApril 1-7â is typically inclusive
   - Fix: use `>= start_date` and `<= end_date`

7. **Failing to parse dates robustly**
   - Mixed datetime formats can produce `NaT`
   - Fix: `pd.to_datetime(..., errors="coerce")` and drop invalid rows

8. **Guessing the delimiter**
   - `.txt` does not imply one format
   - Fix: preview the file or auto-try common separators

9. **Assuming one row equals one day**
   - Some datasets are hourly or irregular
   - Fix: always create a normalized date column before counting

10. **Finalizing without reopening the CSV**
    - A file can exist but still be malformed
    - Fix: reload and validate the output after writing

---

## Minimal Success Checklist

Before you finish, confirm all of these:

- [ ] Output file exists at the exact required path
- [ ] CSV header is exactly `station_id,flood_days`
- [ ] No extra columns or index
- [ ] All station IDs are strings
- [ ] All `flood_days` are positive integers
- [ ] No duplicate `station_id`
- [ ] Date range was filtered inclusively
- [ ] Flood days were counted once per station per calendar day
- [ ] Threshold comparison used compatible units

This checklist catches the majority of avoidable failures in flood-risk-analysis tasks.