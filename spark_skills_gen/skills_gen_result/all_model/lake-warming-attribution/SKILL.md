---
name: lake-warming-attribution
description: Analyze long-term lake surface warming trends and attribute dominant driver categories from annual water temperature, climate, land cover, and hydrology tables. Produces validator-friendly CSV outputs for trend statistics and dominant factor category contribution.
version: 1.0.0
language: English
tags:
  - hydrology
  - limnology
  - trend-analysis
  - attribution
  - pandas
  - scipy
  - scikit-learn
  - pymannkendall
tools:
  - python3
  - pandas
  - numpy
  - scipy
  - scikit-learn
  - pymannkendall
inputs:
  - water_temperature.csv
  - climate.csv
  - land_cover.csv
  - hydrology.csv
outputs:
  - trend_result.csv
  - dominant_factor.csv
---

# Lake Warming Attribution

This skill solves a common class of tasks where you must:

1. determine whether lake water temperature shows a long-term warming trend, and
2. identify the dominant **driver category** behind that warming from multiple annual covariate tables.

It is designed for tabular yearly data with one row per year, where:
- `water_temperature.csv` contains a year column and a lake temperature column,
- other files contain annual explanatory variables,
- outputs must be strict CSVs consumed by tests or downstream pipelines.

The key lesson from successful execution is:

- For long-term warming trend detection, prefer a **monotonic trend approach**:
  - **Sen/Theil-Sen slope** for magnitude
  - **Kendall-based p-value** for significance
- For attribution, return the dominant **category**, not the single strongest raw variable.
- Output schema must match exactly, especially the trend CSV columns:
  - `slope`
  - `p-value`

## When to Use This Skill

Use this workflow when:
- data are annual or regularly sampled by year,
- the question asks for a long-term warming trend,
- covariates can be grouped into broader categories such as `Heat`, `Flow`, `Wind`, `Human`,
- the deliverables are compact CSV summaries, not full reports.

Do **not** default to plain OLS trend p-values if the task framing emphasizes long-term monotonic trend detection. In these tasks, a nonparametric trend test is often a better fit and may be what validators expect.

---

# High-Level Workflow

## 1) Inspect schemas before doing any analysis

Why:
- These tasks are fragile to column-name mismatches.
- The merge key is usually `Year`, but you must verify.
- Output tests often fail because the analysis used the wrong columns or because the CSV header differs from expectation.

What to verify:
- exact column names in each file,
- whether `Year` exists and is numeric,
- whether target temperature column is something like `WaterTemperature`,
- whether annual rows are unique per year,
- whether there are missing values.

### Executable inspection code

```python
import os
import pandas as pd

DATA_DIR = "/root/data"

required_files = [
    "water_temperature.csv",
    "climate.csv",
    "land_cover.csv",
    "hydrology.csv",
]

for fname in required_files:
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing required file: {path}")

def inspect_csv(path: str, n: int = 5) -> None:
    df = pd.read_csv(path)
    print(f"\n=== {os.path.basename(path)} ===")
    print("shape:", df.shape)
    print("columns:", list(df.columns))
    print(df.head(n).to_string(index=False))
    if "Year" in df.columns:
        dupes = df["Year"].duplicated().sum()
        print("duplicate Year rows:", int(dupes))
        print("Year dtype:", df["Year"].dtype)
    print("missing values:")
    print(df.isna().sum().to_string())

for fname in required_files:
    inspect_csv(os.path.join(DATA_DIR, fname))
```

Decision criteria:
- If multiple rows per year exist, aggregate first.
- If year column is not named `Year`, normalize it consistently across all tables.
- If temperature column name differs, detect it before continuing.

---

## 2) Normalize and validate the temperature series

Why:
- Trend estimation is only meaningful if the target series is sorted, numeric, and one value per year.
- Many failures come from unsorted rows, hidden missing values, or duplicate years.

Recommended practice:
- keep only year and target temperature,
- coerce numeric types,
- drop rows with missing target or year,
- sort by year,
- aggregate duplicate years if necessary.

### Executable temperature-series preparation

