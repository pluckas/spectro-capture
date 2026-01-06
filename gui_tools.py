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
gui_tools.py
--------------
Utilities and calibration tools tab for the Spectro Capture application.

Provides a collection of auxiliary functions for managing calibration frames,
bias/dark capture sequences, and general file operations. Acts as a utility
interface to support both manual and automated workflows in spectroscopy
imaging.

Key features:
- Capture bias and dark frame sequences using the connected camera
- Calibration folder management and file copy utilities
- Integration with sequencer_logic.py for image acquisition
- Control of external calibration lamps (K8056 relay interface)
- Shared AppContext access for camera, logging, and configuration state
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import threading

from sequencer_logic import expose_and_save
from utils import lamp_on, lamp_off   # added import


class ToolsTab(ttk.Frame):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        self.running = False
        self.thread = None

        # Initialise save folder from context (if available)
        self.save_folder = getattr(self.context, "calibration_path", "") or ""

        self.build_gui()

    def build_gui(self):
        # Outer box for the whole tool
        tool_frame = ttk.LabelFrame(self, text="Bias and Darks", padding=10)
        tool_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
    
        # Row 1 - Save folder + Browse
        folder_frame = ttk.Frame(tool_frame)
        folder_frame.grid(row=0, column=0, sticky="w", pady=5)
        ttk.Label(folder_frame, text="Save folder:").pack(side="left", padx=(0,5))
        
        # Determine initial calibration folder: use context if set, else fallback to existing value
        initial_folder = self.context.calibration_path or self.save_folder
        
        self.folder_entry = ttk.Entry(folder_frame, width=48)
        self.folder_entry.pack(side="left", padx=(0,5))
        self.folder_entry.delete(0, tk.END)
        self.folder_entry.insert(0, initial_folder)
        
        # >>> BEGIN NEW CODE: persist manual edits <<<
        def _apply_manual_path(event=None):
            path = self.folder_entry.get().strip()
            if path:
                try:
                    self.context.set_calibration_path(path)
                except Exception as e:
                    self.context.log(f"Failed to save calibration path: {e}")
        
                # <<< CRITICAL FIX >>>
                self.save_folder = path
        
                self.context.log(f"Calibration folder set manually: {path}")
        
        self.folder_entry.bind("<Return>", _apply_manual_path)
        # >>> END NEW CODE <<<
        
        # Browse button
        def _browse_calib_folder():
            from tkinter import filedialog
            start = self.context.calibration_path or self.folder_entry.get() or "/"
            path = filedialog.askdirectory(initialdir=start)
            if not path:
                return
            self.folder_entry.delete(0, tk.END)
            self.folder_entry.insert(0, path)
        
            # Ensure ToolsTab actually uses the selected folder
            self.save_folder = path
        
            try:
                self.context.set_calibration_path(path)
            except Exception as e:
                self.context.log(f"Failed to save calibration path: {e}")
            self.context.log(f"Calibration folder selected: {path}")
        
        ttk.Button(folder_frame, text="Browse", command=_browse_calib_folder).pack(side="left")
    
        # Row 2 - Bias count input
        bias_frame = ttk.Frame(tool_frame)
        bias_frame.grid(row=1, column=0, sticky="w", pady=5)
        ttk.Label(bias_frame, text="Bias count:").pack(side="left", padx=(0,5))
        self.bias_count = ttk.Entry(bias_frame, width=6)
        self.bias_count.insert(0, "20")
        self.bias_count.pack(side="left")
    
        # Row 3 - Dark count + exposure
        dark_frame = ttk.Frame(tool_frame)
        dark_frame.grid(row=2, column=0, sticky="w", pady=5)
        ttk.Label(dark_frame, text="Dark count:").pack(side="left", padx=(0,5))
        self.dark_count = ttk.Entry(dark_frame, width=6)
        self.dark_count.insert(0, "10")
        self.dark_count.pack(side="left", padx=(0,15))
        ttk.Label(dark_frame, text="Exp (s):").pack(side="left", padx=(0,5))
        self.dark_exp = ttk.Entry(dark_frame, width=6)
        self.dark_exp.insert(0, "300")
        self.dark_exp.pack(side="left")
    
        # Row 4 - Buttons
        btn_frame = ttk.Frame(tool_frame)
        btn_frame.grid(row=3, column=0, sticky="w", pady=10)
        ttk.Button(btn_frame, text="Take Bias and Darks", command=self.start_series).pack(side="left", padx=(0,10))
        ttk.Button(btn_frame, text="Stop", command=self.stop).pack(side="left")
    
        # Row 5 - Status
        status_frame = ttk.Frame(tool_frame)
        status_frame.grid(row=4, column=0, sticky="w", pady=5)
        ttk.Label(status_frame, text="Status:").pack(side="left", padx=(0,5))
        self.status_lbl = ttk.Label(status_frame, text="Idle")
        self.status_lbl.pack(side="left")

        # ------------------------------------------------------------------
        # Manual Lamp Control (added)
        # ------------------------------------------------------------------
        lamp_frame = ttk.LabelFrame(self, text="Manual Lamp Control", padding=10)
        lamp_frame.grid(row=1, column=0, padx=10, pady=(0,10), sticky="ew")

        ttk.Button(lamp_frame, text="Tungsten",
                   command=lambda: threading.Thread(target=self._tungsten_on, daemon=True).start(),
                   width=12).pack(side="left", padx=5, pady=5)
        ttk.Button(lamp_frame, text="Thorium",
                   command=lambda: threading.Thread(target=self._thorium_on, daemon=True).start(),
                   width=12).pack(side="left", padx=5, pady=5)
        ttk.Button(lamp_frame, text="Clear All",
                   command=lambda: threading.Thread(target=self._clear_all, daemon=True).start(),
                   width=12).pack(side="left", padx=5, pady=5)

    # --- Manual Lamp Control handlers (added) ---
    def _tungsten_on(self):
        try:
            lamp_on("tungsten")
            self.context.tung_on = True        # NEW — update indicator
            self.context.thor_on = False       # NEW — enforce mutual exclusivity
            self.context.log("Tungsten lamp ON (manual)")
        except Exception as e:
            self.context.log(f"Manual tungsten lamp control failed: {e}")
            messagebox.showerror("Lamp Control", f"Tungsten lamp error: {e}")

    def _thorium_on(self):
        try:
            lamp_on("thorium")
            self.context.thor_on = True        # NEW — update indicator
            self.context.tung_on = False       # NEW — enforce mutual exclusivity
            self.context.log("Thorium lamp ON (manual)")
        except Exception as e:
            self.context.log(f"Manual thorium lamp control failed: {e}")
            messagebox.showerror("Lamp Control", f"Thorium lamp error: {e}")

    def _clear_all(self):
        try:
            lamp_off()
            self.context.tung_on = False       # NEW — update indicators
            self.context.thor_on = False       # NEW — update indicators
            self.context.log("All lamps cleared (manual)")
        except Exception as e:
            self.context.log(f"Manual clear all failed: {e}")
            messagebox.showerror("Lamp Control", f"Clear all error: {e}")

    def _guard_connected(self):
        if not (self.context.camera and getattr(self.context.camera, "Connected", False)):
            messagebox.showerror("Camera", "Camera not connected")
            return False
        return True

    def start_series(self):
        if not self._guard_connected(): 
            return
        if self.thread and self.thread.is_alive():
            messagebox.showwarning("Running", "A calibration series is already running.")
            return
        self.running = True
        self.thread = threading.Thread(target=self.run_series, daemon=True)
        self.thread.start()

    def run_series(self):
        b = int(self.context.current_binning)

        # --- Bias ---
        count_bias = int(self.bias_count.get())
        self.context.log(f"Bias sequence started ({count_bias} frames)")
        self.status_lbl.config(text="Running bias sequence")

        for i in range(1, count_bias + 1):
            if not self.running: break
            self.context.log(f"Taking bias {i}/{count_bias}")
            expose_and_save(self.context, 0.0, self.save_folder, "bias", i, b, "bias", image_type="Bias Frame")

        self.context.log("Bias sequence finished")

        # --- Dark ---
        if self.running:
            exp_dark = float(self.dark_exp.get())
            count_dark = int(self.dark_count.get())
            self.context.log(f"Dark sequence started ({count_dark} frames, {exp_dark}s)")
            self.status_lbl.config(text="Running dark sequence")

            for i in range(1, count_dark + 1):
                if not self.running: break
                self.context.log(f"Taking dark {i}/{count_dark}")
                expose_and_save(self.context, exp_dark, self.save_folder, "dark", i, b, "dark", image_type="Dark Frame")

            self.context.log("Dark sequence finished")

        if self.running:
            self.status_lbl.config(text="Calibration series finished")
            self.context.log("Calibration series finished")
        else:
            self.status_lbl.config(text="Stopped")
            self.context.log("Calibration series stopped")

        self.running = False
        self.context.set_status("Idle")


    def stop(self):
        if not self.running:
            # Nothing is running → don’t get stuck in "Stopping"
            self.context.log("Stop requested, but no series is running.")
            self.status_lbl.config(text="Idle")
            return
    
        # Normal stop if a series is running
        self.running = False
        self.status_lbl.config(text="Stopping...")
        self.context.log("Stop requested in Tools tab")
        self.context.set_status("Idle")