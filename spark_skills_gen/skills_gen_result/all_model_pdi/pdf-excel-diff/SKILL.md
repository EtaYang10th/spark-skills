---
title: "PDF-to-Excel Employee Record Diffing"
description: >
  Extract tabular data from a multi-page PDF backup, compare it against a current
  Excel spreadsheet, and produce a structured JSON diff report listing deleted rows
  and field-level modifications. Generalises to any "old PDF vs. current Excel" 
  reconciliation task.
tags:
  - pdf-extraction
  - excel-comparison
  - data-diffing
  - pdfplumber
  - pandas
  - json-report
applies_to:
  - pdf-excel-diff
  - record-reconciliation
  - hr-data-audit
dependencies:
  - pdfplumber>=0.11
  - pandas>=2.0
  - openpyxl>=3.1
---

# PDF-to-Excel Record Diff Skill

## 1. High-Level Workflow

| Step | What | Why |
|------|------|-----|
| 1 | **Probe the inputs** — open the PDF, count pages, inspect the first page's table structure; open the Excel file, check sheet names and shape. | Understand scale (hundreds of pages = thousands of rows) and column layout before writing extraction code. |
| 2 | **Extract all tables from the PDF** using `pdfplumber`. Concatenate page-by-page results into a single DataFrame, dropping repeated header rows. | `pdfplumber` is the most reliable pure-Python PDF table extractor for well-structured tables. `tabula-py` requires Java and is less predictable with column alignment. |
| 3 | **Read the Excel file** into a DataFrame with `pandas` + `openpyxl`. | Straightforward; just make sure to use the correct sheet (usually the first/only one). |
| 4 | **Normalise both DataFrames** — align column names, cast numeric columns to their proper types (`int`, `float`), strip whitespace from strings, and set the ID column as the index. | Type mismatches are the #1 source of false-positive diffs. A salary stored as `"75000"` in the PDF and `75000` in Excel will look like a change unless you cast first. |
| 5 | **Compute deleted records** — IDs present in the PDF DataFrame but absent from the Excel DataFrame. | These are rows that were removed from the current database. |
| 6 | **Compute modified records** — for IDs present in both, compare every field. Record the ID, field name, old value, and new value for each difference. | Field-level granularity is required by the output schema. |
| 7 | **Assemble and write the JSON report** — sort deleted IDs and modified entries by ID, enforce the correct value types (numbers vs. strings), and write to the output path. | Validators check sort order and value types. |
| 8 | **Self-validate** — reload the JSON, assert structural correctness, check row-count arithmetic (PDF rows − deleted = Excel rows), and verify type constraints. | Catch errors before the external verifier runs. |

### Decision Criteria: pdfplumber vs. tabula-py vs. camelot

| Library | Pros | Cons | When to use |
|---------|------|------|-------------|
| **pdfplumber** | Pure Python, no Java, reliable column detection, page-level control | Slower on very large PDFs | Default choice — works in almost all environments |
| **tabula-py** | Fast for simple tables | Requires Java; column misalignment on complex layouts | Fallback if pdfplumber fails |
| **camelot** | Good for bordered tables | Requires Ghostscript; struggles with borderless tables | Only when tables have visible grid lines |

**Recommendation:** Start with `pdfplumber`. Only fall back to `tabula-py` if `pdfplumber` produces garbled output.

---

## 2. Step-by-Step with Code

### 2.1 Probe the Inputs

```python
import pdfplumber
import pandas as pd

# --- PDF inspection ---
pdf = pdfplumber.open("/root/employees_backup.pdf")
print(f"PDF pages: {len(pdf.pages)}")

first_table = pdf.pages[0].extract_table()
print(f"First-page table header: {first_table[0]}")
print(f"First-page table rows:   {len(first_table) - 1}")
pdf.close()

# --- Excel inspection ---
df_excel = pd.read_excel("/root/employees_current.xlsx", engine="openpyxl")
print(f"Excel shape: {df_excel.shape}")
print(f"Excel columns: {list(df_excel.columns)}")
print(df_excel.dtypes)
```