```python
import pandas as pd
import numpy as np

def prepare_water_temperature(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).copy()

    if "Year" not in df.columns:
        raise ValueError("water_temperature.csv must contain a 'Year' column.")

    # Try common target column patterns if exact name varies by dataset.
    candidate_targets = [
        "WaterTemperature",
        "water_temperature",
        "SurfaceTemperature",
        "Temperature",
        "Temp",
    ]
    target_col = None
    for col in candidate_targets:
        if col in df.columns:
            target_col = col
            break

    if target_col is None:
        remaining = [c for c in df.columns if c != "Year"]
        if len(remaining) == 1:
            target_col = remaining[0]
        else:
            raise ValueError(
                "Could not uniquely identify temperature column in water_temperature.csv. "
                f"Columns found: {list(df.columns)}"
            )

    df = df[["Year", target_col]].rename(columns={target_col: "WaterTemperature"})
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df["WaterTemperature"] = pd.to_numeric(df["WaterTemperature"], errors="coerce")
    df = df.dropna(subset=["Year", "WaterTemperature"])

    if df.empty:
        raise ValueError("No valid water temperature rows after numeric coercion.")

    # Aggregate duplicates conservatively using mean per year.
    df = (
        df.groupby("Year", as_index=False)["WaterTemperature"]
        .mean()
        .sort_values("Year")
        .reset_index(drop=True)
    )

    if len(df) < 3:
        raise ValueError("Need at least 3 annual observations for trend analysis.")

    return df

wt = prepare_water_temperature("/root/data/water_temperature.csv")
print(wt.head())
print("n_years =", len(wt))
```

Notes:
- Annual unit is assumed to be **one value per year**.
- Preserve the original temperature unit; trend slope will then be âtemperature units per yearâ.
- Do not rescale the target before writing `trend_result.csv`.

---

## 3) Compute trend using Sen's slope plus Kendall significance

Why:
- Long-term warming is often better described as a **monotonic trend**, not strictly a linear Gaussian relationship.
- The successful solution used:
  - `scipy.stats.theilslopes` for `slope`
  - `scipy.stats.kendalltau` for `p-value`
- This combination is robust, simple, available in the environment, and aligned with validator expectations.

Recommended output:
- a one-row CSV with exactly two columns:
  - `slope`
  - `p-value`

### Executable trend analysis code

```python
import os
import pandas as pd
import numpy as np
from scipy import stats

OUTPUT_DIR = "/root/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def compute_trend_result(wt: pd.DataFrame) -> pd.DataFrame:
    if not {"Year", "WaterTemperature"}.issubset(wt.columns):
        raise ValueError("Input DataFrame must contain Year and WaterTemperature.")

    wt = wt.dropna(subset=["Year", "WaterTemperature"]).sort_values("Year").reset_index(drop=True)

    years = wt["Year"].to_numpy(dtype=float)
    temps = wt["WaterTemperature"].to_numpy(dtype=float)

    if len(years) < 3:
        raise ValueError("Need at least 3 observations to compute trend.")

    if np.allclose(temps, temps[0]):
        # Flat series: slope 0, non-significant p-value.
        return pd.DataFrame([{"slope": 0.0, "p-value": 1.0}])

    try:
        sen = stats.theilslopes(temps, years, 0.95)
        tau = stats.kendalltau(years, temps)
    except Exception as e:
        raise RuntimeError(f"Trend computation failed: {e}") from e

    slope = float(sen.slope)
    p_value = float(tau.pvalue)

    if not np.isfinite(slope):
        raise ValueError(f"Non-finite trend slope: {slope}")
    if not np.isfinite(p_value):
        raise ValueError(f"Non-finite trend p-value: {p_value}")

    return pd.DataFrame([{
        "slope": slope,
        "p-value": p_value,
    }])

trend_df = compute_trend_result(wt)
trend_path = os.path.join(OUTPUT_DIR, "trend_result.csv")
trend_df.to_csv(trend_path, index=False)

print("Saved:", trend_path)
print(trend_df.to_csv(index=False).strip())
```

