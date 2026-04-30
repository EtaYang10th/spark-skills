---
name: dialogue-script-to-graph-parser
description: Parse sectioned dialogue scripts into validated JSON graph and DOT visualization, implementing parse_script(text) plus export and validation utilities.
tags:
  - parsing
  - dialogue
  - json
  - graph
  - dot
  - graphviz
  - python
version: "1.0"
---

# Dialogue Script to JSON Graph Parser

This skill helps you build a parser for **section-based dialogue scripts** and export them as:

- a validated JSON graph
- a Graphviz `.dot` visualization
- a reusable Python function `parse_script(text: str)`

It is designed for tasks where input scripts look like:

```text
[GateScene]
Guard: Halt! State your name and business. -> NameChoice

[NameChoice]
1. I am Sir Aldric, Knight of the Realm. -> KnightPath
2. Just a humble traveler seeking shelter. -> TravelerPath
3. [Lie] I'm a merchant with important goods. -> MerchantPath
4. [Attack] Draw your sword! -> CombatStart
```

and must be converted into:

```json
{
  "nodes": [
    {"id": "GateScene", "text": "Halt! State your name and business.", "speaker": "Guard", "type": "line"},
    {"id": "NameChoice", "text": "NameChoice", "speaker": "", "type": "choice"}
  ],
  "edges": [
    {"from": "GateScene", "to": "NameChoice", "text": ""},
    {"from": "NameChoice", "to": "KnightPath", "text": "I am Sir Aldric, Knight of the Realm."}
  ]
}
```

## When to Use This Skill

Use this skill when:

- dialogue is organized into `[SectionName]` blocks
- sections may contain a single spoken line or a list of numbered choices
- each line or choice may end with `-> TargetNode`
- the validator expects a graph with `nodes` and `edges`
- you must also generate `.dot` output for visualization

## Output Contract

Unless the task says otherwise, produce JSON in exactly this shape:

```json
{
  "nodes": [
    {"id": "", "text": "", "speaker": "", "type": ""}
  ],
  "edges": [
    {"from": "", "to": "", "text": ""}
  ]
}
```

### Node conventions

Every node should include:

- `id`: unique section identifier
- `text`: spoken text for line nodes, section id or summary text for choice nodes
- `speaker`: speaker name for line nodes, empty string for choice nodes
- `type`: `"line"` or `"choice"`

### Edge conventions

Every edge should include:

- `from`: source node id
- `to`: target node id
- `text`: edge label; usually empty for line transitions, choice label for choices

## High-Level Workflow

### 1. Inspect the script shape before coding

**What to do:** Read the input file and identify actual formatting patterns:
- section header syntax
- whether narrative lines use `Speaker: text`
- whether choices are numbered
- whether targets always use `->`
- whether there are blank lines or comments

**Why:** Dialogue files often look simple but vary in small ways. Early inspection prevents writing a parser that is too narrow or too permissive.

**Decision criteria:**
- If a section contains one or more `^\d+\.` lines, treat it as a choice section.
- If a section contains one spoken/narrative line, treat it as a line section.
- If a line has no `->`, allow it as terminal.
- If target `"End"` is referenced but not defined, only allow it if task rules explicitly permit terminal `End`.

#### Inspection code

```python
from pathlib import Path
import re

def inspect_script(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Script file not found: {path}")

    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()

    section_headers = []
    choice_like = 0
    arrow_lines = 0
    speaker_lines = 0

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if re.fullmatch(r"\[[^\]]+\]", stripped):
            section_headers.append((i, stripped))
        if re.match(r"^\d+\.\s+", stripped):
            choice_like += 1
        if "->" in stripped:
            arrow_lines += 1
        if ":" in stripped and not re.match(r"^\d+\.\s+", stripped):
            speaker_lines += 1

    return {
        "line_count": len(lines),
        "section_count": len(section_headers),
        "choice_line_count": choice_like,
        "arrow_line_count": arrow_lines,
        "speaker_line_count": speaker_lines,
        "first_sections": section_headers[:10],
    }

if __name__ == "__main__":
    info = inspect_script("/app/script.txt")
    print(info)
```

