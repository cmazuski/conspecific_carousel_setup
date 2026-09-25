# SocialChoice/gui.py — GUIs for Social Choice sessions
#
# PerformanceGUI.update(df) detects mode from columns:
#   no "outcome" col                  → learning
#   "outcome" + "rt_a" col            → one-choice
#   "choice_type" col                 → two-choice
#
# SensorGUI: live sensor state (ports A, B, C, door proximity, table)

import matplotlib.pyplot as plt
from matplotlib.patches import Circle
import numpy as np
import pandas as pd


PORT_COLORS = {"A": "#2196F3", "B": "#4CAF50", "C": "#FF9800"}


def _outcome_color(outcome):
    return {
        "hit":    "green",
        "miss":   "orange",
        "social": "#9C27B0",  # purple for social presentations
    }.get(str(outcome), "gray")


# ── Performance GUI ───────────────────────────────────────────────────────────

class PerformanceGUI:

    def __init__(self, animal_name="Animal", phase_selection=""):
        plt.ion()
        self._animal = animal_name
        self._phase  = phase_selection

        self.fig = plt.figure(figsize=(11, 8))
        self._base_title = f"Social Choice — {animal_name}"
        if phase_selection:
            self._base_title += f" | {phase_selection}"
        self.fig.suptitle(self._base_title, fontsize=13)

        gs = self.fig.add_gridspec(3, 2, hspace=0.55, wspace=0.35)
        self.ax_rt      = self.fig.add_subplot(gs[0, :])   # full width
        self.ax_choice  = self.fig.add_subplot(gs[1, 0])
        self.ax_block   = self.fig.add_subplot(gs[1, 1])
        self.ax_prop    = self.fig.add_subplot(gs[2, 0])
        self.ax_social  = self.fig.add_subplot(gs[2, 1])

        for ax, title_t, ylabel, xlabel in [
            (self.ax_rt,     "Reaction time",           "RT (s)",         "Trial"),
            (self.ax_choice, "Choice / outcome",        "",               "Trial"),
            (self.ax_block,  "Block hit rate",          "Hit rate (%)",   "Block"),
            (self.ax_prop,   "Choice proportion",       "A choices (%)",  "Trial"),
            (self.ax_social, "Social presentation",     "Duration (s)",   "Trial"),
        ]:
            ax.set_title(title_t, fontsize=10)
            ax.set_ylabel(ylabel, fontsize=9)
            ax.set_xlabel(xlabel, fontsize=9)
            ax.grid(True)

        self.ax_block.set_ylim(0, 100)
        self.ax_prop.set_ylim(-5, 105)

        plt.tight_layout(rect=[0, 0, 1, 0.96])
        plt.show(block=False)

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, df: pd.DataFrame):
        if df is None or df.empty:
            self.fig.canvas.draw()
            self.fig.canvas.flush_events()
            return

        if "trial_num" in df.columns and not df["trial_num"].isna().all():
            trial = int(df["trial_num"].max()) + 1
            self.fig.suptitle(f"{self._base_title}  |  Trial: {trial}", fontsize=13)

        if "choice_type" in df.columns:
            self._update_two_choice(df)
        elif "rt_a" in df.columns:
            self._update_one_choice(df)
        else:
            self._update_learning(df)

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    # ── Learning ──────────────────────────────────────────────────────────────

    def _update_learning(self, df):
        valid = df["rt"].notna()

        self.ax_rt.clear()
        if valid.any():
            self.ax_rt.scatter(
                df.loc[valid, "trial_num"], df.loc[valid, "rt"],
                c=PORT_COLORS["C"], s=25, zorder=3)
        self.ax_rt.set_title("Reaction time (port C)", fontsize=10)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        self.ax_rt.grid(True)

        # Cumulative rewards
        self.ax_choice.clear()
        if "reward_count" in df.columns:
            self.ax_choice.plot(df["trial_num"], df["reward_count"],
                                color="green", linewidth=1.5)
            self.ax_choice.set_title("Cumulative rewards", fontsize=10)
            self.ax_choice.set_ylabel("Reward count")
            self.ax_choice.set_xlabel("Trial")
            self.ax_choice.grid(True)

        self._draw_block_reward_rate(self.ax_block, df, "reward_triggered")
        self.ax_prop.clear()
        self.ax_social.clear()

    # ── One-choice ────────────────────────────────────────────────────────────

    def _update_one_choice(self, df):
        valid_a = df["rt_a"].notna()
        valid_c = df["rt_c"].notna()

        self.ax_rt.clear()
        if valid_a.any():
            self.ax_rt.scatter(
                df.loc[valid_a, "trial_num"], df.loc[valid_a, "rt_a"],
                c=PORT_COLORS["A"], s=25, label="RT port A", zorder=3)
        if valid_c.any():
            self.ax_rt.scatter(
                df.loc[valid_c, "trial_num"], df.loc[valid_c, "rt_c"],
                c=PORT_COLORS["C"], s=25, label="RT port C", zorder=3, marker="^")
        self.ax_rt.set_title("Reaction time (blue=port A, orange=port C)", fontsize=10)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        self.ax_rt.legend(fontsize=8)
        self.ax_rt.grid(True)

        # Outcome dots
        self.ax_choice.clear()
        for _, row in df.iterrows():
            color = _outcome_color(row.get("outcome", ""))
            self.ax_choice.scatter(row["trial_num"], 0.5, c=color, s=30)
        self.ax_choice.set_title("Outcome  (green=hit, orange=miss)", fontsize=10)
        self.ax_choice.set_yticks([])
        self.ax_choice.set_xlabel("Trial")
        self.ax_choice.grid(True, axis="x")

        self._draw_block_reward_rate(self.ax_block, df, None,
                                     col_outcome="outcome")
        self.ax_prop.clear()
        self.ax_social.clear()

    # ── Two-choice ────────────────────────────────────────────────────────────

    def _update_two_choice(self, df):
        # RT for sucrose (A) trials
        a_trials = df[df["choice_type"] == "sucrose"].copy()
        valid_rt  = a_trials["rt_ab"].notna()

        self.ax_rt.clear()
        if valid_rt.any():
            colors = [_outcome_color(o) for o in a_trials.loc[valid_rt, "outcome"]]
            self.ax_rt.scatter(
                a_trials.loc[valid_rt, "trial_num"],
                a_trials.loc[valid_rt, "rt_ab"],
                c=colors, s=25, zorder=3)
        self.ax_rt.set_title(
            "RT port A/B  (green=hit, orange=miss, purple=social)", fontsize=10)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        self.ax_rt.grid(True)

        # Choice plot: y=0 for A, y=1 for B; forced = star
        self.ax_choice.clear()
        port_to_y = {"A": 0, "B": 1}
        for _, row in df.iterrows():
            y      = port_to_y.get(row.get("poked_port"), 0.5)
            color  = _outcome_color(row.get("outcome", ""))
            marker = "*" if row.get("forced") else "o"
            size   = 80 if row.get("forced") else 30
            self.ax_choice.scatter(
                row["trial_num"], y, c=color, s=size, marker=marker)
        self.ax_choice.set_yticks([0, 1])
        self.ax_choice.set_yticklabels(["A (sucrose)", "B (social)"])
        self.ax_choice.set_title("Choice  (★=forced | green=hit, purple=social)",
                                  fontsize=9)
        self.ax_choice.set_ylabel("Port chosen")
        self.ax_choice.set_xlabel("Trial")
        self.ax_choice.set_ylim(-0.5, 1.5)
        self.ax_choice.grid(True, axis="x")

        # Hit rate for A-choice trials per block
        if not a_trials.empty:
            self._draw_block_reward_rate(
                self.ax_block, a_trials, None,
                col_outcome="outcome",
                title="A-choice hit rate (10 trials)")
        else:
            self.ax_block.clear()

        # Running A-choice proportion
        self.ax_prop.clear()
        if not df.empty:
            a_choices = (df["poked_port"] == "A").astype(float)
            window = min(10, len(a_choices))
            running = a_choices.rolling(window, min_periods=1).mean() * 100
            self.ax_prop.plot(df["trial_num"], running,
                              color=PORT_COLORS["A"], linewidth=1.5)
            self.ax_prop.axhline(50, color="gray", linewidth=1,
                                  linestyle="--", alpha=0.6)
            self.ax_prop.set_ylim(-5, 105)
        self.ax_prop.set_title(f"Running A-choice % (last 10)", fontsize=10)
        self.ax_prop.set_ylabel("A choices (%)")
        self.ax_prop.set_xlabel("Trial")
        self.ax_prop.grid(True)

        # Social duration for B-choice trials
        self.ax_social.clear()
        b_trials = df[df["choice_type"] == "social"].copy()
        if not b_trials.empty and "social_duration" in b_trials.columns:
            dur = b_trials["social_duration"].fillna(0)
            self.ax_social.bar(b_trials["trial_num"], dur,
                               color=PORT_COLORS["B"], edgecolor="black")
        self.ax_social.set_title("Social presentation duration", fontsize=10)
        self.ax_social.set_ylabel("Duration (s)")
        self.ax_social.set_xlabel("Trial")
        self.ax_social.grid(True, axis="y")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _draw_block_reward_rate(ax, df: pd.DataFrame, col: str,
                                 col_outcome: str = None,
                                 title: str = "Block hit rate (10 trials)"):
        ax.clear()
        block_size = 10
        blocks, labels = [], []
        for start in range(0, len(df), block_size):
            block = df.iloc[start : start + block_size]
            if block.empty:
                continue
            if col:
                pct = block[col].mean() * 100
            elif col_outcome:
                pct = (block[col_outcome] == "hit").mean() * 100
            else:
                pct = 0.0
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
        ax.set_ylabel("Rate (%)")
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
        self.fig.canvas.manager.set_window_title("Social Choice Sensor State")
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
