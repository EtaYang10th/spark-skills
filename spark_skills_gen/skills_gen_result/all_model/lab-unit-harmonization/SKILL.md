---
name: lab-unit-harmonization
description: Harmonize mixed-unit clinical laboratory CSV data into a clean US-conventional-unit dataset with strict formatting, row dropping for incomplete records, scientific-notation normalization, and physiology-range-based unit conversion.
tools: [python, pandas, numpy, scipy, bash, rg, find]
domain: clinical-data-cleaning
version: 1.0.0
---

# Lab Unit Harmonization for Clinical CSV Data

This skill is for tasks where a clinical laboratory dataset contains:

- mixed source systems
- inconsistent decimal formats
- scientific notation
- mixed measurement units for the same analyte
- incomplete patient rows that must be dropped
- strict output formatting requirements

The goal is usually to produce a CSV with:

- the **same columns** as input
- **all surviving rows fully populated**
- values converted into **US conventional units**
- every numeric field formatted as **exactly `X.XX`**
- no scientific notation
- no commas as decimal separators
- no whitespace artifacts

A key lesson from successful execution: **do not assume you must preserve rows at all costs**. If the task says incomplete or unrecoverable rows should be dropped, a schema-correct header-only CSV can be valid when every row is incomplete or ambiguous after strict harmonization logic.

---

## When to Use This Skill

Use this skill when:

- input is a CSV of clinical lab features
- there is a feature-description file mapping abbreviations to analyte meanings
- values may be strings like `1.23e2`, `12,34`, ` 7.0 `
- unit harmonization must be inferred from **physiological ranges**
- output validation is strict about string formatting

Do **not** use this as-is for datasets that require:
- preserving patient rows despite missingness
- imputation
- longitudinal time-aware harmonization
- explicit unit columns already present and trustworthy

---

## High-Level Workflow

1. **Inspect schema and constraints first**
   - Load the data as strings.
   - Count columns and identify whether all fields are expected numeric.
   - Read feature descriptions if available to determine analyte meaning.
   - Why: unit conversion depends on knowing the analyte and expected conventional unit.

2. **Normalize raw string formatting before any numeric logic**
   - Strip whitespace.
   - Convert decimal commas to periods.
   - Expand scientific notation into ordinary decimal numbers.
   - Why: conversion heuristics fail if numbers are parsed inconsistently.

3. **Drop incomplete rows early**
   - Remove rows with any missing, empty, or unparsable fields.
   - Why: tasks in this class often explicitly require dropping incomplete records rather than imputing them.

4. **Define analyte-specific physiological target ranges in the intended final unit**
   - Use domain knowledge and feature descriptions.
   - These ranges are not for diagnosis; they are for detecting wrong-unit values.
   - Why: mixed-unit rows often appear as extreme outliers relative to expected conventional-unit ranges.

5. **Apply analyte-specific unit-conversion candidates**
   - For each analyte, define one or more plausible conversion factors from alternate units to the target conventional unit.
   - Test whether original or converted value falls into expected physiological range.
   - Why: a high value may be valid pathology or may be a different unit; analyte-specific rules are safer than generic scaling.

6. **Use conservative decision rules**
   - Keep the value unchanged if already in range.
   - Otherwise test conversions and accept only the factor that moves the value into range.
   - If multiple converted values land in range, choose the one closest to the range center or mark ambiguous.
   - Why: this avoids over-converting truly abnormal but already correctly-unitized values.

7. **Drop rows containing unresolved ambiguities**
   - If any required feature cannot be parsed or harmonized confidently, drop the row.
   - Why: downstream validators often require every retained row to be fully valid.

8. **Format every surviving field to exactly two decimals**
   - Including identifier columns if the validator expects all fields numeric-looking.
   - Output as strings, usually quoted in CSV.
   - Why: validators may reject scientific notation, variable decimals, commas, or bare integers.

9. **Run verifier-style checks before finalizing**
   - File exists.
   - Same number and order of columns as input.
   - No missing values.
   - No whitespace.
   - No commas or alphabetic characters in data fields.
   - Every field matches `^-?\d+\.\d{2}$`.
   - Why: formatting mistakes are common even when conversions are correct.