**What to look for:**
- Column names must match between PDF header row and Excel columns. If they differ (e.g. `"Employee ID"` vs `"ID"`), you'll need a rename map.
- The PDF's first row is almost always the header. Subsequent pages may repeat it — you must filter those out.
- Check Excel dtypes: if `Salary` is already `int64`, great. If it's `object`, you'll need to cast.

### 2.2 Extract All Tables from the PDF

```python
import pdfplumber
import pandas as pd

def extract_pdf_tables(pdf_path: str) -> pd.DataFrame:
    """
    Extract all tables from a multi-page PDF into a single DataFrame.
    Handles repeated header rows across pages.
    """
    all_rows = []
    header = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            table = page.extract_table()
            if table is None or len(table) == 0:
                continue

            if header is None:
                # First page: row 0 is the header
                header = table[0]
                data_rows = table[1:]
            else:
                data_rows = table

            for row in data_rows:
                # Skip rows that are exact copies of the header
                # (repeated on subsequent pages)
                if row == header:
                    continue
                # Skip completely empty rows
                if all(cell is None or str(cell).strip() == "" for cell in row):
                    continue
                all_rows.append(row)

    df = pd.DataFrame(all_rows, columns=header)
    return df


df_pdf = extract_pdf_tables("/root/employees_backup.pdf")
print(f"PDF extracted rows: {len(df_pdf)}")
```

**Critical notes:**
- `page.extract_table()` returns a list of lists. The first element is the header on the first page, but on subsequent pages it may or may not be repeated — always check.
- Some PDFs have merged cells or multi-line values that produce `None` entries. Guard against those.
- For very large PDFs (200+ pages), this loop takes 30–90 seconds. That's normal.

### 2.3 Read the Excel File

```python
df_excel = pd.read_excel("/root/employees_current.xlsx", engine="openpyxl")
print(f"Excel rows: {len(df_excel)}")
```

### 2.4 Normalise Both DataFrames

This is the most important step. Type mismatches cause false diffs.

```python
def normalise_dataframe(df: pd.DataFrame, id_col: str,
                        int_cols: list, float_cols: list) -> pd.DataFrame:
    """
    Normalise a DataFrame for comparison:
    - Strip whitespace from all string columns
    - Cast numeric columns to int or float
    - Set the ID column as index
    - Sort by index
    """
    df = df.copy()

    # Strip whitespace from all object columns
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].astype(str).str.strip()

    # Cast integer columns
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(int)

    # Cast float columns
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

    df = df.set_index(id_col).sort_index()
    return df


# Determine which columns are which by inspecting the Excel dtypes
# and the task description. Common patterns:
#   Integer: Salary, Years (of experience), Age
#   Float:   Score, Rating, GPA
#   String:  Name, Department, Position, Email

# Adjust these lists based on actual column inspection from step 2.1
ID_COL = "ID"                          # or "Employee ID", etc.
INT_COLS = ["Salary", "Years"]         # columns that should be int
FLOAT_COLS = ["Score"]                 # columns that should be float

df_pdf_norm = normalise_dataframe(df_pdf, ID_COL, INT_COLS, FLOAT_COLS)
df_excel_norm = normalise_dataframe(df_excel, ID_COL, INT_COLS, FLOAT_COLS)

print(f"PDF  index sample: {df_pdf_norm.index[:5].tolist()}")
print(f"Excel index sample: {df_excel_norm.index[:5].tolist()}")
```

### 2.5 Compute Deleted Records

```python
pdf_ids = set(df_pdf_norm.index)
excel_ids = set(df_excel_norm.index)

deleted_ids = sorted(pdf_ids - excel_ids)
print(f"Deleted employees: {len(deleted_ids)}")
```

### 2.6 Compute Modified Records

