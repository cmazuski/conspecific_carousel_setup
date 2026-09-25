# SocialReward/gui.py — unified sensor and performance GUIs for social reward
# Used by both rat and mouse sessions; works across all phases and the task.

import matplotlib.pyplot as plt
from matplotlib.patches import Circle
import numpy as np
import pandas as pd


class PerformanceGUI:
    def __init__(self, animal_name="Animal", phase_selection=""):
        plt.ion()

        self._base_title       = f"Performance: {animal_name}"
        if phase_selection:
            self._base_title  += f" | Phase {phase_selection}"
        self._displayed_acc    = None   # last accuracy computed at a 10-trial boundary
        self._acc_at_n         = 0      # len(results_df) when _displayed_acc was last set
        self._rewarded_angle   = None   # set by draw_plan(); used for (+)/(-) box labels

        self.fig = plt.figure(figsize=(8, 12))
        gs = self.fig.add_gridspec(6, 1, height_ratios=[2, 2, 2, 2, 2, 1.5], hspace=0.95)

        # Row 0: RT LED A on → Port A poke
        self.ax_rt_dooropen = self.fig.add_subplot(gs[0])
        self.ax_rt_dooropen.set_title("RT: LED A ON → Port A poke", fontsize=9)
        self.ax_rt_dooropen.set_ylabel("RT (s)")
        self.ax_rt_dooropen.set_xlabel("Trial")
        self.ax_rt_dooropen.grid(True)

        # Row 1: RT door open → first table contact
        self.ax_rt_to_first_table = self.fig.add_subplot(gs[1])
        self.ax_rt_to_first_table.set_title("RT: door open → first table contact", fontsize=9)
        self.ax_rt_to_first_table.set_ylabel("RT (s)")
        self.ax_rt_to_first_table.set_xlabel("Trial")
        self.ax_rt_to_first_table.grid(True)

        # Row 2: Decision time LED C on → Port C poke
        self.ax_rt = self.fig.add_subplot(gs[2])
        self.ax_rt.set_title("Decision time: LED C ON → Port C poke", fontsize=9)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        self.ax_rt.grid(True)

        # Row 3: Sensory sampling (grouped bars: total + last bout)
        self.ax_sampling = self.fig.add_subplot(gs[3])
        self.ax_sampling.set_title(
            "Sensory sampling time  [gold = total, orange = last bout]", fontsize=9)
        self.ax_sampling.set_ylabel("Time (s)")
        self.ax_sampling.set_xlabel("Trial")
        self.ax_sampling.grid(True, axis="y")

        # Row 4: Overall trial time LED A on → Port C poke
        self.ax_trial_duration = self.fig.add_subplot(gs[4])
        self.ax_trial_duration.set_title("Overall trial time: LED A ON → Port C poke", fontsize=9)
        self.ax_trial_duration.set_ylabel("Time (s)")
        self.ax_trial_duration.set_xlabel("Trial")
        self.ax_trial_duration.grid(True)

        # Row 5: Per-trial outcome, split by box when task data is present
        self.ax_block = self.fig.add_subplot(gs[5])
        self.ax_block.set_title(
            "Trial outcome by box  [green = correct, red = incorrect]", fontsize=9)
        self.ax_block.set_ylabel("Box")
        self.ax_block.set_xlabel("Trial")
        self.ax_block.set_yticks([])
        self.ax_block.grid(True, axis="x")

        self.fig.suptitle(self._base_title, fontsize=12)
        plt.show(block=False)

    def draw_plan(self, planned_sequence, rewarded_angle=None):
        """Draw gray placeholder dots for all planned trials at session start."""
        self._rewarded_angle = rewarded_angle
        if not planned_sequence:
            return
        angles = sorted(set(planned_sequence))
        y_map  = {a: i for i, a in enumerate(angles)}

        self.ax_block.clear()
        self.ax_block.set_title(
            "Trial outcome by box  [green = correct, red = incorrect]", fontsize=9)
        self.ax_block.set_xlabel("Trial")
        self.ax_block.grid(True, axis="x")
        self.ax_block.set_yticks(list(range(len(angles))))
        self.ax_block.set_yticklabels(
            [_box_label(a, rewarded_angle) for a in angles], fontsize=8)
        self.ax_block.set_ylabel("Box")
        self.ax_block.set_ylim(-0.5, len(angles) - 0.5)

        for t, angle in enumerate(planned_sequence, start=1):
            if angle in y_map:
                self.ax_block.scatter(t, y_map[angle], c="lightgray", s=50, zorder=2)

        self.ax_block.set_xlim(0.5, len(planned_sequence) + 0.5)
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def update(self, results_df: pd.DataFrame, current_trial_port=None,
               planned_sequence=None):
        if results_df.empty:
            return

        # ── Header: current trial + accuracy (refreshes every 10 trials) ─────
        current_trial = int(results_df["trial_num"].max()) + 1
        n = len(results_df)
        if n >= 10 and (n // 10) > (self._acc_at_n // 10):
            if "outcome" in results_df.columns:
                correct = results_df["outcome"].isin(
                    ["hit", "correct_rejection", "rewarded"]).sum()
            elif "reward_triggered" in results_df.columns:
                correct = results_df["reward_triggered"].sum()
            else:
                correct = 0
            self._displayed_acc = correct / n * 100
            self._acc_at_n = n

        acc_str = (f"  |  Accuracy: {self._displayed_acc:.0f}% (trials 1–{self._acc_at_n})"
                   if self._displayed_acc is not None else "")
        box_str = ""
        if planned_sequence and 0 <= current_trial - 1 < len(planned_sequence):
            box_str = f"  |  Current: Box {planned_sequence[current_trial - 1] // 90}"
        self.fig.suptitle(
            f"{self._base_title}  |  Trial: {current_trial}{box_str}{acc_str}",
            fontsize=11)

        xmin = results_df["trial_num"].min() - 0.5
        xmax = results_df["trial_num"].max() + 0.5

        # ── RT: LED A on → Port A poke ────────────────────────────────────────
        self.ax_rt_dooropen.clear()
        self.ax_rt_dooropen.set_title("RT: LED A ON → Port A poke", fontsize=9)
        self.ax_rt_dooropen.grid(True)
        self.ax_rt_dooropen.set_ylabel("RT (s)")
        self.ax_rt_dooropen.set_xlabel("Trial")
        if "rt_dooropen" in results_df.columns:
            valid  = results_df["rt_dooropen"].notna()
            trials = results_df.loc[valid, "trial_num"]
            rts    = results_df.loc[valid, "rt_dooropen"]
            if not trials.empty:
                self.ax_rt_dooropen.plot(trials, rts, c="steelblue", linewidth=1, alpha=0.6)
                self.ax_rt_dooropen.scatter(trials, rts, c="steelblue", s=20)
                self.ax_rt_dooropen.set_xlim(xmin, xmax)

        # ── RT: door open → first table contact ──────────────────────────────
        self.ax_rt_to_first_table.clear()
        self.ax_rt_to_first_table.set_title(
            "RT: door open → first table contact", fontsize=9)
        self.ax_rt_to_first_table.grid(True)
        self.ax_rt_to_first_table.set_ylabel("RT (s)")
        self.ax_rt_to_first_table.set_xlabel("Trial")
        if "rt_to_first_table" in results_df.columns:
            valid  = results_df["rt_to_first_table"].notna()
            trials = results_df.loc[valid, "trial_num"]
            rts    = results_df.loc[valid, "rt_to_first_table"]
            if not trials.empty:
                self.ax_rt_to_first_table.plot(
                    trials, rts, c="darkorange", linewidth=1, alpha=0.6)
                self.ax_rt_to_first_table.scatter(trials, rts, c="darkorange", s=20)
                self.ax_rt_to_first_table.set_xlim(xmin, xmax)

        # ── Decision time: LED C on → Port C poke ────────────────────────────
        self.ax_rt.clear()
        self.ax_rt.set_title("Decision time: LED C ON → Port C poke", fontsize=9)
        self.ax_rt.grid(True)
        self.ax_rt.set_ylabel("RT (s)")
        self.ax_rt.set_xlabel("Trial")
        if "rt" in results_df.columns:
            valid  = results_df["rt"].notna()
            trials = results_df.loc[valid, "trial_num"]
            rts    = results_df.loc[valid, "rt"]
            if not trials.empty:
                colors = (
                    [_outcome_color(o) for o in results_df.loc[valid, "outcome"]]
                    if "outcome" in results_df.columns
                    else ["green"] * len(trials)
                )
                self.ax_rt.plot(trials, rts, c="black", linewidth=1, alpha=0.4)
                self.ax_rt.scatter(trials, rts, c=colors, s=30)
                self.ax_rt.set_xlim(xmin, xmax)

        # ── Sensory sampling: grouped bars (total + last bout) ────────────────
        self.ax_sampling.clear()
        self.ax_sampling.set_title(
            "Sensory sampling time  [gold = total, orange = last bout]", fontsize=9)
        self.ax_sampling.set_ylabel("Time (s)")
        self.ax_sampling.set_xlabel("Trial")
        self.ax_sampling.grid(True, axis="y")
        has_total  = "total_sampling_time" in results_df.columns
        has_last   = "sampling_time" in results_df.columns
        if has_total or has_last:
            all_trials = results_df["trial_num"].values
            bw = 0.35
            x  = np.array(all_trials, dtype=float)
            if has_total:
                total_y = results_df["total_sampling_time"].fillna(0).values
                self.ax_sampling.bar(x - bw / 2, total_y, width=bw,
                                     color="gold", edgecolor="black", label="Total")
            if has_last:
                last_y = results_df["sampling_time"].fillna(0).values
                offset = bw / 2 if has_total else 0
                self.ax_sampling.bar(x + offset, last_y, width=bw,
                                     color="darkorange", edgecolor="black", label="Last bout")
            self.ax_sampling.set_xlim(xmin, xmax)
            self.ax_sampling.legend(fontsize=7, loc="upper left")

        # ── Overall trial time ────────────────────────────────────────────────
        self.ax_trial_duration.clear()
        self.ax_trial_duration.set_title(
            "Overall trial time: LED A ON → Port C poke", fontsize=9)
        self.ax_trial_duration.grid(True)
        self.ax_trial_duration.set_ylabel("Time (s)")
        self.ax_trial_duration.set_xlabel("Trial")
        if "trial_duration" in results_df.columns:
            valid  = results_df["trial_duration"].notna()
            trials = results_df.loc[valid, "trial_num"]
            durs   = results_df.loc[valid, "trial_duration"]
            if not trials.empty:
                self.ax_trial_duration.plot(trials, durs, c="purple", linewidth=1, alpha=0.6)
                self.ax_trial_duration.scatter(trials, durs, c="purple", s=20)
                self.ax_trial_duration.set_xlim(xmin, xmax)

        # ── Per-trial outcome dots (two rows by box for task, one row for training) ─
        self.ax_block.clear()
        self.ax_block.set_title(
            "Trial outcome by box  [green = correct, red = incorrect]", fontsize=9)
        self.ax_block.set_xlabel("Trial")
        self.ax_block.grid(True, axis="x")

        has_angles = "presentation_angle" in results_df.columns or planned_sequence
        if has_angles:
            # Collect all known angles from plan + data
            angle_set = set()
            if planned_sequence:
                angle_set.update(planned_sequence)
            if "presentation_angle" in results_df.columns:
                angle_set.update(results_df["presentation_angle"].dropna().unique())
            angles = sorted(angle_set)
            y_map  = {a: i for i, a in enumerate(angles)}

            self.ax_block.set_yticks(list(range(len(angles))))
            self.ax_block.set_yticklabels(
                [_box_label(a, self._rewarded_angle) for a in angles], fontsize=8)
            self.ax_block.set_ylabel("Box")
            self.ax_block.set_ylim(-0.5, len(angles) - 0.5)

            # Gray dots for all planned trials
            if planned_sequence:
                for t, angle in enumerate(planned_sequence, start=1):
                    if angle in y_map:
                        self.ax_block.scatter(
                            t, y_map[angle], c="lightgray", s=50, zorder=2)

            # Colored overlay for completed trials
            if "presentation_angle" in results_df.columns:
                for _, row in results_df.iterrows():
                    angle = row.get("presentation_angle")
                    if pd.isna(angle) or angle not in y_map:
                        continue
                    outcome = row.get("outcome")
                    correct = outcome in ("hit", "correct_rejection", "rewarded")
                    self.ax_block.scatter(
                        row["trial_num"], y_map[angle],
                        c="green" if correct else "red", s=50, zorder=3)
        else:
            # Training phases: single row
            self.ax_block.set_yticks([])
            self.ax_block.set_ylabel("Outcome")
            self.ax_block.set_ylim(0, 1)

            for _, row in results_df.iterrows():
                outcome = row.get("outcome") if "outcome" in results_df.columns else None
                reward_triggered = (row.get("reward_triggered")
                                    if "reward_triggered" in results_df.columns else None)
                if outcome is not None:
                    correct = outcome in ("hit", "correct_rejection", "rewarded")
                elif reward_triggered is not None:
                    correct = bool(reward_triggered)
                else:
                    continue
                self.ax_block.scatter(
                    row["trial_num"], 0.5,
                    c="green" if correct else "red", s=50, zorder=3)

        # x-axis always spans the full planned range (or completed range)
        plan_xmax = len(planned_sequence) + 0.5 if planned_sequence else xmax
        self.ax_block.set_xlim(0.5, max(xmax, plan_xmax))

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def close(self, save_path=None):
        if save_path is not None:
            self.fig.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"[INFO] Performance figure saved: {save_path}")
        plt.ioff()
        plt.close(self.fig)


