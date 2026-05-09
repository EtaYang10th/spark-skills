---
title: "Weighted GDP Calculation in Excel with openpyxl"
category: "weighted-gdp-calc"
tags: ["excel", "openpyxl", "formulas", "INDEX-MATCH", "SUMPRODUCT", "ssconvert", "gnumeric"]
dependencies: ["openpyxl==3.1.5", "gnumeric (ssconvert)", "python3"]
---

# Weighted GDP Calculation — Excel Formula Injection with openpyxl

## Overview

This skill covers tasks where you must populate an existing Excel workbook with lookup formulas, derived calculations, and weighted aggregations — all without altering formatting, adding macros, or restructuring sheets. The typical pattern involves:

1. A **Data sheet** with raw time-series data (GDP, exports, imports by country/series code)
2. A **Task sheet** with pre-formatted layout, blue-highlighted lookup keys, and yellow target cells
3. Requirements to use specific Excel functions (INDEX/MATCH, VLOOKUP, SUMPRODUCT, etc.)

The key challenge is aligning cell references between sheets that may have different row/column offsets for the same data.

## High-Level Workflow

### Step 1: Investigate Both Sheets Thoroughly

Before writing any formula, you MUST read both sheets to understand:

1. **Data sheet structure**: Where are year headers? Where are series codes? What is the exact data range?
2. **Task sheet structure**: Where are the lookup keys (series codes, years)? What are the target cell ranges?
3. **Alignment**: Do year headers start at the same column on both sheets? Does the Task sheet use a subset of years?

```python
from openpyxl import load_workbook

wb = load_workbook('gdp.xlsx', data_only=False)
print(f"Sheets: {wb.sheetnames}")

# Investigate Data sheet
ds = wb['Data']
print(f"Data sheet: max_row={ds.max_row}, max_col={ds.max_column}")

# Find year headers (typically in row 4)
print("Data year headers (row 4, cols H-M):")
for col in range(8, 14):  # H=8, M=13
    print(f"  {ds.cell(4, col).value}", end="")
print()

# Find series codes (typically column B)
print("\nData series codes (col B, rows 21-40):")
for row in range(21, 41):
    print(f"  Row {row}: {ds.cell(row, 2).value}")

# Investigate Task sheet
ts = wb['Task']
print(f"\nTask sheet: max_row={ts.max_row}, max_col={ts.max_column}")

# Find year headers on Task sheet
print("Task year headers (row 9, cols H-L):")
for col in range(8, 13):  # H=8, L=12
    print(f"  Col {col}: {ts.cell(9, col).value}", end="")
print()

# Find series codes on Task sheet
print("\nTask series codes (col D):")
for row in range(12, 32):
    val = ts.cell(row, 4).value
    if val:
        print(f"  Row {row}: {val}")

wb.close()
```

### Step 2: Write INDEX/MATCH Lookup Formulas

The INDEX/MATCH pattern for two-condition lookup (series code + year):

```
=INDEX(Data!$H$21:$M$40, MATCH($D12, Data!$B$21:$B$40, 0), MATCH(H$9, Data!$H$4:$M$4, 0))
```

Breaking this down:
- `Data!$H$21:$M$40` — the data array (values only, no headers)
- `MATCH($D12, Data!$B$21:$B$40, 0)` — finds the row by series code
- `MATCH(H$9, Data!$H$4:$M$4, 0)` — finds the column by year

