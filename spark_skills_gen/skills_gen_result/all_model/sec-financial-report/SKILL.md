---
name: sec-13f-quarterly-comparison
description: Analyze SEC Form 13F quarter datasets stored as TSV exports to answer fund AUM, holdings-count, quarter-over-quarter position changes, and security-holder ranking questions. Covers fuzzy manager matching, accession resolution, summary-vs-holdings interpretation, CUSIP-based comparisons, and JSON answer generation.
tools: [python3, pandas, rapidfuzz, rg, sed, awk]
---

# SEC 13F Quarterly Comparison Analysis

This skill is for tasks where you must answer questions from SEC Form 13F quarterly datasets exported into flat files such as:

- `COVERPAGE.tsv`
- `SUMMARYPAGE.tsv`
- `INFOTABLE.tsv`
- `SUBMISSION.tsv`
- documentation like `FORM13F_readme.htm` or metadata JSON

Typical questions include:

- Find a manager by fuzzy name search and get its accession number
- Read AUM-like totals from the proper summary field
- Count how many holdings a filer reports
- Compare two quarters for the same manager and rank increased investments
- Find all managers holding a given stock CUSIP and rank them by value
- Emit answers in a strict JSON schema

The key challenge is **using the correct table and field for each question**. Many failures come from mixing up:

- cover page metadata vs holdings rows
- count of holdings rows vs count reported on summary page
- all rows vs deduplicated or amended filings
- share count changes vs dollar value changes
- issuer-name search vs CUSIP search

---

## Domain Conventions You Must Know

### 1) Form 13F table roles
In these datasets, the TSVs usually mean:

- **COVERPAGE.tsv**: filing-level metadata, manager name, accession number, filing date, etc.
- **SUMMARYPAGE.tsv**: summary metrics for a filing, such as:
  - total table value
  - total entry count
  - other reporting-manager counts
- **INFOTABLE.tsv**: security-level holdings rows for each accession number
- **SUBMISSION.tsv**: additional filing metadata and sometimes manager identity details

### 2) Value units
13F holdings `VALUE` fields are commonly reported in **thousands of dollars** in raw SEC format, but some preprocessed datasets may expose summary totals already scaled or preserved as raw SEC values.  
**Do not guess.** Always inspect the included readme/metadata and verify consistency between:

- `SUMMARYPAGE.TABLEVALUETOTAL`
- sum of `INFOTABLE.VALUE`

If they differ by exactly `1000x`, the dataset likely preserves SEC âvalue x $1000â convention in one or both places.

### 3) CUSIP usage
Tasks often expect **CUSIP strings as the final identifier** for stocks. Do not substitute ticker symbols unless explicitly asked.

### 4) Quarter-over-quarter comparisons
For âincreased investmentâ questions, the intended comparison is usually:

- same filer across two quarters
- compare holdings by **CUSIP**
- rank by **increase in dollar value**, not shares
- often only among positions present in both quarters unless task explicitly says ânew positions includedâ

You must inspect wording carefully.

---

## High-Level Workflow

1. **Inspect the dataset schema and documentation first.**  
   Why: You need to know what fields exist, whether values are scaled, and how holdings counts are intended.  
   Decision criteria:
   - If docs mention âvalue x $1000â, preserve that convention consistently.
   - If summary fields directly answer the question, prefer them over reconstructing from holdings rows.

2. **Load only the needed tables and normalize text columns.**  
   Why: These tables can be large. Avoid loading everything blindly if only one or two tables are required.
   Decision criteria:
   - Use `COVERPAGE.tsv` for fuzzy manager lookup
   - Use `SUMMARYPAGE.tsv` for filing-level totals/counts
   - Use `INFOTABLE.tsv` for security-level comparisons and ranking

3. **Resolve the correct accession number(s) using fuzzy manager matching.**  
   Why: Filing manager names vary in punctuation/case across filings and quarters.
   Decision criteria:
   - Search normalized manager names
   - Prefer the highest-quality fuzzy match
   - If multiple strong matches exist, validate by exact quarter folder and manager name inspection

4. **For AUM / total portfolio value questions, use the summary field first.**  
   Why: Tasks often mean the total reported 13F portfolio value, which is best represented by `SUMMARYPAGE.TABLEVALUETOTAL`.
   Decision criteria:
   - Do not derive from row counts or unrelated cover-page values
   - Cross-check against holdings sums only as validation

5. **For âhow many stocks are heldâ questions, prefer the summary count field over counting raw rows.**  
   Why: Raw row counts may overcount due to class-level or duplicated rows; summary page usually contains the filing's official holdings count.
   Decision criteria:
   - Use the summary âentry totalâ / âtable entry totalâ field if available
   - Only fall back to counting distinct CUSIPs in `INFOTABLE` if no summary count exists

6. **For quarter-over-quarter change analysis, aggregate by accession and CUSIP before comparing.**  
   Why: A security may appear multiple times due to class/reporting structure.
   Decision criteria:
   - Sum `VALUE` by CUSIP within each quarter
   - Compare Q3 minus Q2
   - Rank descending by increase
   - Only include positive changes
   - Decide whether to include newly initiated positions based on wording

