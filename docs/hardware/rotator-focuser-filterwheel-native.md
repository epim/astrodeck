# Native control: ZWO CAA (rotator), ZWO EAF (focuser), Wanderer devices

**Date:** 2026-07-20 (recon on astrotown; companion to `zwo-am5-lx200-protocol.md`)
**Goal:** determine how to control the imaging-train accessories natively (no vendor
apps, no ASCOM), extending the AM5N native-driver campaign to the rotator, focuser,
and filter wheel.

## Inventory (verified on the live rig, 2026-07-20)

| Device | USB identity | Transport | Present |
|---|---|---|---|
| ZWO AM5N mount | `VID_03C3&PID_4001&MI_00` → **COM3** | USB CDC serial, LX200 (see mount doc) | ✓ |
| **ZWO CAA** (rotator) | `VID_03C3&PID_1F20` | **USB HID (vendor-defined)** → ZWO SDK | ✓ |
| **ZWO EAF** (focuser) | `VID_03C3&PID_1F10` | **USB HID (vendor-defined)** → ZWO SDK | ✓ (fw 3.3.8) |
| **Wanderer Snowflake filter wheel** | `VID_1A86&PID_7523` (CH340) → **COM8** | USB CDC serial, ASCII | ✓ |
| Wanderer Rotator | (CH340 when attached) | USB CDC serial, ASCII | ✗ not on bus (ghost entries COM4/COM7) |
| POA Poseidon-M Pro (imaging cam) | `VID_A0A0&PID_5715` | Player One SDK | ✓ |
| ZWO ASI220MM Mini (guide cam) | `VID_03C3&PID_2209` | ASICamera2 SDK | ✓ |

No NINA running during recon; COM8 uncontended. ASCOM RemoteServer + Alpaca simulator
+ AstroDeck python tree were the only astro processes.

## ZWO CAA + EAF: the SDK is the native path (not a wire protocol)

The CAA and EAF are **HID devices** — there is no serial wire to speak. ZWO's own
ASCOM drivers and NINA both drive them through ZWO's closed-but-redistributable SDK
DLLs, which we can call directly via ctypes:

- On astrotown: `C:\Program Files\ASIStudio\EAF_focuser.dll` (1.2 MB) and
  `C:\Program Files\ASIStudio\CAA_SRC.dll` (73 KB); ASCOM wrappers
  `CAA_ASCOM_x64.dll`/`x86` under `Common Files\ASCOM\ZWO\`.
- **Redistribution proof:** NINA bundles `EAF_focuser.dll` in its own install
  (`N.I.N.A...\External\x64\ASI\`), and INDI ships the Linux `.so` equivalents in
  `indi-3rdparty`. Same terms available to us.
- NINA does NOT use the ASCOM path for the EAF — the ZWO ASCOM logs on astrotown
  show only polling, never a Move, across real imaging nights. SDK-direct is the
  normal integration route.

**Observed device semantics (from the ASCOM trace logs `Documents\ASCOM\ASICAA` /
`ASIEAF`):**
- CAA: position reported in **degrees** (e.g. `290.7`) with a separate
  **MechanicalPosition** (`290.77`) → the driver layer maintains a sky-vs-mechanical
  sync offset; `CanReverse=True`; connect flow sets `Reverse False` then polls.
- EAF: position in **steps** (e.g. `18128`), firmware `3 3 8`, `IsMove` polled ~0.5 Hz
  while connected; temperature supported (SDK).

**Driver shape for AstroDeck:** a `zwo-usb` accessory backend owning ctypes bindings
(`EAF_focuser.dll` / CAA dll on Windows; `.so` on Linux) exposing
`Rotator` (CAA) and `Focuser` (EAF) devices. The SDK enumerates by index/ID
(`Get number: 1` in the logs), so `discover()` lists attached units without config.

### SDK API surface (research-confirmed)
See `## Research findings` below for the full function tables. Highlights:
- **License:** `libasi/license.txt` in indi-3rdparty is **MIT-style (ZWO Company,
  2015)** — grants use/copy/modify/merge/publish/distribute/sublicense/sell over the
  headers AND the prebuilt binaries. AstroDeck can bundle the SDK exactly as INDI
  does. (Confirmed from source.)
