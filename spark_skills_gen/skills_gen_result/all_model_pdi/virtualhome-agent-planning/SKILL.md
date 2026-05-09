---
title: "PDDL Airport Planning with Unified Planning + Pyperplan"
category: "virtualhome-agent-planning"
domain: "pddl-planning"
tags:
  - pddl
  - planning
  - airport
  - unified-planning
  - pyperplan
  - ipc
dependencies:
  - unified_planning==1.3.0
  - up-pyperplan==1.1.0
  - pyperplan==2.1
  - pyyaml
  - numpy
---

# PDDL Airport Planning — Agent Skill

## Overview

This skill covers solving PDDL planning problems from the IPC Airport domain (and similar domains) using the `unified_planning` Python library with the `pyperplan` backend. The agent must:

1. Read a `problem.json` manifest that lists domain/problem PDDL file pairs.
2. Parse each PDDL domain + problem.
3. Invoke a planner to produce a valid sequential plan.
4. Write the plan in the **exact format** the verifier expects.
5. Validate the plan before finalizing.

The Airport domain models ground traffic control at airports: airplanes move between runway segments, taxiway segments, and parking positions. Actions include `move`, `pushback`, `startup`, `park`, and `takeoff`, with preconditions about segment occupancy, direction, and airplane state.

---

## High-Level Workflow

### Step 1 — Load the task manifest

Read `problem.json` from the working directory. It is a JSON array of task objects, each with keys `id`, `domain`, `problem`, and `plan_output`.

```python
import json, os

PROBLEM_FILE = os.path.join(os.path.dirname(__file__), "problem.json")

with open(PROBLEM_FILE) as f:
    tasks = json.load(f)

for t in tasks:
    print(t["id"], t["domain"], t["problem"], t["plan_output"])
```

### Step 2 — Inspect the PDDL files (understand the domain)

Before planning, read the domain and problem files to understand:
- **Types**: `airplane`, `segment`, `direction`, `airplanetype`
- **Predicates**: `at-segment`, `facing`, `occupied`, `is-moving`, `is-parked`, `is-pushing`, `has-type`, `can-move`, `can-pushback`, `is-start-runway`, `not_occupied`, etc.
- **Actions**: `move`, `pushback`, `startup`, `park`, `takeoff` — each with typed parameters and preconditions about segment connectivity, occupancy, and airplane state.
- **Objects**: Specific airplane names (e.g., `airplane_CFBEG`), segment names (e.g., `seg_rw_0_400`, `seg_tww1_0_200`), directions (`north`, `south`).
- **Goal**: Typically move an airplane to a target segment, or get it airborne.

**Why this matters**: The object names in PDDL are case-sensitive. The planner may lowercase them internally. You must preserve the original casing in the output plan.

```python
# Quick inspection
with open(tasks[0]["domain"]) as f:
    domain_text = f.read()
with open(tasks[0]["problem"]) as f:
    problem_text = f.read()
print(domain_text[:2000])
print(problem_text[:2000])
```

### Step 3 — Parse PDDL and run the planner

Use `unified_planning` to parse the PDDL files and invoke the `pyperplan` engine.

```python
import unified_planning as up
from unified_planning.io import PDDLReader
from unified_planning.shortcuts import OneshotPlanner

reader = PDDLReader()
problem = reader.parse_problem(domain_filename, problem_filename)

with OneshotPlanner(name="pyperplan") as planner:
    result = planner.solve(problem)
    if result.status in (
        up.engines.PlanGenerationResultStatus.SOLVED_SATISFICING,
        up.engines.PlanGenerationResultStatus.SOLVED_OPTIMALLY,
    ):
        plan = result.plan
    else:
        raise RuntimeError(f"Planner failed: {result.status}")
```

### Step 4 — Convert the plan to the correct output format

**This is the most critical step and the #1 source of errors.**

The verifier calls `PDDLReader.parse_plan(problem, plan_file)` and then validates with `PlanValidator`. The `parse_plan` / `parse_plan_string` method expects plans in **parenthesized PDDL format**:

```
(action-name param1 param2 ... paramN)
```

For example:
```
(move airplane_CFBEG seg_rw_0_400 seg_rwe_0_50 south south)
(move airplane_CFBEG seg_rwe_0_50 seg_tww1_0_200 south south)
(park airplane_CFBEG seg_pp_0_60 south)
```

**NOT** the function-call style `move(airplane_CFBEG, seg_rw_0_400, ...)`.

The `unified_planning` plan object stores actions with potentially lowercased names. You must map them back to the **original case** from the PDDL files.

```python
def build_case_map(problem):
    """
    Build a mapping from lowercased object/action names to their original-case
    PDDL names. unified_planning may lowercase everything internally.
    """
    case_map = {}
    # Map fluent objects
    for obj in problem.all_objects:
        name = obj.name
        case_map[name.lower()] = name
    # Map action names
    for action in problem.actions:
        case_map[action.name.lower()] = action.name
    return case_map


def format_plan_line(action, case_map):
    """
    Convert a unified_planning ActionInstance to a parenthesized PDDL plan line
    with original-case names.
    """
    action_name = case_map.get(action.action.name.lower(), action.action.name)
    params = []
    for p in action.actual_parameters:
        pname = str(p)
        params.append(case_map.get(pname.lower(), pname))
    return f"({action_name} {' '.join(params)})"


def write_plan(plan, problem, output_path):
    case_map = build_case_map(problem)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        for action in plan.actions:
            f.write(format_plan_line(action, case_map) + "\n")
```

### Step 5 — Validate the plan before finalizing

Use the same validation logic the test harness uses: `PDDLReader.parse_plan` + `PlanValidator`.

```python
from unified_planning.io import PDDLReader
from unified_planning.engines import PlanValidator

def validate_plan(domain_file, problem_file, plan_file):
    reader = PDDLReader()
    problem = reader.parse_problem(domain_file, problem_file)
    pred_plan = reader.parse_plan(problem, plan_file)
    with PlanValidator(
        problem_kind=problem.kind, plan_kind=pred_plan.kind
    ) as validator:
        result = validator.validate(problem, pred_plan)
    return bool(result)
```

Always run this before declaring success. If validation fails, re-examine the plan format.

### Step 6 — Check plan format constraints

The verifier also checks structural properties:

```python
def check_plan_format(plan_file):
    with open(plan_file) as f:
        lines = [line.strip() for line in f.readlines()]
    for i, line in enumerate(lines):
        assert line, f"Empty line in plan at line {i}"
    for line in lines:
        assert "(" in line and ")" in line, f"Invalid action syntax: {line}"
        assert line.count("(") == 1 and line.count(")") == 1, \
            f"Multiple actions in one line: {line}"
```

Rules:
- One action per line.
- No empty lines.
- Each line has exactly one opening and one closing parenthesis.
- Action names and parameter names must match the PDDL domain/problem exactly (case-sensitive).

---

## Common Pitfalls

### 1. Wrong plan output format (most common failure)

**Symptom**: `parse_plan` raises an error or returns an empty plan; validator says the plan is invalid.

**Cause**: Writing the plan in function-call syntax `action(p1, p2)` instead of parenthesized PDDL syntax `(action p1 p2)`.

**Fix**: Always use `(action-name param1 param2 ... paramN)` — parentheses around the whole action, space-separated, no commas.

### 2. Lowercased object names

**Symptom**: Validator cannot match plan actions to domain actions because `airplane_cfbeg` ≠ `airplane_CFBEG`.

**Cause**: `unified_planning` / `pyperplan` may internally lowercase all names.

**Fix**: Build a case-mapping dictionary from the parsed `problem` object (which retains original names) and restore casing when writing the plan. See `build_case_map()` above.

### 3. Missing output directories

**Symptom**: `FileNotFoundError` when writing the plan file.

**Fix**: Always `os.makedirs(os.path.dirname(output_path), exist_ok=True)` before writing.

### 4. Planner returns no solution

**Symptom**: `result.status` is `UNSOLVABLE` or `TIMEOUT`.