7. **For âtop managers holding stock Xâ questions, resolve the stock CUSIP first, then rank holders by reported value.**  
   Why: Searching issuer names directly across all rows can miss spelling variants or class text.
   Decision criteria:
   - Find candidate rows in `INFOTABLE` by issuer name
   - confirm the CUSIP
   - then filter all rows by that CUSIP and aggregate value by accession/manager

8. **Validate every answer manually before writing JSON.**  
   Why: Common failures are numeric answers sourced from the wrong field.
   Decision criteria:
   - Check manager accession matches expected fund
   - Check summary total/count for the same accession
   - Check top-N ranking logic is consistent with task wording
   - Ensure output JSON uses exact schema and types

---

## Step 1: Inspect Schema and Readme

Start by discovering available files and reading documentation.

```bash
#!/usr/bin/env bash
set -euo pipefail

for quarter_dir in /root/2025-q2 /root/2025-q3; do
  echo "== Inspecting ${quarter_dir} =="
  ls -1 "${quarter_dir}" | sort
  echo
done

if [[ -f /root/2025-q3/FORM13F_readme.htm ]]; then
  echo "== Readme excerpts =="
  rg -n "value|thousand|TABLEVALUETOTAL|ENTRYTOTAL|CUSIP|manager" /root/2025-q3/FORM13F_readme.htm | head -n 50 || true
fi

for f in /root/2025-q3/COVERPAGE.tsv /root/2025-q3/SUMMARYPAGE.tsv /root/2025-q3/INFOTABLE.tsv /root/2025-q3/SUBMISSION.tsv; do
  if [[ -f "$f" ]]; then
    echo "== Head: $f =="
    head -n 3 "$f"
    echo
  fi
done
```

If you want to inspect column names safely in Python:

```python
import pandas as pd
from pathlib import Path

quarter_dir = Path("/root/2025-q3")
for name in ["COVERPAGE.tsv", "SUMMARYPAGE.tsv", "INFOTABLE.tsv", "SUBMISSION.tsv"]:
    path = quarter_dir / name
    if not path.exists():
        continue
    try:
        df = pd.read_csv(path, sep="\t", nrows=3, dtype=str)
        print(f"== {name} ==")
        print(df.columns.tolist())
        print(df.head(2).to_dict(orient="records"))
        print()
    except Exception as exc:
        print(f"Failed to inspect {path}: {exc}")
```

### What to verify here
- Which column contains accession numbers: often `ACCESSION_NUMBER`
- Which manager-name column exists in `COVERPAGE.tsv`: often `FILINGMANAGER_NAME`
- Which summary count field exists: often `TABLEENTRYTOTAL`
- Which summary value field exists: often `TABLEVALUETOTAL`
- Whether `INFOTABLE.VALUE` and summary totals use matching units

---

## Step 2: Load Tables Robustly

Use a reusable loader that normalizes columns and handles missing files.

```python
from pathlib import Path
import pandas as pd

def load_tsv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required TSV: {path}")
    try:
        df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, low_memory=False)
    except Exception as exc:
        raise RuntimeError(f"Failed reading {path}: {exc}") from exc
    df.columns = [c.strip().upper() for c in df.columns]
    return df

def require_columns(df: pd.DataFrame, required: list[str], table_name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"{table_name} missing required columns: {missing}")

def normalize_text(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.upper()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

quarter_dir = Path("/root/2025-q3")
cover = load_tsv(quarter_dir / "COVERPAGE.tsv")
summary = load_tsv(quarter_dir / "SUMMARYPAGE.tsv")
info = load_tsv(quarter_dir / "INFOTABLE.tsv")

require_columns(cover, ["ACCESSION_NUMBER"], "COVERPAGE")
require_columns(summary, ["ACCESSION_NUMBER"], "SUMMARYPAGE")
require_columns(info, ["ACCESSION_NUMBER", "CUSIP"], "INFOTABLE")
```

---

## Step 3: Resolve a Manager by Fuzzy Search

Use `rapidfuzz` to search manager names in `COVERPAGE.tsv`.

