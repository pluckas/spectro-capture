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


class DomeTab(ttk.Frame):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        self.sync_manager = DomeSyncManager(self.log_message)

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
    def connect_dome(self):
        try:
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
            tk.messagebox.showerror("Dome Connection Error", str(e))

    def disconnect_dome(self):
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
    
        except Exception as e:
            self.log_message(f"Failed to disconnect dome: {e}")

    # ------------------------------------------------------------
    # Sync control
    # ------------------------------------------------------------
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
                if self.dome and self.dome.Connected:
                    self.log_message(f"Dome update: {az}, {moving}, {status}")
    
        except Exception as e:
            self.az_label.config(text="---")
            self.slew_label.config(text="---")
            self.shutter_label.config(text="---")
    
        self.after(self.flash_interval, self._update_status)

    # ------------------------------------------------------------
    # Threaded dome commands
    # ------------------------------------------------------------
    def _threaded_command(self, func, name, delay_after=0):
        def run():
            try:
                func()
                if delay_after:
                    time.sleep(delay_after)
                self.log_message(f"{name} command sent.")
            except Exception as e:
                self.log_message(f"Error executing {name}: {e}")
        threading.Thread(target=run, daemon=True).start()

    def home(self): self._threaded_command(lambda: self.dome.FindHome(), "Home", 1)
    def park(self): self._threaded_command(lambda: self.dome.Park(), "Park", 1)
    def abort(self): self._threaded_command(lambda: self.dome.AbortSlew(), "Abort")
    def open_shutter(self): self._threaded_command(lambda: self.dome.OpenShutter(), "Open Shutter")
    def close_shutter(self): self._threaded_command(lambda: self.dome.CloseShutter(), "Close Shutter")

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
