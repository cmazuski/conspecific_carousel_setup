"""
Basler camera recording script
- Records to a compressed H264 MP4 file via a real ffmpeg (libx264) pipe, so the
  CRF setting below actually controls quality/size (OpenCV's built-in VideoWriter
  has no way to do this — see H264_CRF for why this matters)
- Picks a specific camera when several are plugged in (--camera), so two
  instances in two terminals can record two cameras at once; each camera keeps
  its own saved crop
- Optionally crops the sensor ROI before recording (less data in at the source,
  not just a smaller output) via an interactive selector, with the chosen crop
  saved to disk and reused on future runs — see --crop
- Auto-detects mono vs color cameras and encodes accordingly (no wasted chroma
  data on mono sensors)
- Sends a brief TTL pulse every 30 frames (~1 per second at 30 fps)
- Logs a per-frame timestamp (frame_timestamps.csv), relative to --session-start
  if given, so the video can be aligned with other timestamp files from the
  same session
- Shows a live preview window while recording
- Press ENTER (or 'q' in the preview window) to stop

Requirements: pip install pypylon opencv-python
              ffmpeg.exe must be installed and on PATH (https://www.gyan.dev/ffmpeg/builds/)

Standalone usage:  python cameracontrol.py
See which cameras are attached (JSON, one entry per camera):
    python cameracontrol.py --list-cameras
Record from a particular one (serial number, user-defined name, model, or index):
    python cameracontrol.py --camera 40123456
Launched by another process (e.g. main_socialmemory.py), to share its session
clock and write into its session folder:
    python cameracontrol.py --session-start <epoch_seconds> --save-dir <path> --crop reuse
"""

import argparse
import csv
import shutil
import subprocess
import sys
from pypylon import pylon
import cv2
import os
import time
import threading
import json
from datetime import datetime

# ── Settings ──────────────────────────────────────────────────────────────────
OUTPUT_LINE         = "Line4"        # Change to your output line
OUTPUT_USER         = "UserOutput3"  # Change if your camera uses "UserOutput" instead
SAVE_DIR            = "recording"    # Folder to save video into
FRAME_RATE          = 30.0           # Must match your camera's actual frame rate
PULSE_EVERY_N_FRAMES = 30            # Send a TTL pulse every N frames
PULSE_WIDTH_S       = 0.01          # Pulse width in seconds (1 ms)
H264_CRF            = 23             # H264 quality: lower = better quality, larger file
                                     # 18 = near-lossless, 23 = default, 28 = smaller file
FFMPEG_PRESET        = "veryfast"    # Encoder speed/efficiency tradeoff. Must comfortably
                                     # keep up with FRAME_RATE or frames will back up —
                                     # only slow this down (e.g. "fast", "medium") if the
                                     # recording machine has CPU headroom to spare.
SHOW_PREVIEW         = True          # Live preview window while recording
PREVIEW_EVERY_N_FRAMES = 3           # Update the preview only every N frames — throttled
                                     # so it doesn't add latency to the recording/pulse loop
PREVIEW_MAX_WIDTH    = 960           # Downscale the preview/crop-selector window to at
                                     # most this width
# Crops are saved per camera (camera_crop_config_<serial>.json) — two cameras
# looking at two different rigs need two different ROIs.
CROP_CONFIG_DIR      = os.path.dirname(os.path.abspath(__file__))
# ──────────────────────────────────────────────────────────────────────────────

# ── Camera selection ─────────────────────────────────────────────────────────
# With more than one camera plugged in, CreateFirstDevice() picks whichever the
# transport layer happens to enumerate first — so two instances started in two
# terminals would fight over the same camera. Selecting by serial number (or
# user-defined name) makes each instance deterministic.

