---
title: Academic Paper PDF Anonymization
category: paper-anonymizer
domain: pdf-processing
tags:
  - pdf-redaction
  - pymupdf
  - academic-papers
  - anonymization
  - metadata-cleaning
dependencies:
  - pymupdf>=1.24.0
  - poppler-utils
---

# Academic Paper PDF Anonymization

## Overview

This skill covers the end-to-end process of anonymizing academic papers in PDF format for double-blind review. The task involves identifying and redacting author names, institutional affiliations, email addresses, arXiv identifiers, venue/conference headers, DOIs, acknowledgment sections with named individuals, and cleaning PDF metadata — all while preserving the paper's scientific content, self-citations in references, and structural integrity.

## High-Level Workflow

1. **Extract full text from each PDF** to identify all content that needs redaction. Use `pdftotext` (from poppler-utils) for quick plaintext extraction, and PyMuPDF for programmatic page-by-page text access.

2. **Catalog redaction targets per paper.** For each paper, identify:
   - Author names (first page, headers, footers, acknowledgments)
   - Institutional affiliations (universities, companies, labs)
   - Email addresses
   - arXiv identifiers (e.g., `2509.26542`, `arXiv:2509.26542`)
   - Conference/venue headers (e.g., "Interspeech 2024", "ICML 2025 Workshop")
   - DOIs (e.g., `10.21437/Interspeech.2024-33`)
   - "Equal contribution" markers and footnote symbols tied to author info
   - Named individuals in Acknowledgments sections

3. **Build redaction lists as structured data.** Organize targets into categories (names, affiliations, identifiers, emails, venues) so the redaction engine can apply them systematically across all pages.

4. **Apply text-based redaction using PyMuPDF.** For each page, search for each target string and apply a filled redaction annotation. Then call `page.apply_redactions()` to permanently remove the underlying text.

5. **Clean PDF metadata.** Strip author, subject, keywords, and creator fields from the document metadata. These often contain author names (especially for arXiv-sourced PDFs).

6. **Verify the output.** Confirm that:
   - The redacted PDF is structurally valid (opens, has the same page count)
   - No author names, affiliations, or identifiers remain in the extracted text
   - Paper content (title keywords, abstract terms, section headings) is preserved
   - Self-citations in the references section are intact
   - Extracted text length is reasonable (not over-redacted)

## Step 1: Extract Text for Analysis

Use `pdftotext` for a quick full-text dump, then use PyMuPDF for programmatic access.

```bash
# Quick text extraction for manual inspection
pdftotext /root/paper1.pdf - | head -80
pdftotext /root/paper1.pdf - | wc -c   # check total text length
```

```python
import fitz  # PyMuPDF

def extract_full_text(pdf_path):
    """Extract all text from a PDF, page by page."""
    doc = fitz.open(pdf_path)
    pages_text = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        pages_text.append(text)
    doc.close()
    return pages_text

# Use first few pages to identify authors, affiliations
pages = extract_full_text("/root/paper1.pdf")
print(pages[0][:2000])  # First page usually has all author info
```

## Step 2: Identify Redaction Targets

Academic papers follow predictable patterns. The first page contains author names, affiliations, and emails. Headers/footers may repeat venue info. Acknowledgments sections name individuals.

```python
def build_paper_redaction_config(paper_id, pages_text):
    """
    Analyze extracted text and return a dict of redaction targets.
    This must be done manually per paper — there is no reliable
    automatic author detection for arbitrary PDFs.
    """
    first_page = pages_text[0]
    full_text = "\n".join(pages_text)

    # Print diagnostic info
    print(f"=== Paper {paper_id} first page (first 2000 chars) ===")
    print(first_page[:2000])
    print(f"\n=== Paper {paper_id} last 2 pages ===")
    for p in pages_text[-2:]:
        print(p[:1000])

    # Return structure — fill in after reading the text
    return {
        "authors": [],          # ["First Last", "First Last", ...]
        "affiliations": [],     # ["Duke University", "Adobe Research", ...]
        "emails": [],           # ["user@domain.edu", ...]
        "arxiv_ids": [],        # ["2509.26542", "arXiv:2509.26542v1", ...]
        "venues": [],           # ["Interspeech 2024", ...]
        "dois": [],             # ["10.21437/Interspeech.2024-33", ...]
        "extra_strings": [],    # ["Equal contribution", specific footnotes, ...]
        "acknowledgment_names": [],  # Names in acknowledgments not in author list
    }
```