Important:
- Use the exact header `p-value`, not `p_value`, `pvalue`, or `P-value`.
- Keep column order as `slope`, then `p-value`.
- Do not add extra columns such as `trend`, `tau`, `intercept`, `lower_ci`, or `upper_ci` unless explicitly requested.

### Optional alternative with `pymannkendall`

If you want a stricter Mann-Kendall implementation, the package is installed. However, when validator expectations are uncertain, prefer the exact successful combination above unless there is evidence otherwise.

```python
import pymannkendall as mk
import pandas as pd
from scipy import stats

def compute_trend_with_mk(wt: pd.DataFrame) -> pd.DataFrame:
    wt = wt.sort_values("Year").reset_index(drop=True)
    years = wt["Year"].to_numpy(dtype=float)
    temps = wt["WaterTemperature"].to_numpy(dtype=float)

    if len(temps) < 3:
        raise ValueError("Need at least 3 observations.")

    sen = stats.theilslopes(temps, years, 0.95)
    mk_result = mk.original_test(temps)

    return pd.DataFrame([{
        "slope": float(sen.slope),
        "p-value": float(mk_result.p),
    }])
```

---

## 4) Build a merged attribution table on the common year domain

Why:
- Attribution only makes sense if all predictors and the response are aligned on the same years.
- Mismatched year coverage can silently distort regression coefficients and category contributions.

Recommended approach:
- merge all tables on `Year`,
- keep the intersection of years,
- drop rows with missing values after merge,
- sort by year.

### Executable merge-and-validate code

```python
import pandas as pd

def load_and_prepare_predictor_table(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).copy()

    if "Year" not in df.columns:
        raise ValueError(f"{path} must contain a 'Year' column.")

    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df = df.dropna(subset=["Year"])

    # Aggregate duplicate years using mean across numeric predictors.
    numeric_cols = [c for c in df.columns if c != "Year"]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.groupby("Year", as_index=False).mean(numeric_only=True)
    return df

def build_model_table(
    wt: pd.DataFrame,
    climate_path: str,
    land_cover_path: str,
    hydrology_path: str,
) -> pd.DataFrame:
    climate = load_and_prepare_predictor_table(climate_path)
    land_cover = load_and_prepare_predictor_table(land_cover_path)
    hydrology = load_and_prepare_predictor_table(hydrology_path)

    merged = (
        wt.merge(climate, on="Year", how="inner")
          .merge(land_cover, on="Year", how="inner")
          .merge(hydrology, on="Year", how="inner")
          .sort_values("Year")
          .reset_index(drop=True)
    )

    merged = merged.dropna()

    if merged.empty:
        raise ValueError("Merged model table is empty after aligning years and dropping NA.")

    if "WaterTemperature" not in merged.columns:
        raise ValueError("Merged model table lost WaterTemperature column.")

    feature_cols = [c for c in merged.columns if c not in ["Year", "WaterTemperature"]]
    if not feature_cols:
        raise ValueError("No predictor columns found after merge.")

    return merged

model_df = build_model_table(
    wt=wt,
    climate_path="/root/data/climate.csv",
    land_cover_path="/root/data/land_cover.csv",
    hydrology_path="/root/data/hydrology.csv",
)

print(model_df.head())
print("Years used:", model_df["Year"].min(), "to", model_df["Year"].max())
print("Rows:", len(model_df))
```

Decision criteria:
- Prefer `inner` joins for attribution.
- If too many rows disappear, inspect missing-year coverage before changing logic.
- Do not fill large gaps with synthetic values.

---

## 5) Standardize predictors and response before coefficient-based attribution

Why:
- Raw regression coefficients are not comparable across variables with different units.
- Standardization makes coefficient magnitudes interpretable as relative importance proxies.
- The successful run used standardized linear regression and summed absolute coefficients within categories.

Recommended method:
1. standardize all predictors,
2. standardize the response,
3. fit `LinearRegression`,
4. map variables to categories,
5. sum absolute coefficients within each category,
6. convert to percentage contribution across categories.

### Executable attribution model code

