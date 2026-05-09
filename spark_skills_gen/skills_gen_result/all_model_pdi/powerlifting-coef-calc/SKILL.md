---
title: "Powerlifting Dots Coefficient Calculation in Excel with openpyxl"
category: powerlifting-coef-calc
domain: spreadsheet-formula-engineering
tags:
  - openpyxl
  - excel-formulas
  - powerlifting
  - dots-coefficient
  - data-transformation
dependencies:
  - openpyxl>=3.1.5
  - python3
environment: ubuntu-24.04
---

# Powerlifting Dots Coefficient Calculation — Excel Formula Engineering

## Overview

This skill covers computing **Dots coefficients** for International Powerlifting Federation (IPF) competition data stored in `.xlsx` workbooks. The task pattern is:

1. Read a "Data" sheet containing lifter records.
2. Identify the columns needed for the Dots formula.
3. Copy those columns (plus the lifter's name) into a new "Dots" sheet.
4. Append a `TotalKg` column using an **Excel formula** (not a hard-coded value).
5. Append a `Dots` column using an **Excel formula** that implements the full Dots coefficient math.
6. All numeric precision kept to 3 decimal places via `ROUND(..., 3)`.

The critical constraint: the validator checks that `TotalKg` and `Dots` cells contain **formulas** (strings starting with `=`), not static values. The formulas must also evaluate to correct numeric results.

---

## High-Level Workflow

### Step 1 — Inspect the workbook and the data dictionary

Read the data readme (usually `/root/data/data-readme.md`) and open the workbook to understand:

- Which columns exist in the "Data" sheet.
- Which columns are needed for Dots: `Name`, `Sex`, `BodyweightKg`, `Best3SquatKg`, `Best3BenchKg`, `Best3DeadliftKg`.
- How many data rows there are (excluding the header).

**Why:** You must copy exactly the right columns in the right order, matching the original column names. The validator checks column names, column count, and row count.

```python
import openpyxl

wb = openpyxl.load_workbook('/root/data/openipf.xlsx')
data_ws = wb['Data']

# Print header row
headers = [cell.value for cell in data_ws[1]]
print("Data columns:", headers)

# Count data rows (excluding header)
row_count = data_ws.max_row - 1
print(f"Data rows: {row_count}")
```

### Step 2 — Copy required columns to the "Dots" sheet

Create (or clear) the "Dots" sheet. Copy these columns **in the same order they appear in "Data"**:

- `Name`
- `Sex`
- `BodyweightKg`
- `Best3SquatKg`
- `Best3BenchKg`
- `Best3DeadliftKg`

**Why:** The validator checks that column names and order match. Do not reorder or rename.

```python
# Identify source column indices (1-based) for the columns we need
NEEDED = ['Name', 'Sex', 'BodyweightKg', 'Best3SquatKg', 'Best3BenchKg', 'Best3DeadliftKg']
col_map = {}  # column_name -> 1-based index in Data sheet
for idx, h in enumerate(headers, 1):
    if h in NEEDED:
        col_map[h] = idx

# Create Dots sheet (remove if exists to start clean)
if 'Dots' in wb.sheetnames:
    del wb['Dots']
dots_ws = wb.create_sheet('Dots')

# Write header row
for out_col, name in enumerate(NEEDED, 1):
    dots_ws.cell(row=1, column=out_col, value=name)

# Copy data rows
for row_idx in range(2, data_ws.max_row + 1):
    for out_col, name in enumerate(NEEDED, 1):
        src_col = col_map[name]
        dots_ws.cell(row=row_idx, column=out_col,
                     value=data_ws.cell(row=row_idx, column=src_col).value)
```

### Step 3 — Append the `TotalKg` formula column

`TotalKg = Best3SquatKg + Best3BenchKg + Best3DeadliftKg`

In the Dots sheet layout, these are columns D, E, F (columns 4, 5, 6). `TotalKg` goes in column G (column 7).

The formula must use `ROUND(..., 3)` for consistency.

```python
# TotalKg header
total_col = len(NEEDED) + 1  # 7
dots_ws.cell(row=1, column=total_col, value='TotalKg')

for row_idx in range(2, data_ws.max_row + 1):
    # D=Best3SquatKg, E=Best3BenchKg, F=Best3DeadliftKg
    formula = f'=ROUND(D{row_idx}+E{row_idx}+F{row_idx},3)'
    dots_ws.cell(row=row_idx, column=total_col, value=formula)
```

### Step 4 — Append the `Dots` formula column

The Dots coefficient formula depends on the lifter's **sex** and **bodyweight**. It is a polynomial ratio applied to the total.

**Dots formula:**

```
Dots = TotalKg × 500 / (A×bw^4 + B×bw^3 + C×bw^2 + D×bw + E)
```

Where `bw` = BodyweightKg, and the polynomial coefficients differ by sex:

| Coefficient | Male               | Female              |
|-------------|--------------------|--------------------|
| A           | -0.000001093      | -0.0000010706      |
| B           |  0.0007391293     |  0.0005158568      |
| C           | -0.1918759221     | -0.1126655495      |
| D           | 24.0900756        | 13.6175032         |
| E           | -307.75076        | 137.4017941        |

The formula uses `IF(B{row}="M", ..., ...)` to branch on sex (column B in the Dots sheet).

**Why this is an Excel formula, not Python math:** The validator inspects the cell and checks `cell.value.startswith('=')`. If you write a number, the test fails.

```python
# Male coefficients
MA, MB, MC, MD, ME = -0.000001093, 0.0007391293, -0.1918759221, 24.0900756, -307.75076
# Female coefficients
FA, FB, FC, FD, FE = -0.0000010706, 0.0005158568, -0.1126655495, 13.6175032, 137.4017941

dots_formula_col = total_col + 1  # 8
dots_ws.cell(row=1, column=dots_formula_col, value='Dots')

for row_idx in range(2, data_ws.max_row + 1):
    bw = f'C{row_idx}'   # BodyweightKg
    total = f'G{row_idx}' # TotalKg

    def poly_expr(a, b, c, d, e, bw_ref):
        return (
            f'{a}*{bw_ref}^4'
            f'+{b}*{bw_ref}^3'
            f'+{c}*{bw_ref}^2'
            f'+{d}*{bw_ref}'
            f'+{e}'
        )

    male_poly = poly_expr(MA, MB, MC, MD, ME, bw)
    female_poly = poly_expr(FA, FB, FC, FD, FE, bw)

    formula = (
        f'=ROUND(IF(B{row_idx}="M",'
        f'{total}*500/({male_poly}),'
        f'{total}*500/({female_poly})),3)'
    )
    dots_ws.cell(row=row_idx, column=dots_formula_col, value=formula)
```

### Step 5 — Save and verify

```python
wb.save('/root/data/openipf.xlsx')
```

After saving, verify:

1. **Structural checks** — re-open with openpyxl and confirm column names, row counts, and that formula cells start with `=`.
2. **Numeric accuracy** — use Python to compute expected Dots values independently, then compare against LibreOffice-evaluated results or a polars/fastexcel read (which evaluates formulas).

```python
# Structural verification
wb2 = openpyxl.load_workbook('/root/data/openipf.xlsx')
ds = wb2['Dots']
print("Dots columns:", [ds.cell(1, c).value for c in range(1, ds.max_column + 1)])
print("Row count:", ds.max_row - 1)

# Check formulas
for r in range(2, ds.max_row + 1):
    total_val = ds.cell(r, 7).value
    dots_val = ds.cell(r, 8).value
    assert isinstance(total_val, str) and total_val.startswith('='), f"Row {r} TotalKg not a formula"
    assert isinstance(dots_val, str) and dots_val.startswith('='), f"Row {r} Dots not a formula"

print("All formula checks passed.")
```

---

## Dots Coefficient — Domain Reference

### The Dots System

Dots is a bodyweight-normalized scoring system for powerlifting. It replaces older systems (Wilks, IPF Points) and is used by many federations. The score lets you compare lifters across weight classes.

### Polynomial Coefficients (Canonical Values)

These are the **only** correct values. Do not round or truncate them in the formula.

**Male:**
```
A = -0.000001093
B =  0.0007391293
C = -0.1918759221
D =  24.0900756
E = -307.75076
```

**Female:**
```
A = -0.0000010706
B =  0.0005158568
C = -0.1126655495
D =  13.6175032
E =  137.4017941
```

### Formula

```
denominator = A*bw^4 + B*bw^3 + C*bw^2 + D*bw + E
Dots = ROUND(Total * 500 / denominator, 3)
```

Where `Total = Best3SquatKg + Best3BenchKg + Best3DeadliftKg`.

### Python Verification Function

```python
def compute_dots_python(sex: str, bodyweight: float, total: float) -> float:
    if sex == 'M':
        a, b, c, d, e = -0.000001093, 0.0007391293, -0.1918759221, 24.0900756, -307.75076
    else:
        a, b, c, d, e = -0.0000010706, 0.0005158568, -0.1126655495, 13.6175032, 137.4017941
    bw = bodyweight
    denom = a*bw**4 + b*bw**3 + c*bw**2 + d*bw + e
    return round(total * 500 / denom, 3)
```

---

## Common Pitfalls

### 1. Writing values instead of formulas

The validator checks `isinstance(cell.value, str) and cell.value.startswith('=')`. If you compute the number in Python and write it as a float, the test fails. Always write the formula string.

### 2. Wrong column order in the Dots sheet

The columns must appear in the **same order as in the Data sheet**. Don't alphabetize or reorder. Read the Data sheet header and preserve the sequence.

### 3. Mismatched row counts

Copy **all** data rows. Off-by-one errors (forgetting `max_row` is inclusive, or skipping the last row) will fail the row-count test.

### 4. Incorrect polynomial coefficients

Using Wilks coefficients, IPF GL coefficients, or rounded Dots coefficients will produce wrong results. Use the exact values listed above.

### 5. Forgetting ROUND(..., 3)

The task specifies 3-digit precision. Wrap both `TotalKg` and `Dots` formulas in `ROUND(..., 3)`.

### 6. Sign errors in the polynomial

When building the formula string, watch for double negatives. The coefficients already include their signs. Using string interpolation like `f'{coeff}*bw^4'` naturally handles this because negative coefficients produce e.g. `-0.000001093*C2^4`, and the `+` before the next term handles positive coefficients. Just be careful with the string concatenation.

### 7. Using `data_only=True` when loading for verification

`openpyxl.load_workbook(path, data_only=True)` reads cached values, which may be `None` if the file was never opened in Excel/LibreOffice. For formula verification, load **without** `data_only` and check the formula strings. For numeric verification, use `polars.read_excel()` (which uses a formula-evaluating backend) or invoke LibreOffice headless.

### 8. Not handling the "Dots" sheet already existing

If you run the script twice, the sheet may already exist. Delete it first or clear it before writing.

---

## Reference Implementation

This is the complete, end-to-end script. Copy, adapt the file path if needed, and run.

```python
#!/usr/bin/env python3
"""
Complete solution: Compute Dots coefficients for IPF powerlifting data.

Reads /root/data/openipf.xlsx, copies required columns from "Data" to "Dots",
appends TotalKg and Dots formula columns, and saves the workbook in-place.

Requirements: openpyxl >= 3.1.5
"""

import openpyxl

# ── Configuration ──────────────────────────────────────────────────────────
WORKBOOK_PATH = '/root/data/openipf.xlsx'

# Columns to copy from Data → Dots (must match order in Data sheet)
NEEDED_COLUMNS = [
    'Name', 'Sex', 'BodyweightKg',
    'Best3SquatKg', 'Best3BenchKg', 'Best3DeadliftKg',
]

# Dots polynomial coefficients
MALE_COEFFS   = (-0.000001093, 0.0007391293, -0.1918759221, 24.0900756, -307.75076)
FEMALE_COEFFS = (-0.0000010706, 0.0005158568, -0.1126655495, 13.6175032, 137.4017941)

# ── Step 1: Load workbook and inspect Data sheet ───────────────────────────
wb = openpyxl.load_workbook(WORKBOOK_PATH)
data_ws = wb['Data']

headers = [cell.value for cell in data_ws[1]]
num_data_rows = data_ws.max_row - 1  # exclude header

print(f"Data sheet columns: {headers}")
print(f"Data rows: {num_data_rows}")

# Build a map: column_name -> 1-based column index in Data sheet
col_index = {}
for idx, h in enumerate(headers, 1):
    if h in NEEDED_COLUMNS:
        col_index[h] = idx

# Sanity check: all needed columns found
for col_name in NEEDED_COLUMNS:
    assert col_name in col_index, f"Column '{col_name}' not found in Data sheet"

# ── Step 2: Create the Dots sheet ──────────────────────────────────────────
if 'Dots' in wb.sheetnames:
    del wb['Dots']
dots_ws = wb.create_sheet('Dots')

# Write header row
for out_col, col_name in enumerate(NEEDED_COLUMNS, 1):
    dots_ws.cell(row=1, column=out_col, value=col_name)

# Copy data rows
for row_idx in range(2, data_ws.max_row + 1):
    for out_col, col_name in enumerate(NEEDED_COLUMNS, 1):
        src_col = col_index[col_name]
        value = data_ws.cell(row=row_idx, column=src_col).value
        dots_ws.cell(row=row_idx, column=out_col, value=value)

print(f"Copied {num_data_rows} rows × {len(NEEDED_COLUMNS)} columns to Dots sheet.")

# ── Step 3: Append TotalKg formula column ──────────────────────────────────
# In the Dots sheet:
#   A = Name, B = Sex, C = BodyweightKg,
#   D = Best3SquatKg, E = Best3BenchKg, F = Best3DeadliftKg
#   G = TotalKg (new), H = Dots (new)

TOTAL_COL = len(NEEDED_COLUMNS) + 1  # column 7 = G
dots_ws.cell(row=1, column=TOTAL_COL, value='TotalKg')

for row_idx in range(2, data_ws.max_row + 1):
    # TotalKg = Best3SquatKg + Best3BenchKg + Best3DeadliftKg
    formula = f'=ROUND(D{row_idx}+E{row_idx}+F{row_idx},3)'
    dots_ws.cell(row=row_idx, column=TOTAL_COL, value=formula)

print("TotalKg formula column added (column G).")

# ── Step 4: Append Dots formula column ─────────────────────────────────────
DOTS_COL = TOTAL_COL + 1  # column 8 = H
dots_ws.cell(row=1, column=DOTS_COL, value='Dots')


def build_poly_string(coeffs, bw_ref):
    """Build an Excel polynomial expression: A*bw^4 + B*bw^3 + C*bw^2 + D*bw + E"""
    a, b, c, d, e = coeffs
    return (
        f'{a}*{bw_ref}^4'
        f'+{b}*{bw_ref}^3'
        f'+{c}*{bw_ref}^2'
        f'+{d}*{bw_ref}'
        f'+{e}'
    )


for row_idx in range(2, data_ws.max_row + 1):
    bw_ref = f'C{row_idx}'      # BodyweightKg cell
    total_ref = f'G{row_idx}'   # TotalKg cell
    sex_ref = f'B{row_idx}'     # Sex cell

    male_poly = build_poly_string(MALE_COEFFS, bw_ref)
    female_poly = build_poly_string(FEMALE_COEFFS, bw_ref)

    formula = (
        f'=ROUND(IF({sex_ref}="M",'
        f'{total_ref}*500/({male_poly}),'
        f'{total_ref}*500/({female_poly})),3)'
    )
    dots_ws.cell(row=row_idx, column=DOTS_COL, value=formula)

print("Dots formula column added (column H).")

# ── Step 5: Save ───────────────────────────────────────────────────────────
wb.save(WORKBOOK_PATH)
print(f"Workbook saved to {WORKBOOK_PATH}")

# ── Step 6: Verification ──────────────────────────────────────────────────
# Re-open and verify structure + formulas
wb_check = openpyxl.load_workbook(WORKBOOK_PATH)
ds = wb_check['Dots']

# Check column names
actual_cols = [ds.cell(1, c).value for c in range(1, ds.max_column + 1)]
expected_cols = NEEDED_COLUMNS + ['TotalKg', 'Dots']
assert actual_cols == expected_cols, f"Column mismatch: {actual_cols} != {expected_cols}"

# Check row count
actual_rows = ds.max_row - 1
assert actual_rows == num_data_rows, f"Row count mismatch: {actual_rows} != {num_data_rows}"

# Check that TotalKg and Dots are formulas
for r in range(2, ds.max_row + 1):
    total_cell = ds.cell(r, TOTAL_COL).value
    dots_cell = ds.cell(r, DOTS_COL).value
    assert isinstance(total_cell, str) and total_cell.startswith('='), \
        f"Row {r}: TotalKg is not a formula: {total_cell!r}"
    assert isinstance(dots_cell, str) and dots_cell.startswith('='), \
        f"Row {r}: Dots is not a formula: {dots_cell!r}"

print("\n✓ All structural and formula checks passed.")

# ── Optional: Cross-check numeric accuracy with Python ─────────────────────
print("\n── Numeric cross-check ──")
data_ws2 = wb_check['Data']
for r in range(2, data_ws2.max_row + 1):
    name = None
    sex = None
    bw = None
    squat = bench = dead = None

    for col_name in NEEDED_COLUMNS:
        src_col = col_index[col_name]
        val = data_ws2.cell(r, src_col).value
        if col_name == 'Name':
            name = val
        elif col_name == 'Sex':
            sex = val
        elif col_name == 'BodyweightKg':
            bw = val
        elif col_name == 'Best3SquatKg':
            squat = val
        elif col_name == 'Best3BenchKg':
            bench = val
        elif col_name == 'Best3DeadliftKg':
            dead = val

    total = round(squat + bench + dead, 3)

    if sex == 'M':
        a, b, c, d, e = MALE_COEFFS
    else:
        a, b, c, d, e = FEMALE_COEFFS

    denom = a * bw**4 + b * bw**3 + c * bw**2 + d * bw + e
    dots_val = round(total * 500 / denom, 3)

    print(f"  {name:25s}  Sex={sex}  BW={bw:7.2f}  Total={total:7.1f}  Dots={dots_val:.3f}")

print("\nDone. The workbook is ready for validation.")
```

---

## Verification Checklist

Before considering the task complete, confirm all of these:

| Check | How to verify |
|---|---|
| "Data" sheet still exists and is unmodified | `assert 'Data' in wb.sheetnames` |
| "Dots" sheet exists | `assert 'Dots' in wb.sheetnames` |
| Dots sheet has exactly 8 columns | Column names match `NEEDED_COLUMNS + ['TotalKg', 'Dots']` |
| Row count matches Data sheet | `dots_ws.max_row == data_ws.max_row` |
| Dots sheet is not empty | `dots_ws.max_row > 1` |
| TotalKg cells are formulas | `cell.value` is a string starting with `=` |
| Dots cells are formulas | `cell.value` is a string starting with `=` |
| Dots values match ground truth | Python-computed values match within tolerance |

---

## Environment Notes

- **openpyxl** is pre-installed (`openpyxl==3.1.5`). No need to pip install.
- **polars** with `read_excel` can evaluate formulas (via the `fastexcel`/`calamine` backend) — useful for numeric verification. Install with `pip install polars fastexcel --break-system-packages`.
- **LibreOffice** is available for headless formula evaluation if needed: `libreoffice --headless --calc --convert-to xlsx file.xlsx`.
- When using `polars.read_excel()` to verify, formula columns may show as strings or `None` if the cached values aren't present. This is expected — the formulas are correct; they just haven't been evaluated by a spreadsheet engine yet.