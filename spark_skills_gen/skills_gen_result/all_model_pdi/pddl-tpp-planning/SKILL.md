---
title: "PDDL TPP Planning — Solving Travelling Purchase Problems with unified-planning + pyperplan"
category: pddl-tpp-planning
tags:
  - pddl
  - planning
  - tpp
  - pyperplan
  - unified-planning
  - automated-planning
version: 1
---

# PDDL TPP Planning Skill

Solve Travelling Purchase Problem (TPP) tasks expressed in PDDL by parsing domain/problem files, invoking a planner, and producing output in the exact format expected by the verifier.

## Why This Skill Exists

The verifier for these tasks does **not** simply validate your plan against the PDDL semantics. Instead, it **re-solves the problem itself** using `OneshotPlanner(name="pyperplan")` from the `unified-planning` library and compares the result to your saved output via **exact string matching** on the action list. This means you must replicate the verifier's exact code path — same library, same planner, same serialization — or you will fail even with a semantically correct plan.

---

## High-Level Workflow

1. **Read `problem.json`** — Parse the task manifest to get domain file paths, problem file paths, and output paths for each task.
2. **Parse each PDDL problem** — Use `unified_planning.io.PDDLReader` to load the domain + problem into a UPF `Problem` object.
3. **Solve with pyperplan** — Use `OneshotPlanner(name="pyperplan")` to solve. This is the same planner the verifier uses, so the result is deterministic and will match on re-solve.
4. **Write the `.txt` plan file** — One action per line, using `str(action)` from the UPF plan object. The format is `action_name(arg1, arg2, ...)`.
5. **Write the `.pkl` plan file** — Pickle the `result.plan` object directly. The verifier unpickles this and compares `[str(a) for a in plan.actions]` against its own fresh solve.
6. **Self-check** — Before finalizing, re-load the `.pkl`, re-solve the problem, and compare action string lists yourself. This catches serialization mismatches early.
7. **Run the test suite** — Execute `pytest` against the provided test file to confirm both existence and numerical/string correctness checks pass.

---

## Step 1: Read the Task Manifest

```python
import json
from pathlib import Path

def load_tasks(problem_json_path: str = "/app/problem.json") -> list[dict]:
    """Load task definitions from problem.json."""
    with open(problem_json_path, "r") as f:
        tasks = json.load(f)
    # Each task has: id, domain, problem, plan_output
    return tasks
```

The `plan_output` field gives the path for the `.txt` file. The `.pkl` file uses the same stem with a `.pkl` extension. Always derive the `.pkl` path programmatically:

```python
def pkl_path_from_txt(txt_path: str) -> str:
    return str(Path(txt_path).with_suffix(".pkl"))
```

---

## Step 2: Parse PDDL Files with unified-planning

```python
from unified_planning.io import PDDLReader

def parse_pddl(domain_path: str, problem_path: str):
    """Parse a PDDL domain + problem into a UPF Problem object."""
    reader = PDDLReader()
    problem = reader.parse_problem(domain_path, problem_path)
    return problem
```

Key notes:
- `PDDLReader` handles both the domain and problem in a single call.
- The returned `Problem` object contains all types, objects, predicates, actions, init state, and goal.
- Do **not** use any other PDDL parser (e.g., `pddl` library, custom regex). The verifier uses `PDDLReader`, so you must too.

---

## Step 3: Solve with pyperplan via OneshotPlanner

```python
from unified_planning.shortcuts import OneshotPlanner

def solve_problem(problem) -> "up.plans.Plan":
    """Solve a UPF Problem using pyperplan and return the plan."""
    with OneshotPlanner(name="pyperplan") as planner:
        result = planner.solve(problem)
    
    if result.plan is None:
        raise RuntimeError(
            f"Planner returned no plan. Status: {result.status}"
        )
    return result.plan
```

Critical details:
- You **must** use `name="pyperplan"`. Other planners (fast-downward, etc.) may produce different but equally valid plans — the verifier will reject them because the action sequences won't match.
- `pyperplan` is deterministic for a given problem, so re-solving always yields the same plan.
- The `result.plan.actions` list contains `ActionInstance` objects. `str(action_instance)` produces the canonical string form like `drive(truck1, depot1, market1)`.

