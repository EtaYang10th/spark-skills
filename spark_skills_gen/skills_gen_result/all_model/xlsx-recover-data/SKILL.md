---
name: xlsx-recover-data
description: >
  Recover missing values (marked as "???") in multi-sheet Excel workbooks by
  analyzing inter-sheet relationships, row/column totals, year-over-year
  percentages, percentage shares, CAGR, and averages. Save the completed
  workbook as a new .xlsx file.
tags:
  - excel
  - data-recovery
  - openpyxl
  - numerical-reasoning
  - spreadsheet
version: 1
---

# Skill: Recovering Missing Values in Multi-Sheet Excel Workbooks

## Overview

NASA-budget-style tasks give you an `.xlsx` workbook with several interrelated
sheets (e.g. raw budgets, year-over-year changes, directorate shares, growth
analysis). Some cells contain `"???"` instead of numbers. Your job is to
reverse-engineer every missing value from the surrounding data and cross-sheet
relationships, then write a clean `.xlsx` with all `"???"` replaced by the
correct numeric values.

---

## High-Level Workflow

1. **Inspect the workbook structure** — list every sheet, its dimensions, headers,
   and locate every `"???"` cell.
2. **Map the data model** — understand what each sheet represents and how sheets
   reference each other (totals, percentages, averages, CAGR).
3. **Determine a solve order** — some missing values depend on others; solve
   independent ones first, then cascade.
4. **Compute each missing value** using the appropriate formula class (see below).
5. **Write values back** with `openpyxl`, preserving types (int vs float).
6. **Verify** by re-reading the saved file and checking row/column consistency.

---

## Step 1 — Inspect the Workbook

```python
import openpyxl

wb = openpyxl.load_workbook('nasa_budget_incomplete.xlsx')

for name in wb.sheetnames:
    ws = wb[name]
    print(f"\n=== Sheet: {name} (rows={ws.max_row}, cols={ws.max_column}) ===")
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                            max_col=ws.max_column, values_only=False):
        vals = []
        for cell in row:
            v = cell.value
            if v == "???":
                vals.append(f"???@{cell.coordinate}")
            else:
                vals.append(str(v) if v is not None else "")
        print(" | ".join(vals))
```

Capture every `???@CellRef` — you need the full list before planning.

---

## Step 2 — Understand Common Sheet Types

| Sheet type | Typical layout | Key relationship |
|---|---|---|
| **Budget / Raw data** | Rows = fiscal years, Cols = directorates, last col = Total | `Total = sum of all directorate columns in that row` |
| **YoY Changes (%)** | Same grid as budget but values are `(current - previous) / previous * 100` | Links two consecutive budget rows |
| **Directorate Shares (%)** | Same grid, values are `directorate / total * 100` | Links a budget cell to its row total |
| **Growth Analysis** | Summary stats: CAGR, 5-year change, average annual budget, first/last year values | Derived from budget sheet columns |

---

## Step 3 — Determine Solve Order

Build a dependency graph. Common patterns:

- **Row total missing** → sum all known directorate values in that row.
- **Directorate value missing but total known** → `total - sum(other directorates)`.
- **Both a directorate AND total missing in the same row** → solve the total
  first from the YoY sheet (if the previous year's total and the YoY% for
  totals are known), then back-solve the directorate.
- **YoY% missing** → need both current and previous year budget values.
- **Share% missing** → need the directorate value and the row total.
- **CAGR missing** → need first-year and last-year values plus the number of
  periods.
- **Average missing** → need the set of values being averaged. **Pay close
  attention to which years are included** (see Pitfalls).

Pseudocode for ordering:

```python
# 1. Solve budget-sheet totals (row sums) that can be computed directly
# 2. Solve budget-sheet totals derivable from YoY + previous total
# 3. Solve budget-sheet directorate values via total - others
# 4. Solve YoY cells from now-complete budget rows
# 5. Solve share cells from now-complete budget rows + totals
# 6. Solve growth-analysis cells from now-complete budget columns
```

---

## Step 4 — Formula Reference with Code

### 4a. Row Total (sum of directorates)

```python
def compute_row_total(ws, row_idx, first_data_col, last_data_col):
    """Sum columns first_data_col..last_data_col in the given row."""
    total = 0
    for col in range(first_data_col, last_data_col + 1):
        v = ws.cell(row=row_idx, column=col).value
        if v is not None and v != "???":
            total += v
    return total
```

### 4b. Missing Directorate from Total

