---
name: econ-detrending-correlation
description: Compute business-cycle comovement between macroeconomic aggregates by extracting ERP annual/quarterly series from Excel, deflating with CPI, HP-filtering logged real values, and calculating Pearson correlation over a target year range.
category: economics
tags:
  - macroeconomics
  - business-cycles
  - detrending
  - hp-filter
  - correlation
  - excel
  - pandas
  - statsmodels
tools:
  - python
  - pandas
  - numpy
  - openpyxl
  - xlrd
  - scipy
  - statsmodels
---

# econ-detrending-correlation

Use this skill when a task asks you to measure comovement between macroeconomic time series after removing long-run trend, especially when the source data comes from ERP tables or similarly structured government spreadsheets with annual rows plus quarterly rows at the bottom.

The typical workflow is:

1. Inspect workbook structure before coding assumptions
2. Extract the correct nominal annual series
3. Handle partial-current-year observations carefully
4. Build a matching annual price index
5. Convert nominal to real
6. Log-transform and detrend using the required filter
7. Compute correlation over the exact year span
8. Write the answer in the exact format expected by the validator

This skill is designed for annual macro data like PCE, GDP components, investment, etc.

---

## When to Use This Skill

Use this workflow if the task includes most of these features:

- Data is in ERP `.xls` or `.xlsx` tables
- One or more nominal macroeconomic series must be converted to real values
- CPI or another price index is provided separately
- The task explicitly requests detrending
- The detrending method is Hodrick-Prescott with annual smoothing parameter `lambda = 100`
- The final output is a scalar statistic such as correlation, volatility ratio, or relative standard deviation

---

## High-Level Workflow

### 1) Inspect each Excel file and confirm sheet names, row labels, and data placement
**Why:** ERP tables often contain title rows, metadata, annual sections, quarterly sections, and footnotes. You should not assume the data starts at row 0 or that headers are clean.

**Decision criteria:**
- If a workbook has named sheets like `B10`, `B12`, use those directly.
- If annual years appear as integers in column 0 and values in column 1, extract those rows.
- If the most recent year has quarterly-only values, compute an annual average from available quarters exactly as instructed.

```python
import pandas as pd
from pathlib import Path

def inspect_workbook(path: str, max_rows: int = 25) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")

    xls = pd.ExcelFile(path)
    print(f"Workbook: {path}")
    print("Sheets:", xls.sheet_names)

    for sheet in xls.sheet_names[:5]:
        print(f"\n--- Sheet: {sheet} ---")
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        print(df.head(max_rows).to_string(index=False))

# Example:
# inspect_workbook("/root/ERP-2025-table10.xls")
# inspect_workbook("/root/ERP-2025-table12.xls")
# inspect_workbook("/root/CPI.xlsx")
```

**What to verify before proceeding:**
- Correct sheet names
- The series of interest is actually in column 1, not another column
- Annual rows are marked by year values
- Quarterly rows for the final year are identifiable with labels such as `II.`, `III.`, `IV.`, `II p.`, etc.

---

### 2) Extract the annual nominal series from ERP tables using year rows
**Why:** ERP tables often list annual values in a compact annual section, followed by quarterly breakdowns at the bottom. The cleanest extraction is usually:
- take rows where column 0 is a year
- use column 1 if the task requests âTotal, column 1â

**Decision criteria:**
- Prefer numeric-year parsing over row-number assumptions
- Keep only years in the requested range
- Sort and cast index to integer

```python
import pandas as pd
import numpy as np

def extract_annual_series_from_erp(path: str, sheet_name: str, value_col: int = 1) -> pd.Series:
    """
    Extract annual values from an ERP sheet where:
    - year labels are in column 0
    - target values are in `value_col`
    """
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)

    years = pd.to_numeric(df.iloc[:, 0], errors="coerce")
    values = pd.to_numeric(df.iloc[:, value_col], errors="coerce")

    mask = years.notna() & values.notna()
    annual = pd.Series(values[mask].values, index=years[mask].astype(int).values)
    annual = annual[~annual.index.duplicated(keep="first")].sort_index()

    if annual.empty:
        raise ValueError(f"No annual data extracted from {path} sheet {sheet_name}")

    return annual

# Example:
# pce_nom = extract_annual_series_from_erp("/root/ERP-2025-table10.xls", "B10", value_col=1)
# pfi_nom = extract_annual_series_from_erp("/root/ERP-2025-table12.xls", "B12", value_col=1)
```

