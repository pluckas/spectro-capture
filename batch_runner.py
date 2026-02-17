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
batch_runner.py
---------------
Multi-target automation orchestrator for Spectro Capture.

Conventional mode:
- Runs rows in table order.
- Optional per-row UT start time supported.
- Telescope will wait (parked) until the scheduled time if necessary.
- Shutdown happens when the last enabled target finishes.

HA mode ("smart mode"):
- Dynamically selects targets within the HA window.
- Timed targets act as appointments and always take priority.
- Scheduler avoids starting HA targets that would overlap a timed target.
- HA targets are only started when sufficient night remains.

Twilight behaviour:
- New opportunistic (HA) targets will NOT start after:
      latest start = nautical twilight rising (Sun = -12°) − 60 minutes
- Timed targets MAY start before this limit and run past twilight.
- Any target already in progress is never interrupted by the twilight limit.

General behaviour:
- The system prefers waiting over starting a risky target.
- Telescope parks during idle gaps and re-evaluates conditions periodically.
- There is NO batch stop-time concept anywhere in this file.
"""

import threading
import time
import math
from datetime import datetime, timezone, timedelta

from phd2_control import (
    ensure_connection,
    stop_guiding,
)

from utils import (
    hour_angle_hours,
    resolve_target_simbad,
    nautical_dawn_utc,
)

from dome_backend import is_shutter_closed


# --- ROI / ADAPTIVE EXPOSURE CONFIG ----------------------------------------
EXPOSURE_PRESETS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 7.0]
MIN_PROXY = 2000
MAX_PROXY = 65000


# ---------------------------------------------------------------------------
# Twilight cutoff helpers
# ---------------------------------------------------------------------------

def _get_nautical_twilight_rising_utc():
    dt = nautical_dawn_utc()
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _latest_science_start_utc(context):
    """
    Latest time (UTC) we are allowed to START a new science target in HA mode.
    Rule: latest_start = nautical_twilight_rising_utc - 60 minutes.
    """
    tw_utc = _get_nautical_twilight_rising_utc()
    if not tw_utc:
        return None
    return tw_utc - timedelta(minutes=60)


# --- ADAPTIVE EXPOSURE THREAD ----------------------------------------------
def adaptive_exposure_loop(g, context):
    """Background adaptive exposure controller."""
    current_exp = g.GetExposure() / 1000.0 if hasattr(g, "GetExposure") else 1.0
    context.guide_log(f"Adaptive exposure thread active (starting {current_exp:.2f}s)")

    while not context.adaptive_stop.is_set():
        try:
            # End-of-target state — guiding lifecycle is over
            if g.AppState == "Stopped":
                break

            star_mass = getattr(g, "StarMass", 0.0)
            hfd = getattr(g, "HFD", 2.5)
            footprint = max(1.0, math.pi * (hfd / 2.0) ** 2)
            proxy = star_mass / footprint

            idx = min(range(len(EXPOSURE_PRESETS)), key=lambda i: abs(EXPOSURE_PRESETS[i] - current_exp))
            new_exp = current_exp

            # Dual-range threshold selection
            if getattr(context, "on_fibre", False):
                threshold_low = 25000
                threshold_high = 55000
            else:
                threshold_low = MIN_PROXY
                threshold_high = MAX_PROXY

            # Hysteresis counters
            if not hasattr(context, "_exp_low_count"):
                context._exp_low_count = 0
            if not hasattr(context, "_exp_high_count"):
                context._exp_high_count = 0

            required = 3 if getattr(context, "on_fibre", False) else 1

            # In-band
            if threshold_low <= proxy <= threshold_high:
                context._exp_low_count = 0
                context._exp_high_count = 0
                if not getattr(context, "_adaptive_locked", False):
                    context.guide_log("✅ Exposure Locked (within optimal ADU range)")
                    context._adaptive_locked = True

            # Below-band → increase
            elif proxy < threshold_low:
                context._exp_low_count += 1
                context._exp_high_count = 0
                if context._exp_low_count >= required and idx < len(EXPOSURE_PRESETS) - 1:
                    new_exp = EXPOSURE_PRESETS[idx + 1]
                    context.guide_log(f"-> Increasing exposure to {new_exp:.2f}s")
                    context._adaptive_locked = False
                    context._exp_low_count = 0

            # Above-band → decrease
            elif proxy > threshold_high:
                context._exp_high_count += 1
                context._exp_low_count = 0
                if context._exp_high_count >= required and idx > 0:
                    new_exp = EXPOSURE_PRESETS[idx - 1]
                    context.guide_log(f"-> Decreasing exposure to {new_exp:.2f}s")
                    context._adaptive_locked = False
                    context._exp_high_count = 0

            if new_exp != current_exp:
                g.SetExposure(new_exp)
                current_exp = new_exp

            time.sleep(current_exp + 1.0)

        except Exception as e:
            context.log(f"Adaptive exposure error: {e}")
            time.sleep(2.0)

    print("Adaptive exposure thread ending.")

def _check_dome_safety_or_abort(context, where: str) -> bool:
    """
    Batch Runner unattended safety interlock.
    If dome shutter is CLOSED or ERROR twice in a row → abort batch.
    """
    dome = getattr(context, "dome", None)

    # Track consecutive unsafe shutter states on the context
    if not hasattr(context, "_shutter_unsafe_count"):
        context._shutter_unsafe_count = 0

    status = None
    try:
        if dome and getattr(dome, "Connected", False):
            status = getattr(dome, "ShutterStatus", None)
    except Exception:
        status = None

    unsafe = is_shutter_closed(dome)

    if unsafe:
        context._shutter_unsafe_count += 1
    else:
        context._shutter_unsafe_count = 0

    # Require two consecutive unsafe reads before aborting
    if context._shutter_unsafe_count >= 2:
    
        # --- Park telescope on confirmed shutter / weather safety abort ---
        if not hasattr(context, "_weather_park_done"):
            context._weather_park_done = False
    
        if not context._weather_park_done:
            try:
                from utils import park_pwi4
                context.log("🅿️ Weather safety stop — parking telescope via PWI4.")
                park_pwi4()
                context._weather_park_done = True
            except Exception as e:
                context.log(f"⚠️ Weather safety stop: PWI4 park failed: {e}")
    
        context.log(
            f"🚨 SAFETY STOP: Dome shutter unsafe "
            f"(ASCOM status={status}, {where}) — aborting batch."
        )
        context.stop_requested.set()
        return True
    return False

# ---------------------------------------------------------------------------
# Batch public entrypoint
# ---------------------------------------------------------------------------
def run_batch(context, rows):
    """Run multiple targets sequentially in a background thread."""
    def batch_worker():
        total_rows = len(rows)
        context.log(f"🚀 Batch run started ({total_rows} rows)")
        
        # Reset per-batch scheduler pre-calibration state
        context._scheduler_precal_done = False
        
        # Reset shutter safety debounce state
        context._shutter_unsafe_count = 0
        
        # Reset weather-abort park latch
        context._weather_park_done = False
    
        first_target_started = False
    
        # Parse rows once into internal blocks
        blocks = _parse_rows_to_blocks(context, rows)

        smart_mode = bool(getattr(context, "batch_smart_mode", False))
        ha_min = float(getattr(context, "batch_ha_min", -2.0))
        ha_max = float(getattr(context, "batch_ha_max", 1.0))

        try:
            if not blocks:
                context.log("⚠️ No valid rows to run.")
                return

            if smart_mode:
                latest_start_utc = _latest_science_start_utc(context)
            
                if latest_start_utc is None:
                    context.log(
                        "❌ Nautical dawn time could not be calculated — aborting batch for safety."
                    )
                    return
            
                context.log(
                    f"🌅 HA mode cutoff: will not START new targets after "
                    f"{latest_start_utc.strftime('%H:%M')} UTC (nautical twilight - 60m)."
                )

            idx = 0  # conventional-mode index
            
            # Scheduler start-time wait (HA mode)
            if smart_mode:
                start_utc = getattr(context, "batch_start_utc", None)
                if start_utc:
                    context.log(
                        f"⏳ Scheduler enabled — waiting until {start_utc.strftime('%H:%M')} UTC to begin."
                    )
            
                    # Park telescope while waiting for scheduler start time
                    try:
                        from utils import park_pwi4
                        context.log("🅿️ Parking telescope before scheduler start wait.")
                        park_pwi4()
                    except Exception as e:
                        context.log(f"⚠️ PWI4 Park failed before scheduler wait: {e}")
            
                    while datetime.now(timezone.utc) < start_utc:
                        if _check_dome_safety_or_abort(context, "scheduler start wait"):
                            return
                        if context.stop_requested.is_set():
                            context.log("🛑 Stop requested during scheduler wait — aborting batch.")
                            return
                        time.sleep(10)
            
                    context.log("▶️ Scheduler start time reached — entering HA loop.")
            
            while True:
                if _check_dome_safety_or_abort(context, "main batch loop"):
                    break
            
                if context.stop_requested.is_set():
                    context.log("🛑 Stop requested — aborting batch.")
                    break

                # --- HA mode: stop starting new targets after twilight cutoff ---
                if smart_mode:
                    latest_start_utc = _latest_science_start_utc(context)
                    if latest_start_utc:
                        now_utc = datetime.now(timezone.utc)
                        if now_utc >= latest_start_utc:
                            context.log(
                                "🌅 Nautical twilight guard reached — no new targets will be started."
                            )
                            break

                # Select next block
                block = None
                if not smart_mode:
                    block, idx = _next_conventional_block(blocks, idx)
                    if block is None:
                        break
                else:
                    block = _next_ha_block(context, blocks, ha_min, ha_max)
                    
                    # TERMINAL: no remaining targets can ever be eligible
                    if block is None:
                        break
                    
                    # NON-terminal control actions
                    if isinstance(block, dict):
                        if block.get("_action") == "STOP":
                            break
                        if block.get("_action") == "WAIT":
                            continue

                # If block is disabled or already complete, skip (should be filtered, but keep safe)
                if not block.get("enabled", True):
                    continue
                if block.get("completed", False):
                    continue

                target = block["name"]
                exp_s = block["exp_s"]
                frames = block["frames"]
                include_calibs = bool(block["calibrate"])
                start_time_ut = str(block.get("start_time", "")).strip()
                ref = block.get("reference")

                # Conventional-mode: optional per-target UT start time (kept as-is)
                if (not smart_mode) and start_time_ut:
                    if not _wait_until_target_start_utc(context, start_time_ut, target):
                        break  # stop requested during wait

                # Announce
                done_count = sum(1 for b in blocks if b.get("completed"))
                total_enabled = sum(1 for b in blocks if b.get("enabled"))
                context.log(f"🟢 [{done_count}/{total_enabled}] Starting target '{target}'")

                first_target_started = True

                # Science target
                science_ok = False
                try:
                    run_single_target(context, target, exp_s, frames, include_calibs)
                    if context.stop_requested.is_set():
                        context.log("🛑 Stop requested during science target — aborting batch.")
                        break
                    science_ok = True
                except Exception as e:
                    context.log(f"❌ Science target failed for '{target}': {e}")

                # Reference target (only if science succeeded)
                if science_ok and ref:
                    try:
                        run_reference_target(
                            context,
                            ref.get("name", ""),
                            ref.get("exp_s", ""),
                            ref.get("frames", ""),
                        )
                        if context.stop_requested.is_set():
                            context.log("🛑 Stop requested during reference — aborting batch.")
                            break
                    except Exception as e:
                        context.log(
                            f"⚠️ Reference target failed for '{target}' (ref '{ref.get('name','')}'): {e}"
                        )
                
                    # HA mode: stop immediately after final reference target
                    if smart_mode:
                        remaining = sum(
                            1 for b in blocks
                            if b.get("enabled", True) and not b.get("completed", False)
                        )
                        if remaining == 0:
                            context.log("✅ All enabled targets completed (HA mode) — ending batch.")
                            break
                
                # Mark complete
                block["completed"] = True
                context.log(f"✅ Target '{target}' complete.")
                context.log("")  # separator between target blocks

                # HA mode: stop when all enabled targets are complete
                if smart_mode:
                    remaining = sum(
                        1 for b in blocks
                        if b.get("enabled", True) and not b.get("completed", False)
                    )
                    if remaining == 0:
                        context.log("✅ All enabled targets completed (HA mode) — ending batch.")
                        break

                # Conventional-mode: optional park if there is a long idle gap before next start time
                if not smart_mode:
                    _maybe_park_for_idle_gap_before_next_scheduled(context, blocks, idx)

            # End while

        finally:
            # Shutdown always happens when loop ends (last target done OR stop requested OR HA cutoff)
            context.log("🎯 Batch run complete.")

            # No targets started -> do not run optional shutdown actions
            if not first_target_started:
                context.log("ℹ️ No science targets started — skipping shutdown actions.")
                _notify_gui_finished(context)
                return

            _run_optional_shutdown_actions(context)
            _notify_gui_finished(context)

    threading.Thread(target=batch_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Row parsing / selection
# ---------------------------------------------------------------------------
def _parse_rows_to_blocks(context, rows):
    blocks = []
    for i, row in enumerate(rows):
        try:
            name = str(row[0]).strip()
            exp_s = float(row[1])
            frames = int(row[2])

            calibrate = str(row[3]).strip() == "✓"
            enabled = not (len(row) >= 5 and str(row[4]).strip() == "✗")
            start_time = row[5] if len(row) >= 6 else ""

            ref_star = row[6] if len(row) > 6 else ""
            ref_exp = row[7] if len(row) > 7 else ""
            ref_frames = row[8] if len(row) > 8 else ""

            if not name:
                raise ValueError("blank target name")

        except Exception as e:
            context.log(f"⚠️ Invalid batch row {i}: {e}")
            continue

        blocks.append({
            "row_index": i,
            "name": name,
            "exp_s": exp_s,
            "frames": frames,
            "calibrate": calibrate,
            "enabled": enabled,
            "start_time": start_time,
            "completed": False,
            "ra_hours": None,
            "reference": (
                {
                    "name": str(ref_star).strip(),
                    "exp_s": float(ref_exp) if str(ref_exp).strip() else None,
                    "frames": int(ref_frames) if str(ref_frames).strip() else None,
                } if str(ref_star).strip() else None
            ),
        })

    # Clean up ref fields (if present but incomplete)
    for b in blocks:
        ref = b.get("reference")
        if ref:
            if not ref.get("name") or ref.get("exp_s") is None or ref.get("frames") is None:
                context.log(f"⚠️ Reference block incomplete for '{b['name']}' — ignoring reference target.")
                b["reference"] = None

    return blocks


def _next_conventional_block(blocks, idx):
    while idx < len(blocks):
        b = blocks[idx]
        idx += 1
        if not b.get("enabled", True):
            continue
        if b.get("completed", False):
            continue
        return b, idx
    return None, idx

def _will_target_enter_ha_window_before_cutoff(
    ra_hours: float,
    ha_min: float,
    latest_start_utc: datetime,
    now_utc: datetime
) -> bool:
    """
    True if target will cross HA=ha_min before latest_start_utc.
    """
    ha_now = hour_angle_hours(ra_hours, now_utc)

    # Already west → never eligible
    if ha_now > ha_min:
        return False

    # Hours until HA reaches ha_min
    delta_ha = ha_min - ha_now
    crossing_utc = now_utc + timedelta(hours=delta_ha)

    return crossing_utc < latest_start_utc

# ---------------------------------------------------------------------------
# Timed-target protection helpers
# ---------------------------------------------------------------------------

def _parse_start_time_utc(start_str: str, now_utc: datetime) -> datetime | None:
    """Return next occurrence of HH:MM UTC today, or None if invalid/blank."""
    if not start_str:
        return None
    try:
        hh, mm = map(int, str(start_str).strip().split(":"))
        dt = now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt <= now_utc:
            return None  # already passed → no longer a future appointment
        return dt
    except Exception:
        return None


def _estimate_block_duration_seconds(block: dict) -> float:
    """
    Estimate how long this block occupies the telescope.
    Conservative is good — we only need to avoid collisions.
    """
    exp = float(block.get("exp_s", 0))
    frames = int(block.get("frames", 0))

    science = exp * frames

    # calibration allowance (~15 min if enabled, else 0)
    cal = 900 if block.get("calibrate") else 0

    # reference target allowance (rough)
    ref = 0
    r = block.get("reference")
    if r:
        try:
            ref = float(r.get("exp_s", 0)) * int(r.get("frames", 0))
        except Exception:
            ref = 0

    overhead = 60  # guider settle / slew / sequencing buffer

    return science + cal + ref + overhead

def _next_ha_block(context, blocks, ha_min, ha_max):
    """
    HA mode selection:
    - Uses scheduler.choose_next_block_index() if available.
    - If no targets in HA window: park + wait 120s and re-evaluate.
    - If all enabled targets completed: return None (done).
    """
    # Build sched blocks for selection
    try:
        from scheduler import ObservingBlock, Target, choose_next_block_index
    except Exception as e:
        context.log(f"❌ HA mode requires scheduler.py, but import failed: {e}")
        return None

    now_utc = datetime.now(timezone.utc)
    
    # ----------------------------------------------------------
    # Timed-target pre-reservation guard
    # If a timed target is approaching soon, do NOT allow any
    # HA target to start — reserve the timeline.
    # ----------------------------------------------------------
    next_start_utc = None
    timed_block = None
    
    for b in blocks:
        if not b.get("enabled", True) or b.get("completed", False):
            continue
    
        st = _parse_start_time_utc(b.get("start_time", ""), now_utc)
        if st:
            if next_start_utc is None or st < next_start_utc:
                next_start_utc = st
                timed_block = b
    
    if next_start_utc:
        seconds_until = (next_start_utc - now_utc).total_seconds()
    
        # Conservative minimum block duration (~calibration + safety)
        MIN_BLOCK_SECONDS = 20 * 60
    
        if seconds_until <= MIN_BLOCK_SECONDS:
            context.log(
                f"⏰ Upcoming timed target '{timed_block['name']}' at "
                f"{next_start_utc.strftime('%H:%M')} UTC — reserving timeline"
            )
    
            try:
                from utils import park_pwi4
                park_pwi4()
                context.log("🅿️ Telescope parked while waiting for timed target window.")
            except Exception as e:
                context.log(f"⚠️ Park failed: {e}")
    
            for _ in range(12):
                if _check_dome_safety_or_abort(context, "timed reservation wait"):
                    return {"_action": "STOP"}
                if context.stop_requested.is_set():
                    return {"_action": "STOP"}
                time.sleep(10)
    
            return {"_action": "WAIT"}
    
    # ----------------------------------------------------------
    # Timed-target absolute override (ignore HA limits)
    # ----------------------------------------------------------
    due_target = None
    due_time = None
    
    for b in blocks:
        if not b.get("enabled", True) or b.get("completed", False):
            continue
    
        st_str = b.get("start_time", "")
        if st_str:
            try:
                hh, mm = map(int, str(st_str).strip().split(":"))
                st = now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)
        
                if st <= now_utc:
                    if due_time is None or st < due_time:
                        due_time = st
                        due_target = b
            except Exception:
                pass
    
    if due_target:
        context.log(
            f"⏰ Timed target due now — running '{due_target['name']}' (HA limits bypassed)"
        )
        return due_target
    
    sched_blocks = []
    block_map = {}
    
    closest = None  # (abs_distance_to_window, name, ha)
    
    # ----------------------------------------------------------
    # Timed-target protection: find next future appointment
    # ----------------------------------------------------------
    next_start_utc = None
    for tb in blocks:
        if not tb.get("enabled", True) or tb.get("completed", False):
            continue
        st = _parse_start_time_utc(tb.get("start_time", ""), now_utc)
        if st:
            if next_start_utc is None or st < next_start_utc:
                next_start_utc = st
    
    remaining = 0
    for b in blocks:
        if not b.get("enabled", True):
            continue
        if b.get("completed", False):
            continue
        remaining += 1

        if b["ra_hours"] is None:
            try:
                ra_h, _dec = resolve_target_simbad(b["name"])
                b["ra_hours"] = float(ra_h)
            except Exception as e:
                context.log(f"⚠️ HA skip: could not resolve '{b['name']}': {e}")
                b["completed"] = True
                continue

        ha = hour_angle_hours(b["ra_hours"], now_utc)

        if ha < ha_min:
            dist = ha_min - ha
        elif ha > ha_max:
            dist = ha - ha_max
        else:
            dist = 0.0

        if closest is None or dist < closest[0]:
            closest = (dist, b["name"], ha)

        sb = ObservingBlock(
            science=Target(
                name=b["name"],
                ra_hours=b["ra_hours"],
                dec_deg=0.0,
                exp_s=float(b["exp_s"]),
                frames=int(b["frames"]),
                calibrate=bool(b["calibrate"]),
            ),
            completed=b.get("completed", False),
        )
        sched_blocks.append(sb)
        block_map[id(sb)] = b

    if remaining == 0:
        return None  # done

    chosen_idx = choose_next_block_index(
        sched_blocks,
        when_utc=now_utc,
        ha_min=ha_min,
        ha_max=ha_max,
    )

    if chosen_idx is None:
        # Determine whether ANY remaining target east of HA_min
        # can still enter the HA window before the twilight cutoff
        latest_start_utc = _latest_science_start_utc(context)
        now_utc = datetime.now(timezone.utc)
    
        future_eligible = False
    
        if latest_start_utc:
            for b in blocks:
                if not b.get("enabled", True) or b.get("completed", False):
                    continue
                if b["ra_hours"] is None:
                    continue
    
                if _will_target_enter_ha_window_before_cutoff(
                    b["ra_hours"], ha_min, latest_start_utc, now_utc
                ):
                    future_eligible = True
                    break
    
        if not future_eligible:
            context.log("🌙 No remaining targets can enter HA window tonight — ending batch.")
            return None  # TERMINAL → triggers shutdown
    
        # Otherwise: temporarily ineligible → wait
        if closest:
            _dist, _name, _ha = closest
            context.log(f"HA closest: {_name} = {_ha:.2f} h (window {ha_min:.2f} → {ha_max:.2f})")
    
        context.log("⏳ No targets within HA window — parking + waiting 120s then re-evaluating…")
    
        if not context.stop_requested.is_set():
            try:
                from utils import park_pwi4
                park_pwi4()
                context.log("🅿️ Telescope parked via PWI4 (HA wait idle).")
            except Exception as e:
                context.log(f"⚠️ PWI4 Park failed during HA wait: {e}")
    
        for _ in range(12):
            if _check_dome_safety_or_abort(context, "HA wait"):
                return {"_action": "STOP"}
            if context.stop_requested.is_set():
                context.log("🛑 Stop requested during HA wait — aborting batch.")
                return {"_action": "STOP"}
            time.sleep(10)
    
        return {"_action": "WAIT"}

    sb = sched_blocks[chosen_idx]
    candidate = block_map[id(sb)]
    
    # ----------------------------------------------------------
    # Timed-target collision protection
    # ----------------------------------------------------------
    if next_start_utc:
        duration = _estimate_block_duration_seconds(candidate)
        finish_time = now_utc + timedelta(seconds=duration)
    
        if finish_time >= next_start_utc:
            context.log(
                f"⏰ Skipping '{candidate['name']}' — would overlap timed target at "
                f"{next_start_utc.strftime('%H:%M')} UTC"
            )
    
            context.log("🅿️ Parking and waiting for timed target window…")
    
            try:
                from utils import park_pwi4
                park_pwi4()
            except Exception as e:
                context.log(f"⚠️ Park failed: {e}")
    
            for _ in range(12):
                if _check_dome_safety_or_abort(context, "timed wait"):
                    return {"_action": "STOP"}
                if context.stop_requested.is_set():
                    return {"_action": "STOP"}
                time.sleep(10)
    
            return {"_action": "WAIT"}
    
    return candidate


def _wait_until_target_start_utc(context, hhmm_ut, target_name):
    """
    Conventional mode: wait until the per-row UT time.
    Returns False if stop is requested during waiting.
    """
    try:
        hh, mm = map(int, hhmm_ut.split(":"))
        now_utc = datetime.now(timezone.utc)
        scheduled = now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)

        if scheduled <= now_utc:
            context.log(f"⏩ Start time {hhmm_ut} UT already passed — running '{target_name}' now.")
            return True

        context.log(f"⏳ Waiting until {hhmm_ut} UT before starting '{target_name}'...")
        
        # Park telescope while waiting for conventional start time
        try:
            from utils import park_pwi4
            context.log("🅿️ Parking telescope before conventional start wait.")
            park_pwi4()
        except Exception as e:
            context.log(f"⚠️ PWI4 Park failed before conventional start wait: {e}")
        
        while datetime.now(timezone.utc) < scheduled:

            if _check_dome_safety_or_abort(context, "conventional start-time wait"):
                return False

            if context.stop_requested.is_set():
                context.log("🛑 Stop requested during schedule wait.")
                return False

            time.sleep(10)

        context.log(f"▶️ Start time reached ({hhmm_ut} UT) — beginning '{target_name}'.")
        return True

    except Exception as e:
        context.log(f"⚠️ Invalid start time '{hhmm_ut}': {e} — running immediately.")
        return True

def _maybe_park_for_idle_gap_before_next_scheduled(context, blocks, next_idx):
    """
    Conventional mode: if the *next enabled, not-completed* block has a UT start time
    far enough away, park during the idle period.
    """
    # Find next runnable block
    j = next_idx
    next_block = None
    while j < len(blocks):
        b = blocks[j]
        if b.get("enabled", True) and not b.get("completed", False):
            next_block = b
            break
        j += 1

    if not next_block:
        return

    next_start = str(next_block.get("start_time", "")).strip()
    if not next_start:
        return

    try:
        nh, nm = map(int, next_start.split(":"))
        now_utc = datetime.now(timezone.utc)
        scheduled = now_utc.replace(hour=nh, minute=nm, second=0, microsecond=0)
        idle_seconds = (scheduled - now_utc).total_seconds()

        if idle_seconds <= 120:
            return

        context.log(f"⏳ Idle gap before next target is {idle_seconds/60:.1f} min — parking telescope…")

        try:
            from utils import park_pwi4
            park_pwi4()
            context.log("🅿️ Telescope parked via PWI4 (idle gap before next target).")
        except Exception as e:
            context.log(f"⚠️ PWI4 Park failed: {e}")

        while datetime.now(timezone.utc) < scheduled:
            if _check_dome_safety_or_abort(context, "conventional start-time wait"):
                return False
            if context.stop_requested.is_set():
                context.log("🛑 Stop requested during schedule wait.")
                return False
            time.sleep(10)

        context.log(f"⏩ Start time reached ({next_start} UT). Continuing to next target.")

    except Exception as e:
        context.log(f"⚠️ Could not evaluate next start time '{next_start}': {e}")


# ---------------------------------------------------------------------------
# Shutdown + GUI cleanup
# ---------------------------------------------------------------------------
def _notify_gui_finished(context):
    bt = getattr(context, "batch_tab", None)
    if bt and hasattr(bt, "on_batch_finished"):
        try:
            bt.on_batch_finished()
        except Exception:
            pass


def _run_optional_shutdown_actions(context):
    bt = getattr(context, "batch_tab", None)

    # Park telescope
    try:
        if bt and hasattr(bt, "park_var") and bt.park_var.get():
            context.log("User selected: Park Telescope after batch.")
            try:
                from utils import park_pwi4
                park_pwi4()
                context.log("🅿️ Telescope parked via PWI4.")
            except Exception as e:
                context.log(f"⚠️ PWI4 Park failed: {e}")
    except Exception as e:
        context.log(f"❌ Telescope park failed: {e}")

    # Close dome shutter
    try:
        if bt and hasattr(bt, "dome_var") and bt.dome_var.get():
            context.log("User selected: Close Dome after batch.")
            dome = getattr(context, "dome", None)
            if dome and hasattr(dome, "CloseShutter"):
                dome.CloseShutter()
                context.log("🛑 Dome shutter close command issued.")
            else:
                context.log("⚠️ Dome not connected or CloseShutter() unavailable.")
    except Exception as e:
        context.log(f"❌ Dome close failed: {e}")

    # Run calibration after batch (optional)
    try:
        if bt and hasattr(bt, "shutdown_calib_var") and bt.shutdown_calib_var.get():
            seq = getattr(context, "sequencer_tab", None)
            if seq:
                context.log("🧪 Running calibration after batch...")
                seq.run_calibration()
                while seq.sequence_running:
                    if context.stop_requested.is_set():
                        context.log("🛑 Stop requested — aborting shutdown calibration.")
                        break
                    time.sleep(2)
                context.log("✅ Shutdown calibration complete.")
            else:
                context.log("⚠️ Sequencer not available — skipping shutdown calibration.")
    except Exception as e:
        context.log(f"❌ Shutdown calibration failed: {e}")
            
    # Warm camera
    try:
        if bt and hasattr(bt, "warm_var") and bt.warm_var.get():
            context.log("User selected: Warm camera to +5°C after batch.")
            cam = getattr(context, "camera", None)
            if cam and hasattr(cam, "SetCCDTemperature"):
                try:
                    cam.SetCooler(True)
                except Exception:
                    pass
                cam.SetCCDTemperature = 5.0
                context.log("🌡️ Camera warming: Setpoint = +5°C")
            else:
                context.log("⚠️ Camera not connected or temperature control unavailable.")
    except Exception as e:
        context.log(f"❌ Camera warm-up failed: {e}")


# ---------------------------------------------------------------------------
# Reference target helper
# ---------------------------------------------------------------------------
def run_reference_target(context, ref_name, exp_s, frames):
    context.log(f"🔁 Running reference target: {ref_name}")
    run_single_target(
        context,
        target_name=ref_name,
        exp_s=exp_s,
        frames=frames,
        include_calibs=False,  # never run calibs for reference
    )

# ---------------------------------------------------------------------------
# Core execution: one target (science or reference)
# ---------------------------------------------------------------------------
def run_single_target(context, target_name, exp_s, frames, include_calibs=True):
    # STOP is owned by the batch controller, not per-target execution.
    # Adaptive exposure is strictly per-target
    context.adaptive_stop = threading.Event()
    
    if _check_dome_safety_or_abort(context, "before target start"):
        return

    # Dual-range exposure flag
    context.on_fibre = False

    try:
        seq = getattr(context, "sequencer_tab", None)
        guide = getattr(context, "guider", None)

        if not seq:
            context.log("Sequencer tab not available — aborting.")
            return

        # --------------------------------------------------
        # Inject target parameters into Sequencer FIRST
        # --------------------------------------------------
        _inject_target_into_sequencer(context, seq, target_name, exp_s, frames)

        # --------------------------------------------------
        # Slew telescope (via PWI4 HTTP)
        # --------------------------------------------------
        try:
            from utils import slew_target
            context.log(f"Initiating PWI4 slew for target: {target_name}")
            ok = slew_target(target_name, apply_pm=True, log_fn=context.log)
            if not ok:
                context.log(f"⚠️ Slew to {target_name} failed or skipped.")
        except Exception as e:
            context.log(f"❌ Slew failed for {target_name}: {e}")

        # --------------------------------------------------
        # Wait for telescope & dome settle
        # --------------------------------------------------
        _wait_for_settle(context)
        
        if context.stop_requested.is_set():
            context.log("🛑 Stop flag detected — aborting before calibration.")
            return
        
        # Scheduler mode: one-time calibration before first science target
        bt = getattr(context, "batch_tab", None)
        
        if (
            getattr(context, "batch_smart_mode", False)
            and bt
            and hasattr(bt, "pre_calib_var")
            and bt.pre_calib_var.get()
            and not getattr(context, "_scheduler_precal_done", False)
        ):
            context.log("🧪 Scheduler mode: running one-time calibration for first target.")
            try:
                seq.run_calibration()
                while seq.sequence_running:
                    if context.stop_requested.is_set():
                        context.log("🛑 Stop requested — aborting scheduler pre-calibration.")
                        return
                    time.sleep(2)
                context.log("✅ Scheduler pre-first-target calibration complete.")
                context._scheduler_precal_done = True
            except Exception as e:
                context.log(f"❌ Scheduler pre-calibration failed — aborting batch: {e}")
                return
        
        # --------------------------------------------------
        # Calibration block (first, if enabled)
        # --------------------------------------------------
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

        if context.stop_requested.is_set():
            context.log("🛑 Stop flag detected — aborting before guider phase.")
            return

        # --------------------------------------------------
        # PHD2 guiding, ROI star find, fibre settle, adaptive
        # --------------------------------------------------
        guiding_ok = True
        if guide:
            # --- NEW: disk guide-log header per target ---
            context.guide_log("")
            context.guide_log(f"===== GUIDE START: {target_name} =====")
            context.guide_log("")
        
            guiding_ok = _run_guiding_phase(context, guide)
        else:
            context.guide_log("No guider available — skipping PHD2 actions.")
            guiding_ok = False
        
        if not guiding_ok:
            context.log(f"❌ Guiding failed for {target_name} — skipping science imaging.")
            try:
                stop_guiding(context)
                context.guide_log("❌ Guiding session ended (failure).")
                context.guide_log("")
                context.flush_guide_log()
            except Exception:
                pass
            return
        
        # --------------------------------------------------
        # (Re)inject target parameters into Sequencer (kept)
        # --------------------------------------------------
        _inject_target_into_sequencer(context, seq, target_name, exp_s, frames)
        
        # --------------------------------------------------
        # Run target sequence
        # --------------------------------------------------
        if context.stop_requested.is_set():
            context.log("🛑 Stop flag detected — aborting before target imaging.")
            return
        
        context.log(f"Running target sequence for {target_name} ...")
        try:
            seq.run_target()
            while seq.sequence_running:
                if context.stop_requested.is_set():
                    context.log("🛑 Stop requested — waiting for sequencer to halt...")
                time.sleep(5)
        except Exception as e:
            context.log(f"Target run failed: {e}")

        # --------------------------------------------------
        # Wrap up
        # --------------------------------------------------
        if guide:
            try:
                stop_guiding(context)
                context.guide_log("PHD2 guiding session ended.")
                context.guide_log("")  # separator between targets in Guide log
                context.flush_guide_log()
            except Exception as e:
                context.log(f"Could not stop guiding: {e}")

        #context.log(f"Auto Capture complete for {target_name}.")

    finally:
        try:
            if hasattr(context, "adaptive_stop"):
                context.adaptive_stop.set()
                print("Adaptive exposure thread stop signal sent.")
        except Exception as e:
            context.guide_log(f"Could not signal adaptive stop: {e}")


def _inject_target_into_sequencer(context, seq, target_name, exp_s, frames):
    try:
        if hasattr(seq, "target_entry"):
            seq.target_entry.delete(0, "end")
            seq.target_entry.insert(0, target_name)
        if hasattr(seq, "targ_exp"):
            seq.targ_exp.delete(0, "end")
            seq.targ_exp.insert(0, str(exp_s))
        if hasattr(seq, "targ_count"):
            seq.targ_count.delete(0, "end")
            seq.targ_count.insert(0, str(frames))
        seq.update_idletasks()
        context.log(f"Sequencer updated: {target_name}, {exp_s}s × {frames} frames.")
    except Exception as e:
        context.log(f"⚠️ Could not inject target parameters into Sequencer: {e}")

def _wait_for_settle(context):
    try:
        context.log("Waiting for telescope and dome to settle before guiding...")
        time.sleep(2)
        consecutive_clear = 0
        start_time = time.time()

        while time.time() - start_time < 180:

            if _check_dome_safety_or_abort(context, "settle wait"):
                return

            tel_slewing = bool(getattr(getattr(context, "telescope", None), "Slewing", False))
            dome_slewing = bool(getattr(getattr(context, "dome", None), "Slewing", False))

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

            if moving:
                context.log(f"⏳ Still moving: {', '.join(moving)}")
            time.sleep(2)

        time.sleep(2)
        context.log("✅ Telescope and dome settled.")
    except Exception as e:
        context.log(f"⚠️ Settle wait check failed: {e}")

def _run_guiding_phase(context, guide):
    try:
        if not ensure_connection(context):
            context.guide_log("PHD2 not reachable — skipping guiding.")
            return False

        g = guide.phd

        # Dome stationary check (kept)
        dome = getattr(context, "dome", None)
        if dome and getattr(dome, "Connected", False):
            context.log("Checking dome state before starting guider...")
            start_time = time.time()
            while getattr(dome, "Slewing", False) and time.time() - start_time < 120:
                if context.stop_requested.is_set():
                    return False
                time.sleep(2)
            if getattr(dome, "Slewing", False):
                context.log("⚠️ Dome still reporting Slewing — continuing anyway.")
            else:
                context.log("✅ Dome is stationary — safe to start guider.")
        else:
            context.log("No dome connected — proceeding with guider startup.")

        # Start looping
        context.guide_log("Setting guider exposure to 0.01 s before starting loop...")
        g.SetExposure(0.01)
        time.sleep(2)

        context.guide_log("Starting looping exposures...")
        g.Loop()
        time.sleep(5.0)

        # ROI smart find
        if not _roi_star_acquire(context, g):
            return False

        if context.stop_requested.is_set():
            context.guide_log("🛑 Stop flag detected — aborting before guiding.")
            return False

        g.Guide(settlePixels=1.5, settleTime=2.0, settleTimeout=10.0)
        context.guide_log("Guiding started.")

        threading.Thread(target=adaptive_exposure_loop, args=(g, context), daemon=True).start()
        context.guide_log("Waiting 5 s for guider to stabilise before fibre lock restore...")
        time.sleep(5)

        if hasattr(guide, "restore_lock"):
            context.guide_log("Restoring fibre lock position via GuideTab method...")
            guide.restore_lock()
            context.guide_log("Fibre lock restore command issued successfully.")
            time.sleep(7)

        # Fibre settle verification (NOW RETURNS BOOL)
        return _fibre_settle(context, g)

    except Exception as e:
        context.guide_log(f"Guider phase failed: {e}")
        return False


def _roi_star_acquire(context, g):
    context.guide_log("Attempting smart ROI star acquisition (multi-exposure test)...")

    cx = getattr(context, "roi_centre_x", 0)
    cy = getattr(context, "roi_centre_y", 0)
    size = getattr(context, "roi_size", 0)

    if not (cx and cy and size):
        context.guide_log("⚠️ ROI not configured (centre/size missing or zero) — skipping ROI star search.")
        try:
            g.StopCapture()
        except Exception:
            pass
        return False

    try:
        size_int = int(size)
        x0 = int(cx - size_int / 2)
        y0 = int(cy - size_int / 2)
        roi_box = [x0, y0, size_int, size_int]
    except Exception as e:
        context.guide_log(f"⚠️ Invalid ROI configuration ({cx}, {cy}, {size}): {e}")
        try:
            g.StopCapture()
        except Exception:
            pass
        return False

    roi_found = False
    trial_exposures = [0.01, 0.1, 0.5, 1.0, 3.0, 5.0, 6.0, 7.0, 8.0]

    for trial in trial_exposures:
        if context.stop_requested.is_set():
            context.guide_log("🛑 Stop flag detected — aborting ROI star search early.")
            return False

        try:
            g.SetExposure(trial)
            context.guide_log(f"Trying exposure {trial:.2f}s in ROI {roi_box}...")
            
            # Allow at least one real frame at this exposure before FindStar
            time.sleep(max(2.0, trial + 0.5))
            
            g.FindStar(roi_box)
            context.guide_log("ROI-based star search issued.")
            time.sleep(2.0)

            if g.AppState in ("Selected", "Looping", "Guiding"):
                context.guide_log(f"✅ Star found in ROI at {trial:.2f}s — continuing.")
                roi_found = True

                # ROI auto-average sample (kept)
                try:
                    pos = g.GetLockPosition()
                    if isinstance(pos, (list, tuple)) and len(pos) == 2:
                        context.record_roi_hit(pos[0], pos[1])
                        if getattr(context, "setup", None):
                            try:
                                context.setup.update_roi_average_label()
                            except Exception:
                                pass
                except Exception as e:
                    context.guide_log(f"ROI sample record failed: {e}")

                break
            else:
                context.guide_log(f"❌ No star found at {trial:.2f}s — trying next.")

        except Exception as e:
            context.guide_log(f"⚠️ Trial {trial:.2f}s failed: {e}")

    if not roi_found:
        context.guide_log("❌ Could not find any star in ROI after all trials.")
        try:
            g.StopCapture()
        except Exception:
            pass
        return False

    context.guide_log("✅ ROI star acquisition successful — proceeding to guiding.")
    return True


def _fibre_settle(context, g):
    try:
        guider_exp = getattr(g, "GetExposure", lambda: 1000)() / 1000.0
        tolerance_near = 10

        # Hard timeout for reaching fibre region (scaled by guider exposure)
        timeout_near = max(120, guider_exp * 18)  # scales with exposure; avoids hangs
        t0 = time.time()

        context.guide_log(f"Waiting for star to reach fibre region (≤{tolerance_near}px)...")
        while True:
            if context.stop_requested.is_set():
                return False

            if time.time() - t0 > timeout_near:
                context.guide_log("❌ Timed out waiting for star to reach fibre region — guiding failed.")
                return False

            try:
                _state, dist = g.GetStatus()
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
                return False

            try:
                _state, dist = g.GetStatus()
            except Exception:
                dist = None

            if dist is None:
                time.sleep(guider_exp + 1)
                continue

            if dist <= tolerance_stable:
                stable_reads += 1
                if stable_reads >= required_stable:
                    context.guide_log("✅ Guider stable on fibre (within fine tolerance).")
                    context.on_fibre = True
                    context.guide_log("🔒 Switching adaptive exposure to FIBRE mode (LOCK_MIN/LOCK_MAX).")
                    return True
            else:
                stable_reads = 0

            time.sleep(guider_exp + 1)

        context.guide_log("⚠️ Fibre-settle timeout — treating as guiding failure (skip imaging).")
        return False

    except Exception as e:
        context.guide_log(f"Warning: fibre-settle verification failed: {e}")
        return False