---

## Step 4: Write the `.txt` Plan File

```python
def write_plan_txt(plan, output_path: str):
    """Write plan actions to a text file, one action per line."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for action in plan.actions:
            f.write(str(action) + "\n")
```

Format rules:
- One action per line.
- Each line looks like: `action_name(param1, param2, ...)`
- No leading/trailing whitespace on lines.
- File ends with a newline after the last action.
- Action names and parameter names come directly from `str()` on the UPF `ActionInstance` — do **not** reformat them.

---

## Step 5: Write the `.pkl` Plan File

```python
import pickle

def write_plan_pkl(plan, pkl_output_path: str):
    """Pickle the plan object for verifier comparison."""
    Path(pkl_output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(pkl_output_path, "wb") as f:
        pickle.dump(plan, f)
```

This is the most critical step. The verifier does:

```python
with open(pkl_path, "rb") as f:
    pred_plan = pickle.load(f)
pred_actions = [str(i) for i in pred_plan.actions]
# ... compares against fresh solve ...
```

So the pickled object must be the **exact `result.plan` object** returned by `OneshotPlanner(name="pyperplan").solve()`. Do not pickle a list of strings, a custom object, or a plan from a different solver.

---

## Step 6: Self-Check Before Finalizing

```python
def self_check(domain_path: str, problem_path: str, pkl_path: str) -> bool:
    """Replicate the verifier's comparison logic as a self-check."""
    # Re-solve
    problem = parse_pddl(domain_path, problem_path)
    fresh_plan = solve_problem(problem)
    fresh_actions = [str(a) for a in fresh_plan.actions]
    
    # Load pickled plan
    with open(pkl_path, "rb") as f:
        saved_plan = pickle.load(f)
    saved_actions = [str(a) for a in saved_plan.actions]
    
    if fresh_actions != saved_actions:
        print(f"MISMATCH for {pkl_path}!")
        print(f"  Fresh: {fresh_actions}")
        print(f"  Saved: {saved_actions}")
        return False
    
    print(f"OK: {pkl_path} — {len(fresh_actions)} actions match")
    return True
```

Always run this before declaring success. If it fails, something went wrong in serialization or you used a different planner/parser.

---

## Step 7: Run the Test Suite

```bash
pip install pytest -q --break-system-packages
cd /app && python3 -m pytest /tests/test_outputs.py -v
```

The test suite typically has two checks:
1. **`TestOutputFilesExist`** — All `.txt` and `.pkl` files exist at the expected paths.
2. **`TestNumericalCorrectness`** — The pickled plan matches a fresh pyperplan solve (exact string comparison of action lists).

Use `--break-system-packages` on Ubuntu 24.04+ where PEP 668 blocks global pip installs.

---

## Understanding the TPP Domain

The Travelling Purchase Problem involves:
- **Trucks** that drive between locations (depots and markets).
- **Goods** available at specific markets at certain price levels.
- **Depots** where goods must be delivered.
- **Level predicates** that track inventory on trucks and stock at markets (e.g., `level0`, `level1`, `level2`).

Typical actions:
| Action | Description |
|--------|-------------|
| `drive(truck, from, to)` | Move a truck between connected locations |
| `buy(truck, goods, market, level_before, level_after, ...)` | Purchase goods at a market |
| `load(goods, truck, market, ...)` | Load purchased goods onto a truck |
| `unload(goods, truck, depot, ...)` | Unload goods at a depot |

The level parameters encode quantity changes (e.g., truck goes from `level0` to `level1` of a good). The planner handles all of this automatically — you do not need to reason about levels manually.

---

## Common Pitfalls

### 1. Writing Your Own Planner or Using a Different Solver
**Symptom:** Plan is semantically valid but tests fail on `TestNumericalCorrectness`.
**Cause:** The verifier compares action strings from your `.pkl` against a fresh `pyperplan` solve. Even a correct plan with actions in a different order or with different (but valid) choices will fail.
**Fix:** Always use `OneshotPlanner(name="pyperplan")`. Never hand-craft plans or use alternative solvers.

