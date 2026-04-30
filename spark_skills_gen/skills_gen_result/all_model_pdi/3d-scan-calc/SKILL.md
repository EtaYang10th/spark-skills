---
name: binary-stl-main-component-mass
description: Parse a binary STL whose 2-byte triangle attribute stores Material ID, isolate the largest connected component, compute its enclosed volume, map material ID to density from a markdown table, and write a JSON mass report.
tools: [python3, bash, sed, cat, file]
domain: 3d-scan-calc
version: "1.0"
---

# Binary STL Main-Part Mass Calculation

This skill covers tasks where:

- the input is a **binary STL**
- each triangle record's **2-byte Attribute Byte Count** is repurposed to store a **Material ID**
- the scan contains **debris or multiple disconnected components**
- you must identify the **main part**, compute its **volume**, look up **density**, and output **mass** as JSON

The key pattern is:

1. Read the binary STL correctly.
2. Build connected components using **shared exact vertices**.
3. Pick the **largest component by absolute enclosed volume**, not by triangle count.
4. Extract the component's Material ID from triangle attributes.
5. Parse the density table robustly.
6. Compute `mass = volume * density`.
7. Write the exact JSON schema expected by the validator.

---

## When to Use This Skill

Use this workflow when all or most of the following are true:

- the STL is explicitly described as **binary**
- triangle attributes encode metadata such as material, label, or part ID
- the scan may include dust, supports, or detached artifacts
- the validator expects a single part's mass, not the total mass of all triangles
- the material table is provided as a human-readable markdown file, not a strict CSV/JSON API

---

## High-Level Workflow

1. **Inspect the inputs and confirm file formats**
   - Why: many failures come from assuming ASCII STL, misreading attribute bytes, or guessing density table structure.
   - Verify the STL is binary and inspect the markdown density table before coding assumptions.

```bash
#!/usr/bin/env bash
set -euo pipefail

ls -l /root/scan_data.stl /root/material_density_table.md
file /root/scan_data.stl
sed -n '1,220p' /root/material_density_table.md
```

2. **Parse the binary STL exactly according to the format**
   - Why: each triangle record is 50 bytes: 12-byte normal, 36-byte vertices, 2-byte attribute.
   - Decision rule: reject malformed lengths early rather than silently truncating.
   - Important: the attribute field is **not ignorable** here; it stores the Material ID.

```python
#!/usr/bin/env python3
import os
import struct
from typing import List, Tuple

Triangle = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float], int]

def load_binary_stl(path: str) -> List[Triangle]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"STL not found: {path}")

    with open(path, "rb") as f:
        header = f.read(80)
        if len(header) != 80:
            raise ValueError("Invalid STL: missing 80-byte header")

        count_bytes = f.read(4)
        if len(count_bytes) != 4:
            raise ValueError("Invalid STL: missing triangle count")

        tri_count = struct.unpack("<I", count_bytes)[0]
        triangles: List[Triangle] = []

        for i in range(tri_count):
            rec = f.read(50)
            if len(rec) != 50:
                raise ValueError(f"Invalid STL: triangle {i} record has length {len(rec)}, expected 50")

            vals = struct.unpack("<12fH", rec)
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            attr = int(vals[12])
            triangles.append((v1, v2, v3, attr))

        trailing = f.read()
        if trailing not in (b"",):
            # Some files may contain trailing bytes; surface it so the caller can decide.
            # Do not silently reinterpret unknown content.
            raise ValueError(f"Unexpected trailing bytes after STL triangle data: {len(trailing)}")

    return triangles

if __name__ == "__main__":
    tris = load_binary_stl("/root/scan_data.stl")
    print(f"Loaded {len(tris)} triangles")
```

3. **Build connectivity using exact shared vertices**
   - Why: the successful pattern for this task family used exact vertex identity, which is typical for STL meshes exported from CAD or scan post-processing.
   - Decision rule: triangles belong to the same component if they share at least one exact vertex tuple.
   - Do **not** start with triangle count as the âlargestâ metric; use connectivity first, then volume.

```python
#!/usr/bin/env python3
from collections import defaultdict
from typing import Dict, List, Tuple

Vertex = Tuple[float, float, float]

def build_components(triangles):
    parent = list(range(len(triangles)))
    rank = [0] * len(triangles)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1

    vertex_to_faces: Dict[Vertex, List[int]] = defaultdict(list)
    for i, (v1, v2, v3, _attr) in enumerate(triangles):
        vertex_to_faces[v1].append(i)
        vertex_to_faces[v2].append(i)
        vertex_to_faces[v3].append(i)

    for face_ids in vertex_to_faces.values():
        if len(face_ids) > 1:
            first = face_ids[0]
            for other in face_ids[1:]:
                union(first, other)

    components = defaultdict(list)
    for i in range(len(triangles)):
        components[find(i)].append(i)

    return list(components.values())
```

