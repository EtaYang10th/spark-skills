---
id: sales-pivot-analysis
title: Excel Pivot Table Report Generation from Mixed Sources
version: 1.0.0
tags: [excel, pivot-tables, openpyxl, pdfplumber, pandas, data-analysis]
description: >
  Procedural guide for building Excel workbooks with real pivot tables by combining
  data from PDF and XLSX sources, enriching it, and injecting proper pivot XML.
---

# Excel Pivot Table Report Generation

## Module 1: Data Extraction and Preparation

### 1.1 Extract from PDF with pdfplumber

```python
import pdfplumber
import pandas as pd

rows = []
with pdfplumber.open('source.pdf') as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            for row in table[1:]:  # skip header
                rows.append(row)

pdf_df = pd.DataFrame(rows, columns=['COL_A', 'COL_B', ...])
```

Always use the income/XLSX file as the authoritative row source. The PDF may be
incomplete — cross-reference SA2 codes (or equivalent IDs) from the XLSX to ensure
no rows are dropped.

### 1.2 Merge and Enrich

```python
income_df = pd.read_excel('income.xlsx')

# Merge on shared key (e.g. region code)
df = income_df.merge(pdf_df, on='REGION_CODE', how='left')

# Compute quartile column based on a numeric field
df['Quarter'] = pd.qcut(df['MEDIAN_INCOME'], q=4, labels=['Q1','Q2','Q3','Q4'])

# Compute derived numeric column
df['Total'] = df['EARNERS'] * df['MEDIAN_INCOME']
```

Ensure numeric columns are cast properly before arithmetic:

```python
for col in ['POPULATION', 'EARNERS', 'MEDIAN_INCOME']:
    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
```

---

## Module 2: Building Real Excel Pivot Tables via XML Injection

openpyxl does not support writing pivot tables natively. The only reliable approach
is to construct the xlsx zip manually with pivot cache and pivot table XML files.

### 2.1 Write the SourceData Sheet First

```python
import openpyxl
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.worksheet.table import Table, TableStyleInfo

wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'SourceData'

for r in dataframe_to_rows(df, index=False, header=True):
    ws.append(r)

# Define as a named table so pivot cache can reference it
tbl = Table(displayName='SourceData',
            ref=f'A1:{get_column_letter(len(df.columns))}{len(df)+1}')
tbl.tableStyleInfo = TableStyleInfo(name='TableStyleMedium9')
ws.add_table(tbl)

wb.save('output.xlsx')
```

### 2.2 Inject Pivot XML into the xlsx Zip

An xlsx file is a zip archive. After saving the workbook, reopen it as a zip and
inject the pivot cache and pivot table XML files.

```python
import zipfile, shutil, os

src = 'output.xlsx'
tmp = 'output_pivot.xlsx'
shutil.copy(src, tmp)

# Build field index map from DataFrame columns
fields = list(df.columns)  # e.g. ['SA2_CODE','NAME','STATE','POPULATION_2023',...]
field_idx = {name: i for i, name in enumerate(fields)}

def pivot_cache_xml(fields, df, sheet_name, table_name):
    """Generate pivotCacheDefinition XML referencing the source table."""
    cache_fields = ''
    for col in fields:
        vals = df[col].dropna().unique()
        shared = ''.join(f'<s v="{v}"/>' if df[col].dtype == object
                         else f'<n v="{v}"/>' for v in vals[:500])
        cache_fields += f'<cacheField name="{col}" numFmtId="0"><sharedItems>{shared}</sharedItems></cacheField>\n'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<pivotCacheDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  refreshedBy="Python" refreshedDate="0" createdVersion="3" refreshedVersion="3"
  recordCount="{len(df)}" upgradeOnRefresh="1">
  <cacheSource type="worksheet">
    <worksheetSource ref="A1:{get_column_letter(len(fields))}{len(df)+1}" sheet="{sheet_name}"/>
  </cacheSource>
  <cacheFields count="{len(fields)}">
{cache_fields}  </cacheFields>
</pivotCacheDefinition>'''

def pivot_table_xml(cache_id, row_field_idx, data_field_idx,
                    data_func='sum', col_field_idx=None):
    col_block = ''
    if col_field_idx is not None:
        col_block = f'<colFields count="1"><field x="{col_field_idx}"/></colFields>'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<pivotTableDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  name="PivotTable{cache_id}" cacheId="{cache_id}" dataOnRows="1"
  applyNumberFormats="0" applyBorderFormats="0" applyFontFormats="0"
  applyPatternFormats="0" applyAlignmentFormats="0" applyWidthHeightFormats="1"
  dataCaption="Values" updatedVersion="3" minRefreshableVersion="3"
  useAutoFormatting="1" itemPrintTitles="1" createdVersion="3" indent="0"
  compact="0" compactData="0" gridDropZones="1">
  <location ref="A1" firstHeaderRow="1" firstDataRow="2" firstDataCol="1"/>
  <pivotFields count="..."/>
  <rowFields count="1"><field x="{row_field_idx}"/></rowFields>
  {col_block}
  <dataFields count="1">
    <dataField name="Values" fld="{data_field_idx}" subtotal="{data_func}"/>
  </dataFields>
</pivotTableDefinition>'''
```

