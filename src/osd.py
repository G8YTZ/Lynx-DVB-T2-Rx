"""osd.py - Lynx-style graphics for the DVB-T2 receiver (Pillow).

Two renderings share one look (Lynx / QuickLynx palette):
  render_osd()   RGBA panel blended onto the video (sized to the video width)
  render_idle()  full-screen status page shown when there is no picture
G8YTZ narrowband DVB-T2 project, GPLv3.
"""
from PIL import Image, ImageDraw, ImageFont

# Lynx / QuickLynx palette
BG_BASE = (10, 13, 18)
BG_PANEL = (18, 22, 29)
BG_RAISED = (26, 32, 41)
BG_HI = (35, 42, 53)
TXT = (255, 255, 255)
TXT2 = (215, 221, 229)
MUTED = (154, 167, 184)
FAINT = (107, 118, 132)
GREEN = (57, 255, 106)
INFO = (79, 195, 247)
AMBER = (232, 163, 61)
RED = (255, 90, 90)
ACCENT = (0, 122, 255)          # Lynx overlay blue

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_M = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
_fcache = {}


def font(size, bold=False, mono=False):
    key = (int(size), bold, mono)
    if key not in _fcache:
        path = FONT_M if mono else (FONT_B if bold else FONT)
        try:
            _fcache[key] = ImageFont.truetype(path, max(6, int(size)))
        except OSError:
            _fcache[key] = ImageFont.load_default()
    return _fcache[key]


# Approximate C/N needed by DVB-T2 (AWGN, 64800-bit LDPC, incl. typical
# implementation loss). Margin = measured C/N - this.
REQ_CN = {
    ("QPSK", "1/2"): 2.0, ("QPSK", "3/5"): 3.2, ("QPSK", "2/3"): 4.1,
    ("QPSK", "3/4"): 5.1, ("QPSK", "4/5"): 5.7, ("QPSK", "5/6"): 6.2,
    ("16QAM", "1/2"): 7.2, ("16QAM", "3/5"): 8.6, ("16QAM", "2/3"): 9.9,
    ("16QAM", "3/4"): 11.0, ("16QAM", "4/5"): 11.8, ("16QAM", "5/6"): 12.3,
    ("64QAM", "1/2"): 10.9, ("64QAM", "3/5"): 12.9, ("64QAM", "2/3"): 14.2,
    ("64QAM", "3/4"): 15.6, ("64QAM", "4/5"): 16.5, ("64QAM", "5/6"): 17.1,
    ("256QAM", "1/2"): 14.0, ("256QAM", "3/5"): 16.8, ("256QAM", "2/3"): 18.3,
    ("256QAM", "3/4"): 20.2, ("256QAM", "4/5"): 21.3, ("256QAM", "5/6"): 22.0,
}


def margin(st):
    try:
        req = REQ_CN.get((st.get("mod"), st.get("fec")))
        cn = float(st.get("cnr", "-999"))
        if req is None or cn < -100:
            return None
        return cn - req
    except ValueError:
        return None


def margin_colour(m):
    if m is None:
        return MUTED
    return GREEN if m >= 3 else (AMBER if m >= 0 else RED)


def fnum(st, key, fmt, dflt="--"):
    try:
        v = float(st.get(key, ""))
        return dflt if v < -100 else fmt % v
    except ValueError:
        return dflt


def bar(d, x, y, w, h, frac, colour, s):
    frac = max(0.0, min(1.0, frac))
    d.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=BG_HI)
    if frac > 0.02:
        d.rounded_rectangle([x, y, x + max(h, w * frac), y + h], radius=h / 2, fill=colour)


def pill(d, x, y, text, colour, s, fg=BG_BASE):
    f = font(13 * s, bold=True)
    tw = d.textlength(text, font=f)
    h = 22 * s
    d.rounded_rectangle([x, y, x + tw + 18 * s, y + h], radius=h / 2, fill=colour)
    d.text((x + 9 * s, y + h / 2), text, font=f, fill=fg, anchor="lm")
    return tw + 18 * s