**Verification checks:**
- First few years look plausible
- Last complete annual year exists
- No accidental extraction of quarter labels as years
- Values are positive

---

### 3) For an incomplete final year, compute the annual value as the average of available quarters
**Why:** ERP current-year data may appear as quarterly entries instead of a finalized annual row. The task may explicitly require averaging available quarters.

**Decision criteria:**
- Only do this if the task explicitly instructs partial-year averaging
- Start collecting at the row labeled like `2024:` or similar
- Include quarter rows beneath it until a new section or footnotes begin
- Average only available quarter observations

```python
import pandas as pd
import numpy as np

def extract_partial_year_average(
    path: str,
    sheet_name: str,
    target_year: int,
    value_col: int = 1
) -> float:
    """
    Extract partial-year quarterly values from an ERP sheet and return their average.

    Assumes:
    - column 0 contains labels
    - a row starting with 'YYYY:' begins the current-year quarterly section
    - the first row under that label may contain Q1 / first available quarter value
    - subsequent quarter labels may look like 'II.', 'III.', 'IV.', 'II p.', etc.
    """
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)

    start_label = f"{target_year}:"
    quarter_labels = {"II.", "III.", "IV.", "II p.", "III p.", "IV p."}

    in_section = False
    vals = []

    for _, row in df.iterrows():
        raw_label = row.iloc[0]
        label = "" if pd.isna(raw_label) else str(raw_label).strip()

        raw_value = row.iloc[value_col]
        value = pd.to_numeric(pd.Series([raw_value]), errors="coerce").iloc[0]

        if label.startswith(start_label):
            in_section = True
            if pd.notna(value):
                vals.append(float(value))
            continue

        if in_section:
            if label in quarter_labels:
                if pd.isna(value):
                    raise ValueError(f"Quarter label {label} found but value missing in {path}")
                vals.append(float(value))
                continue

            # Stop when footnotes, source lines, or a new section begins
            if label.startswith("Source:") or label.startswith("1 ") or label.startswith("2 "):
                break

    if not vals:
        raise ValueError(f"No partial-year quarterly values found for {target_year} in {path}")

    return float(np.mean(vals))

# Example:
# pce_2024 = extract_partial_year_average("/root/ERP-2025-table10.xls", "B10", 2024, value_col=1)
# pfi_2024 = extract_partial_year_average("/root/ERP-2025-table12.xls", "B12", 2024, value_col=1)
```

**Important note:**  
Do not average annual values with quarterly values. This function is only for the current-year quarterly section.

---

### 4) Build an annual CPI series from the CPI workbook
**Why:** Deflation requires a price index aligned to the same annual frequency as the nominal series.

**Decision criteria:**
- If annual CPI values already exist, use them directly
- If CPI is monthly, compute annual average by calendar year
- If only partial current-year monthly observations are available, average available months if the task permits
- Ensure the CPI index uses the same year labels as the nominal series

