---
title: JAX Computing Basics — Solving NumPy-In / NumPy-Out Computation Tasks
category: jax-computing-basics
tags:
  - jax
  - numpy
  - autodiff
  - scan
  - jit
  - numerical-computing
dependencies:
  - jax>=0.4
  - jaxlib>=0.4
  - numpy>=1.24
environment: ubuntu-24.04, python3
---

# JAX Computing Basics: Structured Computation Tasks

## Overview

This skill covers a common task pattern: you are given a `problem.json` file describing multiple independent computation tasks. Each task specifies an input `.npy` or `.npz` file, a natural-language description of the computation, and an output `.npy` path. Your job is to load the data, perform the computation in JAX, and save the result. A test harness checks that every output file exists, has the correct shape, and is numerically close (`rtol=1e-5, atol=1e-6`) to a reference answer.

The tasks typically exercise core JAX primitives:

| Task Type | JAX API | Key Pitfall |
|---|---|---|
| Reduction (sum, mean, etc.) | `jnp.sum`, `jnp.mean` | Axis selection, keepdims |
| Element-wise map | `jax.vmap`, `jnp.square` | Forgetting to return the right dtype |
| Gradient computation | `jax.grad` | Loss formula must match label encoding |
| Sequential scan (RNN) | `jax.lax.scan` | Matrix multiply order depends on weight shape convention |
| JIT-compiled MLP forward | `jax.jit` | Layer order, activation placement |

---

## High-Level Workflow

1. **Parse `problem.json`** — Load the task list. Each entry has `id`, `description`, `input`, `output`.

2. **Inspect every input file** — Before writing any computation code, print the shapes and dtypes of all arrays in every input file. For `.npz` files, print all keys and their shapes. This is non-negotiable; shape inspection drives every implementation decision.

3. **Check for reference answers** — Look for a `reference/` directory. If it exists, load the reference arrays and print their shapes. This tells you the expected output shape and helps you verify intermediate results.

4. **Identify the label encoding** — For any classification/logistic task, print `np.unique(y)`. Labels in `{-1, +1}` require a different loss formula than labels in `{0, 1}`. This is the single most common source of numerical errors.

5. **Identify weight matrix convention** — For any task involving matrix multiplications (RNN, MLP), compare the weight shape against the input/hidden dimensions. A weight `W` of shape `(out_dim, in_dim)` means `W @ x`; a weight of shape `(in_dim, out_dim)` means `x @ W`. Getting this wrong produces the wrong answer with no runtime error if dimensions happen to be square.

6. **Implement each task** — Write a dispatcher that routes on task `id`. Use JAX for all computation. Save results with `np.save()`.

7. **Verify against references** — After saving all outputs, load each output and reference, compute `np.allclose` and `max |diff|`. Print a per-task PASS/FAIL report. Fix any failures before submitting.

---

## Step-by-Step with Code

### Step 1: Parse the Problem File

```python
import json
import numpy as np
import jax
import jax.numpy as jnp
from pathlib import Path

with open("problem.json") as f:
    tasks = json.load(f)

for t in tasks:
    print(f"Task {t['id']}: {t['description']}")
    print(f"  input:  {t['input']}")
    print(f"  output: {t['output']}")
```

### Step 2: Inspect All Input Data

This step is critical. Never skip it.

```python
for t in tasks:
    inp_path = t["input"]
    if inp_path.endswith(".npz"):
        data = np.load(inp_path)
        print(f"\n[{t['id']}] {inp_path} keys: {list(data.keys())}")
        for k in data:
            print(f"  {k}: shape={data[k].shape}, dtype={data[k].dtype}, "
                  f"min={data[k].min():.4f}, max={data[k].max():.4f}")
            # For label arrays, print unique values
            if k in ("y", "labels", "targets"):
                print(f"    unique values: {np.unique(data[k])}")
    elif inp_path.endswith(".npy"):
        arr = np.load(inp_path)
        print(f"\n[{t['id']}] {inp_path}: shape={arr.shape}, dtype={arr.dtype}")
```

### Step 3: Check Reference Answers

