# Check and adjust focus

These steps use the alternative interface. Open `#/next`, then open the focuser device from **RIG** > **DEVICES**. The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

Native guiding and native autofocus require `astrodeck_native`, which published releases do not include yet ([#630](https://github.com/epim/astrodeck/issues/630)). A connected device alone does not supply that engine.

<a id="the-focuser-panel"></a>

<a id="focus"></a>

## Focus by hand

1. Check that the focuser and imaging camera are connected. In **FOCUS FRAME**, select **SINGLE** for one measurement or **LOOP** to repeat frames. Focus frames use their own exposure settings.

2. Adjust position with the inward and outward step buttons. Start with a step size suited to your focuser, watch the image, and reduce the move as stars sharpen. Use **GO TO POSITION** and **GO** for an absolute position supported by the device.

3. Use **HALT** to stop focuser motion and **STOP** to end the focus-frame loop. Read the movement status before issuing another move.

<a id="autofocus"></a>

<a id="reading-the-v-curve"></a>

<a id="which-engine-focuses"></a>

<a id="the-verdict"></a>

## Run autofocus

1. Review **AUTOFOCUS PROVIDER** and the reason shown for its selection. A backend can provide its own autofocus; selecting the native provider still requires the missing release engine described above.

2. Open **Autofocus settings** and review **STEP SIZE**, **STEPS EACH SIDE**, **EXPOSURE**, **GAIN** and **BINNING**. Read the proposed sweep summary and provider note before starting. A backend provider can own its exposure, gain and binning; when the note says those values are not sent, configure them in that backend.

3. Select **AUTOFOCUS NOW**. Wait for the result and inspect the star image and measured curve. Read any refusal or failure advice before retrying; an unavailable engine cannot be fixed by changing sweep settings.

<a id="bahtinov-mask-focusing"></a>

## Use a Bahtinov mask

Fit the mask, then use **BAHTINOV MODE** and its **START** control to measure the diffraction pattern. Adjust focus while watching the result, stop the mode when finished, and remove the mask before imaging.

<a id="routes"></a>

## Related

[Capture](capture.md) · [Plan editor](plan-and-sequences.md) · [Flows and mosaics](flows-and-mosaics.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
