---
skill_id: protein-expression-analysis
display_name: Protein Expression Analysis in Excel (openpyxl)
version: 1.0.0
task_category: protein-expression-analysis
tags:
  - proteomics
  - excel
  - openpyxl
  - fold-change
  - statistics
  - cancer-cell-lines
description: >
  Procedural skill for populating an Excel workbook with protein expression
  lookups, group statistics, and fold-change calculations using Python/openpyxl.
  Covers two-way data lookup, control/treated group statistics, and log2
  fold-change computation — all written as evaluated numeric values (not formulas).
---

# Protein Expression Analysis in Excel (openpyxl)

## Overview

You will receive an Excel workbook (`protein_expression.xlsx`) with two sheets:
- **Data**: raw expression matrix — proteins as rows, samples as columns, with
  sample metadata in a header row.
- **Task**: a structured worksheet where you must fill in expression values,
  group statistics, and fold-change results.

The data is log2-transformed quantitative proteomics data. All computed values
must be written as **evaluated numeric floats**, never as Excel formula strings.
openpyxl does not evaluate formulas — tests read back raw cell values.

---

## Critical Rule: Never Write Formula Strings

```python
# ❌ WRONG — openpyxl stores this as a string, not a number
ws.cell(row, col).value = "=INDEX(Data!A:A, MATCH(...))"

# ✅ CORRECT — compute in Python, write the float
ws.cell(row, col).value = 0.3457
```

This is the single most common failure mode. Always compute values in Python
and write numeric results.

---

## High-Level Workflow

### Step 0 — Inspect the Workbook Structure

Before writing anything, map out the exact layout of both sheets.

```python
import openpyxl

wb = openpyxl.load_workbook('protein_expression.xlsx')
ws_task = wb['Task']
ws_data = wb['Data']

# Print Task sheet dimensions and non-empty cells to understand layout
print(f"Task sheet dimensions: {ws_task.dimensions}")
print(f"Data sheet dimensions: {ws_data.dimensions}")

# Scan Task sheet for non-empty cells to find anchor rows/cols
print("\n=== Task sheet non-empty cells (first 50) ===")
count = 0
for row in ws_task.iter_rows():
    for cell in row:
        if cell.value is not None:
            print(f"  {cell.coordinate}: {repr(cell.value)}")
            count += 1
            if count >= 50:
                break
    if count >= 50:
        break

# Scan Data sheet headers (first 3 rows, first 20 cols)
print("\n=== Data sheet header rows ===")
for row in ws_data.iter_rows(min_row=1, max_row=3, max_col=20):
    for cell in row:
        if cell.value is not None:
            print(f"  {cell.coordinate}: {repr(cell.value)}")
```

Key things to identify:
- Which row in the Task sheet contains sample names (e.g., row 10)
- Which row contains group labels "Control" / "Treated" (e.g., row 9)
- Which rows contain target protein IDs (e.g., rows 11–20)
- Which rows/cols are designated for expression values, stats, fold change
- In the Data sheet: which row has sample names, which column has protein IDs

### Step 1 — Build Index Structures from the Data Sheet

```python
import openpyxl

wb = openpyxl.load_workbook('protein_expression.xlsx')
ws_task = wb['Task']
ws_data = wb['Data']

# ── Locate the header row in Data sheet ──
# Sample names often have prefixes like "MDAMB468_BREAST_TenPx01"
# Find the row where sample names appear (look for a pattern or known anchor)
data_header_row = None
data_protein_col = None

for row in ws_data.iter_rows(min_row=1, max_row=5):
    for cell in row:
        # The protein ID column header is often blank or labeled "Gene"/"Protein"
        if cell.value and isinstance(cell.value, str) and 'gene' in cell.value.lower():
            data_header_row = cell.row
            data_protein_col = cell.column
            break
    if data_header_row:
        break

# Fallback: assume row 1 is header, col 1 is protein ID
if data_header_row is None:
    data_header_row = 1
    data_protein_col = 1

print(f"Data header row: {data_header_row}, protein col: {data_protein_col}")

# ── Build sample_name -> column_index map from Data sheet ──
sample_to_col = {}
header_row = ws_data[data_header_row]
for cell in header_row:
    if cell.value and isinstance(cell.value, str) and cell.column != data_protein_col:
        sample_to_col[cell.value.strip()] = cell.column

# ── Build protein_id -> row_index map from Data sheet ──
protein_to_row = {}
for row in ws_data.iter_rows(min_row=data_header_row + 1):
    cell = row[data_protein_col - 1]
    if cell.value:
        protein_to_row[str(cell.value).strip()] = cell.row

print(f"Indexed {len(sample_to_col)} samples, {len(protein_to_row)} proteins")
```