```python
def find_modifications(df_old: pd.DataFrame, df_new: pd.DataFrame,
                       int_cols: list, float_cols: list) -> list:
    """
    Compare two DataFrames row-by-row on their shared index.
    Returns a list of dicts: {id, field, old_value, new_value}.
    Values are typed correctly (int, float, or str).
    """
    common_ids = sorted(set(df_old.index) & set(df_new.index))
    compare_cols = [c for c in df_old.columns if c in df_new.columns]
    modifications = []

    for emp_id in common_ids:
        old_row = df_old.loc[emp_id]
        new_row = df_new.loc[emp_id]

        for col in compare_cols:
            old_val = old_row[col]
            new_val = new_row[col]

            # Normalise for comparison
            if col in int_cols:
                old_cmp, new_cmp = int(old_val), int(new_val)
            elif col in float_cols:
                old_cmp = round(float(old_val), 6)
                new_cmp = round(float(new_val), 6)
            else:
                old_cmp = str(old_val).strip()
                new_cmp = str(new_val).strip()

            if old_cmp != new_cmp:
                modifications.append({
                    "id": emp_id,
                    "field": col,
                    "old_value": old_cmp,
                    "new_value": new_cmp,
                })

    return modifications


modifications = find_modifications(df_pdf_norm, df_excel_norm, INT_COLS, FLOAT_COLS)
print(f"Modified records: {len(modifications)}")
```

### 2.7 Assemble and Write the JSON Report

```python
import json

report = {
    "deleted_employees": deleted_ids,
    "modified_employees": sorted(modifications, key=lambda m: m["id"]),
}

with open("/root/diff_report.json", "w") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print("Report written to /root/diff_report.json")
```

### 2.8 Self-Validate

```python
import json

with open("/root/diff_report.json") as f:
    data = json.load(f)

# Structure
assert "deleted_employees" in data
assert "modified_employees" in data
assert isinstance(data["deleted_employees"], list)
assert isinstance(data["modified_employees"], list)

# ID format
for eid in data["deleted_employees"]:
    assert isinstance(eid, str) and eid.startswith("EMP") and len(eid) == 8, f"Bad ID: {eid}"

# Sort order
assert data["deleted_employees"] == sorted(data["deleted_employees"]), "deleted not sorted"
mod_ids = [m["id"] for m in data["modified_employees"]]
assert mod_ids == sorted(mod_ids), "modified not sorted by id"

# Type checks
for m in data["modified_employees"]:
    assert all(k in m for k in ("id", "field", "old_value", "new_value"))
    if m["field"] in ("Salary", "Years", "Age"):
        assert isinstance(m["old_value"], int), f"{m['id']} {m['field']} old not int"
        assert isinstance(m["new_value"], int), f"{m['id']} {m['field']} new not int"
    elif m["field"] in ("Score", "Rating", "GPA"):
        assert isinstance(m["old_value"], (int, float)), f"{m['id']} {m['field']} old not numeric"
        assert isinstance(m["new_value"], (int, float)), f"{m['id']} {m['field']} new not numeric"
    else:
        assert isinstance(m["old_value"], str), f"{m['id']} {m['field']} old not str"
        assert isinstance(m["new_value"], str), f"{m['id']} {m['field']} new not str"

# Row-count arithmetic
pdf_count = len(df_pdf_norm)
excel_count = len(df_excel_norm)
assert pdf_count - len(data["deleted_employees"]) == excel_count, \
    f"Count mismatch: {pdf_count} - {len(data['deleted_employees'])} != {excel_count}"

print(f"Deleted:  {len(data['deleted_employees'])} employees")
print(f"Modified: {len(data['modified_employees'])} records")
print(f"{pdf_count} (PDF) - {len(data['deleted_employees'])} (deleted) = {excel_count} (Excel) ✓")
print("All validations passed ✓")
```

---

## 3. Common Pitfalls

### Pitfall 1: Repeated Header Rows in Multi-Page PDFs
**Symptom:** The extracted DataFrame contains rows where every field matches the column names (e.g., a row with values `["ID", "Name", "Department", ...]`).
**Fix:** Always compare each extracted row against the header and skip exact matches. See the `extract_pdf_tables` function above.

### Pitfall 2: Type Mismatches Causing False Diffs
**Symptom:** Every single row shows up as "modified" because `"75000"` (string from PDF) ≠ `75000` (int from Excel).
**Fix:** Explicitly cast numeric columns to `int` or `float` in both DataFrames before comparison. The `normalise_dataframe` function handles this.

