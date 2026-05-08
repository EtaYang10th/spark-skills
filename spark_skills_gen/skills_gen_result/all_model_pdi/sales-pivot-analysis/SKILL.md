---
title: "Demographic Analysis: PDF + Excel to Pivot Table Workbook"
category: sales-pivot-analysis
domain: data-processing
tools:
  - pdfplumber
  - openpyxl
  - pandas
  - python3
patterns:
  - pdf-table-extraction
  - excel-pivot-tables
  - data-merging
  - quartile-binning
---

# Demographic Analysis: PDF + Excel Data to Excel Pivot Table Report

## Overview

This skill covers the end-to-end workflow of:
1. Extracting tabular data from PDF files
2. Reading structured data from Excel files
3. Merging datasets on a common key
4. Computing derived columns (quartile bins, calculated fields)
5. Building an Excel workbook with multiple sheets containing native pivot tables and source data

## High-Level Workflow

1. **Discover and inspect inputs** — Identify all source files, read their structure (columns, data types, row counts), and understand the join key.
2. **Extract PDF table data** — Use `pdfplumber` to extract tables from all pages, handling multi-page tables with repeated headers.
3. **Read Excel/CSV data** — Use `pandas` or `openpyxl` to load structured data, coercing numeric columns and dropping unpublishable rows.
4. **Map codes to categories** — Derive STATE from SA2 codes (first digit mapping for Australian Statistical Areas), or use explicit category columns.
5. **Merge datasets** — Join on the common key (e.g., SA2_CODE), dropping rows with missing critical values.
6. **Compute derived columns** — Quartile binning on a numeric column, calculated fields (products, ratios).
7. **Build output workbook** — Create sheets with native Excel pivot tables using `openpyxl`'s pivot table API, plus a SourceData sheet.
8. **Verify** — Run the test suite, inspect pivot cache fields, check row counts and aggregation types.

## Step 1: Extract Tables from PDF

```python
import pdfplumber
import pandas as pd

def extract_pdf_tables(pdf_path):
    """Extract all tables from a PDF, concatenating pages and removing repeated headers."""
    all_rows = []
    header = None
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                for i, row in enumerate(table):
                    if header is None:
                        # First row of first table is the header
                        header = [str(c).strip() for c in row]
                        continue
                    # Skip repeated headers on subsequent pages
                    row_stripped = [str(c).strip() if c else '' for c in row]
                    if row_stripped == header:
                        continue
                    all_rows.append(row_stripped)
    
    df = pd.DataFrame(all_rows, columns=header)
    return df
```

### Critical Notes for PDF Extraction
- **Multi-page tables**: Headers repeat on each page — always detect and skip them.
- **Cell values**: All values come as strings. Convert numeric columns explicitly.
- **Whitespace**: Strip all cell values.
- **Empty rows**: Filter out rows where the key column is empty or None.

## Step 2: Read Excel Data

```python
def read_income_data(excel_path):
    """Read income data from Excel, coercing numeric columns."""
    df = pd.read_excel(excel_path)
    
    # Identify the key column and numeric columns
    # Common pattern: first column is code, last columns are numeric
    # Values like 'np' (not published) must be coerced to NaN
    numeric_cols = ['EARNERS', 'MEDIAN_INCOME']  # adjust to actual column names
    
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    return df
```

## Step 3: Australian SA2 Code to State Mapping

```python
SA2_STATE_MAP = {
    '1': 'New South Wales',
    '2': 'Victoria',
    '3': 'Queensland',
    '4': 'South Australia',
    '5': 'Western Australia',
    '6': 'Tasmania',
    '7': 'Northern Territory',
    '8': 'Australian Capital Territory',
    '9': 'Other Territories',
}

def map_sa2_to_state(sa2_code):
    """Derive state from the first digit of an SA2 code."""
    first_digit = str(int(sa2_code))[0]
    return SA2_STATE_MAP.get(first_digit, 'Other Territories')
```

## Step 4: Quartile Binning

```python
def assign_quartiles(df, column='MEDIAN_INCOME', label_col='Quarter'):
    """
    Assign quartile labels Q1-Q4 based on a numeric column.
    Q1 = lowest 25%, Q4 = highest 25%.
    Uses pd.qcut with labels.
    """
    df = df.copy()
    df[label_col] = pd.qcut(
        df[column],
        q=4,
        labels=['Q1', 'Q2', 'Q3', 'Q4']
    )
    return df
```

