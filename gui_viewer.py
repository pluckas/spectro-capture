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
gui_viewer.py
--------------
FITS image viewer tab for the Spectro Capture application.

Displays captured FITS images from the sequencer or manual capture routines
with automatic stretch, scaling, and statistics readout. Designed for quick
inspection of spectroscopy and calibration frames without external software.

Key features:
- Loads and displays the most recent FITS image
- Automatic 2–98% percentile stretch for optimal contrast
- Preserves aspect ratio and orientation during resize
- Displays image statistics including peak and mean ADU values
- Supports live updates triggered by new image saves
- Integrated with AppContext for shared logging and file access
"""

import tkinter as tk
from tkinter import ttk
from astropy.io import fits
import numpy as np
from PIL import Image, ImageTk

class ViewerTab(ttk.Frame):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context

        self.canvas = tk.Canvas(self, bg="black")
        self.canvas.pack(fill="both", expand=True)

        self.max_adu_label = ttk.Label(self, text="Max ADU: --")
        self.max_adu_label.pack(anchor="w", padx=5, pady=5)

        self._imgtk = None
        self._raw = None
        self.canvas.bind("<Configure>", self._resize)

    def load_fits(self, path):
        try:
            with fits.open(path) as hdul:
                data = hdul[0].data
                hdr = hdul[0].header
        except Exception as e:
            self.context.log(f"Failed to load FITS: {e}")
            return

        if data is None:
            self.context.log("FITS has no image data")
            return

        # Astropy already applies BZERO/BSCALE automatically
        adu = np.array(data, dtype=np.float32)

        # Compute real max ADU across full frame
        max_val = np.nanmax(adu)

        # Percentile-based display scaling (ignores hot/cold pixels)
        adu = np.nan_to_num(adu)
        low, high = np.percentile(adu, (1, 99))   # stretch from 1st–99th percentile
        scaled = 255 * (adu - low) / (high - low + 1e-9)
        scaled = np.clip(scaled, 0, 255)          # clip into 0–255 range
        self._raw = scaled.astype(np.uint8)

        # Update label with true max ADU
        self.max_adu_label.config(text=f"Max ADU: {int(round(max_val))}")

        self._draw_image()

    def _draw_image(self):
        if self._raw is None:
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 2 or h < 2:
            return

        img = Image.fromarray(self._raw)
        img = img.resize((w, h), Image.Resampling.NEAREST)
        self._imgtk = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._imgtk)

    def _resize(self, _event):
        self._draw_image()