```python
import pandas as pd
import numpy as np
from pathlib import Path

def load_cpi_annual(path: str) -> pd.Series:
    """
    Generic CPI loader that tries common layouts:
    1) a sheet with 'Year' and annual CPI columns
    2) a monthly date column + CPI value column
    Returns annual average CPI indexed by integer year.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f"CPI workbook not found: {path}")

    xls = pd.ExcelFile(path)

    for sheet in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)

        # Case 1: explicit annual columns
        lowered = {str(c).strip().lower(): c for c in df.columns}
        year_col = next((lowered[c] for c in lowered if "year" == c or c.endswith("year")), None)
        value_col = next((lowered[c] for c in lowered if "cpi" in c or "value" in c or "index" in c), None)

        if year_col is not None and value_col is not None:
            years = pd.to_numeric(df[year_col], errors="coerce")
            vals = pd.to_numeric(df[value_col], errors="coerce")
            mask = years.notna() & vals.notna()
            if mask.any():
                out = pd.Series(vals[mask].values, index=years[mask].astype(int).values).sort_index()
                if len(out) >= 10:
                    return out

        # Case 2: monthly date column + value column
        for date_candidate in df.columns:
            parsed_dates = pd.to_datetime(df[date_candidate], errors="coerce")
            if parsed_dates.notna().sum() >= max(5, len(df) // 2):
                numeric_cols = []
                for col in df.columns:
                    if col == date_candidate:
                        continue
                    vals = pd.to_numeric(df[col], errors="coerce")
                    if vals.notna().sum() >= max(5, len(df) // 2):
                        numeric_cols.append(col)

                if numeric_cols:
                    value_candidate = numeric_cols[0]
                    temp = pd.DataFrame({
                        "date": parsed_dates,
                        "value": pd.to_numeric(df[value_candidate], errors="coerce"),
                    }).dropna()
                    if not temp.empty:
                        annual = temp.groupby(temp["date"].dt.year)["value"].mean().sort_index()
                        if len(annual) >= 10:
                            return annual

    raise ValueError("Could not identify annual or monthly CPI structure in workbook")

# Example:
# cpi = load_cpi_annual("/root/CPI.xlsx")
```

**Verification checks:**
- CPI values should be strictly positive
- CPI should span the full target period
- Annual index should be integer years
- If monthly-derived, confirm calendar-year averaging

---

### 5) Align all series to the exact year range and convert nominal values to real values
**Why:** Correlation must be computed on matched observations only. Misaligned years will silently corrupt results.

**Decision criteria:**
- Use the exact requested year range, inclusive
- Intersect with years available in all series
- Fail loudly if required years are missing
- Use the same deflator for both nominal series unless the task specifies chain-type quantity indexes or series-specific deflators

```python
import pandas as pd
import numpy as np

def align_and_deflate(
    nominal_a: pd.Series,
    nominal_b: pd.Series,
    cpi: pd.Series,
    start_year: int,
    end_year: int
) -> pd.DataFrame:
    years = list(range(start_year, end_year + 1))

    missing_a = [y for y in years if y not in nominal_a.index]
    missing_b = [y for y in years if y not in nominal_b.index]
    missing_cpi = [y for y in years if y not in cpi.index]

    if missing_a:
        raise ValueError(f"Series A missing years: {missing_a}")
    if missing_b:
        raise ValueError(f"Series B missing years: {missing_b}")
    if missing_cpi:
        raise ValueError(f"CPI missing years: {missing_cpi}")

    df = pd.DataFrame({
        "a_nom": nominal_a.loc[years].astype(float),
        "b_nom": nominal_b.loc[years].astype(float),
        "cpi": cpi.loc[years].astype(float),
    })

    if (df <= 0).any().any():
        bad_cols = [col for col in df.columns if (df[col] <= 0).any()]
        raise ValueError(f"Non-positive values found in columns: {bad_cols}")

    df["a_real"] = df["a_nom"] / df["cpi"]
    df["b_real"] = df["b_nom"] / df["cpi"]

    return df

# Example:
# df = align_and_deflate(pce_nom, pfi_nom, cpi, 1973, 2024)
```

**Note on units:**  
For correlation of HP-filtered log real series, the CPI base year does not matter as long as the same strictly positive price index is used consistently across years.

---

### 6) Log-transform real values and apply the HP filter with annual lambda = 100
**Why:** The standard business-cycle practice here is to HP-filter the natural log of real series, not the levels.

**Decision criteria:**
- Use `np.log(real_series)`
- Use `lambda=100` for annual data
- Prefer `statsmodels.tsa.filters.hp_filter.hpfilter`
- If unavailable, solve the HP trend system manually

