"""cec.py - HDMI-CEC for the DVB-T2 receiver, via cec-ctl (v4l-utils).

* Announces the receiver as a Playback device with its own OSD name, so the
  TV lists it by name, and (optionally) switches the TV to it at start-up.
* Answers the TV's menu-status and power-status requests so remote keys are
  passed through, and delivers remote key presses to a callback.
Runs as root (cec-ctl --monitor needs it). G8YTZ project, GPLv3."""
import re
import subprocess
import threading
import time

KEYS = {0x00: "select", 0x01: "up", 0x02: "down", 0x03: "left", 0x04: "right",
        0x09: "menu", 0x0d: "back", 0x30: "ch_up", 0x31: "ch_down", 0x35: "info",
        0x44: "play", 0x45: "stop", 0x46: "pause",
        0x71: "blue", 0x72: "red", 0x73: "green", 0x74: "yellow"}
for _n in range(10):
    KEYS[0x20 + _n] = str(_n)


class Cec:
    def __init__(self, on_key, name="T2 Receiver", dev="/dev/cec0", log=print):
        self.on_key, self.name, self.dev, self.log = on_key, name[:14], dev, log
        self.phys = None
        self.proc = None
        self._last = (None, 0.0)

    def _ctl(self, *args, timeout=8):
        try:
            r = subprocess.run(["cec-ctl", "-d", self.dev] + list(args), capture_output=True,
                               text=True, timeout=timeout)
            return r.stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            self.log("cec: %s" % e)
            return ""

    def start(self, active_source=True):
        out = self._ctl("--playback", "--osd-name", self.name, "--vendor-id", "0x000c03")
        m = re.search(r"Physical Address\s*:\s*([0-9a-f.]+)", out)
        self.phys = m.group(1) if m else None
        self.log("cec: '%s' physical address %s" % (self.name, self.phys))
        if active_source:
            self.announce()
        threading.Thread(target=self._monitor, daemon=True).start()

    def announce(self):
        """One Touch Play: wake the TV and make us the active source."""
        if not self.phys:
            return
        self._ctl("--to", "0", "--image-view-on")
        self._ctl("--to", "15", "--active-source", "phys-addr=%s" % self.phys)

    def _reply(self, to, *args):
        threading.Thread(target=self._ctl, args=("--to", str(to)) + args, daemon=True).start()

    def _monitor(self):
        while True:
            try:
                self.proc = subprocess.Popen(["cec-ctl", "-d", self.dev, "--monitor"],
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, bufsize=1)
            except OSError as e:
                self.log("cec: monitor failed: %s" % e)
                return
            pending_key = False
            for line in self.proc.stdout:
                src = re.search(r"\((\d+) to (\d+)\)", line)
                if "Received from" in line and src:
                    frm = int(src.group(1))
                    pending_key = "USER_CONTROL_PRESSED" in line
                    if "GIVE_DEVICE_MENU_STATUS" in line or "MENU_REQUEST" in line:
                        self._reply(frm, "--menu-status", "menu-state=activated")
                    elif "GIVE_DEVICE_POWER_STATUS" in line:
                        self._reply(frm, "--report-power-status", "pwr-state=on")
                    elif ("REQUEST_ACTIVE_SOURCE" in line or "SET_STREAM_PATH" in line) and self.phys:
                        self._reply(15, "--active-source", "phys-addr=%s" % self.phys)
                    continue
                if pending_key and "ui-cmd" in line:
                    pending_key = False
                    m = re.search(r"\(0x([0-9a-f]+)\)", line)
                    if m:
                        self._key(int(m.group(1), 16))
            time.sleep(2)            # monitor died: restart it

    def _key(self, code):
        name = KEYS.get(code)
        if not name:
            return
        now = time.monotonic()
        last, t = self._last
        if name == last and now - t < 0.35:      # auto-repeat while held
            return
        self._last = (name, now)
        self.on_key(name)

    def stop(self):
        if self.proc:
            self.proc.terminate()
