---
name: California Small Claims Court Form Filling (SC-100)
version: 1.0.0
category: court-form-filling
tags: [pdf, forms, legal, california, small-claims, pymupdf, fitz]
description: >
  Procedural skill for filling California Small Claims Court form SC-100 from
  a case description and saving the result as a valid, field-filled PDF.
---

# Skill: California Small Claims Court Form Filling (SC-100)

## Overview

This skill covers how to programmatically fill the California Judicial Council
form SC-100 (Small Claims) using Python and PyMuPDF (`fitz`). The task involves:

1. Inspecting the blank PDF's AcroForm fields
2. Mapping case description details to the correct field names
3. Setting text fields and checkboxes appropriately
4. Saving the result as a valid PDF

The environment provides: `pymupdf` (fitz), `pypdf`, `fillpdf`, `pdfrw`,
`PyPDF2`, `reportlab`, and `poppler-utils`.

---

## High-Level Workflow

### Step 1 — Verify Available Libraries

Before writing any fill logic, confirm which PDF library is available. PyMuPDF
(`fitz`) is the most capable for AcroForm manipulation and should be preferred.

```python
import subprocess
result = subprocess.run(
    ["python3", "-c", "import fitz; print(fitz.__version__)"],
    capture_output=True, text=True
)
print(result.stdout or result.stderr)
```

Priority order: `fitz` (pymupdf) > `fillpdf` > `pdfrw` > `PyPDF2`.

---

### Step 2 — Inspect All Form Fields

Always enumerate every field in the blank PDF before writing fill logic. This
reveals exact field names, types (text vs checkbox), and page locations.

```python
import fitz

doc = fitz.open('/root/sc100-blank.pdf')

for page_num, page in enumerate(doc):
    widgets = list(page.widgets())
    print(f"\n=== Page {page_num + 1} ({len(widgets)} widgets) ===")
    for w in widgets:
        print(f"  [{w.field_type_string:10s}] {w.field_name!r:60s} | value={w.field_value!r}")

doc.close()
```

Key things to note from the output:
- Field names follow the pattern `SC-100[0].PageN[0].SectionX[0].FieldY[0]`
- Checkboxes have two variants: `[0]` = unchecked, `[1]` = checked (Yes/No pairs)
- Text fields accept strings directly
- Some fields are read-only or court-filled — skip those

---

### Step 3 — Map Case Details to Fields

The SC-100 form has these major sections. Map the case description to each:

| Section | Content | Field Type |
|---------|---------|------------|
| 1 | Plaintiff name, address, city, state, zip, phone, email | Text |
| 2 | Defendant name, address, city, state, zip, phone | Text |
| 3 | Dollar amount, claim description, date range | Text |
| 4 | Was defendant asked to pay? | Checkbox (Yes/No) |
| 5 | Venue reason (where defendant lives, etc.) | Checkbox |
| 6 | Zip code of venue | Text |
| 7 | Attorney fee dispute? | Checkbox (No) |
| 8 | Suing a public entity? | Checkbox (No) |
| 9 | Filed >12 claims in past 12 months? | Checkbox (No for first-time) |
| 10 | Claim >$2,500? | Checkbox |
| Declaration | Date, plaintiff name | Text |

---

### Step 4 — Fill Text Fields and Checkboxes

Use `fitz` to iterate widgets and set values by matching field names.