### What to look for in each paper

| Location | What to redact | Example |
|----------|---------------|---------|
| First page, below title | Author names | "John Smith, Jane Doe" |
| First page, below authors | Affiliations | "Duke University", "Adobe Research" |
| First page or footnotes | Emails | "jsmith@duke.edu" |
| First page header/footer | arXiv ID | "arXiv:2509.26542v1 [cs.CL] 30 May 2025" |
| Header on every page | Venue | "Interspeech 2024" |
| First page footnote | DOI | "DOI: 10.21437/Interspeech.2024-33" |
| First page footnote | Contribution markers | "∗Equal contribution" |
| Acknowledgments section | Named individuals | "We thank Shengyuan Xu for..." |

### Critical: What NOT to redact

- **Paper title and all title words** — these must be preserved
- **Self-citations in references** — e.g., "Lin et al., 2025c" in the bibliography is fine as long as author names/affiliations on the first page are removed
- **Tool/framework names** that happen to match author projects — e.g., "ESPnet-Muskits" is a published tool name, not an identity leak
- **Generic institutional references** in the body text that aren't tied to authorship

## Step 3: Build the Redaction Engine

The core redaction uses PyMuPDF's `search_for` + `add_redact_annot` + `apply_redactions` pipeline.

```python
import fitz
import re

def redact_pdf(input_path, output_path, redaction_config):
    """
    Redact all target strings from a PDF and clean metadata.

    Args:
        input_path: Path to the original PDF
        output_path: Path to save the redacted PDF
        redaction_config: Dict with keys: authors, affiliations, emails,
                         arxiv_ids, venues, dois, extra_strings,
                         acknowledgment_names
    """
    doc = fitz.open(input_path)

    # Collect all target strings, longest first to avoid partial matches
    all_targets = []
    for key in ["authors", "affiliations", "emails", "arxiv_ids",
                "venues", "dois", "extra_strings", "acknowledgment_names"]:
        all_targets.extend(redaction_config.get(key, []))

    # Sort by length descending — redact longer strings first
    # This prevents partial matches when a short string is a substring of a longer one
    all_targets.sort(key=len, reverse=True)

    # Remove empty strings
    all_targets = [t for t in all_targets if t.strip()]

    total_redactions = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_redactions = 0

        for target in all_targets:
            # Search for exact text matches on this page
            instances = page.search_for(target)
            for inst in instances:
                # Add a filled black rectangle redaction
                page.add_redact_annot(inst, fill=(0, 0, 0))
                page_redactions += 1

            # Also try case-insensitive search for emails and identifiers
            if "@" in target or target.startswith("10."):
                instances_lower = page.search_for(target.lower())
                for inst in instances_lower:
                    page.add_redact_annot(inst, fill=(0, 0, 0))
                    page_redactions += 1

        # Apply all redactions on this page (permanently removes text)
        page.apply_redactions()
        total_redactions += page_redactions

    # Clean metadata
    doc.set_metadata({
        "author": "",
        "title": doc.metadata.get("title", ""),  # preserve title
        "subject": "",
        "keywords": "",
        "creator": "Anonymous",
        "producer": "Anonymous",
    })

    doc.save(output_path, garbage=4, deflate=True)
    doc.close()

    print(f"  Redacted {total_redactions} instances, saved to {output_path}")
    return total_redactions
```

### Handling tricky redaction patterns

Some content won't match with simple string search because PDF text extraction can split strings across lines or insert whitespace. Handle these cases:

```python
def build_target_variants(target):
    """
    Generate variants of a target string to catch PDF text quirks.
    """
    variants = [target]

    # For multi-word strings, also try with different whitespace
    if " " in target:
        # Sometimes PDFs insert extra spaces
        variants.append(re.sub(r'\s+', '  ', target))
        # Sometimes line breaks appear as spaces
        variants.append(target.replace(" ", "\n"))

    # For names, try "Last, First" in addition to "First Last"
    parts = target.split()
    if len(parts) == 2:
        variants.append(f"{parts[1]}, {parts[0]}")
        # Also try first-name initial: "F. Last"
        variants.append(f"{parts[0][0]}. {parts[1]}")

    # For arXiv IDs, try with and without prefix
    if re.match(r'\d{4}\.\d{4,5}', target):
        variants.append(f"arXiv:{target}")
        variants.append(f"arXiv: {target}")
        # With version suffix
        variants.append(f"{target}v1")
        variants.append(f"{target}v2")

    return list(set(variants))  # deduplicate
```

