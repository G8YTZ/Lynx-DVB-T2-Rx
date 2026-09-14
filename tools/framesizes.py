#!/usr/bin/env python3
"""framesizes.py - how big is each frame, and does it fit the channel?

    python3 framesizes.py /tmp/cut.ts [video PID]

Lists every coded picture with its size and type, so a scene change shows up as
exactly what it is. The budget line is what the channel can carry in one frame
period; anything far above it arrives late.  G8YTZ, GPLv3.
"""
import sys

def video_pid(d):
    """First PID carrying an H.264 stream, from the PMT."""
    for i in range(0, len(d) - 187, 188):
        p = d[i:i+188]
        if p[0] != 0x47:
            continue
        pid = ((p[1] & 0x1f) << 8) | p[2]
        if pid == 0 and (p[1] & 0x40):
            off = 4 + ((1 + p[4]) if p[3] & 0x20 else 0) + 1 + p[4 + (1 if p[3] & 0x20 else 0)] * 0
            s = p[off:]
            if len(s) > 12:
                pmt = ((s[10] & 0x1f) << 8) | s[11]
                for j in range(0, len(d) - 187, 188):
                    q = d[j:j+188]
                    if q[0] == 0x47 and (((q[1] & 0x1f) << 8) | q[2]) == pmt and (q[1] & 0x40):
                        o = 4 + ((1 + q[4]) if q[3] & 0x20 else 0) + 1
                        sec = q[o:]
                        n = (((sec[1] & 0x0f) << 8) | sec[2]) + 3 - 4
                        k = 12 + (((sec[10] & 0x0f) << 8) | sec[11])
                        while k + 5 <= min(n, len(sec)):
                            if sec[k] == 0x1b:
                                return ((sec[k+1] & 0x1f) << 8) | sec[k+2]
                            k += 5 + (((sec[k+3] & 0x0f) << 8) | sec[k+4])
    return None

def frames(d, pid):
    """(size, type, pts) for each access unit on that PID."""
    out, buf, pts = [], bytearray(), None
    for i in range(0, len(d) - 187, 188):
        p = d[i:i+188]
        if p[0] != 0x47 or (((p[1] & 0x1f) << 8) | p[2]) != pid:
            continue
        start = bool(p[1] & 0x40)
        off = 4 + ((1 + p[4]) if p[3] & 0x20 else 0)
        if off >= 188:
            continue
        pay = p[off:]
        if start:
            if buf:
                out.append((len(buf), kind(buf), pts))
            buf = bytearray()
            if len(pay) > 13 and pay[0] == 0 and pay[1] == 0 and pay[2] == 1 and (pay[7] & 0x80):
                h = pay[8]
                b = pay[9:9+5]
                if len(b) == 5:
                    pts = (((b[0] >> 1) & 7) << 30) | (b[1] << 22) | (((b[2] >> 1) & 0x7f) << 15) \
                          | (b[3] << 7) | (b[4] >> 1)
            hdr = 9 + (pay[8] if len(pay) > 8 else 0)
            buf += pay[hdr:]
        elif buf is not None:
            buf += pay
    if buf:
        out.append((len(buf), kind(buf), pts))
    return out

def kind(b):
    """I, P or B, from the first slice's type."""
    j = 0
    while True:
        k = b.find(b"\x00\x00\x01", j)
        if k < 0 or k + 4 >= len(b):
            return "?"
        t = b[k+3] & 0x1f
        if t == 5:
            return "I"
        if t == 1:
            # slice_type is the second exp-golomb value; 0/5 = P, 1/6 = B, 2/7 = I
            v, p = b[k+4:k+9], 0
            bits = "".join("{:08b}".format(x) for x in v)
            def ue(s, p):
                z = 0
                while p < len(s) and s[p] == "0":
                    z += 1; p += 1
                p += 1
                val = int(s[p:p+z] or "0", 2) + (1 << z) - 1 if z else 0
                return val, p + z
            _, p = ue(bits, 0)          # first_mb_in_slice
            st, _ = ue(bits, p)         # slice_type
            return {0: "P", 1: "B", 2: "I", 5: "P", 6: "B", 7: "I"}.get(st, "?")
        j = k + 3

d = open(sys.argv[1], "rb").read()
pid = int(sys.argv[2]) if len(sys.argv) > 2 else video_pid(d)
if not pid:
    sys.exit("no H.264 stream found")
fr = frames(d, pid)
print("video PID %d, %d coded pictures\n" % (pid, len(fr)))

# what one frame period can carry, from the observed average
total = sum(f[0] for f in fr)
secs = len(fr) / 25.0
print("average %.2f Mb/s over %.1f s at 25 fps" % (total * 8 / secs / 1e6, secs))
budget = total / len(fr)
print("average frame %d bytes\n" % budget)
print("  n   type     bytes   x average   %s" % ("bar: each block is 2x average",))
for n, (sz, t, _) in enumerate(fr):
    x = sz / budget
    bar = "#" * min(40, int(x * 2))
    flag = "  <-- big" if x > 4 else ""
    print("%4d   %-4s %8d   %5.1f  %s%s" % (n, t, sz, x, bar, flag))
