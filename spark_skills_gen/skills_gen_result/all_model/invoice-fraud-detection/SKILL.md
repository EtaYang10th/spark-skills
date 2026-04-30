---
title: Invoice Fraud Detection from PDF, Excel, and CSV Sources
category: invoice-fraud-detection
domain: document-analysis
tags:
  - pdf-extraction
  - fraud-detection
  - fuzzy-matching
  - data-cross-referencing
  - financial-analysis
dependencies:
  - pymupdf OR pdfplumber
  - openpyxl
  - rapidfuzz OR thefuzz
  - python3
---

# Invoice Fraud Detection Skill

## Overview

Detect fraudulent invoices by cross-referencing data extracted from a multi-page PDF against an approved vendor list (Excel) and valid purchase orders (CSV). Each invoice is checked against a priority-ordered set of fraud criteria, and only flagged invoices are reported.

## High-Level Workflow

1. **Install dependencies** — Ensure PDF extraction, Excel reading, and fuzzy matching libraries are available.
2. **Load vendor data** from the Excel file — Build a lookup dictionary keyed by vendor name.
3. **Load purchase order data** from the CSV — Build a lookup dictionary keyed by PO number.
4. **Extract invoice data from every PDF page** — Each page is one invoice. Parse vendor name, amount, IBAN, and PO number.
5. **Cross-reference each invoice** against vendors and POs, applying fraud rules in strict priority order.
6. **Write the fraud report** as JSON, including only flagged invoices.

## Step 1 — Install Dependencies

The environment may already have some packages. Install defensively.

```bash
pip install --break-system-packages pymupdf openpyxl thefuzz python-Levenshtein rapidfuzz 2>&1 | tail -5
```

Use `--break-system-packages` on Ubuntu 24.04+ where PEP 668 is enforced. If the flag isn't needed, it's harmless.

Prefer `rapidfuzz` over `thefuzz` for speed, but both expose a compatible `fuzz.token_sort_ratio` API.

## Step 2 — Load Vendor Data

```python
import openpyxl

def load_vendors(path: str) -> dict:
    """
    Returns dict: {
        vendor_name_stripped: {
            'id': str,
            'iban': str,
            'original_name': str
        }
    }
    """
    wb = openpyxl.load_workbook(path)
    ws = wb.active

    # Auto-detect header row
    headers = [str(c.value).strip().lower() for c in next(ws.iter_rows(min_row=1, max_row=1))]
    # Expect columns like: vendor_id / vendor id, name, iban
    # Be flexible with header names
    id_col = next(i for i, h in enumerate(headers) if 'id' in h)
    name_col = next(i for i, h in enumerate(headers) if 'name' in h)
    iban_col = next(i for i, h in enumerate(headers) if 'iban' in h)

    vendors = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        vals = list(row)
        vid = str(vals[id_col]).strip()
        name = str(vals[name_col]).strip()
        iban = str(vals[iban_col]).strip()
        vendors[name] = {'id': vid, 'iban': iban, 'original_name': name}

    wb.close()
    return vendors
```

## Step 3 — Load Purchase Orders

```python
import csv

def load_purchase_orders(path: str) -> dict:
    """
    Returns dict: {
        po_number_stripped: {
            'vendor_id': str,
            'amount': float
        }
    }
    """
    pos = {}
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalize header keys — strip whitespace
            row = {k.strip(): v.strip() for k, v in row.items()}
            po_num = row.get('po_number') or row.get('PO Number') or row.get('PO_Number', '')
            po_num = po_num.strip()
            vendor_id = (row.get('vendor_id') or row.get('Vendor ID') or row.get('Vendor_ID', '')).strip()
            amount = float((row.get('amount') or row.get('Amount', '0')).strip())
            pos[po_num] = {'vendor_id': vendor_id, 'amount': amount}
    return pos
```

## Step 4 — Extract Invoice Data from PDF

This is the most error-prone step. Invoice PDFs vary wildly in layout. The strategy:

1. Open the PDF with `fitz` (PyMuPDF) — it's faster and more reliable than pdfplumber for text extraction.
2. Extract text from each page.
3. Parse structured fields using regex patterns.

```python
import fitz  # PyMuPDF
import re
from typing import Optional

def extract_invoices(pdf_path: str) -> list[dict]:
    """
    Extract one invoice per page.
    Returns list of dicts with keys:
        page (1-based), vendor, amount (float), iban (str), po (str or None)
    """
    doc = fitz.open(pdf_path)
    invoices = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text("text")
        inv = parse_invoice_text(text, page_num=page_idx + 1)
        invoices.append(inv)

    doc.close()
    return invoices


def parse_invoice_text(text: str, page_num: int) -> dict:
    """
    Parse a single invoice page's text into structured fields.
    Adapt regex patterns to the actual PDF layout.
    """
    lines = text.strip().split('\n')
    lines_joined = ' '.join(lines)

    # --- Vendor Name ---
    # Common patterns: "Vendor: Acme Corp", "Bill From: Acme Corp", or
    # the vendor name appears on a labeled line.
    vendor = extract_field(lines, lines_joined, [
        r'(?:Vendor|Supplier|Bill\s*From|Company)\s*[:\-]\s*(.+)',
        r'(?:From|Issued\s*By)\s*[:\-]\s*(.+)',
    ])

    # --- Amount ---
    # Look for "Total: $1,234.56" or "Amount: 1234.56" or "Invoice Amount: ..."
    amount_str = extract_field(lines, lines_joined, [
        r'(?:Total|Amount|Invoice\s*Amount|Grand\s*Total|Amount\s*Due)\s*[:\-]?\s*\$?([\d,]+\.?\d*)',
    ])
    amount = parse_amount(amount_str) if amount_str else 0.0

    # --- IBAN ---
    iban = extract_field(lines, lines_joined, [
        r'(?:IBAN)\s*[:\-]\s*([A-Z0-9]+)',
        r'(?:Bank\s*Account|Account)\s*[:\-]\s*([A-Z]{2}[A-Z0-9]+)',
    ])

    # --- PO Number ---
    po = extract_field(lines, lines_joined, [
        r'(?:PO|Purchase\s*Order|P\.?O\.?\s*(?:Number|No\.?|#)?)\s*[:\-]?\s*(PO[\-\s]?\d+)',
        r'(?:PO|Purchase\s*Order)\s*[:\-]?\s*(\S+)',
    ])
    # Normalize: if PO field is empty, "N/A", "None", treat as None
    if po and po.strip().lower() in ('', 'n/a', 'none', '-', 'null'):
        po = None

    return {
        'page': page_num,
        'vendor': vendor.strip() if vendor else '',
        'amount': amount,
        'iban': (iban.strip() if iban else ''),
        'po': po.strip() if po else None,
    }


def extract_field(lines: list[str], joined: str, patterns: list[str]) -> Optional[str]:
    """Try each regex pattern against individual lines first, then the joined text."""
    for pattern in patterns:
        for line in lines:
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        m = re.search(pattern, joined, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def parse_amount(s: str) -> float:
    """Parse an amount string like '1,234.56' or '1234.56' into a float."""
    if not s:
        return 0.0
    s = s.replace(',', '').replace('$', '').strip()
    try:
        return float(s)
    except ValueError:
        return 0.0
```

### Critical Notes on PDF Extraction

- **Always inspect a few pages first** before writing the full parser. Print raw text for pages 1–3 to understand the layout.
- **Vendor names on invoices may differ from the vendor list** — "Ltd" vs "Limited", "Inc." vs "Incorporated", trailing punctuation, etc. This is handled in Step 5 with fuzzy matching.
- **Amount formats vary** — watch for currency symbols, thousands separators, and whitespace.
- **PO numbers may be absent** — some invoices legitimately have no PO. Treat missing PO as `None`, not as "Invalid PO". The fraud check for Invalid PO only applies when a PO *is* present but doesn't exist in the PO file.
- **If text extraction yields garbage**, fall back to `pdfplumber` which handles some layouts better:

```python
import pdfplumber

def extract_text_pdfplumber(pdf_path: str) -> list[str]:
    """Fallback: extract text per page using pdfplumber."""
    texts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or '')
    return texts
```

## Step 5 — Cross-Reference and Detect Fraud

The fraud rules must be applied in **strict priority order**. If multiple rules trigger, report only the first one.

Priority order:
1. **Unknown Vendor** — vendor name not found (even with fuzzy matching)
2. **IBAN Mismatch** — vendor found but IBAN differs
3. **Invalid PO** — PO number present but not in PO file
4. **Amount Mismatch** — PO exists but amount difference > 0.01
5. **Vendor Mismatch** — PO exists but linked to a different vendor ID

```python
from rapidfuzz import fuzz  # or: from thefuzz import fuzz

FUZZY_THRESHOLD = 80  # token_sort_ratio score

def match_vendor(invoice_vendor: str, vendors: dict) -> Optional[dict]:
    """
    Try exact match first, then fuzzy match.
    Returns the vendor dict if matched, else None.
    """
    # Exact match
    if invoice_vendor in vendors:
        return vendors[invoice_vendor]

    # Fuzzy match
    best_name = None
    best_score = 0
    inv_lower = invoice_vendor.lower()

    for vname in vendors:
        score = fuzz.token_sort_ratio(inv_lower, vname.lower())
        if score > best_score:
            best_score = score
            best_name = vname

    if best_score >= FUZZY_THRESHOLD:
        return vendors[best_name]

    return None


def detect_fraud(invoices: list[dict], vendors: dict, pos: dict) -> list[dict]:
    """
    Check each invoice against fraud criteria in priority order.
    Returns list of flagged invoices.
    """
    flagged = []

    for inv in invoices:
        reason = check_invoice(inv, vendors, pos)
        if reason:
            entry = {
                'invoice_page_number': inv['page'],
                'vendor_name': inv['vendor'],
                'invoice_amount': inv['amount'],
                'iban': inv['iban'],
                'po_number': inv['po'],  # None if missing
                'reason': reason,
            }
            flagged.append(entry)

    return flagged


def check_invoice(inv: dict, vendors: dict, pos: dict) -> Optional[str]:
    """
    Apply fraud rules in priority order. Return the reason string or None if clean.
    """
    vendor_name = inv['vendor']
    invoice_iban = inv['iban']
    invoice_amount = inv['amount']
    po_number = inv['po']

    # 1. Unknown Vendor
    matched_vendor = match_vendor(vendor_name, vendors)
    if matched_vendor is None:
        return 'Unknown Vendor'

    # 2. IBAN Mismatch
    if invoice_iban != matched_vendor['iban']:
        return 'IBAN Mismatch'

    # 3. Invalid PO (only if PO is present)
    if po_number is None:
        # No PO on invoice — this alone is NOT fraud per the spec.
        # But if the task requires a PO and it's missing, treat as Invalid PO.
        # Check the task description carefully. The standard rule:
        # "The PO number doesn't exist in purchase_orders.csv"
        # A missing PO (None) doesn't "exist" in the CSV, so flag it.
        return 'Invalid PO'

    if po_number not in pos:
        return 'Invalid PO'

    # 4. Amount Mismatch (tolerance: 0.01)
    po_data = pos[po_number]
    if abs(po_data['amount'] - invoice_amount) > 0.01:
        return 'Amount Mismatch'

    # 5. Vendor Mismatch
    if po_data['vendor_id'] != matched_vendor['id']:
        return 'Vendor Mismatch'

    # Clean invoice
    return None
```

### Important: Handling Missing PO Numbers

The spec says: *"The PO number doesn't exist in `purchase_orders.csv`."*

A `None`/missing PO technically doesn't exist in the CSV. However, **read the task description carefully** — some variants treat a missing PO as automatically invalid, others don't. The safest approach:

- If the invoice has **no PO field at all** (the field is absent from the PDF text), set `po_number` to `None` in the output and flag as `"Invalid PO"`.
- If the invoice has a PO field but it's empty/N/A, same treatment.

## Step 6 — Write the Fraud Report

```python
import json

def write_report(flagged: list[dict], output_path: str):
    """Write the fraud report as JSON."""
    # Ensure amount is a float with 2 decimal places in the JSON
    for entry in flagged:
        entry['invoice_amount'] = round(entry['invoice_amount'], 2)

    with open(output_path, 'w') as f:
        json.dump(flagged, f, indent=2)

    print(f"Wrote {len(flagged)} flagged invoices to {output_path}")
```

## Complete Orchestration Script

```python
#!/usr/bin/env python3
"""
Invoice Fraud Detection — Full Pipeline
Usage: python3 detect_fraud.py
"""

import csv
import json
import re
from typing import Optional

import fitz  # PyMuPDF
import openpyxl
from rapidfuzz import fuzz

# ── Configuration ──────────────────────────────────────────────
PDF_PATH = '/root/invoices.pdf'
VENDORS_PATH = '/root/vendors.xlsx'
PO_PATH = '/root/purchase_orders.csv'
OUTPUT_PATH = '/root/fraud_report.json'
FUZZY_THRESHOLD = 80

# ── Load Vendors ───────────────────────────────────────────────
def load_vendors(path):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [str(c.value).strip().lower() for c in next(ws.iter_rows(min_row=1, max_row=1))]
    id_col = next(i for i, h in enumerate(headers) if 'id' in h)
    name_col = next(i for i, h in enumerate(headers) if 'name' in h)
    iban_col = next(i for i, h in enumerate(headers) if 'iban' in h)
    vendors = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        vals = list(row)
        vid = str(vals[id_col]).strip()
        name = str(vals[name_col]).strip()
        iban = str(vals[iban_col]).strip()
        vendors[name] = {'id': vid, 'iban': iban}
    wb.close()
    return vendors

# ── Load Purchase Orders ───────────────────────────────────────
def load_pos(path):
    pos = {}
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            po_num = (row.get('po_number') or row.get('PO Number', '')).strip()
            vendor_id = (row.get('vendor_id') or row.get('Vendor ID', '')).strip()
            amount = float((row.get('amount') or row.get('Amount', '0')).strip())
            pos[po_num] = {'vendor_id': vendor_id, 'amount': amount}
    return pos

# ── Parse Amount ───────────────────────────────────────────────
def parse_amount(s):
    if not s:
        return 0.0
    s = re.sub(r'[^\d.]', '', s)
    try:
        return float(s)
    except ValueError:
        return 0.0

# ── Extract Field via Regex ────────────────────────────────────
def extract_field(lines, joined, patterns):
    for pattern in patterns:
        for line in lines:
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        m = re.search(pattern, joined, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

# ── Extract Invoices from PDF ──────────────────────────────────
def extract_invoices(pdf_path):
    doc = fitz.open(pdf_path)
    invoices = []
    for i in range(len(doc)):
        text = doc[i].get_text("text")
        lines = text.strip().split('\n')
        joined = ' '.join(lines)

        vendor = extract_field(lines, joined, [
            r'(?:Vendor|Supplier|Bill\s*From|Company|From|Issued\s*By)\s*[:\-]\s*(.+)',
        ])

        amount_str = extract_field(lines, joined, [
            r'(?:Total|Amount|Invoice\s*Amount|Grand\s*Total|Amount\s*Due)\s*[:\-]?\s*\$?([\d,]+\.?\d*)',
        ])

        iban = extract_field(lines, joined, [
            r'(?:IBAN)\s*[:\-]\s*([A-Z0-9]+)',
            r'(?:Bank\s*Account|Account)\s*[:\-]\s*([A-Z]{2}[A-Z0-9]+)',
        ])

        po = extract_field(lines, joined, [
            r'(?:PO|Purchase\s*Order|P\.?O\.?\s*(?:Number|No\.?|#)?)\s*[:\-]?\s*(PO[\-\s]?\d+)',
        ])
        if po and po.strip().lower() in ('', 'n/a', 'none', '-', 'null'):
            po = None

        invoices.append({
            'page': i + 1,
            'vendor': (vendor or '').strip(),
            'amount': parse_amount(amount_str),
            'iban': (iban or '').strip(),
            'po': po.strip() if po else None,
        })
    doc.close()
    return invoices

# ── Fuzzy Vendor Matching ──────────────────────────────────────
def match_vendor(name, vendors):
    if name in vendors:
        return vendors[name]
    best_name, best_score = None, 0
    nl = name.lower()
    for vn in vendors:
        score = fuzz.token_sort_ratio(nl, vn.lower())
        if score > best_score:
            best_score = score
            best_name = vn
    if best_score >= FUZZY_THRESHOLD:
        return vendors[best_name]
    return None

# ── Fraud Detection ────────────────────────────────────────────
def check_invoice(inv, vendors, pos):
    v = match_vendor(inv['vendor'], vendors)
    if v is None:
        return 'Unknown Vendor'
    if inv['iban'] != v['iban']:
        return 'IBAN Mismatch'
    if inv['po'] is None or inv['po'] not in pos:
        return 'Invalid PO'
    po = pos[inv['po']]
    if abs(po['amount'] - inv['amount']) > 0.01:
        return 'Amount Mismatch'
    if po['vendor_id'] != v['id']:
        return 'Vendor Mismatch'
    return None

# ── Main ───────────────────────────────────────────────────────
def main():
    vendors = load_vendors(VENDORS_PATH)
    pos = load_pos(PO_PATH)
    invoices = extract_invoices(PDF_PATH)

    print(f"Loaded {len(vendors)} vendors, {len(pos)} POs, {len(invoices)} invoices")

    flagged = []
    for inv in invoices:
        reason = check_invoice(inv, vendors, pos)
        if reason:
            flagged.append({
                'invoice_page_number': inv['page'],
                'vendor_name': inv['vendor'],
                'invoice_amount': round(inv['amount'], 2),
                'iban': inv['iban'],
                'po_number': inv['po'],
                'reason': reason,
            })

    with open(OUTPUT_PATH, 'w') as f:
        json.dump(flagged, f, indent=2)

    print(f"Flagged {len(flagged)} / {len(invoices)} invoices -> {OUTPUT_PATH}")

if __name__ == '__main__':
    main()
```

