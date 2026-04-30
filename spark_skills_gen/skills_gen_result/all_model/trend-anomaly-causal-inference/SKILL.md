---
title: "E-Commerce Trend Anomaly Detection & Causal Inference Pipeline"
category: "trend-anomaly-causal-inference"
domain: "data-science"
tags:
  - anomaly-detection
  - causal-inference
  - difference-in-differences
  - e-commerce
  - counterfactual-prediction
  - feature-engineering
  - data-cleaning
  - pandas
  - statsmodels
  - prophet
dependencies:
  - pandas
  - numpy
  - scipy
  - scikit-learn
  - statsmodels
  - prophet
---

# E-Commerce Trend Anomaly Detection & Causal Inference Pipeline

## Overview

This skill covers a multi-step analytical pipeline for e-commerce data:

1. **Data Cleaning** — Deduplicate, fix corrupted fields, handle missing values in survey and transaction data.
2. **Anomaly Detection** — Build counterfactual daily-spend models per product category and score anomalies during a treatment window.
3. **Feature Engineering** — Transform demographic survey data into numeric features suitable for regression.
4. **Data Aggregation** — Build intensive-margin (spend among buyers) and extensive-margin (purchase probability) datasets.
5. **Causal Analysis** — Run Difference-in-Differences (DiD) regressions to identify demographic drivers of anomalous spending.

The pipeline produces 7 output artifacts: cleaned data, anomaly scores, engineered features, aggregated margins, and a structured JSON causal report.

---

## High-Level Workflow

### Step 0: Read Tests First (Critical)

Before writing any code, read the test file to understand exact expectations for column names, data shapes, value ranges, JSON schema, and correctness thresholds. This prevents rework.

```python
# Always start here
import subprocess
result = subprocess.run(["cat", "../tests/test_outputs.py"], capture_output=True, text=True)
print(result.stdout)
```

Key things to extract from tests:
- Expected column names for every output CSV
- Expected row counts or ranges
- Anomaly index range and sorting expectations
- Feature engineering: which columns become which features, expected feature count
- JSON schema: required keys, nesting, sorting order
- DiD method names expected in the report (e.g., "Univariate DiD", "Multivariate Heterogeneous DiD")

### Step 1: Data Cleaning

**Goal:** Produce `survey_cleaned.csv` and `amazon-purchases-2019-2020-filtered.csv`.

**Survey cleaning checklist:**
- Remove exact duplicate rows
- Detect corrupted column values (e.g., age values placed in household-size column)
- Standardize categorical values (strip whitespace, consistent casing)
- Fill missing values with domain-appropriate defaults
- Ensure all IDs are unique after cleaning

**Purchase cleaning checklist:**
- Remove exact duplicate rows
- Drop rows with null category
- Parse dates properly
- Compute `Total_Spend` = `Quantity` × `Unit Price` (or equivalent)
- Verify all purchase IDs exist in the survey data

```python
import pandas as pd
import numpy as np
import os

os.makedirs('/app/output', exist_ok=True)

# ── Survey Cleaning ──────────────────────────────────────────────────────
survey = pd.read_csv('/app/data/survey_dirty.csv')

# 1. Drop exact duplicates
survey = survey.drop_duplicates()

# 2. Detect corrupted hh-size values (numeric values that are actually ages)
#    Pattern: some rows have age-like integers (e.g., 31, 45) in the hh-size
#    column and NaN in the actual age/howmany column.
#    Identify these by checking if hh-size values are numeric and unusually large.
hhsize_col = [c for c in survey.columns if 'hh' in c.lower() and 'size' in c.lower()][0]
howmany_col = [c for c in survey.columns if 'howmany' in c.lower()][0]

# Find rows where hh-size looks like an age (numeric, > 10 or not a typical hh-size)
def is_corrupted_hhsize(row):
    try:
        val = int(float(row[hhsize_col]))
        return val > 10 and pd.isna(row[howmany_col])
    except (ValueError, TypeError):
        return False

corrupted_mask = survey.apply(is_corrupted_hhsize, axis=1)
print(f"Found {corrupted_mask.sum()} corrupted hh-size rows")

# For corrupted rows: the hh-size value is actually the age
# Map numeric age to the appropriate age bracket used in the survey
age_col = [c for c in survey.columns if 'age' in c.lower()][0]

def age_to_bracket(age_val):
    """Convert numeric age to the bracket format used in the survey."""
    age = int(float(age_val))
    if age < 18:
        return 'Under 18'
    elif age < 25:
        return '18-24'
    elif age < 35:
        return '25-34'
    elif age < 45:
        return '35-44'
    elif age < 55:
        return '45-54'
    elif age < 65:
        return '55-64'
    else:
        return '65+'

# Fix corrupted rows: move hh-size value to age bracket, set hh-size to NaN for now
for idx in survey[corrupted_mask].index:
    numeric_age = survey.loc[idx, hhsize_col]
    survey.loc[idx, age_col] = age_to_bracket(numeric_age)
    survey.loc[idx, hhsize_col] = np.nan

# 3. Standardize categorical columns
for col in survey.select_dtypes(include='object').columns:
    survey[col] = survey[col].str.strip()

# 4. Fill remaining NaN values
#    - For categorical: fill with mode
#    - For numeric: fill with median
for col in survey.columns:
    if col == 'Survey ResponseID':
        continue
    if survey[col].dtype == 'object':
        mode_val = survey[col].mode()
        if len(mode_val) > 0:
            survey[col] = survey[col].fillna(mode_val[0])
    else:
        survey[col] = survey[col].fillna(survey[col].median())

# 5. Verify no nulls remain and IDs are unique
assert survey.isnull().sum().sum() == 0, "Nulls remain in survey"
assert survey['Survey ResponseID'].nunique() == len(survey), "Duplicate IDs in survey"

survey.to_csv('/app/output/survey_cleaned.csv', index=False)
print(f"Survey cleaned: {survey.shape}")


# ── Purchase Cleaning ────────────────────────────────────────────────────
purchases = pd.read_csv('/app/data/amazon-purchases-2019-2020_dirty.csv')

# 1. Drop exact duplicates
purchases = purchases.drop_duplicates()

# 2. Identify key columns (names may vary — adapt to actual headers)
#    Common columns: Order ID, Order Date, Category, Quantity, Unit Price,
#    Survey ResponseID (or similar join key)
print("Purchase columns:", purchases.columns.tolist())

# 3. Drop rows with null Category
cat_col = [c for c in purchases.columns if 'categ' in c.lower()][0]
purchases = purchases.dropna(subset=[cat_col])

# 4. Strip whitespace from Category
purchases[cat_col] = purchases[cat_col].str.strip()

# 5. Parse dates
date_col = [c for c in purchases.columns if 'date' in c.lower()][0]
purchases[date_col] = pd.to_datetime(purchases[date_col])

# 6. Compute Total_Spend if not already present
qty_col = [c for c in purchases.columns if 'quant' in c.lower()][0]
price_col = [c for c in purchases.columns if 'price' in c.lower() or 'cost' in c.lower()][0]

# Clean price column (remove $ signs, commas)
if purchases[price_col].dtype == 'object':
    purchases[price_col] = (
        purchases[price_col]
        .str.replace('$', '', regex=False)
        .str.replace(',', '', regex=False)
        .astype(float)
    )

purchases['Total_Spend'] = purchases[qty_col].astype(float) * purchases[price_col].astype(float)

# 7. Verify all purchase user IDs exist in survey
id_col_p = [c for c in purchases.columns if 'survey' in c.lower() or 'response' in c.lower()][0]
valid_ids = set(survey['Survey ResponseID'].values)
purchases = purchases[purchases[id_col_p].isin(valid_ids)]

purchases.to_csv('/app/output/amazon-purchases-2019-2020-filtered.csv', index=False)
print(f"Purchases cleaned: {purchases.shape}")
```

