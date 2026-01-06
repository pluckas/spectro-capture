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
gui_status.py
---------------
Status and logging tab for the Spectro Capture application.

Displays real-time system messages, progress updates, and diagnostic output
from all active subsystems. Provides a central logging interface shared across
the application for both user-visible updates and backend operations.

Key features:
- Scrollable text log displaying timestamped status messages
- Live updates from sequencer, guider, dome, and telescope modules
- Integration with AppContext for unified logging and message routing
- Supports both GUI and console-based message output
- Thread-safe message handling for concurrent operations
"""

import tkinter as tk
from tkinter import ttk

class StatusTab(ttk.Frame):
    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context

        # --- Shared status header ---
        from status_bar import add_status_header, update_status_header
        self._sync_blink = False
        self.indicators = add_status_header(self, layout="pack")

        # Build log window
        self.text = tk.Text(self, wrap="word", height=20, width=100, state="disabled")
        self.text.pack(fill="both", expand=True, padx=5, pady=5)

        # Attach logging callback
        self.context.set_log_callback(self.log)

        # Start refresh loop
        self.after(1000, self.refresh_status_header)

    def log(self, msg):
        """Append a message to the status log (thread-safe)."""
        def _append():
            self.text.configure(state="normal")
            self.text.insert(tk.END, msg + "\n")
            self.text.see(tk.END)  # auto scroll
            self.text.configure(state="disabled")
        # ensure we update the Text widget on the Tk main thread
        self.after(0, _append)
        
    # -------------------------------------------------
    #  Status Bar Refresh
    # -------------------------------------------------
    def refresh_status_header(self):
        """Update the shared Sequencer/Batch/Guide/Status status bar."""
        from status_bar import update_status_header
        self._sync_blink = update_status_header(
            self.context,
            self.indicators,
            self._sync_blink
        )
        self.after(1000, self.refresh_status_header)
