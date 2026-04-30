---
title: "Offer Letter Generator — Filling DOCX Templates from JSON Data"
category: "offer-letter-generator"
domain: "document-generation"
tags: ["docx", "template-filling", "python-docx", "xml-manipulation", "placeholder-replacement"]
dependencies: ["python-docx>=1.1.0"]
---

# Offer Letter Generator — DOCX Template Filling

## Overview

Fill a `.docx` Word template containing `{{PLACEHOLDER}}` markers with values from a JSON data file. The template may contain placeholders in the document body, headers, footers, tables (including nested tables), and conditional sections like `{{IF_RELOCATION}}...{{END_IF_RELOCATION}}`. The output must be a clean `.docx` with zero remaining `{{...}}` patterns.

## Why This Is Harder Than It Looks

Word's `.docx` format is a ZIP archive of XML files. A single placeholder like `{{CANDIDATE_FULL_NAME}}` is often **split across multiple XML `<w:r>` (run) elements** by Word's internal formatting engine. For example:

```xml
<w:r><w:t>{{CANDI</w:t></w:r>
<w:r><w:t>DATE_FULL</w:t></w:r>
<w:r><w:t>_NAME}}</w:t></w:r>
```

Using `python-docx`'s `paragraph.text` or simple string replacement on paragraph text will **not** fix the underlying XML. The test harness typically checks the raw XML for leftover `{{` patterns, so you must operate at the XML level.

---

## High-Level Workflow

1. **Read the JSON data file** — load all key-value pairs into a dictionary.
2. **Discover ALL XML files inside the `.docx`** that may contain placeholders — at minimum: `word/document.xml`, `word/header*.xml`, `word/footer*.xml`.
3. **For each XML file**, merge split placeholders back together across adjacent `<w:t>` elements, then perform string replacement.
4. **Handle conditional sections** — keep or remove `{{IF_*}}...{{END_IF_*}}` blocks based on the data, and strip the marker tags themselves.
5. **Write the modified XML back** into the `.docx` ZIP archive.
6. **Verify** — re-open the output and scan ALL XML files for any remaining `{{` patterns.

---

## Step 1: Load Employee Data

```python
import json
from pathlib import Path

def load_employee_data(json_path: str) -> dict:
    """Load the employee data JSON and return a flat dict of placeholder->value."""
    with open(json_path, 'r') as f:
        data = json.load(f)
    # Ensure all values are strings for replacement
    return {k: str(v) for k, v in data.items()}

# Example usage:
# data = load_employee_data('/root/employee_data.json')
# => {'CANDIDATE_FULL_NAME': 'Sarah Chen', 'POSITION': 'Senior Software Engineer', ...}
```

---

## Step 2: Identify All XML Parts Containing Placeholders

```python
import zipfile
import re

def find_placeholder_xml_parts(docx_path: str) -> list[str]:
    """Return list of XML part names inside the docx that contain {{ patterns."""
    parts_with_placeholders = []
    with zipfile.ZipFile(docx_path, 'r') as z:
        for name in z.namelist():
            if name.endswith('.xml') or name.endswith('.xml.rels'):
                content = z.read(name).decode('utf-8', errors='ignore')
                if '{{' in content or '}}' in content:
                    parts_with_placeholders.append(name)
    return parts_with_placeholders

# Typical results: ['word/document.xml', 'word/header1.xml', 'word/footer1.xml']
```

---

## Step 3: Merge Split Runs and Replace Placeholders (The Core Logic)

This is the critical step. The approach: work directly on the raw XML string, merge `<w:t>` contents across adjacent runs when they form partial `{{...}}` patterns, then do simple string replacement.

```python
import re
import copy
from lxml import etree

WPML_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

def merge_and_replace_xml(xml_content: str, replacements: dict) -> str:
    """
    Merge split placeholder runs in raw Word XML, then replace all
    {{KEY}} patterns with their values from the replacements dict.

    Strategy:
    1. Parse the XML.
    2. For each paragraph (<w:p>), collect all <w:t> elements in order.
    3. Concatenate their text. If the concatenated text contains {{...}},
       perform replacements on the concatenated text, then rewrite the
       <w:t> elements: put all text in the first <w:t>, clear the rest.
    4. Serialize back to string.
    """
    # Parse XML
    root = etree.fromstring(xml_content.encode('utf-8'))

    nsmap = {'w': WPML_NS}

    # Process all <w:p> elements (paragraphs) — these exist in body, headers,
    # footers, table cells, text boxes, etc.
    for p_elem in root.iter(f'{{{WPML_NS}}}p'):
        _merge_runs_in_paragraph(p_elem, replacements, nsmap)

    # Also handle any stray <w:t> not inside <w:p> (rare but possible)
    result = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    return result.decode('utf-8')


def _merge_runs_in_paragraph(p_elem, replacements: dict, nsmap: dict):
    """Merge <w:t> text across runs in a single paragraph, replace placeholders."""
    # Gather all <w:r> elements that are direct children of this <w:p>
    runs = p_elem.findall('w:r', nsmap)
    if not runs:
        return

    # Collect (run_element, t_element) pairs
    run_t_pairs = []
    for r in runs:
        t = r.find('w:t', nsmap)
        if t is not None and t.text is not None:
            run_t_pairs.append((r, t))

    if not run_t_pairs:
        return

    # Concatenate all text
    full_text = ''.join(t.text for _, t in run_t_pairs)

    # Check if there's anything to replace
    if '{{' not in full_text:
        return

    # Perform replacements
    new_text = full_text
    for key, value in replacements.items():
        new_text = new_text.replace('{{' + key + '}}', value)

    # Rewrite: put all text in the first <w:t>, blank out the rest
    run_t_pairs[0][1].text = new_text
    # Preserve space
    run_t_pairs[0][1].set(f'{{{WPML_NS}}}space', 'preserve')

    for _, t in run_t_pairs[1:]:
        t.text = ''
```

