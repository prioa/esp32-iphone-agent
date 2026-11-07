#!/usr/bin/env python3
"""Small web page for watching eatventure_bot.py: the live picture, and a timeline of what it did, why and when.

usage: python3 -I eatventure_ui.py [--port 8765]     then open http://127.0.0.1:8765

Reads the state dir only (live.png, eatventure_events/events.jsonl and the small pictures the bot saves). It never taps.
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eatventure_map import RestaurantMap

STATE = os.path.expanduser(os.environ.get("ESP32_DRIVE_DIR", "~/.cache/esp32agent-drive"))
EV = os.path.join(STATE, "eatventure_events")
CONTROL = os.path.join(STATE, "eatventure_control.json")
STATUS = os.path.join(STATE, "eatventure_status.json")
LOCK = threading.Lock()
PHASES = ("stations", "menu", "crates", "fly", "map", "abort")
SCROLLS = ("d200", "d300", "d500", "d800", "f1", "f2", "f3", "f4", "f5", "alt1", "alt2", "e140", "e160", "e180", "c3a", "c3b", "c6a", "c6b", "c10a", "wake", "tap", "alt3", "dbl", "left", "mid")

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Eatventure bot</title>
<style>
:root{--bg:#f6f5f2;--fg:#1d1d1b;--mut:#6b6a66;--card:#fff;--line:#e3e1db;--acc:#1b6ef3;--accbg:#e8f0fe;--ok:#1a7f4b;--warn:#b45309;--bad:#b42318}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe7;--mut:#9a9890;--card:#1f1f1d;--line:#32312e;--acc:#6aa3ff;--accbg:#1c2a44;--ok:#4cc38a;--warn:#e0a458;--bad:#ff7b72}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
.banner{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px}
.now{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.dot{width:12px;height:12px;border-radius:50%;background:var(--mut);flex:none}
.busy .dot{background:var(--ok);animation:p 1.2s infinite}.stopped .dot{background:var(--bad)}.dead .dot{background:var(--warn)}
@keyframes p{50%{box-shadow:0 0 0 7px transparent;opacity:.5}0%{box-shadow:0 0 0 0 var(--ok)}}
.big{font-size:18px;font-weight:650}.sub{color:var(--mut)}
.steps{display:flex;gap:8px;margin-left:auto;align-items:center}
.chip{padding:5px 12px;border-radius:999px;border:1px solid var(--line);color:var(--mut);white-space:nowrap}
.chip.act{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.chip.nxt{border-color:var(--acc);color:var(--acc)}
.prog{height:4px;background:var(--line);border-radius:2px;margin-top:8px;overflow:hidden}.prog i{display:block;height:100%;background:var(--acc);width:0;transition:width 1s linear}
.stats{color:var(--mut);margin-top:6px;font-size:13px}.stats b{color:var(--fg)}
main{display:grid;grid-template-columns:260px 1fr;gap:16px;padding:16px;align-items:start}
@media(max-width:700px){main{grid-template-columns:1fr}}
.live{position:sticky;top:110px;max-height:calc(100vh - 126px);overflow-y:auto}
@media(max-width:700px){.live{position:static;max-height:none}}
.mapbox{margin-top:10px}.mapstat{color:var(--mut);font-size:13px;margin-bottom:4px}
#live{max-height:36vh;width:auto;max-width:100%;margin:0 auto}
.mapin{position:relative;width:fit-content;margin:0 auto}.mapin img{border-radius:6px;width:auto;max-width:100%;max-height:calc(100vh - 36vh - 240px)}
@media(max-width:700px){#live{max-height:none;width:100%}.mapin{width:100%}.mapin img{width:100%;max-height:none}}
.mapin i{position:absolute;left:0;right:0;border:2px solid var(--acc);border-radius:4px;display:none;pointer-events:none}
.mapin b{position:absolute;transform:translate(-50%,-50%);font:600 9px/1.1 system-ui,sans-serif;white-space:nowrap;padding:2px 4px;border-radius:5px;color:#fff;background:#b45309;pointer-events:none}
.mapin b.crate{background:#7c4a1e}.mapin b.slot{background:#fff;color:#1d1d1b;border:1px dashed #1d1d1b}
.mapin b.arrow{background:#b42318}.mapin b.max{background:#1a7f4b}
.maplegend{display:flex;flex-wrap:wrap;gap:4px 8px;margin-top:6px;font-size:11px;color:var(--mut)}.maplegend u{text-decoration:none;display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:3px}
.live img{width:100%;border-radius:10px;border:1px solid var(--line);display:block}.live small{color:var(--mut)}
.ctl{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:12px;display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
.ctl button{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--fg);cursor:pointer}
.ctl button:hover{border-color:var(--acc)}.ctl button.on{background:var(--acc);color:#fff;border-color:var(--acc)}.ctl button.stop{color:var(--bad)}
.ctl input{width:64px;font:inherit;padding:5px 6px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
.sep{flex:1 1 100%;height:0}.bar{display:flex;gap:14px;margin-bottom:10px;color:var(--mut)}
details.grp{margin-bottom:12px;border:1px solid var(--line);border-radius:12px;background:var(--card);overflow:hidden}
details.grp.live-grp{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc)}
details.grp>summary{list-style:none;cursor:pointer;padding:10px 14px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
details.grp>summary::-webkit-details-marker{display:none}details.grp>summary::before{content:"▸";color:var(--mut)}details.grp[open]>summary::before{content:"▾"}
.gname{font-weight:650}.gmeta{color:var(--mut)}.badge{font-size:12px;padding:1px 8px;border-radius:999px;background:var(--accbg);color:var(--acc);font-weight:600}
.badge.run{background:var(--ok);color:#fff}
.rows{padding:0 10px 8px}
.row{display:grid;grid-template-columns:78px 1fr;gap:2px 12px;border-top:1px solid var(--line);padding:7px 4px;cursor:pointer}
.row.log{color:var(--mut);cursor:default;padding:3px 4px;border-top:0}
.row.latest{background:var(--accbg);border-left:3px solid var(--acc);border-radius:6px;padding-left:8px}
.row.old{opacity:.78}
.t{color:var(--mut);font-variant-numeric:tabular-nums}.t i{display:block;font-style:normal;font-size:12px}
.a{font-weight:600}.why{color:var(--mut)}
.stop .a{color:var(--bad)}.k-crate .a{color:var(--warn)}.k-station .a{color:var(--ok)}.k-menu .a,.k-fly .a{color:var(--acc)}
.pic{grid-column:2;display:none;margin-top:6px}.row.open .pic{display:block}.pic img{width:240px;border-radius:8px;border:1px solid var(--line)}
</style>
<div class=banner id=banner><div class=now><span class=dot></span><div><div class=big id=big>…</div><div class=sub id=sub></div></div>
<div class=steps id=steps></div></div><div class=prog><i id=prog></i></div><div class=stats id=stats></div></div>
<main><div class=live><img id=live src=/stream.mjpg><small id=fps>live picture</small>
<div class=mapbox><div class=mapstat id=mapstat>Karte: noch keine</div><div class=mapin id=mapin><img id=mapimg alt="" hidden><i id=mapview></i><span id=mapitems></span></div>
<div class=maplegend><span><u style="background:#b42318"></u>Station, Upgrade möglich</span><span><u style="background:#b45309"></u>Station</span><span><u style="background:#1a7f4b"></u>Station auf Max</span><span><u style="background:#7c4a1e"></u>Kiste</span><span><u style="background:#fff;border:1px dashed #1d1d1b"></u>freier Platz</span></div></div></div>
<div><div class=ctl>
<span><button id=mAuto onclick="mode('auto')">Auto</button> <button id=mMan onclick="mode('manual')">Manuell</button></span>
<span><button onclick="cmd('stations')">Stationen</button> <button onclick="cmd('menu')">Menü (Kisten falls nötig)</button>
<button onclick="cmd('crates')">Kisten</button> <button onclick="cmd('fly')">Fliegen prüfen</button> <button onclick="cmd('map')">Karte erstellen/aktualisieren</button> <button class=stop onclick="cmd('abort')">Abbrechen</button></span><i class=sep></i>
<label>Stationen alle <input id=xs type=number min=1 step=1 onchange="xs()"> s</label>
<label>Menü <input id=xm type=number min=1 step=1 onchange="xs()"> s danach</label></div>
<div class=ctl id=scrolls><b>Scroll-Test</b></div>
<div class=bar><label><input type=checkbox id=logs checked> Ergebnisse</label><span id=n></span><span>neueste oben</span></div><div id=list></div></div></main>
<script>
let evs=[],last=0,st={},ctl={};const open={};
const $=id=>document.getElementById(id),PH={stations:'Stationen',menu:'Menü',crates:'Kisten',fly:'Fliegen',map:'Karte'};
const cls=a=>/crate/.test(a)?'k-crate':/station/.test(a)?'k-station':/menu|fly|build/.test(a)?'k-menu':'';
const hm=t=>new Date(t*1000).toLocaleTimeString('de-DE'),now=()=>Date.now()/1000;
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const dur=s=>s<90?Math.round(s)+' s':Math.floor(s/60)+' min '+Math.round(s%60)+' s';
const ago=s=>s<2?'jetzt':s<90?'vor '+Math.round(s)+' s':'vor '+Math.round(s/60)+' min';
function groups(){const g=[];
  for(const e of evs){const m=e.kind==='decide'&&/^(phase: |manual: )/.test(e.text);
    if(m||!g.length){const key=m?(e.text.startsWith('phase: ')?e.text.slice(7):e.text.slice(8)):'start';
      const k=/stations/.test(key)?'stations':/menu|upgrades/.test(key)?'menu':/crate/.test(key)?'crates':/fly/.test(key)?'fly':/map/.test(key)?'map':'';
      g.push({n:e.n,t:e.t,k,name:PH[k]||'Start',auto:/automatic/.test(e.why||''),ev:[]})}
    g[g.length-1].ev.push(e)}
  return g}
function draw(){const g=groups().reverse(),showLogs=$('logs').checked,busy=st.busy&&!st.stopped;let h='',latest=evs.length?evs[evs.length-1].n:0;
  g.forEach((x,i)=>{const first=i===0,end=i===0?(busy?now():x.ev[x.ev.length-1].t):g[i-1].t,live=first&&busy;
    const o=open[x.n]!==undefined?open[x.n]:i<2;
    const rows=x.ev.slice().reverse().filter(e=>e.kind!=='log'||showLogs).map(e=>{const isL=e.n===latest,ago_=ago(now()-e.t);
      if(e.kind==='log')return`<div class="row log${isL?' latest':''}"><div class=t>${hm(e.t)}</div><div>${esc(e.text)}</div></div>`;
      return`<div class="row ${e.kind==='stop'?'stop':cls(e.text)}${isL?' latest':' old'}" onclick="this.classList.toggle('open')"><div class=t>${hm(e.t)}<i>${isL?'<span class=badge>aktuell</span>':ago_}</i></div>
      <div><span class=a>${esc(e.text)}</span><div class=why>${esc(e.why)}</div></div>${e.pic?`<div class=pic><img loading=lazy src=/pic/${e.pic}></div>`:''}</div>`}).join('');
    h+=`<details class="grp${live?' live-grp':''}" ${o?'open':''} ontoggle="open[${x.n}]=this.open"><summary><span class=gname>${x.name}</span>
      <span class=gmeta>${x.auto?'auto':'manuell'} · ab ${hm(x.t)} · ${dur(end-x.t)}</span>${live?'<span class="badge run">läuft</span>':''}</summary><div class=rows>${rows}</div></details>`});
  $('list').innerHTML=h;$('n').textContent=evs.length+' Einträge'}
function banner(){const s=st,c=ctl,t=now();let cls='',big,sub='',steps='';
  if(!s.t){cls='dead';big='Bot nicht gestartet'}
  else if(t-s.t>15){cls='dead';big='Bot antwortet nicht';sub='zuletzt vor '+Math.round(t-s.t)+' s'}
  else if(s.stopped){cls='stopped';big='Gestoppt';sub=esc(s.stopped)}
  else if(s.busy){cls='busy';big='Aktiv: '+(PH[s.busy]||s.busy);sub=(s.auto?'automatisch':'manuell gestartet')+' · seit '+dur(t-s.since)}
  else if(c.mode==='auto'&&s.next){big='Wartet';sub='nächste Phase '+(PH[s.next.phase]||s.next.phase)+' in '+Math.max(0,Math.round(s.next.at-t))+' s'}
  else{big='Wartet auf Befehl';sub='Modus manuell'}
  $('banner').className='banner '+cls;$('big').textContent=big;$('sub').textContent=sub;
  if(c.mode==='auto'){for(const k of['stations','menu']){const a=s.busy===k&&!s.stopped,n=!s.busy&&s.next&&s.next.phase===k;
    steps+=`<span class="chip ${a?'act':n?'nxt':''}">${PH[k]}${a?' · läuft':n?' · in '+Math.max(0,Math.round(s.next.at-t))+' s':''}</span>`}}
  else steps=`<span class="chip ${s.busy?'act':''}">Manuell${s.busy?' · '+(PH[s.busy]||s.busy):''}</span>`;
  $('steps').innerHTML=steps;
  let p=0;if(c.mode==='auto'&&!s.busy&&s.next){const per=s.next.phase==='menu'?c.x_stations:c.x_menu;p=Math.min(100,100*(1-(s.next.at-t)/per))}
  else if(s.busy)p=100;$('prog').style.width=(p||0)+'%';
  const e=evs[evs.length-1],k=(e&&e.counts)||{};
  $('stats').innerHTML=e?`Stationen auf Max <b>${esc(e.maxed)}</b> · Züge <b>${k.turns}</b> · Klicks <b>${k.clicks}</b> · Menü-Käufe <b>${k.menu}</b> · Kisten <b>${k.crates}</b> · Slots <b>${k.slots}</b> · Flüge <b>${k.next}</b>`:'';
  $('mAuto').className=c.mode==='auto'?'on':'';$('mMan').className=c.mode==='manual'?'on':'';
  for(const[id,key]of[['xs','x_stations'],['xm','x_menu']])if(document.activeElement!==$(id)&&c[key])$(id).value=c[key]}
async function poll(){try{const r=await fetch('/api/events?after='+last),d=await r.json();
  for(const e of d){evs.push(e);last=e.n}if(d.length)draw()}catch(_){}}
async function status(){try{const d=await(await fetch('/api/status')).json();st=d.status||{};ctl=d.control||{};banner()}catch(_){}}
const MS={here:'hier',scrolling:'scrollt',covered:'verdeckt',lost:'Position unbekannt',start:'startet','new map':'neue Karte'};
async function mapPoll(){try{const d=await(await fetch('/api/map')).json(),v=$('mapview'),im=$('mapimg');
  if(!d.height){im.hidden=true;v.style.display='none';$('mapitems').innerHTML='';$('mapstat').textContent='Karte: noch keine ('+(MS[d.state]||d.state)+')';return}
  if(im.hidden)mapImg();im.hidden=false;
  $('mapstat').textContent=`Karte: ${d.height} px · ${MS[d.state]||d.state} · oben ${d.top_seen?'✓':'–'} · unten ${d.bottom_seen?'✓':'–'}`;
  if(d.pos===null||d.state==='lost')v.style.display='none';else{v.style.display='block';v.style.top=100*d.pos/d.height+'%';v.style.height=100*d.view/d.height+'%'}
  $('mapitems').innerHTML=d.items.map(i=>{const t=i.kind==='crate'?'Kiste':i.kind==='slot'?'frei':((i.order&&i.order<1e9?'#'+i.order+' ':'')+esc(i.name||'Station')+(i.level?' '+i.level:'')+(i.cost?' · '+esc(i.cost):'')),c=i.kind!=='station'?i.kind:i.max?'max':i.arrow?'arrow':'';
    return`<b class="${c}" style="left:${Math.min(88,Math.max(12,100*i.x/720))}%;top:${100*i.y/d.height}%">${t}</b>`}).join('')}catch(_){}}
function mapImg(){$('mapimg').src='/map_plan.png?t='+Date.now()}
async function post(o){await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});status()}
const mode=m=>post({mode:m}),cmd=c=>post({cmd:c}),xs=()=>post({x_stations:+$('xs').value,x_menu:+$('xm').value});
$('scrolls').innerHTML+=['alt1','alt2','d200','d300','d500','d800','c3a','f3'].map(v=>`<span>${v} <button onclick="cmd('scroll:${v}:up')">↑</button><button onclick="cmd('scroll:${v}:down')">↓</button></span>`).join('');
$('logs').onchange=draw;
setInterval(poll,1500);setInterval(status,1000);setInterval(mapPoll,1000);setInterval(()=>{if(!$('mapimg').hidden)mapImg()},3000);mapPoll();
setInterval(draw,5000);
setInterval(async()=>{try{const d=await(await fetch('/api/fps')).json();$('fps').textContent=`Live-Bild: ${d.fps.toFixed(1)} Bilder/s kommen an`}catch(_){}},2000);
poll();status();
</script>
"""


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Frames:
    """Watches live.png (the newest decoded frame) and keeps it as a JPEG; up to 25 new frames per second are passed on."""

    def __init__(self):
        self.cv = threading.Condition()
        self.jpg, self.n, self.stamps = None, 0, []
        threading.Thread(target=self.watch, daemon=True).start()

    def watch(self):
        path, last = os.path.join(STATE, "live.png"), None
        while True:
            time.sleep(0.02)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if (st.st_mtime_ns, st.st_size) == last:
                continue
            last = (st.st_mtime_ns, st.st_size)
            img = cv2.imread(path)
            if img is None:
                continue  # caught while ffmpeg was replacing it
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                continue
            with self.cv:
                self.jpg, self.n = buf.tobytes(), self.n + 1
                now = time.time()
                self.stamps = [t for t in self.stamps if now - t < 5] + [now]
                self.cv.notify_all()

    def fps(self):
        now = time.time()
        return len([t for t in self.stamps if now - t < 5]) / 5.0