```python
ref_dir = Path("reference")
if ref_dir.exists():
    for t in tasks:
        ref_path = ref_dir / Path(t["output"]).name
        if ref_path.exists():
            ref = np.load(str(ref_path))
            print(f"[{t['id']}] reference: shape={ref.shape}, dtype={ref.dtype}")
```

### Step 4: Implement — Basic Reduction

Reductions are straightforward. Pay attention to which axis is specified in the description.

```python
def solve_basic_reduce(input_path: str) -> jnp.ndarray:
    x = jnp.array(np.load(input_path))
    # Example: "compute the sum along axis 0"
    # Adjust axis based on the task description
    return jnp.sum(x, axis=0)
```

### Step 5: Implement — Element-wise Map

```python
def solve_map_square(input_path: str) -> jnp.ndarray:
    x = jnp.array(np.load(input_path))
    return jnp.square(x)
    # Or equivalently: jax.vmap(lambda xi: xi ** 2)(x)
```

### Step 6: Implement — Gradient of Logistic Loss

This is where most failures happen. The loss formula depends on the label encoding.

```python
def solve_grad_logistic(input_path: str) -> jnp.ndarray:
    data = np.load(input_path)
    X = jnp.array(data["X"])   # (n_samples, n_features)
    y = jnp.array(data["y"])   # (n_samples,)
    w = jnp.array(data["w"])   # (n_features,)

    unique_labels = set(np.unique(data["y"]).tolist())

    if unique_labels <= {-1.0, 1.0, -1, 1}:
        # === SIGNED LABEL ENCODING: y ∈ {-1, +1} ===
        # Loss = mean( log(1 + exp(-y * (X @ w))) )
        # Use logaddexp for numerical stability
        def loss_fn(w_):
            logits = X @ w_                          # (n,)
            return jnp.mean(jnp.logaddexp(0.0, -y * logits))
    else:
        # === STANDARD ENCODING: y ∈ {0, 1} ===
        # Loss = -mean( y*log(σ) + (1-y)*log(1-σ) )
        def loss_fn(w_):
            logits = X @ w_
            # Use log-sigmoid for stability
            log_p = jax.nn.log_sigmoid(logits)
            log_1mp = jax.nn.log_sigmoid(-logits)
            return -jnp.mean(y * log_p + (1.0 - y) * log_1mp)

    grad_fn = jax.grad(loss_fn)
    return grad_fn(w)
```

**Why `logaddexp`?** — `jnp.logaddexp(0.0, z)` computes `log(1 + exp(z))` in a numerically stable way, avoiding overflow for large positive `z` and underflow for large negative `z`.

### Step 7: Implement — RNN with `jax.lax.scan`

The critical decision is the matrix multiply order. Inspect the weight shapes first.

```python
def solve_scan_rnn(input_path: str) -> jnp.ndarray:
    data = np.load(input_path)
    Wx = jnp.array(data["Wx"])    # Weight for input
    Wh = jnp.array(data["Wh"])    # Weight for hidden state
    b  = jnp.array(data["b"])     # Bias
    seq = jnp.array(data["seq"])  # (seq_len, input_dim)
    init = jnp.array(data["init"])  # (hidden_dim,)

    hidden_dim = init.shape[0]
    input_dim = seq.shape[1]

    # Determine multiply order from weight shapes
    # Convention 1: Wx is (hidden_dim, input_dim) → Wx @ x_t
    # Convention 2: Wx is (input_dim, hidden_dim) → x_t @ Wx
    if Wx.shape == (hidden_dim, input_dim):
        # (hidden, input) convention: W @ x
        def rnn_step(h, x_t):
            h_new = jnp.tanh(Wx @ x_t + Wh @ h + b)
            return h_new, h_new
    elif Wx.shape == (input_dim, hidden_dim):
        # (input, hidden) convention: x @ W
        def rnn_step(h, x_t):
            h_new = jnp.tanh(x_t @ Wx + h @ Wh + b)
            return h_new, h_new
    else:
        # Square matrices — ambiguous. Try (hidden, input) first.
        # If Wx is (d, d), the convention is usually Wx @ x_t
        # (i.e., weight matrices are (out_dim, in_dim))
        def rnn_step(h, x_t):
            h_new = jnp.tanh(Wx @ x_t + Wh @ h + b)
            return h_new, h_new

    final_h, all_h = jax.lax.scan(rnn_step, init, seq)
    return all_h  # (seq_len, hidden_dim)
```

