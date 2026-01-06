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
phd2_control.py
---------------
Backend control layer for PHD2 guiding, independent of the GUI.

Provides lightweight, context-aware functions for both manual and automated
operations. Used by gui_guide.py for interactive control and by
auto_capture.py for unattended guiding during automated capture sequences.

Key features:
- JSON-RPC communication with PHD2 guiding software
- Connect, loop exposures, and manage guiding state
- Star acquisition (global or ROI-based)
- Fibre lock restore and settle verification routines
- Adaptive exposure management during Auto Capture
- Built on the local phd2client.guider module
  (dependencies/phd2client/guider.py) for low-level communication
"""

import time
import math
import threading

# -----------------------------------------------------------------------------
#  Core Guider Operations
# -----------------------------------------------------------------------------

def ensure_connection(context):
    """Return True if guider is connected and responsive."""
    g = getattr(context, "guider", None)
    if not g:
        context.log("No guider instance available.")
        return False
    try:
        if hasattr(g, "ensure_connection"):
            return g.ensure_connection()
        elif hasattr(g, "conn"):
            return g.conn is not None and g.conn.IsConnected()
        else:
            context.log("Guider object has no connection attributes.")
            return False
    except Exception as e:
        context.log(f"Guider connection check failed: {e}")
        return False


def loop(context):
    """Start looping exposures in PHD2."""
    try:
        context.log("Starting PHD2 looping...")
        if hasattr(context.guider, "start_loop"):
            context.guider.start_loop()
        elif hasattr(context.guider, "Loop"):
            context.guider.Loop()
        else:
            context.log("No looping method found.")
    except Exception as e:
        context.log(f"PHD2 Loop() failed: {e}")


def find_star(context, roi=None):
    """Ask PHD2 to auto-select a guide star."""
    try:
        context.log("PHD2: finding star...")
        g = context.guider
        if hasattr(g, "find_star"):
            g.find_star()
        elif hasattr(g, "FindStar"):
            g.FindStar(roi)
        else:
            context.log("Guider has no FindStar() method — skipping.")
    except Exception as e:
        context.log(f"PHD2 FindStar() failed: {e}")


def start_guiding(context):
    """Begin guiding with sensible default settle parameters."""
    try:
        context.log("Starting PHD2 guiding...")
        g = context.guider
        if hasattr(g, "start_guiding"):
            g.start_guiding()
        elif hasattr(g, "Guide"):
            g.Guide(0.5, 5.0, 30.0)
        else:
            context.log("No guiding start method found.")
    except Exception as e:
        context.log(f"PHD2 Guide() failed: {e}")


def pause_guiding(context):
    """Pause guiding (looping exposures continue)."""
    try:
        context.log("Pausing guiding...")
        g = context.guider
        if hasattr(g, "pause_guiding"):
            g.pause_guiding()
        elif hasattr(g, "Pause"):
            g.Pause()
        else:
            context.log("No pause method found.")
    except Exception as e:
        context.log(f"PHD2 Pause() failed: {e}")


def resume_guiding(context):
    """Resume guiding after a pause."""
    try:
        context.log("Resuming guiding...")
        g = context.guider
        if hasattr(g, "unpause_guiding"):
            g.unpause_guiding()
        elif hasattr(g, "Unpause"):
            g.Unpause()
        else:
            context.log("No resume/unpause method found.")
    except Exception as e:
        context.log(f"PHD2 Unpause() failed: {e}")


def stop_guiding(context):
    """Stop guiding entirely."""
    try:
        context.log("Stopping PHD2 guiding...")
        g = context.guider
        if hasattr(g, "stop_guiding"):
            g.stop_guiding()
        elif hasattr(g, "StopCapture"):
            g.StopCapture()
        else:
            context.log("No stop method found.")
    except Exception as e:
        context.log(f"PHD2 StopCapture() failed: {e}")


def restore_lock(context):
    """Restore previously saved guide lock position."""
    try:
        context.log("Restoring PHD2 lock position...")
        g = context.guider
        if hasattr(g, "restore_lock"):
            g.restore_lock()
        elif hasattr(g, "RestoreLockPosition"):
            g.RestoreLockPosition()
        else:
            context.log("No restore lock method found.")
    except Exception as e:
        context.log(f"PHD2 RestoreLockPosition() failed: {e}")


# -----------------------------------------------------------------------------
#  Status and Monitoring
# -----------------------------------------------------------------------------

def get_stats(context):
    """Return the latest guider statistics as a dictionary."""
    g = context.guider
    try:
        if hasattr(g, "get_stats"):
            stats_obj = g.get_stats()
        elif hasattr(g, "GetStats"):
            stats_obj = g.GetStats()
        else:
            context.log("No GetStats() available.")
            return None
    except Exception as e:
        context.log(f"PHD2 GetStats() failed: {e}")
        return None

    if not stats_obj:
        return None

    try:
        return {
            "RMS_RA": getattr(stats_obj, "rms_ra", 0),
            "RMS_Dec": getattr(stats_obj, "rms_dec", 0),
            "RMS_Tot": getattr(stats_obj, "rms_tot", 0),
            "Peak_RA": getattr(stats_obj, "peak_ra", 0),
            "Peak_Dec": getattr(stats_obj, "peak_dec", 0),
        }
    except Exception as e:
        context.log(f"Error converting GuideStats: {e}")
        return None


def get_rms(context):
    stats = get_stats(context)
    if not stats:
        return None
    return stats.get("RMS_Tot", None)


def get_hfd(context):
    context.log("HFD unavailable from GuideStats — returning None.")
    return None


def is_guiding(context):
    """Return True if PHD2 AppState == 'Guiding'."""
    g = context.guider
    try:
        if hasattr(g, "is_guiding"):
            return g.is_guiding()
        elif hasattr(g, "GetStatus"):
            state, _ = g.GetStatus()
            return state == "Guiding"
        else:
            return False
    except Exception:
        return False


# -----------------------------------------------------------------------------
#  Stability and Waiting Logic
# -----------------------------------------------------------------------------

def wait_for_stable_guiding(context, tight=True, timeout=60):
    limit = context.cfg.get("RMS_TIGHT" if tight else "RMS_LOOSE", 0.5)
    abort_limit = context.cfg.get("RMS_ABORT", 2.0)
    context.log(f"Waiting for guiding stability (limit={limit}\")...")
    start = time.time()
    stable_count = 0

    while time.time() - start < timeout:
        if getattr(context, "stop_flag", False):
            context.log("Aborting wait_for_stable_guiding() due to stop flag.")
            return False

        rms = get_rms(context)
        if rms is None:
            time.sleep(2)
            continue

        if rms < limit:
            stable_count += 1
        elif rms > abort_limit:
            context.log(f"Warning: guiding unstable (RMS={rms:.2f}\")")
            stable_count = 0
        else:
            stable_count = 0

        if stable_count >= 3:
            context.log(f"Guiding stable at RMS={rms:.2f}\", continuing.")
            return True

        time.sleep(2)

    context.log("Guiding did not reach stability threshold within timeout.")
    return False


# -----------------------------------------------------------------------------
#  Utility
# -----------------------------------------------------------------------------

def graceful_stop(context):
    if is_guiding(context):
        stop_guiding(context)
    context.log("PHD2 guiding session ended.")

# REMOVED: adaptive_exposure_control() and references to adaptive_stop