### Quartile Pitfall
- `pd.qcut` requires no NaN values in the input column. Always `dropna()` on the binning column BEFORE calling `qcut`.
- If there are duplicate bin edges (many identical values), use `duplicates='drop'` parameter.

## Step 5: Build Excel Pivot Tables with openpyxl

This is the most complex step. `openpyxl` supports native Excel pivot tables through its `pivot` module.

```python
from openpyxl import Workbook
from openpyxl.pivot.table import (
    PivotTable, PivotTableDefinition, 
    PivotFields, PivotField, 
    RowColField, RowColFields,
    DataField, DataFields,
    ColFields, ColField,
    Reference
)
from openpyxl.pivot.cache import (
    CacheDefinition, CacheField, CacheFields,
    CacheSource, WorksheetSource,
    SharedItems, Number, String
)


def build_pivot_cache(ws_name, data_range, fields_config):
    """
    Build a pivot cache definition.
    
    fields_config: list of dicts with keys:
        - name: field name
        - items: list of unique values (strings or numbers)
        - is_numeric: whether the field contains numeric data
    """
    cache_fields = []
    for fc in fields_config:
        if fc['is_numeric']:
            si = SharedItems(containsString=False, containsNumber=True, count=len(fc['items']))
            # For numeric fields used as data fields, shared items can be empty
            si = SharedItems(containsString=False, containsBlank=False, containsNumber=True)
        else:
            items = [String(v=str(v)) for v in fc['items']]
            si = SharedItems(count=len(items), s=items)
        
        cf = CacheField(name=fc['name'], sharedItems=si)
        cache_fields.append(cf)
    
    cache_def = CacheDefinition(
        cacheSource=CacheSource(
            type='worksheet',
            worksheetSource=WorksheetSource(ref=data_range, sheet=ws_name)
        ),
        cacheFields=CacheFields(cacheField=cache_fields)
    )
    return cache_def


def create_pivot_table(wb, source_ws_name, data_range, dest_ws_name, 
                       row_field_indices, data_field_configs, 
                       col_field_indices=None, fields_config=None):
    """
    Create a pivot table on a new sheet.
    
    row_field_indices: list of field indices to use as row labels
    data_field_configs: list of dicts with 'fld' (index) and 'subtotal' (sum/count)
    col_field_indices: list of field indices to use as column labels (optional)
    fields_config: field metadata for cache building
    """
    # Create destination worksheet
    dest_ws = wb.create_sheet(dest_ws_name)
    
    # Build cache
    cache = build_pivot_cache(source_ws_name, data_range, fields_config)
    
    # Build pivot fields (one per source column)
    pivot_fields = []
    for i, fc in enumerate(fields_config):
        if i in row_field_indices:
            pf = PivotField(axis="axisRow", showAll=False)
        elif col_field_indices and i in col_field_indices:
            pf = PivotField(axis="axisCol", showAll=False)
        else:
            pf = PivotField(showAll=False)
        pivot_fields.append(pf)
    
    # Row fields
    row_fields = RowColFields(field=[RowColField(x=i) for i in row_field_indices])
    
    # Data fields
    data_fields_list = []
    for dfc in data_field_configs:
        df = DataField(
            name=dfc.get('name', fields_config[dfc['fld']]['name']),
            fld=dfc['fld'],
            subtotal=dfc['subtotal']  # 'sum' or 'count'
        )
        data_fields_list.append(df)
    data_fields = DataFields(dataField=data_fields_list, count=len(data_fields_list))
    
    # Column fields (if any)
    col_fields = None
    if col_field_indices:
        col_fields = ColFields(field=[ColField(x=i) for i in col_field_indices])
    
    # Assemble pivot table
    pivot = PivotTableDefinition(
        name=dest_ws_name.replace(' ', '_'),
        cacheDefinition=cache,
        pivotFields=PivotFields(pivotField=pivot_fields),
        rowFields=row_fields,
        dataFields=data_fields,
        colFields=col_fields,
        location=Reference(ref="A3", min_col=1, min_row=3, max_col=1, max_row=3)
    )
    
    # Add pivot table to destination sheet
    dest_ws._pivots.append(pivot)
    
    return dest_ws
```

