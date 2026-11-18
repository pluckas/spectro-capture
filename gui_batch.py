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
import csv, io, math, os, threading
from hashlib import sha256
from batch_runner import run_batch
from web_monitor import describe_rows, report
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
        self._batch_worker = None
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
        
        self.scheduler_start_times = (
            [f"{h:02d}:00" for h in range(18, 24)] +
            [f"{h:02d}:00" for h in range(0, 7)]
        )

        self.start_entry = ttk.Combobox(
            self.scheduler_frame,
            width=6,
            values=self.scheduler_start_times,
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
            self._remove_target(s)
        self.status_lbl.config(text="Removed selected targets")

    def _remove_target(self, item):
        self.table.delete(item)
        self._row_coords.pop(item, None)

    def clear_all(self):
        self._replace_batch_rows([])
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
            rows = self._batch_rows()
            if not rows:
                messagebox.showinfo("Save Batch List", "No targets to save.")
                return
    
            with open(path, "w", newline="", encoding="utf-8") as f:
                self._write_batch_csv(f, rows)
    
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
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = self._read_batch_csv(f)
            self._replace_batch_rows(rows)

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

    def _batch_rows(self):
        return [self._full_row_values(item) for item in self.table.get_children()]

    def _write_batch_csv(self, stream, rows):
        writer = csv.writer(stream)
        writer.writerow(self.CSV_HEADER)
        writer.writerows(rows)

    def _read_batch_csv(self, stream, *, validate_targets=False):
        reader = csv.reader(stream, strict=True)
        header = next(reader, None)
        if validate_targets:
            expected = [column.casefold() for column in self.CSV_HEADER]
            columns = [column.strip().casefold() for column in (header or ())]
            if len(columns) == len(expected):
                aliases = {
                    3: {"exposure (s)"},
                    4: {"count"},
                    5: {"calibration"},
                    7: {"start time (ut)", "start time ut"},
                }
                for index, names in aliases.items():
                    if columns[index] in names:
                        columns[index] = expected[index]
            if columns != expected:
                raise ValueError("Use a Spectro Capture batch CSV with the standard column headings.")

        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) < len(self.CSV_HEADER) or (validate_targets and len(row) != len(self.CSV_HEADER)):
                raise ValueError(f"CSV line {reader.line_num}: expected 11 columns including RA and Dec.")
            row = row[:len(self.CSV_HEADER)]
            if validate_targets:
                try:
                    self._validate_csv_target(row)
                except ValueError as error:
                    raise ValueError(f"CSV line {reader.line_num}: {error}") from error
            for index in (5, 6):
                row[index] = "✗" if row[index].strip().lower() in ("x", "no", "false", "0", "✗") else "✓"
            rows.append(row)
        return rows

    def _validate_csv_target(self, row):
        self._validate_target_identity(*row[:3])
        if row[7]:
            # Older desktop exports say Local in the header but store UT.
            try:
                if datetime.strptime(row[7], "%H:%M").strftime("%H:%M") != row[7]:
                    raise ValueError
            except ValueError:
                raise ValueError("Start time must be blank or HH:MM UT.") from None
        for label, seconds, frames in (
            ("Target", row[3], row[4]),
            ("Reference", row[9], row[10]),
        ):
            if label == "Reference" and not any(field.strip() for field in row[8:]):
                continue
            if label == "Reference" and not row[8].strip():
                raise ValueError("Reference name is required when reference exposure or frames are supplied.")
            self._validate_exposure_frames(label, seconds, frames)

    @staticmethod
    def _validate_exposure_frames(label, seconds, frames):
        try:
            duration = float(seconds)
            if not math.isfinite(duration) or duration <= 0 or int(frames) <= 0:
                raise ValueError
        except ValueError:
            raise ValueError(f"{label} needs a positive exposure and a positive whole frame count.") from None

    @staticmethod
    def _validate_target_identity(name, ra, dec):
        from utils import parse_ra_dec_strings

        if not name.strip():
            raise ValueError("Target name is required.")
        try:
            parse_ra_dec_strings(ra, dec)
        except ValueError as error:
            raise ValueError(f"Invalid RA or Dec: {error}") from error

    def _replace_batch_rows(self, rows):
        self.table.delete(*self.table.get_children())
        self._row_coords.clear()
        for row in rows:
            item = self.table.insert("", "end", values=(row[0], *row[3:]))
            self._row_coords[item] = (row[1], row[2])
        if not self.batch_running:
            report(self.context, "publish", run=None)
        self._publish_batch_status()

    def _load_web_batch_csv(self, contents):
        if self.batch_running or self.context.camera_work_active():
            raise RuntimeError("Wait for the current acquisition to finish before loading a CSV.")
        with io.StringIO(contents.removeprefix("\ufeff"), newline="") as stream:
            rows = self._read_batch_csv(stream, validate_targets=True)
        if not rows:
            raise ValueError("The CSV contains no targets.")
        self._replace_batch_rows(rows)
        message = f"Loaded {len(rows)} targets from CSV."
        self.status_lbl.config(text=message)
        return message

    def _download_web_batch_csv(self):
        rows = self._batch_rows()
        if not rows:
            raise ValueError("No targets to download.")
        with io.StringIO(newline="") as stream:
            self._write_batch_csv(stream, rows)
            return stream.getvalue()

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
    def run_web_batch_command(self, action, value):
        if action == "run":
            return self.run_batch(from_browser=True)
        if action == "schedule":
            return self._apply_web_schedule(value)
        if action == "load_csv":
            return self._load_web_batch_csv(value)
        if action == "download_csv":
            return self._download_web_batch_csv()
        if action == "clear_targets":
            self._require_target_list_idle()
            if value != self._target_list_revision(self._batch_rows()):
                raise ValueError("The target list changed. Review it before clearing.")
            self.clear_all()
            return ""
        if action == "remove_target":
            self._require_target_list_idle()
            item, _ = self._check_target_revision(value)
            self._remove_target(item)
            report(self.context, "publish", run=None)
            self._publish_batch_status()
            return ""
        if action == "save_target_identity":
            return self._save_web_target_identity(value)
        if action == "save_target_reference":
            return self._save_web_target_reference(value)
        if action == "edit_target":
            return self._edit_web_target(value)
        return self.stop_batch(from_browser=True)

    @staticmethod
    def _target_revision(row):
        return sha256(repr(tuple(row)).encode("utf-8")).hexdigest()

    def _target_list_revision(self, rows):
        return self._target_revision(zip(self.table.get_children(), rows))

    def _require_target_list_idle(self):
        if self.batch_running or self.context.camera_work_active():
            raise RuntimeError("Wait for acquisition to finish before editing targets.")

    def _check_target_revision(self, edit):
        item = edit.get("id")
        if not isinstance(item, str) or not item or not self.table.exists(item):
            raise ValueError("This target is no longer in the list.")
        row = self._full_row_values(item)
        if edit.get("revision") != self._target_revision(row):
            raise ValueError("This target changed in the desktop or another tab. Reopen it to edit.")
        return item, row

    def _save_web_target_identity(self, edit):
        self._require_target_list_idle()
        identity = [edit.get(field) for field in ("name", "ra", "dec")]
        if not all(isinstance(value, str) and "\x00" not in value for value in identity):
            raise ValueError("Enter a target name and optional RA and Dec.")
        name, ra, dec = (value.strip() for value in identity)
        self._validate_target_identity(name, ra, dec)

        if "id" in edit:
            item, row = self._check_target_revision(edit)
            if (name, ra, dec) == row[:3]:
                return ""
            self.table.item(item, values=(name, *row[3:]))
        else:
            item = self.table.insert("", "end", values=(name, "600", "3", "✓", "✓", "", "", "", ""))
        self._row_coords[item] = (ra, dec)
        report(self.context, "publish", run=None)
        self._publish_batch_status()
        return ""

    def _save_web_target_reference(self, edit):
        self._require_target_list_idle()
        item, row = self._check_target_revision(edit)
        reference = edit.get("reference", False)
        if reference is None:
            fields = ("", "", "")
        else:
            if not isinstance(reference, dict):
                raise ValueError("Enter a reference star name, exposure and frame count.")
            values = tuple(reference.get(field) for field in ("name", "seconds", "frames"))
            if not all(isinstance(value, str) and "\x00" not in value for value in values):
                raise ValueError("Enter a reference star name, exposure and frame count.")
            fields = tuple(value.strip() for value in values)
            if not fields[0]:
                raise ValueError("Reference star name is required.")
            self._validate_exposure_frames("Reference", fields[1], fields[2])

        if fields != row[8:]:
            self.table.item(item, values=(row[0], *row[3:8], *fields))
            report(self.context, "publish", run=None)
            self._publish_batch_status()
        return ""

    def _edit_web_target(self, edit):
        self._require_target_list_idle()
        item, row = self._check_target_revision(edit)
        field, value = edit.get("field"), edit.get("value")
        columns = {"seconds": 3, "frames": 4, "calibration": 5, "enabled": 6, "start_ut": 7}
        if not isinstance(field, str) or field not in columns:
            raise ValueError("Invalid target field.")
        if field in ("calibration", "enabled"):
            if type(value) is not bool:
                raise ValueError("Expected a checkbox value.")
            value = "✓" if value else "✗"
        elif field == "start_ut":
            if value != "":
                try:
                    if datetime.strptime(value, "%H:%M").strftime("%H:%M") != value:
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError("Enter a start time as HH:MM UT, or leave it blank.") from None
        else:
            try:
                number = float(value) if field == "seconds" else int(value)
                if not isinstance(value, str) or number <= 0 or not math.isfinite(number):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Enter a positive exposure or a positive whole frame count.") from None
        index = columns[field]
        if value != row[index]:
            values = list(self.table.item(item, "values"))
            values[index - 2] = value
            self.table.item(item, values=values)
            report(self.context, "publish", run=None)
            self._publish_batch_status()
        return ""

    WEB_BATCH_CHECKBOXES = {
        "pre_calib": "pre_calib_var",
        "shutdown_calib": "shutdown_calib_var",
        "park": "park_var",
        "close_dome": "dome_var",
        "warm_camera": "warm_var",
    }

    def _apply_web_schedule(self, options):
        if self.batch_running or self.context.camera_work_active():
            raise RuntimeError("Wait for the current acquisition to finish.")
        if len(options) != 1:
            raise ValueError("Change one scheduling option at a time.")
        field, value = next(iter(options.items()))
        if field == "enabled" and type(value) is bool:
            self.smart_var.set(value)
            self._set_scheduler_enabled(value)
        elif field in self.WEB_BATCH_CHECKBOXES and type(value) is bool:
            if field in ("pre_calib", "shutdown_calib") and not self.smart_var.get():
                raise ValueError("Enable Smart HA Scheduling to change calibration options.")
            getattr(self, self.WEB_BATCH_CHECKBOXES[field]).set(value)
        elif field == "start_local" and (value == "" or value in self.scheduler_start_times):
            self.batch_start_local.set(value)
        elif field in ("ha_min", "ha_max"):
            schedule = self._web_schedule() | options
            low, high = schedule["ha_min"], schedule["ha_max"]
            if (type(low) not in (int, float) or type(high) not in (int, float)
                    or not -6 <= low <= high <= 6):
                raise ValueError("Enter HA limits between −6 and +6 hours, with Min at or below Max.")
            (self.ha_min_var if field == "ha_min" else self.ha_max_var).set(value)
        else:
            raise ValueError("Invalid scheduling option.")

        self._publish_batch_status()
        return ""

    def _web_schedule(self):
        limits = []
        for variable in (self.ha_min_var, self.ha_max_var):
            try:
                value = variable.get()
            except tk.TclError:
                value = None
            limits.append(value if value is not None and math.isfinite(value) else None)
        return {"enabled": self.smart_var.get(), "ha_min": limits[0], "ha_max": limits[1],
                "start_local": self.batch_start_local.get(), "start_times": self.scheduler_start_times,
                **{field: getattr(self, variable).get()
                   for field, variable in self.WEB_BATCH_CHECKBOXES.items()}}

    def run_batch(self, *, from_browser=False):
        try:
            return self._start_batch()
        except (ValueError, RuntimeError) as error:
            if from_browser:
                raise
            messagebox.showinfo("Batch", str(error))

    def _start_batch(self):
        if self.batch_running or self.context.camera_work_active():
            raise RuntimeError("Wait for the current acquisition to finish.")
        rows = self._batch_rows()
        if not any(row["enabled"] for row in describe_rows(rows)):
            raise ValueError("No enabled targets in the batch.")

        smart = self.smart_var.get()
        ha_min = float(self.ha_min_var.get())
        ha_max = float(self.ha_max_var.get())
        start_utc = None
        local_str = self.batch_start_local.get().strip()
        if smart and local_str:
            from utils import local_hhmm_to_utc_hhmm
            hh, mm = map(int, local_hhmm_to_utc_hhmm(local_str).split(":"))
            start_utc = datetime.now(timezone.utc).replace(hour=hh, minute=mm, second=0, microsecond=0)

        self.context.batch_smart_mode = smart
        self.context.batch_ha_min = ha_min
        self.context.batch_ha_max = ha_max
        self.context.batch_start_utc = start_utc
        self.context.batch_stop_utc = None
        self.context.stop_requested.clear()
        self.stop_requested = False
        self._dome_blink = False

        worker = threading.Thread(target=run_batch, args=(self.context, rows), daemon=True)
        self._batch_worker = worker
        self.batch_running = True
        report(self.context, "begin", rows, smart)
        try:
            self.context.start_camera_worker(worker)
        except RuntimeError:
            self.batch_running = False
            self._batch_worker = None
            report(self.context, "finish", False)
            raise
        self.status_lbl.config(text="Starting batch run…")
        self.context.log(f"Starting batch run ({len(rows)} targets)")
        self._publish_batch_status(rows)
        return "Batch start requested."

    def stop_batch(self, *, from_browser=False):
        if not self.batch_running or not self._batch_worker.is_alive():
            if from_browser:
                raise RuntimeError("No batch is running.")
            return
        if self.context.stop_requested.is_set():
            return "Batch is already stopping."

        self.stop_requested = True
        self.context.stop_requested.set()
        report(self.context, "stopping")
        self.status_lbl.config(text="Stopping batch…")
        self.context.log("Batch stop requested.")
        self._publish_batch_status()

        from utils import abort_all
        abort_all(self.context)
        return "Batch stop requested."

    def _publish_batch_status(self, rows=None):
        if getattr(self.context, "web_monitor", None) is None:
            return
        if rows is None:
            rows = self._batch_rows()
        report(self.context, "publish", plan=describe_rows(rows),
               target_list_revision=self._target_list_revision(rows),
               target_edits=[{"id": item, "revision": self._target_revision(row), "ra": row[1], "dec": row[2]}
                             for item, row in zip(self.table.get_children(), rows)],
               plan_mode="Hour angle" if self.smart_var.get() else "List order",
               batch_running=self.batch_running,
               batch_stopping=self.batch_running and self.context.stop_requested.is_set(),
               acquisition_busy=self.context.camera_work_active(),
               batch_schedule=self._web_schedule())

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

            if self.batch_running and not self._batch_worker.is_alive() and not self.context.camera_work_active():
                self.batch_running = False
                self._batch_worker = None
                self.status_lbl.config(text="Idle")
            self._publish_batch_status()
        finally:
            self.after(1000, self.refresh_batch_status)
