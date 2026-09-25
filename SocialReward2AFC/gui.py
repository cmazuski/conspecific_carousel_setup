# SocialReward2AFC/gui.py — GUIs for 2AFC social reward sessions
#
# PerformanceGUI.update(df):
#   Detects phase from columns:
#     "poked_port" only (Phase 1)      → autoshaping mode
#     "rt_dooropen" + "poked_port"     → training mode (phases 2–4)
#     "trial_type" in columns          → task mode (forced/mixed/free)
#
# SensorGUI: live sensor state (ports A, B, C, door proximity, table)

import matplotlib.pyplot as plt
from matplotlib.patches import Circle
import numpy as np
import pandas as pd

# Criterion constants come from the session that applies them, so the plotted
# bar and the advance decision can never drift apart.
from SocialReward2AFC.MixedChoice import (
    BLOCK_SIZE as MIXED_BLOCK_SIZE,
    ADVANCE_CRIT as MIXED_ADVANCE_CRIT,
    MIN_FREE_TRIALS as MIXED_MIN_FREE_TRIALS,
)


PORT_COLORS = {"A": "#2196F3", "B": "#4CAF50", "C": "#FF9800"}


def _outcome_color(outcome):
    return {
        "hit":          "green",
        "rewarded":     "green",
        "error":        "red",
        "wrong_port":   "red",
        "miss":         "orange",
        "missed":       "orange",
        "correct_rejection": "blue",
        "door_failed":  "black",   # hardware fault, not a behavioural outcome
    }.get(str(outcome), "gray")


def _port_color(port):
    return PORT_COLORS.get(str(port), "gray")


# ── Performance GUI ───────────────────────────────────────────────────────────

