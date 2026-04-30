---
skill_id: manufacturing-codebook-normalization
version: 1.0.0
task_category: manufacturing-codebook-normalization
description: Normalize hand-written defect reason texts from manufacturing test logs into standardized codebook entries, producing a structured JSON output with segment-level predictions, confidence scores, and rationales.
success_rate: 1.0
tags: [nlp, normalization, manufacturing, codebook, fuzzy-matching, json-output]
---

# Manufacturing Codebook Normalization

## Overview

Test engineers write defect reasons quickly — with typos, abbreviations, mixed Chinese/English, and non-standard phrasing. Your job is to map each raw reason text to the correct standardized code and label from the product's codebook, then emit a validated `solution.json`.

---

## Module 1: Data Exploration and Schema Understanding

### Step 1 — Read all inputs before writing any output

```bash
cat /app/data/test_center_logs.csv | head -20
ls /app/data/codebook_*.csv
```

For each codebook file:
```bash
cat /app/data/codebook_<PRODUCT_ID>.csv
```

Key things to extract:
- Log fields: `record_id`, `product_id`, `station`, `engineer_id`, `raw_reason_text`
- Codebook fields: `code`, `label` (and any synonym/category columns)
- Which product IDs map to which codebook files

### Step 2 — Understand the output schema

```json
{
  "records": [
    {
      "record_id": "",
      "product_id": "",
      "station": "",
      "engineer_id": "",
      "raw_reason_text": "",
      "normalized": [
        {
          "segment_id": "<record_id>-S<i>",
          "span_text": "<substring of raw_reason_text>",
          "pred_code": "<code from codebook or UNKNOWN>",
          "pred_label": "<label from codebook or empty string>",
          "confidence": 0.0,
          "rationale": ""
        }
      ]
    }
  ]
}
```

Critical constraints:
- `segment_id` starts at `S1`, not `S0`
- `span_text` must be a strict substring of `raw_reason_text` (exact character match)
- `pred_code` and `pred_label` must come from the product's own codebook, or be `"UNKNOWN"` / `""`
- `confidence` in `[0.0, 1.0]`; UNKNOWN entries must have lower confidence than known entries

---

## Module 2: Normalization Pipeline

### Segmentation

Split `raw_reason_text` into meaningful spans. Common delimiters in manufacturing logs:
- Semicolons, commas, Chinese punctuation (`；`, `，`, `、`)
- Newlines or slash separators
- If the text is a single phrase, treat it as one segment

```python
import re

def segment_text(raw_text):
    # Split on common delimiters while preserving spans as substrings
    parts = re.split(r'[;；,，、\n/]+', raw_text)
    segments = []
    cursor = 0
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Find the span in the original text (strict substring)
        idx = raw_text.find(part, cursor)
        if idx != -1:
            segments.append(part)
            cursor = idx + len(part)
    return segments if segments else [raw_text.strip()]
```

### Matching Strategy

Use a multi-signal scoring approach per segment:

```python
from difflib import SequenceMatcher

def score_candidate(span, code, label, synonyms=None):
    """Score how well a span matches a codebook entry."""
    span_lower = span.lower()
    label_lower = label.lower()
    
    # 1. Direct substring match (highest signal)
    if label_lower in span_lower or span_lower in label_lower:
        return 0.95
    
    # 2. Keyword overlap
    label_tokens = set(re.split(r'\W+', label_lower))
    span_tokens = set(re.split(r'\W+', span_lower))
    overlap = label_tokens & span_tokens
    if overlap:
        return 0.7 + 0.1 * min(len(overlap) / len(label_tokens), 1.0)
    
    # 3. Synonym/alias match
    if synonyms:
        for syn in synonyms.get(code, []):
            if syn.lower() in span_lower:
                return 0.75
    
    # 4. Fuzzy ratio fallback
    ratio = SequenceMatcher(None, span_lower, label_lower).ratio()
    return ratio * 0.6  # scale down fuzzy-only matches

def assign_code(span, codebook, synonyms=None, threshold=0.45):
    best_score = 0.0
    best_code = "UNKNOWN"
    best_label = ""
    
    for _, row in codebook.iterrows():
        score = score_candidate(span, row['code'], row['label'], synonyms)
        if score > best_score:
            best_score = score
            best_code = row['code']
            best_label = row['label']
    
    if best_score < threshold:
        return "UNKNOWN", "", best_score * 0.4  # confidence penalty for unknowns
    return best_code, best_label, best_score
```

### Semantic Alignment — Critical

The most common failure is assigning a code whose label category does not semantically match the span. Before finalizing any assignment:

1. Check that the assigned label's *category* aligns with the span's *failure mode*
2. Watch for false positives from short common words (e.g., a span about "current trend" should not match a "SHORT CIRCUIT" code just because it contains `短`)
3. When a span contains a modifier like "趋势" (trend), "疑似" (suspected), or "轻微" (slight), lower confidence accordingly

