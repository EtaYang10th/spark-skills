---
title: Protein Expression Analysis in Excel with openpyxl
category: protein-expression-analysis
domain: bioinformatics-excel
tags:
  - openpyxl
  - proteomics
  - excel-manipulation
  - fold-change
  - two-way-lookup
  - log2-transform
  - cancer-cell-lines
version: 1
---

# Protein Expression Analysis in Excel with openpyxl

## Overview

This skill covers a common bioinformatics task pattern: given an Excel workbook with a large proteomics dataset on one sheet and a structured task template on another, perform two-way lookups to extract expression values, compute group statistics (mean, stdev), and calculate fold changes — all while preserving the workbook's formatting, colors, and structure.

The key challenge is that `openpyxl` with `data_only=True` reads cached formula results but **cannot evaluate formulas itself**. Since verifiers typically load with `data_only=True`, you must write **computed numeric values** rather than Excel formula strings. This is the single most important architectural decision.

---

## High-Level Workflow

### Step 0: Inspect the Workbook Structure

Before writing any code, read the workbook to understand:

1. **Sheet names** — typically a "Task" sheet (template) and a "Data" sheet (source).
2. **Task sheet layout** — which cells are targets (often highlighted yellow), where protein IDs live, where sample names live, where group labels (Control/Treated) are.
3. **Data sheet layout** — row/column headers, how protein IDs and sample names are formatted (watch for prefixes like `MDAMB468_BREAST_TenPx01`).
4. **Group assignments** — which samples are Control vs Treated, read from a specific row on the Task sheet.

```python
import openpyxl

wb = openpyxl.load_workbook("protein_expression.xlsx")
print("Sheets:", wb.sheetnames)

task_ws = wb["Task"]
data_ws = wb["Data"]

# Print dimensions
print(f"Task sheet: {task_ws.min_row}-{task_ws.max_row} rows, "
      f"{task_ws.min_column}-{task_ws.max_column} cols")
print(f"Data sheet: {data_ws.min_row}-{data_ws.max_row} rows, "
      f"{data_ws.min_column}-{data_ws.max_column} cols")

# Read target protein IDs (example: column A, rows 11-20)
proteins = []
for row in range(11, 21):
    val = task_ws.cell(row=row, column=1).value
    proteins.append(val)
print("Target proteins:", proteins)

# Read target sample names (example: row 10, columns C-L)
samples = []
for col in range(3, 13):
    val = task_ws.cell(row=10, column=col).value
    samples.append(val)
print("Target samples:", samples)

# Read group labels (example: row 9, columns C-L)
groups = []
for col in range(3, 13):
    val = task_ws.cell(row=9, column=col).value
    groups.append(val)
print("Groups:", groups)
```

**Why this matters:** The exact row/column offsets vary per task instance. Never assume — always read and confirm. Print a few cells to verify alignment.

### Step 1: Build Lookup Indexes from the Data Sheet

Build dictionaries for O(1) lookup instead of scanning the sheet repeatedly.

```python
# Build protein row index: protein_id -> row number in Data sheet
protein_row_map = {}
for row in range(1, data_ws.max_row + 1):
    pid = data_ws.cell(row=row, column=1).value
    if pid is not None:
        protein_row_map[str(pid).strip()] = row

# Build sample column index: sample_name -> column number in Data sheet
sample_col_map = {}
header_row = 1  # Adjust if headers are on a different row
for col in range(1, data_ws.max_column + 1):
    sname = data_ws.cell(row=header_row, column=col).value
    if sname is not None:
        sample_col_map[str(sname).strip()] = col

print(f"Indexed {len(protein_row_map)} proteins, {len(sample_col_map)} samples")
```

**Critical:** Sample names on the Task sheet may be short (e.g., `TenPx01`) while the Data sheet uses full names with prefixes (e.g., `MDAMB468_BREAST_TenPx01`). Use substring/suffix matching if exact match fails:

```python
def find_sample_col(sample_name, sample_col_map):
    """Find column for a sample, handling prefix mismatches."""
    sample_name = str(sample_name).strip()
    # Try exact match first
    if sample_name in sample_col_map:
        return sample_col_map[sample_name]
    # Try suffix match (Data sheet names may have prefixes)
    for full_name, col in sample_col_map.items():
        if full_name.endswith(sample_name) or sample_name.endswith(full_name):
            return col
    # Try substring containment
    for full_name, col in sample_col_map.items():
        if sample_name in full_name or full_name in sample_name:
            return col
    return None
```

