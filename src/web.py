"""web.py - tiny control server for the Lynx DVB-T2 Receiver.

No framework and no dependencies: Python's own http.server on a low-priority
thread, idle until someone connects. One small page, plus plain URLs that any
automation (Home Assistant, Node-RED, curl) can call as webhooks.
G8YTZ, GPLv3.
"""
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Lynx DVB-T2 Receiver</title><style>
body{background:#0a0d12;color:#fff;font:16px system-ui,sans-serif;margin:0;padding:18px;max-width:720px}
h1{font-size:22px;margin:0 0 14px}h1 b{color:#fff}h1 span{color:#d7dde5;font-weight:400}
.card{background:#1a2029;border:1px solid #232a35;border-radius:12px;padding:14px;margin-bottom:14px}
.row{display:flex;justify-content:space-between;padding:3px 0;color:#9aa7b8}.row b{color:#fff;font-weight:600}
#state{font-size:26px;font-weight:700}
button{background:#232a35;color:#fff;border:1px solid #2f3846;border-radius:9px;
padding:10px 14px;margin:3px;font-size:15px;cursor:pointer}
button:hover{background:#2f3846}button.on{border-color:#007aff;color:#4fc3f7}
input,select{background:#0f141b;color:#fff;border:1px solid #2f3846;border-radius:9px;padding:9px;font-size:15px}
small{color:#6b7684}
</style></head><body>
<h1><b>Lynx</b> <span>DVB-T2 Receiver</span></h1>
<div class=card><div id=state>...</div><div id=info></div></div>
<div class=card><div id=presets></div></div>
<div class=card>
 <div>Tune: <input id=f size=9 placeholder="436.000"> MHz
 <select id=b><option>1350</option><option selected>1700</option><option>2000</option></select> kHz
 <button onclick="go('/tune?freq='+f.value+'&bw='+b.value)">Tune</button></div>
 <div style="margin-top:8px">Store in preset
 <select id=s>1 2 3 4 5 6 7 8 9</select>
 <input id=n size=14 placeholder="name (optional)">
 <button onclick="go('/save?slot='+s.value+'&freq='+f.value+'&bw='+b.value+'&name='+encodeURIComponent(n.value))">Save</button>
 <button onclick="if(confirm('Delete preset '+s.value+'?'))go('/delete?slot='+s.value)">Delete</button></div>
</div>
<div class=card><button onclick="go('/osd')">OSD</button>
<button onclick="go('/prev')">Prev</button><button onclick="go('/next')">Next</button>
<button onclick="go('/update')">Check for updates</button>
<div id=svcs style="margin-top:8px"></div>
<div><small id=ver></small></div></div>
<script>
var s=document.getElementById('s');s.innerHTML='';
for(var i=1;i<10;i++){var o=document.createElement('option');o.text=i;s.add(o)}
function go(u){fetch(u).then(function(){setTimeout(load,400)})}
function load(){fetch('/status').then(function(r){return r.json()}).then(function(d){
 var t=d.tuner||{},i=d.info||{};
 document.getElementById('state').textContent=
   {LOCK:'LOCKED',SYNC:'SEARCHING',NOSIG:'NO SIGNAL'}[t.state]||t.state||'--';
 document.getElementById('state').style.color=
   t.state=='LOCK'?'#39ff6a':(t.state=='SYNC'?'#e8a33d':'#ff5a5a');
 var h='';function row(k,v){h+='<div class=row><span>'+k+'</span><b>'+v+'</b></div>'}
 row('Preset','P'+d.preset+'  '+(i.name||''));
 row('Frequency',(i.freq||0).toFixed(3)+' MHz   '+(i.bw||'')+' kHz');
 if(i.callsign)row('Callsign',i.callsign+(i.provider?'  ('+i.provider+')':''));
 if(t.mod&&t.mod!='-')row('Mode',t.mod+' '+t.fec+'  GI '+t.gi+'  '+t.fft);
 row('Signal',(t.sig||'--')+' dBm');row('C/N',(t.cnr||'--')+' dB');
 row('TS rate',(t.rate||'--')+' Mb/s');
 if(i.video)row('Video',i.video);if(i.audio)row('Audio',i.audio);
 document.getElementById('info').innerHTML=h;
 var p='';(d.presets||[]).forEach(function(x){
  p+='<button class="'+(x.key==d.preset?'on':'')+'" onclick="go(\\'/preset/'+x.key+'\\')">'
   +x.key+'  '+x.name+'<br><small>'+x.freq.toFixed(3)+'  '+x.bw+'</small></button>'});
 document.getElementById('presets').innerHTML=p||'<small>no presets</small>';
 var sv='';(i.services||[]).forEach(function(x){
  sv+='<button class="'+(x[0]==i.service||((!i.service)&&x===(i.services||[])[0])?'on':'')+
      '" onclick="go(\'/service?n='+x[0]+'\')">'+(x[1]||('service '+x[0]))+'</button>'});
 document.getElementById('svcs').innerHTML=(i.services&&i.services.length>1)?('Services in this multiplex: '+sv):'';
 document.getElementById('ver').textContent=d.version+(d.update?('   -   update '+d.update+' available'):'');
})}
load();setInterval(load,2000);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    rx = None            # set by serve()

    def log_message(self, *a):
        pass             # don't log to stderr

    def _send(self, body, ctype="application/json", code=200):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path.rstrip("/") or "/"
        r = self.rx
        try:
            if p == "/":
                return self._send(PAGE, "text/html; charset=utf-8")
            if p == "/status":
                return self._send(json.dumps(r.web_status(), indent=1))
            if p.startswith("/preset/"):
                return self._send(json.dumps(r.web_cmd("preset", slot=p.rsplit("/", 1)[1])))
            if p in ("/next", "/prev", "/osd", "/back", "/update", "/reload"):
                return self._send(json.dumps(r.web_cmd(p[1:])))
            if p in ("/tune", "/save", "/delete", "/service"):
                return self._send(json.dumps(r.web_cmd(p[1:], **q)))
            self._send(json.dumps({"error": "unknown"}), code=404)
        except Exception as e:                       # never take the receiver down
            self._send(json.dumps({"error": str(e)}), code=500)


def serve(rx, port, log=print):
    """Start the server on a low-priority daemon thread. Returns it, or None."""
    try:
        _Handler.rx = rx
        srv = ThreadingHTTPServer(("", int(port)), _Handler)
        srv.daemon_threads = True
    except OSError as e:
        log("web: %s" % e)
        return None

    def run():
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except (OSError, AttributeError):
            pass
        srv.serve_forever(poll_interval=0.5)
    threading.Thread(target=run, daemon=True).start()
    log("web: http://<this-pi>:%s/" % port)
    return srv
