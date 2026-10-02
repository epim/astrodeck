# AstroDeck User Guide

Start with a simulator image, then connect your equipment and build a night plan. AstroDeck can run standalone, accompany NINA, or provide planning beside ASIAIR; the available control paths depend on your backend.

## Start here

1. [Getting started](getting-started.md): install, connect the simulator, set a site and take one image.
2. Choose [a release binary](install-binary.md), [Docker](install-docker.md), a [Windows rig](windows-rig.md), or an [Orange Pi appliance](orange-pi-appliance.md).
3. Set [equipment and profiles](equipment-and-profiles.md) and [site and locations](site-and-locations.md).
4. Learn the [alternative interface](next-ui.md), then [build a Flow and a mosaic](flows-and-mosaics.md).
5. Review [unattended nights](unattended-nights.md) and [safety and automation](safety-and-automation.md) before leaving a run unattended.

## All guides

| Task | Guide |
| --- | --- |
| Take and inspect an exposure | [Capture](capture.md) |
| Focus the telescope | [Focus](focus.md) |
| Set up guiding | [Guiding](guiding.md) |
| Find and frame a target | [Sky Atlas](sky-atlas.md) |
| Build a classic plan | [Plan and sequences](plan-and-sequences.md) |
| Build a visual program or mosaic | [Flows and mosaics](flows-and-mosaics.md) |
| Reuse progress across nights | [Sessions and multi-night](sessions-multi-night.md) |
| Watch or recover a running session | [Monitor](monitor.md) |
| Understand weather and cloud holds | [Weather](weather.md) |
| Configure stop conditions and recovery | [Safety and automation](safety-and-automation.md), [Unattended nights](unattended-nights.md) |
| Use a phone, relay or limited account | [Remote access and roles](remote-access-and-roles.md) |
| Diagnose a failed step | [Troubleshooting](troubleshooting.md) |

## Find it by task

- Resume at dusk: [Unattended nights](unattended-nights.md).
- Recover after a restart: [Sessions and multi-night](sessions-multi-night.md).
- Understand missing coordinates or permission controls: [Remote access and roles](remote-access-and-roles.md).
- Diagnose a stalled run: [Monitor](monitor.md) and [Troubleshooting](troubleshooting.md).
- Export frames for processing: [Sessions and multi-night](sessions-multi-night.md).
- Find a Flow node or understand its ports: [Flows and mosaics](flows-and-mosaics.md).
- Distinguish classic from the alternative interface: [Alternative interface](next-ui.md).

## Who this is written for

Use the first-light path on a fresh simulator installation. For an existing rig, begin with the task you need and check its prerequisites before changing device or safety settings. Hardware support, a simulator result and an unattended field test are different kinds of evidence.

The [project overview](../overview.md) describes how the pieces fit together. The [quickstart](../quickstart.md) collects launch commands. The [project README](../../README.md) and [website](../../site/index.html) give the broader introduction.

These guides were checked against the source at the revision recorded in the [documentation evidence](../../tools/docs/README.md). Executed simulator procedures and source-only checks are identified separately there. Native autofocus and guiding require the optional native engine; its current release packaging gap is tracked in [#630](https://github.com/epim/astrodeck/issues/630). Standalone plate solving also needs ASTAP and its star database installed separately.

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
