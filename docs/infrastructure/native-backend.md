# Native ASCOM / Alpaca Backend (the no-NINA path)

AstroDeck's **native** backend speaks **ASCOM Alpaca** (REST) directly — no NINA in
the loop. This is the vendor-neutral end state. This doc covers how it's wired, how
it was validated, and how to connect your **real gear**.

## How it connects

The native backend talks to an Alpaca server at `http://<host>:<port>/api/v1/<type>/<n>/...`.
A rig is a set of per-role device addresses:

```jsonc
// POST /api/connect/rig   (or save as a Profile, then activate)
{
  "primary": "native",
  "roles": {
    "camera":      { "backend": "native", "host": "127.0.0.1", "port": 32323, "dev_type": "camera",        "dev_num": 0 },
    "telescope":   { "backend": "native", "host": "127.0.0.1", "port": 32323, "dev_type": "telescope",     "dev_num": 0 },
    "focuser":     { "backend": "native", "host": "127.0.0.1", "port": 32323, "dev_type": "focuser",       "dev_num": 0 },
    "filterwheel": { "backend": "native", "host": "127.0.0.1", "port": 32323, "dev_type": "filterwheel",   "dev_num": 0 },
    "switch":      { "backend": "native", "host": "127.0.0.1", "port": 32323, "dev_type": "switch",        "dev_num": 0 },
    "safety":      { "backend": "native", "host": "127.0.0.1", "port": 32323, "dev_type": "safetymonitor", "dev_num": 0 }
  }
}
```

> Always supply explicit `host` / `port` / `dev_num` — the native backend has no
> built-in defaults (a missing port/dev_num errors at connect). Use
> `GET /api/discover/alpaca?host=<h>&port=<p>` (or UDP discovery via
> `GET /api/discover/native`) to enumerate an Alpaca server's devices first.

In the UI: **Settings → Connect** (the backend picker) builds this for you.

## Validated against OmniSim (2026-06-26)

The native path was validated end-to-end on the scope against the **ASCOM Alpaca
Omni Simulator** (`ascom.alpaca.simulators.exe`, a real Alpaca server) — chosen
because it's risk-free (no real gear, no single-client conflict with NINA):

- Connected all six roles (camera/telescope/focuser/filterwheel/switch/safety), `mode=alpaca`.
- **Slew** (goto RA/Dec → tracking), **capture** (1 s frame), **focuser move**, **filter change** all worked.

A saved profile **"OmniSim (native)"** is the easy way to re-run this: Settings →
Connect → activate it. OmniSim runs as a logon Scheduled Task (`OmniSim`); disable
that task to stop it.

## Connecting your REAL gear

Your scope's devices (`ASCOM.ASIMount`, `ASCOM.EAF` focuser, Wanderer Snowflake
filter wheel, `ASCOM.ASICAA` rotator, ZWO/Player One cameras) are **COM** drivers,
which the native (Alpaca) backend can't talk to directly. Three ways to bridge:

1. **`ascom-local` — the bundled COM host (recommended; the single-install path).**
   AstroDeck now ships its own COM→Alpaca bridge, so native COM connectivity works
   from **one install** — no separate bridge process to install or run. On the
   Equipment tab pick the **ASCOM (local)** device per role and Connect Rig; the
   server spawns a loopback-only, ephemeral-port COM host on demand and drives your
   COM drivers through the existing Alpaca client. Windows-only; install the server
   with `pip install -e "server/.[comhost]"`. Full operator/developer guide,
   lifecycle, troubleshooting, and the astrotown migration + on-ASCOM-box validation
   checklist: **[docs/comhost.md](../comhost.md)**.
2. **Native Alpaca drivers** (cleanest where a vendor ships one): ZWO and Player One
   ship Alpaca drivers for some products. Install those and they appear as Alpaca
   devices directly — no bridge.
3. **ASCOM Remote Server** (the general COM→Alpaca bridge; now superseded by
   `ascom-local` for single-install use): install it, add each COM driver as an
   Alpaca device (each gets a `dev_num`), and it serves Alpaca on a port (default
   `11111`). Point AstroDeck's native profile at `127.0.0.1:11111`. On astrotown
   this is the outgoing path — keep it until `ascom-local` is validated on the real
   rig (docs/comhost.md checklist), then retire it.

### ⚠️ The single-client caveat (retiring NINA)

Most ASCOM COM devices are **single-client**: only one app can hold a device at a
time. **NINA and AstroDeck cannot both be connected to the same physical device.**
So moving a device to AstroDeck-native means disconnecting it in NINA first. Plan
the cutover per device (or wholesale) when you're ready to move off NINA — the
native path is proven and waiting.
