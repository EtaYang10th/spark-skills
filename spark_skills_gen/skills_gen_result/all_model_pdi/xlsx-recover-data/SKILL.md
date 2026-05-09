---
title: "Recovering Missing Values in Multi-Sheet Excel Budget Workbooks"
category: xlsx-recover-data
domain: spreadsheet-data-recovery
tags:
  - openpyxl
  - excel
  - data-recovery
  - cross-sheet-relationships
  - budget-analysis
  - financial-formulas
---

# Recovering Missing Values in Multi-Sheet Excel Budget Workbooks

## Overview

This skill covers recovering missing values (typically marked with `"???"`) in Excel workbooks where multiple sheets contain interrelated financial data. The core challenge is identifying the mathematical relationships between sheets and using known values to back-compute unknowns. This pattern appears frequently in budget, accounting, and financial reporting workbooks where data is presented in multiple views (absolute values, percentages, growth rates, shares).

## High-Level Workflow

1. **Load and inventory the workbook** — Read every sheet, identify all cells containing the placeholder (`"???"`), and record their coordinates and sheet names.
2. **Map the data structure** — Understand what each sheet represents (raw budgets, year-over-year changes, percentage shares, growth summaries) and how rows/columns map to categories and fiscal years.
3. **Identify cross-sheet formulas** — Determine the mathematical relationship each sheet has to the base data sheet. Common relationships:
   - **YoY % Change**: `((current - previous) / previous) * 100`
   - **Share %**: `(category / total) * 100`
   - **Row/Column Totals**: sum of constituent parts
   - **CAGR**: `((end / start) ^ (1/n) - 1) * 100`
   - **N-year Change**: `end_value - start_value`
   - **Average Budget**: `sum(values_over_range) / n`
4. **Establish a solve order** — Some missing values depend on other missing values. Build a dependency graph and solve leaf nodes first. Typically: base budget values → totals → derived sheets (YoY, shares, growth).
5. **Compute each missing value** — Use the identified formula and known surrounding values to solve for the unknown algebraically.
6. **Write results back** — Replace each `"???"` with the computed numeric value (int or float as appropriate) and save to the output file.
7. **Verify** — Check row sums, cross-sheet consistency, and that no `"???"` placeholders remain.

## Step 1: Load and Inventory the Workbook

```python
import openpyxl

def load_and_find_missing(filepath, placeholder="???"):
    """Load workbook and find all cells containing the placeholder."""
    wb = openpyxl.load_workbook(filepath)
    missing = {}  # {sheet_name: [(row, col, cell_ref), ...]}

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sheet_missing = []
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                                min_col=1, max_col=ws.max_column):
            for cell in row:
                if cell.value == placeholder:
                    col_letter = openpyxl.utils.get_column_letter(cell.column)
                    cell_ref = f"{col_letter}{cell.row}"
                    sheet_missing.append((cell.row, cell.column, cell_ref))
        if sheet_missing:
            missing[sheet_name] = sheet_missing

    return wb, missing
```

## Step 2: Dump All Sheet Data for Analysis

Before computing anything, dump every sheet into a Python-friendly structure. This avoids repeated file reads and makes formula derivation straightforward.

```python
def dump_sheet_data(ws):
    """Read an entire worksheet into a list of lists (row-major).
    Returns headers (first row) and data rows separately."""
    data = []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                            min_col=1, max_col=ws.max_column,
                            values_only=False):
        data.append([(cell.value, cell.row, cell.column) for cell in row])
    return data

def build_lookup(ws):
    """Build a dict: {(row, col): value} for quick access."""
    lookup = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                            min_col=1, max_col=ws.max_column):
        for cell in row:
            lookup[(cell.row, cell.column)] = cell.value
    return lookup
```

## Step 3: Identify Cross-Sheet Relationships

### Typical Multi-Sheet Budget Workbook Layout

| Sheet | Content | Relationship to Base |
|-------|---------|---------------------|
| Budget by Directorate | Absolute budget values (millions $) | **Base sheet** — rows = categories, columns = fiscal years, last column = totals |
| YoY Changes (%) | Year-over-year percentage changes | `((FY[n] - FY[n-1]) / FY[n-1]) * 100` |
| Directorate Shares (%) | Each category as % of yearly total | `(category_budget / total_budget) * 100` |
| Growth Analysis | Summary stats: 5yr change, avg budget, CAGR, FY2019 value | Derived from base sheet ranges |

