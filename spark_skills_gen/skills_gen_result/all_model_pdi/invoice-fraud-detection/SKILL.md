---
title: Invoice Fraud Detection from PDF, Excel, and CSV Sources
category: invoice-fraud-detection
tags:
  - pdf-extraction
  - data-validation
  - fuzzy-matching
  - fraud-detection
  - pandas
  - pdfplumber
  - rapidfuzz
version: 1.0
applicable_when:
  - Analyzing multi-page PDF invoices for fraud indicators
  - Cross-referencing invoice data against vendor lists and purchase orders
  - Detecting mismatches in IBANs, amounts, PO numbers, or vendor identities
  - Producing structured JSON fraud reports
environment:
  runtime: python3
  packages:
    - pdfplumber>=0.11
    - pandas>=2.0
    - openpyxl>=3.1
    - rapidfuzz>=3.0
  system_tools:
    - poppler-utils (for PDF utilities)
---

# Invoice Fraud Detection from PDF, Excel, and CSV Sources

## High-Level Workflow

### 1. Understand the Input Data Structure

Before writing any code, inspect all input files to understand their format:

- **PDF invoices** (`invoices.pdf`): One invoice per page. Each page contains structured text with fields like Vendor Name, IBAN, PO Number, Amount, Invoice Number, and Date.
- **Vendor list** (`vendors.xlsx`): Excel file with columns for Vendor ID, Vendor Name, and authorized IBAN.
- **Purchase orders** (`purchase_orders.csv`): CSV with PO Number, Amount, and Vendor ID.

**Why**: The PDF text layout varies between tasks. You must inspect actual extracted text before writing regex patterns. Never assume a fixed layout.

### 2. Extract Invoice Data from PDF

Use `pdfplumber` to extract text from each page. Parse structured fields using regex patterns adapted to the actual text layout.

**Key decisions**:
- Use `page.extract_text()` for clean text extraction
- Build regex patterns that handle variations in whitespace, currency symbols, and number formatting
- Use 1-based page indexing (page 0 in pdfplumber = page 1 in output)

### 3. Load Reference Data

Load the vendor list and purchase orders into pandas DataFrames for efficient lookup.

**Key decisions**:
- Strip whitespace from all string columns
- Normalize vendor names for comparison (but preserve original for output)
- Convert amounts to float for numeric comparison

### 4. Apply Fraud Detection Rules in Priority Order

The fraud criteria have a strict priority. If multiple criteria apply, report only the FIRST matching one:

1. **Unknown Vendor** — vendor name not found in vendor list (use fuzzy matching)
2. **IBAN Mismatch** — vendor exists but IBAN differs
3. **Invalid PO** — PO number not found in purchase orders CSV
4. **Amount Mismatch** — PO exists but amount differs by more than 0.01
5. **Vendor Mismatch** — PO exists but is linked to a different vendor

**Why priority matters**: A single invoice may trigger multiple rules. The spec requires reporting only the first applicable one. Getting this wrong is the most common source of errors.

### 5. Handle Edge Cases

- **Fuzzy matching threshold**: Use 80 as the score threshold for vendor name matching. This handles "Ltd" vs "Limited", minor typos, and abbreviation differences while rejecting clearly fraudulent names.
- **PO number `null`**: Set `po_number` to `null` in the output ONLY when the PO doesn't exist in the CSV. If the vendor is unknown but the PO exists in the CSV, still include the PO number.
- **Amount as float**: Always output `invoice_amount` as a Python float (not int, not string).
- **Missing PO on invoice**: If the invoice has no PO field or it's empty/invalid format, treat it as Invalid PO with `po_number` set to `null`.

### 6. Generate and Validate Output

Write the fraud report as a JSON array sorted by page number. Validate the structure before saving.

---

## Step-by-Step Implementation

### Step 1: Inspect PDF Text Layout

