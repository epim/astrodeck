# Accessory Native Drivers — ZWO CAA + EAF (SDK) & Wanderer Snowflake (serial) — Design (sub-project C)

**Date:** 2026-07-20
**Type:** Spec (design approved in compressed brainstorm; DLL-bundling decision taken).
**Campaign:** Platform expansion — completes the native rig except cameras, on the
driver framework (A, merged 4f932d7) using the patterns proven by the AM5N driver
(B, merged bc9cd14).
**Protocol source of truth:** `docs/hardware/rotator-focuser-filterwheel-native.md`
(CAA/EAF SDK API + licensing; Snowflake wire protocol, hardware-verified 2026-07-20).

## Goal

Native drivers for the three attached imaging-train accessories — ZWO CAA (rotator),
ZWO EAF (focuser), Wanderer Snowflake (filter wheel) — with zero vendor software:
the ZWO pair via ctypes over the MIT-licensed ZWO SDK (bundled), the wheel via its
ASCII serial stream.

## Scope

**In:** a vendored ZWO SDK (win-x64 DLLs + loader), ctypes bindings, the `zwo-usb`
backend (one session, rotator+focuser), the `wanderer-snowflake` backend
(stream-native serial filter wheel), the framework's `transport="local"` branch,
tests over fake SDK/stream doubles, at-scope validation runbook items.
**Out (deferred):** the Wanderer *rotator* driver (device not on the bus — protocol
documented for when it appears); Linux `.so` bundling (joins when Linux is a
target); ZWO EFW (none present); cameras (separate campaign); EAF-Pro BLE
(USB-only policy); temperature-compensated focusing (existing provider layer owns
that logic).

## Design

### §1 — Vendored ZWO SDK + loader

- `server/astrodeck/vendor/zwo/` holds the **win-x64 SDK DLLs** (`EAF_focuser.dll`,
  the CAA SDK DLL) plus `LICENSE.txt` (ZWO's MIT-style license verbatim — the legal
  basis; INDI/NINA bundle identically) and a `README.md` naming versions + origin.
- **Correct-DLL identification is part of the work:** astrotown carries three
  candidates (`ASIStudio\EAF_focuser.dll` 1.2 MB, `ASIStudio\CAA_SRC.dll` 73 KB,
  `Common Files\ASCOM\ZWO\CAA_ASCOM_x64.dll` 58 KB — the last is an ASCOM wrapper,
  not the SDK; NINA ships a 55 KB x64 `EAF_focuser.dll`). The plan pulls the
  candidates and **verifies by loading each with ctypes and checking the C API
  exports** (`EAFGetNum`/`CAAGetNum` present ⇒ SDK; absent ⇒ wrapper, reject).
  If no local candidate passes for the CAA, fetch ZWO's official CAA SDK 1.5.9
  (developer downloads) and verify the same way. Only verified DLLs are committed.
- `server/astrodeck/devices/zwo_sdk.py` — the loader + typed bindings:
  - `_find_dll(name) -> Path | None`: ① `ASTRODECK_ZWO_SDK_DIR` env override,
    ② the vendored dir, ③ known installs (ASIStudio, NINA `External\x64\ASI`,
    `Common Files\ASCOM\ZWO`) — first file whose exports verify.
  - `class EafSdk` / `class CaaSdk`: thin ctypes wrappers, one method per SDK
    function used, argtypes/restype declared, returning Python types and raising
    `ZwoSdkError(code, fn)` on non-zero `*_ERROR_CODE`. Loaded lazily; a missing
    DLL/symbol degrades exactly like absent pyserial in B (backend registers, open
    raises a clear `DeviceError`).
  - All SDK calls are blocking C calls → executed via `asyncio.to_thread` under a
    per-session `asyncio.Lock` (the B `SerialLink` discipline; ZWO SDKs are not
    documented thread-safe).

### §2 — `zwo-usb` backend (CAA rotator + EAF focuser)

