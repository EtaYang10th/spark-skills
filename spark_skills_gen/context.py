"""Retry context tracking for the iterative SPARK loop.

Maintains a single evolving exploration memo instead of accumulating
independent error summaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from spark_skills_gen.prompts import RETRY_INJECTION_HEADER, RETRY_INJECTION_FOOTER


# ── PDI helper utilities ─────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """Word-level tokenization for overlap computation."""
    return set(re.findall(r"\w+", (text or "").lower()))


def _jaccard(a: str, b: str) -> float:
    sa, sb = _tokenize(a), _tokenize(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _coverage(source: str, target: str) -> float:
    """Fraction of *target* tokens that also appear in *source*."""
    src, tgt = _tokenize(source), _tokenize(target)
    return len(src & tgt) / len(tgt) if tgt else 0.0


def _extract_section(text: str, title: str) -> str:
    match = re.search(
        rf"### {re.escape(title)}\n([\s\S]*?)(?:\n### |\Z)",
        text or "",
    )
    return match.group(1).strip() if match else ""


def _remove_section(text: str, title: str) -> str:
    updated = re.sub(
        rf"\n### {re.escape(title)}\n[\s\S]*?(?=\n### |\Z)",
        "\n",
        text or "",
    )
    return re.sub(r"\n{3,}", "\n\n", updated).strip()


def _extract_failed_tests(summary: str) -> set[str]:
    failed: set[str] = set()
    for line in (summary or "").splitlines():
        line = line.strip()
        if line.startswith("✗"):
            failed.add(line[1:].strip())
    return failed


# ── PDI Tracker ──────────────────────────────────────────────────────────

@dataclass
class PDISnapshot:
    """One step's proxy-PDI measurement."""
    step: int
    proxy_exec: float
    proxy_plan: float
    proxy_oss: float
    raw_pdi: float
    weight: float
    weighted_pdi: float
    triggered: bool
    level: str | None  # "soft" | "strong" | None

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "proxy_exec": round(self.proxy_exec, 4),
            "proxy_plan": round(self.proxy_plan, 4),
            "proxy_oss": round(self.proxy_oss, 4),
            "raw_pdi": round(self.raw_pdi, 4),
            "weight": round(self.weight, 4),
            "weighted_pdi": round(self.weighted_pdi, 4),
            "triggered": self.triggered,
            "level": self.level,
        }


@dataclass
class PDITracker:
    """Tracks proxy-PDI across reflect steps and decides intervention level.

    Design constraints (from user requirements):
    - Early warmup: weight ramps from 0 → 1 over ``warmup`` steps.
    - Agent never sees PDI values or metric names.
    - Memo is never cleared; only prompt-level guidance is injected.
    """

    enabled: bool = False
    observe_only: bool = False
    warmup: int = 2
    threshold: float = -0.5
    history: list[PDISnapshot] = field(default_factory=list)

    # Internal accumulators for z-score normalisation
    _exec_values: list[float] = field(default_factory=list)
    _plan_values: list[float] = field(default_factory=list)
    _oss_values: list[float] = field(default_factory=list)

    def compute(
        self,
        step: int,
        current_memo: str,
        previous_memo: str,
        agent_commands: str,
        test_summary: str,
        previous_test_summary: str,
    ) -> PDISnapshot:
        """Compute proxy-PDI for the current reflect step.

        ``step`` is 0-indexed (0 = after first reflect).
        """
        # ── proxy_exec: how much execution evidence is absorbed into memo ──
        verified_facts = _extract_section(current_memo, "Verified Facts")
        proxy_exec = _coverage(agent_commands, verified_facts) if verified_facts else 0.0

        # ── proxy_plan: strategy stagnation (high = bad, strategies not changing) ──
        cur_strategy = _extract_section(current_memo, "Next Strategy")
        prev_strategy = _extract_section(previous_memo, "Next Strategy")
        proxy_plan = _jaccard(prev_strategy, cur_strategy) if previous_memo else 0.0

        # ── proxy_oss: memo ossification ──
        prev_facts = _extract_section(previous_memo, "Verified Facts")
        facts_sim = _jaccard(prev_facts, verified_facts) if previous_memo else 0.0

        cur_fails = _extract_failed_tests(test_summary)
        prev_fails = _extract_failed_tests(previous_test_summary)
        if cur_fails or prev_fails:
            fail_sim = len(cur_fails & prev_fails) / len(cur_fails | prev_fails)
        else:
            fail_sim = 0.0
        proxy_oss = 0.5 * facts_sim + 0.5 * fail_sim if previous_memo else 0.0

        # ── Accumulate for running z-score ──
        self._exec_values.append(proxy_exec)
        self._plan_values.append(proxy_plan)
        self._oss_values.append(proxy_oss)

        # ── Compute raw PDI (z-scored if enough samples, else raw difference) ──
        if len(self._exec_values) >= 2:
            raw_pdi = (
                self._zscore(proxy_exec, self._exec_values)
                - self._zscore(proxy_plan, self._plan_values)
                - self._zscore(proxy_oss, self._oss_values)
            )
        else:
            # First step: simple difference, no z-score possible
            raw_pdi = proxy_exec - proxy_plan - proxy_oss

        # ── Warmup weight ──
        weight = min(1.0, step / self.warmup) if self.warmup > 0 else 1.0
        weighted_pdi = weight * raw_pdi

        # ── Trigger decision ──
        triggered = False
        level: str | None = None
        if self.enabled and step >= 1 and weight > 0:
            if weighted_pdi < self.threshold:
                triggered = True
                # Check if previous step also triggered → escalate to strong
                if (
                    self.history
                    and self.history[-1].triggered
                ):
                    level = "strong"
                else:
                    level = "soft"

        snapshot = PDISnapshot(
            step=step,
            proxy_exec=proxy_exec,
            proxy_plan=proxy_plan,
            proxy_oss=proxy_oss,
            raw_pdi=raw_pdi,
            weight=weight,
            weighted_pdi=weighted_pdi,
            triggered=triggered,
            level=level,
        )
        self.history.append(snapshot)
        return snapshot

    @staticmethod
    def _zscore(value: float, values: list[float]) -> float:
        import numpy as np
        arr = np.array(values, dtype=float)
        std = float(arr.std())
        if std == 0:
            return 0.0
        return float((value - arr.mean()) / std)

    @property
    def last_snapshot(self) -> PDISnapshot | None:
        return self.history[-1] if self.history else None

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "observe_only": self.observe_only,
            "warmup": self.warmup,
            "threshold": self.threshold,
            "history": [s.to_dict() for s in self.history],
        }


