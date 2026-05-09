#!/usr/bin/env python3
"""Generate an animated GIF of PDI exploration dynamics for the two case-study tasks.

Key visual design choices:
  - Row 1 (w/ PDI intervention): green sash, green-tinted panel, check-mark at the
    step where the agent finally hits PASS (so viewers see that an early stop is a
    SUCCESS, not truncation).
  - Row 2 (w/o PDI, observe-only): red sash, red-tinted panel, cross-mark at the
    final attempt showing 0 successes after the budget is exhausted.
  - Shared four lines per panel: phi_exec, phi_plan, phi_oss, warmup-weighted PDI.

Output: blog/assets/img/exploration_dynamics_pdi_two_tasks.gif
"""

from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
import matplotlib.patches as mpatches

CODE_DIR = Path(__file__).resolve().parents[1]
BLOG_IMG = CODE_DIR.parent / "blog" / "assets" / "img"
OUT = BLOG_IMG / "exploration_dynamics_pdi_two_tasks.gif"

TASKS = [
    ("3d-scan-calc", "Task: 3d-scan-calc"),
    ("manufacturing-codebook-normalization", "Task: manufacturing-codebook-normalization"),
]

SKILLS_ROOT = CODE_DIR / "spark_skills_gen" / "skills_gen_result"
TAU_SOFT = -0.5


def load_attempts(tag: str, task: str) -> dict:
    p = SKILLS_ROOT / tag / task / "attempts.json"
    return json.loads(p.read_text())


def extract(data: dict) -> dict:
    hist = data["pdi"]["history"]
    attempts = data["attempts"]
    steps = [h["step"] for h in hist]
    # Identify the first attempt that returned PASS, if any.
    pass_attempt = next((a["attempt"] for a in attempts if a["status"] == "PASS"), None)
    final_status = attempts[-1]["status"] if attempts else "UNKNOWN"
    final_reward = attempts[-1]["reward"] if attempts else 0.0
    return {
        "step": steps,
        "phi_exec": [h["proxy_exec"] for h in hist],
        "phi_plan": [h["proxy_plan"] for h in hist],
        "phi_oss": [h["proxy_oss"] for h in hist],
        "pdi": [h["weighted_pdi"] for h in hist],
        "triggered": [h["triggered"] for h in hist],
        "level": [h["level"] for h in hist],
        "n_attempts": len(attempts),
        "pass_attempt": pass_attempt,
        "final_status": final_status,
        "final_reward": final_reward,
    }


