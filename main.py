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
main.py
--------
Main entry point for the Spectro Capture application.

Initializes the Tkinter (or ttkbootstrap) interface, constructs the main
Notebook tab layout, and loads each GUI module (sequencer, dome, guide, etc.).
Manages application startup, geometry persistence, and safe initialization of
all subsystems through the shared AppContext.

Key features:
- Creates main application window and tabbed interface
- Initializes shared AppContext for global device access
- Loads and manages GUI tabs (sequencer, dome, guide, tools, status, etc.)
- Restores last window geometry and layout on startup
- Handles safe error recovery and PyInstaller runtime compatibility
"""

import ttkbootstrap as tb
from ttkbootstrap import ttk


# --- Safe imports for local modules ---
try:
    from app_context import AppContext
except Exception as e:
    print(f"Failed to import AppContext: {e}")
    AppContext = None

try:
    from gui_sequencer import SequencerTab
except Exception as e:
    print(f"Could not import SequencerTab: {e}")
    SequencerTab = None

try:
	from gui_batch import BatchTab
except Exception as e:
	print(f"Could not import BatchTab: {e}")
	BatchTab = None

try:
    from gui_status import StatusTab
except Exception as e:
    print(f"Could not import StatusTab: {e}")
    StatusTab = None

try:
    from gui_viewer import ViewerTab
except Exception as e:
    print(f"Could not import ViewerTab: {e}")
    ViewerTab = None

try:
    from gui_targets import TargetsTab
except Exception as e:
    print(f"Could not import TargetsTab: {e}")
    TargetsTab = None

try:
    from gui_tools import ToolsTab
except Exception as e:
    print(f"Could not import ToolsTab: {e}")
    ToolsTab = None

try:
    from gui_dome import DomeTab
except Exception as e:
    print(f"Could not import DomeTab: {e}")
    DomeTab = None

try:
    from gui_setup import SetupTab
except Exception as e:
    print(f"Could not import SetupTab: {e}")
    SetupTab = None
    
try:
    from gui_guide import GuideTab
except Exception as e:
    print(f"Could not import GuideTab: {e}")
    GuideTab = None

class SpectroscopyToolkit:
    """Main GUI class that builds the Spectro Capture window and all tab interfaces."""

    def __init__(self, root):
        self.root = root
        self.root.title("Spectro Capture")

        # Shared app context
        self.context = AppContext()
        self.context.root = self.root

        # Notebook (tabs) → ttkbootstrap handles the styling
        notebook = ttk.Notebook(root, bootstyle="primary")
        notebook.pack(fill="both", expand=True)

        # --- Sequencer tab ---
        if SequencerTab:
            self.sequencer = SequencerTab(notebook, self.context)
            notebook.add(self.sequencer, text="Sequencer")
            # Register sequencer tab in shared context for automation access
            self.context.sequencer_tab = self.sequencer
        else:
            print("Skipping Sequencer tab due to import error.")
                    
        # --- Batch tab ---
        if BatchTab:
            self.batch = BatchTab(notebook, self.context)
            notebook.add(self.batch, text="Batch")
            self.context.batch = self.batch
        else:
            print("Skipping Batch tab due to import error.")

        # --- Guide tab ---
        if GuideTab:
            self.guide = GuideTab(notebook, self.context)
            notebook.add(self.guide, text="Guide")
            # Register guide tab (guider) in shared context
            self.context.guider = self.guide
        else:
            print("Skipping Guide tab due to import error.")

        # --- Status tab ---
        if StatusTab:
            self.status = StatusTab(notebook, self.context)
            notebook.add(self.status, text="Status")
            self.context.status = self.status
        else:
            print("Skipping Status tab due to import error.")

        # --- FITS Viewer tab ---
        if ViewerTab:
            self.viewer = ViewerTab(notebook, self.context)
            notebook.add(self.viewer, text="FITS Viewer")
            self.context.viewer = self.viewer
            self.context.notebook = notebook
        else:
            print("Skipping Viewer tab due to import error.")

        # --- Targets tab ---
        if TargetsTab:
            self.targets = TargetsTab(notebook, self.context)
            notebook.add(self.targets, text="Targets")
            self.context.targets = self.targets
        else:
            print("Skipping Targets tab due to import error.")

        # --- Tools tab ---
        if ToolsTab:
            self.tools = ToolsTab(notebook, self.context)
            notebook.add(self.tools, text="Tools")
            self.context.tools = self.tools
        else:
            print("Skipping Tools tab due to import error.")

        # --- Dome tab ---
        if DomeTab:
            self.dome = DomeTab(notebook, self.context)
            notebook.add(self.dome, text="Dome")
            self.context.dome = self.dome
        else:
            print("Skipping Dome tab due to import error.")

        # --- Setup tab ---
        if SetupTab:
            self.setup = SetupTab(notebook, self.context)
            notebook.add(self.setup, text="Setup")
            self.context.setup = self.setup
        else:
            print("Skipping Setup tab due to import error.")

        # --- Make all tabs the same width as the longest label ---
        root.update_idletasks()  # ensure tabs are fully drawn
        tab_labels = [notebook.tab(tab_id, "text") for tab_id in notebook.tabs()]
        if tab_labels:  # prevent crash if all tabs skipped
            longest_label = max(tab_labels, key=len)
            char_width = len(longest_label) + 2
            style = ttk.Style()
            style.configure("TNotebook.Tab", width=char_width, anchor="center", font=("Segoe UI", 10))

        # --- Debug summary (optional) ---
        print(f"Tabs loaded: {len(notebook.tabs())}")

if __name__ == "__main__":
    # --- Create the main window ---
    try:
        root = tb.Window(themename="darkly")   # you can change to "flatly", "litera", "superhero", etc.

        # --- Restore last window geometry (size + position) ---
        from app_context import load_config, save_config
        cfg = load_config()
        geometry = cfg.get("window_geometry", "700x500")
        root.geometry(geometry)

        # --- Build the main GUI ---
        app = SpectroscopyToolkit(root)

        # --- Save geometry on close ---
        def on_close():
            try:
                cfg = load_config()
                cfg["window_geometry"] = root.geometry()
                save_config(cfg)
                print(f"Saved window geometry: {cfg['window_geometry']}")
        
                # --- Explicitly disconnect PHD2 to prevent hanging threads ---
                if hasattr(app, "guide") and getattr(app.guide, "phd", None):
                    try:
                        app.guide.phd.Disconnect()
                        print("Disconnected PHD2 cleanly.")
                    except Exception:
                        pass
        
            except Exception as e:
                print(f"Could not save window geometry: {e}")
            finally:
                root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)

        # --- Run the GUI event loop ---
        root.mainloop()

    except Exception as e:
        # --- Handle fatal startup errors (PyInstaller-safe) ---
        import tkinter.messagebox as mbox
        try:
            mbox.showerror(
                "Startup Error",
                f"Spectro Capture failed to start:\n\n{e}"
            )
        except Exception:
            # Fallback: if Tk cannot open, print to console
            print(f"Fatal startup error: {e}")