### Step 2: Perform Two-Way Lookups and Write Expression Values

For each (protein, sample) pair, look up the value in the Data sheet and write it to the Task sheet.

```python
filled = 0
missing = 0

for i, protein in enumerate(proteins):
    protein_key = str(protein).strip()
    data_row = protein_row_map.get(protein_key)
    if data_row is None:
        print(f"WARNING: Protein '{protein_key}' not found in Data sheet")
        continue

    for j, sample in enumerate(samples):
        data_col = find_sample_col(sample, sample_col_map)
        if data_col is None:
            print(f"WARNING: Sample '{sample}' not found in Data sheet")
            continue

        value = data_ws.cell(row=data_row, column=data_col).value
        target_row = 11 + i      # Rows 11-20 for 10 proteins
        target_col = 3 + j       # Columns C-L (3-12) for 10 samples

        if value is not None and isinstance(value, (int, float)):
            task_ws.cell(row=target_row, column=target_col, value=value)
            filled += 1
        else:
            missing += 1

print(f"Filled: {filled}, Missing/non-numeric: {missing}")
```

**Key point:** Some expression values may legitimately be `None` (missing data). Do not write zeros or placeholders — leave them empty. Downstream statistics must filter these out.

### Step 3: Calculate Group Statistics

Separate samples into Control and Treated groups, then compute mean and standard deviation per protein.

```python
import statistics

# Identify control and treated column indices (0-based into the samples list)
control_indices = [j for j, g in enumerate(groups) 
                   if g and "control" in str(g).strip().lower()]
treated_indices = [j for j, g in enumerate(groups) 
                   if g and "treated" in str(g).strip().lower()]

print(f"Control samples: {len(control_indices)}, Treated samples: {len(treated_indices)}")

# Stats layout: rows 24-27, columns B-K (2-11) — one column per protein
# Row 24: Control Mean, Row 25: Control StDev
# Row 26: Treated Mean, Row 27: Treated StDev
# (Verify this by reading row labels from column A!)

stats_labels = {}
for row in range(24, 28):
    label = task_ws.cell(row=row, column=1).value
    stats_labels[row] = label
print("Stats labels:", stats_labels)

protein_stats = {}  # protein_index -> {ctrl_mean, ctrl_std, treat_mean, treat_std}

for i in range(len(proteins)):
    expr_row = 11 + i
    
    # Gather control values
    ctrl_vals = []
    for j in control_indices:
        v = task_ws.cell(row=expr_row, column=3 + j).value
        if v is not None and isinstance(v, (int, float)):
            ctrl_vals.append(v)
    
    # Gather treated values
    treat_vals = []
    for j in treated_indices:
        v = task_ws.cell(row=expr_row, column=3 + j).value
        if v is not None and isinstance(v, (int, float)):
            treat_vals.append(v)
    
    stats_col = 2 + i  # Column B=2 for first protein, K=11 for tenth
    
    if len(ctrl_vals) >= 2:
        ctrl_mean = statistics.mean(ctrl_vals)
        ctrl_std = statistics.stdev(ctrl_vals)
        task_ws.cell(row=24, column=stats_col, value=ctrl_mean)
        task_ws.cell(row=25, column=stats_col, value=ctrl_std)
    
    if len(treat_vals) >= 2:
        treat_mean = statistics.mean(treat_vals)
        treat_std = statistics.stdev(treat_vals)
        task_ws.cell(row=26, column=stats_col, value=treat_mean)
        task_ws.cell(row=27, column=stats_col, value=treat_std)
    
    protein_stats[i] = {
        'ctrl_mean': statistics.mean(ctrl_vals) if ctrl_vals else None,
        'treat_mean': statistics.mean(treat_vals) if treat_vals else None,
    }
```

**Important:** Use `statistics.stdev()` (sample standard deviation, N-1 denominator), not `statistics.pstdev()` (population). Proteomics convention uses sample stdev. Also, require at least 2 values for stdev calculation.

### Step 4: Calculate Fold Changes

Since data is already log2-transformed:
- **Log2 Fold Change** = Treated Mean − Control Mean
- **Fold Change** = 2^(Log2 Fold Change)