```python
import numpy as np

try:
    from statsmodels.tsa.filters.hp_filter import hpfilter
    HAS_STATSMODELS_HP = True
except Exception:
    HAS_STATSMODELS_HP = False

def hp_cycle(series: pd.Series, lamb: float = 100.0) -> pd.Series:
    values = np.asarray(series, dtype=float)

    if np.isnan(values).any():
        raise ValueError("Series contains NaNs; clean and align before HP filtering")
    if len(values) < 5:
        raise ValueError("Series too short for stable HP filtering")

    if HAS_STATSMODELS_HP:
        cycle, trend = hpfilter(values, lamb=lamb)
        return pd.Series(cycle, index=series.index)

    # Manual fallback
    n = len(values)
    I = np.eye(n)
    D = np.zeros((n - 2, n))
    for i in range(n - 2):
        D[i, i] = 1.0
        D[i, i + 1] = -2.0
        D[i, i + 2] = 1.0

    trend = np.linalg.solve(I + lamb * (D.T @ D), values)
    cycle = values - trend
    return pd.Series(cycle, index=series.index)

def detrend_logged_real_series(real_series: pd.Series, lamb: float = 100.0) -> pd.Series:
    if (real_series <= 0).any():
        raise ValueError("Real series must be strictly positive before log transform")

    logged = np.log(real_series.astype(float))
    cycle = hp_cycle(logged, lamb=lamb)
    return cycle

# Example:
# cyc_pce = detrend_logged_real_series(df["a_real"], lamb=100)
# cyc_pfi = detrend_logged_real_series(df["b_real"], lamb=100)
```

**Critical note:**  
Do **not** HP-filter nominal series if the task requests real business-cycle comovement. Deflate first, then log, then HP-filter.

---

### 7) Compute Pearson correlation on the cyclical components
**Why:** The desired statistic is typically the contemporaneous correlation between detrended cycles.

**Decision criteria:**
- Use the full aligned sample unless the task specifies leads/lags
- Use Pearson correlation
- Handle accidental NaNs defensively

```python
import numpy as np
import pandas as pd

def pearson_corr(series_a: pd.Series, series_b: pd.Series) -> float:
    df = pd.DataFrame({"a": series_a, "b": series_b}).dropna()
    if len(df) < 3:
        raise ValueError("Not enough overlapping observations to compute correlation")

    corr = float(np.corrcoef(df["a"], df["b"])[0, 1])

    if not np.isfinite(corr):
        raise ValueError(f"Correlation is not finite: {corr}")

    return corr

# Example:
# corr = pearson_corr(cyc_pce, cyc_pfi)
# print(format(corr, ".5f"))
```

---

### 8) Write output exactly as required
**Why:** Many validators are strict about formatting.

**Decision criteria:**
- Output only the number
- Round to the required decimal places
- Include trailing newline unless prohibited

```python
from pathlib import Path

def write_scalar_answer(path: str, value: float, decimals: int = 5) -> None:
    text = format(value, f".{decimals}f") + "\n"
    Path(path).write_text(text, encoding="utf-8")

# Example:
# write_scalar_answer("/root/answer.txt", corr, decimals=5)
```

---

## Domain Conventions and Notes

### Business-cycle detrending convention
For annual macroeconomic series:
- deflate nominal series into real terms
- take natural logs
- apply HP filter with `lambda = 100`
- use the cyclical component for correlation

### ERP table structure conventions
ERP workbooks often have:
- annual historical rows with year in column 0
- one or more data columns, where âcolumn 1â means spreadsheet column B / zero-based pandas column index `1`
- quarterly/current-year rows near the bottom
- footnotes or source rows after the data

### 2024 or current-year handling
If the task says only partial quarters are available:
- average the available quarterly values to form an annual approximation
- do not extrapolate missing quarters unless explicitly instructed
- do not drop the year if the task explicitly includes it

### CPI handling
If all series are annual:
- annual average CPI is usually appropriate
- dividing by CPI is sufficient for correlation because scaling constants cancel after logging and detrending

---

## Common Pitfalls

1. **Using the wrong ERP sheet**
   - ERP workbooks may contain multiple tables or sheets. Confirm sheet names like `B10`, `B12`, etc.

2. **Assuming the final year already has an annual value**
   - Often the last year appears as quarterly entries only. If instructed, average available quarters.

3. **Using the wrong column**
   - âTotal, column 1â means the first data column, typically pandas column index `1`, not necessarily the first non-empty column after parsing headers.

4. **Deflating after detrending**
   - Wrong order. Correct order is:
     `nominal -> real -> log -> HP filter -> correlation`

5. **HP-filtering levels instead of logs**
   - The task may explicitly require natural logs before filtering. Follow it exactly.