def main():
    BLOG_IMG.mkdir(parents=True, exist_ok=True)

    panels = []
    for task_id, title in TASKS:
        panels.append(
            {
                "title": title,
                "pdi_on": extract(load_attempts("all_model_pdi", task_id)),
                "pdi_off": extract(load_attempts("all_model_observe_only", task_id)),
            }
        )

    # Use the LONGEST history across all four panels for a common x-axis so that
    # early-success panels visibly stop before the x-axis ends.
    max_steps = max(
        max(len(p["pdi_on"]["step"]), len(p["pdi_off"]["step"])) for p in panels
    )
    x_right = max_steps - 0.3

    # 2 columns (one per task) x 2 rows (on / off)
    fig, axes = plt.subplots(
        2, 2, figsize=(13.0, 7.4), sharex=False, sharey=False,
        gridspec_kw={"hspace": 0.45, "wspace": 0.22},
    )

    COLORS = {
        "phi_exec": "#15803d",
        "phi_plan": "#b45309",
        "phi_oss":  "#dc2626",
        "pdi":      "#1a56db",
    }
    LABELS = {
        "phi_exec": r"$\phi_{\mathrm{exec}}$",
        "phi_plan": r"$\phi_{\mathrm{plan}}$",
        "phi_oss":  r"$\phi_{\mathrm{oss}}$",
        "pdi":      r"weighted PDI",
    }

    ROW_STYLE = {
        0: {  # w/ PDI intervention
            "tint": "#ecfdf5",
            "accent": "#059669",
            "label": "w/ PDI intervention",
            "icon": "✓",
            "sash_bg": "#059669",
        },
        1: {  # observe-only
            "tint": "#fef2f2",
            "accent": "#b91c1c",
            "label": "w/o PDI (observe-only)",
            "icon": "✗",
            "sash_bg": "#b91c1c",
        },
    }

    plot_state = []
    for col, panel in enumerate(panels):
        for row, cond_key in [(0, "pdi_on"), (1, "pdi_off")]:
            ax = axes[row, col]
            d = panel[cond_key]
            style = ROW_STYLE[row]

            ax.set_xlim(-0.4, x_right)
            ymin = min([*d["phi_exec"], *d["phi_plan"], *d["phi_oss"], *d["pdi"], TAU_SOFT]) - 0.5
            ymax = max([*d["phi_exec"], *d["phi_plan"], *d["phi_oss"], *d["pdi"], 1.1]) + 0.8
            ax.set_ylim(ymin, ymax)

            # Tinted background so the row identity (with/without PDI) is immediate
            ax.set_facecolor(style["tint"])

            ax.axhline(0, color="#94a3b8", linewidth=0.8, alpha=0.8)
            ax.axhline(TAU_SOFT, color="#b45309", linewidth=0.9, linestyle="--", alpha=0.6)
            ax.text(
                x_right - 0.05, TAU_SOFT,
                r"$\tau=-0.5$", fontsize=8, color="#b45309",
                va="bottom", ha="right",
            )

            for spine_name in ["top", "right"]:
                ax.spines[spine_name].set_visible(False)
            # Strong side-border that matches the row accent
            ax.spines["left"].set_color(style["accent"])
            ax.spines["left"].set_linewidth(2.2)
            ax.spines["bottom"].set_color("#cbd5e1")
            ax.tick_params(colors="#475569", labelsize=9)
            ax.grid(True, linestyle=":", linewidth=0.6, color="#e2e8f0", alpha=0.9)
            ax.set_xticks(np.arange(0, max_steps))

            # Task title on top row only
            if row == 0:
                ax.set_title(panel["title"], fontsize=11.5, fontweight="bold",
                             color="#0f172a", pad=10)

            # Row label as a colored sash on the left
            ax.text(
                -0.15, 0.5, f"{style['icon']}  {style['label']}",
                transform=ax.transAxes,
                fontsize=10.5, fontweight="bold",
                color="#ffffff",
                rotation=90, va="center", ha="center",
                bbox=dict(boxstyle="round,pad=0.35",
                          facecolor=style["sash_bg"], edgecolor="none"),
            )

            if row == 1:
                ax.set_xlabel("reflection step k", fontsize=9, color="#475569")
            ax.set_ylabel("value", fontsize=9, color="#475569")

            # Lines
            lines = {}
            for key in ["phi_exec", "phi_plan", "phi_oss", "pdi"]:
                (ln,) = ax.plot(
                    [], [],
                    color=COLORS[key],
                    linewidth=2.4 if key == "pdi" else 1.7,
                    marker="o",
                    markersize=5.0 if key == "pdi" else 3.5,
                    label=LABELS[key],
                    alpha=0.95,
                )
                lines[key] = ln

            trigger_soft = ax.scatter(
                [], [], s=140, facecolor="none",
                edgecolor="#f59e0b", linewidth=2.2, zorder=5, label="soft trigger",
            )
            trigger_strong = ax.scatter(
                [], [], s=200, facecolor="none",
                edgecolor="#dc2626", linewidth=2.4, zorder=6, label="strong trigger",
            )

            # Outcome annotation (hidden until animation reveals it)
            outcome = ax.text(
                x_right - 0.15, ymax - 0.35, "",
                fontsize=11.5, fontweight="bold",
                color="#ffffff",
                va="top", ha="right",
                bbox=dict(boxstyle="round,pad=0.45",
                          facecolor=style["accent"], edgecolor="none", alpha=0.0),
                zorder=9,
            )

            # Status bar at top-left of each panel: "attempt k/N · status"
            status = ax.text(
                0.015, 0.965, "",
                transform=ax.transAxes,
                fontsize=9.5, color=style["accent"],
                va="top", ha="left", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.25",
                          facecolor="#ffffff", edgecolor=style["accent"],
                          linewidth=1.0, alpha=0.9),
                zorder=8,
            )

            plot_state.append(
                dict(
                    ax=ax,
                    lines=lines,
                    trigger_soft=trigger_soft,
                    trigger_strong=trigger_strong,
                    data=d,
                    row=row,
                    style=style,
                    outcome=outcome,
                    status=status,
                )
            )

    # Shared legend + title
    handles, labels = plot_state[0]["ax"].get_legend_handles_labels()
    fig.legend(
        handles, labels,
        loc="lower center", ncol=6, frameon=False, fontsize=10.5,
        bbox_to_anchor=(0.5, -0.015),
    )
    fig.suptitle(
        "Online PDI intervention — exploration dynamics\n(top: w/ PDI · bottom: observe-only control)",
        fontsize=13, fontweight="bold", color="#0f172a", y=0.985,
    )

    EMPTY = np.zeros((0, 2))

    def init():
        artists = []
        for st in plot_state:
            for ln in st["lines"].values():
                ln.set_data([], [])
                artists.append(ln)
            st["trigger_soft"].set_offsets(EMPTY)
            st["trigger_strong"].set_offsets(EMPTY)
            st["outcome"].set_text("")
            st["outcome"].get_bbox_patch().set_alpha(0.0)
            st["status"].set_text("")
            artists.extend([st["trigger_soft"], st["trigger_strong"], st["outcome"], st["status"]])
        return artists

    def update(frame_idx):
        artists = []
        for st in plot_state:
            d = st["data"]
            n = min(frame_idx + 1, len(d["step"]))
            xs = d["step"][:n]
            for key, ln in st["lines"].items():
                ln.set_data(xs, d[key][:n])
                artists.append(ln)

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

            # Per-panel status + outcome
            last_step_shown = n - 1 if n > 0 else -1
            if st["row"] == 0:  # w/ PDI
                if n >= len(d["step"]):
                    # Reveal the outcome bubble
                    if d["pass_attempt"] is not None:
                        msg = f"✓  SOLVED · attempt {d['pass_attempt']} · reward = {d['final_reward']:.1f}"
                    else:
                        msg = f"final status: {d['final_status']}"
                    st["outcome"].set_text(msg)
                    st["outcome"].get_bbox_patch().set_alpha(0.95)
                    st["status"].set_text(
                        f"reflection steps complete · {len(d['step'])}/{d['n_attempts']} attempts logged"
                    )
                else:
                    st["outcome"].get_bbox_patch().set_alpha(0.0)
                    st["outcome"].set_text("")
                    st["status"].set_text(f"reflection step {last_step_shown+1} / {len(d['step'])}")
            else:  # observe-only
                if n >= len(d["step"]):
                    msg = f"✗  {d['n_attempts']} attempts · 0 successes · reward = {d['final_reward']:.1f}"
                    st["outcome"].set_text(msg)
                    st["outcome"].get_bbox_patch().set_alpha(0.95)
                    st["status"].set_text(
                        f"budget exhausted · {d['n_attempts']} attempts logged"
                    )
                else:
                    st["outcome"].get_bbox_patch().set_alpha(0.0)
                    st["outcome"].set_text("")
                    st["status"].set_text(f"reflection step {last_step_shown+1} / {len(d['step'])}")

            artists.extend([st["trigger_soft"], st["trigger_strong"], st["outcome"], st["status"]])
        return artists

    n_reveal = max_steps + 1
    hold_frames = 10
    total_frames = n_reveal + hold_frames

    def frame_mapper(i):
        return min(i, n_reveal - 1)

    anim = FuncAnimation(
        fig,
        lambda i: update(frame_mapper(i)),
        init_func=init,
        frames=total_frames,
        interval=520,
        blit=False,
    )

    fig.subplots_adjust(left=0.09, right=0.985, top=0.88, bottom=0.12)
    print(f"Writing GIF to {OUT} ({total_frames} frames)...")
    anim.save(OUT, writer=PillowWriter(fps=1.9), dpi=120)
    print("Done.")


if __name__ == "__main__":
    main()
