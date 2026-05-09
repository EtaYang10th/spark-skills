---
title: "Three.js to OBJ Export for Blender (Headless Node.js)"
category: threejs-to-obj
tags:
  - threejs
  - obj-export
  - blender
  - coordinate-conversion
  - headless-nodejs
  - 3d-assets
version: 1
---

# Three.js to OBJ Export for Blender (Headless Node.js)

Convert a Three.js scene defined in a `.js` file into a Blender-compatible `.obj` file, running entirely in headless Node.js (no browser, no GPU).

## High-Level Workflow

1. **Inspect the environment** — confirm Node.js version, `three` package version, and that `OBJExporter` is available under `three/examples/jsm/exporters/`.
2. **Read and understand the source Three.js file** — identify every geometry type used (`BufferGeometry`, `ExtrudeGeometry`, `LatheGeometry`, `InstancedMesh`, `Group`, etc.). Pay special attention to `InstancedMesh` because the standard `OBJExporter` silently skips it.
3. **Plan InstancedMesh expansion** — for every `InstancedMesh`, extract each instance's matrix and create a standalone `Mesh` with a cloned geometry that has the instance transform baked in.
4. **Apply coordinate-space rotation** — Three.js is Y-up; Blender is Z-up. Wrap the entire scene root in a parent `Group` rotated −90° around X before exporting.
5. **Force world-matrix update** — in headless mode there is no render loop, so `updateMatrixWorld(true)` must be called on the root before export. Without this, child transforms are identity and the OBJ will be wrong.
6. **Export with OBJExporter** — call `exporter.parse(root)` and write the resulting string to the output path.
7. **Validate the output** — check vertex count, face count, object names, and spot-check coordinate ranges to confirm the rotation was applied.

## Step 1 — Environment Inspection

```bash
node --version                # expect v18+ (v20 or v22 typical)
npm list three 2>/dev/null    # confirm three is installed
ls node_modules/three/examples/jsm/exporters/OBJExporter.js  # must exist
```

If `three` is not installed:

```bash
npm install three@latest
```

## Step 2 — Analyze the Source File

Read `/root/data/object.js` carefully. Look for:

| Pattern | Why it matters |
|---|---|
| `new THREE.InstancedMesh(geo, mat, count)` | OBJExporter ignores these — must expand |
| `mesh.setMatrixAt(i, matrix)` | Tells you how many instances and their transforms |
| `new THREE.Group()` | Nested groups carry transforms that must propagate |
| `scene.add(...)` or returned root object | Identifies the export root |
| Named meshes (`mesh.name = "..."`) | Names become `o` lines in OBJ — useful for validation |

The source file typically exports a function (e.g., `createObject()` or `buildScene()`) that returns a `THREE.Group` or `THREE.Scene`. You need to call that function to get the live scene graph.

## Step 3 — InstancedMesh Expansion

`OBJExporter.parse()` iterates children looking for `mesh.isMesh` but does NOT handle `mesh.isInstancedMesh`. If you skip this step, instanced objects silently vanish from the OBJ.

```javascript
import * as THREE from 'three';

/**
 * Recursively find all InstancedMesh nodes and replace each with
 * N individual Mesh objects carrying baked-in instance transforms.
 */
function expandInstancedMeshes(root) {
  const toReplace = [];

  root.traverse((child) => {
    if (child.isInstancedMesh) {
      toReplace.push(child);
    }
  });

  for (const instMesh of toReplace) {
    const parent = instMesh.parent;
    if (!parent) continue;

    const count = instMesh.count;
    const tmpMatrix = new THREE.Matrix4();

    for (let i = 0; i < count; i++) {
      instMesh.getMatrixAt(i, tmpMatrix);

      // Clone geometry and bake the instance transform into vertices
      const geo = instMesh.geometry.clone();
      geo.applyMatrix4(tmpMatrix);

      const mesh = new THREE.Mesh(geo, instMesh.material);
      mesh.name = `${instMesh.name}_inst${i}`;

      // Copy the InstancedMesh's own local transform so parent-relative
      // positioning is preserved
      mesh.matrix.copy(instMesh.matrix);
      mesh.matrixAutoUpdate = false;
      mesh.position.copy(instMesh.position);
      mesh.rotation.copy(instMesh.rotation);
      mesh.scale.copy(instMesh.scale);

      parent.add(mesh);
    }

    parent.remove(instMesh);
  }
}
```