FRAMES = Frames()


class MapWatch:
    """Feeds the restaurant map from the live picture. It only reads pictures, like the rest of this page."""

    def __init__(self):
        self.map = RestaurantMap(STATE)
        threading.Thread(target=self.watch, daemon=True).start()

    def watch(self):
        live, reset, last = os.path.join(STATE, "live.png"), os.path.join(STATE, "map_reset"), None
        live_info = os.path.join(STATE, "map_live.json")
        asked = os.path.getmtime(reset) if os.path.exists(reset) else 0
        while True:
            time.sleep(0.5)
            try:
                if os.path.exists(reset) and os.path.getmtime(reset) != asked:
                    asked = os.path.getmtime(reset)
                    self.map.reset()
                st = os.stat(live)
                if (st.st_mtime_ns, st.st_size) != last:
                    last = (st.st_mtime_ns, st.st_size)
                    img = cv2.imread(live)
                    if img is not None:
                        self.map.feed(img)
                with open(live_info + ".tmp", "w") as f:  # for the bot, which scrolls to stations by it. Also without a
                    json.dump({**self.map.info(), "t": time.time()}, f)  # new picture: they can be 5 s apart
                os.replace(live_info + ".tmp", live_info)
            except (OSError, ValueError, cv2.error) as e:
                print("map:", e, flush=True)


