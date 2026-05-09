#!/usr/bin/env python3
"""Animated GIF of the paper Figure 7 "Exploration dynamics (PDI)".

Data and visual style match paper/figure/exploration_dynamics_pdi_two_tasks.py
exactly (1x4 panels, four signals, Soft/Strong triggers, central w/ vs w/o
separator). We simply reveal the trajectory step by step over time.

Output: blog/assets/img/exploration_dynamics_pdi_two_tasks.gif
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

# ---------------------------------------------------------------------------
# Output target
# ---------------------------------------------------------------------------
OUTPUT = (
    Path(__file__).resolve().parents[1].parent
    / "blog" / "assets" / "img" / "exploration_dynamics_pdi_two_tasks.gif"
)

# ---------------------------------------------------------------------------
# Panels — verbatim from the paper figure script
# ---------------------------------------------------------------------------
PANELS: list[tuple[str, str, str]] = [
    ("manufacturing-codebook-normalizatio", "with PDI", """
Step    Exec    Plan    Oss    Raw     W     wPDI    Trigger
1       +0.276  +0.000  +0.000  +0.276  0.00  +0.000  —
2       +0.305  -0.186  -0.147  -1.000  0.50  -0.500  —
3       +0.287  -0.281  -0.876  -2.680  1.00  -2.680  soft
4       +0.321  -0.354  -0.651  -0.391  1.00  -0.391  —
5       +0.234  -0.270  -0.666  -2.725  1.00  -2.725  soft
6       +0.302  -0.163  -0.370  +1.208  1.00  +1.208  —
7       +0.322  -0.257  -0.452  +0.653  1.00  +0.653  —
"""),
    ("manufacturing-codebook-normalizatio", "without PDI", """
Step	Exec	Plan	Oss	Raw	W	wPDI
1	+0.234	+0.000	+0.000	+0.234	0.00	+0.000
2	+0.328	-0.175	-0.317	-1.000	0.50	-0.500
3	+0.239	-0.339	-0.886	-3.181	1.00	-3.181
4	+0.283	-0.437	-0.667	-1.468	1.00	-1.468
5	+0.200	-0.374	-0.661	-2.464	1.00	-2.464
6   +0.230	-0.369	-0.955	-3.207	1.00	-3.207
7   +0.297	-0.509	-0.787	-2.486	1.00	-2.486
    """),
    ("3d-scan-calc", "with PDI", """
Step    Exec    Plan    Oss    Raw     W     wPDI    Trigger
1       +0.307  +0.000  +0.000  +0.307  0.00  +0.000  —
2       +0.380  -0.300  -0.720  -1.000  0.50  -0.500  —
3       +0.418  -0.296  -0.842  -0.481  1.00  -0.481  —
4       +0.440  -0.359  -0.867  -0.529  1.00  -0.529  soft
5       +0.369  -0.312  -0.910  -1.458  1.00  -1.458  strong
6       +0.000  +0.000  -0.500  -0.304  1.00  -0.304  —
7       +0.000  +0.000  +0.000  +1.091  1.00  +1.091  —
"""),
    ("3d-scan-calc", "without PDI", """
