---
name: threejs-to-obj-export
description: Export a Three.js-defined object or scene from a JavaScript module into a Blender-compatible OBJ file while preserving authored transforms and converting from Three.js Y-up to Blender Z-up via a -90 degree X rotation.
tools:
  - node
  - npm
  - bash
  - python3
  - rg
  - sed
  - cat
  - find
tags:
  - threejs
  - obj
  - blender
  - nodejs
  - geometry
  - asset-conversion
---

# threejs-to-obj-export

Use this skill when you need to convert a Three.js-authored object into a simulation-ready `.obj` file that Blender can import correctly.

This pattern is appropriate when:

- The source is a JavaScript or ES module that builds geometry using `three`.
- The source exports a scene, object, or factory like `createScene()`.
- You must preserve the original authored geometry and transforms.
- The output must be Blender-compatible in **Z-up** space.
- The exporter should work in a Node.js environment rather than a browser.

The most reliable workflow is:

1. Inspect the source module and determine what it exports.
2. Confirm `three` and `OBJExporter` are available.
3. Create a Node ESM export script that:
   - imports the source module,
   - builds a root object,
   - updates world matrices,
   - optionally converts unsupported constructs like `InstancedMesh`,
   - wraps the result in a `-90Â°` X-rotation group for Blender Z-up,
   - exports using `OBJExporter`.
4. Verify the generated OBJ structurally and semantically before finishing.

---

## Domain Conventions and Output Rules

### Coordinate systems

- **Three.js default authored space** is usually **Y-up**.
- **Blender import target** is **Z-up**.
- To convert Y-up content into Blender Z-up for exported geometry, apply a **`-90Â°` rotation around X** before export.

This means:

- Three.js `+Y` becomes Blender `+Z`.
- Three.js `+Z` becomes Blender `-Y`.

In code, use:

```js
group.rotation.x = -Math.PI / 2;
```

### OBJ format expectations

A valid OBJ produced for downstream import should typically contain:

- object/group records: `o ...` or `g ...`
- vertices: `v x y z`
- normals if available: `vn x y z`
- UVs if available: `vt u v`
- faces: `f ...`

Minimum correctness checks:

- File exists and is non-empty.
- File contains at least one `v ` line and one `f ` line.
- Exported object names are present when useful for debugging.
- Geometry count is plausible relative to source content.

### Transform preservation rules

To preserve original authored positions:

- Do **not** manually zero out or flatten transforms.
- Do **not** reconstruct meshes from raw geometry unless necessary.
- Always call:
  - `scene.updateMatrixWorld(true)`
  - `wrapper.updateMatrixWorld(true)`

before export.

### Node.js module rules

Many Three.js exporter modules are ESM-only. Prefer a `.mjs` exporter script and dynamic or native `import` syntax.

---

# High-Level Workflow

## 1. Inspect the source module before writing conversion code

**What to do:** Open the input JavaScript file and determine:
- whether it exports a scene, mesh, group, or factory function,
- whether it references browser globals,
- whether it uses `InstancedMesh`, custom classes, or loaders.

**Why:** The correct import and normalization strategy depends on the module shape. You should not assume the source exports a `Scene`, or that `OBJExporter` supports every object type directly.

### Example inspection commands

```bash
set -euo pipefail

SOURCE_JS="${SOURCE_JS:-/path/to/object.js}"

if [ ! -f "$SOURCE_JS" ]; then
  echo "Source file not found: $SOURCE_JS" >&2
  exit 1
fi

echo "=== First 240 lines of source ==="
sed -n '1,240p' "$SOURCE_JS"

echo
echo "=== Likely exports / factory functions ==="
rg -n "export default|export function|export const|module\.exports|createScene|new THREE\.(Scene|Group|Mesh|InstancedMesh)" "$SOURCE_JS" || true
```

### Decision criteria

Use these heuristics:

- If the module exports a function like `createScene()`, call it.
- If it exports a `Scene`, `Group`, or `Object3D`, use it directly.
- If it uses `InstancedMesh`, prepare to expand instances to plain meshes before export.
- If it requires DOM APIs, you may need lightweight stubs or alternate evaluation, but only add those if actually needed.