**Cause**: The problem may be large (Munich airport instances can have hundreds of segments). `pyperplan` uses BFS by default, which may be slow.

**Fix**: For small-to-medium instances, `pyperplan` works fine. For very large instances, consider:
- Increasing timeout if the framework supports it.
- Using a different planner backend if available (e.g., `fast-downward`).
- Checking if the problem is actually solvable (inspect init and goal states).

### 5. Confusing `plan.actions` iteration

**Symptom**: Plan actions are in the wrong order or parameters are misread.

**Fix**: `plan.actions` for a `SequentialPlan` is already in execution order. Each `ActionInstance` has `.action.name` (the action schema name) and `.actual_parameters` (a tuple of `FNode` objects). Use `str(param)` to get the parameter name string.

### 6. Not running validation before submitting

**Symptom**: Tests fail on the verifier even though the plan "looks right."

**Fix**: Always run `validate_plan()` in your script. It catches format issues, invalid actions, and unsatisfied preconditions before the test harness does.

---

## Key Libraries and Tools

| Library | Purpose | Notes |
|---------|---------|-------|
| `unified_planning` | PDDL parsing, plan representation, validation | Main orchestration library |
| `up-pyperplan` | Pyperplan engine integration for `unified_planning` | Registers as `"pyperplan"` engine |
| `pyperplan` | Lightweight PDDL planner (BFS, A*, etc.) | Works well for small/medium IPC problems |
| `json` | Reading `problem.json` manifest | Standard library |

### Checking available planners

```python
from unified_planning.shortcuts import get_environment
env = get_environment()
print(env.factory.engines)  # Lists all registered engines
```

---

## Reference Implementation

This is a complete, self-contained script that solves all tasks in a `problem.json` manifest. Copy, adapt the `PROBLEM_FILE` path if needed, and run.