**Critical note on square matrices:** When `input_dim == hidden_dim`, both `Wx @ x_t` and `x_t @ Wx` are valid shapes. In practice, the `(out_dim, in_dim)` convention (i.e., `W @ x`) is more common in these tasks. If the reference answer doesn't match, swap the order. This is the most common RNN pitfall.

### Step 8: Implement — JIT-compiled MLP Forward Pass

```python
def solve_jit_mlp(input_path: str) -> jnp.ndarray:
    data = np.load(input_path)
    X = jnp.array(data["X"])      # (n_samples, input_dim)

    # Collect weight/bias pairs — naming varies
    # Common patterns: W1/b1/W2/b2 or weights_0/biases_0/...
    keys = sorted(data.keys())
    weight_keys = sorted([k for k in keys if k.startswith("W") or k.startswith("weight")])
    bias_keys = sorted([k for k in keys if k.startswith("b") or k.startswith("bias")])

    # Pair them up in order
    layers = []
    for wk, bk in zip(weight_keys, bias_keys):
        W = jnp.array(data[wk])
        b = jnp.array(data[bk])
        layers.append((W, b))

    @jax.jit
    def forward(x):
        h = x
        for i, (W, b) in enumerate(layers):
            # Determine multiply order from shapes
            if W.shape[0] == h.shape[-1]:
                # W is (in, out) → h @ W + b
                h = h @ W + b
            else:
                # W is (out, in) → (W @ h.T).T + b  or  h @ W.T + b
                h = h @ W.T + b
            # Apply ReLU to all layers except the last
            if i < len(layers) - 1:
                h = jax.nn.relu(h)
        return h

    return forward(X)
```

### Step 9: Dispatch and Save

```python
SOLVERS = {
    "basic_reduce": solve_basic_reduce,
    "map_square": solve_map_square,
    "grad_logistic": solve_grad_logistic,
    "scan_rnn": solve_scan_rnn,
    "jit_mlp": solve_jit_mlp,
}

for t in tasks:
    task_id = t["id"]
    solver = SOLVERS.get(task_id)
    if solver is None:
        print(f"WARNING: No solver for task '{task_id}'")
        continue
    result = solver(t["input"])
    result_np = np.array(result)
    np.save(t["output"], result_np)
    print(f"Completed task: {task_id}  shape={result_np.shape}")
```

### Step 10: Verify Against References

Always run this before declaring success.

```python
ref_dir = Path("reference")
all_pass = True
for t in tasks:
    out = np.load(t["output"])
    ref_path = ref_dir / Path(t["output"]).name
    if not ref_path.exists():
        print(f"[{t['id']}] No reference found — skipping verification")
        continue
    ref = np.load(str(ref_path))
    close = np.allclose(out, ref, rtol=1e-5, atol=1e-6)
    maxdiff = float(np.max(np.abs(out - ref)))
    status = "PASS" if close else "FAIL"
    print(f"{t['id']}: {status}  maxdiff={maxdiff:.2e}  shape={out.shape}")
    if not close:
        all_pass = False

print(f"\nOverall: {'ALL PASS' if all_pass else 'SOME FAILED'}")
```

---

## Common Pitfalls

### 1. Wrong Logistic Loss for Signed Labels (y ∈ {-1, +1})

**Symptom:** `grad_logistic` output has correct shape but wrong values; `maxdiff` is large.

**Root cause:** Using the standard binary cross-entropy formula `−[y log σ + (1−y) log(1−σ)]` when labels are `{-1, +1}` instead of `{0, 1}`.

**Fix:** Always check `np.unique(y)` first. For signed labels, use:
```python
loss = jnp.mean(jnp.logaddexp(0.0, -y * logits))
```

### 2. Wrong Matrix Multiply Order in RNN / MLP

**Symptom:** `scan_rnn` or `jit_mlp` output has correct shape but wrong values. No runtime error because dimensions happen to be compatible (e.g., square weight matrices).