---

## 2. Confirm the runtime and exporter dependencies first

**What to do:** Verify Node.js is available and that `three` plus `OBJExporter` can be imported.

**Why:** Many failures come from writing a script against unavailable packages or mixing CommonJS with ESM.

### Dependency verification

```bash
set -euo pipefail

node -v
npm -v

node -e "try{require('three');console.log('three-present')}catch(e){console.error('three-missing');process.exit(1)}"

node -e "import('three/examples/jsm/exporters/OBJExporter.js').then(()=>console.log('objexporter-ok')).catch((e)=>{console.error(e && e.message ? e.message : e);process.exit(1)})"
```

### If `three` is missing

Install it only if needed and permitted by the environment:

```bash
set -euo pipefail

if ! node -e "require('three')" >/dev/null 2>&1; then
  npm install three
fi
```

### Important note

Do **not** assume `require('three/examples/jsm/exporters/OBJExporter.js')` will work in CommonJS. Prefer ESM:

- exporter script filename: `export_obj.mjs`
- imports via `import ... from ...`

---

## 3. Build a robust exporter script skeleton

**What to do:** Create a standalone Node ESM script that imports the source module, resolves the exported object, wraps it for axis conversion, and writes the OBJ file.

**Why:** A disciplined script avoids ad-hoc one-liners and makes it easy to add object normalization, validation, and error reporting.

### Minimal robust exporter skeleton

```js
// export_obj.mjs
import fs from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { OBJExporter } from 'three/examples/jsm/exporters/OBJExporter.js';

const SOURCE_MODULE = process.env.SOURCE_MODULE || '/path/to/object.js';
const OUTPUT_OBJ = process.env.OUTPUT_OBJ || '/path/to/output/object.obj';

async function loadSourceModule(modulePath) {
  const resolved = path.resolve(modulePath);
  try {
    return await import(`file://${resolved}`);
  } catch (err) {
    throw new Error(`Failed to import source module "${resolved}": ${err.message}`);
  }
}

function isObject3D(value) {
  return !!value && typeof value === 'object' && value.isObject3D === true;
}

function isSceneLike(value) {
  return !!value && typeof value === 'object' && (
    value.isScene === true ||
    value.isGroup === true ||
    value.isMesh === true ||
    value.isObject3D === true
  );
}

async function resolveRootObject(mod) {
  const candidates = [
    mod?.createScene,
    mod?.createObject,
    mod?.buildScene,
    mod?.buildObject,
    mod?.default,
    mod?.scene,
    mod?.object,
  ];

  for (const candidate of candidates) {
    if (typeof candidate === 'function') {
      const result = await candidate();
      if (isSceneLike(result)) return result;
    } else if (isSceneLike(candidate)) {
      return candidate;
    }
  }

  if (isSceneLike(mod)) {
    return mod;
  }

  throw new Error(
    'Could not resolve a Three.js Object3D/Scene from source module. ' +
    'Expected an exported scene/object or a factory such as createScene().'
  );
}

function makeWrapperForBlender(root) {
  const wrapper = new THREE.Group();
  wrapper.name = 'blender_z_up_wrapper';
  wrapper.rotation.x = -Math.PI / 2;
  wrapper.add(root);
  return wrapper;
}

async function ensureOutputDir(filePath) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
}

async function main() {
  const mod = await loadSourceModule(SOURCE_MODULE);
  const root = await resolveRootObject(mod);

  if (!isObject3D(root)) {
    throw new Error('Resolved root is not a Three.js Object3D.');
  }

  root.updateMatrixWorld(true);

  const wrapper = makeWrapperForBlender(root);
  wrapper.updateMatrixWorld(true);

  const exporter = new OBJExporter();
  const objText = exporter.parse(wrapper);

  if (typeof objText !== 'string' || objText.trim().length === 0) {
    throw new Error('OBJ exporter returned empty output.');
  }

  await ensureOutputDir(OUTPUT_OBJ);
  await fs.writeFile(OUTPUT_OBJ, objText, 'utf8');

  console.log(`Wrote OBJ: ${OUTPUT_OBJ}`);
}

