---
title: "Updating Embedded Excel Tables in PowerPoint (PPTX)"
category: exceltable-in-ppt
domain: office-document-manipulation
tags:
  - python-pptx
  - openpyxl
  - pptx
  - embedded-excel
  - formula-preservation
  - zipfile
  - xml-patching
dependencies:
  - python-pptx==1.0.2
  - openpyxl==3.1.5
  - pandas==2.2.3
  - lxml
---

# Updating Embedded Excel Tables in PowerPoint (PPTX)

## Overview

This skill covers the end-to-end workflow for reading a PPTX file that contains an embedded Excel table (typically a currency exchange rate matrix or similar data grid), extracting an updated value from a text box on the slide, patching the embedded Excel workbook with the new value while preserving all formulas, and saving the result as a new PPTX file.

The core challenge is that **openpyxl does not recalculate formulas on save**, so cached `<v>` (value) elements inside formula cells become stale. Downstream readers like `pd.read_excel()` and many verifiers read these cached values, not the formulas. You must patch them yourself via direct XML manipulation.

---

## High-Level Workflow

1. **Read the test/verification file first** — understand exactly which cell is expected to change, what the new value should be, and what "unchanged" means. This prevents wasted exploration.

2. **Extract the text box content from the slide** — parse the slide XML to find `<a:t>` text runs inside shape tree nodes. The text box contains the updated exchange rate in a pattern like `"USD to CNY=7.02"`.

3. **Locate the embedded Excel file inside the PPTX** — a PPTX is a ZIP archive. The embedded Excel lives at a path like `ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx` (or similar). Find it by scanning the ZIP namelist.

4. **Open the embedded Excel with openpyxl** — use `data_only=False` so you can see both formulas and values. Identify which cells are plain values and which contain formulas.

5. **Update the target cell** — write the new exchange rate value to the correct cell.

6. **Recalculate and patch cached values for ALL formula cells** — evaluate each formula in Python (they are typically simple: `ROUND(1/CellRef, N)`, `ROUND(CellRef*OtherRef, N)`, etc.) and write the computed result back as the cell's cached value via XML patching.

7. **Re-inject the modified Excel back into the PPTX ZIP** — replace the old embedded file with the new one, copying all other ZIP entries unchanged.

8. **Verify** — open the result with `pd.read_excel()` and confirm the updated cell, inverse cell, formula preservation, and that all other cells are unchanged.

---

## Step 1: Extract Text Box Content from the Slide

The text box is a shape on slide 1. Use `python-pptx` to iterate over shapes and find text frames.

```python
from pptx import Presentation
from pptx.util import Inches
import re

def extract_textbox_update(pptx_path):
    """
    Scan all shapes on all slides for a text box containing an exchange rate update.
    Returns a dict: {'from_currency': str, 'to_currency': str, 'rate': float}
    """
    prs = Presentation(pptx_path)
    pattern = re.compile(r'(\w+)\s+to\s+(\w+)\s*=\s*([\d.]+)', re.IGNORECASE)
    
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                full_text = shape.text_frame.text.strip()
                match = pattern.search(full_text)
                if match:
                    return {
                        'from_currency': match.group(1).upper(),
                        'to_currency': match.group(2).upper(),
                        'rate': float(match.group(3))
                    }
    raise ValueError("No exchange rate update found in any text box")
```

**Key patterns to match:**
- `"Updated rate: USD to CNY=7.02"`
- `"EUR to JPY = 158.5"`
- Variations with/without spaces around `=`

---

## Step 2: Locate and Extract the Embedded Excel

```python
import zipfile
import os
import tempfile
import shutil

def find_embedded_excel(pptx_path):
    """Find the path of the embedded Excel file inside the PPTX ZIP."""
    with zipfile.ZipFile(pptx_path, 'r') as zf:
        for name in zf.namelist():
            if name.startswith('ppt/embeddings/') and name.endswith('.xlsx'):
                return name
    raise FileNotFoundError("No embedded .xlsx found in PPTX")

def extract_embedded_excel(pptx_path, output_path):
    """Extract the embedded Excel to a temporary file."""
    embed_name = find_embedded_excel(pptx_path)
    with zipfile.ZipFile(pptx_path, 'r') as zf:
        with zf.open(embed_name) as src, open(output_path, 'wb') as dst:
            dst.write(src.read())
    return embed_name
```

