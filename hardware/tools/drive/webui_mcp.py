#!/usr/bin/env python3
"""The phone's tools for a `claude -p` run: an MCP server on stdio that passes every call on to webui.py.

webui_agent.py starts `claude`, and `claude` starts this. It holds no logic of its own, so a task can't get past the
checks in webui.py. Only standard library, so it runs in any Python 3.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from webui_agent import TOOLS

PORT = os.environ["WEBUI_PORT"]
TOKEN = os.environ["WEBUI_AGENT_TOKEN"]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # always this Mac, whatever the proxy settings


def call(name, args):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/agent-tool", method="POST",
                                 data=json.dumps({"token": TOKEN, "name": name, "args": args}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req) as r:  # no timeout: a question to the user takes as long as it takes
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as x:
        return {"content": [{"type": "text", "text": f"The web page did not answer: {x}"}], "isError": True}


def answer(msg):
    m = msg.get("method")
    if m == "initialize":
        version = msg.get("params", {}).get("protocolVersion", "2025-06-18")
        return {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": {"name": "phone", "version": "1"}}
    if m == "ping":
        return {}
    if m == "tools/list":
        return {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]} for t in TOOLS]}
    if m == "tools/call":
        p = msg.get("params", {})
        return call(p.get("name"), p.get("arguments") or {})
    return None


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:  # a notification
            continue
        result = answer(msg)
        out = {"jsonrpc": "2.0", "id": msg["id"]}
        if result is None:
            out["error"] = {"code": -32601, "message": "Method not found"}
        else:
            out["result"] = result
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