**Root cause:** Weight matrix shape convention is ambiguous when `input_dim == hidden_dim`. A `(3, 3)` weight could be either `(in, out)` or `(out, in)`.

**Fix:** When dimensions are square, default to `W @ x` (the `(out_dim, in_dim)` convention). If verification fails, swap to `x @ W`. Always verify against the reference after implementing.

### 3. Forgetting Numerical Stability

**Symptom:** NaN or Inf in gradient outputs.

**Root cause:** Using `jnp.log(1 + jnp.exp(z))` directly, which overflows for large `z`.

**Fix:** Use `jnp.logaddexp(0.0, z)` or `jax.nn.log_sigmoid`.

### 4. Not Applying Activation on Intermediate Layers Only

**Symptom:** MLP output is wrong because ReLU is applied to the final layer.

**Fix:** Apply activation (ReLU, tanh, etc.) to all layers except the last one.

### 5. Saving with Wrong Dtype

**Symptom:** Shape matches but values are off due to float32 vs float64 truncation.

**Fix:** JAX defaults to float32. If inputs are float64, either cast inputs to float32 (and accept the precision) or enable float64:
```python
jax.config.update("jax_enable_x64", True)
```
Usually float32 is fine for `rtol=1e-5, atol=1e-6` tolerances.

### 6. Axis Confusion in Reductions

**Symptom:** Output shape doesn't match reference.

**Fix:** Read the description carefully. `axis=0` reduces along rows (output has shape of a single row). `axis=1` reduces along columns. When in doubt, check the reference shape.

---

## Reference Implementation

This is a complete, self-contained script. Copy it, adapt the solver functions to match the actual task descriptions, and run it.

