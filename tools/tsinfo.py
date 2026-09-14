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
                 0x24: "NO - a Pi has no HEVC decoder for this", 0x51: "NO"}


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
        print()
    print("Choose a service on the receiver with Left/Right, the web page, or")
    print("service = N in /etc/t2rx/presets.conf")


if __name__ == "__main__":
    main()