```python
def compute_missing_from_total(ws, row_idx, total_col, first_data_col, last_data_col, missing_col):
    """total - sum(all other known directorates)."""
    total = ws.cell(row=row_idx, column=total_col).value
    others = 0
    for col in range(first_data_col, last_data_col + 1):
        if col == missing_col:
            continue
        v = ws.cell(row=row_idx, column=col).value
        if v is not None and v != "???":
            others += v
    return total - others
```

### 4c. Year-over-Year Percentage

```python
def compute_yoy(current, previous):
    """YoY% = (current - previous) / previous * 100, rounded to 2 decimals."""
    return round((current - previous) / previous * 100, 2)
```

### 4d. Deriving a Budget Value from YoY

```python
def budget_from_yoy(previous_value, yoy_pct):
    """current = previous * (1 + yoy/100), rounded to nearest int."""
    return round(previous_value * (1 + yoy_pct / 100))
```

### 4e. Directorate Share

```python
def compute_share(directorate_value, row_total):
    """Share% = directorate / total * 100, rounded to 2 decimals."""
    return round(directorate_value / row_total * 100, 2)
```

### 4f. CAGR (Compound Annual Growth Rate)

```python
def compute_cagr(first_year_val, last_year_val, n_years):
    """CAGR = ((last/first)^(1/n) - 1) * 100, rounded to 2 decimals."""
    return round(((last_year_val / first_year_val) ** (1 / n_years) - 1) * 100, 2)
```

### 4g. N-Year Change (absolute)

```python
def compute_change(last_year_val, first_year_val):
    return last_year_val - first_year_val
```

### 4h. Average Annual Budget

```python
def compute_avg(values, decimals=1):
    """Average of a list of annual values. Round to `decimals` places."""
    return round(sum(values) / len(values), decimals)
```

---

## Step 5 — Write Values Back and Save

```python
import openpyxl

wb = openpyxl.load_workbook('nasa_budget_incomplete.xlsx')

ws_budget = wb['Budget']          # adjust sheet names to match actual workbook
ws_yoy    = wb['YoY Changes']
ws_share  = wb['Directorate Shares']
ws_growth = wb['Growth Analysis']

# --- Budget sheet ---
# Example: K5 (Total for FY2016 row)
k5 = compute_row_total(ws_budget, row_idx=5, first_data_col=2, last_data_col=10)
ws_budget['K5'] = k5

# Example: F8 (Space Ops for FY2019) — total known, one directorate missing
f8 = compute_missing_from_total(ws_budget, row_idx=8, total_col=11,
                                 first_data_col=2, last_data_col=10, missing_col=6)
ws_budget['F8'] = f8

# Example: K10 (Total for FY2021) derived from YoY
# Previous total K9 = 22513, YoY for totals row 9 = 3.43%
k10 = budget_from_yoy(22513, 3.43)
ws_budget['K10'] = k10

# Example: E10 (Exploration FY2021) — now that K10 is known
e10 = compute_missing_from_total(ws_budget, row_idx=10, total_col=11,
                                  first_data_col=2, last_data_col=10, missing_col=5)
ws_budget['E10'] = e10

# --- YoY sheet ---
d7_yoy = compute_yoy(current=927, previous=760)
ws_yoy['D7'] = d7_yoy

# --- Share sheet ---
f5_share = compute_share(5029, k5)
ws_share['F5'] = f5_share

# --- Growth sheet ---
# CAGR: Exploration grew from 5047 (FY2019) to 7618 (FY2024) over 5 years
e4_cagr = compute_cagr(5047, 7618, 5)
ws_growth['E4'] = e4_cagr

# 5-year change
b7 = compute_change(8440, 6906)
ws_growth['B7'] = b7

# Average annual budget — CRITICAL: use the correct year range
# FY2019-FY2023 = 5 values (not 6!)
b8 = compute_avg([6906, 7139, 7301, 7614, 8262], decimals=1)
ws_growth['B8'] = b8

wb.save('nasa_budget_recovered.xlsx')
print("Saved nasa_budget_recovered.xlsx")
```

---

## Step 6 — Verification

After saving, re-read the file and run sanity checks:

```python
wb2 = openpyxl.load_workbook('nasa_budget_recovered.xlsx')

# Check no "???" remain
for name in wb2.sheetnames:
    ws = wb2[name]
    for row in ws.iter_rows(values_only=False):
        for cell in row:
            if cell.value == "???":
                print(f"STILL MISSING: {name}!{cell.coordinate}")

# Spot-check row totals
ws = wb2['Budget']
for r in range(ws.min_row + 1, ws.max_row + 1):
    row_sum = sum(ws.cell(row=r, column=c).value or 0
                  for c in range(2, ws.max_column))
    total_cell = ws.cell(row=r, column=ws.max_column).value
    if total_cell and abs(row_sum - total_cell) > 1:
        print(f"Row {r} mismatch: sum={row_sum}, total={total_cell}")

print("Verification complete.")
```

