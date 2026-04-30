---
id: fix-build-python-agentops
title: Fix Python Build Errors (AgentOps-style repos)
version: 1.0.0
tags: [python, build-fix, pytest, async, singleton, imports]
description: >
  Procedural guide for diagnosing and fixing Python build failures in
  decorator-heavy, async-capable, singleton-pattern codebases. Covers
  import errors, async wrapper bugs, singleton lifecycle issues, and
  missing attribute initialization.
---

## Module 1: Triage — Identify Root Causes Before Touching Code

### 1.1 Install dependencies first

Before running any tests, ensure the environment is clean:

```bash
pip3 install -e . -q
pip3 install pytest pytest-asyncio requests_mock psutil -q
```

Missing optional deps (e.g. `langchain`, `langchain-core`) will produce
`ImportError` at collection time, masking the real test failures.

### 1.2 Run failing tests with full tracebacks

```bash
python3 -m pytest tests/ -v --tb=long -s 2>&1 | head -200
```

Capture the full output. Look for:
- `ImportError` / `ModuleNotFoundError` — package moved or renamed
- `AttributeError` — missing attribute, often from incomplete `__init__`
- `TypeError: object is not awaitable` — sync wrapper applied to async method
- `RuntimeError` / stale state — singleton not reset between tests

### 1.3 Write your analysis before patching

Write findings to a notes file before touching any source:

```
failed_reasons.txt
------------------
1. ImportError: langchain.callbacks.base → moved to langchain_core.callbacks.base
2. handle_exceptions wraps async methods with sync wrapper → breaks async tests
3. singleton never re-creates instance → setup_method can't reset client state
4. _tags_for_future_session only set in one branch of __init__ → AttributeError
```

This forces clarity and prevents thrashing.

---

## Module 2: Common Fix Patterns

### 2.1 Relocated package imports

When a library reorganizes its public API (e.g. `langchain` ≥ 0.1.0 moved
callbacks to `langchain_core`), update the import:

```python
# Before
from langchain.callbacks.base import BaseCallbackHandler

# After
from langchain_core.callbacks.base import BaseCallbackHandler
```

Check the installed package's actual module tree if unsure:

```bash
python3 -c "import langchain_core; print(langchain_core.__file__)"
python3 -c "from langchain_core.callbacks.base import BaseCallbackHandler"
```

### 2.2 Async-aware exception/decorator wrappers

A common bug: a metaclass or decorator wraps ALL methods with a sync wrapper,
breaking `async def` methods. Fix by detecting coroutine functions:

```python
import inspect
import functools

def handle_exceptions(fn):
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                # handle or re-raise
                raise
        return async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                raise
        return sync_wrapper
```

Apply this pattern anywhere a metaclass or decorator wraps methods generically.

### 2.3 Singleton reset between tests

A singleton that never re-creates its instance will leak state across tests.
Fix: allow re-creation when called with arguments, or expose a reset mechanism:

```python
def singleton(cls):
    instances = {}

    def getinstance(*args, **kwargs):
        # Re-create if called with args (e.g. init/reset scenario)
        if cls not in instances or (args or kwargs):
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return getinstance
```

If tests use `setup_method` / `setUp` to re-initialize the client, the
singleton MUST allow re-creation on each call with arguments.

### 2.4 Unconditional attribute initialization in `__init__`

Attributes that are only set inside conditional branches cause `AttributeError`
when other methods reference them and the branch wasn't taken:

```python
# Buggy — attribute only exists if condition is True
def __init__(self, auto_start_session=True):
    if not auto_start_session:
        self._tags_for_future_session = []

# Fixed — always initialize
def __init__(self, auto_start_session=True):
    self._tags_for_future_session = []   # unconditional
    if not auto_start_session:
        pass  # other branch logic
```

Scan `__init__` for any attribute that is read elsewhere but only written
inside an `if` block.

---

## Module 3: Patch Workflow

### 3.1 Write diffs in standard format

```bash
# One patch per logical change
git diff > patch_1.diff          # if using git
# or produce manually:
diff -u original.py modified.py > patch_1.diff
```

Name patches sequentially: `patch_1.diff`, `patch_2.diff`, etc.

### 3.2 Apply and verify

```bash
git apply patch_1.diff
git apply patch_2.diff
# ...
python3 -m pytest tests/ -v --tb=short 2>&1
```

All targeted tests must pass before finalizing.

### 3.3 Verify output artifacts exist

The verifier checks for:
- `failed_reasons.txt` — your analysis notes
- At least one `patch_N.diff` file
- A clean test run (build success)

---

## Common Pitfalls

- **Running tests before installing deps** — `ImportError` at collection hides
  real failures. Always `pip install -e .` first.

- **Applying a sync wrapper to async methods** — any metaclass or decorator
  that wraps methods must branch on `inspect.iscoroutinefunction`. Forgetting
  this breaks all `async def` paths silently (they return a coroutine object
  instead of being awaited).

- **Singleton leaking state between tests** — if `setup_method` calls
  `Client(...)` expecting a fresh instance, a naive singleton will return the
  stale one. The singleton must re-create on args/kwargs.

- **Conditional attribute initialization** — always initialize instance
  attributes unconditionally in `__init__`, even to `None` or `[]`. Conditional
  initialization is a latent `AttributeError`.

- **Not capturing `--tb=long` output** — short tracebacks hide the actual
  failing line. Always use `--tb=long -s` for diagnosis.

- **Patching without a notes file** — writing `failed_reasons.txt` first is
  required by the verifier AND forces you to understand all root causes before
  making changes, preventing partial fixes.

- **Assuming one root cause** — these repos typically have 3-4 independent
  bugs. Fix all of them; a single patch rarely achieves a clean build.