```python
import fitz

# --- Configuration: adapt these from the case description ---
FIELD_VALUES = {
    # Plaintiff (Section 1)
    'SC-100[0].Page1[0].Plaintiff[0].PlaintiffName[0]': 'Joyce He',
    'SC-100[0].Page1[0].Plaintiff[0].StreetAddress[0]': '655 S Fair Oaks Ave',
    'SC-100[0].Page1[0].Plaintiff[0].City[0]': 'Sunnyvale',
    'SC-100[0].Page1[0].Plaintiff[0].State[0]': 'CA',
    'SC-100[0].Page1[0].Plaintiff[0].Zip[0]': '94086',
    'SC-100[0].Page1[0].Plaintiff[0].Phone[0]': '4125886066',
    'SC-100[0].Page1[0].Plaintiff[0].Email[0]': 'he1998@gmail.com',

    # Defendant (Section 2)
    'SC-100[0].Page1[0].Defendant[0].DefendantName[0]': 'Zhi Chen',
    'SC-100[0].Page1[0].Defendant[0].StreetAddress[0]': '299 W Washington Ave',
    'SC-100[0].Page1[0].Defendant[0].City[0]': 'Sunnyvale',
    'SC-100[0].Page1[0].Defendant[0].State[0]': 'CA',
    'SC-100[0].Page1[0].Defendant[0].Zip[0]': '94086',
    'SC-100[0].Page1[0].Defendant[0].Phone[0]': '5125658878',

    # Claim amount and description (Section 3)
    'SC-100[0].Page2[0].ClaimAmount[0]': '1500.00',
    'SC-100[0].Page2[0].ClaimDescription[0]': (
        'Defendant failed to return security deposit per signed roommate '
        'sublease contract after moving out.'
    ),
    'SC-100[0].Page2[0].DateFrom[0]': '2025-09-30',
    'SC-100[0].Page2[0].DateTo[0]': '2026-01-19',

    # Venue zip (Section 6)
    'SC-100[0].Page2[0].VenueZip[0]': '94086',

    # Declaration
    'SC-100[0].Page4[0].DeclarationDate[0]': '2026-01-19',
    'SC-100[0].Page4[0].DeclarantName[0]': 'Joyce He',
}

# Checkboxes to set True (checked)
CHECKBOX_TRUE = {
    # Section 4: Yes — defendant was asked to pay
    'SC-100[0].Page2[0].AskedToPay[0].Checkbox_Yes[0]',
    # Section 5a: Filing where defendant lives
    'SC-100[0].Page2[0].Venue[0].Checkbox5a[0]',
    # Section 7: No — not attorney fee dispute
    'SC-100[0].Page2[0].AttorneyFee[0].Checkbox_No[0]',
    # Section 8: No — not suing public entity
    'SC-100[0].Page2[0].PublicEntity[0].Checkbox_No[0]',
    # Section 9: No — not >12 claims
    'SC-100[0].Page4[0].List9[0].Item9[0].Checkbox62[1]',
    # Section 10: No — not >$2500 (adjust if claim exceeds $2500)
    'SC-100[0].Page4[0].List10[0].li10[0].Checkbox63[1]',
    # First time filing
    'SC-100[0].Page4[0].FirstTime[0].Checkbox_Yes[0]',
}

doc = fitz.open('/root/sc100-blank.pdf')

for page in doc:
    for widget in page.widgets():
        fname = widget.field_name
        if fname in FIELD_VALUES:
            widget.field_value = FIELD_VALUES[fname]
            widget.update()
        elif fname in CHECKBOX_TRUE:
            widget.field_value = True
            widget.update()

doc.save('/root/sc100-filled.pdf', encryption=fitz.PDF_ENCRYPT_KEEP)
doc.close()
print("Saved /root/sc100-filled.pdf")
```

> IMPORTANT: The exact field names above are illustrative. Always run Step 2
> first to get the real field names from the specific blank PDF you are working
> with. Field names vary between form versions.

---

### Step 5 — Verify the Filled PDF

After saving, re-open the filled PDF and confirm all critical fields have the
expected values. This catches silent failures where `widget.update()` did not
persist.

```python
import fitz

EXPECTED = {
    'plaintiff_name': 'Joyce He',
    'defendant_name': 'Zhi Chen',
    'plaintiff_zip': '94086',
    'claim_amount': '1500',
}

doc = fitz.open('/root/sc100-filled.pdf')
found = {}

for page in doc:
    for widget in page.widgets():
        val = str(widget.field_value or '')
        for key, expected in EXPECTED.items():
            if expected.lower() in val.lower():
                found[key] = val

doc.close()

for key, expected in EXPECTED.items():
    status = '✓' if key in found else '✗ MISSING'
    print(f"  {status}: {key} = {found.get(key, '(not found)')}")
```

