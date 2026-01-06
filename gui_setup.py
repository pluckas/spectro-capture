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
gui_setup.py
--------------
Device setup and connection tab for the Spectro Capture application.

Provides controls for connecting to ASCOM devices such as the telescope and
camera, managing cooler settings, and configuring camera binning and sensor
parameters. Serves as the main hardware configuration interface for the system.

Key features:
- ASCOM device connection and chooser dialogs
- Camera and telescope connect/disconnect controls
- Cooler ON/OFF and temperature monitoring
- Binning and sensor configuration controls
- Threaded polling of camera status for responsive UI updates
- Shared AppContext integration for device state, logging, and persistence
"""

import tkinter as tk
from tkinter import ttk, messagebox

# ASCOM/COM is Windows-only; this tab is meant for the observatory PC.
import pythoncom
import win32com.client

class SetupTab(ttk.Frame):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        self._camera_progid = self.context.cfg.get("last_camera")
        self._telescope_progid = self.context.cfg.get("last_telescope")

        # ===== BEGIN ROI STATE BINDINGS =====
        # Bind ROI values from AppContext to Tk variables for the GUI.
        # If stored values are 0 (i.e. not yet configured), show blanks.
        cx_val = ""
        cy_val = ""
        size_val = ""

        try:
            if getattr(self.context, "roi_centre_x", 0):
                cx_val = str(self.context.roi_centre_x)
            if getattr(self.context, "roi_centre_y", 0):
                cy_val = str(self.context.roi_centre_y)
            if getattr(self.context, "roi_size", 0):
                size_val = str(self.context.roi_size)
        except Exception:
            # If anything odd happens, just leave them blank
            pass

        self.roi_cx_var = tk.StringVar(value=cx_val)
        self.roi_cy_var = tk.StringVar(value=cy_val)
        self.roi_size_var = tk.StringVar(value=size_val)
        # ===== END ROI STATE BINDINGS =====

        self.build_gui()
        # Restore remembered selections
        self.restore_last_devices()
        # Periodic temperature polling
        self.after(1000, self.refresh_temp)
        
    def set_new_setpoint(self, event=None):
            """Apply a new setpoint immediately if the cooler is already on."""
            try:
                if self.context.camera and getattr(self.context.camera, "Connected", False):
                    sp = float(self.setpoint_entry.get())
                    self.context.cfg["last_setpoint"] = sp
                    self.context.save_config()
        
                    if getattr(self.context.camera, "CoolerOn", False):
                        # Cooler already running — apply new target directly
                        self.context.camera.SetCCDTemperature = sp
                        self.context.log(f"Cooler already ON → new setpoint {sp} °C applied")
                    else:
                        # Cooler off — just remember it
                        self.context.log(f"Cooler OFF → setpoint {sp} °C stored (will apply when turned ON)")
            except Exception as e:
                self.context.log(f"Setpoint update failed: {e}")    

    def build_gui(self):
        # --- Camera ---
        cam = ttk.LabelFrame(self, text="Camera", padding=10)
        cam.grid(row=0, column=0, padx=10, pady=6, sticky="ew")
    
        self.choose_cam_btn = ttk.Button(cam, text="Choose…", command=self.choose_camera)
        self.choose_cam_btn.grid(row=0, column=0, padx=5, pady=5)
    
        self.connect_cam_btn = ttk.Button(cam, text="Connect", command=self.connect_camera, state="disabled")
        self.connect_cam_btn.grid(row=0, column=1, padx=5, pady=5)
    
        self.disconnect_cam_btn = ttk.Button(cam, text="Disconnect", command=self.disconnect_camera, state="disabled")
        self.disconnect_cam_btn.grid(row=0, column=2, padx=5, pady=5)
    
        self.cam_status = ttk.Label(cam, text="No camera chosen")
        self.cam_status.grid(row=0, column=3, padx=6, sticky="w")
    
        ttk.Label(cam, text="Setpoint (°C):").grid(row=1, column=0, sticky="w", padx=5)
        self.setpoint_entry = ttk.Entry(cam, width=6)
        self.setpoint_entry.insert(0, str(self.context.cfg.get("last_setpoint", -10)))
        self.setpoint_entry.grid(row=1, column=1, sticky="w")
        self.setpoint_entry.bind("<Return>", self.set_new_setpoint)
    
        self.cool_on_btn  = ttk.Button(cam, text="Cooler ON", command=self.cooler_on, state="disabled")
        self.cool_on_btn.grid(row=1, column=2, padx=5)
        self.cool_off_btn = ttk.Button(cam, text="Cooler OFF", command=self.cooler_off, state="disabled")
        self.cool_off_btn.grid(row=1, column=3, padx=5)
    
        self.temp_lbl = ttk.Label(cam, text="T: -- °C | Pwr: -- %")
        self.temp_lbl.grid(row=1, column=4, padx=10)
        
        ttk.Label(cam, text="Binning:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.binning_var = tk.StringVar(value=str(self.context.current_binning))
        self.binning_combo = ttk.Combobox(
            cam, textvariable=self.binning_var, values=["1", "2", "3"], width=5, state="readonly"
        )
        self.binning_combo.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        self.binning_combo.bind("<<ComboboxSelected>>", self._update_binning)
    
        # --- Telescope ---
        tel = ttk.LabelFrame(self, text="Telescope", padding=10)
        tel.grid(row=1, column=0, padx=10, pady=6, sticky="ew")

        self.choose_tel_btn = ttk.Button(tel, text="Choose…", command=self.choose_telescope)
        self.choose_tel_btn.grid(row=0, column=0, padx=5, pady=5)

        self.connect_tel_btn = ttk.Button(tel, text="Connect", command=self.connect_telescope, state="disabled")
        self.connect_tel_btn.grid(row=0, column=1, padx=5, pady=5)

        self.disconnect_tel_btn = ttk.Button(tel, text="Disconnect", command=self.disconnect_telescope, state="disabled")
        self.disconnect_tel_btn.grid(row=0, column=2, padx=5, pady=5)

        self.tel_status = ttk.Label(tel, text="No telescope chosen")
        self.tel_status.grid(row=0, column=3, padx=6, sticky="w")
        
        # --- ROI Batch Settings ---
        roi_frame = ttk.LabelFrame(self, text="ROI Guide Settings", padding=10)
        roi_frame.grid(row=2, column=0, padx=10, pady=6, sticky="ew")
        
        ttk.Label(roi_frame, text="ROI centre X:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.roi_cx_entry = ttk.Entry(roi_frame, width=8, textvariable=self.roi_cx_var)
        self.roi_cx_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        ttk.Label(roi_frame, text="ROI centre Y:").grid(row=0, column=2, padx=5, pady=5, sticky="e")
        self.roi_cy_entry = ttk.Entry(roi_frame, width=8, textvariable=self.roi_cy_var)
        self.roi_cy_entry.grid(row=0, column=3, padx=5, pady=5, sticky="w")
        
        ttk.Label(roi_frame, text="ROI size (px):").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.roi_size_entry = ttk.Entry(roi_frame, width=8, textvariable=self.roi_size_var)
        self.roi_size_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        
        self.save_roi_btn = ttk.Button(roi_frame, text="Save ROI", command=self.save_roi_config)
        self.save_roi_btn.grid(row=1, column=3, padx=5, pady=5, sticky="e")
        
        # ===== NEW: ROI auto-measured average display =====
        ttk.Label(roi_frame, text="Auto-measured average:").grid(
            row=2, column=0, padx=5, pady=5, sticky="e"
        )
        self.roi_avg_label = ttk.Label(roi_frame, text="(no data yet)")
        self.roi_avg_label.grid(row=2, column=1, columnspan=3, padx=5, pady=5, sticky="w")
        
        # Initialise from any stored average on startup
        try:
            self.update_roi_average_label()
        except Exception:
            pass
        # ==================================================
        
        # --- Image Root Path ------------------------------------------------
        root_frame = ttk.LabelFrame(self, text="Image Root Path", padding=10)
        root_frame.grid(row=3, column=0, padx=10, pady=6, sticky="ew")
        
        # Value from config, if present
        current_root = self.context.cfg.get("root_path", "")
        self.root_path_var = tk.StringVar(value=current_root)
        
        # Entry box
        self.root_entry = ttk.Entry(root_frame, textvariable=self.root_path_var, width=50)
        self.root_entry.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        
        # Browse button
        def browse_root_path():
            from tkinter import filedialog
        
            # >>> BEGIN NEW CODE: open browse dialog at saved root_path <<<
            initial = self.context.root_path or self.root_path_var.get() or "/"
            path = filedialog.askdirectory(initialdir=initial)
            # >>> END NEW CODE <<<
        
            if path:
                self.root_path_var.set(path)
        
                # Persist user-selected path in AppContext + JSON
                try:
                    self.context.set_root_path(path)
                except Exception as e:
                    self.context.log(f"Failed to save root path: {e}")
        
                self.context.log(f"Selected image root path: {path}")
        
        ttk.Button(root_frame, text="Browse…", command=browse_root_path).grid(
            row=0, column=1, padx=5, pady=5, sticky="w"
        )

    # -----------------------
    # Camera handling
    # -----------------------
    def choose_camera(self):
        try:
            pythoncom.CoInitialize()
            chooser = win32com.client.Dispatch("ASCOM.Utilities.Chooser")
            chooser.DeviceType = "Camera"
            progid = chooser.Choose(None)
            if progid:
                self._camera_progid = progid
                self.cam_status.config(text=f"Chosen: {progid}")
                self.connect_cam_btn.config(state="normal")
                self.context.cfg["last_camera"] = progid
                self.context.save_config()
                self.context.log(f"Chosen camera: {progid}")
        except Exception as e:
            self.context.log(f"Camera chooser failed: {e}")

    def connect_camera(self):
        if not self._camera_progid:
            messagebox.showerror("Error", "No camera selected"); return
        try:
            self.context.camera = win32com.client.Dispatch(self._camera_progid)
            self.context.camera.Connected = True
            self.cam_status.config(text="Camera connected")
            self.connect_cam_btn.config(state="disabled")
            self.disconnect_cam_btn.config(state="normal")
            self.cool_on_btn.config(state="normal")
            self.cool_off_btn.config(state="normal")
            self.context.log("ASCOM camera connected.")
        except Exception as e:
            self.context.log(f"Camera connect failed: {e}")
            messagebox.showerror("Camera", f"Connect failed:\n{e}")

    def disconnect_camera(self):
        try:
            if self.context.camera:
                self.context.camera.Connected = False
                self.context.camera = None
            self.cam_status.config(text="Camera disconnected")
            self.connect_cam_btn.config(state="normal")
            self.disconnect_cam_btn.config(state="disabled")
            self.cool_on_btn.config(state="disabled")
            self.cool_off_btn.config(state="disabled")
            self.temp_lbl.config(text="T: -- °C | Pwr: -- %")
            self.context.log("ASCOM camera disconnected.")
        except Exception as e:
            self.context.log(f"Camera disconnect failed: {e}")

    # Cooling
    def cooler_on(self):
        try:
            if self.context.camera and getattr(self.context.camera, "CanSetCCDTemperature", False):
                sp = float(self.setpoint_entry.get())
                self.context.camera.CoolerOn = True
                self.context.camera.SetCCDTemperature = sp
                self.context.cfg["last_setpoint"] = sp
                self.context.save_config()
                self.context.log(f"Cooler ON → setpoint {sp} °C")
        except Exception as e:
            self.context.log(f"Cooling error: {e}")

    def cooler_off(self):
        try:
            if self.context.camera:
                self.context.camera.CoolerOn = False
                self.context.log("Cooler OFF")
        except Exception as e:
            self.context.log(f"Cooling error: {e}")

    def refresh_temp(self):
        try:
            if self.context.camera and getattr(self.context.camera, "Connected", False):
                t = getattr(self.context.camera, "CCDTemperature", None)
                p = getattr(self.context.camera, "CoolerPower", None)
                s = getattr(self.context.camera, "SetCCDTemperature", None)  # get target setpoint
    
                t_str = f"{t:.1f}" if isinstance(t, (float, int)) else "--"
                p_str = f"{p:.0f}" if isinstance(p, (float, int)) else "--"
                s_str = f"{s:.1f}" if isinstance(s, (float, int)) else "--"
    
                self.temp_lbl.config(text=f"T: {t_str} °C | Set: {s_str} °C | Pwr: {p_str} %")
        except Exception:
            pass
        # poll again
        self.after(1000, self.refresh_temp)

    # -----------------------
    # Telescope handling
    # -----------------------
    def choose_telescope(self):
        try:
            pythoncom.CoInitialize()
            chooser = win32com.client.Dispatch("ASCOM.Utilities.Chooser")
            chooser.DeviceType = "Telescope"
            progid = chooser.Choose(None)
            if progid:
                self._telescope_progid = progid
                self.tel_status.config(text=f"Chosen: {progid}")
                self.connect_tel_btn.config(state="normal")
                self.context.cfg["last_telescope"] = progid
                self.context.save_config()
                self.context.log(f"Chosen telescope: {progid}")
        except Exception as e:
            self.context.log(f"Telescope chooser failed: {e}")

    def connect_telescope(self):
        if not self._telescope_progid:
            messagebox.showerror("Error", "No telescope selected"); return
        try:
            self.context.telescope = win32com.client.Dispatch(self._telescope_progid)
            self.context.telescope.Connected = True
            self.tel_status.config(text="Telescope connected")
            self.connect_tel_btn.config(state="disabled")
            self.disconnect_tel_btn.config(state="normal")
            self.context.log("ASCOM telescope connected.")
        except Exception as e:
            self.context.log(f"Telescope connect failed: {e}")
            messagebox.showerror("Telescope", f"Connect failed:\n{e}")

    def disconnect_telescope(self):
        try:
            if self.context.telescope:
                self.context.telescope.Connected = False
                self.context.telescope = None
            self.tel_status.config(text="Telescope disconnected")
            self.connect_tel_btn.config(state="normal")
            self.disconnect_tel_btn.config(state="disabled")
            self.context.log("ASCOM telescope disconnected.")
        except Exception as e:
            self.context.log(f"Telescope disconnect failed: {e}")

    # -----------------------
    # Binning
    # -----------------------
    def _update_binning(self, _evt=None):
        try:
            b = int(self.binning_var.get())
        except Exception:
            b = 2
        self.context.current_binning = b
        self.context.cfg["binning"] = b
        self.context.save_config()
    
    # -----------------------
    # ROI configuration
    # -----------------------
    def save_roi_config(self):
        """Save ROI centre and size to AppContext and spectro_config.json."""
        try:
            cx = int(self.roi_cx_var.get())
            cy = int(self.roi_cy_var.get())
            size = int(self.roi_size_var.get())
    
            if "roi" not in self.context.cfg or not isinstance(self.context.cfg["roi"], dict):
                self.context.cfg["roi"] = {}
    
            self.context.cfg["roi"]["centre_x"] = cx
            self.context.cfg["roi"]["centre_y"] = cy      # FIXED
            self.context.cfg["roi"]["size"] = size
    
            # Update live context so other modules see the new values immediately.
            self.context.roi_centre_x = cx
            self.context.roi_centre_y = cy
            self.context.roi_size = size
    
            self.context.save_config()
            self.context.log(f"ROI updated: centre=({cx}, {cy}), size={size}")
    
        except ValueError:
            messagebox.showerror("ROI", "ROI values must be integers.")
        except Exception as e:
            self.context.log(f"ROI config save failed: {e}")
            messagebox.showerror("ROI", f"Could not save ROI settings:\n{e}")
    
    # ===== BEGIN NEW: ROI average update method =====
    def update_roi_average_label(self):
        """
        Called by auto_capture / batch_runner when a star is found.
        Displays a running average of auto-measured X/Y positions.
        """
        try:
            avg = self.context.roi_auto_average
            if avg is None:
                self.roi_avg_label.config(text="(no data yet)")
                return
    
            cx, cy = avg
            # --- CHANGE: whole-number display (rounded) ---
            self.roi_avg_label.config(text=f"({int(round(cx))}, {int(round(cy))})")
        except Exception:
            self.roi_avg_label.config(text="(error)")
    # ===== END NEW =====
    
    # -----------------------
    # Restore remembered devices
    # -----------------------
    def restore_last_devices(self):

        if self._camera_progid:
            # self.cam_status.config(text=f"Chosen (last): {self._camera_progid}")
            self.connect_cam_btn.config(state="normal")
        if self._telescope_progid:
            # self.tel_status.config(text=f"Chosen (last): {self._telescope_progid}")
            self.connect_tel_btn.config(state="normal")