### Step 2: Anomaly Detection — Counterfactual Daily Spend

**Goal:** Produce `category_anomaly_index.csv` with columns `Category` and `Anomaly_Index`.

**Method:** For each product category, fit a regression model on daily total spend using all historical data before March 2020 (the treatment window). Predict what March 2020 *would have looked like* without the event. Compare actual vs. predicted to compute a deviation index scaled to [-100, 100].

**Key decisions:**
- Use `LinearRegression` on daily spend with day-of-week and trend features — it's simple, fast, and sufficient for counterfactual prediction at this granularity.
- Prophet is available but can be overkill and slow for 200+ categories. Use it only if linear regression fails tests.
- The anomaly index should reflect both direction and magnitude: positive = surge, negative = slump.

```python
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression

purchases = pd.read_csv('/app/output/amazon-purchases-2019-2020-filtered.csv')

# Identify columns
date_col = [c for c in purchases.columns if 'date' in c.lower()][0]
cat_col = [c for c in purchases.columns if 'categ' in c.lower()][0]

purchases[date_col] = pd.to_datetime(purchases[date_col])

# Define periods
treatment_start = pd.Timestamp('2020-03-01')
treatment_end = pd.Timestamp('2020-03-31')

# Aggregate daily spend per category
daily = (
    purchases
    .groupby([cat_col, pd.Grouper(key=date_col, freq='D')])['Total_Spend']
    .sum()
    .reset_index()
)
daily.columns = ['Category', 'Date', 'Daily_Spend']

# Build full date range per category (fill missing days with 0)
all_dates = pd.date_range(daily['Date'].min(), daily['Date'].max(), freq='D')
all_cats = daily['Category'].unique()

full_index = pd.MultiIndex.from_product([all_cats, all_dates], names=['Category', 'Date'])
daily = daily.set_index(['Category', 'Date']).reindex(full_index, fill_value=0.0).reset_index()

def compute_anomaly_index(cat_df):
    """Compute anomaly index for one category."""
    cat_df = cat_df.sort_values('Date').copy()
    cat_df['day_ordinal'] = (cat_df['Date'] - cat_df['Date'].min()).dt.days
    cat_df['dow'] = cat_df['Date'].dt.dayofweek

    # One-hot encode day of week
    dow_dummies = pd.get_dummies(cat_df['dow'], prefix='dow', drop_first=True).astype(float)
    cat_df = pd.concat([cat_df.reset_index(drop=True), dow_dummies.reset_index(drop=True)], axis=1)

    feature_cols = ['day_ordinal'] + [c for c in cat_df.columns if c.startswith('dow_')]

    # Split: train on everything before treatment, predict treatment
    train = cat_df[cat_df['Date'] < treatment_start]
    test = cat_df[(cat_df['Date'] >= treatment_start) & (cat_df['Date'] <= treatment_end)]

    if len(train) < 14 or len(test) == 0:
        return 0.0  # Not enough data

    X