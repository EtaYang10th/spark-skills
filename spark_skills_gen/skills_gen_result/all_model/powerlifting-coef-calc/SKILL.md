---
id: powerlifting-coef-calc
title: Powerlifting Coefficient Calculation in Excel (openpyxl)
version: 1.0.0
tags: [excel, openpyxl, powerlifting, sports-analytics, formula-injection]
---

## Overview

This skill covers populating a secondary Excel sheet with filtered columns, computed totals, and sport-specific coefficient formulas using `openpyxl`. The pattern applies to any task where you must: read a source sheet, extract relevant columns, and append formula-driven derived columns to a target sheet.

---

## Module 1: Understand the Data Before Writing Anything

### Step 1 — Read the data dictionary

Always start by reading any accompanying README or schema file:

```bash
cat /root/data/data-readme.md
```

Identify:
- Which columns are inputs to the target formula
- Column names (exact spelling and casing matter)
- Any categorical columns (e.g. `Sex`) that gate formula branching

### Step 2 — Inspect the workbook structure

```python
import openpyxl

wb = openpyxl.load_workbook("data/openipf.xlsx")
ws_data = wb["Data"]

# Print headers
headers = [cell.value for cell in ws_data[1]]
print(headers)

# Check row count
print(f"Rows: {ws_data.max_row}, Cols: {ws_data.max_column}")
```

Confirm:
- Header row index (usually row 1)
- Data row range (e.g. rows 2–N)
- Exact column names needed for the target sheet

---

## Module 2: Populate the Target Sheet

### Step 1 — Copy selected columns in original order

Preserve the column order and names from the source sheet. Do not reorder or rename.

```python
ws_dots = wb["Dots"]

# Define which columns to copy (by header name, in source order)
target_headers = ["Name", "Sex", "BodyweightKg", "Best3SquatKg", "Best3BenchKg", "Best3DeadliftKg"]

# Map header name -> column index in source sheet
header_map = {cell.value: cell.column for cell in ws_data[1]}

# Write headers to Dots sheet
for col_idx, name in enumerate(target_headers, start=1):
    ws_dots.cell(row=1, column=col_idx, value=name)

# Copy data rows
for row in ws_data.iter_rows(min_row=2, values_only=True):
    src_row = {ws_data.cell(1, i+1).value: v for i, v in enumerate(row)}
    out_row = [src_row[h] for h in target_headers]
    ws_dots.append(out_row)
```

### Step 2 — Append TotalKg as an Excel formula column

Add a `TotalKg` column after the copied columns. Use `ROUND(..., 3)` for 3-digit precision. Reference the lift columns by their letter positions in the Dots sheet.

```python
total_col = len(target_headers) + 1  # next column index
ws_dots.cell(row=1, column=total_col, value="TotalKg")

# Identify column letters for the three lift columns
from openpyxl.utils import get_column_letter

squat_col  = get_column_letter(target_headers.index("Best3SquatKg") + 1)
bench_col  = get_column_letter(target_headers.index("Best3BenchKg") + 1)
dead_col   = get_column_letter(target_headers.index("Best3DeadliftKg") + 1)
total_letter = get_column_letter(total_col)

for row_idx in range(2, ws_dots.max_row + 1):
    formula = f"=ROUND({squat_col}{row_idx}+{bench_col}{row_idx}+{dead_col}{row_idx},3)"
    ws_dots.cell(row=row_idx, column=total_col, value=formula)
```

### Step 3 — Append the Dots coefficient as an Excel formula column

The Dots formula uses a sex-specific 5th-degree polynomial denominator. Embed the polynomial coefficients directly into the Excel formula string.

```python
dots_col = total_col + 1
ws_dots.cell(row=1, column=dots_col, value="Dots")

sex_col   = get_column_letter(target_headers.index("Sex") + 1)
bw_col    = get_column_letter(target_headers.index("BodyweightKg") + 1)

# Official Dots polynomial coefficients (male and female)
# poly(x) = a0 + a1*x + a2*x^2 + a3*x^3 + a4*x^4 + a5*x^5
def poly_expr(col, coeffs):
    a = coeffs
    return (f"{a[0]}+{a[1]}*{col}+{a[2]}*{col}^2"
            f"+{a[3]}*{col}^3+{a[4]}*{col}^4+{a[5]}*{col}^5")

male_coeffs   = [-307.75076, 24.0900756, -0.1918759221, 0.0007391293, -0.000001093, 0.0000000004701]
female_coeffs = [-57.96288, 13.6175032, -0.1126655495, 0.0005158568, -0.0000010706, 0.0000000009282]

for row_idx in range(2, ws_dots.max_row + 1):
    bw = f"{bw_col}{row_idx}"
    tot = f"{total_letter}{row_idx}"
    sex = f"{sex_col}{row_idx}"
    m_denom = poly_expr(bw, male_coeffs)
    f_denom = poly_expr(bw, female_coeffs)
    formula = (
        f'=IF({sex}="M",'
        f'ROUND(500/({m_denom})*{tot},3),'
        f'ROUND(500/({f_denom})*{tot},3))'
    )
    ws_dots.cell(row=row_idx, column=dots_col, value=formula)

wb.save("data/openipf.xlsx")
```

---

## Module 3: Verify Before Finalizing

Run a quick sanity check after saving:

```python
wb2 = openpyxl.load_workbook("data/openipf.xlsx")
ws = wb2["Dots"]

# Check headers
print([ws.cell(1, c).value for c in range(1, ws.max_column + 1)])

# Check a formula cell
print(ws["G2"].value)  # should start with =ROUND(
print(ws["H2"].value)  # should start with =IF(

# Check row count matches Data sheet
print(f"Dots rows: {ws.max_row}")
```

Confirm:
- Column headers match expected names exactly
- `TotalKg` and `Dots` cells contain formula strings (not raw values)
- Row count in Dots equals row count in Data

---

## Common Pitfalls

- **Wrong column order**: The target sheet must preserve the same column order as the source. Do not sort or regroup columns.
- **Off-by-one row indexing**: Headers are row 1; data starts at row 2. Using `ws.max_row` after appending headers gives the correct last data row.
- **Hardcoding column letters**: If you hardcode `D`, `E`, `F` etc., a column insertion will silently break formulas. Derive letters dynamically from `target_headers.index(...)`.
- **Saving before verifying**: Always reload and spot-check formula strings after `wb.save()`. openpyxl writes formula strings as-is — a typo won't raise an error.
- **Using `values_only=True` when you need formulas**: When reading back to verify, load without `data_only=True` so you see the formula strings, not cached values.
- **Polynomial coefficient precision**: Use the full published Dots coefficients. Truncating them introduces scoring errors that compound across the total weight range.
- **Sex column case sensitivity**: The `IF` branch checks `"M"` vs `"F"` — match the exact casing present in the source data.