### 2. Pickling the Wrong Object
**Symptom:** `TestNumericalCorrectness` fails; unpickling raises an error or produces mismatched actions.
**Cause:** You pickled a list of strings, a dict, or a plan from a different code path instead of the raw `result.plan` object.
**Fix:** Pickle `result.plan` directly — the exact object returned by `planner.solve()`.

### 3. Forgetting the `.pkl` File
**Symptom:** `TestOutputFilesExist` fails.
**Cause:** You only wrote the `.txt` file. The verifier also expects a `.pkl` file at the same stem.
**Fix:** Always write both `.txt` and `.pkl` for every task.

### 4. pip Install Failures on Ubuntu 24.04
**Symptom:** `pip install` fails with PEP 668 error about externally managed environment.
**Fix:** Use `--break-system-packages` flag, or create a venv. For quick agent tasks, the flag is simpler.

### 5. Path Derivation Errors
**Symptom:** Files written to wrong location; tests can't find them.
**Cause:** Hardcoding paths or misinterpreting `plan_output` from `problem.json`.
**Fix:** Use `problem.json` paths exactly as given. Derive `.pkl` path by replacing the `.txt` suffix. Create parent directories with `mkdir(parents=True, exist_ok=True)`.

### 6. Not Running Self-Check
**Symptom:** You think everything is fine, but the test fails.
**Fix:** Always run the self-check (Step 6) before running the official test suite. It replicates the verifier's exact comparison logic and catches mismatches immediately.

### 7. Assuming the Plan Format from Examples
**Symptom:** You format actions manually (e.g., adding parentheses or changing spacing) and the string comparison fails.
**Fix:** Use `str(action_instance)` from the UPF library. It produces the canonical format. Do not post-process or reformat.

---

## Reference Implementation

This is a complete, end-to-end script. Copy it, place it at `/app/solve.py`, and run with `python3 /app/solve.py`. It reads `problem.json`, solves all tasks, writes all outputs, and self-checks.

