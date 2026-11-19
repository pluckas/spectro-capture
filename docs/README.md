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
