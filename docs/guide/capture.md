# Take and inspect a frame

These steps use the alternative interface. Open `#/next`, then open **RIG** > **CAPTURE** (`#/rig/capture`). The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

<a id="exposure-controls"></a>

## Take a saved still frame

1. Connect a camera in [Equipment and profiles](equipment-and-profiles.md). Select **STILL · FRAMES**. Wait for any sequence or other camera task to release the camera.

2. Set **EXPOSURE**, **GAIN**, **COUNT** and **BINNING**. Set **FILTER** when a wheel is available. For a first check, use one short exposure and settings appropriate to the connected camera.

3. Choose the frame type and enable **SAVE FITS TO LIBRARY** if you want a file. Preview-only work and a saved capture are different outcomes.

4. Press the **CAPTURE** button whose suffix shows the count, exposure and filter. Wait for exposure and readout to finish, then inspect the result. **STOP** ends an active manual capture or loop.

5. If the result was not saved and the server still holds the frame, **SAVE TO GALLERY** can save that exposure. **RE-SHOOT AND SAVE** takes a new exposure instead. Read any buffer or save refusal before retrying; a preview alone is not proof that a FITS file is on disk.

<a id="reading-the-live-preview"></a>

<a id="pixel-peeping-the-magnifier-and-real-sensor-pixels"></a>

<a id="capture"></a>

<a id="live-view-live-stacking"></a>

<a id="what-the-alignment-badge-is-telling-you"></a>

<a id="under-the-hood-routes"></a>

## Inspect the preview

Review the last frame, measured star quality and capture readouts before increasing exposure length. A preview is a display rendering; keep the FITS file for processing. The save switch determines whether manual frames enter the capture library.

Use **LOOP** for repeated frames while checking framing or focus. Use **LIVE VIEW** for a running stack, and **STOP** to end the capture loop. Trail rejection changes the live stack; it is not a promise to remove every satellite trail from saved files.

<a id="filter-wheel"></a>

<a id="blackout-slots"></a>

<a id="sensor-gain"></a>

<a id="cooler"></a>

<a id="satellites-and-aircraft"></a>

## Dark and bias frames

Select the appropriate frame type and physically block light as required by your equipment. **MATCH LAST LIGHTS** copies the most recent light-frame settings when that snapshot is available. Check exposure, gain, offset, binning and cooler target before shooting calibration frames.

<a id="recording-where-each-photo-points-plate-solving-into-the-file"></a>

## Record a solved field

Saved-light plate solving is configured with the optics settings. It requires an available solver; local ASTAP and its compatible star database are separate prerequisites. A failed or absent solve is not evidence that a file has measured pointing.

<a id="guide-camera-preview"></a>

## Guide-camera checks

Open the guider device for its camera preview and guiding state. The imaging camera and guide camera are separate roles; use [Guiding](guiding.md) for the provider and calibration checks.

## Related

[Focus](focus.md) · [Guiding](guiding.md) · [Sessions and downloads](sessions-multi-night.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