@dataclass(slots=True)
class AttemptRecord:
    """A single execution attempt record."""

    attempt: int
    status: str
    reward: float
    agent_commands: str = ""
    test_summary: str = ""
    n_passed: int = 0
    n_tests: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "status": self.status,
            "reward": self.reward,
            "agent_commands": self.agent_commands,
            "test_summary": self.test_summary,
            "n_passed": self.n_passed,
            "n_tests": self.n_tests,
        }


@dataclass
class TaskContext:
    """Track retry history for one task with an evolving exploration memo."""

    task_name: str
    max_retries: int
    exploration_memo: str = ""
    attempts: list[AttemptRecord] = field(default_factory=list)
    memo_history: list[str] = field(default_factory=list)
    pdi_tracker: PDITracker = field(default_factory=PDITracker)

    @property
    def current_attempt(self) -> int:
        return len(self.attempts)

    @property
    def is_success(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].status == "PASS"

    @property
    def should_retry(self) -> bool:
        return not self.is_success and self.current_attempt < self.max_retries

    def add_attempt(
        self,
        status: str,
        reward: float,
        agent_commands: str = "",
        test_summary: str = "",
        n_passed: int = 0,
        n_tests: int = 0,
    ) -> None:
        self.attempts.append(
            AttemptRecord(
                attempt=self.current_attempt,
                status=status,
                reward=reward,
                agent_commands=agent_commands,
                test_summary=test_summary,
                n_passed=n_passed,
                n_tests=n_tests,
            )
        )

    def update_memo(self, new_memo: str) -> None:
        """Replace the exploration memo, archiving the previous version."""
        if self.exploration_memo:
            self.memo_history.append(self.exploration_memo)
        self.exploration_memo = new_memo

    def build_injection(self) -> str:
        """Build the text injected into instruction.md for retry attempts."""
        from spark_skills_gen.prompts import (
            PDI_SOFT_INTERVENTION,
            PDI_STRONG_INTERVENTION,
            PDI_STRONG_RETRY_FOOTER,
        )

        if not self.exploration_memo:
            return ""
        attempt_num = self.current_attempt + 1
        remaining = self.max_retries - self.current_attempt
        urgency = (
            f"\n⏳ This is attempt {attempt_num} of {self.max_retries}. "
            f"You have {remaining} attempt(s) remaining"
        )
        if remaining <= 1:
            urgency += " — THIS IS YOUR LAST CHANCE. Be precise and targeted."
        elif remaining <= 2:
            urgency += " — running low. Focus on the most promising fix."
        else:
            urgency += "."

        memo_for_injection = self.exploration_memo
        footer = RETRY_INJECTION_FOOTER

        # ── PDI-guided exploration feedback (agent never sees metric names) ──
        # In observe_only mode, PDI is computed & displayed but never injected.
        pdi_injection = ""
        snapshot = self.pdi_tracker.last_snapshot
        if (
            snapshot is not None
            and snapshot.triggered
            and snapshot.level
            and not self.pdi_tracker.observe_only
        ):
            if snapshot.level == "strong":
                pdi_injection = PDI_STRONG_INTERVENTION
                memo_for_injection = _remove_section(self.exploration_memo, "Next Strategy")
                footer = PDI_STRONG_RETRY_FOOTER
            else:
                pdi_injection = PDI_SOFT_INTERVENTION

        return (
            f"{RETRY_INJECTION_HEADER}"
            f"{memo_for_injection}"
            f"{urgency}"
            f"{pdi_injection}"
            f"{footer}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_name": self.task_name,
            "max_retries": self.max_retries,
            "exploration_memo": self.exploration_memo,
            "memo_history": list(self.memo_history),
            "attempts": [a.to_dict() for a in self.attempts],
            "pdi": self.pdi_tracker.to_dict(),
        }
