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

## When guiding stops during a run

While the guide-RMS reject is set (the Standards setting is on by default), a run does not shoot a light frame with the guider stopped: the reject would refuse it. The run holds, tries twice to restart guiding, a minute apart, and then retries in spells ten minutes apart, up to six spells per target per night. If guiding never returns, the target is stopped and the log says why. With recovery turned off for the plan, the target stops at once. To shoot without guiding, set the reject to 0 in the plan or in Settings > Standards.

## Check that the mount keeps tracking after guide pulses

When guiding runs the star away on one pier side, for example after a meridian flip, the cause may be the mount rather than the calibration: the AM5 emulates guide pulses by replacing its tracking drive for the length of the pulse, and the tracking flag can read on while the RA drive is slow or stopped. Three records help tell the two apart.

- Flight recorder: When a calibration is discarded, the guide star is lost or re-acquired, five corrections in a row reach the pulse limit, or a frame carries the "guide pulses at the pulse limit" note, the native guider writes its last 60 cycles to `captures/guide/<night>/<time>-<event>.json` and logs one line naming the file. Each cycle holds the star position, the error, the pulses sent and the cycle time. No mount position is recorded.
- Tracking probe: After a discarded calibration or runaway, about a minute after each meridian flip, and once per guiding retry spell, AstroDeck watches the mount's own reported right ascension for 10 seconds with no motion and logs the rate. A tracking mount reads about 0 sidereal; a stopped one reads about 1. The line also shows the tracking flag, so a flag that says on beside a rate near 1 is the fault. Only the rate is logged, never a position.
- Pulse probe: `POST /api/mount/pulse-probe` (operator or admin) measures the same rate after rest, after one west pulse, after one east pulse and after a train of ten of each at the guide cadence, then at rest again. It needs no stars, so it runs in daylight, but the mount moves, so it is refused while a sequence is running or armed to start within ten minutes, while the guider is guiding, and while the mount is parked or slewing. It takes about three minutes; the result goes to the night log and to `captures/guide/<night>/<time>-pulse-probe.json`, and `POST /api/mount/stop` ends it at once. Run it on each pier side: a rate that stays near 0 through every stage means the pulses leave tracking alone, and a rate near 1 after a stage names the pulse that stopped it.

<a id="under-the-hood-routes"></a>

## Related

[Focus](focus.md) · [Capture](capture.md) · [Monitor](monitor.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