---

### 2. Parse the script into sections first

**What to do:** Split the file into named sections before interpreting section contents.

**Why:** Section structure is the backbone of the graph. If section extraction is wrong, node and edge generation will also be wrong.

**Decision criteria:**
- A header should match `^\[([^\]]+)\]$`
- Preserve section order; the first section is usually the graph entrypoint
- Ignore empty lines inside sections
- Reject duplicate section ids unless task explicitly allows overriding

#### Section parser

```python
import re
from typing import List, Dict, Any

SECTION_RE = re.compile(r"^\[([^\]]+)\]$")
COMMENT_PREFIXES = ("#", "//", ";")

def parse_sections(text: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith(COMMENT_PREFIXES):
            continue

        m = SECTION_RE.fullmatch(stripped)
        if m:
            section_id = m.group(1).strip()
            if not section_id:
                raise ValueError(f"Empty section id at line {lineno}")
            current = {
                "id": section_id,
                "lines": [],
                "lineno": lineno,
            }
            sections.append(current)
            continue

        if current is None:
            raise ValueError(
                f"Content found before first section header at line {lineno}: {stripped!r}"
            )

        current["lines"].append({
            "text": stripped,
            "lineno": lineno,
        })

    if not sections:
        raise ValueError("No sections found in script")

    seen = set()
    for sec in sections:
        if sec["id"] in seen:
            raise ValueError(f"Duplicate section id: {sec['id']}")
        seen.add(sec["id"])

    return sections
```

---

### 3. Classify each section as `line` or `choice`

**What to do:** Determine whether a section contains a spoken/narrative line or a list of numbered choices.

**Why:** Node type controls both node fields and edge labeling rules.

**Decision criteria:**
- If **all meaningful lines** in a section match numbered choices, it is a `choice`
- Otherwise, if the section contains a single content line, parse it as `line`
- If the section contains multiple non-choice lines, either:
  - reject as unsupported, or
  - combine carefully only if task format clearly allows it

#### Section classification

```python
import re
from typing import Dict, Any

CHOICE_RE = re.compile(r"^\d+\.\s+(.+?)(?:\s*->\s*([A-Za-z0-9_][\w-]*))?$")

def classify_section(section: Dict[str, Any]) -> str:
    raw_lines = [entry["text"] for entry in section["lines"] if entry["text"].strip()]
    if not raw_lines:
        raise ValueError(f"Section [{section['id']}] is empty")

    choice_matches = [bool(CHOICE_RE.match(line)) for line in raw_lines]
    if all(choice_matches):
        return "choice"

    if len(raw_lines) == 1:
        return "line"

    raise ValueError(
        f"Section [{section['id']}] has unsupported mixed or multiline content"
    )
```

---

### 4. Parse line sections robustly

**What to do:** Parse line nodes from `Speaker: text -> Target` or narrative text without a speaker.

**Why:** Validators often inspect node content and speakers. Mis-parsing speaker vs text is a common cause of downstream failures.

**Decision criteria:**
- Split target from the right using `->`
- Split speaker from text using the first `:`
- If no speaker exists, use `"Narrator"` when narrative text is clearly expected, or `""` if task requires blank speakers. Prefer consistency with validator expectations.
- For line edges, edge `text` should usually be `""`

#### Line parser

```python
import re
from typing import Tuple, Optional, Dict, Any

TARGET_RE = re.compile(r"^(.*?)(?:\s*->\s*([A-Za-z0-9_][\w-]*))?$")

def parse_line_content(line: str) -> Tuple[str, str, Optional[str]]:
    """
    Returns: (speaker, text, target)
    """
    m = TARGET_RE.fullmatch(line.strip())
    if not m:
        raise ValueError(f"Invalid line syntax: {line!r}")

    body = (m.group(1) or "").strip()
    target = m.group(2)

    if not body:
        raise ValueError(f"Missing line content before target in: {line!r}")

    if ":" in body:
        speaker, text = body.split(":", 1)
        speaker = speaker.strip()
        text = text.strip()
        if not speaker:
            speaker = "Narrator"
    else:
        speaker = "Narrator"
        text = body.strip()

    if not text:
        raise ValueError(f"Missing line text in: {line!r}")

    return speaker, text, target

def build_line_node(section: Dict[str, Any]) -> Dict[str, Any]:
    if len(section["lines"]) != 1:
        raise ValueError(f"Line section [{section['id']}] must contain exactly one line")

    content = section["lines"][0]["text"]
    speaker, text, target = parse_line_content(content)

    node = {
        "id": section["id"],
        "text": text,
        "speaker": speaker,
        "type": "line",
    }
    edge = None
    if target:
        edge = {
            "from": section["id"],
            "to": target,
            "text": "",
        }
    return {"node": node, "edges": [edge] if edge else []}
```