```python
#!/usr/bin/env python3
"""
PDDL Airport Planning Solver
=============================
Reads problem.json, solves each PDDL planning task using unified_planning + pyperplan,
writes plans in the correct parenthesized format, and validates them.

Usage:
    cd /app && python3 solve_airport.py
"""

import json
import os
import sys

import unified_planning as up
from unified_planning.io import PDDLReader
from unified_planning.shortcuts import OneshotPlanner
from unified_planning.engines import PlanValidator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Adjust this path to wherever problem.json lives in your task directory
PROBLEM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "problem.json")


# ---------------------------------------------------------------------------
# Case-mapping utilities
# ---------------------------------------------------------------------------
def build_case_map(problem):
    """
    Build a dict mapping lowercased names -> original-case names.
    Covers all objects and all action names in the parsed PDDL problem.
    unified_planning / pyperplan may lowercase names internally;
    this lets us restore the original casing for plan output.
    """
    case_map = {}
    for obj in problem.all_objects:
        case_map[obj.name.lower()] = obj.name
    for action in problem.actions:
        case_map[action.name.lower()] = action.name
    return case_map


def format_plan_line(action_instance, case_map):
    """
    Convert a unified_planning ActionInstance into a single parenthesized
    PDDL plan line with original-case names.

    Example output: (move airplane_CFBEG seg_rw_0_400 seg_rwe_0_50 south south)
    """
    raw_name = action_instance.action.name
    action_name = case_map.get(raw_name.lower(), raw_name)

    params = []
    for p in action_instance.actual_parameters:
        pstr = str(p)
        params.append(case_map.get(pstr.lower(), pstr))

    return f"({action_name} {' '.join(params)})"


# ---------------------------------------------------------------------------
# Plan writing
# ---------------------------------------------------------------------------
def write_plan(plan, problem, output_path):
    """
    Write a unified_planning SequentialPlan to a file in parenthesized PDDL format.
    Creates parent directories if they don't exist.
    """
    case_map = build_case_map(problem)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        for action_instance in plan.actions:
            f.write(format_plan_line(action_instance, case_map) + "\n")
    print(f"  Written {len(plan.actions)} actions to {output_path}")


# ---------------------------------------------------------------------------
# Plan format check (mirrors the test harness)
# ---------------------------------------------------------------------------
def check_plan_format(plan_file):
    """
    Structural checks on the plan file:
    - No empty lines
    - Each line has exactly one '(' and one ')'
    """
    with open(plan_file) as f:
        lines = [line.strip() for line in f.readlines()]
    for i, line in enumerate(lines):
        if not line:
            raise ValueError(f"Empty line in plan at line {i} in {plan_file}")
    for line in lines:
        if "(" not in line or ")" not in line:
            raise ValueError(f"Invalid action syntax (missing parens): {line}")
        if line.count("(") != 1 or line.count(")") != 1:
            raise ValueError(f"Multiple actions on one line: {line}")
    return True


# ---------------------------------------------------------------------------
# Plan validation (mirrors the test harness)
# ---------------------------------------------------------------------------
def validate_plan(domain_file, problem_file, plan_file):
    """
    Parse the plan file back through PDDLReader and validate it with PlanValidator.
    Returns True if the plan is valid, raises or returns False otherwise.
    """
    reader = PDDLReader()
    problem = reader.parse_problem(domain_file, problem_file)
    parsed_plan = reader.parse_plan(problem, plan_file)

    with PlanValidator(
        problem_kind=problem.kind, plan_kind=parsed_plan.kind
    ) as validator:
        result = validator.validate(problem, parsed_plan)

    return bool(result)


# ---------------------------------------------------------------------------
# Solve a single task
# ---------------------------------------------------------------------------
def solve_task(task):
    """
    Parse domain + problem, invoke pyperplan, write and validate the plan.
    """
    task_id = task["id"]
    domain_file = task["domain"]
    problem_file = task["problem"]
    output_path = task["plan_output"]

    print(f"\n{'='*60}")
    print(f"Solving task: {task_id}")
    print(f"  Domain:  {domain_file}")
    print(f"  Problem: {problem_file}")
    print(f"  Output:  {output_path}")
    print(f"{'='*60}")

    # --- Parse PDDL ---
    reader = PDDLReader()
    problem = reader.parse_problem(domain_file, problem_file)
    print(f"  Parsed problem: {problem.name}")
    print(f"  Objects: {len(list(problem.all_objects))}")
    print(f"  Actions: {[a.name for a in problem.actions]}")

    # --- Solve ---
    with OneshotPlanner(name="pyperplan") as planner:
        result = planner.solve(problem)

    solved_statuses = (
        up.engines.PlanGenerationResultStatus.SOLVED_SATISFICING,
        up.engines.PlanGenerationResultStatus.SOLVED_OPTIMALLY,
    )

    if result.status not in solved_statuses:
        print(f"  ERROR: Planner returned status {result.status}")
        print(f"  Log: {result.log_messages}")
        raise RuntimeError(
            f"Planner failed for task {task_id}: {result.status}"
        )

    plan = result.plan
    print(f"  Plan found with {len(plan.actions)} actions")

    # --- Write plan ---
    write_plan(plan, problem, output_path)

    # --- Validate format ---
    check_plan_format(output_path)
    print("  Format check: PASSED")

    # --- Validate semantics ---
    valid = validate_plan(domain_file, problem_file, output_path)
    if valid:
        print("  Semantic validation: PASSED")
    else:
        print("  Semantic validation: FAILED")
        # Print the plan for debugging
        with open(output_path) as f:
            print("  Plan contents:")
            for line in f:
                print(f"    {line.rstrip()}")
        raise RuntimeError(f"Plan validation failed for task {task_id}")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Load task manifest
    if not os.path.exists(PROBLEM_FILE):
        print(f"ERROR: {PROBLEM_FILE} not found")
        sys.exit(1)

    with open(PROBLEM_FILE) as f:
        tasks = json.load(f)

    print(f"Loaded {len(tasks)} task(s) from {PROBLEM_FILE}")

    # Solve each task
    results = {}
    for task in tasks:
        try:
            solve_task(task)
            results[task["id"]] = "PASSED"
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            results[task["id"]] = f"FAILED: {e}"

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    all_passed = True
    for task_id, status in results.items():
        icon = "✓" if status == "PASSED" else "✗"
        print(f"  {icon} {task_id}: {status}")
        if status != "PASSED":
            all_passed = False

    if all_passed:
        print(f"\nAll {len(tasks)} task(s) solved and validated successfully.")
    else:
        print(f"\nSome tasks failed. See details above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## Supplementary: Understanding the Airport Domain

### Typical action schemas

| Action | Parameters | What it does |
|--------|-----------|--------------|
| `move` | `(airplane, seg_from, seg_to, dir_from, dir_to)` | Move airplane between connected segments |
| `pushback` | `(airplane, seg_from, seg_to, dir_from, dir_to)` | Push airplane backward (from parking) |
| `startup` | `(airplane, segment, direction)` | Start engines (transition from pushing to moving) |
| `park` | `(airplane, segment, direction)` | Park at a segment (transition from moving to parked) |
| `takeoff` | `(airplane, segment, direction)` | Take off from a runway start segment |

### Segment naming conventions

- `seg_rw_*` — runway segments
- `seg_rwe_*` — runway east segments
- `seg_rww_*` — runway west segments
- `seg_tw_*` — taxiway segments
- `seg_twe_*` / `seg_tww_*` — taxiway east/west
- `seg_pp_*` — parking positions

### Typical goal patterns

- **Parking**: Goal is `(at-segment airplane_X seg_pp_Y)` — move from runway/taxiway to parking.
- **Takeoff**: Goal is `(airborne airplane_X)` — pushback from parking, startup, taxi to runway, takeoff.
- **Multi-airplane**: Multiple airplanes need to reach different goals without blocking each other.

---

## Supplementary: Fallback Strategies for Large Instances

If `pyperplan` times out on large instances (e.g., full Munich airport with dozens of airplanes):

1. **Check if a simpler heuristic search is available**:
   ```python
   # pyperplan supports different search algorithms
   # The unified_planning integration may not expose all of them,
   # but you can try calling pyperplan directly
   from pyperplan import planner as pyperplan_planner
   ```

2. **Try solving with a different engine** if installed:
   ```python
   # List available engines
   from unified_planning.shortcuts import get_environment
   env = get_environment()
   for name, engines in env.factory._engines.items():
       print(name, engines)
   ```

3. **Manual plan construction**: For very constrained problems (single airplane, linear path), you can read the PDDL init/goal states and construct the plan by hand using BFS over the segment connectivity graph. This is a last resort.

```python
def extract_connectivity(problem_text):
    """
    Parse can-move and can-pushback predicates from the PDDL problem init
    to build a segment connectivity graph.
    """
    import re
    graph = {}  # (seg, dir) -> [(seg2, dir2, action_type)]

    # Match (can-move seg1 seg2 dir1 dir2)
    for m in re.finditer(
        r'\(can-move\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\)', problem_text
    ):
        seg1, seg2, dir1, dir2 = m.groups()
        key = (seg1, dir1)
        graph.setdefault(key, []).append((seg2, dir2, "move"))

    # Match (can-pushback seg1 seg2 dir1 dir2)
    for m in re.finditer(
        r'\(can-pushback\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\)', problem_text
    ):
        seg1, seg2, dir1, dir2 = m.groups()
        key = (seg1, dir1)
        graph.setdefault(key, []).append((seg2, dir2, "pushback"))

    return graph
```

---

## Quick Checklist for the Agent

Before submitting, verify:

- [ ] `problem.json` loaded and all tasks iterated
- [ ] Each plan file exists at the path specified by `plan_output`
- [ ] Plan format: one action per line, parenthesized `(action p1 p2 ... pN)`, no empty lines
- [ ] Object names match original PDDL casing (not lowercased)
- [ ] `check_plan_format()` passes for each plan file
- [ ] `validate_plan()` returns `True` for each plan file
- [ ] No trailing whitespace or extra newlines that could confuse the parser