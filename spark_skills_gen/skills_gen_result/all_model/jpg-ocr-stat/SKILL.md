---
name: receipt-jpg-ocr-to-excel
description: Extract dates and total amounts from scanned receipt JPG/PNG images using Tesseract OCR, then write a strict one-sheet Excel workbook with filename-sorted rows and nulls for failed fields.
category: document-ocr
tags:
  - ocr
  - receipts
  - pytesseract
  - pillow
  - openpyxl
  - excel
  - image-processing
tools:
  - python3
  - pytesseract
  - Pillow
  - openpyxl
  - tesseract-ocr
assumptions:
  - Input consists of scanned receipt images in a directory.
  - Receipts mainly contain English text and digits.
  - Required output is an .xlsx workbook with exact schema and sheet constraints.
  - OCR may be noisy, rotated, or split important keyword/value pairs across lines.
outputs:
  - Excel workbook with exactly one sheet named results
  - Columns in exact order: filename, date, total_amount
  - date in ISO format YYYY-MM-DD or null
  - total_amount as string with exactly two decimals or null
---

# Receipt OCR to Strict Excel Summary

Use this skill when you need to:

- Read many scanned receipt image files from a directory
- OCR the text
- Extract a **date** and **final total amount**
- Save results into a **strictly formatted Excel file**
- Preserve deterministic ordering and validator-friendly null handling

This workflow is designed for tasks where downstream validation is exacting: sheet names, column order, row ordering, and formatting all matter.

---

## When to choose this approach

Choose this workflow when:

1. The source documents are raster images (`.jpg`, `.jpeg`, `.png`, etc.), not PDFs.
2. Text is mostly English letters and numbers.
3. The data of interest is usually present as printed receipt text:
   - transaction date
   - final payable amount
4. The environment includes:
   - `pytesseract`
   - `Pillow`
   - `openpyxl`
   - system `tesseract-ocr`

This approach works well when receipts follow common retail patterns like:

- `GRAND TOTAL`
- `TOTAL RM`
- `TOTAL: RM`
- `TOTAL AMOUNT`
- `TOTAL DUE`
- `NET TOTAL`

and when dates appear as formats like:

- `YYYY-MM-DD`
- `YYYY/MM/DD`
- `DD/MM/YYYY`
- `DD-MM-YYYY`

---

## High-Level Workflow

1. **Inspect the task constraints before coding.**  
   Confirm the required workbook path, exact sheet name, column names, sort order, and how failed extractions must be represented. These details are often more important than OCR accuracy because validators compare exact structure.

2. **Enumerate input images deterministically.**  
   Always sort filenames before processing. The output rows must usually match lexicographic filename order.

3. **Preprocess each image for OCR.**  
   Use grayscale + autocontrast, and correct EXIF orientation. This materially improves OCR quality on receipt scans without overcomplicating the pipeline.

4. **Run OCR with a receipt-friendly page segmentation mode.**  
   `--psm 6` works well for receipts because it assumes a uniform block of text. Use timeouts so one bad image does not stall the entire run.

5. **Try multiple rotations when receipts may be sideways.**  
   OCR the image at 0Â°, 90Â°, and 270Â°. This is a high-value robustness improvement for scanned receipts.

6. **Normalize OCR lines before extraction.**  
   Uppercase text, collapse whitespace, and lightly fix common OCR artifacts. This makes keyword matching more reliable.

7. **Extract the date using explicit regex patterns and validity checks.**  
   Search line by line for known date patterns. Validate month/day ranges before converting to ISO.

8. **Extract the total amount using prioritized keyword groups.**  
   Search for the strongest total indicators first. Skip lines containing exclusion keywords like `SUBTOTAL`, `TAX`, `GST`, or `CHANGE` to avoid false positives.

9. **Support split-line totals.**  
   If a matching keyword line contains no amount, inspect the next line and take the last amount there. This is common in OCRed receipts.

10. **Format values strictly for output.**  
    - `date`: `YYYY-MM-DD` or null  
    - `total_amount`: string with exactly two decimal places or null

11. **Write a workbook with exactly one sheet and exact headers.**  
    Create only the required sheet, remove any default extra sheet if necessary, and avoid formulas or extra formatting.

12. **Verify structure before finalizing.**  
    Re-open the generated workbook and confirm:
    - one sheet only
    - sheet name exactly `results`
    - header row exactly correct
    - rows sorted by filename
    - no extra columns or rows