- **CAA:** `CAAGetNum → CAAGetID → CAAOpen`, then `CAAMoveTo` (absolute sky
  degrees) / `CAAMoveToMechanical` / `CAAMove` (relative) / `CAAStop`;
  `CAAGetDegree`, `CAAIsMoving(ID, &moving, &handControl)` (hand-control motion is
  NOT abortable via CAAStop), `CAACurDegree` = sync-without-move; min/max degree
  limits, reverse, beep, temperature, firmware/serial/type (`"CAA-M54"`-style).
  Bundled SDK 1.5.9 = ZWO's current. INDI's CAA driver has NO hotplug (one-shot
  scan) — ours should poll `CAAGetNum` instead.
- **EAF:** `EAFGetNum → EAFGetID → EAFOpen`, `EAFMove` (absolute steps), `EAFStop`,
  `EAFIsMoving`, `EAFGetPosition`, `EAFResetPostion` (sic — sync), backlash 0–255,
  max-step, reverse, beep, temperature, firmware/serial/type; EAF Pro adds battery
  telemetry + BLE (we are USB-only by policy). indi-3rdparty bundles 1.7.7; ZWO
  lists 1.8.1 — check for drift when vendoring.
- **PIDs:** not published in any source — but **empirically observed on astrotown**:
  CAA = `03C3:1F20`, EAF = `03C3:1F10` (this doc, inventory table). The SDK links
  hidapi statically → HID-report transport (inference, consistent with our HID
  enumeration).

## Wanderer Snowflake filter wheel: plain ASCII serial (COM8)

CH340 UART. **Empirical capture (2026-07-20):** opened `COM8` at 19200 8N1 with
**DTR/RTS held low** (important: CH340 DTR toggle = Arduino-style MCU reset — keep
DTR off to avoid resetting the device); the wheel **streams unprompted at ~1 Hz**:

```
WSFW508A20260124A1.00ALXXXXXXXA0A0A0A0A0A0A0A0A0A<CR><LF>
```

Field decode (apparent, `A`-separated): model `WSFW508` · firmware date `20260124` ·
version `1.00` · serial `LXXXXXXX` · 9-slot status vector `0…0`. Command set from
INDI source below.

## Wanderer Rotator: protocol from source (device not on bus today)

