#!/usr/bin/env python3
"""Generate an animated GIF of PDI exploration dynamics for the two case-study tasks.

Reads real data from spark_skills_gen/skills_gen_result/:
  - all_model_pdi/<task>/attempts.json           -> "w/ PDI" condition
  - all_model_observe_only/<task>/attempts.json  -> "w/o PDI" (observe-only) control

For each task produces a two-row panel animation showing the four PDI signals
(phi_exec, phi_plan, phi_oss, warmup-weighted proxy PDI) progressively drawn step by step,
with soft/strong intervention triggers annotated.

Output: blog/assets/img/exploration_dynamics_pdi_two_tasks.gif
"""

from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

CODE_DIR = Path(__file__).resolve().parents[1]
BLOG_IMG = CODE_DIR.parent / "blog" / "assets" / "img"
OUT = BLOG_IMG / "exploration_dynamics_pdi_two_tasks.gif"

TASKS = [
    ("3d-scan-calc", "Task: 3d-scan-calc"),
    ("manufacturing-codebook-normalization", "Task: manufacturing-codebook-normalization"),
]

SKILLS_ROOT = CODE_DIR / "spark_skills_gen" / "skills_gen_result"
TAU_SOFT = -0.5


def load_history(tag: str, task: str):
    p = SKILLS_ROOT / tag / task / "attempts.json"
    data = json.loads(p.read_text())
    return data["pdi"]["history"]


def extract(series):
    steps = [h["step"] for h in series]
    return {
        "step": steps,
        "phi_exec": [h["proxy_exec"] for h in series],
        "phi_plan": [h["proxy_plan"] for h in series],
        "phi_oss": [h["proxy_oss"] for h in series],
        "pdi": [h["weighted_pdi"] for h in series],
        "triggered": [h["triggered"] for h in series],
        "level": [h["level"] for h in series],
    }