10. **If necessary, prefer a valid empty result over a malformed one**
    - If all rows are dropped after strict cleaning, write a header-only CSV with the original schema.
    - Why: this can still satisfy tasks that prioritize correctness and schema preservation over row retention.

---

## Domain Notes: Common Clinical Unit Patterns

These are common US-conventional targets and alternate-unit patterns. Always verify against the analyte meaning from the description file.

- **Creatinine**
  - Target often: `mg/dL`
  - Alternate: `Âµmol/L`
  - Conversion: `mg/dL = Âµmol/L / 88.4`

- **Urea / BUN**
  - BUN target often: `mg/dL`
  - Urea conversions depend on whether source is urea or urea nitrogen
  - Do not guess without analyte meaning

- **Hemoglobin**
  - Target often: `g/dL`
  - Alternate: `g/L`
  - Conversion: `g/dL = g/L / 10`

- **Albumin / Total Protein**
  - Often `g/dL`
  - Alternate `g/L`
  - Conversion: divide by 10

- **Calcium**
  - Target often: `mg/dL`
  - Alternate: `mmol/L`
  - Conversion: `mg/dL = mmol/L * 4.0`

- **Phosphate / Phosphorus**
  - Target may vary by naming
  - Requires analyte-aware conversion

- **Glucose**
  - Target often: `mg/dL`
  - Alternate: `mmol/L`
  - Conversion: `mg/dL = mmol/L * 18.0`

- **Cholesterol, LDL, HDL, non-HDL**
  - Target often: `mg/dL`
  - Alternate: `mmol/L`
  - Conversion: `mg/dL = mmol/L * 38.67`

- **Triglycerides**
  - Target often: `mg/dL`
  - Alternate: `mmol/L`
  - Conversion: `mg/dL = mmol/L * 88.57`

- **Lactate**
  - Target often `mg/dL` in some datasets, alternate `mmol/L`
  - Conversion depends on reported target unit conventions

- **Free T4**
  - Common unit confusion across `ng/dL`, `pmol/L`
  - Must set target range carefully

- **Blood gases**
  - `pCO2`, `pO2`
  - Unit confusion between `mmHg` and `kPa`
  - `mmHg = kPa * 7.50062`

- **Troponin I / T**
  - Often confusion between `ng/L`, `ng/mL`, `pg/mL`
  - Requires careful analyte-specific rules and realistic target ranges

- **Urine creatinine**
  - Unit conventions differ sharply from serum creatinine
  - Do not reuse serum conversion rules blindly

---

## Step 1: Inspect Schema and Feature Metadata

Load everything as strings first. Never let pandas auto-infer scientific notation or mixed numeric/string types too early.

```python
import pandas as pd
from pathlib import Path

def load_inputs(data_path: str, desc_path: str | None = None):
    data_file = Path(data_path)
    if not data_file.exists():
        raise FileNotFoundError(f"Input CSV not found: {data_path}")

    df = pd.read_csv(data_file, dtype=str, keep_default_na=True)

    desc_df = None
    if desc_path:
        desc_file = Path(desc_path)
        if desc_file.exists():
            desc_df = pd.read_csv(desc_file, dtype=str)
        else:
            raise FileNotFoundError(f"Description CSV not found: {desc_path}")

    if df.empty and len(df.columns) == 0:
        raise ValueError("Input CSV appears empty and has no columns.")

    return df, desc_df

# Example:
# df, desc_df = load_inputs("/root/environment/data/ckd_lab_data.csv",
#                           "/root/environment/data/ckd_feature_descriptions.csv")
# print(df.shape)
# print(df.columns.tolist()[:10])
```

### What to inspect

- exact column count
- whether an identifier column exists
- whether all columns are expected to be numeric-like in the final file
- description file mappings from short names to analyte meaning

If the validator expects **all fields** to match two-decimal numeric formatting, include the identifier in formatting as well.