Manifest: `name="zwo-usb"`, `label="ZWO USB accessories"`,
`roles=("rotator","focuser")`, `discoverable=True`, **`hostless=True`** (every role
coalesces into ONE session per the orchestrator's grouping — one process-wide SDK
context), `transport="local"`, `hardware=True`, `driver_type="zwo-usb"`,
`version=<app>`. Registered via a **second entry point** (`zwo_usb = …:register_all`).

- `ZwoUsbSession`: owns one `EafSdk` + one `CaaSdk` handle set. `get_device`:
  `"rotator"` → `CaaRotator` (first CAA by `CAAGetNum/GetID/Open`), `"focuser"` →
  `EafFocuser`; a role with no attached unit raises `DeviceError("no CAA/EAF
  attached")` → the orchestrator's per-role degrade shows an honest red LED.
  `discover()` reports attached units (`CAAGetNum`/`EAFGetNum` + type/serial).
- **`CaaRotator(Rotator)`** — mechanical-space only, per the base-class contract:
  - **Never calls `CAACurDegree`** (the SDK's sync). With the SDK's logical angle
    never offset, `CAAGetDegree` ≡ mechanical position; AstroDeck's client-side
    sync layer (base `Rotator`) does the rest. This mirrors "never call a driver's
    own Sync" (base.py:259).
  - `get_mechanical_position` = `CAAGetDegree`; `move_mechanical` =
    `CAAMoveToMechanical` then poll `CAAIsMoving` every 0.5 s under the base's
    `MOVE_TIMEOUT_S=180`; on `CancelledError`/timeout → `CAAStop` then re-raise
    (B's halt-on-any-abnormal-exit discipline). `halt` = `CAAStop`.
  - `is_moving` = `CAAIsMoving`; hand-controller motion (`pbHandControl=True`) is
    surfaced in the move error (CAAStop cannot abort it — SDK contract).
  - `can_reverse=True`; `get/set_reverse` = `CAAGet/SetReverse`.
  - `CAA_ERROR_STALL`/`OVER_LIMIT`/`OUT_RANGE` map to named `DeviceError`s
    (cable-wrap protection surfaces loudly). Temperature via `CAAGetTemp` exposed
    in `describe()`.
- **`EafFocuser(Focuser)`**:
  - `get_position` = `EAFGetPosition`; `max_position` = `EAFGetProperty.MaxStep`;
    `move_to` = `EAFMove` (absolute steps) then poll `EAFIsMoving` (0.5 s, timeout
    120 s) with halt-on-any-abnormal-exit via `EAFStop`; `halt` = `EAFStop`;
    `get_temperature` = `EAFGetTemp` (None on error).
  - EAF error codes (`E5` stall, `E6`–`E8` thermal via `EAFGetErrorCode`) surface
    in move failures.

### §3 — `wanderer-snowflake` backend (filter wheel, stream-native serial)

Manifest: `name="wanderer-snowflake"`, `label="Wanderer Snowflake FW"`,
`roles=("filterwheel",)`, `discoverable=True`, `hostless=False`,
`transport="serial"`, `hardware=True`, `driver_type="wanderer-snowflake"`.
Third entry point. `discover()` lists CH340 ports (`1A86:7523`) flagged
`verified=False` (CH340 is generic — identity is proven by the banner at connect).

- `SnowflakeLink` (in the backend module): pyserial opened with **DTR/RTS held
  low** (the verified no-reset technique; use the deferred-open pattern so the
  lines are low before the port opens), 19200 8N1. It is a **reader-first** link:
  a background task consumes the ~2 Hz banner stream into
  `latest: Banner | None` + `last_line_at: float`; writes (`200N\r`, etc.) go
  through a lock. This is deliberately NOT B's request/response `SerialLink` —
  the wheel streams unprompted and commands have no acks.
- `Banner` parse (hardware-verified): `model(WSFW508|WSFW368)` / `fw_date` (must
  be ≥ `20260124`, the driver-min our unit runs) / `current_slot` (1-based) /
  8 per-slot letters / 8 reserved / `device_id`. Resync tolerant (skip garbage
  until a model field, like the INDI parser).
- `SnowflakeWheel(FilterWheel)`:
  - `connect()`: open, await first banner (≤5 s else `DeviceError` "not a
    Snowflake / no stream"), validate model + fw_date, seed `filter_names` from
    the slot letters (`X` → `"Slot N"`).
  - `get_position` = latest banner slot − 1 (AstroDeck is 0-based).
  - `set_position(slot)`: send `200{slot+1}\r`; completion = **stream silence
    then resume with the target slot** (verified live: pause ≈4–5 s during the
    physical move). Implementation: wait until a banner NEWER than the command
    shows the target slot; timeout 40 s (INDI's bound) → `DeviceError`; no halt
    exists on this device (carousel completes on its own) so cancel just stops
    waiting.
  - No auto-calibrate at connect (INDI sends `1500002` every connect; we do NOT —
    a calibration sweep moves the carousel unprompted; exposed instead as an
    explicit maintenance action later if wanted).

### §4 — Framework: `transport="local"`

- `DriverEntry._check_transport` gains the third branch: `transport="local"` needs
  no host/port/port_path (identity is SDK enumeration).
- `config_store.add_driver`: `transport="local"` branch — no addressing required;
  label default `f"{type.upper()} (USB)"`.
- `ConnSpec` needs no change (`transport` is already an open str; addressing
  fields simply stay None).
- The A-review M7 note (orchestrator `_normalize` ignores `port_path`) does not
  bite here: `zwo-usb` is `hostless=True` (one session by design) and the wheel is
  a single serial endpoint; multi-instance serial stays a ledgered gap.

## Testing strategy

All hardware-free, mirroring B:
- **FakeEafSdk / FakeCaaSdk** doubles (scripted positions/moving-sequences/error
  codes) injected via a module seam (`zwo_sdk.make_eaf/make_caa` factory
  attributes) — drive both device classes through connect, absolute moves with
  polling completion, cancel→halt, stall/limit error mapping, no-device-attached
  honesty, and the **never-calls-CAACurDegree** rule (assert not in the fake's
  call log; sky-sync layer exercised via the base class).
- **FakeStream** for the wheel: scripted banner sequences incl. the pause-resume
  move signature, garbage resync, wrong-model refusal, old-firmware refusal,
  0/1-based slot mapping, 40 s timeout.
- **Framework integration:** entry-point discovery of both backends (monkeypatched
  entry_points), `transport="local"` DriverEntry/add_driver round-trip, a profile
  wiring rotator+focuser to `zwo-usb` + filterwheel to `wanderer-snowflake`
  through `connect_profile` with `hardware=True` stamps on all three.
- **DLL verification test (skipped-if-absent):** loads the vendored DLLs and
  asserts the expected exports — CI-safe (skip when vendor dir empty), real
  protection on dev/scope boxes.

## At-scope validation runbook (appended to the ledger)

CAA: small ±5° mechanical moves, verify degrees/direction/reverse + stall surfacing;
confirm `CAAGetDegree` tracks the ASCOM-observed values. EAF: ±200-step moves,
position/temp readback. Wheel: full 1→8 cycle (its protocol is already
hardware-verified). All safe daytime actions; CAA needs the cable-slack check first.

## Success criteria

1. With no ZWO/Wanderer software running, AstroDeck connects CAA+EAF+wheel from a
   profile, shows them as real hardware, moves each (validated at scope), and a
   fresh install works from the bundled SDK alone.
2. Both backends load via entry points with zero core edits; absent hardware and
   absent DLLs degrade honestly (red LED / clear DeviceError), never crash.
3. Full suite green; every driver behavior above covered by double-driven tests.