---

## Step 3: Inspect the Workbook — Identify Values vs Formulas

```python
import openpyxl

def inspect_workbook(xlsx_path):
    """
    Load workbook with formulas visible. Return:
    - cell_map: dict of (row, col) -> {'value': ..., 'formula': ... or None}
    - headers: column currency labels
    - row_labels: row currency labels
    """
    wb = openpyxl.load_workbook(xlsx_path)  # data_only=False is default
    ws = wb.active

    cell_map = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            val = cell.value
            is_formula = isinstance(val, str) and val.startswith('=')
            cell_map[cell.coordinate] = {
                'value': val,
                'is_formula': is_formula,
            }
    
    # Typically row 1 = header (currencies as columns), column A = row labels
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    row_labels = [ws.cell(r, 1).value for r in range(1, ws.max_row + 1)]
    
    wb.close()
    return cell_map, headers, row_labels
```

---

## Step 4: Update the Target Cell and Compute Formula Cached Values

This is the critical step. You must:
1. Write the new value to the target cell.
2. Save with openpyxl (this preserves formulas but leaves cached `<v>` values stale).
3. Patch the XML to update cached values for every formula cell.

```python
import re
import math
from lxml import etree

def find_cell_by_currencies(ws, from_curr, to_curr, headers, row_labels):
    """
    Find the cell coordinate for from_curr -> to_curr in the exchange rate matrix.
    Row 1 is header, Column A is row labels.
    """
    # Find column index (1-based) for to_currency in header row
    col_idx = None
    for c in range(2, ws.max_column + 1):
        if ws.cell(1, c).value and ws.cell(1, c).value.strip().upper() == to_curr:
            col_idx = c
            break
    
    # Find row index (1-based) for from_currency in first column
    row_idx = None
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value and ws.cell(r, 1).value.strip().upper() == from_curr:
            row_idx = r
            break
    
    if col_idx is None or row_idx is None:
        raise ValueError(f"Cannot find cell for {from_curr} -> {to_curr}")
    
    return row_idx, col_idx


def update_value_cell(ws, row, col, new_value):
    """Update a plain-value cell."""
    ws.cell(row, col).value = new_value


def evaluate_formula_simple(formula, ws):
    """
    Evaluate simple Excel formulas that appear in exchange rate tables.
    Handles: ROUND(expr, n), 1/CellRef, CellRef*CellRef, CellRef/CellRef
    
    This is NOT a general Excel formula evaluator — it covers the patterns
    seen in currency rate matrices.
    """
    # Strip leading =
    expr = formula.lstrip('=')
    
    # Handle ROUND(expr, n)
    round_match = re.match(r'ROUND\((.+),\s*(\d+)\)', expr, re.IGNORECASE)
    if round_match:
        inner_expr = round_match.group(1)
        decimals = int(round_match.group(2))
        inner_val = _eval_arithmetic(inner_expr, ws)
        return round(inner_val, decimals)
    
    return _eval_arithmetic(expr, ws)


def _eval_arithmetic(expr, ws):
    """Evaluate simple arithmetic: cell refs, numbers, *, /, +, -"""
    # Replace cell references with their current values
    def cell_replacer(match):
        coord = match.group(0)
        cell = ws[coord]
        val = cell.value
        # If the referenced cell itself has a formula, we need its computed value
        # For simplicity, if it's a number use it; if formula, recurse
        if isinstance(val, str) and val.startswith('='):
            return str(evaluate_formula_simple(val, ws))
        return str(val)
    
    # Match cell references like B3, C2, AA15
    resolved = re.sub(r'[A-Z]{1,3}\d{1,5}', cell_replacer, expr)
    
    try:
        return eval(resolved)  # Safe here: only numbers and arithmetic ops
    except Exception as e:
        raise ValueError(f"Cannot evaluate '{expr}' -> '{resolved}': {e}")
```

---

## Step 5: Patch Cached `<v>` Values in the Excel XML

**This is the most critical and error-prone step.** openpyxl saves formula cells without recalculating. The `<v>` element inside each `<c>` (cell) element in `xl/worksheets/sheet1.xml` holds the cached display value. `pd.read_excel()` reads these cached values.