---

## Domain Conventions and Extraction Rules

### Date conventions

Prefer these parsing rules:

- `YYYY[-/.]MM[-/.]DD`
- `DD[-/.]MM[-/.]YYYY`

Then normalize to ISO:

- `YYYY-MM-DD`

Do not emit ambiguous or partial dates.

### Total amount conventions

The target is the **final payable amount**, not intermediate amounts.

Prioritized keyword groups:

1. `GRAND TOTAL`
2. `TOTAL RM`, `TOTAL: RM`
3. `TOTAL AMOUNT`
4. `TOTAL`, `AMOUNT`, `TOTAL DUE`, `AMOUNT DUE`, `BALANCE DUE`, `NETT TOTAL`, `NET TOTAL`

Exclusion keywords to skip entire lines:

- `SUBTOTAL`
- `SUB TOTAL`
- `TAX`
- `GST`
- `SST`
- `DISCOUNT`
- `CHANGE`
- `CASH TENDERED`

### Numeric format conventions

Amounts may look like:

- `47.70`
- `1,234.56`

Always output as a **string** with exactly two decimal places.

---

## Step 1: Inspect Inputs and Confirm Environment

Before implementing extraction, inspect what files exist and confirm OCR libraries are available.

```bash
python3 - <<'PY'
import os
import importlib

input_dir = "/app/workspace/dataset/img"

if not os.path.isdir(input_dir):
    raise SystemExit(f"Input directory not found: {input_dir}")

files = sorted(
    f for f in os.listdir(input_dir)
    if os.path.isfile(os.path.join(input_dir, f))
)
print("Found files:", len(files))
for f in files[:10]:
    print(" -", f)

for mod in ("pytesseract", "PIL", "openpyxl"):
    try:
        importlib.import_module(mod)
        print(f"OK: {mod}")
    except Exception as e:
        print(f"MISSING: {mod}: {e}")
PY
```

Why this matters:

- Prevents writing code against the wrong input path
- Confirms installed dependencies before spending time debugging OCR logic
- Gives you deterministic file counts for later validation

---

## Step 2: OCR Preprocessing with Rotation Handling

Use EXIF transpose, grayscale, autocontrast, and several orientations.

```python
import os
from PIL import Image, ImageOps
import pytesseract

def load_and_prepare_image(image_path):
    """
    Load image safely and apply lightweight OCR-friendly preprocessing.
    Returns a Pillow image in grayscale.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    try:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        img = ImageOps.grayscale(img)
        img = ImageOps.autocontrast(img)
        return img
    except Exception as e:
        raise RuntimeError(f"Failed to open/preprocess image {image_path}: {e}") from e


def ocr_image_variants(image_path, timeout=20):
    """
    OCR the image at multiple orientations.
    Returns a list of OCR text strings.
    """
    base = load_and_prepare_image(image_path)
    texts = []

    for angle in (0, 90, 270):
        try:
            img = base.rotate(angle, expand=True) if angle else base
            text = pytesseract.image_to_string(
                img,
                config="--oem 3 --psm 6",
                timeout=timeout,
            )
            texts.append(text.replace("\x0c", ""))
        except RuntimeError:
            # pytesseract may throw timeout errors as RuntimeError
            texts.append("")
        except Exception:
            texts.append("")

    return texts


if __name__ == "__main__":
    sample = "/app/workspace/dataset/img/sample.jpg"
    if os.path.exists(sample):
        texts = ocr_image_variants(sample)
        for i, t in enumerate(texts):
            print(f"--- OCR variant {i} ---")
            print(t[:1000])
```

Why this matters:

- Receipts are often photographed or scanned sideways
- `--psm 6` is usually better than sparse-text modes for receipt blocks
- Timeouts keep the batch job moving

---

## Step 3: Normalize OCR Text and Split Into Lines

Normalization should be conservative: improve matching, do not rewrite semantics.

```python
import re

def normalize_line(line):
    """
    Normalize OCR line for keyword matching.
    """
    if line is None:
        return ""
    line = line.upper()
    line = line.replace("|", "I")
    line = " ".join(line.split())
    return line.strip()


def normalized_lines(text):
    """
    Return non-empty normalized lines.
    """
    if not text:
        return []
    return [normalize_line(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    sample_text = " Total:  RM  47.70 \n\nDate| 2023/01/02"
    print(normalized_lines(sample_text))
```