4. **Compute enclosed volume per component using the signed tetrahedron formula**
   - Why: volume is the reliable metric for choosing the main part when debris may have many small triangles.
   - Decision rule: choose the component with the largest **absolute signed volume**.
   - Important: many scan tasks use coordinates in mm, but do **not** blindly convert units. Use the task evidence and expected scale. If the validator or success chain suggests raw STL coordinates already match density units, keep them as-is.

```python
#!/usr/bin/env python3
from typing import Iterable

def signed_volume_of_component(triangles, face_ids: Iterable[int]) -> float:
    volume = 0.0
    for idx in face_ids:
        v1, v2, v3, _attr = triangles[idx]
        volume += (
            v1[0] * (v2[1] * v3[2] - v2[2] * v3[1])
            - v1[1] * (v2[0] * v3[2] - v2[2] * v3[0])
            + v1[2] * (v2[0] * v3[1] - v2[1] * v3[0])
        ) / 6.0
    return volume

def largest_component_by_volume(triangles, components):
    if not components:
        raise ValueError("No connected components found")
    return max(components, key=lambda ids: abs(signed_volume_of_component(triangles, ids)))
```

5. **Extract the Material ID from the main component**
   - Why: debris may carry different attributes; you want the material associated with the selected part.
   - Decision rule:
     - collect all attribute values inside the chosen component
     - prefer IDs that exist in the density table
     - typically the dominant attribute in the component is the correct material
   - Guard against junk attribute values not present in the table.

```python
#!/usr/bin/env python3
from collections import Counter
from typing import Dict

def choose_material_id_for_component(triangles, face_ids, material_density: Dict[int, float]) -> int:
    attrs = Counter(int(triangles[idx][3]) for idx in face_ids)
    valid = [aid for aid in attrs if aid in material_density]
    if not valid:
        raise ValueError(f"No component attribute matched known material IDs. Component attrs: {dict(attrs)}")
    return max(valid, key=lambda aid: attrs[aid])
```

6. **Parse the markdown density table robustly**
   - Why: markdown tables vary in spacing, separators, and units.
   - Decision rule: extract integer material IDs and numeric densities from lines that look tabular.
   - Important: validate that the chosen material ID exists before computing mass.

```python
#!/usr/bin/env python3
import os
import re
from typing import Dict

def parse_material_density_table(path: str) -> Dict[int, float]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Density table not found: {path}")

    densities: Dict[int, float] = {}
    line_re = re.compile(r"\|\s*([0-9]+)\s*\|.*?\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|")

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line.startswith("|"):
                continue
            if set(line.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
                continue

            m = line_re.search(line)
            if m:
                material_id = int(m.group(1))
                density = float(m.group(2))
                densities[material_id] = density

    if not densities:
        raise ValueError("Could not parse any material densities from markdown table")

    return densities
```

7. **Compute mass and write the required JSON exactly**
   - Why: validators often fail on schema, types, or wrong field names even if the geometry is right.
   - Required schema:
     ```json
     {
       "main_part_mass": 12345.67,
       "material_id": 42
     }
     ```
   - `material_id` should be an integer.
   - `main_part_mass` should be numeric.

```python
#!/usr/bin/env python3
import json
from typing import Any, Dict

def write_mass_report(path: str, mass: float, material_id: int) -> None:
    if not isinstance(material_id, int):
        raise TypeError("material_id must be an int")
    if not isinstance(mass, (int, float)):
        raise TypeError("mass must be numeric")

    report: Dict[str, Any] = {
        "main_part_mass": float(mass),
        "material_id": material_id,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
```

8. **Sanity-check before finalizing**
   - Why: most failures in this task family are semantic, not syntactic.
   - Verify:
     - there is more than one component if debris is expected
     - the chosen component has the largest absolute volume
     - the selected material ID exists in the density table
     - the mass is positive
     - the JSON schema matches exactly
   - If the number looks off by a factor of 1000, revisit unit assumptions before forcing a conversion.

```python
#!/usr/bin/env python3
import json

def validate_output(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert set(data.keys()) == {"main_part_mass", "material_id"}
    assert isinstance(data["material_id"], int)
    assert isinstance(data["main_part_mass"], (int, float))
    assert data["main_part_mass"] > 0
    print("Output schema and positivity checks passed")
```