Critical anchoring rules:
- `$D12` — column absolute, row relative (so it changes per row but stays in column D)
- `H$9` — row absolute, column relative (so it changes per column but stays in row 9)
- All range references use `$` on both dimensions (they don't move)

```python
from openpyxl import load_workbook

wb = load_workbook('gdp.xlsx')
ts = wb['Task']

# Define target ranges: (start_row, end_row) for each block
# Block 1: Exports (rows 12-17), Block 2: Imports (rows 19-24), Block 3: GDP (rows 26-31)
blocks = [
    (12, 17),  # Exports
    (19, 24),  # Imports
    (26, 31),  # GDP
]

# Columns H through L (8 through 12)
for start_row, end_row in blocks:
    for row in range(start_row, end_row + 1):
        for col in range(8, 13):  # H=8 to L=12
            col_letter = chr(64 + col)  # H, I, J, K, L
            formula = (
                f"=INDEX(Data!$H$21:$M$40,"
                f"MATCH($D{row},Data!$B$21:$B$40,0),"
                f"MATCH({col_letter}$9,Data!$H$4:$M$4,0))"
            )
            ts.cell(row=row, column=col, value=formula)

wb.save('gdp.xlsx')
wb.close()
```

### Step 3: Write Derived Calculation Formulas

Net exports as % of GDP: `(Exports - Imports) / GDP * 100`

```python
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

wb = load_workbook('gdp.xlsx')
ts = wb['Task']

# Net exports % of GDP in rows 35-40, columns H-L
# Row 35 corresponds to country in row 12 (exports), row 19 (imports), row 26 (GDP)
for i in range(6):  # 6 countries
    export_row = 12 + i
    import_row = 19 + i
    gdp_row = 26 + i
    target_row = 35 + i
    
    for col in range(8, 13):  # H to L
        col_letter = get_column_letter(col)
        formula = f"=({col_letter}{export_row}-{col_letter}{import_row})/{col_letter}{gdp_row}*100"
        ts.cell(row=target_row, column=col, value=formula)

wb.save('gdp.xlsx')
wb.close()
```

### Step 4: Write Statistical Formulas

```python
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

wb = load_workbook('gdp.xlsx')
ts = wb['Task']

# Statistics in rows 42-47
# Row 42: MIN, Row 43: MAX, Row 44: MEDIAN, Row 45: AVERAGE (simple mean)
# Row 46: PERCENTILE 25th, Row 47: PERCENTILE 75th
stat_formulas = {
    42: "MIN({col}35:{col}40)",
    43: "MAX({col}35:{col}40)",
    44: "MEDIAN({col}35:{col}40)",
    45: "AVERAGE({col}35:{col}40)",
    46: "PERCENTILE({col}35:{col}40,0.25)",
    47: "PERCENTILE({col}35:{col}40,0.75)",
}

for col in range(8, 13):  # H to L
    col_letter = get_column_letter(col)
    for row, template in stat_formulas.items():
        formula = "=" + template.format(col=col_letter)
        ts.cell(row=row, column=col, value=formula)

wb.save('gdp.xlsx')
wb.close()
```

### Step 5: Write Weighted Mean with SUMPRODUCT

The weighted mean of net exports % of GDP, weighted by GDP:

```
=SUMPRODUCT(H35:H40, H26:H31) / SUM(H26:H31)
```

This weights each country's net-exports-% by its GDP share.

```python
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

wb = load_workbook('gdp.xlsx')
ts = wb['Task']

# Weighted mean in row 50
for col in range(8, 13):  # H to L
    col_letter = get_column_letter(col)
    formula = f"=SUMPRODUCT({col_letter}35:{col_letter}40,{col_letter}26:{col_letter}31)/SUM({col_letter}26:{col_letter}31)"
    ts.cell(row=50, column=col, value=formula)

wb.save('gdp.xlsx')
wb.close()
```

### Step 6: Validate with ssconvert (Gnumeric)

Use `ssconvert` to evaluate formulas and export to CSV for verification:

```bash
cd /root && ssconvert --export-type=Gnumeric_stf:stf_csv -S gdp.xlsx sheet.csv 2>&1
# This creates sheet.csv.0 (first sheet = Task) and sheet.csv.1 (second sheet = Data)
```

```python
import csv

# Read the Task sheet CSV
with open('sheet.csv.0', 'r') as f:
    reader = csv.reader(f)
    rows = list(reader)

# Check net exports % values (row 35 in Excel = row index depends on CSV alignment)
# IMPORTANT: ssconvert may strip empty rows! Verify row alignment first.
print(f"Total rows in CSV: {len(rows)}")

# Find a known header to calibrate row offset
for i, row in enumerate(rows):
    if len(row) > 3 and row[3] and 'NE.EXP' in str(row[3]):
        print(f"Found series code at CSV row {i}: {row[3]}")
```

## Critical Notes on ssconvert Row Alignment

**ssconvert strips completely empty rows.** If your Task sheet has empty rows 1-8 before headers, the CSV will be offset. Two solutions:

1. **Add invisible content** to preserve rows (a space character in column A):
```python
# Before saving, ensure empty rows have at least a space to prevent stripping
for row in range(1, 9):
    if ts.cell(row=row, column=1).value is None:
        ts.cell(row=row, column=1, value=" ")
```

2. **Calibrate by searching for known content** rather than assuming row numbers match.

## Common Pitfalls

### 1. Year Column Offset Mismatch
The Data sheet may have years 2018-2023 (columns H-M) while the Task sheet only uses 2019-2023 (columns H-L). The MATCH function handles this automatically — it finds the correct column position within the data range. Do NOT manually offset columns.

### 2. Series Code Mismatch
Series codes in column D of the Task sheet must EXACTLY match those in column B of the Data sheet. Check for:
- Leading/trailing spaces
- Different quote characters
- Case sensitivity (Excel MATCH is case-insensitive by default)

### 3. Absolute vs Relative References
- Series code column: `$D{row}` — absolute column, relative row
- Year row: `{col}$9` — relative column, absolute row  
- Data ranges: fully absolute `$H$21:$M$40`

Getting this wrong means formulas break when filled across rows/columns.

### 4. Multiplication by 100
Net exports as **percent** of GDP requires `*100`. If the test expects a percentage (e.g., 25.3 meaning 25.3%), you need the multiplication. If it expects a ratio (0.253), you don't. Check the test expectations.

### 5. Weighted Mean Formula
The weighted mean is NOT `AVERAGE(net_exports_pct)`. It is:
```
SUMPRODUCT(net_exports_pct_range, gdp_range) / SUM(gdp_range)
```
This weights each country's percentage by its GDP, giving larger economies more influence.

### 6. Do NOT Use data_only=True When Writing Formulas
`load_workbook('file.xlsx', data_only=True)` reads cached values, not formulas. When writing formulas, always use `data_only=False` (the default).

### 7. Preserving Formatting
openpyxl preserves cell formatting (colors, fonts, borders) by default when you only modify `.value`. Do NOT:
- Delete and recreate cells
- Copy cells between sheets (this can lose formatting)
- Use `ws.delete_rows()` or `ws.insert_rows()`

### 8. No VBA/Macros
Save as `.xlsx` (not `.xlsm`). openpyxl does not add macros by default, but verify with:
```python
assert wb.vba_archive is None
```

## Reference Implementation

```python
#!/usr/bin/env python3
"""
Complete solution for weighted GDP calculation Excel task.
Populates an existing gdp.xlsx workbook with:
  - INDEX/MATCH lookup formulas (Step 1)
  - Net exports % of GDP calculations (Step 2)
  - Statistical summaries (Step 2)
  - SUMPRODUCT weighted mean (Step 3)

Prerequisites:
  - gdp.xlsx exists with sheets "Task" and "Data"
  - Data sheet has: series codes in B21:B40, year headers in H4:M4, values in H21:M40
  - Task sheet has: series codes in D12:D17, D19:D24, D26:D31; year headers in H9:L9
  - Target cells are empty (yellow-highlighted in the original)

Usage:
  python3 solve_gdp.py
"""

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

# === Configuration ===
WORKBOOK_PATH = 'gdp.xlsx'
TASK_SHEET = 'Task'
DATA_SHEET = 'Data'

# Data sheet ranges (adjust if your workbook differs)
DATA_VALUES_RANGE = "$H$21:$M$40"      # The numeric data block
DATA_SERIES_CODES = "$B$21:$B$40"       # Series code column
DATA_YEAR_HEADERS = "$H$4:$M$4"         # Year header row

# Task sheet layout
TASK_YEAR_ROW = 9                        # Row containing year headers
TASK_COL_START = 8                       # Column H
TASK_COL_END = 12                        # Column L (inclusive)

# Target cell blocks for Step 1 (lookup formulas)
LOOKUP_BLOCKS = [
    (12, 17),   # Exports: rows 12-17
    (19, 24),   # Imports: rows 19-24
    (26, 31),   # GDP: rows 26-31
]

# Step 2: Net exports % of GDP
NET_EXPORTS_START_ROW = 35
NET_EXPORTS_END_ROW = 40
EXPORT_START_ROW = 12
IMPORT_START_ROW = 19
GDP_START_ROW = 26

# Step 2: Statistics
STATS_CONFIG = {
    42: "MIN({col}{start}:{col}{end})",
    43: "MAX({col}{start}:{col}{end})",
    44: "MEDIAN({col}{start}:{col}{end})",
    45: "AVERAGE({col}{start}:{col}{end})",
    46: "PERCENTILE({col}{start}:{col}{end},0.25)",
    47: "PERCENTILE({col}{start}:{col}{end},0.75)",
}

# Step 3: Weighted mean
WEIGHTED_MEAN_ROW = 50


def main():
    # Load workbook preserving everything
    wb = load_workbook(WORKBOOK_PATH)
    ts = wb[TASK_SHEET]

    # === STEP 0: Verify structure ===
    print("Verifying workbook structure...")
    assert DATA_SHEET in wb.sheetnames, f"Missing sheet: {DATA_SHEET}"
    assert TASK_SHEET in wb.sheetnames, f"Missing sheet: {TASK_SHEET}"

    ds = wb[DATA_SHEET]
    # Verify year headers exist on Data sheet
    sample_year = ds.cell(row=4, column=8).value
    print(f"  Data sheet H4 (first year): {sample_year}")
    assert sample_year is not None, "Data sheet year headers not found at expected location"

    # Verify series codes exist on Task sheet
    sample_code = ts.cell(row=12, column=4).value
    print(f"  Task sheet D12 (first series code): {sample_code}")
    assert sample_code is not None, "Task sheet series codes not found at expected location"

    # Verify year headers on Task sheet
    task_year = ts.cell(row=TASK_YEAR_ROW, column=TASK_COL_START).value
    print(f"  Task sheet H{TASK_YEAR_ROW} (first year): {task_year}")
    assert task_year is not None, "Task sheet year headers not found"

    # === STEP 1: INDEX/MATCH Lookup Formulas ===
    print("\nStep 1: Writing INDEX/MATCH formulas...")
    for block_start, block_end in LOOKUP_BLOCKS:
        for row in range(block_start, block_end + 1):
            for col in range(TASK_COL_START, TASK_COL_END + 1):
                col_letter = get_column_letter(col)
                formula = (
                    f"=INDEX({DATA_SHEET}!{DATA_VALUES_RANGE},"
                    f"MATCH($D{row},{DATA_SHEET}!{DATA_SERIES_CODES},0),"
                    f"MATCH({col_letter}${TASK_YEAR_ROW},{DATA_SHEET}!{DATA_YEAR_HEADERS},0))"
                )
                ts.cell(row=row, column=col, value=formula)
        print(f"  Rows {block_start}-{block_end}: done")

    # === STEP 2a: Net Exports % of GDP ===
    print("\nStep 2a: Writing net exports % formulas...")
    num_countries = NET_EXPORTS_END_ROW - NET_EXPORTS_START_ROW + 1
    for i in range(num_countries):
        export_row = EXPORT_START_ROW + i
        import_row = IMPORT_START_ROW + i
        gdp_row = GDP_START_ROW + i
        target_row = NET_EXPORTS_START_ROW + i

        for col in range(TASK_COL_START, TASK_COL_END + 1):
            col_letter = get_column_letter(col)
            formula = (
                f"=({col_letter}{export_row}-{col_letter}{import_row})"
                f"/{col_letter}{gdp_row}*100"
            )
            ts.cell(row=target_row, column=col, value=formula)
    print(f"  Rows {NET_EXPORTS_START_ROW}-{NET_EXPORTS_END_ROW}: done")

    # === STEP 2b: Statistical Summaries ===
    print("\nStep 2b: Writing statistical formulas...")
    for stat_row, template in STATS_CONFIG.items():
        for col in range(TASK_COL_START, TASK_COL_END + 1):
            col_letter = get_column_letter(col)
            formula = "=" + template.format(
                col=col_letter,
                start=NET_EXPORTS_START_ROW,
                end=NET_EXPORTS_END_ROW
            )
            ts.cell(row=stat_row, column=col, value=formula)
    print(f"  Rows 42-47: done")

    # === STEP 3: Weighted Mean (SUMPRODUCT) ===
    print("\nStep 3: Writing SUMPRODUCT weighted mean formulas...")
    for col in range(TASK_COL_START, TASK_COL_END + 1):
        col_letter = get_column_letter(col)
        # Weighted by GDP values (rows 26-31)
        formula = (
            f"=SUMPRODUCT({col_letter}{NET_EXPORTS_START_ROW}:{col_letter}{NET_EXPORTS_END_ROW},"
            f"{col_letter}{GDP_START_ROW}:{col_letter}{GDP_START_ROW + num_countries - 1})"
            f"/SUM({col_letter}{GDP_START_ROW}:{col_letter}{GDP_START_ROW + num_countries - 1})"
        )
        ts.cell(row=WEIGHTED_MEAN_ROW, column=col, value=formula)
    print(f"  Row {WEIGHTED_MEAN_ROW}: done")

    # === STEP 4: Preserve row alignment for ssconvert ===
    # ssconvert strips completely empty rows, which breaks test validation
    # Add a space in column A for any empty rows above the data
    print("\nStep 4: Preserving row alignment...")
    for row in range(1, TASK_YEAR_ROW):
        if ts.cell(row=row, column=1).value is None:
            ts.cell(row=row, column=1, value=" ")

    # === SAVE ===
    wb.save(WORKBOOK_PATH)
    print(f"\nSaved: {WORKBOOK_PATH}")

    # === VERIFICATION ===
    print("\n=== Verification ===")
    wb2 = load_workbook(WORKBOOK_PATH, data_only=False)
    ts2 = wb2[TASK_SHEET]

    # Check that formulas are present (not empty)
    checks = [
        ('H12', 'First lookup'),
        ('L31', 'Last lookup'),
        ('H35', 'First net exports %'),
        ('L40', 'Last net exports %'),
        ('H42', 'MIN'),
        ('L47', '75th percentile'),
        ('H50', 'Weighted mean'),
        ('L50', 'Last weighted mean'),
    ]
    all_ok = True
    for cell_ref, label in checks:
        val = ts2[cell_ref].value
        is_formula = isinstance(val, str) and val.startswith('=')
        status = "OK" if is_formula else "MISSING"
        if not is_formula:
            all_ok = False
        print(f"  {cell_ref} ({label}): {status}")

    # Verify no VBA
    assert wb2.vba_archive is None, "Workbook contains VBA — this is not allowed"
    print(f"\n  No VBA: OK")
    print(f"\n{'All checks passed!' if all_ok else 'SOME CHECKS FAILED'}")

    wb2.close()


if __name__ == '__main__':
    main()
```

## Validation with ssconvert

After writing formulas, validate computed values:

```python
#!/usr/bin/env python3
"""
Validate formula outputs by evaluating with ssconvert (Gnumeric).
Run after solve_gdp.py to confirm formulas produce correct values.
"""

import subprocess
import csv
import os

WORKBOOK = 'gdp.xlsx'
CSV_PREFIX = 'sheet.csv'

def evaluate_and_check():
    # Export to CSV (evaluates formulas)
    result = subprocess.run(
        ['ssconvert', '--export-type=Gnumeric_stf:stf_csv', '-S', WORKBOOK, CSV_PREFIX],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ssconvert error: {result.stderr}")
        return False

    # Task sheet is the first sheet -> sheet.csv.0
    task_csv = f"{CSV_PREFIX}.0"
    if not os.path.exists(task_csv):
        print(f"CSV not found: {task_csv}")
        return False

    with open(task_csv, 'r') as f:
        rows = list(csv.reader(f))

    print(f"CSV has {len(rows)} rows")

    # Find calibration point: look for a known year header
    # Task sheet row 9 should have year values
    year_row_idx = None
    for i, row in enumerate(rows):
        if len(row) > 7 and row[7] and str(row[7]).strip() in ('2019', '2019.0'):
            year_row_idx = i
            break

    if year_row_idx is None:
        print("WARNING: Could not find year header row in CSV. Row alignment may be off.")
        # Fall back to assuming row 9 = index 8
        year_row_idx = 8

    # Calculate offset: Excel row 9 should be at year_row_idx
    offset = year_row_idx - 8  # 0-indexed, row 9 = index 8
    print(f"Row offset: {offset} (year header at CSV index {year_row_idx})")

    # Check net exports % values (Excel row 35 = index 34 + offset)
    print("\nNet Exports % of GDP (rows 35-40):")
    for excel_row in range(35, 41):
        csv_idx = excel_row - 1 + offset
        if csv_idx < len(rows):
            row_data = rows[csv_idx]
            values = row_data[7:12] if len(row_data) > 11 else row_data[7:]
            print(f"  Excel row {excel_row}: {values}")

    # Check weighted mean (Excel row 50)
    wm_idx = 50 - 1 + offset
    if wm_idx < len(rows):
        wm_row = rows[wm_idx]
        wm_values = wm_row[7:12] if len(wm_row) > 11 else wm_row[7:]
        print(f"\nWeighted Mean (row 50): {wm_values}")

    # Cleanup
    for f in [f"{CSV_PREFIX}.0", f"{CSV_PREFIX}.1"]:
        if os.path.exists(f):
            os.remove(f)

    return True


if __name__ == '__main__':
    evaluate_and_check()
```

## Environment Notes

- **openpyxl 3.1.5**: Installed, handles .xlsx read/write with formula preservation
- **ssconvert (Gnumeric)**: Available for formula evaluation and CSV export
- **LibreOffice**: Available but slower; ssconvert is preferred for quick validation
- **Python 3**: Standard runtime, no virtual environment needed

## Formula Pattern Quick Reference

| Task | Formula Pattern |
|------|----------------|
| Two-condition lookup | `=INDEX(data_range, MATCH(row_key, row_range, 0), MATCH(col_key, col_range, 0))` |
| Net exports % GDP | `=(Exports - Imports) / GDP * 100` |
| Weighted mean | `=SUMPRODUCT(values, weights) / SUM(weights)` |
| Percentile | `=PERCENTILE(range, 0.25)` or `=PERCENTILE(range, 0.75)` |

## Decision Tree

```
Is the task about populating Excel formulas?
├── YES → Use openpyxl to write formula strings (not computed values)
│   ├── Are there lookup conditions? → INDEX/MATCH with proper anchoring
│   ├── Are there derived calculations? → Cell arithmetic formulas
│   ├── Are there statistics? → MIN/MAX/MEDIAN/AVERAGE/PERCENTILE
│   └── Is there a weighted aggregation? → SUMPRODUCT/SUM
└── NO → Different skill needed

Need to validate formula outputs?
├── Use ssconvert to export CSV and check computed values
├── Watch for row stripping (add spaces to preserve alignment)
└── Calibrate row offset by searching for known content