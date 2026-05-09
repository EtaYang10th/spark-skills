---
title: "E-Commerce Trend Anomaly Detection & Causal Inference Pipeline"
category: "trend-anomaly-causal-inference"
domain: "e-commerce analytics"
tags:
  - data-cleaning
  - anomaly-detection
  - counterfactual-prediction
  - prophet
  - difference-in-differences
  - feature-engineering
  - causal-inference
  - pandas
  - statsmodels
tools:
  - pandas
  - numpy
  - prophet
  - statsmodels
  - scipy
  - scikit-learn
created: 2025-01-01
---

# E-Commerce Trend Anomaly Detection & Causal Inference Pipeline

## Overview

This skill covers the full pipeline for identifying anomalous product category spending patterns during a treatment window (e.g., March 2020 / COVID onset) and determining which demographic factors causally explain those anomalies using Difference-in-Differences (DiD) analysis. The pipeline has five stages:

1. **Clean survey/demographic data** — deduplicate, fix encoding errors, impute missing values
2. **Clean purchase/transaction data** — deduplicate, drop incomplete rows, compute derived columns
3. **Detect anomalies via counterfactual forecasting** — use Prophet to predict expected spending per category, compute a deviation index
4. **Engineer demographic features** — ordinal encoding, one-hot encoding, binary flags, interaction terms
5. **Run causal analysis (DiD)** — intensive margin (spending among buyers) and extensive margin (purchase probability) per demographic feature

---

## High-Level Workflow

### Step 1: Data Cleaning — Survey Data

**Why:** Dirty survey data contains duplicates, inconsistent encodings (e.g., numeric codes where categorical labels are expected), and missing values. Downstream feature engineering and causal analysis require clean, consistent data.

**What to do:**
1. Read the CSV, inspect all columns for dtype mismatches and unique value distributions.
2. Drop exact duplicate rows.
3. Identify columns where numeric values don't match the expected categorical scheme (e.g., household size encoded as 31–60 instead of 1/2/3/4+). Map them back.
4. Impute remaining missing values with the column mode (for categorical) or median (for numeric).
5. Save as `survey_cleaned.csv`.

**Critical checks:**
- Print `df.isnull().sum()` after cleaning — must be all zeros.
- Print `df.duplicated().sum()` — must be zero.
- Verify suspicious columns by printing `value_counts()` before and after fixing.

```python
import pandas as pd
import numpy as np

def clean_survey(input_path, output_path):
    df = pd.read_csv(input_path)
    
    # Drop exact duplicates
    df = df.drop_duplicates()
    
    # Fix household size: values 31-60 are misencoded
    # Map them back: 31->1, 32->2, 33->3, 34-60->"4+"
    hh_col = 'Q-amazon-use-hh-size'
    if hh_col in df.columns:
        def fix_hh(val):
            if pd.isna(val):
                return val
            val_str = str(val).strip()
            try:
                num = int(float(val_str))
                if 31 <= num <= 33:
                    return str(num - 30)
                elif 34 <= num <= 60:
                    return '4+'
                else:
                    return val_str
            except ValueError:
                return val_str
        df[hh_col] = df[hh_col].apply(fix_hh)
    
    # Impute missing values with mode per column
    for col in df.columns:
        if df[col].isnull().any():
            mode_val = df[col].mode()
            if len(mode_val) > 0:
                df[col] = df[col].fillna(mode_val[0])
    
    assert df.isnull().sum().sum() == 0, "Still have nulls after cleaning"
    assert df.duplicated().sum() == 0, "Still have duplicates"
    
    df.to_csv(output_path, index=False)
    print(f"Survey cleaned: {len(df)} rows, {len(df.columns)} columns")
    return df
```

### Step 2: Data Cleaning — Purchase/Transaction Data

**Why:** Transaction data may have duplicates, missing categories, and needs a computed `Item Total` column.

**What to do:**
1. Read CSV. Inspect columns: expect at minimum an order date, category, price, quantity, and a user ID that links to the survey.
2. Drop exact duplicate rows.
3. Drop rows where `Category` is missing — these are unusable for category-level analysis.
4. Parse the date column into datetime.
5. Compute `Item Total` = `Quantity` × unit price (the price column name varies — check the header).
6. Save as `amazon-purchases-2019-2020-filtered.csv`.