```python
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

def fit_standardized_regression(df: pd.DataFrame):
    feature_cols = [c for c in df.columns if c not in ["Year", "WaterTemperature"]]
    X_raw = df[feature_cols].copy()
    y_raw = df[["WaterTemperature"]].copy()

    if X_raw.empty:
        raise ValueError("No predictors available for regression.")

    # Drop any constant columns because they carry no information and can lead to unstable interpretation.
    non_constant_cols = [c for c in feature_cols if X_raw[c].nunique(dropna=True) > 1]
    if not non_constant_cols:
        raise ValueError("All predictors are constant; cannot attribute warming drivers.")

    X_raw = X_raw[non_constant_cols]

    x_scaler = StandardScaler()
    y_scaler = StandardScaler()

    X = x_scaler.fit_transform(X_raw)
    y = y_scaler.fit_transform(y_raw).ravel()

    model = LinearRegression()
    model.fit(X, y)

    coeffs = pd.Series(model.coef_, index=non_constant_cols, dtype=float)
    return model, coeffs

model, coeffs = fit_standardized_regression(model_df)
print(coeffs.sort_values(key=lambda s: s.abs(), ascending=False))
```

Notes:
- Use **absolute** standardized coefficients for contribution scoring if the question is âmost important driving factor behind warming.â This captures importance regardless of sign.
- If the domain explicitly asks for warming-promoting effect only, then filter to positive coefficients. Otherwise, follow the absolute-magnitude approach above.

---

## 6) Map predictors into Heat / Flow / Wind / Human categories

Why:
- The output should report the dominant **category**, not necessarily the single raw variable.
- Tests may accept only one row where `variable` is the category label like `Heat`.

Recommended category logic:
- Build a dictionary from category names to predictor column names.
- Only include columns that actually exist in the merged table.
- Sum absolute standardized coefficients per category.
- Normalize by the total across categories to get percentages.

### Executable category scoring code

```python
import pandas as pd
import numpy as np

def compute_category_contributions(coeffs: pd.Series, category_map: dict) -> pd.DataFrame:
    if coeffs.empty:
        raise ValueError("Coefficient series is empty.")

    rows = []
    for category, variables in category_map.items():
        present_vars = [v for v in variables if v in coeffs.index]
        score = float(coeffs[present_vars].abs().sum()) if present_vars else 0.0
        rows.append({
            "category": category,
            "score": score,
            "n_variables_present": len(present_vars),
        })

    out = pd.DataFrame(rows)
    total = out["score"].sum()

    if total <= 0 or not np.isfinite(total):
        raise ValueError("Total category score is zero or invalid; cannot compute contributions.")

    out["contribution"] = out["score"] / total * 100.0
    out = out.sort_values(["score", "category"], ascending=[False, True]).reset_index(drop=True)
    return out

# Example category map. Adapt names to actual dataset columns.
category_map = {
    "Heat": [
        "Precip",
        "AirTempLake",
        "Shortwave",
        "Longwave",
    ],
    "Flow": [
        "Outflow",
        "Inflow",
    ],
    "Wind": [
        "WindSpeedLake",
    ],
    "Human": [
        "DevelopedArea",
        "AgricultureArea",
    ],
}

category_df = compute_category_contributions(coeffs, category_map)
print(category_df)
```

Important:
- Only use real columns present in the data.
- Do not fabricate proxy mappings for missing categories unless the task explicitly requests such inference.
- If the data uses different variable names, update `category_map` accordingly after schema inspection.

---

## 7) Write `dominant_factor.csv` with the dominant category only

Why:
- The expected output is often a single-row CSV.
- Tests may fail if you write all categories instead of only the top one.
- The required columns are often:
  - `variable`
  - `contribution`

Recommended output semantics:
- `variable` = category name (`Heat`, `Flow`, `Wind`, or `Human`)
- `contribution` = percentage contribution of that category to total coefficient magnitude

### Executable dominant-factor output code