6. **Using the wrong HP lambda**
   - Annual data convention here is `lambda = 100`, not `1600` which is for quarterly data.

7. **Mismatched year alignment**
   - Always align both nominal series and CPI to the exact inclusive year range before computing real values.

8. **Silently accepting missing years**
   - Missing years can shift alignment and produce a wrong but plausible correlation. Validate coverage explicitly.

9. **Writing extra text to the answer file**
   - The output must usually contain only a single numeric value.

10. **Treating monthly CPI as end-of-year instead of annual average**
    - Unless instructed otherwise, annual deflation should use annual average CPI for annual nominal data.

---

## Minimal Validation Checklist

Before finalizing:

- [ ] Correct files opened
- [ ] Correct sheets selected
- [ ] Annual values extracted from year rows
- [ ] Final partial year averaged from available quarters if required
- [ ] CPI annualized correctly
- [ ] All series cover the full target year span
- [ ] Real series are positive
- [ ] Natural logs taken before HP filter
- [ ] HP filter uses `lambda=100`
- [ ] Pearson correlation computed on cyclical components
- [ ] Output file contains only one rounded number

---

## Reference Implementation

This is a complete end-to-end implementation you can copy, run, and adapt. It includes:
- workbook parsing
- ERP annual extraction
- partial-year averaging
- CPI annual loading
- real conversion
- HP filtering
- correlation
- answer writing

