# Start and check guiding

These steps use the alternative interface. Open `#/next`, then open the guider device from **RIG** > **DEVICES**. The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

Native guiding and native autofocus require `astrodeck_native`, which published releases do not include yet ([#630](https://github.com/epim/astrodeck/issues/630)). A connected device alone does not supply that engine.

<a id="provider--phd2-fallback"></a>

<a id="provider-phd2-fallback"></a>

## Choose a provider

Review the guide provider and its availability reason. AstroDeck has native, PHD2 and NINA guider paths. PHD2 must be running and connected to its equipment when selected; a saved provider choice is not proof that guiding has started.

Alongside NINA, use the available backend capabilities. Alongside ASIAIR, you can plan and check weather in AstroDeck while capturing and guiding in the ASIAIR app. Standalone control depends on supported devices and installed providers. These are separate operating choices, not a claim of an official ASIAIR API.

<a id="control"></a>

<a id="guide-error-scatter-and-the-live-graph"></a>

<a id="guiding"></a>

## Start guiding

1. Confirm the guide camera can see stars and the mount is tracking. Review **EXPOSURE** and the calibration state in the guider device.

2. Select **LOOP + PICK STAR**. Wait for the provider to report guiding, then inspect the star and error readouts. Do not infer guiding from a connected camera alone.

3. Use **DITHER NOW** only while guiding is active. Review **SETTLE PX**, **SETTLE S** and **TIMEOUT S** if you need to override the guider defaults.

4. Select **STOP GUIDING** to stop manual guiding. During a sequence, use its run controls and inspect the log before changing guider settings.

<a id="guiding-assistant"></a>

<a id="advanced-measurements-and-per-setting-apply"></a>

<a id="guide-tuning"></a>

<a id="same-night-rms-native-vs-phd2"></a>

<a id="advanced--measurements-and-per-setting-apply"></a>

## Measure before tuning

The **GUIDING ASSISTANT** requires the native guider. Read what it will measure. **ALSO MEASURE MOUNT SLACK (MOVES THE SCOPE)** adds physical mount movement, so enable it only when that movement is appropriate. Start with **RUN GUIDING ASSISTANT**, then review its results before **APPLY RECOMMENDED SETTINGS** or **APPLY SELECTED**.

Compare guiding measurements under comparable conditions. A different night, star field, exposure or provider is not a controlled before-and-after test. The live RMS value describes the reported guide error, not a guarantee of image sharpness.

<a id="under-the-hood-routes"></a>

## Related

[Focus](focus.md) · [Capture](capture.md) · [Monitor](monitor.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