main().catch((err) => {
  console.error(err?.stack || err?.message || String(err));
  process.exit(1);
});
```

Run it with:

```bash
set -euo pipefail

SOURCE_MODULE="/path/to/object.js" \
OUTPUT_OBJ="/path/to/output/object.obj" \
node /path/to/export_obj.mjs
```

---

## 4. Normalize the source object for OBJ export

**What to do:** Convert unsupported or poorly supported object types into exportable meshes while preserving transforms.

**Why:** `OBJExporter` works best with standard `Mesh`, `Line`, and related primitives. Some Three.js constructs, especially `InstancedMesh`, may not export as expected unless expanded.

### Most important case: `InstancedMesh`

If the source uses `THREE.InstancedMesh`, expand each instance into a plain `Mesh` with the per-instance transform baked into the clone's matrix.

### Executable conversion helper

```js
import * as THREE from 'three';

function cloneMaterial(material) {
  if (Array.isArray(material)) {
    return material.map((m) => (m && typeof m.clone === 'function' ? m.clone() : m));
  }
  return material && typeof material.clone === 'function' ? material.clone() : material;
}

function expandInstancedMesh(instancedMesh) {
  if (!instancedMesh || instancedMesh.isInstancedMesh !== true) {
    throw new Error('expandInstancedMesh expects a THREE.InstancedMesh');
  }

  const parentGroup = new THREE.Group();
  parentGroup.name = instancedMesh.name || 'instanced_mesh_group';

  const tempMatrix = new THREE.Matrix4();

  for (let i = 0; i < instancedMesh.count; i += 1) {
    instancedMesh.getMatrixAt(i, tempMatrix);

    const mesh = new THREE.Mesh(
      instancedMesh.geometry,
      cloneMaterial(instancedMesh.material)
    );

    mesh.name = `${instancedMesh.name || 'instance'}_${i}`;
    mesh.matrixAutoUpdate = false;
    mesh.matrix.copy(tempMatrix);

    parentGroup.add(mesh);
  }

  parentGroup.position.copy(instancedMesh.position);
  parentGroup.quaternion.copy(instancedMesh.quaternion);
  parentGroup.scale.copy(instancedMesh.scale);
  parentGroup.visible = instancedMesh.visible;

  return parentGroup;
}

function normalizeForObjExport(root) {
  const replacementMap = [];

  root.traverse((node) => {
    if (node.isInstancedMesh === true) {
      const replacement = expandInstancedMesh(node);
      replacementMap.push({ original: node, replacement });
    }
  });

  for (const { original, replacement } of replacementMap) {
    const parent = original.parent;
    if (!parent) continue;

    parent.add(replacement);
    parent.remove(original);
  }

  root.updateMatrixWorld(true);
  return root;
}

export { normalizeForObjExport };
```

### Integrating normalization into the exporter

```js
// inside export_obj.mjs
import { normalizeForObjExport } from './normalize_for_obj.mjs';

// ...
const root = await resolveRootObject(mod);
normalizeForObjExport(root);
root.updateMatrixWorld(true);
// ...
```

### Why this works

- Instance transforms are preserved.
- Geometry remains unchanged.
- The exported OBJ contains actual mesh objects, which downstream tools understand more reliably.

---

## 5. Preserve transforms correctly

**What to do:** Keep the source hierarchy intact and update matrices before export.

**Why:** The authored positions are often encoded in nested group transforms, not just vertex positions. Destroying the hierarchy or exporting before matrix updates can shift parts unexpectedly.

### Safe transform handling pattern

```js
import * as THREE from 'three';

function prepareRootForExport(root) {
  if (!root || root.isObject3D !== true) {
    throw new Error('prepareRootForExport requires a THREE.Object3D');
  }

  root.updateWorldMatrix(true, true);
  root.updateMatrixWorld(true);

  const wrapper = new THREE.Group();
  wrapper.name = 'export_root_z_up';
  wrapper.rotation.x = -Math.PI / 2;
  wrapper.add(root);

  wrapper.updateWorldMatrix(true, true);
  wrapper.updateMatrixWorld(true);

  return wrapper;
}