def enumerate_cameras():
    """Describe every attached Basler camera. Opens nothing, so it's safe to
    call while another process is already recording from one of them."""
    cameras = []
    for i, dev in enumerate(pylon.TlFactory.GetInstance().EnumerateDevices()):
        try:
            user_id = dev.GetUserDefinedName()
        except Exception:
            user_id = ""      # not supported by every transport layer
        cameras.append({
            "index": i,
            "serial": dev.GetSerialNumber(),
            "model": dev.GetModelName(),
            "user_id": user_id,
            "friendly_name": dev.GetFriendlyName(),
        })
    return cameras

def describe_cameras(cameras):
    return "\n".join(
        f"  [{c['index']}] serial {c['serial']}  {c['model']}"
        + (f"  (name: {c['user_id']})" if c["user_id"] else "")
        for c in cameras
    ) or "  (none)"

def _matches(info, selector):
    sel = selector.strip().lower()
    if sel.isdigit() and int(sel) == info["index"]:
        return True
    return sel in (info["serial"].lower(), info["user_id"].lower(),
                   info["model"].lower(), info["friendly_name"].lower())

def open_camera(selector):
    """Open the requested camera (serial / user-defined name / model / index),
    or the first one found if `selector` is None. Returns (camera, info)."""
    factory = pylon.TlFactory.GetInstance()
    devices = factory.EnumerateDevices()
    infos = enumerate_cameras()
    if not devices:
        raise RuntimeError("No Basler cameras found — check the USB/GigE connection.")

    if selector is None:
        device, info = devices[0], infos[0]
        if len(devices) > 1:
            print(f"{len(devices)} cameras attached, none requested — using the first:\n"
                  f"{describe_cameras(infos)}")
    else:
        matched = [(d, i) for d, i in zip(devices, infos) if _matches(i, selector)]
        if not matched:
            raise RuntimeError(
                f"No camera matching '{selector}'. Attached cameras:\n"
                f"{describe_cameras(infos)}")
        if len(matched) > 1:
            raise RuntimeError(
                f"'{selector}' matches {len(matched)} cameras — use a serial number "
                f"instead. Attached cameras:\n{describe_cameras(infos)}")
        device, info = matched[0]

    camera = pylon.InstantCamera(factory.CreateDevice(device))
    try:
        camera.Open()
    except Exception as e:
        raise RuntimeError(
            f"Could not open camera {info['serial']} ({info['model']}): {e}\n"
            "If another recording is already running, it holds that camera — "
            "pick the other one with --camera.") from e
    return camera, info

def setup_user_output(camera) -> bool:
    """Configure the digital output line used for the TTL sync pulse. Returns
    True if the camera supports it, False if not (e.g. no digital I/O lines
    on this model) — callers should then skip set_output() for the rest of
    the session instead of throwing on every frame."""
    try:
        camera.LineSelector.Value = OUTPUT_LINE
        # Line direction is stored per camera, so a second camera can arrive
        # with this line set to Input — in which case LineSource is read-only
        # and the write below raises. Switch it to an output first.
        if camera.LineMode.Value != "Output":
            camera.LineMode.Value = "Output"
        camera.LineSource.Value = OUTPUT_USER
        camera.UserOutputSelector.Value = OUTPUT_USER
        return True
    except Exception as e:
        print(f"[WARN] Camera has no usable digital output line ({e}) — "
              f"TTL sync pulse disabled for this recording.")
        return False

def set_output(camera, state: bool):
    camera.UserOutputValue.Value = state

def show_preview(frame, width, height, window="Camera Preview") -> bool:
    """Display frame (downscaled to PREVIEW_MAX_WIDTH). Returns True if 'q' was pressed.

    `window` carries the camera's identity so two simultaneous recordings don't
    share (and overwrite) one preview window.
    """
    if width > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / width
        frame = cv2.resize(frame, (PREVIEW_MAX_WIDTH, int(height * scale)))
    cv2.imshow(window, frame)
    return cv2.waitKey(1) & 0xFF == ord("q")

