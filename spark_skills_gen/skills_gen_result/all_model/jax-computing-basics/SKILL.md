---
title: JAX Computing Basics — Solving NumPy-In/NumPy-Out Computation Tasks
category: jax-computing-basics
tags:
  - jax
  - numpy
  - scientific-computing
  - autodiff
  - scan
  - vmap
  - jit
domain: numerical-computing
environment:
  runtime: ubuntu:24.04
  python: python3
  packages:
    - jax>=0.8
    - jaxlib>=0.8
    - numpy>=2.0
---

# JAX Computing Basics — Procedural Skill

## Overview

This skill covers a recurring task pattern: given a `problem.json` manifest describing
multiple JAX computation sub-tasks, load `.npy`/`.npz` input data, perform the
described computation using JAX primitives, and save results as `.npy` files.
A test harness then checks file existence, shape correctness, and numerical
closeness against reference outputs.

---

## High-Level Workflow

### Step 1 — Read the problem manifest and the test harness

Before writing any code, understand *exactly* what is expected.

```bash
cat /app/problem.json
```

Also read the test file to learn:
- where reference/answer files live (often `reference/<name>.npy`)
- tolerance used by `np.allclose` (typically `rtol=1e-5, atol=1e-6`)
- any extra checks (duplicate IDs, shape matching, file existence)

```bash
# Find and read the test file
find /app -name "test_*" -o -name "*_test.py" | head -20
cat /app/../tests/test_outputs.py   # common location
```

### Step 2 — Inspect input data shapes and reference outputs

Before implementing anything, load every input file and every reference file
to know the exact shapes and dtypes you must produce.

```python
import numpy as np, json, pathlib

with open("problem.json") as f:
    tasks = json.load(f)

for t in tasks:
    print(f"\n=== Task {t['id']}: {t['description']} ===")
    inp = t["input"]
    if inp.endswith(".npy"):
        arr = np.load(inp)
        print(f"  input  {inp}: shape={arr.shape} dtype={arr.dtype}")
    elif inp.endswith(".npz"):
        npz = np.load(inp)
        for k in npz.files:
            print(f"  input  {inp}[{k}]: shape={npz[k].shape} dtype={npz[k].dtype}")

    ref_path = f"reference/{pathlib.Path(t['output']).name}"
    if pathlib.Path(ref_path).exists():
        ref = np.load(ref_path)
        print(f"  ref    {ref_path}: shape={ref.shape} dtype={ref.dtype}")
```

This step prevents shape mismatches and tells you which axis to reduce over,
what matrix dimensions to expect, etc.

### Step 3 — Verify JAX is available

```bash
python3 -c "import jax; import jax.numpy as jnp; print('JAX', jax.__version__)"
```

JAX may default to CPU in these environments — that is fine and expected.

### Step 4 — Write the solution script

Create a single `solve.py` that handles every task. The general skeleton:

```python
#!/usr/bin/env python3
"""solve.py — JAX computation tasks from problem.json"""

import json
import numpy as np
import jax
import jax.numpy as jnp

# ── Load problem manifest ──────────────────────────────────────────
with open("problem.json") as f:
    tasks = json.load(f)

# Build a lookup so we can handle tasks by id
task_map = {t["id"]: t for t in tasks}

# ── Helper: save a JAX array as .npy ───────────────────────────────
def save(jax_array, path):
    """Convert JAX array to NumPy and persist."""
    np.save(path, np.array(jax_array))

# ── Task implementations (see detailed patterns below) ─────────────
# ... one block per task ...

print("All outputs saved.")
```

### Step 5 — Run and verify against references

```python
import numpy as np, json, pathlib

with open("problem.json") as f:
    tasks = json.load(f)

for t in tasks:
    name = pathlib.Path(t["output"]).stem
    out = np.load(t["output"])
    ref = np.load(f"reference/{name}.npy")
    ok = np.allclose(out, ref, rtol=1e-5, atol=1e-6)
    diff = np.max(np.abs(out - ref))
    print(f"{name}: shape {out.shape}=={ref.shape}  close={ok}  maxdiff={diff:.2e}")
```

If any task fails, investigate that task in isolation (see Common Pitfalls).

### Step 6 — Run the official test suite

```bash
cd /app && python3 -m pytest ../tests/test_outputs.py -v
```

All five standard checks must pass:
1. `test_all_output_files_exist`
2. `test_all_answer_files_exist`
3. `test_shapes_match`
4. `test_allclose`
5. `test_no_duplicate_ids`

---

## Common JAX Task Patterns — Complete Implementations

### Pattern A: Reduction (e.g. row-wise mean)

```python
# "Compute the row-wise mean of x"
x = jnp.array(np.load("data/x.npy"))
result = jnp.mean(x, axis=1)          # shape (N,) if x is (N, M)
save(result, "basic_reduce.npy")
```

