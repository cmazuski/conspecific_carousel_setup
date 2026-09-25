# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Host-side control software for the **conspecific carousel**, a rodent behavioral rig used to run
social-behavior tasks. The rig has:

- a **4-position turntable** (0°/90°/180°/270°) carrying conspecific stimulus animals,
- a **motorized door** between the subject's chamber and the turntable, with a proximity sensor
  and a table sensor used as safety interlocks,
- **three nose ports A / B / C**, each with an LED, a water valve, and an IR beam-break sensor.

A Python host drives the rig's firmware over a serial link, runs a task session in a background
thread, records synchronized video from a Basler camera, and shows live matplotlib GUIs
(sensor state + running performance).

## Repository layout

Everything lives at the repo root — there is no subpackage. `cameracontrol.py`
sits next to the task scripts, which launch it by path from their own directory; it keeps its
per-camera `camera_crop_config_<serial>.json` files beside itself.

The code used to live in a `conspecific_carousel_firmware_update/` subfolder, with old-firmware
scripts (raw single-byte opcodes over a bare `serial.Serial`) at the root. Those old scripts were
removed and the subfolder flattened; the git tag **`legacy-root-scripts`** marks the last commit
with the old layout. Do not resurrect or port fixes into the old scripts.

## Commands

There is no test suite, no linter config, and no build step. Run everything with the repo's venv
interpreter from the **repo root** — modules import each other flat (`from serial_comm import
...`), so the root must be the working directory.

```bash
# Run a task session (opens a Tk setup dialog first, then the live GUIs)
.venv/Scripts/python.exe main_socialreward2AFC.py
.venv/Scripts/python.exe main_socialmemory.py
.venv/Scripts/python.exe main_socialreward.py
.venv/Scripts/python.exe main_socialchoice.py

# Manual device control GUI — toggle LEDs/valves, drive door and turntable, watch registers
.venv/Scripts/python.exe setup_control.py

# Bench checks / calibration (need the rig attached)
.venv/Scripts/python.exe check_status_reads.py COM4     # does firmware answer status READs?
.venv/Scripts/python.exe water_cal.py                   # valve pulse duration for 1 ul

# Rebuild a finished session's performance figure offline, no hardware needed
.venv/Scripts/python.exe replay_performance.py SocialReward2AFCData/CON1_1_1_2026-07-30_rat --save check.png --no-show

# Camera, standalone
.venv/Scripts/python.exe cameracontrol.py --list-cameras     # JSON, opens nothing — safe mid-recording
.venv/Scripts/python.exe cameracontrol.py --camera 24134346  # record one specific camera
```

Since nothing can be exercised without the rig, the standing substitute for tests is a syntax
check across everything touched:

```bash
.venv/Scripts/python.exe -m py_compile protocol.py hardware.py serial_comm.py \
  SocialReward/*.py SocialReward2AFC/*.py SocialMemory/*.py SocialChoice/*.py \
  main_socialreward.py main_socialmemory.py main_socialchoice.py main_socialreward2AFC.py
```

`replay_performance.py` against a saved (or synthetic) session folder is the only way to verify
plotting changes without the rig.

**Environment:** Python 3.14 in `.venv/` at the repo root. Deps: `pyserial numpy pandas matplotlib`
(in `requirements.txt`) plus `pypylon` and `opencv-python` for the camera, and **`ffmpeg.exe` must
be on PATH** — `cameracontrol` pipes raw frames into it and refuses to start without it.

## Architecture

Layers, innermost first. Each layer only knows the one below it.

1. **`protocol.py`** — wire format and register map, no I/O. Packets are 4 bytes:
   `[0xCC, register, msg_type, value]`, where msg_type is WRITE `0x01`, READ/ACK `0x02`, or
   EVENT `0x03`. Turntable moves encode direction in bit 7 and 1/8-turns (45° each) in bits 6:0 —
   see `build_table_command`. `TRIGGER_OPTIONS` / `ACTION_OPTIONS` exist for the setup GUI's
   condition engine.
2. **`serial_comm.py` — `DeviceConnection`** — owns the port and a daemon reader thread. Writes
   block on a matching ACK with retries under a lock; unsolicited EVENT packets are dispatched to
   registered `on_event` callbacks. Register `on_error` callbacks to surface faults — a dead reader
   thread means no sensor events ever arrive again, which would otherwise hang every wait silently.
3. **`hardware.py`** — everything above the wire: `SharedSensorState` (lock-guarded current
   state of A/B/C, door sensor, table sensor, door status, table motor), `EventLogger` (an
   `on_event` callable that updates shared state *and* appends to `sensor_events.csv`),
   `CameraTriggerLogger`, the actuator helpers (`deliver_reward`, `set_led`, `open_door`,
   `close_door_safe`, `turn_table_degrees`, `apply_motor_speeds`), and the blocking waits.