```python
from rapidfuzz import process, fuzz
import pandas as pd

def find_best_manager_match(
    cover_df: pd.DataFrame,
    search_term: str,
    manager_col: str = "FILINGMANAGER_NAME",
    min_score: int = 70
) -> dict:
    if manager_col not in cover_df.columns:
        raise KeyError(f"Column {manager_col} not found in COVERPAGE")
    work = cover_df.copy()
    work["_MANAGER_NORM"] = normalize_text(work[manager_col])

    choices = work["_MANAGER_NORM"].dropna().unique().tolist()
    if not choices:
        raise ValueError("No manager names available for fuzzy matching")

    result = process.extractOne(
        normalize_text(pd.Series([search_term])).iloc[0],
        choices,
        scorer=fuzz.WRatio
    )
    if not result:
        raise ValueError(f"No fuzzy match found for {search_term!r}")

    matched_name, score, _ = result
    if score < min_score:
        raise ValueError(f"Best match score too low for {search_term!r}: {matched_name=} {score=}")

    candidates = work.loc[work["_MANAGER_NORM"] == matched_name].copy()
    if candidates.empty:
        raise ValueError("Fuzzy match produced no candidate rows")

    # Prefer latest filing if multiple exist within same folder.
    # If there is a filing-date column, sort by it descending.
    for date_col in ["FILING_DATE", "REPORTCALENDARORQUARTER", "PERIODOFREPORT"]:
        if date_col in candidates.columns:
            candidates = candidates.sort_values(date_col, ascending=False, na_position="last")
            break

    row = candidates.iloc[0].to_dict()
    row["_MATCH_SCORE"] = score
    return row

match = find_best_manager_match(cover, "renaissance technologies")
print(match["ACCESSION_NUMBER"], match.get("FILINGMANAGER_NAME"))
```

### Important decision rule
If multiple candidate rows look valid:

- inspect them manually
- confirm quarter folder
- confirm manager name spelling
- if there are amendment filings, decide whether to use original or latest based on dataset/task wording

To inspect top candidates instead of only the best match:

```python
from rapidfuzz import process, fuzz

def top_manager_matches(cover_df: pd.DataFrame, search_term: str, n: int = 10, manager_col: str = "FILINGMANAGER_NAME"):
    work = cover_df.copy()
    work["_MANAGER_NORM"] = normalize_text(work[manager_col])
    choices = work["_MANAGER_NORM"].dropna().unique().tolist()
    scored = process.extract(
        normalize_text(pd.Series([search_term])).iloc[0],
        choices,
        scorer=fuzz.WRatio,
        limit=n,
    )
    return scored

print(top_manager_matches(cover, "berkshire hathaway", n=5))
```

---

## Step 4: Get AUM-Like Summary Total and Holdings Count

For these tasks, the intended âAUMâ is often the filing's total portfolio value as reported in `SUMMARYPAGE.TABLEVALUETOTAL`.

```python
import pandas as pd

def to_int_safe(value: str | int | float | None) -> int:
    if value is None:
        raise ValueError("Cannot convert None to int")
    text = str(value).strip().replace(",", "")
    if text == "":
        raise ValueError("Empty numeric field")
    try:
        return int(float(text))
    except Exception as exc:
        raise ValueError(f"Invalid integer value: {value!r}") from exc

def get_summary_row(summary_df: pd.DataFrame, accession_number: str) -> pd.Series:
    rows = summary_df.loc[summary_df["ACCESSION_NUMBER"] == accession_number]
    if rows.empty:
        raise KeyError(f"No SUMMARYPAGE row for accession {accession_number}")
    if len(rows) > 1:
        rows = rows.head(1)
    return rows.iloc[0]

def pick_first_existing(row: pd.Series, candidates: list[str]) -> str:
    for col in candidates:
        if col in row.index and str(row[col]).strip() != "":
            return str(row[col])
    raise KeyError(f"None of the candidate columns exist or contain data: {candidates}")

summary_row = get_summary_row(summary, accession_number="0000000000-00-000000")
aum_value = to_int_safe(pick_first_existing(summary_row, ["TABLEVALUETOTAL", "TABLE_VALUE_TOTAL"]))
holdings_count = to_int_safe(pick_first_existing(summary_row, ["TABLEENTRYTOTAL", "TABLE_ENTRY_TOTAL", "ENTRYTOTAL"]))

print({"aum": aum_value, "holdings_count": holdings_count})
```

### Why prefer summary count over raw row count
A repeated failure pattern in these tasks is using:

- raw `INFOTABLE` row count
- distinct `TITLEOFCLASS`
- distinct `CUSIP`

when the intended answer is the **official summary entry total**. Use the summary field first.

### Optional validation against infotable totals
This is useful for confidence, not as the primary source unless required.

```python
def aggregate_filing_value(info_df: pd.DataFrame, accession_number: str, value_col: str = "VALUE") -> int:
    if value_col not in info_df.columns:
        raise KeyError(f"INFOTABLE missing {value_col}")
    subset = info_df.loc[info_df["ACCESSION_NUMBER"] == accession_number].copy()
    if subset.empty:
        raise KeyError(f"No INFOTABLE rows for accession {accession_number}")
    subset[value_col] = subset[value_col].astype(str).str.replace(",", "", regex=False)
    subset[value_col] = pd.to_numeric(subset[value_col], errors="coerce").fillna(0)
    return int(subset[value_col].sum())

filing_total = aggregate_filing_value(info, accession_number="0000000000-00-000000")
print(filing_total)
```

---

## Step 5: Compare Two Quarters for Increased Investment

For âtop stocks with increased investmentâ:

- resolve the manager accession for each quarter
- aggregate holdings by `CUSIP`
- compare **VALUE** between quarters
- sort descending by `q3_value - q2_value`
- return CUSIPs

