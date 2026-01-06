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
session_manager.py
------------------
Batch orchestrator that chains real Auto Capture runs.

Each target in the Batch list is executed exactly as if the user
pressed “Auto Capture ▶” for that target in the Sequencer tab.
Nothing in auto_capture.py is changed or duplicated.

Behaviour per target:
    1.  Set Sequencer fields (target, exposure, frames, calibs)
    2.  Call run_auto_capture(context)
    3.  Wait until Auto Capture thread finishes
    4.  Move to next target
"""

import threading
import time
from datetime import datetime
from auto_capture import run_auto_capture


# -------------------------------------------------------------------------
def run_batch(context, plan):
    """Start the batch run in a background thread."""
    if not plan:
        context.log("No targets provided for batch run.")
        return

    batch_tab = getattr(context, "batch_tab", None)

    # --- Reset flags --------------------------------------------------------
    if batch_tab and getattr(batch_tab, "batch_running", False):
        context.log("⚠️ Previous batch still active — resetting state.")
    if batch_tab:
        batch_tab.batch_running = True
        batch_tab.stop_requested = False
        batch_tab.status_lbl.config(text="Batch running...")

    if hasattr(context, "stop_requested"):
        try:
            context.stop_requested.clear()
        except Exception:
            pass

    worker = threading.Thread(target=_batch_worker, args=(context, plan), daemon=True)
    worker.start()


# -------------------------------------------------------------------------
def _batch_worker(context, plan):
    """Sequentially launch Auto Capture for each target."""
    batch_tab = getattr(context, "batch_tab", None)
    seq = getattr(context, "sequencer_tab", None)

    context.log(f"🚀 Starting batch run at {datetime.now():%H:%M:%S}")
    context.log(f"Total targets in queue: {len(plan)}")

    if not seq:
        context.log("❌ Sequencer tab not available — aborting batch run.")
        if batch_tab:
            batch_tab.status_lbl.config(text="Sequencer unavailable.")
        return

    try:
        for i, row in enumerate(plan, start=1):
            # --- Global stop check ------------------------------------------
            if hasattr(context, "stop_requested") and context.stop_requested.is_set():
                context.log("⏹ Batch stop requested — aborting remaining targets.")
                break

            try:
                name, exp, frames, calib = row
                exp = float(exp)
                frames = int(frames)
                include_calib = calib.strip() == "✓"
            except Exception as e:
                context.log(f"⚠️ Skipping malformed row {row}: {e}")
                continue

            context.log(f"🟢 [{i}/{len(plan)}] Starting target '{name}'")
            if batch_tab:
                batch_tab.status_lbl.config(text=f"Running {name} ({i}/{len(plan)})...")

            # --- Configure Sequencer just like manual input -----------------
            try:
                seq.target_entry.delete(0, "end")
                seq.target_entry.insert(0, name)
                seq.targ_exp.delete(0, "end")
                seq.targ_exp.insert(0, str(exp))
                seq.targ_count.delete(0, "end")
                seq.targ_count.insert(0, str(frames))
                seq.include_calibs.set(include_calib)
                context.log(f"🧭 Sequencer updated: {name}, {exp}s × {frames}, Calib={include_calib}")
            except Exception as e:
                context.log(f"Sequencer setup failed for {name}: {e}")
                continue

            # --- Start Auto Capture for this target -------------------------
            try:
                context.log(f"▶ Launching Auto Capture thread for {name}...")
                run_auto_capture(context)
            except Exception as e:
                context.log(f"❌ Auto Capture launch failed for {name}: {e}")
                continue

            # --- Wait for Auto Capture to finish ----------------------------
            context.log("⏳ Waiting for Auto Capture thread to finish...")
            _wait_for_auto_capture(context, name)

            context.log(f"✅ Completed target '{name}'")
            time.sleep(3)

        if not (hasattr(context, "stop_requested") and context.stop_requested.is_set()):
            context.log("🎯 Batch run completed successfully.")
            if batch_tab:
                batch_tab.status_lbl.config(text="Batch complete.")
        else:
            if batch_tab:
                batch_tab.status_lbl.config(text="Batch stopped early.")

    except Exception as e:
        context.log(f"❌ Batch error: {e}")

    finally:
        if batch_tab:
            batch_tab.batch_running = False
            batch_tab.stop_requested = False
        if hasattr(context, "stop_requested"):
            try:
                context.stop_requested.clear()
            except Exception:
                pass
        context.log("🛑 Batch thread finished.")


# -------------------------------------------------------------------------
def _wait_for_auto_capture(context, name):
    """
    Wait until the Auto Capture thread finishes for the current target.
    This relies on sequence_running and stop_requested flags.
    """
    start = time.time()
    seq = getattr(context, "sequencer_tab", None)

    while True:
        if hasattr(context, "stop_requested") and context.stop_requested.is_set():
            context.log(f"🛑 Stop requested — aborting current Auto Capture ({name}).")
            return
        # sequence_running is cleared when run_target() finishes
        running = getattr(seq, "sequence_running", False)
        if not running:
            # Add a small grace period to ensure guider stops
            time.sleep(1.5)
            break
        time.sleep(2)
        if time.time() - start > 3600:  # 1-hour cap per target
            context.log(f"⚠️ Timeout waiting for Auto Capture to finish for {name}")
            break
    context.log(f"✅ Auto Capture thread finished for {name}")