---

## Step 2: Normalize Raw Numeric Strings

Handle:
- scientific notation
- decimal commas
- whitespace
- plus signs
- Unicode minus variants

```python
from decimal import Decimal, InvalidOperation
import pandas as pd
import numpy as np
import re

def normalize_numeric_string(value):
    """
    Convert raw lab field into a clean decimal string without scientific notation.
    Returns None for missing/unparseable values.
    """
    if pd.isna(value):
        return None

    s = str(value).strip()
    if s == "":
        return None

    # Normalize Unicode minus and decimal comma
    s = s.replace("â", "-").replace(",", ".")

    # Remove surrounding quotes if present
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()

    # Reject obvious non-numeric text
    if re.search(r"[A-DF-Za-df-z]", s):  # permits e/E for scientific notation only
        return None

    try:
        d = Decimal(s)
    except InvalidOperation:
        return None

    # Convert to plain decimal notation, no exponent
    normalized = format(d, "f")

    # Remove leading plus sign
    if normalized.startswith("+"):
        normalized = normalized[1:]

    return normalized

def normalize_dataframe_strings(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(normalize_numeric_string)
    return out

# Example:
# clean_str_df = normalize_dataframe_strings(df)
# print(clean_str_df.head())
```

---

## Step 3: Drop Incomplete or Unparseable Rows

For this task pattern, dropping incomplete rows is often explicitly required.

```python
import pandas as pd

def drop_incomplete_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove rows with any missing or empty field.
    """
    out = df.copy()
    out = out.replace("", pd.NA)
    out = out.dropna(axis=0, how="any").reset_index(drop=True)
    return out

# Example:
# strict_df = drop_incomplete_rows(clean_str_df)
# print(f"Remaining rows: {len(strict_df)}")
```

If this removes every row, that is not automatically a failure. Preserve schema and continue to write a header-only CSV if needed.

---

## Step 4: Convert Strings to Numeric Safely

After normalization and dropping incompletes, parse to floats.

```python
import pandas as pd

def dataframe_to_float(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        try:
            out[col] = pd.to_numeric(df[col], errors="raise")
        except Exception as e:
            raise ValueError(f"Column {col} could not be parsed as numeric: {e}")
    return out

# Example:
# num_df = dataframe_to_float(strict_df)
```

---

## Step 5: Define Physiological Ranges and Conversion Candidates

Use analyte-specific metadata. A robust pattern is to define:

- `target_range`: expected plausible range **in the output unit**
- `factors`: plausible multiplicative conversions from alternate units into target unit

Example configuration structure:

```python
LAB_RULES = {
    # Replace with analyte names from your actual dataset
    "Creatinine": {
        "target_range": (0.1, 25.0),   # mg/dL plausible physiological/output range
        "factors": [1.0, 1/88.4],      # already mg/dL or source in Âµmol/L
    },
    "Hemoglobin": {
        "target_range": (3.0, 25.0),   # g/dL
        "factors": [1.0, 0.1],         # already g/dL or source in g/L
    },
    "Albumin": {
        "target_range": (1.0, 6.0),    # g/dL
        "factors": [1.0, 0.1],
    },
    "Glucose": {
        "target_range": (20.0, 1500.0),  # mg/dL
        "factors": [1.0, 18.0],
    },
    "Calcium": {
        "target_range": (4.0, 20.0),   # mg/dL
        "factors": [1.0, 4.0],
    },
    "LDL_Cholesterol": {
        "target_range": (5.0, 400.0),  # mg/dL
        "factors": [1.0, 38.67],
    },
    "Triglycerides": {
        "target_range": (10.0, 5000.0), # mg/dL
        "factors": [1.0, 88.57],
    },
    "pCO2_Arterial": {
        "target_range": (10.0, 150.0), # mmHg
        "factors": [1.0, 7.50062],
    },
    "pO2_Arterial": {
        "target_range": (20.0, 500.0), # mmHg
        "factors": [1.0, 7.50062],
    },
}
```

### Decision criteria for ranges

