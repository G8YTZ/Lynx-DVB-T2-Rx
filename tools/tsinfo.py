#!/usr/bin/env python3
"""tsinfo.py - what is in a transport stream, with no dependencies.

    sudo systemctl stop t2rx
    sudo timeout 20 /opt/t2rx/t2rx -f 445500000 -b 7 -q -G > /tmp/a.ts
    sudo systemctl start t2rx
    python3 tsinfo.py /tmp/a.ts

Lists every programme in the multiplex, its name from the SDT, and the streams
inside it with their codecs - which says at once whether a service carries video
the Pi can decode. Reads a file, or stdin if no file is given.
G8YTZ, GPLv3.
"""
import sys

STREAM_TYPES = {
    0x01: "MPEG-1 video", 0x02: "MPEG-2 video", 0x03: "MPEG-1 audio",
    0x04: "MPEG-2 audio", 0x06: "private data (subtitles, teletext)",
    0x0f: "AAC audio (ADTS)", 0x11: "AAC audio (LATM)",
    0x1b: "H.264 video", 0x24: "H.265 video (HEVC)", 0x51: "H.266 video (VVC)",
    0x81: "AC-3 audio", 0x87: "E-AC-3 audio",
}
PI_CAN_DECODE = {0x1b: "yes, in hardware", 0x02: "no (no MPEG-2 licence on modern Pis)",
                 0x24: "no - a Pi has no HEVC decoder", 0x51: "no"}


def sections(data, pid_wanted):
    """Reassemble PSI sections for one PID."""
    buf, want = b"", 0
    for i in range(0, len(data) - 187, 188):
        p = data[i:i + 188]
        if p[0] != 0x47:
            continue
        pid = ((p[1] & 0x1f) << 8) | p[2]
        if pid != pid_wanted:
            continue
        off = 4 + ((1 + p[4]) if p[3] & 0x20 else 0)
        if off >= 188:
            continue
        if p[1] & 0x40:                         # a section starts here
            off += 1 + p[off]
            buf = p[off:]
            if len(buf) >= 3:
                want = (((buf[1] & 0x0f) << 8) | buf[2]) + 3
        elif buf:
            buf += p[off:]
        while want and len(buf) >= want:
            yield buf[:want]
            buf = buf[want:]
            want = (((buf[1] & 0x0f) << 8) | buf[2]) + 3 if len(buf) >= 3 and buf[0] != 0xff else 0


def parse_pat(data):
    out = {}
    for s in sections(data, 0):
        if s[0] != 0x00:
            continue
        n = (((s[1] & 0x0f) << 8) | s[2]) + 3 - 4
        for i in range(8, n, 4):
            prog = (s[i] << 8) | s[i + 1]
            pmt = ((s[i + 2] & 0x1f) << 8) | s[i + 3]
            if prog:
                out[prog] = pmt
    return out


def parse_pmt(data, pid):
    streams = []
    for s in sections(data, pid):
        if s[0] != 0x02:
            continue
        n = (((s[1] & 0x0f) << 8) | s[2]) + 3 - 4
        i = 12 + (((s[10] & 0x0f) << 8) | s[11])
        while i + 5 <= n:
            t = s[i]
            epid = ((s[i + 1] & 0x1f) << 8) | s[i + 2]
            il = ((s[i + 3] & 0x0f) << 8) | s[i + 4]
            streams.append((epid, t))
            i += 5 + il
        if streams:
            break
    return streams


def parse_sdt(data):
    names = {}
    for s in sections(data, 0x11):
        if s[0] != 0x42:                         # this transport stream
            continue
        n = (((s[1] & 0x0f) << 8) | s[2]) + 3 - 4
        i = 11
        while i + 5 <= n:
            sid = (s[i] << 8) | s[i + 1]
            dl = ((s[i + 3] & 0x0f) << 8) | s[i + 4]
            j, end = i + 5, i + 5 + dl
            while j + 2 <= end:
                tag, ln = s[j], s[j + 1]
                if tag == 0x48 and ln >= 3:      # service descriptor
                    k = j + 3
                    pl = s[k]
                    prov = s[k + 1:k + 1 + pl]
                    k += 1 + pl
                    nl = s[k]
                    nm = s[k + 1:k + 1 + nl]
                    def clean(b):
                        return bytes(c for c in b if c >= 0x20).decode("latin-1", "replace").strip()
                    names[sid] = (clean(nm), clean(prov))
                j += 2 + ln
            i = end
    return names