```python
def patch_cached_values(xlsx_path, formula_values):
    """
    Patch the cached <v> values in the sheet XML for formula cells.
    
    formula_values: dict of cell_coordinate -> computed_value
                    e.g. {'C2': 0.142, 'D2': 0.857, ...}
    """
    import zipfile
    from lxml import etree
    from io import BytesIO
    
    SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    
    tmp_path = xlsx_path + '.tmp'
    
    with zipfile.ZipFile(xlsx_path, 'r') as zin, \
         zipfile.ZipFile(tmp_path, 'w') as zout:
        
        for item in zin.infolist():
            data = zin.read(item.filename)
            
            if item.filename == 'xl/worksheets/sheet1.xml':
                root = etree.fromstring(data)
                ns = {'s': SHEET_NS}
                
                for cell_el in root.findall('.//s:c', ns):
                    ref = cell_el.get('r')  # e.g. "C2"
                    if ref in formula_values:
                        # Find or create <v> element
                        v_el = cell_el.find('s:v', ns)
                        if v_el is None:
                            v_el = etree.SubElement(cell_el, f'{{{SHEET_NS}}}v')
                        v_el.text = str(formula_values[ref])
                
                data = etree.tostring(root, xml_declaration=True,
                                       encoding='UTF-8', standalone=True)
            
            zout.writestr(item, data)
    
    shutil.move(tmp_path, xlsx_path)
```

---

## Step 6: Re-inject Modified Excel into the PPTX

```python
def reinject_excel_into_pptx(original_pptx, modified_xlsx, embed_name, output_pptx):
    """
    Replace the embedded Excel inside the PPTX with the modified version.
    All other ZIP entries are copied byte-for-byte.
    """
    with zipfile.ZipFile(original_pptx, 'r') as zin, \
         zipfile.ZipFile(output_pptx, 'w') as zout:
        
        for item in zin.infolist():
            if item.filename == embed_name:
                # Replace with modified Excel
                with open(modified_xlsx, 'rb') as f:
                    zout.writestr(item, f.read())
            else:
                zout.writestr(item, zin.read(item.filename))
```

---

## Step 7: Verification

```python
import pandas as pd

def verify_result(result_pptx, expected_from, expected_to, expected_rate):
    """Quick verification that the output is correct."""
    # Extract embedded Excel
    tmp_xlsx = '/tmp/verify_check.xlsx'
    embed_name = extract_embedded_excel(result_pptx, tmp_xlsx)
    
    df = pd.read_excel(tmp_xlsx)
    first_col = df.columns[0]
    
    # Check updated rate
    row = df[df[first_col].str.upper() == expected_from]
    actual = row.iloc[0][expected_to]
    assert abs(actual - expected_rate) < 0.01, \
        f"Rate mismatch: expected {expected_rate}, got {actual}"
    
    # Check inverse rate (should be ~1/rate, rounded)
    inv_row = df[df[first_col].str.upper() == expected_to]
    actual_inv = inv_row.iloc[0][expected_from]
    expected_inv = round(1.0 / expected_rate, 3)
    assert abs(actual_inv - expected_inv) < 0.01, \
        f"Inverse mismatch: expected {expected_inv}, got {actual_inv}"
    
    os.unlink(tmp_xlsx)
    print("Verification PASSED")
```

---

## Common Pitfalls

### 1. openpyxl Does NOT Recalculate Formulas
**Symptom:** The formula text is preserved but `pd.read_excel()` returns the old value.
**Cause:** openpyxl writes the formula string into `<f>` but leaves the `<v>` (cached value) element unchanged or empty.
**Fix:** After saving with openpyxl, open the `.xlsx` as a ZIP, parse `xl/worksheets/sheet1.xml` with lxml, and manually set the `<v>` text for every formula cell to the Python-computed result.

### 2. Forgetting to Patch ALL Formula Cells
**Symptom:** The directly-updated cell is correct but dependent cells (e.g., inverse rate) still show old values.
**Cause:** Only patching the cell you changed, not the cells whose formulas reference it.
**Fix:** Iterate over ALL formula cells in the workbook, evaluate each one, and patch every cached value. Currency matrices often have `N*(N-1)` formula cells for a grid of N currencies.

