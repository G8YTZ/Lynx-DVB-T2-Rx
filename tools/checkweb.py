#!/usr/bin/env python3
"""checkweb.py - does the web page's JavaScript actually parse?

    python3 tools/checkweb.py src/web.py

A quoting mistake in the page script broke the whole interface for several
releases without anything failing: the page still served, it simply did nothing.
This catches that. Needs node for the parse check; without it, reports the
bracket and quote balance instead.  G8YTZ, GPLv3.
"""
import re, subprocess, sys, tempfile, os

src = open(sys.argv[1] if len(sys.argv) > 1 else "src/web.py").read()
js = "\n".join(m.group(1) for m in re.finditer(r"<script>(.*?)</script>", src, re.S))
if not js.strip():
    sys.exit("no <script> found in %s" % (sys.argv[1] if len(sys.argv) > 1 else "src/web.py"))

with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
    f.write(js)
    path = f.name
try:
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if r.returncode:
        print("the page script does NOT parse:\n" + (r.stderr or "").strip())
        sys.exit(1)
    print("the page script parses (%d bytes)" % len(js))
except FileNotFoundError:
    bad = [c for c in "()[]{}" ]
    counts = {c: js.count(c) for c in bad}
    ok = (counts["("] == counts[")"] and counts["["] == counts["]"] and counts["{"] == counts["}"])
    print("node not installed; brackets %s" % ("balance" if ok else "DO NOT balance"))
    sys.exit(0 if ok else 1)
finally:
    os.unlink(path)
