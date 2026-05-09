---
title: "PPTX Reference Formatting — Detect Dangling Titles, Restyle, Reposition, and Generate Reference Slide"
category: pptx-reference-formatting
domain: document-processing
tags:
  - python-pptx
  - pptx
  - slide-formatting
  - font-styling
  - xml-manipulation
  - reference-slide
  - text-box-positioning
dependencies:
  - python-pptx
  - lxml
---

# PPTX Reference Formatting Skill

Detect "dangling" paper titles in a PowerPoint presentation, restyle them (font, size, color, bold), resize/reposition their text boxes, and generate a final Reference slide with deduplicated, auto-numbered titles.

---

## 1. High-Level Workflow

1. **Install dependencies** — `python-pptx` (and `lxml`, usually pulled in automatically).
2. **Load and inspect the PPTX** — Open the file, enumerate every shape on every slide, and identify which text boxes are "dangling paper titles" vs. regular content (titles, body placeholders, images, etc.).
3. **Identify dangling titles** — A dangling title is typically a free-standing text box (`<p:sp>` without a placeholder type, or with a specific placeholder index) that contains a paper-title-like string. Heuristics: it is not the slide title placeholder (`ph type="title"` / `ph type="ctrTitle"`), not the body placeholder (`ph idx="1"`), and contains meaningful text.
4. **Restyle each dangling title** — For every run in every paragraph of the identified shape, set font name, size, color, and bold. You must set the font on three typeface attributes (Latin, East Asian, Complex Script) to satisfy strict validators.
5. **Resize the text box** — Widen the shape so the title fits on a single line. A safe strategy: set width to ~93–95% of slide width, then center horizontally.
6. **Reposition to bottom center** — Compute `x` for horizontal centering, compute `y` so the box sits near the bottom edge of the slide (e.g., bottom margin of ~300,000 EMU / ~0.33 inches).
7. **Normalize text box height** — Set `cy` (height) to a single-line value (~369332 EMU for 16pt text) so the box doesn't span multiple lines.
8. **Build the Reference slide** — Append a new slide using a layout that has a title + body placeholder. Set the title to "Reference". Collect all dangling titles, deduplicate (preserve order), and add each as an auto-numbered bullet paragraph in the body placeholder.
9. **Save and verify** — Save the modified presentation. Re-open it and programmatically verify font properties, positions, the Reference slide content, and auto-numbering XML.

---

## 2. Key Concepts and Constants

### EMU (English Metric Units)

`python-pptx` uses EMUs internally. Key conversions:

```python
from pptx.util import Inches, Pt, Emu

# 1 inch = 914400 EMU
# 1 point = 12700 EMU
# Standard slide: 13.333" × 7.5" → 12192000 × 6858000 EMU
```

### Namespace Map for Raw XML

When you need to query or modify the underlying XML (which you will), use these namespaces:

```python
NS = {
    'a':  'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r':  'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'p':  'http://schemas.openxmlformats.org/presentationml/2006/main',
}
```

### Identifying Placeholder Types

```python
def get_placeholder_type(shape):
    """Return (ph_type, ph_idx) or (None, None) for non-placeholders."""
    if not shape.has_text_frame:
        return None, None
    ph = shape.placeholder_format
    if ph is None:
        return None, None
    return ph.type, ph.idx
```

Placeholder types you'll encounter:
- `PP_PLACEHOLDER.TITLE` (type=15) or `PP_PLACEHOLDER.CENTER_TITLE` (type=3) — slide title
- `PP_PLACEHOLDER.BODY` (idx=1) — body/content area
- `PP_PLACEHOLDER.SUBTITLE` (type=4) — subtitle
- `None` — free-form text box (often the dangling title)

---

## 3. Step-by-Step with Code

### Step 1: Install Dependencies

```bash
pip install --break-system-packages python-pptx lxml
```

Use `--break-system-packages` on Ubuntu 24.04+ where the system Python is externally managed.

### Step 2: Load and Inspect

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from lxml import etree
import copy

prs = Presentation('/root/input.pptx')
slide_width = prs.slide_width   # e.g., 12192000
slide_height = prs.slide_height # e.g., 6858000

