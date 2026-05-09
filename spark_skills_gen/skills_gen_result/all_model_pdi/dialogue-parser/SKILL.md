---
title: Dialogue Script to JSON Graph Parser
category: dialogue-parser
domain: text-parsing, graph-construction, game-dialogue
tags:
  - dialogue
  - parser
  - json-graph
  - graphviz
  - dot
  - text-processing
  - branching-narrative
dependencies:
  - python>=3.10
  - graphviz==0.20.3 (Python package)
  - graphviz (system package for DOT rendering)
---

# Dialogue Script to JSON Graph Parser

## Overview

This skill covers parsing branching dialogue scripts (RPG/game-style) from a plain-text format into a structured JSON graph and a Graphviz DOT visualization. The input format uses bracketed section headers, speaker lines with arrow transitions, and numbered choice lists. The output is a validated directed graph with typed nodes and labeled edges.

## High-Level Workflow

1. **Read and understand the script format** — Identify section headers (`[SectionName]`), speaker dialogue lines (`Speaker: text -> Target`), and numbered choice lines (`1. [Tag] text -> Target`). Understand that sections are the primary grouping unit.

2. **Tokenize into sections** — Split the raw text into sections keyed by their header name. Each section contains one or more lines that are either dialogue lines or choice lines.

3. **Classify lines into node types** — Lines with a numbered prefix (`1.`, `2.`, etc.) become `"type": "choice"` nodes. Speaker dialogue lines become `"type": "line"` nodes. The section header itself is used as the node `id` prefix.

4. **Extract transitions (edges)** — Parse the `-> TargetSection` arrow from each line. This creates a directed edge from the current node to the target node. Choice edges carry the choice text as a label; line edges use an empty string.

5. **Build the graph data structure** — Collect all nodes and edges into `{"nodes": [...], "edges": [...]}` with the required schema.

6. **Validate the graph** — Ensure all nodes are reachable from the first node, all edge targets exist (with a special exemption for terminal nodes like `End`), and multiple paths can reach the terminal node.

7. **Generate DOT visualization** — Produce a `.dot` file with nodes shaped by type and edges labeled with choice text.

8. **Write outputs** — Save `dialogue.json` and `dialogue.dot`.

## Step 1: Understand the Script Format

The script format has these key elements:

```
[SectionName]
Speaker: Dialogue text. -> NextSection

[ChoiceSection]
1. Choice text one. -> TargetA
2. [Tag] Choice text two. -> TargetB
3. Choice text three. -> TargetC
```

**Critical distinctions:**

- **Section headers** appear on their own line as `[WordChars]` — they define a new dialogue node or choice group.
- **Inline tags** like `[Lie]`, `[Attack]`, `[Persuade]` appear *within* a line and are NOT section headers. They are flavor/skill tags on choices.
- A section that contains numbered lines (`1. ...`, `2. ...`) is a **choice node**. A section with `Speaker: text -> Target` lines is a **line node** (dialogue).
- Some sections may have multiple sequential speaker lines before a transition. Each line becomes its own node, chained together.
- The arrow `->` always points to a section name (the target node ID).

### How to distinguish section headers from inline tags

This is the single most important parsing decision. Use this rule:

```python
import re

def is_section_header(line: str) -> bool:
    """A section header is a line that is ONLY a bracketed identifier."""
    return bool(re.match(r'^\[([A-Za-z_]\w*)\]\s*$', line))
```

Inline tags like `[Lie]` appear mid-line (e.g., `3. [Lie] I'm a merchant...`) and will NOT match this pattern because there is text before or after the brackets on the same line.

## Step 2: Tokenize Into Sections

```python
def tokenize_sections(text: str) -> list[tuple[str, list[str]]]:
    """
    Split script text into sections.
    Returns list of (section_name, [lines_in_section]).
    """
    sections = []
    current_name = None
    current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if is_section_header(line):
            if current_name is not None:
                sections.append((current_name, current_lines))
            current_name = re.match(r'^\[(\w+)\]', line).group(1)
            current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)

    if current_name is not None:
        sections.append((current_name, current_lines))

    return sections
```