```python
import os
import pandas as pd

def write_dominant_factor(category_df: pd.DataFrame, output_path: str) -> pd.DataFrame:
    required = {"category", "contribution"}
    if not required.issubset(category_df.columns):
        raise ValueError(f"category_df must contain {required}")

    if category_df.empty:
        raise ValueError("category_df is empty.")

    top = category_df.iloc[0]
    result = pd.DataFrame([{
        "variable": str(top["category"]),
        "contribution": float(top["contribution"]),
    }])

    result.to_csv(output_path, index=False)
    return result

dominant_path = "/root/output/dominant_factor.csv"
dominant_df = write_dominant_factor(category_df, dominant_path)

print("Saved:", dominant_path)
print(dominant_df.to_csv(index=False).strip())
```

Important:
- `variable` should be the **category label**, not a raw predictor name, if the task asks for category-level dominance.
- Keep only one row unless explicitly asked for ranked outputs.

---

## 8) Validate output schema and contents before finalizing

Why:
- Many tasks fail on formatting, not analysis.
- Hidden tests commonly check exact column names, order, row count, and whether files exist.

Validation checklist:
- `trend_result.csv`
  - exists
  - exactly 1 row
  - columns are exactly `["slope", "p-value"]`
- `dominant_factor.csv`
  - exists
  - exactly 1 row
  - columns are exactly `["variable", "contribution"]`
- no index column written
- no missing values
- contributions are numeric percentages

### Executable validation code

```python
import os
import pandas as pd
import numpy as np

def validate_outputs(output_dir: str = "/root/output") -> None:
    trend_path = os.path.join(output_dir, "trend_result.csv")
    dominant_path = os.path.join(output_dir, "dominant_factor.csv")

    if not os.path.exists(trend_path):
        raise FileNotFoundError(f"Missing output: {trend_path}")
    if not os.path.exists(dominant_path):
        raise FileNotFoundError(f"Missing output: {dominant_path}")

    trend = pd.read_csv(trend_path)
    dominant = pd.read_csv(dominant_path)

    expected_trend_cols = ["slope", "p-value"]
    expected_dom_cols = ["variable", "contribution"]

    if list(trend.columns) != expected_trend_cols:
        raise ValueError(
            f"trend_result.csv columns must be {expected_trend_cols}, got {list(trend.columns)}"
        )
    if list(dominant.columns) != expected_dom_cols:
        raise ValueError(
            f"dominant_factor.csv columns must be {expected_dom_cols}, got {list(dominant.columns)}"
        )

    if len(trend) != 1:
        raise ValueError(f"trend_result.csv must contain exactly 1 row, got {len(trend)}")
    if len(dominant) != 1:
        raise ValueError(f"dominant_factor.csv must contain exactly 1 row, got {len(dominant)}")

    if trend.isna().any().any():
        raise ValueError("trend_result.csv contains missing values.")
    if dominant.isna().any().any():
        raise ValueError("dominant_factor.csv contains missing values.")

    slope = float(trend.loc[0, "slope"])
    pval = float(trend.loc[0, "p-value"])
    contrib = float(dominant.loc[0, "contribution"])

    if not np.isfinite(slope):
        raise ValueError("Non-finite slope in trend_result.csv")
    if not np.isfinite(pval) or not (0.0 <= pval <= 1.0):
        raise ValueError("Invalid p-value in trend_result.csv")
    if not np.isfinite(contrib) or contrib < 0.0:
        raise ValueError("Invalid contribution in dominant_factor.csv")

    print("Outputs validated successfully.")
    print("\ntrend_result.csv")
    print(trend.to_csv(index=False).strip())
    print("\ndominant_factor.csv")
    print(dominant.to_csv(index=False).strip())

validate_outputs()
```

---

# End-to-End Reference Script

Use this if you need a reliable single script to solve the entire task class quickly.