Why this matters:

- OCR often introduces duplicate spaces and case inconsistency
- Simple normalization greatly improves string matching
- Over-aggressive normalization can destroy amounts or dates, so keep it light

---

## Step 4: Extract Dates with Regex and Validation

Use explicit date patterns and reject impossible dates.

```python
import re
from datetime import date as _date

DATE_PATTERNS = [
    re.compile(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})'),
    re.compile(r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})'),
]

def to_iso_date(year, month, day):
    """
    Convert integers to ISO date if valid, else None.
    """
    try:
        d = _date(int(year), int(month), int(day))
        return d.isoformat()
    except Exception:
        return None


def extract_date_from_texts(texts):
    """
    Search OCR variants line by line and return the first valid ISO date.
    """
    for text in texts:
        for raw_line in text.splitlines():
            line = normalize_line(raw_line)
            if not line:
                continue

            for pat in DATE_PATTERNS:
                for match in pat.finditer(line):
                    a, b, c = match.groups()

                    if len(a) == 4:
                        iso = to_iso_date(a, b, c)
                    else:
                        iso = to_iso_date(c, b, a)

                    if iso:
                        return iso

    return None


if __name__ == "__main__":
    texts = [
        "Invoice date: 2024/07/09\nTotal: RM 10.00",
        "Other text",
    ]
    print(extract_date_from_texts(texts))  # 2024-07-09
```

Why this matters:

- Regex alone is not enough; invalid dates like `2024-99-77` must be rejected
- Line-by-line parsing lowers the chance of matching unrelated digit clusters

---

## Step 5: Extract Amounts Reliably

Use a money regex that supports optional comma separators and exactly two decimals.

```python
import re
from decimal import Decimal, InvalidOperation

AMOUNT_RE = re.compile(r'(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?!\d)')

def parse_amount_strings(text):
    """
    Return Decimal amounts found in text, preserving monetary precision.
    """
    if not text:
        return []

    found = []
    for m in AMOUNT_RE.finditer(text):
        token = m.group(0).replace(",", "")
        try:
            value = Decimal(token).quantize(Decimal("0.01"))
            found.append(value)
        except (InvalidOperation, ValueError):
            continue

    return found


if __name__ == "__main__":
    print(parse_amount_strings("TOTAL RM 1,234.56 CASH 100.00"))
```

Why this matters:

- Use `Decimal`, not float, for money
- Receipt OCR commonly yields multiple amounts per line, so keep all and choose intentionally

---

## Step 6: Extract Final Total Using Prioritized Keywords

Search by keyword groups, skip exclusions, and use next-line fallback.

```python
from decimal import Decimal

EXCLUSION_KEYWORDS = [
    "SUBTOTAL",
    "SUB TOTAL",
    "TAX",
    "GST",
    "SST",
    "DISCOUNT",
    "CHANGE",
    "CASH TENDERED",
]

KEYWORD_GROUPS = [
    ["GRAND TOTAL"],
    ["TOTAL RM", "TOTAL: RM"],
    ["TOTAL AMOUNT"],
    ["TOTAL DUE", "AMOUNT DUE", "BALANCE DUE", "NETT TOTAL", "NET TOTAL", "TOTAL", "AMOUNT"],
]

def line_has_exclusion(line):
    return any(word in line for word in EXCLUSION_KEYWORDS)

def line_has_keyword(line, keywords):
    return any(keyword in line for keyword in keywords)

def extract_total_from_texts(texts):
    """
    Search OCR variants for total amount using prioritized keyword groups.
    If the keyword line has no amount, inspect the next line and take its last amount.
    """
    for text in texts:
        lines = normalized_lines(text)

        for group in KEYWORD_GROUPS:
            for i, line in enumerate(lines):
                if line_has_exclusion(line):
                    continue

                if not line_has_keyword(line, group):
                    continue

                amounts_here = parse_amount_strings(line)
                if amounts_here:
                    return f"{amounts_here[-1]:.2f}"

                if i + 1 < len(lines):
                    next_amounts = parse_amount_strings(lines[i + 1])
                    if next_amounts:
                        return f"{next_amounts[-1]:.2f}"

    return None


if __name__ == "__main__":
    texts = [
        "SUBTOTAL 9.00\nTOTAL RM 10.00\n",
        "GRAND TOTAL\n47.70\n",
    ]
    print(extract_total_from_texts(texts))
```

