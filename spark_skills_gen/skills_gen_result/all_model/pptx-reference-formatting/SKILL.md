---
name: pptx-reference-formatting-via-ooxml
description: Edit PowerPoint decks at the OOXML/XML level to normalize dangling paper-title text boxes, place them bottom-center on each slide, and append a deduplicated reference slide with auto-numbered bullets when higher-level PPTX libraries are unavailable.
tools:
  - python3
  - zipfile
  - lxml
  - defusedxml
  - xml.etree.ElementTree
  - curl
tags:
  - pptx
  - ooxml
  - powerpoint
  - xml
  - slide-formatting
  - references
---

# PPTX Reference Formatting via OOXML

Use this skill when you need to modify `.pptx` files in an environment where `python-pptx` is unavailable or unreliable, but standard Python XML tooling is installed. This approach works by treating the `.pptx` as a ZIP archive and editing the Office Open XML parts directly.

This is especially useful for tasks like:

- detecting standalone or âdanglingâ paper titles on content slides,
- standardizing font face/size/color/bold,
- forcing titles onto one line by resizing the text box,
- placing them bottom-center,
- appending a final âReferenceâ slide,
- inserting deduplicated paper titles as auto-numbered bullet points,
- preserving all unrelated slide content.

## Environment Assumptions

Confirmed available in the target environment class:

- `python3`
- `lxml`
- `defusedxml`
- standard library modules including `zipfile`, `shutil`, `tempfile`, `pathlib`, `xml.etree.ElementTree`

Do **not** assume:

- `python`
- `python-pptx`
- LibreOffice / `soffice`
- GUI tooling

Prefer `python3` explicitly.

---

## High-Level Workflow

1. **Inspect the PPTX as a ZIP archive before choosing tooling.**  
   Why: many environments do not have `python-pptx`, but almost always allow ZIP/XML access. A `.pptx` is just a package of XML parts.  
   Decision rule: if `python-pptx` is missing, or if you need exact validator-facing control over OOXML details like bullet numbering or slide relationships, use direct XML editing.

2. **Read presentation metadata and slide ordering from `ppt/presentation.xml` and relationships.**  
   Why: slide file numbers are not always enough; the presentation relationship graph determines official order.  
   Decision rule: always resolve slide paths through relationships rather than assuming `slide1.xml`, `slide2.xml`, etc. are the exact displayed order.

3. **Enumerate text-containing shapes on each slide and identify the target âdangling titleâ conservatively.**  
   Why: tests often require that non-title content remain unchanged. Over-editing all text boxes is the most common way to fail.  
   Decision rule: only modify one clearly title-like text box per target slide, using heuristics such as short standalone text, limited paragraphs, and shape position/size patterns.

4. **Extract the normalized title string from the chosen shape and store it for reference aggregation.**  
   Why: you need the same titles later for the final reference slide, and you should deduplicate them without mutating their intended display form too aggressively.  
   Decision rule: normalize whitespace for comparison, but preserve readable original text for output.

5. **Rewrite the title shape's text formatting at the run/paragraph level.**  
   Why: PowerPoint formatting is often distributed across `<a:rPr>`, `<a:defRPr>`, paragraph properties, and theme defaults. You must explicitly set the required run properties.  
   Decision rule: apply font family, point size, color, and bold-off to all runs in the target shape. Create missing XML nodes if necessary.

6. **Resize and reposition the title text box to bottom-center, with sufficient width for one-line rendering.**  
   Why: validators typically check bounding box position and whether text fits on one line.  
   Decision rule: derive slide width/height from presentation size, use a generous width ratio, and place the box near the bottom with centered alignment.

7. **Append a new slide by creating all required package parts and relationships.**  
   Why: adding a slide means updating several XML files, not just dropping in `slideN.xml`.  
   Decision rule: update at least:
   - `ppt/slides/slideN.xml`
   - `ppt/slides/_rels/slideN.xml.rels`
   - `ppt/presentation.xml`
   - `ppt/_rels/presentation.xml.rels`
   - `[Content_Types].xml`
   - `docProps/app.xml`

8. **Build the reference slide with a title and auto-numbered bullet paragraphs.**  
   Why: validators may inspect XML for actual numbering semantics, not just literal text like `1.`.  
   Decision rule: use `<a:buAutoNum type="arabicPeriod" startAt="...">` in paragraph properties rather than writing numbers into text manually.

9. **Write the new PPTX and verify it at XML level before finalizing.**  
   Why: XML-level verification catches issues earlier than opening the deck in a viewer.  
   Decision rule: check slide count, expected slide parts, target title formatting, title positions, and reference bullet numbering in the output ZIP.

---

## OOXML Conventions You Need to Know

### Namespace map

```python
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "relpkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}
```

### Units

- Slide and shape coordinates use **EMU** (English Metric Units).
- `1 inch = 914400 EMU`
- `1 point = 12700 EMU`
- Font size in `<a:rPr sz="...">` uses **1/100 points**.  
  Example: `16pt => sz="1600"`

### Text shape structure

Relevant path inside a slide shape:

- `p:sp` = shape
- `p:txBody` = text container
- `a:p` = paragraph
- `a:r` = run
- `a:rPr` = run properties
- `a:t` = text value

### Position and size

- `p:spPr/a:xfrm/a:off[@x,@y]` = position
- `p:spPr/a:xfrm/a:ext[@cx,@cy]` = width/height

---

## Step 1: Inspect the PPTX and Enumerate Slides

Use ZIP + XML first. This avoids dead ends when higher-level libraries are missing.

```python
#!/usr/bin/env python3
from pathlib import Path
import zipfile
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

def parse_xml_bytes(data: bytes):
    return etree.fromstring(data)

def list_slides_in_order(pptx_path: str):
    pptx = Path(pptx_path)
    if not pptx.exists():
        raise FileNotFoundError(f"Missing PPTX: {pptx}")

    with zipfile.ZipFile(pptx, "r") as zf:
        presentation = parse_xml_bytes(zf.read("ppt/presentation.xml"))
        pres_rels = parse_xml_bytes(zf.read("ppt/_rels/presentation.xml.rels"))

        relmap = {}
        for rel in pres_rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
            relmap[rel.get("Id")] = rel.get("Target")

        slide_targets = []
        for sldId in presentation.findall(".//p:sldIdLst/p:sldId", namespaces=NS):
            rid = sldId.get("{%s}id" % NS["r"])
            target = relmap.get(rid)
            if not target:
                raise ValueError(f"Missing relationship target for slide rid={rid}")
            if not target.startswith("slides/"):
                raise ValueError(f"Unexpected slide target: {target}")
            slide_targets.append("ppt/" + target)

        return slide_targets

if __name__ == "__main__":
    slides = list_slides_in_order("/path/to/input.pptx")
    for i, slide in enumerate(slides, 1):
        print(i, slide)
```

