# ZWO AM5N Native Serial Mount Driver — Design (sub-project B)

**Date:** 2026-07-20
**Type:** Spec (follows the approved sub-project A framework spec,
`2026-07-20-driver-framework-design.md`, merged to main 4f932d7).
**Protocol source of truth:** `docs/hardware/zwo-am5-lx200-protocol.md` — the wire
protocol we captured and verified on the real mount (astrotown, 2026-07-19),
including the load-bearing `:Spu#` unpark discovery.

## Goal

AstroDeck drives the ZWO AM5N mount over one USB cable with zero ZWO software: a
native `Telescope` driver speaking Meade LX200 ASCII over USB CDC serial (COM3-style
port), registered through the sub-project A framework as its first real citizen —
entry-point discovered, serial-addressed, `hardware=True`.

## Scope

**In:** the backend/session/device classes, an LX200 codec, a serial transport
helper, park/unpark handling, slew/sync/tracking/move/pulse-guide/pier-side over
LX200, COM-port discovery by VID:PID, unit tests against a fake serial double, and
an at-scope validation runbook.
**Out (deferred):** the ZWO accessory drivers (CAA/EAF/Snowflake — sub-project C,
see `docs/hardware/rotator-focuser-filterwheel-native.md`, incl. the
`transport="local"` SDK branch); meridian-flip orchestration changes (hub already
owns flip logic); Bluetooth (USB-only by policy); firmware update; hand-controller
features.

## Architecture

Three new modules under `server/astrodeck/devices/`, mirroring the
`ascom_local`/`native_backend` session template:

```
devices/lx200.py            # pure codec: build/parse LX200 frames (no I/O)
devices/serial_link.py      # pyserial transport: open/req-resp/read-until-'#',
                            # asyncio-safe via to_thread + an asyncio.Lock
devices/backends/zwo_am5.py # ZwoAm5Backend + ZwoAm5Session + ZwoAm5Telescope
```

- **`lx200.py` (pure, import-light):** `build(cmd) -> bytes` (`:CMD#`),
  `parse_ra("HH:MM:SS#") -> hours`, `parse_dec("sDD*MM:SS#") -> degrees` (`*` =
  0x2A separator), `format_ra(hours)`, `format_dec(deg)`, `parse_status(gu_word: str)`
  → dataclass of `:GU#` bits (parked/tracking/slewing flags as captured), plus the
  ack conventions (commands that answer `1`/`0`/`e14#`, commands that are
  fire-and-forget). 100% unit-testable, golden vectors from the captured session.
- **`serial_link.py`:** `SerialLink(port_path, baud=9600)` wrapping pyserial
  (baud value irrelevant on USB-CDC — set anyway). API:
  `async request(cmd, *, reply=True, timeout=1.5) -> str | None` (write, then
  read-until-`#` when a reply is expected; fire-and-forget when not), guarded by an
  `asyncio.Lock` so concurrent hub polls can't interleave frames; sync pyserial
  calls run via `asyncio.to_thread`. `close()` releases the port. DTR/RTS left at
  pyserial defaults (the AM5N is a real CDC device, not an Arduino — no reset
  concern, confirmed by weeks of ZWO-driver use).
- **`zwo_am5.py`:**
  - `ZwoAm5Backend` — manifest: `name="zwo-am5"`, `label="ZWO AM5 (native serial)"`,
    `roles=("telescope",)`, `discoverable=True`, `hostless=False`,
    `transport="serial"`, `hardware=True`, `driver_type="zwo-am5"`,
    `version=<app>`. `discover()` enumerates `serial.tools.list_ports` and returns
    entries where `vid==0x03C3 and pid==0x4001` (fall back to listing all CDC ports
    flagged `unverified` so a hub-attached mount still shows). `open(conn)` →
    `ZwoAm5Session(conn.port_path)`.
  - `ZwoAm5Session` — owns ONE `SerialLink`. `get_device("telescope", conn)` →
    connected `ZwoAm5Telescope`. `health()` → last-reply age + firmware string.
    `close()` → stop motion (`:Q#`), close link. `native_guider()`/`guide_camera()`
    /`native_solver()` → None.
  - `ZwoAm5Telescope(Telescope)` — the ABC implementation (below). Sets
    `backend="zwo-am5"`, `hardware=True` (class attr, mirroring alpaca/nina).
  - Registered via the **entry point** `[project.entry-points."astrodeck.backends"]
    zwo_am5 = "astrodeck.devices.backends.zwo_am5:register_all"` in
    `server/pyproject.toml` — dogfooding A's discovery path (decision from the A
    brainstorm). NOT added to the built-in import list.

## Device behavior (mapping the `Telescope` ABC onto the captured protocol)

**Connect flow:** open link → identity check `:GVP#` must contain `AM5` (string
mismatch → `DeviceError`, port released) → read `:GV#` firmware into `describe()` →
init clock/site (each must ack `1`): `:SG…#` UTC offset, `:SH0#`, `:SC MM/DD/YY#`,
`:SL HH:MM:SS#`, `:SMGE…#` lat/lon from AstroDeck site config → read initial state
(`:GU#`, `:Gps#`). **Connect does NOT unpark** — parking state is surfaced honestly.

