---
title: "California Small Claims Court SC-100 XFA PDF Form Filling"
category: court-form-filling
domain: legal-forms
tags:
  - pdf-manipulation
  - xfa-forms
  - pypdf
  - california-courts
  - small-claims
dependencies:
  - pypdf>=5.1.0
  - poppler-utils
environment: ubuntu
---

# Filling California SC-100 Small Claims Court PDF (XFA-based)

## Overview

The California SC-100 (Plaintiff's Claim and ORDER to Go to Small Claims Court) is an **XFA-based PDF form**, not a standard AcroForm. This distinction is critical — most PDF form-filling libraries (fillpdf, pdfrw, PyPDF2's simple field writer) silently fail or corrupt XFA forms. The correct approach is to **extract the XFA datasets XML, modify it directly, then write it back** using `pypdf`.

---

## High-Level Workflow

1. **Inspect the PDF structure** — Confirm it's XFA, enumerate field names from both the XFA template and datasets XML.
2. **Extract the XFA datasets XML** — Pull the `datasets` stream from the `/AcroForm/XFA` array.
3. **Parse the XML and identify field nodes** — Map case facts to XFA field names using the template XML as a guide.
4. **Modify field values in the datasets XML** — Set text fields and checkbox values.
5. **Write the modified XML back into the PDF** — Replace the datasets stream object and save.
6. **Verify the output** — Use `pdftotext` (poppler-utils) to confirm text content appears correctly.

---

## Step 1: Inspect the PDF — Confirm XFA and Discover Fields

XFA forms store their field definitions in two XML streams inside `/AcroForm/XFA`: a `template` (field definitions, types, options) and `datasets` (current values). Always inspect both.

```python
from pypdf import PdfReader
import xml.etree.ElementTree as ET

reader = PdfReader("sc100-blank.pdf")

# Confirm AcroForm and XFA exist
acroform = reader.trailer["/Root"]["/AcroForm"]
xfa = acroform["/XFA"]

# XFA is an array of [name, stream_ref, name, stream_ref, ...]
# Common names: "preamble", "template", "datasets", "postamble", "config", "localeSet"
for i in range(0, len(xfa), 2):
    key = str(xfa[i])
    stream_obj = xfa[i + 1].get_object()
    raw = stream_obj.get_data().decode("utf-8", errors="ignore")
    print(f"--- {key} ({len(raw)} bytes) ---")
    # Save to disk for inspection
    with open(f"/tmp/xfa_{key}.xml", "w") as f:
        f.write(raw)
```

### What to look for in the template XML

The template defines every field's name, type, and allowed values. Key patterns:

```xml
<!-- Text field -->
<field name="TextField1" ...>
  <ui><textEdit /></ui>
</field>

<!-- Checkbox with two items (1=checked, 2=unchecked or vice versa) -->
<field name="Checkbox50" ...>
  <ui><checkButton /></ui>
  <items>
    <integer>1</integer>  <!-- value when checked -->
  </items>
  <items>
    <integer>2</integer>  <!-- value when unchecked -->
  </items>
</field>
```

Use `grep` to quickly find checkbox definitions:

```bash
grep -B 5 -A 30 'name="Checkbox' /tmp/xfa_template.xml
```

### SC-100 Field Name Map (General Pattern)

The SC-100 form organizes fields by page and section. Field names follow this hierarchy in the datasets XML:

```
Page1/
  List1/          — Plaintiff info
    TextField1    — Plaintiff name
    TextField2    — Plaintiff street address
    TextField3    — Plaintiff city
    TextField4    — Plaintiff state
    TextField5    — Plaintiff zip code
    TextField7    — Plaintiff phone (no area code field — full number)
    TextField9    — Plaintiff email
    Checkbox1     — "I am suing on my own behalf" (value "1")
    Checkbox2     — "I am suing as a business" etc.
  List2/          — Defendant info (mirrors List1 structure)
    TextField1    — Defendant name
    TextField2    — Defendant street address
    TextField3    — Defendant city
    TextField4    — Defendant state
    TextField5    — Defendant zip code
    TextField7    — Defendant phone
  List3/          — Claim amount
    TextField1    — Dollar amount (e.g. "1,500.00")
    Checkbox1     — "Not more than $10,000" (value "1")
Page2/
  List3/          — Claim details
    TextField1    — What happened (narrative)
    TextField2    — Date started (xxxx-xx-xx)
    TextField3    — Date through (xxxx-xx-xx)
    Checkbox1/    — How amount was calculated
      TextField1  — Explanation of calculation
Page3/
  List5/          — Filing court reason
    Checkbox5cb   — "Where defendant lives" (value "1")
    TextField1    — Zip code of filing location
  List7/          — "Have you asked defendant to pay?"
    Checkbox50    — Yes (value "1"), No (value "2")
  List8/          — Additional questions
    Checkbox60    — Attorney-client fee dispute? (1=Yes, 2=No)
    Checkbox61    — Suing a public entity? (1=Yes, 2=No)
    Checkbox62    — Filed 12+ claims in last 12 months? (1=Yes, 2=No)
    Checkbox63    — Claim amount > $2,500? (1=Yes, 2=No)
Page4/
  List9/          — Signature section
    TextField1    — Date of filing
    TextField2    — Printed name of plaintiff
```