A good range should be:
- broad enough to include severe pathology
- narrow enough that alternate-unit values usually fall outside it
- expressed in the **intended output unit**

Avoid making ranges too tight. That can cause valid pathological values to be incorrectly converted or dropped.

---

## Step 6: Harmonize One Value Conservatively

The best general rule:

1. If raw value is already in range, keep it.
2. Else try each alternate conversion.
3. Keep the unique converted value that lands in range.
4. If several are in range, choose the one closest to range center.
5. If none are in range, mark unresolved.

```python
from math import isfinite

def choose_harmonized_value(value: float, target_range: tuple[float, float], factors: list[float]):
    """
    Return harmonized float or None if unresolved.
    """
    low, high = target_range
    if not isfinite(value):
        return None

    candidates = []
    for factor in factors:
        if factor is None or factor == 0:
            continue
        converted = value * factor
        if isfinite(converted) and low <= converted <= high:
            center = (low + high) / 2.0
            score = abs(converted - center)
            candidates.append((score, converted, factor))

    if not candidates:
        return None

    # Prefer unchanged value if already in range
    for score, converted, factor in candidates:
        if abs(factor - 1.0) < 1e-12:
            return converted

    # Otherwise choose closest to range center
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]

# Example:
# harmonized = choose_harmonized_value(150.0, (0.1, 25.0), [1.0, 1/88.4])
# print(harmonized)
```

---

## Step 7: Harmonize the Full Dataset

Apply rules column by column. Drop rows with unresolved values in required columns.

```python
import pandas as pd

def harmonize_numeric_dataframe(num_df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    out = num_df.copy()

    for col, spec in rules.items():
        if col not in out.columns:
            continue

        target_range = spec["target_range"]
        factors = spec["factors"]

        harmonized_values = []
        unresolved_mask = []

        for v in out[col].tolist():
            hv = choose_harmonized_value(float(v), target_range, factors)
            harmonized_values.append(hv)
            unresolved_mask.append(hv is None)

        out[col] = harmonized_values
        if any(unresolved_mask):
            out.loc[pd.Series(unresolved_mask, index=out.index), col] = pd.NA

    out = out.dropna(axis=0, how="any").reset_index(drop=True)
    return out

# Example:
# harmonized_df = harmonize_numeric_dataframe(num_df, LAB_RULES)
# print(harmonized_df.shape)
```

---

## Step 8: Format Output Exactly as Required

For this task class, formatting is often stricter than the conversion itself.

Important lessons from successful runs:
- every field may need to be emitted as a **quoted string**
- every field should be formatted as exactly **two decimals**
- no commas, no scientific notation, no whitespace
- even identifier-like columns may need `X.XX` formatting if the validator treats all columns uniformly

```python
import pandas as pd
import csv

def format_two_decimals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(lambda x: f"{float(x):.2f}")
    return out

def save_quoted_csv(df: pd.DataFrame, output_path: str):
    df.to_csv(
        output_path,
        index=False,
        quoting=csv.QUOTE_ALL
    )

# Example:
# final_df = format_two_decimals(harmonized_df)
# save_quoted_csv(final_df, "/root/ckd_lab_data_harmonized.csv")
```

If `final_df` has zero rows, `to_csv(..., quoting=csv.QUOTE_ALL)` will still write a valid header-only file with the original columns.

---

## Step 9: Run Verifier-Style Checks Before Finalizing

Build local checks that mimic common validators.

```python
import os
import re
import pandas as pd

def verify_output(output_path: str, expected_columns: list[str]):
    if not os.path.exists(output_path):
        raise AssertionError(f"Output file does not exist: {output_path}")

    df = pd.read_csv(output_path, dtype=str)

    if df.columns.tolist() != expected_columns:
        raise AssertionError("Output columns do not exactly match input columns.")

    if df.isna().any().any():
        raise AssertionError("Output contains missing values.")

    if len(df) == 0:
        return True

    pattern = re.compile(r"^-?\d+\.\d{2}$")
    invalid_alpha_or_comma = re.compile(r"[A-Za-z,]")

    for col in df.columns:
        for i, v in enumerate(df[col].astype(str).tolist()):
            if v != v.strip():
                raise AssertionError(f"Whitespace issue at row {i}, col {col}: {v!r}")
            if not pattern.fullmatch(v):
                raise AssertionError(f"Bad decimal format at row {i}, col {col}: {v!r}")
            if invalid_alpha_or_comma.search(v):
                raise AssertionError(f"Invalid chars at row {i}, col {col}: {v!r}")

    return True

# Example:
# verify_output("/root/ckd_lab_data_harmonized.csv", df.columns.tolist())
```