---

### Step 6 — Handle Incremental Save vs Full Save

When updating an already-saved PDF (e.g., adding more checkboxes after initial
fill), use incremental save to preserve existing data:

```python
doc = fitz.open('/root/sc100-filled.pdf')

# ... make additional widget updates ...

doc.save(
    '/root/sc100-filled.pdf',
    incremental=True,
    encryption=fitz.PDF_ENCRYPT_KEEP
)
doc.close()
```

For a fresh fill from a blank, use a regular save (no `incremental=True`).

---

## Complete Reference Implementation

This is a self-contained script that handles the full workflow. Adapt the
`CASE` dict at the top for each new task.

```python
#!/usr/bin/env python3
"""
Fill California SC-100 Small Claims form from a case description dict.
Requires: pymupdf (fitz)
Usage: python3 fill_sc100.py
"""

import fitz
import sys

# ── Case-specific data (edit this section per task) ──────────────────────────
CASE = {
    # Plaintiff
    'plaintiff_name':    'Joyce He',
    'plaintiff_street':  '655 S Fair Oaks Ave',
    'plaintiff_city':    'Sunnyvale',
    'plaintiff_state':   'CA',
    'plaintiff_zip':     '94086',
    'plaintiff_phone':   '4125886066',
    'plaintiff_email':   'he1998@gmail.com',
    'first_time_filing': True,

    # Defendant
    'defendant_name':    'Zhi Chen',
    'defendant_street':  '299 W Washington Ave',
    'defendant_city':    'Sunnyvale',
    'defendant_state':   'CA',
    'defendant_zip':     '94086',
    'defendant_phone':   '5125658878',

    # Claim
    'claim_amount':      '1500.00',
    'claim_description': (
        'Defendant failed to return security deposit per signed roommate '
        'sublease contract after moving out.'
    ),
    'date_from':         '2025-09-30',
    'date_to':           '2026-01-19',
    'asked_to_pay':      True,   # Was defendant asked to pay?
    'how_asked':         'Via text messages',

    # Venue
    'venue_reason':      'defendant_lives',  # or 'injury_occurred', 'contract_signed', etc.
    'venue_zip':         '94086',

    # Other
    'attorney_fee_dispute': False,
    'suing_public_entity':  False,
    'claims_over_12':       False,   # Filed >12 claims in past 12 months?
    'amount_over_2500':     False,

    # Declaration
    'declaration_date':  '2026-01-19',
}

INPUT_PDF  = '/root/sc100-blank.pdf'
OUTPUT_PDF = '/root/sc100-filled.pdf'
# ─────────────────────────────────────────────────────────────────────────────


def inspect_fields(pdf_path: str) -> dict:
    """Return {field_name: (field_type_string, field_value)} for all widgets."""
    doc = fitz.open(pdf_path)
    fields = {}
    for page_num, page in enumerate(doc):
        for w in page.widgets():
            fields[w.field_name] = {
                'type': w.field_type_string,
                'value': w.field_value,
                'page': page_num,
                'rect': list(w.rect),
            }
    doc.close()
    return fields


def set_field(widget, value):
    """Set a widget value, handling text vs checkbox types."""
    if widget.field_type_string in ('CheckBox', 'RadioButton'):
        widget.field_value = bool(value)
    else:
        widget.field_value = str(value) if value is not None else ''
    widget.update()


def fill_form(case: dict, input_path: str, output_path: str):
    # Step 1: inspect to get real field names
    fields = inspect_fields(input_path)
    print(f"Found {len(fields)} form fields in {input_path}")

    # Step 2: build field-name → value mapping
    # NOTE: These field names are from the SC-100 form as observed.
    # Re-run inspect_fields() if the form version differs.
    text_map = {}
    checkbox_map = {}

    # Helper: only add if field exists in this PDF
    def t(fname, val):
        if fname in fields:
            text_map[fname] = val
        else:
            print(f"  [WARN] text field not found: {fname!r}")

    def cb(fname, val):
        if fname in fields:
            checkbox_map[fname] = val
        else:
            print(f"  [WARN] checkbox not found: {fname!r}")

    # ── Plaintiff ──
    # Discover actual field names by running inspect_fields first, then map:
    for fname, meta in fields.items():
        fl = fname.lower()
        if meta['type'] not in ('CheckBox', 'RadioButton'):
            if 'plaintiffname' in fl or ('plaintiff' in fl and 'name' in fl):
                t(fname, case['plaintiff_name'])
            elif 'plaintiff' in fl and 'street' in fl:
                t(fname, case['plaintiff_street'])
            elif 'plaintiff' in fl and 'city' in fl:
                t(fname, case['plaintiff_city'])
            elif 'plaintiff' in fl and 'state' in fl:
                t(fname, case['plaintiff_state'])
            elif 'plaintiff' in fl and 'zip' in fl:
                t(fname, case['plaintiff_zip'])
            elif 'plaintiff' in fl and 'phone' in fl:
                t(fname, case['plaintiff_phone'])
            elif 'plaintiff' in fl and 'email' in fl:
                t(fname, case['plaintiff_email'])
            # ── Defendant ──
            elif 'defendantname' in fl or ('defendant' in fl and 'name' in fl):
                t(fname, case['defendant_name'])
            elif 'defendant' in fl and 'street' in fl:
                t(fname, case['defendant_street'])
            elif 'defendant' in fl and 'city' in fl:
                t(fname, case['defendant_city'])
            elif 'defendant' in fl and 'state' in fl:
                t(fname, case['defendant_state'])
            elif 'defendant' in fl and 'zip' in fl:
                t(fname, case['defendant_zip'])
            elif 'defendant' in fl and 'phone' in fl:
                t(fname, case['defendant_phone'])
            # ── Claim ──
            elif 'claimamount' in fl or ('claim' in fl and 'amount' in fl):
                t(fname, case['claim_amount'])
            elif 'claimdescription' in fl or ('claim' in fl and 'desc' in fl):
                t(fname, case['claim_description'])
            elif 'datefrom' in fl or ('date' in fl and 'from' in fl):
                t(fname, case['date_from'])
            elif 'dateto' in fl or ('date' in fl and 'to' in fl):
                t(fname, case['date_to'])
            # ── Venue ──
            elif 'venuezip' in fl or ('venue' in fl and 'zip' in fl):
                t(fname, case['venue_zip'])
            # ── Declaration ──
            elif 'declarationdate' in fl or ('declaration' in fl and 'date' in fl):
                t(fname, case['declaration_date'])
            elif 'declarantname' in fl or ('declarant' in fl and 'name' in fl):
                t(fname, case['plaintiff_name'])

    # Step 3: open and fill
    doc = fitz.open(input_path)

    for page in doc:
        for widget in page.widgets():
            fname = widget.field_name
            if fname in text_map:
                set_field(widget, text_map[fname])
            elif fname in checkbox_map:
                set_field(widget, checkbox_map[fname])

    doc.save(output_path, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    print(f"Saved filled form to {output_path}")


def verify_output(output_path: str, case: dict):
    """Quick sanity check that key values appear in the filled PDF."""
    doc = fitz.open(output_path)
    all_values = []
    for page in doc:
        for widget in page.widgets():
            all_values.append(str(widget.field_value or ''))
    doc.close()

    combined = ' '.join(all_values).lower()
    checks = {
        'plaintiff_name':  case['plaintiff_name'].lower(),
        'defendant_name':  case['defendant_name'].lower(),
        'plaintiff_zip':   case['plaintiff_zip'],
        'claim_amount':    case['claim_amount'].replace('.00', ''),
    }
    all_ok = True
    for label, expected in checks.items():
        ok = expected in combined
        print(f"  {'✓' if ok else '✗'} {label}: {expected!r}")
        if not ok:
            all_ok = False
    return all_ok


if __name__ == '__main__':
    # Optional: print all fields for debugging
    if '--inspect' in sys.argv:
        fields = inspect_fields(INPUT_PDF)
        for name, meta in sorted(fields.items()):
            print(f"  [{meta['type']:12s}] p{meta['page']+1} {name!r}")
        sys.exit(0)

    fill_form(CASE, INPUT_PDF, OUTPUT_PDF)
    print("\nVerification:")
    ok = verify_output(OUTPUT_PDF, CASE)
    sys.exit(0 if ok else 1)
```

