---
title: "Offer Letter Generator — Filling DOCX Templates with JSON Data"
category: offer-letter-generator
tags:
  - python-docx
  - docx-template-filling
  - placeholder-replacement
  - conditional-sections
  - document-automation
dependencies:
  - python-docx>=1.1.0
environment:
  runtime: python3
  os: ubuntu
---

# Offer Letter Generator: DOCX Template Filling from JSON Data

## Overview

This skill covers the end-to-end process of filling a `.docx` Word template that contains `{{PLACEHOLDER}}` markers with values from a JSON data file. The core challenges are:

1. **Split-run placeholders** — Word's internal XML often splits a single `{{PLACEHOLDER}}` across multiple "runs" (e.g., `{{CAND`, `IDATE_FULL`, `_NAME}}`), so naive find-and-replace on individual run texts fails silently.
2. **Conditional sections** — Blocks like `{{IF_RELOCATION}}...{{END_IF_RELOCATION}}` must be kept or removed based on a data flag, and the markers themselves must always be stripped.
3. **Multiple document locations** — Placeholders can appear in body paragraphs, nested tables (including tables inside tables), headers, and footers.

---

## High-Level Workflow

1. **Load the data** — Read `employee_data.json` into a Python dict. Every key becomes a placeholder name; every value is the replacement string.

2. **Load the template** — Open `offer_letter_template.docx` with `python-docx`.

3. **Collect all paragraphs** — Gather paragraphs from the document body, every table cell (recursively, for nested tables), and every header/footer section.

4. **Handle conditional sections first** — Scan for `{{IF_*}}` / `{{END_IF_*}}` markers. Decide whether to keep or remove the enclosed content based on the corresponding data value. Always strip the marker tags themselves.

5. **Replace split-run placeholders** — For each paragraph, concatenate all run texts, perform replacements on the combined string, then redistribute the result back into the runs.

6. **Save the filled document** — Write to the output path.

7. **Verify** — Confirm no `{{...}}` markers remain anywhere in the document (body, tables, headers, footers).

---

## Step 1: Load Employee Data

```python
import json
from pathlib import Path

def load_employee_data(json_path: str) -> dict:
    """Load employee data from JSON. All values are converted to strings."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Ensure every value is a string for replacement
    return {k: str(v) for k, v in data.items()}
```

---

## Step 2: Load the DOCX Template

```python
from docx import Document

def load_template(docx_path: str) -> Document:
    return Document(docx_path)
```

---

## Step 3: Collect All Paragraphs (Including Nested Tables, Headers, Footers)

This is the most commonly missed step. Placeholders can hide inside:
- Body paragraphs
- Table cells (and tables nested inside table cells)
- Header and footer paragraphs

```python
from docx.table import Table
from docx.document import Document as DocType

def iter_all_paragraphs(doc: DocType):
    """Yield every paragraph in the document: body, tables (recursive), headers, footers."""
    # Body paragraphs
    for para in doc.paragraphs:
        yield para

    # Tables (recursive)
    for table in doc.tables:
        yield from _iter_table_paragraphs(table)

    # Headers and footers
    for section in doc.sections:
        for header_footer in (section.header, section.footer,
                              section.first_page_header, section.first_page_footer,
                              section.even_page_header, section.even_page_footer):
            if header_footer is not None:
                for para in header_footer.paragraphs:
                    yield para
                for table in header_footer.tables:
                    yield from _iter_table_paragraphs(table)


def _iter_table_paragraphs(table: Table):
    """Recursively yield paragraphs from a table, including nested tables."""
    for row in table.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                yield para
            for nested_table in cell.tables:
                yield from _iter_table_paragraphs(nested_table)
```

---

## Step 4: Handle Conditional Sections

Conditional blocks follow the pattern:
```
{{IF_RELOCATION}}
  ... relocation content ...
{{END_IF_RELOCATION}}
```

**Logic:**
- If the corresponding flag (e.g., `RELOCATION_PACKAGE`) is `"Yes"`, keep the content but remove the `{{IF_*}}` and `{{END_IF_*}}` marker lines.
- If the flag is not `"Yes"`, remove the markers AND all content between them.

Because markers can span across paragraphs, the safest approach is to process the full concatenated text of each paragraph and handle intra-paragraph markers, then do a second pass to remove entire paragraphs that fall between cross-paragraph markers.