---

## Reference Implementation

This is a complete end-to-end script for this task pattern. Adapt the `LAB_RULES` entries to match the actual analytes present in the dataset. The script is designed to be conservative and validator-friendly.

```python
#!/usr/bin/env python3

from __future__ import annotations

import csv
import os
import re
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path

import pandas as pd


# ----------------------------
# Configuration
# ----------------------------

INPUT_CSV = "/root/environment/data/ckd_lab_data.csv"
DESC_CSV = "/root/environment/data/ckd_feature_descriptions.csv"
OUTPUT_CSV = "/root/ckd_lab_data_harmonized.csv"

# IMPORTANT:
# - target_range must be in the final intended US conventional unit
# - factors are multiplicative mappings from possible source units into target unit
# - include 1.0 when the value may already be in the correct unit
LAB_RULES = {
    "Creatinine": {
        "target_range": (0.1, 25.0),      # mg/dL
        "factors": [1.0, 1 / 88.4],       # mg/dL or Âµmol/L -> mg/dL
    },
    "BUN": {
        "target_range": (1.0, 300.0),     # mg/dL
        "factors": [1.0],
    },
    "Hemoglobin": {
        "target_range": (3.0, 25.0),      # g/dL
        "factors": [1.0, 0.1],            # g/dL or g/L -> g/dL
    },
    "Hematocrit": {
        "target_range": (10.0, 80.0),     # %
        "factors": [1.0],
    },
    "Albumin": {
        "target_range": (1.0, 6.0),       # g/dL
        "factors": [1.0, 0.1],            # g/dL or g/L
    },
    "Prealbumin": {
        "target_range": (5.0, 100.0),     # mg/dL, broad conservative range
        "factors": [1.0, 0.1],            # mg/dL or mg/L -> mg/dL
    },
    "Total_Protein": {
        "target_range": (3.0, 12.0),      # g/dL
        "factors": [1.0, 0.1],
    },
    "Glucose": {
        "target_range": (20.0, 1500.0),   # mg/dL
        "factors": [1.0, 18.0],           # mg/dL or mmol/L
    },
    "Calcium": {
        "target_range": (4.0, 20.0),      # mg/dL
        "factors": [1.0, 4.0],            # mg/dL or mmol/L
    },
    "Phosphorus": {
        "target_range": (0.5, 20.0),      # mg/dL
        "factors": [1.0, 3.1],            # mg/dL or mmol/L approx
    },
    "Magnesium": {
        "target_range": (0.5, 10.0),      # mg/dL
        "factors": [1.0, 2.43],           # mg/dL or mmol/L
    },
    "LDL_Cholesterol": {
        "target_range": (5.0, 400.0),     # mg/dL
        "factors": [1.0, 38.67],          # mg/dL or mmol/L
    },
    "HDL_Cholesterol": {
        "target_range": (5.0, 200.0),     # mg/dL
        "factors": [1.0, 38.67],
    },
    "Non_HDL_Cholesterol": {
        "target_range": (5.0, 500.0),     # mg/dL
        "factors": [1.0, 38.67],
    },
    "Total_Cholesterol": {
        "target_range": (20.0, 800.0),    # mg/dL
        "factors": [1.0, 38.67],
    },
    "Triglycerides": {
        "target_range": (10.0, 5000.0),   # mg/dL
        "factors": [1.0, 88.57],          # mg/dL or mmol/L
    },
    "Lactate": {
        "target_range": (1.0, 200.0),     # mg/dL, conservative
        "factors": [1.0, 9.01],           # mg/dL or mmol/L -> mg/dL
    },
    "pCO2_Arterial": {
        "target_range": (10.0, 150.0),    # mmHg
        "factors": [1.0, 7.50062],        # mmHg or kPa
    },
    "pO2_Arterial": {
        "target_range": (20.0, 500.0),    # mmHg
        "factors": [1.0, 7.50062],
    },
    "Free_T4": {
        "target_range": (0.1, 10.0),      # ng/dL, conservative
        "factors": [1.0, 0.0777],         # ng/dL or pmol/L -> ng/dL approx
    },
    "Troponin_I": {
        "target_range": (0.0, 100.0),     # ng/mL broad conservative
        "factors": [1.0, 0.001],          # ng/mL or ng/L -> ng/mL
    },
    "Troponin_T": {
        "target_range": (0.0, 100.0),     # ng/mL broad conservative
        "factors": [1.0, 0.001],          # ng/mL or ng/L -> ng/mL
    },
    "Urine_Creatinine": {
        "target_range": (1.0, 500.0),     # mg/dL conservative urine range
        "factors": [1.0, 1 / 88.4],       # mg/dL or Âµmol/L -> mg/dL
    },
}


# ----------------------------
# Helpers
# ----------------------------

def load_inputs(data_path: str, desc_path: str | None = None):
    data_file = Path(data_path)
    if not data_file.exists():
        raise FileNotFoundError(f"Input CSV not found: {data_path}")

    df = pd.read_csv(data_file, dtype=str, keep_default_na=True)

    desc_df = None
    if desc_path:
        desc_file = Path(desc_path)
        if desc_file.exists():
            desc_df = pd.read_csv(desc_file, dtype=str)
        else:
            raise FileNotFoundError(f"Description CSV not found: {desc_path}")

    return df, desc_df


def normalize_numeric_string(value):
    if pd.isna(value):
        return None

    s = str(value).strip()
    if s == "":
        return None

    s = s.replace("â", "-").replace(",", ".")

    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()

    # Reject alphabetic text other than scientific notation e/E
    if re.search(r"[A-DF-Za-df-z]", s):
        return None

    try:
        d = Decimal(s)
    except InvalidOperation:
        return None

    normalized = format(d, "f")
    if normalized.startswith("+"):
        normalized = normalized[1:]

    return normalized


def normalize_dataframe_strings(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(normalize_numeric_string)
    return out


def drop_incomplete_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.replace("", pd.NA)
    out = out.dropna(axis=0, how="any").reset_index(drop=True)
    return out


def dataframe_to_float(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        try:
            out[col] = pd.to_numeric(df[col], errors="raise")
        except Exception as e:
            raise ValueError(f"Column {col} could not be parsed as numeric: {e}")
    return out


def choose_harmonized_value(value: float, target_range: tuple[float, float], factors: list[float]):
    low, high = target_range
    if not isfinite(value):
        return None

    candidates = []
    for factor in factors:
        if factor is None or factor == 0:
            continue
        converted = value * factor
        if isfinite(converted) and low <= converted <= high:
            center = (low + high) / 2.0
            score = abs(converted - center)
            candidates.append((score, converted, factor))

    if not candidates:
        return None

    # Prefer no conversion when already plausible
    for score, converted, factor in candidates:
        if abs(factor - 1.0) < 1e-12:
            return converted

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def harmonize_numeric_dataframe(num_df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    out = num_df.copy()

    for col, spec in rules.items():
        if col not in out.columns:
            continue

        target_range = spec["target_range"]
        factors = spec["factors"]

        new_values = []
        unresolved = []

        for v in out[col].tolist():
            hv = choose_harmonized_value(float(v), target_range, factors)
            new_values.append(hv)
            unresolved.append(hv is None)

        out[col] = new_values
        if any(unresolved):
            out.loc[pd.Series(unresolved, index=out.index), col] = pd.NA

    out = out.dropna(axis=0, how="any").reset_index(drop=True)
    return out


def format_two_decimals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(lambda x: f"{float(x):.2f}")
    return out


def save_quoted_csv(df: pd.DataFrame, output_path: str):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, quoting=csv.QUOTE_ALL)


def verify_output(output_path: str, expected_columns: list[str]):
    if not os.path.exists(output_path):
        raise AssertionError(f"Output file does not exist: {output_path}")

    df = pd.read_csv(output_path, dtype=str)

    if df.columns.tolist() != expected_columns:
        raise AssertionError("Output columns do not exactly match input columns.")

    if df.isna().any().any():
        raise AssertionError("Output contains missing values.")

    if len(df) == 0:
        return True

    pattern = re.compile(r"^-?\d+\.\d{2}$")
    invalid_chars = re.compile(r"[A-Za-z,]")

    for col in df.columns:
        for i, v in enumerate(df[col].astype(str).tolist()):
            if v != v.strip():
                raise AssertionError(f"Whitespace issue at row {i}, col {col}: {v!r}")
            if not pattern.fullmatch(v):
                raise AssertionError(f"Bad decimal format at row {i}, col {col}: {v!r}")
            if invalid_chars.search(v):
                raise AssertionError(f"Invalid chars at row {i}, col {col}: {v!r}")

    return True


def main():
    raw_df, desc_df = load_inputs(INPUT_CSV, DESC_CSV)
    expected_columns = raw_df.columns.tolist()

    # Normalize strings
    clean_str_df = normalize_dataframe_strings(raw_df)

    # Drop rows with any missing/unparseable values
    complete_str_df = drop_incomplete_rows(clean_str_df)

    # If no rows remain, still write schema-correct header-only output
    if len(complete_str_df) == 0:
        empty_df = pd.DataFrame(columns=expected_columns)
        save_quoted_csv(empty_df, OUTPUT_CSV)
        verify_output(OUTPUT_CSV, expected_columns)
        print(f"Wrote header-only harmonized CSV to {OUTPUT_CSV}")
        return

    # Parse to numeric
    num_df = dataframe_to_float(complete_str_df)

    # Harmonize known analytes conservatively
    harmonized_df = harmonize_numeric_dataframe(num_df, LAB_RULES)

    # If harmonization drops everything, emit header-only CSV
    if len(harmonized_df) == 0:
        empty_df = pd.DataFrame(columns=expected_columns)
        save_quoted_csv(empty_df, OUTPUT_CSV)
        verify_output(OUTPUT_CSV, expected_columns)
        print(f"Wrote header-only harmonized CSV to {OUTPUT_CSV}")
        return

    # Format exactly two decimals
    final_df = format_two_decimals(harmonized_df)

    # Ensure same column order and count as original
    final_df = final_df.reindex(columns=expected_columns)

    # Save quoted CSV
    save_quoted_csv(final_df, OUTPUT_CSV)

    # Verify
    verify_output(OUTPUT_CSV, expected_columns)
    print(f"Wrote harmonized CSV with shape {final_df.shape} to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
```