class PerformanceGUI:

    def __init__(self, animal_name="Animal", phase_selection=""):
        plt.ion()
        self._animal   = animal_name
        self._phase    = phase_selection

        self.fig = plt.figure(figsize=(11, 8))
        self._base_title = f"2AFC — {animal_name}"
        if phase_selection:
            self._base_title += f" | Phase {phase_selection}"
        self.fig.suptitle(self._base_title, fontsize=13)

        gs = self.fig.add_gridspec(3, 2, hspace=0.55, wspace=0.35)
        self.ax_rt      = self.fig.add_subplot(gs[0, :])   # full width
        self.ax_choice  = self.fig.add_subplot(gs[1, 0])
        self.ax_block   = self.fig.add_subplot(gs[1, 1])
        self.ax_sampling = self.fig.add_subplot(gs[2, 0])
        self.ax_ratio   = self.fig.add_subplot(gs[2, 1])

        for ax, title, ylabel, xlabel in [
            (self.ax_rt,      "Reaction time",             "RT (s)",        "Trial"),
            (self.ax_choice,  "Port choice / outcome",     "Port",          "Trial"),
            (self.ax_block,   "Block hit rate",            "Hit rate (%)",  "Block"),
            (self.ax_sampling,"Sampling time",             "Sampling (s)",  "Trial"),
            (self.ax_ratio,   "Forced ratio (Mixed only)", "Forced ratio",  "Block"),
        ]:
            ax.set_title(title, fontsize=10)
            ax.set_ylabel(ylabel, fontsize=9)
            ax.set_xlabel(xlabel, fontsize=9)
            ax.grid(True)

        self.ax_block.set_ylim(0, 100)
        self.ax_ratio.set_ylim(-0.05, 1.05)

        plt.tight_layout(rect=[0, 0, 1, 0.96])
        plt.show(block=False)

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, df: pd.DataFrame, current_trial_port=None):
        if df is None or df.empty:
            self.fig.canvas.draw()
            self.fig.canvas.flush_events()
            return

        if "trial_num" in df.columns and not df["trial_num"].isna().all():
            trial = int(df["trial_num"].max()) + 1
            self.fig.suptitle(f"{self._base_title}  |  Trial: {trial}", fontsize=13)

        if "trial_type" in df.columns:
            self._update_task(df)
        elif "rt_dooropen" in df.columns:
            self._update_training(df)
        else:
            self._update_autoshaping(df)

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    # ── Autoshaping (Phase 1) ─────────────────────────────────────────────────

    def _update_autoshaping(self, df):
        valid = df["rt"].notna()

        self.ax_rt.clear()
        if valid.any():
            for port in ("A", "B"):
                mask = valid & (df["poked_port"] == port)
                if mask.any():
                    self.ax_rt.scatter(
                        df.loc[mask, "trial_num"], df.loc[mask, "rt"],
                        color=_port_color(port), label=f"Port {port}", s=25, zorder=3)
        self.ax_rt.set_title("Reaction time", fontsize=10)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        self.ax_rt.legend(fontsize=8)
        self.ax_rt.grid(True)

        # Port choice
        self.ax_choice.clear()
        port_to_y = {"A": 0, "B": 1}
        for _, row in df.iterrows():
            y = port_to_y.get(row.get("poked_port"), 0.5)
            color = "green" if row.get("reward_triggered") else "red"
            self.ax_choice.scatter(row["trial_num"], y, c=color, s=30)
        self.ax_choice.set_yticks([0, 1])
        self.ax_choice.set_yticklabels(["A", "B"])
        self.ax_choice.set_title("Port choice (green=rewarded)", fontsize=10)
        self.ax_choice.set_ylabel("Port")
        self.ax_choice.set_xlabel("Trial")
        self.ax_choice.set_ylim(-0.5, 1.5)
        self.ax_choice.grid(True, axis="x")

        self._draw_block_bars(self.ax_block, df, "reward_triggered")
        self.ax_sampling.clear()
        self.ax_ratio.clear()

    # ── Training (Phases 2–4) ─────────────────────────────────────────────────

    def _update_training(self, df):
        valid = df["rt"].notna()

        self.ax_rt.clear()
        if valid.any():
            colors = [_port_color(p) for p in df.loc[valid, "poked_port"]]
            self.ax_rt.scatter(
                df.loc[valid, "trial_num"], df.loc[valid, "rt"],
                c=colors, s=25, zorder=3)
        self.ax_rt.set_title("Reaction time (coloured by port poked)", fontsize=10)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        self.ax_rt.grid(True)

        # Choice dots: y=port, colour=outcome
        self.ax_choice.clear()
        port_to_y = {"A": 0, "B": 1}
        for _, row in df.iterrows():
            y = port_to_y.get(row.get("poked_port"), 0.5)
            self.ax_choice.scatter(
                row["trial_num"], y,
                c=_outcome_color(row.get("outcome", "")), s=30)
        self.ax_choice.set_yticks([0, 1])
        self.ax_choice.set_yticklabels(["A", "B"])
        self.ax_choice.set_title("Port choice  (green=reward, red=wrong, orange=miss)",
                                  fontsize=9)
        self.ax_choice.set_ylabel("Port")
        self.ax_choice.set_xlabel("Trial")
        self.ax_choice.set_ylim(-0.5, 1.5)
        self.ax_choice.grid(True, axis="x")

        self._draw_block_bars(self.ax_block, df, "reward_triggered")

        # Sampling time
        self.ax_sampling.clear()
        if "sampling_time" in df.columns and df["sampling_time"].notna().any():
            self.ax_sampling.bar(
                df["trial_num"], df["sampling_time"].fillna(0),
                color="gold", edgecolor="black")
        self.ax_sampling.set_title("Sampling time", fontsize=10)
        self.ax_sampling.set_ylabel("Sampling (s)")
        self.ax_sampling.set_xlabel("Trial")
        self.ax_sampling.grid(True, axis="y")

        self.ax_ratio.clear()

    # ── Task (Forced / Mixed / Free) ──────────────────────────────────────────

    def _update_task(self, df):
        valid = df["rt"].notna()

        # RT coloured by outcome
        self.ax_rt.clear()
        if valid.any():
            colors = [_outcome_color(o) for o in df.loc[valid, "outcome"]]
            self.ax_rt.scatter(
                df.loc[valid, "trial_num"], df.loc[valid, "rt"],
                c=colors, s=25, zorder=3)
            self.ax_rt.plot(
                df.loc[valid, "trial_num"], df.loc[valid, "rt"],
                color="black", linewidth=0.6, alpha=0.4)
        self.ax_rt.set_title("RT  (green=hit, red=error, orange=miss)", fontsize=10)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        self.ax_rt.grid(True)

        # Choice: y = correct_port (A=0, B=1), colour = outcome
        # Triangle shape = forced, circle = free
        self.ax_choice.clear()
        port_to_y = {"A": 0, "B": 1}
        for _, row in df.iterrows():
            y      = port_to_y.get(row.get("correct_port"), 0.5)
            color  = _outcome_color(row.get("outcome", ""))
            marker = "^" if row.get("trial_type") == "forced" else "o"
            self.ax_choice.scatter(
                row["trial_num"], y, c=color, s=35, marker=marker)
        self.ax_choice.set_yticks([0, 1])
        self.ax_choice.set_yticklabels(["Stim→A", "Stim→B"])
        self.ax_choice.set_title(
            "Stimulus  (▲=forced, ●=free | green=hit, red=error)", fontsize=9)
        self.ax_choice.set_ylabel("Correct port")
        self.ax_choice.set_xlabel("Trial")
        self.ax_choice.set_ylim(-0.5, 1.5)
        self.ax_choice.grid(True, axis="x")

        # Block hit rate.
        # Mixed sessions carry block_num, so bars are drawn per real 20-trial
        # block using that block's free trials — the exact quantity
        # MixedChoiceSession._evaluate_block tests against the criterion.
        # Chunking free trials into arbitrary groups of 10 (as this used to do)
        # crosses block boundaries and cannot be compared to the 75 % line.
        self.ax_block.clear()
        if "block_num" in df.columns and "trial_type" in df.columns:
            self._draw_mixed_block_bars(self.ax_block, df)
        elif "trial_type" in df.columns and (df["trial_type"] == "free").any():
            free_df = df[df["trial_type"] == "free"]
            self._draw_block_bars(self.ax_block, free_df, None,
                                  title="Free-trial hit rate (10-trial bins)")
        else:
            self._draw_block_bars(self.ax_block, df, None)

        # Sampling time
        self.ax_sampling.clear()
        if "sampling_time" in df.columns and df["sampling_time"].notna().any():
            # Colour by stimulus (A angle vs B angle)
            colors = [
                PORT_COLORS["A"] if a == self._angle_a(df) else PORT_COLORS["B"]
                for a in df["presentation_angle"]
            ]
            self.ax_sampling.bar(
                df["trial_num"], df["sampling_time"].fillna(0),
                color=colors, edgecolor="black")
        self.ax_sampling.set_title(
            "Sampling time (blue=Stim-A, green=Stim-B)", fontsize=9)
        self.ax_sampling.set_ylabel("Sampling (s)")
        self.ax_sampling.set_xlabel("Trial")
        self.ax_sampling.grid(True, axis="y")

        # Forced ratio progression (mixed only)
        self.ax_ratio.clear()
        if "forced_ratio" in df.columns and "block_num" in df.columns:
            ratio_per_block = (
                df.groupby("block_num")["forced_ratio"].first().reset_index()
            )
            self.ax_ratio.step(
                ratio_per_block["block_num"], ratio_per_block["forced_ratio"],
                where="post", color="purple", linewidth=2)
            self.ax_ratio.set_ylim(-0.05, 1.05)
        self.ax_ratio.set_title("Forced ratio per block", fontsize=10)
        self.ax_ratio.set_ylabel("Forced ratio")
        self.ax_ratio.set_xlabel("Block")
        self.ax_ratio.grid(True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _angle_a(df):
        """Infer angle_a from the data (most common angle for correct_port A)."""
        if "correct_port" in df.columns and "presentation_angle" in df.columns:
            a_trials = df[df["correct_port"] == "A"]["presentation_angle"]
            if not a_trials.empty:
                return a_trials.mode().iloc[0]
        return 90

    @staticmethod
    def _draw_mixed_block_bars(ax, df: pd.DataFrame):
        """One bar per real block: free-trial hit rate, as the criterion sees it.

        Mirrors MixedChoiceSession._evaluate_block — same trials, same
        denominator, same threshold — so a bar clearing the red line means the
        ratio advanced (or will, once the block completes).

        Bar colour encodes the decision:
          green  — criterion met, ratio advances
          blue   — block complete, criterion not met
          grey   — not evaluated (fewer than MIN_FREE_TRIALS scored free trials)
        Hatching marks the block still in progress.
        """
        ax.clear()
        heights, labels, colors, hatches, annots = [], [], [], [], []

        # Only the newest block can still be running. Row count is NOT a test
        # for completeness: a door_failed trial is logged but the animal never
        # chose, and a block whose trials were dropped for other reasons can
        # finish with fewer than BLOCK_SIZE rows.
        last_block = df["block_num"].max()

        for block_num, block in df.groupby("block_num", sort=True):
            # Same denominator as MixedChoiceSession._evaluate_block: free
            # trials the animal actually got to make a choice on.
            free = block[(block["trial_type"] == "free")
                         & (block["outcome"] != "door_failed")]
            n_free = len(free)
            n_hit = int((free["outcome"] == "hit").sum())
            n_failed = int(((block["trial_type"] == "free")
                            & (block["outcome"] == "door_failed")).sum())
            in_progress = block_num == last_block

            if n_free == 0:
                pct = 0.0
            else:
                pct = n_hit / n_free * 100

            if n_free < MIXED_MIN_FREE_TRIALS:
                color = "#BDBDBD"                       # not evaluated
            elif pct >= MIXED_ADVANCE_CRIT * 100:
                color = "#4CAF50"                       # criterion met
            else:
                color = "#2196F3"                       # complete, not met

            heights.append(pct)
            colors.append(color)
            hatches.append("//" if in_progress else "")
            labels.append(f"B{int(block_num)}")
            annot = f"{n_hit}/{n_free}" if n_free else "-"
            if n_failed:
                annot += f"\n({n_failed} door)"   # excluded from the rate
            annots.append(annot)

        x = np.arange(len(heights))
        bars = ax.bar(x, heights, width=0.6, color=colors,
                      edgecolor="black", linewidth=0.5)
        for bar, hatch in zip(bars, hatches):
            if hatch:
                bar.set_hatch(hatch)
        for xi, (h, a) in enumerate(zip(heights, annots)):
            ax.text(xi, min(h + 3, 96), a, ha="center", va="bottom", fontsize=7)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7)
        ax.set_ylim(0, 100)
        ax.axhline(MIXED_ADVANCE_CRIT * 100, color="red",
                   linewidth=1, linestyle="--", alpha=0.8)
        ax.axhline(50, color="gray", linewidth=1, linestyle=":", alpha=0.5)
        ax.set_title(
            f"Free-trial hit rate per {MIXED_BLOCK_SIZE}-trial block "
            f"(criterion {MIXED_ADVANCE_CRIT:.0%}; hatched = in progress)",
            fontsize=8)
        ax.set_ylabel("Hit rate (%)", fontsize=9)
        ax.set_xlabel("Block", fontsize=9)
        ax.grid(True, axis="y")

    @staticmethod
    def _draw_block_bars(ax, df: pd.DataFrame, col=None, title="Block hit rate"):
        ax.clear()
        block_size = 10
        blocks, labels = [], []
        for start in range(0, len(df), block_size):
            block = df.iloc[start : start + block_size]
            if block.empty:
                continue
            if col:
                pct = block[col].mean() * 100
            else:
                pct = (block["outcome"] == "hit").mean() * 100
            t_s = block["trial_num"].iloc[0]
            t_e = block["trial_num"].iloc[-1]
            blocks.append(pct)
            labels.append(f"{t_s}–{t_e}")
        x = np.arange(len(blocks))
        ax.bar(x, blocks, width=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=0, fontsize=7)
        ax.set_ylim(0, 100)
        ax.axhline(75, color="red", linewidth=1, linestyle="--", alpha=0.6)
        ax.axhline(50, color="gray", linewidth=1, linestyle=":", alpha=0.5)
        ax.set_title(title, fontsize=9)
        ax.set_ylabel("Hit rate (%)")
        ax.set_xlabel("Block")
        ax.grid(True, axis="y")

    # ── Close ─────────────────────────────────────────────────────────────────

    def close(self, save_path=None):
        if save_path is not None:
            self.fig.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"[INFO] Performance figure saved: {save_path}")
        plt.ioff()
        plt.close(self.fig)


