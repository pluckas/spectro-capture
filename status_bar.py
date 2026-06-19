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
status_bar.py
-------------
Shared status indicator header and update logic as well as central target
exposure output.

"""

import tkinter as tk
from tkinter import ttk


# ---------------------------------------------------------------
# HEADER CREATION
# ---------------------------------------------------------------

def add_status_header(parent, layout="grid"):
    """
    Creates the standard Spectro Capture header row of indicators.

    Returns a dict:
        {
            "dome_dot": ttk.Label,
            "sync_dot": ttk.Label,
            "tel_dot":  ttk.Label,
            "cam_dot":  ttk.Label,
            "cooler_dot": ttk.Label,
            "bin_lbl": ttk.Label,
            "temp_lbl": ttk.Label
        }

    layout = "grid" or "pack"
    """

    header = ttk.Frame(parent)

    # Layout selection
    if layout == "grid":
        header.grid(row=0, column=0, columnspan=7, sticky="ew", pady=5)
    else:
        header.pack(fill="x", pady=5)

    w = {}  # widget dictionary

    # ----- Dome -----
    ttk.Label(header, text="Dome:").pack(side="left", padx=(5,2))
    w["dome_dot"] = ttk.Label(header, text="●", foreground="red", font=("Arial", 12))
    w["dome_dot"].pack(side="left", padx=(2,2))

    # ----- Dome Sync -----
    w["sync_dot"] = ttk.Label(header, text="●", foreground="red", font=("Arial", 12))
    w["sync_dot"].pack(side="left", padx=(0,15))

    # ----- Telescope -----
    ttk.Label(header, text="Telescope:").pack(side="left", padx=(5,2))
    w["tel_dot"] = ttk.Label(header, text="●", foreground="red", font=("Arial", 12))
    w["tel_dot"].pack(side="left", padx=(0,15))

    # ----- Camera -----
    ttk.Label(header, text="Camera:").pack(side="left", padx=(5,2))
    w["cam_dot"] = ttk.Label(header, text="●", foreground="red", font=("Arial", 12))
    w["cam_dot"].pack(side="left", padx=(3,3))

    # ----- Cooler -----
    w["cooler_dot"] = ttk.Label(header, text="●", foreground="red", font=("Arial", 12))
    w["cooler_dot"].pack(side="left", padx=(0,15))

    # ----- Binning -----
    ttk.Label(header, text="Binning:").pack(side="left", padx=(5,2))
    w["bin_lbl"] = ttk.Label(header, text="--")
    w["bin_lbl"].pack(side="left", padx=(0,15))

    # ----- Temperature -----
    ttk.Label(header, text="Temp:").pack(side="left", padx=(5,2))
    w["temp_lbl"] = ttk.Label(header, text="-- °C")
    w["temp_lbl"].pack(side="left", padx=(0,15))
    
    # ===== NEW: Lamps indicator cluster =====
    ttk.Label(header, text="Lamps:").pack(side="left", padx=(5,2))
    
    # Tungsten lamp (left)
    w["tung_dot"] = ttk.Label(header, text="●", foreground="black", font=("Arial", 12))
    w["tung_dot"].pack(side="left", padx=(2,2))
    
    # Thorium lamp (right)
    w["thor_dot"] = ttk.Label(header, text="●", foreground="black", font=("Arial", 12))
    w["thor_dot"].pack(side="left", padx=(2,15))
    # ========================================
    
    return w


# ---------------------------------------------------------------
# STATUS REFRESH LOGIC
# ---------------------------------------------------------------

def update_status_header(context, widgets, sync_blink):
    """
    Updates the indicator dots and labels based on live device status.

    Parameters:
        context     : AppContext (camera, telescope, dome, etc.)
        widgets     : dict from add_status_header()
        sync_blink  : boolean used for flashing behaviour

    Returns:
        updated sync_blink value
    """

    def safe_get(obj, attr, default=None):
        try:
            return getattr(obj, attr, default)
        except Exception:
            return default

    try:
        cam  = safe_get(context, "camera")
        tel  = safe_get(context, "telescope")
        dome = safe_get(context, "dome")
        sync = bool(safe_get(context, "dome_sync_enabled", False))

        cam_connected = bool(cam and safe_get(cam, "Connected", False))
        tel_connected = bool(tel and safe_get(tel, "Connected", False))
        dome_connected = bool(dome and safe_get(dome, "Connected", False))

        # -------------------------
        # Camera
        # -------------------------
        widgets["cam_dot"].config(foreground="green" if cam_connected else "red")

        # -------------------------
        # Cooler (yellow until at temp)
        # -------------------------
        if cam_connected:
            cooler_on = bool(safe_get(cam, "CoolerOn", False))
            if cooler_on:
                current = safe_get(cam, "CCDTemperature")
                target  = safe_get(cam, "SetCCDTemperature")
                tol = 1.0

                if isinstance(current, (float, int)) and isinstance(target, (float, int)):
                    if abs(current - target) <= tol:
                        widgets["cooler_dot"].config(foreground="green")
                    else:
                        widgets["cooler_dot"].config(foreground="yellow")
                else:
                    widgets["cooler_dot"].config(foreground="yellow")
            else:
                widgets["cooler_dot"].config(foreground="red")
        else:
            widgets["cooler_dot"].config(foreground="red")

        # -------------------------
        # Telescope
        # -------------------------
        widgets["tel_dot"].config(foreground="green" if tel_connected else "red")

        # -------------------------
        # Dome
        # -------------------------
        widgets["dome_dot"].config(foreground="green" if dome_connected else "red")

        # -------------------------
        # Dome Sync (with flashing while dome moves)
        # -------------------------
        if dome_connected and safe_get(dome, "Slewing", False):
            sync_blink = not sync_blink
            widgets["sync_dot"].config(foreground="yellow" if sync_blink else "black")
        else:
            widgets["sync_dot"].config(foreground="green" if sync else "red")
            sync_blink = False

        # -------------------------
        # Binning
        # -------------------------
        widgets["bin_lbl"].config(text=f"{safe_get(context, 'current_binning', '--')}")

        # -------------------------
        # Temperature
        # -------------------------
        if cam_connected:
            t = safe_get(cam, "CCDTemperature")
            t_str = f"{t:.1f}" if isinstance(t, (float, int)) else "--"
        else:
            t_str = "--"

        widgets["temp_lbl"].config(text=f"{t_str} °C")

        # ===== NEW: Lamps (yellow=ON, black=OFF) =====
        widgets["tung_dot"].config(
            foreground="yellow" if safe_get(context, "tung_on", False) else "black"
        )
        widgets["thor_dot"].config(
            foreground="yellow" if safe_get(context, "thor_on", False) else "black"
        )
        # =============================================

    except Exception:
        # Transient ASCOM/COM errors during aborts should not stop future refreshes.
        pass

    return sync_blink
    
    
# ---------------------------------------------------------------
#  CENTRAL EXPOSURE STATUS OUTPUT  (with routing fix)
# ---------------------------------------------------------------
def update_capture_status(
    context,
    *,
    label: str,
    index: int,
    total: int,
    elapsed: int,
    exposure: float,
):
    """
    Centralised 'during imaging' status updater.

    Routing rule:
      - If a Batch run is active (batch_tab.batch_running == True),
        send status ONLY to the Batch tab.
      - Otherwise, send status ONLY to the Sequencer tab.

    This keeps Sequencer and Batch exposure displays fully independent.
    """

    try:
        # Unified MaxIm-style status
        text = f"{label}:  Image {index} of {total}  |  Current Exposure:  {elapsed} of {int(exposure)}s"

        # Look up tabs
        seq = getattr(context, "sequencer", None)
        batch = getattr(context, "batch_tab", None)

        # Determine run state
        batch_running = bool(
            batch and getattr(batch, "batch_running", False)
        )

        if batch_running:
            # --- Batch run active → update ONLY the Batch tab ---
            if batch and hasattr(batch, "status_lbl"):
                batch.status_lbl.after(
                    0,
                    lambda t=text: batch.status_lbl.config(text=t)
                )
        else:
            # --- No batch run → update ONLY the Sequencer tab ---
            if seq and hasattr(seq, "status_lbl"):
                seq.status_lbl.after(
                    0,
                    lambda t=text: seq.status_lbl.config(text=t)
                )

    except Exception:
        # Never interrupt an imaging sequence
        pass