### Pitfall 3: Floating-Point Comparison Noise
**Symptom:** Score fields show diffs like `4.199999999999999` vs `4.2`.
**Fix:** Round float columns to a consistent number of decimal places (6 is safe) before comparing. Use `round(float(val), 6)`.

### Pitfall 4: Whitespace Differences in String Fields
**Symptom:** Names or departments appear modified when they're actually identical except for trailing spaces.
**Fix:** `.str.strip()` all object columns during normalisation.

### Pitfall 5: Wrong ID Format in Output
**Symptom:** Validator rejects IDs like `"EMP2"` instead of `"EMP00002"`.
**Fix:** The IDs come directly from the source data — don't reformat them. If the source already has zero-padded IDs (`EMP00002`), just pass them through. If not, use `f"EMP{int(id_num):05d}"`.

### Pitfall 6: Using `tabula-py` Without Java
**Symptom:** `tabula-py` throws `JavaNotFoundError`.
**Fix:** Check `which java` first. If Java isn't available, use `pdfplumber` instead. In most contest/sandbox environments, `pdfplumber` is the safer default.

### Pitfall 7: Not Sorting the Output
**Symptom:** Validator fails on order check.
**Fix:** Sort `deleted_employees` alphabetically. Sort `modified_employees` by `id`. Do this as the final step before writing JSON.

### Pitfall 8: Multiple Modifications Per Employee
**Symptom:** An employee has both a salary change and a department change, but only one is reported.
**Fix:** Iterate over all columns for each common ID. Each changed field produces its own entry in `modified_employees`. One employee can appear multiple times.

### Pitfall 9: `None` or `NaN` Values from PDF Extraction
**Symptom:** Comparison crashes with `TypeError` or produces spurious diffs against `NaN`.
**Fix:** After extraction, check for nulls: `df.isnull().sum()`. Drop or investigate rows with unexpected nulls — they usually indicate extraction errors (merged cells, page breaks mid-row).

---

## 4. Reference Implementation

This is a complete, self-contained script. Copy it, adjust the column names and type lists to match your specific dataset, and run it.