def state_badge(st):
    s = st.get("state", "")
    if s == "LOCK":
        return "LOCKED", GREEN
    if s == "SYNC":
        return "SEARCHING", AMBER
    return "NO SIGNAL", RED


# --------------------------------------------------------------------- OSD
def render_osd(video_w, st, info, mode="full"):
    """RGBA image to blend over the video (drawn at video resolution).

    st   : tuner status dict (state sig cnr rate mod fec gi fft per)
    info : dict with preset, name, freq, bw, callsign, provider, video, audio
    mode : "full" | "mini"
    """
    s = max(0.5, video_w / 800.0)
    if mode == "mini":
        return _render_mini(s, st, info)
    W, H = int(404 * s), int(196 * s)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=12 * s, fill=BG_PANEL + (215,),
                        outline=BG_HI + (255,), width=max(1, int(s)))
    d.rounded_rectangle([0, 0, 5 * s, H - 1], radius=3 * s, fill=ACCENT + (255,))
    x0, y = 16 * s, 12 * s
    call = info.get("callsign") or "--"
    d.text((x0, y), call, font=font(26 * s, bold=True), fill=TXT)
    label, col = state_badge(st)
    ftmp = font(13 * s, bold=True)
    pw = d.textlength(label, font=ftmp) + 18 * s
    pill(d, W - pw - 12 * s, y + 4 * s, label, col, s)
    y += 34 * s
    sub = info.get("provider") or ""
    pre = "P%s  %s" % (info.get("preset", "?"), info.get("name", ""))
    d.text((x0, y), pre + ("   " + sub if sub and sub != call else ""), font=font(13 * s), fill=MUTED)
    y += 22 * s
    d.text((x0, y), "%.3f MHz" % info.get("freq", 0), font=font(17 * s, bold=True), fill=INFO)
    d.text((x0 + 146 * s, y + 2 * s), "%s kHz   %s %s   GI %s   %s" % (
        info.get("bw", "?"), st.get("mod", "-"), st.get("fec", "-"), st.get("gi", "-"), st.get("fft", "-")),
        font=font(13 * s), fill=TXT2)
    y += 30 * s
    m = margin(st)
    mc = margin_colour(m)
    d.text((x0, y), "C/N", font=font(12 * s), fill=MUTED)
    d.text((x0 + 44 * s, y - 3 * s), fnum(st, "cnr", "%.1f dB"), font=font(17 * s, bold=True), fill=mc)
    d.text((x0 + 150 * s, y), "Margin", font=font(12 * s), fill=MUTED)
    d.text((x0 + 206 * s, y - 3 * s), ("%+.1f dB" % m) if m is not None else "--",
           font=font(17 * s, bold=True), fill=mc)
    y += 24 * s
    try:
        cn = float(st.get("cnr", "-999"))
    except ValueError:
        cn = -999
    bar(d, x0, y, W - x0 - 16 * s, 8 * s, (cn / 35.0) if cn > -100 else 0, mc, s)
    y += 18 * s
    d.text((x0, y), "Signal %s" % fnum(st, "sig", "%.1f dBm"), font=font(12 * s), fill=TXT2)
    d.text((x0 + 150 * s, y), "TS %s" % fnum(st, "rate", "%.2f Mb/s"), font=font(12 * s), fill=TXT2)
    y += 20 * s
    vid = info.get("video") or ""
    aud = info.get("audio") or ""
    d.text((x0, y), "  ".join(t for t in (vid, aud) if t) or " ", font=font(12 * s), fill=FAINT)
    return img