```python
import re

def handle_conditional_sections(doc: DocType, data: dict):
    """
    Process {{IF_*}}...{{END_IF_*}} blocks.
    Works on the full document paragraph list.
    """
    # Find all conditional keys: IF_RELOCATION -> check RELOCATION_PACKAGE
    # Convention: {{IF_<SECTION>}} maps to data key "<SECTION>_PACKAGE" or
    # more generally, we check if the section name appears as a key with value "Yes".
    # The mapping depends on the template; commonly IF_RELOCATION -> RELOCATION_PACKAGE.

    all_paragraphs = list(iter_all_paragraphs(doc))

    # First: handle conditionals that are entirely within a single paragraph
    for para in all_paragraphs:
        full_text = "".join(run.text for run in para.runs)
        # Pattern: {{IF_XXX}}...{{END_IF_XXX}} within one paragraph
        pattern = r'\{\{IF_(\w+)\}\}(.*?)\{\{END_IF_\1\}\}'
        match = re.search(pattern, full_text)
        if match:
            section_name = match.group(1)
            keep = _should_keep_section(section_name, data)
            if keep:
                # Keep inner content, strip markers
                replacement = match.group(2)
            else:
                replacement = ""
            new_text = full_text[:match.start()] + replacement + full_text[match.end():]
            _set_paragraph_text(para, new_text)

    # Second: handle cross-paragraph conditionals
    # Identify paragraphs that contain only the opening/closing marker
    removing = False
    remove_section = ""
    paragraphs_to_clear = []

    for para in all_paragraphs:
        full_text = "".join(run.text for run in para.runs).strip()

        # Check for opening marker
        open_match = re.match(r'^\s*\{\{IF_(\w+)\}\}\s*$', full_text)
        if open_match and not removing:
            section_name = open_match.group(1)
            keep = _should_keep_section(section_name, data)
            if keep:
                # Just clear the marker paragraph
                _set_paragraph_text(para, "")
            else:
                removing = True
                remove_section = section_name
                _set_paragraph_text(para, "")
            continue

        # Check for closing marker
        close_match = re.match(r'^\s*\{\{END_IF_(\w+)\}\}\s*$', full_text)
        if close_match:
            section_name = close_match.group(1)
            if removing and section_name == remove_section:
                removing = False
                remove_section = ""
            _set_paragraph_text(para, "")
            continue

        # If we're inside a removal block, clear the paragraph
        if removing:
            _set_paragraph_text(para, "")

        # Also strip inline markers that aren't the sole content
        for marker_pattern in [r'\{\{IF_\w+\}\}', r'\{\{END_IF_\w+\}\}']:
            current = "".join(run.text for run in para.runs)
            cleaned = re.sub(marker_pattern, '', current)
            if cleaned != current:
                _set_paragraph_text(para, cleaned)


def _should_keep_section(section_name: str, data: dict) -> bool:
    """Determine whether to keep a conditional section."""
    # Common convention: IF_RELOCATION -> RELOCATION_PACKAGE = "Yes"
    package_key = f"{section_name}_PACKAGE"
    if package_key in data:
        return data[package_key].strip().lower() == "yes"
    # Fallback: check if section_name itself is a key
    if section_name in data:
        return data[section_name].strip().lower() == "yes"
    # Default: remove if we can't determine
    return False
```

---

## Step 5: Replace Split-Run Placeholders

This is the critical technique. Word splits text into "runs" based on formatting changes, spell-check boundaries, or editing history. A placeholder like `{{CANDIDATE_FULL_NAME}}` might be stored as three runs: `{{CAND` | `IDATE_FULL_` | `NAME}}`.

**Algorithm:**
1. Concatenate all run texts in the paragraph.
2. Perform all `{{KEY}}` → `value` replacements on the concatenated string.
3. Put the entire result into the first run, clear the rest.

This preserves the formatting of the first run. For most template documents this is acceptable because placeholders share uniform formatting.

```python
import re

def replace_placeholders_in_paragraph(para, data: dict):
    """
    Replace {{KEY}} placeholders in a paragraph, handling split runs.
    """
    # Concatenate all run texts
    full_text = "".join(run.text for run in para.runs)

    if "{{" not in full_text:
        return  # Nothing to replace

    # Replace all {{KEY}} with corresponding values
    new_text = full_text
    for key, value in data.items():
        placeholder = "{{" + key + "}}"
        new_text = new_text.replace(placeholder, value)

    # Only modify if something changed
    if new_text != full_text:
        _set_paragraph_text(para, new_text)


def _set_paragraph_text(para, new_text: str):
    """
    Set paragraph text by putting everything in the first run and clearing the rest.
    Preserves the formatting of the first run.
    """
    if not para.runs:
        return
    para.runs[0].text = new_text
    for run in para.runs[1:]:
        run.text = ""
```