## Step 4: Verify Redaction Quality

Verification is critical. The test suite typically checks:

1. **Structural integrity** — PDF opens, same page count, text length > minimum
2. **Authors redacted** — no author name appears in extracted text
3. **Affiliations redacted** — no institution name appears
4. **Identifiers redacted** — no arXiv IDs, DOIs, or venue names appear
5. **Content preserved** — title keywords and abstract terms still present
6. **Self-citations preserved** — references to the authors' own work remain

```python
import subprocess

def verify_redaction(original_path, redacted_path, redaction_config, 
                     title_keywords, self_citation_terms):
    """
    Verify that redaction was successful and content is preserved.
    Returns a dict of check results.
    """
    results = {}

    # 1. Structural integrity
    orig_doc = fitz.open(original_path)
    red_doc = fitz.open(redacted_path)
    results["same_pages"] = len(orig_doc) == len(red_doc)
    orig_doc.close()
    red_doc.close()

    # Extract text from redacted PDF
    text_output = subprocess.run(
        ["pdftotext", redacted_path, "-"],
        capture_output=True, text=True
    )
    redacted_text = text_output.stdout.lower()

    results["text_length"] = len(redacted_text)
    results["text_sufficient"] = len(redacted_text) > 1000

    # 2. Authors redacted
    authors_found = []
    for author in redaction_config["authors"]:
        # Check last name (most reliable — first names can be common words)
        last_name = author.split()[-1].lower()
        # Search in non-reference sections (first 80% of text)
        # References are typically at the end
        main_text = redacted_text[:int(len(redacted_text) * 0.8)]
        if last_name in main_text:
            authors_found.append(author)
    results["authors_leaked"] = authors_found

    # 3. Affiliations redacted
    affiliations_found = []
    for aff in redaction_config["affiliations"]:
        if aff.lower() in redacted_text:
            affiliations_found.append(aff)
    results["affiliations_leaked"] = affiliations_found

    # 4. Identifiers redacted
    ids_found = []
    for arxiv_id in redaction_config.get("arxiv_ids", []):
        if arxiv_id.lower() in redacted_text:
            ids_found.append(arxiv_id)
    for doi in redaction_config.get("dois", []):
        if doi.lower() in redacted_text:
            ids_found.append(doi)
    results["identifiers_leaked"] = ids_found

    # 5. Content preserved
    missing_keywords = []
    for kw in title_keywords:
        if kw.lower() not in redacted_text:
            missing_keywords.append(kw)
    results["missing_keywords"] = missing_keywords

    # 6. Self-citations preserved
    missing_citations = []
    for cite in self_citation_terms:
        if cite.lower() not in redacted_text:
            missing_citations.append(cite)
    results["missing_self_citations"] = missing_citations

    # 7. Metadata clean
    red_doc = fitz.open(redacted_path)
    meta = red_doc.metadata
    red_doc.close()
    author_meta = (meta.get("author", "") or "").strip()
    results["metadata_clean"] = (author_meta == "" or author_meta.lower() == "anonymous")

    return results
```

## Step 5: Handle Edge Cases

### arXiv header lines

arXiv papers often have a full header line like:
```
arXiv:2509.26542v1 [cs.CL] 30 May 2025
```

Redact the entire line, not just the ID number. Also check for the arXiv identifier appearing in footers or watermarks.

```python
# Build comprehensive arXiv redaction targets
def arxiv_variants(arxiv_id):
    """Generate all variants of an arXiv ID that might appear."""
    targets = [
        arxiv_id,                          # "2509.26542"
        f"arXiv:{arxiv_id}",              # "arXiv:2509.26542"
        f"arXiv: {arxiv_id}",             # "arXiv: 2509.26542"
    ]
    # Add versioned variants
    for v in range(1, 4):
        targets.append(f"{arxiv_id}v{v}")
        targets.append(f"arXiv:{arxiv_id}v{v}")
    return targets
```

### Venue headers that repeat on every page

