# Watch a run and respond to a hold

These steps use the alternative interface. Open `#/next`, then open **MONITOR** > **LIVE** (`#/monitor/live`), or **SESSION** > **NOW** for the current run. The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

<a id="the-header-strip"></a>

<a id="progress"></a>

<a id="countdowns"></a>

<a id="last-frame-guiding-thermal"></a>

## Check what is happening

1. Read the run state and target before the preview. **RUN PROGRESS** reports captured progress and waiting states; its ETA can be absent until enough timing data exists and is withheld while paused.

2. Inspect **LAST FRAME**, guiding and **THERMAL** readouts. A displayed last frame can remain after a run ends; it is not evidence that a sequence is still running.

3. Open **LOG** for the recorded events and refusal reasons. Check timestamps and the current state before retrying a command.

<a id="pause-reads-honestly-here-too"></a>

## Pause or stop

**PAUSE** waits for an exposure already in flight. Keep the telescope and light path undisturbed until the pause takes effect. **RESUME** continues a paused run; during the pending pause the control can instead cancel it.

**STOP** ends the run through its abort path but does not park the mount. Read the confirmation and resulting state. Do not treat a browser timeout as proof that the rig ignored the stop; check the monitor and log before repeating it.

<a id="stall-detection--what-you-actually-see"></a>

<a id="stall-detection-what-you-actually-see"></a>

<a id="weather-operator-admin"></a>

## Investigate a hold or missing progress

Read the waiting reason first: dusk, a paused exposure, cloud hold, safety or a stalled task require different responses. Forecast cloud advice and image-based cloud detection are different inputs. Use [Weather](weather.md) and [Safety](safety-and-automation.md) before overriding a condition.

<a id="recovering-an-interrupted-run"></a>

<a id="monitor"></a>

<a id="weather-operator--admin"></a>

## Recover an interrupted run

When an interrupted run is recoverable, **RESUME INTERRUPTED RUN** shows the recorded progress. The resume button includes the saved frame counts. Review the interruption reason and equipment state before resuming. Starting over is a separate action.

## Related

[Sessions and multiple nights](sessions-multi-night.md) · [Unattended nights](unattended-nights.md) · [Weather](weather.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
