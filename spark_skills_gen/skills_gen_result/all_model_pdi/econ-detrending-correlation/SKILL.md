---
title: "Macroeconomic Detrending and Cyclical Correlation Analysis"
category: econ-detrending-correlation
domain: macroeconomics
tags:
  - hp-filter
  - detrending
  - business-cycle
  - cpi-deflation
  - pearson-correlation
  - excel-parsing
  - economic-report-president
dependencies:
  - pandas
  - numpy
  - openpyxl
  - xlrd
  - statsmodels
  - scipy
---

# Macroeconomic Detrending and Cyclical Correlation Analysis

This skill covers the end-to-end workflow for computing business-cycle correlations between macroeconomic time series: extracting data from government Excel files (ERP tables, FRED CPI), deflating nominal series to real values, applying the Hodrick-Prescott filter, and computing Pearson correlation on the cyclical components.

---

## 1. High-Level Workflow

1. **Inspect the raw Excel files** — ERP tables have irregular layouts: merged cells, header rows with footnotes, mixed annual rows and quarterly sub-rows at the bottom. Never assume a clean CSV-like structure. Open them and print the first 20+ rows to understand skip rows, column positions, and where quarterly data begins.

2. **Extract annual data for each series** — Parse year rows (4-digit integer in column 0) and grab the "Total" column (usually column 1). Stop when you hit quarterly rows or footnotes.

3. **Handle partial-year data** — For the most recent year (e.g., 2024), only some quarters may be available. Detect quarterly sub-rows (labels like `I`, `II`, `III`, `IV`, sometimes with suffixes like `p.` for preliminary). Average all available quarters to produce the annual value. This is the single trickiest parsing step.

4. **Extract and align the CPI deflator** — CPI data from FRED is typically monthly. Compute annual averages, then normalize so the deflator equals 1.0 in the final year of the sample (or a chosen base year). This makes the "real" values interpretable in that year's dollars, though the choice of base year does not affect the correlation.

5. **Deflate nominal to real** — Divide each nominal series by the corresponding annual CPI value.

6. **Apply the HP filter to log-real series** — Take the natural log of each real series, then apply `statsmodels.tsa.filters.hp_filter.hpfilter` with `lamb=100` (standard for annual data). The filter returns `(cycle, trend)`.

7. **Compute Pearson correlation** — Use `numpy.corrcoef` on the two cyclical components. Round to the required decimal places.

8. **Write the result** — Output a single number to the answer file. Verify by reading it back.

---

## 2. Inspecting ERP Excel Files

ERP `.xls` files use the legacy Excel format (requires `xlrd`). They have:
- A title block in the first few rows (variable number of rows to skip)
- Column headers that may span multiple rows
- Annual data rows: column 0 contains a 4-digit year (int or float like `1973.0`)
- Quarterly sub-rows at the bottom for recent years: column 0 contains Roman numerals (`I`, `II`, `III`, `IV`) optionally followed by `p.` or similar annotations
- Footnote rows at the very end

```python
import pandas as pd

def inspect_excel(path, sheet=0):
    """Print raw rows to understand layout before parsing."""
    df = pd.read_excel(path, sheet_name=sheet, header=None)
    for i, row in df.iterrows():
        print(f"Row {i:3d}: {list(row.values[:6])}")
        if i > 30:
            break
    # Also print the LAST 20 rows to see quarterly data
    print("\n--- Last 20 rows ---")
    for i, row in df.tail(20).iterrows():
        print(f"Row {i:3d}: {list(row.values[:6])}")

inspect_excel('/root/ERP-2025-table10.xls')
```

**Why this matters:** The number of header rows to skip varies between ERP tables. Hardcoding `skiprows=5` will break on a different table. Always inspect first.

---

## 3. Extracting Annual + Quarterly Data from ERP Tables

The core challenge is parsing rows where:
- Annual rows have a year (int/float) in column 0 and numeric values in subsequent columns
- Quarterly rows have Roman numeral labels in column 0, belonging to the most recent year
- Some quarterly labels have annotations (e.g., `III p.` for preliminary)