Key decision: the description says "row-wise" → `axis=1`. "Column-wise" → `axis=0`.
Always cross-check with the reference shape from Step 2.

### Pattern B: Mapped / vectorized element-wise ops (jax.vmap)

```python
# "Square each row of x using vmap"
x = jnp.array(np.load("data/x.npy"))

def square_row(row):
    return row ** 2

result = jax.vmap(square_row)(x)       # shape same as x
save(result, "map_square.npy")
```

`jax.vmap` maps a function over the leading axis by default.
For element-wise ops the result shape equals the input shape — verify this.

### Pattern C: Automatic differentiation (jax.grad)

```python
# "Compute gradient of logistic loss w.r.t. weights w"
data = np.load("data/logistic.npz")
w = jnp.array(data["w"])    # (D,)
x = jnp.array(data["X"])    # (N, D)
y = jnp.array(data["y"])    # (N,)

def logistic_loss(w, x, y):
    return jnp.mean(jnp.log(1.0 + jnp.exp(-y * (x @ w))))

grad_fn = jax.grad(logistic_loss, argnums=0)
result = grad_fn(w, x, y)              # shape (D,)
save(result, "grad_logistic.npy")
```

Critical notes:
- `argnums=0` differentiates w.r.t. the first positional argument.
- The loss must be a scalar; `jnp.mean(...)` ensures this.
- Use the numerically stable form `log(1 + exp(...))` — do NOT simplify.

### Pattern D: Sequential scan / RNN (jax.lax.scan)

```python
# "Run an RNN forward pass using jax.lax.scan"
data = np.load("data/seq.npz")
seq  = jnp.array(data["seq"])    # (T, input_dim)
init = jnp.array(data["init"])   # (hidden_dim,)
Wx   = jnp.array(data["Wx"])     # (hidden_dim, input_dim)
Wh   = jnp.array(data["Wh"])     # (hidden_dim, hidden_dim)
b    = jnp.array(data["b"])      # (hidden_dim,)

def rnn_step(h, x_t):
    # IMPORTANT: matrix @ vector, not vector @ matrix
    h_new = jnp.tanh(Wx @ x_t + Wh @ h + b)
    return h_new, h_new           # (carry, output)

_, all_hidden = jax.lax.scan(rnn_step, init, seq)
# all_hidden shape: (T, hidden_dim)
save(all_hidden, "scan_rnn.npy")
```

`jax.lax.scan` signature: `scan(fn, init_carry, xs) → (final_carry, stacked_outputs)`.
The function `fn(carry, x) → (new_carry, output)` is called once per time step.

### Pattern E: JIT-compiled MLP

```python
# "Run a 2-layer MLP with ReLU, JIT compiled"
data = np.load("data/mlp.npz")
X  = jnp.array(data["X"])    # (N, in_dim)
W1 = jnp.array(data["W1"])   # (in_dim, hidden_dim)
b1 = jnp.array(data["b1"])   # (hidden_dim,)
W2 = jnp.array(data["W2"])   # (hidden_dim, out_dim)
b2 = jnp.array(data["b2"])   # (out_dim,)

@jax.jit
def mlp(X, W1, b1, W2, b2):
    h = jax.nn.relu(X @ W1 + b1)   # hidden layer + ReLU
    return h @ W2 + b2              # output layer (linear)

result = mlp(X, W1, b1, W2, b2)    # shape (N, out_dim)
save(result, "jit_mlp.npy")
```

Unless the description explicitly names a different activation (sigmoid, tanh),
default to ReLU for MLP hidden layers. The output layer is typically linear.

---

## Saving Outputs — Conversion Rule

JAX arrays are NOT NumPy arrays. Always convert before saving:

```python
np.save(path, np.array(jax_array))
```

Calling `np.save(path, jax_array)` may work in some JAX versions but can
silently produce wrong dtypes or fail. Always wrap with `np.array(...)`.

---

## Complete Solve Script Template

