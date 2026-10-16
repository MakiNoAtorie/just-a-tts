"""The HTTP API other programs use, on 127.0.0.1 only."""

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from core import engine
from core.engine import skip, start, status, submit, toggle_pause
from core.extract import page_text

PORT = 8765


class Handler(BaseHTTPRequestHandler):
    def allowed(self):
        # Browsers send Origin on cross-site requests; curl does not. Stops web pages driving the daemon.
        # Our own browser extension is let through.
        origin = self.headers.get("Origin")
        if origin and not origin.startswith("chrome-extension://"):
            self.send_error(403)
            return False
        return True

    def reply(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if not self.allowed():
            return
        if url.path != "/state":
            return self.send_error(404)
        e = engine
        state = {"read": e.read_id, "status": status(), "index": e.current, "total": len(e.passages), "error": e.error}
        if parse_qs(url.query).get("have") != [str(engine.read_id)]:  # the text itself only when the caller lacks it
            state["passages"] = engine.passages
        self.reply(state)

    def do_POST(self):
        url = urlsplit(self.path)
        if not self.allowed():
            return
        length = min(int(self.headers.get("Content-Length") or 0), 20_000_000)
        raw = self.rfile.read(length)
        body = raw.decode("utf-8", "replace").strip()
        panel = parse_qs(url.query).get("ui") != ["browser"]
        if url.path == "/stop":
            start([])
        elif url.path == "/pause":
            toggle_pause()
        elif url.path == "/skip":
            skip(-1 if body.startswith("-") else 1)
        elif url.path == "/speak":
            submit(body, panel=panel)
        elif url.path == "/page":
            submit(*page_text(url=body), panel=panel, blocks=True)
        elif url.path == "/html":
            submit(*page_text(html=body), panel=panel, blocks=True)
        elif url.path == "/pdf":
            submit(*page_text(pdf=raw), panel=panel, blocks=True)
        else:
            return self.send_error(404)
        self.reply({"read": engine.read_id})

    def log_message(self, *args):
        pass