---

## Step 6: Save and Verify

```python
def fill_and_save(template_path: str, data_path: str, output_path: str):
    data = load_employee_data(data_path)
    doc = load_template(template_path)

    # Step 1: Handle conditional sections BEFORE placeholder replacement
    handle_conditional_sections(doc, data)

    # Step 2: Replace placeholders everywhere
    for para in iter_all_paragraphs(doc):
        replace_placeholders_in_paragraph(para, data)

    # Step 3: Save
    doc.save(output_path)

    # Step 4: Verify
    verify_no_remaining_placeholders(output_path)


def verify_no_remaining_placeholders(docx_path: str):
    """Open the saved doc and assert no {{...}} markers remain."""
    doc = Document(docx_path)
    all_text = []
    for para in iter_all_paragraphs(doc):
        all_text.append("".join(run.text for run in para.runs))
    combined = "\n".join(all_text)

    remaining = re.findall(r'\{\{.*?\}\}', combined)
    if remaining:
        raise AssertionError(
            f"Unfilled placeholders remain in output: {remaining}"
        )
    print(f"✓ Verification passed — no remaining placeholders in {docx_path}")
```

---

## Common Pitfalls

### 1. Split Runs (Most Common Failure)
**Problem:** Searching for `{{CANDIDATE_FULL_NAME}}` in individual `run.text` values finds nothing because Word split it across runs.
**Fix:** Always concatenate all run texts in a paragraph before searching, then redistribute.

### 2. Missing Table/Header/Footer Paragraphs
**Problem:** Placeholders in table cells, nested tables, or headers/footers are silently skipped.
**Fix:** Use the recursive `iter_all_paragraphs` function that walks tables (including nested ones), headers, and footers.

### 3. Conditional Markers Left Behind
**Problem:** `{{IF_RELOCATION}}` and `{{END_IF_RELOCATION}}` markers remain in the output even when the content is kept.
**Fix:** Always strip the marker tags regardless of whether the section is kept or removed. Process conditionals BEFORE placeholder replacement.

### 4. Conditional Markers Split Across Runs
**Problem:** The marker `{{IF_RELOCATION}}` itself can be split across runs, so regex on individual runs misses it.
**Fix:** Use the same concatenate-then-replace strategy for conditional markers.

### 5. Non-String JSON Values
**Problem:** JSON may contain integers (e.g., `"PTO_DAYS": 20`). The `.replace()` call expects strings.
**Fix:** Convert all data values to strings at load time.

### 6. Processing Order
**Problem:** If you replace placeholders first, the conditional markers might get partially mangled.
**Fix:** Always handle conditional sections first, then do placeholder replacement.

### 7. Installing Dependencies
**Problem:** On restricted environments, `pip install` may require `--break-system-packages`.
**Fix:** Try `pip install python-docx` first; if it fails, use `pip install --break-system-packages python-docx`.

---

## Reference Implementation

This is a complete, self-contained script. Copy it, adjust the three file paths at the bottom, and run.