**Why clone + applyMatrix4?** Each instance has a unique transform stored in the `instanceMatrix` attribute. By baking it directly into the geometry's vertex positions, the resulting `Mesh` sits at the correct world position without needing the instancing machinery.

## Step 4 — Coordinate-Space Rotation (Y-up → Z-up)

Blender uses Z-up. Three.js uses Y-up. The standard conversion is a −90° rotation around the X axis.

```javascript
function wrapWithBlenderRotation(sceneRoot) {
  const wrapper = new THREE.Group();
  wrapper.rotation.x = -Math.PI / 2;   // −90° around X
  wrapper.add(sceneRoot);
  return wrapper;
}
```

**Critical:** This rotation must be applied BEFORE `updateMatrixWorld` and BEFORE `OBJExporter.parse()`. The exporter reads world matrices, so the rotation propagates to all descendants automatically.

**Verification:** After export, original Y values in Three.js should appear as Z values in the OBJ (with sign flip). For example, a vertex at `(x, 0.4, z)` in Three.js Y-up becomes approximately `(x, -z, 0.4)` in the OBJ after the −90° X rotation.

## Step 5 — Force World Matrix Update

```javascript
wrapper.updateMatrixWorld(true);   // recursive = true
```

In a browser, the renderer calls this every frame. In headless Node.js, nothing triggers it. If you skip this call, every child's `matrixWorld` is the identity matrix and the OBJ geometry collapses to the origin.

## Step 6 — Export and Write

```javascript
import { OBJExporter } from 'three/examples/jsm/exporters/OBJExporter.js';
import fs from 'fs';
import path from 'path';

function exportToObj(root, outputPath) {
  const exporter = new OBJExporter();
  const objString = exporter.parse(root);

  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, objString, 'utf-8');

  // Quick stats for validation
  const vCount = (objString.match(/^v /gm) || []).length;
  const fCount = (objString.match(/^f /gm) || []).length;
  const oNames = (objString.match(/^o .+/gm) || []);
  console.log(`Wrote ${outputPath}: ${vCount} vertices, ${fCount} faces, ${oNames.length} objects`);
  oNames.forEach((o) => console.log(`  ${o}`));
}
```

## Step 7 — Validation Checklist

After writing the OBJ, verify:

1. **File exists and is non-empty** — `fs.statSync(outputPath).size > 0`
2. **Has vertices and faces** — grep for `^v ` and `^f ` lines; both counts must be > 0
3. **All expected objects present** — compare `^o ` lines against mesh names from the source
4. **Coordinate space** — sample a few vertices and confirm Z values correspond to original Y values (with the −90° rotation applied)
5. **No NaN or Infinity** — scan for `NaN` or `Infinity` in vertex lines

```bash
# Quick CLI validation
grep -c '^v ' /root/output/object.obj    # vertex count
grep -c '^f ' /root/output/object.obj    # face count
grep '^o ' /root/output/object.obj       # object names
grep -c 'NaN\|Infinity' /root/output/object.obj  # should be 0
```

## Common Pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Forgetting `updateMatrixWorld(true)` | All geometry collapsed at origin; OBJ has vertices but they're all near (0,0,0) | Always call it on the outermost wrapper before `parse()` |
| Not expanding `InstancedMesh` | Some objects missing from OBJ; vertex/face count too low | Traverse and replace every `InstancedMesh` before export |
| Applying rotation AFTER export | OBJ is in Y-up space; Blender import looks sideways | Wrap root in rotated Group BEFORE calling `parse()` |
| Using `require()` with Three.js ESM | `ERR_REQUIRE_ESM` crash | Use ESM: name file `.mjs` or set `"type": "module"` in `package.json` |
| Forgetting to copy InstancedMesh's own local transform | Expanded instances positioned incorrectly — they cluster at origin instead of their group-relative position | Copy `position`, `rotation`, `scale` from the `InstancedMesh` to each expanded `Mesh` |
| Not creating output directory | `ENOENT` write error | `fs.mkdirSync(dir, { recursive: true })` before writing |
| Exporting the scene instead of the wrapper | Rotation not applied because wrapper isn't an ancestor of the scene | Make sure `parse(wrapper)` not `parse(scene)` |
| OBJExporter not handling `LineSegments` / `Points` | Extra geometry types cause errors or are silently dropped | Only `Mesh` objects produce OBJ output; other types are ignored — this is usually fine |