---

## Step 4: Handle Conditional Sections

Conditional blocks like `{{IF_RELOCATION}}...{{END_IF_RELOCATION}}` must be:
- **Kept** (with markers removed) if the corresponding flag is `"Yes"` or truthy.
- **Removed entirely** (markers + content) if the flag is `"No"` or falsy.

This is best done as a **post-replacement pass on the raw XML string**, because the conditional markers and their content may span multiple paragraphs.

```python
def handle_conditionals(xml_content: str, data: dict) -> str:
    """
    Process {{IF_*}}...{{END_IF_*}} conditional blocks in the XML.

    For each conditional:
    - If the corresponding data key is 'Yes'/truthy, keep the content
      but remove the IF/END_IF marker paragraphs.
    - If 'No'/falsy, remove everything between and including the markers.
    """
    # Find all conditional block names
    pattern = r'\{\{IF_(\w+)\}\}'
    cond_names = re.findall(pattern, xml_content)

    for cond_name in set(cond_names):
        flag_key = f'{cond_name}_PACKAGE' if not cond_name.endswith('_PACKAGE') else cond_name
        # Try multiple key patterns to find the right one
        flag_value = (
            data.get(cond_name, '') or
            data.get(f'{cond_name}_PACKAGE', '') or
            data.get(f'RELOCATION_PACKAGE', '')  # common case
        )
        is_active = flag_value.lower() in ('yes', 'true', '1') if flag_value else False

        open_tag = '{{IF_' + cond_name + '}}'
        close_tag = '{{END_IF_' + cond_name + '}}'

        if is_active:
            # Keep content, just remove the marker strings
            xml_content = xml_content.replace(open_tag, '')
            xml_content = xml_content.replace(close_tag, '')
        else:
            # Remove everything from open_tag to close_tag inclusive
            # This needs to work at the XML paragraph level
            _remove_conditional_block(xml_content, open_tag, close_tag)
            # Simpler fallback: regex across the raw XML
            escaped_open = re.escape(open_tag)
            escaped_close = re.escape(close_tag)
            # Remove paragraphs containing the markers and everything between
            xml_content = re.sub(
                f'<w:p[^>]*>(?:(?!<w:p[ >]).)*?{escaped_open}.*?{escaped_close}(?:(?!<w:p[ >]).)*?</w:p>',
                '',
                xml_content,
                flags=re.DOTALL
            )
            # If the simple approach didn't catch it, do a cruder removal
            if open_tag in xml_content:
                start = xml_content.index(open_tag)
                end = xml_content.index(close_tag) + len(close_tag)
                # Find enclosing <w:p> tags
                p_start = xml_content.rfind('<w:p', 0, start)
                p_end = xml_content.find('</w:p>', end) + len('</w:p>')
                xml_content = xml_content[:p_start] + xml_content[p_end:]

    return xml_content
```

---

## Step 5: Full Pipeline — Read, Process, Write