export { prepareRootForExport };
```

### Do not do this

Avoid manually baking every child transform into geometry unless you have a specific reason. That can:

- alter hierarchy semantics,
- complicate debugging,
- introduce matrix math mistakes,
- accidentally double-apply transforms.

---

## 6. Export using `OBJExporter`

**What to do:** Instantiate `OBJExporter`, pass the prepared object, and write the returned string to disk.

**Why:** `OBJExporter` is the canonical built-in path for this conversion class in a Node + Three.js environment.

### Export utility with validation

```js
import fs from 'node:fs/promises';
import path from 'node:path';
import { OBJExporter } from 'three/examples/jsm/exporters/OBJExporter.js';

async function exportObjToFile(rootObject, outputPath) {
  if (!rootObject || rootObject.isObject3D !== true) {
    throw new Error('exportObjToFile expected a THREE.Object3D');
  }

  const exporter = new OBJExporter();
  const objText = exporter.parse(rootObject);

  if (typeof objText !== 'string') {
    throw new Error('OBJExporter.parse did not return a string');
  }

  const trimmed = objText.trim();
  if (!trimmed) {
    throw new Error('OBJ output is empty');
  }

  const hasVertices = /\nv\s+[-+0-9.eE]+\s+[-+0-9.eE]+\s+[-+0-9.eE]+/.test(`\n${trimmed}`);
  const hasFaces = /\nf\s+/.test(`\n${trimmed}`);

  if (!hasVertices || !hasFaces) {
    throw new Error('OBJ output appears invalid: missing vertices or faces');
  }

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, `${trimmed}\n`, 'utf8');
}

export { exportObjToFile };
```

---

## 7. Verify the OBJ before finalizing

**What to do:** Check file existence, format, and rough content sanity.

**Why:** A successful script execution is not enough. You want to catch empty files, missing faces, broken object naming, or obvious transform issues early.

### Basic shell verification

```bash
set -euo pipefail

OUTPUT_OBJ="${OUTPUT_OBJ:-/path/to/output/object.obj}"

test -f "$OUTPUT_OBJ"
test -s "$OUTPUT_OBJ"

echo "=== Line count ==="
wc -l "$OUTPUT_OBJ"

echo "=== First 40 lines ==="
sed -n '1,40p' "$OUTPUT_OBJ"

echo "=== First object/group names ==="
rg -n "^o |^g " "$OUTPUT_OBJ" | head -n 20 || true

echo "=== Vertex count ==="
grep -c '^v ' "$OUTPUT_OBJ" || true

echo "=== Face count ==="
grep -c '^f ' "$OUTPUT_OBJ" || true
```

### Stronger Python validation

```python
#!/usr/bin/env python3
import sys
from pathlib import Path

def validate_obj(path_str: str) -> int:
    path = Path(path_str)
    if not path.exists():
        print(f"ERROR: file does not exist: {path}", file=sys.stderr)
        return 1
    if path.stat().st_size == 0:
        print(f"ERROR: file is empty: {path}", file=sys.stderr)
        return 1

    vertex_count = 0
    face_count = 0
    object_count = 0

    with path.open("r", encoding="utf-8", errors="replace") as f:
      for line in f:
        if line.startswith("v "):
          vertex_count += 1
        elif line.startswith("f "):
          face_count += 1
        elif line.startswith("o ") or line.startswith("g "):
          object_count += 1

    if vertex_count == 0:
        print("ERROR: no vertices found", file=sys.stderr)
        return 1
    if face_count == 0:
        print("ERROR: no faces found", file=sys.stderr)
        return 1

    print({
        "path": str(path),
        "vertices": vertex_count,
        "faces": face_count,
        "objects_or_groups": object_count,
    })
    return 0

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: validate_obj.py <path-to-obj>", file=sys.stderr)
        sys.exit(2)
    sys.exit(validate_obj(sys.argv[1]))
```

Run it:

```bash
python3 validate_obj.py /path/to/output/object.obj
```

---

## 8. Use a full reference implementation when speed matters

When the task matches the common pattern closely, use this complete script and adapt only the module resolution logic if needed.

### Full reference exporter

```js
// export_obj.mjs
import fs from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { OBJExporter } from 'three/examples/jsm/exporters/OBJExporter.js';

