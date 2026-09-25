# replay_performance.py — Reconstruct the PerformanceGUI figure offline from a
# saved session's output folder (no hardware / serial connection required).
#
# Usage:
#   python replay_performance.py <session_dir> [--save [PATH]] [--no-show] [--family NAME]
#
# <session_dir> is the per-run folder created by one of the main_*.py scripts,
# e.g.  SocialMemoryData/Rat1_3_task_2026-06-20_rat
#       SocialRewardData/unknown_1_task_2026-06-09_mouse
#
# The task family (SocialMemory / SocialReward / SocialChoice / SocialReward2AFC)
# is auto-detected from the session folder's parent directory name. Use
# --family to override when a folder has been moved/renamed.

import argparse
import json
import os
import sys

import pandas as pd
import matplotlib.pyplot as plt


_FAMILY_BY_FOLDER = {
    "SocialMemoryData":       "socialmemory",
    "SocialRewardData":       "socialreward",
    "SocialChoiceData":       "socialchoice",
    "SocialReward2AFCData":   "socialreward2afc",
}


def _detect_family(session_dir: str) -> str:
    parent = os.path.basename(os.path.dirname(os.path.abspath(session_dir)))
    family = _FAMILY_BY_FOLDER.get(parent)
    if family is None:
        raise ValueError(
            f"Could not detect task family from parent folder '{parent}'. "
            f"Pass --family explicitly (one of: {', '.join(sorted(set(_FAMILY_BY_FOLDER.values())))})."
        )
    return family


def _load_metadata(session_dir: str) -> dict:
    path = os.path.join(session_dir, "metadata.json")
    if not os.path.exists(path):
        print(f"[WARN] No metadata.json in {session_dir} — using defaults for title/labels")
        return {}
    with open(path) as f:
        return json.load(f)


def _read_csv(session_dir: str, filename: str) -> pd.DataFrame:
    path = os.path.join(session_dir, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{filename} not found in {session_dir}")
    return pd.read_csv(path)


def _fill_missing_columns(df: pd.DataFrame, columns) -> pd.DataFrame:
    """Backfill columns a newer GUI expects but an older CSV schema didn't record."""
    df = df.copy()
    for col in columns:
        if col not in df.columns:
            df[col] = float("nan")
    return df


def _read_sensor_events(session_dir: str):
    """sensor_events.csv has no header: HH:MM:SS.mmm, elapsed_seconds, port, state."""
    path = os.path.join(session_dir, "sensor_events.csv")
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, header=None, names=["time_str", "t", "port", "state"])


def _door_open_segments(events: pd.DataFrame):
    """[(door_open_t, door_close_t), ...] in chronological order.

    SocialMemory only opens the door inside _run_presentation, so there's
    exactly one segment per presentation — pairing them by order recovers
    which door-open window each presentation row belongs to.
    """
    door = events[events["port"] == "door"].sort_values("t")
    segments = []
    open_t = None
    for _, row in door.iterrows():
        if row["state"] == "door opened":
            open_t = row["t"]
        elif row["state"] == "door closed" and open_t is not None:
            segments.append((open_t, row["t"]))
            open_t = None
    return segments


def _reconstruct_engage_and_bouts(presentations: pd.DataFrame, session_dir: str) -> pd.DataFrame:
    """Recover time_to_engage / bout_count for presentations.csv files saved
    before those columns existed, from the raw sensor_events.csv log:
      time_to_engage = first table-triggered event in the door-open window,
                        minus the door-opened event that started it
      bout_count     = number of table-triggered events within
                        [first contact, first contact + presentation_duration]
    """
    events = _read_sensor_events(session_dir)
    if events is None:
        print("[WARN] sensor_events.csv not found — time_to_engage/bout_count left blank")
        return _fill_missing_columns(presentations, ["time_to_engage", "bout_count"])

    segments = _door_open_segments(events)
    table_triggers = events[(events["port"] == "table") & (events["state"] == "triggered")]

    n = min(len(segments), len(presentations))
    if len(segments) != len(presentations):
        print(f"[WARN] {len(segments)} door-open segments in sensor_events.csv vs "
              f"{len(presentations)} presentation rows — reconstructing the first {n}")

    engage = [float("nan")] * len(presentations)
    bouts = [float("nan")] * len(presentations)

    for i in range(n):
        door_open_t, door_close_t = segments[i]
        duration = presentations.iloc[i]["presentation_duration"]
        in_window = table_triggers[(table_triggers["t"] >= door_open_t) &
                                    (table_triggers["t"] <= door_close_t)]
        if in_window.empty:
            continue
        first_contact_t = in_window["t"].min()
        engage[i] = first_contact_t - door_open_t
        bouts[i] = int(((table_triggers["t"] >= first_contact_t) &
                         (table_triggers["t"] <= first_contact_t + duration)).sum())

    presentations = presentations.copy()
    presentations["time_to_engage"] = engage
    presentations["bout_count"] = bouts
    return presentations


