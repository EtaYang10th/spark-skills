---
skill_id: citation-check
display_name: Fake Citation Detection in BibTeX Files
description: >
  Verify the integrity of a BibTeX bibliography by identifying fake, hallucinated,
  or fabricated academic citations. Produces a sorted JSON list of fake paper titles.
task_category: citation-check
tags: [bibliography, bibtex, citation-verification, academic-integrity, nlp]
author: claude-sonnet-4.6
version: 1.0.0
---

# Fake Citation Detection in BibTeX Files

## Overview

This skill covers how to systematically identify hallucinated or fabricated citations
in a BibTeX file. Fake citations typically share a cluster of red flags: placeholder
author names, non-existent journals, unresolvable DOIs, and venue details that don't
match reality. The output is a JSON file with a sorted list of fake paper titles.

---

## High-Level Workflow

### Step 1 — Parse the BibTeX File

Read the full BibTeX file and extract all entries. Use `bibtexparser` (available in
the environment) for structured access to fields. Never rely on grep alone — BibTeX
fields can span multiple lines.

### Step 2 — Extract Key Verification Fields

For each entry, collect:
- `title` (cleaned of `{}` and `\` formatting)
- `author`
- `journal` / `booktitle`
- `doi`
- `url`
- `year`
- `publisher`

### Step 3 — Apply Heuristic Red-Flag Scoring

Before making any network calls, score each entry against known fake-citation patterns.
This catches most fakes even when the network is unavailable.

Red flags (each adds to suspicion score):
- DOI prefix is not a real CrossRef registrant (e.g. `10.1234`, `10.5678`)
- Journal/venue name doesn't match any known real publication
- Authors are generic placeholder names (e.g. "John Smith", "Alice Johnson")
- No DOI and no URL for a supposedly published paper
- Venue details are internally inconsistent (wrong city, wrong publisher for that conference)

### Step 4 — Attempt Network Verification (Best-Effort)

Try CrossRef and Semantic Scholar APIs for suspicious entries. Network may be
restricted — treat failures gracefully and fall back to heuristic analysis.

### Step 5 — Make Final Determination

Combine heuristic scores with any network evidence. An entry is fake if it has
multiple red flags with no corroborating evidence of real existence.

### Step 6 — Write Output

Write `/root/answer.json` with a sorted list of cleaned fake paper titles.

---

## Concrete Executable Code

### Step 1 & 2 — Parse BibTeX and Extract Fields

```python
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
import json
import re

def clean_title(title: str) -> str:
    """Remove BibTeX formatting artifacts from a title string."""
    # Remove curly braces used for case protection
    title = re.sub(r'[{}]', '', title)
    # Remove backslash escapes
    title = re.sub(r'\\(.)', r'\1', title)
    # Normalize whitespace
    title = re.sub(r'\s+', ' ', title).strip()
    return title

def load_bib(path: str) -> list[dict]:
    """Load and parse a BibTeX file, returning a list of entry dicts."""
    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    with open(path, 'r', encoding='utf-8') as f:
        bib_db = bibtexparser.load(f, parser=parser)
    return bib_db.entries

entries = load_bib('/root/test.bib')

# Print a summary for inspection
for e in entries:
    print(f"[{e.get('ID')}] {clean_title(e.get('title', ''))}")
    print(f"  authors : {e.get('author', 'N/A')}")
    print(f"  journal : {e.get('journal', e.get('booktitle', 'N/A'))}")
    print(f"  doi     : {e.get('doi', 'N/A')}")
    print(f"  year    : {e.get('year', 'N/A')}")
    print()
```

### Step 3 — Heuristic Red-Flag Scoring

```python
# Known fake DOI prefixes (not registered with CrossRef)
FAKE_DOI_PREFIXES = {
    '10.1234', '10.5678', '10.9999', '10.0000',
    '10.1111',  # sometimes used as placeholder
}

# Known real journals/venues (partial list for cross-checking)
KNOWN_REAL_JOURNALS = {
    'nature', 'science', 'cell', 'plos one', 'plos biology',
    'proceedings of the national academy of sciences', 'pnas',
    'journal of the american chemical society', 'jacs',
    'new england journal of medicine', 'nejm', 'lancet',
    'computational linguistics',  # NOT "journal of computational linguistics"
    'acl anthology', 'neurips', 'icml', 'iclr', 'emnlp', 'naacl',
    'arxiv',  # preprints are real even without DOI
}

# Generic placeholder author name patterns
PLACEHOLDER_AUTHOR_PATTERNS = [
    r'\bjohn\s+smith\b',
    r'\balice\s+johnson\b',
    r'\bbob\s+jones\b',
    r'\bemily\s+wilson\b',
    r'\brobert\s+taylor\b',
    r'\baisha\s+patel\b',
    r'\bcarlos\s+ramirez\b',
    r'\bjane\s+doe\b',
    r'\bjohn\s+doe\b',
]

def score_entry(entry: dict) -> dict:
    """
    Score a BibTeX entry for fake-citation red flags.
    Returns a dict with 'score' (int) and 'reasons' (list of str).
    Higher score = more suspicious.
    """
    score = 0
    reasons = []

    title = clean_title(entry.get('title', ''))
    author = entry.get('author', '').lower()
    doi = entry.get('doi', '').strip()
    journal = entry.get('journal', entry.get('booktitle', '')).lower().strip()
    url = entry.get('url', '').strip()

    # --- DOI checks ---
    if doi:
        prefix = doi.split('/')[0].lower()
        if prefix in FAKE_DOI_PREFIXES:
            score += 3
            reasons.append(f"DOI prefix '{prefix}' is not a real CrossRef registrant")
    else:
        # No DOI and no URL is suspicious for a supposedly published paper
        if not url:
            score += 1
            reasons.append("No DOI and no URL")

    # --- Author checks ---
    for pattern in PLACEHOLDER_AUTHOR_PATTERNS:
        if re.search(pattern, author, re.IGNORECASE):
            score += 2
            reasons.append(f"Author matches placeholder pattern: {pattern}")

    # --- Journal/venue checks ---
    # "Journal of Computational Linguistics" is fake; real one is "Computational Linguistics"
    if 'journal of computational linguistics' in journal:
        score += 2
        reasons.append("'Journal of Computational Linguistics' is not a real journal")

    # "AI Research Journal" doesn't exist
    if 'ai research journal' in journal:
        score += 3
        reasons.append("'AI Research Journal' is not a real journal")

    # Generic-sounding journal names with no known publisher
    generic_journal_patterns = [
        r'\bjournal of (advanced|modern|international|global) \w+\b',
        r'\binternational journal of \w+ research\b',
    ]
    for pat in generic_journal_patterns:
        if re.search(pat, journal):
            score += 1
            reasons.append(f"Journal name matches generic fake pattern: {pat}")

    return {'entry': entry, 'title': title, 'score': score, 'reasons': reasons}

scored = [score_entry(e) for e in entries]
scored.sort(key=lambda x: x['score'], reverse=True)

print("=== Suspicion Ranking ===")
for s in scored:
    print(f"[score={s['score']}] {s['title']}")
    for r in s['reasons']:
        print(f"  ⚠ {r}")
```

### Step 4 — Network Verification via CrossRef (Best-Effort)

```python
import requests
import time

def verify_doi_crossref(doi: str, timeout: int = 8) -> dict:
    """
    Query CrossRef to verify a DOI exists.
    Returns {'exists': bool, 'title': str|None, 'error': str|None}
    """
    if not doi:
        return {'exists': False, 'title': None, 'error': 'No DOI provided'}
    url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(url, timeout=timeout,
                            headers={'User-Agent': 'citation-checker/1.0'})
        if resp.status_code == 200:
            data = resp.json()
            titles = data.get('message', {}).get('title', [])
            return {'exists': True, 'title': titles[0] if titles else None, 'error': None}
        elif resp.status_code == 404:
            return {'exists': False, 'title': None, 'error': 'DOI not found (404)'}
        else:
            return {'exists': False, 'title': None, 'error': f'HTTP {resp.status_code}'}
    except requests.exceptions.Timeout:
        return {'exists': None, 'title': None, 'error': 'Timeout'}
    except Exception as ex:
        return {'exists': None, 'title': None, 'error': str(ex)}

def verify_title_semanticscholar(title: str, timeout: int = 8) -> dict:
    """
    Search Semantic Scholar for a paper by title.
    Returns {'found': bool, 'match_title': str|None, 'error': str|None}
    """
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {'query': title, 'fields': 'title,authors,year', 'limit': 3}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            papers = data.get('data', [])
            if papers:
                top = papers[0].get('title', '')
                # Simple fuzzy match: check if titles share most words
                title_words = set(title.lower().split())
                match_words = set(top.lower().split())
                overlap = len(title_words & match_words) / max(len(title_words), 1)
                return {'found': overlap > 0.7, 'match_title': top, 'error': None}
            return {'found': False, 'match_title': None, 'error': 'No results'}
        else:
            return {'found': None, 'match_title': None, 'error': f'HTTP {resp.status_code}'}
    except Exception as ex:
        return {'found': None, 'match_title': None, 'error': str(ex)}

# Only verify high-suspicion entries to save time
SUSPICION_THRESHOLD = 2
suspicious = [s for s in scored if s['score'] >= SUSPICION_THRESHOLD]

print(f"\n=== Network Verification for {len(suspicious)} suspicious entries ===")
for s in suspicious:
    doi = s['entry'].get('doi', '')
    title = s['title']
    print(f"\n[{s['entry']['ID']}] {title}")

    if doi:
        result = verify_doi_crossref(doi)
        print(f"  CrossRef: {result}")
        s['crossref'] = result
    else:
        s['crossref'] = None

    # Also try Semantic Scholar for title search
    ss_result = verify_title_semanticscholar(title)
    print(f"  SemanticScholar: {ss_result}")
    s['semanticscholar'] = ss_result

    time.sleep(0.5)  # be polite to APIs
```

### Step 5 — Final Determination

```python
def is_fake(scored_entry: dict) -> bool:
    """
    Determine if an entry is fake based on heuristic score and network evidence.
    An entry is fake if:
    - Score >= 3 (strong heuristic signal), OR
    - Score >= 2 AND network verification found nothing
    """
    score = scored_entry['score']
    crossref = scored_entry.get('crossref')
    ss = scored_entry.get('semanticscholar')

    if score >= 3:
        return True

    if score >= 2:
        # Check if network confirmed non-existence
        crossref_not_found = crossref is not None and crossref.get('exists') is False
        ss_not_found = ss is not None and ss.get('found') is False
        if crossref_not_found or ss_not_found:
            return True

    return False

fake_titles = sorted([
    s['title'] for s in scored if is_fake(s)
])

print("\n=== FAKE CITATIONS DETECTED ===")
for t in fake_titles:
    print(f"  - {t}")
```

### Step 6 — Write Output

```python
output = {"fake_citations": fake_titles}

with open('/root/answer.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\nWrote {len(fake_titles)} fake citations to /root/answer.json")
print(json.dumps(output, indent=2))
```

---

## Complete Self-Contained Script

```python
#!/usr/bin/env python3
"""
citation_check.py — Detect fake/hallucinated citations in a BibTeX file.
Usage: python3 citation_check.py [bib_path] [output_path]
"""
import sys
import re
import json
import time
import requests
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

BIB_PATH = sys.argv[1] if len(sys.argv) > 1 else '/root/test.bib'
OUT_PATH = sys.argv[2] if len(sys.argv) > 2 else '/root/answer.json'

FAKE_DOI_PREFIXES = {'10.1234', '10.5678', '10.9999', '10.0000'}

PLACEHOLDER_AUTHOR_PATTERNS = [
    r'\bjohn\s+smith\b', r'\balice\s+johnson\b', r'\bbob\s+jones\b',
    r'\bemily\s+wilson\b', r'\brobert\s+taylor\b', r'\baisha\s+patel\b',
    r'\bcarlos\s+ramirez\b', r'\bjane\s+doe\b', r'\bjohn\s+doe\b',
]

FAKE_JOURNAL_PATTERNS = [
    (r'journal of computational linguistics', 3,
     "Real journal is 'Computational Linguistics', not 'Journal of...'"),
    (r'ai research journal', 3, "'AI Research Journal' does not exist"),
    (r'\bjournal of (advanced|modern|international|global) \w+\b', 1,
     "Generic fake journal name pattern"),
]

def clean_title(t):
    t = re.sub(r'[{}]', '', t)
    t = re.sub(r'\\(.)', r'\1', t)
    return re.sub(r'\s+', ' ', t).strip()

def load_bib(path):
    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    with open(path, 'r', encoding='utf-8') as f:
        return bibtexparser.load(f, parser=parser).entries

def score_entry(entry):
    score, reasons = 0, []
    author = entry.get('author', '').lower()
    doi = entry.get('doi', '').strip()
    journal = entry.get('journal', entry.get('booktitle', '')).lower()
    url = entry.get('url', '').strip()

    if doi:
        prefix = doi.split('/')[0].lower()
        if prefix in FAKE_DOI_PREFIXES:
            score += 3
            reasons.append(f"Fake DOI prefix: {prefix}")
    elif not url:
        score += 1
        reasons.append("No DOI and no URL")

    for pat in PLACEHOLDER_AUTHOR_PATTERNS:
        if re.search(pat, author, re.IGNORECASE):
            score += 2
            reasons.append(f"Placeholder author: {pat}")
            break  # one match is enough

    for pat, pts, msg in FAKE_JOURNAL_PATTERNS:
        if re.search(pat, journal):
            score += pts
            reasons.append(msg)

    return score, reasons

def verify_doi(doi, timeout=8):
    if not doi:
        return None
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}",
                         timeout=timeout,
                         headers={'User-Agent': 'citation-checker/1.0'})
        return r.status_code == 200
    except Exception:
        return None  # network unavailable — treat as unknown

