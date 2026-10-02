<a id="4-set-your-observing-site"></a>

<a id="where-things-live"></a>

# Getting started

Start with a simulator image, then choose how to connect your own equipment. AstroDeck can run standalone, work alongside NINA, or provide planning and weather beside an ASIAIR setup. Hardware control depends on the selected backend; the optional ASIAIR backend remains experimental.

## 1. Install and run the server

Choose [a release binary](install-binary.md), [Docker](install-docker.md), or the source setup below. A source checkout needs Python 3.11 or newer and a built UI; it does not include a ready-made `ui/dist` directory.

From the repository root on Windows, with Node.js 24 and Python available:

```powershell
cd ui
npm ci
npm run build
cd ../server
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m astrodeck
```

On Linux or macOS, use `.venv/bin/python` for the last two commands. The initial server listens at `http://localhost:8800`, on this computer only. Leave its terminal open; Ctrl+C stops it. Phone access needs authentication and a network deployment: see [remote access](remote-access-and-roles.md).

## 2. Open the alternative interface

1. Open `http://localhost:8800`. The bare address opens the classic interface.
2. Open `http://localhost:8800/#/next` for this walkthrough. The alternative interface has a different navigation layout. Use `#/classic` to return to classic.
3. Open `http://localhost:8800/#/rig/devices`. On a fresh, disconnected simulator installation, press **RUN THE SIMULATOR**. Wait for the device rows to report connected.

This procedure is for a fresh installation with no real equipment connected. Starting the simulator replaces the active device arrangement; do not use it to diagnose a running real session.

<a id="3-connect-the-simulator-rig"></a>

## 3. Set a site before planning

1. Open `http://localhost:8800/#/settings/general` and choose **SITES**.
2. Press **+ NEW SITE HERE**. Fill **Name**, **Latitude**, **Longitude**, and **Elevation (m)**. Coordinates use positive magnitudes with hemisphere selectors.
3. Press **SAVE SITE**, then select the radio control beside that saved site to make it active. Saving a library entry alone does not activate it.

Use the telescope's location for real observing. For an isolated tutorial, a clearly named public synthetic site is sufficient. Keep your real location out of shared screenshots and support logs. [Site and locations](site-and-locations.md) explains both interfaces.

<a id="2-first-launch"></a>

<a id="5-take-your-first-image"></a>

## 4. Take your first image

1. Open `http://localhost:8800/#/rig/capture`.
2. Set **EXPOSURE** to `1` second and **COUNT** to `1`. Leave the other camera settings alone for this first test.
3. Keep **SAVE FITS TO LIBRARY** off for a disposable preview.
4. Press the capture button. Its label includes the count, exposure and filter, beginning with **CAPTURE**.
5. Wait for the preview. It is a simulated image, not a photograph from connected equipment.

Native guiding and native autofocus require `astrodeck_native`, which published releases do not include yet (#630). A simulator connection and one exposure do not prove those engines are installed or that an unattended hardware session is ready. Standalone plate solving needs a separately installed ASTAP executable and star database.

## Next steps

Connect [equipment and profiles](equipment-and-profiles.md), configure a [Windows rig](windows-rig.md), or read the [Orange Pi appliance guide](orange-pi-appliance.md). Continue with [capture](capture.md), [Flows and mosaics](flows-and-mosaics.md), and [unattended nights](unattended-nights.md).

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
