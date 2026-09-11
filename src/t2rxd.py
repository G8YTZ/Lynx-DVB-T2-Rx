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
import time

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
try:
    import osdplane  # noqa: E402
except (OSError, ImportError):          # no libdrm: fall back to blending
    osdplane = None

VERSION = "t2rx 1.7"
# Test hooks: T2RX_TUNER (tuner program), T2RX_DECODER, T2RX_VSINK, T2RX_ASINK, T2RX_ROOT
ENV = os.environ.get
CONF = "/etc/t2rx/t2rx.conf"
PRESETS = "/etc/t2rx/presets.conf"
STATE = "/var/lib/t2rx/state.json"
STATUS = "/run/t2rx.status"
SOCK = "/run/t2rx.sock"
LOG = "/var/log/t2rx.log"
NB = "/sys/module/cxd2880/parameters"
# driver override per bandwidth: (nb_fs_hz, nb_if_bw); 1700 = stock driver
BW_TABLE = {1700: (0, -1), 2000: (2285714, 0), 1350: (1542857, 3)}


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_conf():
    c = configparser.ConfigParser()
    c.read_dict({"receiver": {
        "audio": "hdmi:CARD=vc4hdmi,DEV=0", "scale": "kms", "osd": "auto", "osd_timeout": "15",
        "osd_plane": "auto", "audio_buffer_ms": "200", "audio_volume": "0.8",
        "osd_interval": "2", "start_buffer_ms": "1500", "max_buffer_ms": "8000",
        "updates": "auto",
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
                              "bw": bw, "plp": int(p.get("plp", "0"))}))
        except ValueError:
            continue
    out.sort()
    if not out:
        out = [(1, {"name": "70cm T2 1.7", "freq": 436.0, "bw": 1700, "plp": 0})]
    return out


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
        self.update_tag = None       # newer release found, shown on the status page
        self.updating = False
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
    def set_driver(self, bw):
        fs, ifb = BW_TABLE.get(bw, (0, -1))
        self.warning = None
        if not os.path.exists(NB + "/nb_fs_hz"):
            if bw != 1700:
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
        self.set_driver(p["bw"])
        self.info = {"preset": self.preset, "name": p["name"], "freq": p["freq"], "bw": p["bw"],
                     "callsign": "", "provider": "", "video": "", "audio": "", "warning": self.warning}
        self.st = {"state": "NOSIG"}
        self.video_on = False
        self.message = None
        log("tune P%d %s %.3f MHz %d kHz" % (self.preset, p["name"], p["freq"], p["bw"]))
        args = [ENV("T2RX_TUNER", os.path.join(HERE, "t2rx")), "-f", str(int(round(p["freq"] * 1e6))), "-b", "1.7",
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
        if gen != self.gen:
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
        desc = ("fdsrc fd=%d ! queue max-size-bytes=4000000 max-size-time=0 max-size-buffers=0 ! "
                "tsparse ! tsdemux name=d latency=400 "
                "d. ! video/x-h264 ! queue name=vq %s ! h264parse ! %s " % (fd, q, vchain))
        audio = self.cfg.get("audio", "none")
        if audio != "none":
            buf = int(float(self.cfg.get("audio_buffer_ms", "1000")) * 1000)     # microseconds
            asink = ENV("T2RX_ASINK", "alsasink device=%s buffer-time=%d latency-time=%d async=false"
                        % (audio, buf, max(10000, buf // 10)))
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
        probe = self.pipe.get_by_name("vprobe")
        if probe:
            probe.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._first_frame, self.gen)
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
        self.fails = 0
        self.info["video"] = vinfo + ("  (safe mode, no OSD)" if self.safe_mode else "")
        log("picture: %s" % vinfo)
        self.fb.blank()
        if self.osd_mode in ("auto", "full"):
            self.osd_until = time.monotonic() + float(self.cfg.get("osd_timeout", "15"))
        self.update_osd(force=True)
        return False

    def _on_bus(self, bus, msg, gen):
        if gen != self.gen:
            return
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log("player error: %s | %s" % (err.message, " ".join((dbg or "").split())[:300]))
            if not self.video_on and self.st.get("state") == "LOCK":
                self.fails += 1
                if self.fails >= 3 and not self.safe_mode:
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
        try:
            sec = GstMpegts.message_parse_mpegts_section(msg)
        except (TypeError, AttributeError):
            return
        if not sec or sec.section_type != GstMpegts.SectionType.SDT:
            return
        sdt = sec.get_sdt()
        for svc in sdt.services or []:
            for desc in svc.descriptors or []:
                if desc.tag == GstMpegts.DVBDescriptorType.SERVICE:
                    ok, stype, name, provider = desc.parse_dvb_service()
                    if ok:
                        if name != self.info.get("callsign") or provider != self.info.get("provider"):
                            self.info["callsign"] = name or ""
                            self.info["provider"] = provider or ""
                            log("service: %s / %s" % (name, provider))
                            self.update_osd(force=True)
                        return

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
        self.video_on = False
        self.last_osd = None
        if self.plane is not None:
            self.plane.hide()

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
        try:
            with open(STATUS) as f:
                line = f.read().strip()
            self.st = dict(kv.split("=", 1) for kv in line.split() if "=" in kv)
        except OSError:
            pass

    # ------------------------------------------------------------ updates
    def check_updates(self):
        """Look for a new release, in the background: shortly after boot and daily."""
        if self.cfg.get("updates", "auto") == "off" or self.updating:
            return True

        def work():
            tag = updater.check(VERSION)
            if tag:
                GLib.idle_add(self._found_update, tag)
        threading.Thread(target=work, daemon=True).start()
        return True

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
        log("installing update %s" % tag)
        self.stop()
        self.draw_idle(force=True)

        def work():
            ok = updater.install(tag, log)
            GLib.idle_add(self._installed, ok)
        threading.Thread(target=work, daemon=True).start()

    def _installed(self, ok):
        self.updating = False
        if ok:
            log("restarting into the new version")
            self.quit()                      # systemd Restart=always brings it back
        else:
            self.update_tag = None           # don't loop on a failure
            self.restart_at = time.monotonic() + 1
        return False

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
        if self.restart_at and time.monotonic() >= self.restart_at:
            self.restart_at = None
            self.start()
            return True
        self.read_status()
        self._pace()
        self._maybe_install()
        if self.video_on:
            if not self.info.get("audio"):
                self._audio_info()
            self.update_osd()
        else:
            self.draw_idle()
        return True

    def draw_idle(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_idle < 1.0:
            return
        self.last_idle = now
        msg = self.message
        if not msg and self.st.get("state") == "LOCK" and not self.video_on:
            msg = ("LOCKED - waiting for picture", osd.GREEN)
        info = dict(self.info)
        if self.updating:
            info["update"] = "Installing update %s - please wait" % self.update_tag
        elif self.update_tag:
            info["update"] = "Update %s available%s" % (
                self.update_tag, "" if self.cfg.get("updates", "auto") == "auto" else " - press OK to install")
        img = osd.render_idle(self.fb.w, self.fb.h, self.st, info, self.presets, msg, VERSION)
        # Only the status card changes second to second: redraw just that
        # unless the layout (preset, message, presets list) changed.
        layout = (self.preset, msg, str(self.presets), self.info.get("warning"), self.fb.w, self.fb.h)
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
            mode, st, info = job
            s = self.plane.w / 800.0
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
        if mode == "off":
            if self.last_osd != "off":
                if self.plane is not None:
                    self.plane.hide()
                else:
                    self.osd_el.set_property("alpha", 0.0)
                self.last_osd = "off"
            return
        key = self._osd_key(mode)
        interval = float(self.cfg.get("osd_interval", "2"))
        if not force and (key == self.last_osd or now - getattr(self, "_osd_t", 0) < interval):
            return                                   # unchanged, or redrawn too recently
        self.last_osd = key
        self._osd_t = now
        if self.plane is not None:
            # drawn on a low-priority thread so a redraw can never hold up the audio
            self._osd_job = (mode, dict(self.st), dict(self.info))
            self._osd_event.set()
            return
        img = osd.render_osd(self.video_w, self.st, self.info, mode)
        data = GLib.Bytes.new(img.tobytes())
        pb = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, True, 8,
                                             img.width, img.height, img.width * 4)
        s = max(0.5, self.video_w / 800.0)
        self.osd_el.set_property("offset-x", int(24 * s))
        self.osd_el.set_property("offset-y", int(20 * s))
        self.osd_el.set_property("pixbuf", pb)
        self.osd_el.set_property("alpha", 1.0)

    # ------------------------------------------------------------ control
    def key(self, name):
        log("key %s" % name)
        keys = [k for k, _ in self.presets]
        if name in ("up", "ch_up", "right"):
            self.select(keys[(keys.index(self.preset) + 1) % len(keys)])
        elif name in ("down", "ch_down", "left"):
            self.select(keys[(keys.index(self.preset) - 1) % len(keys)])
        elif name.isdigit():
            n = int(name)
            if n in keys:
                self.select(n)
        elif name in ("select", "info"):
            if self.update_tag and not self.video_on and not self.updating:
                self.install_update()
            else:
                self.cycle_osd()
        elif name == "back":
            self.osd_mode = "off"
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
        if n == self.preset and self.tuner:
            return
        self.preset = n
        self._save_state()
        self.osd_until = time.monotonic() + float(self.cfg.get("osd_timeout", "15"))
        if self.osd_mode == "off":
            self.osd_mode = self.cfg.get("osd", "auto")
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
                    tag = updater.check(VERSION)
                    if tag:
                        self.update_tag = tag
                    reply = ("update %s available" % tag) if tag else "up to date (%s)" % VERSION
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
        GLib.timeout_add_seconds(60, self.check_updates)          # shortly after boot
        GLib.timeout_add_seconds(24 * 3600, self.check_updates)   # and daily
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
        self.fb.blank()
        fbmod.console(True)
        self.loop.quit()
        return False


if __name__ == "__main__":
    if os.geteuid() != 0 and not ENV("T2RX_ROOT"):
        sys.exit("t2rxd must run as root (sudo)")
    Receiver().run()