---

## Output Contract

Write a JSON file with exactly this structure:

```json
{
  "main_part_mass": 12345.67,
  "material_id": 42
}
```

Rules:

- use the keys `main_part_mass` and `material_id` exactly
- `material_id` must be an integer
- `main_part_mass` must be numeric
- do not add extra keys unless the task explicitly allows them

---

## Domain Notes

### Binary STL Record Layout

Each triangle record is exactly 50 bytes:

- 12 bytes: normal vector (`3 x float32`)
- 36 bytes: vertices (`9 x float32`)
- 2 bytes: attribute byte count (`uint16`)

For these tasks, that final `uint16` should be interpreted as a **Material ID**.

### Connectivity Rule

Use **exact shared vertices** as the default connectivity heuristic:

- map each vertex tuple `(x, y, z)` to the triangles that contain it
- union all triangles sharing the same vertex
- each disjoint union-find set is one connected component

This worked for the successful execution chain and is the correct first-choice strategy for similar tasks.

### Volume Formula

For one triangle with vertices `a, b, c`, its contribution to the signed volume is:

\[
V = \frac{1}{6} \, a \cdot (b \times c)
\]

Summing over all faces gives the signed enclosed volume for a consistently oriented closed mesh. Use `abs(total)` for component size comparison and mass computation.

### Units

Do **not** automatically convert STL coordinate units.

A repeated failure pattern in this task family is to assume STL coordinates are in `mmÂ³` and divide by 1000 to get `cmÂ³`. The successful result came from using the raw computed volume directly with the density table's numeric density value.

So:

- first compute volume directly from the mesh
- inspect task wording and validation behavior
- only convert units if there is explicit evidence

---

## Reference Implementation

The following script is a complete end-to-end solver. It:

- reads the STL
- parses the density table
- builds connected components
- selects the largest component by absolute volume
- chooses the dominant valid material ID
- computes mass
- writes the required JSON
- prints diagnostics for manual checking

Copy, paste, and adapt paths as needed.