```python
#!/usr/bin/env python3
"""
Complete PDDL TPP solver that replicates the verifier's exact code path.

Usage:
    python3 solve.py

Reads /app/problem.json, solves each task with unified-planning + pyperplan,
writes .txt and .pkl output files, and runs self-checks.
"""

import json
import pickle
from pathlib import Path

# --- unified-planning imports ---
from unified_planning.io import PDDLReader
from unified_planning.shortcuts import OneshotPlanner


# ============================================================
# Configuration
# ============================================================
PROBLEM_JSON_PATH = "/app/problem.json"


# ============================================================
# Core Functions
# ============================================================

def load_tasks(problem_json_path: str) -> list:
    """Load task definitions from problem.json."""
    with open(problem_json_path, "r") as f:
        tasks = json.load(f)
    return tasks


def parse_pddl(domain_path: str, problem_path: str):
    """Parse PDDL domain + problem into a unified-planning Problem."""
    reader = PDDLReader()
    problem = reader.parse_problem(domain_path, problem_path)
    return problem


def solve_problem(problem):
    """Solve with pyperplan via unified-planning's OneshotPlanner."""
    with OneshotPlanner(name="pyperplan") as planner:
        result = planner.solve(problem)

    if result.plan is None:
        raise RuntimeError(
            f"Planner failed to find a plan. Status: {result.status}"
        )
    return result.plan


def write_plan_txt(plan, output_path: str):
    """Write plan to text file — one action per line."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for action in plan.actions:
            f.write(str(action) + "\n")


def write_plan_pkl(plan, pkl_output_path: str):
    """Pickle the plan object (must be the raw result.plan from pyperplan)."""
    Path(pkl_output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(pkl_output_path, "wb") as f:
        pickle.dump(plan, f)


def pkl_path_from_txt(txt_path: str) -> str:
    """Derive .pkl path from .txt path."""
    return str(Path(txt_path).with_suffix(".pkl"))


def self_check(domain_path: str, problem_path: str, pkl_path: str) -> bool:
    """
    Replicate the verifier's comparison logic:
    1. Re-solve the problem from scratch with pyperplan.
    2. Load the pickled plan.
    3. Compare action string lists for exact equality.
    """
    # Fresh solve
    problem = parse_pddl(domain_path, problem_path)
    fresh_plan = solve_problem(problem)
    fresh_actions = [str(a) for a in fresh_plan.actions]

    # Load saved plan
    with open(pkl_path, "rb") as f:
        saved_plan = pickle.load(f)
    saved_actions = [str(a) for a in saved_plan.actions]

    if fresh_actions != saved_actions:
        print(f"  SELF-CHECK FAILED for {pkl_path}")
        print(f"    Fresh actions ({len(fresh_actions)}): {fresh_actions[:3]}...")
        print(f"    Saved actions ({len(saved_actions)}): {saved_actions[:3]}...")
        return False

    print(f"  SELF-CHECK OK: {pkl_path} — {len(fresh_actions)} actions match")
    return True


# ============================================================
# Main Orchestration
# ============================================================

def main():
    print(f"Loading tasks from {PROBLEM_JSON_PATH}")
    tasks = load_tasks(PROBLEM_JSON_PATH)
    print(f"Found {len(tasks)} task(s)\n")

    all_ok = True

    for task in tasks:
        task_id = task["id"]
        domain_path = task["domain"]
        problem_path = task["problem"]
        txt_output = task["plan_output"]
        pkl_output = pkl_path_from_txt(txt_output)

        print(f"--- Task: {task_id} ---")
        print(f"  Domain:  {domain_path}")
        print(f"  Problem: {problem_path}")
        print(f"  Output:  {txt_output}  /  {pkl_output}")

        # Step 1: Parse
        print("  Parsing PDDL...")
        problem = parse_pddl(domain_path, problem_path)

        # Step 2: Solve
        print("  Solving with pyperplan...")
        plan = solve_problem(problem)
        print(f"  Plan found: {len(plan.actions)} actions")

        # Step 3: Write .txt
        write_plan_txt(plan, txt_output)
        print(f"  Wrote {txt_output}")

        # Step 4: Write .pkl
        write_plan_pkl(plan, pkl_output)
        print(f"  Wrote {pkl_output}")

        # Step 5: Print plan for inspection
        print("  Plan actions:")
        for action in plan.actions:
            print(f"    {action}")

        # Step 6: Self-check
        ok = self_check(domain_path, problem_path, pkl_output)
        if not ok:
            all_ok = False

        print()

    # Summary
    if all_ok:
        print("ALL TASKS SOLVED AND SELF-CHECKED SUCCESSFULLY")
    else:
        print("WARNING: Some self-checks failed — review output above")
        exit(1)


if __name__ == "__main__":
    main()
```

---

## Running the Full Pipeline

```bash
# 1. Solve all tasks (writes .txt and .pkl files, runs self-checks)
cd /app && python3 solve.py

# 2. Install pytest if needed (Ubuntu 24.04 requires --break-system-packages)
pip install pytest -q --break-system-packages

# 3. Run the official test suite
cd /app && python3 -m pytest /tests/test_outputs.py -v
```

Expected output:
```
../tests/test_outputs.py::TestOutputFilesExist::test_all_output_files_exist PASSED
../tests/test_outputs.py::TestNumericalCorrectness::test_allclose[1e-05-1e-06] PASSED
```

---

## Environment Notes

| Component | Details |
|-----------|---------|
| OS | Ubuntu 24.04 |
| Python | 3.12 |
| `unified-planning` | 1.3.0 (provides `PDDLReader`, `OneshotPlanner`) |
| `pyperplan` | 2.1 (the actual search engine) |
| `up-pyperplan` | 1.1.0 (bridge between unified-planning and pyperplan) |
| pip caveat | Use `--break-system-packages` for global installs |

All three planning packages (`unified-planning`, `pyperplan`, `up-pyperplan`) are pre-installed. No additional installation is needed for the core solve logic.

---

## Key Insight (TL;DR)

The verifier doesn't check plan validity against PDDL semantics — it **re-solves with pyperplan and compares action strings**. Your only job is to run the same solver, save the same plan object, and write the text representation. Don't overthink the planning; let pyperplan do the work. Focus on getting the I/O exactly right.