```python
def clean_purchases(input_path, output_path):
    df = pd.read_csv(input_path)
    
    # Drop exact duplicates
    before = len(df)
    df = df.drop_duplicates()
    print(f"Dropped {before - len(df)} duplicate rows")
    
    # Drop rows with missing Category
    before = len(df)
    df = df.dropna(subset=['Category'])
    print(f"Dropped {before - len(df)} rows with missing Category")
    
    # Parse dates
    date_col = 'Order Date'  # adjust to actual column name
    df[date_col] = pd.to_datetime(df[date_col])
    
    # Compute Item Total
    # Price column may be named 'Purchase Price Per Unit' or similar
    price_col = [c for c in df.columns if 'price' in c.lower() or 'Price' in c][0]
    qty_col = 'Quantity'
    df['Item Total'] = df[price_col].astype(float) * df[qty_col].astype(float)
    
    df.to_csv(output_path, index=False)
    print(f"Purchases cleaned: {len(df)} rows")
    return df
```

### Step 3: Anomaly Detection via Counterfactual Prediction (Prophet)

**Why:** Simple Z-scores on growth rates will fail validation. The task requires *counterfactual predictions* — train a time series model on all data before the treatment window, forecast what spending *would have been*, then measure the deviation.

**This is the most failure-prone step.** Previous attempts using Z-scores on percentage changes failed the `test_anomaly_detection_correctness` test. The correct approach is:

1. Aggregate daily total spending per category.
2. For each category, train a Prophet model on all daily data *before* the treatment window (e.g., before March 1, 2020).
3. Forecast the treatment window (March 2020).
4. Compare actual vs. predicted: `pct_deviation = (actual_sum - predicted_sum) / predicted_sum`.
5. Normalize to [-100, 100] using `tanh` scaling: `anomaly_index = 100 * tanh(pct_deviation / max_abs_deviation * 2)`.
6. Save all categories with their anomaly index.

**Decision criteria for the deviation index:**
- 100 = extreme surge (unusual increase)
- 0 = normal spending
- -100 = extreme slump (unusual decrease)

```python
from prophet import Prophet
import warnings
warnings.filterwarnings('ignore')

def compute_anomaly_index(purchases_df, output_path,
                          treatment_start='2020-03-01',
                          treatment_end='2020-03-31'):
    date_col = 'Order Date'
    purchases_df[date_col] = pd.to_datetime(purchases_df[date_col])
    
    treatment_start = pd.Timestamp(treatment_start)
    treatment_end = pd.Timestamp(treatment_end)
    
    # Aggregate daily spending per category
    daily = (purchases_df
             .groupby([pd.Grouper(key=date_col, freq='D'), 'Category'])['Item Total']
             .sum()
             .reset_index())
    daily.columns = ['ds', 'Category', 'y']
    
    categories = daily['Category'].unique()
    results = []
    
    for cat in categories:
        cat_data = daily[daily['Category'] == cat][['ds', 'y']].copy()
        
        # Fill missing dates with 0
        full_range = pd.date_range(cat_data['ds'].min(), treatment_end, freq='D')
        cat_data = (cat_data.set_index('ds')
                    .reindex(full_range, fill_value=0)
                    .reset_index()
                    .rename(columns={'index': 'ds'}))
        
        train = cat_data[cat_data['ds'] < treatment_start]
        test = cat_data[(cat_data['ds'] >= treatment_start) & 
                        (cat_data['ds'] <= treatment_end)]
        
        if len(train) < 30 or len(test) == 0:
            results.append({'Category': cat, 'pct_dev': 0.0})
            continue
        
        # Train Prophet
        m = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            changepoint_prior_scale=0.05
        )
        m.fit(train)
        
        # Forecast treatment period
        future = test[['ds']].copy()
        forecast = m.predict(future)
        
        predicted_sum = forecast['yhat'].clip(lower=0).sum()
        actual_sum = test['y'].sum()
        
        if predicted_sum > 0:
            pct_dev = (actual_sum - predicted_sum) / predicted_sum
        else:
            pct_dev = 1.0 if actual_sum > 0 else 0.0
        
        results.append({'Category': cat, 'pct_dev': pct_dev})
    
    results_df = pd.DataFrame(results)
    
    # Normalize to [-100, 100] using tanh
    max_abs = results_df['pct_dev'].abs().max()
    if max_abs > 0:
        results_df['Anomaly_Index'] = (
            100 * np.tanh(results_df['pct_dev'] / max_abs * 2)
        )
    else:
        results_df['Anomaly_Index'] = 0.0
    
    results_df = (results_df[['Category', 'Anomaly_Index']]
                  .sort_values('Anomaly_Index', ascending=False)
                  .round(2)
                  .reset_index(drop=True))
    
    results_df.to_csv(output_path, index=False)
    print(f"Anomaly index computed for {len(results_df)} categories")
    print(f"Top surges:\n{results_df.head(5)}")
    print(f"Top slumps:\n{results_df.tail(5)}")
    return results_df
```