const SOURCE_MODULE = process.env.SOURCE_MODULE || '/path/to/object.js';
const OUTPUT_OBJ = process.env.OUTPUT_OBJ || '/path/to/output/object.obj';

function isObject3D(value) {
  return !!value && typeof value === 'object' && value.isObject3D === true;
}

function isSceneLike(value) {
  return !!value && typeof value === 'object' && (
    value.isScene === true ||
    value.isGroup === true ||
    value.isMesh === true ||
    value.isObject3D === true
  );
}

async function importModule(modulePath) {
  const resolved = path.resolve(modulePath);
  return import(`file://${resolved}`);
}

async function resolveSceneOrObject(mod) {
  const candidateKeys = [
    'createScene',
    'createObject',
    'buildScene',
    'buildObject',
    'default',
    'scene',
    'object',
  ];

  for (const key of candidateKeys) {
    const value = mod?.[key];

    if (typeof value === 'function') {
      const result = await value();
      if (isSceneLike(result)) return result;
    } else if (isSceneLike(value)) {
      return value;
    }
  }

  if (isSceneLike(mod)) return mod;

  throw new Error(
    'Unable to resolve scene/object from module. ' +
    'Expected an exported Object3D or a factory function.'
  );
}

function cloneMaterial(material) {
  if (Array.isArray(material)) {
    return material.map((m) => (m && typeof m.clone === 'function' ? m.clone() : m));
  }
  return material && typeof material.clone === 'function' ? material.clone() : material;
}

function expandInstancedMesh(instancedMesh) {
  const group = new THREE.Group();
  group.name = instancedMesh.name || 'instanced_mesh_group';

  const matrix = new THREE.Matrix4();

  for (let i = 0; i < instancedMesh.count; i += 1) {
    instancedMesh.getMatrixAt(i, matrix);

    const mesh = new THREE.Mesh(instancedMesh.geometry, cloneMaterial(instancedMesh.material));
    mesh.name = `${instancedMesh.name || 'instance'}_${i}`;
    mesh.matrixAutoUpdate = false;
    mesh.matrix.copy(matrix);
    group.add(mesh);
  }

  group.position.copy(instancedMesh.position);
  group.quaternion.copy(instancedMesh.quaternion);
  group.scale.copy(instancedMesh.scale);
  group.visible = instancedMesh.visible;

  return group;
}

function normalizeUnsupportedNodes(root) {
  const replacements = [];

  root.traverse((node) => {
    if (node.isInstancedMesh === true) {
      replacements.push({
        original: node,
        replacement: expandInstancedMesh(node),
      });
    }
  });

  for (const { original, replacement } of replacements) {
    const parent = original.parent;
    if (!parent) continue;
    parent.add(replacement);
    parent.remove(original);
  }

  root.updateMatrixWorld(true);
}

function prepareForBlender(root) {
  const wrapper = new THREE.Group();
  wrapper.name = 'blender_z_up_wrapper';
  wrapper.rotation.x = -Math.PI / 2;
  wrapper.add(root);
  wrapper.updateMatrixWorld(true);
  return wrapper;
}

async function writeObj(root, outputPath) {
  const exporter = new OBJExporter();
  const obj = exporter.parse(root);

  if (typeof obj !== 'string' || obj.trim().length === 0) {
    throw new Error('OBJ export produced empty content');
  }

  if (!/\nv\s+/.test(`\n${obj}`) || !/\nf\s+/.test(`\n${obj}`)) {
    throw new Error('OBJ content missing vertices or faces');
  }

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, obj.endsWith('\n') ? obj : `${obj}\n`, 'utf8');
}

async function main() {
  const mod = await importModule(SOURCE_MODULE);
  const root = await resolveSceneOrObject(mod);

  if (!isObject3D(root)) {
    throw new Error('Resolved root is not a THREE.Object3D');
  }

  normalizeUnsupportedNodes(root);
  root.updateWorldMatrix(true, true);
  root.updateMatrixWorld(true);

  const exportRoot = prepareForBlender(root);
  exportRoot.updateWorldMatrix(true, true);
  exportRoot.updateMatrixWorld(true);

  await writeObj(exportRoot, OUTPUT_OBJ);
  console.log(`OBJ written to ${OUTPUT_OBJ}`);
}