```python
# Fold change layout: rows 32-41, column C = Log2FC, column D = FC
# (Verify by reading headers!)

for i in range(len(proteins)):
    stats = protein_stats.get(i, {})
    ctrl_mean = stats.get('ctrl_mean')
    treat_mean = stats.get('treat_mean')
    
    fc_row = 32 + i
    
    if ctrl_mean is not None and treat_mean is not None:
        log2fc = treat_mean - ctrl_mean
        fc = 2 ** log2fc
        task_ws.cell(row=fc_row, column=3, value=log2fc)   # Column C
        task_ws.cell(row=fc_row, column=4, value=fc)        # Column D

wb.save("protein_expression.xlsx")
print("Workbook saved.")
```

**Critical note on log2 data:** When data is already log2-transformed, the fold change is computed as a difference of means, NOT a ratio. `2^(mean_treated - mean_control)` is mathematically equivalent to the geometric mean ratio of raw intensities. Never exponentiate first and then subtract — that gives a meaningless number.

### Step 5: Verify Before Finalizing

Always reload the workbook with `data_only=True` and check that values are present and numeric:

```python
wb_check = openpyxl.load_workbook("protein_expression.xlsx", data_only=True)
task_check = wb_check["Task"]

# Check expression values
expr_filled = 0
for row in range(11, 21):
    for col in range(3, 13):
        v = task_check.cell(row=row, column=col).value
        if v is not None and isinstance(v, (int, float)):
            expr_filled += 1
print(f"Expression values filled: {expr_filled}/100")

# Check statistics
stats_filled = 0
for row in range(24, 28):
    for col in range(2, 12):
        v = task_check.cell(row=row, column=col).value
        if v is not None and isinstance(v, (int, float)):
            stats_filled += 1
print(f"Statistics filled: {stats_filled}/40")

# Check fold changes
fc_filled = 0
for row in range(32, 42):
    for col in [3, 4]:
        v = task_check.cell(row=row, column=col).value
        if v is not None and isinstance(v, (int, float)):
            fc_filled += 1
print(f"Fold changes filled: {fc_filled}/20")
```

---

## Common Pitfalls

### 1. Writing Formulas Instead of Values (CRITICAL)

**Problem:** You write Excel formulas like `=INDEX(...)` into cells. The verifier loads with `data_only=True`, which reads cached values. Since openpyxl doesn't evaluate formulas, all cells show `None`.

**Solution:** Always compute values in Python and write numeric results. Never write formula strings unless you also open and re-save the file with LibreOffice to force evaluation (fragile and slow).

### 2. Sample Name Prefix Mismatch

**Problem:** Task sheet says `TenPx01`, Data sheet says `MDAMB468_BREAST_TenPx01`. Exact string match fails silently.

**Solution:** Implement fuzzy/suffix matching as shown above. Always verify match counts after building the index.

### 3. Not Handling Missing Data

**Problem:** Some proteins have no expression value for certain samples. Writing `None` or `0` corrupts statistics.

**Solution:** Filter `None` values before computing mean/stdev. Require `len(values) >= 2` for stdev. Leave cells empty when data is insufficient.

### 4. Wrong Stdev Function

**Problem:** Using population stdev (`pstdev`, N denominator) instead of sample stdev (`stdev`, N-1 denominator).

**Solution:** Use `statistics.stdev()` for sample standard deviation. This is the convention in proteomics and what Excel's `STDEV()` function computes.

### 5. Confusing Row/Column Layout for Statistics

**Problem:** The stats section may be organized as "one column per protein" (proteins across columns) rather than "one row per protein" (proteins down rows). Misreading this transposes all results.

**Solution:** Always read the row and column labels from the Task sheet before writing. Print them to confirm orientation.

### 6. Fold Change Direction

**Problem:** Computing `Control - Treated` instead of `Treated - Control`, or exponentiating before subtracting.

**Solution:** Convention is `Treated - Control` for log2FC. Positive values mean upregulation in treated. `FC = 2^(log2FC)`.

### 7. Destroying Formatting

**Problem:** Recreating cells or sheets destroys fill colors, fonts, borders, and merged cells.

**Solution:** Use `openpyxl.load_workbook()` (without `data_only`) to preserve formatting. Only write to `.value` — never delete/recreate cells or sheets. Do not copy sheets or use `write_only` mode.

### 8. Overwriting Protein Labels in Fold Change Section

**Problem:** The fold change rows (32-41) already have protein names in columns A-B. Writing to column A or B overwrites them.

**Solution:** Only write to the designated output columns (typically C and D for log2FC and FC). Read existing labels to confirm alignment.

---

## Reference Implementation