```python
#!/usr/bin/env python3
"""
Offer Letter Generator — Fill a DOCX template from JSON data.

Handles:
  - Split-run placeholders (Word XML splitting {{KEY}} across runs)
  - Conditional sections: {{IF_SECTION}}...{{END_IF_SECTION}}
  - Nested tables, headers, and footers
  - Non-string JSON values

Usage:
    python3 fill_offer_letter.py

Adjust TEMPLATE_PATH, DATA_PATH, OUTPUT_PATH at the bottom as needed.
"""

import json
import re
from pathlib import Path
from docx import Document
from docx.table import Table


# ---------------------------------------------------------------------------
# 1. DATA LOADING
# ---------------------------------------------------------------------------

def load_employee_data(json_path: str) -> dict:
    """Load employee data from JSON. All values are coerced to strings."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: str(v) for k, v in data.items()}


# ---------------------------------------------------------------------------
# 2. PARAGRAPH COLLECTION (body + tables + nested tables + headers + footers)
# ---------------------------------------------------------------------------

def _iter_table_paragraphs(table: Table):
    """Recursively yield paragraphs from a table, including nested tables."""
    for row in table.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                yield para
            for nested_table in cell.tables:
                yield from _iter_table_paragraphs(nested_table)


def iter_all_paragraphs(doc: Document):
    """Yield every paragraph in the document."""
    # Body
    for para in doc.paragraphs:
        yield para

    # Body tables (recursive)
    for table in doc.tables:
        yield from _iter_table_paragraphs(table)

    # Headers and footers across all sections
    for section in doc.sections:
        for hf in (
            section.header, section.footer,
            section.first_page_header, section.first_page_footer,
            section.even_page_header, section.even_page_footer,
        ):
            if hf is None:
                continue
            for para in hf.paragraphs:
                yield para
            for table in hf.tables:
                yield from _iter_table_paragraphs(table)


# ---------------------------------------------------------------------------
# 3. LOW-LEVEL HELPERS
# ---------------------------------------------------------------------------

def _get_paragraph_text(para) -> str:
    """Get the full text of a paragraph by joining all runs."""
    return "".join(run.text for run in para.runs)


def _set_paragraph_text(para, new_text: str):
    """
    Replace paragraph text by putting everything in the first run
    and clearing subsequent runs. Preserves first-run formatting.
    """
    if not para.runs:
        return
    para.runs[0].text = new_text
    for run in para.runs[1:]:
        run.text = ""


# ---------------------------------------------------------------------------
# 4. CONDITIONAL SECTION HANDLING
# ---------------------------------------------------------------------------

def _should_keep_section(section_name: str, data: dict) -> bool:
    """
    Determine whether to keep a conditional section.
    Convention: {{IF_RELOCATION}} checks data["RELOCATION_PACKAGE"] == "Yes"
    """
    # Try <SECTION>_PACKAGE first (most common convention)
    package_key = f"{section_name}_PACKAGE"
    if package_key in data:
        return data[package_key].strip().lower() == "yes"
    # Fallback: check section name directly
    if section_name in data:
        return data[section_name].strip().lower() == "yes"
    # Unknown section — remove to be safe
    return False


def handle_conditional_sections(doc: Document, data: dict):
    """
    Process {{IF_*}}...{{END_IF_*}} blocks across all paragraphs.

    - If the condition is true: keep inner content, strip markers.
    - If the condition is false: remove markers AND inner content.
    """
    all_paragraphs = list(iter_all_paragraphs(doc))

    # --- Pass 1: Inline conditionals (opening + closing in same paragraph) ---
    for para in all_paragraphs:
        full_text = _get_paragraph_text(para)
        pattern = r'\{\{IF_(\w+)\}\}(.*?)\{\{END_IF_\1\}\}'
        match = re.search(pattern, full_text)
        if match:
            section_name = match.group(1)
            keep = _should_keep_section(section_name, data)
            replacement = match.group(2) if keep else ""
            new_text = full_text[:match.start()] + replacement + full_text[match.end():]
            _set_paragraph_text(para, new_text)

    # --- Pass 2: Cross-paragraph conditionals ---
    removing = False
    remove_section = ""

    for para in all_paragraphs:
        full_text = _get_paragraph_text(para).strip()

        # Opening marker (standalone paragraph)
        open_match = re.match(r'^\s*\{\{IF_(\w+)\}\}\s*$', full_text)
        if open_match and not removing:
            section_name = open_match.group(1)
            keep = _should_keep_section(section_name, data)
            _set_paragraph_text(para, "")  # Always strip the marker
            if not keep:
                removing = True
                remove_section = section_name
            continue

        # Closing marker (standalone paragraph)
        close_match = re.match(r'^\s*\{\{END_IF_(\w+)\}\}\s*$', full_text)
        if close_match:
            section_name = close_match.group(1)
            if removing and section_name == remove_section:
                removing = False
                remove_section = ""
            _set_paragraph_text(para, "")  # Always strip the marker
            continue

        # Inside a removal block — clear content
        if removing:
            _set_paragraph_text(para, "")
            continue

        # Strip any remaining inline markers (mixed content paragraphs)
        current = _get_paragraph_text(para)
        cleaned = current
        for marker_re in [r'\{\{IF_\w+\}\}', r'\{\{END_IF_\w+\}\}']:
            cleaned = re.sub(marker_re, '', cleaned)
        if cleaned != current:
            _set_paragraph_text(para, cleaned)


# ---------------------------------------------------------------------------
# 5. PLACEHOLDER REPLACEMENT
# ---------------------------------------------------------------------------

def replace_placeholders_in_paragraph(para, data: dict):
    """
    Replace all {{KEY}} placeholders in a paragraph.
    Handles split-run placeholders by concatenating runs first.
    """
    full_text = _get_paragraph_text(para)

    if "{{" not in full_text:
        return

    new_text = full_text
    for key, value in data.items():
        placeholder = "{{" + key + "}}"
        new_text = new_text.replace(placeholder, value)

    if new_text != full_text:
        _set_paragraph_text(para, new_text)


# ---------------------------------------------------------------------------
# 6. MAIN PIPELINE
# ---------------------------------------------------------------------------

def fill_offer_letter(template_path: str, data_path: str, output_path: str):
    """
    End-to-end: load data, load template, process conditionals,
    replace placeholders, save, and verify.
    """
    # Load
    data = load_employee_data(data_path)
    doc = Document(template_path)

    # Process conditionals FIRST (before placeholder replacement)
    handle_conditional_sections(doc, data)

    # Replace placeholders everywhere
    for para in iter_all_paragraphs(doc):
        replace_placeholders_in_paragraph(para, data)

    # Save
    doc.save(output_path)
    print(f"Saved filled offer letter to {output_path}")

    # Verify
    verify_no_remaining_placeholders(output_path)


def verify_no_remaining_placeholders(docx_path: str):
    """Reopen the saved document and assert no {{...}} markers remain."""
    doc = Document(docx_path)
    all_text_parts = []
    for para in iter_all_paragraphs(doc):
        all_text_parts.append(_get_paragraph_text(para))
    combined = "\n".join(all_text_parts)

    remaining = re.findall(r'\{\{.*?\}\}', combined)
    if remaining:
        raise AssertionError(
            f"Unfilled placeholders remain in output: {remaining}"
        )
    print(f"✓ Verification passed — no remaining placeholders in {docx_path}")


# ---------------------------------------------------------------------------
# 7. ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    TEMPLATE_PATH = "/root/offer_letter_template.docx"
    DATA_PATH = "/root/employee_data.json"
    OUTPUT_PATH = "/root/offer_letter_filled.docx"

    fill_offer_letter(TEMPLATE_PATH, DATA_PATH, OUTPUT_PATH)
```