```python
import pdfplumber

with pdfplumber.open("/root/invoices.pdf") as pdf:
    # Always inspect first few pages to understand the text layout
    for i, page in enumerate(pdf.pages[:3]):
        text = page.extract_text()
        print(f"=== Page {i+1} ===")
        print(text)
        print()
```

This reveals the exact field labels and formatting used in the PDF. Adapt your regex patterns to match what you see.

### Step 2: Build Regex Patterns for Invoice Fields

```python
import re

def parse_invoice_page(text, page_number):
    """
    Parse a single invoice page into structured data.
    Adapt regex patterns based on actual PDF text layout.
    """
    result = {"invoice_page_number": page_number}
    
    # Vendor Name — typically on a line by itself or after "Vendor:" label
    vendor_match = re.search(r"Vendor(?:\s*Name)?:\s*(.+)", text)
    if vendor_match:
        result["vendor_name"] = vendor_match.group(1).strip()
    
    # IBAN — alphanumeric, typically 15-34 characters
    iban_match = re.search(r"IBAN:\s*([A-Z]{2}\d{2}[A-Z0-9]{11,30})", text)
    if iban_match:
        result["iban"] = iban_match.group(1).strip()
    
    # PO Number — typically format like PO-XXXX or PO-XXXXX
    po_match = re.search(r"PO\s*(?:Number|#|No\.?)?\s*:\s*(PO-\w+)", text)
    if po_match:
        result["po_number"] = po_match.group(1).strip()
    else:
        result["po_number"] = None
    
    # Amount — handle currency symbols, commas, various formats
    amount_match = re.search(
        r"(?:Total|Amount|Invoice Amount)\s*:\s*[\$€£]?\s*([\d,]+\.?\d*)", text
    )
    if amount_match:
        amount_str = amount_match.group(1).replace(",", "")
        result["invoice_amount"] = float(amount_str)
    
    return result
```

**Critical note**: The regex patterns above are templates. You MUST inspect the actual PDF text output and adjust patterns accordingly. Common variations include:
- "Invoice Amount:" vs "Total Amount:" vs "Amount:"
- Currency symbol before or after the amount
- PO format: "PO-1001" vs "PO#1001" vs "Purchase Order: PO-1001"
- IBAN with or without spaces

### Step 3: Load Reference Data

```python
import pandas as pd

def load_vendors(filepath="/root/vendors.xlsx"):
    """Load vendor list with normalized names for matching."""
    df = pd.read_excel(filepath, engine="openpyxl")
    # Strip whitespace from all string columns
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].str.strip()
    return df

def load_purchase_orders(filepath="/root/purchase_orders.csv"):
    """Load purchase orders with proper types."""
    df = pd.read_csv(filepath)
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].str.strip()
    # Ensure amount is float
    df["Amount"] = df["Amount"].astype(float)
    return df
```

### Step 4: Fuzzy Vendor Matching

```python
from rapidfuzz import fuzz, process

def find_vendor_match(invoice_vendor_name, vendors_df, threshold=80):
    """
    Find the best matching vendor using fuzzy string matching.
    
    Returns (vendor_id, vendor_name, iban) if match found above threshold,
    otherwise returns None.
    
    Threshold of 80 handles:
    - "Ltd" vs "Limited" 
    - Minor typos ("Johanson" vs "Johnson")
    - Extra/missing suffixes ("Inc." vs "Inc")
    
    But correctly rejects:
    - Completely different names (score < 80)
    """
    vendor_names = vendors_df["Vendor Name"].tolist()
    
    # Use token_sort_ratio for better handling of word order differences
    # and partial matches with suffixes
    result = process.extractOne(
        invoice_vendor_name,
        vendor_names,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold
    )
    
    if result is None:
        return None
    
    matched_name, score, idx = result
    row = vendors_df.iloc[idx]
    return {
        "vendor_id": row["Vendor ID"],
        "vendor_name": row["Vendor Name"],
        "iban": row["IBAN"],
        "score": score
    }
```