def _reconstruct_sampling_time(presentations: pd.DataFrame, session_dir: str) -> pd.DataFrame:
    """Recover sampling_time for presentations.csv files saved before that
    column existed, from the raw sensor_events.csv log.

    Mirrors BaseSession._run_presentation's live accumulation: the beam is
    already triggered at the first table contact after door-open (that's what
    starts the presentation timer), so sampling_time is the total time the
    table sensor spends "triggered" within
    [first contact, first contact + presentation_duration], summed across
    every triggered→cleared bout (a bout still open at the window end is
    capped there, same as the live loop hitting its deadline).

    Like _reconstruct_engage_and_bouts, this works entirely in
    sensor_events.csv's own elapsed-time clock and pairs door-open segments
    with presentation rows positionally — presentations.csv's own timestamp
    columns (door_open_time, presentation_start, ...) are never compared
    against sensor_events.csv's, since older files logged those as raw
    time.time() values rather than session-relative ones and the two clocks
    aren't guaranteed to line up.
    """
    events = _read_sensor_events(session_dir)
    if events is None:
        print("[WARN] sensor_events.csv not found — sampling_time left blank")
        return _fill_missing_columns(presentations, ["sampling_time"])

    segments = _door_open_segments(events)
    table = events[events["port"] == "table"].sort_values("t")
    table_triggers = table[table["state"] == "triggered"]

    n = min(len(segments), len(presentations))
    if len(segments) != len(presentations):
        print(f"[WARN] {len(segments)} door-open segments in sensor_events.csv vs "
              f"{len(presentations)} presentation rows — reconstructing the first {n}")

    sampling = [float("nan")] * len(presentations)
    for i in range(n):
        door_open_t, door_close_t = segments[i]
        duration = presentations.iloc[i]["presentation_duration"]
        in_window = table_triggers[(table_triggers["t"] >= door_open_t) &
                                    (table_triggers["t"] <= door_close_t)]
        if in_window.empty:
            continue
        first_contact_t = in_window["t"].min()
        window_end = first_contact_t + duration

        contact_time = 0.0
        contact_start = first_contact_t  # beam is already triggered at first contact
        bout_events = table[(table["t"] > first_contact_t) & (table["t"] <= window_end)]
        for _, ev in bout_events.iterrows():
            if ev["state"] == "cleared" and contact_start is not None:
                contact_time += ev["t"] - contact_start
                contact_start = None
            elif ev["state"] == "triggered" and contact_start is None:
                contact_start = ev["t"]
        if contact_start is not None:
            contact_time += window_end - contact_start
        sampling[i] = contact_time

    presentations = presentations.copy()
    presentations["sampling_time"] = sampling
    return presentations


# ── Per-family replay ───────────────────────────────────────────────────────────