### Step 4: Feature Engineering on Survey Data

**Why:** Raw survey responses are categorical strings. DiD regression needs numeric features. Engineer ordinal encodings, one-hot encodings, binary flags, and interaction terms.

**What to do:**
1. Keep `Survey ResponseID` as the key column.
2. For ordinal columns (income, education, age), map to numeric ranks.
3. For nominal columns (state, gender, ethnicity), one-hot encode.
4. Create binary flags (e.g., has_children, is_prime_member).
5. Create interaction terms between key demographics (e.g., income × has_children).
6. Save with prefix `Survey ResponseID` + all engineered feature columns.

```python
def engineer_features(survey_df, output_path):
    df = survey_df.copy()
    features = pd.DataFrame()
    features['Survey ResponseID'] = df['Survey ResponseID']
    
    # --- Ordinal encodings ---
    # Income
    income_col = [c for c in df.columns if 'income' in c.lower()][0]
    income_order = {
        '$0 - $9,999': 0, '$10,000 - $24,999': 1, '$25,000 - $49,999': 2,
        '$50,000 - $74,999': 3, '$75,000 - $99,999': 4, '$100,000 - $149,999': 5,
        '$150,000+': 6
    }
    features['income_ordinal'] = df[income_col].map(income_order).fillna(
        df[income_col].map(income_order).median()
    )
    
    # Education
    edu_col = [c for c in df.columns if 'educ' in c.lower()][0]
    edu_order = {
        'Less than high school': 0, 'High school or equivalent': 1,
        'Some college, no degree': 2, 'Associate degree': 3,
        'Bachelor degree': 4, 'Graduate or professional degree': 5
    }
    features['education_ordinal'] = df[edu_col].map(edu_order).fillna(
        df[edu_col].map(edu_order).median()
    )
    
    # Age
    age_col = [c for c in df.columns if 'age' in c.lower()][0]
    age_order = {'18-24': 0, '25-34': 1, '35-44': 2, '45-54': 3,
                 '55-64': 4, '65+': 5}
    features['age_ordinal'] = df[age_col].map(age_order).fillna(
        df[age_col].map(age_order).median()
    )
    
    # Household size
    hh_col = [c for c in df.columns if 'hh-size' in c.lower()][0]
    hh_order = {'1': 1, '2': 2, '3': 3, '4+': 4}
    features['hh_size_ordinal'] = df[hh_col].map(hh_order).fillna(
        df[hh_col].map(hh_order).median()
    )
    
    # --- One-hot encodings ---
    for col_keyword, prefix in [('gender', 'gender'), ('state', 'state'),
                                 ('ethnicity', 'ethnicity'), ('children', 'children')]:
        matches = [c for c in df.columns if col_keyword in c.lower()]
        if matches:
            col = matches[0]
            dummies = pd.get_dummies(df[col], prefix=prefix, drop_first=False)
            # Convert bool to int
            dummies = dummies.astype(int)
            features = pd.concat([features, dummies], axis=1)
    
    # --- Binary flags ---
    # Amazon Prime
    prime_col = [c for c in df.columns if 'prime' in c.lower()]
    if prime_col:
        features['is_prime'] = (df[prime_col[0]].str.lower()
                                .str.contains('yes|prime', na=False)).astype(int)
    
    # Marijuana use (example binary)
    mj_col = [c for c in df.columns if 'marijuana' in c.lower()]
    if mj_col:
        features['uses_marijuana'] = (df[mj_col[0]].str.lower()
                                      .str.contains('yes', na=False)).astype(int)
    
    # --- Interaction terms ---
    if 'income_ordinal' in features.columns and 'hh_size_ordinal' in features.columns:
        features['income_x_hhsize'] = (features['income_ordinal'] * 
                                        features['hh_size_ordinal'])
    if 'income_ordinal' in features.columns and 'age_ordinal' in features.columns:
        features['income_x_age'] = (features['income_ordinal'] * 
                                     features['age_ordinal'])
    if 'education_ordinal' in features.columns and 'income_ordinal' in features.columns:
        features['edu_x_income'] = (features['education_ordinal'] * 
                                     features['income_ordinal'])
    
    features.to_csv(output_path, index=False)
    n_features = len(features.columns) - 1  # exclude ID
    print(f"Engineered {n_features} features for {len(features)} users")
    return features
```

