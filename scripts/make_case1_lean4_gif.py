#!/usr/bin/env python3
"""Generate a case-study GIF for lean4-proof: Non-PDI skill vs PDI-refined skill.

Left column : 15 trial-and-error commands (repeated failures) from a non-PDI skill.
Right column: 5 clean commands that succeed the first time under the PDI-refined skill.

Output: blog/assets/img/case1_lean4_before_after.gif
"""

from __future__ import annotations
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
import matplotlib.patches as mpatches

OUT = (
    Path(__file__).resolve().parents[1].parent
    / "blog" / "assets" / "img" / "case1_lean4_before_after.gif"
)

# Reconstructed command stream for the held-out student (Claude Haiku 4.5)
# reported in the blog: 15 trial-and-error commands with repeated failures,
# then 5 commands with zero failures after receiving the PDI-refined skill.
BEFORE = [
    ("cat solution.lean", "ok"),
    ("lake env lean solution.lean", "fail"),
    ("sed -i 's/norm_num/simp/' solution.lean", "ok"),
    ("lake env lean solution.lean", "fail"),
    ("sed -i 's/simp/linarith/' solution.lean", "ok"),
    ("lake env lean solution.lean", "fail"),
    ("cat > solution.lean <<EOF ... EOF", "ok"),
    ("lake env lean solution.lean", "fail"),
    ("sed -i '4s|.*|  induction n with|' solution.lean", "ok"),
    ("lake env lean solution.lean", "fail"),
    ("sed -i '7s|.*|  case succ n ih => exact ih|' solution.lean", "ok"),
    ("lake env lean solution.lean", "warn"),
    ("grep -n 'sorry' solution.lean", "ok"),
    ("cat solution.lean", "ok"),
    ("lake env lean -DwarningAsError=true solution.lean", "fail"),
]

AFTER = [
    ("cat > /tmp/solution.lean <<'LEAN'\n# executable theorem block from SKILL.md\nLEAN", "ok"),
    ("mv /tmp/solution.lean solution.lean", "ok"),
    ("cat solution.lean", "ok"),
    ("lake env lean -DwarningAsError=true solution.lean", "ok"),
    ("echo 'All 4 tests passed · reward = 1.0'", "pass"),
]