## Node.js ESM Setup for Headless Three.js

Three.js is ESM-only since v0.160. Your export script must use ESM imports.

**Option A — `.mjs` extension (simplest):**
```bash
node /root/export.mjs
```

**Option B — `"type": "module"` in package.json:**
```json
{ "type": "module" }
```
Then `node /root/export.js` works with `import` syntax.

**Option C — if the source file uses `require`-style exports:**
You can still import it from ESM using dynamic `import()` or by reading and evaluating it. Most task files use ESM `export` already.

## OBJ Format Quick Reference

```
# Comment line
o object_name
v x y z          # vertex position
vn nx ny nz      # vertex normal (optional)
vt u v           # texture coordinate (optional)
f v1 v2 v3       # face (1-indexed vertex indices)
f v1/vt1/vn1 v2/vt2/vn2 v3/vt3/vn3   # face with tex+normal indices
```

- Indices are **1-based** (not 0-based).
- `o` lines separate named objects.
- OBJExporter outputs `v`, `vn`, and `f` lines with `v//vn` format (no texture coords unless UVs exist).

## Reference Implementation

This is a complete, self-contained export script. Copy it, adjust the import path for the source file's export function name, and run with `node export.mjs`.

```javascript
// /root/export.mjs
// Complete Three.js → OBJ exporter for Blender (Z-up, headless Node.js)
// Usage: node /root/export.mjs

import * as THREE from 'three';
import { OBJExporter } from 'three/examples/jsm/exporters/OBJExporter.js';
import fs from 'fs';
import path from 'path';

// ─── Configuration ───────────────────────────────────────────────
const SOURCE_PATH = '/root/data/object.js';
const OUTPUT_PATH = '/root/output/object.obj';

// ─── 1. Import the scene builder from the source file ────────────
// The source file typically exports a default function or a named export
// that returns a THREE.Group or THREE.Scene.  Adjust the import to match.
const sourceModule = await import(SOURCE_PATH);

// Try common export patterns: default, createObject, buildScene, scene
const buildScene =
  sourceModule.default ||
  sourceModule.createObject ||
  sourceModule.buildScene ||
  sourceModule.createScene ||
  sourceModule.scene;

if (typeof buildScene !== 'function' && !(buildScene instanceof THREE.Object3D)) {
  // If none of the common names match, grab the first exported function or Object3D
  const keys = Object.keys(sourceModule);
  let found = null;
  for (const key of keys) {
    const val = sourceModule[key];
    if (typeof val === 'function' || val instanceof THREE.Object3D) {
      found = val;
      break;
    }
  }
  if (!found) {
    console.error('Could not find a scene builder function or Object3D in source module.');
    console.error('Exports found:', keys);
    process.exit(1);
  }
  var sceneRoot = typeof found === 'function' ? found() : found;
} else {
  var sceneRoot = typeof buildScene === 'function' ? buildScene() : buildScene;
}

console.log(`Scene root: ${sceneRoot.type}, name="${sceneRoot.name}", children=${sceneRoot.children.length}`);

// ─── 2. Expand InstancedMesh nodes ──────────────────────────────
function expandInstancedMeshes(root) {
  const toReplace = [];
  root.traverse((child) => {
    if (child.isInstancedMesh) {
      toReplace.push(child);
    }
  });

  if (toReplace.length > 0) {
    console.log(`Expanding ${toReplace.length} InstancedMesh node(s)...`);
  }

  for (const instMesh of toReplace) {
    const parent = instMesh.parent;
    if (!parent) continue;

    const count = instMesh.count;
    const tmpMatrix = new THREE.Matrix4();

    for (let i = 0; i < count; i++) {
      instMesh.getMatrixAt(i, tmpMatrix);

      const geo = instMesh.geometry.clone();
      geo.applyMatrix4(tmpMatrix);

      const mesh = new THREE.Mesh(geo, instMesh.material);
      mesh.name = `${instMesh.name || 'instanced'}_inst${i}`;

      // Preserve the InstancedMesh's own local transform
      mesh.position.copy(instMesh.position);
      mesh.rotation.copy(instMesh.rotation);
      mesh.scale.copy(instMesh.scale);

      parent.add(mesh);
    }

    parent.remove(instMesh);
    console.log(`  Expanded "${instMesh.name}" → ${count} individual meshes`);
  }
}

expandInstancedMeshes(sceneRoot);

// ─── 3. Wrap in Blender Z-up rotation ───────────────────────────
// Three.js = Y-up, Blender = Z-up → rotate −90° around X
const wrapper = new THREE.Group();
wrapper.rotation.x = -Math.PI / 2;
wrapper.add(sceneRoot);

// ─── 4. Force world matrix computation (no render loop in headless) ─
wrapper.updateMatrixWorld(true);

// ─── 5. Export to OBJ ───────────────────────────────────────────
const exporter = new OBJExporter();
const objString = exporter.parse(wrapper);

// ─── 6. Write output ───────────────────────────────────────────
fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
fs.writeFileSync(OUTPUT_PATH, objString, 'utf-8');

// ─── 7. Validate ───────────────────────────────────────────────
const vCount = (objString.match(/^v /gm) || []).length;
const fCount = (objString.match(/^f /gm) || []).length;
const oNames = (objString.match(/^o .+/gm) || []);
const hasNaN = /NaN|Infinity/.test(objString);

console.log(`\n=== Export Summary ===`);
console.log(`Output: ${OUTPUT_PATH}`);
console.log(`Vertices: ${vCount}`);
console.log(`Faces: ${fCount}`);
console.log(`Objects (${oNames.length}):`);
oNames.forEach((o) => console.log(`  ${o}`));
console.log(`Contains NaN/Infinity: ${hasNaN}`);

if (vCount === 0 || fCount === 0) {
  console.error('\nERROR: OBJ has no geometry! Check InstancedMesh expansion and updateMatrixWorld.');
  process.exit(1);
}
if (hasNaN) {
  console.error('\nERROR: OBJ contains NaN or Infinity values!');
  process.exit(1);
}

console.log('\nExport complete ✓');
```