```python
from pathlib import Path
import pandas as pd

def aggregate_holdings_by_cusip(info_df: pd.DataFrame, accession_number: str) -> pd.DataFrame:
    required = ["ACCESSION_NUMBER", "CUSIP", "VALUE"]
    require_columns(info_df, required, "INFOTABLE")
    subset = info_df.loc[info_df["ACCESSION_NUMBER"] == accession_number, ["CUSIP", "VALUE"]].copy()
    if subset.empty:
        raise KeyError(f"No holdings for accession {accession_number}")

    subset["CUSIP"] = subset["CUSIP"].astype(str).str.strip()
    subset["VALUE"] = pd.to_numeric(
        subset["VALUE"].astype(str).str.replace(",", "", regex=False),
        errors="coerce"
    ).fillna(0)

    agg = subset.groupby("CUSIP", as_index=False)["VALUE"].sum()
    return agg

def top_increased_positions(
    q2_info: pd.DataFrame,
    q3_info: pd.DataFrame,
    q2_accession: str,
    q3_accession: str,
    top_n: int = 5,
    include_new_positions: bool = False
) -> list[str]:
    q2 = aggregate_holdings_by_cusip(q2_info, q2_accession).rename(columns={"VALUE": "VALUE_Q2"})
    q3 = aggregate_holdings_by_cusip(q3_info, q3_accession).rename(columns={"VALUE": "VALUE_Q3"})

    merged = q3.merge(q2, on="CUSIP", how="left")
    merged["VALUE_Q2"] = merged["VALUE_Q2"].fillna(0)

    if not include_new_positions:
        merged = merged.loc[merged["CUSIP"].isin(set(q2["CUSIP"]))].copy()

    merged["DELTA"] = merged["VALUE_Q3"] - merged["VALUE_Q2"]
    merged = merged.loc[merged["DELTA"] > 0].copy()
    merged = merged.sort_values(["DELTA", "CUSIP"], ascending=[False, True])

    return merged["CUSIP"].head(top_n).tolist()
```

### Decision rule: include new positions or not?
Use task wording:

- âreceived increased investmentâ usually means **existing holdings increased**
- âlargest additions / biggest buysâ may include newly initiated positions
- If ambiguous, inspect examples/tests if available, but do not guess silently

### Debugging the comparison
Before finalizing, print a comparison table:

```python
def explain_top_changes(
    q2_info: pd.DataFrame,
    q3_info: pd.DataFrame,
    q2_accession: str,
    q3_accession: str,
    limit: int = 10
) -> pd.DataFrame:
    q2 = aggregate_holdings_by_cusip(q2_info, q2_accession).rename(columns={"VALUE": "VALUE_Q2"})
    q3 = aggregate_holdings_by_cusip(q3_info, q3_accession).rename(columns={"VALUE": "VALUE_Q3"})
    merged = q3.merge(q2, on="CUSIP", how="outer").fillna(0)
    merged["DELTA"] = merged["VALUE_Q3"] - merged["VALUE_Q2"]
    merged = merged.sort_values("DELTA", ascending=False)
    return merged.head(limit)

print(explain_top_changes(q2_info, q3_info, q2_accession, q3_accession, limit=15))
```

---

## Step 6: Find Top Managers Holding a Given Stock

The robust method is:

1. Find candidate rows in `INFOTABLE` by issuer name
2. Confirm the target `CUSIP`
3. Filter all rows for that `CUSIP`
4. Aggregate `VALUE` by accession
5. Attach manager names from `COVERPAGE`

```python
import pandas as pd

def resolve_cusip_for_issuer(info_df: pd.DataFrame, issuer_search: str, issuer_col: str = "NAMEOFISSUER") -> pd.DataFrame:
    if issuer_col not in info_df.columns or "CUSIP" not in info_df.columns:
        raise KeyError(f"INFOTABLE must contain {issuer_col} and CUSIP")
    work = info_df[[issuer_col, "CUSIP"]].copy()
    work["_ISSUER_NORM"] = normalize_text(work[issuer_col])
    target = normalize_text(pd.Series([issuer_search])).iloc[0]
    matches = work.loc[work["_ISSUER_NORM"].str.contains(target, regex=False, na=False)].copy()
    if matches.empty:
        raise ValueError(f"No issuer match found for {issuer_search!r}")
    return matches.drop_duplicates().sort_values(["CUSIP", issuer_col])

def rank_holders_by_cusip(info_df: pd.DataFrame, cover_df: pd.DataFrame, cusip: str, top_n: int = 3) -> list[str]:
    require_columns(info_df, ["ACCESSION_NUMBER", "CUSIP", "VALUE"], "INFOTABLE")
    require_columns(cover_df, ["ACCESSION_NUMBER", "FILINGMANAGER_NAME"], "COVERPAGE")

    subset = info_df.loc[info_df["CUSIP"].astype(str).str.strip() == cusip].copy()
    if subset.empty:
        raise ValueError(f"No rows found for CUSIP {cusip}")

    subset["VALUE"] = pd.to_numeric(
        subset["VALUE"].astype(str).str.replace(",", "", regex=False),
        errors="coerce"
    ).fillna(0)

    ranked = subset.groupby("ACCESSION_NUMBER", as_index=False)["VALUE"].sum()
    ranked = ranked.sort_values(["VALUE", "ACCESSION_NUMBER"], ascending=[False, True])

    names = cover_df[["ACCESSION_NUMBER", "FILINGMANAGER_NAME"]].drop_duplicates()
    ranked = ranked.merge(names, on="ACCESSION_NUMBER", how="left")

    result = ranked["FILINGMANAGER_NAME"].fillna("").astype(str).str.strip()
    result = result[result != ""].head(top_n).tolist()
    if len(result) < top_n:
        raise ValueError(f"Only found {len(result)} ranked managers for CUSIP {cusip}")
    return result

issuer_candidates = resolve_cusip_for_issuer(info, "Palantir")
print(issuer_candidates.head(20))
```