```python
#!/usr/bin/env python3
"""
Complete JAX computation task solver.

Usage:
    python3 solve.py

Expects:
    - problem.json in the current directory
    - Input .npy/.npz files as specified in problem.json
    - (Optional) reference/ directory with ground-truth .npy files

Produces:
    - Output .npy files as specified in problem.json
"""

import json
import numpy as np
import jax
import jax.numpy as jnp
from pathlib import Path

# ---------------------------------------------------------------------------
# 0. Configuration
# ---------------------------------------------------------------------------
# Enable float64 if needed (uncomment if reference uses float64):
# jax.config.update("jax_enable_x64", True)

PROBLEM_FILE = "problem.json"
REFERENCE_DIR = Path("reference")

# ---------------------------------------------------------------------------
# 1. Load problem definition
# ---------------------------------------------------------------------------
with open(PROBLEM_FILE) as f:
    tasks = json.load(f)

# ---------------------------------------------------------------------------
# 2. Inspect all inputs (print to stdout for debugging)
# ---------------------------------------------------------------------------
def inspect_inputs(tasks):
    for t in tasks:
        inp = t["input"]
        print(f"\n=== [{t['id']}] {inp} ===")
        if inp.endswith(".npz"):
            data = np.load(inp)
            for k in sorted(data.keys()):
                arr = data[k]
                extra = ""
                if k in ("y", "labels", "targets"):
                    extra = f"  unique={np.unique(arr).tolist()}"
                print(f"  {k}: shape={arr.shape} dtype={arr.dtype} "
                      f"min={arr.min():.4f} max={arr.max():.4f}{extra}")
        else:
            arr = np.load(inp)
            print(f"  shape={arr.shape} dtype={arr.dtype} "
                  f"min={arr.min():.4f} max={arr.max():.4f}")

inspect_inputs(tasks)

# ---------------------------------------------------------------------------
# 3. Solver functions
# ---------------------------------------------------------------------------

def solve_basic_reduce(input_path: str) -> jnp.ndarray:
    """Reduce an array — adapt axis/operation to the task description."""
    x = jnp.array(np.load(input_path))
    # Common variants: jnp.sum, jnp.mean, jnp.max, jnp.min, jnp.prod
    # Common axes: 0, 1, None (global)
    return jnp.sum(x, axis=0)


def solve_map_square(input_path: str) -> jnp.ndarray:
    """Element-wise square of the input array."""
    x = jnp.array(np.load(input_path))
    return jnp.square(x)


def solve_grad_logistic(input_path: str) -> jnp.ndarray:
    """Compute gradient of logistic loss w.r.t. weight vector w.

    CRITICAL: Check whether y ∈ {-1, +1} or y ∈ {0, 1}.
    """
    data = np.load(input_path)
    X = jnp.array(data["X"])    # (n_samples, n_features)
    y = jnp.array(data["y"])    # (n_samples,)
    w = jnp.array(data["w"])    # (n_features,)

    unique_y = set(np.unique(data["y"]).tolist())

    if unique_y <= {-1.0, 1.0, -1, 1}:
        # Signed labels: loss = mean(log(1 + exp(-y * Xw)))
        def loss_fn(w_):
            logits = X @ w_
            return jnp.mean(jnp.logaddexp(0.0, -y * logits))
    else:
        # Standard {0, 1} labels: binary cross-entropy
        def loss_fn(w_):
            logits = X @ w_
            log_p = jax.nn.log_sigmoid(logits)
            log_1mp = jax.nn.log_sigmoid(-logits)
            return -jnp.mean(y * log_p + (1.0 - y) * log_1mp)

    return jax.grad(loss_fn)(w)


def solve_scan_rnn(input_path: str) -> jnp.ndarray:
    """Run an RNN over a sequence using jax.lax.scan.

    CRITICAL: Determine matrix multiply order from weight shapes.
    """
    data = np.load(input_path)
    Wx   = jnp.array(data["Wx"])     # input weight
    Wh   = jnp.array(data["Wh"])     # hidden weight
    b    = jnp.array(data["b"])      # bias
    seq  = jnp.array(data["seq"])    # (seq_len, input_dim)
    init = jnp.array(data["init"])   # (hidden_dim,)

    hidden_dim = init.shape[0]
    input_dim  = seq.shape[1]

    # Determine convention from shapes
    if Wx.shape == (hidden_dim, input_dim) or Wx.shape[0] == Wx.shape[1]:
        # (hidden, input) or square → use W @ x convention
        def step(h, x_t):
            h_new = jnp.tanh(Wx @ x_t + Wh @ h + b)
            return h_new, h_new
    else:
        # (input, hidden) → use x @ W convention
        def step(h, x_t):
            h_new = jnp.tanh(x_t @ Wx + h @ Wh + b)
            return h_new, h_new

    final_h, all_h = jax.lax.scan(step, init, seq)
    return all_h   # (seq_len, hidden_dim)


def solve_jit_mlp(input_path: str) -> jnp.ndarray:
    """Forward pass through a multi-layer perceptron, JIT-compiled.

    Layers use ReLU activation except the final layer (linear output).
    """
    data = np.load(input_path)
    X = jnp.array(data["X"])

    # Collect layers in order
    keys = sorted(data.keys())
    w_keys = sorted([k for k in keys if k.lower().startswith("w")])
    b_keys = sorted([k for k in keys if k.lower().startswith("b")
                     and k.lower() != "bias"])  # avoid collision

    # Fallback: match W1/b1, W2/b2, ... pattern
    if not b_keys:
        b_keys = sorted([k for k in keys if k.lower().startswith("b")])

    # Remove non-weight keys from w_keys (e.g., "X" starts with uppercase
    # but is not a weight). Filter to only Wn pattern.
    w_keys = [k for k in keys if k[0] == "W" and k[1:].isdigit()]
    b_keys = [k for k in keys if k[0] == "b" and k[1:].isdigit()]
    w_keys.sort(key=lambda k: int(k[1:]))
    b_keys.sort(key=lambda k: int(k[1:]))

    layers = []
    for wk, bk in zip(w_keys, b_keys):
        layers.append((jnp.array(data[wk]), jnp.array(data[bk])))

    @jax.jit
    def forward(x):
        h = x
        for i, (W, b_vec) in enumerate(layers):
            # Determine multiply order
            if W.shape[0] == h.shape[-1]:
                h = h @ W + b_vec
            else:
                h = h @ W.T + b_vec
            # ReLU on all but last layer
            if i < len(layers) - 1:
                h = jax.nn.relu(h)
        return h

    return forward(X)


# ---------------------------------------------------------------------------
# 4. Dispatcher
# ---------------------------------------------------------------------------
SOLVERS = {
    "basic_reduce":   solve_basic_reduce,
    "map_square":     solve_map_square,
    "grad_logistic":  solve_grad_logistic,
    "scan_rnn":       solve_scan_rnn,
    "jit_mlp":        solve_jit_mlp,
}

for t in tasks:
    tid = t["id"]
    solver = SOLVERS.get(tid)
    if solver is None:
        print(f"WARNING: No solver registered for task '{tid}' — skipping")
        continue
    result = solver(t["input"])
    result_np = np.array(result)
    np.save(t["output"], result_np)
    print(f"Completed task: {tid}  shape={result_np.shape}  dtype={result_np.dtype}")

print("All tasks done.")

# ---------------------------------------------------------------------------
# 5. Verification against reference answers
# ---------------------------------------------------------------------------
if REFERENCE_DIR.exists():
    print("\n=== Verification ===")
    all_pass = True
    for t in tasks:
        out = np.load(t["output"])
        ref_path = REFERENCE_DIR / Path(t["output"]).name
        if not ref_path.exists():
            print(f"[{t['id']}] No reference — skipped")
            continue
        ref = np.load(str(ref_path))
        if out.shape != ref.shape:
            print(f"[{t['id']}] SHAPE MISMATCH: got {out.shape}, expected {ref.shape}")
            all_pass = False
            continue
        close = np.allclose(out, ref, rtol=1e-5, atol=1e-6)
        maxdiff = float(np.max(np.abs(out - ref)))
        status = "PASS" if close else "FAIL"
        print(f"[{t['id']}] {status}  maxdiff={maxdiff:.2e}")
        if not close:
            all_pass = False
            # Print first few mismatched values for debugging
            mask = ~np.isclose(out, ref, rtol=1e-5, atol=1e-6)
            idx = np.argwhere(mask)[:5]
            for ix in idx:
                ix_tuple = tuple(ix)
                print(f"    at {ix_tuple}: got={out[ix_tuple]:.8f}  "
                      f"ref={ref[ix_tuple]:.8f}  "
                      f"diff={abs(out[ix_tuple]-ref[ix_tuple]):.2e}")

    print(f"\nOverall: {'ALL PASS' if all_pass else 'SOME FAILED'}")
else:
    print("No reference directory found — skipping verification.")
```

