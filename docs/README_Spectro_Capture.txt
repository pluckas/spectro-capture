Spectro Capture – Notes

======================

What this is
------------
Spectro Capture is a custom Python application I use to run my observatory.
It handles telescope, dome, camera, guiding, sequencing, and batch automation
for spectroscopy. It is capable of running a list of targets completely autonomously, without user intervention, all night.

This is not a generic astronomy app and it is not plug-and-play. It has grown
over time to solve very specific problems at my observatory.


Current state
-------------
The system is actively used and works reliably in real observing runs. 
Core things like slewing, guiding, dome sync, sequencing, and batch runs are stable.
The code reflects incremental development rather than a clean-sheet design.

What’s solid:
- Camera connect, cooling, exposures
- Telescope slews via PlaneWave PWI4 HTTP
- Dome control and sync
- PHD2 guiding with custom ROI, adaptive exposure and fibre logic
- Sequencer for manual work
- Batch Runner (now the main automation engine)
- Logging and file handling


Known issues / caveats
---------------------

Auto Capture:
- Auto Capture is out of date.
- It still exists but should be treated as legacy.
- Anything Auto Capture used to do can now be done using the Batch Runner,
  including single targets.
- Batch Runner is the current source of truth.

General:
- Some logic is duplicated in places.
- Refactoring has been avoided on purpose to keep a working system stable.
- This assumes you know how the hardware behaves at night.


Observatory-specific
--------------------
This is heavily customised for my setup:
- PlaneWave L-350 mount (PWI4 HTTP API)
- Fixed dome geometry and offsets
- Known guide camera and fibre geometry
- Hard-coded site location assumptions
- Local directory layout for spectroscopy data
- PHD2 configuration and guiding behaviour

If you try to run this elsewhere, you will need to change things like:
- utils.py (site constants, slewing, time handling)
- Dome geometry
- ROI and fibre positions
- Paths and folder structure
- Hardware connection logic


How I actually use it
---------------------
Most nights are very simple:
1. Connect hardware and make sure everything is green
2. Load a target CSV into Batch Runner
3. Set a start time
4. Click Go

Everything else is already configured.


How to run it
-------------
Requirements:
- Python 3 (Windows)
- ASCOM (camera and dome)
- PHD2 running
- PlaneWave PWI4 running and reachable over HTTP
- Usual Python dependencies (see imports)

Start the app:
From the project root:
    python main.py

Everything runs from there.


Who this is for
---------------
- Me
- A couple of trusted colleagues
- Anyone curious how a real observatory automation system evolved

This is not polished software and not meant to be.


Final note
----------
If something looks odd, it is probably there because it fixed a real problem at 2am.
Stability beats elegance here.

Refactoring and cleanup can wait until observing season allows it.
