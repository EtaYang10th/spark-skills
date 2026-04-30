---
name: airport-pddl-planning
version: 1.0.0
description: Solve airport ground traffic PDDL planning problems using pyperplan's BFS solver
domain: pddl-planning
tags: [pddl, planning, airport, pyperplan, ipc]
---

# Airport PDDL Planning Skill

## Overview

This skill covers solving PDDL planning problems from the airport domain (IPC benchmark suite). Tasks involve controlling ground traffic at airports — taxiing airplanes, parking, pushback, startup, and takeoff. The environment has `pyperplan` installed and ready to use.

---

## High-Level Workflow

### 1. Read `problem.json` to Discover Tasks

Always start here. It tells you the domain file, problem file, and output path for each task.

```python
import json

with open('/app/problem.json') as f:
    tasks = json.load(f)

for task in tasks:
    print(task['id'], task['domain'], task['problem'], task['plan_output'])
```

### 2. Inspect the PDDL Files (Optional but Recommended)

Skim the domain and problem files to understand:
- What actions are available (`:action` blocks)
- Initial state (`:init`)
- Goal state (`:goal`)
- Object names and types

This helps you verify the plan makes sense and catch naming issues early.

```bash
cat /app/airport/domain01.pddl | head -80
cat /app/airport/task01.pddl
```

### 3. Solve Each Task with pyperplan BFS

Use `pyperplan`'s built-in BFS search. It's installed and works out of the box for airport-scale problems. Do NOT attempt to hand-craft plans — use the solver.

```python
from pyperplan import planner
from pyperplan.search import breadth_first_search
from pyperplan.heuristics.blind import BlindHeuristic

def solve_task(domain_path, problem_path, output_path):
    bfs = lambda task, h: breadth_first_search(task)
    solution = planner.search_plan(domain_path, problem_path, bfs, BlindHeuristic)
    if solution is None:
        raise RuntimeError(f"No solution found for {problem_path}")
    planner.write_solution(solution, output_path)
    return solution
```

### 4. Write Plans Using `planner.write_solution`

Always use `planner.write_solution` — it writes in the exact format the test harness expects:
```
(action_name param1 param2 ...)
```

Do NOT write plans manually as plain text like `action_name param1 param2` — the parentheses and lowercase formatting matter.

### 5. Verify Output Files Exist and Are Non-Empty

```python
import os

for task in tasks:
    path = task['plan_output']
    assert os.path.exists(path), f"Missing output: {path}"
    assert os.path.getsize(path) > 0, f"Empty output: {path}"
    with open(path) as f:
        print(f"=== {task['id']} ===")
        print(f.read())
```

### 6. Understand What the Tests Check

The test suite runs two checks:
- `test_all_output_files_exist` — all `plan_output` paths must exist
- `test_allclose[1e-05-1e-06]` — numerical values in the output must match expected values within tight tolerances

The "numerical" check does NOT mean you need to output raw numbers. It means the plan itself (action sequence) must be correct — pyperplan's BFS finds the optimal/valid plan that matches the expected solution. Use the solver; don't guess.

---

## Complete Runnable Solution

```python
import json
import os
from pyperplan import planner
from pyperplan.search import breadth_first_search
from pyperplan.heuristics.blind import BlindHeuristic

def solve_all_tasks(problem_json_path='/app/problem.json'):
    with open(problem_json_path) as f:
        tasks = json.load(f)

    bfs = lambda task, h: breadth_first_search(task)

    for task in tasks:
        task_id = task['id']
        domain_path = task['domain']
        problem_path = task['problem']
        output_path = task['plan_output']

        print(f"Solving {task_id}...")
        print(f"  Domain:  {domain_path}")
        print(f"  Problem: {problem_path}")
        print(f"  Output:  {output_path}")

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True) if os.path.dirname(output_path) else None

        # Solve
        solution = planner.search_plan(domain_path, problem_path, bfs, BlindHeuristic)

        if solution is None:
            raise RuntimeError(f"pyperplan BFS found no solution for {task_id}")

        # Write in correct PDDL plan format: (action_name param1 param2 ...)
        planner.write_solution(solution, output_path)

        # Verify
        assert os.path.exists(output_path), f"Output not written: {output_path}"
        with open(output_path) as f:
            content = f.read().strip()
        assert content, f"Output is empty: {output_path}"

        print(f"  -> {len(solution)} steps written to {output_path}")
        print(content)
        print()

    print(f"All {len(tasks)} tasks solved.")

solve_all_tasks()
```

---

## PDDL Plan Format Reference

pyperplan writes plans in this format (lowercase, parenthesized):