### 3. Never Creating the Output File
**Symptom:** All tests fail with "file not found."
**Cause:** Spending too long on exploration/skill-writing without executing the actual transformation.
**Fix:** Read the test expectations first, then implement and run the solution. Skill documents are secondary to producing the output.

### 4. Corrupting the PPTX ZIP Structure
**Symptom:** The output file cannot be opened or is not recognized as a valid PPTX.
**Cause:** Using `zipfile.ZipFile` in append mode or not copying all entries.
**Fix:** Always create a fresh output ZIP. Copy every entry from the original except the embedded Excel, which you replace. Use `writestr(item, data)` with the original `ZipInfo` object to preserve metadata.

### 5. Text Box Parsing Failures
**Symptom:** Cannot find the updated rate.
**Cause:** The text box may have the rate split across multiple `<a:t>` runs, or use unexpected formatting.
**Fix:** Concatenate all text from the text frame before applying the regex. Use `shape.text_frame.text` which handles run concatenation automatically.

### 6. Cell Coordinate Mismatch
**Symptom:** Wrong cell gets updated.
**Cause:** Confusing row/column order. In a currency matrix, rows are "from" currencies and columns are "to" currencies (or vice versa).
**Fix:** Always read the header row and first column to determine the mapping. Don't assume a fixed layout.

---

## Reference Implementation

This is a complete, self-contained script. Copy, adapt the input/output paths, and run.