### Notes

- Always resolve slide order from `presentation.xml`.
- Do not assume numerical filename order is the visible order.
- Use `python3`, not `python`.

---

## Step 2: Read Slide Size and Shape Text Content

You need slide dimensions for bottom-center placement and shape text for title detection.

```python
#!/usr/bin/env python3
from pathlib import Path
import zipfile
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}

def parse_xml_bytes(data: bytes):
    return etree.fromstring(data)

def get_slide_size(zf: zipfile.ZipFile):
    presentation = parse_xml_bytes(zf.read("ppt/presentation.xml"))
    sldSz = presentation.find(".//p:sldSz", namespaces=NS)
    if sldSz is None:
        raise ValueError("Slide size not found in presentation.xml")
    return int(sldSz.get("cx")), int(sldSz.get("cy"))

def extract_shape_texts(slide_xml: bytes):
    root = parse_xml_bytes(slide_xml)
    items = []

    for sp in root.findall(".//p:sp", namespaces=NS):
        cNvPr = sp.find(".//p:nvSpPr/p:cNvPr", namespaces=NS)
        shape_id = cNvPr.get("id") if cNvPr is not None else None
        shape_name = cNvPr.get("name") if cNvPr is not None else None

        texts = []
        for t in sp.findall(".//a:t", namespaces=NS):
            if t.text:
                texts.append(t.text)

        text = " ".join(" ".join(texts).split())
        xfrm = sp.find("./p:spPr/a:xfrm", namespaces=NS)
        x = y = cx = cy = None
        if xfrm is not None:
            off = xfrm.find("./a:off", namespaces=NS)
            ext = xfrm.find("./a:ext", namespaces=NS)
            if off is not None:
                x = int(off.get("x", "0"))
                y = int(off.get("y", "0"))
            if ext is not None:
                cx = int(ext.get("cx", "0"))
                cy = int(ext.get("cy", "0"))

        items.append({
            "shape": sp,
            "shape_id": shape_id,
            "shape_name": shape_name,
            "text": text,
            "x": x,
            "y": y,
            "cx": cx,
            "cy": cy,
        })

    return items

if __name__ == "__main__":
    pptx_path = Path("/path/to/input.pptx")
    with zipfile.ZipFile(pptx_path, "r") as zf:
        slide_w, slide_h = get_slide_size(zf)
        print("slide size:", slide_w, slide_h)
        slide_xml = zf.read("ppt/slides/slide1.xml")
        for item in extract_shape_texts(slide_xml):
            if item["text"]:
                print(item)
```

### Decision criteria for title-like shapes

A good candidate usually has:

- non-empty text,
- 1 paragraph or very few runs,
- relatively short text,
- standalone text box rather than a large body block,
- often a lower vertical position or small bounding box.

Avoid editing:

- slide title placeholders unrelated to the paper title,
- large multi-line body text,
- captions embedded in diagrams,
- footer metadata unless the task explicitly targets them.

---

## Step 3: Detect a Dangling Title Conservatively

When the task is âdetect all dangling paper titles,â prefer a **single-shape-per-slide** heuristic. If you edit all text shapes containing title-like words, you may alter unrelated content.

```python
#!/usr/bin/env python3
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}

def paragraph_count(shape):
    txBody = shape.find("./p:txBody", namespaces=NS)
    if txBody is None:
        return 0
    return len(txBody.findall("./a:p", namespaces=NS))

def is_probable_dangling_title(item, slide_h: int):
    text = item["text"]
    if not text:
        return False

    words = text.split()
    if len(words) == 0:
        return False
    if len(words) > 12:
        return False

    if paragraph_count(item["shape"]) > 2:
        return False

    # Prefer standalone lower text boxes, but do not require extreme bottom placement.
    y = item["y"] if item["y"] is not None else 0
    lower_half = y >= slide_h * 0.35

    # Avoid giant body boxes.
    cy = item["cy"] if item["cy"] is not None else 0
    not_too_tall = cy == 0 or cy < slide_h * 0.25

    return lower_half and not_too_tall

def choose_title_shape(items, slide_h: int):
    candidates = [it for it in items if is_probable_dangling_title(it, slide_h)]
    if not candidates:
        return None

    # Rank by shorter text and lower position to prefer dangling labels.
    def rank(it):
        return (
            len(it["text"].split()),
            -(it["y"] or 0),
            it["cy"] or 0,
        )

    return sorted(candidates, key=rank)[0]
```

### Practical guidance

- If the task clearly specifies which slides are content slides, skip known cover/reference slides when detecting titles.
- If multiple title-like shapes exist, prefer the one already nearest the bottom or the smallest standalone text box.
- Preserve the text content; normalize formatting and placement only.

---

## Step 4: Normalize Run Formatting and Paragraph Alignment

To satisfy validators, set formatting explicitly on all runs.