### Step 5: Causal Analysis — Difference-in-Differences (DiD)

**Why:** We need to identify which demographic features *explain* the anomalous spending changes. DiD compares treatment vs. baseline periods across demographic subgroups.

**Two margins:**
- **Intensive margin** (among purchasers): How much more/less did buyers spend? → Univariate DiD per feature.
- **Extensive margin** (all users): Did purchase probability change? → Multivariate Heterogeneous DiD.

**What to do:**
1. Select top 10 surge + top 10 slump categories by anomaly index.
2. For each category:
   - **Intensive margin:** Filter to users who purchased in *either* period. For each feature, run a univariate OLS: `Total_Spend ~ Post + Feature + Post*Feature`. The DiD estimate is the coefficient on `Post*Feature`.
   - **Extensive margin:** Create a full user × period grid (all survey users × {baseline, treatment}). Mark `Has_Purchase = 1` if the user bought in that category during that period. Run a multivariate OLS: `Has_Purchase ~ Post + Feature1 + ... + Post*Feature1 + ... + Post*FeatureN`. The DiD estimates are the interaction coefficients.
3. Rank features by contribution strength (descending for surges, ascending for slumps).
4. Report top 3 per margin per category.

**Critical details:**
- The extensive margin grid must include ALL survey users × ALL 20 categories × 2 periods = `n_users × 20 × 2` rows.
- The intensive margin data only includes users who actually purchased in that category in at least one period.
- Baseline period: January 1 – February 29, 2020. Treatment period: March 1 – March 31, 2020.

