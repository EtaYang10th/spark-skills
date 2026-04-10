"""Prompt templates for blueprint generation and repair."""

from __future__ import annotations


CRITIQUE_SYSTEM = """You review Harbor task blueprints for consistency and evidence quality.

Output JSON only with the form:
{
  "issues": [
    {
      "severity": "error|warning",
      "area": "instruction|environment|oracle|verifier|evidence",
      "message": "what is wrong"
    }
  ]
}
"""


CRITIQUE_USER = """Critique this task blueprint for internal mismatches, answer leakage, verifier/oracle drift, and unsupported assumptions.

## Prompt Specification
{prompt_spec_json}

## Blueprint
{blueprint_json}
"""


# ---------------------------------------------------------------------------
# Harbor task format specification (used in tools-pool mode)
# ---------------------------------------------------------------------------

HARBOR_FORMAT_SPEC = """A Harbor task is a self-contained directory:

```
<task-id>/
├── instruction.md              # Task description for the solving agent
├── task.toml                   # Metadata, timeouts, resource limits
├── environment/
│   ├── Dockerfile              # Container setup (base image, packages, data build)
│   ├── files/                  # Static support files (copied into container at WORKDIR)
│   └── scripts/
│       └── build_data.py       # Deterministic data generator (runs at Docker build time)
├── solution/
│   └── solve.sh                # Oracle: bash script that solves the task correctly
└── tests/
    ├── test_outputs.py         # Verifier: pytest code that checks the agent's output
    └── test.sh                 # Test runner (installs pytest, runs tests, writes reward)
```

### Dockerfile conventions

```dockerfile
FROM python:3.10-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y <apt-packages> && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --break-system-packages <pip-packages>
WORKDIR /root
COPY files/ ./
COPY scripts/build_data.py /tmp/spark_task_build_data.py
RUN python3 /tmp/spark_task_build_data.py && rm /tmp/spark_task_build_data.py
```

Key points:
- `COPY files/ ./` copies support files into WORKDIR first.
- `build_data.py` runs AFTER files are copied, so it can read support files.
- `build_data.py` must generate ALL input data and any hidden expected-output
  files (e.g. `/root/.expected.json`) deterministically.

### solve.sh conventions

```bash
#!/bin/bash
set -euo pipefail
python3 <<'SPARK_ORACLE_EOF'
# Python code that produces the correct output
SPARK_ORACLE_EOF
```

### test.sh conventions

```bash
#!/bin/bash
set -euo pipefail
python3 -m pip install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5
mkdir -p /logs/verifier
if pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit 0
```

### test_outputs.py conventions

- Every test MUST be a `def test_*():` function with NO parameters. Pytest only discovers `test_*` functions.
- Do NOT place bare assert statements at module level — they will not be collected by pytest.
- Load data (expected output, agent output) at module level or inside each test function.
- Check output file exists, validate JSON structure and keys, compare values against expected output.
- Use `pytest.approx()` or `math.isclose()` for floating-point tolerances.
"""


# ---------------------------------------------------------------------------
# Tools-pool-based generation prompts
# ---------------------------------------------------------------------------

TOOLS_BLUEPRINT_SYSTEM = """You design Harbor tasks from a task prompt and a catalog of available tools.

Hard rules:
- Select tools from the provided catalog that the task requires. Standard-library modules are always available even if not listed.
- All input data must be deterministically generated in `data_builder_python`.
- The oracle (`oracle_python`) must produce correct output that passes the verifier.
- The verifier must check the behavior described in the instruction, not a different hidden task.
- If generated data has known expected outputs, persist them in a hidden file (e.g. `.expected.json`) during build and let the verifier read them.
- Do not make the verifier re-implement the full oracle when a hidden expected-output file can be generated deterministically.
- Keep units and numeric transformations explicit and consistent across instruction, oracle, verifier, and any generated ground-truth files.
- `data_builder_python` runs at Docker build time in `WORKDIR` after support files are copied.
  It is saved as `scripts/build_data.py` and executed via `python3 /tmp/spark_task_build_data.py`.
  Write generated data and hidden ground-truth files to absolute paths.
- The task should require non-trivial reasoning — at least one substantive computation, transformation, or filtering step.
- Evidence entries can only cite the user prompt (`source_type: "prompt"`); there are no reference tasks.
- `reference_tasks` must be an empty list `[]`.
- Record all design decisions in `assumptions`.
- When listing pip packages, do NOT pin exact versions (use `pandas` not `pandas==2.0.0`) to avoid binary incompatibility with transitive dependencies.
- Prefer stdlib modules in `data_builder_python` to minimize build-time failures. Heavy packages like pandas or numpy should only appear in pip_packages if the task truly requires them.
- Verifier test code MUST define `def test_*():` functions (no parameters). Do NOT use bare asserts at module level — pytest will not collect them.
- Output JSON only. No markdown fences.
"""