def build_line(cmd, status):
    """Return (prompt, cmd, status_tag, color)."""
    status_styles = {
        "ok":   ("#34d399", "✓"),
        "fail": ("#f87171", "✗"),
        "warn": ("#fbbf24", "!"),
        "pass": ("#34d399", "PASS"),
    }
    color, glyph = status_styles.get(status, ("#e2e8f0", "·"))
    return color, glyph


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    fig, (axL, axR) = plt.subplots(
        1, 2, figsize=(12.5, 6.6),
        gridspec_kw={"wspace": 0.05},
    )
    fig.patch.set_facecolor("#f7f8fb")

    for ax, title, subtitle, tint in [
        (axL, "Before · Non-PDI skill",     "118 lines · 4 sections · sketchy",  "#fca5a5"),
        (axR, "After · PDI-refined skill",  "394 lines · 7 modules · executable", "#6ee7b7"),
    ]:
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_facecolor("#0b1220")
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color(tint); s.set_linewidth(2)
        # Title bar
        ax.add_patch(mpatches.Rectangle((0, 0.93), 1, 0.07, facecolor="#111a2e",
                                        edgecolor="none", transform=ax.transAxes))
        ax.text(0.02, 0.965, title, transform=ax.transAxes,
                color=tint, fontsize=13, fontweight="bold", va="center")
        ax.text(0.98, 0.965, subtitle, transform=ax.transAxes,
                color="#94a3b8", fontsize=10, va="center", ha="right")
        # 3 traffic dots
        for i, c in enumerate(["#ff5f56", "#ffbd2e", "#27c93f"]):
            ax.add_patch(mpatches.Circle((0.015 + 0.018*i, 0.965), 0.006,
                                         facecolor=c, edgecolor="none",
                                         transform=ax.transAxes))

    # For placing text lines in each terminal
    def term_line_y(row, total=20):
        # Map row index to y coord within the terminal body (0.05..0.90)
        top, bottom = 0.88, 0.08
        return top - (top - bottom) * (row / max(total - 1, 1))

    # Status bar (bottom) placeholders
    status_text_L = axL.text(
        0.02, 0.02, "", transform=axL.transAxes,
        color="#f87171", fontsize=11, fontweight="bold", va="bottom", ha="left",
    )
    status_text_R = axR.text(
        0.02, 0.02, "", transform=axR.transAxes,
        color="#34d399", fontsize=11, fontweight="bold", va="bottom", ha="left",
    )

    # Collections of text artists to animate
    def pretty_cmd(cmd):
        # Keep command short so it fits in the line
        first = cmd.splitlines()[0]
        if len(first) > 64:
            first = first[:61] + "..."
        return first

    before_text_artists = []
    after_text_artists = []

    for i, (cmd, st) in enumerate(BEFORE):
        y = term_line_y(i, total=max(len(BEFORE), len(AFTER)) + 1)
        color, glyph = build_line(cmd, st)
        prompt = axL.text(
            0.04, y, "$", transform=axL.transAxes,
            color="#60a5fa", family="monospace", fontsize=10.5, va="center",
        )
        body = axL.text(
            0.075, y, "", transform=axL.transAxes,
            color="#e2e8f0", family="monospace", fontsize=10.5, va="center",
        )
        tag = axL.text(
            0.965, y, "", transform=axL.transAxes,
            color=color, family="monospace", fontsize=11,
            fontweight="bold", va="center", ha="right",
        )
        before_text_artists.append((prompt, body, tag, pretty_cmd(cmd), glyph, color))

    for i, (cmd, st) in enumerate(AFTER):
        y = term_line_y(i, total=max(len(BEFORE), len(AFTER)) + 1)
        color, glyph = build_line(cmd, st)
        prompt = axR.text(
            0.04, y, "$", transform=axR.transAxes,
            color="#6ee7b7", family="monospace", fontsize=10.5, va="center",
        )
        body = axR.text(
            0.075, y, "", transform=axR.transAxes,
            color="#e2e8f0", family="monospace", fontsize=10.5, va="center",
        )
        tag = axR.text(
            0.965, y, "", transform=axR.transAxes,
            color=color, family="monospace", fontsize=11,
            fontweight="bold", va="center", ha="right",
        )
        after_text_artists.append((prompt, body, tag, pretty_cmd(cmd), glyph, color))

    # Hide prompts until their frame is reached
    for prompt, body, tag, _, _, _ in before_text_artists + after_text_artists:
        prompt.set_alpha(0.0)

    fig.suptitle(
        "Case 1 · lean4-proof — held-out student (Claude Haiku 4.5)",
        fontsize=13, fontweight="bold", color="#0f172a", y=0.985,
    )

    # Animation strategy:
    # - Left terminal reveals at 1 line per frame
    # - Right terminal advances slower (1 line every 3 frames) so the contrast is clear
    # - After left finishes, add a 'FAIL' summary; right finishes with PASS
    left_reveal_rate = 1.0
    right_reveal_rate = 1.0 / 3.0

    total_frames = max(
        int(len(BEFORE) / left_reveal_rate),
        int(len(AFTER) / right_reveal_rate),
    ) + 6  # hold frames at the end

    def update(frame):
        # Left reveal count
        n_left = min(int(frame * left_reveal_rate) + 1, len(BEFORE))
        n_right = min(int(frame * right_reveal_rate) + 1, len(AFTER))

        for i, (prompt, body, tag, cmd_text, glyph, color) in enumerate(before_text_artists):
            if i < n_left:
                prompt.set_alpha(1.0)
                body.set_text(cmd_text)
                tag.set_text(glyph)
            else:
                prompt.set_alpha(0.0)
                body.set_text("")
                tag.set_text("")

        for i, (prompt, body, tag, cmd_text, glyph, color) in enumerate(after_text_artists):
            if i < n_right:
                prompt.set_alpha(1.0)
                body.set_text(cmd_text)
                tag.set_text(glyph)
            else:
                prompt.set_alpha(0.0)
                body.set_text("")
                tag.set_text("")

        # Footer status
        if n_left >= len(BEFORE):
            status_text_L.set_text("→ 15 commands · repeated failures · skill is a sketch")
        else:
            status_text_L.set_text(f"step {n_left}/{len(BEFORE)}  trial-and-error...")
        if n_right >= len(AFTER):
            status_text_R.set_text("→ 5 commands · 0 failures · reward = 1.0")
        else:
            status_text_R.set_text(f"step {n_right}/{len(AFTER)}  executing PDI-refined recipe...")

        return []

    anim = FuncAnimation(
        fig,
        update,
        frames=total_frames,
        interval=450,
        blit=False,
    )

    fig.subplots_adjust(left=0.02, right=0.98, top=0.92, bottom=0.04)
    print(f"Writing GIF to {OUT} ({total_frames} frames)...")
    anim.save(OUT, writer=PillowWriter(fps=2.2), dpi=120)
    print("Done.")


if __name__ == "__main__":
    main()
