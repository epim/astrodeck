<a id="astrodeck--overview"></a>

<a id="focus-solve-guide"></a>

# AstroDeck overview

AstroDeck puts imaging control, planning and monitoring in a browser. The server runs beside the equipment; the phone or desktop browser is the interface. Start with [the user guide](guide/README.md) or [a simulator image](guide/getting-started.md).

## Philosophy

<a id="backends-in-detail"></a>

<a id="configuration-configpy"></a>

<a id="vendor-neutrality-is-the-whole-point"></a>

### Vendor neutrality

Choose standalone native or ASCOM Alpaca control, a NINA Advanced API bridge, or planning and weather alongside ASIAIR. Each route has different device and task-provider capabilities. The optional ASIAIR backend is experimental; a driver implementation is not evidence of hardware validation.

<a id="sequencing--automation-sequence"></a>

<a id="sky-atlas-catalog"></a>

<a id="the-open-path-is-the-default-nina-is-the-on-ramp"></a>

### Be specific about what is installed

Native guiding and native autofocus depend on `astrodeck_native`, which published releases do not yet package (#630). Standalone ASTAP solving needs a separate executable and star database. The Atlas has an offline schematic sky; survey tiles can be fetched for personal use, but AstroDeck distributes none.

<a id="the-big-picture"></a>

## Architecture

| Component | Job |
|---|---|
| `server/astrodeck` | Python server, API, device orchestration, planning and sequence execution |
| `ui/src` | Browser interface; classic at the bare root, alternative at `#/next` and its hub routes |
| `native` | Rust crates for image analysis, autofocus, guiding and related numerical work |
| `relay` | Optional outbound remote-access transport |
| `supervisor` | Supervised source-release switching and rollback |
| `orangepi5` | Board Wi-Fi provisioning and recovery services |

<a id="the-device-abstraction-devicesbasepy"></a>

### Device and task boundaries

Device adapters expose roles such as camera, telescope and focuser. Profiles store a rig's configuration. Task providers determine which engine handles operations such as autofocus or solving, independently of the camera driver's brand.

<a id="the-ui-uisrc"></a>

### Browser and server

The browser receives status and previews; the server owns the run. A browser disconnect is not a stop command. Sign-in capabilities control viewing, operation, configuration and administration. Some changes require direct access to the controller and are blocked over the relay.

<a id="imaging-pipeline-imaging"></a>

<a id="polar-alignment-polarsessionpy"></a>

### Persistent state

Configuration, profiles and accounts live under the configuration directory. Captures and durable night logs live under the capture directory. Deployment environment variables can put both outside the versioned application directory so an update can replace code without replacing state.

<a id="honest-software-for-a-technical-audience"></a>

<a id="project-status-honest"></a>

## Project status

Check the [hardware page](https://epim.github.io/astrodeck/hardware.html) for named device checks and limitations, and [release notes](https://github.com/epim/astrodeck/releases) for the selected build. Simulator procedures in this guide are not certification of a complete unattended hardware session.

The top-level project licence is Apache-2.0. Native components and third-party materials have their own terms; see [third-party notices](../THIRD-PARTY-NOTICES.md).