for i, slide in enumerate(prs.slides, 1):
    print(f"\n=== Slide {i} ===")
    for shape in slide.shapes:
        ph = shape.placeholder_format
        ph_info = f"ph_type={ph.type}, idx={ph.idx}" if ph else "no placeholder"
        text = shape.text_frame.text[:80] if shape.has_text_frame else "(no text)"
        print(f"  Shape: {shape.shape_id} | {ph_info} | {text}")
```

### Step 3: Identify Dangling Titles

A "dangling paper title" is a text box that:
- Has text content
- Is NOT a title placeholder (type 15, 3) and NOT a body placeholder (idx 1)
- Contains a paper-title-like string (heuristic: longer than ~10 chars, not a single word)

```python
def is_dangling_title(shape):
    """Determine if a shape is a dangling paper title text box."""
    if not shape.has_text_frame:
        return False
    text = shape.text_frame.text.strip()
    if not text or len(text) < 10:
        return False
    ph = shape.placeholder_format
    if ph is not None:
        # Skip title placeholders and body placeholders
        if ph.type in (15, 3, 4):  # TITLE, CENTER_TITLE, SUBTITLE
            return False
        if ph.idx == 1:  # BODY
            return False
    return True
```

**Important**: The exact heuristic depends on the task. Some presentations use free text boxes (no placeholder) for dangling titles. Others might use a placeholder with a non-standard index. Always inspect the XML first.

### Step 4: Restyle Font Properties

Setting font via the `python-pptx` API is straightforward, but for strict validators you must also set East Asian and Complex Script typefaces via raw XML:

```python
def restyle_runs(shape, font_name='Arial', font_size=16, font_color='989596', bold=False):
    """Apply font styling to all runs in a text frame."""
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.name = font_name
            run.font.size = Pt(font_size)
            run.font.bold = bold
            run.font.color.rgb = RGBColor.from_string(font_color)

            # Set East Asian and Complex Script typefaces via XML
            rPr = run._r.find('{http://schemas.openxmlformats.org/drawingml/2006/main}rPr')
            if rPr is None:
                rPr = run._r.makeelement(
                    '{http://schemas.openxmlformats.org/drawingml/2006/main}rPr', {}
                )
                run._r.insert(0, rPr)

            # Latin typeface (python-pptx sets this, but be explicit)
            a_ns = 'http://schemas.openxmlformats.org/drawingml/2006/main'
            for tag in ['latin', 'ea', 'cs']:
                elem = rPr.find(f'{{{a_ns}}}{tag}')
                if elem is None:
                    elem = rPr.makeelement(f'{{{a_ns}}}{tag}', {})
                    rPr.append(elem)
                elem.set('typeface', font_name)
```

### Step 5: Resize and Reposition

```python
def reposition_bottom_center(shape, slide_width, slide_height,
                              margin_x=400000, bottom_margin=300000,
                              box_height=369332):
    """
    Resize shape to nearly full slide width and position at bottom center.

    Args:
        margin_x: horizontal margin on each side (EMU)
        bottom_margin: gap between bottom of box and bottom of slide (EMU)
        box_height: fixed height for single-line text (EMU)
    """
    new_width = slide_width - 2 * margin_x
    new_x = margin_x
    new_y = slide_height - bottom_margin - box_height

    shape.left = new_x
    shape.top = new_y
    shape.width = new_width
    shape.height = box_height
```

**Why 369332 EMU for height?** For 16pt text with default line spacing, this is approximately one line height. Adjust if your font size differs. You can calculate: `Pt(16).emu` = 203200, plus ~80% padding ≈ 365760. The exact value depends on the template; inspect the original XML to calibrate.

### Step 6: Center-Align Text

```python
def center_align_paragraphs(shape):
    """Set all paragraphs in the shape to center alignment."""
    for paragraph in shape.text_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.CENTER