Why this matters:

- Prioritization avoids picking weaker or generic matches too early
- Exclusion filtering is essential to avoid subtotal/tax mistakes
- The ânext lineâ fallback handles common OCR text layout issues

---

## Step 7: Process a Directory into Row Records

Convert all images into strict output rows.

```python
import os

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

def list_image_files(input_dir):
    if not os.path.isdir(input_dir):
        raise NotADirectoryError(f"Not a directory: {input_dir}")

    files = []
    for name in os.listdir(input_dir):
        path = os.path.join(input_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if os.path.isfile(path) and ext in VALID_EXTENSIONS:
            files.append(name)

    return sorted(files)


def extract_receipt_record(input_dir, filename):
    image_path = os.path.join(input_dir, filename)
    texts = ocr_image_variants(image_path, timeout=20)
    return {
        "filename": filename,
        "date": extract_date_from_texts(texts),
        "total_amount": extract_total_from_texts(texts),
    }


def process_receipt_directory(input_dir):
    rows = []
    for filename in list_image_files(input_dir):
        try:
            row = extract_receipt_record(input_dir, filename)
        except Exception:
            row = {
                "filename": filename,
                "date": None,
                "total_amount": None,
            }
        rows.append(row)
    return rows


if __name__ == "__main__":
    rows = process_receipt_directory("/app/workspace/dataset/img")
    for row in rows[:5]:
        print(row)
```

Why this matters:

- Batch processing should not crash on one unreadable image
- Validators usually prefer missing fields as null, not missing rows

---

## Step 8: Write the Excel Workbook Exactly

Create exactly one sheet with exact headers in exact order.

```python
import os
from openpyxl import Workbook

HEADERS = ["filename", "date", "total_amount"]

def write_results_xlsx(rows, output_path):
    """
    Write rows to an xlsx file with exactly one sheet named 'results'.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "results"

    # Ensure exactly the required headers
    ws.append(HEADERS)

    for row in rows:
        ws.append([
            row.get("filename"),
            row.get("date"),
            row.get("total_amount"),
        ])

    # Remove any accidental extra sheets
    for sheet_name in list(wb.sheetnames):
        if sheet_name != "results":
            del wb[sheet_name]

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    wb.save(output_path)


if __name__ == "__main__":
    sample_rows = [
        {"filename": "000.jpg", "date": "2024-01-01", "total_amount": "10.00"},
        {"filename": "001.jpg", "date": None, "total_amount": None},
    ]
    write_results_xlsx(sample_rows, "/app/workspace/stat_ocr.xlsx")
```

Why this matters:

- `openpyxl` writes Python `None` as blank cells, which is typically the correct Excel null representation
- Extra sheets or wrong header order often fail validation even when extraction is correct

---

## Step 9: Validate Workbook Structure Before Finalizing

Always verify output after saving.

```python
from openpyxl import load_workbook

def validate_output_workbook(output_path, expected_filenames=None):
    if not os.path.isfile(output_path):
        raise FileNotFoundError(f"Workbook not found: {output_path}")

    wb = load_workbook(output_path)
    sheetnames = wb.sheetnames
    if sheetnames != ["results"]:
        raise AssertionError(f"Expected only ['results'], got {sheetnames}")

    ws = wb["results"]

    header = [ws.cell(row=1, column=i).value for i in range(1, 4)]
    if header != ["filename", "date", "total_amount"]:
        raise AssertionError(f"Bad header: {header}")

    rows = list(ws.iter_rows(min_row=2, values_only=True))

    if expected_filenames is not None:
        workbook_filenames = [r[0] for r in rows]
        if workbook_filenames != expected_filenames:
            raise AssertionError(
                f"Filenames not sorted/matching.\n"
                f"Expected: {expected_filenames}\n"
                f"Got: {workbook_filenames}"
            )

    for idx, row in enumerate(rows, start=2):
        if len(row) != 3:
            raise AssertionError(f"Row {idx} does not have 3 columns: {row}")

    return True


if __name__ == "__main__":
    validate_output_workbook(
        "/app/workspace/stat_ocr.xlsx",
        expected_filenames=["000.jpg", "001.jpg"],
    )
```

Why this matters:

- Prevents avoidable format failures
- Confirms row ordering and schema before submission

---

## Reference Implementation

This is the full end-to-end script: enumerate images, OCR them, extract date and total, write the workbook, then validate it.

