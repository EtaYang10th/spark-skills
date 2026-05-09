---
title: LaTeX Formula Extraction from Research Paper PDFs
category: latex-formula-extraction
domain: document-processing
tags:
  - pdf-parsing
  - latex
  - formula-extraction
  - marker-pdf
  - pymupdf
dependencies:
  - marker-pdf==1.3.3
  - pymupdf
  - pillow
environment: ubuntu-24.04-python3
---

# LaTeX Formula Extraction from Research Paper PDFs

## Overview

This skill covers extracting all standalone (display-mode) LaTeX formulas from a research paper PDF, formatting them as `$$ ... $$` blocks, detecting syntax errors (bracket mismatches, typos), and writing corrected versions. The output is a markdown file with one formula per line.

"Standalone formula" means a formula that occupies its own line in the PDF — not inline math embedded in prose.

---

## High-Level Workflow

1. **Inventory available tools.** Check for `marker_single` (marker-pdf CLI), `pymupdf`/`fitz`, `pdftotext`, and image rendering capabilities. The strategy depends on what's installed.

2. **Run marker-pdf on the PDF.** `marker_single` converts the PDF to markdown and preserves LaTeX formulas with `$$...$$` delimiters. This gives you a strong first pass but is NOT sufficient alone — it can miss formulas, hallucinate content, or mangle bracket nesting.

3. **Render every page to an image.** Use PyMuPDF (`fitz`) to render each page at high DPI. Visually inspect every page to build a ground-truth inventory of standalone formulas. Count them and note their equation numbers.

4. **Cross-reference marker output against visual inspection.** For each formula you see in the images, confirm it appears in the marker output. For any missing formula, manually transcribe it from the image + marker text context.

5. **Clean each formula.** Remove equation tags like `(1)`, `(2)`, trailing commas, periods, and any `\tag{...}` commands. Preserve only the mathematical content.

6. **Validate LaTeX syntax.** Check every formula for:
   - Mismatched `\left`/`\right` bracket pairs (e.g., `\left[` paired with `\right)`)
   - Unclosed braces `{}`
   - Malformed commands (e.g., `\frc` instead of `\frac`)
   - Missing superscript/subscript braces

7. **Write the output file.** Original formulas first (in document order), then a blank line, then any fixed formulas. One formula per line, each wrapped in `$$...$$`.

8. **Verify the output.** Re-read the file, confirm line count, confirm no duplicates among originals, confirm each `$$` pair is balanced.

---

## Step 1: Check Available Tools

```bash
# Check for marker-pdf CLI
which marker_single 2>/dev/null && marker_single --help 2>&1 | head -5

# Check for PyMuPDF
python3 -c "import fitz; print('PyMuPDF version:', fitz.__version__)"

# Check for other PDF tools
which pdftotext 2>/dev/null
pip list 2>/dev/null | grep -iE "marker|pymupdf|pdfminer|pdf|fitz" | head -20
```

**Decision point:** If `marker_single` is available, use it as the primary extractor. If not, fall back to PyMuPDF text extraction + manual LaTeX reconstruction. Marker is strongly preferred because it preserves LaTeX notation.

---

## Step 2: Run marker-pdf to Extract Markdown with Formulas

```bash
marker_single /root/latex_paper.pdf \
  --output_format markdown \
  --disable_image_extraction \
  --output_dir /tmp/marker_out 2>&1
```

Then read the output:

```python
import glob, os

# marker_single creates a subdirectory; find the .md file
md_files = glob.glob("/tmp/marker_out/**/*.md", recursive=True)
for f in md_files:
    with open(f) as fh:
        content = fh.read()
    print(f"=== {f} ===")
    print(content)
```

**What to look for in marker output:**
- Lines containing `$$...$$` — these are the extracted display formulas
- Lines containing `$...$` — these are inline formulas (skip them for this task)
- Equation numbers like `(1)`, `(2)` near formulas — these confirm formula identity but must be stripped from the final output

---

## Step 3: Render Every Page as an Image for Visual Verification

This is the most critical quality gate. Never trust marker output alone.

```python
import fitz  # PyMuPDF

doc = fitz.open("/root/latex_paper.pdf")
print(f"Total pages: {len(doc)}")

for page_num in range(len(doc)):
    page = doc[page_num]
    # Render at 2x zoom for clarity (144 DPI)
    mat = fitz.Matrix(2, 2)
    pix = page.get_pixmap(matrix=mat)
    out_path = f"/tmp/page_{page_num + 1}.png"
    pix.save(out_path)
    print(f"Saved {out_path} ({pix.width}x{pix.height})")

doc.close()
```

