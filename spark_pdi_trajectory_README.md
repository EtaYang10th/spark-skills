---
language:
- en
license: mit
pretty_name: SPARK PDI Trajectory
size_categories:
- n<1K
task_categories:
- text-generation
- other
tags:
- agents
- llm-agents
- skill-distillation
- trajectory
- pdi
- spark
- posterior-distillation-index
- code-agents
---

# SPARK PDI Trajectory

Trajectory-level artifacts released alongside the paper
**Evidence Over Plans: Online Trajectory Verification for Skill Distillation**.

This dataset contains the raw execution trajectories, exploration memos, and distilled `SKILL.md`
documents produced by the SPARK skill-generation pipeline. It is the primary data source used to compute the
**Posterior Distillation Index (PDI)** — a trajectory-level score that measures whether a distilled skill is
grounded in posterior execution evidence rather than stale prior plans.

- 📄 Paper / blog: https://etayang10th.github.io/spark.github.io/
- 💻 Code: https://github.com/EtaYang10th/spark-skills

## What's inside

Per task, three files are provided:

| File | What it contains |
|---|---|
| `SKILL.md` | The final distilled skill document produced after a successful trajectory. |
| `attempts.json` | The full attempt history: per-attempt status, reward, commands, memo rewrites, and reflection notes. |
| `trajectory.jsonl` | Line-delimited JSON with the raw agent I/O — every `command_execution`, `agent_message`, verifier call, and final `execution_result`. |

Directory layout:

```
all_model_pdi/
├── adaptive-cruise-control/
│   ├── SKILL.md
│   ├── attempts.json
│   └── trajectory.jsonl
├── citation-check/
│   └── ...
└── <task-id>/
    └── ...
```

Each top-level folder is one task from **SkillsBench** (86 runnable tasks across 11 domains).

## Intended use

- Reproducing PDI analysis (φ<sub>exec</sub>, φ<sub>plan</sub>, φ<sub>oss</sub>) from the paper.
- Training / evaluating trajectory-level verifiers and skill-quality metrics.
- Studying how LLM agents explore, fail, and eventually succeed on runnable Docker tasks.
- Mining successful command sequences for procedural-skill distillation research.

## Quick start

```python
from huggingface_hub import snapshot_download
import json, pathlib

local = snapshot_download(
    repo_id="EtaYang10th/SPARK_PDI_Trajectory",
    repo_type="dataset",
)

task_dir = pathlib.Path(local) / "all_model_pdi" / "adaptive-cruise-control"

skill = (task_dir / "SKILL.md").read_text()
attempts = json.loads((task_dir / "attempts.json").read_text())
trajectory = [json.loads(line) for line in (task_dir / "trajectory.jsonl").read_text().splitlines() if line.strip()]

print(attempts["attempts"][0]["status"], attempts["attempts"][0]["reward"])
```

## Fields (trajectory.jsonl)

Each line is a JSON object. The most common types:

- `execution_result` — terminal verdict of a full attempt, with `status` (`PASS` / `FAIL` / `PARTIAL`), `reward`, `n_passed` / `n_tests`, and the complete `agent_stdout_full` stream.
- `command_execution` — a single shell command the agent ran, with its `aggregated_output` and `exit_code`.
- `agent_message` — free-form agent reasoning emitted between commands.

## Known limitations

- Task set is restricted to SkillsBench (Docker + pytest verifier) — not a general web-agent dataset.
- `agent_stdout_full` retains raw stdout/stderr; some outputs are long and may include non-UTF-8 bytes escaped as JSON strings.
- Trajectories reflect teacher-model behavior during skill generation; they are *not* balanced demonstrations.
- No chat-format labels are provided; use the raw JSON structure as-is.

## License

MIT License (same as the SPARK codebase).

## Citation

```bibtex
@misc{zhou2026spark,
  title  = {Evidence Over Plans: Online Trajectory Verification for Skill Distillation},
  author = {Zhou, Yang and Dong, Zihan and Wang, Zhenting and Jin, Can and
            Zhao, Shiyu and Guo, Bangwei and Gu, Difei and
            Zhang, Linjun and Zhou, Mu and Metaxas, Dimitris N.},
  year   = {2026}
}
```