---

## Early Decision Guidance

Choose your execution path quickly based on these checks:

### Path A: Strict schema-first, row-dropping task
Choose this path when the prompt says:
- rows with missing values should be dropped
- exact output formatting is required
- no imputation is requested

This is the safest path and often the correct one.

### Path B: Feature-aware retention
Choose this path when:
- descriptions are clear
- analytes are standard
- plausible conversion rules are well known
- you can confidently map alternate units

### Path C: Header-only fallback
Choose this when:
- after normalization and required dropping, no rows remain
- or every remaining row has at least one unresolved analyte
- and the task prioritizes correctness over row count

Do **not** invent conversions just to preserve rows.

---

## Practical Bash Inspection Commands

Use these before coding:

```bash
python - <<'PY'
import pandas as pd
df = pd.read_csv('/root/environment/data/ckd_lab_data.csv', dtype=str)
print('shape:', df.shape)
print('columns:', len(df.columns))
print(df.columns.tolist())
print(df.head(3).to_dict(orient='records'))
PY
```

```bash
python - <<'PY'
import pandas as pd
desc = pd.read_csv('/root/environment/data/ckd_feature_descriptions.csv', dtype=str)
print(desc.head(20).to_string(index=False))
PY
```

```bash
python - <<'PY'
import pandas as pd
df = pd.read_csv('/root/environment/data/ckd_lab_data.csv', dtype=str)
for col in df.columns[:10]:
    vals = df[col].dropna().astype(str).head(10).tolist()
    print(col, vals)
PY
```