Then visually inspect each image. For each page, note:
- Whether any standalone formulas exist
- Their equation numbers (if any)
- Their approximate content (to match against marker output)

**Key insight:** Not every page has standalone formulas. Page 1 of a paper is often all prose. Formulas typically appear in the methods/results sections.

---

## Step 4: Cross-Reference and Build Formula Inventory

```python
import re

# Parse marker output for display formulas
marker_content = open("/tmp/marker_out/latex_paper/latex_paper.md").read()

# Extract all $$...$$ blocks
display_formulas = re.findall(r'\$\$(.*?)\$\$', marker_content, re.DOTALL)

print(f"Marker found {len(display_formulas)} display formulas:")
for i, f in enumerate(display_formulas):
    print(f"  [{i+1}] {f.strip()[:80]}...")
```

Compare this count against your visual inspection. If marker missed any, you'll need to manually transcribe them using the image as reference and the surrounding marker text for context.

---

## Step 5: Clean Formulas

Remove equation tags, trailing punctuation, and normalize whitespace:

```python
def clean_formula(latex: str) -> str:
    """Remove equation tags, trailing punctuation, and normalize whitespace."""
    formula = latex.strip()

    # Remove \tag{...} commands
    formula = re.sub(r'\\tag\{[^}]*\}', '', formula)

    # Remove trailing equation numbers like (1), (2) etc.
    # These appear at the end of the formula string
    formula = re.sub(r'\s*\(\d+\)\s*$', '', formula)

    # Remove trailing commas, periods, semicolons
    formula = re.sub(r'[,;.]+\s*$', '', formula)

    # Normalize internal whitespace (collapse multiple spaces)
    formula = re.sub(r'\s+', ' ', formula).strip()

    return formula
```

---

## Step 6: Validate LaTeX Syntax

This is where most errors hide. The most common issue is **mismatched bracket pairs** from `\left`/`\right` commands.

```python
def validate_latex_brackets(formula: str) -> list:
    """
    Check for mismatched \left/\right bracket pairs.
    Returns a list of issues found.
    """
    issues = []

    # Find all \left and \right delimiters
    left_pattern = r'\\left\s*([(\[{|.])'
    right_pattern = r'\\right\s*([)\]}|.])'

    lefts = [(m.start(), m.group(1)) for m in re.finditer(left_pattern, formula)]
    rights = [(m.start(), m.group(1)) for m in re.finditer(right_pattern, formula)]

    # Matching pairs
    bracket_pairs = {'(': ')', '[': ']', '{': '}', '|': '|', '.': '.'}

    if len(lefts) != len(rights):
        issues.append(f"Unequal \\left ({len(lefts)}) and \\right ({len(rights)}) count")

    # Check each pair in order
    for i, ((lpos, lbr), (rpos, rbr)) in enumerate(zip(lefts, rights)):
        expected_right = bracket_pairs.get(lbr, lbr)
        if rbr != expected_right:
            issues.append(
                f"Pair {i+1}: \\left{lbr} at pos {lpos} paired with "
                f"\\right{rbr} at pos {rpos} — expected \\right{expected_right}"
            )

    # Check for unclosed braces (simple depth check)
    depth = 0
    for ch in formula:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        if depth < 0:
            issues.append("Extra closing brace '}' found")
            break
    if depth > 0:
        issues.append(f"Unclosed braces: {depth} '{' without matching '}}'")

    return issues


def fix_bracket_mismatch(formula: str) -> str:
    """
    Fix the most common bracket mismatch: \left[ ... \right) -> \left( ... \right)
    Uses the RIGHT delimiter as the authority (since it's usually correct in PDFs)
    and fixes the LEFT to match.

    Also handles the reverse case.
    """
    fixed = formula

    # Strategy: for each \right delimiter, find its matching \left and ensure
    # they use compatible brackets. Prefer matching to what the PDF shows.
    # In practice, the most common error is a [ that should be ( or vice versa.

    left_matches = list(re.finditer(r'\\left\s*([(\[{|.])', fixed))
    right_matches = list(re.finditer(r'\\right\s*([)\]}|.])', fixed))

    bracket_pairs = {'(': ')', '[': ']', '{': '}', '|': '|', '.': '.'}
    reverse_pairs = {v: k for k, v in bracket_pairs.items()}

    if len(left_matches) == len(right_matches):
        # Process from right to left to preserve positions
        replacements = []
        for lm, rm in zip(left_matches, right_matches):
            lbr = lm.group(1)
            rbr = rm.group(1)
            expected_right = bracket_pairs.get(lbr, lbr)
            if rbr != expected_right:
                # Fix the left bracket to match the right
                correct_left = reverse_pairs.get(rbr, rbr)
                replacements.append((lm.start(1), lm.end(1), correct_left))

        # Apply replacements from right to left
        for start, end, replacement in reversed(replacements):
            fixed = fixed[:start] + replacement + fixed[end:]

    return fixed
```

