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
gui_sequencer.py
----------------
Sequencer tab for the Spectro Capture application.

Provides the main user interface for controlling image sequences,
including calibration and science target captures. Integrates with
the camera, telescope, guider, and dome subsystems via AppContext.

Key features:
- Manual and automated (Auto Capture) imaging runs
- Real-time status updates and logging
- Threaded sequencing to keep the UI responsive
- Interaction with auto_capture.py for full target automation
- Coordination with phd2_control.py for guiding management
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import os
import tempfile
from astropy.io import fits
import numpy as np

from utils import nightly_folder, next_run_folder, copy_latest_calib_to, lamp_on, lamp_off, SafeLogMixin
from sequencer_logic import expose_and_save
from status_bar import add_status_header, update_status_header

class SequencerTab(ttk.Frame, SafeLogMixin):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        context.sequencer = self

        # --- Header row (shared) ---
        self._sync_blink = False
        self.indicators = add_status_header(self, layout="grid")

        # --- Target input row ---
        target_row = ttk.Frame(self)
        target_row.grid(row=1, column=0, columnspan=7, sticky="ew", pady=5)
        
        ttk.Label(target_row, text="Target:").pack(side="left", padx=(5,2))
        self.target_entry = ttk.Entry(target_row, width=25)
        self.target_entry.pack(side="left", padx=5)
        
        # --- NEW: Manual / Auto mode switch next to target box ---
        self.mode_var = tk.StringVar(value="manual")
        ttk.Label(target_row, text="Mode:").pack(side="left", padx=(10,2))
        ttk.Radiobutton(
            target_row, text="Manual", value="manual", variable=self.mode_var,
            command=self.update_mode
        ).pack(side="left", padx=(2,2))
        ttk.Radiobutton(
            target_row, text="Auto", value="auto", variable=self.mode_var,
            command=self.update_mode
        ).pack(side="left", padx=(2,2))


        # --- Calibration ---
        calib = ttk.LabelFrame(self, text="Calibration (Tungsten + Thorium)", padding=10)
        calib.grid(row=2, column=0, columnspan=7, padx=5, pady=5, sticky="ew")

        ttk.Label(calib, text="Tung exp (s):").grid(row=0, column=0)
        self.tung_exp = ttk.Entry(calib, width=5); self.tung_exp.insert(0, "15"); self.tung_exp.grid(row=0, column=1)
        ttk.Label(calib, text="Count:").grid(row=0, column=2)
        self.tung_count = ttk.Entry(calib, width=5); self.tung_count.insert(0, "41"); self.tung_count.grid(row=0, column=3)

        ttk.Label(calib, text="Thor exp (s):").grid(row=1, column=0)
        self.thor_exp = ttk.Entry(calib, width=5); self.thor_exp.insert(0, "15"); self.thor_exp.grid(row=1, column=1)
        ttk.Label(calib, text="Count:").grid(row=1, column=2)
        self.thor_count = ttk.Entry(calib, width=5); self.thor_count.insert(0, "15"); self.thor_count.grid(row=1, column=3)

        self.run_calib_btn = ttk.Button(calib, text="Run Calibration", command=self.run_calibration)
        self.run_calib_btn.grid(row=2, column=0, columnspan=4, pady=5)

        # --- Top row: Target Frames + Auto Capture ---
        row = ttk.Frame(self)
        row.grid(row=3, column=0, columnspan=7, padx=5, pady=5, sticky="ew")
        
        # --- Target Frames box (left) ---
        targ = ttk.LabelFrame(row, text="Target Frames", padding=10)
        targ.pack(side="left", fill="x", expand=True, padx=(0,5))
        
        ttk.Label(targ, text="Exp (s):").grid(row=0, column=0)
        self.targ_exp = ttk.Entry(targ, width=5)
        self.targ_exp.insert(0, "600")
        self.targ_exp.grid(row=0, column=1)
        
        ttk.Label(targ, text="Count:").grid(row=0, column=2)
        self.targ_count = ttk.Entry(targ, width=5)
        self.targ_count.insert(0, "6")
        self.targ_count.grid(row=0, column=3)
        
        self.run_target_btn = ttk.Button(targ, text="Run Target", command=self.run_target)
        self.run_target_btn.grid(row=1, column=0, columnspan=4, pady=5)
        
        self.add_exposure_btn = ttk.Button(targ, text="Add Frames", command=self.add_exposure)
        self.add_exposure_btn.grid(row=1, column=4, padx=(10,0))
        
        # --- Auto Capture (right) ---
        from auto_capture import run_auto_capture  # local import to avoid circular imports
        
        auto_frame = ttk.LabelFrame(row, text="Auto Capture", padding=10)
        auto_frame.pack(side="left", fill="y", padx=(5,0), ipady=10)
        
        self.include_calibs = tk.BooleanVar(value=True)
        
        def update_include_calibs():
            self.context.include_calibrations = self.include_calibs.get()
        
        self.include_calibs_chk = ttk.Checkbutton(
            auto_frame,
            text="Include Calibs",
            variable=self.include_calibs,
            command=update_include_calibs,
        )
        self.include_calibs_chk.pack(anchor="w")
        
        self.auto_capture_btn = ttk.Button(
            auto_frame,
            text="Run ▶",
            command=lambda: run_auto_capture(self.context),
        )
        self.auto_capture_btn.pack(anchor="w", pady=(5,0))


        # --- Single & Stop ---
        self.single_btn = ttk.Button(self, text="Take Single Image", command=self.take_single)
        self.single_btn.grid(row=4, column=0, pady=10)
        self.stop_btn = ttk.Button(self, text="Stop", command=self.stop_sequence)
        self.stop_btn.grid(row=4, column=1, pady=10)

        # Internal state
        self.sequence_running = False
        self._blink_on = True
        self.last_save_path = None   # remembers the last run folder for post-run adds
        
        # --- Status row (like Tools tab) ---
        status_frame = ttk.Frame(self)
        status_frame.grid(row=5, column=0, columnspan=7, sticky="w", pady=5)
        ttk.Label(status_frame, text="Status:").pack(side="left", padx=(0,5))
        self.status_lbl = ttk.Label(status_frame, text="Idle")
        self.status_lbl.pack(side="left")

        # Start refresh loop
        self.refresh_status()
        
        # Register this tab so automation and other modules can access it
        self.context.sequencer_tab = self
        self.update_mode()


    # --- UI status loop ---
    def refresh_status(self):
    
        # Update binning label (stored in indicators dict now)
        self.indicators["bin_lbl"].config(text=f"{self.context.current_binning}")
    
        # Update all other indicators through shared helper
        self._sync_blink = update_status_header(
            self.context,
            self.indicators,
            self._sync_blink
        )
    
        # Continue refresh loop
        self.after(1000, self.refresh_status)

    def set_target(self, name: str):
        """Externally set the target name in the Sequencer input box."""
        try:
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, name)
            self.context.log(f"Sequencer target set to: {name}")
        except Exception as e:
            self.context.log(f"Failed to set Sequencer target: {e}")
    
    def _guard_connected(self):
        if not (self.context.camera and getattr(self.context.camera, "Connected", False)):
            messagebox.showerror("Camera", "Camera not connected")
            return False
        return True
    
    def _on_run_start(self):
        """Disable buttons and mark running (main thread)."""
        self.sequence_running = True
        try:
            self.run_calib_btn.config(state="disabled")
            self.run_target_btn.config(state="disabled")
            self.single_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
        except Exception:
            pass

    def _on_run_finished(self):
        """Re-enable buttons and mark idle (main thread)."""
        self.sequence_running = False
        self.status_lbl.config(text="Idle")   # <--- add this
        try:
            self.run_calib_btn.config(state="normal")
            self.run_target_btn.config(state="normal")
            self.single_btn.config(state="normal")
        except Exception:
            pass

    # --- Actions (LIVE) ---
    def run_calibration(self):
        if not self._guard_connected(): return
        target = self.target_entry.get().strip()
        if not target:
            messagebox.showerror("Error", "Enter target name"); return

        # Capture all UI values in the main thread (safe)
        try:
            tung_exp   = float(self.tung_exp.get())
            tung_count = int(self.tung_count.get())
            thor_exp   = float(self.thor_exp.get())
            thor_count = int(self.thor_count.get())
            binning    = int(self.context.current_binning)
        except Exception as e:
            messagebox.showerror("Error", f"Bad calibration settings: {e}")
            return

        base      = nightly_folder(target)
        save_path = next_run_folder(base, target, True)

        # Update UI now, then run worker
        self._on_run_start()
        self.status_lbl.config(text="Running calibration sequence")
        
        def worker():
            try:
                lamp_on("tungsten")
                self.context.tung_on = True     # NEW: update lamp indicator
                self.context.thor_on = False    # NEW: ensure Thorium is OFF
                self.safe_log("Tungsten lamp ON")
        
                # Tell the status system how many Tungsten frames we plan
                self.context.sequence_total = tung_count
        
                time.sleep(3)   # allow lamp to stabilise
                for i in range(1, tung_count + 1):
                    if not self.sequence_running:
                        break
                
                    # --- live MaxIm-style tungsten status ---
                    from status_bar import update_capture_status
                    update_capture_status(
                        self.context,
                        label="Tungsten",
                        index=i,
                        total=tung_count,
                        elapsed=0,
                        exposure=tung_exp,
                    )
                
                    self.safe_log(f"Starting tungsten exposure {i} of {tung_count}")
                
                    # Perform exposure (handles its own timing)
                    expose_and_save(
                        self.context,
                        tung_exp,
                        save_path,
                        f"{target}_tung",
                        i,
                        binning,
                        target,
                        image_type="Light Frame",
                        status_label="Tungsten"
                    )
        
                lamp_on("thorium")
                self.context.thor_on = True     # NEW: update lamp indicator
                self.context.tung_on = False    # NEW: ensure Tungsten is OFF
                self.safe_log("Thorium lamp ON")
        
                # Now tell the status system how many Thorium frames we plan
                self.context.sequence_total = thor_count
        
                time.sleep(3)   # allow lamp to stabilise
                for i in range(1, thor_count + 1):
                    if not self.sequence_running:
                        break
                
                    # --- NEW: live MaxIm-style thorium status ---
                    from status_bar import update_capture_status
                    update_capture_status(
                        self.context,
                        label="Thorium",
                        index=i,
                        total=thor_count,
                        elapsed=0,
                        exposure=thor_exp,
                    )
                
                    self.safe_log(f"Starting thorium exposure {i} of {thor_count}")
                
                    expose_and_save(
                        self.context,
                        thor_exp,
                        save_path,
                        f"{target}_thor",
                        i,
                        binning,
                        target,
                        image_type="Light Frame",
                        status_label="Thorium"
                    )
        
                self.safe_log("Calibration block finished.")
                self.status_lbl.config(text="Calibration sequence complete")  # <-- nice finishing touch
            except Exception as e:
                self.safe_log(f"Calibration error: {e}")
            finally:
                try:
                    lamp_off()
                    self.context.tung_on = False     # NEW
                    self.context.thor_on = False     # NEW
                    self.safe_log("Lamp OFF (end of calibration)")
                except Exception:
                    pass
                self.after(0, self._on_run_finished)
        
        threading.Thread(target=worker, daemon=True).start()


    def run_target(self):
        if not self._guard_connected(): 
            return
    
        target = self.target_entry.get().strip()
        if not target:
            messagebox.showerror("Error", "Enter target name")
            return
    
        # Capture UI values in main thread
        try:
            exp     = float(self.targ_exp.get())
            count   = int(self.targ_count.get())
            binning = int(self.context.current_binning)
        except Exception as e:
            messagebox.showerror("Error", f"Bad target settings: {e}")
            return
    
        base = nightly_folder(target)
        copy_latest_calib_to(base, log_fn=self.context.log)
        save_path = next_run_folder(base, target, False)
        self.last_save_path = save_path   # <-- so "Add Exposure" can continue this folder later
    
        # Initialize run state
        self._on_run_start()
        self.status_lbl.config(text="Running target sequence")  # <-- new line
        self.requested_total = count
        self.completed_count = 0
    
        def worker():
            try:
                lamp_off()
                self.safe_log("Lamp OFF (safety before target run)")
        
                # Tell the status system how many science frames we plan
                self.context.sequence_total = self.requested_total
        
                i = 1
                while i <= self.requested_total:
                    if not self.sequence_running:
                        break
                
                    # --- NEW: live MaxIm-style target status ---
                    from status_bar import update_capture_status
                    update_capture_status(
                        self.context,
                        label=target,
                        index=i,
                        total=self.requested_total,
                        elapsed=0,
                        exposure=exp,
                    )
                    
                    self.safe_log(f"Starting exposure {i} of {self.requested_total}")
                    
                    expose_and_save(
                        self.context,
                        exp,
                        save_path,
                        target,
                        i,
                        binning,
                        target,
                        image_type="Light Frame"
                    )
                
                    i += 1
        
                self.safe_log("Target block finished.")
            except Exception as e:
                self.safe_log(f"Target run error: {e}")
            finally:
                self.after(0, self._on_run_finished)
    
        threading.Thread(target=worker, daemon=True).start()

    def add_exposure(self):
        """
        Add one exposure:
          - During a running sequence: queue +1 (unchanged behavior).
          - After a sequence finishes: resume same folder, next index, take 1 exposure.
        """
        # Case 1: sequence currently running -> just increment total (your existing behavior)
        if self.sequence_running:
            self.requested_total += 1
            self.context.log(f"Added 1 exposure (total now {self.requested_total})")
            self.status_lbl.config(
                text=f"Running sequence: image {self.completed_count} of {self.requested_total}"
            )
            return
    
        # Case 2: sequence is idle -> resume same folder and continue numbering
        if not self.last_save_path or not os.path.isdir(self.last_save_path):
            messagebox.showinfo("Add Exposure", "No recent run found to continue. Start a new run first.")
            return
    
        target = self.target_entry.get().strip() or "target"
        try:
            exp     = float(self.targ_exp.get())
            binning = int(self.context.current_binning)
        except Exception as e:
            messagebox.showerror("Error", f"Bad input: {e}")
            return
    
        save_path = self.last_save_path
        # Next file index based on existing FITS in the run folder
        existing = [f for f in os.listdir(save_path) if f.lower().endswith(".fit")]
        next_index = len(existing) + 1
    
        # Run a single appended exposure as a short, separate worker
        self._on_run_start()
        self.status_lbl.config(text="Running added exposure: 0 of 1")
    
        def worker():
            try:
                # Single appended frame: status system should see 1 total image
                self.context.sequence_total = 1
        
                expose_and_save(
                    self.context, exp, save_path, target, next_index, binning, target,
                    image_type="Light Frame"
                )
                self.status_lbl.config(text="Running added exposure: 1 of 1")
                self.safe_log("Added 1 exposure (post-run append)")
            except Exception as e:
                self.safe_log(f"Add exposure error: {e}")
            finally:
                self.after(0, self._on_run_finished)
    
        threading.Thread(target=worker, daemon=True).start()

    def take_single(self):
        if not self._guard_connected():
            return
    
        try:
            exp     = float(self.targ_exp.get() or 1.0)
            binning = int(self.context.current_binning)
        except Exception as e:
            messagebox.showerror("Error", f"Bad exposure value: {e}")
            return
    
        self._on_run_start()
        self.status_lbl.config(text="Taking single image")  # <-- add this
    
        def worker():
            import pythoncom
            pythoncom.CoInitialize()
            try:
                cam = self.context.camera
                cam.BinX = binning
                cam.BinY = binning
                cam.NumX = cam.CameraXSize // binning
                cam.NumY = cam.CameraYSize // binning
                cam.StartX, cam.StartY = 0, 0
        
                # Start exposure
                cam.StartExposure(float(exp), True)
        
                while not cam.ImageReady:
                    time.sleep(0.05)
        
                # Get image
                try:
                    arr = np.array(cam.ImageArray)
                except Exception:
                    arr = np.array(getattr(cam, "ImageArrayVariant"))
                    
                # --- Match orientation used in sequencer exposures ---
                expected = (cam.CameraYSize // binning, cam.CameraXSize // binning)
                if arr.shape != expected:
                    arr = arr.T
                
                # --- write to a temp FITS file ---
                tmp = tempfile.NamedTemporaryFile(suffix=".fits", delete=False)
                tmp.close()
                fits.writeto(tmp.name, arr, overwrite=True)
        
                # --- load into viewer ---
                self.context.viewer.load_fits(tmp.name)
                self.context.notebook.select(self.context.viewer)   # switch to Viewer tab
        
                # --- delete temp file ---
                os.remove(tmp.name)
        
                self.safe_log(f"Single check exposure complete (temp only). Max ADU={arr.max()}")
        
            except Exception as e:
                self.safe_log(f"Single exposure error: {e}")
            finally:
                self.after(0, self._on_run_finished)
    
        threading.Thread(target=worker, daemon=True).start()


    def update_mode(self):
        """Enable or disable Sequencer controls based on selected mode."""
        mode = self.mode_var.get()
    
        if mode == "manual":
            # Enable manual controls
            for w in [self.run_calib_btn, self.run_target_btn, self.add_exposure_btn, self.single_btn]:
                w.config(state="normal")
            # Disable auto controls
            for w in [self.auto_capture_btn, self.include_calibs_chk]:
                w.config(state="disabled")
            self.status_lbl.config(text="Manual mode active")
    
        elif mode == "auto":
            # Disable manual controls (except Add Frames)
            for w in [self.run_calib_btn, self.run_target_btn, self.single_btn]:
                w.config(state="disabled")
            # Enable auto controls and Add Frames
            for w in [self.auto_capture_btn, self.include_calibs_chk, self.add_exposure_btn]:
                w.config(state="normal")
            self.status_lbl.config(text="Auto Capture mode active")

    def stop_sequence(self):
        """
        Stop all active capture processes (manual or auto).
    
        This is triggered by the red Stop button and performs a full abort:
        - Sets global stop flags (stop_requested, adaptive_stop)
        - Aborts any active camera exposure
        - Stops guiding via phd2_control
        - Halts any Sequencer activity (manual or Auto Capture)
        """
        self.status_lbl.config(text="Stopping...")
        self.context.log("🛑 Stop requested by user.")
        try:
            from utils import abort_all
            abort_all(self.context)
        except Exception as e:
            self.context.log(f"Stop sequence failed: {e}")