def main():
    BLOG_IMG.mkdir(parents=True, exist_ok=True)

    panels = []
    for task_id, title in TASKS:
        panels.append(
            {
                "title": title,
                "pdi_on": extract(load_history("all_model_pdi", task_id)),
                "pdi_off": extract(load_history("all_model_observe_only", task_id)),
            }
        )

    # Global frame count = max steps across all panels
    max_steps = max(
        max(len(p["pdi_on"]["step"]), len(p["pdi_off"]["step"])) for p in panels
    )

    # 2 columns x 2 rows: row 1 w/ PDI, row 2 w/o PDI (observe-only)
    fig, axes = plt.subplots(
        2, 2, figsize=(12.5, 6.4), sharex=False, sharey=False,
        gridspec_kw={"hspace": 0.38, "wspace": 0.22},
    )

    COLORS = {
        "phi_exec": "#15803d",    # green — execution grounding
        "phi_plan": "#b45309",    # amber — plan copying
        "phi_oss":  "#dc2626",    # red   — ossification
        "pdi":      "#1a56db",    # blue  — weighted PDI (the decision signal)
    }
    LABELS = {
        "phi_exec": r"$\phi_{\mathrm{exec}}$",
        "phi_plan": r"$\phi_{\mathrm{plan}}$",
        "phi_oss":  r"$\phi_{\mathrm{oss}}$",
        "pdi":      r"weighted PDI",
    }

    # Pre-configure each subplot
    plot_state = []
    for col, panel in enumerate(panels):
        for row, cond_key, cond_label in [
            (0, "pdi_on",  "w/ PDI intervention"),
            (1, "pdi_off", "w/o PDI (observe-only)"),
        ]:
            ax = axes[row, col]
            d = panel[cond_key]
            ax.set_xlim(-0.3, max(max_steps, len(d["step"])) - 0.7)
            ymin = min([*d["phi_exec"], *d["phi_plan"], *d["phi_oss"], *d["pdi"], TAU_SOFT]) - 0.4
            ymax = max([*d["phi_exec"], *d["phi_plan"], *d["phi_oss"], *d["pdi"], 1.1]) + 0.4
            ax.set_ylim(ymin, ymax)
            ax.axhline(0, color="#94a3b8", linewidth=0.8, linestyle="-", alpha=0.7)
            ax.axhline(TAU_SOFT, color="#b45309", linewidth=0.9, linestyle="--", alpha=0.7)
            ax.text(
                max_steps - 1.2, TAU_SOFT,
                r"$\tau=-0.5$",
                fontsize=8, color="#b45309", va="bottom", ha="right",
            )
            ax.set_facecolor("#fbfcfe")
            for spine in ["top", "right"]:
                ax.spines[spine].set_visible(False)
            ax.spines["left"].set_color("#cbd5e1")
            ax.spines["bottom"].set_color("#cbd5e1")
            ax.tick_params(colors="#475569", labelsize=9)
            ax.grid(True, linestyle=":", linewidth=0.6, color="#e2e8f0", alpha=0.9)

            if row == 0:
                ax.set_title(panel["title"], fontsize=11, fontweight="bold", color="#0f172a", pad=8)
            ax.set_ylabel(cond_label, fontsize=9, color="#475569")
            if row == 1:
                ax.set_xlabel("reflection step k", fontsize=9, color="#475569")

            # Line handles (empty data), to be extended each frame
            lines = {}
            for key in ["phi_exec", "phi_plan", "phi_oss", "pdi"]:
                (ln,) = ax.plot(
                    [], [],
                    color=COLORS[key],
                    linewidth=2.2 if key == "pdi" else 1.7,
                    marker="o",
                    markersize=4.5 if key == "pdi" else 3.5,
                    label=LABELS[key],
                    alpha=0.95,
                )
                lines[key] = ln
            trigger_soft = ax.scatter(
                [], [], s=120, facecolor="none",
                edgecolor="#f59e0b", linewidth=2.0, zorder=5, label="soft trigger",
            )
            trigger_strong = ax.scatter(
                [], [], s=170, facecolor="none",
                edgecolor="#dc2626", linewidth=2.2, zorder=6, label="strong trigger",
            )

            plot_state.append(
                dict(
                    ax=ax,
                    lines=lines,
                    trigger_soft=trigger_soft,
                    trigger_strong=trigger_strong,
                    data=d,
                    row=row,
                )
            )

    # Legend (only once, at top of figure)
    handles, labels = plot_state[0]["ax"].get_legend_handles_labels()
    fig.legend(
        handles, labels,
        loc="lower center", ncol=6, frameon=False, fontsize=10,
        bbox_to_anchor=(0.5, -0.015),
    )
    fig.suptitle(
        "Online PDI intervention — exploration dynamics",
        fontsize=13, fontweight="bold", color="#0f172a", y=0.985,
    )

    # Animate: progressively reveal steps up to frame index
    EMPTY = np.zeros((0, 2))

    def init():
        artists = []
        for st in plot_state:
            for ln in st["lines"].values():
                ln.set_data([], [])
                artists.append(ln)
            st["trigger_soft"].set_offsets(EMPTY)
            st["trigger_strong"].set_offsets(EMPTY)
            artists.extend([st["trigger_soft"], st["trigger_strong"]])
        return artists

    def update(frame_idx):
        artists = []
        # frame_idx is the LAST step index to show (inclusive)
        for st in plot_state:
            d = st["data"]
            n = min(frame_idx + 1, len(d["step"]))
            xs = d["step"][:n]
            for key, ln in st["lines"].items():
                ln.set_data(xs, d[key][:n])
                artists.append(ln)
            # triggers
            soft_pts = [
                [d["step"][i], d["pdi"][i]]
                for i in range(n) if d["level"][i] == "soft"
            ]
            strong_pts = [
                [d["step"][i], d["pdi"][i]]
                for i in range(n) if d["level"][i] == "strong"
            ]
            st["trigger_soft"].set_offsets(np.array(soft_pts) if soft_pts else EMPTY)
            st["trigger_strong"].set_offsets(np.array(strong_pts) if strong_pts else EMPTY)
            artists.extend([st["trigger_soft"], st["trigger_strong"]])
        return artists

    # Pad with a few "hold" frames at the end
    n_reveal = max_steps + 1
    hold_frames = 8
    total_frames = n_reveal + hold_frames

    def frame_mapper(i):
        return min(i, n_reveal - 1)

    anim = FuncAnimation(
        fig,
        lambda i: update(frame_mapper(i)),
        init_func=init,
        frames=total_frames,
        interval=520,       # ms
        blit=False,
    )

    fig.subplots_adjust(left=0.08, right=0.98, top=0.90, bottom=0.12)
    print(f"Writing GIF to {OUT} ({total_frames} frames)...")
    anim.save(OUT, writer=PillowWriter(fps=1.9), dpi=120)
    print("Done.")


if __name__ == "__main__":
    main()