main().catch((err) => {
  console.error(err?.stack || err?.message || String(err));
  process.exit(1);
});
```

Run:

```bash
set -euo pipefail

SOURCE_MODULE="/path/to/object.js" \
OUTPUT_OBJ="/path/to/output/object.obj" \
node /path/to/export_obj.mjs
```

---

# Common Pitfalls

## 1. Forgetting the Blender axis conversion

**Symptom:** The model imports rotated incorrectly in Blender.

**Fix:** Apply exactly a `-90Â°` X rotation on a wrapper group before export:

```js
wrapper.rotation.x = -Math.PI / 2;
```

Do not rotate the geometry arbitrarily around another axis.

---

## 2. Using CommonJS for ESM-only Three.js example modules

**Symptom:** Import errors like âUnexpected token exportâ or inability to `require()` the exporter.

**Fix:** Use a `.mjs` script and native ESM imports:

```js
import { OBJExporter } from 'three/examples/jsm/exporters/OBJExporter.js';
```

---

## 3. Exporting before matrices are updated

**Symptom:** Parts appear in wrong places, often near the origin, or nested transforms are lost.

**Fix:** Always call:

```js
root.updateWorldMatrix(true, true);
root.updateMatrixWorld(true);
wrapper.updateWorldMatrix(true, true);
wrapper.updateMatrixWorld(true);
```

before `exporter.parse(...)`.

---

## 4. Assuming the source always exports `createScene()`

**Symptom:** Script runs but fails to resolve the object, or crashes on calling undefined.

**Fix:** Probe several export patterns:
- `createScene`
- `createObject`
- `default`
- direct `scene` / `object`

Use a resolver instead of hard-coding one symbol.

---

## 5. Ignoring `InstancedMesh`

**Symptom:** Expected repeated parts are missing from the OBJ, or count is too low.

**Fix:** Expand `InstancedMesh` into ordinary `Mesh` nodes before export. Preserve each instance matrix.

---

## 6. Flattening transforms unnecessarily

**Symptom:** Geometry drifts, scales incorrectly, or becomes harder to debug.

**Fix:** Preserve the original hierarchy unless there is a concrete exporter limitation. Wrapping the root is safer than rebaking everything.

---

## 7. Finalizing without reading the OBJ header and counts

**Symptom:** Empty or malformed OBJ slips through.

**Fix:** Check:
- file exists,
- non-zero size,
- contains `v ` lines,
- contains `f ` lines,
- object/group names look reasonable.

Use `wc`, `sed`, `rg`, or a small Python validator.

---

# Quick Triage Checklist

Before coding:

```bash
node -v
npm -v
node -e "try{require('three');console.log('three-present')}catch(e){console.log('three-missing')}"
node -e "import('three/examples/jsm/exporters/OBJExporter.js').then(()=>console.log('objexporter-ok')).catch(e=>{console.error(e.message);process.exit(1)})"
sed -n '1,240p' /path/to/object.js
```

Before finalizing:

```bash
test -f /path/to/output/object.obj
test -s /path/to/output/object.obj
wc -l /path/to/output/object.obj
sed -n '1,40p' /path/to/output/object.obj
rg -n "^o |^g " /path/to/output/object.obj | head
grep -c '^v ' /path/to/output/object.obj
grep -c '^f ' /path/to/output/object.obj
```

---

# Recommended Execution Strategy

If the source is a normal Three.js module and `three` is already installed:

1. Inspect exports and object types.
2. Write an ESM exporter script.
3. Resolve the root object robustly.
4. Expand `InstancedMesh` if present.
5. Wrap the root in a group rotated `-Math.PI / 2` around X.
6. Export with `OBJExporter`.
7. Validate the OBJ structurally.

This path is fast, reliable, and preserves authored positions without unnecessary geometry manipulation.