### Step 2 — Read Target Proteins and Sample Names from Task Sheet

```python
# ── Read target protein IDs from Task sheet (e.g., rows 11-20, col A=1) ──
EXPR_ROW_START = 11   # adjust based on inspection
EXPR_ROW_END   = 20
PROTEIN_COL    = 1    # column A

target_proteins = []
for r in range(EXPR_ROW_START, EXPR_ROW_END + 1):
    val = ws_task.cell(r, PROTEIN_COL).value
    target_proteins.append(str(val).strip() if val else None)

print(f"Target proteins: {target_proteins}")

# ── Read sample names from Task sheet (e.g., row 10, cols C-L = 3-12) ──
SAMPLE_ROW     = 10
SAMPLE_COL_START = 3   # column C
SAMPLE_COL_END   = 12  # column L

task_samples = []
for c in range(SAMPLE_COL_START, SAMPLE_COL_END + 1):
    val = ws_task.cell(SAMPLE_ROW, c).value
    task_samples.append(str(val).strip() if val else None)

print(f"Task samples: {task_samples}")

# ── Read group labels from Task sheet (e.g., row 9, cols C-L) ──
GROUP_ROW = 9
groups = []
for c in range(SAMPLE_COL_START, SAMPLE_COL_END + 1):
    val = ws_task.cell(GROUP_ROW, c).value
    groups.append(str(val).strip() if val else None)

print(f"Groups: {groups}")
```

### Step 3 — Populate Expression Values (Step 1 of Task)

```python
# ── Two-way lookup: protein x sample -> expression value ──
# Expression target: rows 11-20, cols C-L (rows EXPR_ROW_START to EXPR_ROW_END,
# cols SAMPLE_COL_START to SAMPLE_COL_END)

for i, protein_id in enumerate(target_proteins):
    expr_row = EXPR_ROW_START + i
    for j, sample_name in enumerate(task_samples):
        expr_col = SAMPLE_COL_START + j
        value = None

        if protein_id and sample_name:
            p_row = protein_to_row.get(protein_id)
            s_col = sample_to_col.get(sample_name)

            if p_row and s_col:
                value = ws_data.cell(p_row, s_col).value
            else:
                # Try partial match for sample names with prefixes
                if not s_col:
                    for key in sample_to_col:
                        if sample_name in key or key in sample_name:
                            s_col = sample_to_col[key]
                            break
                if p_row and s_col:
                    value = ws_data.cell(p_row, s_col).value

        ws_task.cell(expr_row, expr_col).value = value

    print(f"  Protein {protein_id} (row {expr_row}): written")
```

### Step 4 — Compute Group Statistics (Step 2 of Task)

The stats section typically occupies 4 rows:
- Row 24: Control Mean
- Row 25: Control StDev
- Row 26: Treated Mean
- Row 27: Treated StDev

Columns align with proteins (one column per protein, starting at col B or C).

