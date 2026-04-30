---
name: latex-formula-extraction
version: 1.0.0
description: Extract all display-mode LaTeX formulas from a research paper PDF, clean them, and write them to a markdown file with syntax fixes.
tags: [latex, pdf, formula-extraction, markdown, ocr]
---

# Skill: LaTeX Formula Extraction from Research PDFs

## Overview

This skill covers extracting all display-mode (standalone line) LaTeX formulas from a PDF research paper, cleaning them, and writing them to a markdown file. It also covers detecting and fixing syntax errors (bracket mismatches, typos) in the extracted formulas.

The output format is a markdown file where each formula is wrapped in `$$ ... $$` on its own line, with original formulas first, then a blank line, then any corrected versions.

---

## High-Level Workflow

### Step 1: Verify Available Tools

Before doing anything, check what PDF extraction tools are available. The preferred tool is `marker-pdf` (Python package), which preserves LaTeX math during OCR. Fall back to `pdftotext` or `pymupdf` only if marker is unavailable.

```bash
# Check for marker-pdf
python3 -c "import marker; print(marker.__file__)" 2>/dev/null && echo "marker available"

# Check for fallback tools
which pdftotext 2>/dev/null
python3 -c "import fitz; print('pymupdf available')" 2>/dev/null
pip list 2>/dev/null | grep -iE "marker|pymupdf|pdfminer|pdfplumber"
```

### Step 2: Extract PDF Content with marker-pdf

`marker-pdf` is the best tool for this task — it uses ML-based OCR and outputs markdown with LaTeX math blocks preserved. Run `marker_single` on the input PDF.

```bash
# marker_single <input_pdf> --output_dir <output_directory>
marker_single /path/to/latex_paper.pdf --output_dir /root/marker_output 2>&1 | tail -20
```

This is CPU-intensive and may take 1–5 minutes for a typical research paper. The output will be at `<output_dir>/<pdf_basename>/<pdf_basename>.md`.

```bash
# After completion, verify output exists
ls /root/marker_output/latex_paper/
cat /root/marker_output/latex_paper/latex_paper.md
```

### Step 3: Identify All Display-Mode Formulas

Display-mode formulas are those on their own line (not inline). In the marker output, they appear as `$$ ... $$` blocks. Extract them:

```bash
# Find all lines containing display math
grep -n '\$\$' /root/marker_output/latex_paper/latex_paper.md
```

For each match, inspect the surrounding context to confirm it's a standalone formula (not inline math that marker may have wrapped incorrectly):

```bash
# Inspect context around a specific line number (e.g., line 35)
sed -n '30,45p' /root/marker_output/latex_paper/latex_paper.md
```

### Step 4: Clean Each Formula

For each extracted formula, apply these cleaning rules:

1. Strip equation tags/labels (e.g., `(1)`, `\tag{1}`, `\label{eq:foo}`)
2. Remove trailing commas or periods that are part of surrounding prose, not the formula
3. Preserve all mathematical content exactly — do not "improve" notation unless it's a clear syntax error

```python
import re

def clean_formula(raw: str) -> str:
    """
    Remove equation tags, trailing punctuation, and label commands
    from a raw LaTeX formula string (without the $$ delimiters).
    """
    # Remove \tag{...} or \tag*{...}
    raw = re.sub(r'\\tag\*?\{[^}]*\}', '', raw)
    # Remove \label{...}
    raw = re.sub(r'\\label\{[^}]*\}', '', raw)
    # Remove standalone equation numbers like (1), (2), (A.3) at end
    raw = re.sub(r'\s*\(\s*[A-Za-z0-9.]+\s*\)\s*$', '', raw)
    # Remove trailing comma or period
    raw = re.sub(r'[,\.]\s*$', '', raw)
    # Strip surrounding whitespace
    return raw.strip()
```

### Step 5: Detect Syntax Errors

After cleaning, scan each formula for common LaTeX syntax errors. The most frequent issues in OCR-extracted formulas:

#### 5a. Bracket Mismatch Detection

```python
def find_bracket_mismatches(formula: str) -> list:
    """
    Detect mismatched \\left / \\right bracket pairs.
    Returns list of (open_bracket, close_bracket) mismatches.
    """
    import re
    # Find all \\left<X> and \\right<X> tokens
    left_tokens = re.findall(r'\\left\s*([(\[{|.])', formula)
    right_tokens = re.findall(r'\\right\s*([)\]|}|.])', formula)
    
    # Matching pairs
    pairs = {'(': ')', '[': ']', '{': '}', '|': '|', '.': '.'}
    
    mismatches = []
    for l, r in zip(left_tokens, right_tokens):
        expected_r = pairs.get(l)
        if expected_r and r != expected_r:
            mismatches.append((l, r))
    return mismatches

# Example usage
formula = r'\exp\left[i\eta_{i,m}\left[a_m + a_m^\dagger\right)\right]'
mismatches = find_bracket_mismatches(formula)
# Returns: [('[', ')')] — the inner \left[ is closed with \right)
```

