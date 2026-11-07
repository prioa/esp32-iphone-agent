#!/usr/bin/env python3
"""Local web page to drive the iPhone: connection status, session steps, live picture, commands, AI tasks.

usage: python3 -I hardware/tools/drive/webui.py [--port 8770]     then open http://127.0.0.1:8770

AI tasks run on your Claude Code login (`claude` in PATH); everything else works without it. See README.md.
"""
import argparse
import collections
import glob
import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive
import webui_agent

STATE = drive.STATE
LOG = os.path.join(STATE, "webui_log.jsonl")
BOT_STATUS = os.path.join(STATE, "eatventure_status.json")
REC_LOG = os.path.join(STATE, "rec.log")
PAGE = os.path.join(HERE, "webui.html")
FRAME_POLL = 0.03  # s between looks at live.png while a page waits for the next frame
FRAME_WAIT = 20    # s a page waits before it asks again
POLL_S = 2.0
FRAME_W, FRAME_H = 720, 2000  # the mirrored frame is 720 wide; its height depends on the phone
CONFIRM_RAW = {"unpair"}  # board commands that undo the setup: the page must have asked first
BLOCKED_RAW = {"net"}  # takes the Wi-Fi password, which must not reach the log: use `drive.py net-sta`
BOT_RE = re.compile(r"\b(eatventure_bot|eatventure_up|chess_\w+)\.py\b")  # scripts that use the serial port


class Invalid(ValueError):
    """A command the server refuses: the message is for the page."""


def scrub(text):
    """No Wi-Fi password and no video token on the page or in the log."""
    return re.sub(r'("(?:pass|vtok)"\s*:\s*)"(?:[^"\\]|\\.)*"', r'\1"***"', str(text))


def _num(v, name, low, high):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
        raise Invalid(f"{name} muss eine Zahl von {low} bis {high} sein")
    return v


def check_text(text):
    if not isinstance(text, str) or not text:
        raise Invalid("Kein Text angegeben")
    if len(text) > 512:
        raise Invalid("Text ist länger als 512 Zeichen: bitte in Teilen tippen")
    if any(ord(c) < 32 or ord(c) > 126 for c in text):
        raise Invalid("Nur ASCII ohne Zeilenumbruch: Umlaute, ß und Emoji gehen über die U.S.-Tastatur nicht "
                      "(umschreiben, z. B. ae, oe, ue, ss; Enter ist eine eigene Taste)")
    return text


def validate(kind, a):
    """The cleaned arguments of a command, or Invalid. Everything that reaches the board passes here."""
    if not isinstance(a, dict):
        raise Invalid("Ungültige Angaben")
    if kind == "tap":
        return {"x": _num(a.get("x"), "x", 0, FRAME_W), "y": _num(a.get("y"), "y", 0, FRAME_H)}
    if kind == "drag":
        return {"x1": _num(a.get("x1"), "x1", 0, FRAME_W), "y1": _num(a.get("y1"), "y1", 0, FRAME_H),
                "x2": _num(a.get("x2"), "x2", 0, FRAME_W), "y2": _num(a.get("y2"), "y2", 0, FRAME_H)}
    if kind == "flick":
        n = a.get("n", 1)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 10:
            raise Invalid("n muss 1 bis 10 sein")
        dy = a.get("dy")
        if isinstance(dy, bool) or not isinstance(dy, int) or not -600 <= dy <= 600 or not dy:
            raise Invalid("dy muss eine ganze Zahl von -600 bis 600 sein, nicht 0")
        return {"x": _num(a.get("x"), "x", 0, FRAME_W), "y": _num(a.get("y"), "y", 0, FRAME_H), "dy": dy, "n": n}
    if kind == "type":
        return {"text": check_text(a.get("text"))}
    if kind == "key":
        combo = a.get("combo")
        if not isinstance(combo, str) or not re.fullmatch(r"[A-Za-z0-9_+-]{1,32}", combo):
            raise Invalid("Ungültige Taste, Beispiele: enter, backspace, cmd+a")
        return {"combo": combo}
    if kind in ("homescreen", "info"):
        return {}
    if kind == "raw":
        line = a.get("line")
        if not isinstance(line, str) or not line.strip() or len(line) > 600:
            raise Invalid("Kein Befehl angegeben")
        line = line.strip()
        if any(ord(c) < 32 for c in line):
            raise Invalid("Ein Befehl muss eine einzige Zeile sein")
        word = line.split()[0].lower()
        if word in BLOCKED_RAW:
            raise Invalid("`net` geht hier nicht, weil das WLAN-Passwort im Log landen würde: drive.py net-sta nutzen")
        if word in CONFIRM_RAW and a.get("confirm") is not True:
            raise Invalid(f"`{word}` muss bestätigt werden")
        if word == "type":
            check_text(line[5:])
        return {"line": line}
    raise Invalid("Unbekannter Befehl")