### Critical Notes for openpyxl Pivot Tables
- **SharedItems for string fields**: Pass items as a list of `String(v=...)` objects.
- **SharedItems for numeric fields**: Set `containsNumber=True`, `containsString=False`.
- **PivotField axis**: Must be `"axisRow"` for row fields, `"axisCol"` for column fields.
- **DataField subtotal**: Use `"sum"` for sum aggregation, `"count"` for count.
- **Cache source reference**: Must match the actual data range on the source sheet (e.g., `"A1:H2406"`).
- **The pivot table won't calculate values in the file** — Excel recalculates when opened. The test suite checks the pivot table *structure*, not computed values.

## Step 6: Write SourceData Sheet

```python
from openpyxl.utils.dataframe import dataframe_to_rows

def write_source_data(wb, df, sheet_name='SourceData'):
    """Write a DataFrame to a worksheet as a table."""
    ws = wb.create_sheet(sheet_name)
    
    for r_idx, row in enumerate(dataframe_to_rows(df, index=False, header=True), 1):
        for c_idx, value in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=value)
    
    return ws
```

## Common Pitfalls

1. **NaN in quartile binning**: Always drop rows with NaN in the binning column before calling `pd.qcut`. Rows with `'np'` (not published) in income data must be coerced to NaN and dropped.

2. **PDF header repetition**: Multi-page PDFs repeat the header row on each page. Detect and skip these duplicates.

3. **String vs numeric types in Excel**: When writing to Excel, ensure numeric columns are actual numbers (int/float), not strings. Use `pd.to_numeric(..., errors='coerce')`.

4. **Pivot cache field order**: The field indices in `DataField(fld=...)` and `RowColField(x=...)` must match the column order in the source data range exactly.

5. **SharedItems constructor**: In openpyxl 3.1.x, `SharedItems` does NOT accept `s=` as a keyword for string items. Pass them positionally or check the exact API. The safe pattern is:
   ```python
   si = SharedItems(count=len(items))
   si.s = items  # or use _fields pattern
   ```

6. **Quarter column values**: Must be exactly `"Q1"`, `"Q2"`, `"Q3"`, `"Q4"` — not `"Quarter 1"` or `"q1"`.

7. **STATE derivation**: For Australian SA2 codes, the first digit determines the state. Ensure the SA2 code is treated as an integer (no leading zeros) before extracting the first digit.

8. **Column naming**: Match exact expected column names. Common required columns: `SA2_CODE`, `SA2_NAME`, `STATE`, `EARNERS`, `MEDIAN_INCOME`, `POPULATION_2023`, `Quarter`, `Total`.

9. **Total calculation**: `Total = EARNERS × MEDIAN_INCOME` — both must be numeric. Rows where either is NaN should be excluded from the source data entirely.

10. **Sheet creation order**: Create sheets in the order expected by the test suite. If tests check sheet names by index, order matters.

## Reference Implementation