Some published papers have the conference name in a running header. PyMuPDF's `search_for` will find it on every page automatically — just include the venue string in your targets.

### Acknowledgment sections

Scan the last few pages for "Acknowledgment" or "Acknowledgement" sections. These often thank specific people by name who aren't in the author list.

```python
def find_acknowledgment_names(pages_text):
    """Find the acknowledgments section and extract named individuals."""
    full_text = "\n".join(pages_text)
    # Find acknowledgment section
    ack_match = re.search(
        r'(?:Acknowledgment|Acknowledgement)s?\s*\n(.*?)(?:\n(?:References|Bibliography|\[1\]))',
        full_text, re.DOTALL | re.IGNORECASE
    )
    if ack_match:
        ack_text = ack_match.group(1)
        print(f"Found acknowledgments: {ack_text[:200]}")
        # Names need to be identified manually from this text
        return ack_text
    return ""
```

### "Equal contribution" and footnote markers

First-page footnotes like "∗Equal contribution" or "†Work done at X" leak authorship info. Include these in your redaction targets. Watch for Unicode symbols (∗, †, ‡, §) that may differ from ASCII asterisks.

```python
equal_contribution_variants = [
    "Equal contribution",
    "∗Equal contribution",
    "*Equal contribution",
    "† Equal contribution",
    "These authors contributed equally",
    "Work done while at",
    "Work done during an internship at",
]
```

## Common Pitfalls

1. **Forgetting PDF metadata.** The `author` field in PDF metadata often contains all author names, especially for arXiv papers. Always clean metadata even if you redact all visible text.

2. **Redacting too aggressively in references.** Self-citations like "Lin et al., 2025c" in the bibliography should be preserved. The test checks that self-citations survive. Only redact author names/affiliations from the paper's own authorship block, not from cited references.

3. **Missing venue information.** Conference headers ("Published at Interspeech 2024"), DOIs, and submission IDs are identity leaks. Check headers, footers, and first-page footnotes.

4. **Partial name matches.** If an author's last name is a common word (e.g., "Li", "Wang"), be careful not to over-redact. PyMuPDF's `search_for` does substring matching, so "Li" would match "Li" inside "Linear". Use the full name when possible, or verify that partial matches don't destroy content.

5. **Not sorting targets by length.** If you redact "Duke" before "Duke University", the word "University" may be left orphaned. Always sort targets longest-first.

6. **Forgetting to call `apply_redactions()`.** Adding redaction annotations does NOT remove text. You must call `page.apply_redactions()` to permanently remove the underlying content. Without this, the text is still extractable.

7. **Unicode and encoding issues.** PDF text extraction may produce different Unicode representations. An author name with accented characters might not match a plain ASCII search. Try both forms.

8. **Missing the arXiv watermark/header.** arXiv adds a header line with the paper ID, subject area, and date. This appears as rendered text on the first page and must be redacted.

## Reference Implementation

This is a complete, self-contained script that anonymizes academic papers. Copy, adapt the `PAPERS_CONFIG` dict for your specific papers, and run.