def describe(kind, a):
    if kind == "tap":
        return f"tap {a['x']:.0f} {a['y']:.0f}"
    if kind == "drag":
        return f"drag {a['x1']:.0f} {a['y1']:.0f} -> {a['x2']:.0f} {a['y2']:.0f}"
    if kind == "flick":
        return f"flick {a['x']:.0f} {a['y']:.0f} dy {a['dy']} x{a['n']}"
    if kind == "type":
        return f"type {a['text']}"
    if kind == "key":
        return f"key {a['combo']}"
    if kind == "raw":
        return a["line"]
    return kind


def parse_etime(s):
    """ps's elapsed time [[dd-]hh:]mm:ss in seconds."""
    days, _, rest = s.strip().rpartition("-")
    parts = [int(p) for p in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return int(days or 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def processes():
    """The running drive scripts: {"rec": [...], "scripts": [...]}, each {pid, name, started}. Shell wrappers don't count."""
    out = {"rec": [], "scripts": []}
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    now = time.time()
    for line in ps.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not os.path.basename(parts[2].split()[0]).lower().startswith("python"):
            continue
        try:
            p = {"pid": int(parts[0]), "started": now - parse_etime(parts[1])}
        except ValueError:
            continue
        if re.search(r"drive\.py rec(\s|$)", parts[2]):
            out["rec"].append(p)
        else:
            m = BOT_RE.search(parts[2])
            if m:
                out["scripts"].append({**p, "name": m.group(0)})
    return out


def bot_state(procs, drive_mtime, status=None, now=None):
    """What the running scripts mean for us. `old`: started before drive.py got its port lock, so it does not take it."""
    now = now or time.time()
    scripts = procs["scripts"]
    bot = {"running": bool(scripts), "names": sorted({s["name"] for s in scripts}), "old": False, "busy": None,
           "auto": False, "blocked": None}
    if not scripts:
        return bot
    bot["old"] = any(s["started"] < drive_mtime for s in scripts)
    if any(s["name"] == "eatventure_bot.py" for s in scripts) and status:
        bot["busy"], bot["auto"] = status.get("busy"), bool(status.get("auto"))
    if bot["old"]:
        bot["blocked"] = "Ein Skript läuft noch ohne Port-Sperre: " + ", ".join(bot["names"]) + " einmal neu starten"
    elif bot["busy"] or bot["auto"]:
        bot["blocked"] = "Der Bot arbeitet gerade" + (f" ({bot['busy']})" if bot["busy"] else " (Auto-Modus)")
    elif any(s["name"] != "eatventure_bot.py" for s in scripts):
        bot["blocked"] = "Ein Skript nutzt gerade den Port: " + ", ".join(bot["names"])
    return bot


def age(path, now=None):
    try:
        return (now or time.time()) - os.path.getmtime(path)
    except OSError:
        return None


def ago(s):
    if s is None:
        return "nie"
    if s < 90:
        return f"vor {s:.0f} s"
    if s < 5400:
        return f"vor {s / 60:.0f} min"
    return f"vor {s / 3600:.1f} h"


def link(key, label, level, detail):
    return {"key": key, "label": label, "level": level, "detail": detail}


def newer_frame(path, after, timeout=FRAME_WAIT):
    """(png, stamp) of the frame at `path` as soon as its stamp differs from `after`; None if none came in time.

    The stamp comes from the open file, so it belongs to these bytes: ffmpeg renames each new frame into place.
    Raises OSError while there is no frame at all.
    """
    end = time.monotonic() + timeout
    while True:
        with open(path, "rb") as f:
            stamp = str(os.fstat(f.fileno()).st_mtime_ns)
            if stamp != after:
                return f.read(), stamp
        if time.monotonic() >= end:
            return None
        time.sleep(FRAME_POLL)


def chains(ports, st, st_age, skipped, rec_pids, rec_age, frame_age):
    """The two ways through the setup, each link ok / warn / bad / unknown. `st` is the board's newest status or None."""
    known = st is not None
    why = "nicht abgefragt: " + skipped if skipped else "keine Antwort vom Board"
    control = [link("usb", "USB-Port", "ok" if ports else "bad", ports[0] if ports else "Kein Board an USB (/dev/cu.usbmodem*)")]
    if known:
        mem = f"Firmware {st.get('fw')}, läuft seit {st.get('up', 0) / 60000:.0f} min, Neustart: {st.get('rst')}, " \
              f"Heap {st.get('heap', 0) // 1024} KB, PSRAM {st.get('psram', 0) // 1024} KB"
        stale = st_age is not None and st_age > 3 * POLL_S + 3
        control.append(link("board", "Board", "warn" if stale else "ok", mem + (f" (Stand {ago(st_age)})" if stale else "")))
        hid = st.get("hid")
        control.append(link("hid", "Bluetooth-Maus und -Tastatur",
                            "ok" if hid == "ble" else "warn" if hid == "usb" else "bad",
                            {"ble": "iPhone verbunden", "usb": "läuft über USB statt Bluetooth",
                             "off": "Nicht verbunden: iPhone-Einstellungen → Bluetooth → ESP32 Agent antippen"}.get(hid, str(hid))
                            + f", Steuerung: {st.get('owner', '?')}"))
    else:
        level = "unknown" if ports else "bad"
        control.append(link("board", "Board", level, why))
        control.append(link("hid", "Bluetooth-Maus und -Tastatur", "unknown", why))

    video = []
    if known:
        ap = st.get("ap")
        problem = [t for t, on in (("eingefroren, Mirroring neu starten", st.get("fz")),
                                   (f"{st.get('ovf')} Pufferüberläufe", st.get("ovf"))) if on]
        detail = {"streaming": "Mirroring aktiv", "connected": "iPhone verbunden, noch kein Bild",
                  "idle": "Kein Mirroring: iPhone Kontrollzentrum → Bildschirmsynchronisierung → ESP32 Agent"}.get(ap, str(ap))
        if ap == "streaming":
            detail += f", {st.get('w')}×{st.get('h')}, {st.get('fps', 0):.1f} Bilder/s, {st.get('kbps', 0)} kbit/s"
        video.append(link("airplay", "AirPlay", "bad" if st.get("fz") else "ok" if ap == "streaming" else "warn",
                          "; ".join([detail] + problem)))
        udp = st.get("udp") or {}
        video.append(link("wifi", "WLAN-Video", "ok" if udp.get("n") else "warn",
                          f"{st.get('net')} {st.get('ip')}, "
                          + (f"Empfänger verbunden, {udp.get('kbps', 0)} kbit/s" if udp.get("n") else "kein Empfänger verbunden")))
    else:
        video.append(link("airplay", "AirPlay", "unknown", why))
        video.append(link("wifi", "WLAN-Video", "unknown", why))
    if len(rec_pids) > 1:
        video.append(link("rec", "Aufnahme (rec)", "bad", f"{len(rec_pids)} laufen gleichzeitig, das zerstört rec.h264: PIDs {rec_pids}"))
    elif rec_pids:
        video.append(link("rec", "Aufnahme (rec)", "ok", f"PID {rec_pids[0]}, letzte Daten {ago(rec_age)}"))
    else:
        video.append(link("rec", "Aufnahme (rec)", "bad", "läuft nicht"))
    if frame_age is None:
        video.append(link("frame", "Live-Bild", "bad", "noch kein Bild"))
    elif not rec_pids:
        video.append(link("frame", "Live-Bild", "warn", f"altes Bild, {ago(frame_age)}"))
    elif frame_age > 30:
        video.append(link("frame", "Live-Bild", "warn", f"{ago(frame_age)}: Bildschirm ruhig oder Mirroring steht"))
    else:
        video.append(link("frame", "Live-Bild", "ok", f"neuestes Bild {ago(frame_age)}"))
    return {"control": control, "video": video}


def session_steps(ports, st, skipped, token_age, rec_pids, frame_age):
    """The README's session as a checklist. done: True, False, or None when the board can't be asked."""
    known = st is not None
    # the token is new with every boot: one saved before this boot is useless, and unknown while the board can't be asked
    if token_age is None:
        token = False
    else:
        token = token_age <= st.get("up", 0) / 1000 if known else None
    # a calm screen sends no new frames, so an old picture is fine while mirroring runs; without a status it must be recent
    picture = frame_age is not None and bool(rec_pids) and (st.get("ap") == "streaming" if known else frame_age < 300)
    steps = [
        {"key": "board", "title": "Board an USB", "done": bool(ports),
         "hint": "Board per USB an den Mac anstecken."},
        {"key": "info", "title": "IP und Video-Token geholt", "done": token,
         "hint": "Der Token ist nach jedem Board-Start neu.", "action": "info"},
        {"key": "ble", "title": "iPhone per Bluetooth gekoppelt",
         "done": st.get("hid") in ("ble", "usb") if known else None,
         "hint": "iPhone-Einstellungen → Bluetooth → ESP32 Agent antippen."},
        {"key": "rec", "title": "Aufnahme läuft", "done": bool(rec_pids),
         "hint": "Vor dem Mirroring starten: Eine Sitzung bekommt erst ab dem nächsten Schlüsselbild ein Bild.",
         "action": "rec_start"},
        {"key": "mirror", "title": "Bildschirmsynchronisierung aktiv", "done": st.get("ap") == "streaming" if known else None,
         "hint": "iPhone: ESP32-Agent-App schließen, Kontrollzentrum → Bildschirmsynchronisierung → ESP32 Agent. "
                 "Nach dem Start der Aufnahme starten oder neu starten."},
        {"key": "frame", "title": "Bild kommt an", "done": picture,
         "hint": "Kommt kein Bild, die Bildschirmsynchronisierung neu starten."},
    ]
    nxt = next((s["key"] for s in steps if s["done"] is not True), None)
    return {"steps": steps, "next": nxt, "skipped": skipped}


class App:
    """The state behind the page: one worker runs the commands in order, one poller reads the board's status."""

    def __init__(self, port=8770):
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        self.entries = collections.deque(maxlen=300)
        self.next_id = 1
        self.status, self.status_t, self.skipped, self.port_busy = None, None, None, False
        self.current = None
        self.procs = {"rec": [], "scripts": []}
        self.agent = webui_agent.Agent(self, port=port)
        self._load_log()
        drive.trace = self._trace

    def start(self):
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._poller, daemon=True).start()

    # log and queue

    def _load_log(self):
        try:
            with open(LOG) as f:
                for line in collections.deque(f, maxlen=300):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    self.entries.append(e)
                    self.next_id = max(self.next_id, e.get("id", 0) + 1)
        except OSError:
            pass

    def _write(self, e):
        try:
            with open(LOG, "a") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _new(self, src, kind, text, state):
        with self.lock:
            e = {"id": self.next_id, "t": time.time(), "src": src, "kind": kind, "text": scrub(text), "state": state,
                 "dur": None, "lines": [], "err": None}
            self.next_id += 1
            self.entries.append(e)
        return e

    def note(self, src, kind, text, state="info"):
        """A line in the timeline that is no command: what the AI said, a rec start, a refusal."""
        e = self._new(src, kind, text, state)
        self._write(e)
        return e

    def clear_log(self):
        with self.lock:
            self.entries.clear()
        try:
            os.remove(LOG)
        except OSError:
            pass

    def submit(self, kind, args, src="manuell", force=False):
        """Validate and queue a command; returns (entry, done event). Raises Invalid."""
        a = validate(kind, args)
        if src == "manuell" and self.agent.active():
            raise Invalid("Ein KI-Auftrag läuft: erst stoppen")
        done = threading.Event()
        e = self._new(src, kind, describe(kind, a), "wartet")
        self.queue.put((e, kind, a, force, done))
        return e, done

    def run_sync(self, kind, args):
        """For the AI: run through the same queue and checks. Returns the finished entry."""
        e, done = self.submit(kind, args, src="KI")
        done.wait()
        return e

    def _trace(self, lines, replies):
        """drive.board() reports here, on the worker thread: which lines went out and what came back."""
        e = self.current
        if e is None:
            return
        first = len(e["lines"])
        for i, line in enumerate(lines):
            reply = replies.get(900 + i)
            e["lines"].append({"line": scrub(line), "reply": scrub(reply) if reply is not None else None})
        bad = [x for x in e["lines"][first:] if x["reply"] is None or x["reply"].startswith("err")]
        if bad and not e["err"]:
            e["err"] = f"`{bad[0]['line']}`: " + (bad[0]["reply"] or "keine Antwort vom Board")

    def _worker(self):
        while True:
            e, kind, a, force, done = self.queue.get()
            t0 = time.time()
            self.current = e
            e["state"] = "läuft"
            try:
                reason = None if force else self.blocked()
                if reason:
                    raise Invalid(reason)
                self._execute(e, kind, a)
                e["state"] = "Fehler" if e["err"] else "ok"
            except (Exception, SystemExit) as x:  # serial_open() exits when the board is gone
                e["err"], e["state"] = scrub(x), "Fehler"
            e["dur"] = round(time.time() - t0, 2)
            self.current = None
            self._write(e)
            done.set()

    def _execute(self, e, kind, a):
        if kind == "tap":
            drive.tap(a["x"], a["y"])
        elif kind == "drag":
            drive.drag(a["x1"], a["y1"], a["x2"], a["y2"])
        elif kind == "flick":
            drive.flick(a["x"], a["y"], a["dy"], a["n"])
        elif kind == "type":
            drive.board("type " + a["text"])
        elif kind == "key":
            drive.board("key " + a["combo"])
        elif kind == "homescreen":
            drive.board("homescreen")
        elif kind == "info":
            d = drive.query("netinfo", timeout=3)
            if not d:
                raise Invalid("Keine Antwort vom Board")
            drive.state("ip", d["ip"])
            drive.state("vtok", d["vtok"])
            e["lines"].append({"line": "netinfo", "reply": f"{d.get('mode')} {d['ip']}, Token gespeichert"})
        elif kind == "raw":
            line = a["line"]
            if line in ("status", "netinfo"):
                d = drive.query(line, timeout=3)
                e["lines"].append({"line": line, "reply": scrub(json.dumps(d)) if d else None})
                if not d:
                    e["err"] = "Keine Antwort vom Board"
            else:
                drive.forget_pointer()
                drive.board(line)

    # status

    def _poller(self):
        while True:
            t0 = time.time()
            procs = processes()
            bot = bot_state(procs, self._drive_mtime(), _read_json(BOT_STATUS))
            self.procs = procs
            if bot["old"]:
                self.skipped, self.status = "ein Skript ohne Port-Sperre läuft", None
            else:
                self.skipped = None
                try:
                    d = drive.query("status", block=False)
                    self.port_busy = False
                    if d:
                        self.status, self.status_t = d, time.time()
                    elif self.status_t is None or time.time() - self.status_t > 15:
                        self.status = None
                except drive.PortBusy:
                    self.port_busy = True  # a command has the port: keep the last status
                except (OSError, SystemExit):
                    self.status, self.port_busy = None, False
            time.sleep(max(0.2, POLL_S - (time.time() - t0)))

    @staticmethod
    def _drive_mtime():
        return os.path.getmtime(os.path.join(HERE, "drive.py"))

    def bot(self):
        return bot_state(self.procs, self._drive_mtime(), _read_json(BOT_STATUS))

    def blocked(self):
        """Why the phone must not get a command right now, or None."""
        return self.bot()["blocked"]

    def frame_png(self):
        """The newest frame, and its age in seconds."""
        path = os.path.join(STATE, "live.png")
        with open(path, "rb") as f:
            return f.read(), age(path)

    def snapshot(self):
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        now = time.time()
        st, st_age = self.status, (now - self.status_t) if self.status_t else None
        rec_pids = [p["pid"] for p in self.procs["rec"]]
        rec_age = age(os.path.join(STATE, "rec.h264"), now)
        frame_path = os.path.join(STATE, "live.png")
        frame_age = age(frame_path, now)
        token_age = age(os.path.join(STATE, "vtok"), now)
        c = chains(ports, st, st_age, self.skipped, rec_pids, rec_age, frame_age)
        s = session_steps(ports, st, self.skipped, token_age, rec_pids, frame_age)
        with self.lock:
            entries = [dict(e) for e in list(self.entries)[-150:]]
        return {
            "t": now, "chains": c, "session": s, "status": _public_status(st), "status_age": st_age,
            "port_busy": self.port_busy, "bot": self.bot(),
            "frame": {"mtime": os.path.getmtime(frame_path) if frame_age is not None else None, "age": frame_age},
            "queue": self.queue.qsize(), "entries": entries, "agent": self.agent.snapshot(),
        }

    # rec

    def rec_start(self):
        if processes()["rec"]:
            raise Invalid("Die Aufnahme läuft schon: Eine zweite würde rec.h264 überschreiben")
        for name in ("ip", "vtok"):
            if not os.path.exists(os.path.join(STATE, name)):
                raise Invalid("Erst IP und Token holen")
        log = open(REC_LOG, "ab")
        p = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "drive.py"), "rec"], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        self.note("manuell", "rec", f"Aufnahme gestartet, PID {p.pid}")

    def rec_stop(self):
        pids = [p["pid"] for p in processes()["rec"]]
        if not pids:
            raise Invalid("Es läuft keine Aufnahme")
        for pid in pids:
            os.kill(pid, signal.SIGTERM)
        self.note("manuell", "rec", f"Aufnahme gestoppt, PID {pids}. Für ein neues Bild die Bildschirmsynchronisierung neu starten.")


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _public_status(st):
    return json.loads(scrub(json.dumps(st))) if st else None


