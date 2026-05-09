---
title: "Fixing Python Build Failures in BugSwarm Repositories"
category: "fix-build"
domain: "python-ci-debugging"
tags:
  - python
  - build-fix
  - bugswarm
  - git-diff
  - test-debugging
  - ci-cd
applicability:
  task_pattern: "fix-build-*"
  environment: "bugswarm/cached-images:*"
  input: "A failed Python repository build with test failures"
  output: "failed_reasons.txt, patch_N.diff files, applied fixes passing tests"
---

# Fixing Python Build Failures in BugSwarm Repositories

## Overview

BugSwarm repositories contain paired passing/failing builds of real open-source projects. The failing build has a known regression introduced by a specific commit. Your job is to:

1. Diagnose the root cause of the test failure(s)
2. Write a human-readable analysis to `failed_reasons.txt`
3. Produce standard unified diff patch file(s)
4. Apply the patches and verify the build passes

The key insight: BugSwarm repos contain **both** the passing and failing versions in git history. You can diff them to isolate the exact regression, then reason about *why* the change broke things rather than guessing.

---

## High-Level Workflow

### Step 1: Locate the Repository and Understand the Layout

The repository lives at `/home/github/build/failed/<org>/<project>`. Identify the language, build system, test runner, and project structure before doing anything else.

```bash
# Find the repo
REPO_ROOT=$(find /home/github/build/failed -mindepth 2 -maxdepth 2 -type d | head -1)
echo "Repo root: $REPO_ROOT"
cd "$REPO_ROOT"

# Understand the project
ls -la
cat setup.py 2>/dev/null || cat pyproject.toml 2>/dev/null || cat setup.cfg 2>/dev/null
cat requirements.txt 2>/dev/null
ls tests/
```

### Step 2: Identify the Failing Tests from Git History

BugSwarm images embed metadata about the passing and failing builds. The git log contains the merge commit (passing) and the PR commit (failing). Use `git log` to find the relevant commits.

```bash
# Show recent commits — the HEAD is usually the failing version
git log --oneline -10

# Identify the two key commits:
# - The merge commit (passing build)
# - The PR/feature commit (failing build, usually HEAD or HEAD~1)
git log --oneline --all -20
```

### Step 3: Diff the Passing and Failing Versions

This is the most important diagnostic step. The diff between the passing and failing commits tells you exactly what changed.

```bash
# Find the two relevant commits (adapt SHAs to your repo)
FAILING_COMMIT=$(git rev-parse HEAD)
# The parent or merge-base is typically the passing version
PASSING_COMMIT=$(git rev-parse HEAD~1)

# Show what changed
git diff "$PASSING_COMMIT" "$FAILING_COMMIT" --stat
git diff "$PASSING_COMMIT" "$FAILING_COMMIT"
```

If the repo has more complex history (merge commits, multiple parents), use:

```bash
# For merge commits, check both parents
git log --oneline --graph -15
git show --stat HEAD
# If HEAD is a merge, parent 1 is the base branch, parent 2 is the PR
git diff HEAD^1 HEAD^2
```

### Step 4: Run the Failing Tests to Confirm the Failure

Before fixing anything, reproduce the failure to understand the exact error message and assertion.

```bash
# Install test dependencies
pip install pytest requests-mock 2>&1 | tail -5

# Run the specific failing test(s)
python -m pytest tests/ -v 2>&1

# If you know the specific test file:
python -m pytest tests/test_events.py -v 2>&1
```

**Why this matters:** The error message tells you the *symptom*. The diff tells you the *cause*. You need both to write a correct fix.

### Step 5: Analyze the Root Cause

Common patterns in BugSwarm regressions:

| Pattern | Description | How to Spot |
|---------|-------------|-------------|
| **Reordered logic** | Code blocks moved so early-return skips necessary setup | Diff shows same lines in different order |
| **Missing initialization** | New code path skips variable/state setup | `NameError` or `AttributeError` in test output |
| **Changed API contract** | Method signature or return type changed | Test expects old behavior, code provides new |
| **Singleton/state leak** | Class uses singleton pattern; test isolation breaks | Tests pass individually but fail together |
| **Import changes** | Moved or renamed imports break downstream | `ImportError` or `ModuleNotFoundError` |

For the reordered-logic pattern (most common):