**Critical note on what to fix vs. what to leave alone:**
- FIX: Bracket mismatches (`\left[...\right)` → `\left(...\right)`), typos in command names, unclosed braces
- DO NOT FIX: Physics meaning errors, stylistic choices, display preferences
- The task explicitly says: "Only detect syntax problems / typos. Do not worry about formulas' physics meaning."

---

## Step 7: Write the Output File

```python
def write_output(original_formulas: list, fixed_formulas: list, output_path: str):
    """
    Write the final markdown file.
    original_formulas: list of cleaned LaTeX strings (document order)
    fixed_formulas: list of (original_index, fixed_latex) tuples
    """
    lines = []

    # Original formulas
    for f in original_formulas:
        lines.append(f"$${f}$$")

    # Blank line separator before fixes (if any)
    if fixed_formulas:
        lines.append("")
        for idx, fixed in fixed_formulas:
            lines.append(f"$${fixed}$$")

    # Trailing newline
    lines.append("")

    with open(output_path, 'w') as fh:
        fh.write("\n".join(lines))

    print(f"Written {len(original_formulas)} original + {len(fixed_formulas)} fixed formulas to {output_path}")
```

---

## Step 8: Verify the Output

```python
def verify_output(output_path: str):
    """Final verification checks."""
    with open(output_path) as fh:
        content = fh.read()

    lines = [l for l in content.strip().split('\n') if l.strip()]

    print(f"Total non-empty lines: {len(lines)}")

    for i, line in enumerate(lines):
        line = line.strip()
        if not (line.startswith('$$') and line.endswith('$$')):
            print(f"  WARNING: Line {i+1} not wrapped in $$: {line[:60]}...")
        # Check for duplicate formulas
        inner = line[2:-2].strip()
        for j, other_line in enumerate(lines):
            if i != j and other_line.strip()[2:-2].strip() == inner:
                print(f"  WARNING: Line {i+1} duplicates line {j+1}")

    print("Verification complete.")
```

---

## Common Pitfalls

### 1. Trusting marker-pdf output blindly
Marker is a good first pass but can miss formulas, merge adjacent formulas, or introduce artifacts. **Always render pages as images and visually count formulas.**

### 2. Mismatched `\left`/`\right` brackets
This is the single most common LaTeX syntax error in extracted formulas. The PDF renders fine because the TeX engine is forgiving, but the raw LaTeX has mismatched delimiters (e.g., `\left[...\right)` when it should be `\left(...\right)`). Always run bracket validation on every formula.

### 3. Forgetting to strip equation tags
Formulas in papers have equation numbers like `(1)`, `(2)`. These must be removed. Also strip `\tag{...}` commands and trailing punctuation (commas, periods).

### 4. Including inline formulas
The task asks for standalone/display formulas only — those on their own line. Do not include `$...$` inline math from prose paragraphs.

### 5. Over-fixing formulas
Only fix genuine syntax errors (bracket mismatches, typos in command names). Do NOT:
- Change `\sum_{i=0}^{N}` to `\sum_{i=1}^{N}` even if the physics is wrong
- Rearrange terms for "clarity"
- Add `\displaystyle` or other formatting improvements

### 6. Duplicate formulas in the output
The fixed version of a formula is an ADDITIONAL line, not a replacement. The original (with the error) stays in the originals section. But don't accidentally duplicate an original formula.

### 7. Not handling multi-line formulas from marker
Marker sometimes splits a single formula across multiple lines. The `re.DOTALL` flag in the regex handles this, but verify that each extracted formula is actually one logical formula.

---

## Reference Implementation

This is a complete, end-to-end script. Copy, adapt the input/output paths, and run.