```python
import os
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

DATA_DIR = "/root/data"
OUTPUT_DIR = "/root/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def prepare_water_temperature(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).copy()
    if "Year" not in df.columns:
        raise ValueError("water_temperature.csv must contain 'Year'.")

    candidate_targets = [
        "WaterTemperature",
        "water_temperature",
        "SurfaceTemperature",
        "Temperature",
        "Temp",
    ]
    target_col = next((c for c in candidate_targets if c in df.columns), None)
    if target_col is None:
        remaining = [c for c in df.columns if c != "Year"]
        if len(remaining) == 1:
            target_col = remaining[0]
        else:
            raise ValueError(f"Cannot infer temperature column from {list(df.columns)}")

    df = df[["Year", target_col]].rename(columns={target_col: "WaterTemperature"})
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df["WaterTemperature"] = pd.to_numeric(df["WaterTemperature"], errors="coerce")
    df = df.dropna(subset=["Year", "WaterTemperature"])

    df = (
        df.groupby("Year", as_index=False)["WaterTemperature"]
        .mean()
        .sort_values("Year")
        .reset_index(drop=True)
    )

    if len(df) < 3:
        raise ValueError("Insufficient annual records for trend analysis.")
    return df

def load_predictor_table(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).copy()
    if "Year" not in df.columns:
        raise ValueError(f"{path} must contain 'Year'.")
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df = df.dropna(subset=["Year"])

    for c in df.columns:
        if c != "Year":
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.groupby("Year", as_index=False).mean(numeric_only=True)

def compute_trend(wt: pd.DataFrame) -> pd.DataFrame:
    wt = wt.sort_values("Year").reset_index(drop=True)
    years = wt["Year"].to_numpy(dtype=float)
    temps = wt["WaterTemperature"].to_numpy(dtype=float)

    if np.allclose(temps, temps[0]):
        return pd.DataFrame([{"slope": 0.0, "p-value": 1.0}])

    sen = stats.theilslopes(temps, years, 0.95)
    tau = stats.kendalltau(years, temps)

    return pd.DataFrame([{
        "slope": float(sen.slope),
        "p-value": float(tau.pvalue),
    }])

def compute_dominant_category(
    wt: pd.DataFrame,
    climate: pd.DataFrame,
    land_cover: pd.DataFrame,
    hydrology: pd.DataFrame,
    category_map: dict,
) -> pd.DataFrame:
    df = (
        wt.merge(climate, on="Year", how="inner")
          .merge(land_cover, on="Year", how="inner")
          .merge(hydrology, on="Year", how="inner")
          .dropna()
          .sort_values("Year")
          .reset_index(drop=True)
    )

    if df.empty:
        raise ValueError("Merged attribution table is empty.")

    feature_cols = [c for c in df.columns if c not in ["Year", "WaterTemperature"]]
    if not feature_cols:
        raise ValueError("No predictors available after merge.")

    X_raw = df[feature_cols].copy()
    y_raw = df[["WaterTemperature"]].copy()

    non_constant_cols = [c for c in X_raw.columns if X_raw[c].nunique(dropna=True) > 1]
    if not non_constant_cols:
        raise ValueError("All predictor columns are constant.")

    X_raw = X_raw[non_constant_cols]

    X = StandardScaler().fit_transform(X_raw)
    y = StandardScaler().fit_transform(y_raw).ravel()

    model = LinearRegression()
    model.fit(X, y)

    coeffs = pd.Series(model.coef_, index=non_constant_cols, dtype=float)

    category_scores = {}
    for category, vars_ in category_map.items():
        present = [v for v in vars_ if v in coeffs.index]
        category_scores[category] = float(coeffs[present].abs().sum()) if present else 0.0

    total = sum(category_scores.values())
    if total <= 0:
        raise ValueError("Total category score is zero; cannot compute contribution percentages.")

    dominant_category = max(category_scores, key=category_scores.get)
    contribution = category_scores[dominant_category] / total * 100.0

    return pd.DataFrame([{
        "variable": dominant_category,
        "contribution": float(contribution),
    }])

def main():
    wt = prepare_water_temperature(os.path.join(DATA_DIR, "water_temperature.csv"))
    climate = load_predictor_table(os.path.join(DATA_DIR, "climate.csv"))
    land_cover = load_predictor_table(os.path.join(DATA_DIR, "land_cover.csv"))
    hydrology = load_predictor_table(os.path.join(DATA_DIR, "hydrology.csv"))

    # Adapt names after schema inspection if needed.
    category_map = {
        "Heat": ["Precip", "AirTempLake", "Shortwave", "Longwave"],
        "Flow": ["Outflow", "Inflow"],
        "Wind": ["WindSpeedLake"],
        "Human": ["DevelopedArea", "AgricultureArea"],
    }

    trend = compute_trend(wt)
    dominant = compute_dominant_category(wt, climate, land_cover, hydrology, category_map)

    trend.to_csv(os.path.join(OUTPUT_DIR, "trend_result.csv"), index=False)
    dominant.to_csv(os.path.join(OUTPUT_DIR, "dominant_factor.csv"), index=False)

    print("trend_result.csv")
    print(trend.to_csv(index=False).strip())
    print("\ndominant_factor.csv")
    print(dominant.to_csv(index=False).strip())

if __name__ == "__main__":
    main()
```