### Ranking rule
Unless the task says otherwise, rank by **reported share value (`VALUE`)**, not share count (`SSHPRNAMT`).

---

## Step 7: Write Strict JSON Output

Always validate the exact schema.

```python
import json
from pathlib import Path

def write_answers_json(
    path: str | Path,
    q1_answer: int,
    q2_answer: int,
    q3_answer: list[str],
    q4_answer: list[str],
) -> None:
    payload = {
        "q1_answer": int(q1_answer),
        "q2_answer": int(q2_answer),
        "q3_answer": [str(x) for x in q3_answer],
        "q4_answer": [str(x) for x in q4_answer],
    }

    if len(payload["q3_answer"]) != 5:
        raise ValueError("q3_answer must contain exactly 5 CUSIPs")
    if len(payload["q4_answer"]) != 3:
        raise ValueError("q4_answer must contain exactly 3 manager names")

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

write_answers_json("/root/answers.json", 1, 2, ["a","b","c","d","e"], ["x","y","z"])
```

---

## Validation Checklist Before Finalizing

Run this checklist every time:

1. **Manager match sanity**
   - Does the matched manager name actually correspond to the search term?
   - Is the accession from the correct quarter folder?

2. **AUM/portfolio total sanity**
   - Did you use `SUMMARYPAGE.TABLEVALUETOTAL` or the task's true intended summary field?
   - Did you avoid confusing row-level values with summary totals?

3. **Holdings count sanity**
   - Did you use the summary entry total, not raw `INFOTABLE` row count?
   - If you fell back to counting rows, can you justify why?

4. **Quarter comparison sanity**
   - Did you compare by CUSIP?
   - Did you rank by dollar-value increase, not shares?
   - Did you correctly handle whether new positions should be included?

5. **Holder ranking sanity**
   - Did you resolve the CUSIP for the issuer first?
   - Did you aggregate by manager/accession before ranking?

6. **Output sanity**
   - Is the output JSON at the required location?
   - Are types correct: numbers vs strings vs arrays?
   - Are array lengths exact?

---

## Common Pitfalls

These are the mistakes most likely to fail tests in this task family.

### 1) Counting raw holdings rows instead of using summary entry total
This is the most frequent failure.  
A filing's official ânumber of stocks heldâ is often represented by a summary-page field like `TABLEENTRYTOTAL`.  
Counting `INFOTABLE` rows can produce a larger number due to duplicates, classes, or filing structure.

**Avoid:**  
```python
wrong_count = len(info_df[info_df["ACCESSION_NUMBER"] == accession])
```

**Prefer:**  
```python
right_count = to_int_safe(summary_row["TABLEENTRYTOTAL"])
```

---

### 2) Ranking Berkshire-style quarter changes by shares instead of value
Tasks often say âranked by dollar value increase.â That means compare `VALUE`, not `SSHPRNAMT`.

**Avoid:** comparing on share counts unless explicitly requested.

---

### 3) Including new positions when the wording implies increased existing holdings
If the task says âreceived increased investment,â the expected result may exclude positions absent in the prior quarter.

**Safer approach:** add a parameter like `include_new_positions=False` and decide intentionally.

---

### 4) Guessing AUM from the wrong source
Do not use:

- arbitrary metadata fields
- row count
- raw manual guesses
- unverified `SUBMISSION` fields

For these datasets, âAUMâ in task wording often maps to the filing's total reported portfolio value from `SUMMARYPAGE`.

---

### 5) Searching issuer names only, without resolving final CUSIP
Issuer names can vary by punctuation/case/class descriptions.  
Find candidate issuer rows, inspect candidate CUSIPs, then rank holders by exact CUSIP.

---

### 6) Failing to aggregate by CUSIP before comparing or ranking
A CUSIP may appear multiple times for one accession.  
Always aggregate `VALUE` by `CUSIP` within a filing before quarter comparison.

---

### 7) Ignoring unit conventions
If documentation says value fields are in thousands, preserve that convention consistently.  
Do not multiply or divide unless you verify that the task expects a different scale.

---

### 8) Hardcoding an answer after one failed test
A test mismatch often indicates a logic error, not just one bad number.  
Fix the sourcing logic, then revalidate all answers.

