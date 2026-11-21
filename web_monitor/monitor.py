"""Thread-safe status, run events and image snapshots."""

from collections import deque
from datetime import datetime, timezone
from io import BytesIO
import json
from threading import Lock
from uuid import uuid4

class Monitor:
    def __init__(self):
        self.lock = Lock()
        self.state = {"hardware": {}, "plan": [], "run": None}
        self.events = deque(maxlen=40)
        self._viewer_pixels = None
        self._viewer_png = None
        self._guide_png = None
        self._guide_star_png = None

    def guide_star_frame(self, png, message):
        with self.lock:
            self.state["guide_star"] = uuid4().hex if png else None
            self.state["guide_star_message"] = message
            self._guide_star_png = png

    def guide_star_png(self, version):
        with self.lock:
            return self._guide_star_png if self.state.get("guide_star") == version else None

    def guide_frame(self, png):
        with self.lock:
            self.state["guide_frame"] = uuid4().hex
            self._guide_png = png

    def guide_png(self, version):
        with self.lock:
            return self._guide_png if self.state.get("guide_frame") == version else None

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
            now = datetime.now(timezone.utc)
            return json.dumps(dict(self.state, events=list(self.events),
                                   local_time=now.astimezone().strftime("%H:%M:%S"),
                                   utc_time=now.strftime("%H:%M:%S")), allow_nan=False).encode()


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