APP = None


def allowed_request(headers, post):
    """Only this machine's own page: no rebinding host, no other origin, and POST only as JSON."""
    host = (headers.get("Host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return False
    origin = headers.get("Origin")
    if origin and (urlparse(origin).hostname or "") not in ("127.0.0.1", "localhost"):
        return False
    return not post or "application/json" in (headers.get("Content-Type") or "")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, ctype, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, code, o):
        self.send(code, "application/json", json.dumps(o, ensure_ascii=False).encode())

    def do_GET(self):
        if not allowed_request(self.headers, False):
            return self.send(403, "text/plain", b"no")
        url = urlparse(self.path)
        path = url.path
        if path == "/":
            with open(PAGE, "rb") as f:
                return self.send(200, "text/html; charset=utf-8", f.read())
        if path == "/api/state":
            return self.json(200, APP.snapshot())
        if path == "/live.png":
            try:  # waits for a frame newer than ?after=, so the page shows each one as it arrives
                got = newer_frame(os.path.join(STATE, "live.png"), parse_qs(url.query).get("after", [""])[0])
            except OSError:
                return self.send(404, "text/plain", b"no frame")
            if not got:
                return self.send(204, "image/png", b"")
            try:
                return self.send(200, "image/png", got[0], {"X-Frame-Stamp": got[1]})
            except (BrokenPipeError, ConnectionResetError):  # the page was closed or reloaded while it waited
                return
        self.send(404, "text/plain", b"not found")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not allowed_request(self.headers, True) or n > 65536:
            return self.send(403, "text/plain", b"no")
        try:
            o = json.loads(self.rfile.read(n))
            if not isinstance(o, dict):
                raise ValueError
        except ValueError:
            return self.json(400, {"error": "Kein gültiges JSON"})
        try:
            path = urlparse(self.path).path
            if path == "/api/command":
                e, _ = APP.submit(o.get("kind"), o.get("args") or {}, force=o.get("force") is True)
                return self.json(200, {"id": e["id"]})
            if path == "/api/rec":
                if o.get("action") == "start":
                    APP.rec_start()
                elif o.get("action") == "stop" and o.get("confirm") is True:
                    APP.rec_stop()
                else:
                    raise Invalid("Unbekannte Aktion")
                return self.json(200, {})
            if path == "/api/agent-tool":  # from webui_mcp.py, with the running task's token
                return self.json(200, APP.agent.tool_call(o.get("token"), o.get("name"), o.get("args")))
            if path == "/api/log":
                if o.get("action") == "clear" and o.get("confirm") is True:
                    APP.clear_log()
                else:
                    raise Invalid("Unbekannte Aktion")
                return self.json(200, {})
            if path == "/api/agent":
                action = o.get("action")
                if action == "start":
                    APP.agent.start(o.get("goal"), step_confirm=o.get("step_confirm") is True, model=o.get("model"))
                elif action == "stop":
                    APP.agent.stop()
                elif action == "answer":
                    APP.agent.answer(o.get("id"), o.get("value"))
                else:
                    raise Invalid("Unbekannte Aktion")
                return self.json(200, {})
            self.send(404, "text/plain", b"not found")
        except (Invalid, webui_agent.Refused) as x:
            self.json(409, {"error": str(x)})


def main():
    global APP
    ap = argparse.ArgumentParser(description="Local web page to drive the iPhone")
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    APP = App(port=a.port)
    APP.start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