| ABC method | Wire | Notes |
|---|---|---|
| `get_position()` | `:GR#` / `:GD#` | JNow (mount-native); hub's JNOW handling applies as with Alpaca |
| `is_parked()` | `:Gps#` → `2#`=parked (else `:GU#` park bit) | |
| `unpark()` | `:Spu#` → `1` | THE discovery; verified on hardware |
| `park()` | `:hP#` when unparked | UNVERIFIED (parked-state refusal was all we saw) — validation item; on failure raise clear DeviceError |
| `slew(ra, dec)` | `:Sr HH:MM:SS#`→`1`, `:Sd sDD*MM:SS#`→`1`, `:MS#`→`0`=accepted | Then poll `:GU#`/coords to settle (see below). `e14#` (parked) → DeviceError "mount is parked" |
| `sync(ra, dec)` | `:Sr#`/`:Sd#` then `:CM#` | `:CM#` format-standard, UNVERIFIED on AM5N — validation item |
| `set_tracking(on)` | `:Te#` / `:Td#` | Parse-verified only — validation item |
| `get_tracking()` | `:GAT#` (or `:GU#` bit) | |
| `move_axis(axis, rate)` | rate→`:R{0..9}#` map, then `:Mn/:Ms/:Me/:Mw#`; rate 0 → `:Qn/:Qs/:Qe/:Qw#` | Verified on hardware (the capture session). Deg/s→R-index map documented in the protocol doc |
| `is_slewing()` | `:GU#` motion bit; fallback coord-delta | Moves are fire-and-forget — polling is the ONLY truth |
| `stop()` | `:Q#` then re-assert tracking state | Emergency stop; also the default axis-zeroing composition stays correct |
| `pulse_guide(dir, ms)` | `:Mg{n,s,e,w}NNNN#` | Format-verified only; `can_pulse_guide` flips True after at-scope validation passes (until then False → guider won't select it) |
| `guide_rates()` | `:GdG#` | |
| `pier_side()` | `:Gm#` → `E#`/`W#` | |
| `destination_pier_side()` | default UNKNOWN | AM5N exposes no query; harmonic mounts have no meridian flip in the GEM sense — return UNKNOWN, `reports_destination_pier_side` stays False |
| `time_to_meridian_flip()` | `None` | harmonic mount |

**Slew settle (cancel-safe):** after `:MS#` accepted, poll every 0.5 s (wall-clock
timeout 120 s): slewing bit clear AND coord delta < 0.05° across two consecutive
polls → settled. On `asyncio.CancelledError` → send `:Q#`, re-raise (mirrors
`AlpacaTelescope.slew` abort discipline). On timeout → `:Q#` + DeviceError.

**Parked-refusal honesty:** every motion path maps `e14#` to
`DeviceError("mount is parked — unpark first")` rather than a generic failure, so
the UI tells the user exactly what the AM5N's cryptic refusal means.

## Framework touchpoints (what B exercises in A)

Entry-point discovery loads it (no core edit); `driver_type="zwo-am5"` makes it a
configurable driver; profile rows carry `transport="serial"` + `port_path`;
orchestrator stamps `hardware=True` (plus the class attr); `_probe_configured`
reports the serial type neutrally. **One A-minor lands here:** `config_store.
add_driver` requires `DRIVER_DEFAULT_PORTS` membership — generalize it (registry
check + per-transport defaults: serial types need no default port) so a serial
driver can actually be created from the API. Also unify `SimSolver._REAL_MODES`
handling: `orchestrator._solver_mode` passes unknown session names through — add
`"zwo-am5"` to the real-mode mapping (or key the guard on the device `hardware`
flag) so the sim solver refuses to sync a native-serial rig (A-minor #2).

## Dependencies

`pyserial>=3.5` added to `server/pyproject.toml` dependencies (pure-Python, tiny).
Import-guarded in `zwo_am5.py` (absent → backend registers as unavailable in its
probe, matching the comtypes degrade pattern) so non-serial installs stay clean.

## Testing

- **Codec:** golden vectors lifted verbatim from the captured ASIMount session
  (RA/Dec strings, `:GU#` words, `e14#`, ack forms). Round-trip format/parse.
- **FakeSerialLink:** an in-memory double scripted with request→reply maps +
  unsolicited frames; drives `ZwoAm5Telescope` through connect/identity-fail/init,
  unpark, slew-settle (including cancel mid-slew → `:Q#` sent), parked-refusal
  mapping, move_axis rate mapping, stop-reasserts-tracking.
- **Framework integration:** backend registers via a monkeypatched entry point;
  profile with `transport="serial", port_path="COM3"` connects through
  `connect_profile` against the fake link; device reads `hardware=True`;
  `describe_all` shows the serial driver row neutrally.
- **At-scope validation runbook** (documented in the driver module + protocol doc):
  daytime, tracking off, small `:R2#` nudges N/S/E/W with stop, `:Spu#`/`:Gps#`
  round-trip, then (clear night) a real goto + sync + tracking + pulse-guide check
  before flipping `can_pulse_guide=True` and closing the park()/`:CM#` validation
  items.

## Success criteria

1. With ZWO/ASCOM software absent, AstroDeck connects the AM5N on its COM port via
   a profile row (`zwo-am5` + `port_path`), shows it as real hardware, and
   slews/syncs/tracks/nudges it (validated at scope).
2. The driver loads through the entry-point path with zero core-file edits beyond
   its own module + pyproject entry.
3. Parked-state UX is honest (`e14#` → "unpark first"); no motion path can hang
   (wall-clock timeouts everywhere; `:Q#` on cancel/timeout).
4. Full suite green; new codec/driver tests cover the table above.