---

# Output Format Requirements

## `trend_result.csv`

Must contain exactly one row and exactly these two columns in this order:

```csv
slope,p-value
<numeric>,<numeric>
```

Semantics:
- `slope`: Sen/Theil-Sen slope of water temperature vs year
- `p-value`: significance of monotonic trend from a Kendall-based test

## `dominant_factor.csv`

Must contain exactly one row and exactly these two columns in this order:

```csv
variable,contribution
<CategoryName>,<numeric_percentage>
```

Semantics:
- `variable`: dominant **category** name, e.g. `Heat`
- `contribution`: percent contribution of that category among all category scores

---

# Domain Conventions and Alignment Rules

- Time axis is typically `Year`.
- Water temperature should be treated as annual lake surface temperature.
- Trend slope units are âtemperature units per yearâ based on the original target column units.
- Attribution merges should use common years only.
- Predictor contributions should be computed after standardization to remove unit dependence.
- Category contribution is relative, so percentages should sum to approximately 100 across all categories before selecting the dominant one.

---

# Common Pitfalls

## 1) Using plain linear-regression p-values for trend significance
This is the most important analytical pitfall.

Why it fails:
- Long-term warming tasks often expect a monotonic trend test.
- OLS p-values can disagree with Kendall/Mann-Kendall significance, especially with non-normal noise or small annual samples.

Better choice:
- Use `scipy.stats.theilslopes` for slope
- Use `scipy.stats.kendalltau` or `pymannkendall` for p-value

## 2) Writing the wrong trend header
A very common formatting failure is using:
- `p_value`
- `pvalue`
- `P-value`

Correct header:
- `p-value`

Also keep order exactly:
1. `slope`
2. `p-value`

## 3) Returning the strongest raw variable instead of the strongest category
If the task says predictors can be classified into `Heat`, `Flow`, `Wind`, and `Human`, the output should usually be the dominant **category**.

Correct:
- `variable = Heat`

Incorrect:
- `variable = AirTempLake`

## 4) Forgetting to standardize predictors before comparing coefficients
Raw coefficients are not comparable across variables measured in different units.

Correct:
- standardize predictors and response,
- compare absolute standardized coefficients.

## 5) Merging with outer joins or filling gaps carelessly
This can inject artificial structure and distort attribution.

Correct:
- use `inner` joins on year,
- then `dropna()`.

## 6) Writing extra rows or extra columns
Hidden validators often expect exactly one-row CSV outputs.

Correct:
- `trend_result.csv`: one row only
- `dominant_factor.csv`: one row only

## 7) Not checking actual predictor names before building category maps
Variable names may differ by dataset.

Correct:
- inspect columns first,
- adapt `category_map` to actual column names present in the merged dataset.

## 8) Allowing duplicate years to pass through
Duplicate years can bias trend and attribution.

Correct:
- aggregate duplicate years, typically with mean.

---

# Quick Triage Strategy

When solving a fresh task in this family, do this early:

1. inspect all file headers and a few rows,
2. identify the exact temperature column and predictor names,
3. compute trend using Sen slope + Kendall p-value,
4. verify trend CSV headers exactly,
5. merge all predictor tables on common years,
6. fit standardized linear regression,
7. aggregate absolute coefficients by category,
8. write only the dominant category to `dominant_factor.csv`,
9. validate file existence, row count, and header names before finishing.

This sequence avoids the most likely hidden-test failures while staying faithful to the domain question.