MAP = MapWatch()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "text/plain", b"no")  # no DNS-rebinding host
        u = urlparse(self.path)
        if u.path == "/":
            return self.send(200, "text/html; charset=utf-8", PAGE.encode())
        if u.path == "/stream.mjpg":
            return self.stream()
        if u.path == "/api/status":
            return self.send(200, "application/json", json.dumps({"status": read_json(STATUS), "control": read_json(CONTROL)}).encode())
        if u.path == "/api/fps":
            return self.send(200, "application/json", json.dumps({"fps": FRAMES.fps()}).encode())
        if u.path == "/api/map":
            return self.send(200, "application/json", json.dumps(MAP.map.info()).encode())
        if u.path == "/map_plan.png":
            return self.file(os.path.join(STATE, "map_plan.png"), "image/png")
        if u.path == "/live.png":
            return self.file(os.path.join(STATE, "live.png"), "image/png")
        if u.path.startswith("/pic/"):
            name = os.path.basename(u.path)
            return self.file(os.path.join(EV, name), "image/jpeg")
        if u.path == "/api/events":
            after = int(parse_qs(u.query).get("after", ["0"])[0])
            out = []
            try:
                with open(os.path.join(EV, "events.jsonl")) as f:
                    for n, line in enumerate(f, 1):  # the line number: older runs of the bot started their own count at 1
                        if n <= after:
                            continue
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue  # a half-written last line: it comes again with the next poll
                        e["n"] = n
                        out.append(e)
            except OSError:
                pass
            return self.send(200, "application/json", json.dumps(out[-500:]).encode())
        self.send(404, "text/plain", b"not found")

    def do_POST(self):
        if not self.host_ok() or urlparse(self.path).path != "/api/control" \
                or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.send(403, "text/plain", b"no")  # no other page, no rebinding host, only our own JSON
        try:
            o = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        except ValueError:
            return self.send(400, "text/plain", b"bad json")
        with LOCK:
            c = read_json(CONTROL)
            c.setdefault("cmds", [])
            if o.get("mode") in ("auto", "manual"):
                c["mode"] = o["mode"]
            for k in ("x_stations", "x_menu"):
                if isinstance(o.get(k), (int, float)) and 1 <= o[k] <= 3600:
                    c[k] = o[k]
            cmd = o.get("cmd")
            ok = cmd in PHASES or (isinstance(cmd, str) and cmd.startswith("scroll:") and cmd.split(":")[1:2] and cmd.split(":")[1] in SCROLLS and cmd.endswith((":up", ":down")))
            if ok:
                c["cmds"] = c["cmds"][-20:] + [{"id": max([m["id"] for m in c["cmds"]] + [0]) + 1, "cmd": cmd}]
            tmp = CONTROL + ".tmp"
            with open(tmp, "w") as f:
                json.dump(c, f)
            os.replace(tmp, CONTROL)
        self.send(200, "application/json", b"{}")

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        seen = 0
        try:
            while True:
                with FRAMES.cv:
                    FRAMES.cv.wait_for(lambda: FRAMES.n != seen, timeout=5)
                    jpg, seen = FRAMES.jpg, FRAMES.n
                if jpg is None:
                    continue
                self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
                time.sleep(0.04)  # at most 25 per second
        except (BrokenPipeError, ConnectionResetError):
            pass

    def file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                self.send(200, ctype, f.read())
        except OSError:
            self.send(404, "text/plain", b"no file")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