```python
#!/usr/bin/env python3
import os
import re
from decimal import Decimal, InvalidOperation
from datetime import date as _date

from PIL import Image, ImageOps
import pytesseract
from openpyxl import Workbook, load_workbook


# ---------------------------
# Configuration
# ---------------------------

INPUT_DIR = "/app/workspace/dataset/img"
OUTPUT_XLSX = "/app/workspace/stat_ocr.xlsx"

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

HEADERS = ["filename", "date", "total_amount"]

EXCLUSION_KEYWORDS = [
    "SUBTOTAL",
    "SUB TOTAL",
    "TAX",
    "GST",
    "SST",
    "DISCOUNT",
    "CHANGE",
    "CASH TENDERED",
]

KEYWORD_GROUPS = [
    ["GRAND TOTAL"],
    ["TOTAL RM", "TOTAL: RM"],
    ["TOTAL AMOUNT"],
    ["TOTAL DUE", "AMOUNT DUE", "BALANCE DUE", "NETT TOTAL", "NET TOTAL", "TOTAL", "AMOUNT"],
]

DATE_PATTERNS = [
    re.compile(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})'),
    re.compile(r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})'),
]

AMOUNT_RE = re.compile(r'(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?!\d)')


# ---------------------------
# OCR helpers
# ---------------------------

def load_and_prepare_image(image_path):
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    try:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        img = ImageOps.grayscale(img)
        img = ImageOps.autocontrast(img)
        return img
    except Exception as e:
        raise RuntimeError(f"Failed to preprocess image {image_path}: {e}") from e


def ocr_image_variants(image_path, timeout=20):
    """
    OCR 0Â°, 90Â°, and 270Â° variants to handle rotated receipts.
    Returns a list of OCR text strings.
    """
    base = load_and_prepare_image(image_path)
    texts = []

    for angle in (0, 90, 270):
        try:
            img = base.rotate(angle, expand=True) if angle else base
            text = pytesseract.image_to_string(
                img,
                config="--oem 3 --psm 6",
                timeout=timeout,
            )
            texts.append(text.replace("\x0c", ""))
        except RuntimeError:
            texts.append("")
        except Exception:
            texts.append("")

    return texts


# ---------------------------
# Normalization helpers
# ---------------------------

def normalize_line(line):
    if line is None:
        return ""
    line = line.upper()
    line = line.replace("|", "I")
    line = " ".join(line.split())
    return line.strip()


def normalized_lines(text):
    if not text:
        return []
    return [normalize_line(line) for line in text.splitlines() if line.strip()]


# ---------------------------
# Date extraction
# ---------------------------

def to_iso_date(year, month, day):
    try:
        d = _date(int(year), int(month), int(day))
        return d.isoformat()
    except Exception:
        return None


def extract_date_from_texts(texts):
    for text in texts:
        for raw_line in text.splitlines():
            line = normalize_line(raw_line)
            if not line:
                continue

            for pat in DATE_PATTERNS:
                for match in pat.finditer(line):
                    a, b, c = match.groups()

                    if len(a) == 4:
                        iso = to_iso_date(a, b, c)
                    else:
                        iso = to_iso_date(c, b, a)

                    if iso:
                        return iso

    return None


# ---------------------------
# Amount extraction
# ---------------------------

def parse_amount_strings(text):
    if not text:
        return []

    values = []
    for match in AMOUNT_RE.finditer(text):
        token = match.group(0).replace(",", "")
        try:
            values.append(Decimal(token).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            continue
    return values


def line_has_exclusion(line):
    return any(word in line for word in EXCLUSION_KEYWORDS)


def line_has_keyword(line, keywords):
    return any(keyword in line for keyword in keywords)


def extract_total_from_texts(texts):
    """
    Find the most likely final total using prioritized keywords.
    Uses next-line fallback if amount is not on the same line as the keyword.
    """
    for text in texts:
        lines = normalized_lines(text)

        for group in KEYWORD_GROUPS:
            for i, line in enumerate(lines):
                if line_has_exclusion(line):
                    continue

                if not line_has_keyword(line, group):
                    continue

                current_amounts = parse_amount_strings(line)
                if current_amounts:
                    return f"{current_amounts[-1]:.2f}"

                if i + 1 < len(lines):
                    next_amounts = parse_amount_strings(lines[i + 1])
                    if next_amounts:
                        return f"{next_amounts[-1]:.2f}"

    return None


# ---------------------------
# File enumeration and row extraction
# ---------------------------

def list_image_files(input_dir):
    if not os.path.isdir(input_dir):
        raise NotADirectoryError(f"Input directory not found: {input_dir}")

    files = []
    for name in os.listdir(input_dir):
        path = os.path.join(input_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if os.path.isfile(path) and ext in VALID_EXTENSIONS:
            files.append(name)

    return sorted(files)


def extract_receipt_record(input_dir, filename):
    image_path = os.path.join(input_dir, filename)
    texts = ocr_image_variants(image_path, timeout=20)

    return {
        "filename": filename,
        "date": extract_date_from_texts(texts),
        "total_amount": extract_total_from_texts(texts),
    }


def process_receipt_directory(input_dir):
    rows = []
    filenames = list_image_files(input_dir)

    for filename in filenames:
        try:
            row = extract_receipt_record(input_dir, filename)
        except Exception as e:
            # For strict batch completion, degrade only this file to nulls
            print(f"[WARN] Failed processing {filename}: {e}")
            row = {
                "filename": filename,
                "date": None,
                "total_amount": None,
            }
        rows.append(row)

    return rows


# ---------------------------
# Excel writing
# ---------------------------

def write_results_xlsx(rows, output_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "results"

    ws.append(HEADERS)
    for row in rows:
        ws.append([
            row.get("filename"),
            row.get("date"),
            row.get("total_amount"),
        ])

    # Remove any accidental extra sheets
    for sheet_name in list(wb.sheetnames):
        if sheet_name != "results":
            del wb[sheet_name]

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    wb.save(output_path)


# ---------------------------
# Validation
# ---------------------------

def validate_output_workbook(output_path, expected_filenames):
    if not os.path.isfile(output_path):
        raise FileNotFoundError(f"Workbook not found: {output_path}")

    wb = load_workbook(output_path)
    if wb.sheetnames != ["results"]:
        raise AssertionError(f"Expected only ['results'], got {wb.sheetnames}")

    ws = wb["results"]

    header = [ws.cell(row=1, column=i).value for i in range(1, 4)]
    if header != HEADERS:
        raise AssertionError(f"Expected header {HEADERS}, got {header}")

    rows = list(ws.iter_rows(min_row=2, values_only=True))
    workbook_filenames = [r[0] for r in rows]

    if workbook_filenames != expected_filenames:
        raise AssertionError(
            "Filenames in workbook do not match sorted input files.\n"
            f"Expected: {expected_filenames}\n"
            f"Got: {workbook_filenames}"
        )

    for idx, row in enumerate(rows, start=2):
        if len(row) != 3:
            raise AssertionError(f"Row {idx} has wrong width: {row}")

        filename, date_value, total_value = row

        if filename is None:
            raise AssertionError(f"Row {idx} missing filename")

        if date_value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_value)):
                raise AssertionError(f"Row {idx} bad date format: {date_value}")

        if total_value is not None:
            if not re.fullmatch(r"\d+\.\d{2}", str(total_value)):
                raise AssertionError(f"Row {idx} bad total format: {total_value}")

    return True


# ---------------------------
# Main
# ---------------------------

def main():
    filenames = list_image_files(INPUT_DIR)
    print(f"[INFO] Found {len(filenames)} image files")

    rows = process_receipt_directory(INPUT_DIR)

    write_results_xlsx(rows, OUTPUT_XLSX)
    print(f"[INFO] Wrote workbook: {OUTPUT_XLSX}")

    validate_output_workbook(OUTPUT_XLSX, filenames)
    print("[INFO] Workbook validation passed")

    # Optional console preview
    for row in rows[:10]:
        print(row)


if __name__ == "__main__":
    main()
```