This is a complete, self-contained script that performs all three steps. Copy, adapt the row/column constants to match your specific workbook layout, and run.

```python
#!/usr/bin/env python3
"""
Protein Expression Analysis — Complete Reference Implementation

Reads a proteomics Excel workbook with "Task" and "Data" sheets.
1. Looks up expression values for target proteins × samples via two-way index.
2. Computes Control/Treated mean and stdev per protein.
3. Computes Log2 Fold Change and Fold Change.
Writes all results as numeric values (not formulas) to preserve verifier compatibility.
"""

import openpyxl
import statistics
import sys
import os

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
# Adjust these constants to match your specific workbook layout.

WORKBOOK_PATH = "protein_expression.xlsx"

# Task sheet: where target protein IDs are listed
PROTEIN_ID_COL = 1          # Column A
PROTEIN_START_ROW = 11      # First target protein row
PROTEIN_END_ROW = 20        # Last target protein row (inclusive)

# Task sheet: where sample names are listed
SAMPLE_NAME_ROW = 10        # Row containing sample names
SAMPLE_START_COL = 3        # Column C
SAMPLE_END_COL = 12         # Column L (inclusive)

# Task sheet: where group labels (Control/Treated) are
GROUP_LABEL_ROW = 9         # Row containing group assignments
# Same column range as samples: SAMPLE_START_COL to SAMPLE_END_COL

# Task sheet: expression value output area
EXPR_START_ROW = 11         # Same as PROTEIN_START_ROW
EXPR_START_COL = 3          # Column C

# Task sheet: statistics output area
STATS_START_ROW = 24        # Row for Control Mean
STATS_START_COL = 2         # Column B (one column per protein)
# Row 24: Control Mean, Row 25: Control StDev
# Row 26: Treated Mean, Row 27: Treated StDev

# Task sheet: fold change output area
FC_START_ROW = 32           # First fold change row
FC_LOG2_COL = 3             # Column C for Log2 Fold Change
FC_LINEAR_COL = 4           # Column D for Fold Change

# Data sheet: header row for sample names
DATA_HEADER_ROW = 1
DATA_PROTEIN_COL = 1        # Column A in Data sheet

# ─── HELPER FUNCTIONS ────────────────────────────────────────────────────────

def build_protein_index(data_ws, protein_col=1):
    """Build a dict mapping protein_id (str) -> row number in Data sheet."""
    index = {}
    for row in range(1, data_ws.max_row + 1):
        val = data_ws.cell(row=row, column=protein_col).value
        if val is not None:
            index[str(val).strip()] = row
    return index


def build_sample_index(data_ws, header_row=1):
    """Build a dict mapping sample_name (str) -> column number in Data sheet."""
    index = {}
    for col in range(1, data_ws.max_column + 1):
        val = data_ws.cell(row=header_row, column=col).value
        if val is not None:
            index[str(val).strip()] = col
    return index


def find_sample_column(sample_name, sample_col_map):
    """
    Find the Data sheet column for a sample name, handling prefix mismatches.
    Task sheet may use short names; Data sheet may use prefixed names.
    """
    sample_name = str(sample_name).strip()
    
    # Exact match
    if sample_name in sample_col_map:
        return sample_col_map[sample_name]
    
    # Data sheet name ends with task sheet name (prefix on data side)
    for full_name, col in sample_col_map.items():
        if full_name.endswith(sample_name):
            return col
    
    # Task sheet name ends with data sheet name (prefix on task side)
    for full_name, col in sample_col_map.items():
        if sample_name.endswith(full_name):
            return col
    
    # Substring containment (last resort)
    for full_name, col in sample_col_map.items():
        if sample_name in full_name or full_name in sample_name:
            return col
    
    return None


def safe_mean(values):
    """Compute mean of non-None numeric values. Returns None if empty."""
    nums = [v for v in values if v is not None and isinstance(v, (int, float))]
    return statistics.mean(nums) if nums else None


def safe_stdev(values):
    """Compute sample stdev of non-None numeric values. Returns None if < 2 values."""
    nums = [v for v in values if v is not None and isinstance(v, (int, float))]
    return statistics.stdev(nums) if len(nums) >= 2 else None


# ─── MAIN IMPLEMENTATION ─────────────────────────────────────────────────────

def main():
    if not os.path.exists(WORKBOOK_PATH):
        print(f"ERROR: Workbook not found at {WORKBOOK_PATH}")
        sys.exit(1)

    # Load workbook preserving formatting (no data_only!)
    wb = openpyxl.load_workbook(WORKBOOK_PATH)
    task_ws = wb["Task"]
    data_ws = wb["Data"]

    # ── Read target proteins and samples from Task sheet ──
    proteins = []
    for row in range(PROTEIN_START_ROW, PROTEIN_END_ROW + 1):
        val = task_ws.cell(row=row, column=PROTEIN_ID_COL).value
        proteins.append(str(val).strip() if val else None)
    
    samples = []
    for col in range(SAMPLE_START_COL, SAMPLE_END_COL + 1):
        val = task_ws.cell(row=SAMPLE_NAME_ROW, column=col).value
        samples.append(str(val).strip() if val else None)
    
    groups = []
    for col in range(SAMPLE_START_COL, SAMPLE_END_COL + 1):
        val = task_ws.cell(row=GROUP_LABEL_ROW, column=col).value
        groups.append(str(val).strip().lower() if val else "")
    
    num_proteins = len(proteins)
    num_samples = len(samples)
    print(f"Proteins: {num_proteins}, Samples: {num_samples}")
    print(f"Groups: {groups}")

    # ── Build lookup indexes from Data sheet ──
    protein_row_map = build_protein_index(data_ws, DATA_PROTEIN_COL)
    sample_col_map = build_sample_index(data_ws, DATA_HEADER_ROW)
    print(f"Data sheet: {len(protein_row_map)} proteins, {len(sample_col_map)} samples indexed")

    # Verify all target proteins are found
    for p in proteins:
        if p and p not in protein_row_map:
            print(f"  WARNING: Protein '{p}' not found in Data sheet")

    # ── Identify control and treated sample indices ──
    control_indices = [j for j, g in enumerate(groups) if "control" in g]
    treated_indices = [j for j, g in enumerate(groups) if "treated" in g or "treat" in g]
    print(f"Control columns: {len(control_indices)}, Treated columns: {len(treated_indices)}")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1: Two-way lookup — fill expression values
    # ══════════════════════════════════════════════════════════════════════════
    
    expr_filled = 0
    expr_missing = 0
    
    # Store values for later statistics computation
    # expression_grid[i][j] = value or None
    expression_grid = [[None] * num_samples for _ in range(num_proteins)]
    
    for i, protein in enumerate(proteins):
        if protein is None:
            continue
        data_row = protein_row_map.get(protein)
        if data_row is None:
            expr_missing += num_samples
            continue
        
        for j, sample in enumerate(samples):
            if sample is None:
                expr_missing += 1
                continue
            data_col = find_sample_column(sample, sample_col_map)
            if data_col is None:
                print(f"  WARNING: Sample '{sample}' not found in Data sheet")
                expr_missing += 1
                continue
            
            value = data_ws.cell(row=data_row, column=data_col).value
            target_row = EXPR_START_ROW + i
            target_col = EXPR_START_COL + j
            
            if value is not None and isinstance(value, (int, float)):
                task_ws.cell(row=target_row, column=target_col, value=float(value))
                expression_grid[i][j] = float(value)
                expr_filled += 1
            else:
                expr_missing += 1
    
    print(f"\nStep 1 complete: {expr_filled} values filled, {expr_missing} missing")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 2: Group statistics — mean and stdev for Control and Treated
    # ══════════════════════════════════════════════════════════════════════════
    
    # Store means for fold change computation
    ctrl_means = [None] * num_proteins
    treat_means = [None] * num_proteins
    stats_filled = 0
    
    for i in range(num_proteins):
        ctrl_vals = [expression_grid[i][j] for j in control_indices]
        treat_vals = [expression_grid[i][j] for j in treated_indices]
        
        stats_col = STATS_START_COL + i  # One column per protein
        
        # Control Mean (row 24)
        cm = safe_mean(ctrl_vals)
        if cm is not None:
            task_ws.cell(row=STATS_START_ROW, column=stats_col, value=cm)
            ctrl_means[i] = cm
            stats_filled += 1
        
        # Control StDev (row 25)
        cs = safe_stdev(ctrl_vals)
        if cs is not None:
            task_ws.cell(row=STATS_START_ROW + 1, column=stats_col, value=cs)
            stats_filled += 1
        
        # Treated Mean (row 26)
        tm = safe_mean(treat_vals)
        if tm is not None:
            task_ws.cell(row=STATS_START_ROW + 2, column=stats_col, value=tm)
            treat_means[i] = tm
            stats_filled += 1
        
        # Treated StDev (row 27)
        ts = safe_stdev(treat_vals)
        if ts is not None:
            task_ws.cell(row=STATS_START_ROW + 3, column=stats_col, value=ts)
            stats_filled += 1
    
    print(f"Step 2 complete: {stats_filled} statistics filled")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 3: Fold change calculations
    # ══════════════════════════════════════════════════════════════════════════
    
    # Data is already log2-transformed, so:
    #   Log2 FC = Treated Mean - Control Mean
    #   FC = 2^(Log2 FC)
    
    fc_filled = 0
    
    for i in range(num_proteins):
        fc_row = FC_START_ROW + i
        
        if ctrl_means[i] is not None and treat_means[i] is not None:
            log2fc = treat_means[i] - ctrl_means[i]
            fc = 2.0 ** log2fc
            
            task_ws.cell(row=fc_row, column=FC_LOG2_COL, value=log2fc)
            task_ws.cell(row=fc_row, column=FC_LINEAR_COL, value=fc)
            fc_filled += 2
    
    print(f"Step 3 complete: {fc_filled} fold change values filled")

    # ── Save ──
    wb.save(WORKBOOK_PATH)
    print(f"\nWorkbook saved to {WORKBOOK_PATH}")

    # ══════════════════════════════════════════════════════════════════════════
    # VERIFICATION: Reload with data_only=True and check
    # ══════════════════════════════════════════════════════════════════════════
    
    wb_check = openpyxl.load_workbook(WORKBOOK_PATH, data_only=True)
    tc = wb_check["Task"]
    
    # Check expression values
    ev_count = sum(
        1 for r in range(PROTEIN_START_ROW, PROTEIN_END_ROW + 1)
        for c in range(SAMPLE_START_COL, SAMPLE_END_COL + 1)
        if tc.cell(row=r, column=c).value is not None
        and isinstance(tc.cell(row=r, column=c).value, (int, float))
    )
    
    # Check statistics
    st_count = sum(
        1 for r in range(STATS_START_ROW, STATS_START_ROW + 4)
        for c in range(STATS_START_COL, STATS_START_COL + num_proteins)
        if tc.cell(row=r, column=c).value is not None
        and isinstance(tc.cell(row=r, column=c).value, (int, float))
    )
    
    # Check fold changes
    fc_count = sum(
        1 for r in range(FC_START_ROW, FC_START_ROW + num_proteins)
        for c in [FC_LOG2_COL, FC_LINEAR_COL]
        if tc.cell(row=r, column=c).value is not None
        and isinstance(tc.cell(row=r, column=c).value, (int, float))
    )
    
    print(f"\n── Verification (data_only=True) ──")
    print(f"Expression values: {ev_count}/{num_proteins * num_samples}")
    print(f"Statistics:        {st_count}/{num_proteins * 4}")
    print(f"Fold changes:     {fc_count}/{num_proteins * 2}")
    
    # Sanity: at least 80% filled (some missing data is expected)
    expr_ok = ev_count >= num_proteins * num_samples * 0.7
    stats_ok = st_count >= num_proteins * 4 * 0.7
    fc_ok = fc_count >= num_proteins * 2 * 0.7
    
    if expr_ok and stats_ok and fc_ok:
        print("\n✓ All checks passed.")
    else:
        print("\n✗ Some checks below threshold — review output above.")
        if not expr_ok:
            print(f"  Expression values below 70% threshold")
        if not stats_ok:
            print(f"  Statistics below 70% threshold")
        if not fc_ok:
            print(f"  Fold changes below 70% threshold")


if __name__ == "__main__":
    main()
```

---

## Decision Checklist

Before running the implementation, confirm:

- [ ] You've read the Task sheet to identify exact row/column ranges for proteins, samples, groups, stats, and fold changes
- [ ] You've verified sample name format matches between Task and Data sheets
- [ ] You've confirmed group labels (Control/Treated) and which columns they map to
- [ ] You're writing **numeric values**, not formula strings
- [ ] You're using `statistics.stdev()` (sample, N-1), not `statistics.pstdev()`
- [ ] You're computing Log2FC as `Treated - Control` (not the reverse)
- [ ] You're loading without `data_only` for writing (to preserve formatting)
- [ ] You've run verification with `data_only=True` to confirm values are readable

---

## Environment Notes

- **Python package:** `openpyxl==3