---

### 5. Parse choice sections as one node with labeled outgoing edges

**What to do:** Represent a choice block as a single node of type `choice`, then create one edge per option.

**Why:** This matches common validators and avoids inventing extra intermediate nodes.

**Decision criteria:**
- Choice node `id` should be the section name
- Choice node `speaker` should be `""`
- Choice node `text` should usually be the section id or a short prompt if present
- For each choice line, edge `text` is the visible option label
- Preserve tags like `[Lie]` or `[Attack]` as part of the label unless task instructions say otherwise

#### Choice parser

```python
import re
from typing import Dict, Any, List

CHOICE_RE = re.compile(r"^\d+\.\s+(.+?)(?:\s*->\s*([A-Za-z0-9_][\w-]*))?$")

def parse_choice_line(line: str) -> Dict[str, str]:
    m = CHOICE_RE.fullmatch(line.strip())
    if not m:
        raise ValueError(f"Invalid choice syntax: {line!r}")

    label = (m.group(1) or "").strip()
    target = m.group(2)

    if not label:
        raise ValueError(f"Choice label is empty: {line!r}")
    if not target:
        raise ValueError(f"Choice target is missing: {line!r}")

    return {"label": label, "target": target}

def build_choice_node(section: Dict[str, Any]) -> Dict[str, Any]:
    node = {
        "id": section["id"],
        "text": section["id"],
        "speaker": "",
        "type": "choice",
    }

    edges: List[Dict[str, str]] = []
    for entry in section["lines"]:
        parsed = parse_choice_line(entry["text"])
        edges.append({
            "from": section["id"],
            "to": parsed["target"],
            "text": parsed["label"],
        })

    return {"node": node, "edges": edges}
```

---

### 6. Build `parse_script(text)` as the authoritative parser

**What to do:** Assemble section parsing, classification, node generation, and edge collection into the required public function.

**Why:** The validator may import and call `parse_script(text)` directly. This function must be deterministic and return the final graph object.

**Decision criteria:**
- Return a plain dictionary: `{"nodes": [...], "edges": [...]}`
- Preserve section order in `nodes`
- Do not silently discard malformed input
- Validate basic shape before returning

#### Complete parser function

```python
from typing import Dict, Any, List

def parse_script(text: str) -> Dict[str, Any]:
    sections = parse_sections(text)

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    for section in sections:
        section_type = classify_section(section)
        if section_type == "line":
            built = build_line_node(section)
        elif section_type == "choice":
            built = build_choice_node(section)
        else:
            raise ValueError(f"Unsupported section type for [{section['id']}]")

        nodes.append(built["node"])
        edges.extend(built["edges"])

    graph = {"nodes": nodes, "edges": edges}
    validate_graph(graph)
    return graph
```

---

### 7. Validate graph constraints before writing outputs

**What to do:** Verify reachability, target existence, schema consistency, and basic graph integrity.

**Why:** Many tasks fail not because parsing crashes, but because the parser emits a graph that violates hidden structural checks.

**Decision criteria:**
- All node ids must be unique
- All nodes must be reachable from the first node
- Every edge target must exist, except an explicitly allowed terminal target such as `"End"`
- Edge sources must always exist
- Node types must be only `"line"` or `"choice"`

#### Graph validator

