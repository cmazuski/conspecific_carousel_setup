# SocialMemory/resume.py — plan a continuation of a stopped passive test
#
# When a passive test is cut short (e.g. the board stopped responding), a new
# "continuation" session runs the presentations that are left, in their
# original planned order, in its own folder. The two folders are merged
# offline; each keeps its own clock (t=0 = its own start).
#
# Where to pick up:
#   completed  = rows in the stopped session's presentations.csv (always the
#                first N of the planned sequence, which is saved in its
#                metadata.json as "sequence" / "sequence_labels")
#   interrupted = the next planned presentation, IF it had already started —
#                a "door opened" in sensor_events.csv after the last completed
#                presentation ended. It is skipped, not re-run: the animal has
#                seen part of it.
#   remaining  = everything after that, in the original order and with the
#                original labels (Box2_3, ...) and presentation numbers, so the
#                merged data reads as one planned sequence.
#
# The carousel is assumed to be back at home (0°) when the continuation starts.

import csv
import json
import os

import pandas as pd

# Setup-dialog keys that describe the rig / this run rather than the task — a
# continuation takes these from its own setup dialog, everything else (animal,
# boxes, durations, ITIs, conditioning, buzzer, ...) from the stopped session.
RUNTIME_KEYS = {
    "port", "baud", "save_root", "door_open_speed", "door_close_speed",
    "table_speed", "record_camera", "camera_serial", "camera_fps",
    "camera_exposure_ms", "show_sensor_display", "setup", "notes",
}
# Written by main for a particular run — never carried over.
DERIVED_KEYS = {
    "date", "save_dir", "timestamp", "board_failure", "sequence",
    "sequence_labels", "resume_from", "resume_plan", "reward_increment_s",
    "reward_max_valve_s",
}


def plan_resume(folder: str) -> dict:
    """Work out how to continue the passive test saved in `folder`.

    Returns a dict with:
      meta              the stopped session's metadata.json
      completed         labels of the presentations that finished
      skipped           label of the one that was in progress (None if none)
      remaining         box indices still to run, in planned order
      remaining_labels  their original labels
      first_presentation_num  presentation_num of remaining[0]
      buzzer_iti_remaining    ITI buzzes not yet delivered (ITI buzzer mode)
    Raises ValueError with an operator-readable message if it can't."""
    meta_path = os.path.join(folder, "metadata.json")
    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except FileNotFoundError:
        raise ValueError(f"No metadata.json in {folder} - not a session folder.")
    if meta.get("mode") != "passivetest":
        raise ValueError(f"That session is mode '{meta.get('mode')}' - only passive "
                         f"tests can be continued.")
    sequence = meta.get("sequence")
    labels = meta.get("sequence_labels")
    if not sequence or not labels or len(sequence) != len(labels):
        raise ValueError("That session was recorded before the planned presentation "
                         "order was saved, so where to continue can't be worked out.")

    pres_path = os.path.join(folder, "presentations.csv")
    if not os.path.exists(pres_path):
        raise ValueError("No presentations.csv in that folder - the session didn't "
                         "shut down far enough to save its results.")
    pres = pd.read_csv(pres_path)
    completed = [str(p) for p in pres["period"]] if "period" in pres.columns else []
    n_done = len(completed)
    if completed != labels[:n_done]:
        raise ValueError(f"presentations.csv ({completed}) doesn't match the start of "
                         f"the planned order ({labels[:n_done]}) - not continuing.")

    # Had the next presentation started? A door opening after the last
    # completed one ended means yes (the completed one's own close only
    # produces 'door moving' / 'door closed').
    after = float(pres["presentation_end"].iloc[-1]) if n_done else 0.0
    started_next = False
    ev_path = os.path.join(folder, "sensor_events.csv")
    if os.path.exists(ev_path):
        with open(ev_path, newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 4 and row[2].strip() == "door" \
                        and row[3].strip() == "door opened":
                    try:
                        if float(row[1]) > after:
                            started_next = True
                            break
                    except ValueError:
                        pass

    start = n_done + (1 if started_next and n_done < len(sequence) else 0)
    skipped = labels[n_done] if started_next and n_done < len(sequence) else None
    remaining = [int(b) for b in sequence[start:]]
    if not remaining:
        raise ValueError("Nothing left to run - every planned presentation was "
                         "completed or had started.")

    buzzer_iti_remaining = 0
    if meta.get("buzzer_mode") == "iti":
        delivered = 0
        bz_path = os.path.join(folder, "buzzer_events.csv")
        if os.path.exists(bz_path):
            bz = pd.read_csv(bz_path)
            delivered = int((bz["trigger"] == "iti").sum())
        buzzer_iti_remaining = max(0, int(meta.get("buzzer_iti_total") or 0) - delivered)

    return {
        "meta": meta,
        "completed": completed,
        "skipped": skipped,
        "remaining": remaining,
        "remaining_labels": labels[start:],
        "first_presentation_num": start + 1,
        "buzzer_iti_remaining": buzzer_iti_remaining,
    }


def describe(plan: dict) -> str:
    """Short operator-facing summary of a plan (for the setup dialog)."""
    lines = [f"Completed: {len(plan['completed'])} of "
             f"{len(plan['completed']) + (1 if plan['skipped'] else 0) + len(plan['remaining'])}"]
    if plan["skipped"]:
        lines.append(f"In progress when it stopped (skipped): {plan['skipped']}")
    lines.append(f"Will run {len(plan['remaining'])}: " + ", ".join(plan["remaining_labels"]))
    m = plan["meta"]
    lines.append(f"Animal {m.get('animal')}, session {m.get('session_n')}, {m.get('species')} "
                 f"- task settings are taken from that session.")
    if m.get("buzzer_mode") == "iti":
        lines.append(f"ITI buzzes left: {plan['buzzer_iti_remaining']}")
    return "\n".join(lines)


def merge_params(plan: dict, dialog_params: dict) -> dict:
    """Parameters for the continuation: the task settings from the stopped
    session, plus this run's rig/runtime settings from the setup dialog."""
    params = {k: v for k, v in plan["meta"].items()
              if k not in RUNTIME_KEYS and k not in DERIVED_KEYS}
    for k in RUNTIME_KEYS:
        if k in dialog_params:
            params[k] = dialog_params[k]
    params["resume_from"] = dialog_params["resume_from"]
    params["resume_plan"] = {
        "completed": plan["completed"],
        "skipped": plan["skipped"],
        "remaining": plan["remaining_labels"],
        "first_presentation_num": plan["first_presentation_num"],
    }
    if params.get("buzzer_mode") == "iti":
        params["buzzer_iti_total"] = plan["buzzer_iti_remaining"]
    return params