---

## Common Pitfalls

### 1. Wrong Year Range for Averages (CRITICAL)

The most common failure in this task class. When a growth-analysis sheet says
"Avg Annual Budget", you must determine **exactly which fiscal years** are
included. Typical trap:

- The budget sheet may have FY2019 through FY2024 (6 rows).
- But the growth analysis header says "FY2019–FY2023" (5 years).
- If you blindly average all 6 years you get the wrong answer.

**How to determine the correct range:**
- Read the growth-analysis sheet headers/labels carefully for year ranges.
- Cross-reference with the "5-Year Change" row — if it says "5-Year Change",
  the average likely covers 5 fiscal years (not 6).
- Check whether the CAGR exponent matches: `(1/5)` means 5 periods → 6 data
  points for CAGR but the average may still be over 5 years if the label says so.
- When in doubt, look at which years the "5-Year Change" spans (e.g., last − first)
  and use those same years for the average.

```python
# WRONG — includes FY2024 (6 values)
avg_wrong = round((6906 + 7139 + 7301 + 7614 + 8262 + 8440) / 6, 1)  # 7610.3

# CORRECT — FY2019 through FY2023 only (5 values)
avg_right = round((6906 + 7139 + 7301 + 7614 + 8262) / 5, 1)  # 7444.4
```

### 2. Integer vs Float Types

Budget values are typically integers. YoY%, shares, CAGR, and averages are
typically floats rounded to 2 decimal places (or 1 for averages). Match the
expected precision:

```python
# Budget values → int (or round to int)
ws_budget['K10'] = 23285       # not 23285.0

# Percentages → float, 2 decimals
ws_yoy['D7'] = 21.97           # not 21.974...

# Averages → float, 1 decimal (check the sheet)
ws_growth['B8'] = 7444.4       # not 7444
```

### 3. Circular Dependencies

Sometimes two cells in the same row are both `"???"` (e.g., a directorate AND
the row total). You cannot solve one from the other directly. Break the cycle
by using a **different sheet**:

- Use the YoY sheet to derive the total from the previous year's total.
- Then back-solve the missing directorate from the now-known total.

### 4. Rounding Accumulation

When deriving a budget value from YoY (`round(prev * (1 + pct/100))`), use
Python's `round()` which does banker's rounding. For most tasks this matches
the expected output. If you get off-by-one errors, check whether the test
expects `int(...)`, `round(...)`, or `math.floor(...)`.

### 5. Column/Row Index Mapping

Excel columns are 1-indexed and letter-based. `openpyxl` supports both:

```python
# These are equivalent:
ws['K5']                              # letter-based
ws.cell(row=5, column=11)            # 1-indexed numeric

# To convert: openpyxl.utils.column_index_from_string('K') → 11
from openpyxl.utils import column_index_from_string, get_column_letter
```

Always double-check your column mapping by printing the header row first.

### 6. Don't Trust Sheet Names Blindly

Sheet names vary across task instances. Always enumerate `wb.sheetnames` and
inspect headers before hard-coding references.

### 7. Negative YoY Values Are Valid

A directorate's budget can decrease year-over-year. Don't assume all YoY
values are positive:

```python
# Space Ops went from 3989 to 3986 → -0.08%
yoy = round((3986 - 3989) / 3989 * 100, 2)  # -0.08
```

---

## Quick-Reference: Formula Cheat Sheet

| What | Formula | Round |
|---|---|---|
| Row total | `sum(directorates)` | int |
| Missing directorate | `total - sum(others)` | int |
| YoY % | `(curr - prev) / prev * 100` | 2 dp |
| Budget from YoY | `prev * (1 + yoy/100)` | int |
| Share % | `dir / total * 100` | 2 dp |
| CAGR | `((last/first)^(1/n) - 1) * 100` | 2 dp |
| N-year change | `last - first` | int |
| Avg annual budget | `sum(values) / count` | 1 dp |

---

## Environment Notes

- `openpyxl` is pre-installed (`openpyxl==3.1.5`). No need for `pandas` unless
  you prefer it for exploration.
- Python 3 is available at `/usr/bin/python3`.
- Work in `/root` unless told otherwise.
- Save output as the exact filename specified in the task (e.g.,
  `nasa_budget_recovered.xlsx`).