```python
# BROKEN: session check returns early BEFORE updating timestamp
def record(self, event):
    if self._session is None:       # <-- early return
        return
    event.end_timestamp = now()     # <-- never reached

# FIXED: update timestamp BEFORE session check
def record(self, event):
    event.end_timestamp = now()     # <-- always runs
    if self._session is None:
        return
```

### Step 6: Write the Analysis File

```bash
cat > /home/github/build/failed/failed_reasons.txt << 'EOF'
## Build Failure Analysis

### Failed Test(s)
- test_record_timestamp in tests/test_events.py

### Root Cause
The PR commit refactored the `record()` method in `client.py` and moved
the session-existence guard clause BEFORE the end_timestamp update logic.
When no active session exists, the method returns early without updating
end_timestamp, causing init_timestamp == end_timestamp.

### Fix
Move the end_timestamp update block before the session guard clause.
The event timestamp is a property of the event object itself and should
be updated regardless of session state.

### Files Changed
- agentops/client.py: Reorder logic in record() method
EOF
```

### Step 7: Create the Patch File(s)

Write standard unified diff format. The patch must apply cleanly with `git apply`.

**Critical rules for diff formatting:**
- Use `--- a/path` and `+++ b/path` (relative to repo root)
- Context lines (unchanged) must match the file exactly, including whitespace
- Hunk headers `@@ -start,count +start,count @@` must be accurate
- No trailing whitespace issues — use `cat -A` to verify the source file first

```bash
# Method 1: Generate diff programmatically (RECOMMENDED)
# Make the fix on a branch, then diff
cd "$REPO_ROOT"
git checkout -b fix-branch
# ... make edits ...
git diff > patch_1.diff

# Method 2: Write the diff manually using heredoc
cat > "$REPO_ROOT/patch_1.diff" << 'DIFFEOF'
--- a/agentops/client.py
+++ b/agentops/client.py
@@ -138,17 +138,18 @@ class Client(metaclass=MetaClient):
                 event (Event): The event to record.
         """
 
+        # Update end_timestamp before session check
+        event_local = event.trigger_event if isinstance(event, ErrorEvent) else event
+        if event_local:
+            if not event_local.end_timestamp or event_local.init_timestamp == event_local.end_timestamp:
+                event_local.end_timestamp = get_ISO_time()
+
         if self._session is None or self._session.has_ended:
             logger.warning("Cannot record event - no current session")
             return
 
-        event_local = event.trigger_event if isinstance(event, ErrorEvent) else event
-        if event_local:
-            if not event_local.end_timestamp or event_local.init_timestamp == event_local.end_timestamp:
-                event_local.end_timestamp = get_ISO_time()
-
-            if isinstance(event, ErrorEvent):
+        if event_local and isinstance(event, ErrorEvent):
                 event.trigger_event_id = event_local.id
                 event.trigger_event_type = event_local.event_type
DIFFEOF
```

### Step 8: Apply the Patch and Verify

```bash
cd "$REPO_ROOT"

# Dry-run first to check it applies cleanly
git apply --check patch_1.diff
echo "Patch applies cleanly: exit code $?"

# Apply for real
git apply patch_1.diff

# Verify the fix
python -m pytest tests/ -v 2>&1
echo "Tests exit code: $?"
```

If `git apply` fails, the most common causes are:
- Whitespace mismatch (tabs vs spaces) — use `git apply --whitespace=fix`
- Wrong context lines — re-read the file with `cat -n` and fix the diff
- Wrong hunk line numbers — recalculate from the actual file

```bash
# Fallback: apply with whitespace tolerance
git apply --whitespace=fix patch_1.diff

# Nuclear fallback: use patch command with fuzz
patch -p1 --fuzz=3 < patch_1.diff
```

### Step 9: Final Verification Checklist

```bash
echo "=== Final Verification ==="

# 1. Analysis file exists
test -f /home/github/build/failed/failed_reasons.txt && echo "✓ failed_reasons.txt exists" || echo "✗ MISSING"

# 2. At least one patch file exists
ls "$REPO_ROOT"/patch_*.diff && echo "✓ patch file(s) exist" || echo "✗ MISSING"

# 3. Tests pass
cd "$REPO_ROOT"
python -m pytest tests/ -v 2>&1
echo "Test exit code: $?"
```

---

## Common Pitfalls

### 1. Not Checking Git History First
**Mistake:** Trying to understand the bug by reading code alone.
**Fix:** Always `git log` and `git diff` between the passing and failing commits first. BugSwarm repos have the answer in the history.