## Debugging and Verification Strategy

### Before running the full pipeline, always inspect the raw data:

```python
# 1. Print raw text from first 3 PDF pages to calibrate regex
import fitz
doc = fitz.open('/root/invoices.pdf')
for i in range(min(3, len(doc))):
    print(f"=== PAGE {i+1} ===")
    print(doc[i].get_text("text"))
    print()
doc.close()

# 2. Print vendor data to confirm column mapping
import openpyxl
wb = openpyxl.load_workbook('/root/vendors.xlsx')
ws = wb.active
for row in ws.iter_rows(max_row=5, values_only=True):
    print(row)
wb.close()

# 3. Print first few POs
import csv
with open('/root/purchase_orders.csv') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        if i >= 5: break
        print(row)
```

### After generating the report, verify edge cases:

```python
# Spot-check: print invoices that were NOT flagged
import json
with open('/root/fraud_report.json') as f:
    flagged = json.load(f)
flagged_pages = {e['invoice_page_number'] for e in flagged}
total_pages = len(fitz.open('/root/invoices.pdf'))
clean_pages = [p for p in range(1, total_pages + 1) if p not in flagged_pages]
print(f"Clean pages ({len(clean_pages)}): {clean_pages}")
# Manually verify a few clean pages to ensure they truly pass all checks
```

## Common Pitfalls