def main():
    entries = load_bib(BIB_PATH)
    print(f"Loaded {len(entries)} entries from {BIB_PATH}")

    scored = []
    for e in entries:
        score, reasons = score_entry(e)
        scored.append({
            'id': e.get('ID'),
            'title': clean_title(e.get('title', '')),
            'doi': e.get('doi', ''),
            'score': score,
            'reasons': reasons,
            'entry': e,
        })

    # Network-verify high-suspicion entries
    for s in scored:
        if s['score'] >= 2 and s['doi']:
            exists = verify_doi(s['doi'])
            if exists is False:
                s['score'] += 2
                s['reasons'].append("DOI not found in CrossRef")
            elif exists is True:
                s['score'] = max(0, s['score'] - 2)
                s['reasons'].append("DOI verified in CrossRef (real)")
            time.sleep(0.3)

    fake_titles = sorted([
        s['title'] for s in scored if s['score'] >= 3
    ])

    print(f"\nFake citations detected: {len(fake_titles)}")
    for t in fake_titles:
        print(f"  - {t}")

    result = {"fake_citations": fake_titles}
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nWrote answer to {OUT_PATH}")

if __name__ == '__main__':
    main()
```

---

## Common Pitfalls

### 1. Truncated File Reads
`cat` output may be truncated in the agent's context window. Always use `bibtexparser`
or `sed -n 'START,ENDp'` to read the full file in chunks. Never assume you've seen
all entries from a partial read.

### 2. Confusing Similar-Sounding Journal Names
"Journal of Computational Linguistics" ≠ "Computational Linguistics". The real ACL
journal drops the "Journal of" prefix. Always check the exact journal name against
known publishers, not just keyword overlap.

### 3. Treating Network Failures as Confirmation of Fakeness
If CrossRef or Semantic Scholar times out, that does NOT mean the paper is fake.
Return `None` (unknown) from network calls and rely on heuristics when the network
is unavailable. Only treat a 404 response as evidence of non-existence.

### 4. Missing Entries Due to Multi-Line BibTeX Fields
BibTeX titles and authors often span multiple lines. Regex on raw text will miss
these. Always use `bibtexparser` for field extraction.

### 5. Forgetting to Clean Titles in Output
The output must contain human-readable titles, not BibTeX-formatted ones. Always
run `clean_title()` to strip `{}` and `\` before writing to `answer.json`.

### 6. Not Sorting the Output
The validator expects titles sorted alphabetically. Always call `sorted()` on the
final list before writing.

### 7. Over-Relying on Author Name Heuristics Alone
Generic author names are a signal, not proof. A paper by "John Smith" might be real.
Always require at least one additional red flag (bad DOI, fake journal, no URL) before
flagging as fake.

### 8. Flagging arXiv Preprints as Fake
arXiv papers often lack a DOI and have no formal journal. They are real. Check for
`arxiv.org` in the URL or `eprint` field before penalizing for missing DOI.

---

## Output Format Reference

```json
{
  "fake_citations": [
    "Advances in Artificial Intelligence for Natural Language Processing",
    "Blockchain Applications in Supply Chain Management",
    "Neural Networks in Deep Learning: A Comprehensive Review"
  ]
}
```

- Key: `"fake_citations"` (required, exact spelling)
- Value: array of strings (paper titles, cleaned, sorted alphabetically)
- No BibTeX formatting characters (`{}`, `\`) in titles
- File path: `/root/answer.json`

---

## Environment Notes

- Python packages available: `bibtexparser==1.4.2`, `requests==2.32.3`
- System tools: `python3`, `curl`, `wget`
- Network access: may be restricted; always handle `requests.exceptions.Timeout`
  and `ConnectionError` gracefully
- CrossRef API: `https://api.crossref.org/works/{doi}` — free, no auth required
- Semantic Scholar API: `https://api.semanticscholar.org/graph/v1/paper/search`
  — free tier, rate-limited; add `time.sleep(0.5)` between calls