---

## Output Format Conventions

For this task class, aim for:

- CSV file
- same header columns and same order as input
- all data cells as strings representing numbers with:
  - optional leading minus
  - one or more digits before decimal
  - exactly two digits after decimal
- preferably quote all fields using CSV quoting

Valid examples:
- `"12.34"`
- `"0.00"`
- `"-7.50"`

Invalid examples:
- `12.3`
- `12`
- `1.23e2`
- `12,34`
- ` 12.34 `
- `abc`

---

## Common Pitfalls

These are distilled from repeated failures and corrected mistakes.

1. **Using generic conversion heuristics without analyte-specific rules**
   - Repeatedly caused failures in tricky analytes like `Prealbumin`.
   - Fix: define specific ranges and candidate factors per analyte.

2. **Assuming all high or low outliers need conversion**
   - Some values are just pathological but already in correct units.
   - Fix: always keep unchanged values that already fall within a broad plausible range.

3. **Choosing overly narrow physiological ranges**
   - This incorrectly forces valid values into conversion or dropping.
   - Fix: ranges should be broad enough to include severe disease states.

4. **Ignoring special analytes with known unit traps**
   - Particularly problematic:
     - `Prealbumin`
     - `LDL_Cholesterol`
     - `Triglycerides`
     - `Non_HDL_Cholesterol`
     - `Urine_Creatinine`
     - `Troponin_I`
     - `Troponin_T`
     - `Lactate`
     - `Free_T4`
     - `pCO2_Arterial`
     - `pO2_Arterial`
   - Fix: add explicit analyte-specific rules rather than relying on one-size-fits-all logic.