---

## Common Pitfalls

### 1. Field names differ between form versions
The SC-100 form is periodically revised by the Judicial Council. Field names
like `SC-100[0].Page1[0].Plaintiff[0].PlaintiffName[0]` are version-specific.
Always run `inspect_fields()` on the actual blank PDF before hardcoding names.

### 2. Checkbox pairs (Yes/No) — only set the correct one
Checkboxes come in pairs. Setting `Checkbox_Yes[0]` to `True` does NOT
automatically uncheck `Checkbox_No[0]`. If the form pre-fills a default, you
may need to explicitly set the unwanted checkbox to `False`.

```python
# Correct pattern for Yes/No pairs:
widget.field_value = True   # for the one you want checked
widget.update()
# Also explicitly uncheck the other if needed:
# other_widget.field_value = False
# other_widget.update()
```

### 3. `widget.update()` must be called after every change
Forgetting `widget.update()` means the change is not staged. The value will
appear to be set in memory but will not persist to the saved file.

### 4. Date format must be `xxxx-xx-xx`
The task specifies ISO-style dates (`2025-09-30`), not `09/30/2025` or
`September 30, 2025`. Always use `YYYY-MM-DD` unless the form field explicitly
requires another format.

### 5. Do not fill court-filled or optional fields
Fields like case number, court name, and judge assignment are filled by the
court clerk. Leave them blank. Only fill fields that correspond to information
explicitly provided in the case description.

