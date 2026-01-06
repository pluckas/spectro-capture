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
sequencer_logic.py
-------------------
Core backend logic for image sequencing in the Spectro Capture application.

Implements low-level routines for image exposure, FITS file generation, and
header population. Handles communication with the connected camera and ensures
thread-safe sequencing for both manual and automated capture workflows.

Key features:
- Image acquisition via ASCOM camera interface
- FITS file saving with 16-bit signed data and BZERO offset
- Automatic FITS header population (DATE-OBS, EXPOSURE, JD, RA, DEC, etc.)
- Integration with telescope, dome, and environment state via AppContext
- Queues the FITS viewer for live updates after each exposure
- Robust error handling and timeouts to prevent UI blocking
"""

import os
import time
import math
import numpy as np
from astropy.io import fits
from astropy.time import Time

from utils import SITE_INFO

# --- RA/Dec helpers (for FITS header) ---
def ra_to_hms(ra_hours: float) -> str:
    h = int(ra_hours)
    m_float = (ra_hours - h) * 60
    m = int(m_float)
    s = int(round((m_float - m) * 60))
    if s == 60: s, m = 0, m+1
    if m == 60: m, h = 0, (h+1) % 24
    return f"{h:02d} {m:02d} {s:02d}"

def dec_to_dms(dec_deg: float) -> str:
    sign = "-" if dec_deg < 0 else "+"
    dec_abs = abs(dec_deg)
    d = int(dec_abs)
    m_float = (dec_abs - d) * 60
    m = int(m_float)
    s = int(round((m_float - m) * 60))
    if s == 60: s, m = 0, m+1
    if m == 60: m, d = 0, d+1
    return f"{sign}{d:02d} {m:02d} {s:02d}"

def fill_fits_header(hdr, exptime, binning, target_name, telescope=None,
                     camera=None, camera_progid=None, image_type="Light Frame",
                     start_time_utc=None):
    # --- Use the actual start of exposure instead of end ---
    if start_time_utc is not None:
        hdr["DATE-OBS"] = start_time_utc.isot
        hdr["JD"]       = start_time_utc.jd
        hdr["JD-HELIO"] = start_time_utc.tdb.jd
    else:
        # fallback — should not occur in sequencer
        now = Time.now()
        hdr["DATE-OBS"] = now.isot
        hdr["JD"]       = now.jd
        hdr["JD-HELIO"] = now.tdb.jd
    
    hdr["EXPTIME"]  = exptime
    hdr["EXPOSURE"] = exptime
    hdr["AIRMASS"]  = "NaN"  # placeholder until telescope data available

    hdr["XBINNING"] = binning
    hdr["YBINNING"] = binning
    hdr["XPIXSZ"]   = SITE_INFO["XPIXSZ"] * binning
    hdr["YPIXSZ"]   = SITE_INFO["YPIXSZ"] * binning
    hdr["IMAGETYP"] = image_type
    hdr["SWCREATE"] = "Spectro Capture by Paul Luckas"

    hdr["INSTRUME"] = str(camera_progid) if camera_progid else "ASCOM"

    # Optional driver gain/offset if exposed
    if camera:
        try:
            hdr["GAIN"] = float(camera.Gain)
        except Exception:
            hdr["GAIN"] = "NaN"
        try:
            hdr["OFFSET"] = float(camera.Offset)
        except Exception:
            hdr["OFFSET"] = "NaN"
    # Site metadata
    hdr["TELESCOP"] = SITE_INFO["TELESCOP"]
    hdr["FOCALLEN"] = SITE_INFO["FOCALLEN"]
    hdr["APTDIA"]   = SITE_INFO["APTDIA"]
    hdr["APTAREA"]  = SITE_INFO["APTAREA"]
    hdr["SITELAT"]  = SITE_INFO["SITELAT"]
    hdr["SITELONG"] = SITE_INFO["SITELONG"]
    hdr["OBSERVER"] = SITE_INFO["OBSERVER"]
    hdr["OBJECT"]   = target_name

    # Cooler info if available
    if camera and getattr(camera, "Connected", False):
        setpoint_val = None
        for prop in ("CCDTemperatureSetPoint", "SetCCDTemperature", "TemperatureSetPoint"):
            try:
                setpoint_val = getattr(camera, prop)
                if setpoint_val is not None:
                    setpoint_val = float(setpoint_val)
                    break
            except Exception:
                continue
        hdr["SET-TEMP"] = setpoint_val if setpoint_val is not None else "NaN"
        try:
            hdr["CCD-TEMP"] = float(camera.CCDTemperature)
        except Exception:
            hdr["CCD-TEMP"] = "NaN"

    # Telescope pointing (optional)
    if telescope and getattr(telescope, "Connected", False):
        try:
            ra  = telescope.RightAscension
            dec = telescope.Declination
            alt = telescope.Altitude
            az  = telescope.Azimuth
            hdr["OBJCTRA"]  = ra_to_hms(ra)
            hdr["OBJCTDEC"] = dec_to_dms(dec)
            hdr["OBJCTALT"] = f"{alt:.4f}"
            hdr["OBJCTAZ"]  = f"{az:.4f}"
            # More stable Kasten–Young (1989) airmass model
            z = 90 - alt  # zenith angle
            if z < 90:  # avoid horizon singularity
                hdr["AIRMASS"] = 1.0 / (math.cos(math.radians(z)) + 0.50572 * (96.07995 - z) ** -1.6364)
            else:
                hdr["AIRMASS"] = "NaN"
        except Exception:
            pass

def expose_and_save(context, exptime, save_dir, basename, index, binning, target_name, image_type="Light Frame", status_label=None):
    """
    Live exposure using ASCOM camera in context, writes FITS with your
    exact data handling (signed int16 with BZERO=32768), updates Viewer, logs.
    """
    cam = context.camera
    tel = context.telescope

    if not cam or not getattr(cam, "Connected", False):
        context.log("Camera not connected."); return None

    # Binning and full-frame
    cam.BinX = binning
    cam.BinY = binning
    cam.NumX = cam.CameraXSize // binning
    cam.NumY = cam.CameraYSize // binning
    cam.StartX, cam.StartY = 0, 0

    # --- Record start of exposure (UTC) ---
    start_time_utc = Time.now()
    
    # --- Start exposure ---
    try:
        cam.StartExposure(float(exptime), True)
    except Exception as e:
        context.log(f"StartExposure failed: {e}")
        return None
    
    context.log(f"Exposing {basename}-{index}.fit for {exptime}s...")
    
    # --- Poll until image is ready, with safety timeout ---
    start_time = time.time()
    timeout = float(exptime) + 120  # allow 2-minute margin beyond nominal exposure
    last_update = -1  # to track 1-second updates
    
    while not cam.ImageReady:
        elapsed = time.time() - start_time
    
        # --- Update GUI once per second (non-blocking, MaxIM-style) ---
        if int(elapsed) != last_update:
            last_update = int(elapsed)
            try:
                # Centralised exposure status updater
                from status_bar import update_capture_status
                update_capture_status(
                    context,
                    label=status_label if status_label else target_name,
                    index=index,
                    total=getattr(context, "sequence_total", index),
                    elapsed=last_update,
                    exposure=exptime,
                )
            except Exception:
                pass	
    
        # --- Timeout protection (unchanged) ---
        if elapsed > timeout:
            context.log("Exposure timeout – camera did not signal ImageReady.")
            try:
                cam.AbortExposure()  # if supported, stop exposure cleanly
                context.log("Exposure aborted.")
            except Exception:
                context.log("AbortExposure not supported or failed.")
            return None
    
        time.sleep(0.2)  # safe, slightly slower poll interval
        
        # --- Abort guard: ONLY apply to Sequencer light-frame runs ---
        # Bias / Dark / ToolsTab exposures must NOT be aborted here.
        if (
            image_type == "Light Frame"
            and hasattr(context, "sequencer")
            and not getattr(context.sequencer, "sequence_running", True)
        ):
            context.log("Exposure aborted by user – skipping image download and save.")
            return None


    # Get image
    try:
        arr = np.array(cam.ImageArray)
    except Exception:
        arr = np.array(getattr(cam, "ImageArrayVariant"))

    expected = (cam.CameraYSize // binning, cam.CameraXSize // binning)
    if arr.shape != expected:
        arr = arr.T

    # Normalize dtype to uint16 0..65535
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.clip(np.rint(arr), 0, 65535).astype(np.uint16, copy=False)
    else:
        if arr.dtype != np.uint16:
            arr = np.clip(arr.astype(np.int32), 0, 65535).astype(np.uint16, copy=False)

    # Convert to signed int16 with pedestal (BZERO = 32768)
    arr16 = (arr.astype(np.int32) - 32768).astype(np.int16, copy=False)

    hdu = fits.PrimaryHDU(arr16)
    hdr = hdu.header
    # MaxIM/CCD Commander/ISIS-compatible scaling
    hdr["BSCALE"] = 1
    hdr["BZERO"]  = 32768
    hdr["READOUTM"] = "Mode0"
    hdr["SBSTDVER"] = "SBFITSEXT Version 1.0"
    hdr["ROWORDER"] = "TOP-DOWN"
    hdr["FLIPSTAT"] = "        "
    hdr["XORGSUBF"] = 0
    hdr["YORGSUBF"] = 0

    fill_fits_header(
        hdr,
        float(exptime),
        binning,
        target_name,
        telescope=tel,
        camera=cam,
        camera_progid=context.cfg.get("last_camera"),
        image_type=image_type,
        start_time_utc=start_time_utc
    )

    # ---------------------------------------------------------
    # NEW: sanitise basename for safe file naming
    # ---------------------------------------------------------
    from utils import make_file_safe
    basename = make_file_safe(basename)
    # ---------------------------------------------------------
    
    os.makedirs(save_dir, exist_ok=True)
    fname = os.path.join(save_dir, f"{basename}-{index}.fit")
    hdu.writeto(fname, overwrite=True)
    context.log(f"Saved {fname}")

    # Push to viewer if present (thread-safe via Tk)
    if context.viewer:
        try:
            context.viewer.after(0, lambda f=fname: context.viewer.load_fits(f))
        except Exception:
            # Fallback if .after isn't available for some reason
            context.viewer.load_fits(fname)

    return fname