```python
import statistics

# ── Determine which sample indices are Control vs Treated ──
ctrl_indices = [j for j, g in enumerate(groups) if g and 'control' in g.lower()]
trt_indices  = [j for j, g in enumerate(groups) if g and 'treat' in g.lower()]

print(f"Control sample indices: {ctrl_indices}")
print(f"Treated sample indices: {trt_indices}")

# ── Stats layout ──
# Per task instructions: stats go in rows 24-27, cols B-K (cols 2-11)
# One column per protein, matching the order of target_proteins
STATS_ROW_CTRL_MEAN = 24
STATS_ROW_CTRL_STD  = 25
STATS_ROW_TRT_MEAN  = 26
STATS_ROW_TRT_STD   = 27
STATS_COL_START     = 2   # column B — verify against actual sheet layout!

# Store computed stats for fold-change step
protein_stats = {}

for i, protein_id in enumerate(target_proteins):
    stats_col = STATS_COL_START + i
    expr_row  = EXPR_ROW_START + i

    # Read expression values already written in Step 3
    ctrl_vals = []
    trt_vals  = []
    for j in range(len(task_samples)):
        expr_col = SAMPLE_COL_START + j
        val = ws_task.cell(expr_row, expr_col).value
        if val is not None:
            try:
                fval = float(val)
                if j in ctrl_indices:
                    ctrl_vals.append(fval)
                elif j in trt_indices:
                    trt_vals.append(fval)
            except (TypeError, ValueError):
                pass

    ctrl_mean = statistics.mean(ctrl_vals) if ctrl_vals else None
    ctrl_std  = (statistics.stdev(ctrl_vals) if len(ctrl_vals) > 1
                 else (0.0 if len(ctrl_vals) == 1 else None))
    trt_mean  = statistics.mean(trt_vals) if trt_vals else None
    trt_std   = (statistics.stdev(trt_vals) if len(trt_vals) > 1
                 else (0.0 if len(trt_vals) == 1 else None))

    ws_task.cell(STATS_ROW_CTRL_MEAN, stats_col).value = ctrl_mean
    ws_task.cell(STATS_ROW_CTRL_STD,  stats_col).value = ctrl_std
    ws_task.cell(STATS_ROW_TRT_MEAN,  stats_col).value = trt_mean
    ws_task.cell(STATS_ROW_TRT_STD,   stats_col).value = trt_std

    protein_stats[protein_id] = {
        'ctrl_mean': ctrl_mean, 'ctrl_std': ctrl_std,
        'trt_mean': trt_mean,   'trt_std': trt_std
    }

    print(f"  {protein_id} col={stats_col}: "
          f"ctrl={ctrl_mean:.4f}±{ctrl_std:.4f}, "
          f"trt={trt_mean:.4f}±{trt_std:.4f}")
```

### Step 5 — Compute Fold Changes (Step 3 of Task)

Since data is log2-transformed:
- Log2 Fold Change = Treated Mean − Control Mean
- Fold Change = 2^(Log2FC)

```python
# ── Fold change layout ──
# Rows 32-41 (one per protein), cols C-D (cols 3-4)
# Col C = Fold Change (linear), Col D = Log2 Fold Change
FC_ROW_START = 32
FC_COL_FC    = 3   # column C
FC_COL_LOG2  = 4   # column D

for i, protein_id in enumerate(target_proteins):
    fc_row = FC_ROW_START + i
    stats  = protein_stats.get(protein_id, {})
    ctrl_mean = stats.get('ctrl_mean')
    trt_mean  = stats.get('trt_mean')

    if ctrl_mean is not None and trt_mean is not None:
        log2fc = trt_mean - ctrl_mean
        fc     = 2 ** log2fc
    else:
        log2fc = None
        fc     = None

    ws_task.cell(fc_row, FC_COL_FC).value    = fc
    ws_task.cell(fc_row, FC_COL_LOG2).value  = log2fc

    print(f"  {protein_id} row={fc_row}: FC={fc:.4f}, Log2FC={log2fc:.4f}")

wb.save('protein_expression.xlsx')
print("\nSaved successfully.")
```

### Step 6 — Verify Written Values

Always verify after saving by reloading the workbook.

```python
wb2    = openpyxl.load_workbook('protein_expression.xlsx')
ws2    = wb2['Task']

print("\n=== Expression values (rows 11-20, cols 3-12) ===")
for r in range(EXPR_ROW_START, EXPR_ROW_END + 1):
    vals = [ws2.cell(r, c).value for c in range(SAMPLE_COL_START, SAMPLE_COL_END + 1)]
    non_none = [v for v in vals if v is not None]
    print(f"  Row {r}: {len(non_none)}/10 filled, sample={vals[:3]}...")

print("\n=== Stats (rows 24-27, cols 2-11) ===")
for r in range(24, 28):
    vals = [(c, ws2.cell(r, c).value)
            for c in range(STATS_COL_START, STATS_COL_START + 10)
            if ws2.cell(r, c).value is not None]
    print(f"  Row {r}: {vals}")

print("\n=== Fold changes (rows 32-41, cols 3-4) ===")
for r in range(FC_ROW_START, FC_ROW_START + 10):
    fc   = ws2.cell(r, FC_COL_FC).value
    l2fc = ws2.cell(r, FC_COL_LOG2).value
    print(f"  Row {r}: FC={fc}, Log2FC={l2fc}")

# Sanity check: all expression cells should be numeric
print("\n=== Type check ===")
for r in range(EXPR_ROW_START, EXPR_ROW_END + 1):
    for c in range(SAMPLE_COL_START, SAMPLE_COL_END + 1):
        v = ws2.cell(r, c).value
        if v is not None and not isinstance(v, (int, float)):
            print(f"  WARNING: non-numeric at ({r},{c}): {repr(v)}")
```

