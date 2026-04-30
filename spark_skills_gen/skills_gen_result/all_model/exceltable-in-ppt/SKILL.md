---
name: Update Embedded Excel Table in PowerPoint
version: 1.0.0
category: exceltable-in-ppt
tags: [pptx, excel, openpyxl, python-pptx, xml-manipulation]
description: >
  Procedural guide for reading and surgically updating an embedded Excel workbook
  inside a .pptx file — preserving formulas, cached values, and all untouched cells.
---

## Module 1: Extract and Inspect the Embedded Excel

### 1.1 Locate the embedded OLE/xlsx part

A pptx is a zip archive. Embedded Excel objects live under `ppt/embeddings/` as
`.xlsx` files (or occasionally `.xlsm` for macro-enabled workbooks).

```python
from pptx import Presentation
from pptx.util import Inches
import zipfile, io, re

prs = Presentation("input.pptx")

# Find the embedded xlsx part
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.shape_type == 3:  # MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT = 3
            ole = shape.ole_format
            print(shape.name, ole.prog_id)  # e.g. "Excel.Sheet.12"
```

### 1.2 Read the workbook with openpyxl (preserve formulas)

```python
import openpyxl, io

# Get raw bytes of the embedded xlsx
xlsx_bytes = shape.ole_format.blob          # python-pptx >= 1.0
# OR extract directly from the zip:
# with zipfile.ZipFile("input.pptx") as z:
#     xlsx_bytes = z.read("ppt/embeddings/Microsoft_Excel_Sheet1.xlsx")

wb = openpyxl.load_workbook(
    io.BytesIO(xlsx_bytes),
    data_only=False,   # CRITICAL: keep formula strings, not cached values
    keep_vba=True,
    keep_links=True,
)
ws = wb.active

# Print full cell map
for row in ws.iter_rows():
    for cell in row:
        print(cell.coordinate, repr(cell.value))
```

### 1.3 Read the text box for the updated rate

```python
import re

for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            text = shape.text_frame.text.strip()
            # e.g. "USD to CNY=7.02"
            m = re.search(r'(\w+)\s+to\s+(\w+)\s*=\s*([\d.]+)', text, re.I)
            if m:
                from_ccy, to_ccy, rate = m.group(1), m.group(2), float(m.group(3))
```

---

## Module 2: Surgical XML Update (Preferred Approach)

openpyxl can silently alter shared strings, styles, or cached formula values when
you save. Use direct XML string replacement to guarantee zero side-effects on
untouched cells.

### 2.1 Identify the target cell coordinate

Map the currency pair to its row/column in the worksheet header row/column before
touching anything. The first row and first column are typically currency labels.

```python
# Build coordinate map from headers
col_map = {}  # currency -> column letter
row_map = {}  # currency -> row number

for cell in ws[1]:          # header row
    if cell.value:
        col_map[cell.value.upper()] = cell.column_letter

for row in ws.iter_rows(min_col=1, max_col=1):
    cell = row[0]
    if cell.value:
        row_map[cell.value.upper()] = cell.row

target_coord = f"{col_map[to_ccy]}{row_map[from_ccy]}"  # e.g. "B3"
```

### 2.2 Patch the XML directly

