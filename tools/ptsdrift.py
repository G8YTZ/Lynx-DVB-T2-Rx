#!/usr/bin/env python3
"""ptsdrift.py - how do the transmitter's audio and video timestamps run against
each other, and against the Pi's clock? Reads a TS on stdin:
    sudo systemctl stop t2rx
    sudo /opt/t2rx/t2rx -f 436000000 -b 1.7 -q -G | python3 tools/ptsdrift.py
Every 15 s prints the audio-minus-video timestamp offset (it should stay put;
if it grows, the transmitter's sound is drifting away from its picture) and
each stream's rate against the Pi's clock (least-squares, ppm).
Ctrl-C to stop, then: sudo systemctl start t2rx
"""
import sys, time

def section(p):
    off = 4 + (1 + p[4] if p[3] & 0x20 else 0)
    return p[off + 1 + p[off]:]

def pts_of(p):
    off = 4 + (1 + p[4] if p[3] & 0x20 else 0)
    if off + 14 > 188 or p[off:off + 3] != b"\x00\x00\x01" or not (p[off + 7] & 0x80):
        return None
    b = p[off + 9:off + 14]
    return (((b[0] >> 1) & 7) << 30) | (b[1] << 22) | ((b[2] >> 1) << 15) | (b[3] << 7) | (b[4] >> 1)

def slope_ppm(samples):
    """least-squares slope of PTS seconds against Pi seconds, as ppm off 1.0"""
    n = len(samples)
    if n < 20:
        return 0.0
    mx = sum(w for w, _ in samples) / n; my = sum(p for _, p in samples) / n
    sxx = sum((w - mx) ** 2 for w, _ in samples)
    sxy = sum((w - mx) * (p - my) for w, p in samples)
    return (sxy / sxx - 1) * 1e6 if sxx else 0.0

pmt = vid = aud = None
samples = {}; lastpts = {}
t0 = time.time(); t_report = t0 + 15
buf = b""
while True:
    chunk = sys.stdin.buffer.read(188 * 64)
    if not chunk:
        break
    buf += chunk
    now = time.time() - t0
    n = len(buf) // 188 * 188
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
                t = s[j]; epid = ((s[j + 1] & 0x1f) << 8) | s[j + 2]
                if t == 0x1b and vid is None: vid = epid
                if t in (0x03, 0x04, 0x0f, 0x11) and aud is None: aud = epid
                j += 5 + (((s[j + 3] & 0x0f) << 8) | s[j + 4])
            print("video PID %s, audio PID %s" % (vid, aud), flush=True)
        elif pid in (vid, aud):
            pts = pts_of(p)
            if pts is not None:
                if pid in lastpts and abs(pts - lastpts[pid]) > 45000:     # >0.5 s jump
                    print("       (%s timestamp jump of %+.0f ms at %.0f s - rate measurement restarted)"
                          % ("video" if pid == vid else "audio", (pts - lastpts[pid]) / 90.0, now), flush=True)
                    samples[pid] = []
                lastpts[pid] = pts
                if now > 3:
                    samples.setdefault(pid, []).append((now, pts / 90000.0))
    buf = buf[n:]
    if now + t0 >= t_report and vid in lastpts and aud in lastpts:
        t_report = time.time() + 15
        off = (lastpts[aud] - lastpts[vid]) / 90.0
        print("%5.0f s  audio-video offset %+7.0f ms   video %+7.0f ppm   audio %+7.0f ppm"
              % (now, off, slope_ppm(samples.get(vid, [])), slope_ppm(samples.get(aud, []))), flush=True)