# ── Sensor cropping ─────────────────────────────────────────────────────────
# Cropping is done on the camera's sensor ROI (Width/Height/OffsetX/OffsetY),
# not by cropping frames in software after the fact — this actually reduces
# the data read off the sensor and pushed through conversion/encoding.

def configure_converter(camera):
    """Pick BGR8 vs Mono8 output based on the camera's actual pixel format, so
    mono cameras aren't forced through a 3-channel color pipeline for nothing."""
    is_mono = str(camera.PixelFormat.Value).startswith("Mono")
    converter = pylon.ImageFormatConverter()
    converter.OutputPixelFormat = pylon.PixelType_Mono8 if is_mono else pylon.PixelType_BGR8packed
    converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
    return converter, is_mono

def reset_to_full_sensor(camera):
    """Zero the offsets and maximize Width/Height, returning the true sensor size."""
    camera.OffsetX.Value = camera.OffsetX.Min
    camera.OffsetY.Value = camera.OffsetY.Min
    camera.Width.Value = camera.Width.Max
    camera.Height.Value = camera.Height.Max
    return camera.Width.Value, camera.Height.Value

def grab_single_frame(camera, converter):
    camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
    try:
        with camera.RetrieveResult(5000) as result:
            if not result.GrabSucceeded():
                raise RuntimeError("Failed to grab a preview frame from the camera.")
            return converter.Convert(result).GetArray()
    finally:
        camera.StopGrabbing()

def _snap(value, minv, maxv, inc):
    value = max(minv, min(value, maxv))
    steps = round((value - minv) / inc)
    return max(minv, min(minv + steps * inc, maxv))

def apply_crop(camera, x, y, w, h):
    """Set the sensor ROI, snapping to the camera's valid increments/bounds."""
    # Zero offsets first so Width/Height aren't constrained by the old ROI.
    camera.OffsetX.Value = camera.OffsetX.Min
    camera.OffsetY.Value = camera.OffsetY.Min
    w = _snap(w, camera.Width.Min, camera.Width.Max, camera.Width.Inc)
    h = _snap(h, camera.Height.Min, camera.Height.Max, camera.Height.Inc)
    camera.Width.Value = w
    camera.Height.Value = h
    x = _snap(x, camera.OffsetX.Min, camera.OffsetX.Max, camera.OffsetX.Inc)
    y = _snap(y, camera.OffsetY.Min, camera.OffsetY.Max, camera.OffsetY.Inc)
    camera.OffsetX.Value = x
    camera.OffsetY.Value = y
    return x, y, w, h

def _to_display(frame, sensor_w, sensor_h):
    scale = 1.0
    display = frame
    if sensor_w > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / sensor_w
        display = cv2.resize(frame, (int(sensor_w * scale), int(sensor_h * scale)))
    return display, scale

def select_crop_roi(camera, converter):
    """Let the operator drag a crop rectangle on a live full-sensor frame."""
    sensor_w, sensor_h = reset_to_full_sensor(camera)
    frame = grab_single_frame(camera, converter)
    display, scale = _to_display(frame, sensor_w, sensor_h)
    window = "Select crop region  --  drag a box, ENTER/SPACE to confirm, C to cancel"
    x, y, w, h = cv2.selectROI(window, display, showCrosshair=True)
    cv2.destroyWindow(window)
    if w == 0 or h == 0:
        return None  # cancelled -> full frame
    x, y, w, h = (int(round(v / scale)) for v in (x, y, w, h))
    return apply_crop(camera, x, y, w, h)

