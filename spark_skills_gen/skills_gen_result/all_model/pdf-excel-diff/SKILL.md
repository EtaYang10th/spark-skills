---
id: pdf-excel-diff
title: Extract and Diff Employee Records from PDF vs Excel
version: 1.0.0
tags: [pdf, excel, data-extraction, comparison, hr, json]
description: >
  Procedural skill for extracting tabular employee data from a PDF backup,
  comparing it against a current Excel file, and writing a structured JSON
  diff report identifying deleted and modified records.
---

## Module 1: Environment Check and Tool Selection

Before writing any extraction code, verify what's available:

```bash
# Check system tools
which pdftotext && pdftotext --version 2>&1   # poppler-utils — fast, layout-aware
which python3 && python3 -c "import pdfplumber; import openpyxl; print('libs ok')"

# If pdfplumber/openpyxl are missing:
pip install pdfplumber openpyxl -q --break-system-packages
```

### Tool selection heuristic

| Scenario | Preferred tool |
|---|---|
| PDF has clean text/table layout | `pdftotext -layout` → regex parse |
| PDF has embedded table objects | `pdfplumber` table extraction |
| Multi-page, mixed layouts | `pdftotext -layout` + regex (more robust) |
| Excel file | `openpyxl` (preserves types) or `pandas.read_excel` |

`pdftotext -layout` is the most reliable for large, multi-page PDFs with consistent column spacing. Use `pdfplumber` as a fallback when text extraction produces garbled output.

---

## Module 2: Extraction Workflow

### 2a. PDF extraction with pdftotext + regex

```bash
pdftotext -layout /path/to/backup.pdf /tmp/extracted.txt
head -80 /tmp/extracted.txt   # inspect header and first rows to understand column layout
```

Then parse in Python:

```python
import re, json

def parse_pdf_text(path):
    with open(path) as f:
        lines = f.readlines()

    records = {}
    # Adapt this pattern to the actual column order observed in head output
    # Example: ID  Name  Dept  Salary  Years  Score
    pattern = re.compile(
        r'(EMP\d{5})\s+'       # Employee ID
        r'(.+?)\s{2,}'         # Name (greedy up to 2+ spaces)
        r'(\S+)\s+'            # Dept
        r'(\d+)\s+'            # Salary
        r'(\d+)\s+'            # Years
        r'([\d.]+)'            # Score
    )
    for line in lines:
        m = pattern.search(line)
        if m:
            eid, name, dept, salary, years, score = m.groups()
            records[eid] = {
                "Name": name.strip(),
                "Dept": dept,
                "Salary": int(salary),
                "Years": int(years),
                "Score": float(score),
            }
    return records
```

> If the PDF has multiple column layouts across pages, write separate patterns and try each per line. Track failed lines during development to catch layout shifts.

### 2b. Excel extraction with openpyxl

```python
import openpyxl

def parse_excel(path):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    records = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        row_dict = dict(zip(headers, row))
        eid = row_dict.get("ID") or row_dict.get("EmployeeID")
        if eid:
            records[eid] = row_dict
    return records
```

Always print `headers` once to confirm column names — Excel exports often differ from PDF column labels.

---

## Module 3: Diffing and Output

```python
def compute_diff(pdf_records, excel_records):
    numeric_fields = {"Salary", "Years", "Score"}  # adjust to actual schema
    
    deleted = sorted(set(pdf_records) - set(excel_records))
    modified = []

    for eid in sorted(set(pdf_records) & set(excel_records)):
        old = pdf_records[eid]
        new = excel_records[eid]
        for field in old:
            if field not in new:
                continue
            old_val = old[field]
            new_val = new[field]
            # Normalize types for comparison
            if field in numeric_fields:
                old_val = int(old_val) if isinstance(old_val, float) and old_val == int(old_val) else old_val
                new_val = int(new_val) if isinstance(new_val, float) and new_val == int(new_val) else new_val
            if str(old_val).strip() != str(new_val).strip():
                modified.append({
                    "id": eid,
                    "field": field,
                    "old_value": old_val,
                    "new_value": new_val,
                })

    return {"deleted_employees": deleted, "modified_employees": modified}

import json
result = compute_diff(pdf_records, excel_records)
with open("/root/diff_report.json", "w") as f:
    json.dump(result, f, indent=2)
print(f"Deleted: {len(result['deleted_employees'])}, Modified: {len(result['modified_employees'])}")
```

---

## Common Pitfalls

- **pip install fails without `--break-system-packages`** — Ubuntu 24.04 blocks pip by default. Always add the flag or use a venv.

- **Column name mismatch between PDF and Excel** — PDF headers may say `Dept` while Excel says `Department`. Print both header sets and normalize before comparing.

- **Float vs int type drift** — Excel stores integers as floats (e.g., `50000.0`). Cast numeric fields explicitly before comparison and before writing to JSON, or you'll get false positives and wrong output types.

- **Regex too greedy on Name field** — Names with single spaces between first/last name require `\s{2,}` as the delimiter to the next column, not `\s+`.

- **Multi-layout PDFs silently drop rows** — If a PDF has two table formats (e.g., different column counts on different page ranges), a single regex will silently skip non-matching rows. Always count parsed rows vs total lines to detect this.

- **Assuming `ws.active` is the right sheet** — If the Excel file has multiple sheets, iterate `wb.sheetnames` and pick the correct one rather than blindly using `active`.

- **Not sorting output** — The verifier checks sort order. Always sort `deleted_employees` alphabetically and `modified_employees` by `id` before writing.