### Formula Identification Strategy

```python
def verify_yoy_formula(current, previous):
    """YoY % change: ((current - previous) / previous) * 100"""
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 2)

def verify_share_formula(part, total):
    """Share %: (part / total) * 100"""
    if total == 0:
        return None
    return round((part / total) * 100, 2)

def verify_cagr(start_val, end_val, n_years):
    """CAGR: ((end/start)^(1/n) - 1) * 100"""
    if start_val <= 0:
        return None
    return round(((end_val / start_val) ** (1 / n_years) - 1) * 100, 2)

# To IDENTIFY which formula a sheet uses, pick 2-3 known (non-???) cells
# and test each formula against the base sheet. The one that matches is correct.
```

## Step 4: Solve Order — Dependency-Aware Computation

This is the most critical step. Missing values often form dependency chains:

```
Budget sheet missing value → needed for Total column → needed for Share % → needed for Growth stats
```

**Strategy**: Solve in waves.

```python
def determine_solve_order(missing_by_sheet, sheet_roles):
    """
    sheet_roles: dict mapping sheet name to role.
    Solve order: base_budget → totals_in_budget → yoy → shares → growth
    """
    order = []
    # Wave 1: Base budget values that can be derived from row/column sums
    #   e.g., if Total column is known, missing category = Total - sum(other categories)
    #   e.g., if all categories known, missing Total = sum(categories)
    order.append(("budget", "category_from_total"))
    order.append(("budget", "total_from_categories"))

    # Wave 2: Budget values derivable from YoY sheet
    #   If YoY% is known and previous year is known: current = previous * (1 + yoy/100)
    #   If YoY% is known and current year is known: previous = current / (1 + yoy/100)
    order.append(("budget", "from_yoy"))

    # Wave 3: Recompute totals after wave 2 fills
    order.append(("budget", "total_recompute"))

    # Wave 4: YoY values from now-complete budget data
    order.append(("yoy", "compute"))

    # Wave 5: Share values from now-complete budget data
    order.append(("shares", "compute"))

    # Wave 6: Growth analysis from now-complete budget data
    order.append(("growth", "compute"))

    return order
```

## Step 5: Computing Missing Values — Algebraic Inversions

### Recovering a budget value from a row total

```python
def recover_from_row_sum(lookup, row, category_cols, total_col, placeholder="???"):
    """If one category is missing but total is known, solve for it."""
    total = lookup.get((row, total_col))
    if total == placeholder or total is None:
        return None

    missing_col = None
    known_sum = 0
    for col in category_cols:
        val = lookup.get((row, col))
        if val == placeholder:
            if missing_col is not None:
                return None  # More than one unknown — can't solve from sum alone
            missing_col = col
        else:
            known_sum += float(val)

    if missing_col is not None:
        return missing_col, round(float(total) - known_sum)
    return None
```

### Recovering a budget value from YoY percentage

```python
def recover_from_yoy(budget_prev, yoy_pct):
    """current = previous * (1 + yoy/100)"""
    return round(budget_prev * (1 + yoy_pct / 100))

def recover_previous_from_yoy(budget_current, yoy_pct):
    """previous = current / (1 + yoy/100)"""
    return round(budget_current / (1 + yoy_pct / 100))
```

### Computing Growth Analysis values

```python
def compute_5yr_change(fy_end, fy_start):
    """Absolute change over 5 years."""
    return round(fy_end - fy_start)

def compute_avg_budget(values):
    """Average annual budget over a range of years.

    CRITICAL: Determine the correct year range by checking known columns first.
    The range might be FY2019-FY2024 (6 years) or FY2019-FY2023 (5 years).
    If a value in the range is itself a '???' that was computed AFTER the growth
    sheet was originally created, the average may exclude that year.
    """
    return round(sum(values) / len(values), 1)

def compute_cagr(start_val, end_val, n_years):
    """Compound Annual Growth Rate."""
    return round(((end_val / start_val) ** (1 / n_years) - 1) * 100, 2)
```

## Step 6: Write Results and Save