```python
import pandas as pd
import numpy as np
from pathlib import Path

try:
    from statsmodels.tsa.filters.hp_filter import hpfilter
    HAS_SM = True
except Exception:
    HAS_SM = False


# -----------------------------
# Configuration
# -----------------------------
PCE_PATH = "/root/ERP-2025-table10.xls"
PFI_PATH = "/root/ERP-2025-table12.xls"
CPI_PATH = "/root/CPI.xlsx"

PCE_SHEET = "B10"
PFI_SHEET = "B12"

VALUE_COL = 1
START_YEAR = 1973
END_YEAR = 2024
OUTPUT_PATH = "/root/answer.txt"


# -----------------------------
# Utility / validation helpers
# -----------------------------
def require_file(path: str) -> None:
    if not Path(path).exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def numeric_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


# -----------------------------
# ERP extraction
# -----------------------------
def extract_annual_series_from_erp(path: str, sheet_name: str, value_col: int = 1) -> pd.Series:
    """
    Extract annual ERP series using:
    - column 0 as year labels
    - `value_col` as the requested data column
    """
    require_file(path)
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)

    year_vals = pd.to_numeric(df.iloc[:, 0], errors="coerce")
    data_vals = pd.to_numeric(df.iloc[:, value_col], errors="coerce")

    mask = year_vals.notna() & data_vals.notna()
    out = pd.Series(data_vals[mask].values, index=year_vals[mask].astype(int).values)
    out = out[~out.index.duplicated(keep="first")].sort_index()

    if out.empty:
        raise ValueError(f"No annual ERP data found in {path} / {sheet_name}")

    if (out <= 0).any():
        bad_years = out[out <= 0].index.tolist()
        raise ValueError(f"Non-positive annual values in {path} / {sheet_name}: {bad_years}")

    return out


def extract_partial_year_average(path: str, sheet_name: str, target_year: int, value_col: int = 1) -> float:
    """
    For ERP current-year quarterly section:
    - row label starts with 'YYYY:'
    - collect that row's value plus subsequent quarter rows like II., III., IV., II p., ...
    - stop at source/footnote lines
    """
    require_file(path)
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)

    start_label = f"{target_year}:"
    quarter_labels = {"II.", "III.", "IV.", "II p.", "III p.", "IV p."}

    vals = []
    in_section = False

    for _, row in df.iterrows():
        label_raw = row.iloc[0]
        label = "" if pd.isna(label_raw) else str(label_raw).strip()

        value_raw = row.iloc[value_col]
        value = pd.to_numeric(pd.Series([value_raw]), errors="coerce").iloc[0]

        if label.startswith(start_label):
            in_section = True
            if pd.notna(value):
                vals.append(float(value))
            continue

        if in_section:
            if label in quarter_labels:
                if pd.isna(value):
                    raise ValueError(f"Missing quarterly value at label {label} in {path}")
                vals.append(float(value))
                continue

            if label.startswith("Source:") or label.startswith("1 ") or label.startswith("2 "):
                break

    if not vals:
        raise ValueError(f"No partial-year quarterly values found for {target_year} in {path}")

    avg = float(np.mean(vals))
    if not np.isfinite(avg) or avg <= 0:
        raise ValueError(f"Invalid partial-year average for {target_year} in {path}: {avg}")

    return avg


# -----------------------------
# CPI extraction
# -----------------------------
def load_cpi_annual(path: str) -> pd.Series:
    """
    Load annual CPI from a workbook with either:
    - annual columns (Year + CPI/value/index), or
    - monthly date/value data, which will be averaged by year.
    """
    require_file(path)
    xls = pd.ExcelFile(path)

    # Pass 1: explicit annual layout
    for sheet in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)
        if df.empty:
            continue

        lowered = {str(c).strip().lower(): c for c in df.columns}
        year_col = next((lowered[k] for k in lowered if k == "year" or k.endswith("year")), None)
        value_col = next((lowered[k] for k in lowered if "cpi" in k or "value" in k or "index" in k), None)

        if year_col is not None and value_col is not None:
            years = pd.to_numeric(df[year_col], errors="coerce")
            vals = pd.to_numeric(df[value_col], errors="coerce")
            mask = years.notna() & vals.notna()

            if mask.any():
                annual = pd.Series(vals[mask].values, index=years[mask].astype(int).values).sort_index()
                if len(annual) >= 5 and (annual > 0).all():
                    return annual

    # Pass 2: monthly layout with date column
    for sheet in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)
        if df.empty:
            continue

        for date_candidate in df.columns:
            dates = pd.to_datetime(df[date_candidate], errors="coerce")
            if dates.notna().sum() < max(5, len(df) // 2):
                continue

            numeric_candidates = []
            for col in df.columns:
                if col == date_candidate:
                    continue
                vals = pd.to_numeric(df[col], errors="coerce")
                if vals.notna().sum() >= max(5, len(df) // 2):
                    numeric_candidates.append(col)

            if not numeric_candidates:
                continue

            value_candidate = numeric_candidates[0]
            temp = pd.DataFrame({
                "date": dates,
                "value": pd.to_numeric(df[value_candidate], errors="coerce"),
            }).dropna()

            if temp.empty:
                continue

            annual = temp.groupby(temp["date"].dt.year)["value"].mean().sort_index()
            if len(annual) >= 5 and (annual > 0).all():
                return annual

    raise ValueError(f"Unable to detect CPI structure in workbook: {path}")


# -----------------------------
# Alignment / deflation
# -----------------------------
def align_and_deflate(
    nominal_a: pd.Series,
    nominal_b: pd.Series,
    cpi: pd.Series,
    start_year: int,
    end_year: int
) -> pd.DataFrame:
    years = list(range(start_year, end_year + 1))

    missing_a = [y for y in years if y not in nominal_a.index]
    missing_b = [y for y in years if y not in nominal_b.index]
    missing_cpi = [y for y in years if y not in cpi.index]

    if missing_a:
        raise ValueError(f"Nominal series A missing years: {missing_a}")
    if missing_b:
        raise ValueError(f"Nominal series B missing years: {missing_b}")
    if missing_cpi:
        raise ValueError(f"CPI missing years: {missing_cpi}")

    df = pd.DataFrame({
        "a_nom": nominal_a.loc[years].astype(float),
        "b_nom": nominal_b.loc[years].astype(float),
        "cpi": cpi.loc[years].astype(float),
    })

    if (df <= 0).any().any():
        bad_cols = [c for c in df.columns if (df[c] <= 0).any()]
        raise ValueError(f"Non-positive values found in: {bad_cols}")

    df["a_real"] = df["a_nom"] / df["cpi"]
    df["b_real"] = df["b_nom"] / df["cpi"]
    return df


# -----------------------------
# HP filter
# -----------------------------
def hp_cycle(values: pd.Series, lamb: float = 100.0) -> pd.Series:
    arr = np.asarray(values, dtype=float)

    if np.isnan(arr).any():
        raise ValueError("NaNs present before HP filtering")
    if len(arr) < 5:
        raise ValueError("Series too short for HP filtering")

    if HAS_SM:
        cycle, trend = hpfilter(arr, lamb=lamb)
        return pd.Series(cycle, index=values.index)

    # Manual fallback
    n = len(arr)
    I = np.eye(n)
    D = np.zeros((n - 2, n))
    for i in range(n - 2):
        D[i, i] = 1.0
        D[i, i + 1] = -2.0
        D[i, i + 2] = 1.0

    trend = np.linalg.solve(I + lamb * (D.T @ D), arr)
    cycle = arr - trend
    return pd.Series(cycle, index=values.index)


def detrend_logged_real(real_series: pd.Series, lamb: float = 100.0) -> pd.Series:
    if (real_series <= 0).any():
        bad_years = real_series[real_series <= 0].index.tolist()
        raise ValueError(f"Non-positive real values at years: {bad_years}")

    logged = np.log(real_series.astype(float))
    return hp_cycle(logged, lamb=lamb)


# -----------------------------
# Correlation / output
# -----------------------------
def pearson_corr(a: pd.Series, b: pd.Series) -> float:
    df = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(df) < 3:
        raise ValueError("Not enough overlapping observations for correlation")

    corr = float(np.corrcoef(df["a"], df["b"])[0, 1])
    if not np.isfinite(corr):
        raise ValueError(f"Non-finite correlation: {corr}")
    return corr


def write_answer(path: str, value: float, decimals: int = 5) -> None:
    Path(path).write_text(format(value, f".{decimals}f") + "\n", encoding="utf-8")


# -----------------------------
# Main workflow
# -----------------------------
def main() -> None:
    # 1) Load ERP annual nominal data
    pce_nom = extract_annual_series_from_erp(PCE_PATH, PCE_SHEET, value_col=VALUE_COL)
    pfi_nom = extract_annual_series_from_erp(PFI_PATH, PFI_SHEET, value_col=VALUE_COL)

    # 2) If target end year is not a finalized annual row, overwrite with average of available quarters
    # This is safe when the task explicitly instructs using available-quarter average.
    pce_nom.loc[END_YEAR] = extract_partial_year_average(PCE_PATH, PCE_SHEET, END_YEAR, value_col=VALUE_COL)
    pfi_nom.loc[END_YEAR] = extract_partial_year_average(PFI_PATH, PFI_SHEET, END_YEAR, value_col=VALUE_COL)

    # 3) Load annual CPI
    cpi = load_cpi_annual(CPI_PATH)

    # 4) Align and deflate
    df = align_and_deflate(pce_nom, pfi_nom, cpi, START_YEAR, END_YEAR)

    # 5) HP-filter logged real series with annual lambda
    cyc_pce = detrend_logged_real(df["a_real"], lamb=100.0)
    cyc_pfi = detrend_logged_real(df["b_real"], lamb=100.0)

    # 6) Compute Pearson correlation
    corr = pearson_corr(cyc_pce, cyc_pfi)

    # 7) Save answer
    write_answer(OUTPUT_PATH, corr, decimals=5)

    # Optional diagnostics for interactive runs
    print("Years:", START_YEAR, "to", END_YEAR)
    print("Observations:", len(df))
    print("Correlation:", corr)
    print("Rounded:", format(corr, ".5f"))
    print(f"Wrote answer to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

---

## Fast Execution Strategy

When solving a new instance, do this in order:

1. **Inspect the sheets first**
   - Confirm the exact sheet names and where the annual and quarterly sections live.

2. **Extract annual rows by parsing year labels**
   - Avoid hard-coded row numbers.

3. **Patch the final year with quarterly-average only if required**
   - This is a common ERP edge case.

4. **Load or derive annual CPI**
   - Make sure the annual frequency matches the annual nominal data.

5. **Align the exact year interval**
   - Never compute on vaguely overlapping years.

6. **Deflate -> log -> HP filter**
   - Keep this order fixed.

7. **Compute the scalar statistic and round exactly**
   - Write only the requested number to the answer file.

If you follow this path, you minimize the most common failure mode in this task family: producing a plausible but wrong result from year misalignment or incorrect current-year handling.