## Step 3: Classify Lines and Build Nodes + Edges

```python
CHOICE_RE = re.compile(r'^(\d+)\.\s*(.*?)\s*->\s*(\w+)\s*$')
LINE_RE = re.compile(r'^([^:]+?):\s*(.*?)\s*->\s*(\w+)\s*$')
LINE_NO_ARROW_RE = re.compile(r'^([^:]+?):\s*(.+)$')

def parse_section(section_name: str, lines: list[str]):
    """
    Parse a single section into nodes and edges.
    Returns (nodes_list, edges_list).
    """
    nodes = []
    edges = []

    # Check if this is a choice section (any line starts with a digit + dot)
    has_choices = any(CHOICE_RE.match(l) for l in lines)

    if has_choices:
        # The section header itself is a node (the prompt before choices)
        # Find any non-choice lines as the prompt text
        prompt_lines = [l for l in lines if not CHOICE_RE.match(l)]
        prompt_text = ' '.join(prompt_lines) if prompt_lines else section_name

        # Extract speaker from prompt if present
        speaker = ""
        pm = LINE_NO_ARROW_RE.match(prompt_text)
        if pm:
            speaker = pm.group(1).strip()
            prompt_text = pm.group(2).strip()

        nodes.append({
            "id": section_name,
            "text": prompt_text,
            "speaker": speaker,
            "type": "choice"
        })

        for line in lines:
            cm = CHOICE_RE.match(line)
            if cm:
                _num, choice_text, target = cm.group(1), cm.group(2), cm.group(3)
                # Strip inline tags for cleaner text but keep them in edge label
                edges.append({
                    "from": section_name,
                    "to": target,
                    "text": choice_text.strip()
                })
    else:
        # Sequential dialogue lines — chain them together
        parsed_lines = []
        for line in lines:
            lm = LINE_RE.match(line)
            if lm:
                speaker, text, target = lm.group(1).strip(), lm.group(2).strip(), lm.group(3).strip()
                parsed_lines.append((speaker, text, target))
            else:
                lm2 = LINE_NO_ARROW_RE.match(line)
                if lm2:
                    speaker, text = lm2.group(1).strip(), lm2.group(2).strip()
                    parsed_lines.append((speaker, text, None))

        if len(parsed_lines) == 0:
            # Bare section with no parseable lines — create a pass-through node
            nodes.append({
                "id": section_name,
                "text": section_name,
                "speaker": "",
                "type": "line"
            })
        elif len(parsed_lines) == 1:
            speaker, text, target = parsed_lines[0]
            nodes.append({
                "id": section_name,
                "text": text,
                "speaker": speaker,
                "type": "line"
            })
            if target:
                edges.append({"from": section_name, "to": target, "text": ""})
        else:
            # Multiple lines: first gets the section name as ID,
            # subsequent lines get suffixed IDs, all chained.
            for i, (speaker, text, target) in enumerate(parsed_lines):
                node_id = section_name if i == 0 else f"{section_name}_{i}"
                nodes.append({
                    "id": node_id,
                    "text": text,
                    "speaker": speaker,
                    "type": "line"
                })

                if i < len(parsed_lines) - 1:
                    next_id = section_name if (i + 1) == 0 else f"{section_name}_{i + 1}"
                    edges.append({"from": node_id, "to": next_id, "text": ""})
                elif target:
                    edges.append({"from": node_id, "to": target, "text": ""})

    return nodes, edges
```

## Step 4: Validate the Graph

Three mandatory constraints:

```python
from collections import deque

def validate_graph(graph: dict) -> tuple[bool, list[str]]:
    """
    Validate:
    1. All nodes reachable from the first node
    2. All edge targets exist (except terminal 'End' node)
    3. Multiple paths lead to 'End'
    Returns (is_valid, list_of_issues).
    """
    issues = []
    node_ids = {n["id"] for n in graph["nodes"]}
    edges = graph["edges"]

    # Edge target validity
    terminal_nodes = {"End"}  # Known terminal that may not have its own section
    for e in edges:
        if e["to"] not in node_ids and e["to"] not in terminal_nodes:
            issues.append(f"Edge target '{e['to']}' does not exist as a node")

    # Add terminal nodes to node set for reachability check
    all_ids = node_ids | terminal_nodes

    # Reachability from first node
    if not graph["nodes"]:
        issues.append("No nodes in graph")
        return False, issues

    start_id = graph["nodes"][0]["id"]
    adj = {}
    for e in edges:
        adj.setdefault(e["from"], []).append(e["to"])

    visited = set()
    queue = deque([start_id])
    while queue:
        cur = queue.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        for nb in adj.get(cur, []):
            if nb not in visited and nb in all_ids:
                queue.append(nb)

    unreachable = node_ids - visited
    if unreachable:
        issues.append(f"Unreachable nodes: {unreachable}")

    # Multiple paths to End
    end_sources = {e["from"] for e in edges if e["to"] == "End"}
    if len(end_sources) < 2:
        issues.append(f"Only {len(end_sources)} path(s) to End; need >= 2")

    return len(issues) == 0, issues
```

## Step 5: Generate DOT Visualization

```python
def generate_dot(graph: dict) -> str:
    """Generate Graphviz DOT format string from the graph."""
    lines = ['digraph DialogueGraph {', '    rankdir=TB;', '    node [fontname="Arial"];']

    for node in graph["nodes"]:
        shape = "diamond" if node["type"] == "choice" else "box"
        label = node["text"][:60].replace('"', '\\"')
        if node["speaker"]:
            label = f"{node['speaker']}:\\n{label}"
        lines.append(f'    "{node["id"]}" [label="{label}", shape={shape}];')

    # Also add End node if referenced
    edge_targets = {e["to"] for e in graph["edges"]}
    node_ids = {n["id"] for n in graph["nodes"]}
    for target in edge_targets - node_ids:
        lines.append(f'    "{target}" [label="{target}", shape=doubleoctagon];')

    for edge in graph["edges"]:
        elabel = edge["text"].replace('"', '\\"')[:50] if edge["text"] else ""
        if elabel:
            lines.append(f'    "{edge["from"]}" -> "{edge["to"]}" [label="{elabel}"];')
        else:
            lines.append(f'    "{edge["from"]}" -> "{edge["to"]}";')

    lines.append("}")
    return "\n".join(lines)
```

## Step 6: Assemble the `parse_script` Function

The verifier imports and calls `parse_script(text: str)` — it must return the graph dict.

```python
def parse_script(text: str) -> dict:
    """
    Main entry point. Takes raw script text, returns
    {"nodes": [...], "edges": [...]}.
    """
    sections = tokenize_sections(text)
    all_nodes = []
    all_edges = []

    for section_name, lines in sections:
        nodes, edges = parse_section(section_name, lines)
        all_nodes.extend(nodes)
        all_edges.extend(edges)

    # Ensure End node exists if referenced
    node_ids = {n["id"] for n in all_nodes}
    end_targets = {e["to"] for e in all_edges if e["to"] not in node_ids}
    for t in end_targets:
        all_nodes.append({
            "id": t,
            "text": t,
            "speaker": "",
            "type": "line"
        })

    graph = {"nodes": all_nodes, "edges": all_edges}
    return graph
```

## Common Pitfalls

### 1. Confusing section headers with inline tags
The `[Lie]`, `[Attack]`, `[Persuade]` tags inside choice lines look like section headers but are NOT. Always check that the bracketed text is the ONLY content on the line. Use the `is_section_header` regex that anchors to start and end of line.