```python
#!/usr/bin/env python3
"""
Complete pipeline: Extract all standalone LaTeX formulas from a research paper PDF.

Usage:
    python3 extract_formulas.py /root/latex_paper.pdf /root/latex_formula_extraction.md

Requirements:
    - marker-pdf (marker_single CLI)
    - PyMuPDF (fitz)
    - pillow
"""

import subprocess
import sys
import os
import re
import glob
import fitz  # PyMuPDF


# ─── Configuration ───────────────────────────────────────────────────────────

PDF_PATH = "/root/latex_paper.pdf"
OUTPUT_PATH = "/root/latex_formula_extraction.md"
MARKER_OUT_DIR = "/tmp/marker_out"
PAGE_IMAGE_DIR = "/tmp/pdf_pages"


# ─── Step 1: Run marker-pdf ─────────────────────────────────────────────────

def run_marker(pdf_path: str, output_dir: str) -> str:
    """Run marker_single and return the extracted markdown content."""
    # Clean previous output
    subprocess.run(["rm", "-rf", output_dir], check=False)
    os.makedirs(output_dir, exist_ok=True)

    result = subprocess.run(
        [
            "marker_single", pdf_path,
            "--output_format", "markdown",
            "--disable_image_extraction",
            "--output_dir", output_dir,
        ],
        capture_output=True, text=True, timeout=120
    )

    if result.returncode != 0:
        print(f"marker_single stderr: {result.stderr[:500]}")
        # Don't fail — we can still work with images

    # Find the output markdown file
    md_files = glob.glob(os.path.join(output_dir, "**/*.md"), recursive=True)
    if not md_files:
        print("WARNING: marker produced no markdown output")
        return ""

    with open(md_files[0]) as fh:
        content = fh.read()

    print(f"Marker output: {len(content)} chars from {md_files[0]}")
    return content


# ─── Step 2: Render pages as images ─────────────────────────────────────────

def render_pages(pdf_path: str, image_dir: str) -> list:
    """Render each page to PNG at 2x zoom. Returns list of image paths."""
    os.makedirs(image_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    paths = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(2, 2)  # 144 DPI
        pix = page.get_pixmap(matrix=mat)
        out_path = os.path.join(image_dir, f"page_{page_num + 1}.png")
        pix.save(out_path)
        paths.append(out_path)
        print(f"Rendered page {page_num + 1}: {pix.width}x{pix.height}")

    doc.close()
    return paths


# ─── Step 3: Extract display formulas from marker output ────────────────────

def extract_display_formulas(marker_content: str) -> list:
    """Extract all $$...$$ display formulas from marker markdown."""
    # Match $$ blocks (may span multiple lines)
    formulas = re.findall(r'\$\$(.*?)\$\$', marker_content, re.DOTALL)
    return [f.strip() for f in formulas if f.strip()]


# ─── Step 4: Clean a formula ────────────────────────────────────────────────

def clean_formula(latex: str) -> str:
    """Remove equation tags, trailing punctuation, normalize whitespace."""
    formula = latex.strip()

    # Remove \tag{...}
    formula = re.sub(r'\\tag\{[^}]*\}', '', formula)

    # Remove trailing equation numbers: (1), (2), etc.
    formula = re.sub(r'\s*\(\d+\)\s*$', '', formula)

    # Remove leading equation numbers too (some PDFs put them at the start)
    formula = re.sub(r'^\s*\(\d+\)\s*', '', formula)

    # Remove trailing commas, periods, semicolons
    formula = re.sub(r'[,;.]+\s*$', '', formula)

    # Collapse internal whitespace
    formula = re.sub(r'\s+', ' ', formula).strip()

    return formula


# ─── Step 5: Validate and fix LaTeX syntax ──────────────────────────────────

def find_bracket_issues(formula: str) -> list:
    """Check for mismatched \\left/\\right bracket pairs."""
    issues = []

    left_pattern = r'\\left\s*([(\[{|.])'
    right_pattern = r'\\right\s*([)\]}|.])'

    lefts = [(m.start(), m.group(1)) for m in re.finditer(left_pattern, formula)]
    rights = [(m.start(), m.group(1)) for m in re.finditer(right_pattern, formula)]

    bracket_pairs = {'(': ')', '[': ']', '{': '}', '|': '|', '.': '.'}

    if len(lefts) != len(rights):
        issues.append(f"Unequal \\left ({len(lefts)}) / \\right ({len(rights)}) count")

    for i, ((lpos, lbr), (rpos, rbr)) in enumerate(zip(lefts, rights)):
        expected = bracket_pairs.get(lbr, lbr)
        if rbr != expected:
            issues.append(f"Pair {i+1}: \\left{lbr} (pos {lpos}) vs \\right{rbr} (pos {rpos})")

    # Simple brace depth check
    depth = 0
    for ch in formula:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        if depth < 0:
            issues.append("Extra closing brace '}'")
            break
    if depth > 0:
        issues.append(f"{depth} unclosed '{{' brace(s)")

    return issues


def fix_bracket_mismatches(formula: str) -> str:
    """
    Fix mismatched \\left/\\right pairs.
    Strategy: the \\right delimiter is usually correct (it's what the PDF shows
    at the closing side). Fix the \\left to produce a matching opening bracket.
    """
    fixed = formula

    left_matches = list(re.finditer(r'\\left\s*([(\[{|.])', fixed))
    right_matches = list(re.finditer(r'\\right\s*([)\]}|.])', fixed))

    bracket_pairs = {'(': ')', '[': ']', '{': '}', '|': '|', '.': '.'}
    reverse_pairs = {v: k for k, v in bracket_pairs.items()}

    if len(left_matches) != len(right_matches):
        return fixed  # Can't auto-fix unequal counts safely

    replacements = []
    for lm, rm in zip(left_matches, right_matches):
        lbr = lm.group(1)
        rbr = rm.group(1)
        expected_right = bracket_pairs.get(lbr, lbr)
        if rbr != expected_right:
            correct_left = reverse_pairs.get(rbr, rbr)
            replacements.append((lm.start(1), lm.end(1), correct_left))

    # Apply from right to left to preserve positions
    for start, end, replacement in reversed(replacements):
        fixed = fixed[:start] + replacement + fixed[end:]

    return fixed


def check_common_typos(formula: str) -> str:
    """Fix common LaTeX command typos."""
    typo_map = {
        r'\\frc{': r'\\frac{',
        r'\\sqr{': r'\\sqrt{',
        r'\\lamda': r'\\lambda',
        r'\\apha': r'\\alpha',
        r'\\bea': r'\\beta',
        r'\\infity': r'\\infty',
        r'\\parial': r'\\partial',
        r'\\dager': r'\\dagger',
    }
    fixed = formula
    for typo, correct in typo_map.items():
        fixed = re.sub(typo, correct, fixed)
    return fixed


# ─── Step 6: Full pipeline ──────────────────────────────────────────────────

def main():
    pdf_path = PDF_PATH
    output_path = OUTPUT_PATH

    # --- Run marker ---
    print("=" * 60)
    print("STEP 1: Running marker-pdf...")
    marker_content = run_marker(pdf_path, MARKER_OUT_DIR)

    # --- Render pages for visual cross-reference ---
    print("=" * 60)
    print("STEP 2: Rendering pages as images...")
    page_images = render_pages(pdf_path, PAGE_IMAGE_DIR)
    print(f"  Rendered {len(page_images)} pages. Inspect them at {PAGE_IMAGE_DIR}/")
    print("  (In an agentic setting, use vision to count standalone formulas per page)")

    # --- Extract formulas from marker output ---
    print("=" * 60)
    print("STEP 3: Extracting display formulas from marker output...")
    raw_formulas = extract_display_formulas(marker_content)
    print(f"  Found {len(raw_formulas)} raw display formulas")

    # --- IMPORTANT: Visual verification step ---
    # In an agentic setting, you would now:
    #   1. Look at each page image
    #   2. Count standalone formulas per page
    #   3. Compare against raw_formulas
    #   4. Manually add any formulas marker missed
    #
    # For this reference implementation, we proceed with marker's output.
    # If marker missed formulas, add them to raw_formulas here:
    #
    # raw_formulas.append(r"\text{manually transcribed formula}")

    # --- Clean formulas ---
    print("=" * 60)
    print("STEP 4: Cleaning formulas...")
    cleaned = []
    for i, f in enumerate(raw_formulas):
        c = clean_formula(f)
        print(f"  [{i+1}] {c[:80]}{'...' if len(c) > 80 else ''}")
        cleaned.append(c)

    # --- Validate and fix ---
    print("=" * 60)
    print("STEP 5: Validating LaTeX syntax...")
    fixed_formulas = []  # list of (original_index, fixed_latex)

    for i, formula in enumerate(cleaned):
        issues = find_bracket_issues(formula)

        # Also check for common typos
        typo_fixed = check_common_typos(formula)
        if typo_fixed != formula:
            issues.append("Common typo detected")

        if issues:
            print(f"  Formula [{i+1}] has issues: {issues}")
            fixed = fix_bracket_mismatches(formula)
            fixed = check_common_typos(fixed)

            if fixed != formula:
                print(f"    FIXED: {fixed[:80]}...")
                fixed_formulas.append((i, fixed))
            else:
                print(f"    Could not auto-fix")

    # --- Write output ---
    print("=" * 60)
    print("STEP 6: Writing output...")

    lines = []
    for f in cleaned:
        lines.append(f"$${f}$$")

    if fixed_formulas:
        lines.append("")  # blank separator
        for idx, fixed in fixed_formulas:
            lines.append(f"$${fixed}$$")

    lines.append("")  # trailing newline

    with open(output_path, 'w') as fh:
        fh.write("\n".join(lines))

    print(f"  Written to {output_path}")
    print(f"  {len(cleaned)} original formulas + {len(fixed_formulas)} fixed formulas")

    # --- Verify ---
    print("=" * 60)
    print("STEP 7: Verification...")
    with open(output_path) as fh:
        content = fh.read()

    nonempty = [l for l in content.strip().split('\n') if l.strip()]
    print(f"  Non-empty lines: {len(nonempty)}")

    for i, line in enumerate(nonempty):
        line = line.strip()
        if not (line.startswith('$$') and line.endswith('$$')):
            print(f"  ERROR: Line {i+1} not wrapped in $$")
        # Check for duplicates among originals only
        if i < len(cleaned):
            inner = line[2:-2].strip()
            for j in range(len(cleaned)):
                if j != i and nonempty[j].strip()[2:-2].strip() == inner:
                    print(f"  WARNING: Original line {i+1} duplicates line {j+1}")

    print("  Done.")


if __name__ == "__main__":
    main()
```