**Critical detail:** When reading with `pd.read_excel(..., header=None)`, the label column comes back as-is. But if you `.strip()` the label, you lose the leading whitespace that distinguishes quarterly continuation rows from annual rows. Either: (a) check the raw string before stripping, or (b) use a different detection strategy (regex on Roman numerals).

```python
import re
import numpy as np

def extract_erp_series(path, value_col=1, start_year=1973, end_year=2024):
    """
    Extract annual series from an ERP Excel table.
    
    Parameters
    ----------
    path : str
        Path to the .xls file.
    value_col : int
        0-based column index for the desired series (usually 1 for 'Total').
    start_year, end_year : int
        Inclusive year range.
    
    Returns
    -------
    dict : {year: value} including partial-year average for the last year if needed.
    """
    df = pd.read_excel(path, sheet_name=0, header=None)
    
    annual = {}
    quarterly_values = []
    current_q_year = None
    
    roman_to_int = {'I': 1, 'II': 2, 'III': 3, 'IV': 4}
    quarter_pattern = re.compile(r'^(I{1,3}V?)\s*[p.]*$')
    
    for idx, row in df.iterrows():
        raw_label = row.iloc[0]
        val = row.iloc[value_col]
        
        # Try to parse as year
        if isinstance(raw_label, (int, float)) and not np.isnan(raw_label):
            year = int(raw_label)
            if 1900 <= year <= 2100:
                try:
                    annual[year] = float(val)
                    current_q_year = year  # track for quarterly sub-rows
                except (ValueError, TypeError):
                    pass
                continue
        
        # Try to parse as quarterly sub-row (Roman numeral)
        if isinstance(raw_label, str):
            label_clean = raw_label.strip().rstrip('.')
            # Handle annotations like "III p" -> strip trailing letters
            label_core = re.sub(r'\s+[a-zA-Z]+$', '', label_clean)
            m = quarter_pattern.match(label_core)
            if m and current_q_year is not None:
                try:
                    qval = float(val)
                    quarterly_values.append((current_q_year, m.group(1), qval))
                except (ValueError, TypeError):
                    pass
    
    # If the end_year has quarterly data but no clean annual row,
    # or if we want to override with the average of available quarters:
    q_for_end = [v for (y, q, v) in quarterly_values if y == end_year]
    if q_for_end:
        annual[end_year] = np.mean(q_for_end)
        print(f"  {end_year}: avg of {len(q_for_end)} quarters = {annual[end_year]:.2f} (vals: {q_for_end})")
    
    # Filter to requested range
    result = {y: annual[y] for y in range(start_year, end_year + 1) if y in annual}
    
    if len(result) != (end_year - start_year + 1):
        missing = [y for y in range(start_year, end_year + 1) if y not in result]
        print(f"  WARNING: missing years: {missing}")
    
    return result
```

**Key edge cases:**
- The year `2024` may appear as an annual row with a value that is only Q1, or it may not appear at all — only quarterly sub-rows exist. Always check quarterly data for the final year and average what's available.
- Some ERP tables have the year as `float` (e.g., `2020.0`), not `int`. Cast with `int()`.
- Quarterly labels can be `"I"`, `" II"`, `" III p."`, `"  IV"` — the leading spaces and trailing annotations vary. Use a flexible regex.

---

## 4. CPI Extraction and Deflation

FRED CPI data (`CPI.xlsx` or `.csv`) is typically monthly with columns like `observation_date` and a value column (e.g., `CPIAUCSL`).

