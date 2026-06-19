#!/usr/bin/env python3
# Spectro Capture
# © Paul Luckas, 2025
#
# This file is part of the Spectro Capture project.
# Licensed for personal, educational, and research use only.
# Commercial use is not permitted without prior permission.
#
# See the LICENSE file for full terms.

"""
gui_guide.py
------------
Guide tab for the Spectro Capture application.

Provides a graphical interface for controlling and monitoring the
PHD2 guiding system. Acts as a high-level frontend to the low-level
PHD2 JSON-RPC driver (guider.py) and backend controller (phd2_control.py).

Key features:
- Connects to and monitors PHD2 guiding state
- Displays live guider status, star metrics, and connection info
- Allows looping, guiding, pausing, and lock restoration
- Interfaces with AppContext for shared access across modules
- Supports integration with auto_capture.py for automated guiding
"""

import sys
import threading
import json
import os
import socket
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path

# --- Local phd2client dependency path ---
base = Path(__file__).resolve().parent
deps = base / "dependencies" / "phd2client"
if str(deps) not in sys.path:
    sys.path.insert(0, str(deps))

from guider import Guider, GuiderError

LOCK_FILE = "saved_lock.json"


class GuideTab(ttk.Frame):
    """Guide control tab integrated into the Spectro Capture main window."""

    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        # Register this Guide tab as the logging target for guider messages
        self.context.set_guide_log_callback(self.log)
        self.host = "localhost"
        self.phd = None
        self._busy = False
    
        self.create_widgets()
        self.load_fibre_position()
    
        # Register this guide tab with the shared app context
        self.context.guider = self

    # -------------------------------------------------
    #  GUI Setup
    # -------------------------------------------------
    def create_widgets(self):
        """Build compact two-column Guide tab layout."""
        def make_button_pair(row, left_label, left_cmd, right_label=None, right_cmd=None, width=None):
            b1 = ttk.Button(self, text=left_label, command=lambda: self.run_threaded(left_cmd))
            b1.grid(row=row, column=0, padx=5, pady=3, sticky="ew")
    
            if right_label:
                b2 = ttk.Button(self, text=right_label, command=lambda: self.run_threaded(right_cmd))
                if not width:
                    width = max(len(left_label), len(right_label)) + 4
                b1.config(width=width)
                b2.config(width=width)
                b2.grid(row=row, column=1, padx=5, pady=3, sticky="ew")
                return b1, b2
            else:
                b1.config(width=len(left_label) + 4)
                return (b1,)
    
        # --- Shared status header (replaces old spacer row) ---
        from status_bar import add_status_header, update_status_header
        self._sync_blink = False
        self.indicators = add_status_header(self, layout="grid")

        # --- Main control buttons (2 per row) ---
        self.buttons = []
        self.buttons += make_button_pair(1, "Status", self.get_status, "Loop", self.start_loop)
        self.buttons += make_button_pair(2, "Find Star", self.find_star, "Guide", self.start_guiding)
        self.buttons += make_button_pair(3, "Pause", self.pause_guiding, "Resume", self.unpause_guiding)
        self.buttons += make_button_pair(4, "Move to Fibre", self.restore_lock, "Stop", self.stop_guiding)

        # --- Exposure Section ---
        exposure_frame = ttk.LabelFrame(self, text="Guide Camera Exposure", padding=10)
        exposure_frame.grid(row=5, column=0, columnspan=2, padx=5, pady=5, sticky="ew")

        ttk.Label(exposure_frame, text="Exposure (s):").grid(row=0, column=0, sticky="e", padx=3, pady=2)

        self.exposure_values = [
            "0.01", "0.02", "0.05", "0.1", "0.2", "0.5",
            "1.0", "1.5", "2.0", "2.5", "3.0", "3.5",
            "4.0", "4.5", "5.0", "6.0", "7.0", "8.0", "9.0", "10.0"
        ]
        self.exposure_var = tk.StringVar(value="0.01")
        self.exposure_combo = ttk.Combobox(
            exposure_frame,
            textvariable=self.exposure_var,
            values=self.exposure_values,
            width=6,
            state="readonly"
        )
        self.exposure_combo.grid(row=0, column=1, padx=3, pady=2)
        self.exposure_combo.bind("<<ComboboxSelected>>", self.on_exposure_change)
        # Force combobox display to show the default value at startup
        self.exposure_combo.set(self.exposure_var.get())

        # --- Fibre Position Section ---
        fibre_frame = ttk.LabelFrame(self, text="Fibre Lock Position", padding=10)
        fibre_frame.grid(row=6, column=0, columnspan=2, padx=5, pady=5, sticky="ew")

        ttk.Label(fibre_frame, text="Fibre X:").grid(row=0, column=0, sticky="e", padx=3, pady=2)
        self.fibre_x_entry = ttk.Entry(fibre_frame, width=8)
        self.fibre_x_entry.insert(0, "480")
        self.fibre_x_entry.grid(row=0, column=1, padx=3, pady=2)

        ttk.Label(fibre_frame, text="Fibre Y:").grid(row=0, column=2, sticky="e", padx=3, pady=2)
        self.fibre_y_entry = ttk.Entry(fibre_frame, width=8)
        self.fibre_y_entry.insert(0, "310")
        self.fibre_y_entry.grid(row=0, column=3, padx=3, pady=2)

        save_fibre_btn = ttk.Button(
            fibre_frame, text="Save", command=lambda: self.run_threaded(self.save_fibre_position)
        )
        save_fibre_btn.grid(row=0, column=4, padx=6, pady=2)

        # --- Lock Adjust Section ---
        adjust_frame = ttk.LabelFrame(self, text="Adjust Lock Position", padding=10)
        adjust_frame.grid(row=7, column=0, columnspan=2, padx=5, pady=5, sticky="ew")

        def nudge(dx, dy):
            if not self.ensure_connection():
                return
            try:
                lock = self.phd.GetLockPosition()
                if isinstance(lock, (list, tuple)) and len(lock) == 2:
                    x, y = lock
                else:
                    self.log("Could not get valid lock position.")
                    return
                new_x, new_y = x + dx, y + dy
                self.phd.SetLockPosition(new_x, new_y)
                self.log(f"Nudged lock position to ({new_x:.1f}, {new_y:.1f})")
            except Exception as e:
                self.log(f"Nudge error: {e}")

        ttk.Button(adjust_frame, text="↑", width=4, command=lambda: nudge(0, -1)).grid(row=0, column=1, pady=(0, 2))
        ttk.Button(adjust_frame, text="←", width=4, command=lambda: nudge(-1, 0)).grid(row=1, column=0, padx=(15, 5))
        ttk.Button(adjust_frame, text="→", width=4, command=lambda: nudge(1, 0)).grid(row=1, column=2, padx=(5, 15))
        ttk.Button(adjust_frame, text="↓", width=4, command=lambda: nudge(0, 1)).grid(row=2, column=1, pady=(2, 0))
        ttk.Label(adjust_frame, text="").grid(row=1, column=1)

        # --- Status Text Box ---
        self.status = scrolledtext.ScrolledText(self, width=70, height=18, wrap="word", state="disabled")
        self.status.grid(row=1, column=2, rowspan=7, padx=10, pady=5, sticky="nsew")
        
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(6, weight=1)
        
        # --- Start status-bar update loop ---
        self.after(1000, self.refresh_status_header)

    # -------------------------------------------------
    #  Utility Functions
    # -------------------------------------------------
    def log(self, msg):
        self.status.config(state="normal")
        self.status.insert("end", msg + "\n")
        self.status.see("end")
        self.status.config(state="disabled")
        self.update_idletasks()

    def set_busy(self, busy: bool):
        self._busy = busy
        for b in self.buttons:
            b.state(["disabled" if busy else "!disabled"])
        self.update_idletasks()

    def run_threaded(self, func):
        if self._busy:
            return
        t = threading.Thread(target=self._safe_wrapper, args=(func,))
        t.daemon = True
        t.start()

    def _safe_wrapper(self, func):
        try:
            self.set_busy(True)
            func()
        except Exception as e:
            self.log(f"Unexpected error: {e}")
        finally:
            self.set_busy(False)

    # -------------------------------------------------
    #  Fibre Position Save/Load
    # -------------------------------------------------
    def save_fibre_position(self):
        try:
            x = float(self.fibre_x_entry.get())
            y = float(self.fibre_y_entry.get())
            with open(LOCK_FILE, "w") as f:
                json.dump({"x": x, "y": y}, f)
            self.log(f"Saved fibre lock position ({x:.1f}, {y:.1f})")
        except ValueError:
            self.log("Invalid input. Please enter numeric X/Y values.")
        except Exception as e:
            self.log(f"Error saving fibre position: {e}")

    def load_fibre_position(self):
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE) as f:
                    pos = json.load(f)
                self.fibre_x_entry.delete(0, "end")
                self.fibre_y_entry.delete(0, "end")
                self.fibre_x_entry.insert(0, str(pos.get("x", "")))
                self.fibre_y_entry.insert(0, str(pos.get("y", "")))
                self.log(f"Loaded fibre lock position ({pos['x']:.1f}, {pos['y']:.1f})")
            except Exception as e:
                self.log(f"Could not load fibre position: {e}")

    # -------------------------------------------------
    #  PHD2 Command Handlers
    # -------------------------------------------------
    def ensure_connection(self):
        """Ensure PHD2 is connected; fail fast if closed."""
        # Quick port probe with 1 s timeout
        try:
            with socket.create_connection(("127.0.0.1", 4400), timeout=1):
                pass
        except OSError:
            self.log("PHD2 not running (connection refused or timed out).")
            return False

        # If reachable, connect or verify
        try:
            if self.phd is None:
                self.log("Connecting to PHD2...")
                self.phd = Guider(self.host)
                self.phd.Connect()
                self.log("Connected to PHD2.")
                return True
            else:
                try:
                    _ = self.phd.GetStatus()
                    return True
                except Exception as e:
                    if "ConnectionRefusedError" in str(e) or "ConnectionResetError" in str(e):
                        self.log("PHD2 connection lost, attempting reconnect...")
                        self.phd = Guider(self.host)
                        self.phd.Connect()
                        self.log("Reconnected to PHD2.")
                        return True
                    else:
                        self.log(f"PHD2 status warning: {e}")
                        return True
        except Exception as e:
            self.phd = None
            self.log(f"Cannot connect to PHD2: {e}")
            return False

    def get_status(self):
        if not self.ensure_connection():
            return
        try:
            state, avgDist = self.phd.GetStatus()
            self.log(f"Status: {state}, avg dist = {avgDist:.2f}")
        except Exception as e:
            self.log(f"GetStatus error: {e}")

    def start_loop(self):
        if not self.ensure_connection():
            return
        try:
            self.phd.Call("loop")
            self.log("Loop command sent.")
        except Exception as e:
            self.log(f"Loop command failed: {e}")

    def find_star(self):
        if not self.ensure_connection():
            return
        try:
            result = self.phd.FindStar()
            self.log(f"FindStar result: {result}")
        except Exception as e:
            self.log(f"FindStar error: {e}")

    def start_guiding(self):
        if not self.ensure_connection():
            return
        try:
            self.phd.Guide(settlePixels=1.5, settleTime=5.0, settleTimeout=30.0)
            self.log("Guiding started.")
        except Exception as e:
            self.log(f"Guide error: {e}")

    def pause_guiding(self):
        if not self.ensure_connection():
            return
        try:
            self.phd.Pause()
            self.log("Guiding paused.")
        except Exception as e:
            self.log(f"Pause error: {e}")

    def unpause_guiding(self):
        if not self.ensure_connection():
            return
        try:
            self.phd.Unpause()
            self.log("Guiding resumed.")
        except Exception as e:
            self.log(f"Unpause error: {e}")

    def restore_lock(self):
        if not self.ensure_connection():
            return
        try:
            if not os.path.exists(LOCK_FILE):
                self.log("No saved lock position file found.")
                return
            with open(LOCK_FILE) as f:
                pos = json.load(f)
            x, y = float(pos["x"]), float(pos["y"])
            self.phd.SetLockPosition(x, y)
            self.log(f"Restored to saved fibre lock position ({x:.1f}, {y:.1f}).")
    
            # ⚡ Expose fibre lock coordinates to app context
            self.lock_x = x
            self.lock_y = y
            self.fibre_lock = (x, y)
    
        except Exception as e:
            self.log(f"RestoreLockPosition error: {e}")


    def stop_guiding(self):
        if not self.ensure_connection():
            return
        try:
            self.phd.StopCapture()
            self.log("Guiding stopped.")
        except Exception as e:
            self.log(f"StopCapture error: {e}")
            
    # -------------------------------------------------
    #  Backend Helper Methods (non-GUI, for automation)
    # -------------------------------------------------
    def get_stats(self):
        """Return the latest guider statistics from PHD2 (dict)."""
        if not self.ensure_connection():
            return None
        try:
            result = self.phd.GetStats()
            # Expected keys: RMS_RA, RMS_Dec, HFD, StarMass, etc.
            return result
        except Exception as e:
            self.log(f"GetStats error: {e}")
            return None
    
    def get_rms(self):
        """Return combined RMS guiding error in arcseconds."""
        stats = self.get_stats()
        if not stats:
            return None
        try:
            rms_ra = stats.get("RMS_RA", 0.0)
            rms_dec = stats.get("RMS_Dec", 0.0)
            rms_total = (rms_ra ** 2 + rms_dec ** 2) ** 0.5
            return rms_total
        except Exception:
            return None
    
    def get_hfd(self):
        """Return current star HFD (Half-Flux Diameter), if available."""
        stats = self.get_stats()
        if stats and "HFD" in stats:
            return stats["HFD"]
        return None
    
    def is_guiding(self):
        """Return True if PHD2 reports state == 'Guiding'."""
        if not self.ensure_connection():
            return False
        try:
            state, _ = self.phd.GetStatus()
            return state == "Guiding"
        except Exception:
            return False

    # -------------------------------------------------
    #  Exposure Control
    # -------------------------------------------------
    def on_exposure_change(self, event=None):
        self.run_threaded(self.set_exposure)

    def set_exposure(self):
        if not self.ensure_connection():
            return
        try:
            exp_time = float(self.exposure_var.get())
            ms = int(exp_time * 1000)
            self.phd.Call("set_exposure", [ms])

            result = self.phd.Call("get_exposure")
            if isinstance(result, dict) and "exposure" in result:
                current_s = result["exposure"] / 1000.0
            elif isinstance(result, (int, float)):
                current_s = result / 1000.0
            else:
                current_s = exp_time

            self.log(f"PHD2 exposure set to {current_s:.3f} s")
        except Exception as e:
            self.log(f"Set exposure error: {e}")
            
    # -------------------------------------------------
    #  Status Bar Refresh
    # -------------------------------------------------
    def refresh_status_header(self):
        """Update the shared Sequencer/Batch/Guide status bar."""
        try:
            from status_bar import update_status_header
            self._sync_blink = update_status_header(
                self.context,
                self.indicators,
                self._sync_blink
            )
        finally:
            self.after(1000, self.refresh_status_header)