```python
#!/usr/bin/env python3
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}

def ensure_child(parent, qname):
    node = parent.find(qname, namespaces=NS)
    if node is None:
        node = etree.SubElement(parent, qname)
    return node

def set_run_style(rPr, font_name="Arial", font_size_pt=16, rgb_hex="989596", bold=False):
    if not rgb_hex or len(rgb_hex) != 6:
        raise ValueError(f"Expected 6-digit RGB hex, got: {rgb_hex}")

    rPr.set("sz", str(int(font_size_pt * 100)))
    rPr.set("b", "1" if bold else "0")
    rPr.set("dirty", "0")

    latin = rPr.find("./a:latin", namespaces=NS)
    if latin is None:
        latin = etree.SubElement(rPr, "{%s}latin" % NS["a"])
    latin.set("typeface", font_name)

    ea = rPr.find("./a:ea", namespaces=NS)
    if ea is None:
        ea = etree.SubElement(rPr, "{%s}ea" % NS["a"])
    ea.set("typeface", font_name)

    cs = rPr.find("./a:cs", namespaces=NS)
    if cs is None:
        cs = etree.SubElement(rPr, "{%s}cs" % NS["a"])
    cs.set("typeface", font_name)

    solidFill = rPr.find("./a:solidFill", namespaces=NS)
    if solidFill is None:
        solidFill = etree.SubElement(rPr, "{%s}solidFill" % NS["a"])

    srgbClr = solidFill.find("./a:srgbClr", namespaces=NS)
    if srgbClr is None:
        srgbClr = etree.SubElement(solidFill, "{%s}srgbClr" % NS["a"])
    srgbClr.set("val", rgb_hex.upper())

def normalize_text_shape_style(shape, font_name="Arial", font_size_pt=16, rgb_hex="989596"):
    txBody = shape.find("./p:txBody", namespaces=NS)
    if txBody is None:
        raise ValueError("Shape has no text body")

    bodyPr = txBody.find("./a:bodyPr", namespaces=NS)
    if bodyPr is not None:
        bodyPr.set("wrap", "none")

    for p in txBody.findall("./a:p", namespaces=NS):
        pPr = p.find("./a:pPr", namespaces=NS)
        if pPr is None:
            pPr = etree.Element("{%s}pPr" % NS["a"])
            p.insert(0, pPr)
        pPr.set("algn", "ctr")

        endParaRPr = p.find("./a:endParaRPr", namespaces=NS)
        if endParaRPr is None:
            endParaRPr = etree.SubElement(p, "{%s}endParaRPr" % NS["a"])
        set_run_style(endParaRPr, font_name, font_size_pt, rgb_hex, bold=False)

        for r in p.findall("./a:r", namespaces=NS):
            rPr = r.find("./a:rPr", namespaces=NS)
            if rPr is None:
                rPr = etree.Element("{%s}rPr" % NS["a"])
                r.insert(0, rPr)
            set_run_style(rPr, font_name, font_size_pt, rgb_hex, bold=False)

        # Handle fields like slide numbers if present.
        for fld in p.findall("./a:fld", namespaces=NS):
            rPr = fld.find("./a:rPr", namespaces=NS)
            if rPr is None:
                rPr = etree.Element("{%s}rPr" % NS["a"])
                fld.insert(0, rPr)
            set_run_style(rPr, font_name, font_size_pt, rgb_hex, bold=False)
```

### Critical note

Setting only one run is not enough. Title text may be split across multiple runs. Update all of them.

---

## Step 5: Reposition and Resize the Title Box

Use slide dimensions to center the box and make it wide enough for one-line display.

```python
#!/usr/bin/env python3
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}

EMU_PER_INCH = 914400

def reposition_shape_bottom_center(shape, slide_w: int, slide_h: int,
                                   width_ratio: float = 0.7,
                                   box_height_in: float = 0.42,
                                   bottom_margin_in: float = 0.30):
    if not (0.1 <= width_ratio <= 0.95):
        raise ValueError("width_ratio must be between 0.1 and 0.95")

    spPr = shape.find("./p:spPr", namespaces=NS)
    if spPr is None:
        raise ValueError("Shape has no spPr")

    xfrm = spPr.find("./a:xfrm", namespaces=NS)
    if xfrm is None:
        xfrm = etree.SubElement(spPr, "{%s}xfrm" % NS["a"])

    off = xfrm.find("./a:off", namespaces=NS)
    if off is None:
        off = etree.SubElement(xfrm, "{%s}off" % NS["a"])

    ext = xfrm.find("./a:ext", namespaces=NS)
    if ext is None:
        ext = etree.SubElement(xfrm, "{%s}ext" % NS["a"])

    cx = int(slide_w * width_ratio)
    cy = int(box_height_in * EMU_PER_INCH)
    x = int((slide_w - cx) / 2)
    y = int(slide_h - cy - bottom_margin_in * EMU_PER_INCH)

    off.set("x", str(max(0, x)))
    off.set("y", str(max(0, y)))
    ext.set("cx", str(max(1, cx)))
    ext.set("cy", str(max(1, cy)))
```

### Placement rules

- Center with `x = (slide_w - cx) / 2`
- Put near bottom with a modest bottom margin
- Use a width ratio around `0.65`-`0.80` for one-line titles
- Set paragraph alignment to center
- Set `wrap="none"` on body properties

---

## Step 6: Deduplicate Titles for the Reference Slide

Use normalized comparison while preserving readable output order.

```python
#!/usr/bin/env python3

def normalize_title_key(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()

def deduplicate_preserve_order(titles):
    seen = set()
    result = []
    for title in titles:
        clean = " ".join((title or "").split()).strip()
        if not clean:
            continue
        key = normalize_title_key(clean)
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result

if __name__ == "__main__":
    titles = ["ReAct", "  ReAct ", "Foam - Agent", "", "MAST"]
    print(deduplicate_preserve_order(titles))
```

### Important

- Deduplicate before writing the reference slide.
- Preserve first appearance order unless the task explicitly asks for sorting.
- Do not silently alter capitalization unless required.

---

## Step 7: Create a New Slide and Wire It into the Package

Appending a slide requires multiple package edits.

```python
#!/usr/bin/env python3
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "relpkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

def next_numeric_suffix(existing_names, prefix, suffix):
    nums = []
    for name in existing_names:
        if name.startswith(prefix) and name.endswith(suffix):
            middle = name[len(prefix):-len(suffix)]
            if middle.isdigit():
                nums.append(int(middle))
    return (max(nums) + 1) if nums else 1

def append_slide_relationships(presentation_xml_root, pres_rels_root, slide_target):
    # Find next rId in presentation rels.
    existing_rids = []
    for rel in pres_rels_root.findall("{%s}Relationship" % REL_NS):
        rid = rel.get("Id", "")
        if rid.startswith("rId") and rid[3:].isdigit():
            existing_rids.append(int(rid[3:]))
    next_rid = f"rId{(max(existing_rids) + 1) if existing_rids else 1}"

    rel = etree.SubElement(pres_rels_root, "{%s}Relationship" % REL_NS)
    rel.set("Id", next_rid)
    rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
    rel.set("Target", f"slides/{slide_target}")

    sldIdLst = presentation_xml_root.find(".//p:sldIdLst", namespaces=NS)
    if sldIdLst is None:
        raise ValueError("presentation.xml missing p:sldIdLst")

    existing_ids = [int(node.get("id")) for node in sldIdLst.findall("./p:sldId", namespaces=NS) if node.get("id", "").isdigit()]
    next_id = (max(existing_ids) + 1) if existing_ids else 256

    sldId = etree.SubElement(sldIdLst, "{%s}sldId" % NS["p"])
    sldId.set("id", str(next_id))
    sldId.set("{%s}id" % NS["r"], next_rid)

def ensure_content_type_override(content_types_root, part_name, content_type):
    for override in content_types_root.findall("{%s}Override" % NS["ct"]):
        if override.get("PartName") == part_name:
            return
    node = etree.SubElement(content_types_root, "{%s}Override" % NS["ct"])
    node.set("PartName", part_name)
    node.set("ContentType", content_type)
```