```python
#!/usr/bin/env python3
import json
import os
import re
import struct
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

STL_PATH = "/root/scan_data.stl"
DENSITY_TABLE_PATH = "/root/material_density_table.md"
OUTPUT_PATH = "/root/mass_report.json"

Vertex = Tuple[float, float, float]
Triangle = Tuple[Vertex, Vertex, Vertex, int]


def parse_material_density_table(path: str) -> Dict[int, float]:
    """
    Parse a markdown table containing material IDs and densities.

    Expected to match lines such as:
    | 42 | Some Material | 5.55 |
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Density table not found: {path}")

    densities: Dict[int, float] = {}

    # Conservative markdown-table parser:
    # first captured number = material ID
    # later captured decimal/integer = density
    line_re = re.compile(r"\|\s*([0-9]+)\s*\|.*?\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|")

    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line.startswith("|"):
                continue

            # Skip separator rows like |---|---|---|
            reduced = line.replace("|", "").replace("-", "").replace(":", "").strip()
            if reduced == "":
                continue

            m = line_re.search(line)
            if not m:
                continue

            material_id = int(m.group(1))
            density = float(m.group(2))
            densities[material_id] = density

    if not densities:
        raise ValueError("No material densities could be parsed from markdown table")

    return densities


def load_binary_stl(path: str) -> List[Triangle]:
    """
    Load a binary STL, interpreting the 2-byte attribute field as a material ID.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"STL not found: {path}")

    file_size = os.path.getsize(path)
    if file_size < 84:
        raise ValueError(f"STL too small to be valid: {file_size} bytes")

    triangles: List[Triangle] = []

    with open(path, "rb") as f:
        header = f.read(80)
        if len(header) != 80:
            raise ValueError("Invalid STL: failed to read 80-byte header")

        tri_count_bytes = f.read(4)
        if len(tri_count_bytes) != 4:
            raise ValueError("Invalid STL: failed to read 4-byte triangle count")

        tri_count = struct.unpack("<I", tri_count_bytes)[0]
        expected_size = 84 + tri_count * 50
        if file_size != expected_size:
            raise ValueError(
                f"Binary STL size mismatch: header says {tri_count} triangles "
                f"(expected {expected_size} bytes) but file is {file_size} bytes"
            )

        for i in range(tri_count):
            rec = f.read(50)
            if len(rec) != 50:
                raise ValueError(f"Invalid STL: triangle {i} record length {len(rec)}, expected 50")

            vals = struct.unpack("<12fH", rec)
            # vals[0:3] = normal, ignored
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            attr = int(vals[12])
            triangles.append((v1, v2, v3, attr))

        trailing = f.read()
        if trailing != b"":
            raise ValueError(f"Unexpected trailing bytes after STL payload: {len(trailing)}")

    return triangles


def build_components(triangles: List[Triangle]) -> List[List[int]]:
    """
    Group triangles into connected components using exact shared vertices.
    """
    if not triangles:
        raise ValueError("No triangles loaded from STL")

    parent = list(range(len(triangles)))
    rank = [0] * len(triangles)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1

    vertex_to_faces: Dict[Vertex, List[int]] = defaultdict(list)
    for i, (v1, v2, v3, _attr) in enumerate(triangles):
        vertex_to_faces[v1].append(i)
        vertex_to_faces[v2].append(i)
        vertex_to_faces[v3].append(i)

    for face_ids in vertex_to_faces.values():
        if len(face_ids) <= 1:
            continue
        first = face_ids[0]
        for other in face_ids[1:]:
            union(first, other)

    components: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(triangles)):
        components[find(i)].append(i)

    return list(components.values())


def signed_volume_of_component(triangles: List[Triangle], face_ids: Iterable[int]) -> float:
    """
    Compute signed volume using the tetrahedron formula.
    """
    total = 0.0
    for idx in face_ids:
        v1, v2, v3, _attr = triangles[idx]
        total += (
            v1[0] * (v2[1] * v3[2] - v2[2] * v3[1])
            - v1[1] * (v2[0] * v3[2] - v2[2] * v3[0])
            + v1[2] * (v2[0] * v3[1] - v2[1] * v3[0])
        ) / 6.0
    return total


def choose_largest_component_by_volume(triangles: List[Triangle], components: List[List[int]]) -> List[int]:
    if not components:
        raise ValueError("No components found")
    return max(components, key=lambda ids: abs(signed_volume_of_component(triangles, ids)))


def choose_material_id_for_component(
    triangles: List[Triangle],
    face_ids: List[int],
    material_density: Dict[int, float],
) -> int:
    """
    Pick the most frequent attribute value in the component that is also present
    in the density table.
    """
    attrs = Counter(int(triangles[idx][3]) for idx in face_ids)
    valid_attrs = [aid for aid in attrs if aid in material_density]
    if not valid_attrs:
        raise ValueError(
            f"No valid material ID found in selected component. "
            f"Component attrs={dict(attrs)}, known IDs={sorted(material_density.keys())[:20]}..."
        )
    return max(valid_attrs, key=lambda aid: attrs[aid])


def write_mass_report(path: str, mass: float, material_id: int) -> None:
    report = {
        "main_part_mass": float(mass),
        "material_id": int(material_id),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def main() -> None:
    material_density = parse_material_density_table(DENSITY_TABLE_PATH)
    triangles = load_binary_stl(STL_PATH)
    components = build_components(triangles)

    largest = choose_largest_component_by_volume(triangles, components)
    signed_vol = signed_volume_of_component(triangles, largest)
    volume = abs(signed_vol)

    material_id = choose_material_id_for_component(triangles, largest, material_density)
    density = material_density[material_id]

    # Important:
    # Do not force a mm^3 -> cm^3 conversion unless the task explicitly proves it is needed.
    mass = volume * density

    write_mass_report(OUTPUT_PATH, mass, material_id)

    # Diagnostics
    component_summaries = []
    for comp in components:
        comp_vol = abs(signed_volume_of_component(triangles, comp))
        attrs = Counter(int(triangles[idx][3]) for idx in comp)
        component_summaries.append({
            "faces": len(comp),
            "abs_volume": comp_vol,
            "attrs": dict(attrs),
        })

    component_summaries.sort(key=lambda x: x["abs_volume"], reverse=True)

    print(json.dumps({
        "triangle_count": len(triangles),
        "component_count": len(components),
        "top_components": component_summaries[:5],
        "selected_component_faces": len(largest),
        "selected_volume": volume,
        "selected_material_id": material_id,
        "selected_density": density,
        "mass": mass,
        "output_path": OUTPUT_PATH,
    }, indent=2))

    # Output schema validation
    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert set(data.keys()) == {"main_part_mass", "material_id"}
    assert isinstance(data["material_id"], int)
    assert isinstance(data["main_part_mass"], (int, float))
    assert data["main_part_mass"] > 0


if __name__ == "__main__":
    main()
```

Run it with:

```bash
#!/usr/bin/env bash
set -euo pipefail
python3 /tmp/solve_mass_from_stl.py
cat /root/mass_report.json
```

---

## Fast Triage Checklist

