"""HTTP routes and dispatch to the desktop GUI thread."""

from concurrent.futures import Future, TimeoutError
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
from threading import Lock, Thread

DEVICE_ACTIONS = {
    "guiding": ("status", "loop", "find_star", "guide", "pause", "resume", "restore", "stop", "snapshot",
                "up", "down", "left", "right", "exposure", "save_fibre"),
    "lamps": ("tungsten", "thorium", "off"),
    "calibration": ("save_settings", "start_calibration"),
    "batch": ("run", "stop", "schedule", "load_csv", "download_csv", "edit_target", "save_target_identity", "save_target_reference", "remove_target", "clear_targets", "move_target"),
    "camera": ("connect", "disconnect", "setpoint", "binning", "cooler_on", "cooler_off", "test_image"),
    "telescope": ("connect", "disconnect", "slew_name", "slew_coords", "stop_slew"),
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
        elif self.path.startswith("/api/guide-star/"):
            body = self.monitor.guide_star_png(self.path.removeprefix("/api/guide-star/"))
            if body is None:
                self.send_error(404)
                return
            content_type = "image/png"
        elif self.path.startswith("/api/guide-field/"):
            body = self.monitor.guide_png(self.path.removeprefix("/api/guide-field/"))
            if body is None:
                self.send_error(404)
                return
            content_type = "image/png"
        elif self.path.startswith(("/api/viewer/", "/api/thumbnail/")):
            _, _, route, version = self.path.split("/", 3)
            body = self.monitor.viewer_png(version, thumbnail=route == "thumbnail")
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
            elif action == "test_image":
                value = request["seconds"]
                if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
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
            elif action in ("schedule", "save_settings", "start_calibration", "slew_name", "slew_coords",
                            "exposure", "save_fibre"):
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
        ]
    }
    assets.update({
        f"/{asset.name}": ("text/javascript; charset=utf-8", asset.read_bytes())
        for asset in directory.glob("*.js")
    })
    handler = partial(Handler, monitor=monitor, assets=assets, commands=commands)
    server = ThreadingHTTPServer((host, port), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