TOOLS_BLUEPRINT_USER = """Design one Harbor task from the prompt and tools catalog below.

## Prompt Specification
{prompt_spec_json}

## Available Tools Catalog
Select the tools you need. Standard-library modules not listed are also available.
{tools_catalog_json}

## Harbor Task Format Specification
{format_spec}

## Required JSON Schema
{{
  "task_id": "harbor-task-id",
  "title": "short task title",
  "instruction_md": "full instruction markdown shown to the agent",
  "difficulty": "easy|medium|hard",
  "category": "short category",
  "tags": ["tag1", "tag2"],
  "output_path": "absolute output path required by the task",
  "acceptance_criteria": ["criterion 1", "criterion 2"],
  "environment": {{
    "base_image": "python:3.10-slim",
    "workdir": "/root",
    "apt_packages": [],
    "pip_packages": ["python-package==version"],
    "build_timeout_sec": 600.0,
    "cpus": 1,
    "memory_mb": 4096,
    "storage_mb": 10240,
    "allow_internet": false
  }},
  "support_files": [
    {{
      "relative_path": "reference_data.md",
      "content": "file contents",
      "purpose": "why the file is needed"
    }}
  ],
  "data_builder_python": "python code that deterministically creates inputs and hidden expected outputs in the workdir",
  "oracle_python": "python code that solves the task correctly",
  "verifier": {{
    "timeout_sec": 900.0,
    "pip_packages": ["pytest==8.4.1"],
    "test_code": "pytest code for tests/test_outputs.py"
  }},
  "agent_timeout_sec": 1800.0,
  "evidence": [
    {{
      "evidence_id": "ev1",
      "source_type": "prompt",
      "source_name": "prompt",
      "quote": "verbatim substring from the task prompt",
      "rationale": "what this quote supports"
    }}
  ],
  "assumptions": ["design decisions not directly stated in the prompt"],
  "family_hypotheses": ["candidate task family categories"],
  "reference_tasks": [],
  "validation_checks": ["consistency checks the renderer/validator should expect"]
}}

## Additional Requirements
- Install selected tools in the environment (`apt_packages` for system tools, `pip_packages` for Python packages).
- Generate realistic input data in `data_builder_python` — avoid trivial one-element datasets.
- Create a hidden `.expected.json` with the expected output and use it in the verifier.
- `instruction_md`, `oracle_python`, and `verifier.test_code` must agree on the same output path and JSON keys.
- Use absolute output paths in the instruction and tests.
- Set `agent_timeout_sec` to a reasonable timeout (typically 2-3x `verifier.timeout_sec`).
- `evidence.quote` values must be verbatim substrings from the task prompt.
- `reference_tasks` must be `[]` — do not fabricate reference task citations.
- Do not pin exact pip package versions; use bare package names (e.g. `pandas` not `pandas==2.0.0`).
"""


TOOLS_REPAIR_USER = """Revise this task blueprint.

## Prompt Specification
{prompt_spec_json}

## Current Blueprint
{blueprint_json}

## Available Tools Catalog
{tools_catalog_json}

## Critique And Validation Feedback
{feedback_json}

## Revision Rules
- Preserve the task's core goal unless the feedback proves it is inconsistent.
- Fix instruction/oracle/verifier mismatches first.
- If the task needs generated data, make the generator deterministic.
- Prefer writing hidden expected-output files over re-deriving the same ground truth multiple ways.
- When feedback includes a numeric mismatch or build failure, fix that concrete issue before changing unrelated parts.
- Keep all design decisions visible in `assumptions`.
- Return a complete replacement JSON object with the same schema as before.
"""


REPAIR_SYSTEM = """You repair Harbor task blueprints.

Keep the task goal intact. Fix only the inconsistencies, unsupported assumptions, or validation failures described in the input.
Do not respond with explanations. Output JSON only.
"""