Step	Exec	Plan	Oss	Raw	W	wPDI
1	+0.275	+0.000	+0.000	+0.275	0.00	+0.000
2	+0.308	-0.333	-0.768	-1.000	0.50	-0.500
3	+0.353	-0.281	-0.804	+0.009	1.00	+0.009
4	+0.367	-0.329	-0.794	-0.136	1.00	-0.136
5	+0.278	-0.411	-0.975	-2.883	1.00	-2.883
6	+0.257	-0.480	-0.814	-2.744	1.00	-2.744
7	+0.000	-0.508	-0.420	-2.659	1.00	-2.659
"""),
]

SERIES_STYLES = {
    "exec": {"label": "Exec Grounding", "color": "#2ec4b6", "marker": "o", "linewidth": 1.9, "markersize": 4.8, "zorder": 4},
    "plan": {"label": "Plan Stagnation", "color": "#ff6b6b", "marker": "o", "linewidth": 1.8, "markersize": 4.6, "zorder": 3},
    "oss":  {"label": "Ossification",   "color": "#f6bd16", "marker": "o", "linewidth": 1.8, "markersize": 4.6, "zorder": 3},
    "wpdi": {"label": "Weighted PDI",   "color": "#8b5cf6", "marker": "o", "linewidth": 2.4, "markersize": 5.2, "zorder": 5},
}
TRIGGER_STYLES = {
    "soft":   {"label": "Soft trigger",   "color": "#b5651d"},
    "strong": {"label": "Strong trigger", "color": "#8c2f39"},
}
PLOTTED_COLUMNS = ("exec", "plan", "oss", "wpdi")


def configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 140,
            "savefig.dpi": 140,
            "font.family": "serif",
            "font.serif": ["DejaVu Serif", "Times New Roman", "Times"],
            "mathtext.fontset": "stix",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.95,
            "axes.labelsize": 11.5,
            "axes.titlesize": 10.8,
            "xtick.labelsize": 9.2,
            "ytick.labelsize": 9.2,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "xtick.major.width": 0.8,
            "ytick.major.width": 0.8,
            "legend.fontsize": 8.2,
        }
    )


def normalize_trigger(token: str) -> str | None:
    t = token.strip().lower()
    if t in {"—", "-", "--", "none", "null", "na", "n/a"}:
        return None
    return t


def parse_table(table_text: str) -> list[dict]:
    rows: list[dict] = []
    for raw_line in table_text.strip().splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("step"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) == 7:
            parts.append("—")
        if len(parts) != 8:
            raise ValueError(f"Malformed row: {raw_line!r}")
        rows.append(
            {
                "step": int(parts[0]),
                "exec": float(parts[1]),
                "plan": float(parts[2]),
                "oss":  float(parts[3]),
                "raw":  float(parts[4]),
                "weight": float(parts[5]),
                "wpdi": float(parts[6]),
                "trigger": normalize_trigger(parts[7]),
            }
        )
    return sorted(rows, key=lambda r: r["step"])


def round_to_step(value: float, step: float, *, up: bool) -> float:
    scaled = value / step
    r = math.ceil(scaled) if up else math.floor(scaled)
    return r * step


def compute_y_limits(all_rows: list[list[dict]]) -> tuple[float, float]:
    values = [float(r[c]) for rows in all_rows for r in rows for c in PLOTTED_COLUMNS]
    lo, hi = min(values), max(values)
    span = max(hi - lo, 1.0)
    pad = max(0.25, 0.08 * span)
    return round_to_step(lo - pad, 0.5, up=False), round_to_step(hi + pad, 0.5, up=True)


def annotate_group_contrast(fig, left_ax, right_ax, left_label, right_label) -> None:
    left_pos = left_ax.get_position()
    right_pos = right_ax.get_position()
    center_x = (left_pos.x1 + right_pos.x0) / 2
    center_y = left_pos.y0 + 0.43 * (left_pos.y1 - left_pos.y0)
    pair_height = left_pos.y1 - left_pos.y0
    line_half = 0.10 * pair_height
    label_y = center_y + 0.002
    arrow_y = label_y - 0.028
    arrow_span = 0.046
    label_gap = 0.004

    fig.add_artist(
        Line2D([center_x, center_x], [center_y - line_half, center_y + line_half],
               transform=fig.transFigure, color="0.45", linestyle=(0, (3, 2)),
               linewidth=1.35, zorder=2)
    )
    fx = [pe.withStroke(linewidth=2.4, foreground="white", alpha=0.96)]
    fig.text(center_x - label_gap, label_y, left_label, ha="right", va="center",
             fontsize=7.8, fontweight="bold", fontstyle="italic",
             color="#2f6f2f", path_effects=fx)
    fig.text(center_x + label_gap, label_y, right_label, ha="left", va="center",
             fontsize=7.8, fontweight="bold", fontstyle="italic",
             color="#9c3d18", path_effects=fx)
    fig.add_artist(FancyArrowPatch(
        (center_x - 0.007, arrow_y), (center_x - arrow_span, arrow_y),
        transform=fig.transFigure,
        arrowstyle="Simple,tail_width=0.7,head_width=4.8,head_length=5.8",
        mutation_scale=1, linewidth=0, color="#2f6f2f", shrinkA=0, shrinkB=0, alpha=0.96))
    fig.add_artist(FancyArrowPatch(
        (center_x + 0.007, arrow_y), (center_x + arrow_span, arrow_y),
        transform=fig.transFigure,
        arrowstyle="Simple,tail_width=0.7,head_width=4.8,head_length=5.8",
        mutation_scale=1, linewidth=0, color="#9c3d18", shrinkA=0, shrinkB=0, alpha=0.96))


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    configure_style()

    parsed = [(name, grp, parse_table(tbl)) for name, grp, tbl in PANELS]
    all_rows = [rows for _, _, rows in parsed]
    y_limits = compute_y_limits(all_rows)
    max_steps = max(len(r) for r in all_rows)

    n_panels = len(parsed)
    fig, axes = plt.subplots(1, n_panels, figsize=(3.6 * n_panels, 3.8), sharey=True)
    axes = np.atleast_1d(axes)

    # Pre-configure axes (static layer)
    per_panel = []
    for ax, (task_name, group, rows) in zip(axes, parsed):
        steps = np.asarray([r["step"] for r in rows])
        ax.axhline(0.0, color="0.35", linestyle=(0, (4, 2)), linewidth=1.25, zorder=1)
        ax.grid(axis="y", linestyle=":", linewidth=0.65, alpha=0.28)
        ax.set_axisbelow(True)
        ax.set_xlim(steps.min() - 0.25, steps.max() + 0.25)
        ax.set_ylim(*y_limits)
        ax.set_xticks(steps)
        ax.yaxis.set_major_locator(mticker.MultipleLocator(0.5))
        ax.set_xlabel("Step")

        # Task label chip (upper-left of panel)
        ax.text(0.018, 1.02, task_name, transform=ax.transAxes,
                ha="left", va="bottom", fontsize=7.8, fontweight="semibold",
                color="0.30")

        # Animated line objects — one per series
        lines = {}
        for col, style in SERIES_STYLES.items():
            (ln,) = ax.plot([], [],
                            color=style["color"], marker=style["marker"],
                            linewidth=style["linewidth"], markersize=style["markersize"],
                            zorder=style["zorder"], solid_capstyle="round")
            lines[col] = ln

        # Triggers: precompute the step and a placeholder axvline / chip;
        # we reveal them when the frame reaches that step.
        triggers = []  # list of (step, level, vline, text_chip)
        top_y = y_limits[1] - 0.08 * (y_limits[1] - y_limits[0])
        for r in rows:
            lvl = r["trigger"]
            if lvl not in TRIGGER_STYLES:
                continue
            st = TRIGGER_STYLES[lvl]
            vl = ax.axvline(r["step"], color=st["color"], linestyle=(0, (4, 2.4)),
                            linewidth=1.8, alpha=0.0, zorder=1)
            chip = ax.text(
                r["step"], top_y, st["label"].split()[0],
                rotation=90, ha="center", va="top", fontsize=8.4,
                fontweight="bold", color=st["color"],
                bbox={"boxstyle": "round,pad=0.16", "facecolor": "white",
                      "edgecolor": st["color"], "linewidth": 0.5, "alpha": 0.0},
                alpha=0.0,
            )
            triggers.append((r["step"], lvl, vl, chip))

        per_panel.append({"ax": ax, "rows": rows, "lines": lines, "triggers": triggers})

    axes[0].set_ylabel("PDI Score")

    # Legend
    legend_handles = [
        Line2D([0], [0], color=s["color"], marker=s["marker"],
               linewidth=s["linewidth"], markersize=s["markersize"], label=s["label"])
        for s in SERIES_STYLES.values()
    ]
    for tname in ("soft", "strong"):
        st = TRIGGER_STYLES[tname]
        legend_handles.append(Line2D([0], [0], color=st["color"],
                                     linestyle=(0, (4, 2.4)), linewidth=1.8,
                                     label=st["label"]))
    fig.legend(
        handles=legend_handles, loc="upper center",
        bbox_to_anchor=(0.5, 0.975), ncol=len(legend_handles),
        frameon=False, columnspacing=0.85, handlelength=2.0,
        handletextpad=0.45, fontsize=8.6,
    )

    fig.subplots_adjust(left=0.055, right=0.995, bottom=0.20, top=0.85, wspace=0.12)

    # Pairwise w/ vs w/o contrast annotations (panels 0-1 and 2-3)
    pairs = [(0, 1), (2, 3)]
    for li, ri in pairs:
        annotate_group_contrast(fig, axes[li], axes[ri],
                                parsed[li][1], parsed[ri][1])

    # Divider line between the two tasks (between panel 1 and 2)
    if len(axes) >= 4:
        p2 = axes[1].get_position()
        p3 = axes[2].get_position()
        split_x = (p2.x1 + p3.x0) / 2
        y0 = min(ax.get_position().y0 for ax in axes) - 0.02
        y1 = max(ax.get_position().y1 for ax in axes) + 0.01
        fig.add_artist(Line2D([split_x, split_x], [y0, y1],
                              transform=fig.transFigure, color="0.55",
                              linestyle=(0, (3, 3)), linewidth=1.1,
                              alpha=0.9, zorder=1))

    # ----------------------------------------------------------------- animation
    def update(frame: int):
        # frame = last-step index to reveal (0-based → step = frame+1)
        artists = []
        for panel in per_panel:
            rows = panel["rows"]
            n = min(frame + 1, len(rows))
            xs = [r["step"] for r in rows[:n]]
            for col, ln in panel["lines"].items():
                ys = [r[col] for r in rows[:n]]
                ln.set_data(xs, ys)
                artists.append(ln)
            # Reveal triggers once their step is reached
            for step, lvl, vl, chip in panel["triggers"]:
                visible = step <= (frame + 1)
                alpha = 0.98 if visible else 0.0
                vl.set_alpha(alpha)
                chip.set_alpha(0.98 if visible else 0.0)
                if visible:
                    chip.get_bbox_patch().set_alpha(0.84)
                else:
                    chip.get_bbox_patch().set_alpha(0.0)
                artists.extend([vl, chip])
        return artists

    total_reveal = max_steps
    hold = 6
    frames = total_reveal + hold

    def mapper(i: int) -> int:
        return min(i, total_reveal - 1)

    anim = FuncAnimation(fig, lambda i: update(mapper(i)),
                         frames=frames, interval=480, blit=False)

    print(f"Writing GIF to {OUTPUT} ({frames} frames)...")
    anim.save(OUTPUT, writer=PillowWriter(fps=2.1), dpi=140)
    plt.close(fig)
    print("Done.")


if __name__ == "__main__":
    main()