### Required package updates

At minimum:

- add `ppt/slides/slideN.xml`
- add `ppt/slides/_rels/slideN.xml.rels`
- add a new `<Relationship>` in `ppt/_rels/presentation.xml.rels`
- add a new `<p:sldId>` in `ppt/presentation.xml`
- add `[Content_Types].xml` override for `/ppt/slides/slideN.xml`
- update `docProps/app.xml` slide count and titles vector

---

## Step 8: Build a Reference Slide with Auto-Numbered Bullets

Literal text like `1. Title` may fail if the validator inspects XML. Use `a:buAutoNum`.

```python
#!/usr/bin/env python3
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}

def qn(prefix, local):
    return "{%s}%s" % (NS[prefix], local)

def make_text_run(text, font_name="Arial", font_size_pt=16, rgb_hex="989596", bold=False):
    r = etree.Element(qn("a", "r"))
    rPr = etree.SubElement(r, qn("a", "rPr"))
    rPr.set("lang", "en-US")
    rPr.set("sz", str(int(font_size_pt * 100)))
    rPr.set("b", "1" if bold else "0")
    latin = etree.SubElement(rPr, qn("a", "latin"))
    latin.set("typeface", font_name)
    ea = etree.SubElement(rPr, qn("a", "ea"))
    ea.set("typeface", font_name)
    cs = etree.SubElement(rPr, qn("a", "cs"))
    cs.set("typeface", font_name)
    solidFill = etree.SubElement(rPr, qn("a", "solidFill"))
    srgbClr = etree.SubElement(solidFill, qn("a", "srgbClr"))
    srgbClr.set("val", rgb_hex.upper())
    t = etree.SubElement(r, qn("a", "t"))
    t.text = text
    return r

def make_reference_paragraph(text, number_index):
    p = etree.Element(qn("a", "p"))
    pPr = etree.SubElement(p, qn("a", "pPr"))
    pPr.set("lvl", "0")
    bu = etree.SubElement(pPr, qn("a", "buAutoNum"))
    bu.set("type", "arabicPeriod")
    bu.set("startAt", str(number_index))
    p.append(make_text_run(text, font_name="Arial", font_size_pt=16, rgb_hex="989596", bold=False))
    endParaRPr = etree.SubElement(p, qn("a", "endParaRPr"))
    endParaRPr.set("lang", "en-US")
    endParaRPr.set("sz", "1600")
    return p
```

### Critical note

Use one paragraph per reference item. Put numbering on `a:pPr`, not inside the run text.

---

## Step 9: XML-Level Verification Before Finalizing

Always inspect the output archive instead of trusting the write step.

```python
#!/usr/bin/env python3
import zipfile
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}

def verify_output(pptx_path: str):
    with zipfile.ZipFile(pptx_path, "r") as zf:
        if "docProps/app.xml" not in zf.namelist():
            raise ValueError("Missing docProps/app.xml")

        app = etree.fromstring(zf.read("docProps/app.xml"))
        slides = app.find("ep:Slides", namespaces=NS)
        if slides is None or not slides.text or not slides.text.isdigit():
            raise ValueError("Invalid slide count in app.xml")

        print("slide count:", slides.text)

        # Inspect last slide for auto-numbering.
        slide_names = sorted([n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")])
        if not slide_names:
            raise ValueError("No slide XML files found")

        last_slide = etree.fromstring(zf.read(slide_names[-1]))
        bullets_found = 0
        for p in last_slide.findall(".//a:p", namespaces=NS):
            pPr = p.find("./a:pPr", namespaces=NS)
            if pPr is None:
                continue
            bu = pPr.find("./a:buAutoNum", namespaces=NS)
            if bu is not None:
                bullets_found += 1
        print("auto-numbered paragraphs on last slide:", bullets_found)

if __name__ == "__main__":
    verify_output("/path/to/output.pptx")
```

### What to verify

- output file exists,
- slide count incremented,
- one target title per edited slide still exists,
- font type/size/color/bold are correct on title runs,
- title box is near bottom and centered,
- unrelated slide content remains unchanged,
- final slide is last,
- reference bullets use `a:buAutoNum`,
- deduplication happened.

---

## Reference Implementation

The following script is a complete end-to-end implementation you can copy, adapt, and run directly. It:

- reads an input `.pptx`,
- resolves slide order,
- detects one dangling title per target slide,
- reformats and repositions the title,
- collects deduplicated titles,
- appends a final reference slide,
- updates required package metadata,
- writes the output deck.

