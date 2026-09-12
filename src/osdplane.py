"""osdplane.py - show the OSD on its own hardware display plane (DRM/KMS).

The video goes on one overlay plane (kmssink, sharing our DRM fd so it runs
under the same DRM master) and the OSD on a second overlay plane above it.
The display hardware (VC4 HVS) mixes them, so the OSD costs no CPU per video
frame - only when the panel is redrawn. Pure ctypes over libdrm.so.2.
Lynx DVB-T2 Receiver, G8YTZ, GPLv3.
"""
import ctypes as C
import glob
import mmap
import os

_drm = C.CDLL("libdrm.so.2")
u32, u16, i32 = C.c_uint32, C.c_uint16, C.c_int


class ModeRes(C.Structure):
    _fields_ = [("count_fbs", i32), ("fbs", C.POINTER(u32)),
                ("count_crtcs", i32), ("crtcs", C.POINTER(u32)),
                ("count_connectors", i32), ("connectors", C.POINTER(u32)),
                ("count_encoders", i32), ("encoders", C.POINTER(u32)),
                ("min_width", u32), ("max_width", u32), ("min_height", u32), ("max_height", u32)]


class ModeInfo(C.Structure):
    _fields_ = [("clock", u32), ("hdisplay", u16), ("hsync_start", u16), ("hsync_end", u16),
                ("htotal", u16), ("hskew", u16), ("vdisplay", u16), ("vsync_start", u16),
                ("vsync_end", u16), ("vtotal", u16), ("vscan", u16), ("vrefresh", u32),
                ("flags", u32), ("type", u32), ("name", C.c_char * 32)]


class Crtc(C.Structure):
    _fields_ = [("crtc_id", u32), ("buffer_id", u32), ("x", u32), ("y", u32),
                ("width", u32), ("height", u32), ("mode_valid", i32), ("mode", ModeInfo),
                ("gamma_size", i32)]


class PlaneRes(C.Structure):
    _fields_ = [("count_planes", u32), ("planes", C.POINTER(u32))]


class Plane(C.Structure):
    _fields_ = [("count_formats", u32), ("formats", C.POINTER(u32)), ("plane_id", u32),
                ("crtc_id", u32), ("fb_id", u32), ("crtc_x", u32), ("crtc_y", u32),
                ("x", u32), ("y", u32), ("possible_crtcs", u32), ("gamma_size", u32)]


class CreateDumb(C.Structure):
    _fields_ = [("height", u32), ("width", u32), ("bpp", u32), ("flags", u32),
                ("handle", u32), ("pitch", u32), ("size", C.c_uint64)]


class MapDumb(C.Structure):
    _fields_ = [("handle", u32), ("pad", u32), ("offset", C.c_uint64)]


_drm.drmModeGetResources.restype = C.POINTER(ModeRes)
_drm.drmModeGetResources.argtypes = [i32]
_drm.drmModeFreeResources.argtypes = [C.POINTER(ModeRes)]
_drm.drmModeGetCrtc.restype = C.POINTER(Crtc)
_drm.drmModeGetCrtc.argtypes = [i32, u32]
_drm.drmModeFreeCrtc.argtypes = [C.POINTER(Crtc)]
_drm.drmModeGetPlaneResources.restype = C.POINTER(PlaneRes)
_drm.drmModeGetPlaneResources.argtypes = [i32]
_drm.drmModeFreePlaneResources.argtypes = [C.POINTER(PlaneRes)]
_drm.drmModeGetPlane.restype = C.POINTER(Plane)
_drm.drmModeGetPlane.argtypes = [i32, u32]
_drm.drmModeFreePlane.argtypes = [C.POINTER(Plane)]
_drm.drmModeAddFB2.argtypes = [i32, u32, u32, u32, C.POINTER(u32), C.POINTER(u32),
                               C.POINTER(u32), C.POINTER(u32), u32]
_drm.drmModeRmFB.argtypes = [i32, u32]
_drm.drmModeSetPlane.argtypes = [i32, u32, u32, u32, u32, i32, i32, u32, u32, u32, u32, u32, u32]
_drm.drmIoctl.argtypes = [i32, C.c_ulong, C.c_void_p]

AR24 = 0x34325241                    # DRM_FORMAT_ARGB8888 ('AR24')
IOCTL_CREATE_DUMB = 0xC02064B2       # DRM_IOWR(0xB2, struct drm_mode_create_dumb)
IOCTL_MAP_DUMB = 0xC01064B3          # DRM_IOWR(0xB3, struct drm_mode_map_dumb)
IOCTL_DESTROY_DUMB = 0xC00464B4      # DRM_IOWR(0xB4, struct drm_mode_destroy_dumb)