```

### Step 7: Build the Reference Slide

```python
def add_reference_slide(prs, paper_titles):
    """
    Add a Reference slide at the end with deduplicated, auto-numbered paper titles.

    Args:
        prs: Presentation object
        paper_titles: list of paper title strings (may contain duplicates)
    """
    # Deduplicate while preserving order
    seen = set()
    unique_titles = []
    for t in paper_titles:
        if t not in seen:
            seen.add(t)
            unique_titles.append(t)

    # Pick a layout with title + body (usually index 1, but inspect)
    layout = prs.slide_layouts[1]  # Title + Content layout
    slide = prs.slides.add_slide(layout)

    # Set title
    slide.shapes.title.text = "Reference"

    # Find body placeholder (idx=1)
    body_ph = None
    for shape in slide.placeholders:
        if shape.placeholder_format.idx == 1:
            body_ph = shape
            break

    if body_ph is None:
        raise RuntimeError("No body placeholder found in layout")

    # Clear default paragraphs
    tf = body_ph.text_frame
    tf.clear()

    a_ns = 'http://schemas.openxmlformats.org/drawingml/2006/main'

    for i, title in enumerate(unique_titles):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()

        p.text = title

        # Add auto-numbering via XML
        pPr = p._p.find(f'{{{a_ns}}}pPr')
        if pPr is None:
            pPr = p._p.makeelement(f'{{{a_ns}}}pPr', {})
            p._p.insert(0, pPr)

        # Remove any existing bullet settings
        for old in pPr.findall(f'{{{a_ns}}}buNone'):
            pPr.remove(old)
        for old in pPr.findall(f'{{{a_ns}}}buChar'):
            pPr.remove(old)
        for old in pPr.findall(f'{{{a_ns}}}buAutoNum'):
            pPr.remove(old)

        # Add auto-numbered bullet
        buAutoNum = pPr.makeelement(f'{{{a_ns}}}buAutoNum', {
            'type': 'arabicPeriod'  # "1. 2. 3. ..."
        })
        pPr.append(buAutoNum)

    return slide
```

**Critical**: The `buAutoNum` element must be a child of `a:pPr` (paragraph properties). The `type` attribute controls numbering style:
- `arabicPeriod` → 1. 2. 3.
- `arabicParenR` → 1) 2) 3)
- `alphaLcPeriod` → a. b. c.

### Step 8: Verify the Output

```python
def verify_output(path):
    """Quick programmatic verification of the processed PPTX."""
    prs = Presentation(path)
    a_ns = 'http://schemas.openxmlformats.org/drawingml/2006/main'

    for i, slide in enumerate(prs.slides, 1):
        if 2 <= i <= 6:  # Slides with dangling titles
            for shape in slide.shapes:
                if is_dangling_title(shape):
                    for para in shape.text_frame.paragraphs:
                        for run in para.runs:
                            assert run.font.name == 'Arial', f"Slide {i}: font name"
                            assert run.font.size == Pt(16), f"Slide {i}: font size"
                            assert run.font.bold == False, f"Slide {i}: bold"
                            assert str(run.font.color.rgb) == '989596', f"Slide {i}: color"
                    print(f"Slide {i}: OK — {shape.text_frame.text[:60]}")

    # Check last slide is Reference
    last = prs.slides[-1]
    assert last.shapes.title.text == "Reference"
    print(f"Reference slide: OK")
```

---

## 4. Common Pitfalls

### Pitfall 1: Font Typeface Not Set on All Three Sub-Elements

`python-pptx`'s `run.font.name = 'Arial'` only sets the `<a:latin>` typeface. Strict validators also check `<a:ea>` (East Asian) and `<a:cs>` (Complex Script). Always set all three via XML manipulation as shown in Step 4.

### Pitfall 2: Text Box Height Allows Multi-Line Rendering

If you only widen the box but leave the original `cy` (height), the validator may flag it as "not single line." Explicitly set `cy` to a single-line height value. Inspect the original XML to find the right value for your font size.

### Pitfall 3: Incorrect Namespace Usage in `findall`/`find`

When using `lxml` with `findall`, you must pass the namespace map as the second argument and use the prefix syntax:

```python
# WRONG — will silently return empty list
shape._element.findall('.//a:rPr')

# RIGHT
NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
shape._element.findall('.//a:rPr', NS)

