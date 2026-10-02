# Set up a Windows rig

Choose this path when AstroDeck runs on the Windows computer connected to the equipment. You can use native device drivers, ASCOM Alpaca, the optional ASCOM COM host, or a NINA bridge; those are separate connection routes.

## 1. Start locally

1. Download the Windows executable using [install binary](install-binary.md), or complete the source installation in [getting started](getting-started.md).
2. Run AstroDeck as a standard user and open `http://localhost:8800` on that computer.
3. Complete the simulator walkthrough before assigning real devices.
4. Set your real observing site and imaging train when preparing for hardware. Keep its values private.

The executable's default configuration is under `%LOCALAPPDATA%/AstroDeck/config`. Keep it on NTFS or ReFS with the same owner account. Startup refuses an elevated server or unsafe private-directory permissions.

## 2. Choose who owns each device

1. If NINA will control the equipment, enable its Advanced API plugin and add a NINA backend in AstroDeck. Keep each physical device under one controller's ownership.
2. For an Alpaca server, add its host and port, then assign the devices it advertises.
3. For native USB or serial devices, use the hardware scan and inspect what it actually finds. Serial mounts also need the correct Windows COM port.
4. For traditional Windows ASCOM COM drivers, install the ASCOM platform and the device's driver, then use the COM-host route described in [its guide](../comhost.md).

Follow [equipment and profiles](equipment-and-profiles.md) to assign roles and connect. A driver being present does not prove a particular camera, mount or control works on your rig.

## 3. Check the capabilities you need

The native ZWO AM5 family driver uses USB serial; the recorded native movement/tracking check names an AM5N. Native ZWO cameras use ASICamera2, but native cooler and anti-dew controls are not implemented. The Player One adapter implements cooling and dew controls when the camera reports those capabilities. See the [hardware evidence and limits](https://epim.github.io/astrodeck/hardware.html).

Install ASTAP and its star database separately for standalone solving. Native guiding and native autofocus also need `astrodeck_native`; that extension is not in published releases yet (#630). PHD2 can provide guiding; NINA can supply the task providers its backend advertises. Check the selected provider in the UI.

## 4. Save and reconnect

1. Save the connected rig as a profile.
2. Activate that profile and verify each device's status before a hardware session.
3. On a later reconnect, activate the saved profile again. Avoid replacing a working arrangement with simulator or discovery shortcuts.
4. Add authenticated [phone or remote access](remote-access-and-roles.md) only after the local path works.

An activated profile becomes the boot profile. Review this before leaving powered hardware connected across a server restart.

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