```python
from collections import deque
from typing import Dict, Any, Set

ALLOWED_TERMINAL_TARGETS = {"End"}

def validate_graph(graph: Dict[str, Any]) -> None:
    if not isinstance(graph, dict):
        raise TypeError("Graph must be a dictionary")

    nodes = graph.get("nodes")
    edges = graph.get("edges")

    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("Graph must contain lists for 'nodes' and 'edges'")

    if not nodes:
        raise ValueError("Graph must contain at least one node")

    node_ids = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError(f"Node must be a dict: {node!r}")
        for key in ("id", "text", "speaker", "type"):
            if key not in node:
                raise ValueError(f"Node missing key '{key}': {node!r}")
        if node["type"] not in {"line", "choice"}:
            raise ValueError(f"Unsupported node type: {node['type']!r}")
        node_ids.append(node["id"])

    if len(set(node_ids)) != len(node_ids):
        raise ValueError("Duplicate node ids detected")

    node_set: Set[str] = set(node_ids)
    adjacency = {node_id: [] for node_id in node_ids}

    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError(f"Edge must be a dict: {edge!r}")
        for key in ("from", "to", "text"):
            if key not in edge:
                raise ValueError(f"Edge missing key '{key}': {edge!r}")

        src = edge["from"]
        dst = edge["to"]

        if src not in node_set:
            raise ValueError(f"Edge source does not exist: {src!r}")

        if dst not in node_set and dst not in ALLOWED_TERMINAL_TARGETS:
            raise ValueError(f"Edge target does not exist: {dst!r}")

        if dst in node_set:
            adjacency[src].append(dst)

    start = nodes[0]["id"]
    reachable = set()
    queue = deque([start])

    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        for nxt in adjacency.get(current, []):
            if nxt not in reachable:
                queue.append(nxt)

    unreachable = [node_id for node_id in node_ids if node_id not in reachable]
    if unreachable:
        raise ValueError(f"Unreachable nodes detected: {unreachable}")
```

---

### 8. Export exact JSON schema with stable formatting

**What to do:** Serialize the graph in a deterministic, validator-friendly way.

**Why:** Stable formatting helps debugging and reduces accidental schema drift.

**Decision criteria:**
- Use UTF-8
- Use `ensure_ascii=False`
- Use indenting for readability unless task requests minified JSON

#### JSON writer

```python
import json
from pathlib import Path
from typing import Dict, Any

def write_json(graph: Dict[str, Any], path: str) -> None:
    out = Path(path)
    out.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
```

---

### 9. Export a `.dot` graph that is readable and syntactically safe

**What to do:** Create a Graphviz DOT file with shape distinctions and escaped labels.

**Why:** DOT output is often checked for existence and sometimes manually inspected. Bad escaping can break rendering even when JSON is valid.

**Decision criteria:**
- Use `digraph`
- Render `line` nodes as boxes
- Render `choice` nodes as diamonds
- Escape backslashes, quotes, and newlines
- Include node id, type, and text/speaker in labels

#### DOT exporter

```python
from typing import Dict, Any, List

def dot_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )

def graph_to_dot(graph: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("digraph Dialogue {")
    lines.append('  rankdir=LR;')
    lines.append('  node [fontname="Helvetica"];')

    for node in graph["nodes"]:
        node_id = dot_escape(node["id"])
        shape = "diamond" if node["type"] == "choice" else "box"

        if node["type"] == "choice":
            label = f'{node["id"]}\\n[{node["type"]}]\\n{node["text"]}'
        else:
            label = f'{node["id"]}\\n[{node["type"]}]\\n{node["speaker"]}: {node["text"]}'

        lines.append(
            f'  "{node_id}" [shape={shape}, label="{dot_escape(label)}"];'
        )

    for edge in graph["edges"]:
        src = dot_escape(edge["from"])
        dst = dot_escape(edge["to"])
        edge_text = edge.get("text", "")

        if edge_text:
            lines.append(
                f'  "{src}" -> "{dst}" [label="{dot_escape(edge_text)}"];'
            )
        else:
            lines.append(f'  "{src}" -> "{dst}";')

    lines.append("}")
    return "\n".join(lines) + "\n"

def write_dot(graph: Dict[str, Any], path: str) -> None:
    from pathlib import Path
    Path(path).write_text(graph_to_dot(graph), encoding="utf-8")
```