---

## Complete Script Template

```python
#!/usr/bin/env python3
"""
Protein expression analysis: populate Task sheet with expression values,
group statistics, and fold-change calculations.
All values written as evaluated numeric floats (never formula strings).
"""
import openpyxl
import statistics

# ── Configuration (adjust after inspecting the workbook) ──
WORKBOOK_PATH    = 'protein_expression.xlsx'
SHEET_TASK       = 'Task'
SHEET_DATA       = 'Data'

# Task sheet layout (verify by inspection)
PROTEIN_COL      = 1    # col A: protein IDs in Task sheet
GROUP_ROW        = 9    # row with "Control"/"Treated" labels
SAMPLE_ROW       = 10   # row with sample names
EXPR_ROW_START   = 11   # first protein expression row
EXPR_ROW_END     = 20   # last protein expression row
SAMPLE_COL_START = 3    # col C: first sample column
SAMPLE_COL_END   = 12   # col L: last sample column

STATS_ROW_CTRL_MEAN = 24
STATS_ROW_CTRL_STD  = 25
STATS_ROW_TRT_MEAN  = 26
STATS_ROW_TRT_STD   = 27
STATS_COL_START     = 2   # col B: first stats column

FC_ROW_START = 32
FC_COL_FC    = 3   # col C: fold change
FC_COL_LOG2  = 4   # col D: log2 fold change

# Data sheet layout (verify by inspection)
DATA_HEADER_ROW  = 1
DATA_PROTEIN_COL = 1


def build_data_index(ws_data, header_row, protein_col):
    """Build protein->row and sample->col lookup dicts from Data sheet."""
    sample_to_col = {}
    for cell in ws_data[header_row]:
        if cell.value and cell.column != protein_col:
            sample_to_col[str(cell.value).strip()] = cell.column

    protein_to_row = {}
    for row in ws_data.iter_rows(min_row=header_row + 1):
        cell = row[protein_col - 1]
        if cell.value:
            protein_to_row[str(cell.value).strip()] = cell.row

    return sample_to_col, protein_to_row


def lookup_value(ws_data, protein_id, sample_name, protein_to_row, sample_to_col):
    """Look up a single expression value with partial-match fallback."""
    p_row = protein_to_row.get(protein_id)
    s_col = sample_to_col.get(sample_name)

    # Partial match fallback for sample names with prefixes
    if s_col is None:
        for key in sample_to_col:
            if sample_name in key or key in sample_name:
                s_col = sample_to_col[key]
                break

    if p_row and s_col:
        return ws_data.cell(p_row, s_col).value
    return None


def main():
    wb      = openpyxl.load_workbook(WORKBOOK_PATH)
    ws_task = wb[SHEET_TASK]
    ws_data = wb[SHEET_DATA]

    # Build lookup indices
    sample_to_col, protein_to_row = build_data_index(
        ws_data, DATA_HEADER_ROW, DATA_PROTEIN_COL
    )

    # Read target proteins and samples from Task sheet
    target_proteins = [
        str(ws_task.cell(r, PROTEIN_COL).value).strip()
        for r in range(EXPR_ROW_START, EXPR_ROW_END + 1)
        if ws_task.cell(r, PROTEIN_COL).value
    ]
    task_samples = [
        str(ws_task.cell(SAMPLE_ROW, c).value).strip()
        for c in range(SAMPLE_COL_START, SAMPLE_COL_END + 1)
    ]
    groups = [
        str(ws_task.cell(GROUP_ROW, c).value).strip()
        for c in range(SAMPLE_COL_START, SAMPLE_COL_END + 1)
    ]

    ctrl_indices = [j for j, g in enumerate(groups) if 'control' in g.lower()]
    trt_indices  = [j for j, g in enumerate(groups) if 'treat' in g.lower()]

    protein_stats = {}

    for i, protein_id in enumerate(target_proteins):
        expr_row  = EXPR_ROW_START + i
        stats_col = STATS_COL_START + i
        fc_row    = FC_ROW_START + i

        # Step 1: expression values
        ctrl_vals, trt_vals = [], []
        for j, sample_name in enumerate(task_samples):
            expr_col = SAMPLE_COL_START + j
            val = lookup_value(ws_data, protein_id, sample_name,
                               protein_to_row, sample_to_col)
            ws_task.cell(expr_row, expr_col).value = val
            if val is not None:
                try:
                    fval = float(val)
                    if j in ctrl_indices:
                        ctrl_vals.append(fval)
                    elif j in trt_indices:
                        trt_vals.append(fval)
                except (TypeError, ValueError):
                    pass

        # Step 2: group statistics
        ctrl_mean = statistics.mean(ctrl_vals) if ctrl_vals else None
        ctrl_std  = (statistics.stdev(ctrl_vals) if len(ctrl_vals) > 1
                     else (0.0 if len(ctrl_vals) == 1 else None))
        trt_mean  = statistics.mean(trt_vals) if trt_vals else None
        trt_std   = (statistics.stdev(trt_vals) if len(trt_vals) > 1
                     else (0.0 if len(trt_vals) == 1 else None))

        ws_task.cell(STATS_ROW_CTRL_MEAN, stats_col).value = ctrl_mean
        ws_task.cell(STATS_ROW_CTRL_STD,  stats_col).value = ctrl_std
        ws_task.cell(STATS_ROW_TRT_MEAN,  stats_col).value = trt_mean
        ws_task.cell(STATS_ROW_TRT_STD,   stats_col).value = trt_std

        protein_stats[protein_id] = {
            'ctrl_mean': ctrl_mean, 'trt_mean': trt_mean
        }

        # Step 3: fold change (log2-transformed data)
        if ctrl_mean is not None and trt_mean is not None:
            log2fc = trt_mean - ctrl_mean
            fc     = 2 ** log2fc
        else:
            log2fc = fc = None

        ws_task.cell(fc_row, FC_COL_FC).value   = fc
        ws_task.cell(fc_row, FC_COL_LOG2).value = log2fc

        print(f"  {protein_id}: ctrl={ctrl_mean:.4f}, trt={trt_mean:.4f}, "
              f"log2fc={log2fc:.4f}, fc={fc:.4f}")

    wb.save(WORKBOOK_PATH)
    print(f"\nSaved {WORKBOOK_PATH}")


if __name__ == '__main__':
    main()
```

