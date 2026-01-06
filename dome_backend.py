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
dome_backend.py
----------------
Backend control layer for dome synchronization and motion logic.

Implements the DomeSyncManager class, which communicates with the ASCOM
MaxDome II driver to coordinate dome azimuth and shutter operations. Handles
geometric corrections between telescope and dome center, enabling precise
tracking of telescope pointing.

Key features:
- ASCOM-based dome communication via win32com
- Dome azimuth and shutter state monitoring
- Automatic dome–telescope synchronization
- Configurable geometric offsets:
  • East/West, North/South, and Up/Down dome center offsets  
  • Optical axis offset rotation with RA (equatorial mount)
- Background polling and safety interlocks
- Designed for southern-hemisphere equatorial systems (e.g., PlaneWave L-350)
"""

import threading
import time
import math
import pythoncom
import win32com.client
import numpy as np

# Status lookup for dome shutter
SHUTTER_STATUS = {
    0: "Open",
    1: "Closed",
    2: "Opening",
    3: "Closing",
    4: "Error"
}

# Constants for sync
SLEW_THRESHOLD = 1.0
SLEW_SETTLING_TIME = 1
DOME_COMMAND_INTERVAL = 5
AZIMUTH_ZERO_OFFSET = 0.0
AZIMUTH_DIRECTION = 1  # Clockwise

# --- Sync Manager --- #
class DomeSyncManager:
    def __init__(self, log_func):
        self.thread = None
        self.stop_event = threading.Event()
        self.log = log_func
        self.dome = None
        self.on_stop_callback = None  # GUI callback for async stop

        # Default settings
        self.settings = {
            "latitude": -31.95917,
            "dome_radius": 1.15,
            "offset_east": 0.023,
            "offset_north": 0.0,
            "offset_up": 0.0,
            "optical_offset": -0.127
        }

    def set_dome(self, dome):
        self.dome = dome

    def start(self):
        if self.thread and self.thread.is_alive():
            self.log("Tele Sync already running.")
            return
        if not self.dome:
            self.log("Cannot start sync: Dome not set.")
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.sync_loop, daemon=True)
        self.thread.start()
        # self.log("Tele Sync started.\n")

    def stop(self):
        # Trigger asynchronous stop
        self.stop_event.set()
        self.log("Tele Sync stopping...")
        if self.on_stop_callback:
            self.on_stop_callback()  # Let GUI handle button greying

    def compute_dome_azimuth(self, lst_hours, ra_hours, dec_deg, lat_deg,
                             offset_east=0.023, offset_north=0.0, offset_up=0.0,
                             optical_offset_dec=-0.127, dome_radius=1.15):
        # Same geometry logic as original
        ha_deg = (lst_hours - ra_hours) * 15.0
        ha_rad = math.radians(ha_deg)
        dec_rad = math.radians(dec_deg)
        lat_rad = math.radians(lat_deg)

        sin_alt = math.sin(dec_rad) * math.sin(lat_rad) + math.cos(dec_rad) * math.cos(lat_rad) * math.cos(ha_rad)
        alt_rad = math.asin(sin_alt)

        cos_az = (math.sin(dec_rad) - math.sin(alt_rad) * math.sin(lat_rad)) / (math.cos(alt_rad) * math.cos(lat_rad))
        cos_az = max(-1, min(1, cos_az))
        az_rad = math.acos(cos_az)

        if math.sin(ha_rad) > 0:
            azimuth_deg = 360 - math.degrees(az_rad)
        else:
            azimuth_deg = math.degrees(az_rad)

        az_rad = math.radians(azimuth_deg)
        x_opt = math.cos(alt_rad) * math.sin(az_rad)
        y_opt = math.cos(alt_rad) * math.cos(az_rad)
        z_opt = math.sin(alt_rad)
        opt_vec = np.array([x_opt, y_opt, z_opt])

        sin_lat = math.sin(lat_rad)
        cos_lat = math.cos(lat_rad)
        ra_axis = np.array([0, cos_lat, sin_lat])
        dec_axis_vec = np.cross(ra_axis, opt_vec)
        dec_axis_vec /= np.linalg.norm(dec_axis_vec)

        shifted_vec = opt_vec + optical_offset_dec * dec_axis_vec
        P0 = np.array([offset_east, offset_north, offset_up])
        d = shifted_vec / np.linalg.norm(shifted_vec)

        a = 1.0
        b = 2 * np.dot(P0, d)
        c = np.dot(P0, P0) - dome_radius ** 2

        discriminant = b**2 - 4*a*c
        if discriminant < 0:
            intersection_point = P0 + d
        else:
            sqrt_disc = math.sqrt(discriminant)
            t1 = (-b + sqrt_disc) / (2*a)
            t2 = (-b - sqrt_disc) / (2*a)
            t_candidates = [t for t in [t1, t2] if t > 0]
            t = min(t_candidates) if t_candidates else 1
            intersection_point = P0 + t * d

        x_h, y_h = intersection_point[0], intersection_point[1]
        dome_az = (math.degrees(math.atan2(x_h, y_h)) + 360) % 360
        dome_az = (AZIMUTH_ZERO_OFFSET + AZIMUTH_DIRECTION * dome_az) % 360

        return dome_az

    def sync_loop(self):
        try:
            pythoncom.CoInitialize()
            telescope = win32com.client.Dispatch("ASCOM.PWI4.Telescope")
            telescope.Connected = True
    
            last_command_time = 0
            settle_until = 0
            prev_tel_slewing = False  # track last slewing state
    
            while not self.stop_event.is_set():
                try:
                    ra = telescope.RightAscension
                    dec = telescope.Declination
                    lst = telescope.SiderealTime
                    dome_az = self.dome.Azimuth
    
                    # --- Detect real telescope slews (ignore tracking corrections) ---
                    try:
                        tel_slewing = telescope.Slewing  # revert: trust PWI4 slewing flag
                    except Exception:
                        tel_slewing = False
                    
                    # --- Log slewing start/complete events once ---
                    if tel_slewing and not prev_tel_slewing:
                        self.log("Telescope slewing...")
                        settle_until = time.time() + SLEW_SETTLING_TIME
                    elif not tel_slewing and prev_tel_slewing:
                        self.log("Telescope slew complete.")
                    
                    prev_tel_slewing = tel_slewing
    
                    # --- Pause dome sync during active telescope slews ---
                    if tel_slewing or time.time() < settle_until:
                        time.sleep(1)
                        continue
    
                    # --- Normal sync geometry below ---
                    s = self.settings
                    target_az = self.compute_dome_azimuth(
                        lst, ra, dec, s["latitude"],
                        s["offset_east"], s["offset_north"], s["offset_up"],
                        s["optical_offset"], s["dome_radius"]
                    )
    
                    diff = abs((dome_az - target_az + 180) % 360 - 180)
    
                    # --- Dome movement control & logging ---
                    try:
                        dome_slewing = self.dome.Slewing
                    except Exception:
                        dome_slewing = False
                    
                    # --- Detect if dome needs to move significantly ---
                    if diff > SLEW_THRESHOLD and not dome_slewing and \
                       (time.time() - last_command_time) > DOME_COMMAND_INTERVAL:
                        target_az = round(target_az, 2)
                        self.dome.SlewToAzimuth(target_az)
                        last_command_time = time.time()
                    
                    # --- Log dome settle (once) after motion completes ---
                    prev_dome_slewing = getattr(self, "_prev_dome_slewing", False)
                    if not dome_slewing and prev_dome_slewing:
                        self.log("Dome at telescope position.")
                    self._prev_dome_slewing = dome_slewing
    
                    # No need to log anything when aligned — stay quiet
                    time.sleep(1)
    
                except Exception as e:
                    self.log(f"Sync error: {e}")
                    time.sleep(5)
    
        finally:
            try:
                telescope.Connected = False
            except Exception:
                pass
            self.log("Sync loop exited.")



