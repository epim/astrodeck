# Prepare an unattended night

These steps use the alternative interface. Open `#/next`, then review **SESSION**, **WEATHER** and **MONITOR** before arming a session. The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

Native guiding and native autofocus require `astrodeck_native`, which published releases do not include yet ([#630](https://github.com/epim/astrodeck/issues/630)). A connected device alone does not supply that engine.

A saved flow is only a plan. Complete a supervised run with the same devices, providers and shutdown settings before using it unattended. The procedures below describe configured behaviour, not a hardware safety certification.

## Before leaving the rig

1. Check the site, clock, optics, horizon and equipment connections. Confirm the connected safety monitor has a current reading. Resolve every run refusal and unsupported stage in [Flows and mosaics](flows-and-mosaics.md).

2. Confirm that autofocus, guiding and plate solving work with the selected providers. Native focus and guide need the separate engine; local ASTAP needs its separate installation and star database.

3. Review the intended park, warm-up and enclosure behaviour. A manual stop does not park the mount. Test the separate shutdown actions while present and verify the resulting equipment state in [Monitor](monitor.md).

4. Review forecast availability and any weather override. Forecast rain can prevent a dusk restart, forecast cloud does not, and missing forecast data does not supply a veto. When safety monitoring is enabled, a connected unsafe or stale safety monitor remains a separate blocker.

## Arm and check dusk resume

1. In **SESSION** > **GALLERY**, choose a dormant session with work remaining. Open its **MORE** actions and select **AUTO-RESUME OFF** to enable it. Read the no-monitor warning if one is shown.

2. The menu closes after the action. Reopen **MORE** and confirm **AUTO-RESUME ON**. You can also inspect the armed state in **SESSION** > **NOW**. Do not call the session running until the monitor reports that it has started.

3. To cancel future starts, open **MORE** again and select **AUTO-RESUME ON** to turn it off. The menu closes again; reopen **MORE** to verify **AUTO-RESUME OFF**. If recovery is already moving through its restart steps, use **STOP AUTO-RESUME** on the current-run screen and check the result.

## Respond to cloud hold

Cloud-hold and dawn coverage here combines source tracing with controlled automated tests, including injected conditions and clocks. It does not demonstrate a naturally occurring simulator cloud hold or a real unattended night. A completed simulator capture or flow is separate evidence. See the [procedure evidence](../../tools/docs/procedure-evidence.md) for the executed steps and limits.

A configured cloud hold uses image-based cloud checks. Read the actual hold reason and wait state. Depending on the configured recovery actions, the system can pause lights, run calibration work and re-acquire the target before resuming. Do not assume tracking remains on throughout every hold.

The engine bounds one cloud hold at 45 minutes. Its timeout check runs between guarded operations and frames, so this is not a promise of a hardware stop at an exact second. Expiry takes the safety-abort park path; warming follows the configured unsafe action. A second hold request does not restart the timer. Changing the **HOLD / RESUME** card's **Max hold** field does not change this engine constant.

If recovery fails or the safety state changes, inspect the log and equipment before retrying. A cloudy forecast, missing stars and an unsafe safety-monitor reading are different conditions; clearing one does not clear the others.

## When a mosaic repeatedly makes no progress

The mosaic held-pass rule, tracked as D-03, counts consecutive passes in which all attempted live panels fail centring or all defer for transient solves. It alerts at 3 held passes and sets the group aside for the night at 6. Two consecutive held passes with the same specific rig-side reason can set it aside sooner. Generic solve failure is not treated as a specific rig-side diagnosis.

This rule also applies when only one live panel remains. It counts passes, not minutes; do not treat the third-pass alert as an exact half-hour timer. Inspect the reported cause and fix it before resuming. A group set aside for the night is not complete, and a restart that night does not erase the set-aside.

## At dawn and the next start

A flow-derived plan requests park and camera warm-up when its observing window ends, including dawn. The wind-down closes the dust cover; roof or dome closure depends on the configured policy. If a funded shutdown calibration lane requests day darks, a normal night can take them between park and warm-up. An abort or unsafe trip does not run that day-darks lane.

For a continuing campaign, remaining work stays in the session ledger and a later window can resume it. Before that restart, forecast rain can veto it; cloud advice alone cannot. Missing forecast data gives no rain veto. Review the armed state after the night, and disarm it if the rig is no longer ready.

## Related

[Safety and automation](safety-and-automation.md) · [Weather](weather.md) · [Sessions and multiple nights](sessions-multi-night.md) · [Monitor](monitor.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