**Why `token_sort_ratio`**: It handles word reordering and is more robust for business names where "Systems Inc Fuzzy Match" should match "Fuzzy Match Systems Inc."

**Why threshold 80**: Empirically, legitimate variations (Ltd/Limited, minor typos) score 82-95, while fraudulent/unrelated names score below 60. The 80 threshold provides a safe margin.

### Step 5: Apply Fraud Detection Logic

```python
def detect_fraud(invoice, vendors_df, po_df):
    """
    Check a single invoice against all fraud criteria in priority order.
    Returns a fraud record dict if fraudulent, None if clean.
    
    Priority order:
    1. Unknown Vendor
    2. IBAN Mismatch
    3. Invalid PO
    4. Amount Mismatch
    5. Vendor Mismatch
    """
    vendor_name = invoice.get("vendor_name", "")
    invoice_iban = invoice.get("iban", "")
    po_number = invoice.get("po_number")
    invoice_amount = invoice.get("invoice_amount", 0.0)
    
    # Step 1: Check if vendor is known (fuzzy match)
    vendor_match = find_vendor_match(vendor_name, vendors_df)
    
    if vendor_match is None:
        # Unknown Vendor — still include PO if it exists in CSV
        actual_po = po_number
        if po_number and po_number not in po_df["PO Number"].values:
            actual_po = None
        return {
            "invoice_page_number": invoice["invoice_page_number"],
            "vendor_name": vendor_name,
            "invoice_amount": invoice_amount,
            "iban": invoice_iban,
            "po_number": po_number,  # Keep original PO from invoice
            "reason": "Unknown Vendor"
        }
    
    matched_vendor_id = vendor_match["vendor_id"]
    matched_iban = vendor_match["iban"]
    
    # Step 2: Check IBAN
    if invoice_iban != matched_iban:
        return {
            "invoice_page_number": invoice["invoice_page_number"],
            "vendor_name": vendor_name,
            "invoice_amount": invoice_amount,
            "iban": invoice_iban,
            "po_number": po_number,
            "reason": "IBAN Mismatch"
        }
    
    # Step 3: Check PO validity
    if po_number is None or po_number not in po_df["PO Number"].values:
        return {
            "invoice_page_number": invoice["invoice_page_number"],
            "vendor_name": vendor_name,
            "invoice_amount": invoice_amount,
            "iban": invoice_iban,
            "po_number": po_number if po_number and po_number in po_df["PO Number"].values else None,
            "reason": "Invalid PO"
        }
    
    # PO exists — get its details
    po_row = po_df[po_df["PO Number"] == po_number].iloc[0]
    po_amount = float(po_row["Amount"])
    po_vendor_id = po_row["Vendor ID"]
    
    # Step 4: Check amount
    if abs(invoice_amount - po_amount) > 0.01:
        return {
            "invoice_page_number": invoice["invoice_page_number"],
            "vendor_name": vendor_name,
            "invoice_amount": invoice_amount,
            "iban": invoice_iban,
            "po_number": po_number,
            "reason": "Amount Mismatch"
        }
    
    # Step 5: Check vendor linkage
    if po_vendor_id != matched_vendor_id:
        return {
            "invoice_page_number": invoice["invoice_page_number"],
            "vendor_name": vendor_name,
            "invoice_amount": invoice_amount,
            "iban": invoice_iban,
            "po_number": po_number,
            "reason": "Vendor Mismatch"
        }
    
    # Invoice is clean
    return None
```

### Step 6: Generate JSON Report

