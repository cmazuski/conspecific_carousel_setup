# rig_setups.py — the physical setups, and which camera films each one
#
# The cameras are bolted to their rigs and never move, so the camera for a
# session follows from the setup it runs on. Picking a setup in the setup
# dialog selects its camera automatically (the operator can still override it,
# and is asked to confirm if they do).
#
# Cameras are identified by serial number (printed on the camera; also shown by
# `cameracontrol.py --list-cameras`). Leave a serial blank ("") for a setup with
# no camera, or whose camera hasn't been assigned yet — the dialog then leaves
# the camera choice alone.
#
# Known cameras (one camera_crop_config_<serial>.json each): 24134340,
# 24134346, 24658803.

SETUPS = ["Setup 1", "Setup 2", "Setup 3", "Setup 4"]

SETUP_CAMERAS = {
    "Setup 1": "24134346",
    "Setup 2": "24134340",
    "Setup 3": "",
    "Setup 4": "",
}