# ALSO RIGHT — use Clark notation directly
shape._element.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}rPr')
```

### Pitfall 4: Not Clearing Default Content in New Slide Placeholders

When you add a slide from a layout, the body placeholder may contain default text ("Click to add text"). Always call `tf.clear()` before adding your content, then use `tf.paragraphs[0]` for the first item and `tf.add_paragraph()` for subsequent items.

### Pitfall 5: Duplicate Papers in Reference Slide

Always deduplicate paper titles before adding them to the Reference slide. Use an order-preserving dedup (seen-set pattern), not `set()` which loses order.

### Pitfall 6: Forgetting to Remove Existing Bullet Styles

When adding `buAutoNum`, first remove any existing `buNone`, `buChar`, or `buAutoNum` elements from the paragraph properties. Otherwise the XML may contain conflicting bullet definitions.

### Pitfall 7: Choosing the Wrong Slide Layout

Slide layouts vary by template. Always inspect `prs.slide_layouts` to find one with both a title placeholder and a body placeholder (idx=1). Layout index 1 is typically "Title + Content" but this is not guaranteed.

```python
for i, layout in enumerate(prs.slide_layouts):
    print(f"Layout {i}: {layout.name}")
    for ph in layout.placeholders:
        print(f"  Placeholder idx={ph.placeholder_format.idx}, type={ph.placeholder_format.type}")
```

### Pitfall 8: Horizontal Centering Math

To center a shape horizontally: `x = (slide_width - shape_width) / 2`. Using equal left/right margins is equivalent: `x = margin`, `width = slide_width - 2 * margin`.

---

## 5. Reference Implementation

This is a complete, self-contained script. Copy, adapt the input/output paths and the dangling-title detection heuristic, and run.

```python
#!/usr/bin/env python3
"""
PPTX Reference Formatting — Complete Reference Implementation

Detects dangling paper titles in slides, restyles them, repositions to bottom center,
and creates a Reference slide with deduplicated auto-numbered titles.

Usage:
    python3 process_pptx.py

Adjust INPUT_PATH, OUTPUT_PATH, and the is_dangling_title() heuristic for your task.
"""

import copy
from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# ── Configuration ──────────────────────────────────────────────────────────
INPUT_PATH  = '/root/Awesome-Agent-Papers.pptx'
OUTPUT_PATH = '/root/Awesome-Agent-Papers_processed.pptx'

FONT_NAME   = 'Arial'
FONT_SIZE   = 16          # points
FONT_COLOR  = '989596'    # hex without #
FONT_BOLD   = False

MARGIN_X       = 400000   # EMU — horizontal margin on each side
BOTTOM_MARGIN  = 300000   # EMU — gap from bottom of text box to bottom of slide
BOX_HEIGHT     = 369332   # EMU — single-line height for 16pt text

A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'
NS = {'a': A_NS, 'p': P_NS}

# ── Helpers ────────────────────────────────────────────────────────────────

def is_dangling_title(shape, slide_index):
    """
    Determine if a shape is a dangling paper title.

    Heuristic: it's a text box (no placeholder or non-title/body placeholder)
    with meaningful text, on a content slide (not the first/title slide).

    Adjust this function for your specific presentation structure.
    """
    if slide_index == 0:
        return False  # Skip the title slide

    if not shape.has_text_frame:
        return False

    text = shape.text_frame.text.strip()
    if not text or len(text) < 10:
        return False

    ph = shape.placeholder_format
    if ph is not None:
        # Skip title placeholders
        if ph.type is not None and ph.type in (
            1,   # TITLE (some enums)
            3,   # CENTER_TITLE
            4,   # SUBTITLE
            15,  # TITLE
        ):
            return False
        # Skip body placeholders
        if ph.idx == 0:
            return False  # Title placeholder by index
        if ph.idx == 1:
            return False  # Body placeholder

    # Additional heuristic: if the shape is near the bottom half and has
    # paper-title-like text, it's likely a dangling title.
    # For robustness, also check it's not an image or table.
    if shape.shape_type is not None:
        # shape_type 13 = FREEFORM, 1 = AUTO_SHAPE, etc.
        # We want text boxes: type 17 (TEXT_BOX) or shapes with text
        pass

    return True