### 6. Incremental save can corrupt if used on a fresh fill
Use `incremental=True` only when updating an already-saved PDF. For the initial
fill from a blank, use a plain `doc.save(output_path)`. Mixing these can cause
MuPDF structure errors (though usually non-fatal).

### 7. `fillpdf` / `pdfrw` may silently drop checkbox values
These libraries have inconsistent checkbox support. If you must use them as
fallback, verify checkbox state after saving. PyMuPDF (`fitz`) is the most
reliable for AcroForm checkboxes.

### 8. Email and phone fields may be on a different page than the address
On SC-100, plaintiff email is sometimes on page 1 in a separate sub-section
from the address block. Always check page assignments in the field inspection
output rather than assuming all plaintiff fields are co-located.

---

## Field Discovery Cheat Sheet

Run this one-liner to get a quick field inventory:

```bash
python3 -c "
import fitz
doc = fitz.open('/root/sc100-blank.pdf')
for pn, page in enumerate(doc):
    for w in page.widgets():
        print(f'p{pn+1} [{w.field_type_string:10}] {w.field_name}')
"
```

Filter to just checkboxes:
```bash
python3 -c "
import fitz
doc = fitz.open('/root/sc100-blank.pdf')
for pn, page in enumerate(doc):
    for w in page.widgets():
        if w.field_type_string == 'CheckBox':
            print(f'p{pn+1} {w.field_name}')
"
```

---

## Decision Tree: Which Library to Use

```
Is fitz (pymupdf) importable?
  YES → Use fitz. It handles text fields AND checkboxes reliably.
  NO  → Is fillpdf importable?
          YES → Use fillpdf for text fields only; verify checkboxes manually.
          NO  → Use pdfrw for text fields; checkboxes require manual overlay.
```

Always prefer `fitz`. It is installed in the standard environment for this task
category.
