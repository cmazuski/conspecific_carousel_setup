from pypylon import pylon
import time
tl = pylon.TlFactory.GetInstance()
dev = [d for d in tl.EnumerateDevices() if d.GetSerialNumber() == "24134340"][0]
cam = pylon.InstantCamera(tl.CreateDevice(dev)); cam.Open()
print(cam.GetDeviceInfo().GetModelName())
print("Lines:", cam.LineSelector.Symbolics)
cam.LineSelector.Value = "Line4"
cam.LineMode.Value = "Output"
cam.LineSource.Value = "UserOutput3"
cam.UserOutputSelector.Value = "UserOutput3"
print("Inverter (saved):", cam.LineInverter.Value)
cam.LineInverter.Value = False
print("Inverter (now):", cam.LineInverter.Value)
for v in (True, False, True, False):
    cam.UserOutputValue.Value = v
    time.sleep(0.5)
    print(v, "-> LineStatus", cam.LineStatus.Value)
cam.Close()