```python
def write_recovered_values(wb, recovered, output_path):
    """
    recovered: list of (sheet_name, cell_ref, value) tuples
    """
    for sheet_name, cell_ref, value in recovered:
        ws = wb[sheet_name]
        ws[cell_ref] = value

    wb.save(output_path)
    print(f"Saved recovered workbook to {output_path}")
```

## Step 7: Verification

```python
def verify_no_placeholders(filepath, placeholder="???"):
    """Ensure no ??? remain in the output file."""
    wb = openpyxl.load_workbook(filepath)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        for row in ws.iter_rows(values_only=True):
            for val in row:
                if val == placeholder:
                    raise AssertionError(
                        f"Placeholder still present in sheet '{sheet_name}'"
                    )
    print("✓ No placeholders remaining")

def verify_row_sums(filepath, budget_sheet_name, category_cols, total_col, data_rows):
    """Verify that row sums match the Total column."""
    wb = openpyxl.load_workbook(filepath)
    ws = wb[budget_sheet_name]
    for row_idx in data_rows:
        cat_sum = sum(
            float(ws.cell(row=row_idx, column=c).value or 0)
            for c in category_cols
        )
        total = float(ws.cell(row=row_idx, column=total_col).value or 0)
        if abs(cat_sum - total) > 1:
            raise AssertionError(
                f"Row {row_idx}: sum={cat_sum} != total={total}"
            )
    print("✓ Row sums consistent")

def verify_cross_sheet(filepath, budget_sheet, yoy_sheet, shares_sheet):
    """Spot-check YoY and Share values against budget data."""
    wb = openpyxl.load_workbook(filepath)
    # Pick a few known cells and verify the formula holds
    # (implementation depends on specific sheet layout)
    print("✓ Cross-sheet consistency verified")
```

## Common Pitfalls

### 1. Average Budget Year Range Ambiguity

The "Average Annual Budget" in a growth/summary sheet may use a **different year range** than you expect. The most dangerous case: if a fiscal year's value was itself missing (`???`) in the base sheet, the growth sheet may have been computed **before** that value was filled in, meaning the average **excludes** that year.

**How to detect**: Compute the average for known (non-missing) columns using both possible ranges (e.g., 5-year vs 6-year). Compare against known average values in the growth sheet. The range that matches known columns is the correct one. If one column's average only matches a shorter range, it's because that column had a missing base value.

```python
# Example: verify which year range the average uses
known_values_6yr = [v1, v2, v3, v4, v5, v6]  # FY2019-FY2024
known_values_5yr = [v1, v2, v3, v4, v5]       # FY2019-FY2023

avg_6 = sum(known_values_6yr) / 6
avg_5 = sum(known_values_5yr) / 5

# Compare against the known average in the growth sheet
# The one that matches (within rounding tolerance) is correct
```

### 2. Circular Dependencies Between Sheets

A budget value might be missing, and the YoY sheet also has a missing value for the same category/year. You cannot solve both simultaneously from those two sheets alone. Look for a **third constraint** — typically the row total or the share percentage — to break the cycle.

### 3. Integer vs Float Output

Budget values are typically integers (millions of dollars). Percentages and averages are typically floats rounded to 2 decimal places. CAGR values are floats rounded to 2 decimal places. Averages may be rounded to 1 decimal place. **Match the precision of existing known values in the same column/row.**

### 4. Rounding Accumulation

When back-computing from percentages, rounding errors can accumulate. Always round at the **final step**, not intermediate steps. Compare your computed value against what makes the row sum work, and prefer the value that maintains sum consistency.

```python
# BAD: round intermediate steps
intermediate = round(prev * (1 + yoy/100))  # rounding here
total_check = sum_others + intermediate       # may not match total

# GOOD: compute precisely, then verify against total
precise = prev * (1 + yoy/100)
from_total = total - sum_others
# If both are close, prefer from_total (it maintains row consistency)
if abs(precise - from_total) < 2:
    final_value = round(from_total)
else:
    final_value = round(precise)
```

### 5. Column/Row Index Mapping Errors

The most common bug is getting the mapping between column letters and column indices wrong, or between fiscal year labels and their column positions. **Always build an explicit mapping from headers.**

