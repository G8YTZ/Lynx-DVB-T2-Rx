#!/usr/bin/env python3
"""ptsjumps.py - list irregular video/audio timestamp steps in a live TS (60 s).
    sudo systemctl stop t2rx
    sudo /opt/t2rx/t2rx -f 436000000 -b 1.7 -q -G | python3 ptsjumps.py
    sudo systemctl start t2rx
Prints each step that differs from the stream's usual step by more than half a
frame, then a summary. A good encoder shows none."""
import sys, time
from collections import Counter

def section(p):
    off = 4 + (1 + p[4] if p[3] & 0x20 else 0)
    return p[off + 1 + p[off]:]

def pts_of(p):
    off = 4 + (1 + p[4] if p[3] & 0x20 else 0)
    if off + 14 > 188 or p[off:off + 3] != b"\x00\x00\x01" or not (p[off + 7] & 0x80):
        return None
    b = p[off + 9:off + 14]
    return (((b[0] >> 1) & 7) << 30) | (b[1] << 22) | ((b[2] >> 1) << 15) | (b[3] << 7) | (b[4] >> 1)

pmt = vid = aud = None
last = {}; steps = {256: [], 257: []}; odd = []
t0 = time.time(); buf = b""
while time.time() - t0 < 60:
    chunk = sys.stdin.buffer.read(188 * 64)
    if not chunk:
        break
    buf += chunk; n = len(buf) // 188 * 188
    for i in range(0, n, 188):
        p = buf[i:i + 188]
        if p[0] != 0x47 or not (p[1] & 0x40):
            continue
        pid = ((p[1] & 0x1f) << 8) | p[2]
        if pid == 0 and pmt is None:
            s = section(p); pmt = ((s[10] & 0x1f) << 8) | s[11]
        elif pid == pmt and vid is None:
            s = section(p); slen = ((s[1] & 0x0f) << 8) | s[2]
            j = 12 + (((s[10] & 0x0f) << 8) | s[11])
            while j + 5 <= slen - 1:
                t = s[j]; e = ((s[j + 1] & 0x1f) << 8) | s[j + 2]
                if t == 0x1b and vid is None: vid = e
                if t in (0x03, 0x04, 0x0f, 0x11) and aud is None: aud = e
                j += 5 + (((s[j + 3] & 0x0f) << 8) | s[j + 4])
            steps = {vid: [], aud: []}
        elif pid in (vid, aud):
            v = pts_of(p)
            if v is None:
                continue
            if pid in last:
                d = ((v - last[pid]) % (1 << 33)) / 90.0          # ms
                if d > 1 << 32: d -= (1 << 33) / 90.0
                steps[pid].append((time.time() - t0, d))
            last[pid] = v
    buf = buf[n:]
for pid, name in ((vid, "video"), (aud, "audio")):
    st = steps.get(pid, [])
    if not st:
        print("%s: no timestamps" % name); continue
    usual = Counter(round(d) for _, d in st).most_common(1)[0][0]
    bad = [(t, d) for t, d in st if abs(d - usual) > usual / 2]
    print("%s: %d steps, usual %d ms, %d irregular" % (name, len(st), usual, len(bad)))
    for t, d in bad[:12]:
        print("   at %5.1f s: step %+8.1f ms" % (t, d))
    if len(bad) > 12:
        print("   ... %d more" % (len(bad) - 12))
