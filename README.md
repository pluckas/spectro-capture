# Spectro Capture

© Paul Luckas, 2025

Spectro Capture is a Python-based astronomical spectroscopy acquisition and
automation application developed for use in a private observatory environment.

While presented as a single application, Spectro Capture is implemented as a
collection of cooperating Python scripts. Individual components handle tasks
such as device control, guiding, sequencing, scheduling, and data management,
and are intended to be used together as part of the overall Spectro Capture
workflow.

Many aspects of the implementation follow common, conventional approaches used
throughout the astronomy and scientific Python ecosystem. Other developers may
reasonably arrive at similar solutions independently.

This repository is shared for evaluation, personal use, and collaboration within
the amateur and research astronomy community.

---

## Licensing and Use

Spectro Capture is made available for **personal, educational, and research use
only**.

Commercial use of these scripts, or of software incorporating them in whole or in
part, is **not permitted** without explicit prior permission from the author.

You are free to:
- Use the scripts for non-commercial purposes
- Modify the scripts for your own use
- Share modified or unmodified versions for non-commercial purposes

You may **not**:
- Use these scripts, or software incorporating them in whole or in part, as part
  of a commercial or paid product
- Sell, sublicense, or commercially distribute these scripts or derivative works
  based on them

All redistributed copies or derivatives should retain:
- Author attribution
- This README (or an equivalent attribution notice)
- The accompanying LICENSE file

Full licensing terms are provided in the `LICENSE` file.

---

## Third-Party Software and Libraries

Spectro Capture makes extensive use of established third-party software and
Python libraries, which are not distributed as part of this repository and
remain subject to their own licenses.

These include, but are not limited to:

- **Astropy** and affiliated packages
- Standard scientific Python libraries (e.g. NumPy, SciPy)
- Platform- and device-specific drivers and interfaces

Users are responsible for ensuring compliance with the licenses of all
third-party software used alongside Spectro Capture.

---

## Guiding Software Attribution

Spectro Capture interacts with external guiding software and related tools,
which are not part of this repository.

In particular:

- **PHD2 Guiding**  
  Spectro Capture communicates with PHD2 via its published JSON-RPC interface.

- **Andy Galasso’s PHD2 Python Client**  
  Portions of the guiding control logic are based on, or inspired by, Andy
  Galasso’s Python client for PHD2. Copyright for that work remains with its
  respective author and is governed by its own license.

---

## Hardware and Vendor Software

Spectro Capture interfaces with a range of third-party hardware and vendor
software. These products and their drivers are not distributed with this
repository and remain subject to their own licenses and terms.

Examples include:
- **PlaneWave Instruments** (mount control via PWI4 or related interfaces)
- **Diffraction Limited** (MaxDome / MaxDome II dome control)
- **ZWO (ASI)** cameras and associated drivers
- **ASCOM Platform** for device interoperability


## Attribution

If you use or adapt Spectro Capture or its scripts in your own non-commercial
projects, attribution to the original project and author is **highly
encouraged**.

Example attribution:

> “Based on Spectro Capture by Paul Luckas.”

---

## Project Status

This project is under active development.

The codebase reflects a working, evolving system and may change without notice.
Stability, interfaces, and internal structure should not be considered fixed at
this stage.

Spectro Capture is provided as Python source code rather than as a packaged installer. Python dependency and environment information is provided in the `docs` folder..

---


## Disclaimer

This software is provided “as is”, without warranty of any kind.
Use at your own risk. 
Use of this software does not imply endorsement by the author of any derived work.

### Browser monitor

Start Spectro Capture from Windows Command Prompt with the monitor enabled.
Set the host to the Observatory PC's LAN address:

```bat
set SPECTRO_WEB_HOST=192.168.20.10
python main.py
```

Open <http://192.168.20.10:8765/> on a Mac or iPad on the observatory LAN.
Use `127.0.0.1` as the host to test from the Observatory PC itself.
Use the monitor on your trusted observatory LAN and keep TCP port
8765 off the public internet. Starting normally with the variable unset
runs the desktop application on its own. To unset it in the same Command
Prompt, run `set SPECTRO_WEB_HOST=`.

The monitor shows the desktop's batch plan, the active run, camera exposure
progress and the recent Status log. A **Processed** row means the batch
worker reached the end of the science target step. Check the Status log
and output folder for frame-save results.


#### Camera controls

Click **Camera** in the browser status strip to connect or disconnect the
camera, select binning (1, 2 or 3), or apply a temperature setpoint. Select the ASCOM driver in the
Windows app's Setup tab. If the cooler is off, Apply saves the setpoint
for the next time the cooler is turned on. Use **Cooler On** to start
cooling at the saved setpoint, and **Cooler Off** to stop it. Camera commands are blocked
during acquisition. Anyone who can reach the monitor can use its camera
controls.

#### Telescope and dome controls

Click **Telescope** in the status strip to connect or disconnect the
telescope configured in Windows Setup. Click **Dome** for shutter,
position and sync status, and for Connect, Disconnect, Open Shutter,
Close Shutter, Park, Home, Abort and Sync. Shutter state also appears
in the status strip. The browser asks for confirmation
before requesting movement. Abort sends the same slew-stop command as
the desktop Dome tab and leaves the sync setting as it was.

Dome Abort remains available during acquisition. Other browser telescope
and dome commands, including Close Shutter, are blocked. Disable dome sync and let it stop before
parking, homing, or disconnecting either device.

#### Running a batch

Load the target list on the Observatory PC. In the browser, open **Batch
options** to change Smart HA Scheduling, HA Min and Max, and an optional
start time in observatory local time. The panel also includes the
desktop calibration checkboxes and the options to park the telescope,
close the dome, and warm the camera after a batch. Each change updates
the Windows Batch tab.
In the browser’s Batch Runner card, **Run** starts the loaded batch using
those settings. **Stop** requests the same stop as the desktop Batch tab.
Wait for cleanup to finish before starting another batch.


#### Batch CSV files

Use **Load CSV** under **Targets** to pick a Spectro Capture CSV from
the browser's computer, a synced folder, or a mounted share. A valid
import replaces the current desktop and browser target list. **Download
CSV** exports that list with RA, Dec, and reference settings included.
Per-target start times are stored as UT, including in desktop-compatible
exports whose column header reads `Start Time (Local)`.


When the batch is idle, click an exposure, frame count, or UT start time
in the Targets table to edit it. Enter saves the value and Escape cancels.
Use the Calibration and Enabled checkboxes to update those options.


Click a target name to edit its name and coordinates together, or use
**Add Target** to add a row with the desktop defaults. Enter both RA and
Dec or leave both blank to resolve the target by name at run time.