def confirm_or_redefine(camera, converter, saved):
    """Show the previously saved crop on a live frame; let the operator accept,
    redefine, or drop to full frame — all via keypresses on the preview window
    (not stdin, since stdin may be a pipe from a launching process)."""
    assert saved is not None  # only called with a compatible saved crop
    sensor_w, sensor_h = reset_to_full_sensor(camera)
    frame = grab_single_frame(camera, converter)
    display, scale = _to_display(frame, sensor_w, sensor_h)
    if display.ndim == 2:
        display = cv2.cvtColor(display, cv2.COLOR_GRAY2BGR)
    else:
        display = display.copy()
    x, y, w, h = (int(round(saved[k] * scale))
                  for k in ("offset_x", "offset_y", "width", "height"))
    cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 2)
    for i, line in enumerate([
        f"Saved crop: {saved['width']}x{saved['height']} at ({saved['offset_x']},{saved['offset_y']})",
        "ENTER = use saved crop   R = redefine   F = full frame",
    ]):
        cv2.putText(display, line, (10, 25 + 25 * i), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2)
    window = "Crop settings"
    cv2.imshow(window, display)
    try:
        while True:
            key = cv2.waitKey(0) & 0xFF
            if key in (13, 10):  # Enter
                return apply_crop(camera, saved["offset_x"], saved["offset_y"],
                                   saved["width"], saved["height"])
            elif key in (ord("r"), ord("R")):
                return select_crop_roi(camera, converter)
            elif key in (ord("f"), ord("F")):
                reset_to_full_sensor(camera)
                return None
    finally:
        cv2.destroyWindow(window)

def load_crop_config(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

def save_crop_config(path, cfg):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)

def resolve_crop_config(args, serial):
    """Where this camera's crop lives. Each camera gets its own file so a second
    camera's ROI doesn't overwrite the first's; a camera with no file yet
    records full frame until a crop is chosen."""
    if args.crop_config:
        return args.crop_config
    return os.path.join(CROP_CONFIG_DIR, f"camera_crop_config_{serial}.json")

def decide_crop(camera, converter, args, crop_config):
    """Apply --crop policy, loading/saving this camera's crop from `crop_config`.
    Returns the (x, y, w, h) crop actually applied, or None if recording at full
    sensor resolution."""
    if args.crop == "none":
        sensor_w, sensor_h = reset_to_full_sensor(camera)
        print(f"Crop disabled — full sensor frame ({sensor_w}x{sensor_h})")
        return None

    saved = load_crop_config(crop_config)
    sensor_w, sensor_h = reset_to_full_sensor(camera)
    compatible = (saved is not None
                  and saved.get("sensor_width") == sensor_w
                  and saved.get("sensor_height") == sensor_h)

    if args.crop == "redefine":
        crop = select_crop_roi(camera, converter)
    elif args.crop == "reuse":
        if compatible and saved is not None:
            crop = apply_crop(camera, saved["offset_x"], saved["offset_y"],
                               saved["width"], saved["height"])
        else:
            if saved is not None:
                print("Saved crop doesn't match this sensor's resolution — using full frame.")
            crop = None
    else:  # "ask"
        crop = confirm_or_redefine(camera, converter, saved) if compatible and saved is not None \
            else select_crop_roi(camera, converter)

    if crop is not None:
        x, y, w, h = crop
        save_crop_config(crop_config, {
            "offset_x": x, "offset_y": y, "width": w, "height": h,
            "sensor_width": sensor_w, "sensor_height": sensor_h,
        })
        print(f"Crop applied: {w}x{h} at ({x},{y}) — saved to {crop_config}")
    else:
        print(f"Recording at full sensor frame ({sensor_w}x{sensor_h})")
    return crop

# ── ffmpeg (H264) writer ─────────────────────────────────────────────────────
# cv2.VideoWriter has no way to set CRF/bitrate — whatever codec ends up
# opening (and it may silently fall back to a much less efficient one) uses
# an opaque default rate control. Piping raw frames into a real ffmpeg
# process gives real control over quality/size via -crf.

def find_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it (e.g. static builds at "
            "https://www.gyan.dev/ffmpeg/builds/) and make sure ffmpeg.exe is on PATH."
        )
    return path