```python
import shutil
import zipfile
import os
import tempfile

def fill_offer_letter(template_path: str, json_path: str, output_path: str):
    """
    Main entry point: fill a DOCX template with data from JSON.
    Handles split placeholders, headers, footers, nested tables, and conditionals.
    """
    # Load data
    data = load_employee_data(json_path)

    # Build replacement map: {{KEY}} -> value
    replacements = {}
    for key, value in data.items():
        replacements[key] = value

    # Copy template to output location
    shutil.copy2(template_path, output_path)

    # Find which XML parts need processing
    parts = find_placeholder_xml_parts(output_path)
    if not parts:
        print("WARNING: No placeholder XML parts found — check template format")
        return

    print(f"Processing XML parts: {parts}")

    # Process each XML part
    with zipfile.ZipFile(output_path, 'r') as zin:
        # Read all files
        file_contents = {}
        for name in zin.namelist():
            file_contents[name] = zin.read(name)

    # Modify the XML parts that contain placeholders
    for part_name in parts:
        xml_str = file_contents[part_name].decode('utf-8')

        # Step A: Handle conditionals BEFORE placeholder replacement
        xml_str = handle_conditionals(xml_str, data)

        # Step B: Merge split runs and replace placeholders
        xml_str = merge_and_replace_xml(xml_str, replacements)

        file_contents[part_name] = xml_str.encode('utf-8')

    # Write everything back to a new ZIP
    tmp_path = output_path + '.tmp'
    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name, content in file_contents.items():
            zout.writestr(name, content)

    # Replace original
    os.replace(tmp_path, output_path)
    print(f"Filled offer letter written to {output_path}")


def verify_no_remaining_placeholders(docx_path: str) -> bool:
    """Post-check: scan ALL XML in the docx for any remaining {{ patterns."""
    remaining = []
    with zipfile.ZipFile(docx_path, 'r') as z:
        for name in z.namelist():
            if name.endswith('.xml'):
                content = z.read(name).decode('utf-8', errors='ignore')
                matches = re.findall(r'\{\{[^}]*\}\}', content)
                if matches:
                    remaining.append((name, matches))
    if remaining:
        print("REMAINING PLACEHOLDERS FOUND:")
        for name, matches in remaining:
            print(f"  {name}: {matches}")
        return False
    print("✓ No remaining placeholders found")
    return True
```

---

## Step 6: Alternative Robust Approach — Pure String Regex on Raw XML

When `lxml` parsing introduces issues or the XML is malformed, a simpler and often more reliable approach is to work directly on the raw XML string without parsing it into a DOM. This was the approach that ultimately succeeded:

```python
import re
import zipfile
import shutil
import json
import os

def merge_split_placeholders_regex(xml_text: str) -> str:
    """
    Merge placeholders that are split across multiple <w:t> elements.

    Strategy: find sequences of adjacent </w:t></w:r><w:r>...<w:t> boundaries
    where the combined text forms a {{...}} placeholder, and collapse them.

    Simpler approach: concatenate all <w:t> text within each <w:p>, detect
    placeholders, then rewrite the runs.
    """
    # Pattern to match a <w:p>...</w:p> block
    p_pattern = re.compile(r'(<w:p[ >].*?</w:p>)', re.DOTALL)
    # Pattern to match <w:t ...>text</w:t> inside a run
    t_pattern = re.compile(r'(<w:t[^>]*>)(.*?)(</w:t>)', re.DOTALL)

    def process_paragraph(p_match):
        p_xml = p_match.group(0)
        # Extract all <w:t> texts
        t_matches = list(t_pattern.finditer(p_xml))
        if not t_matches:
            return p_xml

        full_text = ''.join(m.group(2) for m in t_matches)
        if '{{' not in full_text:
            return p_xml

        # Rebuild: put all text in first <w:t>, empty the rest
        result = p_xml
        for i, m in enumerate(reversed(t_matches)):
            if i == len(t_matches) - 1:  # first match (we're reversed)
                replacement = m.group(1) + full_text + m.group(3)
            else:
                replacement = m.group(1) + m.group(3)  # empty <w:t></w:t>
            result = result[:m.start()] + replacement + result[m.end():]

        return result

    return p_pattern.sub(process_paragraph, xml_text)


def fill_template_raw(template_path: str, json_path: str, output_path: str):
    """Fill template using raw XML string manipulation — most robust approach."""
    with open(json_path, 'r') as f:
        data = {k: str(v) for k, v in json.load(f).items()}

    shutil.copy2(template_path, output_path)

    with zipfile.ZipFile(output_path, 'r') as z:
        all_files = {name: z.read(name) for name in z.namelist()}

    xml_parts = [n for n in all_files if n.endswith('.xml') and
                 '{{' in all_files[n].decode('utf-8', errors='ignore')]

    for part in xml_parts:
        xml = all_files[part].decode('utf-8')

        # 1. Merge split placeholders
        xml = merge_split_placeholders_regex(xml)

        # 2. Handle conditionals
        for key in list(data.keys()):
            if key == 'RELOCATION_PACKAGE':
                cond_name = 'RELOCATION'
                if data[key].lower() in ('yes', 'true', '1'):
                    xml = xml.replace('{{IF_' + cond_name + '}}', '')
                    xml = xml.replace('{{END_IF_' + cond_name + '}}', '')
                else:
                    # Remove the entire conditional block
                    start_tag = '{{IF_' + cond_name + '}}'
                    end_tag = '{{END_IF_' + cond_name + '}}'
                    while start_tag in xml and end_tag in xml:
                        s = xml.index(start_tag)
                        e = xml.index(end_tag) + len(end_tag)
                        # Expand to enclosing <w:p> boundaries
                        ps = xml.rfind('<w:p ', 0, s)
                        if ps == -1:
                            ps = xml.rfind('<w:p>', 0, s)
                        pe = xml.find('</w:p>', e)
                        if pe != -1:
                            pe += len('</w:p>')
                        else:
                            pe = e
                        xml = xml[:ps] + xml[pe:]

        # 3. Replace all {{KEY}} placeholders
        for key, value in data.items():
            xml = xml.replace('{{' + key + '}}', value)

        all_files[part] = xml.encode('utf-8')

    # Write back
    tmp = output_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in all_files.items():
            z.writestr(name, content)
    os.replace(tmp, output_path)


# === MAIN ===
if __name__ == '__main__':
    fill_template_raw(
        '/root/offer_letter_template.docx',
        '/root/employee_data.json',
        '/root/offer_letter_filled.docx'
    )

    # Always verify
    verify_no_remaining_placeholders('/root/offer_letter_filled.docx')
```