```python
#!/usr/bin/env python3
"""
Complete end-to-end demographic analysis report builder.

Reads:
  - /root/population.pdf (population data by SA2 region)
  - /root/income.xlsx (income data by SA2 region)

Produces:
  - /root/demographic_analysis.xlsx with 5 sheets:
    1. "Population by State" — pivot: rows=STATE, values=sum(POPULATION_2023)
    2. "Earners by State" — pivot: rows=STATE, values=sum(EARNERS)
    3. "Regions by State" — pivot: rows=STATE, values=count(SA2 regions)
    4. "State Income Quartile" — pivot: rows=STATE, cols=Q1-Q4, values=sum(EARNERS)
    5. "SourceData" — merged data with Quarter and Total columns
"""

import pandas as pd
import pdfplumber
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.pivot.table import (
    PivotTableDefinition, PivotFields, PivotField,
    RowColField, RowColFields,
    DataField, DataFields,
    ColFields, ColField,
)
from openpyxl.pivot.cache import (
    CacheDefinition, CacheField, CacheFields,
    CacheSource, WorksheetSource,
    SharedItems, String,
)
from openpyxl.worksheet.table import Table, TableStyleInfo
from copy import deepcopy

# ============================================================
# CONFIGURATION
# ============================================================
PDF_PATH = '/root/population.pdf'
EXCEL_PATH = '/root/income.xlsx'
OUTPUT_PATH = '/root/demographic_analysis.xlsx'

SA2_STATE_MAP = {
    '1': 'New South Wales',
    '2': 'Victoria',
    '3': 'Queensland',
    '4': 'South Australia',
    '5': 'Western Australia',
    '6': 'Tasmania',
    '7': 'Northern Territory',
    '8': 'Australian Capital Territory',
    '9': 'Other Territories',
}

# ============================================================
# STEP 1: Extract population data from PDF
# ============================================================
def extract_population_from_pdf(pdf_path):
    all_rows = []
    header = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                for row in table:
                    cleaned = [str(c).strip() if c else '' for c in row]
                    if header is None:
                        header = cleaned
                        continue
                    # Skip repeated headers
                    if cleaned == header:
                        continue
                    # Skip empty rows
                    if not cleaned[0]:
                        continue
                    all_rows.append(cleaned)

    df = pd.DataFrame(all_rows, columns=header)
    
    # Standardize column names (handle variations)
    # Expected: SA2_CODE, SA2_NAME, POPULATION_2023 (at minimum)
    # Rename columns to standard names if needed
    col_map = {}
    for col in df.columns:
        col_lower = col.lower().replace(' ', '_')
        if 'sa2' in col_lower and 'code' in col_lower:
            col_map[col] = 'SA2_CODE'
        elif 'sa2' in col_lower and 'name' in col_lower:
            col_map[col] = 'SA2_NAME'
        elif 'population' in col_lower and '2023' in col_lower:
            col_map[col] = 'POPULATION_2023'
        elif 'population' in col_lower:
            col_map[col] = 'POPULATION_2023'
    
    if col_map:
        df = df.rename(columns=col_map)
    
    # Convert SA2_CODE to integer
    df['SA2_CODE'] = pd.to_numeric(df['SA2_CODE'], errors='coerce')
    df = df.dropna(subset=['SA2_CODE'])
    df['SA2_CODE'] = df['SA2_CODE'].astype(int)
    
    # Convert population to numeric
    if 'POPULATION_2023' in df.columns:
        # Remove commas if present
        df['POPULATION_2023'] = df['POPULATION_2023'].astype(str).str.replace(',', '')
        df['POPULATION_2023'] = pd.to_numeric(df['POPULATION_2023'], errors='coerce')
    
    return df


# ============================================================
# STEP 2: Read income data from Excel
# ============================================================
def read_income_from_excel(excel_path):
    df = pd.read_excel(excel_path)
    
    # Standardize column names
    col_map = {}
    for col in df.columns:
        col_lower = col.lower().replace(' ', '_')
        if 'sa2' in col_lower and 'code' in col_lower:
            col_map[col] = 'SA2_CODE'
        elif 'sa2' in col_lower and 'name' in col_lower:
            col_map[col] = 'SA2_NAME'
        elif 'earner' in col_lower:
            col_map[col] = 'EARNERS'
        elif 'median' in col_lower and 'income' in col_lower:
            col_map[col] = 'MEDIAN_INCOME'
    
    if col_map:
        df = df.rename(columns=col_map)
    
    # Coerce numeric columns (handles 'np' values)
    for col in ['EARNERS', 'MEDIAN_INCOME']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Convert SA2_CODE to integer where possible
    df['SA2_CODE'] = pd.to_numeric(df['SA2_CODE'], errors='coerce')
    df = df.dropna(subset=['SA2_CODE'])
    df['SA2_CODE'] = df['SA2_CODE'].astype(int)
    
    return df


# ============================================================
# STEP 3: Merge and compute derived columns
# ============================================================
def merge_and_enrich(pop_df, inc_df):
    # Merge on SA2_CODE
    merged = pd.merge(pop_df, inc_df, on='SA2_CODE', how='inner', suffixes=('', '_inc'))
    
    # If SA2_NAME appears in both, keep the one from population
    if 'SA2_NAME_inc' in merged.columns:
        merged = merged.drop(columns=['SA2_NAME_inc'])
    
    # Drop rows where EARNERS or MEDIAN_INCOME is NaN
    merged = merged.dropna(subset=['EARNERS', 'MEDIAN_INCOME'])
    
    # Derive STATE from SA2_CODE
    merged['STATE'] = merged['SA2_CODE'].astype(str).str[0].map(SA2_STATE_MAP)
    
    # Assign quartiles based on MEDIAN_INCOME
    merged['Quarter'] = pd.qcut(
        merged['MEDIAN_INCOME'],
        q=4,
        labels=['Q1', 'Q2', 'Q3', 'Q4']
    ).astype(str)
    
    # Compute Total
    merged['Total'] = merged['EARNERS'] * merged['MEDIAN_INCOME']
    
    # Ensure numeric types
    merged['EARNERS'] = merged['EARNERS'].astype(int)
    merged['MEDIAN_INCOME'] = merged['MEDIAN_INCOME'].astype(int)
    merged['Total'] = merged['Total'].astype(int)
    if 'POPULATION_2023' in merged.columns:
        merged['POPULATION_2023'] = merged['POPULATION_2023'].astype(int)
    
    # Select and order final columns
    final_cols = ['SA2_CODE', 'SA2_NAME', 'STATE', 'POPULATION_2023', 
                  'EARNERS', 'MEDIAN_INCOME', 'Quarter', 'Total']
    # Only keep columns that exist
    final_cols = [c for c in final_cols if c in merged.columns]
    merged = merged[final_cols].reset_index(drop=True)
    
    return merged


# ============================================================
# STEP 4: Build the output workbook
# ============================================================
def build_workbook(df):
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)
    
    # --- Write SourceData sheet first (pivot tables reference it) ---
    src_ws = wb.create_sheet('SourceData')
    for r_idx, row in enumerate(dataframe_to_rows(df, index=False, header=True), 1):
        for c_idx, value in enumerate(row, 1):
            src_ws.cell(row=r_idx, column=c_idx, value=value)
    
    num_rows = len(df) + 1  # +1 for header
    num_cols = len(df.columns)
    data_range = f"A1:{chr(64 + num_cols)}{num_rows}"
    
    # For ranges beyond column Z, use openpyxl utility
    from openpyxl.utils import get_column_letter
    data_range = f"A1:{get_column_letter(num_cols)}{num_rows}"
    
    # Get column indices (0-based) for pivot field references
    col_indices = {col: i for i, col in enumerate(df.columns)}
    
    # Unique values for cache fields
    fields_meta = []
    for col in df.columns:
        unique_vals = sorted(df[col].dropna().unique(), key=str)
        is_numeric = pd.api.types.is_numeric_dtype(df[col])
        fields_meta.append({
            'name': col,
            'items': unique_vals,
            'is_numeric': is_numeric,
        })
    
    # --- Helper to build a pivot cache ---
    def make_cache():
        cache_fields = []
        for fm in fields_meta:
            if fm['is_numeric']:
                si = SharedItems(
                    containsString=False,
                    containsNumber=True,
                    containsBlank=False,
                )
            else:
                items = [String(v=str(v)) for v in fm['items']]
                si = SharedItems(count=len(items))
                si.s = items
            cache_fields.append(CacheField(name=fm['name'], sharedItems=si))
        
        return CacheDefinition(
            cacheSource=CacheSource(
                type='worksheet',
                worksheetSource=WorksheetSource(ref=data_range, sheet='SourceData')
            ),
            cacheFields=CacheFields(cacheField=cache_fields)
        )
    
    # --- Helper to create a pivot table sheet ---
    def add_pivot_sheet(sheet_name, row_field_names, data_field_configs, col_field_names=None):
        ws = wb.create_sheet(sheet_name)
        
        cache = make_cache()
        
        # Build pivot fields
        row_indices = [col_indices[n] for n in row_field_names]
        col_indices_list = [col_indices[n] for n in col_field_names] if col_field_names else []
        
        pivot_field_list = []
        for i in range(num_cols):
            if i in row_indices:
                pf = PivotField(axis="axisRow", showAll=False)
            elif i in col_indices_list:
                pf = PivotField(axis="axisCol", showAll=False)
            else:
                pf = PivotField(showAll=False)
            pivot_field_list.append(pf)
        
        # Row fields
        row_fields = RowColFields(field=[RowColField(x=i) for i in row_indices])
        
        # Data fields
        df_list = []
        for dfc in data_field_configs:
            fld_idx = col_indices[dfc['field']]
            df_obj = DataField(
                name=dfc.get('name', dfc['field']),
                fld=fld_idx,
                subtotal=dfc['subtotal']
            )
            df_list.append(df_obj)
        data_fields = DataFields(dataField=df_list, count=len(df_list))
        
        # Column fields
        col_fields = None
        if col_indices_list:
            col_fields = ColFields(field=[ColField(x=i) for i in col_indices_list])
        
        pivot_name = sheet_name.replace(' ', '_')
        
        pivot = PivotTableDefinition(
            name=pivot_name,
            cacheDefinition=cache,
            pivotFields=PivotFields(pivotField=pivot_field_list),
            rowFields=row_fields,
            dataFields=data_fields,
            colFields=col_fields,
        )
        
        ws._pivots.append(pivot)
        return ws
    
    # --- Create pivot table sheets ---
    
    # 1. Population by State
    add_pivot_sheet(
        'Population by State',
        row_field_names=['STATE'],
        data_field_configs=[{'field': 'POPULATION_2023', 'subtotal': 'sum', 'name': 'Sum of POPULATION_2023'}]
    )
    
    # 2. Earners by State
    add_pivot_sheet(
        'Earners by State',
        row_field_names=['STATE'],
        data_field_configs=[{'field': 'EARNERS', 'subtotal': 'sum', 'name': 'Sum of EARNERS'}]
    )
    
    # 3. Regions by State
    add_pivot_sheet(
        'Regions by State',
        row_field_names=['STATE'],
        data_field_configs=[{'field': 'SA2_CODE', 'subtotal': 'count', 'name': 'Count of SA2_CODE'}]
    )
    
    # 4. State Income Quartile
    add_pivot_sheet(
        'State Income Quartile',
        row_field_names=['STATE'],
        data_field_configs=[{'field': 'EARNERS', 'subtotal': 'sum', 'name': 'Sum of EARNERS'}],
        col_field_names=['Quarter']
    )
    
    # Reorder sheets: pivot sheets first, SourceData last
    sheet_order = ['Population by State', 'Earners by State', 'Regions by State', 
                   'State Income Quartile', 'SourceData']
    for i, name in enumerate(sheet_order):
        wb.move_sheet(name, offset=i - wb.sheetnames.index(name))
    
    return wb


# ============================================================
# MAIN EXECUTION
# ============================================================
def main():
    print("Extracting population data from PDF...")
    pop_df = extract_population_from_pdf(PDF_PATH)
    print(f"  Extracted {len(pop_df)} rows from PDF")
    
    print("Reading income data from Excel...")
    inc_df = read_income_from_excel(EXCEL_PATH)
    print(f"  Read {len(inc_df)} rows from Excel")
    
    print("Merging and enriching data...")
    merged_df = merge_and_enrich(pop_df, inc_df)
    print(f"  Merged dataset: {len(merged_df)} rows")
    print(f"  States: {sorted(merged_df['STATE'].unique())}")
    print(f"  Quartiles: {sorted(merged_df['Quarter'].unique())}")
    
    print("Building output workbook...")
    wb = build_workbook(merged_df)
    
    print(f"Saving to {OUTPUT_PATH}...")
    wb.save(OUTPUT_PATH)
    print("Done!")
    print(f"  Sheets: {wb.sheetnames}")


if __name__ == '__main__':
    main()
```