#### 5b. Common OCR Errors to Check

- `\left[...\right)` or `\left(...\right]` — mismatched bracket types
- Missing `^` or `_` before `{...}` superscript/subscript groups
- `\\` rendered as `\` (single backslash) in some contexts
- `\dagger` vs `^\dagger` — dagger must be a superscript
- Unmatched `{` or `}` braces (count them)

```python
def check_brace_balance(formula: str) -> bool:
    """Returns True if curly braces are balanced."""
    depth = 0
    i = 0
    while i < len(formula):
        if formula[i] == '\\':
            i += 2  # skip escaped character
            continue
        if formula[i] == '{':
            depth += 1
        elif formula[i] == '}':
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0
```

### Step 6: Write the Output File

The output format is strict:
- One formula per line, wrapped in `$$ ... $$`
- Original formulas first (in page order)
- A blank line separator
- Fixed formulas after the blank line (only for formulas that had syntax errors)
- No headers, no labels, no extra text

```python
def write_formula_file(output_path: str, originals: list, fixed: list):
    """
    Write formulas to markdown file.
    
    Args:
        output_path: Path to output .md file
        originals: List of cleaned original formula strings (no $$ delimiters)
        fixed: List of (original_index, fixed_formula) tuples for corrected formulas
    """
    lines = []
    
    # Write original formulas
    for formula in originals:
        lines.append(f'$${formula}$$')
    
    # If there are fixes, add blank line then fixed versions
    if fixed:
        lines.append('')  # blank separator line
        for _, fix in fixed:
            lines.append(f'$${fix}$$')
    
    # Final trailing newline
    content = '\n'.join(lines) + '\n'
    
    with open(output_path, 'w') as f:
        f.write(content)
    
    print(f"Written {len(originals)} original + {len(fixed)} fixed formulas to {output_path}")

# Example
originals = [
    r'\frac{d^2x_i}{dt^2} = -\omega_0^2 x_i + \frac{kq^2}{m} \sum_{\substack{j=1 \\ j\neq i}}^N \frac{1}{\left(x_i - x_j\right)^2} \cdot \text{sgn}\left(x_i - x_j\right)',
    r'\rho_c(\tau) = \rho_1(\tau)\rho_2(\tau + \delta T)',
    r'H_{i,M} = (\hbar/2)\Omega^{(i)}\sigma_x^{(i)}\prod_{m=1}^{M} \exp\left[i\eta_{i,m}\left[a_m + a_m^\dagger\right)\right]',
    r'P_e(t) = \frac{1}{2N} \left[ 1 - \sum_{n=0}^{\infty} \sum_{i=0}^{N} P_n \cos \left( \Omega_n^{(i)} t \right) \right]',
]

fixed = [
    (2, r'H_{i,M} = (\hbar/2)\Omega^{(i)}\sigma_x^{(i)}\prod_{m=1}^{M} \exp\left[i\eta_{i,m}\left(a_m + a_m^\dagger\right)\right]'),
]

write_formula_file('/root/latex_formula_extraction.md', originals, fixed)
```

### Step 7: Verify the Output

```bash
# Check the file looks correct
cat /root/latex_formula_extraction.md

# Verify each line is wrapped in $$
grep -c '^\$\$.*\$\$$' /root/latex_formula_extraction.md

# Check for blank lines (should be exactly one, between originals and fixes)
grep -n '^$' /root/latex_formula_extraction.md

# Confirm no duplicate formulas
sort /root/latex_formula_extraction.md | uniq -d
```

---

## Fallback: Using pymupdf if marker-pdf is Unavailable

If `marker-pdf` is not installed, use `pymupdf` to extract raw text and then manually identify formula regions:

```python
import fitz  # pymupdf

def extract_text_by_page(pdf_path: str) -> dict:
    """Extract text from each page of a PDF."""
    doc = fitz.open(pdf_path)
    pages = {}
    for i, page in enumerate(doc):
        pages[i + 1] = page.get_text("text")
    doc.close()
    return pages

pages = extract_text_by_page('/path/to/latex_paper.pdf')
for page_num, text in pages.items():
    print(f"=== Page {page_num} ===")
    print(text[:500])
