"""LLM prompt templates for the SPARK iterative skill generation pipeline.

Two LLM call types:
  1. Reflect  — after a failed attempt, rewrite the exploration memo
  2. Skill Generation — after a successful attempt, produce SKILL.md
"""

# ── Reflect (rewrite exploration memo after failure) ──

REFLECT_SYSTEM = """\
You are a debugging analyst. After a failed task attempt, you maintain a single \
"Exploration Memo" — a living document that consolidates ALL knowledge gained so far.

You will receive:
- The current memo (empty on first failure)
- The agent's commands from this attempt
- A structured test summary (which tests passed / failed)

Your job: REWRITE the memo to incorporate the new failure. Do NOT simply append — \
resolve contradictions, merge insights, and keep it compact.

Output EXACTLY the following format (no extra text):

## Exploration Memo (N failed attempts)

### Attempts Log
- #1: [1-line: approach + result]
- #2: ...

### Commands From Last Attempt
[key commands the agent ran, no output]

### Verified Facts
- [things confirmed to work — PRESERVE across rewrites]
- [things confirmed to NOT work]

### Current Error Pattern
[concise description of what is going wrong NOW]

### Next Strategy
[specific, actionable plan for the next attempt — MUST differ from all previous approaches]\

"""

REFLECT_USER = """\
## Task: {task_name}

### Current Exploration Memo:
{exploration_memo}

### This Attempt's Result: {status} (attempt #{attempt_number}, reward: {reward})
Tests: {n_passed}/{n_tests} passed

### Agent Commands This Attempt:
{agent_commands}

### Test Summary:
{test_summary}

Rewrite the Exploration Memo incorporating this new failure.\
"""

# ── Skill Generation ──

SKILL_GENERATION_SYSTEM = """\
You are an expert at writing Agent Skills — structured packages of procedural \
knowledge that help LLM agents complete tasks.

Given structured evidence from a successful task execution AND lessons from \
all failed attempts, \
generate a high-quality SKILL.md that:
1. Provides GENERAL procedural guidance for a CLASS of similar tasks
2. Follows the standard format with YAML frontmatter
3. Contains actionable workflows, code examples, and critical notes
4. Includes a "Common Pitfalls" section distilled from repeated or corrected mistakes

STRUCTURE — every skill MUST contain ALL of the following:
A) HIGH-LEVEL WORKFLOW: A numbered step-by-step procedure describing WHAT to do \
   and WHY, with domain context and decision criteria.
B) CONCRETE EXECUTABLE CODE: For every non-trivial step, provide a complete, \
   runnable code block that the agent can adapt. Do NOT replace code with prose \
   like "interpolate the data" — show the actual implementation. \
   Include imports, error handling, and edge-case guards.
C) COMPLETE REFERENCE IMPLEMENTATION: In addition to per-step snippets, include \
   ONE large, self-contained code block (clearly marked "## Reference Implementation") \
   that chains the entire solution end-to-end: imports → data loading → core logic \
   → output/save. This block must be directly runnable (copy-paste-and-adapt). \
   A student agent that only reads this single block should be able to produce a \
   passing solution with minimal modification. This is the MOST IMPORTANT section — \
   skills without a complete reference implementation have near-zero transfer value.

The skill should be long enough to be genuinely useful. Aim for 200-500 lines. \
Do NOT sacrifice concrete code for brevity.

WHAT TO INCLUDE:
- General procedures, patterns, and domain conventions
- Reusable code snippets with full implementations (not pseudocode)
- Libraries, tools, and frameworks available in the environment (e.g. installed \
  packages, CLI tools, pre-existing scripts). Reference them by name.
- Exact output formats expected by validators or downstream consumers \
  (e.g. PDDL plan format, JSON schema, CSV column order)
- Coordinate systems, unit conventions, and data alignment rules for the domain

WHAT TO EXCLUDE:
- Hard-coded constants or magic numbers from THIS specific task instance \
  (e.g. specific file paths like "/root/data/task42.csv", specific answer values)
- Specific test case names or expected output values
- Invalidated, speculative, or clearly failed hypotheses as recommended workflow
- Fallback strategies that produce low-quality results (e.g. "divide evenly", \
  "use dummy values") — these cause agents to give up too early

EVIDENCE PRIORITY:
- Prioritize the success execution chain and success verification signals
- Distill lessons from all attempts into generalized "pitfalls to avoid"
- Use the raw support tail only when it adds signal beyond the structured sections

Output ONLY the SKILL.md content, starting with the YAML frontmatter (---).\
"""