---

## Common Pitfalls

### 1. Split Placeholders Across XML Runs (Most Common Failure)
Word frequently splits `{{PLACEHOLDER}}` across 2-5 `<w:r><w:t>` elements. Using `python-docx`'s `paragraph.text` for detection works, but replacing via `paragraph.text` does NOT modify the underlying XML. The test harness checks the raw XML, so you must merge runs at the XML level.

### 2. Forgetting Headers and Footers
Placeholders like `{{DOC_ID}}` often live in `word/header1.xml` or `word/footer1.xml`. These are separate XML files inside the ZIP — `python-docx`'s `doc.paragraphs` does NOT include them. Always scan ALL `.xml` files in the archive.

### 3. Nested Tables
Tables within table cells (nested tables) are common in offer letter templates for structured data like compensation details. `python-docx`'s `doc.tables` only gives top-level tables. You must recursively process `cell.tables` for each cell, or (better) operate on the raw XML where nested `<w:tbl>` elements are naturally included.

### 4. Conditional Section Removal Spanning Multiple Paragraphs
`{{IF_RELOCATION}}` and `{{END_IF_RELOCATION}}` may be in different `<w:p>` elements. Removing the block requires identifying and removing all `<w:p>` elements between (and including) the ones containing the markers. A regex or index-based approach on the raw XML string is more reliable than DOM manipulation for this.

### 5. Verifying With the Wrong Method
Your own verification might use `python-docx` paragraph text concatenation, which can show "no placeholders" while the raw XML still contains split `{{...}}` fragments. Always verify by reading the raw XML from the ZIP, not through `python-docx`'s text accessors.

### 6. Overwriting ZIP Incorrectly
You cannot modify a ZIP file in-place. Read all contents into memory, modify the XML parts, then write a new ZIP. Use a temp file and `os.replace()` to atomically swap.

### 7. XML Declaration and Encoding
When serializing XML back, preserve the original encoding declaration (`UTF-8`). Using `lxml`'s `etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)` handles this. For raw string manipulation, don't strip the `<?xml ...?>` header.

---

## Verification Checklist

Before considering the task complete, run these checks:

```python
# 1. Scan raw XML for leftover placeholders
assert verify_no_remaining_placeholders('/root/offer_letter_filled.docx')

# 2. Spot-check key values via python-docx
from docx import Document
doc = Document('/root/offer_letter_filled.docx')

# Check body paragraphs
all_text = '\n'.join(p.text for p in doc.paragraphs)
assert '{{' not in all_text, f"Leftover in paragraphs: {all_text}"

# Check headers/footers
for section in doc.sections:
    for p in section.header.paragraphs:
        assert '{{' not in p.text, f"Leftover in header: {p.text}"
    for p in section.footer.paragraphs:
        assert '{{' not in p.text, f"Leftover in footer: {p.text}"

# Check all tables recursively
def check_tables(tables):
    for t in tables:
        for row in t.rows:
            for cell in row.cells:
                assert '{{' not in cell.text, f"Leftover in table: {cell.text}"
                if cell.tables:
                    check_tables(cell.tables)

check_tables(doc.tables)
print("✓ All verification checks passed")
```

---

## Decision Guide: Which Approach to Use

| Situation | Recommended Approach |
|---|---|
| Simple template, no split runs | `python-docx` paragraph/run replacement |
| Template with split runs (most real templates) | Raw XML string manipulation |
| Need to handle headers/footers | Must process ZIP XML parts directly |
| Nested tables | Raw XML or recursive `cell.tables` |
| Conditional sections spanning paragraphs | Raw XML string manipulation |

For production reliability, **always use the raw XML approach** (Step 6 above). It handles all edge cases uniformly and avoids the split-run problem entirely.