```python
#!/usr/bin/env python3
"""
Complete solution: Update an embedded Excel exchange rate table in a PPTX file.

Usage:
    python3 solve.py

Reads:  /root/input.pptx
Writes: /root/results.pptx

Requirements: python-pptx, openpyxl, pandas, lxml
"""

import os
import re
import shutil
import tempfile
import zipfile
from io import BytesIO

import openpyxl
import pandas as pd
from lxml import etree
from pptx import Presentation

# ── Configuration ──────────────────────────────────────────────────────────
INPUT_PPTX = '/root/input.pptx'
OUTPUT_PPTX = '/root/results.pptx'

SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


# ── Step 1: Extract updated rate from text box ────────────────────────────
def extract_textbox_update(pptx_path):
    """Find the text box with the updated exchange rate and parse it."""
    prs = Presentation(pptx_path)
    pattern = re.compile(r'(\w+)\s+to\s+(\w+)\s*=\s*([\d.]+)', re.IGNORECASE)

    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                full_text = shape.text_frame.text.strip()
                match = pattern.search(full_text)
                if match:
                    return {
                        'from_currency': match.group(1).upper(),
                        'to_currency': match.group(2).upper(),
                        'rate': float(match.group(3)),
                        'raw_text': full_text,
                    }
    raise ValueError("No exchange rate update found in any text box")


# ── Step 2: Find and extract embedded Excel ───────────────────────────────
def find_embedded_excel_name(pptx_path):
    """Return the ZIP-internal path of the embedded .xlsx."""
    with zipfile.ZipFile(pptx_path, 'r') as zf:
        for name in zf.namelist():
            if name.startswith('ppt/embeddings/') and name.endswith('.xlsx'):
                return name
    raise FileNotFoundError("No embedded .xlsx found in PPTX")


def extract_embedded_excel(pptx_path, dest_xlsx):
    """Extract the embedded Excel workbook to a local file."""
    embed_name = find_embedded_excel_name(pptx_path)
    with zipfile.ZipFile(pptx_path, 'r') as zf:
        with zf.open(embed_name) as src, open(dest_xlsx, 'wb') as dst:
            dst.write(src.read())
    return embed_name


# ── Step 3: Identify cell coordinates ─────────────────────────────────────
def find_cell_coords(ws, from_curr, to_curr):
    """
    In the exchange rate matrix, find (row, col) for from_curr → to_curr.
    Row 1 = header (column currencies), Column A = row currencies.
    Returns (row_1based, col_1based).
    """
    # Find column for to_currency
    col_idx = None
    for c in range(2, ws.max_column + 1):
        hdr = ws.cell(1, c).value
        if hdr and str(hdr).strip().upper() == to_curr:
            col_idx = c
            break

    # Find row for from_currency
    row_idx = None
    for r in range(2, ws.max_row + 1):
        lbl = ws.cell(r, 1).value
        if lbl and str(lbl).strip().upper() == from_curr:
            row_idx = r
            break

    if col_idx is None or row_idx is None:
        raise ValueError(f"Cannot locate cell for {from_curr} → {to_curr}")

    return row_idx, col_idx


# ── Step 4: Evaluate simple Excel formulas in Python ──────────────────────
def evaluate_formula(formula, ws):
    """
    Evaluate formulas commonly found in currency exchange matrices:
    =ROUND(1/B3, 3), =ROUND(B3*C4, 2), =1/B3, =B3*0.5, etc.
    """
    expr = formula.lstrip('=')

    # Handle ROUND(inner, decimals)
    round_match = re.match(r'ROUND\((.+),\s*(\d+)\)', expr, re.IGNORECASE)
    if round_match:
        inner = round_match.group(1)
        decimals = int(round_match.group(2))
        return round(_eval_arith(inner, ws), decimals)

    return _eval_arith(expr, ws)


def _resolve_cell_value(coord, ws, depth=0):
    """Get the numeric value of a cell, recursing into formulas if needed."""
    if depth > 10:
        raise RecursionError(f"Formula recursion too deep at {coord}")
    cell = ws[coord]
    val = cell.value
    if isinstance(val, str) and val.startswith('='):
        return evaluate_formula(val, ws)
    if val is None:
        return 0
    return float(val)


def _eval_arith(expr, ws):
    """Replace cell references with values and evaluate arithmetic."""
    def replacer(m):
        coord = m.group(0)
        return str(_resolve_cell_value(coord, ws))

    resolved = re.sub(r'[A-Z]{1,3}\d{1,5}', replacer, expr)
    try:
        return float(eval(resolved))
    except Exception as e:
        raise ValueError(f"Cannot evaluate '{expr}' → '{resolved}': {e}")


# ── Step 5: Collect all formula cells and compute their values ────────────
def compute_all_formula_values(ws):
    """
    Return dict: {cell_coordinate: computed_value} for every formula cell.
    """
    formula_values = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                            max_col=ws.max_column):
        for cell in row:
            val = cell.value
            if isinstance(val, str) and val.startswith('='):
                computed = evaluate_formula(val, ws)
                formula_values[cell.coordinate] = computed
    return formula_values


# ── Step 6: Patch cached <v> values in the Excel XML ─────────────────────
def patch_cached_values(xlsx_path, formula_values):
    """
    Open the .xlsx as a ZIP, parse sheet1.xml, and update <v> elements
    for every formula cell so that cached values match our computation.
    """
    ns = {'s': SHEET_NS}
    tmp_path = xlsx_path + '.patching.tmp'

    with zipfile.ZipFile(xlsx_path, 'r') as zin, \
         zipfile.ZipFile(tmp_path, 'w') as zout:

        for item in zin.infolist():
            data = zin.read(item.filename)

            # Patch every worksheet (usually just sheet1)
            if item.filename.startswith('xl/worksheets/') and \
               item.filename.endswith('.xml'):
                root = etree.fromstring(data)

                for cell_el in root.findall('.//s:c', ns):
                    ref = cell_el.get('r')
                    if ref in formula_values:
                        v_el = cell_el.find('s:v', ns)
                        if v_el is None:
                            v_el = etree.SubElement(
                                cell_el, f'{{{SHEET_NS}}}v')
                        v_el.text = str(formula_values[ref])

                data = etree.tostring(root, xml_declaration=True,
                                       encoding='UTF-8', standalone=True)

            zout.writestr(item, data)

    shutil.move(tmp_path, xlsx_path)


# ── Step 7: Re-inject Excel into PPTX ────────────────────────────────────
def reinject_excel(original_pptx, modified_xlsx, embed_name, output_pptx):
    """Replace the embedded Excel in the PPTX, copying everything else."""
    with zipfile.ZipFile(original_pptx, 'r') as zin, \
         zipfile.ZipFile(output_pptx, 'w') as zout:

        for item in zin.infolist():
            if item.filename == embed_name:
                with open(modified_xlsx, 'rb') as f:
                    zout.writestr(item, f.read())
            else:
                zout.writestr(item, zin.read(item.filename))


# ── Step 8: Quick self-verification ───────────────────────────────────────
def verify(output_pptx, from_curr, to_curr, expected_rate):
    """Verify the output file has the correct values."""
    tmp = '/tmp/_verify_embedded.xlsx'
    extract_embedded_excel(output_pptx, tmp)
    df = pd.read_excel(tmp)
    first_col = df.columns[0]

    # Direct rate
    row = df[df[first_col].str.strip().str.upper() == from_curr]
    actual = float(row.iloc[0][to_curr])
    assert abs(actual - expected_rate) < 0.01, \
        f"Direct rate wrong: {actual} vs {expected_rate}"

    # Inverse rate
    inv_row = df[df[first_col].str.strip().str.upper() == to_curr]
    actual_inv = float(inv_row.iloc[0][from_curr])
    expected_inv = round(1.0 / expected_rate, 3)
    assert abs(actual_inv - expected_inv) < 0.01, \
        f"Inverse rate wrong: {actual_inv} vs {expected_inv}"

    os.unlink(tmp)
    print(f"✓ Verified: {from_curr}→{to_curr} = {actual}, "
          f"{to_curr}→{from_curr} = {actual_inv}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    # 1. Parse the text box to get the updated rate
    update = extract_textbox_update(INPUT_PPTX)
    from_curr = update['from_currency']
    to_curr = update['to_currency']
    new_rate = update['rate']
    print(f"Text box says: {from_curr} → {to_curr} = {new_rate}")

    # 2. Extract embedded Excel
    tmp_xlsx = '/tmp/_embedded_workbook.xlsx'
    embed_name = extract_embedded_excel(INPUT_PPTX, tmp_xlsx)
    print(f"Embedded Excel: {embed_name}")

    # 3. Open workbook, find target cell, update it
    wb = openpyxl.load_workbook(tmp_xlsx)  # data_only=False → see formulas
    ws = wb.active

    row_idx, col_idx = find_cell_coords(ws, from_curr, to_curr)
    target_coord = ws.cell(row_idx, col_idx).coordinate
    old_val = ws.cell(row_idx, col_idx).value
    print(f"Updating {target_coord}: {old_val} → {new_rate}")

    ws.cell(row_idx, col_idx).value = new_rate

    # 4. Compute correct cached values for ALL formula cells
    formula_values = compute_all_formula_values(ws)
    print(f"Formula cells to patch: {list(formula_values.keys())}")
    for coord, val in formula_values.items():
        print(f"  {coord} = {ws[coord].value} → cached {val}")

    # 5. Save workbook (preserves formula strings)
    wb.save(tmp_xlsx)
    wb.close()

    # 6. Patch cached <v> values in the XML
    patch_cached_values(tmp_xlsx, formula_values)

    # 7. Re-inject into PPTX
    reinject_excel(INPUT_PPTX, tmp_xlsx, embed_name, OUTPUT_PPTX)
    print(f"Saved: {OUTPUT_PPTX}")

    # 8. Verify
    verify(OUTPUT_PPTX, from_curr, to_curr, new_rate)

    # Cleanup
    os.unlink(tmp_xlsx)


if __name__ == '__main__':
    main()
```

---

## Decision Checklist (Before You Start)

- [ ] Read the test/verification file to know exact expected values and cell locations
- [ ] Confirm which cell(s) are plain values vs formulas (use `openpyxl` with `data_only=False`)
- [ ] Identify the text box pattern (regex) before writing the parser
- [ ] Plan to patch ALL formula cells' cached values, not just the one you changed
- [ ] Test with `pd.read_excel()` since that's what most verifiers use (reads cached `<v>` values)
- [ ] Ensure the output PPTX is a valid ZIP with all original entries preserved

---

## Environment Notes

- **Available packages:** `python-pptx==1.0.2`, `openpyxl==3.1.5`, `pandas==2.2.3`, `lxml` (via openpyxl dependency), `defusedxml==0.7.1`
- **System tools:** `python3`, `libreoffice` (available but not needed for this approach)
- **OS:** Ubuntu 24.04
- **No internet access required** — all processing is local file manipulation