---

## Reference Implementation

The following script is a complete end-to-end solution template for this task class. It:

- loads quarter datasets
- fuzzy-matches managers
- retrieves summary total and entry count
- compares a manager's holdings across quarters by CUSIP/value
- resolves a target issuer's CUSIP and ranks holders
- writes `/root/answers.json`

Adapt only the search terms, quarter directories, and exact output mapping as needed.

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd
from rapidfuzz import process, fuzz


# ---------- Basic utilities ----------

def load_tsv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required TSV: {path}")
    try:
        df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, low_memory=False)
    except Exception as exc:
        raise RuntimeError(f"Failed reading {path}: {exc}") from exc
    df.columns = [c.strip().upper() for c in df.columns]
    return df


def require_columns(df: pd.DataFrame, required: Iterable[str], table_name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"{table_name} missing required columns: {missing}")


def normalize_text(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.upper()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def to_int_safe(value) -> int:
    if value is None:
        raise ValueError("Cannot convert None to int")
    text = str(value).strip().replace(",", "")
    if text == "":
        raise ValueError("Empty numeric field")
    try:
        return int(float(text))
    except Exception as exc:
        raise ValueError(f"Invalid integer value: {value!r}") from exc


def pick_first_existing(row: pd.Series, candidates: list[str]) -> str:
    for col in candidates:
        if col in row.index and str(row[col]).strip() != "":
            return str(row[col])
    raise KeyError(f"None of the candidate columns exist or contain data: {candidates}")


# ---------- Dataset wrappers ----------

class Quarter13F:
    def __init__(self, quarter_dir: str | Path):
        self.quarter_dir = Path(quarter_dir)
        if not self.quarter_dir.exists():
            raise FileNotFoundError(f"Quarter directory not found: {self.quarter_dir}")

        self.cover = load_tsv(self.quarter_dir / "COVERPAGE.tsv")
        self.summary = load_tsv(self.quarter_dir / "SUMMARYPAGE.tsv")
        self.info = load_tsv(self.quarter_dir / "INFOTABLE.tsv")

        require_columns(self.cover, ["ACCESSION_NUMBER"], "COVERPAGE")
        require_columns(self.summary, ["ACCESSION_NUMBER"], "SUMMARYPAGE")
        require_columns(self.info, ["ACCESSION_NUMBER", "CUSIP", "VALUE"], "INFOTABLE")

        self.manager_col = self._resolve_manager_column()
        self.summary_value_col = self._resolve_first_column(
            self.summary, ["TABLEVALUETOTAL", "TABLE_VALUE_TOTAL"]
        )
        self.summary_count_col = self._resolve_first_column(
            self.summary, ["TABLEENTRYTOTAL", "TABLE_ENTRY_TOTAL", "ENTRYTOTAL"]
        )

    @staticmethod
    def _resolve_first_column(df: pd.DataFrame, candidates: list[str]) -> str:
        for col in candidates:
            if col in df.columns:
                return col
        raise KeyError(f"Could not find any of expected columns: {candidates}")

    def _resolve_manager_column(self) -> str:
        candidates = [
            "FILINGMANAGER_NAME",
            "NAME",
            "MANAGER_NAME",
            "FORM13FFILENUMBER_NAME",
        ]
        for col in candidates:
            if col in self.cover.columns:
                return col
        raise KeyError(f"No recognized manager-name column in COVERPAGE: {self.cover.columns.tolist()}")

    def find_best_manager_match(self, search_term: str, min_score: int = 70) -> dict:
        work = self.cover.copy()
        work["_MANAGER_NORM"] = normalize_text(work[self.manager_col])

        choices = work["_MANAGER_NORM"].dropna().unique().tolist()
        if not choices:
            raise ValueError("No manager names available for fuzzy matching")

        result = process.extractOne(
            normalize_text(pd.Series([search_term])).iloc[0],
            choices,
            scorer=fuzz.WRatio,
        )
        if not result:
            raise ValueError(f"No fuzzy match found for {search_term!r}")

        matched_name, score, _ = result
        if score < min_score:
            raise ValueError(f"Best match score too low: search={search_term!r}, matched={matched_name!r}, score={score}")

        candidates = work.loc[work["_MANAGER_NORM"] == matched_name].copy()
        if candidates.empty:
            raise ValueError("Fuzzy match produced zero candidate rows")

        for date_col in ["FILING_DATE", "REPORTCALENDARORQUARTER", "PERIODOFREPORT"]:
            if date_col in candidates.columns:
                candidates = candidates.sort_values(date_col, ascending=False, na_position="last")
                break

        row = candidates.iloc[0].to_dict()
        row["_MATCH_SCORE"] = score
        return row

    def get_summary_row(self, accession_number: str) -> pd.Series:
        rows = self.summary.loc[self.summary["ACCESSION_NUMBER"] == accession_number]
        if rows.empty:
            raise KeyError(f"No SUMMARYPAGE row for accession {accession_number}")
        return rows.iloc[0]

    def get_filing_total_value(self, accession_number: str) -> int:
        row = self.get_summary_row(accession_number)
        return to_int_safe(row[self.summary_value_col])

    def get_filing_entry_count(self, accession_number: str) -> int:
        row = self.get_summary_row(accession_number)
        return to_int_safe(row[self.summary_count_col])

    def aggregate_holdings_by_cusip(self, accession_number: str) -> pd.DataFrame:
        subset = self.info.loc[self.info["ACCESSION_NUMBER"] == accession_number, ["CUSIP", "VALUE"]].copy()
        if subset.empty:
            raise KeyError(f"No INFOTABLE rows for accession {accession_number}")

        subset["CUSIP"] = subset["CUSIP"].astype(str).str.strip()
        subset["VALUE"] = pd.to_numeric(
            subset["VALUE"].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        ).fillna(0)

        agg = subset.groupby("CUSIP", as_index=False)["VALUE"].sum()
        return agg

    def aggregate_holders_for_cusip(self, cusip: str) -> pd.DataFrame:
        subset = self.info.loc[self.info["CUSIP"].astype(str).str.strip() == cusip].copy()
        if subset.empty:
            raise ValueError(f"No rows found for CUSIP {cusip}")

        subset["VALUE"] = pd.to_numeric(
            subset["VALUE"].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        ).fillna(0)

        ranked = subset.groupby("ACCESSION_NUMBER", as_index=False)["VALUE"].sum()
        names = self.cover[["ACCESSION_NUMBER", self.manager_col]].drop_duplicates()
        ranked = ranked.merge(names, on="ACCESSION_NUMBER", how="left")
        ranked = ranked.sort_values(["VALUE", "ACCESSION_NUMBER"], ascending=[False, True])
        return ranked

    def resolve_cusip_candidates_for_issuer(self, issuer_search: str) -> pd.DataFrame:
        issuer_col_candidates = ["NAMEOFISSUER", "ISSUERNAME", "ISSUER_NAME"]
        issuer_col = None
        for col in issuer_col_candidates:
            if col in self.info.columns:
                issuer_col = col
                break
        if issuer_col is None:
            raise KeyError("Could not find issuer-name column in INFOTABLE")

        work = self.info[[issuer_col, "CUSIP"]].copy()
        work["_ISSUER_NORM"] = normalize_text(work[issuer_col])
        target = normalize_text(pd.Series([issuer_search])).iloc[0]

        matches = work.loc[work["_ISSUER_NORM"].str.contains(target, regex=False, na=False)].copy()
        if matches.empty:
            raise ValueError(f"No issuer match found for {issuer_search!r}")

        matches = matches.drop_duplicates().sort_values(["CUSIP", issuer_col])
        return matches


# ---------- Cross-quarter analysis ----------

def top_increased_positions(
    q2: Quarter13F,
    q3: Quarter13F,
    q2_accession: str,
    q3_accession: str,
    top_n: int = 5,
    include_new_positions: bool = False,
) -> list[str]:
    q2_holdings = q2.aggregate_holdings_by_cusip(q2_accession).rename(columns={"VALUE": "VALUE_Q2"})
    q3_holdings = q3.aggregate_holdings_by_cusip(q3_accession).rename(columns={"VALUE": "VALUE_Q3"})

    merged = q3_holdings.merge(q2_holdings, on="CUSIP", how="left")
    merged["VALUE_Q2"] = merged["VALUE_Q2"].fillna(0)

    if not include_new_positions:
        merged = merged.loc[merged["CUSIP"].isin(set(q2_holdings["CUSIP"]))].copy()

    merged["DELTA"] = merged["VALUE_Q3"] - merged["VALUE_Q2"]
    merged = merged.loc[merged["DELTA"] > 0].copy()
    merged = merged.sort_values(["DELTA", "CUSIP"], ascending=[False, True])

    result = merged["CUSIP"].head(top_n).tolist()
    if len(result) < top_n:
        raise ValueError(f"Only found {len(result)} increasing positions, need {top_n}")
    return result


def rank_top_holders_by_issuer(
    quarter: Quarter13F,
    issuer_search: str,
    chosen_cusip: str | None = None,
    top_n: int = 3,
) -> tuple[str, list[str]]:
    candidates = quarter.resolve_cusip_candidates_for_issuer(issuer_search)

    if chosen_cusip is None:
        # If exactly one CUSIP candidate exists, use it automatically.
        unique_cusips = candidates["CUSIP"].astype(str).str.strip().unique().tolist()
        if len(unique_cusips) != 1:
            raise ValueError(
                f"Multiple CUSIPs found for issuer search {issuer_search!r}: {unique_cusips}. "
                "Pass chosen_cusip explicitly after inspecting candidates."
            )
        chosen_cusip = unique_cusips[0]

    ranked = quarter.aggregate_holders_for_cusip(chosen_cusip)
    manager_col = quarter.manager_col
    names = ranked[manager_col].fillna("").astype(str).str.strip()
    names = names[names != ""].head(top_n).tolist()
    if len(names) < top_n:
        raise ValueError(f"Only found {len(names)} holder names for CUSIP {chosen_cusip}")
    return chosen_cusip, names


# ---------- Output ----------

def write_answers_json(
    path: str | Path,
    q1_answer: int,
    q2_answer: int,
    q3_answer: list[str],
    q4_answer: list[str],
) -> None:
    payload = {
        "q1_answer": int(q1_answer),
        "q2_answer": int(q2_answer),
        "q3_answer": [str(x) for x in q3_answer],
        "q4_answer": [str(x) for x in q4_answer],
    }

    if len(payload["q3_answer"]) != 5:
        raise ValueError("q3_answer must contain exactly 5 items")
    if len(payload["q4_answer"]) != 3:
        raise ValueError("q4_answer must contain exactly 3 items")

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------- Main task template ----------

def main() -> None:
    # Adapt these directories and search terms for the specific task instance.
    q2_dir = Path("/root/2025-q2")
    q3_dir = Path("/root/2025-q3")

    renaissance_search = "renaissance technologies"
    berkshire_search = "berkshire hathaway"
    issuer_search = "Palantir"

    q2 = Quarter13F(q2_dir)
    q3 = Quarter13F(q3_dir)

    # Q1/Q2-style manager summary answers
    ren_q3_match = q3.find_best_manager_match(renaissance_search)
    ren_q3_accession = ren_q3_match["ACCESSION_NUMBER"]

    q1_answer = q3.get_filing_total_value(ren_q3_accession)
    q2_answer = q3.get_filing_entry_count(ren_q3_accession)

    # Q3-style quarter-over-quarter manager comparison
    berk_q2_match = q2.find_best_manager_match(berkshire_search)
    berk_q3_match = q3.find_best_manager_match(berkshire_search)
    berk_q2_accession = berk_q2_match["ACCESSION_NUMBER"]
    berk_q3_accession = berk_q3_match["ACCESSION_NUMBER"]

    # Default: exclude newly initiated positions unless task wording explicitly includes them.
    q3_answer = top_increased_positions(
        q2=q2,
        q3=q3,
        q2_accession=berk_q2_accession,
        q3_accession=berk_q3_accession,
        top_n=5,
        include_new_positions=False,
    )

    # Q4-style top holder ranking for a target issuer
    # First inspect candidates if multiple CUSIPs are possible.
    issuer_candidates = q3.resolve_cusip_candidates_for_issuer(issuer_search)
    unique_cusips = issuer_candidates["CUSIP"].astype(str).str.strip().unique().tolist()
    if not unique_cusips:
        raise ValueError(f"No CUSIP candidates found for issuer {issuer_search!r}")

    if len(unique_cusips) == 1:
        target_cusip = unique_cusips[0]
    else:
        # Choose explicitly after inspecting issuer_candidates in a real task.
        # Here we fail loudly rather than guessing.
        raise ValueError(
            f"Multiple candidate CUSIPs for issuer {issuer_search!r}: {unique_cusips}. "
            "Inspect issuer_candidates and set target_cusip explicitly."
        )

    _, q4_answer = rank_top_holders_by_issuer(
        quarter=q3,
        issuer_search=issuer_search,
        chosen_cusip=target_cusip,
        top_n=3,
    )

    # Final output
    write_answers_json(
        path="/root/answers.json",
        q1_answer=q1_answer,
        q2_answer=q2_answer,
        q3_answer=q3_answer,
        q4_answer=q4_answer,
    )

    # Optional debug prints
    print("Renaissance accession:", ren_q3_accession, ren_q3_match.get(q3.manager_col))
    print("Q1 total value:", q1_answer)
    print("Q2 entry count:", q2_answer)
    print("Berkshire Q2 accession:", berk_q2_accession, berk_q2_match.get(q2.manager_col))
    print("Berkshire Q3 accession:", berk_q3_accession, berk_q3_match.get(q3.manager_col))
    print("Top increased positions:", q3_answer)
    print("Target issuer CUSIP:", target_cusip)
    print("Top holders:", q4_answer)
    print("Wrote /root/answers.json")


if __name__ == "__main__":
    main()
```

---

## Fast Triage Strategy

When under time pressure, use this order:

1. Inspect readme and TSV headers
2. Fuzzy-match manager in `COVERPAGE`
3. Pull summary value/count from `SUMMARYPAGE`
4. Aggregate by `CUSIP` in `INFOTABLE` for comparisons
5. Resolve issuer CUSIP before ranking holders
6. Validate all answers before writing JSON

This path avoids the most common dead ends: scanning huge tables blindly, counting wrong fields, and ranking using the wrong metric.

---

## Final Reminder

For this class of tasks, the highest-probability correct path is:

- **Manager identity from `COVERPAGE`**
- **Portfolio total and holdings count from `SUMMARYPAGE`**
- **Security comparisons and holder ranking from `INFOTABLE`**
- **CUSIP as the canonical stock identifier**
- **Value-based ranking unless shares are explicitly requested**

If one numeric answer looks off, do not patch it manually first. Re-check whether you used the correct table and field.