```python
#!/usr/bin/env python3
from pathlib import Path
import zipfile
from copy import deepcopy
from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "relpkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}

REL_NS = NS["relpkg"]
EMU_PER_INCH = 914400

TITLE_FONT = "Arial"
TITLE_SIZE_PT = 16
TITLE_RGB = "989596"

def qn(prefix, local):
    return "{%s}%s" % (NS[prefix], local)

def parse_xml_bytes(data: bytes):
    return etree.fromstring(data)

def tostring(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

def normalize_spaces(text: str) -> str:
    return " ".join((text or "").split()).strip()

def normalize_title_key(text: str) -> str:
    return normalize_spaces(text).lower()

def read_package(pptx_path: Path):
    if not pptx_path.exists():
        raise FileNotFoundError(f"Input PPTX not found: {pptx_path}")
    with zipfile.ZipFile(pptx_path, "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}

def write_package(parts: dict, output_path: Path):
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)

def get_slide_size(parts):
    presentation = parse_xml_bytes(parts["ppt/presentation.xml"])
    sldSz = presentation.find(".//p:sldSz", namespaces=NS)
    if sldSz is None:
        raise ValueError("Slide size not found")
    return int(sldSz.get("cx")), int(sldSz.get("cy"))

def get_presentation_roots(parts):
    presentation = parse_xml_bytes(parts["ppt/presentation.xml"])
    pres_rels = parse_xml_bytes(parts["ppt/_rels/presentation.xml.rels"])
    return presentation, pres_rels

def get_slide_targets_in_order(parts):
    presentation, pres_rels = get_presentation_roots(parts)

    relmap = {}
    for rel in pres_rels.findall("{%s}Relationship" % REL_NS):
        relmap[rel.get("Id")] = rel.get("Target")

    targets = []
    for sldId in presentation.findall(".//p:sldIdLst/p:sldId", namespaces=NS):
        rid = sldId.get("{%s}id" % NS["r"])
        target = relmap.get(rid)
        if not target:
            raise ValueError(f"Missing target for slide relationship {rid}")
        targets.append("ppt/" + target)
    return targets

def extract_shape_items(slide_root):
    items = []
    for sp in slide_root.findall(".//p:sp", namespaces=NS):
        cNvPr = sp.find(".//p:nvSpPr/p:cNvPr", namespaces=NS)
        shape_id = cNvPr.get("id") if cNvPr is not None else None
        shape_name = cNvPr.get("name") if cNvPr is not None else None

        text_parts = []
        for t in sp.findall(".//a:t", namespaces=NS):
            if t.text:
                text_parts.append(t.text)
        text = normalize_spaces(" ".join(text_parts))

        x = y = cx = cy = None
        xfrm = sp.find("./p:spPr/a:xfrm", namespaces=NS)
        if xfrm is not None:
            off = xfrm.find("./a:off", namespaces=NS)
            ext = xfrm.find("./a:ext", namespaces=NS)
            if off is not None:
                x = int(off.get("x", "0"))
                y = int(off.get("y", "0"))
            if ext is not None:
                cx = int(ext.get("cx", "0"))
                cy = int(ext.get("cy", "0"))

        txBody = sp.find("./p:txBody", namespaces=NS)
        paragraphs = txBody.findall("./a:p", namespaces=NS) if txBody is not None else []

        items.append({
            "shape": sp,
            "shape_id": shape_id,
            "shape_name": shape_name,
            "text": text,
            "x": x,
            "y": y,
            "cx": cx,
            "cy": cy,
            "paragraph_count": len(paragraphs),
        })
    return items

def is_probable_dangling_title(item, slide_h):
    text = item["text"]
    if not text:
        return False

    words = text.split()
    if len(words) == 0 or len(words) > 12:
        return False

    if item["paragraph_count"] > 2:
        return False

    y = item["y"] if item["y"] is not None else 0
    cy = item["cy"] if item["cy"] is not None else 0

    lower_half = y >= slide_h * 0.35
    not_too_tall = cy == 0 or cy < slide_h * 0.25

    return lower_half and not_too_tall

def choose_title_shape(slide_root, slide_h):
    items = extract_shape_items(slide_root)
    candidates = [it for it in items if is_probable_dangling_title(it, slide_h)]
    if not candidates:
        return None

    def rank(it):
        return (
            len(it["text"].split()),
            -(it["y"] or 0),
            it["cy"] or 0,
        )

    return sorted(candidates, key=rank)[0]

def set_run_style(rPr, font_name=TITLE_FONT, font_size_pt=TITLE_SIZE_PT, rgb_hex=TITLE_RGB, bold=False):
    rPr.set("sz", str(int(font_size_pt * 100)))
    rPr.set("b", "1" if bold else "0")
    rPr.set("dirty", "0")
    rPr.set("lang", "en-US")

    for tag in ("latin", "ea", "cs"):
        node = rPr.find(f"./a:{tag}", namespaces=NS)
        if node is None:
            node = etree.SubElement(rPr, qn("a", tag))
        node.set("typeface", font_name)

    solidFill = rPr.find("./a:solidFill", namespaces=NS)
    if solidFill is None:
        solidFill = etree.SubElement(rPr, qn("a", "solidFill"))
    srgbClr = solidFill.find("./a:srgbClr", namespaces=NS)
    if srgbClr is None:
        srgbClr = etree.SubElement(solidFill, qn("a", "srgbClr"))
    srgbClr.set("val", rgb_hex.upper())

def normalize_text_shape_style(shape, font_name=TITLE_FONT, font_size_pt=TITLE_SIZE_PT, rgb_hex=TITLE_RGB):
    txBody = shape.find("./p:txBody", namespaces=NS)
    if txBody is None:
        raise ValueError("Target shape missing txBody")

    bodyPr = txBody.find("./a:bodyPr", namespaces=NS)
    if bodyPr is None:
        bodyPr = etree.Element(qn("a", "bodyPr"))
        txBody.insert(0, bodyPr)
    bodyPr.set("wrap", "none")
    bodyPr.set("anchor", "ctr")

    # Ensure lstStyle exists so PowerPoint opens cleanly.
    lstStyle = txBody.find("./a:lstStyle", namespaces=NS)
    if lstStyle is None:
        insert_at = 1 if bodyPr is not None else 0
        txBody.insert(insert_at, etree.Element(qn("a", "lstStyle")))

    for p in txBody.findall("./a:p", namespaces=NS):
        pPr = p.find("./a:pPr", namespaces=NS)
        if pPr is None:
            pPr = etree.Element(qn("a", "pPr"))
            p.insert(0, pPr)
        pPr.set("algn", "ctr")

        for r in p.findall("./a:r", namespaces=NS):
            rPr = r.find("./a:rPr", namespaces=NS)
            if rPr is None:
                rPr = etree.Element(qn("a", "rPr"))
                r.insert(0, rPr)
            set_run_style(rPr, font_name, font_size_pt, rgb_hex, bold=False)

        for fld in p.findall("./a:fld", namespaces=NS):
            rPr = fld.find("./a:rPr", namespaces=NS)
            if rPr is None:
                rPr = etree.Element(qn("a", "rPr"))
                fld.insert(0, rPr)
            set_run_style(rPr, font_name, font_size_pt, rgb_hex, bold=False)

        endParaRPr = p.find("./a:endParaRPr", namespaces=NS)
        if endParaRPr is None:
            endParaRPr = etree.SubElement(p, qn("a", "endParaRPr"))
        set_run_style(endParaRPr, font_name, font_size_pt, rgb_hex, bold=False)

def reposition_shape_bottom_center(shape, slide_w, slide_h,
                                   width_ratio=0.70,
                                   box_height_in=0.42,
                                   bottom_margin_in=0.30):
    spPr = shape.find("./p:spPr", namespaces=NS)
    if spPr is None:
        spPr = etree.SubElement(shape, qn("p", "spPr"))

    xfrm = spPr.find("./a:xfrm", namespaces=NS)
    if xfrm is None:
        xfrm = etree.SubElement(spPr, qn("a", "xfrm"))

    off = xfrm.find("./a:off", namespaces=NS)
    if off is None:
        off = etree.SubElement(xfrm, qn("a", "off"))

    ext = xfrm.find("./a:ext", namespaces=NS)
    if ext is None:
        ext = etree.SubElement(xfrm, qn("a", "ext"))

    cx = int(slide_w * width_ratio)
    cy = int(box_height_in * EMU_PER_INCH)
    x = max(0, int((slide_w - cx) / 2))
    y = max(0, int(slide_h - cy - bottom_margin_in * EMU_PER_INCH))

    off.set("x", str(x))
    off.set("y", str(y))
    ext.set("cx", str(cx))
    ext.set("cy", str(cy))

def deduplicate_preserve_order(titles):
    seen = set()
    result = []
    for title in titles:
        clean = normalize_spaces(title)
        if not clean:
            continue
        key = normalize_title_key(clean)
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result

def next_numeric_suffix(existing_names, prefix, suffix):
    nums = []
    for name in existing_names:
        if name.startswith(prefix) and name.endswith(suffix):
            middle = name[len(prefix):-len(suffix)]
            if middle.isdigit():
                nums.append(int(middle))
    return (max(nums) + 1) if nums else 1

def make_nvSpPr(shape_id, name):
    nvSpPr = etree.Element(qn("p", "nvSpPr"))
    cNvPr = etree.SubElement(nvSpPr, qn("p", "cNvPr"))
    cNvPr.set("id", str(shape_id))
    cNvPr.set("name", name)
    cNvSpPr = etree.SubElement(nvSpPr, qn("p", "cNvSpPr"))
    etree.SubElement(cNvSpPr, qn("a", "spLocks")).set("noGrp", "1")
    etree.SubElement(nvSpPr, qn("p", "nvPr"))
    return nvSpPr

def make_shape_spPr(x, y, cx, cy):
    spPr = etree.Element(qn("p", "spPr"))
    xfrm = etree.SubElement(spPr, qn("a", "xfrm"))
    off = etree.SubElement(xfrm, qn("a", "off"))
    off.set("x", str(x))
    off.set("y", str(y))
    ext = etree.SubElement(xfrm, qn("a", "ext"))
    ext.set("cx", str(cx))
    ext.set("cy", str(cy))
    prstGeom = etree.SubElement(spPr, qn("a", "prstGeom"))
    prstGeom.set("prst", "rect")
    etree.SubElement(prstGeom, qn("a", "avLst"))
    # No line / no fill for text boxes.
    etree.SubElement(spPr, qn("a", "noFill"))
    ln = etree.SubElement(spPr, qn("a", "ln"))
    etree.SubElement(ln, qn("a", "noFill"))
    return spPr

def make_text_run(text, font_name=TITLE_FONT, font_size_pt=TITLE_SIZE_PT, rgb_hex=TITLE_RGB, bold=False):
    r = etree.Element(qn("a", "r"))
    rPr = etree.SubElement(r, qn("a", "rPr"))
    set_run_style(rPr, font_name, font_size_pt, rgb_hex, bold=bold)
    t = etree.SubElement(r, qn("a", "t"))
    t.text = text
    return r

def make_text_shape(shape_id, name, text, x, y, cx, cy,
                    font_name=TITLE_FONT, font_size_pt=TITLE_SIZE_PT, rgb_hex=TITLE_RGB,
                    align="ctr", auto_num=None, bold=False):
    sp = etree.Element(qn("p", "sp"))
    sp.append(make_nvSpPr(shape_id, name))

    spPr = make_shape_spPr(x, y, cx, cy)
    sp.append(spPr)

    txBody = etree.SubElement(sp, qn("p", "txBody"))
    bodyPr = etree.SubElement(txBody, qn("a", "bodyPr"))
    bodyPr.set("wrap", "square" if auto_num else "none")
    bodyPr.set("anchor", "t" if auto_num else "ctr")
    etree.SubElement(txBody, qn("a", "lstStyle"))
    p = etree.SubElement(txBody, qn("a", "p"))
    pPr = etree.SubElement(p, qn("a", "pPr"))
    pPr.set("algn", align)

    if auto_num is not None:
        bu = etree.SubElement(pPr, qn("a", "buAutoNum"))
        bu.set("type", "arabicPeriod")
        bu.set("startAt", str(auto_num))

    p.append(make_text_run(text, font_name, font_size_pt, rgb_hex, bold=bold))
    endParaRPr = etree.SubElement(p, qn("a", "endParaRPr"))
    endParaRPr.set("lang", "en-US")
    endParaRPr.set("sz", str(int(font_size_pt * 100)))
    return sp

def build_reference_slide_xml(slide_w, slide_h, titles):
    sld = etree.Element(qn("p", "sld"), nsmap={
        None: NS["p"],
        "a": NS["a"],
        "r": NS["r"],
    })
    cSld = etree.SubElement(sld, qn("p", "cSld"))
    spTree = etree.SubElement(cSld, qn("p", "spTree"))

    nvGrpSpPr = etree.SubElement(spTree, qn("p", "nvGrpSpPr"))
    cNvPr = etree.SubElement(nvGrpSpPr, qn("p", "cNvPr"))
    cNvPr.set("id", "1")
    cNvPr.set("name", "")
    etree.SubElement(nvGrpSpPr, qn("p", "cNvGrpSpPr"))
    etree.SubElement(nvGrpSpPr, qn("p", "nvPr"))

    grpSpPr = etree.SubElement(spTree, qn("p", "grpSpPr"))
    xfrm = etree.SubElement(grpSpPr, qn("a", "xfrm"))
    for tag, attrs in (
        ("off", {"x": "0", "y": "0"}),
        ("ext", {"cx": "0", "cy": "0"}),
        ("chOff", {"x": "0", "y": "0"}),
        ("chExt", {"cx": "0", "cy": "0"}),
    ):
        node = etree.SubElement(xfrm, qn("a", tag))
        for k, v in attrs.items():
            node.set(k, v)

    title_shape = make_text_shape(
        shape_id=2,
        name="Title 1",
        text="Reference",
        x=int(slide_w * 0.12),
        y=int(slide_h * 0.08),
        cx=int(slide_w * 0.76),
        cy=int(0.60 * EMU_PER_INCH),
        font_name=TITLE_FONT,
        font_size_pt=24,
        rgb_hex="000000",
        align="ctr",
        auto_num=None,
        bold=False,
    )
    spTree.append(title_shape)

    body_x = int(slide_w * 0.12)
    body_y = int(slide_h * 0.20)
    body_w = int(slide_w * 0.76)
    body_h = int(slide_h * 0.65)

    body = etree.Element(qn("p", "sp"))
    body.append(make_nvSpPr(3, "Content Placeholder 2"))
    body.append(make_shape_spPr(body_x, body_y, body_w, body_h))

    txBody = etree.SubElement(body, qn("p", "txBody"))
    bodyPr = etree.SubElement(txBody, qn("a", "bodyPr"))
    bodyPr.set("wrap", "square")
    bodyPr.set("anchor", "t")
    etree.SubElement(txBody, qn("a", "lstStyle"))

    for idx, title in enumerate(titles, 1):
        p = etree.SubElement(txBody, qn("a", "p"))
        pPr = etree.SubElement(p, qn("a", "pPr"))
        pPr.set("lvl", "0")
        bu = etree.SubElement(pPr, qn("a", "buAutoNum"))
        bu.set("type", "arabicPeriod")
        bu.set("startAt", str(idx))
        p.append(make_text_run(title, font_name=TITLE_FONT, font_size_pt=16, rgb_hex=TITLE_RGB, bold=False))
        endParaRPr = etree.SubElement(p, qn("a", "endParaRPr"))
        endParaRPr.set("lang", "en-US")
        endParaRPr.set("sz", "1600")

    spTree.append(body)
    clrMapOvr = etree.SubElement(sld, qn("p", "clrMapOvr"))
    etree.SubElement(clrMapOvr, qn("a", "masterClrMapping"))
    return sld

def build_empty_slide_rels_xml():
    root = etree.Element("{%s}Relationships" % REL_NS)
    return root

def ensure_content_type_override(content_types_root, part_name, content_type):
    for override in content_types_root.findall("{%s}Override" % NS["ct"]):
        if override.get("PartName") == part_name:
            return
    node = etree.SubElement(content_types_root, "{%s}Override" % NS["ct"])
    node.set("PartName", part_name)
    node.set("ContentType", content_type)

def append_slide_to_presentation(parts, slide_filename):
    presentation = parse_xml_bytes(parts["ppt/presentation.xml"])
    pres_rels = parse_xml_bytes(parts["ppt/_rels/presentation.xml.rels"])

    existing_rids = []
    for rel in pres_rels.findall("{%s}Relationship" % REL_NS):
        rid = rel.get("Id", "")
        if rid.startswith("rId") and rid[3:].isdigit():
            existing_rids.append(int(rid[3:]))
    next_rid = f"rId{(max(existing_rids) + 1) if existing_rids else 1}"

    new_rel = etree.SubElement(pres_rels, "{%s}Relationship" % REL_NS)
    new_rel.set("Id", next_rid)
    new_rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
    new_rel.set("Target", f"slides/{slide_filename}")

    sldIdLst = presentation.find(".//p:sldIdLst", namespaces=NS)
    if sldIdLst is None:
        raise ValueError("presentation.xml missing sldIdLst")
    existing_ids = [int(n.get("id")) for n in sldIdLst.findall("./p:sldId", namespaces=NS) if n.get("id", "").isdigit()]
    next_id = (max(existing_ids) + 1) if existing_ids else 256

    sldId = etree.SubElement(sldIdLst, qn("p", "sldId"))
    sldId.set("id", str(next_id))
    sldId.set("{%s}id" % NS["r"], next_rid)

    parts["ppt/presentation.xml"] = tostring(presentation)
    parts["ppt/_rels/presentation.xml.rels"] = tostring(pres_rels)

def update_content_types(parts, slide_filename):
    root = parse_xml_bytes(parts["[Content_Types].xml"])
    ensure_content_type_override(
        root,
        f"/ppt/slides/{slide_filename}",
        "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
    )
    parts["[Content_Types].xml"] = tostring(root)

def update_app_xml(parts, new_slide_title="Reference"):
    if "docProps/app.xml" not in parts:
        return
    app = parse_xml_bytes(parts["docProps/app.xml"])

    slides = app.find("ep:Slides", namespaces=NS)
    if slides is not None and slides.text and slides.text.isdigit():
        slides.text = str(int(slides.text) + 1)

    titles_vector = app.find("ep:TitlesOfParts/vt:vector", namespaces=NS)
    if titles_vector is not None:
        current_items = titles_vector.findall("vt:lpstr", namespaces=NS)
        lp = etree.SubElement(titles_vector, qn("vt", "lpstr"))
        lp.text = new_slide_title
        titles_vector.set("size", str(len(current_items) + 1))

    parts["docProps/app.xml"] = tostring(app)

def process_pptx(input_path: str, output_path: str, skip_first_slide=True):
    input_p = Path(input_path)
    output_p = Path(output_path)

    parts = read_package(input_p)
    slide_w, slide_h = get_slide_size(parts)
    slide_targets = get_slide_targets_in_order(parts)

    collected_titles = []

    for idx, slide_path in enumerate(slide_targets, 1):
        # Common pattern: leave cover slide untouched.
        if skip_first_slide and idx == 1:
            continue

        slide_root = parse_xml_bytes(parts[slide_path])
        target = choose_title_shape(slide_root, slide_h)
        if target is None:
            continue

        title_text = normalize_spaces(target["text"])
        if not title_text:
            continue

        normalize_text_shape_style(target["shape"], TITLE_FONT, TITLE_SIZE_PT, TITLE_RGB)
        reposition_shape_bottom_center(target["shape"], slide_w, slide_h,
                                       width_ratio=0.70,
                                       box_height_in=0.42,
                                       bottom_margin_in=0.30)

        parts[slide_path] = tostring(slide_root)
        collected_titles.append(title_text)

    unique_titles = deduplicate_preserve_order(collected_titles)

    existing_slide_xmls = [name for name in parts if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
    next_slide_num = next_numeric_suffix(existing_slide_xmls, "ppt/slides/slide", ".xml")
    new_slide_filename = f"slide{next_slide_num}.xml"
    new_slide_path = f"ppt/slides/{new_slide_filename}"
    new_slide_rels_path = f"ppt/slides/_rels/{new_slide_filename}.rels"

    slide_root = build_reference_slide_xml(slide_w, slide_h, unique_titles)
    slide_rels_root = build_empty_slide_rels_xml()

    parts[new_slide_path] = tostring(slide_root)
    parts[new_slide_rels_path] = tostring(slide_rels_root)

    append_slide_to_presentation(parts, new_slide_filename)
    update_content_types(parts, new_slide_filename)
    update_app_xml(parts, new_slide_title="Reference")

    write_package(parts, output_p)

def verify_output(output_path: str):
    output_p = Path(output_path)
    if not output_p.exists():
        raise FileNotFoundError(f"Output was not created: {output_p}")

    with zipfile.ZipFile(output_p, "r") as zf:
        presentation = parse_xml_bytes(zf.read("ppt/presentation.xml"))
        sldIds = presentation.findall(".//p:sldIdLst/p:sldId", namespaces=NS)
        if not sldIds:
            raise ValueError("No slides recorded in presentation.xml")

        app = parse_xml_bytes(zf.read("docProps/app.xml"))
        slides = app.find("ep:Slides", namespaces=NS)
        if slides is None or not slides.text or not slides.text.isdigit():
            raise ValueError("Invalid docProps/app.xml slide count")
        app_count = int(slides.text)
        pres_count = len(sldIds)
        if app_count != pres_count:
            raise ValueError(f"Slide count mismatch: app.xml={app_count}, presentation.xml={pres_count}")

        slide_names = sorted([n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")])
        last_slide = parse_xml_bytes(zf.read(slide_names[-1]))

        auto_num_count = 0
        for p in last_slide.findall(".//a:p", namespaces=NS):
            pPr = p.find("./a:pPr", namespaces=NS)
            if pPr is None:
                continue
            if pPr.find("./a:buAutoNum", namespaces=NS) is not None:
                auto_num_count += 1

        print(f"Verified output: {output_p}")
        print(f"Slide count: {pres_count}")
        print(f"Auto-numbered reference paragraphs: {auto_num_count}")

if __name__ == "__main__":
    # Adapt these paths as needed.
    input_pptx = "/path/to/input.pptx"
    output_pptx = "/path/to/output_processed.pptx"

    process_pptx(input_pptx, output_pptx, skip_first_slide=True)
    verify_output(output_pptx)
```