### 2. Missing the `End` terminal node
Many scripts reference an `End` node that has no `[End]` section in the script. Your parser must create this node if it's referenced by edges but not defined. Otherwise the "all edge targets must exist" constraint fails.

### 3. Multi-line dialogue sections
A section can have multiple `Speaker: text -> Target` lines. These must be chained as sequential nodes (e.g., `Scene`, `Scene_1`, `Scene_2`) with edges between them. Only the last line's arrow points to the next section. If you flatten them into one node, you lose dialogue and the node/edge counts drop below minimums.

### 4. Node count minimums
Typical test scripts expect 100+ nodes and 200+ edges. If your counts are low, you're probably collapsing multi-line sections or skipping lines that don't match your regex. Add a fallback: if a line doesn't match choice or dialogue patterns, still create a node for it.

### 5. Reachability failures
If any node is unreachable from the first node, the graph is invalid. Common cause: a section name in the script has a typo or case mismatch with the `-> Target` reference. Your parser should use exact string matching (case-sensitive) since the script is the source of truth.

### 6. DOT file must exist
The verifier checks for the existence of `dialogue.dot`. Don't skip this step. It doesn't need to be rendered to an image — just the `.dot` text file.

### 7. The `parse_script` function signature
The verifier imports `solution.py` and calls `parse_script(text)`. It must:
- Accept a single `str` argument (the raw script content)
- Return a `dict` with keys `"nodes"` and `"edges"`
- NOT read from files internally (the verifier passes the text directly)

### 8. Edge `text` field
Choice edges should have the choice label as `text`. Line/dialogue edges should have `text: ""` (empty string, not `None`). The schema validator checks for string type.

### 9. Speaker extraction
The speaker is everything before the first `:` on a dialogue line. For choice nodes, the speaker may come from a prompt line above the numbered choices, or may be empty. Don't force a speaker where there isn't one.

## Output Schema Reference

```json
{
  "nodes": [
    {
      "id": "string — unique node identifier (section name or section_N)",
      "text": "string — dialogue text or choice prompt",
      "speaker": "string — character name or empty string",
      "type": "string — 'line' or 'choice'"
    }
  ],
  "edges": [
    {
      "from": "string — source node id",
      "to": "string — target node id",
      "text": "string — choice label or empty string"
    }
  ]
}
```

## Verification Checklist

Before finalizing, run these checks programmatically:

1. `dialogue.json` exists and is valid JSON
2. Top-level keys are exactly `"nodes"` and `"edges"`, both lists
3. Every node has all four fields: `id`, `text`, `speaker`, `type`
4. Every edge has all three fields: `from`, `to`, `text`
5. All field values are strings (not None, not int)
6. `len(nodes) >= 100` and `len(edges) >= 200` (for typical scripts)
7. All nodes reachable from the first node via BFS
8. All edge targets exist as node IDs
9. At least 2 distinct nodes have edges pointing to `End`
10. Key speakers present (check script for names like Narrator, Barkeep, Merchant, etc.)
11. Key section nodes present (Start, TavernChoice, etc. — read from script)
12. `dialogue.dot` file exists and starts with `digraph`

## Reference Implementation