> **Important**: These names are stable across blank SC-100 versions but always verify by inspecting the template XML for your specific PDF. Field names can shift between form revisions.

---

## Step 2: Extract and Parse the Datasets XML

```python
def extract_xfa_datasets(reader):
    """Extract the datasets XML string and its stream object reference."""
    acroform = reader.trailer["/Root"]["/AcroForm"]
    xfa = acroform["/XFA"]
    for i in range(0, len(xfa), 2):
        if str(xfa[i]) == "datasets":
            stream_obj = xfa[i + 1].get_object()
            xml_data = stream_obj.get_data().decode("utf-8", errors="ignore")
            return xml_data, stream_obj
    raise ValueError("No 'datasets' stream found in XFA array")
```

---

## Step 3: Modify Field Values via String Replacement

While you *can* use an XML parser, the XFA datasets XML often has namespace complexities that make `ElementTree` cumbersome. **Direct string replacement on the XML is more reliable** for this form, as long as you're careful with the patterns.

### Strategy: Build a helper that sets field values by tag name

```python
import re

def set_field(xml: str, field_name: str, value: str) -> str:
    """
    Set a field value in XFA datasets XML.
    Handles both empty tags (<FieldName/>) and tags with existing content.
    """
    # Pattern 1: self-closing tag  <FieldName/>
    pattern_empty = f"<{field_name}/>"
    replacement = f"<{field_name}>{value}</{field_name}>"
    if pattern_empty in xml:
        xml = xml.replace(pattern_empty, replacement, 1)
        return xml

    # Pattern 2: tag with existing content  <FieldName>old</FieldName>
    pattern_filled = re.compile(f"<{field_name}>(.*?)</{field_name}>", re.DOTALL)
    if pattern_filled.search(xml):
        xml = pattern_filled.sub(f"<{field_name}>{value}</{field_name}>", xml, count=1)
        return xml

    # If field not found, warn (don't silently fail)
    print(f"WARNING: Field '{field_name}' not found in datasets XML")
    return xml
```

### Handling nested fields (same name at different paths)

The SC-100 has multiple `TextField1`, `TextField2`, etc. under different parent nodes. You need **context-aware replacement** — find the parent node first, then replace within that scope.

```python
def set_field_in_parent(xml: str, parent_name: str, field_name: str, value: str) -> str:
    """
    Set a field value within a specific parent element.
    E.g., set TextField1 inside <List1>...</List1>
    """
    parent_pattern = re.compile(
        f"(<{parent_name}[^>]*>)(.*?)(</{parent_name}>)",
        re.DOTALL
    )
    match = parent_pattern.search(xml)
    if not match:
        print(f"WARNING: Parent '{parent_name}' not found")
        return xml

    parent_open = match.group(1)
    parent_content = match.group(2)
    parent_close = match.group(3)

    # Replace field within parent content
    modified_content = set_field(parent_content, field_name, value)

    xml = xml[:match.start()] + parent_open + modified_content + parent_close + xml[match.end():]
    return xml
```

### Handling deeply nested parents (Page > List > Checkbox > TextField)

For fields nested multiple levels deep, chain the parent lookups or use a more targeted regex:

```python
def set_nested_field(xml: str, page: str, list_name: str, field_name: str, value: str) -> str:
    """Set a field nested under Page/List/Field hierarchy."""
    # Find the page block
    page_pattern = re.compile(f"(<{page}[^>]*>)(.*?)(</{page}>)", re.DOTALL)
    page_match = page_pattern.search(xml)
    if not page_match:
        print(f"WARNING: Page '{page}' not found")
        return xml

    page_content = page_match.group(2)

    # Find the list block within the page
    list_pattern = re.compile(f"(<{list_name}[^>]*>)(.*?)(</{list_name}>)", re.DOTALL)
    list_match = list_pattern.search(page_content)
    if not list_match:
        print(f"WARNING: List '{list_name}' not found in {page}")
        return xml

    list_content = list_match.group(2)

    # Replace the field within the list
    modified_list = set_field(list_content, field_name, value)

    # Reconstruct
    new_page_content = (
        page_content[:list_match.start()] +
        list_match.group(1) + modified_list + list_match.group(3) +
        page_content[list_match.end():]
    )

    xml = (
        xml[:page_match.start()] +
        page_match.group(1) + new_page_content + page_match.group(3) +
        xml[page_match.end():]
    )
    return xml
```

---

## Step 4: Write Modified XML Back to PDF

```python
from pypdf import PdfReader, PdfWriter

def write_xfa_pdf(input_path: str, output_path: str, modified_xml: str):
    """Write modified XFA datasets XML back into the PDF."""
    reader = PdfReader(input_path)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    # Copy the AcroForm to the writer
    writer._root_object["/AcroForm"] = reader.trailer["/Root"]["/AcroForm"]

    # Find and replace the datasets stream
    acroform = writer._root_object["/AcroForm"]
    xfa = acroform["/XFA"]
    for i in range(0, len(xfa), 2):
        if str(xfa[i]) == "datasets":
            stream_obj = xfa[i + 1].get_object()
            # Encode and set the new data
            stream_obj._data = modified_xml.encode("utf-8")
            # Remove any existing filter (compression) so raw XML is stored
            if "/Filter" in stream_obj:
                del stream_obj["/Filter"]
            break

    with open(output_path, "wb") as f:
        writer.write(f)
```

---

## Step 5: Verify the Output

Always verify with `pdftotext` from poppler-utils. This renders the XFA form and extracts visible text.

```bash
pdftotext -layout sc100-filled.pdf - | head -200
```

Check for:
- All plaintiff/defendant info appears
- Claim amount is present
- Dates are formatted correctly
- Checkbox selections are reflected (checked boxes often render as a character or the text of the selected option)

```python
import subprocess

def verify_pdf(path: str, expected_strings: list[str]) -> bool:
    """Verify that expected content appears in the rendered PDF text."""
    result = subprocess.run(
        ["pdftotext", "-layout", path, "-"],
        capture_output=True, text=True
    )
    text = result.stdout
    all_ok = True
    for s in expected_strings:
        if s.lower() in text.lower():
            print(f"✓ Found: {s}")
        else:
            print(f"✗ MISSING: {s}")
            all_ok = False
    return all_ok
```

---

## Common Pitfalls

### 1. Using AcroForm-only libraries on XFA forms
Libraries like `fillpdf`, `pdfrw`, and PyPDF2's `update_page_form_field_values()` target AcroForm fields. The SC-100 is XFA. These tools will either silently produce an unchanged PDF or corrupt it. **Always check for XFA first** and use the XML manipulation approach.

### 2. Overwriting the wrong TextField1
The form reuses generic names (`TextField1`, `TextField2`, etc.) across different sections. A naive global replace of `<TextField1/>` will fill the plaintiff name into every section. **Always scope replacements to the correct parent element** (Page → List → Field).

### 3. Checkbox values: "1" vs "2" vs "on"
SC-100 checkboxes use integer values, not "on"/"off" or "Yes"/"No":
- `1` = first option (typically "Yes" or the checked state)
- `2` = second option (typically "No" or unchecked)

Check the template XML `<items>` to confirm which value maps to which state. Setting the wrong value silently selects the wrong option.

### 4. Forgetting to remove the `/Filter` on the stream
If the original datasets stream was compressed (e.g., FlateDecode), you must remove the `/Filter` key after replacing `_data` with raw XML bytes. Otherwise the PDF reader will try to decompress your raw XML and fail.

### 5. Not copying AcroForm to the writer
`PdfWriter` doesn't automatically carry over the AcroForm/XFA structure. You must explicitly assign `writer._root_object["/AcroForm"] = reader.trailer["/Root"]["/AcroForm"]` before modifying the XFA streams.