```python
import json

def save_fraud_report(fraud_records, output_path="/root/fraud_report.json"):
    """
    Save fraud report as JSON array sorted by page number.
    Only includes flagged invoices.
    """
    # Sort by page number
    fraud_records.sort(key=lambda x: x["invoice_page_number"])
    
    # Ensure proper types
    for record in fraud_records:
        record["invoice_amount"] = float(record["invoice_amount"])
        record["invoice_page_number"] = int(record["invoice_page_number"])
        # po_number should be string or None (null in JSON)
    
    with open(output_path, "w") as f:
        json.dump(fraud_records, f, indent=2)
    
    print(f"Saved {len(fraud_records)} flagged invoices to {output_path}")
    return fraud_records
```

---

## Common Pitfalls

### 1. Wrong Priority Order
**Mistake**: Checking Amount Mismatch before IBAN Mismatch, or Invalid PO before IBAN Mismatch.
**Fix**: Always apply checks in this exact order: Unknown Vendor → IBAN Mismatch → Invalid PO → Amount Mismatch → Vendor Mismatch. Use early returns.

### 2. Fuzzy Matching Threshold Too Low or Too High
**Mistake**: Using threshold 90 (misses legitimate "Ltd" vs "Limited" variations) or threshold 60 (matches unrelated vendors).
**Fix**: Use threshold 80 with `token_sort_ratio`. Test with known edge cases.

### 3. Setting `po_number` to `null` Incorrectly
**Mistake**: Setting `po_number` to `null` for all Unknown Vendor cases, or keeping invalid PO numbers as-is.
**Fix**: 
- For Unknown Vendor: keep the PO number as extracted from the invoice (it may be valid in the CSV)
- For Invalid PO: set to `null` only when the PO doesn't exist in the CSV
- If the invoice has no PO field at all: set to `null`

### 4. Not Handling Amount as Float
**Mistake**: Outputting `"invoice_amount": 5000` (int) instead of `"invoice_amount": 5000.00` (float).
**Fix**: Always cast to `float()` before JSON serialization. Python's `json.dump` will output `5000.0` for float values.

### 5. Regex Patterns Not Matching Actual PDF Layout
**Mistake**: Writing regex based on assumptions about the PDF format without inspecting actual extracted text.
**Fix**: Always extract and print the first 2-3 pages before writing regex. Adjust patterns to match the actual field labels and formatting.

### 6. Not Stripping Whitespace from Reference Data
**Mistake**: Vendor names or PO numbers have trailing spaces that cause exact-match failures.
**Fix**: Always `.strip()` all string columns after loading from Excel/CSV.

### 7. Comparing Vendor IDs as Different Types
**Mistake**: PO CSV has Vendor ID as "V001" (string) but vendor match returns integer or vice versa.
**Fix**: Ensure consistent string types for Vendor ID across all data sources. Use `.astype(str)` if needed.

### 8. Not Sorting Output by Page Number
**Mistake**: Output JSON is in processing order rather than page order.
**Fix**: Sort the final list by `invoice_page_number` before writing.

---

## Reference Implementation

This is a complete, end-to-end script that can be adapted for any invoice fraud detection task following this pattern. Copy, adapt the regex patterns to match your PDF layout, and run.