```python
# Example: suppress false SHORT matches when context is about current levels
def context_override(span, pred_code):
    """Return True if the prediction should be suppressed based on context."""
    false_positive_patterns = {
        'SHORT': [r'短路趋势', r'疑似短路'],  # trend/suspected — lower confidence
    }
    for code, patterns in false_positive_patterns.items():
        if pred_code == code:
            for pat in patterns:
                if re.search(pat, span):
                    return True  # flag for confidence reduction, not full rejection
    return False
```

### Confidence Calibration

Engineers validate confidence distributions. Follow these guidelines:

| Match type | Confidence range |
|---|---|
| Direct label match | 0.85 – 0.98 |
| Strong keyword overlap | 0.65 – 0.84 |
| Synonym/alias match | 0.55 – 0.74 |
| Fuzzy-only match | 0.45 – 0.60 |
| UNKNOWN (low score) | 0.10 – 0.35 |

Target metrics:
- Mean confidence for known predictions: ~0.85+
- Mean confidence for UNKNOWN: ~0.25–0.35
- Separation between known and unknown means: >0.5
- UNKNOWN rate: typically 5–10% of segments is reasonable

---

## Module 3: Output Generation and Validation

### Build the output

```python
import json, pandas as pd

def build_solution(logs_df, codebooks, synonyms=None):
    records = []
    for _, row in logs_df.iterrows():
        segments = segment_text(row['raw_reason_text'])
        product_codebook = codebooks[row['product_id']]
        normalized = []
        for i, span in enumerate(segments, start=1):
            code, label, conf = assign_code(span, product_codebook, synonyms)
            if context_override(span, code):
                conf *= 0.6  # reduce but don't zero out
            normalized.append({
                "segment_id": f"{row['record_id']}-S{i}",
                "span_text": span,
                "pred_code": code,
                "pred_label": label,
                "confidence": round(conf, 4),
                "rationale": f"Matched '{label}' via keyword overlap" if code != "UNKNOWN" else "No confident match found"
            })
        records.append({
            "record_id": row['record_id'],
            "product_id": row['product_id'],
            "station": row['station'],
            "engineer_id": row['engineer_id'],
            "raw_reason_text": row['raw_reason_text'],
            "normalized": normalized
        })
    return {"records": records}
```

### Pre-submission validation checklist

Run these checks before writing the file:

```python
def validate(solution, logs_df, codebooks):
    errors = []
    record_ids = set(logs_df['record_id'])
    
    for r in solution['records']:
        rid = r['record_id']
        raw = r['raw_reason_text']
        cb_codes = set(codebooks[r['product_id']]['code'])
        
        for s in r['normalized']:
            # span_text must be strict substring
            if s['span_text'] not in raw:
                errors.append(f"{rid}: span_text not in raw_reason_text")
            
            # pred_code must be from codebook or UNKNOWN
            if s['pred_code'] not in cb_codes and s['pred_code'] != 'UNKNOWN':
                errors.append(f"{rid}: invalid pred_code {s['pred_code']}")
            
            # UNKNOWN must have empty label
            if s['pred_code'] == 'UNKNOWN' and s['pred_label'] != '':
                errors.append(f"{rid}: UNKNOWN must have empty pred_label")
            
            # confidence range
            if not (0.0 <= s['confidence'] <= 1.0):
                errors.append(f"{rid}: confidence out of range")
            
            # segment_id format
            expected_prefix = f"{rid}-S"
            if not s['segment_id'].startswith(expected_prefix):
                errors.append(f"{rid}: bad segment_id format")
    
    # coverage: all log records must appear
    output_ids = {r['record_id'] for r in solution['records']}
    missing = record_ids - output_ids
    if missing:
        errors.append(f"Missing records: {missing}")
    
    return errors
```

---

## Common Pitfalls

1. **`span_text` not a strict substring** — Never clean or normalize the span before storing it. The span must be character-for-character present in `raw_reason_text`. Extract it by finding its position in the original string.

2. **`segment_id` starting at S0** — Always start at `S1`. The format is `<record_id>-S1`, `<record_id>-S2`, etc.

3. **Cross-product code assignment** — Each record's `pred_code` must come from that record's product codebook. Never mix codebooks across products.

4. **Semantic mismatch (T11-style failures)** — A structurally valid code is not enough. The assigned label must semantically match the span's failure mode. Short common substrings (especially single Chinese characters) can cause false positives. Add context-aware suppression for ambiguous matches.

5. **UNKNOWN with non-empty `pred_label`** — When `pred_code` is `"UNKNOWN"`, `pred_label` must be `""` (empty string), not `null` or any label text.

6. **Confidence not separated** — If UNKNOWN confidence overlaps with known confidence, calibration fails. Keep UNKNOWN mean well below known mean (target separation > 0.5).

7. **Fixing logic without regenerating output** — Diagnosing a bug and patching the script is not enough. Always re-run the full generation pipeline and overwrite `solution.json` after any fix.

8. **Fabricating record fields** — `record_id`, `product_id`, `station`, `engineer_id`, and `raw_reason_text` must be copied verbatim from the input log. Do not infer or modify them.