---

## Validation Checklist

Before you hand off the output, check all of these:

- [ ] Output `.pptx` exists and opens as a valid ZIP archive
- [ ] `ppt/presentation.xml` includes the new slide ID
- [ ] `ppt/_rels/presentation.xml.rels` includes the new slide relationship
- [ ] `[Content_Types].xml` has an override for the new slide part
- [ ] `docProps/app.xml` slide count is incremented
- [ ] Only the intended title-like shape was changed on each content slide
- [ ] Title text remains the correct paper title
- [ ] Title runs use:
  - [ ] font family `Arial`
  - [ ] size `16pt` (`sz="1600"`)
  - [ ] color `#989596`
  - [ ] bold disabled
- [ ] Title text box is wide enough for one-line display
- [ ] Title box is bottom-centered
- [ ] Final slide title is `Reference`
- [ ] Reference entries are deduplicated
- [ ] Reference paragraphs use `a:buAutoNum`

---

## Common Pitfalls

### 1. Using `python` instead of `python3`
In minimal Ubuntu images, `python` may not exist even when `python3` does.

**Avoid:**  
```bash
python script.py
```

**Use:**  
```bash
python3 script.py
```

---

### 2. Assuming `python-pptx` is installed
For this task class, direct OOXML editing is often the correct first-choice path.