---

### 10. Provide a CLI entrypoint that reads input and writes both outputs

**What to do:** Implement a script entrypoint that reads `/app/script.txt`, parses it, validates it, and writes `/app/dialogue.json` and `/app/dialogue.dot`.

**Why:** Most evaluation harnesses execute the solution file directly, not just import functions.

**Decision criteria:**
- Exit nonzero on failure
- Print concise diagnostic messages to stderr
- Avoid hard-coding non-required paths unless task specifies them

#### End-to-end solution file pattern

```python
#!/usr/bin/env python3
import sys
from pathlib import Path

def main() -> int:
    input_path = Path("/app/script.txt")
    json_path = Path("/app/dialogue.json")
    dot_path = Path("/app/dialogue.dot")

    try:
        text = input_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Input script not found: {input_path}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed reading input: {exc}", file=sys.stderr)
        return 1

    try:
        graph = parse_script(text)
        write_json(graph, str(json_path))
        write_dot(graph, str(dot_path))
    except Exception as exc:
        print(f"Failed to parse/export dialogue: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {len(graph['nodes'])} nodes and {len(graph['edges'])} edges "
        f"to {json_path} and {dot_path}"
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Full Reference Implementation

If you need a complete starting point, use this integrated implementation and adapt only where the task format differs.

```python
#!/usr/bin/env python3
import json
import re
import sys
from collections import deque
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Set

SECTION_RE = re.compile(r"^\[([^\]]+)\]$")
CHOICE_RE = re.compile(r"^\d+\.\s+(.+?)(?:\s*->\s*([A-Za-z0-9_][\w-]*))?$")
TARGET_RE = re.compile(r"^(.*?)(?:\s*->\s*([A-Za-z0-9_][\w-]*))?$")
COMMENT_PREFIXES = ("#", "//", ";")
ALLOWED_TERMINAL_TARGETS = {"End"}

