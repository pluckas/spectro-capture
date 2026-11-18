"""Browser monitor for the running Spectro Capture session."""

from collections import deque
from concurrent.futures import Future, TimeoutError
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4


class Monitor:
    def __init__(self):
        self.lock = Lock()
        self.state = {"hardware": {}, "plan": [], "run": None}
        self.events = deque(maxlen=40)
        self._viewer_pixels = None
        self._viewer_png = None

    def viewer_frame(self, pixels, max_adu):
        with self.lock:
            version = uuid4().hex
            self.state["viewer"] = {"version": version, "max_adu": max_adu}
            self._viewer_pixels = pixels
            self._viewer_png = None

    def viewer_png(self, version):
        from PIL import Image

        with self.lock:
            frame = self.state.get("viewer")
            if frame is None or frame["version"] != version:
                return None
            if self._viewer_png is not None:
                return self._viewer_png
            pixels = self._viewer_pixels

        output = BytesIO()
        Image.fromarray(pixels).save(output, format="PNG")
        png = output.getvalue()
        with self.lock:
            if self.state.get("viewer") is frame:
                self._viewer_png = png
        return png

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


DEVICE_ACTIONS = {
    "lamps": ("tungsten", "thorium", "off"),
    "calibration": ("save_settings", "start_calibration"),
    "batch": ("run", "stop", "schedule", "load_csv", "download_csv", "edit_target", "save_target_identity", "save_target_reference", "remove_target", "clear_targets", "move_target"),
    "camera": ("connect", "disconnect", "setpoint", "binning", "cooler_on", "cooler_off"),
    "telescope": ("connect", "disconnect"),
    "dome": ("connect", "disconnect", "open", "close", "home", "park", "abort", "sync_on", "sync_off"),
}


class DeviceCommands:
    """Pass one browser request at a time to the desktop event loop."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.lock = Lock()
        self.pending = None

    def submit(self, device, action, value):
        with self.lock:
            if self.pending is not None and not self.pending[0].done():
                return 409, "A command is already pending."
            result = Future()
            self.pending = (result, device, action, value)
        try:
            return result.result(timeout=10)
        except TimeoutError:
            if result.cancel():
                return 503, "Desktop did not respond. Command was cancelled."
            return 504, "The result is pending. Check the status before trying again."

    def poll(self, root):
        with self.lock:
            pending = self.pending
        if pending is not None:
            result, device, action, value = pending
            if not result.done() and result.set_running_or_notify_cancel():
                try:
                    message = self.handlers[device](action, value)
                except Exception as error:
                    result.set_result((409, str(error)))
                else:
                    result.set_result((200, message))
        root.after(100, self.poll, root)


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, monitor, assets, commands, **kwargs):
        self.monitor = monitor
        self.assets = assets
        self.commands = commands
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/api/status":
            content_type, body = "application/json", self.monitor.snapshot()
        elif self.path.startswith("/api/viewer/"):
            body = self.monitor.viewer_png(self.path.removeprefix("/api/viewer/"))
            if body is None:
                self.send_error(404)
                return
            content_type = "image/png"
        elif self.path in self.assets:
            content_type, body = self.assets[self.path]
        else:
            self.send_error(404)
            return
        self.respond(200, content_type, body)

    def do_POST(self):
        prefix, _, device = self.path.rpartition("/")
        if prefix != "/api" or self.commands is None or device not in self.commands.handlers:
            self.send_error(404)
            return
        if self.headers.get_content_type() != "application/json":
            self.respond_json(415, "Expected JSON.")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            limit = 1024 * 1024 if device == "batch" else 256
            if not 0 < size <= limit:
                self.respond_json(413, "Request is empty or too large.")
                return
            self.connection.settimeout(3)
            request = json.loads(self.rfile.read(size))
            action = request["action"]
            if action not in DEVICE_ACTIONS[device] or (size > 1024 and action != "load_csv"):
                raise ValueError
            value = None
            if action == "setpoint":
                value = request["setpoint"]
                if type(value) not in (float, int) or not math.isfinite(value):
                    raise ValueError
                value = float(value)
            elif action == "load_csv":
                value = request["csv"]
                if not isinstance(value, str) or "\x00" in value:
                    raise ValueError
            elif action in ("edit_target", "save_target_identity", "save_target_reference", "remove_target", "move_target"):
                value = request["edit"]
                if not isinstance(value, dict):
                    raise ValueError
            elif action in ("run", "clear_targets"):
                value = request["revision"]
                if not isinstance(value, str):
                    raise ValueError
            elif action in ("schedule", "save_settings", "start_calibration"):
                value = request["options"]
                if not isinstance(value, dict):
                    raise ValueError
            elif action == "binning":
                value = request["binning"]
                if type(value) is not int or value not in (1, 2, 3):
                    raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            self.respond_json(400, "Invalid command.")
            return

        status, message = self.commands.submit(device, action, value)
        if action == "download_csv" and status == 200:
            self.respond(status, "text/csv; charset=utf-8", message.encode("utf-8"))
        else:
            self.respond_json(status, message)

    def respond_json(self, status, message):
        self.respond(status, "application/json", json.dumps({"message": message}).encode())

    def respond(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start(monitor, host, port=8765, *, commands=None):
    directory = Path(__file__).parent
    assets = {
        route: (mime, (directory / filename).read_bytes())
        for route, mime, filename in [
            ("/", "text/html; charset=utf-8", "index.html"),
            ("/style.css", "text/css; charset=utf-8", "style.css"),
            ("/app.js", "text/javascript; charset=utf-8", "app.js"),
        ]
    }
    handler = partial(Handler, monitor=monitor, assets=assets, commands=commands)
    server = ThreadingHTTPServer((host, port), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
