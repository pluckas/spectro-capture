"""Read-only browser monitor for the running Spectro Capture session."""

from collections import deque
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread


class Monitor:
    def __init__(self):
        self.lock = Lock()
        self.state = {"hardware": {}, "plan": [], "run": None}
        self.events = deque(maxlen=40)

    def publish(self, **values):
        with self.lock:
            self.state.update(values)

    def begin(self, rows, smart):
        with self.lock:
            self.state["run"] = {
                "rows": [dict(row, outcome="Pending" if row["enabled"] else "Disabled")
                         for row in describe_rows(rows)],
                "mode": "Hour angle" if smart else "List order",
                "activity": "Starting batch",
                "active": None,
                "exposure": None,
                "running": True,
            }

    def activity(self, message, active=None):
        with self.lock:
            run = self.state["run"]
            if run and run["running"]:
                run.update(activity=message, active=active, exposure=None)

    def exposure(self, **progress):
        with self.lock:
            run = self.state["run"]
            if run and run["running"] and run["active"] is not None and run["activity"] != "Stopping batch":
                run["exposure"] = progress

    def outcome(self, index, value):
        with self.lock:
            run = self.state["run"]
            if run and run["running"]:
                run["rows"][index]["outcome"] = value

    def processed(self, index):
        with self.lock:
            run = self.state["run"]
            if run and run["running"] and run["rows"][index]["outcome"] == "Pending":
                run["rows"][index]["outcome"] = "Processed"

    def science_failure(self, value):
        with self.lock:
            run = self.state["run"]
            if run and run["running"] and run["active"] is not None:
                row = run["rows"][run["active"]]
                if row["outcome"] == "Pending":
                    row["outcome"] = value

    def stopping(self):
        with self.lock:
            run = self.state["run"]
            if run and run["running"]:
                run.update(activity="Stopping batch", exposure=None)

    def finish(self, stopped):
        with self.lock:
            run = self.state["run"]
            if run:
                active = run["active"]
                if stopped and active is not None and run["rows"][active]["outcome"] == "Pending":
                    run["rows"][active]["outcome"] = "Interrupted"
                for row in run["rows"]:
                    if row["outcome"] == "Pending":
                        row["outcome"] = "Not run"
                run.update(running=False, active=None, exposure=None,
                           activity="Batch stopped" if stopped else "Batch ended")

    def log(self, message):
        with self.lock:
            self.events.append({"time": utc_now(), "message": str(message)})

    def snapshot(self):
        with self.lock:
            return json.dumps(dict(self.state, events=list(self.events)), allow_nan=False).encode()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def describe_rows(rows):
    return [
        {"target": row[0], "seconds": row[3], "frames": row[4],
         "calibration": row[5] == "✓", "enabled": row[6] != "✗",
         "start_ut": row[7], "reference": row[8],
         "reference_seconds": row[9], "reference_frames": row[10]}
        for row in rows
    ]


def report(context, method, *args, **kwargs):
    monitor = getattr(context, "web_monitor", None)
    if monitor is not None:
        try:
            getattr(monitor, method)(*args, **kwargs)
        except Exception as error:
            print(f"Web monitor update failed: {error}")


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, monitor, assets, **kwargs):
        self.monitor = monitor
        self.assets = assets
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/api/status":
            content_type, body = "application/json", self.monitor.snapshot()
        elif self.path in self.assets:
            content_type, body = self.assets[self.path]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start(monitor, host, port=8765):
    directory = Path(__file__).parent
    assets = {
        route: (mime, (directory / filename).read_bytes())
        for route, mime, filename in [
            ("/", "text/html; charset=utf-8", "index.html"),
            ("/style.css", "text/css; charset=utf-8", "style.css"),
            ("/app.js", "text/javascript; charset=utf-8", "app.js"),
        ]
    }
    handler = partial(Handler, monitor=monitor, assets=assets)
    server = ThreadingHTTPServer((host, port), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