---

## Common Pitfalls

### 1. Writing formula strings instead of numeric values
openpyxl does not evaluate Excel formulas. If you write `"=INDEX(...)"`, the
cell contains that string — not a number. Tests that read cell values will fail.
Always compute in Python and write floats.

### 2. Off-by-one column alignment for stats
The task description may say "columns B-K" but you might accidentally start at
column C. Always verify: if there are 10 proteins and stats start at col B
(index 2), the last protein's stats land in col K (index 11). Double-check
with the inspection step before writing.

### 3. Sample name prefix mismatch
Data sheet sample names often have prefixes like `MDAMB468_BREAST_TenPx01`
while the Task sheet may show just `TenPx01`. Implement partial-match fallback
(check if task sample name is a substring of data sample name or vice versa).

### 4. Assuming layout without inspecting
The task description's cell references (e.g., "rows 11-20, cols C-L") may not
match the actual workbook. Always run the inspection step first and confirm
anchor cells before writing.

### 5. Using `statistics.stdev` on a single-element list
`statistics.stdev` raises `StatisticsError` for n < 2. Guard with:
```python
std = statistics.stdev(vals) if len(vals) > 1 else (0.0 if len(vals) == 1 else None)
```

### 6. Not saving before verifying
Always call `wb.save(path)` before reloading to verify. Verification on the
in-memory workbook object will show the values you set, not what's on disk.

### 7. Forgetting to handle None expression values in stats
If a lookup returns `None` (protein/sample not found), skip it in the stats
computation. Passing `None` to `statistics.mean` raises a `TypeError`.

### 8. Modifying formatting or structure
The task explicitly forbids changing colors, fonts, or file format. Use
`openpyxl.load_workbook(path)` (not `data_only=True` for writing) and only
set `.value` on target cells. Do not touch cell styles.

---

## Environment Notes

- Python: `python3`
- Available: `openpyxl==3.1.5`, `statistics` (stdlib)
- Also available: `libreoffice`, `gnumeric` (for inspection/conversion if needed,
  but openpyxl is sufficient for read/write)
- Do not use `data_only=True` when opening for writing — it strips formula
  metadata and can corrupt the workbook on save.
- Use `openpyxl.load_workbook(path)` (default) for all read+write operations.