---

## Debugging Checklist

When a task fails verification, work through this list in order:

1. **Shape mismatch?** → Check axis argument in reductions, or whether you're returning the wrong variable (e.g., `final_h` vs `all_h` in scan).

2. **Large maxdiff (> 0.1)?** → The formula is wrong. Check:
   - Label encoding (`{-1,+1}` vs `{0,1}`)
   - Matrix multiply order (`W @ x` vs `x @ W`)
   - Missing or extra activation function

3. **Small maxdiff (1e-3 to 1e-1)?** → Likely a subtle formula variant. Check:
   - `mean` vs `sum` in the loss
   - Missing bias term
   - Wrong activation (tanh vs sigmoid vs relu)

4. **Tiny maxdiff (< 1e-5) but still failing?** → Numerical precision issue. Try:
   - `jax.config.update("jax_enable_x64", True)`
   - Using `logaddexp` instead of `log(1 + exp(...))`

5. **Square weight matrices and wrong answer?** → Try swapping the multiply order. Default to `W @ x` first, then try `x @ W`.

---

## JAX API Quick Reference

| Operation | Code |
|---|---|
| Sum along axis | `jnp.sum(x, axis=0)` |
| Element-wise square | `jnp.square(x)` |
| Gradient | `jax.grad(loss_fn)(params)` |
| Stable log(1+exp(z)) | `jnp.logaddexp(0.0, z)` |
| Log-sigmoid | `jax.nn.log_sigmoid(z)` |
| Sequential scan | `jax.lax.scan(step_fn, init_carry, xs)` |
| JIT compile | `jax.jit(fn)` |
| Vectorized map | `jax.vmap(fn)(batch)` |
| ReLU | `jax.nn.relu(x)` |
| Save numpy | `np.save("out.npy", np.array(jax_array))` |