```python
#!/usr/bin/env python3
"""
PDF-to-Excel Record Diff Report Generator

Extracts employee data from a multi-page PDF backup, compares it against
a current Excel file, and writes a JSON diff report with deleted and
modified records.

Usage:
    python3 diff_report.py

Inputs:
    /root/employees_backup.pdf   — old/backup employee table
    /root/employees_current.xlsx — current employee database

Output:
    /root/diff_report.json
"""

import json
import pdfplumber
import pandas as pd

# ============================================================
# CONFIGURATION — adjust these to match your dataset
# ============================================================
PDF_PATH = "/root/employees_backup.pdf"
EXCEL_PATH = "/root/employees_current.xlsx"
OUTPUT_PATH = "/root/diff_report.json"

ID_COL = "ID"  # Column name used as the unique employee identifier

# Columns that should be compared as integers
INT_COLS = ["Salary", "Years"]

# Columns that should be compared as floats
FLOAT_COLS = ["Score"]

# All other columns are compared as stripped strings.

# ============================================================
# STEP 1: Extract tables from PDF
# ============================================================
def extract_pdf_tables(pdf_path: str) -> pd.DataFrame:
    """
    Read every page of a PDF, extract its table, concatenate into
    one DataFrame. Filters out repeated header rows and empty rows.
    """
    all_rows = []
    header = None

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        print(f"PDF has {total_pages} pages. Extracting tables...")

        for page_idx, page in enumerate(pdf.pages):
            table = page.extract_table()
            if table is None or len(table) == 0:
                continue

            if header is None:
                header = [str(h).strip() for h in table[0]]
                data_rows = table[1:]
            else:
                data_rows = table

            for row in data_rows:
                # Skip repeated headers
                cleaned = [str(c).strip() if c is not None else "" for c in row]
                if cleaned == header:
                    continue
                # Skip fully empty rows
                if all(c == "" or c == "None" for c in cleaned):
                    continue
                all_rows.append(cleaned)

            if (page_idx + 1) % 50 == 0:
                print(f"  ... processed {page_idx + 1}/{total_pages} pages "
                      f"({len(all_rows)} rows so far)")

    print(f"PDF extraction complete: {len(all_rows)} data rows")
    df = pd.DataFrame(all_rows, columns=header)
    return df


# ============================================================
# STEP 2: Read Excel file
# ============================================================
def read_excel(excel_path: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, engine="openpyxl")
    print(f"Excel loaded: {len(df)} rows, columns: {list(df.columns)}")
    return df


# ============================================================
# STEP 3: Normalise a DataFrame for comparison
# ============================================================
def normalise(df: pd.DataFrame, id_col: str,
              int_cols: list, float_cols: list) -> pd.DataFrame:
    """
    - Strip whitespace from string columns
    - Cast numeric columns
    - Set ID as index, sort
    """
    df = df.copy()

    # String cleanup
    for col in df.columns:
        if col not in int_cols and col not in float_cols:
            df[col] = df[col].astype(str).str.strip()

    # Integer columns
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            # Handle NaN before int conversion
            if df[col].isna().any():
                nan_count = df[col].isna().sum()
                print(f"  WARNING: {nan_count} NaN values in '{col}' after numeric cast")
            df[col] = df[col].fillna(0).astype(int)

    # Float columns
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            if df[col].isna().any():
                nan_count = df[col].isna().sum()
                print(f"  WARNING: {nan_count} NaN values in '{col}' after numeric cast")
            df[col] = df[col].fillna(0.0).astype(float)

    df = df.set_index(id_col).sort_index()

    # Check for duplicate IDs
    dup_count = df.index.duplicated().sum()
    if dup_count > 0:
        print(f"  WARNING: {dup_count} duplicate IDs found — keeping first occurrence")
        df = df[~df.index.duplicated(keep="first")]

    return df


# ============================================================
# STEP 4: Find deleted IDs
# ============================================================
def find_deleted(df_old: pd.DataFrame, df_new: pd.DataFrame) -> list:
    old_ids = set(df_old.index)
    new_ids = set(df_new.index)
    deleted = sorted(old_ids - new_ids)
    return deleted


# ============================================================
# STEP 5: Find modified records
# ============================================================
def find_modified(df_old: pd.DataFrame, df_new: pd.DataFrame,
                  int_cols: list, float_cols: list) -> list:
    """
    For every ID present in both DataFrames, compare all fields.
    Returns a list of {id, field, old_value, new_value} dicts
    with properly typed values.
    """
    common_ids = sorted(set(df_old.index) & set(df_new.index))
    compare_cols = [c for c in df_old.columns if c in df_new.columns]
    modifications = []

    for emp_id in common_ids:
        old_row = df_old.loc[emp_id]
        new_row = df_new.loc[emp_id]

        for col in compare_cols:
            old_val = old_row[col]
            new_val = new_row[col]

            # Type-aware comparison
            if col in int_cols:
                old_typed = int(old_val)
                new_typed = int(new_val)
            elif col in float_cols:
                old_typed = round(float(old_val), 6)
                new_typed = round(float(new_val), 6)
            else:
                old_typed = str(old_val).strip()
                new_typed = str(new_val).strip()

            if old_typed != new_typed:
                modifications.append({
                    "id": emp_id,
                    "field": col,
                    "old_value": old_typed,
                    "new_value": new_typed,
                })

    return modifications


# ============================================================
# STEP 6: Assemble report and write JSON
# ============================================================
def write_report(deleted: list, modified: list, output_path: str):
    report = {
        "deleted_employees": deleted,
        "modified_employees": sorted(modified, key=lambda m: m["id"]),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nReport written to {output_path}")
    print(f"  Deleted employees:  {len(deleted)}")
    print(f"  Modified records:   {len(modified)}")
    return report


# ============================================================
# STEP 7: Self-validation
# ============================================================
def validate(report: dict, pdf_row_count: int, excel_row_count: int,
             int_cols: list, float_cols: list):
    """Run structural and type checks on the generated report."""
    d = report["deleted_employees"]
    m = report["modified_employees"]

    # Sort checks
    assert d == sorted(d), "deleted_employees is not sorted"
    m_ids = [entry["id"] for entry in m]
    assert m_ids == sorted(m_ids), "modified_employees is not sorted by id"

    # ID format
    for eid in d:
        assert isinstance(eid, str) and eid.startswith("EMP"), f"Bad deleted ID: {eid}"

    # Modification structure and types
    for entry in m:
        assert all(k in entry for k in ("id", "field", "old_value", "new_value")), \
            f"Missing keys in modification: {entry}"

        field = entry["field"]
        if field in int_cols:
            assert isinstance(entry["old_value"], int), \
                f"{entry['id']}.{field} old_value should be int, got {type(entry['old_value'])}"
            assert isinstance(entry["new_value"], int), \
                f"{entry['id']}.{field} new_value should be int, got {type(entry['new_value'])}"
        elif field in float_cols:
            assert isinstance(entry["old_value"], (int, float)), \
                f"{entry['id']}.{field} old_value should be float"
            assert isinstance(entry["new_value"], (int, float)), \
                f"{entry['id']}.{field} new_value should be float"
        else:
            assert isinstance(entry["old_value"], str), \
                f"{entry['id']}.{field} old_value should be str"
            assert isinstance(entry["new_value"], str), \
                f"{entry['id']}.{field} new_value should be str"

    # Row arithmetic
    expected_excel = pdf_row_count - len(d)
    assert expected_excel == excel_row_count, \
        f"Row count mismatch: {pdf_row_count} - {len(d)} = {expected_excel} != {excel_row_count}"

    print(f"\n✓ All validations passed")
    print(f"  {pdf_row_count} (PDF) - {len(d)} (deleted) = {excel_row_count} (Excel)")


# ============================================================
# MAIN
# ============================================================
def main():
    # Extract
    df_pdf = extract_pdf_tables(PDF_PATH)
    df_excel = read_excel(EXCEL_PATH)

    # Normalise
    print("\nNormalising PDF data...")
    df_pdf_norm = normalise(df_pdf, ID_COL, INT_COLS, FLOAT_COLS)
    print(f"  PDF normalised: {len(df_pdf_norm)} rows")

    print("Normalising Excel data...")
    df_excel_norm = normalise(df_excel, ID_COL, INT_COLS, FLOAT_COLS)
    print(f"  Excel normalised: {len(df_excel_norm)} rows")

    # Compare
    deleted = find_deleted(df_pdf_norm, df_excel_norm)
    modified = find_modified(df_pdf_norm, df_excel_norm, INT_COLS, FLOAT_COLS)

    # Write
    report = write_report(deleted, modified, OUTPUT_PATH)

    # Validate
    validate(report, len(df_pdf_norm), len(df_excel_norm), INT_COLS, FLOAT_COLS)


if __name__ == "__main__":
    main()
```

