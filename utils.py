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
utils.py
----------
Utility functions and constants for the Spectro Capture application.

Provides shared helper routines used across multiple modules, including site
constants, coordinate conversions, and file management utilities. Designed to
keep non-GUI logic centralized and easily maintainable.

Key features:
- Observatory site constants and coordinate utilities
- J2000 → JNow precession and RA/Dec formatting helpers
- Nightly and run folder management routines
- File copy and calibration frame utilities
- Shared functions for FITS header generation and timekeeping
"""

import os
import shutil
import datetime
import subprocess
import requests
from astroquery.simbad import Simbad
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, CIRS
from astropy.time import Time
import astropy.units as u

# ----------------------------
# Target name sanitiser
# ----------------------------
def make_file_safe(name: str) -> str:
    """
    Convert a target name into a filesystem-safe version.
    - Removes spaces
    - Removes unsafe punctuation
    This is used ONLY for folder/file naming; SIMBAD uses the original name.
    """
    # Remove spaces
    s = name.replace(" ", "")
    # Keep only allowed characters
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    return "".join(ch for ch in s if ch in allowed)

# ----------------------------
# Config / Paths
# ----------------------------
ROOT_PATH = r"C:\Users\Luckas Observatory\OneDrive\astronomy\Spectroscopy"
LAMP_EXE  = "lamp_control.exe"   # must be on PATH or alongside exe

# --- NEW: allow AppContext to override ROOT_PATH dynamically -------------
def set_root_path_override(new_path: str):
    """Update the global ROOT_PATH used by nightly_folder and run creation."""
    global ROOT_PATH
    try:
        ROOT_PATH = new_path
    except Exception:
        pass

# ----------------------------
# Observatory & instrument constants
# ----------------------------
SITE_INFO = {
    "OBSERVER": "Paul Luckas",
    "TELESCOP": "RC14C",
    "FOCALLEN": 2587.0,       # mm
    "APTDIA": 355.0,          # mm
    "APTAREA": 95020.6,       # mm^2
    "SITELAT": "-31 57 33",   # DMS string
    "SITELONG": "115 48 54",  # DMS string
    "XPIXSZ": 3.76,           # µm
    "YPIXSZ": 3.76,           # µm
}

# ----------------------------
# Nightly/run folder helpers
# ----------------------------
def nightly_folder(target: str) -> str:
    """
    Return a nightly folder based on *astronomical night*.
    Night is considered to begin at local noon (12:00).
    Before noon → treat as previous night's session.
    """
    safe = make_file_safe(target)

    now = datetime.datetime.now()
    # If before local noon, use yesterday's date
    if now.hour < 12:
        night_date = (now - datetime.timedelta(days=1)).date()
    else:
        night_date = now.date()

    year = str(night_date.year)
    datecode = night_date.strftime("%y%m%d")

    base = os.path.join(ROOT_PATH, year, safe, datecode)
    os.makedirs(base, exist_ok=True)
    return base

def observing_night_date_str():
    """
    Return observing-night date as YYYY-MM-DD
    using the same noon rollover as nightly_folder().
    """
    now = datetime.datetime.now()
    if now.hour < 12:
        night_date = (now - datetime.timedelta(days=1)).date()
    else:
        night_date = now.date()

    return night_date.strftime("%Y-%m-%d")

def next_run_folder(base_path: str, target: str, is_calibration: bool) -> str:
    safe = make_file_safe(target)
    run_prefix = f"{safe}_calib_run" if is_calibration else f"{safe}_run"

    subfolders = [
        d for d in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, d)) and d.startswith(run_prefix)
    ]
    if subfolders:
        run_idx = 2
        while True:
            candidate = os.path.join(base_path, f"{run_prefix}{run_idx}")
            if not os.path.exists(candidate):
                os.makedirs(candidate, exist_ok=True)
                return candidate
            run_idx += 1

    # Calibration: detect tungsten/thorium from SAFE names
    if is_calibration:
        existing_files = [
            f for f in os.listdir(base_path)
            if (f.startswith(f"{safe}_tung") or f.startswith(f"{safe}_thor"))
        ]
    else:
        # Science frames
        existing_files = [
            f for f in os.listdir(base_path)
            if f.startswith(safe)
            and not (f.startswith(f"{safe}_tung") or f.startswith(f"{safe}_thor"))
        ]

    if existing_files:
        run2 = os.path.join(base_path, f"{run_prefix}2")
        os.makedirs(run2, exist_ok=True)
        return run2

    return base_path

def find_latest_calib():
    year_path = os.path.join(ROOT_PATH, str(datetime.date.today().year))
    latest, latest_time = None, None
    if not os.path.exists(year_path):
        return None
    for target in os.listdir(year_path):
        tpath = os.path.join(year_path, target)
        if not os.path.isdir(tpath):
            continue
        for nightly in os.listdir(tpath):
            npath = os.path.join(tpath, nightly)
            calib_path = os.path.join(npath, "calib")
            if os.path.isdir(calib_path):
                mtime = os.path.getmtime(calib_path)
                if latest_time is None or mtime > latest_time:
                    latest_time, latest = mtime, calib_path
    return latest


def copy_latest_calib_to(nightly_path: str, log_fn=print):
    dest = os.path.join(nightly_path, "calib")
    if os.path.exists(dest):
        return
    latest = find_latest_calib()
    if latest:
        try:
            shutil.copytree(latest, dest)
            log_fn(f"Copied calib from {latest} → {dest}")
        except Exception as e:
            log_fn(f"Failed to copy calib: {e}")
    else:
        log_fn("No 'calib' folder found to copy.")


# ------------------------------------------------------------
# Local HH:MM → UTC HH:MM converter
# ------------------------------------------------------------
def local_hhmm_to_utc_hhmm(local_str: str) -> str:
    """
    Convert a local-time HH:MM string into a UTC HH:MM string.
    Returns "" on invalid input.

    Example:
        "01:30" local (UTC+8) → "17:30" UT (previous day)
    """
    try:
        from datetime import datetime, timezone

        # Parse HH:MM
        hh, mm = map(int, local_str.split(":"))

        # Construct local datetime for today
        now_local = datetime.now()
        dt_local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)

        # Convert to UTC
        dt_utc = dt_local.astimezone(timezone.utc)

        # Return HH:MM
        return dt_utc.strftime("%H:%M")

    except Exception:
        return ""

# ------------------------------------------------------------
# UTC HH:MM → Local HH:MM converter
# ------------------------------------------------------------
def utc_hhmm_to_local_hhmm(utc_str: str) -> str:
    """
    Convert a UTC HH:MM string into a local-time HH:MM string.
    Returns "" on invalid input.
    """
    try:
        from datetime import datetime, timezone

        hh, mm = map(int, utc_str.split(":"))

        # Construct UTC datetime for today
        now_utc = datetime.now(timezone.utc)
        dt_utc = now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)

        # Convert to local time
        dt_local = dt_utc.astimezone()

        return dt_local.strftime("%H:%M")

    except Exception:
        return ""

# ----------------------------
# Lamps
# ----------------------------
def lamp_on(which: str):
    if which.lower() in ("tungsten", "thorium"):
        subprocess.run([LAMP_EXE, which], check=False)


def lamp_off():
    subprocess.run([LAMP_EXE, "off"], check=False)


# ----------------------------
# Name resolver (Sesame → SIMBAD)
# ----------------------------
def resolve_target_simbad(target: str):
    """
    Resolve an object name to (RA_hours, Dec_degs).
    1) Try Astropy's Sesame resolver (CDS), which returns a SkyCoord directly.
    2) If that fails and astroquery is available, try Simbad().query_object.
    Raises ValueError on failure.
    """
    try:
        c = SkyCoord.from_name(target)
        return c.ra.hour, c.dec.degree
    except Exception as e1:
        last_err = e1

    try:
        sim = Simbad()
        sim.TIMEOUT = 30
        res = sim.query_object(target)
        if res is None or len(res) == 0:
            raise ValueError(f"Target '{target}' not found in SIMBAD")
        ra_col = 'RA' if 'RA' in res.colnames else 'ra'
        dec_col = 'DEC' if 'DEC' in res.colnames else 'dec'
        ra_str = res[ra_col][0]
        dec_str = res[dec_col][0]
        c = SkyCoord(str(ra_str), str(dec_str), unit=(u.hourangle, u.deg))
        return c.ra.hour, c.dec.degree
    except Exception as e2:
        raise ValueError(f"SIMBAD resolution failed: {e1}, {e2}")

# ----------------------------
# Thread-safe logging helper
# ----------------------------
class SafeLogMixin:
    """Provides a thread-safe logging helper usable by any Tkinter tab."""
    def safe_log(self, msg):
        """Safely queue log messages onto the Tkinter main thread."""
        try:
            # Schedule on main thread if 'after' is available (Tkinter widget context)
            self.after(0, lambda: self.context.log(msg))
        except Exception as e:
            # Fallback so logging errors never crash the app
            print(f"[SafeLogMixin] Logging failed: {e}")

# ----------------------------
# Sidereal time & Hour Angle helpers
# ----------------------------
def lst_hours_utc(when_utc: datetime.datetime) -> float:
    """
    Return Local Sidereal Time (hours) at observatory location for a UTC datetime.
    """
    if when_utc.tzinfo is None:
        raise ValueError("when_utc must be timezone-aware (UTC).")

    # Observatory location (use SITE_INFO)
    lat = SITE_INFO["SITELAT"]
    lon = SITE_INFO["SITELONG"]

    location = EarthLocation(
        lat=lat,
        lon=lon
    )

    t = Time(when_utc)
    lst = t.sidereal_time("apparent", longitude=location.lon)
    return lst.hour


def _wrap_hours_pm12(x: float) -> float:
    """Wrap hours into (-12, +12]."""
    while x <= -12.0:
        x += 24.0
    while x > 12.0:
        x -= 24.0
    return x


def hour_angle_hours(ra_hours: float, when_utc: datetime.datetime) -> float:
    """
    Compute hour angle in hours: HA = LST - RA
    Returned in range (-12, +12].
    """
    lst = lst_hours_utc(when_utc)
    return _wrap_hours_pm12(lst - ra_hours)

# ----------------------------
# Coordinate conversion helpers
# ----------------------------
def j2000_to_current(ra_hours: float, dec_degs: float):
    """Convert J2000 coordinates to JNow (apparent of date)."""
    try:
        location = EarthLocation(lat=-32.0 * u.deg, lon=116.0 * u.deg)
        c = SkyCoord(ra=ra_hours * u.hour, dec=dec_degs * u.deg,
                     frame='fk5', equinox='J2000')
        now = Time.now()
        c_cirs = c.transform_to(CIRS(obstime=now, location=location))
        return c_cirs.ra.hour, c_cirs.dec.degree
    except Exception:
        return ra_hours, dec_degs


# --- PWI4 Mount & SIMBAD Utilities (J2000 + Proper Motion) ---------------------
PWI4_HOST = "localhost"
PWI4_PORT = 8220
BASE_URL = f"http://{PWI4_HOST}:{PWI4_PORT}"


def pwi4_request(path, **params):
    """Generic HTTP GET request to PWI4."""
    url = f"{BASE_URL}{path}"
    r = requests.get(url, params=params, timeout=5)
    r.raise_for_status()
    return r.text


def connect_mount():
    print("Connecting to PWI4 mount ...")
    pwi4_request("/mount/connect")
    print("✅ Mount connected.")


def disconnect_mount():
    print("Disconnecting mount ...")
    try:
        pwi4_request("/mount/disconnect")
        print("✅ Mount disconnected.")
    except Exception as e:
        print(f"⚠️  Disconnect failed: {e}")


def get_status():
    """Retrieve the current PWI4 status as a dictionary."""
    r = requests.get(f"{BASE_URL}/status", timeout=5)
    r.raise_for_status()
    lines = r.text.splitlines()
    status = {}
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            status[k.strip()] = v.strip()
    return status


def is_slewing():
    """Check if the mount is currently slewing."""
    try:
        s = get_status()
        return s.get("mount.is_slewing", "false").lower() == "true"
    except Exception:
        return False


def slew_pwi4(ra_hours, dec_degs, target_name):
    """Command the PWI4 mount to slew to given J2000 coordinates."""
    import time
    print(f"Slewing to {target_name} (J2000):")
    print(f"  → RA  = {ra_hours:.6f} h")
    print(f"  → Dec = {dec_degs:.6f} °")
    pwi4_request("/mount/goto_ra_dec_j2000", ra_hours=ra_hours, dec_degs=dec_degs)
    while is_slewing():
        print("  ... slewing ...")
        time.sleep(2)
    print("✅ Slew complete.")


# ---------------------------------------------------------------------------
# NEW: PWI4 Park Function (HTTP-based)
# ---------------------------------------------------------------------------
def park_pwi4():
    """
    Park the mount using PWI4's HTTP API.

    This replaces all ASCOM-based Park calls and ensures a unified
    behaviour across Batch Runner, Auto Capture, and shutdown sequences.
    """
    import time
    try:
        print("Parking telescope (PWI4 HTTP)...")
        pwi4_request("/mount/park")

        # Wait until mount is no longer slewing
        while is_slewing():
            print("  ... parking (slewing) ...")
            time.sleep(2)

        print("✅ Mount parked via PWI4.")

    except Exception as e:
        print(f"⚠️  PWI4 Park failed: {e}")
        raise
# ---------------------------------------------------------------------------


def resolve_target_simbad_pm(target_name: str):
    """Resolve via SIMBAD, retrieve pmRA/pmDec, and apply proper motion."""
    print(f"Resolving '{target_name}' via SIMBAD (with proper motion)...")

    Simbad.add_votable_fields('pmra', 'pmdec')
    result = Simbad.query_object(target_name)
    if result is None or len(result) == 0:
        raise ValueError(f"Target '{target_name}' not found in SIMBAD.")

    pm_ra = float(result['pmra'][0]) if result['pmra'][0] is not None else 0.0
    pm_dec = float(result['pmdec'][0]) if result['pmdec'][0] is not None else 0.0

    c_j2000 = SkyCoord.from_name(target_name)
    print(f"  → RA (J2000)  = {c_j2000.ra.hour:.6f} h")
    print(f"  → Dec (J2000) = {c_j2000.dec.degree:.6f} °")
    print(f"  → Proper motions (mas/yr): {pm_ra:.2f}, {pm_dec:.2f}")

    c_j2000 = SkyCoord(
        ra=c_j2000.ra,
        dec=c_j2000.dec,
        pm_ra_cosdec=pm_ra * u.mas/u.yr,
        pm_dec=pm_dec * u.mas/u.yr,
        obstime=Time("J2000.0"),
        frame="icrs"
    )

    now = Time.now()
    c_now = c_j2000.apply_space_motion(new_obstime=now)
    print(f"  Epoch: {now.iso}")
    print(f"  → RA (computed)  = {c_now.ra.hour:.6f} h")
    print(f"  → Dec (computed) = {c_now.dec.degree:.6f} °")

    return c_now, pm_ra, pm_dec


def apply_proper_motion(c: SkyCoord):
    """Apply proper motion to current epoch."""
    now = Time.now()
    return c.apply_space_motion(new_obstime=now)


def format_coords(c: SkyCoord):
    """Return RA/Dec in sexagesimal string format."""
    ra_str = c.ra.to_string(unit=u.hour, sep=' ', precision=2)
    dec_str = c.dec.to_string(unit=u.deg, sep=' ', precision=2, alwayssign=True)
    return ra_str, dec_str


def parse_ra_dec_strings(ra_value, dec_value):
    """
    Parse manual batch coordinates.
    Returns (ra_hours, dec_deg), or (None, None) if both fields are blank.
    """
    ra_text = str(ra_value).strip()
    dec_text = str(dec_value).strip()

    if not ra_text and not dec_text:
        return None, None
    if not ra_text or not dec_text:
        raise ValueError("Please fill all fields.")

    coord = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg))
    return coord.ra.hour, coord.dec.degree
    
# ----------------------------
# Unified Target Slew Helper
# ----------------------------
def slew_target(target_name: str, apply_pm: bool = False, log_fn=print):
    """
    Resolve a target name via SIMBAD and slew the PWI4 mount accordingly.

    Parameters
    ----------
    target_name : str
        Object name (e.g. 'Sirius')
    apply_pm : bool, optional
        If True, applies proper motion correction to current epoch.
    log_fn : callable, optional
        Logging function (e.g. context.log); defaults to print.

    Returns
    -------
    bool
        True if slew succeeded, False otherwise.
    """
    from utils import (
        resolve_target_simbad,
        resolve_target_simbad_pm,
        apply_proper_motion,
        format_coords,
        slew_pwi4,
        connect_mount,
    )

    try:
        connect_mount()
        if apply_pm:
            log_fn(f"Resolving '{target_name}' via SIMBAD (with proper motion)...")
            coord, pm_ra, pm_dec = resolve_target_simbad_pm(target_name)
            coord_now = apply_proper_motion(coord)
            ra_now, dec_now = coord_now.ra.hour, coord_now.dec.degree
            ra_str, dec_str = format_coords(coord_now)
            log_fn(f"Resolved {target_name}: RA={ra_str}, Dec={dec_str} "
                   f"(pmRA={pm_ra:.2f}, pmDec={pm_dec:.2f} mas/yr)")
        else:
            log_fn(f"Resolving '{target_name}' via SIMBAD (J2000, no PM)...")
            ra_now, dec_now = resolve_target_simbad(target_name)
            log_fn(f"Resolved {target_name}: RA={ra_now:.6f}h, Dec={dec_now:.6f}°")

        log_fn(f"Slewing to {target_name} via PWI4 HTTP (apply_pm={apply_pm})...")
        slew_pwi4(ra_now, dec_now, target_name)
        log_fn(f"✅ Slew to {target_name} complete.")
        return True

    except Exception as e:
        log_fn(f"❌ Slew failed for {target_name}: {e}")
        return False


def slew_target_coords(ra_hours: float, dec_deg: float, target_name: str = "", log_fn=print):
    """
    Slew directly to supplied coordinates, bypassing SIMBAD resolution.
    """
    from utils import connect_mount, format_coords, slew_pwi4

    label = target_name or "manual coordinates"

    try:
        connect_mount()
        coord = SkyCoord(ra=ra_hours * u.hour, dec=dec_deg * u.deg, frame="icrs")
        ra_str, dec_str = format_coords(coord)
        log_fn(f"Using supplied coordinates for {label}: RA={ra_str}, Dec={dec_str}")
        log_fn(f"Slewing to {label} via PWI4 HTTP (manual coordinates)...")
        slew_pwi4(ra_hours, dec_deg, target_name)
        log_fn(f"✅ Slew to {label} complete.")
        return True
    except Exception as e:
        log_fn(f"❌ Slew failed for {label}: {e}")
        return False
        
# ======================================================================
# --- GLOBAL STOP / ABORT HANDLER -------------------------------------
# ======================================================================

def abort_all(context):
    """
    Unified global stop handler for Spectro Capture.
    Halts all active subsystems cleanly:
    - Auto Capture / Sequencer loops
    - Camera exposures
    - PHD2 guiding and adaptive exposure thread
    - Dome and telescope slews

    Safe to call from any tab or thread.
    """
    try:
        context.log("🛑 Total abort requested — halting all systems.")

        # --- 1. Set global stop flag --------------------------------------
        if hasattr(context, "stop_requested"):
            try:
                context.stop_requested.set()
                context.log("Global stop flag set.")
            except Exception as e:
                context.log(f"Could not set global stop flag: {e}")

        # --- 2. Stop guiding and adaptive exposure -----------------------
        try:
            from phd2_control import stop_guiding
            stop_guiding(context)
            context.log("PHD2 guiding stopped.")
        except Exception as e:
            context.log(f"Guide stop error: {e}")

        if hasattr(context, "adaptive_stop"):
            try:
                context.adaptive_stop.set()
                context.log("Adaptive exposure thread signalled to stop.")
            except Exception:
                pass

        # --- 3. Abort current camera exposure ----------------------------
        cam = getattr(context, "camera", None)
        if cam and getattr(cam, "Connected", False):
            try:
                if hasattr(cam, "AbortExposure"):
                    cam.AbortExposure()
                    context.log("Camera exposure aborted.")
            except Exception as e:
                context.log(f"AbortExposure failed: {e}")

        # --- 4. Abort dome motion ----------------------------------------
        dome = getattr(context, "dome", None)
        if dome and getattr(dome, "Connected", False):
            try:
                if hasattr(dome, "AbortSlew"):
                    dome.AbortSlew()
                    context.log("Dome slew aborted.")
            except Exception as e:
                context.log(f"Dome abort failed: {e}")

        # --- 5. Sequencer / Auto Capture reset ----------------------------
        seq = getattr(context, "sequencer", None)
        if seq:
            seq.sequence_running = False
            context.log("Sequencer flagged as stopped.")

        # --- 6. Telescope (optional safety) -------------------------------
        tel = getattr(context, "telescope", None)
        if tel and getattr(tel, "Slewing", False):
            try:
                if hasattr(tel, "AbortSlew"):
                    tel.AbortSlew()
                    context.log("Telescope slew aborted.")
            except Exception as e:
                context.log(f"Telescope abort failed: {e}")
        
        # --- 7. Lamps OFF (final optical safety) --------------------------
        try:
            from utils import lamp_off
            lamp_off()
            context.log("Lamps switched OFF (Stop safety).")
        
            # NEW: ensure header lamp indicators go dark
            if hasattr(context, "tung_on"):
                context.tung_on = False
            if hasattr(context, "thor_on"):
                context.thor_on = False
        
        except Exception as e:
            context.log(f"Lamp shutdown error: {e}")
        
        context.log("✅ All subsystems stopped cleanly.")
    
        # --- UI reset (Sequencer status + buttons) ---
        try:
            seq = getattr(context, "sequencer", None)
            if seq:
                # Reset label
                seq.after(0, lambda: seq.status_lbl.config(text="Idle"))
                # Re-enable buttons (manual + auto)
                for w in [
                    getattr(seq, "run_calib_btn", None),
                    getattr(seq, "run_target_btn", None),
                    getattr(seq, "single_btn", None),
                    getattr(seq, "auto_capture_btn", None),
                ]:
                    if w:
                        seq.after(0, lambda w=w: w.config(state="normal"))
            context.log("✅ Stop complete — Sequencer reset to Idle.")
        except Exception as e:
            context.log(f"⚠️ UI reset after abort failed: {e}")
    
    except Exception as e:
        try:
            context.log(f"Fatal error during abort_all: {e}")
        except Exception:
            print(f"abort_all() fatal error: {e}")

# ======================================================================
# --- END GLOBAL STOP / ABORT HANDLER ----------------------------------
# ======================================================================

# ======================================================================
# Twilight helpers (nautical = Sun -12°)
# ======================================================================

def nautical_dawn_utc(now_local=None, sun_alt_deg: float = -12.0):
    """
    Return the next *morning* time (UTC datetime) when the Sun reaches sun_alt_deg
    on the way UP (i.e., dawn). Default is nautical twilight: -12°.

    - Uses SITE_INFO lat/lon
    - Uses the same "astronomical night" date rule as nightly_folder():
      night begins at local noon; dawn is the following morning.
    """
    from datetime import datetime, timedelta, timezone
    from astropy.coordinates import get_sun

    if now_local is None:
        now_local = datetime.now()

    # Match nightly_folder() night-date logic (local noon rollover)
    if now_local.hour < 12:
        night_date = (now_local - timedelta(days=1)).date()
    else:
        night_date = now_local.date()

    # Dawn is the following morning (night_date + 1), search between 00:00 and 12:00 local
    dawn_date = night_date + timedelta(days=1)
    start_local = datetime(dawn_date.year, dawn_date.month, dawn_date.day, 0, 0, 0)
    end_local   = datetime(dawn_date.year, dawn_date.month, dawn_date.day, 12, 0, 0)

    start_utc = start_local.astimezone(timezone.utc)
    end_utc   = end_local.astimezone(timezone.utc)

    location = EarthLocation(lat=SITE_INFO["SITELAT"], lon=SITE_INFO["SITELONG"])

    def sun_alt_at(t_utc: datetime) -> float:
        tt = Time(t_utc)
        altaz = get_sun(tt).transform_to(AltAz(obstime=tt, location=location))
        return float(altaz.alt.deg)

    # Step forward to find the UP-crossing through sun_alt_deg
    step = timedelta(minutes=5)
    t1 = start_utc
    a1 = sun_alt_at(t1)

    # We want: a1 < sun_alt_deg and later a2 >= sun_alt_deg (rising through threshold)
    while t1 < end_utc:
        t2 = t1 + step
        if t2 > end_utc:
            t2 = end_utc
        a2 = sun_alt_at(t2)

        if a1 < sun_alt_deg <= a2:
            # Binary refine between t1 and t2 to ~10 seconds
            lo, hi = t1, t2
            for _ in range(20):  # ~ (12h range / 2^20) << 10s
                mid = lo + (hi - lo) / 2
                am = sun_alt_at(mid)
                if am >= sun_alt_deg:
                    hi = mid
                else:
                    lo = mid
            return hi  # first time >= threshold

        t1, a1 = t2, a2

    # If we didn't find it, return None (caller decides what to do)
    return None


# ======================================================================
# Scheduler / Batch Time Window Helpers
# ======================================================================

def local_hhmm_window_to_utc_datetimes(
    start_local: str,
    stop_local: str,
    now_local=None
):
    """
    Convert local HH:MM start/stop strings into UTC datetimes.
    Handles midnight rollover automatically.

    Rules:
    - Both start and stop MUST be provided
    - If stop <= start → stop is assumed to be next day
    """
    from datetime import datetime, timezone, timedelta

    if not start_local or not stop_local:
        raise ValueError("Both start and stop times must be provided")

    if now_local is None:
        now_local = datetime.now()

    sh, sm = map(int, start_local.split(":"))
    eh, em = map(int, stop_local.split(":"))

    start_dt_local = now_local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    stop_dt_local  = now_local.replace(hour=eh, minute=em, second=0, microsecond=0)

    # Midnight rollover
    if stop_dt_local <= start_dt_local:
        stop_dt_local += timedelta(days=1)

    start_utc = start_dt_local.astimezone(timezone.utc)
    stop_utc  = stop_dt_local.astimezone(timezone.utc)

    return start_utc, stop_utc



def scheduler_window_open(start_utc, stop_utc, now_utc=None) -> bool:
    """Return True if current UTC time is within scheduler window."""
    from datetime import datetime, timezone
    if not start_utc or not stop_utc:
        return False
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    return start_utc <= now_utc < stop_utc


def scheduler_window_closed(stop_utc, now_utc=None) -> bool:
    """Return True if scheduler stop time has been reached."""
    from datetime import datetime, timezone
    if not stop_utc:
        return False
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    return now_utc >= stop_utc


def wait_until_utc(target_utc, stop_event=None, log_fn=None):
    """
    Block until target UTC time.
    Returns False if stop_event is set before reaching target.
    """
    from datetime import datetime, timezone
    import time

    while datetime.now(timezone.utc) < target_utc:
        if stop_event and stop_event.is_set():
            if log_fn:
                log_fn("🛑 Stop requested while waiting for scheduler start.")
            return False
        time.sleep(5)

    return True
