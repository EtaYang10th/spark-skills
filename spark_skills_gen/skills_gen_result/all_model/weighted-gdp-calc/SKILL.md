---
id: weighted-gdp-calc
title: Weighted Mean Calculation with Excel Lookup Population (openpyxl)
version: 1.0.0
tags: [excel, openpyxl, gdp, weighted-mean, data-lookup, spreadsheet]
task_category: weighted-gdp-calc
---

## Overview

This skill covers populating Excel workbooks with computed numeric values using Python/openpyxl, specifically for tasks involving multi-condition data lookups, derived financial metrics, statistical summaries, and weighted means. The pattern applies to any task where you must fill structured cell ranges in a "Task" sheet using source data from a "Data" sheet.

---

## Module 1: Reading and Mapping Source Data

### Step 1 — Inspect the workbook structure first

Before writing anything, read both sheets to understand:
- Which rows/columns hold the source data (series codes, country names, years)
- Which cell ranges need to be populated in the Task sheet
- What the row/column index layout is (headers vs. data rows)

```python
import openpyxl

wb = openpyxl.load_workbook("file.xlsx")
data_ws = wb["Data"]
task_ws = wb["Task"]

# Print a slice to understand layout
for row in data_ws.iter_rows(min_row=1, max_row=5, values_only=True):
    print(row)
```

### Step 2 — Build lookup dictionaries from source data

Replicate what VLOOKUP/INDEX-MATCH would do, but in Python. Key on (series_code, year) or (country, year) tuples.

```python
lookup = {}  # (series_code, country) -> {year: value}

for row in data_ws.iter_rows(min_row=21, max_row=40, values_only=True):
    series_code = row[series_col_idx]
    country     = row[country_col_idx]
    year        = row[year_col_idx]
    value       = row[value_col_idx]
    lookup.setdefault((series_code, country), {})[year] = value
```

Adjust column indices to match the actual sheet layout after inspection.

### Step 3 — Write values as numeric literals, never as formula strings

**Critical:** openpyxl does not recalculate formulas. Tests that load with `data_only=True` will read `None` or stale cached values if you write formula strings like `=INDEX(...)`. Always write computed Python floats/ints directly.

```python
# BAD — formula string, will read as None with data_only=True
task_ws["H12"] = "=INDEX(Data!$E:$E,MATCH(...))"

# GOOD — numeric literal
task_ws["H12"] = 12345.67
```

---

## Module 2: Computing Derived Metrics

### Net exports as % of GDP

```python
net_exports_pct = (exports - imports) / gdp * 100
```

Apply per country per year, writing each result into the corresponding cell.

### Statistical summary (min, max, median, mean, percentiles)

Use Python's `statistics` module and `numpy` for percentiles. Match Excel's `PERCENTILE.INC` interpolation:

```python
import statistics
import numpy as np

values = [v for v in column_values if v is not None]

min_val    = min(values)
max_val    = max(values)
median_val = statistics.median(values)
mean_val   = statistics.mean(values)
p25        = float(np.percentile(values, 25))   # matches PERCENTILE.INC
p75        = float(np.percentile(values, 75))
```

Write these into the designated stats rows in order (min, max, median, mean, p25, p75).

### Weighted mean via SUMPRODUCT pattern

```python
# Weighted mean = Σ(metric × weight) / Σ(weight)
# e.g., net_exports_pct weighted by GDP

def weighted_mean(metrics, weights):
    return sum(m * w for m, w in zip(metrics, weights)) / sum(weights)
```

Apply per year column across all countries.

---

## Module 3: Writing Back and Verifying

### Write all computed values

```python
# Example: fill a range H12:L17 (6 rows × 5 cols)
countries = ["BHR", "KWT", "OMN", "QAT", "SAU", "ARE"]
years     = [2019, 2020, 2021, 2022, 2023]

start_row, start_col = 12, 8  # H=8 in 1-based openpyxl

for r, country in enumerate(countries):
    for c, year in enumerate(years):
        val = lookup.get((series_code, country), {}).get(year)
        task_ws.cell(row=start_row + r, column=start_col + c, value=val)

wb.save("file.xlsx")
```

### Verify by re-reading with data_only=True

Simulate what the test harness does:

```python
wb_check = openpyxl.load_workbook("file.xlsx", data_only=True)
ws = wb_check["Task"]
print(ws["H12"].value)  # Should be a number, not None or a formula string
```

If you see `None` or a formula string, you wrote a formula instead of a value — go back and compute it in Python.

---

## Common Pitfalls

1. **Writing formula strings instead of numeric values.** Tests use `data_only=True`, which returns `None` for unexecuted formulas. Always compute in Python and write floats/ints.

2. **Wrong column/row index mapping.** openpyxl uses 1-based indexing for `.cell(row, column)` but 0-based for list slicing. Verify your index math against the actual sheet layout before bulk-writing.

3. **Off-by-one in source data range.** If the task says "rows 21 to 40", that's `min_row=21, max_row=40` in openpyxl — inclusive on both ends.

4. **Percentile method mismatch.** Excel's `PERCENTILE.INC` corresponds to `numpy.percentile` with default linear interpolation. Do not use `numpy.percentile(..., method='hazen')` or similar variants.

5. **Not inspecting the workbook before writing.** Always print a few rows of both sheets first. Series codes, country identifiers, and year columns vary by file — never assume positions.

6. **Skipping the re-read verification step.** Save and re-open with `data_only=True` before declaring done. This catches formula-string mistakes immediately.

7. **Modifying sheet formatting or structure.** Load with `keep_vba=False` and avoid touching cell styles unless explicitly required. Use `data_only=False` for the write pass (default) so existing cached values are preserved for cells you don't touch.