SKILL_GENERATION_USER = """\
## Task Category: {task_name}
### Task Pattern:
{task_pattern}

### Success Execution Chain:
{success_execution_chain}

### Success Verification Signals:
{success_verification_signals}

### Lessons From All Attempts:
{lessons_from_all_attempts}

### Environment Affordances:
{environment_affordances}

### Raw Support Tail:
{raw_support_tail}

Generate a SKILL.md that would help another agent solve SIMILAR tasks in this domain. \
Teach the future agent how to choose the right execution path early, what to verify \
before finalizing, and which repeated mistakes to avoid.

REMINDER: The "## Reference Implementation" section with a COMPLETE, end-to-end \
runnable code block is mandatory. Without it, the skill is worthless.\
"""

# ── Injection into instruction.md for retries ──

RETRY_INJECTION_HEADER = """
---
## ⚠️ Previous Exploration Results

"""

RETRY_INJECTION_FOOTER = """
---
Focus on the "Next Strategy" above. Do NOT repeat approaches that already failed.
"""

PDI_STRONG_RETRY_FOOTER = """
---
The previous "Next Strategy" has been withheld because repeated continuation led to stagnation.
Use "Verified Facts" and "Current Error Pattern" as your primary anchors.
Do not reconstruct or continue the old strategy unless new execution evidence supports it.
"""

# ── PDI-guided exploration feedback ──────────────────────────────────────
# These are injected when the pipeline detects exploration stagnation.
# CRITICAL: No metric names, scores, or technical jargon — the agent must
# never know that a quantitative check is happening.

PDI_SOFT_INTERVENTION = """

---
### 🔄 Exploration Feedback

Your recent attempts appear to follow a recurring pattern: the same core \
assumptions are being carried forward while only surface-level changes are \
made to the strategy. Consider the following before your next attempt:

- What if your current understanding of the problem is fundamentally incomplete?
- Are there alternative libraries, algorithms, or data interpretations you \
have not explored at all?
- Re-examine your Verified Facts — which ones are truly confirmed by test \
output versus assumed from the task description?
- Look at the actual error messages and test failures more carefully — they \
may be pointing to a different root cause than you think.

Your next attempt should explore a meaningfully different direction.
"""

PDI_STRONG_INTERVENTION = """

---
### 🚨 Critical Exploration Feedback

Multiple consecutive attempts have followed essentially the same reasoning \
path with only cosmetic variations. This pattern rarely leads to a \
breakthrough. You MUST change your approach fundamentally:

1. **Abandon your current hypothesis entirely.** Assume your core assumption \
about how to solve this task is wrong.
2. **Start from the raw problem statement and test output.** What do the \
failing tests actually check? What output format, value, or behavior do they \
expect? Read them again as if for the first time.
3. **Try a completely different technical approach.** If you have been using \
library A, try library B. If you have been parsing data one way, try another. \
If you have been writing to one format, reconsider the expected format.
4. **Challenge your Verified Facts.** Some of them may be wrong or misleading. \
A fact that seemed verified in attempt 1 may have been a coincidence.

Do NOT make incremental tweaks to your previous approach. Take a fresh path.
"""

# ── Verifier analysis hint injected into instruction.md (all attempts) ──

VERIFIER_HINT_HEADER = """
---
## 📋 Verifier Script Analysis (READ THIS FIRST)

The following is a REDACTED version of the test/verifier script that will evaluate your output.
Expected answer values have been stripped — you MUST solve the problem yourself.
Study the structure to understand:
1. **What output files** are expected (filenames, formats, paths)
2. **What validation logic** is applied (schemas, tolerances, comparison methods)
3. **What libraries/approaches** the verifier imports (hints at useful tools)

DO NOT attempt to reverse-engineer or guess the stripped answer values.
You must implement the actual solution logic.

```python
"""

VERIFIER_HINT_FOOTER = """```
---
"""