def restyle_shape(shape):
    """Apply required font styling to all runs in the shape."""
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            # Basic font properties via python-pptx API
            run.font.name = FONT_NAME
            run.font.size = Pt(FONT_SIZE)
            run.font.bold = FONT_BOLD
            run.font.color.rgb = RGBColor.from_string(FONT_COLOR)

            # Set all three typeface elements via XML for strict validators
            rPr = run._r.find(f'{{{A_NS}}}rPr')
            if rPr is None:
                rPr = run._r.makeelement(f'{{{A_NS}}}rPr', {})
                run._r.insert(0, rPr)

            for tag in ['latin', 'ea', 'cs']:
                elem = rPr.find(f'{{{A_NS}}}{tag}')
                if elem is None:
                    elem = rPr.makeelement(f'{{{A_NS}}}{tag}', {})
                    rPr.append(elem)
                elem.set('typeface', FONT_NAME)


def reposition_bottom_center(shape, slide_width, slide_height):
    """Resize and reposition shape to bottom center of slide."""
    new_width = slide_width - 2 * MARGIN_X
    new_x = MARGIN_X
    new_y = slide_height - BOTTOM_MARGIN - BOX_HEIGHT

    shape.left   = new_x
    shape.top    = new_y
    shape.width  = new_width
    shape.height = BOX_HEIGHT


def center_align(shape):
    """Center-align all paragraphs in the shape."""
    for paragraph in shape.text_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.CENTER


def set_word_wrap_off(shape):
    """
    Disable word wrap on the text frame so text stays on one line.
    This is done via XML since python-pptx doesn't expose it directly.
    """
    txBody = shape._element.find(f'{{{A_NS}}}txBody')
    if txBody is None:
        return
    bodyPr = txBody.find(f'{{{A_NS}}}bodyPr')
    if bodyPr is None:
        return
    bodyPr.set('wrap', 'none')


def add_reference_slide(prs, paper_titles):
    """
    Append a Reference slide with deduplicated, auto-numbered paper titles.

    Returns the new slide object.
    """
    # Deduplicate preserving order
    seen = set()
    unique = []
    for t in paper_titles:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    # Find a layout with title + body
    # Try layout index 1 first (usually "Title + Content")
    layout = None
    for i, sl in enumerate(prs.slide_layouts):
        has_title = False
        has_body = False
        for ph in sl.placeholders:
            if ph.placeholder_format.idx == 0:
                has_title = True
            if ph.placeholder_format.idx == 1:
                has_body = True
        if has_title and has_body:
            layout = sl
            break

    if layout is None:
        # Fallback: use layout index 1
        layout = prs.slide_layouts[1]

    slide = prs.slides.add_slide(layout)

    # Set title
    slide.shapes.title.text = "Reference"

    # Find body placeholder
    body_ph = None
    for shape in slide.placeholders:
        if shape.placeholder_format.idx == 1:
            body_ph = shape
            break

    if body_ph is None:
        raise RuntimeError("Could not find body placeholder (idx=1) in chosen layout")

    # Clear default content
    tf = body_ph.text_frame
    tf.clear()

    # Add each unique title as an auto-numbered paragraph
    for i, title in enumerate(unique):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()

        p.text = title

        # Add auto-numbering via XML
        pPr = p._p.find(f'{{{A_NS}}}pPr')
        if pPr is None:
            pPr = p._p.makeelement(f'{{{A_NS}}}pPr', {})
            p._p.insert(0, pPr)

        # Remove conflicting bullet elements
        for tag in ['buNone', 'buChar', 'buAutoNum', 'buBlip', 'buFont']:
            for old in pPr.findall(f'{{{A_NS}}}{tag}'):
                pPr.remove(old)

        # Insert auto-number bullet
        buAutoNum = pPr.makeelement(f'{{{A_NS}}}buAutoNum', {
            'type': 'arabicPeriod'  # 1. 2. 3. ...
        })
        pPr.append(buAutoNum)

    return slide


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    prs = Presentation(INPUT_PATH)
    slide_width  = prs.slide_width
    slide_height = prs.slide_height

    print(f"Slide dimensions: {slide_width} x {slide_height} EMU")
    print(f"  = {slide_width / 914400:.2f}\" x {slide_height / 914400:.2f}\"")

    collected_titles = []

    for slide_idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if is_dangling_title(shape, slide_idx):
                title_text = shape.text_frame.text.strip()
                collected_titles.append(title_text)
                print(f"Slide {slide_idx + 1}: Found dangling title: \"{title_text[:70]}\"")

                # Step 1: Restyle font
                restyle_shape(shape)

                # Step 2: Center-align text
                center_align(shape)

                # Step 3: Resize and reposition to bottom center
                reposition_bottom_center(shape, slide_width, slide_height)

    print(f"\nCollected {len(collected_titles)} dangling titles")

    # Step 4: Add Reference slide
    if collected_titles:
        ref_slide = add_reference_slide(prs, collected_titles)
        seen = set()
        unique_count = 0
        for t in collected_titles:
            if t not in seen:
                seen.add(t)
                unique_count += 1
        print(f"Added Reference slide with {unique_count} unique papers")
    else:
        print("WARNING: No dangling titles found — no Reference slide added")

    # Step 5: Save
    prs.save(OUTPUT_PATH)
    print(f"\nSaved to {OUTPUT_PATH}")

    # Step 6: Quick verification
    verify(OUTPUT_PATH)


