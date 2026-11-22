# Spectro Capture Browser Monitor

## Setup

To start the web monitor automatically, add `"web_host": "192.0.2.10"`
to the top level of the Windows host's local `spectro_config.json`
alongside its existing settings. Replace the example address `192.0.2.10`
with the LAN IP address of the computer running Spectro Capture, then start
the application as usual.
For a one-session override, set the host in Windows Command Prompt,
substituting the LAN IP address again:

```bat
set SPECTRO_WEB_HOST=192.0.2.10
python main.py
```

On a device on the same LAN, open `http://<windows-host-ip>:8765/`.
Use `127.0.0.1` as the host to test directly on the Windows computer.
Use the monitor on a trusted private LAN and keep TCP port
8765 off the public internet. If neither setting supplies a host, only the
desktop app starts. Run `set SPECTRO_WEB_HOST=` to clear the override
in that Command Prompt.

The monitor shows the desktop's batch plan, the active run, camera exposure
progress and the recent Status log. A **Processed** row means the batch
worker reached the end of the science target step. Check the Status log
and output folder for frame-save results.

## Optional public view

With the LAN monitor configured, set `"web_public_view": true` in the same
local configuration and restart Spectro Capture. This starts a second listener
at `http://127.0.0.1:8766/` on the Windows host. It serves the dashboard,
including live status, logs and previews, and rejects all POST requests.
Visitors can open and collapse panels in their own browser. The public view
includes the same run-log text, including any local paths it contains.

Point a tunnel running on that host at port 8766 only. Port 8765 is the LAN
control listener. Remove the setting or set it to false and restart Spectro
Capture to disable the local public-view listener.

## Camera controls

Click **Camera** in the browser status strip to connect or disconnect the
camera, select binning (1, 2 or 3), or apply a temperature setpoint. Select the ASCOM driver in the
Windows app's Setup tab. If the cooler is off, Apply saves the setpoint
for the next time the cooler is turned on. Use **Cooler On** to start
cooling at the saved setpoint, and **Cooler Off** to stop it. Camera commands are blocked
during acquisition. Anyone who can reach the monitor can use its camera
controls. Use **Take Test Image** to take a single exposure and view it in
**FITS Viewer**.

## Telescope and dome controls

Click **Telescope** in the status strip to connect or disconnect the
telescope configured in Windows Setup. You can slew by target name or enter
J2000 RA and Dec directly. Click **Dome** for shutter,
position and sync status, and for Connect, Disconnect, Open Shutter,
Close Shutter, Park, Home, Abort and Sync. Shutter state also appears
in the status strip. Open Shutter, Close Shutter, Park and Home ask for confirmation. Abort sends the same slew-stop command as
the desktop Dome tab and leaves the sync setting as it was.

Dome Abort remains available during acquisition. Other browser telescope
and dome commands, including Close Shutter, are blocked. Disable dome sync and let it stop before
parking, homing, or disconnecting either device.

## Lamps, calibration and guiding

Open **Lamp Control** in the status strip to select **Tungsten**, **Thorium**,
or **Clear All**. The lamp panel closes after a selection, so the Camera
controls can stay open for a test exposure.

Use **Calibration Settings** to set tungsten and thorium exposure durations
and frame counts, or run a manual calibration. Open **Guiding** for PHD2
controls and the guide-field and fibre previews.

## Running a batch

Load the target list in the desktop app or through **Targets** in the browser.
Open **Batch options** to change Smart HA Scheduling, HA Min and Max, and an optional
start time in observatory local time (24-hour HH:MM). The panel also includes the
desktop calibration checkboxes and the options to park the telescope,
close the dome, and warm the camera after a batch. Each change updates
the Windows Batch tab.
In the browser’s Batch Runner card, **Run** starts the loaded batch using
those settings. **Stop** requests the same stop as the desktop Batch tab.
Wait for cleanup to finish before starting another batch.

## Batch CSV files

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

While the batch is idle, drag a target by its handle to change the shared
list order. You can also focus the handle and use the Up and Down arrow
keys. The desktop table and CSV export use the new order. Runs in List
order mode follow that sequence.