def parse_sections(text: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()

        if not stripped:
            continue
        if stripped.startswith(COMMENT_PREFIXES):
            continue

        m = SECTION_RE.fullmatch(stripped)
        if m:
            section_id = m.group(1).strip()
            if not section_id:
                raise ValueError(f"Empty section id at line {lineno}")
            current = {"id": section_id, "lines": [], "lineno": lineno}
            sections.append(current)
            continue

        if current is None:
            raise ValueError(f"Content before first section at line {lineno}")

        current["lines"].append({"text": stripped, "lineno": lineno})

    if not sections:
        raise ValueError("No sections found")

    seen = set()
    for sec in sections:
        if sec["id"] in seen:
            raise ValueError(f"Duplicate section id: {sec['id']}")
        seen.add(sec["id"])

    return sections

def classify_section(section: Dict[str, Any]) -> str:
    raw_lines = [entry["text"] for entry in section["lines"] if entry["text"].strip()]
    if not raw_lines:
        raise ValueError(f"Section [{section['id']}] is empty")

    if all(CHOICE_RE.match(line) for line in raw_lines):
        return "choice"

    if len(raw_lines) == 1:
        return "line"

    raise ValueError(f"Unsupported content in section [{section['id']}]")

def parse_line_content(line: str) -> Tuple[str, str, Optional[str]]:
    m = TARGET_RE.fullmatch(line.strip())
    if not m:
        raise ValueError(f"Invalid line syntax: {line!r}")

    body = (m.group(1) or "").strip()
    target = m.group(2)

    if not body:
        raise ValueError(f"Missing body in line: {line!r}")

    if ":" in body:
        speaker, text = body.split(":", 1)
        speaker = speaker.strip() or "Narrator"
        text = text.strip()
    else:
        speaker = "Narrator"
        text = body

    if not text:
        raise ValueError(f"Missing text in line: {line!r}")

    return speaker, text, target

def parse_choice_line(line: str) -> Dict[str, str]:
    m = CHOICE_RE.fullmatch(line.strip())
    if not m:
        raise ValueError(f"Invalid choice syntax: {line!r}")

    label = (m.group(1) or "").strip()
    target = m.group(2)

    if not label:
        raise ValueError(f"Empty choice label: {line!r}")
    if not target:
        raise ValueError(f"Missing choice target: {line!r}")

    return {"label": label, "target": target}

def build_line_node(section: Dict[str, Any]) -> Dict[str, Any]:
    content = section["lines"][0]["text"]
    speaker, text, target = parse_line_content(content)

    node = {
        "id": section["id"],
        "text": text,
        "speaker": speaker,
        "type": "line",
    }
    edges = []
    if target:
        edges.append({"from": section["id"], "to": target, "text": ""})
    return {"node": node, "edges": edges}

def build_choice_node(section: Dict[str, Any]) -> Dict[str, Any]:
    node = {
        "id": section["id"],
        "text": section["id"],
        "speaker": "",
        "type": "choice",
    }
    edges = []
    for entry in section["lines"]:
        parsed = parse_choice_line(entry["text"])
        edges.append({
            "from": section["id"],
            "to": parsed["target"],
            "text": parsed["label"],
        })
    return {"node": node, "edges": edges}

def validate_graph(graph: Dict[str, Any]) -> None:
    nodes = graph.get("nodes")
    edges = graph.get("edges")

    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("Graph must contain 'nodes' and 'edges' lists")
    if not nodes:
        raise ValueError("Graph must contain at least one node")

    node_ids = []
    for node in nodes:
        for key in ("id", "text", "speaker", "type"):
            if key not in node:
                raise ValueError(f"Node missing key '{key}'")
        if node["type"] not in {"line", "choice"}:
            raise ValueError(f"Invalid node type: {node['type']}")
        node_ids.append(node["id"])

    if len(set(node_ids)) != len(node_ids):
        raise ValueError("Duplicate node ids")

    node_set: Set[str] = set(node_ids)
    adjacency = {node_id: [] for node_id in node_ids}

    for edge in edges:
        for key in ("from", "to", "text"):
            if key not in edge:
                raise ValueError(f"Edge missing key '{key}'")

        src = edge["from"]
        dst = edge["to"]

        if src not in node_set:
            raise ValueError(f"Missing edge source node: {src}")
        if dst not in node_set and dst not in ALLOWED_TERMINAL_TARGETS:
            raise ValueError(f"Missing edge target node: {dst}")

        if dst in node_set:
            adjacency[src].append(dst)

    start = nodes[0]["id"]
    visited = set()
    queue = deque([start])

    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        queue.extend(adjacency.get(node_id, []))

    unreachable = [node_id for node_id in node_ids if node_id not in visited]
    if unreachable:
        raise ValueError(f"Unreachable nodes: {unreachable}")

def parse_script(text: str) -> Dict[str, Any]:
    sections = parse_sections(text)
    nodes = []
    edges = []

    for section in sections:
        kind = classify_section(section)
        built = build_choice_node(section) if kind == "choice" else build_line_node(section)
        nodes.append(built["node"])
        edges.extend(built["edges"])

    graph = {"nodes": nodes, "edges": edges}
    validate_graph(graph)
    return graph

def dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

def graph_to_dot(graph: Dict[str, Any]) -> str:
    lines = [
        "digraph Dialogue {",
        '  rankdir=LR;',
        '  node [fontname="Helvetica"];',
    ]

    for node in graph["nodes"]:
        shape = "diamond" if node["type"] == "choice" else "box"
        if node["type"] == "choice":
            label = f'{node["id"]}\\n[{node["type"]}]\\n{node["text"]}'
        else:
            label = f'{node["id"]}\\n[{node["type"]}]\\n{node["speaker"]}: {node["text"]}'
        lines.append(
            f'  "{dot_escape(node["id"])}" [shape={shape}, label="{dot_escape(label)}"];'
        )

    for edge in graph["edges"]:
        if edge["text"]:
            lines.append(
                f'  "{dot_escape(edge["from"])}" -> "{dot_escape(edge["to"])}" '
                f'[label="{dot_escape(edge["text"])}"];'
            )
        else:
            lines.append(
                f'  "{dot_escape(edge["from"])}" -> "{dot_escape(edge["to"])}";'
            )

    lines.append("}")
    return "\n".join(lines) + "\n"

def write_json(graph: Dict[str, Any], path: str) -> None:
    Path(path).write_text(
        json.dumps(graph, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def write_dot(graph: Dict[str, Any], path: str) -> None:
    Path(path).write_text(graph_to_dot(graph), encoding="utf-8")

def main() -> int:
    input_path = Path("/app/script.txt")
    json_path = Path("/app/dialogue.json")
    dot_path = Path("/app/dialogue.dot")

    try:
        text = input_path.read_text(encoding="utf-8")
        graph = parse_script(text)
        write_json(graph, str(json_path))
        write_dot(graph, str(dot_path))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Success: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Verification Checklist

Before finalizing, confirm all of the following:

1. `parse_script(text)` exists and returns a dictionary
2. Output JSON contains top-level keys:
   - `nodes`
   - `edges`
3. Every node contains:
   - `id`
   - `text`
   - `speaker`
   - `type`
4. Every edge contains:
   - `from`
   - `to`
   - `text`
5. The first node is the first section in the file
6. Every non-terminal edge target exists as a node
7. All nodes are reachable from the first node
8. Choice nodes use labeled outgoing edges
9. Line nodes store spoken content in `text`
10. DOT file exists and is syntactically valid

## Optional DOT Syntax Check

If Graphviz is installed, validate the output after writing it:

```python
import subprocess
from pathlib import Path

def verify_dot(path: str) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"DOT file not found: {path}")

    result = subprocess.run(
        ["dot", "-Tsvg", str(p)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"DOT validation failed:\n{result.stderr}")

if __name__ == "__main__":
    verify_dot("/app/dialogue.dot")
```

## Common Pitfalls

### 1. Parsing content before section boundaries are correct
If section extraction is wrong, everything else will look inconsistent. Always parse `[Section]` headers first.

### 2. Creating one node per choice line
For this task family, a **choice block is usually one node**, not many. The options become **edges**, not nodes.

### 3. Dropping edge labels on choices
Choice text belongs in `edge["text"]`. If you leave choice edge labels empty, validators or downstream tools may reject the graph as semantically incomplete.

### 4. Losing the speaker when splitting `Speaker: text -> Target`
Split `->` first, then parse `speaker: text` from the remaining body. This avoids accidentally including targets inside the text.

### 5. Treating all target names as required nodes
Some tasks explicitly allow a terminal target like `End` without a corresponding node definition. Make this allowance explicit, not accidental.

### 6. Forgetting reachability validation
A graph can be structurally valid but still fail if some nodes are disconnected. Always BFS/DFS from the first node.

### 7. Producing invalid DOT due to unescaped quotes
Dialogue text often contains quotes and apostrophes. Always escape backslashes, quotes, and newlines before writing DOT labels.

### 8. Silently accepting mixed-content sections
A section with both dialogue lines and numbered choices usually indicates malformed input or a format variant. Raise a clear error unless the spec explicitly supports it.

### 9. Using inconsistent speaker defaults
If a line lacks `Speaker:`, use a consistent fallback such as `"Narrator"` when narrative content is expected. Do not alternate between `""` and `"Narrator"` arbitrarily.

### 10. Writing the files but forgetting the importable function
Some validators call `parse_script(text)` directly. The CLI alone is not enough.

## Environment Notes

Useful tools commonly available in this task family:

- **Python 3.12**
- **graphviz** Python package
- **Graphviz CLI** (`dot`)

If the task only requires `.dot`, you do **not** need to render PNG/SVG unless explicitly requested.

## Strategy Summary

Choose this execution path early:

1. inspect the script format
2. parse into ordered sections
3. classify each section as `line` or `choice`
4. generate nodes and edges directly from sections
5. validate graph structure
6. export JSON and DOT
7. run a final syntax/integrity check

This path is reliable because it matches how dialogue scripts are typically authored: **section ids define nodes, dialogue lines define node payloads, and arrows define transitions**.