# Build a flow and a mosaic

These steps use the alternative interface. Open `#/next`, then open **SESSION** > **FLOWS** (`#/session/flows`). The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

A flow is a saved graph of stages and wires. The compiler translates it into a sequence plan; its checks and the connected rig determine what can run.

Native guiding and native autofocus require `astrodeck_native`, which published releases do not include yet ([#630](https://github.com/epim/astrodeck/issues/630)). A connected device alone does not supply that engine.

## Create and review a flow

1. Select **+ NEW FLOW**. Choose **Deep-sky target**, **Best of several** or **EAA quick look**, then enter a catalogue target. Select only the automation you intend to use.

2. Select **GENERATE FLOW** to save and open the generated graph. **START BLANK** instead creates an unwired target and capture stage, so it still needs a target, settings and wires before it is runnable.

3. Inspect every stage and the compiler checks. The generator includes dusk waiting and autofocus even with **Guiding** turned off. Flow-derived targets request autofocus even if its block is deleted. Without an available autofocus provider, use [manual capture](capture.md) or a [plan](plan-and-sequences.md) with its target autofocus option off for your first run.

4. To remove a stage, select it, choose **DELETE STAGE**, then **CONFIRM DELETE**. Its incoming and outgoing wires are also deleted, and this surface has no undo. Reconnect the intended flow path and review the compiler result; deletion does not create a bypass or disable engine defaults such as target autofocus.

5. For a brief simulator check once the required providers work, select **CAPTURE LOOP** and set **Exposure** to 1 second, **Count** to 1 and **Integration goal** to 0. The goal is a planning estimate, not a run quota. Review the dusk window, target visibility and any other capture stages too; a small frame count does not skip waiting, autofocus or centring.

6. Select **SAVE**, wait for the saved state, then read the checks and any run refusal. For a new flow, select **RUN**, then **CONFIRM RUN**. An existing dormant session instead offers continuation with its flow name and recorded progress. Use [Monitor](monitor.md) to follow the actual run. Saving or generating a flow has not yet captured anything.

After a completed flow, the toolbar can still show **STOP** ([#647](https://github.com/epim/astrodeck/issues/647)). Verify that the run is idle in [Monitor](monitor.md), then reload the page before starting again. Do not press the stale **STOP** to clear it: it can start another run.

## Frame a 2 by 2 mosaic

1. Select the **TARGET** stage, then **FRAME ON SKY**. The framing dialog edits this stage locally; it does not replace another sky framing session.

2. Choose the target in **WHERE**. Confirm its **NAME**, **RA** and **DEC** before adjusting the grid.

3. Confirm the telescope and camera optics are configured. Under **GRID**, select **MATCH CAMERA** to record the live camera field in this target. A known live field is not necessarily recorded in the draft yet. Set **COLS** to 2, **ROWS** to 2 and **OVERLAP** to the percentage you intend to keep between panels, such as 25. Check the drawn footprint.

4. Under **ANGLE**, choose an explicit camera angle for the mosaic. **ANY ANGLE** is insufficient for a multi-panel grid. Use **ROTATE TO** with an available rotator, or **CAMERA FIXED AT** for a camera you align by hand, and enter the intended position angle. A simulator example with its rotator can use 0 degrees. Review **CENTRING**, its tolerance and retry count. Local ASTAP requires a separate installation and compatible star database.

5. Under **PANELS**, review **ORDER** and each panel's **SHOOT** selection. A skipped panel is not a completed panel.

6. Under **RUN**, choose **Rotate panels every pass** if the sequence should revisit panels in turns. This manages the pass-loop wire. With rotation off, the lane works panel by panel. Review **PASSES PER VISIT** and **AT LEAST** when rotation is enabled.

7. Select **DONE**, wait for the compile, and review its checks. If a changed framing would restart recorded counts, read the confirmation before choosing **RE-FRAME**. The old saved subs stay on disk, but the affected layout starts counting from zero. Running flows and read-only examples open framing for inspection rather than editing.

## Continue on another night

New target blocks count accepted subs. Review saved progress and [Sessions](sessions-multi-night.md) before changing framing or restarting a campaign.

## Node reference

Read **CHECKS**, **NOT HONOURED BY A RUN** and **BEFORE YOU RUN** after each change. The table distinguishes what a block represents from what the current engine actually uses. Accepting a reported loss does not implement the missing behaviour.

| Node | Current behaviour and limit |
| --- | --- |
| **DUSK WINDOW** | Sets the observing window. Sun-based timing needs a configured site. A single-night run and a campaign have different resume behaviour; the 30-night choice is not an enforced night-count limit. |
| **TARGET** | Owns target identity, coordinates, camera angle, centring and mosaic grid. Its panel lane can run panel by panel or rotate by pass. |
| **SAFETY MONITOR** | Represents the unsafe watch. The engine reads the connected monitor and safety settings even without this block; the card does not replace the configured source or stale-reading policy. |
| **CLOUD WATCH** | Supplies cloud event wires. The detector uses captured-frame evidence; the card's source, cloud-percentage and clear-time controls do not configure a forecast or a new physical sensor. Read the reported losses. |
| **DOME CONTROL** | Declares dome intent. The node's binding and timeout do not reach the engine. Unsafe closure depends on safety configuration; a promised closure the rig cannot honour blocks a dome run. |
| **FLAT PANEL** | Represents panel readiness for calibration wiring. It is not a working automatic flat-acquisition stage; the calibration queue's flat leg is not implemented. |
| **SLEW + CENTER** | Legacy block: saved flows can still contain it, but it is not offered in the palette. Set centring on TARGET; the legacy block's tolerance, retry count and solver choice are not used. |
| **AUTOFOCUS** | Flow-derived targets request autofocus at their start with or without this block. Sweep geometry comes from the focuser, not this card's method, step size or samples. A working provider is required. |
| **GUIDE** | Its presence enables guiding. Provider, settle and dither settings come from the guider configuration, not these card fields. |
| **CAPTURE LOOP** | Captures the selected filter, exposure, gain, binning and frame count. Integration hours are a planning estimate, not a run quota; absolute reject-HFR on the card is not the rig's grading rule. |
| **FILTER CYCLE** | Interleaves the configured wheel-slot table over passes. Capture settings reach the plan; the card's absolute reject-HFR setting does not. |
| **DUSK FLATS** | The **Flat panel** method runs ([#603](https://github.com/epim/astrodeck/issues/603)): once per night, after the cooling wait and before the first light, it shoots the card's count of flats on each filter (the plan's filters, or every filter in the wheel), at the lights' gain and binning, with the dust cover shut and the lamp lit, and exposure metered to the ADU target. A filter whose flats the library already holds fresh is skipped, so a restart does not reshoot. A filter whose exposure will not meter to the ADU target is skipped, and the log says why. It needs a connected flat panel; without one the run says so once and goes on without flats. The Sun window is not waited for (a panel needs no sky), the panel is driven as a dust-cover panel, and flats are taken at the rotator's current angle, not each mosaic panel's. A cover that was open before the flats is opened again after them; a cover that was shut stays shut. **Translucent lens cap** and **Twilight sky** are not run yet: a flow that uses them warns that the run will take no flats. |
| **CALIBRATION QUEUE** | Can take dark frames during a cloud hold. A shutdown-complete wire can take day darks after a normal night. Queue ordering, bias and flat legs are not implemented. |
| **TARGET POOL** | Supplies candidate targets to the scheduler. Completed quotas are skipped across nights; targets set aside still need frames. Frame counts end the campaign, not its estimated integration hours. |
| **CONDITION** | Creates a supported trigger and threshold. Read compile losses for unsupported triggers; its window and fire-once fields are not carried. Rules are evaluated at frame boundaries. |
| **HOLD / RESUME** | A pause event invokes the self-releasing cloud hold, which keeps dawn and safety checks active. The engine uses its 45-minute bound; the card's Max hold and other policy fields do not override it. |
| **NOTIFY** | A supported event can call the engine's notification action. Sink routing comes from configured alerts; message text and severity on this node are not carried. |
| **REFOCUS** | A supported event wire invokes autofocus at a frame boundary. The node's boundary field does not change that timing. |
| **PARK + CLOSE** | Represents scheduled shutdown. Flow-derived plans already request park and warm, and wind-down closes the dust cover. Dome closure needs the configured policy. The node's Hold cold setting is not honoured. After-shutdown darks come from a calibration step wired to `on_shutdown_complete`, not this setting ([#646](https://github.com/epim/astrodeck/issues/646)). |
| **ABORT + PARK** | A supported event invokes abort and park. Warming depends on the configured unsafe action, not the card alone. This is different from the operator's manual STOP, which does not park. |
| **SESSION REPORT** | The engine writes a report for every run, even without this block. Its format and destination fields do not override the engine's report location. |

The current shutdown warning can incorrectly say that no darks are taken after shutdown ([#646](https://github.com/epim/astrodeck/issues/646)). A wired shutdown-complete calibration lane can take day darks after a normal night, between park and warm-up. See [Unattended nights](unattended-nights.md) for the abort and unsafe-trip limits.

Flow wires carry the sequence path; event wires express conditions and actions. Connect compatible port kinds. A mosaic pass loop is an event wire from the last stage's pass output to the owning target's next-panel input, not an arbitrary cycle in the sequence path.

## Related

[Plan editor](plan-and-sequences.md) · [Unattended nights](unattended-nights.md) · [Monitor](monitor.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