```python
#!/usr/bin/env python3
"""
Academic Paper Anonymizer
=========================
Redacts author-identifying information from academic PDFs using PyMuPDF.

Usage:
    1. Fill in PAPERS_CONFIG with paper-specific redaction targets
    2. Run: python3 anonymize.py
    3. Redacted PDFs are saved to OUTPUT_DIR

Requires: pymupdf (pip install pymupdf), poppler-utils (for pdftotext verification)
"""

import fitz  # PyMuPDF
import os
import re
import subprocess
import sys

# ============================================================
# CONFIGURATION — adapt these for each set of papers
# ============================================================

INPUT_DIR = "/root"
OUTPUT_DIR = "/root/redacted"

# Each paper needs its own redaction config.
# To build this config:
#   1. Run: pdftotext paperN.pdf - | head -80
#   2. Read the first page to find authors, affiliations, emails
#   3. Check for arXiv IDs, venue headers, DOIs
#   4. Check acknowledgments section for named individuals
#   5. List title keywords and self-citation terms for verification

PAPERS_CONFIG = {
    "paper1.pdf": {
        "authors": [
            # List every author's full name as it appears on the paper
            # Example: "Yinghao Aaron Li", "Xilin Jiang", ...
        ],
        "affiliations": [
            # Example: "Duke University", "Adobe Research"
        ],
        "emails": [
            # Example: "yl768@duke.edu"
        ],
        "arxiv_ids": [
            # The numeric ID, e.g., "2509.26542"
            # The script auto-generates variants with arXiv: prefix and version suffixes
        ],
        "venues": [
            # Example: "Interspeech 2024", "ICML 2025 Workshop"
        ],
        "dois": [
            # Example: "10.21437/Interspeech.2024-33"
        ],
        "extra_strings": [
            # Footnote markers, contribution statements, etc.
            # Example: "∗Equal contribution", "Work done while at Adobe"
        ],
        "acknowledgment_names": [
            # People thanked in acknowledgments who aren't authors
            # Example: "Shengyuan Xu", "Pengcheng Zhu"
        ],
        # For verification only — not used in redaction
        "title_keywords": ["VERA", "Reasoning"],  # words from the title
        "self_citations": ["Lin et al., 2025c"],   # self-cites to preserve
    },
    # "paper2.pdf": { ... },
    # "paper3.pdf": { ... },
}


# ============================================================
# CORE FUNCTIONS
# ============================================================

def expand_targets(config):
    """
    Build the full list of redaction target strings from a paper config.
    Generates variants for names, arXiv IDs, and emails.
    Returns a deduplicated list sorted by length (longest first).
    """
    targets = set()

    # Author names and variants
    for name in config.get("authors", []):
        targets.add(name)
        parts = name.split()
        if len(parts) >= 2:
            first = parts[0]
            last = parts[-1]
            # "Last, First" variant
            targets.add(f"{last}, {first}")
            # If there's a middle name, try without it
            if len(parts) > 2:
                targets.add(f"{first} {last}")
                targets.add(f"{last}, {first}")
            # First initial variant: "Y. Li"
            targets.add(f"{first[0]}. {last}")
            # Full name with middle initials: "Y. A. Li"
            if len(parts) == 3:
                targets.add(f"{parts[0][0]}. {parts[1][0]}. {parts[2]}")

    # Affiliations — add as-is
    for aff in config.get("affiliations", []):
        targets.add(aff)

    # Emails — add as-is and lowercase
    for email in config.get("emails", []):
        targets.add(email)
        targets.add(email.lower())

    # arXiv IDs — generate all variants
    for arxiv_id in config.get("arxiv_ids", []):
        targets.add(arxiv_id)
        targets.add(f"arXiv:{arxiv_id}")
        targets.add(f"arXiv: {arxiv_id}")
        for v in range(1, 5):
            targets.add(f"{arxiv_id}v{v}")
            targets.add(f"arXiv:{arxiv_id}v{v}")
            targets.add(f"arXiv: {arxiv_id}v{v}")

    # Venues
    for venue in config.get("venues", []):
        targets.add(venue)

    # DOIs
    for doi in config.get("dois", []):
        targets.add(doi)
        targets.add(f"DOI: {doi}")
        targets.add(f"doi: {doi}")

    # Extra strings (contribution markers, etc.)
    for s in config.get("extra_strings", []):
        targets.add(s)

    # Acknowledgment names
    for name in config.get("acknowledgment_names", []):
        targets.add(name)

    # Remove empty strings, sort longest first
    targets = [t for t in targets if t.strip()]
    targets.sort(key=len, reverse=True)
    return targets


def redact_pdf(input_path, output_path, targets):
    """
    Apply text redaction to a PDF using PyMuPDF.

    For each page, searches for every target string and places a filled
    black rectangle over each match. Then applies redactions to permanently
    remove the underlying text. Finally cleans PDF metadata.

    Args:
        input_path: Source PDF path
        output_path: Destination path for redacted PDF
        targets: List of strings to redact, sorted longest-first
    """
    doc = fitz.open(input_path)
    total_redactions = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_count = 0

        for target in targets:
            # PyMuPDF search_for finds all non-overlapping occurrences
            instances = page.search_for(target)
            for rect in instances:
                # Expand rect slightly to ensure full coverage
                expanded = fitz.Rect(
                    rect.x0 - 1, rect.y0 - 1,
                    rect.x1 + 1, rect.y1 + 1
                )
                page.add_redact_annot(expanded, fill=(0, 0, 0))
                page_count += 1

        # CRITICAL: apply_redactions() permanently removes text under annotations
        page.apply_redactions()
        total_redactions += page_count

    # Clean metadata — preserve title, clear everything else
    original_title = doc.metadata.get("title", "") or ""
    doc.set_metadata({
        "author": "",
        "title": original_title,
        "subject": "",
        "keywords": "",
        "creator": "Anonymous",
        "producer": "Anonymous",
    })

    # Save with garbage collection and compression
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()

    print(f"  [{os.path.basename(input_path)}] {total_redactions} redactions applied")
    return total_redactions


def verify_redaction(input_path, output_path, config):
    """
    Run verification checks on a redacted PDF.
    Returns (passed: bool, details: str).
    """
    issues = []

    # Check structural integrity
    try:
        orig = fitz.open(input_path)
        red = fitz.open(output_path)
        if len(orig) != len(red):
            issues.append(f"Page count mismatch: {len(orig)} vs {len(red)}")
        orig.close()
        red.close()
    except Exception as e:
        issues.append(f"Cannot open redacted PDF: {e}")
        return False, "\n".join(issues)

    # Extract text from redacted PDF
    result = subprocess.run(
        ["pdftotext", output_path, "-"],
        capture_output=True, text=True
    )
    text = result.stdout
    text_lower = text.lower()

    if len(text) < 1000:
        issues.append(f"Extracted text too short ({len(text)} chars) — possible over-redaction")

    # Check no author names remain (check full text for affiliations/emails,
    # but for author names, skip the references section)
    # Heuristic: references are in the last 20% of text
    main_text_lower = text_lower[:int(len(text_lower) * 0.8)]

    for author in config.get("authors", []):
        last_name = author.split()[-1].lower()
        # Only flag if last name appears in main text (not references)
        if len(last_name) > 2 and last_name in main_text_lower:
            issues.append(f"Author name leaked: '{author}' (found '{last_name}' in main text)")

    # Check affiliations (these should not appear anywhere)
    for aff in config.get("affiliations", []):
        if aff.lower() in text_lower:
            issues.append(f"Affiliation leaked: '{aff}'")

    # Check identifiers
    for aid in config.get("arxiv_ids", []):
        if aid.lower() in text_lower:
            issues.append(f"arXiv ID leaked: '{aid}'")
    for doi in config.get("dois", []):
        if doi.lower() in text_lower:
            issues.append(f"DOI leaked: '{doi}'")

    # Check emails
    for email in config.get("emails", []):
        if email.lower() in text_lower:
            issues.append(f"Email leaked: '{email}'")

    # Check venues
    for venue in config.get("venues", []):
        if venue.lower() in text_lower:
            issues.append(f"Venue leaked: '{venue}'")

    # Check metadata
    red = fitz.open(output_path)
    meta_author = (red.metadata.get("author", "") or "").strip()
    red.close()
    if meta_author and meta_author.lower() != "anonymous":
        issues.append(f"Metadata author not cleaned: '{meta_author}'")

    # Verify content preservation
    for kw in config.get("title_keywords", []):
        if kw.lower() not in text_lower:
            issues.append(f"Title keyword missing: '{kw}'")

    # Verify self-citations preserved
    for cite in config.get("self_citations", []):
        if cite.lower() not in text_lower:
            issues.append(f"Self-citation missing: '{cite}'")

    if issues:
        return False, "\n".join(issues)
    return True, "All checks passed"


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_passed = True

    for filename, config in PAPERS_CONFIG.items():
        input_path = os.path.join(INPUT_DIR, filename)
        output_path = os.path.join(OUTPUT_DIR, filename)

        if not os.path.exists(input_path):
            print(f"WARNING: {input_path} not found, skipping")
            continue

        print(f"\nProcessing {filename}...")

        # Build expanded target list
        targets = expand_targets(config)
        print(f"  {len(targets)} redaction targets")

        # Apply redaction
        redact_pdf(input_path, output_path, targets)

        # Verify
        passed, details = verify_redaction(input_path, output_path, config)
        if passed:
            print(f"  ✓ Verification passed")
        else:
            print(f"  ✗ Verification issues:")
            for line in details.split("\n"):
                print(f"    - {line}")
            all_passed = False

    if all_passed:
        print(f"\nAll papers anonymized successfully. Output in {OUTPUT_DIR}/")
    else:
        print(f"\nSome papers have verification