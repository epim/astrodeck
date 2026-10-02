# Troubleshooting

Start with the visible refusal and the most recent log entry. Keep the configuration and capture directories intact while diagnosing a problem.

## The server won't start

1. Run the launch command from a terminal so the error stays visible.
2. If the UI is missing in a source checkout, build it with `npm ci` and `npm run build` in `ui`, then restart the server.
3. If the port is already occupied, find the existing instance or choose another port with `--port`. Do not start a second controller against the same devices.
4. If a non-loopback bind is refused, create a named administrator using [auth setup](../auth-setup.md). Do not disable authentication to repair a network deployment.
5. On Windows, use a standard account and a privately owned NTFS/ReFS configuration directory. Permission failures happen before the listener opens.

## The simulator or rig won't connect

1. Check which interface you are using. The alternative interface's fresh Rig screen offers **RUN THE SIMULATOR**; classic **Equipment** offers **Simulator**.
2. For real equipment, open the configured driver and check its address or USB/serial connection.
3. In classic **Equipment**, inspect **Link Status** and the per-device error.
4. Reconnect a saved rig by activating its profile. A simulator success does not establish a hardware driver's behavior.

See [equipment and profiles](equipment-and-profiles.md) for assignment and connection steps.

## Native autofocus, guiding or plate solving is unavailable

Native autofocus and native guiding require `astrodeck_native`; current published releases do not contain it (#630). Check the selected task provider before treating this as a device fault. PHD2 can provide guiding; NINA can supply the task providers its backend advertises.

For ASTAP, install both its executable and a suitable star database. Set `ASTAP_PATH` before server launch when the executable is outside the discovery paths; `ASTAP_DATA` can identify the database directory. Check focal length and sensor details before retrying a solve.

## The sky or dawn time looks wrong

1. Check that the intended saved site is active, not merely stored in the library.
2. Check hemispheres and the controller's time.
3. Read the displayed dark window carefully. The known next-dawn presentation issue (#640) can make the countdown misleading outside the current dark interval; do not treat it as proof that the next day is continuously dark.
4. In Atlas, schematic sky is available offline. Survey imagery requires tiles fetched for your own use; AstroDeck distributes no DSS2 survey pack.

<a id="the-run-looks-stuck-stall-diagnosis"></a>

## The run looks stuck

Open the [Monitor guide](monitor.md), compare the current run state with the latest log entries, and inspect device connectivity. A paused run, a solve, a filter change and a failed exposure are different states. Read the reason before stopping or restarting anything. Do not repeatedly issue manual capture or mount commands into a run that owns those devices.

<a id="remote-access-relay-issues"></a>

## Remote access issues

Confirm that local sign-in works first, then check the relay connection. Some admin writes are intentionally local-only. A viewer cannot use operator controls, and an operator cannot change backend or site configuration. See [remote access and roles](remote-access-and-roles.md).

<a id="related"></a>

## Where the logs are

The default `/api/logs` response is a recent-event buffer. A `night` query reads a persisted night, and `level` filters results. Durable night logs are written beneath the capture directory in `logs/<night>.jsonl`. Console logs depend on your launcher or service. Preserve the relevant timestamp and error before the live buffer rolls over; redact secrets and private location details before sharing anything.

<a id="night-mode-and-screen-brightness"></a>

## Config got into a bad state

Stop the server and back up its configuration directory before recovery. Source installs default to `server/config`; binaries use their per-user data directory, and services can override it with `ASTRODECK_CONFIG_DIR`. Keep profiles, accounts and session data together. For a lost administrator password, use `create-admin` against that same directory rather than deleting configuration files.

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