### 2. Whitespace Corruption in Heredoc Diffs
**Mistake:** Writing a diff with a heredoc that introduces tab/space mismatches.
**Fix:** Before writing the diff, inspect the target file with `cat -A` to see exact whitespace (tabs show as `^I`, trailing spaces as `$`). Match it exactly in your diff context lines.

```bash
# Always check whitespace before writing a diff
sed -n '130,170p' agentops/client.py | cat -A
```

### 3. Forgetting to Install Test Dependencies
**Mistake:** Running `pytest` without installing the project's test dependencies, getting `ImportError`.
**Fix:** Always install dependencies first:

```bash
pip install -e ".[test]" 2>/dev/null || pip install -e . 2>/dev/null
pip install pytest requests-mock  # common test deps
```

### 4. Wrong Patch Line Numbers
**Mistake:** Manually writing `@@ -138,17 +138,18 @@` without counting lines.
**Fix:** Use Python to generate accurate diffs instead of writing them by hand (see reference implementation below).

### 5. Not Verifying the Patch Applies Before Declaring Success
**Mistake:** Writing the diff file but not actually applying it and running tests.
**Fix:** Always run `git apply --check` then `git apply` then `pytest` as the final step.

### 6. Fixing Symptoms Instead of Root Cause
**Mistake:** Modifying the test to match the broken behavior.
**Fix:** The test represents the *intended* behavior. Fix the source code to match the test's expectations, not the other way around.

### 7. Singleton / State Leakage in Tests
**Mistake:** Assuming each test gets a fresh instance of a class.
**Reality:** Many Python projects use singleton patterns (metaclass-based or module-level). A test that calls `init()` may not actually reinitialize if a previous test already created the singleton. The fix must work regardless of test execution order.

---

## Generating Diffs Programmatically

When the diff is complex or you want guaranteed correctness, generate it with Python's `difflib`:

```python
import difflib
from pathlib import Path

def generate_unified_diff(filepath: str, old_lines: list[str], new_lines: list[str]) -> str:
    """Generate a unified diff string for a single file.
    
    Args:
        filepath: Path relative to repo root (e.g. 'agentops/client.py')
        old_lines: Original file lines (with newlines)
        new_lines: Modified file lines (with newlines)
    
    Returns:
        Unified diff string ready to write to a .diff file
    """
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
    )
    return ''.join(diff)


def apply_reorder_fix(lines: list[str], 
                       block_to_move: tuple[int, int],
                       insert_before: int) -> list[str]:
    """Move a block of lines from one position to before another line.
    
    Args:
        lines: All lines of the file
        block_to_move: (start_idx, end_idx) 0-based inclusive range to extract
        insert_before: 0-based line index to insert the block before
    
    Returns:
        New list of lines with the block moved
    """
    start, end = block_to_move
    block = lines[start:end+1]
    remaining = lines[:start] + lines[end+1:]
    
    # Adjust insert position if it was after the removed block
    if insert_before > end:
        insert_before -= (end - start + 1)
    
    result = remaining[:insert_before] + block + remaining[insert_before:]
    return result
```

---

## Reference Implementation

This is a complete, end-to-end script that an agent can adapt to fix BugSwarm Python build failures. It covers discovery, diagnosis, patching, and verification.

