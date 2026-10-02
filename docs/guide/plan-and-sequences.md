# Review and run a sequence plan

These steps use the alternative interface. Open `#/next`, then open **SESSION** > **FLOWS**, then **PLAN EDITOR** on a tablet or desktop. The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

The plan editor is a separate way to inspect and edit the sequence plan. A flow run compiles its graph again; changing the plan editor does not rewrite a flow's stages. Use the flow editor for changes you want to keep in a flow.

<a id="building-a-plan"></a>

<a id="targets"></a>

<a id="steps-per-filter"></a>

<a id="the-plan-panel--identity-library-saveimportexport"></a>

<a id="scheduling-per-target"></a>

## Build or load a plan

1. Set **PLAN NAME**, or choose **LOAD** in the saved plan list. Use **IMPORT** for a saved plan file. Check the loaded target list before proceeding.

2. Use **ADD TARGET** to add a target. Review its coordinates and the **Centre on this target** and **Autofocus first** choices. Leave autofocus off when its provider is unavailable; centring needs a working plate solver.

3. For each capture step, choose its frame type and filter, then set **EXPOSURE**, **GAIN** and **COUNT**. Review binning and any calibration-frame settings before running.

4. Review the automation, schedule and instruction sections. Remove any actions that require equipment or providers you do not have. Save with **SAVE**, or use **SAVE AS** to keep a second plan.

<a id="running-a-sequence"></a>

<a id="plan--sequences"></a>

<a id="automation"></a>

<a id="combining-conditions"></a>

## Run and stop

1. Read the preflight result and resolve refusals before starting. If a run later fails, **HOW TO FIX** explains a reported problem when that control is offered.

2. Select **START SEQUENCE**, then press again while **TAP AGAIN TO START** is shown. Watch [Monitor](monitor.md). This starts the loaded plan; use a session resume action to continue recorded multi-night progress.

3. **PAUSE** finishes an exposure already in flight before stopping at a frame boundary. During that interval **CANCEL PAUSE** keeps the run going; after the pause takes effect, **RESUME** continues it.

4. **STOP** requires a second press while **TAP AGAIN TO STOP** is shown. A manual stop does not park the mount. Check the resulting status and log. **RE-RUN PLAN** starts over; use the recovery or session-resume action when you intend to retain progress.

<a id="count-modes-and-quotas-multi-night"></a>

<a id="the-target-lifecycle"></a>

<a id="jumping-between-targets"></a>

## Keep progress across nights

Use [Sessions and multiple nights](sessions-multi-night.md) for accepted-frame quotas, dormant sessions, updating a saved session from a changed plan and resuming it. Use [Flows and mosaics](flows-and-mosaics.md) for panel order and pass rotation.

<a id="instructions-when-this-then-that-rules"></a>

<a id="preview-what-these-rules-do"></a>

<a id="routes"></a>

## Related

[Capture](capture.md) · [Safety and automation](safety-and-automation.md) · [Unattended nights](unattended-nights.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