### Running the Script

```bash
# Ensure output directory exists
mkdir -p /root/output

# Run the export
node /root/export.mjs

# Validate output
head -20 /root/output/object.obj
grep -c '^v ' /root/output/object.obj
grep -c '^f ' /root/output/object.obj
grep '^o ' /root/output/object.obj
```

### Adapting to Different Source Files

The reference implementation tries several common export names (`default`, `createObject`, `buildScene`, `createScene`, `scene`). If the source uses a different name:

1. Read the source file: `cat /root/data/object.js | grep -E 'export (default|function|const)'`
2. Identify the exported symbol name
3. Adjust the import: `const { myFunctionName } = await import(SOURCE_PATH);`

### When the Source Returns a Scene vs. a Group

- If the source returns a `THREE.Scene`, it works the same way — `wrapper.add(scene)` is valid.
- If the source returns a `THREE.Mesh` (single object), wrapping still works correctly.
- The wrapper approach is universal regardless of what the source returns.

### Edge Cases

- **Merged geometries** (`BufferGeometryUtils.mergeGeometries`): These export as a single mesh. No special handling needed.
- **Multi-material meshes**: OBJExporter handles these by splitting into groups. Works automatically.
- **Empty geometry**: Some decorative meshes may have 0 vertices. OBJExporter skips them silently — this is fine.
- **Very large instance counts** (>1000): Expanding creates many individual meshes. This is memory-intensive but correct. For extremely large counts, consider batching, but for typical task sizes (<100 instances) it's not an issue.