**Avoid:** spending time on unavailable libraries.  
**Do:** inspect the ZIP structure immediately.

---

### 3. Editing every text box on a slide
This often breaks ânon-title content unchangedâ checks.

**Avoid:** blanket formatting over all `p:sp` text shapes.  
**Do:** choose one conservative dangling-title candidate per target slide.

---

### 4. Changing text styling on only one run
A title may be split across multiple runs.

**Avoid:** updating only the first `a:rPr`.  
**Do:** iterate all runs, fields, and end-paragraph run properties.

---

### 5. Forgetting to disable wrapping
A wide box alone may not force one-line display if wrapping remains enabled.

**Do:** set `a:bodyPr wrap="none"` and use a sufficiently wide `cx`.

---

### 6. Positioning without reading slide size
Hardcoded coordinates often fail on decks with different aspect ratios.

**Do:** read `p:sldSz` from `presentation.xml` and compute positions proportionally.

---

### 7. Adding a slide without updating relationships and metadata
A new `slideN.xml` file alone is not enough.

**Do update all of:**
- slide XML part,
- slide rels part,
- `presentation.xml`,
- `presentation.xml.rels`,
- `[Content_Types].xml`,
- `docProps/app.xml`.

---

### 8. Writing numbered text instead of real auto-number bullets
Some validators inspect paragraph properties, not visible text.

**Avoid:** `1. Paper Title` as literal run text.  
**Do:** create `<a:buAutoNum type="arabicPeriod" startAt="N">`.

---

### 9. Deduplicating too aggressively
Over-normalization can accidentally merge distinct references.

**Do:** normalize whitespace and case only for comparison; preserve original readable text for output.

---

### 10. Trusting visual intuition without ZIP-level verification
A deck can look âfineâ yet fail structural tests.

**Do:** reopen the generated `.pptx`, inspect XML, and confirm:
- slide count,
- last slide presence,
- bullet numbering nodes,
- run properties,
- shape coordinates.

---

## When to Use This Skill

Choose this workflow when:

- you must manipulate PowerPoint files non-interactively,
- the environment is headless,
- `python-pptx` is unavailable,
- tests inspect XML structure,
- you need precise control over formatting, geometry, and relationships.

If the task is structurally similarâfind target text shapes, standardize them, preserve other content, and append a metadata-driven summary slideâthis XML-first approach is robust and portable.