---

## 5. Environment Notes

| Tool | Available | Notes |
|------|-----------|-------|
| `pdfplumber` | ✅ `pip install pdfplumber` (usually pre-installed) | Pure Python, no system deps |
| `pandas` | ✅ pre-installed | Use with `openpyxl` for `.xlsx` |
| `openpyxl` | ✅ pre-installed | Required engine for `pd.read_excel` on `.xlsx` |
| `tabula-py` | ✅ but needs Java | Only use as fallback; check `which java` first |
| `poppler-utils` | ✅ (`pdftotext`, `pdfinfo`) | Useful for quick page-count checks: `pdfinfo file.pdf` |
| `libreoffice` | ✅ | Can convert PDF→text as last resort: `libreoffice --headless --convert-to csv` |

---

## 6. Quick Checklist Before Submitting

- [ ] JSON file exists at the expected output path
- [ ] JSON is valid (parseable)
- [ ] Contains both `deleted_employees` and `modified_employees` keys
- [ ] `deleted_employees` is a sorted list of ID strings
- [ ] `modified_employees` entries have all four keys: `id`, `field`, `old_value`, `new_value`
- [ ] `modified_employees` is sorted by `id`
- [ ] Numeric fields (`Salary`, `Years`) have `int` values, not strings
- [ ] Float fields (`Score`) have `float` values
- [ ] Text fields have `str` values
- [ ] Row arithmetic checks out: `len(PDF) - len(deleted) == len(Excel)`
- [ ] No duplicate entries (same id + field appearing twice with identical values)