def verify(path):
    """Programmatic verification of the output."""
    prs = Presentation(path)
    print("\n── Verification ──")

    for slide_idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if is_dangling_title(shape, slide_idx):
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        assert run.font.name == FONT_NAME, \
                            f"Slide {slide_idx+1}: font name is {run.font.name}"
                        assert run.font.size == Pt(FONT_SIZE), \
                            f"Slide {slide_idx+1}: font size is {run.font.size}"
                        assert run.font.bold == FONT_BOLD, \
                            f"Slide {slide_idx+1}: bold is {run.font.bold}"
                        assert str(run.font.color.rgb) == FONT_COLOR, \
                            f"Slide {slide_idx+1}: color is {run.font.color.rgb}"

                # Check position
                expected_width = prs.slide_width - 2 * MARGIN_X
                expected_y = prs.slide_height - BOTTOM_MARGIN - BOX_HEIGHT
                assert shape.left == MARGIN_X, \
                    f"Slide {slide_idx+1}: left={shape.left}, expected {MARGIN_X}"
                assert shape.top == expected_y, \
                    f"Slide {slide_idx+1}: top={shape.top}, expected {expected_y}"
                assert shape.width == expected_width, \
                    f"Slide {slide_idx+1}: width={shape.width}, expected {expected_width}"
                assert shape.height == BOX_HEIGHT, \
                    f"Slide {slide_idx+1}: height={shape.height}, expected {BOX_HEIGHT}"

                print(f"  Slide {slide_idx+1}: ✓ {shape.text_frame.text[:60]}")

    # Check Reference slide
    last_slide = prs.slides[-1]
    assert last_slide.shapes.title.text == "Reference", \
        f"Last slide title: {last_slide.shapes.title.text}"

    # Check body has auto-numbered paragraphs
    for shape in last_slide.placeholders:
        if shape.placeholder_format.idx == 1:
            paras = [p for p in shape.text_frame.paragraphs
                     if p.text.strip()]
            print(f"  Reference slide: {len(paras)} papers")
            for p in paras:
                pPr = p._p.find(f'{{{A_NS}}}pPr')
                buAutoNum = pPr.find(f'{{{A_NS}}}buAutoNum') if pPr is not None else None
                assert buAutoNum is not None, f"Missing buAutoNum for: {p.text[:40]}"
            break

    print("  All checks passed ✓")


if __name__ == '__main__':
    main()
```

---

## 6. Adapting the Reference Implementation

When applying this to a new task:

1. **Adjust `is_dangling_title()`** — This is the most task-specific part. Inspect the PPTX XML first to understand which shapes are the "dangling" ones. Use the inspection code from Step 2 to enumerate all shapes and their placeholder types.

2. **Adjust font parameters** — Change `FONT_NAME`, `FONT_SIZE`, `FONT_COLOR`, `FONT_BOLD` as required.

3. **Adjust positioning constants** — `MARGIN_X`, `BOTTOM_MARGIN`, and `BOX_HEIGHT` control where the text box lands. Calibrate `BOX_HEIGHT` by inspecting the original XML: look at the `<a:ext cy="...">` value for a text box that already displays correctly as a single line.

4. **Adjust the Reference slide layout** — The script auto-detects a layout with title + body, but verify it picks the right one for your template.

5. **Adjust numbering style** — Change `arabicPeriod` to another `buAutoNum` type if needed (e.g., `arabicParenR`