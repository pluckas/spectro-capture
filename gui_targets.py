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
gui_targets.py
---------------
Target management tab for the Spectro Capture application.

Provides tools for adding, editing, and managing spectroscopy targets within
the application’s database. Integrates with SIMBAD for target resolution and
enables direct telescope slewing to selected objects via the verified PWI4
HTTP interface (no ASCOM).

Key features:
- Target list display with add/edit/delete functionality
- SIMBAD query integration for automatic coordinate lookup
- Telescope slew commands via PWI4 HTTP API
- Optional proper motion correction toggle (matches PWI4 "Apply PM")
- Logging of observations and recent activity
- Shared AppContext integration for database access and telescope control
"""

import tkinter as tk
from tkinter import ttk, messagebox
import csv
import webbrowser
import re
import requests
import threading

# --- START NEW BLOCK: PWI4 + PM Imports --------------------------------------
from utils import (
    resolve_target_simbad,
    resolve_target_simbad_pm,
    apply_proper_motion,
    format_coords,
    slew_pwi4,
    connect_mount
)
# --- END NEW BLOCK: PWI4 + PM Imports ----------------------------------------

FAVOURITES_PATH = r"C:\Users\Luckas Observatory\Documents\PlaneWave Instruments\PWI4\Mount\FavoriteTargets.csv"


class TargetsTab(ttk.Frame):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        self.selected_target = None
        self.favorites = []
        self.build_gui()
        self.load_favorites()
        self.slew_aborted = False

    # --------------------------------------------------------
    # UI Layout
    # --------------------------------------------------------
    def build_gui(self):
        entry_frame = ttk.Frame(self)
        entry_frame.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))
    
        # --- Row 1: Target input, Slew, PM checkbox, Stop ---
        ttk.Label(entry_frame, text="Target name:").pack(side="left", padx=(0, 5))
        self.target_entry = ttk.Entry(entry_frame, width=25)
        self.target_entry.pack(side="left", padx=(0, 5))
    
        self.slew_btn = ttk.Button(entry_frame, text="Slew Telescope", command=self.slew_telescope)
        self.slew_btn.pack(side="left", padx=(5, 5))
    
        self.pm_var = tk.BooleanVar(value=True)
        self.pm_check = ttk.Checkbutton(entry_frame, text="Apply Proper Motion", variable=self.pm_var)
        self.pm_check.pack(side="left", padx=(10, 5))
    
        self.stop_btn = ttk.Button(entry_frame, text="Stop", command=self.stop_slew)
        self.stop_btn.pack(side="left", padx=5)
    
        # --- Row 2: Other actions ---
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 5))
    
        self.simbad_btn = ttk.Button(btn_frame, text="Open in SIMBAD", command=self.open_simbad)
        self.simbad_btn.pack(side="left", padx=5)
    
        self.add_btn = ttk.Button(btn_frame, text="Add to Favourites", command=self.add_target)
        self.add_btn.pack(side="left", padx=5)
    
        self.send_btn = ttk.Button(btn_frame, text="Send to Sequencer", command=self.send_to_sequencer)
        self.send_btn.pack(side="left", padx=5)

        # --- Favourites Table ---
        table_frame = ttk.LabelFrame(self, text="Favourite Targets", padding=10)
        table_frame.grid(row=2, column=0, padx=10, pady=10, sticky="nsew")

        columns = ("Name", "RA(J2000)", "Dec(J2000)")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        for col in columns:
            self.tree.heading(col, text=col, command=lambda c=col: self.sort_column(c, False))
            self.tree.column(col, width=150 if col == "Name" else 120, anchor="center")

        self.tree.tag_configure("evenrow", background="#404040", foreground="#ffffff")
        self.tree.tag_configure("oddrow", background="#606060", foreground="#ffffff")

        style = ttk.Style()
        style.map("Treeview",
                  background=[("selected", "#0078D7")],
                  foreground=[("selected", "white")])

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.status_lbl = ttk.Label(self, text="No action yet")
        self.status_lbl.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 10))

    # --------------------------------------------------------
    # Favourites
    # --------------------------------------------------------
    def load_favorites(self):
        self.tree.delete(*self.tree.get_children())
        self.favorites = []
        try:
            with open(FAVOURITES_PATH, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or row[0].startswith("#"):
                        continue
                    name, ra, dec = row
                    tag = "evenrow" if len(self.favorites) % 2 == 0 else "oddrow"
                    self.tree.insert("", "end", values=(name, ra, dec), tags=(tag,))
                    self.favorites.append((name, ra, dec))
            self.status_lbl.config(text=f"Loaded {len(self.favorites)} favourites.")
        except Exception as e:
            self.status_lbl.config(text=f"Failed to load favourites: {e}")

    def add_target(self):
        name = self.target_entry.get().strip()
        if not name:
            messagebox.showwarning("No target", "Enter a target name first.")
            return
        try:
            ra, dec = resolve_target_simbad(name)
            ra_hms = self._format_ra(ra)
            dec_dms = self._format_dec(dec)
            with open(FAVOURITES_PATH, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([name, ra_hms, dec_dms])
            self.load_favorites()
            self.status_lbl.config(text=f"Added {name} to favourites.")
            self.context.log(f"Added {name} ({ra_hms}, {dec_dms}) to favourites.")
        except Exception as e:
            self.status_lbl.config(text=f"Failed to add target: {e}")
            self.context.log(f"Add target failed: {e}")

    def on_select(self, _event=None):
        item = self.tree.selection()
        if not item:
            self.selected_target = None
            return
        values = self.tree.item(item[0], "values")
        self.selected_target = values
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, values[0])

    # --------------------------------------------------------
    # Slew using PWI4 HTTP + Proper Motion  (non-blocking)
    # --------------------------------------------------------
    def slew_telescope(self):
        """Slew to the entered or selected target using unified PWI4 HTTP API."""
        target_name = self.target_entry.get().strip()
        if not target_name:
            messagebox.showwarning("Missing Target", "Please enter a target name or select one.")
            return
    
        def _run_slew():
            from utils import slew_target
            self.status_lbl.config(text=f"Slewing to {target_name}...")
            self.slew_aborted = False
    
            try:
                ok = slew_target(target_name, apply_pm=self.pm_var.get(), log_fn=self.context.log)
                if ok:
                    self.status_lbl.config(text=f"Slew to {target_name} complete.")
                else:
                    self.status_lbl.config(text=f"Slew failed for {target_name}.")
            except Exception as e:
                self.context.log(f"❌ Slew error: {e}")
                self.status_lbl.config(text=f"Slew failed: {e}")
    
        threading.Thread(target=_run_slew, daemon=True).start()

    # --------------------------------------------------------
    # Other Actions
    # --------------------------------------------------------
    def send_to_sequencer(self):
        name = self.target_entry.get().strip()
        if not name:
            self.status_lbl.config(text="Enter or select a target first.")
            return
        try:
            if hasattr(self.context, "sequencer_tab") and hasattr(self.context.sequencer_tab, "set_target"):
                self.context.sequencer_tab.set_target(name)
                self.status_lbl.config(text=f"Sent '{name}' to Sequencer.")
            else:
                self.status_lbl.config(text="Sequencer tab not available.")
        except Exception as e:
            self.status_lbl.config(text=f"Failed to send: {e}")
            self.context.log(f"Send to Sequencer failed: {e}")

    def open_simbad(self):
        name = self.target_entry.get().strip()
        if not name:
            self.status_lbl.config(text="Enter or select a target first.")
            return
        url = f"https://simbad.u-strasbg.fr/simbad/sim-basic?Ident={name}"
        webbrowser.open(url)
        self.context.log(f"Opened SIMBAD for {name}")
        self.status_lbl.config(text=f"Opened SIMBAD for {name}")

    def stop_slew(self):
        """Immediately stop any telescope motion via PWI4 HTTP API."""
        self.slew_aborted = True
        try:
            url = "http://localhost:8220/mount/stop"
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            self.status_lbl.config(text="Slew stopped.")
            self.context.log("Telescope slew stopped via PWI4 HTTP API.")
        except Exception as e:
            msg = f"Stop command failed: {e}"
            self.status_lbl.config(text="Stop command failed.")
            self.context.log(msg)

    # --------------------------------------------------------
    # Table Sorting
    # --------------------------------------------------------
    def sort_column(self, col, reverse):
        try:
            data = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
            data.sort(key=lambda t: t[0], reverse=reverse)
            for index, (val, k) in enumerate(data):
                self.tree.move(k, "", index)
            self.tree.heading(col, command=lambda: self.sort_column(col, not reverse))
        except Exception as e:
            print(f"Sort error on {col}: {e}")

    # --------------------------------------------------------
    # Utilities
    # --------------------------------------------------------
    def _format_ra(self, ra_hours):
        h = int(ra_hours)
        m = int((ra_hours - h) * 60)
        s = (ra_hours - h - m / 60) * 3600
        return f"{h:02d}:{m:02d}:{s:04.1f}"

    def _format_dec(self, dec_deg):
        sign = "+" if dec_deg >= 0 else "-"
        dec_deg = abs(dec_deg)
        d = int(dec_deg)
        m = int((dec_deg - d) * 60)
        s = (dec_deg - d - m / 60) * 3600
        return f"{sign}{d:02d}:{m:02d}:{s:04.1f}"

    def _sexagesimal_to_decimal(self, value, is_ra=True):
        value = value.strip()
        if not value:
            return 0.0
        sign = 1
        if not is_ra and value[0] in ('-', '+'):
            if value[0] == '-':
                sign = -1
            value = value[1:]
        parts = re.split('[: ]+', value)
        if len(parts) < 3:
            return float(value)
        h, m, s = map(float, parts[:3])
        decimal = h + m / 60.0 + s / 3600.0
        return decimal if is_ra else sign * decimal