class SensorGUI:
    def __init__(self):
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(4, 2))
        self.ax.axis("off")

        self.circle_a = Circle((0.3, 0.5), 0.12, transform=self.ax.transAxes,
                               facecolor="red", edgecolor="black")
        self.circle_b = Circle((0.7, 0.5), 0.12, transform=self.ax.transAxes,
                               facecolor="red", edgecolor="black")
        self.circle_c = Circle((0.5, 0.8), 0.12, transform=self.ax.transAxes,
                               facecolor="red", edgecolor="black")
        self.circle_door = Circle((0.3, 0.2), 0.12, transform=self.ax.transAxes,
                                  facecolor="red", edgecolor="black")
        self.circle_table = Circle((0.7, 0.2), 0.12, transform=self.ax.transAxes,
                                   facecolor="red", edgecolor="black")

        for patch in (self.circle_a, self.circle_b, self.circle_c,
                      self.circle_door, self.circle_table):
            self.ax.add_patch(patch)

        self.ax.text(0.3, 0.65, "Sensor A", ha="center", fontsize=9, transform=self.ax.transAxes)
        self.ax.text(0.7, 0.65, "Sensor B", ha="center", fontsize=9, transform=self.ax.transAxes)
        self.ax.text(0.5, 0.92, "Sensor C", ha="center", fontsize=9, transform=self.ax.transAxes)
        self.ax.text(0.3, 0.04, "Door",     ha="center", fontsize=9, transform=self.ax.transAxes)
        self.ax.text(0.7, 0.04, "Table",    ha="center", fontsize=9, transform=self.ax.transAxes)

        self.text_a     = self.ax.text(0.3, 0.56, "", ha="center", fontsize=7, transform=self.ax.transAxes)
        self.text_b     = self.ax.text(0.7, 0.56, "", ha="center", fontsize=7, transform=self.ax.transAxes)
        self.text_c     = self.ax.text(0.5, 0.84, "", ha="center", fontsize=7, transform=self.ax.transAxes)
        self.text_door  = self.ax.text(0.3, 0.01, "", ha="center", fontsize=7, transform=self.ax.transAxes)
        self.text_table = self.ax.text(0.7, 0.01, "", ha="center", fontsize=7, transform=self.ax.transAxes)

        plt.show(block=False)

    def update(self, snapshot):
        self.circle_a.set_facecolor("green" if snapshot.A == "triggered" else "red")
        self.circle_b.set_facecolor("green" if snapshot.B == "triggered" else "red")
        self.circle_c.set_facecolor("green" if snapshot.C == "triggered" else "red")
        # doorsensor is the proximity sensor (triggered/cleared), not the door state string
        self.circle_door.set_facecolor("green" if snapshot.doorsensor == "triggered" else "red")
        self.circle_table.set_facecolor("green" if snapshot.table == "triggered" else "red")

        self.circle_a.set_radius(0.14 if snapshot.A == "triggered" else 0.12)
        self.circle_b.set_radius(0.14 if snapshot.B == "triggered" else 0.12)
        self.circle_c.set_radius(0.14 if snapshot.C == "triggered" else 0.12)

        self.text_a.set_text(    snapshot.tA.strftime("%H:%M:%S.%f")[:-3]          if snapshot.tA          else "-")
        self.text_b.set_text(    snapshot.tB.strftime("%H:%M:%S.%f")[:-3]          if snapshot.tB          else "-")
        self.text_c.set_text(    snapshot.tC.strftime("%H:%M:%S.%f")[:-3]          if snapshot.tC          else "-")
        self.text_door.set_text( snapshot.tDoorsensor.strftime("%H:%M:%S.%f")[:-3] if snapshot.tDoorsensor else "-")
        self.text_table.set_text(snapshot.tTable.strftime("%H:%M:%S.%f")[:-3]      if snapshot.tTable      else "-")

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def close(self):
        plt.ioff()
        plt.close(self.fig)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _box_label(angle: float, rewarded_angle) -> str:
    box_num = int(angle) // 90
    if rewarded_angle is not None:
        suffix = " (+)" if int(angle) == int(rewarded_angle) else " (-)"
    else:
        suffix = ""
    return f"Box {box_num}{suffix}"


def _outcome_color(outcome):
    return {
        "hit":               "green",
        "rewarded":          "green",
        "false_alarm":       "red",
        "miss":              "orange",
        "correct_rejection": "blue",
    }.get(outcome, "gray")