# ── Sensor GUI ────────────────────────────────────────────────────────────────

class SensorGUI:

    def __init__(self):
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(4.5, 2.2))
        self.fig.canvas.manager.set_window_title("2AFC Sensor State")
        self.ax.axis("off")

        def _circle(cx, cy, label, label_cy):
            c = Circle((cx, cy), 0.10, transform=self.ax.transAxes,
                        facecolor="red", edgecolor="black")
            self.ax.add_patch(c)
            self.ax.text(cx, label_cy, label, ha="center",
                          fontsize=9, transform=self.ax.transAxes)
            return c

        self.circle_a    = _circle(0.15, 0.60, "Port A",  0.74)
        self.circle_b    = _circle(0.50, 0.60, "Port B",  0.74)
        self.circle_c    = _circle(0.85, 0.60, "Port C",  0.74)
        self.circle_door = _circle(0.30, 0.22, "Door",    0.09)
        self.circle_tbl  = _circle(0.70, 0.22, "Table",   0.09)

        self.text_a    = self.ax.text(0.15, 0.50, "", ha="center",
                                      fontsize=7, transform=self.ax.transAxes)
        self.text_b    = self.ax.text(0.50, 0.50, "", ha="center",
                                      fontsize=7, transform=self.ax.transAxes)
        self.text_c    = self.ax.text(0.85, 0.50, "", ha="center",
                                      fontsize=7, transform=self.ax.transAxes)
        self.text_door = self.ax.text(0.30, 0.02, "", ha="center",
                                      fontsize=7, transform=self.ax.transAxes)
        self.text_tbl  = self.ax.text(0.70, 0.02, "", ha="center",
                                      fontsize=7, transform=self.ax.transAxes)

        plt.show(block=False)

    def update(self, snapshot):
        def _ts(t):
            return t.strftime("%H:%M:%S") if t else "–"

        self.circle_a.set_facecolor(
            "green" if snapshot.A == "triggered" else "red")
        self.circle_b.set_facecolor(
            "green" if snapshot.B == "triggered" else "red")
        self.circle_c.set_facecolor(
            "green" if snapshot.C == "triggered" else "red")
        self.circle_door.set_facecolor(
            "green" if snapshot.doorsensor == "triggered" else "red")
        self.circle_tbl.set_facecolor(
            "green" if snapshot.table == "triggered" else "red")

        self.circle_a.set_radius(0.12 if snapshot.A == "triggered" else 0.10)
        self.circle_b.set_radius(0.12 if snapshot.B == "triggered" else 0.10)
        self.circle_c.set_radius(0.12 if snapshot.C == "triggered" else 0.10)

        self.text_a.set_text(_ts(snapshot.tA))
        self.text_b.set_text(_ts(snapshot.tB))
        self.text_c.set_text(_ts(snapshot.tC))
        self.text_door.set_text(_ts(snapshot.tDoorsensor))
        self.text_tbl.set_text(_ts(snapshot.tTable))

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def close(self):
        plt.ioff()
        plt.close(self.fig)