```

Note: `pymupdf` extracts plain text, not LaTeX. You'll need to manually reconstruct LaTeX from the mathematical symbols in the text. This is significantly harder and error-prone — prefer `marker-pdf` whenever possible.

---

## Fallback: Using pdftotext

```bash
# Extract text preserving layout
pdftotext -layout /path/to/latex_paper.pdf /tmp/paper_text.txt
cat /tmp/paper_text.txt
```

Again, this gives plain text, not LaTeX. Use only as a last resort to understand the structure of the paper.

---

## Common Pitfalls

### 1. marker-pdf Takes a Long Time — Don't Kill It

`marker_single` uses ML models and can consume 300%+ CPU for several minutes on a typical paper. Do not interrupt it. Check progress with:

```bash
ps aux | grep -E "marker|python" | grep -v grep
ls -la /root/marker_output/ 2>/dev/null
```

### 2. Bracket Mismatch is the Most Common Syntax Error

OCR frequently confuses `(` and `[` in `\left...\right` pairs. Always check that every `\left[` is closed by `\right]` and every `\left(` is closed by `\right)`. A mismatch like `\left[...\right)` will fail to render.

### 3. Do NOT Fix Formula Meaning — Only Fix Syntax

The task is to fix LaTeX syntax errors (bracket mismatches, typos, unbalanced braces), NOT to correct the physics or mathematics. Even if a formula is physically wrong, leave it as-is unless there's a clear rendering error.

Examples of what to fix:
- `\left[a_m + a_m^\dagger\right)` → `\left(a_m + a_m^\dagger\right)` (bracket mismatch)
- `\sigm_x` → `\sigma_x` (typo)
- Unbalanced `{` or `}` braces

Examples of what NOT to fix:
- Wrong physical constants
- Incorrect summation limits (unless clearly a typo like `\infyt` → `\infty`)
- Different notation style than you'd prefer

### 4. Strip Equation Numbers and Trailing Punctuation

Papers often end display equations with a comma or period (grammatically part of the surrounding sentence). These must be removed. Equation numbers like `(1)` or `(A.3)` at the end of a formula line must also be stripped.

### 5. Only Include Display-Mode Formulas

The task asks for formulas "in their own line" — i.e., display-mode math, not inline math. In the marker output, display math appears as `$$ ... $$` blocks. Inline math appears as `$ ... $`. Do not include inline formulas.

### 6. Preserve Page Order

Write formulas in the order they appear in the paper (page 1 first, then page 2, etc.). The marker output is already in page order, so process it sequentially.

### 7. No Duplicate Formulas in the Original Section

If the same formula appears multiple times in the paper (e.g., repeated in a summary), include it only once in the original section. The fixed section should only contain formulas that actually had syntax errors — do not add a "fixed" copy of a formula that was already correct.

### 8. Output File Must End with a Newline

Some validators check for a trailing newline. Always end the file with `\n`.

---

## Quick Reference: marker-pdf Output Structure

After running `marker_single input.pdf --output_dir /root/marker_output`, the output is:

```
/root/marker_output/
└── input/                    # directory named after the PDF (without .pdf)
    ├── input.md              # main markdown file with LaTeX math
    ├── input_meta.json       # metadata
    └── images/               # extracted images (if any)
```

Display formulas in the `.md` file look like:

```markdown
Some prose text here.

$$\frac{d^2x}{dt^2} = -\omega_0^2 x$$

More prose text.
```

---

## Complete End-to-End Script

```python
#!/usr/bin/env python3
"""
Extract display-mode LaTeX formulas from a research paper PDF.
Requires: marker-pdf installed (pip install marker-pdf)
"""

import re
import subprocess
import os
import sys
from pathlib import Path


def run_marker(pdf_path: str, output_dir: str) -> str:
    """Run marker_single and return path to output markdown file."""
    pdf_name = Path(pdf_path).stem
    md_path = os.path.join(output_dir, pdf_name, f"{pdf_name}.md")
    
    if os.path.exists(md_path):
        print(f"Using existing marker output: {md_path}")
        return md_path
    
    print(f"Running marker_single on {pdf_path}...")
    result = subprocess.run(
        ["marker_single", pdf_path, "--output_dir", output_dir],
        capture_output=True, text=True
    )
    
    if not os.path.exists(md_path):
        raise FileNotFoundError(f"marker output not found at {md_path}. stderr: {result.stderr}")
    
    return md_path


def extract_display_formulas(md_path: str) -> list:
    """Extract all $$ ... $$ display formulas from a markdown file."""
    with open(md_path, 'r') as f:
        content = f.read()
    
    # Match $$ ... $$ blocks (possibly multiline)
    pattern = re.compile(r'\$\$(.*?)\$\$', re.DOTALL)
    matches = pattern.findall(content)
    
    formulas = []
    for m in matches:
        formula = m.strip()
        if formula:
            formulas.append(formula)
    
    return formulas


def clean_formula(raw: str) -> str:
    """Remove tags, labels, equation numbers, trailing punctuation."""
    raw = re.sub(r'\\tag\*?\{[^}]*\}', '', raw)
    raw = re.sub(r'\\label\{[^}]*\}', '', raw)
    raw = re.sub(r'\s*\(\s*[A-Za-z0-9.]+\s*\)\s*$', '', raw)
    raw = re.sub(r'[,\.]\s*$', '', raw)
    return raw.strip()


def find_bracket_mismatches(formula: str) -> list:
    """Find mismatched \\left / \\right pairs."""
    left_tokens = re.findall(r'\\left\s*([(\[{|.])', formula)
    right_tokens = re.findall(r'\\right\s*([)\]|}|.])', formula)
    pairs = {'(': ')', '[': ']', '{': '}', '|': '|', '.': '.'}
    mismatches = []
    for l, r in zip(left_tokens, right_tokens):
        expected = pairs.get(l)
        if expected and r != expected:
            mismatches.append((l, r))
    return mismatches


def check_brace_balance(formula: str) -> bool:
    depth = 0
    i = 0
    while i < len(formula):
        if formula[i] == '\\':
            i += 2
            continue
        if formula[i] == '{':
            depth += 1
        elif formula[i] == '}':
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def detect_and_fix(formula: str) -> tuple:
    """
    Returns (has_error: bool, fixed_formula: str).
    Only fixes bracket mismatches and brace imbalance.
    """
    mismatches = find_bracket_mismatches(formula)
    if mismatches:
        # Report but don't auto-fix — requires manual inspection
        print(f"  BRACKET MISMATCH in: {formula[:80]}...")
        print(f"  Mismatches: {mismatches}")
        return True, None  # Signal that manual fix is needed
    
    if not check_brace_balance(formula):
        print(f"  BRACE IMBALANCE in: {formula[:80]}...")
        return True, None
    
    return False, formula


def write_output(output_path: str, originals: list, fixed_pairs: list):
    """Write formulas to output markdown file."""
    lines = [f'$${f}$$' for f in originals]
    if fixed_pairs:
        lines.append('')
        lines.extend(f'$${fix}$$' for _, fix in fixed_pairs)
    
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    
    print(f"Wrote {len(originals)} original + {len(fixed_pairs)} fixed formulas to {output_path}")


def main(pdf_path: str, output_path: str):
    output_dir = '/root/marker_output'
    os.makedirs(output_dir, exist_ok=True)
    
    md_path = run_marker(pdf_path, output_dir)
    raw_formulas = extract_display_formulas(md_path)
    print(f"Found {len(raw_formulas)} display formulas")
    
    cleaned = [clean_formula(f) for f in raw_formulas]
    
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for f in cleaned:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    
    # Detect syntax errors — fixed_pairs must be filled in manually
    # based on inspection of the mismatches reported above
    fixed_pairs = []
    for i, f in enumerate(unique):
        has_error, _ = detect_and_fix(f)
        if has_error:
            print(f"  Formula {i} needs manual fix: {f}")
    
    # After manual inspection, add fixed versions here:
    # fixed_pairs = [(index, "corrected formula latex")]
    
    write_output(output_path, unique, fixed_pairs)


if __name__ == '__main__':
    pdf = sys.argv[1] if len(sys.argv) > 1 else '/root/latex_paper.pdf'
    out = sys.argv[2] if len(sys.argv) > 2 else '/root/latex_formula_extraction.md'
    main(pdf, out)
```

---

## Bracket Fix Reference

When OCR produces a bracket mismatch, use this table to determine the correct fix:

| OCR Output | Likely Correct Fix | Reasoning |
|---|---|---|
| `\left[...\right)` | `\left(...\right)` | Inner group is parentheses, outer is square bracket |
| `\left(...\right]` | `\left(...\right)` | Closing bracket wrong |
| `\left\{...\right)` | `\left\{...\right\}` | Curly brace must close with curly brace |
| `\left[...\right]` | Keep as-is | Already matched |

Always cross-reference with the original PDF rendering to confirm the correct bracket type.
