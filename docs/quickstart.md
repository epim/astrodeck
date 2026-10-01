<a id="astrodeck--quickstart"></a>

# AstroDeck quickstart

Use the [getting-started walkthrough](guide/getting-started.md) to install AstroDeck, connect an isolated simulator, set a site and take one preview frame. It explicitly opens the alternative interface at `/#/next`; the bare root still opens classic.

## 1. Install and run the server

Choose [a binary](guide/install-binary.md), [Docker](guide/install-docker.md), or the source recipe in [getting started](guide/getting-started.md). Source installs need Python 3.11 or newer and a UI build. The first listener is local-only.

<a id="2-your-first-session--the-simulator"></a>

<a id="guiding-with-phd2"></a>

<a id="set-your-site-recommended-even-in-sim"></a>

## 2. Your first session: the simulator

Follow [the simulator procedure](guide/getting-started.md#2-open-the-alternative-interface). Use a fresh configuration with no real hardware connected. One successful preview checks the basic browser/server/camera path; it does not certify native guiding, autofocus or an unattended night.

<a id="3-connecting-real-gear-ascom-alpaca"></a>

## 3. Connecting real gear

Declare a backend, assign its devices, connect, and save a profile using [equipment and profiles](guide/equipment-and-profiles.md). For platform-specific setup, use [Windows rig](guide/windows-rig.md) or [Orange Pi appliance](guide/orange-pi-appliance.md).

<a id="4-running-alongside-nina"></a>

<a id="plate-solving-with-astap"></a>

<a id="rebuilding-the-ui-only-if-you-change-it"></a>

<a id="try-the-bridge-without-a-nina-install"></a>

## 4. Running alongside NINA or ASIAIR

NINA's Advanced API is a software backend. ASIAIR owners can use planning and weather beside their existing workflow; the optional libasi backend remains experimental and is not a claim of tested feature parity. Standalone native or Alpaca setups are an equal route.

## 5. Where things are saved

Source installations default to `server/config` and the repository-root `captures` directory; binary installs use per-user storage. `ASTRODECK_CONFIG_DIR` and `ASTRODECK_CAPTURE_DIR` override those paths. See [binary storage](guide/install-binary.md#where-your-data-goes).

## Troubleshooting

Use [troubleshooting](guide/troubleshooting.md), [authentication setup](auth-setup.md), or the [guide index](guide/README.md).