---

## Verification Checklist

Before considering the task complete, run these checks:

1. **No remaining placeholders** — Reopen the output `.docx` and search for `{{`. There should be zero matches.
2. **Conditional markers stripped** — Search for `{{IF_` and `{{END_IF_`. Zero matches.
3. **Conditional content correct** — If the flag is `"Yes"`, the relocation (or other conditional) content should be present. If `"No"`, it should be absent.
4. **Table values filled** — Open the document and visually or programmatically confirm that table cells contain the expected values (position, salary, etc.), not placeholders.
5. **Headers/footers filled** — Check headers and footers specifically; they are a separate XML part and easy to miss.

```python
# Quick programmatic verification you can run after generation:
import re
from docx import Document

doc = Document("/root/offer_letter_filled.docx")
all_text = []
for para in iter_all_paragraphs(doc):
    all_text.append("".join(run.text for run in para.runs))
combined = "\n".join(all_text)

assert not re.findall(r'\{\{.*?\}\}', combined), "Unfilled placeholders found!"
assert "{{IF_" not in combined, "Conditional markers remain!"
assert "{{END_IF_" not in combined, "Conditional end markers remain!"
print("All checks passed.")
```

---

## Environment Setup

```bash
# Install python-docx (the only required dependency)
pip install python-docx==1.1.2

# On restricted Ubuntu systems where system Python is managed:
pip install --break-system-packages python-docx==1.1.2

# For running tests:
pip install --break-system-packages pytest
```

---

## Adapting to New Templates

When you encounter a new template:

1. **Inspect the template first** — Run a quick script to dump all paragraph texts (concatenated runs) to see what placeholders exist and how they're split:

```python
from docx import Document

doc = Document("template.docx")
for i, para in enumerate(doc.paragraphs):
    runs_detail = [repr(r.text) for r in para.runs]
    combined = "".join(r.text for r in para.runs)
    if "{{" in combined or "}}" in combined:
        print(f"Para {i}: {runs_detail}")
        print(f"  Combined: {combined}")
```

2. **Identify conditional patterns** — Look for `{{IF_*}}` markers and determine the corresponding data key convention.

3. **Check for nested tables** — Some templates use tables within table cells for layout. The recursive iterator handles this, but verify by checking `cell.tables` in your inspection.

4. **Match data keys to placeholders** — Ensure every `{{KEY}}` in the template has a corresponding key in the JSON data. Log any mismatches as warnings.