```python
def load_annual_cpi(path, start_year=1973, end_year=2024):
    """
    Load monthly CPI, compute annual averages, normalize to base year = end_year.
    
    Returns
    -------
    dict : {year: deflator} where deflator for end_year = 1.0
    """
    cpi_df = pd.read_excel(path)
    
    # Find the date column and value column
    date_col = None
    val_col = None
    for col in cpi_df.columns:
        col_lower = str(col).lower()
        if 'date' in col_lower:
            date_col = col
        elif 'cpi' in col_lower or col_lower in ('value', 'cpiaucsl'):
            val_col = col
    
    # Fallback: first column is date, second is value
    if date_col is None:
        date_col = cpi_df.columns[0]
    if val_col is None:
        val_col = cpi_df.columns[1]
    
    cpi_df[date_col] = pd.to_datetime(cpi_df[date_col])
    cpi_df['year'] = cpi_df[date_col].dt.year
    cpi_df[val_col] = pd.to_numeric(cpi_df[val_col], errors='coerce')
    
    annual_cpi = cpi_df.groupby('year')[val_col].mean()
    
    # Normalize: deflator = CPI_year / CPI_base_year
    base_cpi = annual_cpi[end_year]
    deflator = {int(y): v / base_cpi for y, v in annual_cpi.items()
                if start_year <= y <= end_year}
    
    return deflator


def deflate_series(nominal, cpi_deflator):
    """
    Convert nominal to real: real = nominal / deflator.
    Both inputs are dicts keyed by year.
    """
    return {y: nominal[y] / cpi_deflator[y] for y in nominal if y in cpi_deflator}
```

**Important notes on deflation:**
- The choice of CPI base year does NOT affect the HP-filter cyclical component or the correlation. The log transformation absorbs any multiplicative constant. But normalizing to the final year makes the real values intuitive.
- If CPI data for the final year is incomplete (e.g., only Jan–Sep 2024), the annual average is computed from available months. This is fine — it matches the partial-year convention used for the macro series.

---

## 5. HP Filter Detrending

The Hodrick-Prescott filter decomposes a time series \( y_t \) into trend \( \tau_t \) and cycle \( c_t = y_t - \tau_t \). The smoothing parameter \( \lambda \) controls how smooth the trend is:
- \( \lambda = 100 \) for annual data (standard)
- \( \lambda = 1600 \) for quarterly data
- \( \lambda = 129600 \) for monthly data

**Always apply the filter to the log of the real series**, not the raw levels. This ensures the cyclical component represents percentage deviations from trend.

```python
from statsmodels.tsa.filters.hp_filter import hpfilter

def hp_detrend(real_series_dict, lamb=100):
    """
    Apply HP filter to log of real series.
    
    Parameters
    ----------
    real_series_dict : dict
        {year: real_value}, must be in chronological order.
    lamb : float
        Smoothing parameter (100 for annual).
    
    Returns
    -------
    cycle : np.ndarray
        Cyclical component (log deviations from trend).
    trend : np.ndarray
        Trend component.
    years : list
        Corresponding years.
    """
    years = sorted(real_series_dict.keys())
    values = np.array([real_series_dict[y] for y in years])
    log_values = np.log(values)
    
    cycle, trend = hpfilter(log_values, lamb=lamb)
    return cycle, trend, years
```

**Pitfall:** If you pass `lambda` as the keyword argument name, Python will throw a syntax error because `lambda` is a reserved word. The `statsmodels` function uses `lamb` as the parameter name.

---

## 6. Pearson Correlation and Output

```python
def compute_and_save_correlation(cycle_a, cycle_b, output_path, decimals=5):
    """Compute Pearson correlation, round, and save to file."""
    corr_matrix = np.corrcoef(cycle_a, cycle_b)
    corr = corr_matrix[0, 1]
    result = round(corr, decimals)
    
    with open(output_path, 'w') as f:
        f.write(str(result))
    
    print(f"Correlation: {corr}")
    print(f"Rounded to {decimals} decimals: {result}")
    print(f"Written to {output_path}")
    
    return result
```

**Output format:** The answer file must contain ONLY the number (e.g., `0.68885`), no newline issues, no extra text. Use `str(round(corr, 5))` which produces the correct format. Verify by reading the file back.

---

## 7. Common Pitfalls

### 7.1 Quarterly Row Parsing Failures
**Problem:** The most common failure mode is incorrectly parsing quarterly sub-rows for the final year, resulting in either (a) missing the final year entirely, or (b) using only Q1 instead of averaging all available quarters.

