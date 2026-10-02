# Review safety before moving the rig

These steps use the alternative interface. Open `#/next`, then review **SETTINGS** and the connected devices in **RIG**. The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

Automation acts on configured limits and available devices. A forecast is not a rain sensor, and a connected safety monitor is not proof that every stop, park, roof or warm-up action works with your equipment.

<a id="the-safety-monitor"></a>

<a id="sun-avoidance-daytime-guard"></a>

<a id="escalation-policy"></a>

## Review the protections

1. Open the safety settings at `#/settings/general/safetyTuning`. Inspect **LIVE SAFETY STATUS**, then **WHEN A LIMIT TRIPS** and **LIMITS THE RIG ENFORCES**. Check the action configured for each limit.

2. Confirm the monitor is connected and reporting safe before a run. A connected monitor that has not reported, is stale or reports unsafe can prevent recovery movement. With no monitor connected, auto-resume can still be armed after a warning; missing equipment is not a safe reading.

3. Review the Sun guard, local horizon, mount limits and planned target path before moving. Keep guards enabled for real equipment; simulator checks do not validate physical clearance.

<a id="altitude-floors-horizon-and-pier-limits"></a>

<a id="safety--automation"></a>

## Set the horizon

A graphical horizon editor is available at `#/sky/horizon`. Draw or adjust the obstruction line from the telescope's position, inspect the result, and save it. A camera scan is an estimate that needs review; it can include photographs of your surroundings.

<a id="warming-the-camera"></a>

<a id="meridian-flip-and-in-sequence-guards"></a>

## Check the end-of-run actions

Review **WARM RAMP** and the run's park, flip and enclosure actions. Configure only actions the connected devices can perform. A manual sequence stop does not park the mount. Test the separate shutdown actions while present before relying on them overnight.

<a id="alerts-and-the-dead-mans-switch"></a>

<a id="automatic-resume"></a>

## Alerts and unattended restarts

Use the alert settings and **ALERTS** screen to inspect delivery rather than assuming a configured destination received a message. Follow [Unattended nights](unattended-nights.md) before arming dusk resume.

## Related

[Weather](weather.md) · [Sessions and multiple nights](sessions-multi-night.md) · [Monitor](monitor.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
