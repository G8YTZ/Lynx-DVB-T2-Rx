"""fb.py - draw full-screen images on the Linux framebuffer (/dev/fb0).
Used for the status page while there is no picture; kmssink puts the video on
a plane above it. The text console is unbound from fb0 while we own it.
G8YTZ narrowband DVB-T2 project, GPLv3."""
import glob
import os


def _read(path, dflt=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return dflt


class Framebuffer:
    def __init__(self, dev="/dev/fb0"):
        self.dev = dev
        base = "/sys/class/graphics/" + os.path.basename(dev)
        w, h = (_read(base + "/virtual_size", "1920,1080").split(",") + ["1080"])[:2]
        self.w, self.h = int(w), int(h)
        self.bpp = int(_read(base + "/bits_per_pixel", "32") or 32)
        stride = _read(base + "/stride", "")
        self.stride = int(stride) if stride else self.w * self.bpp // 8
        self.ok = os.path.exists(dev)
        self._np = None
        if self.bpp == 16:
            try:
                import numpy
                self._np = numpy
            except ImportError:
                self._np = None

    def show(self, img, box=None):
        """img: PIL RGB image. box=(x, y) writes img as a sub-rectangle at x, y
        (cheaper on a Pi Zero); otherwise img fills the whole screen."""
        if not self.ok:
            return
        if box is None:
            if img.size != (self.w, self.h):
                img = img.resize((self.w, self.h))
            x0, y0 = 0, 0
        else:
            x0, y0 = int(box[0]), int(box[1])
        w, h = img.size
        bpp = self.bpp // 8
        if self.bpp == 32:
            data = img.convert("RGBX").tobytes("raw", "BGRX")
        elif self.bpp == 16 and self._np is not None:
            np = self._np
            a = np.asarray(img.convert("RGB"), dtype=np.uint16)
            v = ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)
            data = v.astype("<u2").tobytes()
        else:
            return
        row = w * bpp
        try:
            with open(self.dev, "r+b", buffering=0) as f:
                if x0 == 0 and w == self.w and row == self.stride:
                    f.seek(y0 * self.stride)
                    f.write(data)
                else:
                    for y in range(h):
                        f.seek((y0 + y) * self.stride + x0 * bpp)
                        f.write(data[y * row:(y + 1) * row])
        except OSError:
            pass

    def blank(self):
        from PIL import Image
        self.show(Image.new("RGB", (self.w, self.h), (0, 0, 0)))


def console(bind):
    """Bind/unbind the text console from the framebuffer."""
    for vt in glob.glob("/sys/class/vtconsole/vtcon*"):
        if "frame buffer" in _read(vt + "/name"):
            try:
                with open(vt + "/bind", "w") as f:
                    f.write("1" if bind else "0")
            except OSError:
                pass