```python
def build_column_map(ws, header_row=1):
    """Map header labels to column indices."""
    col_map = {}
    for cell in ws[header_row]:
        if cell.value is not None:
            col_map[str(cell.value).strip()] = cell.column
    return col_map

def build_row_map(ws, label_col=1):
    """Map row labels to row indices."""
    row_map = {}
    for row in ws.iter_rows(min_col=label_col, max_col=label_col):
        cell = row[0]
        if cell.value is not None:
            row_map[str(cell.value).strip()] = cell.row
    return row_map
```

### 6. Not Verifying Before Saving

Always run verification checks (row sums, cross-sheet consistency, no remaining placeholders) **before** considering the task complete. A single wrong value can cascade into multiple test failures.

## Reference Implementation

This is a complete, end-to-end script that recovers missing values from a multi-sheet NASA budget workbook. Adapt the sheet names, column mappings, and formulas to match your specific workbook.

```python
#!/usr/bin/env python3
"""
Recover missing values (marked '???') in a multi-sheet Excel budget workbook.

Workbook structure (adapt as needed):
  - Sheet 1: "Budget by Directorate" — absolute budget values, rows=FY years, cols=directorates + Total
  - Sheet 2: "YoY Changes (%)" — year-over-year percentage changes
  - Sheet 3: "Directorate Shares (%)" — each directorate as % of yearly total
  - Sheet 4: "Growth Analysis" — summary stats (5yr change, avg budget, CAGR, FY2019 value)

Usage:
    python3 recover_budget.py nasa_budget_incomplete.xlsx nasa_budget_recovered.xlsx
"""

import sys
import openpyxl
from openpyxl.utils import get_column_letter, column_index_from_string

# ─── Configuration ───────────────────────────────────────────────────────────

INPUT_FILE = "nasa_budget_incomplete.xlsx"
OUTPUT_FILE = "nasa_budget_recovered.xlsx"
PLACEHOLDER = "???"

# ─── Helper Functions ────────────────────────────────────────────────────────

def load_workbook_data(filepath):
    """Load workbook and build lookup dicts for every sheet."""
    wb = openpyxl.load_workbook(filepath)
    lookups = {}
    for name in wb.sheetnames:
        ws = wb[name]
        lookup = {}
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                                min_col=1, max_col=ws.max_column):
            for cell in row:
                lookup[(cell.row, cell.column)] = cell.value
        lookups[name] = lookup
    return wb, lookups


def find_all_missing(lookups, placeholder=PLACEHOLDER):
    """Find all cells with the placeholder across all sheets."""
    missing = []
    for sheet_name, lookup in lookups.items():
        for (r, c), val in lookup.items():
            if val == placeholder:
                ref = f"{get_column_letter(c)}{r}"
                missing.append((sheet_name, r, c, ref))
    return missing


def get_val(lookup, row, col):
    """Get a numeric value from lookup, returning None if placeholder or missing."""
    v = lookup.get((row, col))
    if v is None or v == PLACEHOLDER:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def build_header_maps(ws):
    """Build column and row header maps from a worksheet."""
    col_map = {}
    for cell in ws[1]:
        if cell.value is not None:
            col_map[str(cell.value).strip()] = cell.column

    row_map = {}
    for row in ws.iter_rows(min_col=1, max_col=1):
        cell = row[0]
        if cell.value is not None:
            row_map[str(cell.value).strip()] = cell.row

    return col_map, row_map


# ─── Main Recovery Logic ─────────────────────────────────────────────────────

def recover(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    wb, lookups = load_workbook_data(input_file)

    # Print workbook structure for debugging
    print("Sheets:", wb.sheetnames)
    for name in wb.sheetnames:
        ws = wb[name]
        print(f"\n--- {name} ({ws.max_row}x{ws.max_column}) ---")
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 3),
                                values_only=True):
            print(row)

    # Find all missing cells
    missing = find_all_missing(lookups)
    print(f"\nTotal missing cells: {len(missing)}")
    for sheet, r, c, ref in missing:
        print(f"  {sheet} -> {ref}")

    # ── Step 1: Identify sheet roles ──
    # Adapt these names to match your workbook
    sheet_names = wb.sheetnames
    budget_sheet = sheet_names[0]   # e.g., "Budget by Directorate"
    yoy_sheet = sheet_names[1]      # e.g., "YoY Changes (%)"
    shares_sheet = sheet_names[2]   # e.g., "Directorate Shares (%)"
    growth_sheet = sheet_names[3]   # e.g., "Growth Analysis"

    budget_ws = wb[budget_sheet]
    yoy_ws = wb[yoy_sheet]
    shares_ws = wb[shares_sheet]
    growth_ws = wb[growth_sheet]

    # Build header maps for each sheet
    b_col_map, b_row_map = build_header_maps(budget_ws)
    y_col_map, y_row_map = build_header_maps(yoy_ws)
    s_col_map, s_row_map = build_header_maps(shares_ws)
    g_col_map, g_row_map = build_header_maps(growth_ws)

    print(f"\nBudget columns: {b_col_map}")
    print(f"Budget rows: {b_row_map}")

    b_lookup = lookups[budget_sheet]
    y_lookup = lookups[yoy_sheet]
    s_lookup = lookups[shares_sheet]
    g_lookup = lookups[growth_sheet]

    # ── Step 2: Identify category columns and total column in budget sheet ──
    # Typically: col 1 = row labels, cols 2..N-1 = directorates, col N = Total
    # Or: col 1 = labels, cols 2..N-1 = fiscal years, col N = something
    # Inspect headers to determine orientation

    # Determine the budget sheet orientation:
    # If columns are fiscal years (FY20XX), rows are directorates
    # If columns are directorates, rows are fiscal years
    first_col_headers = [str(budget_ws.cell(row=1, column=c).value or "")
                         for c in range(2, budget_ws.max_column + 1)]
    is_years_as_cols = any("FY" in h or "20" in h for h in first_col_headers)

    if is_years_as_cols:
        # Columns = fiscal years, rows = directorates
        # Category axis = rows, time axis = columns
        print("Layout: rows=directorates, cols=fiscal years")
    else:
        # Columns = directorates, rows = fiscal years
        print("Layout: rows=fiscal years, cols=directorates")

    # ── Step 3: Recover budget sheet values ──
    # Strategy: use row/column sums, YoY relationships, and cross-sheet data

    recovered = []  # list of (sheet_name, cell_ref, value)

    def set_val(sheet_name, row, col, value):
        """Set a value in both the lookup and the worksheet."""
        lookups[sheet_name][(row, col)] = value
        ref = f"{get_column_letter(col)}{row}"
        wb[sheet_name][ref] = value
        recovered.append((sheet_name, ref, value))
        print(f"  Recovered {sheet_name}!{ref} = {value}")

    # ── Iterative solving: repeat until no more progress ──
    max_iterations = 10
    for iteration in range(max_iterations):
        progress = False
        remaining = find_all_missing(lookups)
        if not remaining:
            break

        print(f"\n=== Iteration {iteration + 1}, {len(remaining)} missing ===")

        for sheet_name, r, c, ref in remaining:
            val = lookups[sheet_name].get((r, c))
            if val != PLACEHOLDER:
                continue  # Already solved

            ws = wb[sheet_name]

            if sheet_name == budget_sheet:
                # Try to recover from row sum (Total column or row)
                # Find the "Total" column
                total_col = None
                for header, col_idx in b_col_map.items():
                    if "total" in header.lower():
                        total_col = col_idx
                        break

                if total_col is not None and c != total_col:
                    # Missing a category value — solve from total minus others
                    total_val = get_val(b_lookup, r, total_col)
                    if total_val is not None:
                        other_sum = 0
                        can_solve = True
                        for hdr, ci in b_col_map.items():
                            if ci == 1 or ci == total_col or ci == c:
                                continue
                            v = get_val(b_lookup, r, ci)
                            if v is None:
                                can_solve = False
                                break
                            other_sum += v
                        if can_solve:
                            result = round(total_val - other_sum)
                            set_val(sheet_name, r, c, result)
                            progress = True
                            continue

                if total_col is not None and c == total_col:
                    # Missing the total — sum all categories
                    cat_sum = 0
                    can_solve = True
                    for hdr, ci in b_col_map.items():
                        if ci == 1 or ci == total_col:
                            continue
                        v = get_val(b_lookup, r, ci)
                        if v is None:
                            can_solve = False
                            break
                        cat_sum += v
                    if can_solve:
                        result = round(cat_sum)
                        set_val(sheet_name, r, c, result)
                        progress = True
                        continue

                # Try to recover from YoY sheet
                # Map budget (row, col) to YoY (row, col)
                # YoY sheets typically have same row/col structure
                # YoY[r][c] = ((Budget[r][c] - Budget[r][c-1]) / Budget[r][c-1]) * 100
                # So: Budget[r][c] = Budget[r][c-1] * (1 + YoY[r][c]/100)
                yoy_val = get_val(y_lookup, r, c)
                prev_budget = get_val(b_lookup, r, c - 1) if c > 2 else None
                if yoy_val is not None and prev_budget is not None:
                    result = round(prev_budget * (1 + yoy_val / 100))
                    set_val(sheet_name, r, c, result)
                    progress = True
                    continue

                # Try reverse: Budget[r][c-1] = Budget[r][c] / (1 + YoY[r][c]/100)
                next_budget = get_val(b_lookup, r, c + 1)
                yoy_next = get_val(y_lookup, r, c + 1)
                if next_budget is not None and yoy_next is not None:
                    result = round(next_budget / (1 + yoy_next / 100))
                    set_val(sheet_name, r, c, result)
                    progress = True
                    continue

            elif sheet_name == yoy_sheet:
                # YoY[r][c] = ((Budget[r][c] - Budget[r][c-1]) / Budget[r][c-1]) * 100
                curr = get_val(b_lookup, r, c)
                prev = get_val(b_lookup, r, c - 1) if c > 2 else None
                if curr is not None and prev is not None and prev != 0:
                    result = round(((curr - prev) / prev) * 100, 2)
                    set_val(sheet_name, r, c, result)
                    progress = True
                    continue

            elif sheet_name == shares_sheet:
                # Share[r][c] = (Budget[r][c] / Budget[total_row][c]) * 100
                # or Share[r][c] = (Budget[r][c] / Total_for_that_year) * 100
                budget_val = get_val(b_lookup, r, c)
                # Find total for this column in budget sheet
                total_col_budget = None
                for hdr, ci in b_col_map.items():
                    if "total" in hdr.lower():
                        total_col_budget = ci
                        break
                # The total for a given year-column is in the budget sheet
                # For shares, we need the total of all directorates for that year
                # This depends on orientation — find the total row
                total_row = None
                for hdr, ri in b_row_map.items():
                    if "total" in hdr.lower():
                        total_row = ri
                        break

                if total_row is not None:
                    year_total = get_val(b_lookup, total_row, c)
                else:
                    # If no total row, use total column with transposed logic
                    year_total = get_val(b_lookup, r, total_col_budget) if total_col_budget else None

                if budget_val is not None and year_total is not None and year_total != 0:
                    result = round((budget_val / year_total) * 100, 2)
                    set_val(sheet_name, r, c, result)
                    progress = True
                    continue

            elif sheet_name == growth_sheet:
                # Growth sheet has summary rows: 5yr change, avg budget, CAGR, FY2019 value
                # Identify which metric this row represents
                row_label = str(g_lookup.get((r, 1), "")).strip().lower()

                # Identify which directorate this column represents
                col_label = str(g_lookup.get((1, c), "")).strip()

                # Find the directorate's row in the budget sheet
                dir_row = b_row_map.get(col_label)
                if dir_row is None:
                    # Try partial match
                    for hdr, ri in b_row_map.items():
                        if col_label.lower() in hdr.lower() or hdr.lower() in col_label.lower():
                            dir_row = ri
                            break

                if dir_row is None:
                    continue

                # Gather all fiscal year values for this directorate
                fy_values = {}
                for hdr, ci in b_col_map.items():
                    if ci == 1:
                        continue
                    if "total" in hdr.lower():
                        continue
                    v = get_val(b_lookup, dir_row, ci)
                    if v is not None:
                        fy_values[hdr] = v

                sorted_years = sorted(fy_values.keys())

                if "change" in row_label or "5yr" in row_label or "5-yr" in row_label:
                    # 5-year change = last FY value - value from 5 years before last
                    if len(sorted_years) >= 2:
                        # Typically: FY2024 - FY2019 (or last - 5th from last)
                        end_val = fy_values[sorted_years[-1]]
                        # Find the year that is 5 years before the end
                        start_idx = max(0, len(sorted_years) - 6)
                        start_val = fy_