def _render_mini(s, st, info):
    f = font(15 * s, bold=True)
    call = info.get("callsign") or ("P%s" % info.get("preset", "?"))
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = tmp.textlength(call, font=f)
    W, H = int(tw + 92 * s), int(30 * s)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H / 2, fill=BG_PANEL + (190,))
    _, col = state_badge(st)
    d.ellipse([10 * s, H / 2 - 5 * s, 20 * s, H / 2 + 5 * s], fill=col)
    d.text((28 * s, H / 2), call, font=f, fill=TXT, anchor="lm")
    m = margin(st)
    try:
        cn = float(st.get("cnr", "-999"))
    except ValueError:
        cn = -999
    bar(d, 34 * s + tw, H / 2 - 4 * s, 44 * s, 8 * s, (cn / 35.0) if cn > -100 else 0, margin_colour(m), s)
    return img


# ------------------------------------------------------------- tune panel
def render_tune(width, t, presets):
    """Panel for the on-screen tuning entry. t: dict(digits, bw, stage)."""
    s = max(0.5, width / 1920.0) * 2.4
    W, H = int(360 * s), int(150 * s)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=12 * s, fill=BG_PANEL + (235,),
                        outline=ACCENT + (255,), width=max(2, int(2 * s)))
    x0, y = 18 * s, 12 * s
    if t.get("stage") == "save":
        d.text((x0, y), "Store preset", font=font(17 * s, bold=True), fill=TXT)
        y += 30 * s
        d.text((x0, y), "%s MHz   %s kHz" % (t["shown"], t["bw"]), font=font(20 * s, bold=True), fill=INFO)
        y += 34 * s
        d.text((x0, y), "Press 1-9 to store in that preset", font=font(14 * s), fill=TXT2)
        y += 22 * s
        d.text((x0, y), "BACK to keep it for now only", font=font(14 * s), fill=MUTED)
        return img
    d.text((x0, y), "Tune", font=font(17 * s, bold=True), fill=TXT)
    d.text((W - 18 * s, y + 2 * s), "%s kHz" % t["bw"], font=font(16 * s, bold=True), fill=INFO, anchor="rm")
    y += 30 * s
    digits = t.get("digits", "")
    shown = t["shown"]
    d.text((x0, y), shown, font=font(30 * s, bold=True), fill=TXT if digits else MUTED)
    d.text((x0 + 190 * s, y + 10 * s), "MHz", font=font(15 * s), fill=MUTED)
    y += 44 * s
    d.text((x0, y), "0-9 frequency   " + ("\u25b2\u25bc bandwidth" if not digits else "OK tune"),
           font=font(14 * s), fill=TXT2)
    y += 20 * s
    d.text((x0, y), "BACK delete / exit", font=font(14 * s), fill=MUTED)
    return img


# -------------------------------------------------------------- idle page
def idle_card_box(W, H):
    """Screen area of the live status card (for partial updates)."""
    s = H / 1080.0
    return (int(60 * s), int(150 * s), int(1140 * s) + 1, int(710 * s) + 1)