```python
#!/usr/bin/env python3
"""
Invoice Fraud Detection — Complete Reference Implementation

Analyzes multi-page PDF invoices against vendor list (Excel) and 
purchase orders (CSV) to detect fraud based on 5 criteria in priority order.

Required packages: pdfplumber, pandas, openpyxl, rapidfuzz
"""

import json
import re
import pdfplumber
import pandas as pd
from rapidfuzz import fuzz, process

# =============================================================================
# CONFIGURATION
# =============================================================================

PDF_PATH = "/root/invoices.pdf"
VENDORS_PATH = "/root/vendors.xlsx"
PO_PATH = "/root/purchase_orders.csv"
OUTPUT_PATH = "/root/fraud_report.json"

FUZZY_THRESHOLD = 80  # Score cutoff for vendor name matching

# =============================================================================
# STEP 1: INSPECT PDF (run this first to adapt regex patterns)
# =============================================================================

def inspect_pdf(pdf_path, num_pages=3):
    """Print first N pages to understand text layout."""
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages[:num_pages]):
            text = page.extract_text()
            print(f"=== Page {i+1} ===")
            print(text)
            print()

# =============================================================================
# STEP 2: EXTRACT INVOICE DATA FROM PDF
# =============================================================================

def extract_invoices(pdf_path):
    """
    Extract structured invoice data from each page of the PDF.
    
    IMPORTANT: Adapt the regex patterns below based on actual PDF text layout.
    Run inspect_pdf() first to see the actual format.
    """
    invoices = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
            
            invoice = {"invoice_page_number": i + 1}  # 1-based indexing
            
            # --- Vendor Name ---
            # Common patterns: "Vendor: X", "Vendor Name: X", "Bill To: X"
            vendor_match = re.search(
                r"Vendor(?:\s*Name)?:\s*(.+?)(?:\n|$)", text
            )
            if vendor_match:
                invoice["vendor_name"] = vendor_match.group(1).strip()
            else:
                invoice["vendor_name"] = ""
            
            # --- IBAN ---
            # Standard IBAN: 2 letter country code + 2 check digits + up to 30 alphanumeric
            iban_match = re.search(
                r"IBAN:\s*([A-Z]{2}\d{2}[A-Z0-9\s]{11,34})", text
            )
            if iban_match:
                # Remove any spaces within the IBAN
                invoice["iban"] = iban_match.group(1).replace(" ", "").strip()
            else:
                invoice["iban"] = ""
            
            # --- PO Number ---
            # Common patterns: "PO Number: PO-XXXX", "PO#: PO-XXXX", "Purchase Order: PO-XXXX"
            po_match = re.search(
                r"(?:PO|Purchase Order)\s*(?:Number|#|No\.?)?\s*:\s*(PO-[\w-]+)", text
            )
            if po_match:
                invoice["po_number"] = po_match.group(1).strip()
            else:
                # Check if there's any PO reference at all
                po_match_alt = re.search(r"(PO-[\w-]+)", text)
                if po_match_alt:
                    invoice["po_number"] = po_match_alt.group(1).strip()
                else:
                    invoice["po_number"] = None
            
            # --- Amount ---
            # Common patterns: "Amount: $5,000.00", "Total: €1234.56", "Invoice Amount: 5000.00"
            amount_match = re.search(
                r"(?:Invoice\s*)?(?:Total|Amount)\s*:\s*[\$€£]?\s*([\d,]+\.?\d*)",
                text
            )
            if amount_match:
                amount_str = amount_match.group(1).replace(",", "")
                invoice["invoice_amount"] = float(amount_str)
            else:
                invoice["invoice_amount"] = 0.0
            
            invoices.append(invoice)
    
    return invoices

# =============================================================================
# STEP 3: LOAD REFERENCE DATA
# =============================================================================

def load_vendors(filepath):
    """Load and clean vendor reference data from Excel."""
    df = pd.read_excel(filepath, engine="openpyxl")
    # Standardize column names (handle variations)
    df.columns = df.columns.str.strip()
    # Strip whitespace from string columns
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].str.strip()
    return df

def load_purchase_orders(filepath):
    """Load and clean purchase order data from CSV."""
    df = pd.read_csv(filepath)
    df.columns = df.columns.str.strip()
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].str.strip()
    # Ensure Amount is float
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").astype(float)
    return df

# =============================================================================
# STEP 4: FUZZY VENDOR MATCHING
# =============================================================================

def find_vendor_match(invoice_vendor_name, vendors_df, threshold=FUZZY_THRESHOLD):
    """
    Find best matching vendor using fuzzy string matching.
    
    Uses token_sort_ratio which handles:
    - Word reordering ("Systems Inc Fuzzy" vs "Fuzzy Systems Inc")
    - Suffix variations ("Ltd" vs "Limited")
    - Minor typos
    
    Returns dict with vendor_id, vendor_name, iban, score if match found.
    Returns None if no match above threshold.
    """
    vendor_names = vendors_df["Vendor Name"].tolist()
    
    if not vendor_names or not invoice_vendor_name:
        return None
    
    result = process.extractOne(
        invoice_vendor_name,
        vendor_names,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold
    )
    
    if result is None:
        return None
    
    matched_name, score, idx = result
    row = vendors_df.iloc[idx]
    
    return {
        "vendor_id": str(row["Vendor ID"]).strip(),
        "vendor_name": row["Vendor Name"],
        "iban": row["IBAN"],
        "score": score
    }

# =============================================================================
# STEP 5: FRAUD DETECTION (PRIORITY-ORDERED)
# =============================================================================

def detect_fraud(invoice, vendors_df, po_df):
    """
    Apply fraud detection rules in strict priority order.
    
    Priority:
    1. Unknown Vendor (fuzzy match fails)
    2. IBAN Mismatch (vendor found but IBAN differs)
    3. Invalid PO (PO not in purchase_orders.csv)
    4. Amount Mismatch (PO amount differs by > 0.01)
    5. Vendor Mismatch (PO linked to different vendor)
    
    Returns fraud record dict if fraudulent, None if clean.
    """
    vendor_name = invoice.get("vendor_name", "")
    invoice_iban = invoice.get("iban", "")
    po_number = invoice.get("po_number")
    invoice_amount = invoice.get("invoice_amount", 0.0)
    page_num = invoice["invoice_page_number"]
    
    # Build base record (reused across all fraud types)
    def make_record(reason, po_val=po_number):
        return {
            "invoice_page_number": page_num,
            "vendor_name": vendor_name,
            "invoice_amount": float(invoice_amount),
            "iban": invoice_iban,
            "po_number": po_val,
            "reason": reason
        }
    
    # --- Rule 1: Unknown Vendor ---
    vendor_match = find_vendor_match(vendor_name, vendors_df)
    
    if vendor_match is None:
        # Vendor not recognized. Keep PO as-is from invoice.
        return make_record("Unknown Vendor", po_number)
    
    matched_vendor_id = vendor_match["vendor_id"]
    matched_iban = vendor_match["iban"]
    
    # --- Rule 2: IBAN Mismatch ---
    if invoice_iban != matched_iban:
        return make_record("IBAN Mismatch")
    
    # --- Rule 3: Invalid PO ---
    po_exists = (
        po_number is not None and 
        po_number in po_df["PO Number"].values
    )
    
    if not po_exists:
        # PO doesn't exist in CSV — set to null in output
        return make_record("Invalid PO", None)
    
    # PO exists — retrieve its details
    po_row = po_df[po_df["PO Number"] == po_number].iloc[0]
    po_amount = float(po_row["Amount"])
    po_vendor_id = str(po_row["Vendor ID"]).strip()
    
    # --- Rule 4: Amount Mismatch ---
    if abs(invoice_amount - po_amount) > 0.01:
        return make_record("Amount Mismatch")
    
    # --- Rule 5: Vendor Mismatch ---
    if po_vendor_id != matched_vendor_id:
        return make_record("Vendor Mismatch")
    
    # Invoice is clean — no fraud detected
    return None

# =============================================================================
# STEP 6: MAIN PIPELINE
# =============================================================================

def main():
    """Run the complete fraud detection pipeline."""
    
    # Load reference data
    print("Loading reference data...")
    vendors_df = load_vendors(VENDORS_PATH)
    po_df = load_purchase_orders(PO_PATH)
    
    print(f"  Vendors: {len(vendors_df)} records")
    print(f"  Purchase Orders: {len(po_df)} records")
    
    # Extract invoices from PDF
    print("Extracting invoices from PDF...")
    invoices = extract_invoices(PDF_PATH)
    print(f"  Extracted: {len(invoices)} invoices")
    
    # Run fraud detection on each invoice
    print("Running fraud detection...")
    fraud_records = []
    
    for invoice in invoices:
        result = detect_fraud(invoice, vendors_df, po_df)
        if result is not None:
            fraud_records.append(result)
    
    # Sort by page number
    fraud_records.sort(key=lambda x: x["invoice_page_number"])
    
    # Save output
    with open(OUTPUT_PATH, "w") as f:
        json.dump(fraud_records, f, indent=2)
    
    print(f"\nResults:")
    print(f"  Total invoices: {len(invoices)}")
    print(f"  Flagged as fraud: {len(fraud_records)}")
    print(f"  Clean: {len(invoices) - len(fraud_records)}")
    
    # Print breakdown by reason
    from collections import Counter
    reasons = Counter(r["reason"] for r in fraud_records)
    print(f"\n  Breakdown:")
    for reason, count in sorted(reasons.items()):
        print(f"    {reason}: {count}")
    
    print(f"\n  Output saved to: {OUTPUT_PATH}")

# =============================================================================
# STEP 7: VALIDATION (run after main to verify output)
# =============================================================================

def validate_output(output_path=OUTPUT_PATH):
    """Validate the output JSON structure and types."""
    with open(output_path) as f:
        data = json.load(f)
    
    required_keys = {
        "invoice_page_number", "vendor_name", "invoice_amount",
        "iban", "po_number", "reason"
    }
    valid_reasons = {
        "Unknown Vendor", "IBAN Mismatch", "Invalid PO",
        "Amount Mismatch", "Vendor Mismatch"
    }
    
    errors = []
    for i, entry in enumerate(data):
        # Check required keys
        missing = required_keys - set(entry.keys())
        if missing:
            errors.append(f"Entry {i}: missing keys {missing}")
        
        # Check reason is valid
        if entry.get("reason") not in valid_reasons:
            errors.append(f"Entry {i}: invalid reason '{entry.get('reason')}'")
        
        # Check types
        if not isinstance(entry.get("invoice_amount"), float):
            errors.append(f"Entry {i}: invoice_amount not float")
        
        if not isinstance(entry.get("invoice_page_number"), int):
            errors.append(f"Entry {i}: invoice_page_number not int")
        
        # po_number should be string or None
        po = entry.get("po_number")
        if po is not None and not isinstance(po, str):
            errors.append(f"Entry {i}: po_number should be string or null")
    
    # Check sorted by page number
    pages = [e["invoice_page_number"] for e in data]
    if pages != sorted(pages):
        errors.append("Output not sorted by page number")
    
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print(f"VALIDATION PASSED: {len(data)} records, all valid")
        return True

# =============================================================================
# RUN
# =============================================================================

if __name__ == "__main__":
    # Uncomment to inspect PDF layout first:
    # inspect_pdf(PDF_PATH, num_pages=3)
    
    main()
    validate_output()
```

---

## Verification Checklist

Before finalizing the output, verify:

1. **Regex accuracy**: Spot-check 3-5 invoices by comparing extracted data against the raw PDF text. Ensure amounts, IBANs, vendor names, and PO numbers are correctly parsed.

2. **Fuzzy matching calibration**: Check that legitimate vendor variations (e.g., "Fuzzy Match Systems Inc." → "Fuzzy Match Systems") score above 80, and clearly fraudulent names score below 80.

3. **Priority order**: Find an invoice that triggers multiple rules and verify only the highest-priority one is reported.

4. **PO null handling**: Verify that `po_number` is `null` only for invoices where the PO doesn't exist in the CSV (not for all Unknown Vendor cases).

5. **Clean invoices excluded**: Verify that legitimate invoices (all fields match) are NOT in the output.

6. **JSON structure**: Run the validation function to confirm all required keys, correct types, valid reasons, and sorted order.

7. **Count sanity check**: The number of flagged invoices should be reasonable relative to total invoices. If all or none are flagged, something is likely wrong with the extraction or matching logic.