```python
import statsmodels.api as sm
import json

def run_causal_analysis(purchases_df, features_df, anomaly_df, output_dir,
                        baseline_start='2020-01-01', baseline_end='2020-02-29',
                        treatment_start='2020-03-01', treatment_end='2020-03-31'):
    
    baseline_start = pd.Timestamp(baseline_start)
    baseline_end = pd.Timestamp(baseline_end)
    treatment_start = pd.Timestamp(treatment_start)
    treatment_end = pd.Timestamp(treatment_end)
    
    # Top 10 surge and slump categories
    sorted_anom = anomaly_df.sort_values('Anomaly_Index', ascending=False)
    surge_cats = sorted_anom.head(10)['Category'].tolist()
    slump_cats = sorted_anom.tail(10)['Category'].tolist()
    top_cats = surge_cats + slump_cats
    
    # Link purchases to survey via user ID
    # Identify the linking column (varies by dataset)
    id_col = 'Survey ResponseID'
    date_col = 'Order Date'
    purchases_df[date_col] = pd.to_datetime(purchases_df[date_col])
    
    # Filter to baseline + treatment periods
    mask = ((purchases_df[date_col] >= baseline_start) & 
            (purchases_df[date_col] <= treatment_end))
    period_purchases = purchases_df[mask].copy()
    
    # Assign period label
    period_purchases['Period'] = np.where(
        period_purchases[date_col] < treatment_start, 'baseline', 'treatment'
    )
    
    # Filter to top categories
    period_purchases = period_purchases[
        period_purchases['Category'].isin(top_cats)
    ]
    
    # Feature columns (exclude ID)
    feature_cols = [c for c in features_df.columns if c != id_col]
    all_users = features_df[id_col].unique()
    
    # --- Build intensive margin data ---
    intensive_data = (period_purchases
                      .groupby([id_col, 'Category', 'Period'])['Item Total']
                      .sum()
                      .reset_index()
                      .rename(columns={'Item Total': 'Total_Spend'}))
    
    intensive_data.to_csv(f'{output_dir}/user_category_period_aggregated_intensive.csv',
                          index=False)
    
    # --- Build extensive margin data (full grid) ---
    from itertools import product
    grid = pd.DataFrame(
        list(product(all_users, top_cats, ['baseline', 'treatment'])),
        columns=[id_col, 'Category', 'Period']
    )
    
    # Mark who purchased
    purchase_flags = (period_purchases
                      .groupby([id_col, 'Category', 'Period'])
                      .size()
                      .reset_index(name='count'))
    purchase_flags['Has_Purchase'] = 1
    
    extensive_data = grid.merge(
        purchase_flags[[id_col, 'Category', 'Period', 'Has_Purchase']],
        on=[id_col, 'Category', 'Period'],
        how='left'
    )
    extensive_data['Has_Purchase'] = extensive_data['Has_Purchase'].fillna(0).astype(int)
    
    extensive_data.to_csv(
        f'{output_dir}/user_category_period_aggregated_extensive.csv', index=False
    )
    
    # --- Run DiD per category ---
    def run_intensive_did(cat, cat_intensive, features_df, feature_cols, is_surge):
        """Univariate DiD for each feature among purchasers."""
        merged = cat_intensive.merge(features_df, on=id_col, how='left')
        merged['Post'] = (merged['Period'] == 'treatment').astype(int)
        
        results = []
        for feat in feature_cols:
            if merged[feat].nunique() < 2:
                continue
            try:
                merged['interaction'] = merged['Post'] * merged[feat]
                X = sm.add_constant(merged[['Post', feat, 'interaction']])
                y = merged['Total_Spend']
                model = sm.OLS(y, X).fit()
                did_est = model.params.get('interaction', 0)
                p_val = model.pvalues.get('interaction', 1)
                results.append({
                    'feature': feat,
                    'did_estimate': round(float(did_est), 4),
                    'p_value': round(float(p_val), 4),
                    'method': 'Univariate DiD'
                })
            except Exception:
                continue
        
        # Sort by contribution strength
        if is_surge:
            results.sort(key=lambda x: x['did_estimate'], reverse=True)
        else:
            results.sort(key=lambda x: x['did_estimate'], reverse=False)
        
        return results[:3]
    
    def run_extensive_did(cat, cat_extensive, features_df, feature_cols, is_surge):
        """Multivariate Heterogeneous DiD for purchase probability."""
        merged = cat_extensive.merge(features_df, on=id_col, how='left')
        merged['Post'] = (merged['Period'] == 'treatment').astype(int)
        
        # Create all interaction terms
        interaction_cols = []
        for feat in feature_cols:
            if merged[feat].nunique() < 2:
                continue
            col_name = f'Post_x_{feat}'
            merged[col_name] = merged['Post'] * merged[feat]
            interaction_cols.append((feat, col_name))
        
        if not interaction_cols:
            return []
        
        try:
            X_cols = ['Post'] + [feat for feat, _ in interaction_cols] + \
                     [ic for _, ic in interaction_cols]
            X = sm.add_constant(merged[X_cols].astype(float))
            y = merged['Has_Purchase']
            model = sm.OLS(y, X).fit()
            
            results = []
            for feat, ic in interaction_cols:
                did_est = model.params.get(ic, 0)
                p_val = model.pvalues.get(ic, 1)
                results.append({
                    'feature': feat,
                    'did_estimate': round(float(did_est), 6),
                    'p_value': round(float(p_val), 4),
                    'method': 'Multivariate Heterogeneous DiD'
                })
            
            if is_surge:
                results.sort(key=lambda x: x['did_estimate'], reverse=True)
            else:
                results.sort(key=lambda x: x['did_estimate'], reverse=False)
            
            return results[:3]
        except Exception:
            return []
    
    # --- Assemble report ---
    report = {
        'metadata': {
            'baseline_start': baseline_start.strftime('%m-%d-%Y'),
            'baseline_end': baseline_end.strftime('%m-%d-%Y'),
            'treatment_start': treatment_start.strftime('%m-%d-%Y'),
            'treatment_end': treatment_end.strftime('%m-%d-%Y'),
            'total_features_analyzed': len(feature_cols)
        },
        'surge_categories': [],
        'slump_categories': [],
        'summary': {
            'surge': {'total_categories': 10,
                      'total_intensive_drivers': 30,
                      'total_extensive_drivers': 30},
            'slump': {'total_categories': 10,
                      'total_intensive_drivers': 30,
                      'total_extensive_drivers': 30}
        }
    }
    
    for group_name, cat_list, is_surge in [
        ('surge_categories', surge_cats, True),
        ('slump_categories', slump_cats, False)
    ]:
        for cat in cat_list:
            anom_idx = float(
                anomaly_df[anomaly_df['Category'] == cat]['Anomaly_Index'].values[0]
            )
            
            # Intensive data for this category
            cat_int = intensive_data[intensive_data['Category'] == cat]
            baseline_spend = cat_int[cat_int['Period'] == 'baseline']
            treatment_spend = cat_int[cat_int['Period'] == 'treatment']
            
            # Extensive data for this category
            cat_ext = extensive_data[extensive_data['Category'] == cat]
            
            n_purchasers_baseline = baseline_spend[id_col].nunique()
            n_purchasers_treatment = treatment_spend[id_col].nunique()
            n_at_risk = len(all_users)
            
            cat_entry = {
                'category': cat,
                'anomaly_index': round(anom_idx, 2),
                'baseline_avg_spend': round(
                    float(baseline_spend['Total_Spend'].mean()) 
                    if len(baseline_spend) > 0 else 0, 2
                ),
                'treatment_avg_spend': round(
                    float(treatment_spend['Total_Spend'].mean()) 
                    if len(treatment_spend) > 0 else 0, 2
                ),
                'n_purchasers_baseline': int(n_purchasers_baseline),
                'n_purchasers_treatment': int(n_purchasers_treatment),
                'baseline_purchase_rate': round(
                    n_purchasers_baseline / n_at_risk, 4
                ),
                'treatment_purchase_rate': round(
                    n_purchasers_treatment / n_at_risk, 4
                ),
                'n_at_risk': int(n_at_risk),
                'intensive_margin': run_intensive_did(
                    cat, cat_int, features_df, feature_cols, is_surge
                ),
                'extensive_margin': run_extensive_did(
                    cat, cat_ext, features_df, feature_cols, is_surge
                )
            }
            report[group_name].append(cat_entry)
    
    with open(f'{output_dir}/causal_analysis_report.json', 'w') as f:
        json.dump(report, f, indent=2)
    
    print("Causal analysis complete")
    return report
```