def start_ffmpeg_writer(ffmpeg_path, video_path, width, height, is_mono):
    pix_fmt_in = "gray" if is_mono else "bgr24"
    log_path = video_path + ".ffmpeg.log"
    cmd = [
        ffmpeg_path, "-y",
        "-f", "rawvideo", "-pix_fmt", pix_fmt_in,
        "-s", f"{width}x{height}", "-r", str(FRAME_RATE),
        "-i", "-",
        "-an",
        "-c:v", "libx264", "-crf", str(H264_CRF), "-preset", FFMPEG_PRESET,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-nostats", "-loglevel", "warning",
        video_path,
    ]
    log_file = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log_file, stderr=subprocess.STDOUT)
    return proc, log_file, log_path

def stop_ffmpeg_writer(proc, log_file, log_path):
    try:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
    except (OSError, BrokenPipeError):
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log_file.close()
    if proc.returncode != 0:
        print(f"ffmpeg exited with code {proc.returncode} — see {log_path}")

def parse_args():
    parser = argparse.ArgumentParser(description="Basler camera recording script")
    parser.add_argument(
        "--session-start", type=float, default=None,
        help="Epoch seconds to use as t=0 for frame_timestamps.csv. "
             "Defaults to this process's own start time (standalone use).",
    )
    parser.add_argument(
        "--save-dir", type=str, default=SAVE_DIR,
        help="Folder to save the video and frame_timestamps.csv into.",
    )
    parser.add_argument(
        "--crop", choices=["ask", "reuse", "redefine", "none"], default="ask",
        help="Sensor-crop policy. 'ask' (default, standalone use): reuse the saved "
             "crop after a quick confirm, or select one if none saved. 'reuse': load "
             "the saved crop with no prompt, full frame if none saved — use this when "
             "launched non-interactively (e.g. from main_socialmemory.py). 'redefine': "
             "always prompt for a new crop. 'none': always record full sensor frame.",
    )
    parser.add_argument(
        "--crop-config", type=str, default=None,
        help="Path to save/load crop settings. Default: camera_crop_config_"
             "<serial>.json next to this script, i.e. one saved crop per camera.",
    )
    parser.add_argument(
        "--camera", type=str, default=None,
        help="Which camera to record from: serial number, user-defined name, "
             "model name, or index from --list-cameras. Default: the first "
             "camera found. Give a serial when two cameras are plugged in, so "
             "two instances in two terminals each get their own.",
    )
    parser.add_argument(
        "--list-cameras", action="store_true",
        help="Print the attached Basler cameras as JSON and exit (opens nothing, "
             "so it's safe to run while a recording is in progress).",
    )
    return parser.parse_args()