Registered ASCOM local-server drivers exist for three models
(`ASCOM.WandererRotator1/2/3.Rotator`, WandererEmpire V2.4.0 suite at
`Program Files (x86)\Wanderer Astro\` — .NET, decompilable fallback). Protocol comes
from the open INDI 3rd-party drivers; verify against hardware when the unit is
attached.

## Research findings

Source: open INDI drivers, read in full (2026-07-20 research pass). Wanderer rotator
drivers live in **indi core** `drivers/rotator/wanderer_rotator_{base,lite,lite_v2,mini}.*`
(GPL v2+, Frank Wang & Jérémie Klein 2025); ZWO CAA/EAF via `indi-3rdparty/indi-asi`
+ `libasi` (`CAA_API.h`, `EAF_focuser.h` committed verbatim, MIT-style license).
Fetched sources archived in the session scratchpad. Confidence: CONFIRMED from
source unless marked otherwise.

### Wanderer rotator wire protocol (CONFIRMED from INDI source)

Transport: USB CDC serial, **19200** baud (8N1 by INDI default — framing inferred).
**Every device reply is a frame terminated by ASCII `'A'`** (`tty_read_section(…,'A',…)`).

| Purpose | Command sent | Write terminator |
|---|---|---|
| Handshake / status burst | `1500001` | none |
| Set current position as mechanical zero | `1500002` | `\n` |
| Set backlash (0–3°, 0.1° step) | `str(int(deg*10 + 1600000))` (0.5° → `1600005`) | `\n` |
| Reverse ON / OFF | `1700001` / `1700000` | `\n` |
| **Move (RELATIVE)** | `str(int(delta_deg * steps_per_degree + 1000000))` | none |
| Abort | `Stop` (literal) | none |

- Handshake reply burst (each `'A'`-terminated): model name → firmware version →
  current angle ×1000 → backlash → reverse flag. The model name is **string-equality
  checked** — use as positive device ID on a scanned COM port.
- **The wire move is a relative delta in motor steps offset by +1,000,000** even
  though ASCOM/INDI expose absolute goto — the driver computes `delta = target −
  current`. Home = move by `−current` through the same encoding.
- **Unsolicited push:** after a move completes the device pushes a final-angle frame
  on its own; the INDI driver dead-reckons during motion (±1°/240 ms) then does a
  bare read. A native driver must treat the serial line as async, not pure
  request/response.
- Per-model constants: Lite V1 name `WandererRotatorLite`, min fw `20240403`,
  **1155 steps/°** · Lite V2 `WandererRotatorLiteV2`, fw `20240226`, **1199** ·
  Mini `WandererRotatorMini`, fw `20240226`, **1142**.
- **No INDI driver exists for the Pro/Pro V2** (repo-confirmed gap); vendor site
  claims INDI compat — unverified. Same command family is likely but UNCONFIRMED;
  identify via handshake name when one appears.

### ZWO CAA/EAF SDK (CONFIRMED from headers; see §SDK API surface above)

Full function tables live in `CAA_API.h` / `EAF_focuser.h` (indi-3rdparty `libasi`).
Error enums include `CAA_ERROR_STALL` / thermal codes (EAF `E5`–`E8`) worth surfacing
in AstroDeck telemetry. EAF has INDI hotplug support (1 s `EAFGetNum` polling
pattern); CAA driver does not (one-shot scan) — do better. Temperature-compensation
focusing is client-side in INDI (steps-per-°C over plain `EAFMove`) — matches our
existing autofocus provider layer, not the SDK.

### Wanderer Snowflake filter wheel (protocol pass pending)

Empirical banner + transport captured above; INDI driver exists (product-line
confirmed). Command table to be appended from the follow-up source read.

## Native driver targets (summary)

| AstroDeck role | Device | Native path | Effort |
|---|---|---|---|
| rotator | ZWO CAA | ctypes → CAA SDK (MIT-licensed, bundleable) | Low |
| focuser | ZWO EAF | ctypes → EAF SDK (same package) | Low |
| filterwheel | Wanderer Snowflake | pyserial, ASCII protocol (banner-streaming) | Low |
| telescope | ZWO AM5N | pyserial, LX200 + `:Spu#` (mount doc) | Medium (sub-project B) |
| rotator (alt) | Wanderer Rotator Lite/Mini | pyserial, offset-encoded numeric protocol | Low (when attached) |

## Framework note (feeds sub-project B/C design)

The driver framework (spec 2026-07-20) models `transport ∈ {network, serial}` with
per-transport addressing. The ZWO accessories introduce a third shape: a **local
SDK/library transport** — no host/port, no port_path; enumeration by SDK index.
`ConnSpec.transport` is an open `str`, but `DriverEntry._check_transport` currently
requires host/port for anything non-serial → the SDK backend needs either a
`transport="local"` branch (no addressing required) or DriverEntry stays out of the
picture (implicit-row style, like `ascom-local`). Decide in the accessory-driver
spec.

## Safety notes for future at-scope validation

- Filter wheel moves: safe anytime (internal carousel).
- EAF moves: safe when idle; avoid during imaging (defocus).
- CAA moves: rotates the camera train — **cable-wrap risk**; keep test moves small
  (±5°) and return to start; verify cable slack first.
- Always open Wanderer serial ports with DTR/RTS disabled unless a reset is intended.