Before spending time debugging, check these in order:

1. **Is the STL really binary?**
   - Use `file`.
   - Binary STL should have a fixed-size structure and a meaningful triangle count.

2. **Did you parse all 50 bytes per triangle?**
   - If your per-record length is anything else, your geometry and material IDs will be corrupted.

3. **Did you use the attribute bytes as Material ID?**
   - Ignoring them guarantees wrong material selection.

4. **Did you split the mesh into connected components?**
   - Scanning debris is common.

5. **Did you choose the main component by absolute volume, not triangle count?**
   - This was a repeated source of wrong answers.

6. **Did you parse the density table instead of guessing?**
   - Material IDs must be matched exactly.

7. **Did you avoid unjustified unit conversion?**
   - A factor-of-1000 error is common.

8. **Did you output the exact JSON schema?**
   - Extra keys or wrong types can fail even with correct math.

---

## Common Pitfalls

These are distilled from repeated failed attempts and corrected behavior.

### 1. Choosing the âlargestâ component by triangle count
This is a common trap. A debris cluster can have many triangles while enclosing less volume than the real part.

**Correct approach:**  
Choose the connected component with the largest **absolute signed volume**.

---

### 2. Assuming the component's material is just the global or naive majority
If you count attributes over the entire STL, debris can contaminate the result.

**Correct approach:**  
First select the main component, then choose the most frequent **valid** material ID within that component.

---

### 3. Blindly converting `mmÂ³` to `cmÂ³`
This caused incorrect numeric results in this task family.

**Correct approach:**  
Do not convert units unless the task explicitly requires it or the validation evidence strongly supports it. Compute the raw mesh volume first and verify scale.

---

### 4. Parsing the STL record incorrectly
Examples of bad parsing include:

- reading the wrong record size
- not validating triangle count against file size
- ignoring trailing bytes
- reading attribute bytes with the wrong endianness

**Correct approach:**  
Use `struct.unpack("<12fH", rec)` on each 50-byte triangle record.

---

### 5. Ignoring connected components entirely
Summing volume over all triangles can include debris and give an inflated mass.

**Correct approach:**  
Always inspect or compute components when the prompt mentions scan artifacts, debris, or the âmain partâ.

---

### 6. Parsing the density table too loosely or too rigidly
If your parser assumes a fixed column title or exact spacing, it may miss valid rows.

**Correct approach:**  
Use a robust markdown-row regex and validate that the selected Material ID exists in the parsed mapping.

---

### 7. Using bounding box size as a proxy for volume
Bounding boxes are useful for diagnostics but not for mass.

**Correct approach:**  
Use the tetrahedron signed-volume formula on the mesh itself.

---

### 8. Writing the wrong JSON shape
Even a correct mass can fail validation if the file contains wrong keys or stringified numbers.

**Correct approach:**  
Write exactly:
- `main_part_mass` as numeric
- `material_id` as integer

---

## Debugging Aids

If the result seems wrong, print a compact component summary:

```python
#!/usr/bin/env python3
import json
from collections import Counter

def debug_components(triangles, components):
    rows = []
    for comp in components:
        attrs = Counter(triangles[idx][3] for idx in comp)
        rows.append({
            "faces": len(comp),
            "abs_volume": abs(signed_volume_of_component(triangles, comp)),
            "attrs": dict(attrs),
        })
    rows.sort(key=lambda r: r["abs_volume"], reverse=True)
    print(json.dumps(rows[:10], indent=2))
```

Useful questions:

- Does the biggest-by-volume component also have a coherent single material?
- Is the selected material present in the density table?
- Is the magnitude of mass plausible given the density?
- If your answer differs by exactly 1000Ã, did you perform an unnecessary unit conversion?

---

## Minimal Validation Script

Use this after writing the output:

```bash
#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import json

path = "/root/mass_report.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

assert set(data.keys()) == {"main_part_mass", "material_id"}, data
assert isinstance(data["material_id"], int), type(data["material_id"])
assert isinstance(data["main_part_mass"], (int, float)), type(data["main_part_mass"])
assert data["main_part_mass"] > 0, data["main_part_mass"]
print("mass_report.json schema looks valid")
print(json.dumps(data, indent=2))
PY
```

---

## Recommended Execution Path

If time is limited, do this:

1. inspect STL and density table
2. parse binary STL with `"<12fH"`
3. build exact-vertex connected components
4. compute absolute volume for each component
5. select largest-by-volume component
6. select most frequent valid material ID in that component
7. multiply volume by density
8. write exact JSON
9. validate schema

That path is the highest-yield route for this class of tasks.