def render_idle(W, H, st, info, presets, message=None, version=""):
    """Full-screen RGB status page (no picture)."""
    s = H / 1080.0
    img = Image.new("RGB", (W, H), BG_BASE)
    d = ImageDraw.Draw(img)
    # header
    d.rectangle([0, 0, W, 96 * s], fill=BG_PANEL)
    d.rectangle([0, 96 * s, W, 100 * s], fill=ACCENT)
    d.text((60 * s, 48 * s), "Lynx", font=font(46 * s, bold=True), fill=TXT, anchor="lm")
    lw = d.textlength("Lynx", font=font(46 * s, bold=True))
    d.text((60 * s + lw + 22 * s, 52 * s), "DVB-T2 Receiver", font=font(34 * s), fill=TXT2, anchor="lm")
    d.text((W - 60 * s, 52 * s), version + "  \u00b7  G8YTZ", font=font(20 * s), fill=FAINT, anchor="rm")

    # status card
    cx, cy, cw, ch = 60 * s, 150 * s, 1080 * s, 560 * s
    d.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=24 * s, fill=BG_RAISED, outline=BG_HI, width=2)
    label, col = state_badge(st)
    if message:
        label, col = message
    size = 64
    while size > 30 and d.textlength(label, font=font(size * s, bold=True)) > cw - 100 * s:
        size -= 4                                      # fit long messages inside the card
    d.text((cx + 50 * s, cy + 70 * s), label, font=font(size * s, bold=True), fill=col, anchor="lm")
    d.text((cx + 50 * s, cy + 150 * s), "P%s  %s" % (info.get("preset", "?"), info.get("name", "")),
           font=font(34 * s, bold=True), fill=TXT)
    d.text((cx + 50 * s, cy + 205 * s), "%.3f MHz    %s kHz" % (info.get("freq", 0), info.get("bw", "?")),
           font=font(34 * s), fill=INFO)
    if st.get("mod", "-") != "-":
        d.text((cx + 50 * s, cy + 255 * s), "%s %s   GI %s   %s" % (
            st.get("mod"), st.get("fec"), st.get("gi"), st.get("fft")), font=font(28 * s), fill=TXT2)
    # meters
    m = margin(st)
    mc = margin_colour(m)
    try:
        cn = float(st.get("cnr", "-999"))
    except ValueError:
        cn = -999
    try:
        sig = float(st.get("sig", "-999"))
    except ValueError:
        sig = -999
    y = cy + 320 * s
    rows = [("Signal", fnum(st, "sig", "%.1f dBm"), (sig + 100) / 60.0 if sig > -200 else 0, INFO),
            ("C/N", fnum(st, "cnr", "%.1f dB"), cn / 35.0 if cn > -100 else 0, mc),
            ("Margin", ("%+.1f dB" % m) if m is not None else "--", (m / 20.0) if m is not None else 0, mc)]
    for name, val, frac, c in rows:
        d.text((cx + 50 * s, y), name, font=font(26 * s), fill=MUTED, anchor="lm")
        d.text((cx + 210 * s, y), val, font=font(28 * s, bold=True), fill=c, anchor="lm")
        bar(d, cx + 430 * s, y - 9 * s, cw - 480 * s, 18 * s, frac, c, s)
        y += 62 * s
    warn = info.get("warning")
    if warn:
        d.text((cx + 50 * s, cy + ch - 30 * s), warn, font=font(24 * s), fill=AMBER, anchor="lm")
    upd = info.get("update")
    if upd:
        h = 56 * s
        d.rounded_rectangle([cx, cy + ch + 24 * s, cx + cw, cy + ch + 24 * s + h], radius=12 * s,
                            fill=BG_RAISED, outline=ACCENT, width=2)
        d.text((cx + 30 * s, cy + ch + 24 * s + h / 2), upd, font=font(26 * s, bold=True),
               fill=INFO, anchor="lm")

    # presets
    px, py, pw = 1200 * s, 150 * s, W - 1260 * s
    d.text((px, py), "PRESETS", font=font(24 * s, bold=True), fill=MUTED)
    py += 50 * s
    cur = str(info.get("preset", ""))
    for key, p in presets:
        h = 52 * s
        on = (str(key) == cur)
        d.rounded_rectangle([px, py, px + pw, py + h], radius=12 * s,
                            fill=(BG_HI if on else BG_PANEL), outline=(ACCENT if on else BG_HI), width=2)
        d.text((px + 20 * s, py + h / 2), str(key), font=font(26 * s, bold=True),
               fill=(ACCENT if on else FAINT), anchor="lm")
        d.text((px + 60 * s, py + h / 2), p.get("name", ""), font=font(24 * s, bold=on),
               fill=(TXT if on else TXT2), anchor="lm")
        d.text((px + pw - 20 * s, py + h / 2), "%.3f  %s" % (p["freq"], p["bw"]),
               font=font(20 * s), fill=MUTED, anchor="rm")
        py += h + 10 * s
        if py > 900 * s:
            break

    # footer
    d.rectangle([0, H - 70 * s, W, H], fill=BG_PANEL)
    d.text((60 * s, H - 35 * s),
           "Remote:  \u25b2 \u25bc  preset     0-9  direct     RED  tune     OK  info     BACK  hide",
           font=font(24 * s), fill=MUTED, anchor="lm")
    return img
