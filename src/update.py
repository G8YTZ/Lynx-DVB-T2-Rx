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
import time
import urllib.error
import urllib.request

REPO = "G8YTZ/Lynx-DVB-T2-Rx"
API_RELEASE = "https://api.github.com/repos/%s/releases/latest" % REPO
API_TAGS = "https://api.github.com/repos/%s/tags?per_page=30" % REPO


def _ver(s):
    """'v1.6' / 't2rx 1.6' -> (1, 6); unparseable -> (0,)"""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", s or "")
    return tuple(int(x) for x in m.groups() if x is not None) if m else (0,)


def _run(args, cwd, timeout=300):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if args and args[0] == "git":
        # The receiver runs as root while the checkout belongs to the user, so git
        # refuses it ("dubious ownership") unless the path is marked safe. -c is
        # certain; writing it to root's global config depends on $HOME being root's.
        args = ["git", "-c", "safe.directory=%s" % cwd] + list(args[1:])
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def _allow_dir(d, log):
    """Also record it in root's git config, so install.sh's own git calls work."""
    try:
        r = _run(["git", "config", "--global", "--get-all", "safe.directory"], d, timeout=20)
        if d not in (r.stdout or "").split():
            _run(["git", "config", "--global", "--add", "safe.directory", d], d, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        log("update: %s" % e)


def find_checkout():
    """The git checkout this receiver was installed from, or None."""
    for d in ("/home/pi/Lynx-DVB-T2-Rx", "/opt/t2rx/src", os.path.expanduser("~/Lynx-DVB-T2-Rx")):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
    return None


STATE = "/var/lib/t2rx/update.json"


def _state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE + ".tmp", "w") as f:
            json.dump(d, f)
        os.replace(STATE + ".tmp", STATE)
    except OSError:
        pass


def _get(url, timeout, etag=None):
    """Returns (data, etag, status). A conditional request that comes back 304 -
    nothing has changed - does not count against GitHub's hourly limit, so the
    receiver can look often without using its sixty requests up."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "lynx-dvbt2-rx"})
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r), r.headers.get("ETag"), 200
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag, 304
        if e.code == 403 and e.headers.get("x-ratelimit-remaining") == "0":
            try:
                until = int(e.headers.get("x-ratelimit-reset", "0"))
            except ValueError:
                until = 0
            raise RateLimited(until)
        raise


class RateLimited(Exception):
    def __init__(self, until):
        super().__init__("GitHub hourly request limit reached")
        self.until = until


def latest(timeout=15, log=None):
    """Newest version tag on GitHub, or None. Uses one conditional request; the
    releases API is only consulted if the tag list is unavailable."""
    st = _state()
    now = time.time()
    if now < st.get("blocked_until", 0):
        if log:
            log("update: waiting for GitHub's hourly limit to reset (%d min)"
                % max(1, int((st["blocked_until"] - now) / 60)))
        return st.get("tag")
    try:
        data, etag, code = _get(API_TAGS, timeout, st.get("etag"))
    except RateLimited as e:
        st["blocked_until"] = e.until or (now + 3600)
        _save_state(st)
        if log:
            log("update: GitHub hourly limit reached - trying again later")
        return st.get("tag")
    except Exception as e:
        if log:
            log("update: check failed (%s)" % str(e)[:80])
        return st.get("tag")
    if code == 304:
        return st.get("tag")                       # unchanged since we last looked
    best = ""
    for t in data or []:
        name = t.get("name", "")
        if _ver(name) > _ver(best):
            best = name
    st.update(etag=etag, tag=best or None, blocked_until=0)
    _save_state(st)
    return best or None


def check(current, timeout=15, log=None):
    """The newest tag on GitHub if it is newer than `current`, else None."""
    tag = latest(timeout, log)
    return tag if tag and _ver(tag) > _ver(current) else None


def install(tag, log=print, progress=None):
    """Fetch and install `tag`. Returns True if the receiver should restart.
    progress(text) is called as each step starts, for the screen."""
    def step(t):
        if progress:
            progress(t)
    d = find_checkout()
    if not d:
        log("update: no git checkout found - install manually")
        return False
    _allow_dir(d, log)
    owner = None
    try:
        st = os.stat(d)
        owner = (st.st_uid, st.st_gid)
    except OSError:
        pass
    try:
        for args, what in ((["git", "fetch", "--tags", "--force", "--quiet"], "Downloading"),
                           (["git", "checkout", "--quiet", "--force", tag], "Unpacking")):
            step(what)
            r = _run(args, d)
            if r.returncode:
                log("update: %s failed: %s" % (args[1], (r.stderr or "").strip()[:200]))
                return False
        step("Installing - this takes a minute")
        r = _run(["./install.sh", "--no-boot"], d, timeout=1800)
        if r.returncode:
            log("update: install.sh failed: %s" % (r.stderr or r.stdout or "").strip()[-200:])
            return False
    except (OSError, subprocess.TimeoutExpired) as e:
        log("update: %s" % e)
        return False
    if owner and owner[0] != 0:
        # we run as root; leave the checkout owned by whoever it belonged to, or
        # git refuses to work there as that user afterwards
        _run(["chown", "-R", "%d:%d" % owner, d], d, timeout=120)
    step("Restarting")
    log("update: installed %s" % tag)
    return True
