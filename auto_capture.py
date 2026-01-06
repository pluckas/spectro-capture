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
auto_capture.py
---------------
Single-target automation orchestrator that drives the Spectro Capture
pipeline front to back.

Flow overview:
1. Slew via PWI4 and confirm the telescope/dome are settled
2. Run calibration blocks (if enabled) before engaging the guider
3. Perform smart ROI star acquisition and start PHD2 guiding
4. Manage fibre lock restore, settle verification, and adaptive exposures
5. Execute the sequencer science block and wrap up cleanly

Every stage guards the user's stop flag and automatically skips if the
required hardware interface is unavailable.
"""

import threading
import time
import math
from phd2_control import (
    ensure_connection,
    pause_guiding,
    stop_guiding,
)

# --- ADAPTIVE EXPOSURE CONFIG (USED ONLY AFTER GUIDING STARTS) --------------
# These exposure presets are used exclusively by the adaptive exposure thread
# to adjust the guider exposure during guiding, fibre restore, fibre settle,
# and science exposures. They have NOTHING to do with ROI star-finding.
#
# ROI star acquisition uses its own separate trial_exposures list within the
# ROI search block of run_auto_capture(), before guiding begins.
#
# MIN_PROXY / MAX_PROXY define the wide-bracket brightness limits used during
# early guiding (pre-fibre), while LOCK_MIN / LOCK_MAX may be used later for
# tighter control once the star is stable on the fibre.
EXPOSURE_PRESETS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 7.0]
MIN_PROXY = 2000
MAX_PROXY = 65000


# --- ADAPTIVE EXPOSURE THREAD ---------------------------------------------
def adaptive_exposure_loop(g, context):
    """Background adaptive exposure controller."""
    current_exp = g.GetExposure() / 1000.0 if hasattr(g, "GetExposure") else 1.0
    context.guide_log(f"Adaptive exposure thread active (starting {current_exp:.2f}s)")

    while not context.adaptive_stop.is_set():
        try:
            if g.AppState in ("Paused", "Stopped", "Calibrating"):
                context.guide_log("Guiding paused — suspending adaptive exposure adjustments.")
                time.sleep(2.0)
                continue

            star_mass = getattr(g, "StarMass", 0.0)
            hfd = getattr(g, "HFD", 2.5)
            footprint = max(1.0, math.pi * (hfd / 2.0) ** 2)
            proxy = star_mass / footprint
            
            idx = min(range(len(EXPOSURE_PRESETS)), key=lambda i: abs(EXPOSURE_PRESETS[i] - current_exp))
            new_exp = current_exp
            
            # ===== BEGIN NEW: dual-range threshold selection =====
            if getattr(context, "on_fibre", False):
                threshold_low  = 25000
                threshold_high = 55000
            else:
                threshold_low  = MIN_PROXY
                threshold_high = MAX_PROXY
            # ===== END NEW =====
            
            # ===== BEGIN NEW BLOCK: hysteresis counters =====
            # Create counters on first run
            if not hasattr(context, "_exp_low_count"):
                context._exp_low_count = 0
            if not hasattr(context, "_exp_high_count"):
                context._exp_high_count = 0
            
            # For on-fibre mode, require more evidence before changing exposure.
            # Pre-fibre stays fast-response.
            required = 3 if getattr(context, "on_fibre", False) else 1
            
            # In-band → reset counters and lock
            if threshold_low <= proxy <= threshold_high:
                context._exp_low_count = 0
                context._exp_high_count = 0
                if not getattr(context, "_adaptive_locked", False):
                    context.guide_log("✅ Exposure Locked (within optimal ADU range)")
                    context._adaptive_locked = True
            
            # Below-band → count toward increasing exposure
            elif proxy < threshold_low:
                context._exp_low_count += 1
                context._exp_high_count = 0
                if context._exp_low_count >= required and idx < len(EXPOSURE_PRESETS) - 1:
                    new_exp = EXPOSURE_PRESETS[idx + 1]
                    context.guide_log(f"-> Increasing exposure to {new_exp:.2f}s")
                    context._adaptive_locked = False
                    context._exp_low_count = 0  # reset after action
            
            # Above-band → count toward decreasing exposure
            elif proxy > threshold_high:
                context._exp_high_count += 1
                context._exp_low_count = 0
                if context._exp_high_count >= required and idx > 0:
                    new_exp = EXPOSURE_PRESETS[idx - 1]
                    context.guide_log(f"-> Decreasing exposure to {new_exp:.2f}s")
                    context._adaptive_locked = False
                    context._exp_high_count = 0  # reset after action
            # ===== END NEW BLOCK =====

            if new_exp != current_exp:
                g.SetExposure(new_exp)
                current_exp = new_exp

            time.sleep(current_exp + 1.0)

        except Exception as e:
            context.log(f"Adaptive exposure error: {e}")
            time.sleep(2.0)

    print("Adaptive exposure thread ending.")


# --- MAIN AUTO CAPTURE -----------------------------------------------------
def run_auto_capture(context):
    """Main entry point triggered by the 'Auto Capture ▶' button."""

    def worker():
        context.stop_requested.clear()
    
        if not hasattr(context, "adaptive_stop"):
            context.adaptive_stop = threading.Event()
        else:
            context.adaptive_stop.clear()
    
        # ===== BEGIN NEW: dual-range exposure flag =====
        context.on_fibre = False
        # ===== END NEW =====
    
        try:
            seq = getattr(context, "sequencer_tab", None)
            guide = getattr(context, "guider", None)
            tel = getattr(context, "telescope", None)
            dome = getattr(context, "dome", None)

            if not seq:
                context.log("Sequencer tab not available — aborting Auto Capture.")
                return

            target = seq.target_entry.get().strip() or "ManualTarget"
            context.log(f"===== Starting Auto Capture for {target} =====")

            # --------------------------------------------------
            # Slew telescope (always via PWI4 HTTP)
            # --------------------------------------------------
            try:
                from utils import slew_target
                context.log(f"Initiating PWI4 slew for Auto Capture target: {target}")
                ok = slew_target(target, apply_pm=True, log_fn=context.log)
                context.log(f"{'✅' if ok else '⚠️'} Slew to {target} {'complete' if ok else 'failed or skipped'}.")
            except Exception as e:
                context.log(f"❌ Slew failed for {target}: {e}")

            # --------------------------------------------------
            # Wait for telescope and dome to settle
            # --------------------------------------------------
            try:
                context.log("Waiting for telescope and dome to settle...")
                time.sleep(2)
                consecutive_clear = 0
                start_time = time.time()
                while time.time() - start_time < 180:
                    tel_slewing = getattr(tel, "Slewing", False)
                    dome_slewing = getattr(dome, "Slewing", False)
                    if not tel_slewing and not dome_slewing:
                        consecutive_clear += 1
                        if consecutive_clear >= 2:
                            break
                    else:
                        consecutive_clear = 0
                    moving = []
                    if tel_slewing:
                        moving.append("telescope")
                    if dome_slewing:
                        moving.append("dome")
                    context.log(f"⏳ Still moving: {', '.join(moving)}")
                    time.sleep(2)
                time.sleep(2)
                context.log("✅ Telescope and dome settled — safe to continue.")
            except Exception as e:
                context.log(f"⚠️ Settle wait check failed: {e}")

            # --------------------------------------------------
            # Calibration frames (moved to before guiding)
            # --------------------------------------------------
            include_calibs = getattr(context, "include_calibrations", True)
            if context.stop_requested.is_set():
                context.log("🛑 Stop flag detected — skipping calibration block.")
                return
            if include_calibs:
                try:
                    context.log("Running calibration block before guiding...")
                    seq.run_calibration()
                    while seq.sequence_running:
                        if context.stop_requested.is_set():
                            context.log("🛑 Stop flag detected — aborting calibration early.")
                            return
                        time.sleep(2)
                    context.log("✅ Calibration block complete.")
                except Exception as e:
                    context.log(f"Calibration sequence failed: {e}")
            else:
                context.log("Skipping calibration frames (unchecked).")

            # --------------------------------------------------
            # PHD2 guiding: loop, ROI Smart Find, start guiding
            # --------------------------------------------------
            if guide:
                try:
                    if not ensure_connection(context):
                        context.guide_log("PHD2 not reachable — skipping guiding.")
                        return
                    g = guide.phd

                    # Wait for dome before starting guider
                    if dome and getattr(dome, "Connected", False):
                        context.log("Checking dome state before starting guider...")
                        start_time = time.time()
                        while getattr(dome, "Slewing", False) and time.time() - start_time < 120:
                            time.sleep(2)
                        if getattr(dome, "Slewing", False):
                            context.log("⚠️ Dome still reporting Slewing — continuing anyway.")
                        else:
                            context.log("✅ Dome is stationary — safe to start guider.")
                    else:
                        context.log("No dome connected — proceeding with guider startup.")

                    # Reset guider exposure BEFORE looping
                    try:
                        context.guide_log("Setting guider exposure to 0.01 s before starting loop...")
                        g.SetExposure(0.01)
                        time.sleep(2)
                    except Exception as e:
                        context.guide_log(f"Warning: could not preset guider exposure: {e}")

                    # Start looping exposures
                    context.guide_log("Starting looping exposures...")
                    g.Loop()
                    time.sleep(5.0)

                    # ROI SMART FIND
                    context.guide_log("Attempting smart ROI star acquisition (multi-exposure test)...")
                    
                    # --- Build ROI box from context-configured values ---
                    cx = getattr(context, "roi_centre_x", 0)
                    cy = getattr(context, "roi_centre_y", 0)
                    size = getattr(context, "roi_size", 0)
                    
                    if not (cx and cy and size):
                        context.guide_log("⚠️ ROI not configured (centre/size missing or zero) — skipping ROI star search.")
                        g.StopCapture()
                        return
                    
                    try:
                        size_int = int(size)
                        x0 = int(cx - size_int / 2)
                        y0 = int(cy - size_int / 2)
                        roi_box = [x0, y0, size_int, size_int]
                    except Exception as e:
                        context.guide_log(f"⚠️ Invalid ROI configuration ({cx}, {cy}, {size}): {e}")
                        g.StopCapture()
                        return
                    
                    roi_found = False
                    trial_exposures = [0.01, 0.1, 0.5, 1.0, 3.0, 5.0, 6.0, 7.0, 8.0]
                    
                    for trial in trial_exposures:
                        if context.stop_requested.is_set():
                            context.guide_log("🛑 Stop flag detected — aborting ROI star search early.")
                            return
                        try:
                            g.SetExposure(trial)
                            context.guide_log(f"Trying exposure {trial:.2f}s in ROI {roi_box}...")
                            time.sleep(2.0)
                            g.FindStar(roi_box)
                            context.guide_log("ROI-based star search issued.")
                            time.sleep(3.0)
                            if g.AppState in ("Selected", "Looping", "Guiding"):
                                context.guide_log(f"✅ Star found in ROI at {trial:.2f}s — continuing.")
                                roi_found = True
                            
                                # ===== BEGIN ROI AUTO-AVERAGE SAMPLE INSERT =====
                                # Record the (x, y) star position to app_context for ROI averaging
                                try:
                                    pos = g.GetLockPosition()   # returns (x, y)
                                    if isinstance(pos, (list, tuple)) and len(pos) == 2:
                                        context.record_roi_hit(pos[0], pos[1])
                                
                                        # ===== NEW: update ROI average label in Setup tab =====
                                        if context.setup:
                                            try:
                                                context.setup.update_roi_average_label()
                                            except Exception:
                                                pass
                                        # ===== END NEW =====
                                
                                except Exception as e:
                                    context.guide_log(f"ROI sample record failed: {e}")
                                # ===== END ROI AUTO-AVERAGE SAMPLE INSERT =====
                            
                                break
                            else:
                                context.guide_log(f"❌ No star found at {trial:.2f}s — trying next.")
                        except Exception as e:
                            context.guide_log(f"⚠️ Trial {trial:.2f}s failed: {e}")

                    if not roi_found:
                        context.guide_log("❌ Could not find any star in ROI after all trials.")
                        g.StopCapture()
                        return

                    context.guide_log("✅ ROI star acquisition successful — proceeding to guiding.")
                    if context.stop_requested.is_set():
                        context.guide_log("🛑 Stop flag detected — aborting before guiding.")
                        return

                    # Start guiding
                    context.guide_log("Starting guiding...")
                    g.Guide(settlePixels=1.5, settleTime=2.0, settleTimeout=10.0)
                    context.guide_log("Guiding started.")

                    # Start adaptive exposure control in background
                    threading.Thread(target=adaptive_exposure_loop, args=(g, context), daemon=True).start()

                    context.guide_log("Waiting 5 s for guider to stabilise before fibre lock restore...")
                    time.sleep(5)

                    # Fibre restore
                    try:
                        if hasattr(guide, "restore_lock"):
                            context.guide_log("Restoring fibre lock position via GuideTab method...")
                            guide.restore_lock()
                            context.guide_log("Fibre lock restore command issued successfully.")
                            time.sleep(7)
                        else:
                            context.guide_log("GuideTab restore_lock() not available — skipping restore.")
                    except Exception as e:
                        context.guide_log(f"Warning: could not restore fibre lock — {e}")

                    # Fibre settle verification
                    try:
                        g = guide.phd
                        guider_exp = getattr(g, "GetExposure", lambda: 1000)() / 1000.0
                        tolerance_near = 10
                        context.guide_log(f"Waiting for star to reach fibre region (≤{tolerance_near}px)...")
                        while True:
                            try:
                                state, dist = g.GetStatus()
                            except Exception:
                                dist = None
                            if dist is None:
                                time.sleep(guider_exp + 1)
                                continue
                            if dist <= tolerance_near:
                                context.guide_log("✅ Star reached fibre region.")
                                break
                            time.sleep(guider_exp + 1)

                        tolerance_stable = 8 if guider_exp >= 2.0 else 5
                        required_stable = 2 if guider_exp >= 2.0 else 3
                        stable_reads = 0
                        timeout_stable = max(180, guider_exp * 40)
                        start_time = time.time()
                        context.guide_log(f"Refining fibre lock (tolerance={tolerance_stable}px)...")

                        while time.time() - start_time < timeout_stable:
                            if context.stop_requested.is_set():
                                context.guide_log("🛑 Stop flag detected — aborting fibre-settle loop early.")
                                return
                            try:
                                state, dist = g.GetStatus()
                            except Exception:
                                dist = None
                            if dist is None:
                                time.sleep(guider_exp + 1)
                                continue
                            if dist <= tolerance_stable:
                                stable_reads += 1
                                if stable_reads >= required_stable:
                                    context.guide_log("✅ Guider stable on fibre (within fine tolerance).")
                                    # ===== BEGIN NEW: mark fibre-settle complete =====
                                    context.on_fibre = True
                                    context.guide_log("🔒 Switching adaptive exposure to FIBRE mode (LOCK_MIN/LOCK_MAX).")
                                    # ===== END NEW =====
                                    break
                            else:
                                stable_reads = 0
                            time.sleep(guider_exp + 1)
                        else:
                            context.guide_log("⚠️ Guider not perfectly stable but star is on fibre — continuing.")
                    except Exception as e:
                        context.guide_log(f"Warning: fibre-settle verification failed: {e}")

                except Exception as e:
                    context.guide_log(f"Guider phase failed: {e}")
            else:
                context.guide_log("No guider available — skipping PHD2 actions.")

            # --------------------------------------------------
            # Science target sequence
            # --------------------------------------------------
            context.log(f"Running target sequence for {target} ...")
            if context.stop_requested.is_set():
                context.log("🛑 Stop flag detected — aborting before target imaging.")
                return

            try:
                seq.run_target()
                while seq.sequence_running:
                    time.sleep(5)
                context.log(f"Target sequence for {target} complete.")
            except Exception as e:
                context.log(f"Target run failed: {e}")

            # --------------------------------------------------
            # Wrap up
            # --------------------------------------------------
            if guide:
                try:
                    stop_guiding(context)
                    context.guide_log("PHD2 guiding session ended.")
                except Exception as e:
                    context.log(f"Could not stop guiding: {e}")

            context.log("Auto Capture complete.")

            # Stop adaptive thread
            try:
                if hasattr(context, "adaptive_stop"):
                    context.adaptive_stop.set()
                    print("Adaptive exposure thread stop signal sent.")
            except Exception as e:
                context.guide_log(f"Could not signal adaptive stop: {e}")

        except Exception as e:
            context.log(f"Auto Capture failed: {e}")

    threading.Thread(target=worker, daemon=True).start()
