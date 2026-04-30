---
id: 3d-scan-calc
title: 3D Scan Mass Calculation from Binary STL with Material IDs
version: 1.0.0
tags: [stl, 3d-printing, geometry, mass-calculation, connected-components]
---

## Overview

Parse a binary STL file where the 2-byte "Attribute Byte Count" field encodes a **Material ID** per triangle. Identify the largest connected component (the main part), look up its density, compute volume via the signed divergence theorem, and write a mass report.

---

## Module 1: File Inspection and Setup

Before writing any calculation code, inspect both input files to understand the data.

```bash
ls -lh /root/scan_data.stl /root/material_density_table.md
cat /root/material_density_table.md
```

Key things to confirm:
- The STL file exists and is binary (not ASCII)
- The density table maps integer Material IDs to density values (typically in g/cm³)
- Note the units — volume will need to match density units (usually mm³ → cm³ conversion)

---

## Module 2: Parse STL, Find Largest Component, Compute Mass

Do all heavy lifting in a single Python script.

### Binary STL Structure

Each triangle record is 50 bytes:
- 12 bytes: normal vector (3 × float32)
- 36 bytes: 3 vertices (9 × float32)
- 2 bytes: attribute byte count → **Material ID**

Header is 80 bytes, followed by a 4-byte uint32 triangle count.

```python
import struct, json
from collections import defaultdict

def parse_stl(path):
    with open(path, 'rb') as f:
        f.read(80)  # skip header
        num_triangles = struct.unpack('<I', f.read(4))[0]
        triangles = []
        for _ in range(num_triangles):
            data = f.read(50)
            normal = struct.unpack_from('<3f', data, 0)
            v1 = struct.unpack_from('<3f', data, 12)
            v2 = struct.unpack_from('<3f', data, 24)
            v3 = struct.unpack_from('<3f', data, 36)
            material_id = struct.unpack_from('<H', data, 48)[0]
            triangles.append((v1, v2, v3, material_id))
    return triangles
```

### Connected Component Detection

Two triangles are connected if they share an edge (two vertices). Use Union-Find for efficiency.

```python
def find_connected_components(triangles):
    parent = list(range(len(triangles)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    # Build edge → triangle index map
    edge_map = defaultdict(list)
    for i, (v1, v2, v3, _) in enumerate(triangles):
        for edge in [
            tuple(sorted([v1, v2])),
            tuple(sorted([v2, v3])),
            tuple(sorted([v1, v3])),
        ]:
            edge_map[edge].append(i)

    for tris in edge_map.values():
        for j in range(1, len(tris)):
            union(tris[0], tris[j])

    components = defaultdict(list)
    for i in range(len(triangles)):
        components[find(i)].append(i)

    return components
```

### Volume via Signed Divergence Theorem

This gives the signed volume of a closed mesh. Take the absolute value.

```python
def triangle_signed_volume(v1, v2, v3):
    # (v1 · (v2 × v3)) / 6
    x1, y1, z1 = v1
    x2, y2, z2 = v2
    x3, y3, z3 = v3
    return (x1*(y2*z3 - y3*z2) - y1*(x2*z3 - x3*z2) + z1*(x2*y3 - x3*y2)) / 6.0

def compute_volume(triangles, indices):
    vol = 0.0
    for i in indices:
        v1, v2, v3, _ = triangles[i]
        vol += triangle_signed_volume(v1, v2, v3)
    return abs(vol)
```

### Full Pipeline

```python
triangles = parse_stl('/root/scan_data.stl')
components = find_connected_components(triangles)

# Largest component = main part
largest_key = max(components, key=lambda k: len(components[k]))
main_indices = components[largest_key]

# Material ID: use the most common one in the component
from collections import Counter
mat_ids = [triangles[i][3] for i in main_indices]
material_id = Counter(mat_ids).most_common(1)[0][0]

# Volume in mm³, convert to cm³ if density is in g/cm³
volume_mm3 = compute_volume(triangles, main_indices)
volume_cm3 = volume_mm3 / 1000.0  # 1 cm³ = 1000 mm³

# Look up density from parsed table (example: density = 5.55)
density = DENSITY_TABLE[material_id]  # g/cm³

mass = volume_cm3 * density

result = {"main_part_mass": round(mass, 2), "material_id": material_id}
with open('/root/mass_report.json', 'w') as f:
    json.dump(result, f, indent=2)

print(result)
```

---

## Module 3: Density Table Parsing

The density table is a Markdown file. Parse it programmatically rather than hardcoding values.

```python
import re

def parse_density_table(path):
    density_map = {}
    with open(path) as f:
        for line in f:
            # Match table rows like: | 42 | Unobtanium | 5.55 |
            m = re.search(r'\|\s*(\d+)\s*\|[^|]+\|\s*([\d.]+)\s*\|', line)
            if m:
                mat_id = int(m.group(1))
                density = float(m.group(2))
                density_map[mat_id] = density
    return density_map
```

Always parse dynamically — never hardcode density values from a specific run.

---

## Common Pitfalls

- **Wrong unit conversion**: STL coordinates are typically in mm. Density tables use g/cm³. Always divide volume by 1000 when converting mm³ → cm³. Skipping this produces a mass 1000× too large.

- **Not taking absolute value of volume**: The signed divergence theorem returns negative volume for meshes with inward-facing normals. Always use `abs()`.

- **Assuming a single material ID per file**: Debris fragments often carry different material IDs (e.g., 0 or 1). The main part's material ID should come from the largest component, not the whole file.

- **Hardcoding density or material ID**: Always parse the density table dynamically. The material ID and density will vary across task instances.

- **Treating attribute bytes as padding**: The 2-byte attribute field at offset 48 of each triangle record is the Material ID. Do not skip or zero it out — it is the primary signal for material identification.

- **Using vertex equality with floats**: When building the edge map for connected components, use the raw float tuples as keys (they come from the same binary source, so exact equality holds). Avoid rounding or epsilon comparisons unless you see disconnected meshes that should be connected.

- **Rounding the final mass too aggressively**: The verifier allows 0.1% tolerance. Use `round(mass, 2)` to preserve precision without over-truncating.