### 6. Date format
The task specifies `xxxx-xx-xx` format (e.g., `2026-01-19`). Do not use `MM/DD/YYYY` or other formats. Always follow the format specified in the task instructions.

### 7. Dollar amounts
Format claim amounts with commas and two decimal places: `1,500.00`, not `1500` or `$1500`. The dollar sign is typically pre-printed on the form.

### 8. Leaving court-filled fields empty
Fields like case number, court name/address, hearing date, and judge name are filled by the court clerk. Do not populate these even if they appear in the XML.

---

## Reference Implementation

This is a complete, end-to-end script for filling the SC-100 form. Adapt the case-specific values in the `CASE_DATA` dictionary for each new case.

```python
#!/usr/bin/env python3
"""
Fill California SC-100 Small Claims Court XFA PDF form.

Usage:
    python3 fill_sc100.py

Requires: pypdf>=5.1.0, poppler-utils (for verification)
"""

import re
import subprocess
from pypdf import PdfReader, PdfWriter

# ============================================================
# CASE DATA — Adapt these values for each case
# ============================================================
CASE_DATA = {
    # Plaintiff
    "plaintiff_name": "Joyce He",
    "plaintiff_street": "655 S Fair Oaks Ave",
    "plaintiff_city": "Sunnyvale",
    "plaintiff_state": "CA",
    "plaintiff_zip": "94086",
    "plaintiff_phone": "4125886066",
    "plaintiff_email": "he1998@gmail.com",

    # Defendant
    "defendant_name": "Zhi Chen",
    "defendant_street": "299 W Washington Ave",
    "defendant_city": "Sunnyvale",
    "defendant_state": "CA",
    "defendant_zip": "94086",
    "defendant_phone": "5125658878",

    # Claim
    "claim_amount": "1,500.00",
    "claim_narrative": (
        "Defendant failed to return my security deposit of $1,500 "
        "based on the signed roommate sublease contract after moving out. "
        "I have asked him to return the money multiple times via text "
        "but he is not responding."
    ),
    "claim_date_start": "2025-09-30",
    "claim_date_end": "2026-01-19",
    "claim_calculation": (
        "The amount of $1,500 is listed on the signed roommate sublease contract "
        "as the security deposit."
    ),

    # Filing details
    "filing_zip": "94086",
    "filing_date": "2026-01-19",

    # Checkboxes (see field map above for meanings)
    # Plaintiff type: 1 = individual
    "plaintiff_type_checkbox": ("Checkbox1", "1"),
    # Claim amount bracket: 1 = not more than $10,000
    "claim_amount_checkbox_value": "1",
    # Asked defendant to pay: 1 = Yes
    "asked_to_pay": "1",
    # Filing reason: defendant lives/does business in this district
    "filing_reason_checkbox": "Checkbox5cb",
    # Attorney-client dispute: 2 = No
    "attorney_dispute": "2",
    # Suing public entity: 2 = No
    "public_entity": "2",
    # Filed 12+ claims: 2 = No
    "frequent_filer": "2",
    # Claim > $2,500: 2 = No
    "over_2500": "2",
}

INPUT_PDF = "/root/sc100-blank.pdf"
OUTPUT_PDF = "/root/sc100-filled.pdf"


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def set_field(xml: str, field_name: str, value: str) -> str:
    """Replace a field's value in XFA datasets XML."""
    # Self-closing tag
    pattern_empty = f"<{field_name}/>"
    replacement = f"<{field_name}>{value}</{field_name}>"
    if pattern_empty in xml:
        return xml.replace(pattern_empty, replacement, 1)

    # Tag with existing content
    pattern_filled = re.compile(f"<{field_name}>(.*?)</{field_name}>", re.DOTALL)
    if pattern_filled.search(xml):
        return pattern_filled.sub(f"<{field_name}>{value}</{field_name}>", xml, count=1)

    print(f"WARNING: Field '{field_name}' not found in XML")
    return xml


def set_nested_field(xml: str, parents: list, field_name: str, value: str) -> str:
    """
    Set a field nested under a chain of parent elements.
    parents: list of parent tag names from outermost to innermost
             e.g. ["Page1", "List1"] to target Page1/List1/FieldName
    """
    # Build a stack of (start, end, content) for each nesting level
    regions = []
    search_text = xml
    offset = 0

    for parent in parents:
        pattern = re.compile(f"(<{parent}[^>]*>)(.*?)(</{parent}>)", re.DOTALL)
        match = pattern.search(search_text)
        if not match:
            print(f"WARNING: Parent '{parent}' not found")
            return xml
        regions.append((offset + match.start(), offset + match.end(), match))
        search_text = match.group(2)
        offset += match.start() + len(match.group(1))

    # Replace the field in the innermost region
    innermost = regions[-1][2]
    inner_content = innermost.group(2)
    modified_inner = set_field(inner_content, field_name, value)

    if modified_inner == inner_content:
        # Field not found at this level — it might be the field itself
        return xml

    # Reconstruct from inside out
    new_xml = (
        xml[:regions[-1][0]] +
        innermost.group(1) + modified_inner + innermost.group(3) +
        xml[regions[-1][1]:]
    )
    return new_xml


def extract_xfa_datasets(reader: PdfReader) -> tuple:
    """Return (xml_string, stream_object) for the XFA datasets."""
    acroform = reader.trailer["/Root"]["/AcroForm"]
    xfa = acroform["/XFA"]
    for i in range(0, len(xfa), 2):
        if str(xfa[i]) == "datasets":
            stream_obj = xfa[i + 1].get_object()
            xml_data = stream_obj.get_data().decode("utf-8", errors="ignore")
            return xml_data, stream_obj
    raise ValueError("No 'datasets' stream found in XFA")


# ============================================================
# MAIN FORM-FILLING LOGIC
# ============================================================

def fill_form():
    d = CASE_DATA
    reader = PdfReader(INPUT_PDF)
    xml, stream_obj = extract_xfa_datasets(reader)

    # --- Page 1: Plaintiff (List1) ---
    xml = set_nested_field(xml, ["Page1", "List1"], "TextField1", d["plaintiff_name"])
    xml = set_nested_field(xml, ["Page1", "List1"], "TextField2", d["plaintiff_street"])
    xml = set_nested_field(xml, ["Page1", "List1"], "TextField3", d["plaintiff_city"])
    xml = set_nested_field(xml, ["Page1", "List1"], "TextField4", d["plaintiff_state"])
    xml = set_nested_field(xml, ["Page1", "List1"], "TextField5", d["plaintiff_zip"])
    xml = set_nested_field(xml, ["Page1", "List1"], "TextField7", d["plaintiff_phone"])
    xml = set_nested_field(xml, ["Page1", "List1"], "TextField9", d["plaintiff_email"])
    # "Suing on my own behalf" checkbox
    cb_name, cb_val = d["plaintiff_type_checkbox"]
    xml = set_nested_field(xml, ["Page1", "List1"], cb_name, cb_val)

    # --- Page 1: Defendant (List2) ---
    xml = set_nested_field(xml, ["Page1", "List2"], "TextField1", d["defendant_name"])
    xml = set_nested_field(xml, ["Page1", "List2"], "TextField2", d["defendant_street"])
    xml = set_nested_field(xml, ["Page1", "List2"], "TextField3", d["defendant_city"])
    xml = set_nested_field(xml, ["Page1", "List2"], "TextField4", d["defendant_state"])
    xml = set_nested_field(xml, ["Page1", "List2"], "TextField5", d["defendant_zip"])
    xml = set_nested_field(xml, ["Page1", "List2"], "TextField7", d["defendant_phone"])

    # --- Page 1: Claim amount (List3) ---
    xml = set_nested_field(xml, ["Page1", "List3"], "TextField1", d["claim_amount"])
    xml = set_nested_field(xml, ["Page1", "List3"], "Checkbox1", d["claim_amount_checkbox_value"])

    # --- Page 2: Claim details (List3) ---
    xml = set_nested_field(xml, ["Page2", "List3"], "TextField1", d["claim_narrative"])
    xml = set_nested_field(xml, ["Page2", "List3"], "TextField2", d["claim_date_start"])
    xml = set_nested_field(xml, ["Page2", "List3"], "TextField3", d["claim_date_end"])

    # How claim amount was calculated (nested under Checkbox1 in Page2/List3)
    xml = set_nested_field(xml, ["Page2", "List3", "Checkbox1"], "TextField1", d["claim_calculation"])

    # --- Page 3: Filing location (List5) ---
    xml = set_nested_field(xml, ["Page3", "List5"], d["filing_reason_checkbox"], "1")
    xml = set_nested_field(xml, ["Page3", "List5"], "TextField1", d["filing_zip"])

    # --- Page 3: Asked to pay (List7) ---
    xml = set_nested_field(xml, ["Page3", "List7"], "Checkbox50", d["asked_to_pay"])

    # --- Page 3: Additional questions (List8) ---
    xml = set_nested_field(xml, ["Page3", "List8"], "Checkbox60", d["attorney_dispute"])
    xml = set_nested_field(xml, ["Page3", "List8"], "Checkbox61", d["public_entity"])
    xml = set_nested_field(xml, ["Page3", "List8"], "Checkbox62", d["frequent_filer"])
    xml = set_nested_field(xml, ["Page3", "List8"], "Checkbox63", d["over_2500"])

    # --- Page 4: Signature (List9) ---
    xml = set_nested_field(xml, ["Page4", "List9"], "TextField1", d["filing_date"])
    xml = set_nested_field(xml, ["Page4", "List9"], "TextField2", d["plaintiff_name"])

    # --- Write the modified PDF ---
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer._root_object["/AcroForm"] = reader.trailer["/Root"]["/AcroForm"]

    # Find and update the datasets stream in the writer's copy
    acroform = writer._root_object["/AcroForm"]
    xfa = acroform["/XFA"]
    for i in range(0, len(xfa), 2):
        if str(xfa[i]) == "datasets":
            obj = xfa[i + 1].get_object()
            obj._data = xml.encode("utf-8")
            if "/Filter" in obj:
                del obj["/Filter"]
            break

    with open(OUTPUT_PDF, "wb") as f:
        writer.write(f)

    print(f"✓ Filled form saved to {OUTPUT_PDF}")


# ============================================================
# VERIFICATION
# ============================================================

def verify():
    """Verify the filled PDF contains expected content."""
    result = subprocess.run(
        ["pdftotext", "-layout", OUTPUT_PDF, "-"],
        capture_output=True, text=True
    )
    text = result.stdout
    print(f"Extracted {len(text)} characters from PDF\n")

    checks = [
        ("Plaintiff name", CASE_DATA["plaintiff_name"]),
        ("Plaintiff phone", CASE_DATA["plaintiff_phone"]),
        ("Plaintiff address", CASE_DATA["plaintiff_street"]),
        ("Plaintiff city", CASE_DATA["plaintiff_city"]),
        ("Plaintiff email", CASE_DATA["plaintiff_email"]),
        ("Defendant name", CASE_DATA["defendant_name"]),
        ("Defendant phone", CASE_DATA["defendant_phone"]),
        ("Defendant address", CASE_DATA["defendant_street"]),
        ("Claim amount", CASE_DATA["claim_amount"]),
        ("Date started", CASE_DATA["claim_date_start"]),
        ("Date through", CASE_DATA["claim_date_end"]),
        ("Zip code", CASE_DATA["filing_zip"]),
    ]

    all_ok = True
    for label, expected in checks:
        if expected.lower() in text.lower():
            print(f"✓ {label}: {expected}")
        else:
            print(f"✗ MISSING {label}: {expected}")
            all_ok = False

    if all_ok:
        print("\n✓ All verifications passed!")
    else:
        print("\n✗ Some checks failed — review the output PDF")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    fill_form()
    verify()
```

---

## Adapting for New Cases

To fill the form for a different case:

1. **Update `CASE_DATA`** with the new plaintiff, defendant, claim details, and checkbox selections.
2. **Inspect the blank PDF** if it's a different version of SC-100 — field names may have shifted. Run the Step 1 inspection code to dump the template and datasets XML.
3. **Add or remove sections** as needed. For example, if there are multiple defendants, you may need to fill `List2` (first defendant) and additional defendant sections.
4. **Adjust checkboxes** based on the case:
   - If the plaintiff is a business, change `plaintiff_type_checkbox` to `("Checkbox2", "1")`.
   - If filing where the incident occurred (not where defendant lives), use `Checkbox5ca` instead of `Checkbox5cb`.
   - If the claim is over $2,500, set `over_2500` to `"1"`.

## Environment Notes

- **pypdf** (v5.1.0+) is the primary library. It handles XFA stream access and PDF writing.
- **poppler-utils** provides `pdftotext` for verification. Install with `apt-get install poppler-utils`.
- **fillpdf**, **pdfrw**, and **PyPDF2** are available but should NOT be used for XFA forms — they only handle AcroForm fields.
- The `reportlab` package is available if you need to generate overlay PDFs, but direct XFA manipulation is the correct approach for this form.