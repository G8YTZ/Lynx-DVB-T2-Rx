#!/usr/bin/env python3
"""ptsgaps.py - are frames missing? Lists the interval between coded pictures.

    python3 ptsgaps.py /tmp/cut.ts

At 25 fps each frame should be 3600 ticks (40 ms) after the last. Anything
larger means the encoder produced no frame at all for that period - which a
receiver keeping real time sees as the picture stopping.  G8YTZ, GPLv3.
"""
import sys

def video_pid(d):
    """First PID carrying H.264, from the PAT and PMT."""
    for i in range(0, len(d) - 187, 188):
        p = d[i:i+188]
        if p[0] != 0x47 or (((p[1] & 0x1f) << 8) | p[2]) != 0 or not (p[1] & 0x40):
            continue
        off = 4 + ((1 + p[4]) if p[3] & 0x20 else 0) + 1
        s0 = p[off:]
        if len(s0) < 12:
            continue
        pmt = ((s0[10] & 0x1f) << 8) | s0[11]
        for j in range(0, len(d) - 187, 188):
            q = d[j:j+188]
            if q[0] != 0x47 or (((q[1] & 0x1f) << 8) | q[2]) != pmt or not (q[1] & 0x40):
                continue
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
    """(size, pts) per access unit on that PID."""
    out, buf, pts = [], bytearray(), None
    for i in range(0, len(d) - 187, 188):
        p = d[i:i+188]
        if p[0] != 0x47 or (((p[1] & 0x1f) << 8) | p[2]) != pid:
            continue
        off = 4 + ((1 + p[4]) if p[3] & 0x20 else 0)
        if off >= 188:
            continue
        pay = p[off:]
        if p[1] & 0x40:
            if buf:
                out.append((len(buf), None, pts))
            buf = bytearray()
            if len(pay) > 13 and pay[0] == 0 and pay[1] == 0 and pay[2] == 1 and (pay[7] & 0x80):
                b = pay[9:14]
                if len(b) == 5:
                    pts = (((b[0] >> 1) & 7) << 30) | (b[1] << 22) | (((b[2] >> 1) & 0x7f) << 15) \
                          | (b[3] << 7) | (b[4] >> 1)
            buf += pay[9 + (pay[8] if len(pay) > 8 else 0):]
        else:
            buf += pay
    if buf:
        out.append((len(buf), None, pts))
    return out


d = open(sys.argv[1], "rb").read()
pid = int(sys.argv[2]) if len(sys.argv) > 2 else video_pid(d)
fr = [f for f in frames(d, pid) if f[2] is not None]
print("video PID %d, %d pictures with timestamps\n" % (pid, len(fr)))

# work the frame period out from the stream rather than assuming it
deltas = []
prev = None
for _, _, pts in fr:
    if prev is not None:
        deltas.append((pts - prev) & 0x1FFFFFFFF)
    prev = pts
if not deltas:
    sys.exit("no timestamps")
period = sorted(deltas)[len(deltas) // 2]
print("frame period %d ticks (%.1f ms, %.2f fps)\n"
      % (period, period / 90.0, 90000.0 / period))

gaps = [(n, dt) for n, dt in enumerate(deltas) if dt > period * 1.5]
print("%d gaps longer than one and a half frame periods:" % len(gaps))
for n, dt in gaps[:40]:
    print("   after picture %4d: %8.1f ms  (%.1f frames missing)"
          % (n, dt / 90.0, dt / period - 1))
if not gaps:
    print("   none - the encoder produced every frame on time")
else:
    lost = sum(dt / period - 1 for _, dt in gaps)
    print("\ntotal missing: %.0f frames, %.1f s of the %.1f s captured"
          % (lost, lost * period / 90000.0, len(fr) * period / 90000.0))