```
(move_seg_rw_0_400_seg_rww_0_50_south_south_medium airplane_cfbeg)
(move_seg_rww_0_50_seg_tww4_0_50_south_north_medium airplane_cfbeg)
(park_seg_pp_0_60_south airplane_cfbeg)
```

Key formatting rules:
- All names are **lowercase** (even if the PDDL problem uses mixed case like `airplane_CFBEG`)
- Each action is wrapped in **parentheses**
- One action per line
- No trailing semicolons or comments

---

## Airport Domain Concepts

Understanding these helps you verify plans make sense:

| Action | Meaning |
|--------|---------|
| `move_<from>_<to>_<dir_from>_<dir_to>_<size>` | Taxi airplane from segment to segment |
| `park_<seg>_<dir>` | Park airplane at a gate/parking position |
| `pushback_<from>_<to>_<dir_from>_<dir_to>_<size>` | Push airplane backward from gate |
| `startup_<seg>_<dir>_<size>` | Start engines after pushback |
| `takeoff_<seg>_<dir>` | Take off from runway segment |

Segments encode location + direction + size in their names (e.g., `seg_rw_0_400` = runway segment, `seg_pp_0_60` = parking position).

---

## Common Pitfalls

### 1. Writing Plans Without Parentheses
Wrong:
```
move_seg_rw_0_400_seg_rww_0_50_south_south_medium airplane_cfbeg
```
Right:
```
(move_seg_rw_0_400_seg_rww_0_50_south_south_medium airplane_cfbeg)
```
Always use `planner.write_solution` — never write action strings manually.

### 2. Using Mixed-Case Object Names
PDDL problem files may define `airplane_CFBEG` but pyperplan normalizes everything to lowercase. The output plan will use `airplane_cfbeg`. This is correct — do not "fix" it back to uppercase.

### 3. Hand-Crafting Plans Instead of Using the Solver
Airport action names encode full path segments and directions. Manually tracing paths is error-prone and slow. pyperplan BFS is fast enough for the IPC airport benchmark instances — always use it.

### 4. Forgetting to Check `plan_output` Directory Exists
If `plan_output` is something like `results/task01.txt`, the `results/` directory may not exist. Create it with `os.makedirs(..., exist_ok=True)` before writing.

### 5. Assuming `test_allclose` Needs Numeric Output
The test name sounds numeric but it validates the plan solution against expected values. The correct approach is to produce a valid plan via the solver — not to output raw numbers or cost metrics.

### 6. Using a Heuristic Search When BFS Suffices
For small-to-medium airport instances (IPC benchmark), BFS with `BlindHeuristic` finds valid plans reliably. Avoid over-engineering with A* or domain-specific heuristics unless BFS times out.

### 7. Not Verifying Output After Writing
Always read back the written file and confirm it's non-empty and well-formed before finishing. A silent write failure (wrong path, permissions) will cause `test_all_output_files_exist` to fail.

---

## Environment Reference

| Tool | Usage |
|------|-------|
| `pyperplan==2.1` | Main PDDL solver — use `planner.search_plan` + `planner.write_solution` |
| `unified_planning==1.3.0` | Alternative planning framework (not needed for basic BFS) |
| `up-pyperplan==1.1.0` | unified_planning bridge to pyperplan |
| `python3` | Runtime |

### pyperplan API Quick Reference

```python
from pyperplan import planner
from pyperplan.search import breadth_first_search, astar_search
from pyperplan.heuristics.blind import BlindHeuristic
from pyperplan.heuristics.lm_cut import LmCutHeuristic  # if BFS is too slow

# Parse only (no solving)
domain = planner._parse(domain_path, problem_path)

# Solve
solution = planner.search_plan(domain_path, problem_path, search_fn, heuristic_class)
# solution is a list of pyperplan Operator objects, or None if unsolvable

# Write
planner.write_solution(solution, output_path)
# Writes: (operator_name)\n per line
```

---

## Debugging Tips

If `search_plan` returns `None`:
1. Check that domain and problem file paths are correct
2. Verify the problem file's `:objects`, `:init`, and `:goal` sections are well-formed
3. Try printing the parsed task to inspect grounded actions:

```python
from pyperplan.pddl.parser import Parser
from pyperplan import grounding

parser = Parser(domain_path, problem_path)
dom = parser.parse_domain()
prob = parser.parse_problem(dom)
task = grounding.ground(prob)
print(f"Actions: {len(task.operators)}")
print(f"Initial facts: {len(task.initial_state)}")
print(f"Goal facts: {len(task.goals)}")
```

If BFS is too slow for large instances, switch to A* with a heuristic:

```python
from pyperplan.search import astar_search
from pyperplan.heuristics.lm_cut import LmCutHeuristic

astar = lambda task, h: astar_search(task, h)
solution = planner.search_plan(domain_path, problem_path, astar, LmCutHeuristic)
```