**Root cause:** Quarterly labels have inconsistent formatting — leading whitespace, trailing periods, preliminary annotations (`p.`, `p`). If you `.strip()` the raw label early, you lose the whitespace signal. If your regex is too strict, you miss annotated quarters.

**Fix:** Use a flexible regex that handles annotations: strip the label, remove trailing periods, remove trailing letter annotations, then match the Roman numeral core. Always print the number of quarters found and their values as a sanity check.

### 7.2 Wrong Lambda Value
**Problem:** Using `lamb=1600` (quarterly convention) on annual data produces an overly smooth trend that absorbs most of the cyclical variation, yielding a near-zero correlation.

**Fix:** Always use `lamb=100` for annual data. If the task specifies a different value, use that.

### 7.3 Forgetting to Take Logs
**Problem:** Applying HP filter to levels instead of logs. The cyclical component then represents absolute deviations rather than percentage deviations, and the correlation can differ meaningfully.

**Fix:** Always `np.log()` the real series before filtering.

### 7.4 CPI Alignment Issues
**Problem:** CPI years don't cover the full range of the macro series, causing KeyError during deflation.

**Fix:** Check that the CPI data covers the full year range before deflating. Print the CPI year range as a sanity check.

### 7.5 Excel Column Indexing
**Problem:** ERP tables have a label column (col 0) and then data columns. "Total" is typically column 1 (0-indexed), but some tables have sub-categories that shift the column index.

**Fix:** Inspect the header row to confirm which column index corresponds to "Total" or the desired series. Print the first data row to verify.

---

## 8. Verification Checklist

Before writing the final answer, verify:

1. **Year count:** `end_year - start_year + 1` observations (e.g., 52 for 1973–2024).
2. **2024 value:** Print the 2024 value for each series and confirm it's an average of the expected number of quarters (typically 3 for a mid-year release).
3. **Real values are reasonable:** PCE and PFI in the final year should roughly equal their nominal values (since CPI is normalized to 1.0 in the base year).
4. **Correlation sign and magnitude:** For PCE and PFI, expect a positive correlation in the range 0.5–0.9. A negative or near-zero value suggests a data or methodology error.
5. **File content:** Read back the answer file and confirm it contains exactly one number with the correct number of decimal places.

---

## Reference Implementation

This is a complete, self-contained script that performs the entire task. Copy, adapt file paths and column indices as needed, and run.

