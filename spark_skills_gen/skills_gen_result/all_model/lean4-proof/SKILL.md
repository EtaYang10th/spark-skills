---
id: lean4-proof-induction
title: Lean 4 Proof by Induction (Inequality via Closed Form)
category: lean4-proof
version: 1.0.0
tags: [lean4, induction, inequality, closed-form, mathlib]
---

## Overview

This skill covers proving inequalities about recursively defined sequences in Lean 4. The core pattern: when a direct induction on an inequality yields a weak IH, first prove a closed-form identity, then derive the bound as a corollary.

---

## Module 1: Environment Orientation

Before writing any proof, understand the workspace.

```bash
# Check toolchain version
cat /app/workspace/lean-toolchain

# Find available custom tactics/libraries
find /app/workspace/Library -name "*.lean" 2>/dev/null

# Check the lakefile for build targets
cat /app/workspace/lakefile.lean

# Read the template with line numbers — never modify before line 15
cat -n /app/workspace/solution.lean
```

Key things to confirm:
- What imports are already present (`Mathlib`, custom `Library/Tactic/*`)
- The exact theorem statement and definition of the sequence
- Which tactics are available (`linarith`, `norm_num`, `positivity`, `ring`, `simp`, `induction`)

To typecheck without a full build:
```bash
cd /app/workspace && lake env lean solution.lean 2>&1
```

A clean exit (no output, exit code 0) means success. Prefer `lake env lean` over `lake build` for faster iteration.

---

## Module 2: Proof Strategy — Closed Form Before Inequality

### When direct induction is too weak

If the goal is `S n ≤ C` and the inductive step requires `S (k+1) ≤ C` given only `S k ≤ C`, the IH is often too weak — you can't tighten the bound.

### The fix: prove the exact closed form first

```lean
-- Step 1: prove the closed form as a helper lemma
lemma S_closed_form : ∀ n : ℕ, S n = C - 1 / (base ^ n) := by
  intro n
  induction n with
  | zero => norm_num [S]          -- base case: unfold definition, normalize
  | succ k ih =>
      simp [S, ih]                -- unfold recurrence, substitute IH
      field_simp
      ring                        -- or linarith with arithmetic helpers

-- Step 2: derive the inequality as a corollary
theorem problemsolution : ∀ n : ℕ, S n ≤ C := by
  intro n
  rw [S_closed_form]
  have h : 1 / base ^ n > 0 := by positivity
  linarith
```

### Tactic cheat sheet for this pattern

| Goal type | Tactic |
|---|---|
| Numeric equality after unfolding | `norm_num` |
| Algebraic identity with variables | `ring` |
| Linear arithmetic from hypotheses | `linarith` |
| Positivity of powers/products | `positivity` |
| Simplify with simp lemmas | `simp [defn, ih]` |
| Clear denominators | `field_simp` |

---

## Module 3: Inductive Step Arithmetic

When the recurrence is `S (n+1) = S n + f(n)`, the inductive step typically needs:

```lean
| succ k ih =>
    simp only [S]        -- unfold one step of the recurrence
    rw [ih]              -- substitute the closed form for S k
    -- now goal is purely arithmetic, e.g.:
    -- (C - 1/b^k) + 1/b^(k+1) = C - 1/b^(k+1)
    have key : (1 : ℚ) / b ^ k - 1 / (b * b ^ k) = 1 / (b * b ^ k) := by
      field_simp; ring
    linarith
```

If `linarith` can't close it directly, introduce intermediate `have` steps to break the arithmetic into pieces it can handle.

---

## Common Pitfalls

- Don't modify anything before line 15 in `solution.lean`. The test checks the exact prefix byte-for-byte.

- Don't try to prove `S n ≤ C` by induction directly when the IH `S k ≤ C` gives no information about how far below `C` you are. The closed form `S n = C - ε(n)` is almost always the right intermediate lemma.

- `lake build` is slower and noisier than `lake env lean solution.lean`. Use the latter for iteration; only use `lake build` if you need to verify the full project compiles.

- `linarith` works on linear arithmetic but can't handle nonlinear terms like `1/2^n` symbolically. Introduce `have h : ... > 0 := by positivity` to give it the numeric facts it needs.

- `norm_num` handles concrete numeric goals (base cases). `ring` handles symbolic algebraic identities. Don't swap them — `ring` won't evaluate `2 - 1 = 1`, and `norm_num` won't prove `a * b = b * a` in general.

- After writing the proof, always run `lake env lean solution.lean 2>&1` and confirm zero output before declaring done. Warnings are treated as errors in this environment.