def _replay_socialmemory(meta: dict, session_dir: str):
    from SocialMemory.gui import PerformanceGUI

    mode = meta.get("mode")
    if mode is None:
        # Fall back to inferring mode from which CSVs were written.
        mode = "task" if os.path.exists(os.path.join(session_dir, "presentations.csv")) else "training"
        print(f"[WARN] No 'mode' in metadata.json — inferred mode={mode!r} from files present")

    perf_gui = PerformanceGUI(animal_name=meta.get("animal", "Animal"), mode=mode,
                               stim1_id=meta.get("s1_id"), stim2_id=meta.get("s2_id"))

    if mode == "training":
        df = _read_csv(session_dir, "trials.csv")
        perf_gui.update(df)
    elif mode == "task":
        presentations = _read_csv(session_dir, "presentations.csv")
        if "time_to_engage" not in presentations.columns or "bout_count" not in presentations.columns:
            presentations = _reconstruct_engage_and_bouts(presentations, session_dir)
        if "sampling_time" not in presentations.columns:
            presentations = _reconstruct_sampling_time(presentations, session_dir)
        conditioning = _read_csv(session_dir, "conditioning_trials.csv")
        perf_gui.update(presentations, conditioning)
    else:
        raise ValueError(f"Unknown SocialMemory mode: {mode!r}")

    return perf_gui


def _replay_socialreward(meta: dict, session_dir: str):
    from SocialReward.gui import PerformanceGUI

    phase = meta.get("phase", "")
    perf_gui = PerformanceGUI(animal_name=meta.get("animal", "Animal"), phase_selection=phase)

    # Recover the rewarded-box (+)/(-) labelling that live runs get from
    # draw_plan(planned_sequence, rewarded_angle=...) — planned_sequence itself
    # isn't persisted, but the rewarded angle is, so the box labels can still
    # be reconstructed for completed task/4stimuli runs.
    rewarded_angle = None
    if phase == "task":
        rewarded_angle = meta.get("task_rewarded_angle")
    elif phase == "4stimuli":
        stim4_config = meta.get("stim4_config") or {}
        box_config = stim4_config.get("box_config", {})
        rewarded_boxes = [b for b, cfg in box_config.items() if cfg.get("rewarded")]
        if rewarded_boxes:
            rewarded_angle = int(rewarded_boxes[0]) * 90
    if rewarded_angle is not None:
        perf_gui.draw_plan(None, rewarded_angle=rewarded_angle)

    df = _read_csv(session_dir, "trials.csv")
    perf_gui.update(df)
    return perf_gui


def _replay_socialchoice(meta: dict, session_dir: str):
    from SocialChoice.gui import PerformanceGUI

    perf_gui = PerformanceGUI(animal_name=meta.get("animal", "Animal"),
                               phase_selection=meta.get("phase", ""))
    df = _read_csv(session_dir, "trials.csv")
    perf_gui.update(df)
    return perf_gui


def _replay_socialreward2afc(meta: dict, session_dir: str):
    from SocialReward2AFC.gui import PerformanceGUI

    perf_gui = PerformanceGUI(animal_name=meta.get("animal", "Animal"),
                               phase_selection=meta.get("phase", ""))
    df = _read_csv(session_dir, "trials.csv")
    perf_gui.update(df)
    return perf_gui


_REPLAYERS = {
    "socialmemory":     _replay_socialmemory,
    "socialreward":     _replay_socialreward,
    "socialchoice":     _replay_socialchoice,
    "socialreward2afc": _replay_socialreward2afc,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", nargs="?", default=".",
                         help="Path to the session's output folder (default: current directory)")
    parser.add_argument("--family", choices=sorted(_REPLAYERS), default=None,
                         help="Override task-family auto-detection")
    parser.add_argument("--save", nargs="?", const="performance_replay.png", default=None,
                         metavar="PATH",
                         help="Save the reconstructed figure (default name: "
                              "performance_replay.png in the session folder)")
    parser.add_argument("--no-show", action="store_true",
                         help="Don't open an interactive window (e.g. for batch regeneration)")
    args = parser.parse_args()

    session_dir = os.path.abspath(args.session_dir)
    if not os.path.isdir(session_dir):
        sys.exit(f"[ERROR] Not a directory: {session_dir}")

    try:
        family = args.family or _detect_family(session_dir)
        meta = _load_metadata(session_dir)
        print(f"[INFO] Replaying {family} session: {session_dir}")
        perf_gui = _REPLAYERS[family](meta, session_dir)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"[ERROR] {e}")

    if args.save is not None:
        save_path = args.save
        if not os.path.isabs(save_path):
            save_path = os.path.join(session_dir, save_path)
        perf_gui.fig.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"[INFO] Saved: {save_path}")

    if not args.no_show:
        plt.show(block=True)


if __name__ == "__main__":
    main()