def main():
    args = parse_args()

    if args.list_cameras:
        # Machine-readable so the setup GUIs can offer a camera dropdown
        # without importing pypylon into the Tk process.
        print(json.dumps(enumerate_cameras()))
        return 0

    session_start = args.session_start if args.session_start is not None else time.time()
    save_dir = args.save_dir

    ffmpeg_path = find_ffmpeg()

    try:
        camera, cam_info = open_camera(args.camera)
    except RuntimeError as e:
        print(e)
        return 1
    cam_label = (f"{cam_info['model']} ({cam_info['user_id']}, {cam_info['serial']})"
                 if cam_info["user_id"] else
                 f"{cam_info['model']} ({cam_info['serial']})")
    preview_window = f"Camera Preview — {cam_label}"
    print(f"Camera: {cam_label}")

    os.makedirs(save_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = os.path.join(save_dir, f"recording_{timestamp}.mp4")
    timestamps_path = os.path.join(save_dir, "frame_timestamps.csv")
    frame_timestamps = []

    # Configure UserOutput line (not all camera models expose a digital I/O
    # line — output_available gates every later set_output() call so a camera
    # without one just runs without a sync pulse instead of throwing).
    output_available = setup_user_output(camera)
    if output_available:
        set_output(camera, False)

    converter, is_mono = configure_converter(camera)
    decide_crop(camera, converter, args,
                resolve_crop_config(args, cam_info["serial"]))

    # Grab one frame first to get resolution (post-crop) for the video writer
    first_frame = grab_single_frame(camera, converter)
    height, width = first_frame.shape[:2]

    if SHOW_PREVIEW:
        show_preview(first_frame, width, height, preview_window)

    ffmpeg_proc, ffmpeg_log, ffmpeg_log_path = start_ffmpeg_writer(
        ffmpeg_path, video_path, width, height, is_mono)
    assert ffmpeg_proc.stdin is not None  # guaranteed by stdin=subprocess.PIPE above

    # Write the first frame we already grabbed
    ffmpeg_proc.stdin.write(first_frame.tobytes())
    frame_count = 1
    frame_timestamps.append(time.time() - session_start)

    # Stop flag controlled by keypress
    stop_flag = threading.Event()

    def wait_for_enter():
        input("Recording... Press ENTER to stop.\n")
        stop_flag.set()

    print(f"Recording started -> {video_path}")
    print(f"Resolution: {width}x{height} @ {FRAME_RATE} fps ({'mono' if is_mono else 'color'})")
    print(f"H264 CRF {H264_CRF}, preset {FFMPEG_PRESET}")
    print(f"TTL pulse every {PULSE_EVERY_N_FRAMES} frames."
          if output_available else "TTL pulse disabled (no digital output line on this camera).")
    if SHOW_PREVIEW:
        print("Live preview window open — press 'q' there (or ENTER here) to stop.")

    key_thread = threading.Thread(target=wait_for_enter, daemon=True)
    key_thread.start()

    camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    # try/finally guarantees the video is finalized and timestamps are written
    # no matter how the loop ends — including a stray Ctrl+C reaching this
    # process directly (KeyboardInterrupt isn't caught by "except Exception"
    # below, so without this it would skip straight past all cleanup).
    try:
        while camera.IsGrabbing() and not stop_flag.is_set():
            try:
                with camera.RetrieveResult(1000, pylon.TimeoutHandling_Return) as result:
                    if result and result.GrabSucceeded():
                        frame = converter.Convert(result).GetArray()
                        ffmpeg_proc.stdin.write(frame.tobytes())
                        frame_count += 1
                        frame_timestamps.append(time.time() - session_start)

                        # TTL pulse every N frames (skipped if this camera has
                        # no usable digital output line — see output_available)
                        if output_available and frame_count % PULSE_EVERY_N_FRAMES == 0:
                            set_output(camera, True)
                            time.sleep(PULSE_WIDTH_S)
                            set_output(camera, False)

                        if SHOW_PREVIEW and frame_count % PREVIEW_EVERY_N_FRAMES == 0:
                            if show_preview(frame, width, height, preview_window):
                                stop_flag.set()

            except Exception as e:
                print(f"Grab error: {e}")
                break
    except KeyboardInterrupt:
        print("Interrupted — finalizing recording...")
    finally:
        stop_ffmpeg_writer(ffmpeg_proc, ffmpeg_log, ffmpeg_log_path)
        camera.StopGrabbing()
        camera.Close()
        if SHOW_PREVIEW:
            cv2.destroyAllWindows()

        with open(timestamps_path, "w", newline="") as f:
            csv_writer = csv.writer(f)
            csv_writer.writerow(["frame_num", "t_rel_s"])
            csv_writer.writerows(
                (i, f"{t:.3f}") for i, t in enumerate(frame_timestamps, start=1))

        duration_s = frame_count / FRAME_RATE
        size_mb = os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 0.0
        print(f"Recording stopped.")
        print(f"  Frames:     {frame_count} ({duration_s:.1f} s)")
        print(f"  File:       {video_path} ({size_mb:.1f} MB)")
        print(f"  Timestamps: {timestamps_path}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
