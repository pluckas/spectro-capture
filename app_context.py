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
app_context.py
---------------
Shared application state and configuration manager for the Spectro Capture suite.

Defines the AppContext class, which centralizes access to global resources such
as the camera, telescope, guider, dome, sequencer, and logger. Provides methods
for loading, saving, and persisting configuration data across sessions.

Key features:
- Centralized storage of connected device instances
- Unified logging and message dispatch to all GUI tabs
- Persistent configuration management via spectro_config.json
- Thread-safe access to shared state between modules
- Integration point for sequencer, guider, dome, and tool subsystems
"""

import os
import json
from datetime import datetime
import utils

LOG_DIR = r"C:\Users\Luckas Observatory\OneDrive\astronomy\Spectroscopy\AutoCapture_Logs"

# Always store config in the same folder as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "spectro_config.json")


def load_config():
    """Load saved settings from spectro_config.json (or return defaults)."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Config load failed: {e}")
            return {}
    return {}


def save_config(cfg):
    """Save settings safely to spectro_config.json."""
    tmp_path = CONFIG_FILE + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp_path, CONFIG_FILE)
    except Exception as e:
        print(f"Config save failed: {e}")


class AppContext:
    def __init__(self):
        # Shared device handles
        self.camera = None          # ASCOM camera object
        self.telescope = None       # ASCOM telescope object
        self.dome = None            # ASCOM dome object

        # Dome–Telescope sync flag (for GUI indicator)
        self.dome_sync_enabled = False

        # Shared UI/tab refs (set by main.py)
        self.sequencer = None   # Sequencer tab
        self.status = None      # Status/log output tab
        self.viewer = None      # FITS viewer tab
        self.targets = None     # Target management tab
        self.tools = None       # Calibration tools tab
        self.dome_tab = None    # Dome control tab
        self.setup = None       # Setup tab (camera/telescope connections)

        # Configuration
        self.cfg = load_config()
        if "last_setpoint" not in self.cfg:
            self.cfg["last_setpoint"] = -5
        
        # --- NEW: load root path from config (if set by user) ---
        self.root_path = self.cfg.get("root_path", None)
        
        # >>> BEGIN NEW CODE: persistent folder paths <<<
        # Calibration save folder (bias/darks) – stored by Tools tab
        self.calibration_path = self.cfg.get("calibration_path", None)
        
        # Batch CSV folder – used ONLY by gui_batch to load/save batch target lists
        self.batch_csv_path = self.cfg.get("batch_csv_path", None)
        # >>> END NEW CODE <<<
        
        # ===== BEGIN ROI CONFIG (READ-ONLY; CONFIG-DRIVEN) =====
        # Read ROI config if present; otherwise use neutral placeholders.
        roi_cfg = self.cfg.get("roi", {})

        cx = roi_cfg.get("centre_x", 0)
        cy = roi_cfg.get("centre_y", 0)
        size = roi_cfg.get("size", 0)

        # Expose on context for other modules (auto_capture, batch_runner, etc.)
        # We do NOT write back into self.cfg here; gui_setup will own creation
        # and persistence of ROI values on first user configuration.
        try:
            self.roi_centre_x = int(cx)
        except (TypeError, ValueError):
            self.roi_centre_x = 0

        try:
            self.roi_centre_y = int(cy)
        except (TypeError, ValueError):
            self.roi_centre_y = 0

        try:
            self.roi_size = int(size)
        except (TypeError, ValueError):
            self.roi_size = 0
        # ===== END ROI CONFIG (READ-ONLY; CONFIG-DRIVEN) =====
        
        # ===== NEW: stored ROI average from previous sessions =====
        roi_stats = self.cfg.get("roi_stats", {})
        self.roi_avg_x = roi_stats.get("avg_x", None)
        self.roi_avg_y = roi_stats.get("avg_y", None)
        # ==========================================================
        
        # Binning chosen in Setup (default 2)
        self.current_binning = int(self.cfg.get("binning", 2))
        
        # ----- NEW: Lamp indicator states -----
        # Used by status_bar.update_status_header()
        self.tung_on = False   # Tungsten lamp OFF by default
        self.thor_on = False   # Thorium lamp OFF by default
        
        # Logging (Status tab will set this)
        self._log_callback = None
        
        # --- Global control flags (for Stop / Abort handling) ---
        import threading
        self.stop_requested = threading.Event()   # master Stop flag (set by Stop button)
        self.adaptive_stop = threading.Event()    # used by adaptive exposure thread
        
        # --- ROI auto-measured star-hit buffer (non-persistent) ---
        # Auto Capture / Batch Runner will append (x, y) samples when PHD2 finds a star.
        # This list is kept small (<= 30 samples) to avoid unbounded growth.
        self._roi_samples = []
        
        # --- Observing-night log date (fixed for this app session) ---
        self.log_date_str = utils.observing_night_date_str()

    def set_log_callback(self, callback):
        """Register a function (usually from Status tab) to receive log messages."""
        self._log_callback = callback

    def log(self, msg):
        """Safely send a message to the shared log, console, and daily log file."""
        try:
            if self._log_callback:
                self._log_callback(msg)
            else:
                print(msg)
    
            os.makedirs(LOG_DIR, exist_ok=True)
            log_name = os.path.join(
                LOG_DIR, f"spectro_{self.log_date_str}.log"
            )
    
            ts = datetime.now().strftime("%H:%M:%S")
            with open(log_name, "a", encoding="utf-8") as f:
                f.write(f"{ts} {msg}\n")
    
        except Exception as e:
            print(f"[LOGGING ERROR] {e}: {msg}")

    # --- Guide Tab logging ----------------------------------------------------
    def set_guide_log_callback(self, callback):
        """Register a function (usually from Guide tab) to receive guider log messages."""
        self._guide_log_callback = callback
    
    def guide_log(self, msg):
        try:
            # Always show everything in the Guide tab (run-time UI)
            if hasattr(self, "_guide_log_callback") and self._guide_log_callback:
                self._guide_log_callback(msg)
    
            # --- suppress adaptive / control-loop noise from disk log only ---
            NOISY_GUIDE_STRINGS = (
                "Adaptive exposure",
                "Exposure Locked",
                "Increasing exposure",
                "Decreasing exposure",
                "Guiding paused",
            )
    
            for s in NOISY_GUIDE_STRINGS:
                if s in msg:
                    return
    
            # --- observing-night log file ---
            os.makedirs(LOG_DIR, exist_ok=True)
            log_name = os.path.join(
                LOG_DIR, f"spectro_{self.log_date_str}.log"
            )
    
            ts = datetime.now().strftime("%H:%M:%S")
            with open(log_name, "a", encoding="utf-8") as f:
                f.write(f"{ts} [GUIDE] {msg}\n")
    
        except Exception:
            pass

    def is_connected(self, device_name):
        """Return True if a given device ('camera', 'telescope', 'dome') is connected."""
        dev = getattr(self, device_name, None)
        return bool(dev and getattr(dev, "Connected", False))

    def save_config(self):
        save_config(self.cfg)
    
    # --- NEW: setter for user-selected root path --------------------------
    def set_root_path(self, path: str):
        """Set and persist the user-selected image root path."""
        try:
            self.root_path = path
            self.cfg["root_path"] = path
            self.save_config()
    
            try:
                import utils
                utils.set_root_path_override(path)
            except Exception:
                pass
    
        except Exception as e:
            self.log(f"Failed to save root path: {e}")
    
    # >>> BEGIN NEW CODE: setters for new persistent folder paths <<<
    
    def set_calibration_path(self, path: str):
        """Set and persist the bias/dark calibration save folder."""
        try:
            self.calibration_path = path
            self.cfg["calibration_path"] = path
            self.save_config()
        except Exception as e:
            self.log(f"Failed to save calibration path: {e}")
    
    # >>> BEGIN NEW CODE: setter for Batch CSV folder <<<
    def set_batch_csv_path(self, path: str):
        """Set and persist the folder used for Batch CSV files."""
        try:
            self.batch_csv_path = path
            self.cfg["batch_csv_path"] = path
            self.save_config()
        except Exception as e:
            self.log(f"Failed to save batch CSV path: {e}")
    # >>> END NEW CODE <<<
        
    def set_status(self, text):
        """Safely update the Sequencer status label from any thread."""
        seq = getattr(self, "sequencer", None)
        if seq and hasattr(seq, "status_lbl"):
            try:
                seq.after(0, lambda: seq.status_lbl.config(text=text))
            except Exception:
                pass
    
    # ===== BEGIN ROI SAMPLE MANAGEMENT (with persisted average) =====
    def record_roi_hit(self, x, y):
        """
        Called by auto_capture/batch_runner when a star is successfully found.
        Records (x, y) into a rolling buffer of <= 30 samples
        AND updates a stored average in spectro_config.json.
        """
        try:
            fx = float(x)
            fy = float(y)
    
            # Rolling in-memory buffer (as before)
            self._roi_samples.append((fx, fy))
            if len(self._roi_samples) > 30:
                self._roi_samples.pop(0)
    
            # Recompute average from current buffer and persist it
            xs = [p[0] for p in self._roi_samples]
            ys = [p[1] for p in self._roi_samples]
            avg_x = sum(xs) / len(xs)
            avg_y = sum(ys) / len(ys)
    
            # Cache for this session
            self.roi_avg_x = avg_x
            self.roi_avg_y = avg_y
    
            # Store in config for next startup
            self.cfg.setdefault("roi_stats", {})
            self.cfg["roi_stats"]["avg_x"] = avg_x
            self.cfg["roi_stats"]["avg_y"] = avg_y
            self.save_config()
        except Exception:
            pass
    
    @property
    def roi_auto_average(self):
        """
        Return (avg_x, avg_y) from:
          - current session samples if present, otherwise
          - last stored average from previous sessions.
        """
        if self._roi_samples:
            xs = [p[0] for p in self._roi_samples]
            ys = [p[1] for p in self._roi_samples]
            return (sum(xs) / len(xs), sum(ys) / len(ys))
    
        if self.roi_avg_x is not None and self.roi_avg_y is not None:
            return (self.roi_avg_x, self.roi_avg_y)
    
        return None
    # ===== END ROI SAMPLE MANAGEMENT =====