### 2.3 Wire Up Relationships in [Content_Types].xml and .rels

For each pivot sheet, you need:
- `xl/pivotCache/pivotCacheDefinition{n}.xml`
- `xl/pivotCache/pivotCacheRecords{n}.xml` (can be empty `<pivotCacheRecords count="0"/>`)
- `xl/pivotTables/pivotTable{n}.xml`
- Relationship entries in `xl/workbook.xml.rels` and the sheet's `.rels` file
- Content type entries in `[Content_Types].xml`

Use `zipfile.ZipFile` in append mode to inject these files, then patch the existing
XML files for relationships and content types using string replacement.

---

## Module 3: Verification Before Finalizing

Before saving the final file, verify:

```python
wb2 = openpyxl.load_workbook('output_pivot.xlsx')
assert 'SourceData' in wb2.sheetnames
assert len(list(wb2['SourceData'].rows)) > 1  # has data rows

# Confirm all expected sheets exist
for sheet in ['Population by State', 'Earners by State',
              'Regions by State', 'State Income Quartile', 'SourceData']:
    assert sheet in wb2.sheetnames, f"Missing sheet: {sheet}"

# Confirm SourceData has enriched columns
headers = [c.value for c in next(wb2['SourceData'].iter_rows(max_row=1))]
assert 'Quarter' in headers
assert 'Total' in headers
```

Also verify the zip contains pivot XML files:

```python
with zipfile.ZipFile('output_pivot.xlsx') as z:
    names = z.namelist()
    assert any('pivotTable' in n for n in names), "No pivot table XML found"
    assert any('pivotCacheDefinition' in n for n in names), "No pivot cache XML found"
```

---

## Common Pitfalls

1. Using the PDF as the sole data source — always treat the XLSX as authoritative.
   Cross-join on region/SA2 codes to avoid missing rows.

2. Relying on openpyxl's built-in pivot support — it doesn't exist. You must inject
   raw XML into the zip archive.

3. Forgetting `pivotCacheRecords` XML — even an empty records file must be present
   or Excel will refuse to open the file.

4. Wrong field indices in pivot XML — compute indices dynamically from the actual
   DataFrame column order, never hardcode them.

5. Missing content type and relationship entries — every new XML part needs a
   corresponding entry in `[Content_Types].xml` and the relevant `.rels` file.

6. Quartile labels not matching expected values — use `pd.qcut` with explicit
   `labels=['Q1','Q2','Q3','Q4']` and verify the column dtype is string/object
   before writing to Excel.

7. Numeric columns stored as strings after PDF extraction — always cast with
   `pd.to_numeric(..., errors='coerce')` before aggregation or arithmetic.

8. STATE column empty or mismatched after merge — validate the merge key and check
   for whitespace/encoding differences between PDF-extracted text and XLSX values.