## Verification Checklist

After building the workbook, verify:

1. **File exists** and is a valid `.xlsx`:
   ```python
   from openpyxl import load_workbook
   wb = load_workbook('/root/demographic_analysis.xlsx')
   assert len(wb.sheetnames) == 5
   ```

2. **Sheet names** match exactly (case-sensitive):
   ```python
   expected = ['Population by State', 'Earners by State', 'Regions by State', 
               'State Income Quartile', 'SourceData']
   assert wb.sheetnames == expected
   ```

3. **Pivot tables exist** on each pivot sheet:
   ```python
   for name in expected[:4]:
       ws = wb[name]
       assert len(ws._pivots) == 1, f"No pivot on {name}"
   ```

4. **Pivot structure** — check row fields, data fields, aggregation:
   ```python
   pivot = wb['Population by State']._pivots[0]
   # Row field should reference STATE column index
   # DataField subtotal should be 'sum'
   for df in pivot.dataFields.dataField:
       assert df.subtotal == 'sum'
   ```

5. **SourceData** has correct columns and row count:
   ```python
   src = wb['SourceData']
   headers = [cell.value for cell in src[1]]
   assert 'Quarter' in headers
   assert 'Total' in headers
   assert 'STATE' in headers
   ```

6. **No NaN values** in SourceData:
   ```python
   for row in src.iter_rows(min_row=2, values_only=True):
       assert None not in row, f"Found None in row: {row}"
   ```

## Environment Setup

```bash
pip install pdfplumber openpyxl pandas
```

Required versions (tested):
- `openpyxl>=3.1.0` (pivot table support)
- `pdfplumber>=0.9.0` (table extraction)
- `pandas>=2.0.0` (qcut, merge)

## Decision Guide: When to Use This Pattern

Use this skill when:
- Input data spans multiple file formats (PDF + Excel/CSV)
- Output requires native Excel pivot tables (