class OsdPlane:
    """Owns the DRM device: finds the active display, one overlay plane for the
    video (given to kmssink) and one above it for the OSD."""

    def __init__(self):
        self.fd = -1
        self.crtc_id = self.crtc_index = None
        self.w = self.h = 0
        self.video_plane = self.osd_plane = None
        self.fb = None
        self.shown = False
        for dev in sorted(glob.glob("/dev/dri/card*")):
            fd = os.open(dev, os.O_RDWR | os.O_CLOEXEC)
            if self._probe(fd):
                self.fd, self.dev = fd, dev
                break
            os.close(fd)
        if self.fd < 0:
            raise RuntimeError("no active DRM display found")

    def _probe(self, fd):
        res = _drm.drmModeGetResources(fd)
        if not res or res.contents.count_connectors == 0:
            return False
        r = res.contents
        try:
            for i in range(r.count_crtcs):
                c = _drm.drmModeGetCrtc(fd, r.crtcs[i])
                if not c:
                    continue
                cc = c.contents
                if cc.mode_valid and cc.buffer_id:
                    self.crtc_id, self.crtc_index = cc.crtc_id, i
                    self.w, self.h = cc.mode.hdisplay, cc.mode.vdisplay
                _drm.drmModeFreeCrtc(c)
                if self.crtc_id:
                    break
            if not self.crtc_id:
                return False
            # without DRM_CLIENT_CAP_UNIVERSAL_PLANES only overlay planes are listed
            pres = _drm.drmModeGetPlaneResources(fd)
            overlays = []
            for i in range(pres.contents.count_planes):
                p = _drm.drmModeGetPlane(fd, pres.contents.planes[i])
                pc = p.contents
                fmts = [pc.formats[k] for k in range(pc.count_formats)]
                if pc.possible_crtcs & (1 << self.crtc_index) and AR24 in fmts:
                    overlays.append(pc.plane_id)
                _drm.drmModeFreePlane(p)
            _drm.drmModeFreePlaneResources(pres)
            if len(overlays) < 2:
                return False
            # the hardware stacks planes in id order by default: video below, OSD above
            self.video_plane, self.osd_plane = overlays[0], overlays[-1]
            return True
        finally:
            _drm.drmModeFreeResources(res)

    def _make_fb(self, w, h):
        cd = CreateDumb(height=h, width=w, bpp=32)
        if _drm.drmIoctl(self.fd, IOCTL_CREATE_DUMB, C.byref(cd)):
            raise OSError("create dumb buffer failed")
        md = MapDumb(handle=cd.handle)
        if _drm.drmIoctl(self.fd, IOCTL_MAP_DUMB, C.byref(md)):
            raise OSError("map dumb buffer failed")
        buf = mmap.mmap(self.fd, cd.size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                        offset=md.offset)
        handles = (u32 * 4)(cd.handle, 0, 0, 0)
        pitches = (u32 * 4)(cd.pitch, 0, 0, 0)
        offsets = (u32 * 4)(0, 0, 0, 0)
        fb_id = u32()
        if _drm.drmModeAddFB2(self.fd, w, h, AR24, handles, pitches, offsets, C.byref(fb_id), 0):
            raise OSError("AddFB2 failed")
        return {"id": fb_id.value, "handle": cd.handle, "pitch": cd.pitch, "map": buf, "w": w, "h": h}

    def _free_fb(self):
        if not self.fb:
            return
        _drm.drmModeRmFB(self.fd, self.fb["id"])
        self.fb["map"].close()
        h = u32(self.fb["handle"])
        _drm.drmIoctl(self.fd, IOCTL_DESTROY_DUMB, C.byref(h))
        self.fb = None

    def show(self, img, x, y):
        """img: PIL RGBA image in screen pixels; shown with its top-left at x, y."""
        import numpy as np
        w, h = img.size
        a = np.asarray(img.convert("RGBA"), dtype=np.uint16)
        alpha = a[:, :, 3:4]
        pm = (a[:, :, :3] * alpha // 255).astype(np.uint8)           # premultiplied
        bgra = np.concatenate([pm[:, :, ::-1], alpha.astype(np.uint8)], axis=2)
        if not self.fb or self.fb["w"] != w or self.fb["h"] != h:
            if self.shown:
                self.hide()
            self._free_fb()
            self.fb = self._make_fb(w, h)
        m, pitch = self.fb["map"], self.fb["pitch"]
        data = bgra.tobytes()
        row = w * 4
        if pitch == row:
            m.seek(0)
            m.write(data)
        else:
            for r in range(h):
                m.seek(r * pitch)
                m.write(data[r * row:(r + 1) * row])
        rc = _drm.drmModeSetPlane(self.fd, self.osd_plane, self.crtc_id, self.fb["id"], 0,
                                  int(x), int(y), w, h, 0, 0, w << 16, h << 16)
        self.shown = rc == 0
        return rc

    def hide(self):
        _drm.drmModeSetPlane(self.fd, self.osd_plane, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.shown = False

    def hide_video(self):
        """Turn off the video plane. kmssink leaves its last frame on screen when
        the player stops, and that frame covers the status page underneath."""
        _drm.drmModeSetPlane(self.fd, self.video_plane, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    def close(self):
        if self.fd >= 0:
            self.hide()
            self.hide_video()
            self._free_fb()
            os.close(self.fd)
            self.fd = -1


class FakePlane:
    """Test stand-in (T2RX_FAKEPLANE=1): records calls instead of using DRM."""
    def __init__(self):
        self.fd, self.dev, self.w, self.h = -1, "fake", 1920, 1080
        self.video_plane, self.osd_plane, self.crtc_id = 98, 263, 97
        self.calls = []
        self.shown = False

    def show(self, img, x, y):
        self.calls.append(("show", img.size, int(x), int(y)))
        self.shown = True
        import sys
        print("FAKEPLANE show %dx%d at %d,%d" % (img.width, img.height, x, y), file=sys.stderr)
        return 0

    def hide(self):
        if self.shown:
            import sys
            print("FAKEPLANE hide", file=sys.stderr)
        self.shown = False

    def close(self):
        self.hide()

    def hide_video(self):
        pass