class Bits:
    """Bit reader with the Exp-Golomb coding H.264 headers use."""
    def __init__(self, data):
        self.d, self.p = data, 0

    def u(self, n):
        v = 0
        for _ in range(n):
            byte = self.d[self.p >> 3] if (self.p >> 3) < len(self.d) else 0
            v = (v << 1) | ((byte >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def ue(self):
        z = 0
        while self.u(1) == 0 and z < 32:
            z += 1
        return (1 << z) - 1 + self.u(z) if z else 0

    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k % 2 else -(k // 2)


def parse_sps(nal):
    """Size, profile, level and whether the picture is interlaced - the three
    things a Raspberry Pi's hardware decoder actually cares about."""
    b = Bits(nal)
    profile = b.u(8)
    b.u(8)                                        # constraint flags
    level = b.u(8)
    b.ue()                                        # sps id
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = b.ue()
        if chroma == 3:
            b.u(1)
        b.ue(); b.ue(); b.u(1)
        if b.u(1):                                # scaling matrices
            for i in range(8 if chroma != 3 else 12):
                if b.u(1):
                    last, nxt, size = 8, 8, 16 if i < 6 else 64
                    for _ in range(size):
                        if nxt:
                            nxt = (last + b.se() + 256) % 256
                        last = nxt or last
    b.ue()                                        # log2_max_frame_num
    poc = b.ue()
    if poc == 0:
        b.ue()
    elif poc == 1:
        b.u(1); b.se(); b.se()
        for _ in range(b.ue()):
            b.se()
    b.ue(); b.u(1)                                # max_num_ref_frames, gaps flag
    w = (b.ue() + 1) * 16
    h_units = b.ue() + 1
    frame_mbs_only = b.u(1)
    h = h_units * 16 * (1 if frame_mbs_only else 2)
    if not frame_mbs_only:
        b.u(1)                                    # mb_adaptive_frame_field_flag
    b.u(1)                                        # direct_8x8
    if b.u(1):                                    # cropping
        cl, cr, ct, cb = b.ue(), b.ue(), b.ue(), b.ue()
        w -= (cl + cr) * 2
        h -= (ct + cb) * 2 * (1 if frame_mbs_only else 2)
    names = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High",
             110: "High 10", 122: "High 4:2:2", 244: "High 4:4:4"}
    return {"w": w, "h": h, "profile": names.get(profile, "profile %d" % profile),
            "level": "%.1f" % (level / 10.0), "level_num": level,
            "interlaced": not frame_mbs_only}


def find_sps(data, pid):
    """First SPS on a video PID, unescaped."""
    payload = b""
    for i in range(0, len(data) - 187, 188):
        p = data[i:i + 188]
        if p[0] != 0x47 or (((p[1] & 0x1f) << 8) | p[2]) != pid:
            continue
        off = 4 + ((1 + p[4]) if p[3] & 0x20 else 0)
        payload += p[off:]
        if len(payload) > 400000:
            break
    j = 0
    while True:
        k = payload.find(b"\x00\x00\x01", j)
        if k < 0:
            return None
        nal = payload[k + 3]
        if (nal & 0x1f) == 7:                     # SPS
            end = payload.find(b"\x00\x00\x01", k + 3)
            raw = payload[k + 4:end if end > 0 else k + 300]
            out, z = bytearray(), 0                # remove emulation prevention
            for byte in raw:
                if z >= 2 and byte == 3:
                    z = 0
                    continue
                z = z + 1 if byte == 0 else 0
                out.append(byte)
            try:
                return parse_sps(bytes(out))
            except Exception:
                return None
        j = k + 3


def main():
    data = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else sys.stdin.buffer.read()
    print("%d bytes, %d packets\n" % (len(data), len(data) // 188))
    pat = parse_pat(data)
    if not pat:
        print("No PAT found - is this a transport stream, and did it lock?")
        return
    names = parse_sdt(data)
    print("%d service%s in this multiplex:\n" % (len(pat), "" if len(pat) == 1 else "s"))
    for prog in sorted(pat):
        nm, prov = names.get(prog, ("", ""))
        print("  service %d%s%s" % (prog, ("  \"%s\"" % nm) if nm else "",
                                    ("  (%s)" % prov) if prov else ""))
        for epid, t in parse_pmt(data, pat[prog]):
            what = STREAM_TYPES.get(t, "type 0x%02x" % t)
            note = PI_CAN_DECODE.get(t)
            print("      PID %-5d %-28s%s" % (epid, what, ("   %s" % note) if note else ""))
            if t == 0x1b:
                sps = find_sps(data, epid)
                if sps:
                    print("              %dx%d %s, %s profile, level %s" % (
                        sps["w"], sps["h"], "INTERLACED" if sps["interlaced"] else "progressive",
                        sps["profile"], sps["level"]))
                    if sps["interlaced"]:
                        print("              (interlaced: UK Freeview HD is coded this way and a Pi")
                        print("               decodes it, so this alone is not a reason for no picture)")
                    elif sps["level_num"] > 41:
                        print("              ^ above level 4.1 - may be beyond the Pi's hardware decoder")
                    elif sps["h"] == 1088:
                        print("              (1088 is normal: 1080 rounded up to whole macroblocks,")
                        print("               with the cropping flag left out. A Pi decodes it fine.)")
                    elif sps["h"] > 1088:
                        print("              ^ taller than 1080 - beyond the Pi's hardware decoder")
        print()
    print("Choose a service on the receiver with Left/Right, the web page, or")
    print("service = N in /etc/t2rx/presets.conf")


if __name__ == "__main__":
    main()