```python
#!/usr/bin/env python3
"""
Macroeconomic Detrending & Cyclical Correlation
================================================
Computes the Pearson correlation between HP-filtered cyclical components
of real Personal Consumption Expenditures (PCE) and real Private Fixed
Investment (PFI) for a given year range.

Inputs:
  - ERP table for PCE (nominal, .xls)
  - ERP table for PFI (nominal, .xls)
  - CPI data (.xlsx or .csv from FRED)

Output:
  - answer.txt containing the rounded correlation coefficient
"""

import re
import numpy as np
import pandas as pd
from statsmodels.tsa.filters.hp_filter import hpfilter

# ============================================================
# CONFIGURATION — adapt these for each task instance
# ============================================================
PCE_PATH = '/root/ERP-2025-table10.xls'
PFI_PATH = '/root/ERP-2025-table12.xls'
CPI_PATH = '/root/CPI.xlsx'
OUTPUT_PATH = '/root/answer.txt'

PCE_VALUE_COL = 1   # 0-indexed column for Total PCE
PFI_VALUE_COL = 1   # 0-indexed column for Total PFI

START_YEAR = 1973
END_YEAR = 2024
HP_LAMBDA = 100      # Standard for annual data
ROUND_DECIMALS = 5

# ============================================================
# STEP 1: Extract annual + quarterly data from ERP Excel tables
# ============================================================
def extract_erp_series(path, value_col, start_year, end_year):
    """
    Parse an ERP .xls table to extract an annual time series.
    Handles mixed annual/quarterly layouts and partial final years.
    """
    df = pd.read_excel(path, sheet_name=0, header=None)
    
    annual = {}
    quarterly_values = []
    current_q_year = None
    
    quarter_pattern = re.compile(r'^(I{1,3}V?)\s*[p.]*$')
    
    for idx, row in df.iterrows():
        raw_label = row.iloc[0]
        val = row.iloc[value_col]
        
        # --- Try to parse as a year row ---
        if isinstance(raw_label, (int, float)):
            try:
                if np.isnan(raw_label):
                    continue
            except (TypeError, ValueError):
                pass
            year = int(raw_label)
            if 1900 <= year <= 2100:
                try:
                    fval = float(val)
                    annual[year] = fval
                    current_q_year = year
                except (ValueError, TypeError):
                    pass
                continue
        
        # --- Try to parse as a quarterly sub-row ---
        if isinstance(raw_label, str):
            # Clean the label: strip whitespace, remove trailing dots
            label_clean = raw_label.strip().rstrip('.')
            # Remove trailing annotations like " p", " r", " p."
            label_core = re.sub(r'\s+[a-zA-Z]+$', '', label_clean).strip()
            
            m = quarter_pattern.match(label_core)
            if m and current_q_year is not None:
                try:
                    qval = float(val)
                    quarterly_values.append((current_q_year, m.group(1), qval))
                except (ValueError, TypeError):
                    pass
    
    # For the final year, use average of available quarters
    q_for_end = [v for (y, q, v) in quarterly_values if y == end_year]
    if q_for_end:
        annual[end_year] = np.mean(q_for_end)
        print(f"  {end_year}: avg of {len(q_for_end)} quarters = "
              f"{annual[end_year]:.2f} (vals: {q_for_end})")
    
    # Filter to requested range
    result = {}
    for y in range(start_year, end_year + 1):
        if y in annual:
            result[y] = annual[y]
    
    expected = end_year - start_year + 1
    if len(result) != expected:
        missing = [y for y in range(start_year, end_year + 1) if y not in result]
        print(f"  WARNING: expected {expected} years, got {len(result)}. "
              f"Missing: {missing}")
    else:
        print(f"  Extracted {len(result)} years ({start_year}-{end_year})")
    
    return result


# ============================================================
# STEP 2: Load CPI and compute annual deflator
# ============================================================
def load_annual_cpi(path, start_year, end_year):
    """
    Load CPI data (monthly from FRED), compute annual averages,
    normalize so deflator = 1.0 in end_year.
    """
    # Try reading as Excel; fall back to CSV
    try:
        cpi_df = pd.read_excel(path)
    except Exception:
        cpi_df = pd.read_csv(path)
    
    # Identify date and value columns
    date_col = None
    val_col = None
    for col in cpi_df.columns:
        cl = str(col).lower()
        if 'date' in cl:
            date_col = col
        elif 'cpi' in cl or cl in ('value', 'cpiaucsl'):
            val_col = col
    if date_col is None:
        date_col = cpi_df.columns[0]
    if val_col is None:
        val_col = cpi_df.columns[1]
    
    cpi_df[date_col] = pd.to_datetime(cpi_df[date_col])
    cpi_df['year'] = cpi_df[date_col].dt.year
    cpi_df[val_col] = pd.to_numeric(cpi_df[val_col], errors='coerce')
    
    annual_cpi = cpi_df.groupby('year')[val_col].mean()
    
    # Normalize to end_year
    base = annual_cpi.get(end_year, annual_cpi.iloc[-1])
    deflator = {}
    for y in range(start_year, end_year + 1):
        if y in annual_cpi.index:
            deflator[y] = annual_cpi[y] / base
    
    print(f"CPI: {len(deflator)} years, base year {end_year} "
          f"(CPI={base:.2f})")
    
    # Check coverage
    missing = [y for y in range(start_year, end_year + 1) if y not in deflator]
    if missing:
        print(f"  WARNING: CPI missing for years: {missing}")
    
    return deflator


# ============================================================
# STEP 3: Deflate nominal to real
# ============================================================
def deflate(nominal, cpi_deflator):
    """real = nominal / deflator"""
    return {y: nominal[y] / cpi_deflator[y]
            for y in nominal if y in cpi_deflator}


# ============================================================
# STEP 4: HP filter on log-real series
# ============================================================
def hp_detrend(real_dict, lamb):
    """Apply HP filter to log of real series. Returns (cycle, trend, years)."""
    years = sorted(real_dict.keys())
    values = np.array([real_dict[y] for y in years])
    log_values = np.log(values)
    cycle, trend = hpfilter(log_values, lamb=lamb)
    return cycle, trend, years


# ============================================================
# STEP 5: Compute correlation and save
# ============================================================
def compute_correlation(cycle_a, cycle_b, output_path, decimals):
    """Pearson correlation, rounded, written to file."""
    corr = np.corrcoef(cycle_a, cycle_b)[0, 1]
    result = round(corr, decimals)
    
    with open(output_path, 'w') as f:
        f.write(str(result))
    
    print(f"\nCorrelation: {corr}")
    print(f"Rounded to {decimals} decimals: {result}")
    print(f"Written to {output_path}")
    return result


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("PCE extraction:")
    pce_nominal = extract_erp_series(PCE_PATH, PCE_VALUE_COL,
                                      START_YEAR, END_YEAR)
    
    print("\nPFI extraction:")
    pfi_nominal = extract_erp_series(PFI_PATH, PFI_VALUE_COL,
                                      START_YEAR, END_YEAR)
    
    print("\nCPI loading:")
    cpi = load_annual_cpi(CPI_PATH, START_YEAR, END_YEAR)
    
    # Deflate
    pce_real = deflate(pce_nominal, cpi)
    pfi_real = deflate(pfi_nominal, cpi)
    
    # Sanity checks
    years = sorted(pce_real.keys())
    print(f"\nYears: {years[0]}-{years[-1]} ({len(years)} obs)")
    print(f"Real PCE {END_YEAR}: {pce_real[END_YEAR]:.1f}")
    print(f"Real PFI {END_YEAR}: {pfi_real[END_YEAR]:.1f}")
    
    # HP filter
    cycle_pce, _, _ = hp_detrend(pce_real, HP_LAMBDA)
    cycle_pfi, _, _ = hp_detrend(pfi_real, HP_LAMBDA)
    
    # Correlation
    result = compute_correlation(cycle_pce, cycle_pfi,
                                  OUTPUT_PATH, ROUND_DECIMALS)
    
    # Verify
    with open(OUTPUT_PATH, 'r') as f:
        content = f.read().strip()
    print(f"Verification — file contains: '{content}'")
    assert content == str(result), "File content mismatch!"
    print("=" * 60)


if __name__ == '__main__':
    main()
```

---

## 9. Environment Setup

If packages are not pre-installed:

```bash
pip install pandas numpy openpyxl xlrd statsmodels scipy
```

- `xlrd` is required for `.xls` (legacy Excel) files. `openpyxl` handles `.xlsx`.
- `statsmodels` provides `hpfilter`. No need to implement it manually.
- `scipy` is a dependency of `statsmodels` and provides `scipy.stats.pearsonr` as an alternative to `np.corrcoef`, but `np.corrcoef` is simpler for two-series correlation.

---

## 10. Adapting to Variations

| Variation | What to change |
|---|---|
| Different ERP table | Adjust `value_col` index; inspect headers |
| Quarterly analysis (not annual) | Use quarterly data directly; set `HP_LAMBDA = 1600` |
| Different deflator (GDP deflator) | Replace CPI loading with GDP deflator extraction |
| Different year range | Change `START_YEAR` / `END_YEAR` |
| Multiple correlations (correlation matrix) | Loop over pairs of series; use `pd.DataFrame.corr()` |
| Band-pass filter instead of HP | Use `statsmodels.tsa.filters.bk_filter.bkfilter` |
| Output as percentage | Multiply cyclical component by 100 before correlation (does not change correlation) |