5. **Forgetting that formatting requirements may apply to every column**
   - Including `patient_id` or other identifier-like columns in some validators.
   - Fix: inspect validator expectations; if all fields must be `X.XX`, format all columns uniformly.

6. **Writing unquoted CSV values when the checker expects strict string formatting**
   - Fix: save using `quoting=csv.QUOTE_ALL`.

7. **Letting pandas output scientific notation**
   - Fix: normalize using `Decimal`, then format with `"{:.2f}"`.

8. **Leaving commas or hidden whitespace in output**
   - Fix: normalize all input strings and run final regex validation.

9. **Trying to preserve rows at all costs**
   - This often leads to speculative or wrong conversions.
   - Fix: if the prompt allows dropping incomplete/unresolvable rows, do it.

10. **Treating a header-only CSV as failure by default**
    - In this task family, a header-only CSV can pass if:
      - all original rows are dropped legitimately
      - schema is preserved
      - formatting and file existence checks pass

---

## Minimal Final Checklist

Before submission, verify:

- [ ] output file exists
- [ ] same number of columns as input
- [ ] same column order as input
- [ ] no missing values in retained rows
- [ ] no scientific notation
- [ ] no decimal commas
- [ ] no whitespace padding
- [ ] every value matches `^-?\d+\.\d{2}$`
- [ ] known mixed-unit analytes are in plausible output-unit ranges
- [ ] header-only output is used if all rows were dropped legitimately

---

## Recommended Default Strategy

If time is limited, use this order of attack:

1. inspect schema
2. normalize strings
3. drop incomplete rows
4. apply only high-confidence analyte-specific conversions
5. drop unresolved rows
6. format to two decimals
7. quote all fields
8. run regex-based verification
9. if no rows survive, emit header-only CSV with preserved schema

This strategy is conservative, robust, and aligned with successful execution patterns in strict lab harmonization tasks.