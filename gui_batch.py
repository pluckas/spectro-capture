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
gui_batch.py
------------
Standalone multi-target automation tab for Spectro Capture.

Runs a list of targets sequentially through telescope/dome
slew, calibration (optional), and imaging — without touching
Sequencer or Auto Capture internals.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import csv, os, threading
from batch_runner import run_batch
from utils import utc_hhmm_to_local_hhmm
from datetime import datetime, timezone


class BatchTab(ttk.Frame):
    CSV_HEADER = [
        "Target", "RA", "Dec", "Exp (s)", "Frames",
        "Calibrate", "Enabled", "Start Time (Local)",
        "Ref Star", "Ref Exp (s)", "Ref Frames"
    ]

    def _set_scheduler_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
    
        for attr in (
            "ha_min_spin",
            "ha_max_spin",
            "start_entry",
            "pre_calib_cb",
            "shutdown_calib_cb",
        ):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.configure(state=state)
                except Exception:
                    pass

    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        context.batch_tab = self
        self.batch_running = False
        self.stop_requested = False
        self._row_coords = {}

        # ============================================================
        # >>> NEW LAYOUT: top content (50%), spacer (50%), footer
        # ============================================================
        # row 0 = top content (header + table + buttons + optional panel)
        # row 1 = spacer to enforce ~50% top-height
        # row 2 = footer status label (fixed height)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)

        # --- Top content frame (upper half of window) ---
        self.top_frame = ttk.Frame(self)
        self.top_frame.grid(row=0, column=0, sticky="nsew")

        # --- Header row (shared status bar) ---
        from status_bar import add_status_header, update_status_header
        self._sync_blink = False
        # Use pack so we do not mix grid/pack inside same parent
        self.indicators = add_status_header(self.top_frame, layout="pack")

        # ---- Table ---------------------------------------------------------
        cols = (
            "Target", "Exposure (s)", "Count",
            "Calibration", "Enabled", "Start Time (UT)",
            "Ref Star", "Ref Exp (s)", "Ref Frames"
        )

        table_frame = ttk.Frame(self.top_frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        self.table = ttk.Treeview(table_frame, columns=cols, show="headings", height=12)
        for c in cols:
            self.table.heading(c, text=c)
            self.table.column(c, width=85, anchor="center")
        self.table.pack(fill="both", expand=True)

        # ---- Buttons -------------------------------------------------------
        btns = ttk.Frame(self.top_frame)
        btns.pack(pady=5)

        ttk.Button(btns, text="Add", command=self.add_target).pack(side="left", padx=4)
        ttk.Button(btns, text="Edit", command=self.edit_selected).pack(side="left", padx=4)
        ttk.Button(btns, text="Remove", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear", command=self.clear_all).pack(side="left", padx=4)
        ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(btns, text="Load CSV", command=self.load_list).pack(side="left", padx=4)
        ttk.Button(btns, text="Save CSV", command=self.save_list).pack(side="left", padx=4)
        ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(btns, text="Run ▶", command=self.run_batch).pack(side="left", padx=4)
        ttk.Button(btns, text="Stop", command=self.stop_batch).pack(side="left", padx=4)
        
        # ============================================================
        # >>> SMART HA SCHEDULING
        # ============================================================
        self.scheduler_frame = ttk.LabelFrame(self.top_frame, text="Scheduling")
        self.scheduler_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        self.smart_var = tk.BooleanVar(value=False)
        self.ha_min_var = tk.DoubleVar(value=-2.0)
        self.ha_max_var = tk.DoubleVar(value=1.0)
        self.batch_start_local = tk.StringVar(value="")
        
        ttk.Checkbutton(
            self.scheduler_frame,
            text="Enable Smart HA Scheduling",
            variable=self.smart_var,
            command=lambda: self._set_scheduler_enabled(self.smart_var.get())
        ).grid(row=0, column=0, padx=(10, 20), pady=6, sticky="w")
        
        ttk.Label(self.scheduler_frame, text="HA Min (hrs)").grid(
            row=0, column=1, padx=(0, 4), sticky="w"
        )
        self.ha_min_spin = ttk.Spinbox(
            self.scheduler_frame,
            from_=-6.0, to=6.0, increment=0.5,
            width=5, textvariable=self.ha_min_var
        )
        self.ha_min_spin.grid(row=0, column=2, padx=(0, 14))
        
        ttk.Label(self.scheduler_frame, text="HA Max (hrs)").grid(
            row=0, column=3, padx=(0, 4), sticky="w"
        )
        self.ha_max_spin = ttk.Spinbox(
            self.scheduler_frame,
            from_=-6.0, to=6.0, increment=0.5,
            width=5, textvariable=self.ha_max_var
        )
        self.ha_max_spin.grid(row=0, column=4, padx=(0, 20))
        
        ttk.Label(self.scheduler_frame, text="Start (Local)").grid(
            row=0, column=5, sticky="e"
        )
        
        time_options = (
            [f"{h:02d}:00" for h in range(18, 24)] +
            [f"{h:02d}:00" for h in range(0, 7)]
        )
        
        self.start_entry = ttk.Combobox(
            self.scheduler_frame,
            width=6,
            values=time_options,
            textvariable=self.batch_start_local,
            state="readonly"
        )
        self.start_entry.grid(row=0, column=6, padx=(4, 14))
        
        
        # --- calibration options (row 1, scheduler-scoped) ---
        self.pre_calib_var = tk.BooleanVar(value=False)
        self.pre_calib_cb = ttk.Checkbutton(
            self.scheduler_frame,
            text="Run Calibration at First Target",
            variable=self.pre_calib_var
        )
        self.pre_calib_cb.grid(row=1, column=0, padx=10, pady=(0, 6), sticky="w")
        
        self.shutdown_calib_var = tk.BooleanVar(value=False)
        self.shutdown_calib_cb = ttk.Checkbutton(
            self.scheduler_frame,
            text="Run Calibration After Batch",
            variable=self.shutdown_calib_var
        )
        self.shutdown_calib_cb.grid(row=1, column=1, columnspan=2, padx=(0, 10), pady=(0, 6), sticky="w")
        
        
        # ============================================================
        # >>> OPTIONAL FUNCTIONS PANEL
        # ============================================================
        self.optional_frame = ttk.LabelFrame(self.top_frame, text="Optional Functions")
        self.optional_frame.pack(fill="x", padx=10, pady=10)
        
        opt_inner = ttk.Frame(self.optional_frame)
        opt_inner.pack(fill="x", pady=(0, 8))
        
        self.park_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_inner,
            text="Park Telescope After Batch",
            variable=self.park_var
        ).grid(row=0, column=0, sticky="w", padx=10, pady=4)
        
        self.dome_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_inner,
            text="Close Dome After Batch",
            variable=self.dome_var
        ).grid(row=0, column=1, sticky="w", padx=(20, 10), pady=4)
        
        self.warm_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_inner,
            text="Warm Camera to +5°C After Batch",
            variable=self.warm_var
        ).grid(row=0, column=2, sticky="w", padx=(20, 10), pady=4)
        
        
        # Disable scheduler fields until Smart Scheduling is enabled
        self._set_scheduler_enabled(False)
        
        # ---- Status (footer always at bottom) ------------------------------
        self.status_lbl = ttk.Label(self, text="Idle", anchor="w")
        self.status_lbl.grid(row=2, column=0, sticky="ew", padx=10, pady=(5, 8))
        
        # --- Start indicator updater ---
        self.after(1000, self.refresh_batch_status)

    # ----------------------------------------------------------------------
    # Table management
    # ----------------------------------------------------------------------
    def add_target(self):
        self._target_dialog()

    def edit_selected(self):
        sel = self.table.selection()
        if not sel:
            messagebox.showinfo("Edit Target", "Select a target first.")
            return
        values = list(self.table.item(sel[0], "values"))
        ra, dec = self._row_coords.get(sel[0], ("", ""))
        prefill = (
            values[0], ra, dec, values[1], values[2], values[3],
            values[4], values[5], values[6], values[7], values[8]
        )
        self._target_dialog(prefill=prefill, item=sel[0])

    def _target_dialog(self, prefill=None, item=None):
        win = tk.Toplevel(self)
        win.title("Target Details")
        win.resizable(False, False)
        self.update_idletasks()
        x, y = self.winfo_rootx() + 100, self.winfo_rooty() + 100
        win.geometry(f"+{x}+{y}")
    
        # ---------------------------------------------------------
        # Text entry fields
        # ---------------------------------------------------------
        labels = [
            "Target name:", "RA:", "Dec:", "Exposure (s):",
            "Frames:", "Start Time (Local):", "Ref star:",
            "Ref exposure (s):", "Ref frames:"
        ]
        entries = {}
        
        # defaults: name, ra, dec, exp, frames, calib, enabled, start_time, ref, ref_exp, ref_frames
        if prefill:
            defaults = tuple(prefill) + ("",) * (11 - len(prefill))
        else:
            defaults = ("", "", "", "600", "3", "✓", "✓", "", "", "", "")
        
        # ---------------------------------------------------------
        # REPLACEMENT LOOP (adds Combobox for Start Time)
        # ---------------------------------------------------------
        field_map = {
            "Target name:": 0,
            "RA:": 1,
            "Dec:": 2,
            "Exposure (s):": 3,
            "Frames:": 4,
            "Start Time (Local):": 7,
            "Ref star:": 8,
            "Ref exposure (s):": 9,
            "Ref frames:": 10,
        }
        
        for i, text in enumerate(labels):
            ttk.Label(win, text=text).grid(row=i, column=0, padx=5, pady=4, sticky="e")
        
            if text == "Start Time (Local):":
                time_options = (
                    [f"{h:02d}:00" for h in range(18, 24)] +
                    [f"{h:02d}:00" for h in range(0, 6)]
                )
        
                cb = ttk.Combobox(win, values=time_options, width=10)
                
                if defaults[7]:
                    # Stored value is UTC → convert back to Local for editing
                    cb.set(utc_hhmm_to_local_hhmm(defaults[7]))
                else:
                    cb.set("")
                
                cb.grid(row=i, column=1, padx=5, pady=4, sticky="w")
                entries[text] = cb
        
            else:
                e = ttk.Entry(win, width=25)
                j = field_map.get(text)
                if j is not None:
                    e.insert(0, defaults[j])
                e.grid(row=i, column=1, padx=5, pady=4)
                entries[text] = e
    
        # ---------------------------------------------------------
        # Calibration checkbox (row 4)
        # ---------------------------------------------------------
        calib_row = len(labels)
        enabled_row = len(labels) + 1
        ok_row = len(labels) + 2

        calib_default = (defaults[5] == "✓")
        calib_var = tk.BooleanVar(value=calib_default)
        ttk.Checkbutton(win, text="Include calibration", variable=calib_var)\
            .grid(row=calib_row, column=1, sticky="w", padx=5, pady=4)
    
        # ---------------------------------------------------------
        # Enabled checkbox (row 5)
        # ---------------------------------------------------------
        enabled_default = (defaults[6] == "✓")
        enabled_var = tk.BooleanVar(value=enabled_default)
        ttk.Checkbutton(win, text="Enabled", variable=enabled_var)\
            .grid(row=enabled_row, column=1, sticky="w", padx=5, pady=4)
    
        # ---------------------------------------------------------
        # OK button (row 6)
        # ---------------------------------------------------------
        def ok():
            name = entries["Target name:"].get().strip()
            ra = entries["RA:"].get().strip()
            dec = entries["Dec:"].get().strip()
            exp = entries["Exposure (s):"].get().strip()
            frames = entries["Frames:"].get().strip()
    
            if not (name and exp and frames) or bool(ra) != bool(dec):
                messagebox.showwarning("Incomplete", "Please fill all fields.")
                return
    
            calib = "✓" if calib_var.get() else "✗"
            enabled = "✓" if enabled_var.get() else "✗"
            
            # --- Convert LOCAL TIME (HH:MM) → UT (HH:MM) ---
            local_str = entries["Start Time (Local):"].get().strip()
            if local_str:
                from utils import local_hhmm_to_utc_hhmm
                start_time = local_hhmm_to_utc_hhmm(local_str)
            else:
                start_time = ""
    
            ref_name   = entries["Ref star:"].get().strip()
            ref_exp    = entries["Ref exposure (s):"].get().strip()
            ref_frames = entries["Ref frames:"].get().strip()
            
            vals = (
                name, exp, frames, calib, enabled, start_time,
                ref_name, ref_exp, ref_frames
            )
    
            if item:
                self.table.item(item, values=vals)
                self._row_coords[item] = (ra, dec)
            else:
                item_id = self.table.insert("", "end", values=vals)
                self._row_coords[item_id] = (ra, dec)
            
            self.status_lbl.config(text=f"Added/edited {name}")
            win.destroy()
    
        ttk.Button(win, text="OK", command=ok)\
            .grid(row=ok_row, column=0, columnspan=2, pady=8)
    
        # Ensure focus and modal behavior
        first = list(entries.values())[0]
        first.focus_set()
        win.grab_set()

    def remove_selected(self):
        for s in self.table.selection():
            self._row_coords.pop(s, None)
            self.table.delete(s)
        self.status_lbl.config(text="Removed selected targets")

    def clear_all(self):
        for i in self.table.get_children():
            self.table.delete(i)
        self._row_coords.clear()
        self.status_lbl.config(text="Cleared all targets")

    # ----------------------------------------------------------------------
    # Load / Save
    # ----------------------------------------------------------------------
    def save_list(self):
        """Save current batch table to CSV."""
        # Use the persisted Batch CSV folder if available
        initial_dir = getattr(self.context, "batch_csv_path", None) or os.getcwd()
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            title="Save Batch List",
            initialdir=initial_dir
        )
        if not path:
            return
    
        try:
            rows = [self._full_row_values(i) for i in self.table.get_children()]
            if not rows:
                messagebox.showinfo("Save Batch List", "No targets to save.")
                return
    
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(self.CSV_HEADER)
                w.writerows(rows)
    
            # Remember the folder used for future saves/loads
            folder = os.path.dirname(path)
            try:
                if hasattr(self.context, "set_batch_csv_path"):
                    self.context.set_batch_csv_path(folder)
            except Exception:
                pass
    
            self.status_lbl.config(text=f"Saved: {os.path.basename(path)}")
    
        except Exception as e:
            self.context.log(f"Error saving list: {e}")
            messagebox.showerror("Error", f"Failed to save list:\n{e}")

    def load_list(self):
        """Load batch table from CSV."""
        # Use the persisted Batch CSV folder if available
        initial_dir = getattr(self.context, "batch_csv_path", None) or os.getcwd()
        path = filedialog.askopenfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            title="Load Batch List",
            initialdir=initial_dir
        )
        if not path:
            return
    
        try:
            for i in self.table.get_children():
                self.table.delete(i)
            self._row_coords.clear()
    
            with open(path, newline="", encoding="utf-8") as f:
                r = csv.reader(f)
                next(r, None)  # skip header
                count = 0
                for row in r:
                    if len(row) < 11:
                        raise ValueError("Batch CSV must contain 11 columns including RA and Dec.")
                
                    # --- NORMALISE CALIBRATION (col 5) ---
                    raw = row[5].strip().lower()
                    if raw in ("x", "no", "false", "0", "✗"):
                        row[5] = "✗"
                    else:
                        row[5] = "✓"
                
                    # --- NORMALISE ENABLED (col 6) ---
                    raw = row[6].strip().lower()
                    if raw in ("x", "no", "false", "0", "✗"):
                        row[6] = "✗"
                    else:
                        row[6] = "✓"
                
                    visible_vals = (
                        row[0], row[3], row[4], row[5], row[6],
                        row[7], row[8], row[9], row[10]
                    )
                    item_id = self.table.insert("", "end", values=visible_vals)
                    self._row_coords[item_id] = (row[1], row[2])
                    count += 1
    
            # Remember the folder used for future saves/loads
            folder = os.path.dirname(path)
            try:
                if hasattr(self.context, "set_batch_csv_path"):
                    self.context.set_batch_csv_path(folder)
            except Exception:
                pass
    
            self.status_lbl.config(text=f"Loaded: {os.path.basename(path)}")
    
        except Exception as e:
            self.context.log(f"Error loading list: {e}")
            messagebox.showerror("Error", f"Failed to load list:\n{e}")

    def _full_row_values(self, item_id):
        values = list(self.table.item(item_id, "values"))
        ra, dec = self._row_coords.get(item_id, ("", ""))
        return (
            values[0], ra, dec, values[1], values[2],
            values[3], values[4], values[5], values[6], values[7], values[8]
        )

    # ----------------------------------------------------------------------
    # Run / Stop
    # ----------------------------------------------------------------------
    def run_batch(self):
        if self.batch_running:
            messagebox.showinfo("Batch", "Already running.")
            return
        rows = [self._full_row_values(i) for i in self.table.get_children()]
        if not rows:
            messagebox.showinfo("Batch", "No targets in list.")
            return

        if not hasattr(self.context, "stop_requested") \
           or not isinstance(self.context.stop_requested, threading.Event):
            self.context.stop_requested = threading.Event()
        else:
            self.context.stop_requested.clear()

        self.batch_running = True
        self.stop_requested = False
        self._dome_blink = False
        self.status_lbl.config(text="Starting batch run…")
        self.context.log(f"Starting batch run ({len(rows)} targets)")

        # Push scheduler settings into context
        self.context.batch_smart_mode = self.smart_var.get()
        self.context.batch_ha_min = float(self.ha_min_var.get())
        self.context.batch_ha_max = float(self.ha_max_var.get())
        
        # ===== BEGIN FIX: scheduler start time wiring =====
        # Only applies to scheduler (HA) mode
        
        if self.smart_var.get():
            local_str = self.batch_start_local.get().strip()
            if local_str:
                from utils import local_hhmm_to_utc_hhmm
                hhmm_utc = local_hhmm_to_utc_hhmm(local_str)
        
                hh, mm = map(int, hhmm_utc.split(":"))
                now_utc = datetime.now(timezone.utc)
                self.context.batch_start_utc = now_utc.replace(
                    hour=hh, minute=mm, second=0, microsecond=0
                )
            else:
                self.context.batch_start_utc = None
        else:
            self.context.batch_start_utc = None
        
        self.context.batch_stop_utc = None
        # ===== END FIX =====

        
        threading.Thread(
            target=run_batch,
            args=(self.context, rows),
            daemon=True
        ).start()

    def stop_batch(self):
        if not self.batch_running:
            return
        self.stop_requested = True
        if hasattr(self.context, "stop_requested"):
            self.context.stop_requested.set()
        self.status_lbl.config(text="Stopping batch…")
        self.context.log("Batch stop requested.")

        from utils import abort_all
        abort_all(self.context)

    # ----------------------------------------------------------------------
    # Batch finished handler
    # ----------------------------------------------------------------------
    def on_batch_finished(self):
        self.batch_running = False
        try:
            self.status_lbl.config(text="Idle")
        except:
            pass

    # ----------------------------------------------------------------------
    # Status indicator updater
    # ----------------------------------------------------------------------
    def refresh_batch_status(self):
        try:
            from status_bar import update_status_header

            self._sync_blink = update_status_header(
                self.context,
                self.indicators,
                self._sync_blink
            )

            if self.batch_running:
                if self.context.stop_requested.is_set():
                    self.batch_running = False
                    self.status_lbl.config(text="Idle")
        finally:
            self.after(1000, self.refresh_batch_status)