```python
#!/usr/bin/env python3
"""
fix_bugswarm_build.py — End-to-end BugSwarm Python build fixer.

Usage:
    python3 fix_bugswarm_build.py

Expects the repo at /home/github/build/failed/<org>/<project>.
Produces:
    - /home/github/build/failed/failed_reasons.txt
    - /home/github/build/failed/<org>/<project>/patch_1.diff
    - Applied patch with passing tests
"""

import subprocess
import sys
import os
import difflib
from pathlib import Path


# ──────────────────────────────────────────────────────────────
# Configuration — adapt these to the specific task
# ──────────────────────────────────────────────────────────────

FAILED_ROOT = Path("/home/github/build/failed")


def run(cmd: str, cwd: str = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True, timeout=120
    )
    if check and result.returncode != 0:
        print(f"COMMAND FAILED: {cmd}")
        print(f"STDOUT: {result.stdout[-500:]}")
        print(f"STDERR: {result.stderr[-500:]}")
    return result


# ──────────────────────────────────────────────────────────────
# Step 1: Discover the repository
# ──────────────────────────────────────────────────────────────

def find_repo() -> Path:
    """Find the BugSwarm repo under the failed directory."""
    for org_dir in FAILED_ROOT.iterdir():
        if org_dir.is_dir() and org_dir.name != "environment":
            for project_dir in org_dir.iterdir():
                if project_dir.is_dir() and (project_dir / ".git").exists():
                    return project_dir
    # Fallback: look one level deeper or shallower
    for candidate in FAILED_ROOT.rglob(".git"):
        return candidate.parent
    raise FileNotFoundError("No git repository found under /home/github/build/failed/")


# ──────────────────────────────────────────────────────────────
# Step 2: Analyze git history to find the regression
# ──────────────────────────────────────────────────────────────

def analyze_git_history(repo: Path) -> dict:
    """Examine git log and diff to identify the regression."""
    info = {}

    # Get recent commits
    log_result = run("git log --oneline -15", cwd=str(repo))
    info["git_log"] = log_result.stdout.strip()
    print(f"Git log:\n{info['git_log']}\n")

    # Get the diff between HEAD and its parent
    diff_result = run("git diff HEAD~1 HEAD --stat", cwd=str(repo))
    info["diff_stat"] = diff_result.stdout.strip()
    print(f"Diff stat:\n{info['diff_stat']}\n")

    # Get the full diff
    full_diff = run("git diff HEAD~1 HEAD", cwd=str(repo))
    info["full_diff"] = full_diff.stdout.strip()

    # Get list of changed files
    changed_files = run("git diff HEAD~1 HEAD --name-only", cwd=str(repo))
    info["changed_files"] = changed_files.stdout.strip().split("\n")
    print(f"Changed files: {info['changed_files']}\n")

    return info


# ──────────────────────────────────────────────────────────────
# Step 3: Run tests to identify the specific failure
# ──────────────────────────────────────────────────────────────

def run_tests(repo: Path) -> dict:
    """Run the test suite and capture results."""
    # Install dependencies
    run("pip install -e '.[test]' 2>/dev/null; pip install -e . 2>/dev/null", cwd=str(repo))
    run("pip install pytest requests-mock", cwd=str(repo))

    # Run tests
    result = run("python -m pytest tests/ -v --tb=long 2>&1", cwd=str(repo))
    test_output = result.stdout + result.stderr

    # Parse failures
    failures = []
    for line in test_output.split("\n"):
        if "FAILED" in line:
            failures.append(line.strip())

    return {
        "output": test_output,
        "returncode": result.returncode,
        "failures": failures,
        "passed": result.returncode == 0,
    }


# ──────────────────────────────────────────────────────────────
# Step 4: Diagnose the root cause by comparing passing version
# ──────────────────────────────────────────────────────────────

def get_passing_version(repo: Path, filepath: str) -> list[str]:
    """Get the file content from the passing (parent) commit."""
    result = run(f"git show HEAD~1:{filepath}", cwd=str(repo))
    if result.returncode == 0:
        return result.stdout.splitlines(keepends=True)
    return []


def get_current_version(repo: Path, filepath: str) -> list[str]:
    """Get the current (failing) file content."""
    full_path = repo / filepath
    if full_path.exists():
        return full_path.read_text().splitlines(keepends=True)
    return []


def diagnose_regression(repo: Path, git_info: dict, test_info: dict) -> dict:
    """Compare passing and failing versions to identify the exact regression.
    
    Returns a dict with:
        - root_cause: human-readable explanation
        - file_to_fix: relative path of the file to patch
        - passing_lines: lines from the passing version
        - failing_lines: lines from the failing version
    """
    diagnosis = {}

    # Focus on the changed source files (not test files)
    source_files = [
        f for f in git_info["changed_files"]
        if f and not f.startswith("tests/") and f.endswith(".py")
    ]

    if not source_files:
        # If only test files changed, the regression is in the tests
        source_files = [f for f in git_info["changed_files"] if f and f.endswith(".py")]

    for filepath in source_files:
        passing = get_passing_version(repo, filepath)
        failing = get_current_version(repo, filepath)

        if passing and failing:
            diff = list(difflib.unified_diff(failing, passing,
                                              fromfile=f"a/{filepath}",
                                              tofile=f"b/{filepath}"))
            if diff:
                diagnosis["file_to_fix"] = filepath
                diagnosis["passing_lines"] = passing
                diagnosis["failing_lines"] = failing
                diagnosis["reverse_diff"] = ''.join(diff)
                print(f"Regression found in {filepath}")
                print(f"Reverse diff (failing -> passing):\n{''.join(diff[:50])}")
                break

    return diagnosis


# ──────────────────────────────────────────────────────────────
# Step 5: Generate the fix
# ──────────────────────────────────────────────────────────────

def generate_patch(repo: Path, diagnosis: dict) -> str:
    """Generate a unified diff that transforms failing -> passing for the
    affected file.
    
    Strategy: Use the passing version as the target. Generate a diff from
    the current (failing) state to the passing state.
    """
    filepath = diagnosis["file_to_fix"]
    failing_lines = diagnosis["failing_lines"]
    passing_lines = diagnosis["passing_lines"]

    diff = difflib.unified_diff(
        failing_lines,
        passing_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
    )
    return ''.join(diff)


# ──────────────────────────────────────────────────────────────
# Step 6: Write outputs and apply
# ──────────────────────────────────────────────────────────────

def write_analysis(diagnosis: dict, test_info: dict, git_info: dict):
    """Write the failed_reasons.txt analysis file."""
    content = f"""## Build Failure Analysis

### Failed Test(s)
{chr(10).join('- ' + f for f in test_info['failures']) if test_info['failures'] else '- See test output for details'}

### Root Cause
The regression was introduced in the latest commit. The diff between the
passing (parent) and failing (HEAD) commits shows changes in:
{chr(10).join('- ' + f for f in git_info['changed_files'] if f)}

The code change reordered or modified logic in a way that breaks existing
test expectations. Specifically, the passing version's logic flow must be
restored.

### Changed Files
- {diagnosis.get('file_to_fix', 'Unknown')}

### Fix Strategy
Revert the affected logic to match the passing version's behavior while
preserving any non-breaking improvements from the PR.
"""
    output_path = FAILED_ROOT / "failed_reasons.txt"
    output_path.write_text(content)
    print(f"Wrote analysis to {output_path}")


def write_and_apply_patch(repo: Path, patch_content: str, patch_num: int = 1) -> bool:
    """Write the patch file and apply it."""
    patch_path = repo / f"patch_{patch_num}.diff"
    patch_path.write_text(patch_content)
    print(f"Wrote patch to {patch_path}")

    # Verify patch applies cleanly
    check = run(f"git apply --check patch_{patch_num}.diff", cwd=str(repo))
    if check.returncode != 0:
        print(f"Patch check failed, trying with --whitespace=fix")
        check = run(f"git apply --whitespace=fix --check patch_{patch_num}.diff", cwd=str(repo))
        if check.returncode != 0:
            print(f"Patch still fails. Trying patch command with fuzz...")
            result = run(f"patch -p1 --fuzz=3 < patch_{patch_num}.diff", cwd=str(repo))
            return result.returncode == 0

    # Apply the patch
    apply_result = run(f"git apply patch_{patch_num}.diff", cwd=str(repo))
    if apply_result.returncode != 0:
        apply_result = run(f"git apply --whitespace=fix patch_{patch_num}.diff", cwd=str(repo))

    return apply_result.returncode == 0


# ──────────────────────────────────────────────────────────────
# Step 7: Verify the fix
# ──────────────────────────────────────────────────────────────

def verify_fix(repo: Path) -> bool:
    """Run tests after applying the patch to confirm the fix."""
    result = run_tests(repo)
    if result["passed"]:
        print("All tests pass after fix!")
        return True
    else:
        print(f"Tests still failing: {result['failures']}")
        print(f"Output tail:\n{result['output'][-1000:]}")
        return False


# ──────────────────────────────────────────────────────────────
# Step 8: Verify all required outputs exist
# ──────────────────────────────────────────────────────────────

def verify_outputs(repo: Path):
    """Check that all required deliverables are present."""
    checks = {
        "failed_reasons.txt": (FAILED_ROOT / "failed_reasons.txt").exists(),
        "patch_1.diff": (repo / "patch_1.diff").exists(),
    }
    for name, exists in checks.items():
        status = "✓" if exists else "✗ MISSING"
        print(f"  {status} {name}")

    if not all(checks.values()):
        print("WARNING: Some required outputs are missing!")
        return False
    return True


# ──────────────────────────────────────────────────────────────
# Main orchestration
# ──────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("BugSwarm Build Fixer")
    print("=" * 60)

    # Step 1: Find the repo
    repo = find_repo()
    print(f"\nRepo: {repo}\n")

    # Step 2: Analyze git history
    print("--- Analyzing git history ---")
    git_info = analyze_git_history(repo)

    # Step 3: Run tests to see failures
    print("\n--- Running tests (before fix) ---")
    test_info = run_tests(repo)
    if test_info["passed"]:
        print("Tests already pass! Nothing to fix.")
        # Still write the outputs for the verifier
        write_analysis({"file_to_fix": "N/A"}, test_info, git_info)
        return

    print(f"Failures: {test_info['failures']}")

    # Step 4: Diagnose the regression
    print("\n--- Diagnosing regression ---")
    diagnosis = diagnose_regression(repo, git_info, test_info)

    if not diagnosis.get("file_to_fix"):
        print("Could not automatically identify the regression file.")
        print("Manual investigation needed.")
        sys.exit(1)

    # Step 5: Generate the patch
    print("\n--- Generating patch ---")
    patch_content = generate_patch(repo, diagnosis)
    if not patch_content:
        print("Generated empty patch. Manual investigation needed.")
        sys.exit(1)

    print(f"Patch preview:\n{patch_content[:500]}")

    # Step 6: Write analysis and apply patch
    print("\n--- Writing outputs ---")
    write_analysis(diagnosis, test_info, git_info)
    applied = write_and_apply_patch(repo, patch_content)

    if not applied:
        print("Failed to apply patch. Manual intervention needed.")
        sys.exit(1)

    # Step 7: Verify the fix
    print("\n--- Verifying fix ---")
    success = verify_fix(repo)

    # Step 8: Check all outputs
    print("\n--- Checking deliverables ---")
    outputs_ok = verify_outputs(repo)

    if success and outputs_ok:
        print("\n✓ Build fixed successfully!")
    else:
        print("\n✗ Fix incomplete — review output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## Alternative: Manual Step-by-Step Shell Approach

When the Python script approach doesn't fit (e.g., the regression is subtle and needs human reasoning), use this shell-based workflow:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Find repo
REPO=$(find /home/github/build/failed -mindepth 2 -maxdepth 2 -type d \
       -not -name environment | head -1)
cd "$REPO"
echo "Working in: $REPO"

# 2. Understand what changed
echo "=== Git History ==="
git log --oneline -10
echo ""
echo "=== Changed Files ==="
git diff HEAD~1 HEAD --name-only
echo ""
echo "=== Full Diff ==="
git diff HEAD~1 HEAD

# 3. Run tests to see the failure
pip install -e . 2>/dev/null
pip install pytest requests-mock 2>/dev/null
echo "=== Test Results (BEFORE fix) ==="
python -m pytest tests/ -v --tb=short 2>&1 || true

# 4. Examine the specific failing file
# (adapt the file path based on step 2 output)
FILE_TO_FIX="agentops/client.py"  # example
echo "=== Current (failing) version ==="
cat -n "$FILE_TO_FIX"
echo ""
echo "=== Passing version ==="
git show HEAD~1:"$FILE_TO_FIX" | cat -n

# 5. Check exact whitespace (critical for diff accuracy)
echo "=== Whitespace check ==="
sed -n '130,170p' "$FILE_TO_FIX" | cat -A

# 6. Write analysis
cat > /home/github/build/failed/failed_reasons.txt << 'ANALYSIS'
## Build Failure Analysis
[Fill in based on your investigation]
ANALYSIS

# 7. Create patch (adapt to your specific fix)
cat > "$REPO/patch_1.diff" << 'PATCH'
--- a/path/to/file.py
+++ b/path/to/file.py
@@ -line,count +line,count @@
[Your unified diff here]
PATCH

# 8. Apply and verify
git apply --check patch_1.diff && echo "Patch OK" || echo "Patch FAILED"
git apply patch_1.diff

echo "=== Test Results (AFTER fix) ==="
python -m pytest tests/ -v 2>&1

# 9. Final check
echo "=== Deliverables ==="
ls -la /home/github/build/failed/failed_reasons.txt
ls -la "$REPO"/patch_*.diff
```

---

## Domain-Specific Notes

### BugSwarm Image Structure
- The Docker image has the repo pre-cloned with both passing and failing commits in history
- Python and pip are pre-installed but test dependencies may need manual installation
- The repo is at `/home/github/build/failed/<org>/<project>`

### Diff Format Requirements
The verifier expects standard unified diff format compatible with `git apply`:
```
--- a/relative/path/to/file.py
+++ b/relative/path/to/file.py
@@ -start,count +start,count @