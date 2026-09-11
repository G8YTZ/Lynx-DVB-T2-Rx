#!/usr/bin/env python3
"""osd_plane_test.py - try the OSD on its own display plane, over zero-copy video.
Plays /tmp/bench.ts (made by zero_bench.sh). Run with the receiver stopped:
    sudo systemctl stop t2rx
    sudo python3 osd_plane_test.py
    sudo systemctl start t2rx
"""
import sys
import time
sys.path.insert(0, "/opt/t2rx")
sys.path.insert(0, "/opt/t2rx")
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
import osd
from osdplane import OsdPlane


def cpu():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:8]]
    return v[0] + v[1] + v[2] + v[4] + v[5] + v[6], v[3]


Gst.init(None)
pl = OsdPlane()
print("display %s: %dx%d, crtc %d, video plane %d, OSD plane %d"
      % (pl.dev, pl.w, pl.h, pl.crtc_id, pl.video_plane, pl.osd_plane))
Q = "queue max-size-time=0 max-size-buffers=0 max-size-bytes=20000000"
desc = ("filesrc location=/tmp/bench.ts ! tsparse ! tsdemux name=d "
        "d. ! video/x-h264 ! %s ! h264parse ! v4l2h264dec ! kmssink fd=%d plane-id=%d "
        "d. ! audio/mpeg ! %s ! decodebin ! audioconvert ! audioresample ! "
        "alsasink device=hdmi:CARD=vc4hdmi,DEV=0 async=false" % (Q, pl.fd, pl.video_plane, Q))
pipe = Gst.parse_launch(desc)
pipe.set_state(Gst.State.PLAYING)
st = {"state": "LOCK", "sig": "-77.6", "cnr": "28.3", "rate": "1.10", "mod": "QPSK",
      "fec": "1/2", "gi": "1/8", "fft": "2K"}
info = {"preset": 1, "name": "70cm T2 1.7", "freq": 436.0, "bw": 1700, "callsign": "G8YTZ",
        "provider": "Portsdown 4", "video": "H.264 800x448", "audio": "AAC 48 kHz"}
s = pl.w / 800.0
b0, i0 = cpu()
t0 = time.time()
bus = pipe.get_bus()
shown = None
while time.time() - t0 < 55:
    msg = bus.timed_pop_filtered(500 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
    if msg:
        if msg.type == Gst.MessageType.ERROR:
            print("player error:", msg.parse_error()[0].message)
        break
    el = time.time() - t0
    mode = "full" if el < 20 else ("mini" if el < 40 else "off")
    if mode == "off":
        if shown != "off":
            pl.hide()
    else:
        st["cnr"] = "%.1f" % (28.0 + (int(el) % 5) * 0.1)          # changing value, like live
        img = osd.render_osd(pl.w, st, info, mode)
        rc = pl.show(img, 24 * s, 20 * s)
        if shown != mode:
            print("%4.0f s: OSD %s (%dx%d) -> %s" % (el, mode, img.width, img.height,
                                                     "on screen" if rc == 0 else "SetPlane error %d" % rc))
    if shown != mode:
        shown = mode
    time.sleep(0.5)
b1, i1 = cpu()
print("CPU %.0f%% over %.0f s (zero-copy video + OSD plane, OSD redrawn every 0.5 s)"
      % ((b1 - b0) * 100.0 / ((b1 - b0) + (i1 - i0)), time.time() - t0))
pipe.set_state(Gst.State.NULL)
pl.close()
