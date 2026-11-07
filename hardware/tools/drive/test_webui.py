#!/usr/bin/env python3
"""Tests for webui.py and webui_agent.py. No board, phone or network: `python3 -I hardware/tools/drive/test_webui.py`."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["ESP32_DRIVE_DIR"] = tempfile.mkdtemp(prefix="webui-test-")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive
import webui
import webui_agent


class Validate(unittest.TestCase):
    def refused(self, kind, args):
        with self.assertRaises(webui.Invalid):
            webui.validate(kind, args)

    def test_text_cannot_smuggle_a_second_command(self):
        self.refused("type", {"text": "hi\n900 unpair"})
        self.refused("type", {"text": "hi\r"})
        self.refused("raw", {"line": "ping\n901 unpair"})
        self.refused("raw", {"line": "type hi\tthere"})

    def test_text_is_ascii_and_short(self):
        self.refused("type", {"text": "Grüße"})
        self.refused("type", {"text": "x" * 513})
        self.refused("type", {"text": ""})
        self.assertEqual(webui.validate("type", {"text": "Hello, you!"}), {"text": "Hello, you!"})

    def test_raw_rules(self):
        self.refused("raw", {"line": 'net {"mode":"sta","pass":"x"}'})
        self.refused("raw", {"line": "NET x"})
        self.refused("raw", {"line": "unpair"})
        self.refused("raw", {"line": "unpair", "confirm": "yes"})
        self.assertEqual(webui.validate("raw", {"line": "unpair", "confirm": True}), {"line": "unpair"})
        self.assertEqual(webui.validate("raw", {"line": " vdbg "}), {"line": "vdbg"})

    def test_coordinates(self):
        self.refused("tap", {"x": -1, "y": 5})
        self.refused("tap", {"x": 721, "y": 5})
        self.refused("tap", {"x": "1", "y": 5})
        self.refused("tap", {"x": True, "y": 5})
        self.refused("tap", {"x": float("nan"), "y": 5})
        self.refused("flick", {"x": 1, "y": 1, "dy": 0})
        self.refused("flick", {"x": 1, "y": 1, "dy": 120, "n": 99})
        self.assertEqual(webui.validate("flick", {"x": 1, "y": 1, "dy": -120})["n"], 1)

    def test_key(self):
        self.refused("key", {"combo": "enter\n1 unpair"})
        self.assertEqual(webui.validate("key", {"combo": "cmd+a"}), {"combo": "cmd+a"})

    def test_unknown(self):
        self.refused("reboot", {})


class Secrets(unittest.TestCase):
    def test_scrub(self):
        s = webui.scrub('{"ssid":"home","pass":"s3\\"cret","ip":"1.2.3.4","vtok":"abc"}')
        self.assertNotIn("s3", s)
        self.assertNotIn("abc", s)
        self.assertIn("1.2.3.4", s)
        self.assertIn("home", s)


class Requests(unittest.TestCase):
    def test_guard(self):
        ok = {"Host": "127.0.0.1:8770", "Content-Type": "application/json"}
        self.assertTrue(webui.allowed_request(ok, True))
        self.assertTrue(webui.allowed_request({**ok, "Origin": "http://localhost:8770"}, True))
        self.assertFalse(webui.allowed_request({**ok, "Host": "evil.example"}, False))
        self.assertFalse(webui.allowed_request({**ok, "Origin": "http://evil.example"}, True))
        self.assertFalse(webui.allowed_request({"Host": "127.0.0.1:8770", "Content-Type": "text/plain"}, True))
        self.assertTrue(webui.allowed_request({"Host": "localhost"}, False))


class Status(unittest.TestCase):
    def test_etime(self):
        self.assertEqual(webui.parse_etime("00:05"), 5)
        self.assertEqual(webui.parse_etime("02:03:04"), 7384)
        self.assertEqual(webui.parse_etime("1-00:00:10"), 86410)

    def test_bot_states(self):
        none = {"rec": [], "scripts": []}
        self.assertIsNone(webui.bot_state(none, 100)["blocked"])
        bot = {"rec": [], "scripts": [{"pid": 1, "started": 200, "name": "eatventure_bot.py"}]}
        self.assertIsNone(webui.bot_state(bot, 100, {"busy": None, "auto": False})["blocked"])
        self.assertIn("arbeitet", webui.bot_state(bot, 100, {"busy": "stations", "auto": False})["blocked"])
        self.assertIn("arbeitet", webui.bot_state(bot, 100, {"busy": None, "auto": True})["blocked"])
        old = webui.bot_state(bot, 300, {"busy": None, "auto": False})
        self.assertTrue(old["old"])
        self.assertIn("Port-Sperre", old["blocked"])

    def test_chain_without_board(self):
        c = webui.chains([], None, None, None, [], None, None)
        self.assertEqual(c["control"][0]["level"], "bad")
        self.assertEqual({l["key"]: l["level"] for l in c["video"]}["rec"], "bad")

    def test_two_recs_are_an_error(self):
        c = webui.chains(["/dev/x"], None, None, None, [1, 2], 1, 1)
        self.assertEqual({l["key"]: l["level"] for l in c["video"]}["rec"], "bad")

    def test_session_next_step(self):
        st = {"up": 60000, "hid": "off", "ap": "idle"}
        s = webui.session_steps(["/dev/x"], st, None, 10, [], None)
        self.assertEqual(s["next"], "ble")
        old_token = webui.session_steps(["/dev/x"], st, None, 120, [], None)
        self.assertEqual(old_token["next"], "info")
        self.assertEqual(webui.session_steps([], None, None, None, [], None)["next"], "board")
        self.assertFalse(next(x for x in webui.session_steps(["/dev/x"], st, None, 10, [1], 2)["steps"] if x["key"] == "frame")["done"])
        ok = {"up": 60000, "hid": "ble", "ap": "streaming"}
        self.assertIsNone(webui.session_steps(["/dev/x"], ok, None, 10, [1], 2)["next"])


class Frame(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(dir=os.environ["ESP32_DRIVE_DIR"]), "live.png")

    def put(self, data):  # like ffmpeg: a new file renamed into place
        with open(self.path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(self.path + ".tmp", self.path)

    def test_no_frame_yet(self):
        with self.assertRaises(OSError):
            webui.newer_frame(self.path, "", timeout=0)

    def test_first_ask_gets_the_frame_at_once(self):
        self.put(b"one")
        self.assertEqual(webui.newer_frame(self.path, "", timeout=0)[0], b"one")

    def test_same_stamp_waits_then_gives_up(self):
        self.put(b"one")
        stamp = webui.newer_frame(self.path, "", timeout=0)[1]
        self.assertIsNone(webui.newer_frame(self.path, stamp, timeout=0.05))

    def test_new_frame_ends_the_wait(self):
        self.put(b"one")
        stamp = webui.newer_frame(self.path, "", timeout=0)[1]
        time.sleep(0.01)
        threading.Timer(0.1, self.put, [b"two"]).start()
        started = time.monotonic()
        data, newer = webui.newer_frame(self.path, stamp, timeout=5)
        self.assertEqual(data, b"two")
        self.assertNotEqual(newer, stamp)
        self.assertLess(time.monotonic() - started, 2)


class PortLock(unittest.TestCase):
    def test_second_process_is_told_busy(self):
        code = ("import sys;sys.path.insert(0,sys.argv[1]);import drive\n"
                "try:\n  with drive.port_lock(block=False): print('free')\n"
                "except drive.PortBusy: print('busy')\n")
        run = lambda: subprocess.run([sys.executable, "-c", code, HERE], capture_output=True, text=True,
                                     env={**os.environ}).stdout.strip()
        with drive.port_lock():
            self.assertEqual(run(), "busy")
        self.assertEqual(run(), "free")


class FakeApp:
    def __init__(self, blocked=None):
        self.ran, self.notes, self.block_reason = [], [], blocked

    def blocked(self):
        return self.block_reason

    def frame_png(self):
        return b"png", 1.0

    def note(self, src, kind, text, state="info"):
        self.notes.append((src, kind, text, state))

    def run_sync(self, kind, args):
        a = webui.validate(kind, args)
        self.ran.append((kind, a))
        return {"err": None}


INIT = {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "tools": list(webui_agent.ALLOWED),
        "mcp_servers": [{"name": "phone", "status": "connected"}]}
DONE = {"type": "result", "subtype": "success", "is_error": False, "result": "Fertig: iOS 26"}


class FakeProc:
    """Stands in for `claude`: gives out lines from a queue until it is stopped."""

    def __init__(self):
        self.lines, self.stopped = queue.Queue(), False
        self.stdout = iter(self.lines.get, None)

    def say(self, *events):
        for e in events:
            self.lines.put(e if isinstance(e, str) else json.dumps(e))

    def end(self):
        self.lines.put(None)

    def stop(self):
        self.stopped = True
        self.end()

    def wait(self):
        return 0


def agent(app=None, **kw):
    app, proc, calls = app or FakeApp(), FakeProc(), []

    def spawner(cmd, env, cwd, prompt):
        with open(cmd[cmd.index("--mcp-config") + 1]) as f:
            calls.append({"cmd": cmd, "env": env, "cwd": cwd, "prompt": prompt, "config": json.load(f)})
        return proc
    return webui_agent.Agent(app, spawner=spawner, settle=0, **kw), app, proc, calls


def finish(a, seconds=5):
    a.thread.join(seconds)
    assert not a.thread.is_alive()


def wait_pending(a, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        p = a.snapshot()["pending"]
        if p:
            return p
        time.sleep(0.01)
    raise AssertionError("no question came")


def in_thread(fn, *args):
    out = []
    t = threading.Thread(target=lambda: out.append(fn(*args)), daemon=True)
    t.start()
    return t, out


def wait_spawned(calls, seconds=5):
    end = time.time() + seconds
    while time.time() < end and not calls:
        time.sleep(0.01)
    assert calls


class AgentRun(unittest.TestCase):
    def test_command_keeps_the_run_to_the_phone_tools_and_the_login(self):
        os.environ["ANTHROPIC_API_KEY"] = "secret"
        self.addCleanup(os.environ.pop, "ANTHROPIC_API_KEY")
        a, _, proc, calls = agent()
        a.start("-v version?")
        wait_spawned(calls)
        c = calls[0]
        proc.say(INIT, DONE)
        proc.end()
        finish(a)
        cmd = c["cmd"]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertNotIn("--bare", cmd)  # it would need an API key
        self.assertTrue(all(t.startswith("mcp__phone__") for t in cmd[cmd.index("--allowedTools") + 1].split(",")))
        self.assertNotIn("-v version?", " ".join(cmd))
        self.assertEqual(c["prompt"], "Task: -v version?")
        self.assertNotIn("ANTHROPIC_API_KEY", c["env"])
        self.assertFalse(os.path.exists(c["cwd"]))  # with the token file

    def test_finishes_and_keeps_the_answer(self):
        a, app, proc, calls = agent()
        a.start("x")
        proc.say(INIT, {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 5, "output_tokens": 2},
                                                         "content": [{"type": "thinking", "thinking": "Ich schaue"}, {"type": "text", "text": "Ich tippe"}]}},
                 {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 5, "output_tokens": 2}, "content": [{"type": "tool_use"}]}},
                 {"type": "user", "message": {}},
                 {"type": "assistant", "message": {"id": "m2", "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [{"type": "text", "text": "Fertig: iOS 26"}]}},
                 DONE)
        proc.end()
        finish(a)
        self.assertEqual((a.state, a.result), ("fertig", "Fertig: iOS 26"))
        self.assertEqual(a.usage["input"], 6)  # one message, several lines: counted once
        said = [n[2] for n in app.notes if n[1] == "sagt"]
        self.assertEqual(said, ["Ich tippe"])
        self.assertIn(("KI", "denkt", "Ich schaue", "info"), app.notes)

    def test_another_tool_in_the_run_aborts_it(self):
        a, _, proc, _ = agent()
        a.start("x")
        proc.say({**INIT, "tools": webui_agent.ALLOWED + ["Bash"]})
        finish(a)
        self.assertTrue(proc.stopped)
        self.assertEqual(a.state, "Fehler")
        self.assertIn("Bash", a.result)

    def test_mcp_not_connected_aborts(self):
        a, _, proc, _ = agent()
        a.start("x")
        proc.say({**INIT, "mcp_servers": [{"name": "phone", "status": "failed"}]})
        finish(a)
        self.assertEqual(a.state, "Fehler")

    def test_error_result(self):
        a, _, proc, _ = agent()
        a.start("x")
        proc.say(INIT, {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Limit erreicht"})
        proc.end()
        finish(a)
        self.assertEqual(a.state, "Fehler")
        self.assertIn("Limit erreicht", a.result)

    def test_end_without_result(self):
        a, _, proc, _ = agent()
        a.start("x")
        proc.say("Please log in")
        proc.end()
        finish(a)
        self.assertEqual(a.state, "Fehler")
        self.assertIn("Please log in", a.result)

    def test_token_is_needed_and_ends_with_the_run(self):
        a, app, proc, calls = agent()
        with self.assertRaises(webui_agent.Refused):
            a.tool_call("", "tap", {"x": 1, "y": 2})
        a.start("x")
        wait_spawned(calls)
        token = calls[0]["config"]["mcpServers"]["phone"]["env"]["WEBUI_AGENT_TOKEN"]
        with self.assertRaises(webui_agent.Refused):
            a.tool_call("wrong", "tap", {"x": 1, "y": 2})
        with self.assertRaises(webui_agent.Refused):
            a.tool_call(None, "tap", {"x": 1, "y": 2})
        r = a.tool_call(token, "tap", {"x": 1, "y": 2})
        self.assertFalse(r["isError"])
        self.assertEqual(r["content"][1]["mimeType"], "image/png")
        self.assertEqual(app.ran, [("tap", {"x": 1, "y": 2})])
        proc.say(DONE)
        proc.end()
        finish(a)
        with self.assertRaises(webui_agent.Refused):
            a.tool_call(token, "tap", {"x": 1, "y": 2})

    def run_with_token(self, app=None, **kw):
        a, app, proc, calls = agent(app, **kw)
        a.start("x", step_confirm=kw.pop("step_confirm", False))
        wait_spawned(calls)
        self.addCleanup(lambda: a.active() and a.stop())
        return a, app, proc, calls[0]["config"]["mcpServers"]["phone"]["env"]["WEBUI_AGENT_TOKEN"]

    def test_no_step_limit(self):
        a, app, proc, token = self.run_with_token()
        for _ in range(100):
            self.assertFalse(a.tool_call(token, "screenshot", {})["isError"])
        self.assertFalse(a.tool_call(token, "tap", {"x": 1, "y": 2})["isError"])
        self.assertEqual(a.steps, 101)
        self.assertEqual(len(app.ran), 1)
        self.assertTrue(a.active())

    def test_stop_while_waiting_for_an_answer(self):
        a, app, proc, token = self.run_with_token()
        t, out = in_thread(a.tool_call, token, "ask_user", {"question": "Welche?"})
        wait_pending(a)
        a.stop()
        t.join(5)
        finish(a)
        self.assertEqual(a.state, "gestoppt")
        self.assertTrue(proc.stopped)
        self.assertTrue(out[0]["isError"])

    def confirm_answer(self, value):
        a, _, _, token = self.run_with_token()
        t, out = in_thread(a.tool_call, token, "confirm", {"action": "Nachricht an Max senden"})
        p = wait_pending(a)
        self.assertEqual(p["kind"], "bestaetigen")
        a.answer(p["id"], value)
        t.join(5)
        return out[0]["content"][0]["text"]

    def test_confirm_needs_exactly_ja(self):
        self.assertEqual(self.confirm_answer("nein"), "abgelehnt")
        self.assertEqual(self.confirm_answer("vielleicht"), "abgelehnt")
        self.assertEqual(self.confirm_answer("ja"), "bestaetigt")

    def test_step_confirm_holds_every_action(self):
        a, app, proc, calls = agent()
        a.start("x", step_confirm=True)
        wait_spawned(calls)
        token = calls[0]["config"]["mcpServers"]["phone"]["env"]["WEBUI_AGENT_TOKEN"]
        t, out = in_thread(a.tool_call, token, "tap", {"x": 1, "y": 2})
        p = wait_pending(a)
        self.assertEqual(p["kind"], "schritt")
        self.assertEqual(app.ran, [])
        a.answer(p["id"], "nein")
        t.join(5)
        self.assertEqual(app.ran, [])
        t, out = in_thread(a.tool_call, token, "tap", {"x": 3, "y": 4})
        a.answer(wait_pending(a)["id"], "ja")
        t.join(5)
        self.assertEqual(app.ran, [("tap", {"x": 3, "y": 4})])
        a.stop()
        finish(a)

    def test_bad_input_goes_back_to_the_model(self):
        a, app, _, token = self.run_with_token()
        r = a.tool_call(token, "type_text", {"text": "Grüße"})
        self.assertTrue(r["isError"])
        self.assertIn("ASCII", r["content"][0]["text"])
        self.assertEqual(app.ran, [])

    def test_bot_blocks_start_and_action(self):
        a, _, _, _ = agent(app=FakeApp(blocked="Der Bot arbeitet gerade"))
        with self.assertRaises(webui_agent.Refused):
            a.start("x")
        app2 = FakeApp()
        b, _, proc, token = self.run_with_token(app=app2)
        app2.block_reason = "Der Bot arbeitet gerade"
        self.assertTrue(b.tool_call(token, "tap", {"x": 1, "y": 2})["isError"])
        finish(b)
        self.assertEqual(app2.ran, [])
        self.assertEqual(b.state, "Fehler")
        self.assertIn("Bot", b.result)

    def test_notes_only_from_the_list(self):
        a, _, _, token = self.run_with_token()
        self.assertTrue(a.tool_call(token, "notes", {"name": "../../../../etc/passwd"})["isError"])

    def test_only_one_task(self):
        a, _, _, _ = self.run_with_token()
        with self.assertRaises(webui_agent.Refused):
            a.start("y")


class Mcp(unittest.TestCase):
    """webui_mcp.py on real stdio, against a stub of the page."""

    def test_passes_calls_on(self):
        seen = []

        class Stub(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                out = json.dumps({"content": [{"type": "text", "text": "ok"}], "isError": False}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tap", "arguments": {"x": 1, "y": 2}}},
                {"jsonrpc": "2.0", "id": 4, "method": "nope"}]
        p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "webui_mcp.py")], capture_output=True, text=True, timeout=20,
                           input="".join(json.dumps(m) + "\n" for m in msgs),
                           env={**os.environ, "WEBUI_PORT": str(srv.server_port), "WEBUI_AGENT_TOKEN": "tok"})
        out = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
        self.assertEqual(sorted(out), [1, 2, 3, 4])
        self.assertEqual(out[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual({t["name"] for t in out[2]["result"]["tools"]}, {t["name"] for t in webui_agent.TOOLS})
        self.assertEqual(out[3]["result"]["content"][0]["text"], "ok")
        self.assertIn("error", out[4])
        self.assertEqual(seen, [("/api/agent-tool", {"token": "tok", "name": "tap", "args": {"x": 1, "y": 2}})])


class Queue(unittest.TestCase):
    def setUp(self):
        self.app = webui.App()
        threading.Thread(target=self.app._worker, daemon=True).start()
        self.sent = []
        self._board = drive.board
        drive.board = self.fake_board

    def tearDown(self):
        drive.board = self._board

    def fake_board(self, *lines, **kw):
        replies = {900 + i: "ok" for i in range(len(lines))}
        self.sent += lines
        drive.trace(lines, replies)
        return replies

    def test_runs_and_logs_the_lines(self):
        e, done = self.app.submit("key", {"combo": "enter"})
        self.assertTrue(done.wait(3))
        self.assertEqual(e["state"], "ok")
        self.assertEqual(e["lines"], [{"line": "key enter", "reply": "ok"}])

    def test_a_blocked_command_never_reaches_the_board(self):
        self.app.blocked = lambda: "Der Bot arbeitet gerade"
        e, done = self.app.submit("key", {"combo": "enter"})
        self.assertTrue(done.wait(3))
        self.assertEqual(e["state"], "Fehler")
        self.assertEqual(self.sent, [])
        e2, done2 = self.app.submit("key", {"combo": "enter"}, force=True)
        self.assertTrue(done2.wait(3))
        self.assertEqual(e2["state"], "ok")

    def test_missing_reply_is_an_error(self):
        def silent(*lines, **kw):
            drive.trace(lines, {})
            return {}
        drive.board = silent
        e, done = self.app.submit("raw", {"line": "vdbg"})
        self.assertTrue(done.wait(3))
        self.assertEqual(e["state"], "Fehler")
        self.assertIn("keine Antwort", e["err"])

    def test_manual_commands_wait_for_a_running_task(self):
        self.app.agent.state = "läuft"
        with self.assertRaises(webui.Invalid):
            self.app.submit("key", {"combo": "enter"})
        e, done = self.app.submit("key", {"combo": "enter"}, src="KI")
        self.assertTrue(done.wait(3))
        self.assertEqual(e["state"], "ok")


if __name__ == "__main__":
    unittest.main()