4. **`<Family>/base_session.py`** — per-family session base class. Owns the session thread, the
   trial loop, results DataFrames (guarded by `_df_lock`), reward delivery, and family-specific
   trial primitives. Subclasses implement `_run_trial()`.
5. **`<Family>/<Phase>.py`** — one module per training phase or task variant.
6. **`main_*.py`** — the entry point: run the setup dialog, create the output folder, launch the
   camera subprocess, connect the device, wire up loggers and GUIs, construct the session class for
   the chosen phase, poll GUIs at ~20 Hz while the session thread runs trials, then shut everything
   down in a `finally` block.
7. **`<Family>/setup_dialog.py`** (Tkinter, pre-session parameters) and **`<Family>/gui.py`**
   (matplotlib `SensorGUI` + `PerformanceGUI`, live during the session).

### Task families

Each family is a folder holding its session classes, `setup_dialog.py`, `gui.py` and
`last_settings.json`, with a `main_*.py` entry point at the root. They share layers 1–3 but have
independent session bases and different port semantics.

| Family | Entry point | Folder | Phases / modes |
|---|---|---|---|
| Social Reward | `main_socialreward.py` | `SocialReward/` | 1, 2, 3a, 3b, 4, task |
| Social Reward 2AFC | `main_socialreward2AFC.py` | `SocialReward2AFC/` | 1, 2, 3, 3b, 4, forced, mixed, free |
| Social Memory | `main_socialmemory.py` | `SocialMemory/` | training, task, passivetest |
| Social Choice | `main_socialchoice.py` | `SocialChoice/` | learning, one_choice, two_choice |

Port roles are **not** consistent across families — check the family's `base_session.py` header.
In 2AFC, C initiates the trial and A/B are the two choice ports; in Social Reward, A cues and C is
the response port.

Only `main_socialmemory.py` and `main_socialreward2AFC.py` currently launch the camera and log
camera sync pulses.

### Session output

Every run creates one folder, `<save_root>/<animal>_<session_n>_<phase>_<YYYY-MM-DD>_<species>/`
(`save_root` comes from the setup dialog and is often an external drive; it defaults to
`<Family>Data/` relative to the working directory). Inside:

- `metadata.json` — the full setup-dialog parameter dict plus a timestamp. Written **first**, so a
  crashed session is still identifiable.
- `sensor_events.csv` — headerless: `HH:MM:SS.mmm, t_rel_s, port, state`. Appended line by line as
  events arrive.
- `trials.csv` — the session's results DataFrame, written at shutdown.
- `performance.png` — the final performance figure.
- `recording_<timestamp>.mp4` + `frame_timestamps.csv` + `camera_sync.csv` — video and its two
  timebases.

**Everything shares one `t=0`**: `main_*.py` captures `session_start = time.time()` and passes it
to the `EventLogger`, the `CameraTriggerLogger`, and (via `--session-start`) the camera subprocess.
All `t_rel_s` columns are directly comparable. Preserve this when adding any new log — do not
start a fresh clock.

`replay_performance.py` reads a folder back and rebuilds its figure, auto-detecting the family from
the **parent directory name** (`SocialMemoryData` → socialmemory, etc.); `--family` overrides when a
folder has been moved. It backfills columns that newer GUI code expects but older CSVs lack, so
adding a column to a results DataFrame does not break replay of old sessions.

### Video recording

`cameracontrol` is a standalone script, never imported — the task scripts run it as a subprocess
(`sys.executable cameracontrol.py --session-start ... --save-dir ... --crop reuse`, plus
`--camera <serial>` when one was chosen). Consequences worth knowing:

- It is launched with `--crop reuse` deliberately: its interactive default (`ask`) would block
  forever, because stdin is a pipe the parent only writes to on stop.
- Stop is signalled by writing `"\n"` to that pipe (as if ENTER were pressed), then escalating to
  terminate/kill. On Windows it is started in its own process group so Ctrl+C on the parent doesn't
  bypass its cleanup.
- Cameras are identified by **serial number**, not index — pylon's enumeration order is not stable.
  Each camera keeps its own sensor crop in `camera_crop_config_<serial>.json` at the repo root, so
  two rigs can record simultaneously from two terminals. A camera with no file yet records full
  frame until a crop is picked (run `cameracontrol.py --camera <serial>` standalone to choose one).
  These files are tracked in git, one per camera.
- `camera_select.py` builds the setup dialogs' camera dropdown by shelling out to
  `cameracontrol.py --list-cameras`, keeping `pypylon` out of the Tk process.