```python
import zipfile, shutil, os, re

src = "input.pptx"
dst = "results.pptx"
shutil.copy2(src, dst)

EMBED_PATH = "ppt/embeddings/Microsoft_Excel_Sheet1.xlsx"  # adjust name as needed

# Read the inner xlsx bytes
with zipfile.ZipFile(dst, "r") as z:
    inner_xlsx = z.read(EMBED_PATH)

# Open inner xlsx as another zip
with zipfile.ZipFile(io.BytesIO(inner_xlsx), "r") as iz:
    sheet_xml = iz.read("xl/worksheets/sheet1.xml").decode("utf-8")
    all_files = {name: iz.read(name) for name in iz.namelist()}

# --- Patch the value cell (no formula) ---
# Replace <v>OLD</v> inside the target cell element
coord = target_coord   # e.g. "B3"
new_val = str(rate)

def patch_value_cell(xml, coord, new_val):
    # Match the cell element and replace only its <v> child
    pattern = rf'(<c r="{re.escape(coord)}"[^>]*>)(.*?)(</c>)'
    def replacer(m):
        inner = re.sub(r'<v>[^<]*</v>', f'<v>{new_val}</v>', m.group(2))
        return m.group(1) + inner + m.group(3)
    return re.sub(pattern, replacer, xml, flags=re.DOTALL)

sheet_xml = patch_value_cell(sheet_xml, coord, new_val)

# --- Update cached value of formula cells that reference the changed cell ---
# e.g. inverse rate cell: formula stays, only <v> is refreshed
def patch_formula_cached_value(xml, coord, new_cached):
    pattern = rf'(<c r="{re.escape(coord)}"[^>]*>)(.*?)(</c>)'
    def replacer(m):
        inner = re.sub(r'<v>[^<]*</v>', f'<v>{new_cached}</v>', m.group(2))
        return m.group(1) + inner + m.group(3)
    return re.sub(pattern, replacer, xml, flags=re.DOTALL)

# Compute inverse and update its cached value (formula string is untouched)
inverse_coord = f"{col_map[from_ccy]}{row_map[to_ccy]}"  # e.g. "C2"
inverse_cached = round(1 / rate, 3)
sheet_xml = patch_formula_cached_value(sheet_xml, inverse_coord, str(inverse_cached))

# --- Repack inner xlsx ---
new_inner = io.BytesIO()
with zipfile.ZipFile(new_inner, "w", zipfile.ZIP_DEFLATED) as oz:
    for name, data in all_files.items():
        if name == "xl/worksheets/sheet1.xml":
            oz.writestr(name, sheet_xml.encode("utf-8"))
        else:
            oz.writestr(name, data)

# --- Inject patched xlsx back into pptx ---
import tempfile
tmp = dst + ".tmp"
with zipfile.ZipFile(dst, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        if item.filename == EMBED_PATH:
            zout.writestr(item, new_inner.getvalue())
        else:
            zout.writestr(item, zin.read(item.filename))

os.replace(tmp, dst)
print("Done:", dst)
```

---

## Module 3: Verification Before Finalizing

```python
# Re-open results.pptx and confirm
with zipfile.ZipFile("results.pptx") as z:
    inner = z.read(EMBED_PATH)

wb2 = openpyxl.load_workbook(io.BytesIO(inner), data_only=False)
ws2 = wb2.active

# 1. Value cell updated
assert ws2[coord].value == rate, f"Expected {rate}, got {ws2[coord].value}"

# 2. Formula cell still has formula (not a hardcoded number)
inv_cell = ws2[inverse_coord]
assert isinstance(inv_cell.value, str) and inv_cell.value.startswith("="), \
    f"Formula was overwritten: {inv_cell.value}"

# 3. Slide structure intact
prs2 = Presentation("results.pptx")
assert len(prs2.slides) == len(prs.slides), "Slide count changed"

print("All checks passed")
```

---

## Common Pitfalls

- **`data_only=True` destroys formulas.** Always load with `data_only=False`.
  openpyxl with `data_only=True` reads cached values and writes back plain numbers,
  silently converting formula cells to constants.

- **openpyxl save() has side effects.** Even a round-trip `load → save` with no
  explicit changes can alter shared strings indexes, style IDs, or strip cached
  `<v>` elements from formula cells. Prefer direct XML patching for surgical edits.

- **Updating only the direct value cell is not enough.** Formula cells that
  reference the changed cell have a cached `<v>` element. Tests often check that
  the cached value is consistent with the new rate. Recompute and patch those too.

- **Finding the right embedding path.** The filename inside `ppt/embeddings/` can
  vary (`Microsoft_Excel_Sheet1.xlsx`, `Microsoft_Excel_Worksheet1.xlsx`, etc.).
  Always enumerate `ppt/embeddings/` first rather than hardcoding the path.

- **Regex must be DOTALL.** Cell XML spans multiple lines in some files. Use
  `re.DOTALL` (or `re.S`) when matching `<c ...>...</c>` blocks.

- **Keep the outer pptx structure intact.** When repacking, copy every entry from
  the original zip verbatim except the one embedding file. Do not re-compress
  already-stored entries with a different compression level — some validators are
  sensitive to this.