---

## Execution Checklist

Before running:

- [ ] Input directory exists
- [ ] Output path is correct
- [ ] Filenames are sorted lexicographically
- [ ] OCR dependencies import successfully

After running:

- [ ] Workbook exists
- [ ] Sheet list is exactly `["results"]`
- [ ] Header row is exactly `filename`, `date`, `total_amount`
- [ ] Number of data rows equals number of input image files
- [ ] Rows are in filename order
- [ ] `date` values are ISO strings or blank
- [ ] `total_amount` values are strings with two decimals or blank

---

## Debugging Strategy

If extraction quality is poor, debug in this order:

1. **Print OCR text for a few sample receipts.**  
   Do not guess extraction rules blindly.

2. **Check whether receipts are rotated.**  
   If a rotated OCR variant clearly reads better, multi-angle OCR is justified.

3. **Inspect false total matches.**  
   Most mistakes come from selecting:
   - subtotal
   - tax
   - cash tendered
   - change

4. **Check whether amount is on the next line.**  
   Many receipts show:
   - `GRAND TOTAL`
   - `47.70`

5. **Confirm workbook structure separately from extraction content.**  
   A perfect extractor can still fail if the workbook has:
   - wrong sheet name
   - extra sheet
   - wrong header order

Example OCR inspection helper:

```bash
python3 - <<'PY'
import os
from PIL import Image, ImageOps
import pytesseract

input_dir = "/app/workspace/dataset/img"
for name in sorted(os.listdir(input_dir))[:5]:
    path = os.path.join(input_dir, name)
    if not os.path.isfile(path):
        continue
    img = ImageOps.autocontrast(ImageOps.grayscale(ImageOps.exif_transpose(Image.open(path))))
    txt = pytesseract.image_to_string(img, config="--oem 3 --psm 6", timeout=20)
    print("=" * 80)
    print(name)
    print(txt[:2000])
PY
```

---

## Common Pitfalls

### 1. Picking subtotal/tax instead of final total
**Symptom:** extracted amount is too small or wrong.  
**Cause:** matching generic `TOTAL` without exclusions.  
**Fix:** always skip lines containing `SUBTOTAL`, `TAX`, `GST`, `SST`, `DISCOUNT`, `CHANGE`, `CASH TENDERED`.

### 2. Missing totals because keyword and number are split across lines
**Symptom:** keyword is found but no amount extracted.  
**Cause:** OCR preserved a line break between label and value.  
**Fix:** if keyword line has no amount, inspect the next line and take the last amount there.

### 3. Failing on rotated receipts
**Symptom:** OCR text is gibberish or nearly empty on some files.  
**Cause:** image orientation differs across scans.  
**Fix:** OCR multiple rotations, especially 0Â°, 90Â°, and 270Â°.

### 4. Using floats for money
**Symptom:** formatting inconsistencies like `47.7` or precision noise.  
**Cause:** float arithmetic/formatting.  
**Fix:** parse with `Decimal` and output with `"{value:.2f}"`.

### 5. Emitting non-ISO dates
**Symptom:** output contains `01/02/2024` or ambiguous date strings.  
**Cause:** passing raw OCR date text through unchanged.  
**Fix:** normalize to `YYYY-MM-DD` only after validating day/month/year.

### 6. Writing the wrong Excel structure
**Symptom:** validator fails despite seemingly correct values.  
**Cause:** extra sheet, wrong sheet name, wrong headers, or extra columns.  
**Fix:** explicitly enforce one sheet named `results` and exact header order.

### 7. Crashing the whole batch on one bad image
**Symptom:** no output workbook produced because one image fails OCR.  
**Cause:** unhandled exception during image open or OCR.  
**Fix:** catch per-file exceptions and emit null fields for that file.

---

## Adaptation Notes

You can extend this skill for similar tasks by:

- Adding more date patterns if the receipts use month names
- Adding store-specific total keywords if a dataset is homogeneous
- Using `pytesseract.image_to_data` if line segmentation becomes critical
- Applying thresholding or resizing if scans are consistently faint

But for general receipt OCR tasks, start with the simpler workflow above first. It is robust, fast to implement, and aligned with strict spreadsheet validators.

---

## Minimal Command to Run the Reference Script

If you save the reference implementation as `/app/workspace/create_stat_ocr.py`:

```bash
python3 /app/workspace/create_stat_ocr.py
```

This should produce:

- `/app/workspace/stat_ocr.xlsx`

with:

- exactly one sheet named `results`
- columns `filename`, `date`, `total_amount`
- rows sorted by filename
- blank cells where extraction fails

---

## Final Decision Rule

If time is limited, prioritize in this order:

1. **Exact workbook structure**
2. **Deterministic filename ordering**
3. **Robust total extraction with exclusions and keyword priority**
4. **Date normalization**
5. **OCR quality improvements like multi-rotation**

In receipt-to-Excel tasks, a structurally perfect workbook with a conservative extractor usually beats a sophisticated extractor that violates schema requirements.