### 1. Regex patterns don't match the actual PDF layout
**Symptom**: Vendor name, amount, or IBAN extracted as `None` or wrong value.
**Fix**: Always print raw text from 2–3 pages before writing regex. Adjust patterns to match the actual field labels and formatting in the PDF.

### 2. Fuzzy matching threshold too high or too low
**Symptom**: Legitimate vendors flagged as "Unknown Vendor" (threshold too high) or fraudulent vendors matched to real ones (threshold too low).
**Fix**: Use `token_sort_ratio` with a threshold of 80. This handles "Ltd" vs "Limited", "Inc." vs "Incorporated", minor typos, and word reordering. Print match scores during debugging to calibrate.

### 3. Amount parsing fails on currency formatting
**Symptom**: `$1,234.56` parsed as `1.0` or `0.0`.
**Fix**: Strip all non-numeric characters except `.` before converting to float. Use `re.sub(r'[^\d.]', '', s)`.

### 4. PO number normalization mismatch
**Symptom**: PO exists in CSV but not matched because of whitespace, dash differences ("PO-1001" vs "PO 1001").
**Fix**: Normalize PO numbers by stripping whitespace. If the PDF uses spaces where the CSV uses dashes, normalize both to the same format.

### 5. Forgetting priority order of fraud rules
**Symptom**: Invoice flagged as "Amount Mismatch" when it should be "Unknown Vendor".
**Fix**: Always check rules in this exact order: Unknown Vendor → IBAN Mismatch → Invalid PO → Amount Mismatch → Vendor Mismatch. Return on the first match.

### 6. Treating missing PO as non-fraudulent
**Symptom**: Invoices with no PO number pass all checks and aren't flagged.
**Fix**: If the PO field is `None` (missing from invoice), it doesn't exist in the PO file, so it should be flagged as "Invalid PO". Set `po_number` to `null` in the JSON output.

### 7. Amount comparison using exact equality
**Symptom**: Amounts like `500.009` and `500.01` incorrectly flagged.
**Fix**: Use `abs(po_amount - invoice_amount) > 0.01` as the threshold. This is a strict `>`, not `>=`.

### 8. Not using `--break-system-packages` on Ubuntu 24.04
**Symptom**: `pip install` fails with "externally-managed-environment" error.
**Fix**: Always pass `--break-system-packages` flag on Ubuntu 24.04+ systems.

### 9. JSON output includes clean invoices
**Symptom**: Validator fails because the report contains entries with no fraud reason.
**Fix**: Only append to the flagged list when `check_invoice` returns a non-None reason.

### 10. Vendor name has trailing whitespace or special characters
**Symptom**: Exact match fails even though the name looks identical.
**Fix**: Always `.strip()` vendor names from all three sources (PDF, Excel, CSV). Consider stripping non-breaking spaces (`\xa0`) as well: `name.replace('\xa0', ' ').strip()`.

## Library Reference

| Library | Import | Purpose |
|---------|--------|---------|
| PyMuPDF | `import fitz` | Fast PDF text extraction |
| pdfplumber | `import pdfplumber` | Alternative PDF extraction (better for tables) |
| openpyxl | `import openpyxl` | Read `.xlsx` files |
| rapidfuzz | `from rapidfuzz import fuzz` | Fast fuzzy string matching |
| thefuzz | `from thefuzz import fuzz` | Fuzzy string matching (slower, compatible API) |

## Output Format Checklist

- [ ] JSON array at top level
- [ ] Only flagged invoices included (no clean ones)
- [ ] `invoice_page_number`: 1-based integer
- [ ] `vendor_name`: string as extracted from PDF
- [ ] `invoice_amount`: float (e.g., `5000.00`, not `"5000.00"`)
- [ ] `iban`: string
- [ ] `po_number`: string or `null` (JSON null, not the string `"null"`)
- [ ] `reason`: one of exactly: `"Unknown Vendor"`, `"IBAN Mismatch"`, `"Invalid PO"`, `"Amount Mismatch"`, `"Vendor Mismatch"`