---

## Domain-Specific Notes

### Formula Identification Heuristics

When visually inspecting page images, standalone formulas typically:
- Are centered on the page (not left-aligned with body text)
- Have vertical whitespace above and below
- Often have equation numbers `(1)`, `(2)` at the right margin
- May span multiple lines (aligned equations)

### Common LaTeX Constructs in Physics Papers

| Pattern | Example | Notes |
|---------|---------|-------|
| Fractions | `\frac{a}{b}` | Nested fractions are common |
| Summations | `\sum_{i=0}^{N}` | Watch for `\substack` in multi-condition sums |
| Products | `\prod_{m=1}^{M}` | Similar syntax to sums |
| Operators | `\exp`, `\cos`, `\sin`, `\text{sgn}` | `\text{sgn}` is common in physics |
| Brackets | `\left( ... \right)` | Most common source of extraction errors |
| Superscripts | `x^{(i)}`, `a_m^\dagger` | Parenthesized superscripts need braces |
| Dagger | `a^\dagger`, `a^{\dagger}` | Common in quantum mechanics |
| Hbar | `\hbar` | Reduced Planck constant |

### Bracket Mismatch Patterns

The most frequent error pattern in marker-pdf output:

```
# WRONG (marker sometimes produces this):
\left[ a_m + a_m^\dagger \right)

# CORRECT (matching brackets):
\left( a_m + a_m^\dagger \right)
```

The fix strategy: trust the `\right` delimiter (it's usually correct because it's the last thing the OCR/parser sees) and adjust the `\left` to match. This follows common mathematical convention where inner groupings use parentheses `()` and outer groupings use brackets `[]`.

### Output Format Specification

```
$$formula_1$$        ← original formula 1 (cleaned, no tags/punctuation)
$$formula_2$$        ← original formula 2
$$formula_3$$        ← original formula 3 (even if it has a syntax error)
$$formula_4$$        ← original formula 4
                     ← blank line separator
$$formula_3_fixed$$  ← fixed version of formula 3 (syntax error corrected)
```

Key rules:
- One formula per line
- Each line starts with `$$` and ends with `$$`
- No blank lines between original formulas
- One blank line before the fixed formulas section
- Fixed formulas are ADDITIONS, not replacements
- No duplicate formulas among the originals
- Trailing newline at end of file