"""update.py - check GitHub for a newer release and install it.

The receiver is an appliance, so it behaves like a TV: it looks for a new
release at boot and once a day, shows a note on the status page, and (unless
switched off) installs it when nothing is being received. Uses git if the
receiver was cloned, otherwise the GitHub releases API.
Lynx DVB-T2 Receiver, G8YTZ, GPLv3.
"""
import json
import os
import re
import subprocess
import urllib.request

REPO = "G8YTZ/Lynx-DVB-T2-Rx"
API = "https://api.github.com/repos/%s/releases/latest" % REPO


def _ver(s):
    """'v1.6' / 't2rx 1.6' -> (1, 6); unparseable -> (0,)"""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", s or "")
    return tuple(int(x) for x in m.groups() if x is not None) if m else (0,)


def _run(args, cwd, timeout=300):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def find_checkout():
    """The git checkout this receiver was installed from, or None."""
    for d in ("/home/pi/Lynx-DVB-T2-Rx", "/opt/t2rx/src", os.path.expanduser("~/Lynx-DVB-T2-Rx")):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
    return None


def check(current, timeout=15):
    """Latest release tag if it is newer than `current`, else None."""
    try:
        req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "lynx-dvbt2-rx"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            tag = json.load(r).get("tag_name", "")
    except Exception:
        return None
    return tag if _ver(tag) > _ver(current) else None


def install(tag, log=print):
    """Fetch and install `tag`. Returns True if the receiver should restart."""
    d = find_checkout()
    if not d:
        log("update: no git checkout found - install manually")
        return False
    try:
        for args in (["git", "fetch", "--tags", "--quiet"],
                     ["git", "checkout", "--quiet", tag]):
            r = _run(args, d)
            if r.returncode:
                log("update: %s failed: %s" % (args[1], (r.stderr or "").strip()[:200]))
                return False
        r = _run(["./install.sh", "--no-boot"], d, timeout=1800)
        if r.returncode:
            log("update: install.sh failed: %s" % (r.stderr or r.stdout or "").strip()[-200:])
            return False
    except (OSError, subprocess.TimeoutExpired) as e:
        log("update: %s" % e)
        return False
    log("update: installed %s" % tag)
    return True