- Cropping sets the camera's sensor ROI (`Width/Height/OffsetX/OffsetY`), not a software crop.
- Frames are piped to real `ffmpeg`/libx264 rather than `cv2.VideoWriter`, because only that gives
  real control over quality via CRF.
- Two different camera timebases: `frame_timestamps.csv` is per-frame; `camera_sync.csv` records a
  ~1 Hz TTL pulse (every 30 frames) as seen by the *firmware*, which is what actually ties video to
  rig time. `CameraTriggerLogger` filters on `value == 1` because the firmware usually absorbs the
  falling edge.

## Conventions and hazards

These encode failures already debugged on the rig — preserve them.

- **The firmware sends status EVENTs only on change, and packets do get dropped.** A lost event
  leaves `SharedSensorState` permanently stale and any wait on it hangs the session silently
  (observed roughly 1 session in 4 for the door). Two defenses apply to every wait on device state:
  periodic **resync** (re-read the status register directly — `resync_door_state`,
  `resync_table_motor_state`) and a **bounded timeout plus `WaitHeartbeat`** progress lines. Any new
  blocking wait must have both. Never write an unbounded wait on device state.
- **Recovery-path `print()`s must be ASCII only** (`->`, not `→`). These lines fire on a cp1252
  Windows console, and a `UnicodeEncodeError` there turns the recovery into the crash it exists to
  prevent.
- **Errors must be printed, not only passed to callbacks.** `_report_error` prints unconditionally
  because registering an error callback is optional, and a silently-discarded serial fault is
  indistinguishable from a frozen program.
- **Sessions run on a daemon thread; the main thread owns the GUIs.** matplotlib is not
  thread-safe — the session thread appends rows, the main thread calls `session.snapshot(df)` for a
  locked copy and draws. Never draw from the session thread.
- **`STOP_EVENT` in `hardware.py` is a process-global stop flag** checked by every wait loop; SIGINT
  sets it. `SocialMemory`'s `stop_internal()` exists to stop one session *without* tripping it.
- **A trial that raises must end the session loudly.** `_run_session` catches `TimeoutError` per
  trial but breaks out on anything else — a dead session thread with `self.running` still True
  leaves the GUI loop spinning against a rig that stopped working.
- **Door closing is interlocked and interruptible.** `close_door_safe` stops the door whenever the
  door or table proximity sensor triggers and resumes when both clear; it is meant to run in a
  daemon thread. Pressing **`d`** with a GUI window focused toggles a manual override that forces
  the door open (to clear a jam) and then resumes. While the operator holds it open, the `held_open`
  event suspends the caller's timeout — otherwise a wait would expire and the task would move the
  turntable with the door open and someone's hands in it.
- **Setup dialogs persist their last values** to `<Family>/last_settings.json`, next to the
  dialog module. (The dialogs' default `save_root` is still the repo root, not the family folder.)
  Adding a field means adding it to both the save and the restore path; these files are
  per-PC operator state (gitignored), not configuration to depend on.
- **SocialMemory is the pilot for dialog features** that will later be ported to the other
  families: a required **Setup** dropdown (`rig_setups.SETUPS`) that auto-selects the rig's camera
  from the hard-coded `rig_setups.SETUP_CAMERAS`, a **Treatment** checkbox + description (both go
  into `metadata.json` as `setup` / `treatment` / `treatment_details`), dropdowns of the 4 most
  recent values for free-text fields (`"recent"` in `last_settings.json`), and a **Show live
  sensor display** checkbox — off means `SensorGUI` is never created and `shared.get()` is not
  polled; logging is unaffected.
- **Species changes behavior.** `rat` gets `incremental_reward` (valve time grows with reward
  count, capped); `mouse` gets a fixed `deliver_reward`. Setup dialogs carry separate
  `SPECIES_DEFAULTS`.
- **Motor speed fields may be left blank.** `parse_motor_speed` returns `None` for empty/`off`,
  meaning "don't write that register" — older firmware builds predate the speed registers and would
  not ACK the write.
- **Turntable angles must be multiples of 45°**; `turn_table_degrees` normalizes to the shorter
  direction and raises otherwise. Task phases return the table to home (0°) during shutdown.
- State strings (`"triggered"`/`"cleared"`, `"door opened"`/`"door closed"`/`"door moving"`,
  `"table moving"`/`"table stopped"`) are compared literally throughout the session code and were
  chosen to match the old firmware's wording. Changing one means changing every comparison.
- **Data outputs are gitignored** (`*Data/`, `recording/`, `*.csv`, `*.png`, `*.mp4`), so
  session folders stay local. Video is large — a one-hour session is ~180 MB.
