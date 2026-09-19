#!/usr/bin/env python3
"""t2rxd - G8YTZ DVB-T2 receiver appliance (Raspberry Pi + TV HAT -> HDMI).

  tuner    : t2rx (C) - locks the Sony CXD2880, TS to a pipe, status file
  player   : GStreamer - tsdemux, hardware H.264 decode, kmssink, HDMI audio
  OSD      : Lynx-style panel blended onto the video (gdkpixbufoverlay)
  idle     : full-screen status page on the framebuffer when there is no picture
  control  : HDMI-CEC TV remote, and t2rx-ctl on a local socket
Bandwidths 1350 / 1700 / 2000 kHz (1350 and 2000 need the patched driver).
G8YTZ narrowband DVB-T2 project, GPLv3.
"""
import configparser
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import traceback
import time

from PIL import Image  # noqa: E402
import gi
gi.require_version("Gst", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gst, GdkPixbuf  # noqa: E402
try:
    gi.require_version("GstMpegts", "1.0")
    from gi.repository import GstMpegts  # noqa: E402
except (ValueError, ImportError):
    GstMpegts = None

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import osd  # noqa: E402
import fb as fbmod  # noqa: E402
import update as updater  # noqa: E402
import web as webmod  # noqa: E402
try:
    import osdplane  # noqa: E402
except (OSError, ImportError):          # no libdrm: fall back to blending
    osdplane = None

VERSION = "t2rx 1.9.41"
# Test hooks: T2RX_TUNER (tuner program), T2RX_DECODER, T2RX_VSINK, T2RX_ASINK, T2RX_ROOT
ENV = os.environ.get
CONF = "/etc/t2rx/t2rx.conf"
PRESETS = "/etc/t2rx/presets.conf"
STATE = "/var/lib/t2rx/state.json"
STATUS = "/run/t2rx.status"
SOCK = "/run/t2rx.sock"
LOG = "/var/log/t2rx.log"
LOG_MAX = 512 * 1024          # trim to half this when it is reached
NB = "/sys/module/cxd2880/parameters"
# driver override per bandwidth: (nb_fs_hz, nb_if_bw); 1700 = stock driver
# bandwidth -> (nb_fs_hz, nb_if_bw) driver overrides. 0/-1 means none is needed:
# 1.7, 5, 6, 7 and 8 MHz are standard DVB-T2 modes the tuner already knows, and
# only 1350 and 2000 kHz require the patched driver.
BW_TABLE = {1350: (1542857, 3), 1700: (0, -1), 2000: (2285714, 0),
            5000: (0, -1), 6000: (0, -1), 7000: (0, -1), 8000: (0, -1)}
STANDARD_BW = (1700, 5000, 6000, 7000, 8000)      # no driver patch needed


def log(msg):
    # strip control characters: text from git, or a DVB service name in another
    # character set, would otherwise make the log a "binary file" to grep
    msg = "".join(c if (c.isprintable() or c == " ") else "?" for c in str(msg))
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
            # keep the log bounded: this runs for years on an SD card
            if f.tell() > LOG_MAX:
                _trim_log()
    except OSError:
        pass


def _trim_log():
    """Keep the most recent half and throw the rest away, in one pass."""
    try:
        with open(LOG, "rb") as f:
            f.seek(-LOG_MAX // 2, 2)
            f.readline()                       # start at a line boundary
            tail = f.read()
        with open(LOG + ".tmp", "wb") as f:
            f.write(b"(earlier entries removed to keep this file small)\n" + tail)
        os.replace(LOG + ".tmp", LOG)
    except OSError:
        pass


def read_conf():
    c = configparser.ConfigParser()
    c.read_dict({"receiver": {
        "audio": "hdmi:CARD=vc4hdmi,DEV=0", "scale": "kms", "osd": "auto", "osd_timeout": "15",
        "osd_plane": "auto", "audio_buffer_ms": "100", "audio_volume": "0.8",
        "osd_interval": "2", "start_buffer_ms": "1500", "max_buffer_ms": "8000",
        "updates": "auto", "web": "on", "web_port": "8080",
        "decoder": "auto", "deinterlace": "auto", "stall_secs": "6",
        "pacing": "on", "pace_ms": "40", "audio_slave": "resample",
        "no_picture_secs": "20",
        "cec": "yes", "cec_name": "Lynx DVB-T2 Rx", "cec_active_source": "yes",
        "adapter": "0", "loss_seconds": "5"}})
    c.read(CONF)
    return c["receiver"]


def read_presets():
    c = configparser.ConfigParser()
    c.read(PRESETS)
    out = []
    for sec in c.sections():
        try:
            key = int(sec)
            p = c[sec]
            bw = int(p.get("bw", "1700"))
            out.append((key, {"name": p.get("name", "Preset %d" % key), "freq": float(p.get("freq", "436")),
                              "bw": bw, "plp": int(p.get("plp", "0")),
                              "service": int(p.get("service", "0")),
                              "if_bw": p.get("if_bw", "")}))
        except ValueError:
            continue
    out.sort()
    if not out:
        out = [(1, {"name": "70cm T2 1.7", "freq": 436.0, "bw": 1700, "plp": 0})]
    return out


# 2000 is deliberately not offered: the tuner has no IF filter between 1.7 and
# 5 MHz, so a 2 MHz signal either collects the 5 MHz filter's noise (measured
# 10 dB worse than 1350 on the same path) or has its edges clipped by the 1.7 MHz
# one (30 dB, and flakey). It still works if a preset asks for it by name.
BW_CHOICES = (1350, 1700, 5000, 6000, 7000, 8000)


def write_presets(presets):
    """Rewrite presets.conf from a list of (key, dict). Written via a temporary
    file so a power cut can't leave it empty."""
    lines = ["# /etc/t2rx/presets.conf - Lynx DVB-T2 Receiver presets (1-9).",
             "# freq in MHz. bw in kHz: 1350, 1700, 2000, 5000, 6000, 7000 or 8000.",
             "# service = N picks one programme when a multiplex carries several.", ""]
    for key, p in sorted(presets):
        lines += ["[%d]" % key, "name = %s" % p.get("name", "Preset %d" % key),
                  "freq = %.3f" % p["freq"], "bw = %d" % int(p["bw"])]
        if p.get("plp"):
            lines.append("plp = %d" % int(p["plp"]))
        if p.get("service"):
            lines.append("service = %d" % int(p["service"]))
        if str(p.get("if_bw", "")) != "":
            lines.append("if_bw = %s" % p["if_bw"])
        lines.append("")
    try:
        os.makedirs(os.path.dirname(PRESETS), exist_ok=True)
        with open(PRESETS + ".tmp", "w") as f:
            f.write("\n".join(lines))
            f.flush()
            os.fsync(f.fileno())
        os.replace(PRESETS + ".tmp", PRESETS)
        return True
    except OSError as e:
        log("presets: %s" % e)
        return False


def display_par():
    """Pixel shape kmssink will assume for this monitor (same rule as kmssink)."""
    import glob
    import re
    try:
        d = open(glob.glob("/sys/class/drm/card*-HDMI-A-1/edid")[0], "rb").read()
        t = d[54:72]
        w = ((t[14] >> 4) << 8) | t[12]
        h = ((t[14] & 15) << 8) | t[13]
        W, H = map(int, re.findall(r"\d+", open(glob.glob("/sys/class/drm/card*-HDMI-A-1/modes")[0]).readline())[:2])
        r = w * H / (h * W) if w and h else 1.0
    except (OSError, IndexError, ValueError, ZeroDivisionError):
        r = 1.0
    m = [(1, 1), (16, 15), (11, 10), (54, 59), (64, 45), (5, 3), (4, 3)]
    c = m + [(b, a) for a, b in m]
    a, b = min(c, key=lambda p: abs(r - p[0] / p[1]))
    return "%d/%d" % (a, b)


class Receiver:
    def __init__(self):
        Gst.init(None)
        if GstMpegts is not None:
            GstMpegts.initialize()
        self.cfg = read_conf()
        self.presets = read_presets()
        self.preset = self._load_state()
        self.loop = GLib.MainLoop()
        self.tuner = None
        self.pipe = None
        self.st = {}
        self.info = {}
        self.video_on = False
        self.video_w = 800
        self.osd_mode = self.cfg.get("osd", "auto")      # auto | full | mini | off
        self.osd_until = 0.0
        self.osd_el = None
        self.last_osd = None
        self.warning = None
        self.message = None
        self.fb = fbmod.Framebuffer()
        self.last_idle = 0.0
        self.restart_at = None
        self.gen = 0                                      # pipeline generation (ignore stale callbacks)
        self.par = display_par() if self.cfg.get("scale") == "fix" else "1/1"
        # OSD on its own display plane (zero-copy video, no per-frame CPU); else blend (1.1 method)
        self.plane = None
        if self.cfg.get("osd_plane", "auto") != "off" and osdplane is not None:
            try:
                self.plane = (osdplane.FakePlane() if ENV("T2RX_FAKEPLANE") else osdplane.OsdPlane())
            except Exception as e:
                log("OSD plane unavailable (%s) - blending the OSD instead" % e)
        self._osd_job = None
        self._osd_event = threading.Event()
        if self.plane is not None:
            threading.Thread(target=self._osd_worker, daemon=True).start()
        self.playing = False
        self.pause_t = 0.0
        self.over_t = None
        self.services = []           # [(program number, name)] from the PAT and SDT
        self._logged_mux = False
        self.service = 0             # 0 = whichever the multiplex offers first
        self.tune = None             # on-screen tuning entry, see key()
        self.tune_t = 0.0
        self.list_until = 0.0        # show the preset list over the picture until this time
        self.update_tag = None       # newer release found, shown on the status page
        self.updating = False
        self.update_stage = ""
        self.updating_exit = False
        self.sw_decode = (self.cfg.get("decoder", "auto") == "sw")
        self.last_frame = 0.0        # when the decoder last produced a frame
        self.stalls = 0
        self.locked_at = 0.0         # when the tuner last reported lock
        self.no_picture = 0
        self.safe_mode = False       # set if the OSD chain keeps failing: play without it
        self.fails = 0               # player errors since the last picture

    # ------------------------------------------------------------ state
    def _load_state(self):
        try:
            with open(STATE) as f:
                p = int(json.load(f).get("preset", 1))
        except (OSError, ValueError):
            p = 1
        keys = [k for k, _ in self.presets]
        return p if p in keys else keys[0]

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE + ".tmp", "w") as f:
                json.dump({"preset": self.preset}, f)
            os.replace(STATE + ".tmp", STATE)
        except OSError:
            pass

    def cur(self):
        return dict(self.presets)[self.preset]

    # ------------------------------------------------------------ driver
    def set_driver(self, bw, if_bw=None):
        """The tuner's IF filter has no setting between 1.7 and 5 MHz, so a 2 MHz
        signal is either passed through the 5 MHz filter - admitting far more
        noise than it needs - or through the 1.7 MHz one, which clips its edges.
        Measured on air, 1350 (1.7 MHz filter) gave 10 dB better C/N than 2000
        (5 MHz filter), so a preset can say which to use: if_bw = 3 forces the
        1.7 MHz filter, 0 the 5/6 MHz one, 1 = 7 MHz, 2 = 8 MHz."""
        fs, ifb = BW_TABLE.get(bw, (0, -1))
        if if_bw is not None and str(if_bw) != "":
            ifb = int(if_bw)
        self.warning = None
        if not os.path.exists(NB + "/nb_fs_hz"):
            if bw not in STANDARD_BW:
                self.warning = "%d kHz needs the patched cxd2880 driver" % bw
            return
        for name, val in (("nb_fs_hz", fs), ("nb_if_bw", ifb)):
            try:
                with open("%s/%s" % (NB, name), "w") as f:
                    f.write(str(val))
            except OSError as e:
                log("driver: %s" % e)
        if bw not in BW_TABLE:
            self.warning = "%d kHz is not a supported bandwidth" % bw

    # ------------------------------------------------------------ tuner + player
    def start(self):
        self.stop()
        self.gen += 1
        gen = self.gen
        p = self.cur()
        self.set_driver(p["bw"], p.get("if_bw"))
        self.services = []
        self._logged_mux = False
        self.service = int(p.get("service", 0) or 0)
        self.info = {"preset": self.preset, "name": p["name"], "freq": p["freq"], "bw": p["bw"],
                     "service": self.service, "services": [],
                     "callsign": "", "provider": "", "video": "", "audio": "", "warning": self.warning}
        self.st = {"state": "NOSIG"}
        self.video_on = False
        self.message = None
        log("tune P%d %s %.3f MHz %d kHz" % (self.preset, p["name"], p["freq"], p["bw"]))
        # the narrow modes are tuned as 1.7 MHz with the driver overrides set;
        # the standard ones are simply asked for by name
        bw_arg = "1.7" if p["bw"] in (1350, 1700, 2000) else str(p["bw"] / 1000.0)
        args = [ENV("T2RX_TUNER", os.path.join(HERE, "t2rx")), "-f", str(int(round(p["freq"] * 1e6))), "-b", bw_arg,
                "-p", str(p["plp"]), "-a", self.cfg.get("adapter", "0"), "-s", STATUS,
                "-l", self.cfg.get("loss_seconds", "5"), "-q"]
        try:
            self.tuner = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as e:
            log("tuner: %s" % e)
            self.message = ("TUNER ERROR", osd.RED)
            self.restart_at = time.monotonic() + 5
            return
        threading.Thread(target=self._watch_tuner, args=(self.tuner, gen), daemon=True).start()
        self._build_pipeline(self.tuner.stdout.fileno())
        self.draw_idle(force=True)

    def _watch_tuner(self, proc, gen):
        rc = proc.wait()
        GLib.idle_add(self._tuner_exited, rc, gen)

    def _tuner_exited(self, rc, gen):
        if gen != self.gen or self.updating:
            return False
        log("tuner exited (%s) - retuning" % rc)
        self.message = ("SIGNAL LOST", osd.RED) if rc == 2 else None
        self._stop_pipeline()
        self.restart_at = time.monotonic() + 1
        return False

    def _pipeline_desc(self, fd):
        # Size-limited queues (never time-limited or leaky): nothing is dropped, so
        # the sound has no gaps. There is no backlog to trim because the tuner holds
        # the stream back until the first keyframe (tsgate.h).
        q = "max-size-time=0 max-size-buffers=0 max-size-bytes=20000000"
        vsink = ENV("T2RX_VSINK", "kmssink name=vsink")
        # The Pi's hardware decoder handles progressive H.264 up to level 4.0. A
        # broadcast-fed repeater may send 1080i, or 1080p50 at level 4.2, and
        # neither will decode - so a bigger Pi can be told to do it in software.
        want = self.cfg.get("decoder", "auto")
        sw = "avdec_h264 max-threads=4"
        if want == "sw" or (want == "auto" and self.sw_decode):
            dec = ENV("T2RX_DECODER", sw)
            if self.cfg.get("deinterlace", "auto") != "off":
                dec += " ! deinterlace method=linear"
        else:
            dec = ENV("T2RX_DECODER", "v4l2h264dec")
        if self.plane is not None:
            # zero-copy: decoder DMABuf frames straight to the video plane; the OSD
            # is on a separate plane mixed by the display hardware
            vsink = ENV("T2RX_VSINK", "kmssink name=vsink fd=%d plane-id=%d"
                        % (self.plane.fd, self.plane.video_plane))
            vchain = "%s ! identity name=vprobe silent=true ! %s" % (dec, vsink)
        elif self.safe_mode:
            # plain chain that is known to play (no OSD, no shape fix)
            vchain = "%s ! identity name=vprobe silent=true ! %s" % (dec, vsink)
        else:
            par = ""
            if self.cfg.get("scale") == "fix" and self.par != "1/1":
                par = "capssetter caps=video/x-raw,pixel-aspect-ratio=%s ! " % self.par
            # The Pi decoder offers DMABuf (DMA_DRM) frames first, which capssetter and
            # the overlay can't take: ask for ordinary I420 frames in memory.
            vchain = ("%s ! video/x-raw,format=I420 ! %s"
                      "gdkpixbufoverlay name=osd offset-x=24 offset-y=20 ! "
                      "identity name=vprobe silent=true ! %s" % (dec, par, vsink))
        # A pipe, not a live source: timing comes from the stream's own timestamps,
        # so the T2 demodulator's frame-sized bursts can't disturb the sound (1.3's
        # live UDP feed made the audio resync - gaps then catch-up).
        #
        # But the tuner still hands us data in frame-sized bursts, and without
        # pacing those reach the decoder as clumps: measured on a 25 fps stream,
        # frames arrived a median 20 ms apart with 79 gaps over 100 ms in 20 s.
        # tsparse can timestamp its output from the PCR, so the pipeline delivers
        # at the rate the stream was made: 40.0 ms median, nothing over 53 ms.
        prog = (" program-number=%d" % self.service) if self.service else ""
        if self.cfg.get("pacing", "on") != "off":
            pace_us = int(float(self.cfg.get("pace_ms", "200")) * 1000)
            parse = ("tsparse set-timestamps=true smoothing-latency=%d ! "
                     "queue max-size-time=%d max-size-bytes=0 max-size-buffers=0 ! "
                     % (pace_us, 2 * Gst.SECOND))
        else:
            parse = "tsparse ! "
        desc = ("fdsrc fd=%d ! queue max-size-bytes=4000000 max-size-time=0 max-size-buffers=0 ! "
                "%stsdemux name=d latency=400%s "
                "d. ! video/x-h264 ! queue name=vq %s ! h264parse ! %s " % (fd, parse, prog, q, vchain))
        audio = self.cfg.get("audio", "none")
        if audio != "none":
            buf = int(float(self.cfg.get("audio_buffer_ms", "1000")) * 1000)     # microseconds
            # The sound card's clock and the stream's are never quite the same, and
            # GStreamer's default correction is "skew" - dropping or inserting
            # samples now and then, which is audible as wow on music or a tone.
            # "resample" stretches the audio slightly instead, which is not.
            slave = self.cfg.get("audio_slave", "resample")
            asink = ENV("T2RX_ASINK",
                        "alsasink device=%s buffer-time=%d latency-time=%d async=false "
                        "slave-method=%s" % (audio, buf, max(10000, buf // 10), slave))
            vol = float(self.cfg.get("audio_volume", "0.8"))
            # volume < 1 leaves headroom: AAC decoding can overshoot full scale on
            # peaks, which clips (crackles) when converted to 16-bit
            desc += ("d. ! audio/mpeg ! queue name=aq %s ! decodebin ! audioconvert ! volume volume=%.2f ! "
                     "audioconvert ! audioresample ! %s" % (q, vol, asink))
        return desc

    def _build_pipeline(self, fd):
        try:
            self.pipe = Gst.parse_launch(self._pipeline_desc(fd))
        except GLib.Error as e:
            log("pipeline: %s" % e)
            self.fails += 1
            if self.fails >= 3 and not self.safe_mode:
                self.safe_mode = True
                log("player could not be built %d times with the OSD chain - safe mode" % self.fails)
            self.restart_at = time.monotonic() + 3
            return
        self.osd_el = self.pipe.get_by_name("osd")
        if self.osd_el:
            self.osd_el.set_property("alpha", 0.0)
        self.last_frame = time.monotonic()
        probe = self.pipe.get_by_name("vprobe")
        if probe:
            pad = probe.get_static_pad("src")
            pad.add_probe(Gst.PadProbeType.BUFFER, self._first_frame, self.gen)
            pad.add_probe(Gst.PadProbeType.BUFFER, self._frame_seen, None)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus, self.gen)
        # Start PAUSED and let a cushion build before playing: it rides over the T2
        # demodulator's frame-sized bursts, the transmitter's audio-behind-video mux
        # offset (up to ~1.4 s measured on a Portsdown), and small clock differences.
        self.playing = False
        self.pause_t = 0.0
        self.over_t = None
        self.pipe.set_state(Gst.State.PAUSED)

    def _frame_seen(self, pad, info, _):
        """Every decoded frame, so a picture that stops can be noticed."""
        self.last_frame = time.monotonic()
        return Gst.PadProbeReturn.OK

    def _first_frame(self, pad, info, gen):
        caps = pad.get_current_caps()
        if caps:
            s = caps.get_structure(0)
            w, h = s.get_value("width"), s.get_value("height")
            fr = s.get_fraction("framerate")
            fps = (fr[1] / fr[2]) if fr and fr[0] and fr[2] else 0
            self.video_w = w or 800
            GLib.idle_add(self._video_started, gen, "H.264 %dx%d %s" % (w, h, ("%g fps" % fps) if fps else ""))
        return Gst.PadProbeReturn.REMOVE

    def _video_started(self, gen, vinfo):
        if gen != self.gen:
            return False
        self.video_on = True
        self.no_picture = 0
        self.last_frame = time.monotonic()
        if self.sw_decode and self.cfg.get("decoder", "auto") == "auto":
            log("decoding in software")
        self.fails = 0
        self.info["video"] = vinfo + ("  (safe mode, no OSD)" if self.safe_mode else "")
        log("picture: %s" % vinfo)
        self.fb.blank()
        if self.osd_mode in ("auto", "full"):
            self.osd_until = time.monotonic() + float(self.cfg.get("osd_timeout", "15"))
        self.update_osd(force=True)
        return False

    def _on_bus(self, bus, msg, gen):
        """Anything thrown here is swallowed by GLib and leaves the handler deaf
        to whatever message came next, so catch it and say so instead."""
        try:
            self._on_bus_inner(bus, msg, gen)
        except Exception as e:
            if not getattr(self, "_bus_warned", False):
                self._bus_warned = True
                log("bus handler: %s: %s" % (type(e).__name__, e))
                for ln in traceback.format_exc().splitlines()[-4:]:
                    log("   " + ln.strip())

    def _on_bus_inner(self, bus, msg, gen):
        if gen != self.gen:
            return
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log("player error: %s | %s" % (err.message, " ".join((dbg or "").split())[:300]))
            if not self.video_on and self.st.get("state") == "LOCK":
                self.fails += 1
                # the hardware decoder refuses interlaced H.264 and anything above
                # level 4.0; on a Pi with cores to spare, try software instead
                if (self.fails == 2 and not self.sw_decode
                        and self.cfg.get("decoder", "auto") == "auto" and os.cpu_count() > 1):
                    self.sw_decode = True
                    log("hardware decoder will not take this stream - trying software")
                elif self.fails >= 3 and not self.safe_mode:
                    self.safe_mode = True
                    log("player failed %d times with the OSD chain - playing without OSD (safe mode)" % self.fails)
            self._restart_soon(3)
        elif t == Gst.MessageType.EOS:
            self._restart_soon(1)
        elif t == Gst.MessageType.ELEMENT and GstMpegts is not None:
            self._section(msg)
        elif t == Gst.MessageType.STREAM_START or t == Gst.MessageType.ASYNC_DONE:
            self._audio_info()

    def _audio_info(self):
        if not self.pipe or self.info.get("audio"):
            return
        it = self.pipe.iterate_recurse()
        while True:
            ok, el = it.next()
            if ok != Gst.IteratorResult.OK:
                break
            name = el.get_factory().get_name() if el.get_factory() else ""
            if name in ("avdec_aac", "avdec_mp2float", "avdec_mp3", "mpg123audiodec", "faad", "fdkaacdec"):
                pad = el.get_static_pad("src")
                caps = pad.get_current_caps() if pad else None
                rate = caps.get_structure(0).get_value("rate") if caps else None
                codec = "AAC" if "aac" in name or name == "faad" else "MPEG audio"
                self.info["audio"] = "%s %s" % (codec, ("%g kHz" % (rate / 1000)) if rate else "")
                break

    def _section(self, msg):
        """PAT tells us which programmes exist; SDT gives them names. A multiplex
        may carry several - an Australian repeater sends two on one channel - so we
        keep the list and let the user choose."""
        try:
            sec = GstMpegts.message_parse_mpegts_section(msg)
        except (TypeError, AttributeError):
            return
        if not sec:
            return

        if sec.section_type == GstMpegts.SectionType.PAT:
            try:
                progs = [p.program_number for p in (sec.get_pat() or []) if p.program_number]
            except (TypeError, AttributeError):
                return
            known = {n for n, _ in self.services}
            for n in progs:
                if n not in known:
                    self.services.append((n, ""))
            self.services.sort()
            if len(progs) > 1 and not self._logged_mux:
                self._logged_mux = True
                log("multiplex carries %d services" % len(progs))
            self.info["services"] = list(self.services)
            return

        if sec.section_type != GstMpegts.SectionType.SDT:
            return
        sdt = sec.get_sdt()
        changed = False
        for svc in sdt.services or []:
            sid = getattr(svc, "service_id", 0)
            for desc in svc.descriptors or []:
                if desc.tag != GstMpegts.DVBDescriptorType.SERVICE:
                    continue
                ok, stype, name, provider = desc.parse_dvb_service()
                if not ok:
                    continue
                for i, (n, old) in enumerate(self.services):
                    if n == sid and old != (name or ""):
                        self.services[i] = (sid, name or "")
                        changed = True
                if sid not in [n for n, _ in self.services]:
                    self.services.append((sid, name or ""))
                    self.services.sort()
                    changed = True
                # the one we are actually watching names the station on screen
                if (self.service and sid == self.service) or (not self.service and not self.info.get("callsign")):
                    if name != self.info.get("callsign") or provider != self.info.get("provider"):
                        self.info["callsign"] = name or ""
                        self.info["provider"] = provider or ""
                        log("service %d: %s / %s" % (sid, name, provider))
                        changed = True
        if changed:
            self.info["services"] = list(self.services)
            self.update_osd(force=True)

    def _restart_soon(self, secs):
        self._stop_pipeline()
        if self.tuner and self.tuner.poll() is None:
            self.tuner.terminate()
        self.gen += 1
        self.restart_at = time.monotonic() + secs

    def _stop_pipeline(self):
        if self.pipe:
            self.pipe.set_state(Gst.State.NULL)
            self.pipe.get_bus().remove_signal_watch()
            self.pipe = None
            self.osd_el = None
        had_video = self.video_on
        self.video_on = False
        self.last_osd = None
        if self.plane is not None:
            self.plane.hide()
            self.plane.hide_video()      # else its last frame stays on screen, hiding the status page
        if had_video:
            self._idle_layout = None     # the whole page must be drawn again over the old picture

    def stop(self):
        self.gen += 1
        self._stop_pipeline()
        if self.tuner and self.tuner.poll() is None:
            self.tuner.terminate()
            try:
                self.tuner.wait(3)
            except subprocess.TimeoutExpired:
                self.tuner.kill()
        self.tuner = None

    # ------------------------------------------------------------ status / OSD
    def read_status(self):
        was = self.st.get("state")
        try:
            with open(STATUS) as f:
                line = f.read().strip()
            self.st = dict(kv.split("=", 1) for kv in line.split() if "=" in kv)
        except OSError:
            pass
        now = self.st.get("state")
        if now == "LOCK" and was != "LOCK":
            self.locked_at = time.monotonic()        # for the no-picture watchdog
            self.no_picture = 0
        elif now != "LOCK":
            self.locked_at = 0.0

    # ------------------------------------------------------------ updates
    def check_updates(self, repeat=True):
        """Look for a new release, in the background. Returning True keeps a GLib
        timer running, so the boot check must return False - as written, it asked
        GitHub every 60 seconds and used up the hourly request limit by itself."""
        if self.cfg.get("updates", "auto") != "off" and not self.updating:
            def work():
                tag = updater.check(VERSION, log=log)
                if tag:
                    GLib.idle_add(self._found_update, tag)
            threading.Thread(target=work, daemon=True).start()
        return repeat

    def _found_update(self, tag):
        if tag != self.update_tag:
            self.update_tag = tag
            log("update %s available" % tag)
            self.draw_idle(force=True)
        return False

    def _maybe_install(self):
        """Install by itself when nothing is being received (like a TV)."""
        if (self.update_tag and not self.updating and self.cfg.get("updates", "auto") == "auto"
                and not self.video_on and self.st.get("state") != "LOCK"):
            self.install_update()

    def install_update(self):
        if self.updating or not self.update_tag:
            return
        self.updating = True
        tag = self.update_tag
        self.update_stage = "Starting"
        log("installing update %s" % tag)
        self.stop()
        self.st = {"state": "-"}
        self.draw_idle(force=True)

        def work():
            ok = updater.install(tag, log, progress=self._update_stage)
            GLib.idle_add(self._installed, ok)
        threading.Thread(target=work, daemon=True).start()

    def _update_stage(self, text):
        def show():
            self.update_stage = text
            self.draw_idle(force=True)
            return False
        GLib.idle_add(show)

    def _installed(self, ok):
        self.updating = False
        if ok:
            log("restarting into the new version")
            self.updating_exit = True
            self.quit()                      # systemd Restart=always brings it back
        else:
            self.update_tag = None           # don't loop on a failure
            self.restart_at = time.monotonic() + 1
        return False

    def _stall_check(self):
        """A frozen picture looks exactly like a good one from the outside: the
        signal is locked, the stream is arriving, and the player reports no error
        - the decoder has simply stopped delivering frames. Restart the player."""
        if self.updating or self.tune is not None:
            return
        if not self.video_on:
            self._no_picture_check()
            return
        limit = float(self.cfg.get("stall_secs", "6") or 0)
        if limit <= 0 or not self.last_frame:
            return
        if time.monotonic() - self.last_frame < limit:
            return
        self.stalls += 1
        log("picture stopped %.0f s ago with the signal still locked - restarting the player%s"
            % (time.monotonic() - self.last_frame,
               " (%d times now)" % self.stalls if self.stalls > 1 else ""))
        # if it keeps happening, the decoder cannot manage this stream: on a
        # machine with the cores for it, try software instead
        if (self.stalls >= 3 and not self.sw_decode
                and self.cfg.get("decoder", "auto") == "auto" and os.cpu_count() > 2):
            self.sw_decode = True
            log("repeated stalls - trying software decoding")
        self.last_frame = 0.0
        self._restart_soon(0.5)

    def _picture_note(self):
        """The tuner reads the H.264 parameters out of the stream. A Pi's hardware
        decoder takes up to level 4.1, so 1080p50 (level 4.2) produces no picture
        at all - indistinguishable, from the outside, from a weak signal. Say so."""
        vid = self.st.get("vid")
        if not vid:
            return None
        try:
            level = float(self.st.get("level", "0"))
        except ValueError:
            level = 0.0
        if level > 4.1 and self.cfg.get("decoder", "auto") != "sw":
            return "%s L%.1f - too much for this Pi" % (vid, level)
        return "%s L%.1f" % (vid, level)

    def _no_picture_check(self):
        """Locked, but nothing has ever been decoded. Reported from Australia: the
        receiver sat on "waiting for picture" indefinitely after being restarted
        mid-transmission, and only recovered when the transmitting station stopped
        and started again - which made the repeater send fresh tables. Restarting
        the player achieves the same thing without troubling anybody."""
        limit = float(self.cfg.get("no_picture_secs", "20") or 0)
        if limit <= 0 or self.st.get("state") != "LOCK" or not self.locked_at:
            return
        if time.monotonic() - self.locked_at < limit:
            return
        self.no_picture += 1
        self.locked_at = time.monotonic()
        note = self._picture_note()
        if note and self.no_picture == 1:
            log("no picture: the stream is %s" % note)
            if "too much" in note:
                self.warning = "this Pi decodes H.264 to level 4.1; try decoder = sw on a Pi 4 or 5"
        # a multiplex often carries a service with no video in it; if restarting
        # twice has not helped, try the next one before giving up on this pass
        if self.no_picture >= 3 and len(self.services) > 1:
            log("locked for %.0f s with no picture - trying the next service" % limit)
            self.no_picture = 0
            self.next_service(1)
            return
        log("locked for %.0f s with no picture - restarting the player" % limit)
        self._restart_soon(0.5)

    def _buffered(self):
        """Seconds of stream waiting to be played (audio if there is sound, else video)."""
        if not self.pipe:
            return 0.0
        for name in ("aq", "vq"):
            q = self.pipe.get_by_name(name)
            if q is not None:
                return q.get_property("current-level-time") / 1e9
        return 0.0

    def _pace(self):
        if not self.pipe:
            return
        lvl = self._buffered()
        now = time.monotonic()
        if not self.playing:
            start = float(self.cfg.get("start_buffer_ms", "1500")) / 1000.0
            if lvl > 0 and not self.pause_t:
                self.pause_t = now               # stream has started to arrive (after the keyframe)
            if lvl >= start or (self.pause_t and now - self.pause_t > 5):
                log("playing with %.1f s buffered" % lvl)
                self.playing = True
                self.pipe.set_state(Gst.State.PLAYING)
            return
        # a fast transmitter slowly builds the cushion (and the delay); if it gets
        # beyond max_buffer_ms for 10 s, start again with a normal cushion
        cap = float(self.cfg.get("max_buffer_ms", "8000")) / 1000.0
        if lvl > cap:
            if self.over_t is None:
                self.over_t = now
            elif now - self.over_t > 10:
                log("%.1f s buffered - transmitter running fast; restarting the player" % lvl)
                self.over_t = None
                self._restart_soon(0.5)
        else:
            self.over_t = None

    def tick(self):
        if self.updating:                      # nothing but the update page while it installs
            self.draw_idle()
            return True
        if self.restart_at and time.monotonic() >= self.restart_at:
            self.restart_at = None
            self.start()
            return True
        self.read_status()
        self._stall_check()
        self._tune_idle_check()
        self._pace()
        self._maybe_install()
        if self.video_on:
            if not self.info.get("audio"):
                self._audio_info()
            self.update_osd()
        else:
            self.draw_idle()
        return True

    def _tune_box(self):
        h = self.fb.h / 1080.0
        panel = osd.render_tune(self.fb.w, self.tune, self.presets)
        return panel, (int(self.fb.w - panel.width - 60 * h), int(self.fb.h - panel.height - 110 * h))

    def draw_idle(self, force=False, part=None):
        """part="presets" or "tune" redraws only that area: a full 1080p page in
        Python costs a Pi Zero a good fraction of a second, and redrawing all of
        it for every keypress made the remote feel laggy."""
        now = time.monotonic()
        if not force and part is None and now - self.last_idle < 1.0:
            return
        if part and getattr(self, "_idle_layout", None) is not None and not self.updating:
            if part == "presets":
                img, box = osd.render_presets(self.fb.w, self.fb.h, self.presets, self.preset)
                self.fb.show(img, box[:2])
                return
            if part == "tune" and self.tune is not None:
                panel, at = self._tune_box()
                bg = Image.new("RGB", panel.size, osd.BG_BASE)
                bg.paste(panel, (0, 0), panel)
                self.fb.show(bg, at)
                return
        self.last_idle = now
        msg = self.message
        if not msg and self.st.get("state") == "LOCK" and not self.video_on:
            note = self._picture_note()
            if note and "too much" in note:
                msg = (note, osd.AMBER)
            else:
                msg = ("LOCKED - waiting for picture", osd.GREEN)
        info = dict(self.info)
        # self.info is only refreshed when the tuner starts, so between a preset
        # change and the retune it still describes the old channel - which made
        # the highlight jump back. The selection always comes from self.preset.
        cur = self.cur()
        info.update(preset=self.preset, name=cur["name"], freq=cur["freq"], bw=cur["bw"])
        if self.tune is not None:
            info.pop("update", None)
        if self.updating:
            # the page becomes the update page until the new version starts
            msg = ("Installing update %s" % self.update_tag, osd.INFO)
            info = {"preset": self.preset, "name": "", "freq": self.cur()["freq"],
                    "bw": self.cur()["bw"],
                    "update": (self.update_stage or "Please wait") + " - do not switch off"}
        elif self.update_tag:
            info["update"] = "Update %s available%s" % (
                self.update_tag, "" if self.cfg.get("updates", "auto") == "auto" else " - press OK to install")
        img = osd.render_idle(self.fb.w, self.fb.h, self.st, info, self.presets, msg, VERSION)
        if self.tune is not None:
            panel, at = self._tune_box()
            img.paste(panel, at, panel)
        # Only the status card changes second to second: redraw just that
        # unless the layout (preset, message, presets list) changed.
        layout = (self.preset, msg, str(self.presets), self.info.get("warning"),
                  self.fb.w, self.fb.h, self.update_stage, self.updating, self.tune is not None)
        if force or layout != getattr(self, "_idle_layout", None):
            self._idle_layout = layout
            self.fb.show(img)
        else:
            box = osd.idle_card_box(self.fb.w, self.fb.h)
            self.fb.show(img.crop(box), box[:2])

    def _osd_worker(self):
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)   # this thread only
        except (OSError, AttributeError):
            pass
        while True:
            self._osd_event.wait()
            self._osd_event.clear()
            job, self._osd_job = self._osd_job, None
            if job is None or self.plane is None:
                continue
            mode, st, info, tune = job
            s = self.plane.w / 800.0
            if tune is not None:
                img = osd.render_tune(self.plane.w, tune, self.presets)
            elif mode == "list":
                img = osd.render_list(self.plane.w, self.presets, self.preset)
            else:
                img = osd.render_osd(self.plane.w, st, info, mode)
            if not self.video_on or self.last_osd == "off":
                continue                              # hidden while we were drawing
            if self.plane.show(img, 24 * s, 20 * s) != 0:
                log("OSD plane: SetPlane failed")

    def _osd_key(self, mode):
        """What the panel shows, at display resolution: redraw only when it changes."""
        st, info = self.st, self.info
        if mode == "mini":
            try:
                bar = int(float(st.get("cnr", "-999")) / 35.0 * 22)
            except ValueError:
                bar = 0
            return (mode, st.get("state"), bar, info.get("callsign"), info.get("preset"))
        return (mode, tuple(sorted(st.items())), tuple(sorted((k, str(v)) for k, v in info.items())))

    def update_osd(self, force=False):
        if self.plane is None and not self.osd_el:
            return
        if self.plane is not None and not self.video_on:
            return                                   # the plane OSD only goes over a picture
        mode = self.osd_mode
        now = time.monotonic()
        if mode == "auto":
            mode = "full" if now < self.osd_until else "mini"
        elif mode == "full" and self.osd_until and now >= self.osd_until and self.cfg.get("osd") == "auto":
            mode = "mini"
        if mode == "off" and self.tune is None:
            if self.last_osd != "off":
                if self.plane is not None:
                    self.plane.hide()
                else:
                    self.osd_el.set_property("alpha", 0.0)
                self.last_osd = "off"
            return
        key = ("list", str(self.preset)) if mode == "list" else (
            self._osd_key(mode) if self.tune is None else ("tune", str(self.tune)))
        interval = 0.2 if mode == "list" else float(self.cfg.get("osd_interval", "2"))
        if not force and (key == self.last_osd or now - getattr(self, "_osd_t", 0) < interval):
            return                                   # unchanged, or redrawn too recently
        self.last_osd = key
        self._osd_t = now
        if time.monotonic() < self.list_until and self.tune is None:
            mode = "list"
        if self.plane is not None:
            # drawn on a low-priority thread so a redraw can never hold up the audio
            self._osd_job = (mode, dict(self.st), dict(self.info), self.tune and dict(self.tune))
            self._osd_event.set()
            return
        img = (osd.render_list(self.video_w, self.presets, self.preset) if mode == "list"
               else osd.render_osd(self.video_w, self.st, self.info, mode))
        data = GLib.Bytes.new(img.tobytes())
        pb = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, True, 8,
                                             img.width, img.height, img.width * 4)
        s = max(0.5, self.video_w / 800.0)
        self.osd_el.set_property("offset-x", int(24 * s))
        self.osd_el.set_property("offset-y", int(20 * s))
        self.osd_el.set_property("pixbuf", pb)
        self.osd_el.set_property("alpha", 1.0)

    # ------------------------------------------------------------ tuning
    # Works with arrows and OK alone (many TV and AV remotes have no keypad).
    # Three stages: frequency (a digit at a time), bandwidth, then where to store.
    def _tune_shown(self):
        d = "".join(self.tune["digits"])
        return d[:3] + "." + d[3:]

    def _tune_idle_check(self):
        """Close the panel if nothing has been pressed for a while: while it is
        open it takes every key, so it must never be possible to get stuck in it."""
        if self.tune is not None and time.monotonic() - self.tune_t > 90:
            log("tune panel closed (no keys for 90 s)")
            self.close_tune()

    def open_tune(self):
        # the panel must show even if the OSD was hidden with BACK
        if self.osd_mode == "off":
            self.osd_mode = self.cfg.get("osd", "auto")
        self.osd_until = time.monotonic() + 3600
        p = self.cur()
        f = "%06d" % int(round(p["freq"] * 1000))
        self.tune = {"digits": list(f), "pos": 0, "stage": "freq",
                     "bw": p["bw"] if p["bw"] in BW_CHOICES else 1700, "slot": 0}
        self.tune_t = time.monotonic()
        self._tune_refresh(part=None)          # full page: the panel appears
        log("tune panel open")

    def close_tune(self):
        self.tune = None
        self.osd_until = time.monotonic() + float(self.cfg.get("osd_timeout", "15"))
        self._redraw()

    def _tune_refresh(self, part="tune"):
        t = self.tune
        t["digits"] = list("".join(t["digits"]))
        t["shown"] = self._tune_shown()
        t["freq"] = float("".join(t["digits"])) / 1000.0
        self._redraw(part)

    def _tune_key(self, name):
        t = self.tune
        self.tune_t = time.monotonic()
        up = name in ("up", "ch_up")
        down = name in ("down", "ch_down")
        ok = name in ("select", "info", "play")
        if name == "back":
            if t["stage"] == "freq" and t["pos"] > 0:
                t["pos"] -= 1
            elif t["stage"] == "bw":
                t["stage"] = "freq"
            elif t["stage"] == "save":
                self.close_tune()
                return
            else:
                self.close_tune()
                return
            self._tune_refresh()
            return

        if t["stage"] == "freq":
            if name.isdigit():                       # keypad, where there is one
                t["digits"][t["pos"]] = name
                t["pos"] = min(t["pos"] + 1, 5)
            elif up or down:
                d = int(t["digits"][t["pos"]])
                t["digits"][t["pos"]] = str((d + (1 if up else -1)) % 10)
            elif name == "right":
                t["pos"] = min(t["pos"] + 1, 5)
            elif name == "left":
                t["pos"] = max(t["pos"] - 1, 0)
            elif ok:
                if t["pos"] < 5:
                    t["pos"] += 1                    # OK steps through the digits
                else:
                    t["stage"] = "bw"
            self._tune_refresh()
            return

        if t["stage"] == "bw":
            if up or down or name in ("left", "right"):
                i = BW_CHOICES.index(t["bw"])
                fwd = up or name == "right"
                t["bw"] = BW_CHOICES[(i + (1 if fwd else -1)) % len(BW_CHOICES)]
            elif name.isdigit() and 1 <= int(name) <= len(BW_CHOICES):
                t["bw"] = BW_CHOICES[int(name) - 1]
            elif ok:
                t["stage"] = "save"
                t["slot"] = 0
                self.tune_to(t["freq"], t["bw"])
            self._tune_refresh()
            return

        # stage "save": 0 = don't store, 1-9 = that preset. Up and Down choose the
        # slot; Left and Right switch between storing and deleting it, so a preset
        # can be cleared from the armchair as well as from the web page.
        if up:
            t["slot"] = (t["slot"] + 1) % 10
        elif down:
            t["slot"] = (t["slot"] - 1) % 10
        elif name in ("left", "right"):
            t["action"] = "delete" if t.get("action", "save") == "save" else "save"
        elif name.isdigit():
            t["slot"] = int(name)
        elif ok:
            if t["slot"]:
                if t.get("action", "save") == "delete":
                    self.delete_preset(t["slot"])
                else:
                    self.save_preset(t["slot"], t["freq"], t["bw"])
            self.close_tune()
            return
        self._tune_refresh()

    def tune_to(self, freq, bw, name=None):
        """Tune somewhere not in the presets (shown as preset 0, 'Manual')."""
        if int(bw) not in BW_TABLE:
            log("tune: %s kHz is not one of %s - ignoring"
                % (bw, ", ".join(str(b) for b in BW_CHOICES)))
            return
        log("tuning %.3f MHz %d kHz" % (float(freq), int(bw)))
        self.presets = [(k, p) for k, p in self.presets if k != 0]
        self.presets.insert(0, (0, {"name": name or "Manual", "freq": float(freq),
                                    "bw": int(bw), "plp": 0}))
        self.preset = 0
        self.start()

    def save_preset(self, slot, freq, bw, name=None):
        label = {1350: "1.35", 1700: "1.7", 2000: "2.0"}.get(int(bw), "%g" % (int(bw) / 1000.0))
        ps = [(k, p) for k, p in self.presets if k not in (slot, 0)]
        ps.append((slot, {"name": name or "%.3f %s" % (float(freq), label),
                          "freq": float(freq), "bw": int(bw), "plp": 0}))
        ps.sort()
        if not write_presets(ps):
            return False
        log("preset %d saved: %.3f MHz %d kHz" % (slot, float(freq), int(bw)))
        self.presets = ps
        self.preset = slot
        self._save_state()
        self.start()
        return True

    def delete_preset(self, slot):
        ps = [(k, p) for k, p in self.presets if k != slot]
        if len(ps) == len(self.presets) or not [k for k, _ in ps if k != 0]:
            return False
        if not write_presets([(k, p) for k, p in ps if k != 0]):
            return False
        log("preset %d deleted" % slot)
        self.presets = ps
        if self.preset == slot:
            self.preset = ps[0][0]
            self.start()
        return True

    def select_service(self, sid):
        """Watch a particular programme in the multiplex, and remember it."""
        if sid == self.service:
            return
        self.service = int(sid)
        p = self.cur()
        p["service"] = self.service
        if self.preset:                       # remember it for next time
            write_presets([(k, v) for k, v in self.presets if k != 0])
        names = dict(self.services)
        log("watching service %s%s" % (self.service or "(first in the multiplex)",
                                       (" - %s" % names[self.service]) if names.get(self.service) else ""))
        self.info["callsign"] = names.get(self.service, "")
        self.start()

    def next_service(self, step=1):
        ids = [n for n, _ in self.services]
        if len(ids) < 2:
            return
        here = ids.index(self.service) if self.service in ids else 0
        self.select_service(ids[(here + step) % len(ids)])

    def _redraw(self, part=None):
        """Draw the tune panel wherever it will be seen: over the picture if there
        is one, and on the status page as well, so it cannot end up hidden."""
        self.last_osd = None
        if self.video_on:
            self.update_osd(force=True)
        self.draw_idle(force=(part is None), part=part)

    # ------------------------------------------------------------ control
    def key(self, name):
        log("key %s" % name)
        if self.tune is not None:
            self._tune_key(name)
            return False
        # shortcuts for remotes that have them (presets are 1-9, so 0 is free);
        # "Tune" also sits after the last preset, for arrows-only remotes
        if name in ("0", "red", "menu"):
            self.open_tune()
            return False
        # "Tune" sits after the last preset, so arrows alone reach it
        keys = [k for k, _ in self.presets] + ["tune"]
        # Up/Left move up the on-screen list (to a lower preset number), Down/Right
        # move down it. CH+/CH- follow the numbers, as on a TV.
        if name in ("left", "right") and len(self.services) > 1:
            self.next_service(1 if name == "right" else -1)    # this multiplex has several
            return False
        here = keys.index(self.preset) if self.preset in keys else 0
        if name in ("down", "right", "ch_up", "up", "left", "ch_down"):
            # the full preset list is only on the status page, so put a compact
            # one over the picture while you are moving through it
            self.list_until = time.monotonic() + 8
        if name in ("down", "right", "ch_up"):
            self.select(keys[(here + 1) % len(keys)])
        elif name in ("up", "left", "ch_down"):
            self.select(keys[(here - 1) % len(keys)])
        elif name.isdigit():
            n = int(name)
            if n in keys and n != 0:
                self.select(n)
        elif name in ("select", "info", "play"):
            if self.update_tag and not self.video_on and not self.updating:
                self.install_update()
            else:
                self.cycle_osd()
        elif name == "back":
            # BACK brings up the preset list, the way it goes back to the channel
            # list on a television - and Tune... sits at the end of it. Pressing it
            # again, with the list already up, hides the display.
            if time.monotonic() < self.list_until:
                self.list_until = 0
                self.osd_mode = "off"
            else:
                self.list_until = time.monotonic() + 8
            self.update_osd(force=True)
        return False

    def cycle_osd(self):
        order = ["full", "mini", "off"]
        curm = self.osd_mode if self.osd_mode in order else "mini"
        if self.osd_mode == "auto" and time.monotonic() < self.osd_until:
            curm = "full"
        self.osd_mode = order[(order.index(curm) + 1) % 3]
        self.osd_until = time.monotonic() + float(self.cfg.get("osd_timeout", "15")) if self.osd_mode == "full" else 0
        if self.osd_mode == "full" and self.cfg.get("osd") == "auto":
            self.osd_mode = "auto"
        self.update_osd(force=True)

    def select(self, n):
        if n == "tune":
            self.list_until = 0
            self.open_tune()
            return
        if n == self.preset and self.tuner:
            return
        self.preset = n
        self._save_state()
        self.osd_until = time.monotonic() + float(self.cfg.get("osd_timeout", "15"))
        if self.osd_mode == "off":
            self.osd_mode = self.cfg.get("osd", "auto")
        # Retune shortly, not instantly: stepping through the list with the arrows
        # would otherwise stop and restart the tuner and player for every press.
        self.stop()
        self.draw_idle(part="presets")         # only the list changes: cheap on a Zero
        self.restart_at = time.monotonic() + 0.4

    # ------------------------------------------------------------ web
    def web_status(self):
        return {"version": VERSION, "preset": self.preset, "tuner": self.st, "info": self.info,
                "video": self.video_on, "osd": self.osd_mode, "update": self.update_tag,
                "presets": [dict(key=k, **p) for k, p in self.presets]}

    def web_cmd(self, cmd, **q):
        def later(fn, *a):
            GLib.idle_add(lambda: (fn(*a), False)[1])
        def close_then(fn, *a):
            def run():
                if self.tune is not None:
                    self.close_tune()
                fn(*a)
                return False
            GLib.idle_add(run)
        if cmd == "preset":
            close_then(self.select, int(q["slot"]))
        elif cmd in ("next", "prev"):
            close_then(self.key, "up" if cmd == "prev" else "down")
        elif cmd in ("osd", "back"):
            close_then(self.key, "select" if cmd == "osd" else "back")
        elif cmd == "tune":
            close_then(self.tune_to, float(q["freq"]), int(q.get("bw", 1700)), q.get("name"))
        elif cmd == "save":
            slot = int(q["slot"])
            freq = float(q.get("freq") or self.cur()["freq"])
            bw = int(q.get("bw") or self.cur()["bw"])
            later(self.save_preset, slot, freq, bw, q.get("name") or None)
        elif cmd == "service":
            close_then(self.select_service, int(q.get("n", 0)))
        elif cmd == "delete":
            later(self.delete_preset, int(q["slot"]))
        elif cmd == "update":
            later(self.install_update)
        elif cmd == "reload":
            later(self._reload_presets)
        else:
            return {"error": "unknown command"}
        return {"ok": cmd}

    def _reload_presets(self):
        self.presets = read_presets()
        self.start()

    def _socket(self):
        try:
            os.unlink(SOCK)
        except OSError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(SOCK)
        os.chmod(SOCK, 0o666)

        def on_msg(src, cond):
            data, addr = s.recvfrom(256)
            cmd = data.decode(errors="ignore").split()
            reply = "ok"
            if not cmd:
                pass
            elif cmd[0] == "preset" and len(cmd) > 1 and cmd[1].isdigit():
                self.key(cmd[1])
            elif cmd[0] in ("next", "prev", "osd", "back"):
                self.key({"next": "up", "prev": "down", "osd": "select", "back": "back"}[cmd[0]])
            elif cmd[0] == "status":
                reply = json.dumps({"preset": self.preset, "info": self.info, "tuner": self.st,
                                    "video": self.video_on, "osd": self.osd_mode})
            elif cmd[0] == "update":
                if self.update_tag:
                    self.install_update()
                    reply = "installing %s" % self.update_tag
                else:
                    tag = updater.check(VERSION, log=log)
                    if tag:
                        self.update_tag = tag
                    reply = ("update %s available" % tag) if tag else "up to date (%s)" % VERSION
            elif cmd[0] == "tune" and len(cmd) > 2:
                self.tune_to(float(cmd[1]), int(cmd[2]))
            elif cmd[0] == "save" and len(cmd) > 1:
                c = self.cur()
                reply = "saved" if self.save_preset(int(cmd[1]), c["freq"], c["bw"],
                                                    " ".join(cmd[2:]) or None) else "save failed"
            elif cmd[0] == "delete" and len(cmd) > 1:
                reply = "deleted" if self.delete_preset(int(cmd[1])) else "delete failed"
            elif cmd[0] == "reload":
                self.presets = read_presets()
                self.start()
            else:
                reply = "unknown command"
            if addr:
                try:
                    s.sendto(reply.encode(), addr)
                except OSError:
                    pass
            return True
        GLib.io_add_watch(s, GLib.IO_IN, on_msg)
        self.sock = s

    def run(self):
        try:
            if os.path.getsize(LOG) > 1000000:
                os.replace(LOG, LOG + ".1")
        except OSError:
            pass
        log("%s starting, preset %d, OSD %s" % (VERSION, self.preset,
            ("on display plane %d (video plane %d)" % (self.plane.osd_plane, self.plane.video_plane))
            if self.plane is not None else "blended (display PAR %s)" % self.par))
        fbmod.console(False)
        try:
            subprocess.run(["chvt", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._socket()
        if self.cfg.get("web", "on") != "off":
            webmod.serve(self, self.cfg.get("web_port", "8080"), log)
        if self.cfg.get("cec", "yes") == "yes" and os.path.exists("/dev/cec0"):
            try:
                import cec
                self.cec = cec.Cec(lambda k: GLib.idle_add(self.key, k), self.cfg.get("cec_name", "T2 Receiver"), log=log)
                self.cec.start(self.cfg.get("cec_active_source", "yes") == "yes")
            except Exception as e:  # CEC is optional
                log("cec: %s" % e)
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.quit)
        self.start()
        GLib.timeout_add(500, self.tick)
        GLib.timeout_add_seconds(60, self.check_updates, False)   # once, shortly after boot
        GLib.timeout_add_seconds(3600, self.check_updates)        # and hourly after that
        self.loop.run()

    def quit(self):
        log("stopping")
        self.stop()
        try:
            for name, val in (("nb_fs_hz", 0), ("nb_if_bw", -1)):
                with open("%s/%s" % (NB, name), "w") as f:
                    f.write(str(val))
        except OSError:
            pass
        if self.plane is not None:
            self.plane.close()
        if not self.updating_exit:      # leave the update message up across the restart
            self.fb.blank()
            fbmod.console(True)
        self.loop.quit()
        return False


if __name__ == "__main__":
    if os.geteuid() != 0 and not ENV("T2RX_ROOT"):
        sys.exit("t2rxd must run as root (sudo)")
    Receiver().run()