```python
#!/usr/bin/env python3
"""
Solve all JAX computation tasks defined in problem.json.
Usage: python3 solve.py
"""

import json
import pathlib
import numpy as np
import jax
import jax.numpy as jnp

# ── Configuration ──────────────────────────────────────────────────
PROBLEM_FILE = "problem.json"

with open(PROBLEM_FILE) as f:
    tasks = json.load(f)

def save(jax_arr, path):
    np.save(path, np.array(jax_arr))

# ── Dispatch by task description keywords ──────────────────────────
for task in tasks:
    tid = task["id"]
    desc = task["description"].lower()
    inp = task["input"]
    out = task["output"]

    print(f"[{tid}] {task['description']}")

    # --- Reduction tasks -------------------------------------------
    if "mean" in desc and "row" in desc:
        x = jnp.array(np.load(inp))
        save(jnp.mean(x, axis=1), out)

    elif "mean" in desc and "col" in desc:
        x = jnp.array(np.load(inp))
        save(jnp.mean(x, axis=0), out)

    # --- vmap / map tasks ------------------------------------------
    elif "vmap" in desc or "square" in desc:
        x = jnp.array(np.load(inp))
        save(jax.vmap(lambda row: row ** 2)(x), out)

    # --- Gradient tasks --------------------------------------------
    elif "grad" in desc and "logistic" in desc:
        d = np.load(inp)
        w, x, y = jnp.array(d["w"]), jnp.array(d["X"]), jnp.array(d["y"])
        def logistic_loss(w, x, y):
            return jnp.mean(jnp.log(1.0 + jnp.exp(-y * (x @ w))))
        save(jax.grad(logistic_loss, argnums=0)(w, x, y), out)

    # --- Scan / RNN tasks ------------------------------------------
    elif "scan" in desc or "rnn" in desc:
        d = np.load(inp)
        seq  = jnp.array(d["seq"])
        init = jnp.array(d["init"])
        Wx, Wh, b = jnp.array(d["Wx"]), jnp.array(d["Wh"]), jnp.array(d["b"])
        def rnn_step(h, x_t):
            return jnp.tanh(Wx @ x_t + Wh @ h + b), None
        # Second pass to collect outputs
        def rnn_step_collect(h, x_t):
            h_new = jnp.tanh(Wx @ x_t + Wh @ h + b)
            return h_new, h_new
        _, hidden_states = jax.lax.scan(rnn_step_collect, init, seq)
        save(hidden_states, out)

    # --- JIT / MLP tasks -------------------------------------------
    elif "jit" in desc or "mlp" in desc:
        d = np.load(inp)
        X  = jnp.array(d["X"])
        W1, b1 = jnp.array(d["W1"]), jnp.array(d["b1"])
        W2, b2 = jnp.array(d["W2"]), jnp.array(d["b2"])
        @jax.jit
        def mlp(X, W1, b1, W2, b2):
            h = jax.nn.relu(X @ W1 + b1)
            return h @ W2 + b2
        save(mlp(X, W1, b1, W2, b2), out)

    else:
        print(f"  WARNING: unrecognized task description, skipping.")

print("All outputs saved.")
```

---

## Verification Script

Run this after `solve.py` to catch problems before the official test suite:

```python
#!/usr/bin/env python3
"""verify.py — quick self-check against reference outputs."""

import json, pathlib, numpy as np

with open("problem.json") as f:
    tasks = json.load(f)

all_ok = True
for t in tasks:
    name = pathlib.Path(t["output"]).stem
    out_path = t["output"]
    ref_path = f"reference/{name}.npy"

    if not pathlib.Path(out_path).exists():
        print(f"MISSING: {out_path}")
        all_ok = False
        continue
    if not pathlib.Path(ref_path).exists():
        print(f"NO REF:  {ref_path} (cannot verify)")
        continue

    out = np.load(out_path)
    ref = np.load(ref_path)

    shape_ok = out.shape == ref.shape
    close_ok = np.allclose(out, ref, rtol=1e-5, atol=1e-6)
    maxdiff  = np.max(np.abs(out - ref))

    status = "OK" if (shape_ok and close_ok) else "FAIL"
    print(f"{status}  {name}: shape {out.shape}=={ref.shape}  "
          f"close={close_ok}  maxdiff={maxdiff:.2e}")
    if not (shape_ok and close_ok):
        all_ok = False

print("\n" + ("ALL PASSED" if all_ok else "SOME FAILED — investigate above"))
```

---

## Common Pitfalls

### 1. Matrix multiplication order in RNN step (most frequent bug)

The RNN recurrence is `h_new = tanh(Wx @ x_t + Wh @ h + b)` where `@` is
matrix-vector multiplication: `(hidden, input) @ (input,) → (hidden,)`.

Wrong: `x_t @ Wx + h @ Wh + b` — this is vector-matrix and produces the
wrong shape or wrong values silently when dimensions happen to align.

Always check: `Wx.shape == (hidden_dim, input_dim)` and use `Wx @ x_t`.

### 2. Wrong activation function in MLP

Unless the description says otherwise, hidden layers use ReLU (`jax.nn.relu`),
not sigmoid or tanh. Using the wrong activation produces values that are
numerically close enough to pass shape checks but fail `allclose`.

### 3. Wrong reduction axis

"Row-wise mean" means reduce across columns → `axis=1`.
"Column-wise mean" means reduce across rows → `axis=0`.
Always verify the output shape against the reference before saving.

### 4. Forgetting to convert JAX arrays to NumPy before np.save

```python
# WRONG — may silently produce bad files
np.save("out.npy", jax_array)

# CORRECT
np.save("out.npy", np.array(jax_array))
```

### 5. Not reading the test harness first

The test file reveals:
- Exact tolerance values (`rtol`, `atol`)
- Where reference files are stored (`reference/`, `answers/`, etc.)
- Whether output files must be in the working directory or a subdirectory

Read it before writing any code.

### 6. Hardcoding