```python
#!/usr/bin/env python3
"""
Dialogue Script Parser — converts branching dialogue scripts into
a JSON graph and Graphviz DOT visualization.

Usage:
    python solution.py

Reads:  /app/script.txt
Writes: /app/dialogue.json, /app/dialogue.dot

Also exposes parse_script(text) for programmatic / test-harness use.
"""

import json
import re
from collections import deque
from pathlib import Path

# ── Regex patterns ──────────────────────────────────────────────────────────

SECTION_HEADER_RE = re.compile(r'^\[([A-Za-z_]\w*)\]\s*$')
CHOICE_RE = re.compile(r'^(\d+)\.\s*(.*?)\s*->\s*(\w+)\s*$')
LINE_ARROW_RE = re.compile(r'^([^:]+?):\s*(.*?)\s*->\s*(\w+)\s*$')
LINE_NO_ARROW_RE = re.compile(r'^([^:]+?):\s*(.+)$')


# ── Helpers ─────────────────────────────────────────────────────────────────

def is_section_header(line: str) -> bool:
    return bool(SECTION_HEADER_RE.match(line))


def tokenize_sections(text: str) -> list[tuple[str, list[str]]]:
    """Split raw script into (section_name, [content_lines]) pairs."""
    sections: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = SECTION_HEADER_RE.match(line)
        if m:
            if current_name is not None:
                sections.append((current_name, current_lines))
            current_name = m.group(1)
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        sections.append((current_name, current_lines))
    return sections


def parse_section(name: str, lines: list[str]):
    """Return (nodes, edges) for one section."""
    nodes: list[dict] = []
    edges: list[dict] = []

    has_choices = any(CHOICE_RE.match(l) for l in lines)

    if has_choices:
        # ── Choice section ──────────────────────────────────────────────
        prompt_parts = [l for l in lines if not CHOICE_RE.match(l)]
        prompt_text = ' '.join(prompt_parts) if prompt_parts else name

        speaker = ""
        pm = LINE_NO_ARROW_RE.match(prompt_text)
        if pm:
            speaker = pm.group(1).strip()
            prompt_text = pm.group(2).strip()

        nodes.append({
            "id": name,
            "text": prompt_text,
            "speaker": speaker,
            "type": "choice",
        })

        for line in lines:
            cm = CHOICE_RE.match(line)
            if cm:
                choice_text = cm.group(2).strip()
                target = cm.group(3).strip()
                edges.append({"from": name, "to": target, "text": choice_text})
    else:
        # ── Dialogue (line) section ─────────────────────────────────────
        parsed: list[tuple[str, str, str | None]] = []
        for line in lines:
            lm = LINE_ARROW_RE.match(line)
            if lm:
                parsed.append((lm.group(1).strip(), lm.group(2).strip(), lm.group(3).strip()))
            else:
                lm2 = LINE_NO_ARROW_RE.match(line)
                if lm2:
                    parsed.append((lm2.group(1).strip(), lm2.group(2).strip(), None))

        if len(parsed) == 0:
            nodes.append({"id": name, "text": name, "speaker": "", "type": "line"})
        elif len(parsed) == 1:
            sp, txt, tgt = parsed[0]
            nodes.append({"id": name, "text": txt, "speaker": sp, "type": "line"})
            if tgt:
                edges.append({"from": name, "to": tgt, "text": ""})
        else:
            for i, (sp, txt, tgt) in enumerate(parsed):
                nid = name if i == 0 else f"{name}_{i}"
                nodes.append({"id": nid, "text": txt, "speaker": sp, "type": "line"})

                if i < len(parsed) - 1:
                    next_id = f"{name}_{i + 1}"
                    edges.append({"from": nid, "to": next_id, "text": ""})
                elif tgt:
                    edges.append({"from": nid, "to": tgt, "text": ""})

    return nodes, edges


def validate_graph(graph: dict) -> tuple[bool, list[str]]:
    """Check reachability, edge validity, and multiple endings."""
    issues: list[str] = []
    node_ids = {n["id"] for n in graph["nodes"]}
    edges = graph["edges"]
    terminal = {"End"}

    for e in edges:
        if e["to"] not in node_ids and e["to"] not in terminal:
            issues.append(f"Dangling edge target: {e['to']}")

    if not graph["nodes"]:
        issues.append("Empty graph")
        return False, issues

    all_ids = node_ids | terminal
    start = graph["nodes"][0]["id"]
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["from"], []).append(e["to"])

    visited: set[str] = set()
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        for nb in adj.get(cur, []):
            if nb not in visited and nb in all_ids:
                q.append(nb)

    unreachable = node_ids - visited
    if unreachable:
        issues.append(f"Unreachable: {unreachable}")

    end_sources = {e["from"] for e in edges if e["to"] == "End"}
    if len(end_sources) < 2:
        issues.append(f"Only {len(end_sources)} path(s) to End")

    return len(issues) == 0, issues


def generate_dot(graph: dict) -> str:
    """Produce Graphviz DOT source."""
    out = ['digraph DialogueGraph {', '    rankdir=TB;', '    node [fontname="Arial"];']

    for n in graph["nodes"]:
        shape = "diamond" if n["type"] == "choice" else "box"
        lbl = n["text"][:60].replace('"', '\\"').replace('\n', ' ')
        if n["speaker"]:
            lbl = f"{n['speaker']}:\\n{lbl}"
        out.append(f'    "{n["id"]}" [label="{lbl}", shape={shape}];')

    node_ids = {n["id"] for n in graph["nodes"]}
    for t in {e["to"] for e in graph["edges"]} - node_ids:
        out.append(f'    "{t}" [label="{t}", shape=doubleoctagon];')

    for e in graph["edges"]:
        elbl = e["text"].replace('"', '\\"')[:50] if e["text"] else ""
        arrow = f'    "{e["from"]}" -> "{e["to"]}"'
        if elbl:
            arrow += f' [label="{elbl}"]'
        out.append(arrow + ";")

    out.append("}")
    return "\n".join(out)


# ── Main entry point ───────────────────────────────────────────────────────

def parse_script(text: str) -> dict:
    """
    Parse a dialogue script string into a graph dict.

    Returns {"nodes": [...], "edges": [...]}.
    """
    sections = tokenize_sections(text)
    all_nodes: list[dict] = []
    all_edges: list[dict] = []

    for sec_name, sec_lines in sections:
        ns, es = parse_section(sec_name, sec_lines)
        all_nodes.extend(ns)
        all_edges.extend(es)

    # Ensure every edge target exists as a node
    node_ids = {n["id"] for n in all_nodes}
    for target in {e["to"] for e in all_edges} - node_ids:
        all_nodes.append({
            "id": target,
            "text": target,
            "speaker": "",
            "type": "line",
        })

    return {"nodes": all_nodes, "edges": all_edges}


if __name__ == "__main__":
    script_path = Path("/app/script.txt")
    json_path = Path("/app/dialogue.json")
    dot_path = Path("/app/dialogue.dot")

    text = script_path.read_text(encoding="utf-8")
    graph = parse_script(text)

    # Validate
    ok, issues = validate_graph(graph)
    if not ok:
        print("⚠ Validation issues:")
        for iss in issues:
            print(f"  - {iss}")
    else:
        print("✓ Graph valid")

    print(f"  Nodes: {len(graph['nodes'])}")
    print(f"  Edges: {len(graph['edges'])}")

    # Write JSON
    json_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ Wrote {json_path}")

    # Write DOT
    dot_src = generate_dot(graph)
    dot_path.write_text(dot_src, encoding="utf-8")
    print(f"✓ Wrote {dot_path}")

    # Summary stats
    speakers = sorted({n["speaker"] for n in graph["nodes"] if n["speaker"]})
    print(f"  Speakers: {', '.join(speakers)}")
    choice_nodes = [n for n in graph["nodes"] if n["type"] == "choice"]
    print(f"  Choice nodes: {len(choice_nodes)}")
    end_sources = sorted({e["from"] for e in graph["edges"] if e["to"] == "End"})
    print(f"  Paths to End: {len(end_sources)} ({', '.join(end_sources)})")
```

## Environment Notes

- **Runtime:** Python 3.12 (slim image). No pandas/numpy needed.
- **Python `graphviz` package** (0.20.3) is available but not required — writing the DOT string manually is simpler and avoids import overhead. Use the package only if you need to render to PNG/SVG.
- **System `graphviz`** is installed (`dot` CLI available) if rendering is needed.
- The verifier imports `solution.py` directly and calls `parse_script(text)`. The file must be at `/app/solution.py`.
- Output files must be at `/app/dialogue.json` and `/app/dialogue.dot`.