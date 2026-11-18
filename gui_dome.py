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
gui_dome.py
------------
Dome control tab for the Spectro Capture application.

Provides the user interface and backend linkage for controlling and monitoring
the observatory dome. Integrates with ASCOM via the DomeSyncManager class to
maintain dome–telescope synchronization, manage shutter state, and apply
geometric offsets for accurate dome positioning.

Key features:
- Dome azimuth display and movement control
- Shutter open/close commands with live status
- Automatic dome–telescope sync toggle
- Configurable geometric offsets (East/West, North/South, Up/Down, Optical)
- Background polling thread for smooth, responsive updates
- Shared AppContext integration for persistent configuration and logging
"""

import tkinter as tk
from ttkbootstrap import ttk
import threading
import time
import win32com.client

from dome_backend import DomeSyncManager, SHUTTER_STATUS
from web_monitor import report


class DomeTab(ttk.Frame):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        self.sync_manager = DomeSyncManager(self.log_message)
        self._motion_lock = threading.Lock()

        # Load dome settings from shared config
        defaults = {
            "latitude": -31.95917,
            "dome_radius": 1.15,
            "offset_east": 0.023,
            "offset_north": 0.0,
            "offset_up": 0.0,
            "optical_offset": -0.127
        }
        self.settings = context.cfg.get("dome", defaults)
        self.sync_manager.settings = self.settings

        # Flashing state for shutter label
        self.shutter_flash_state = True
        self.flash_interval = 500  # ms

        self._build_ui()
        self._update_status()

    # ------------------------------------------------------------
    # UI
    # ------------------------------------------------------------
    def _build_ui(self):
        # --- Connection section ---
        conn = ttk.LabelFrame(self, text="Connection", padding=10)
        conn.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
    
        self.btn_connect = ttk.Button(conn, text="Connect Dome", command=self.connect_dome)
        self.btn_connect.pack(side="left", padx=5, pady=5)
    
        self.btn_disconnect = ttk.Button(conn, text="Disconnect", command=self.disconnect_dome, state="disabled")
        self.btn_disconnect.pack(side="left", padx=5, pady=5)
    
        self.sync_var = tk.BooleanVar()
        self.sync_check = ttk.Checkbutton(conn, text="Telescope Sync", variable=self.sync_var, command=self.toggle_sync)
        self.sync_check.pack(side="left", padx=15, pady=5)
    
        self.btn_settings = ttk.Button(conn, text="Settings", command=self.open_settings)
        self.btn_settings.pack(side="right", padx=5, pady=5)
    
        # --- Dome controls section ---
        controls = ttk.LabelFrame(self, text="Dome Controls", padding=10)
        controls.grid(row=1, column=0, padx=10, pady=10, sticky="ew")
    
        # Row 1 – main motion
        row1 = ttk.Frame(controls)
        row1.pack(fill="x", pady=5)
        self.btn_home = ttk.Button(row1, text="Home", command=self.home, state="disabled")
        self.btn_home.pack(side="left", padx=5)
        self.btn_park = ttk.Button(row1, text="Park", command=self.park, state="disabled")
        self.btn_park.pack(side="left", padx=5)
        self.btn_abort = ttk.Button(row1, text="Abort", command=self.abort, state="disabled")
        self.btn_abort.pack(side="left", padx=5)
    
        # Row 2 – shutter control
        row2 = ttk.Frame(controls)
        row2.pack(fill="x", pady=5)
        self.btn_open = ttk.Button(row2, text="Open Shutter", command=self.open_shutter, state="disabled")
        self.btn_open.pack(side="left", padx=5)
        self.btn_close = ttk.Button(row2, text="Close Shutter", command=self.close_shutter, state="disabled")
        self.btn_close.pack(side="left", padx=5)
    
        # --- Status section ---
        status = ttk.LabelFrame(self, text="Status", padding=10)
        status.grid(row=2, column=0, padx=10, pady=10, sticky="ew")
    
        ttk.Label(status, text="Azimuth:").pack(side="left", padx=(0, 5))
        self.az_label = ttk.Label(status, text="---", width=8)
        self.az_label.pack(side="left", padx=(0, 15))
    
        ttk.Label(status, text="Dome:").pack(side="left", padx=(0, 5))
        self.slew_label = ttk.Label(status, text="---", width=12)
        self.slew_label.pack(side="left", padx=(0, 15))
    
        ttk.Label(status, text="Shutter:").pack(side="left", padx=(0, 5))
        self.shutter_label = ttk.Label(status, text="---", width=8)
        self.shutter_label.pack(side="left", padx=(0, 15))

    # ------------------------------------------------------------
    # Logging helper (uses shared context)
    # ------------------------------------------------------------
    def log_message(self, msg):
        if "Dome update" in msg:
            return
        self.context.log(msg)

    # ------------------------------------------------------------
    # Dome connection
    # ------------------------------------------------------------
    def connect_dome(self, *, from_browser=False):
        try:
            if self.context.dome and self.context.dome.Connected:
                return "Dome already connected."
            self.dome = win32com.client.Dispatch("MaxDome64.Dome")
            self.dome.Connected = True
    
            # ===== NEW: Verify that hardware actually connected =====
            if not self.dome.Connected:
                raise RuntimeError("Dome not connected (driver loaded but hardware not detected).")
            # ===== END NEW =====
    
            self.sync_manager.set_dome(self.dome)
            self.context.dome = self.dome
            self._update_button_states()
            self.log_message("Dome connected.")
            return "Dome connected."
    
        except Exception as e:
            # Cleanup on failure
            try:
                if hasattr(self, "dome") and self.dome:
                    self.dome.Connected = False
            except Exception:
                pass
    
            self.context.dome = None
            self._update_button_states()
            self.log_message(f"Failed to connect dome: {e}")
            if from_browser:
                raise
            tk.messagebox.showerror("Dome Connection Error", str(e))

    def disconnect_dome(self, *, from_browser=False):
        try:
            if hasattr(self, "dome") and self.dome:
                self.dome.Connected = False
    
            # --- Stop sync and clear both GUI + shared context flags ---
            self.sync_manager.stop()
            self.sync_var.set(False)
            self.context.dome_sync_enabled = False   # ✅ force Sequencer sync dot to red
    
            self.context.dome = None
            self._update_button_states()
            self.log_message("Dome disconnected (sync disabled).")
            return "Dome disconnected."
    
        except Exception as e:
            self.log_message(f"Failed to disconnect dome: {e}")
            if from_browser:
                raise

    # ------------------------------------------------------------
    # Sync control
    # ------------------------------------------------------------
    def sync_running(self):
        thread = self.sync_manager.thread
        return bool(thread and thread.is_alive())

    def run_web_dome_command(self, action, _setpoint):
        if action != "abort" and self.context.camera_work_active():
            raise RuntimeError("Dome controls are unavailable while an acquisition is active.")
        if action == "connect":
            return self.connect_dome(from_browser=True)

        dome = self.context.dome
        if not dome or not dome.Connected:
            raise RuntimeError("Dome is disconnected.")

        if action == "abort":
            return self._send_motion("abort")

        sync_running = self.sync_running()
        if action == "sync_off":
            self.sync_var.set(False)
            self.toggle_sync()
            return "Dome sync stopping." if sync_running else "Dome sync is off."
        if action == "sync_on" and self.sync_var.get() and sync_running:
            return "Dome sync is already enabled."
        if action in ("disconnect", "home", "park", "sync_on") and sync_running:
            raise RuntimeError("Turn off dome sync and wait for it to stop first.")

        if not self._motion_lock.acquire(blocking=False):
            raise RuntimeError("A dome command is already in progress.")
        try:
            shutter = dome.ShutterStatus
            if shutter in (2, 3) or dome.Slewing:
                raise RuntimeError("Wait for dome or shutter movement to finish.")
            if action == "disconnect":
                return self.disconnect_dome(from_browser=True)
            if action == "sync_on":
                telescope = self.context.telescope
                if not telescope or not telescope.Connected:
                    raise RuntimeError("Connect the telescope before enabling dome sync.")
                self.sync_var.set(True)
                self.toggle_sync()
                return "Dome sync start requested."
            if action in ("open", "close"):
                if shutter not in (0, 1):
                    raise RuntimeError("Check the shutter state in the desktop Dome tab.")
                if shutter == (0 if action == "open" else 1):
                    return f"Shutter already {SHUTTER_STATUS[shutter].lower()}."
            return self._send_motion(action)
        finally:
            self._motion_lock.release()

    def toggle_sync(self):
        """Handle Dome–Telescope sync checkbox toggle."""
        state = self.sync_var.get()
    
        # ✅ Update the shared context flag so the Sequencer tab can show green/red
        self.context.dome_sync_enabled = state
    
        if state:
            self.btn_home.config(state="disabled")
            self.btn_park.config(state="disabled")
            self.sync_manager.start()
            self.log_message("Dome–Telescope sync enabled.")
        else:
            self.sync_manager.stop()
            self.log_message("Dome–Telescope sync disabled.")
            self._wait_for_sync_stop()

    def _wait_for_sync_stop(self):
        def poll():
            if self.sync_manager.thread and self.sync_manager.thread.is_alive():
                self.after(100, poll)
            else:
                self._update_button_states()
        poll()

    # ------------------------------------------------------------
    # Button state management
    # ------------------------------------------------------------
    def _update_button_states(self):
        if not hasattr(self, "dome") or not self.dome or not self.dome.Connected:
            for b in [self.btn_home, self.btn_park, self.btn_abort,
                      self.btn_open, self.btn_close, self.btn_disconnect]:
                b.config(state="disabled")
            self.btn_connect.config(state="normal")
            return

        connected = self.dome.Connected
        self.btn_connect.config(state="disabled" if connected else "normal")
        self.btn_disconnect.config(state="normal" if connected else "disabled")

        sync_active = self.sync_var.get()
        self.btn_home.config(state="disabled" if sync_active else "normal")
        self.btn_park.config(state="disabled" if sync_active else "normal")

        for b in [self.btn_abort, self.btn_open, self.btn_close]:
            b.config(state="normal" if connected else "disabled")

    # ------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------
    def _update_status(self):
        try:
            if not hasattr(self, "dome") or not self.dome or not self.dome.Connected:
                az = "---"
                moving = "---"
                status = "---"
            else:
                az = f"{self.dome.Azimuth:.1f}°"
                moving = "Moving" if self.dome.Slewing else "Stationary"
                status = SHUTTER_STATUS.get(self.dome.ShutterStatus, "Error")
    
            # Update GUI labels
            self.az_label.config(text=az)
            self.slew_label.config(text=moving)
            self.shutter_label.config(text=status)
    
            # Only log when state actually changes
            state_tuple = (az, moving, status)
            if state_tuple != getattr(self, "_last_state", None):
                self._last_state = state_tuple
                if getattr(self, "dome", None) and self.dome.Connected:
                    self.log_message(f"Dome update: {az}, {moving}, {status}")
    
        except Exception as e:
            self.az_label.config(text="---")
            self.slew_label.config(text="---")
            self.shutter_label.config(text="---")
            az = moving = status = "---"

        report(self.context, "publish", dome_status={
            "azimuth": az, "motion": moving, "shutter": status,
            "sync": self.sync_running(),
        })
        self.after(self.flash_interval, self._update_status)

    # ------------------------------------------------------------
    # Threaded dome commands
    # ------------------------------------------------------------
    def _send_motion(self, action, delay_after=0):
        name, method = {
            "home": ("Home", "FindHome"),
            "park": ("Park", "Park"),
            "abort": ("Abort", "AbortSlew"),
            "open": ("Open Shutter", "OpenShutter"),
            "close": ("Close Shutter", "CloseShutter"),
        }[action]
        getattr(self.dome, method)()
        if delay_after:
            time.sleep(delay_after)
        message = f"{name} command sent."
        self.log_message(message)
        return message

    def _threaded_command(self, action, delay_after=0):
        def run():
            try:
                if action == "abort":
                    self._send_motion(action)
                else:
                    if not self._motion_lock.acquire(blocking=False):
                        raise RuntimeError("A dome command is already in progress.")
                    try:
                        self._send_motion(action, delay_after)
                    finally:
                        self._motion_lock.release()
            except Exception as e:
                self.log_message(f"Error executing dome {action}: {e}")
        threading.Thread(target=run, daemon=True).start()

    def home(self): self._threaded_command("home", 1)
    def park(self): self._threaded_command("park", 1)
    def abort(self): self._threaded_command("abort")
    def open_shutter(self): self._threaded_command("open")
    def close_shutter(self): self._threaded_command("close")

    # ------------------------------------------------------------
    # Settings window
    # ------------------------------------------------------------
    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("Dome Settings")
        win.resizable(False, False)

        fields = [
            ("Latitude", "latitude"),
            ("Dome Radius (m)", "dome_radius"),
            ("Offset East (m)", "offset_east"),
            ("Offset North (m)", "offset_north"),
            ("Offset Up (m)", "offset_up"),
            ("Optical Dec Offset (m)", "optical_offset"),
        ]

        entries = {}
        for i, (label, key) in enumerate(fields):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=10, pady=3)
            entry = ttk.Entry(win)
            entry.insert(0, str(self.settings[key]))
            entry.grid(row=i, column=1, padx=10, pady=3)
            entries[key] = entry

        def save_and_close():
            try:
                for key in entries:
                    self.settings[key] = float(entries[key].get())
                self.context.cfg["dome"] = self.settings
                self.context.save_config()
                win.destroy()
                self.log_message("Dome settings saved.")
            except ValueError:
                self.log_message("Invalid value in dome settings.")

        ttk.Button(win, text="Save", command=save_and_close).grid(
            row=len(fields), column=0, columnspan=2, pady=10)

    # ------------------------------------------------------------
    # Cleanup (called on app shutdown)
    # ------------------------------------------------------------
    def cleanup(self):
        self.sync_manager.stop()
        try:
            if hasattr(self, "dome") and self.dome:
                self.dome.Connected = False
        except Exception:
            pass