---

## Common Pitfalls

### 1. Using Z-scores or simple percentage changes for anomaly detection (WILL FAIL)
The task explicitly says "make counterfactual predictions on daily sales using all historical data." Tests validate that you used a proper forecasting model (Prophet). Simple statistical measures like Z-scores on month-over-month growth rates will fail `test_anomaly_detection_correctness`.

### 2. Forgetting to fill missing dates with zero spending
Prophet needs a continuous daily time series. If a category has no sales on certain days, those days must be filled with `y=0`. Without this, the model trains on a sparse series and produces poor forecasts.

### 3. Wrong anomaly index normalization
The index must range from -100 to 100. Use `tanh` scaling: `100 * tanh(pct_deviation / max_abs_deviation * 2)`. This ensures the range is bounded and the distribution is reasonable. Do NOT use min-max scaling or raw percentages.

### 4. Extensive margin grid must include ALL users
The extensive margin measures whether purchase *probability* changed. You need a full cross-product: every survey user × every top category × both periods. Users who didn't purchase get `Has_Purchase = 0`. If you only include purchasers, you're measuring intensive margin again.

### 5. Household size encoding errors
Survey data commonly has household size values encoded as numbers 31–60 instead of 1/2/3/4+. Check `value_counts()` — if you see values in the 30–60 range, map them: 31→"1", 32→"2", 33→"3", 34+→"4+".

### 6. Sorting direction for surge vs. slump drivers
- Surge categories: sort intensive/extensive drivers by `did_estimate` **descending** (largest positive effects first)
- Slump categories: sort by `did_estimate` **ascending** (largest negative effects first)

### 7. Date format in metadata
The JSON report expects `MM-DD-YYYY` format (e.g., `01-01-2020`), not ISO format. Use `strftime('%m-%d-%Y')`.

### 8. Method labels must be exact strings
- Intensive margin: `"Univariate DiD"`
- Extensive margin: `"Multivariate Heterogeneous DiD"`

### 9. Read the test file first
Before implementing, read `/app/tests/test_outputs.py` to understand exactly what each test validates. This saves hours of debugging. The tests check column names, data shapes, value ranges, and specific methodological choices.

### 10. Feature count consistency
The `total_features_analyzed` in metadata must match the actual number of engineered feature columns (excluding the ID column). The `total_intensive_drivers` and `total_extensive_drivers` should each be `10 categories × 3 drivers = 30`.

---

## Reference Implementation

This is the complete, end-to-end pipeline. Copy, adapt column names to your dataset, and run.

```python
#!/usr/bin/env python3
"""
Complete E-Commerce Trend Anomaly Detection & Causal Inference Pipeline.

Stages:
  1. Clean survey data
  2. Clean purchase data
  3. Anomaly detection via Prophet counterfactual forecasting
  4. Feature engineering on survey demographics
  5. Difference-in-Differences causal analysis (intensive + extensive margins)

Outputs saved to OUTPUT_DIR.
"""

import pandas as pd
import numpy as np
import json
import warnings
import os
from itertools import product

import statsmodels.api as sm
from prophet import Prophet

warnings.filterwarnings('ignore')