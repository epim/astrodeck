# ZWO AM5 / AM5N mount — USB + serial protocol (captured)

**Status:** empirically captured 2026-07-19 on the observatory rig ("astrotown").
**Purpose:** foundation for a **native AstroDeck ZWO-mount driver** — no ZWO software,
no ASCOM, no ASIAIR required. This documents exactly what the mount speaks on the wire.

## TL;DR

A ZWO AM5-series mount is, on the wire, **a plain USB CDC-ACM virtual serial port
speaking the classic Meade LX200 ASCII command set** (with a small OnStep-style
extension). There is **no proprietary binary USB protocol** to reverse-engineer. A
native driver only needs to open the serial port and exchange `:CMD#` → `RESPONSE#`
ASCII strings. This is the best-case outcome for the vendor-neutral goal.

## Hardware identification

| Field | Value |
|---|---|
| Product (`:GVP#`) | `AM5N` |
| Firmware version (`:GV#`) | `1.8.8` |
| Firmware build (`:GVD#`/`:GVT#`) | `Jan 25 2026` `14:01:29` |
| USB VID:PID | `03C3:4001` (composite; `MI_00` = CDC serial) |
| Windows enumeration | `USB Serial Device (COMn)` |

The `03C3:4001` composite device is the mount's built-in USB hub bridge. Interface
`MI_00` is the CDC-ACM serial function; the other interfaces / downstream hub carry
ZWO cameras and HID accessories (EAF/EFW/hand controller) — unrelated to mount control.

## USB transport layer

Standard USB **CDC-ACM** (communications device class, abstract control model).
Captured with USBPcap on the mount's root hub while the port was driven:

| Endpoint | Type | Direction | Role |
|---|---|---|---|
| `0x00` | control | — | CDC setup (SET_LINE_CODING, SET_CONTROL_LINE_STATE) |
| `0x81` | interrupt | IN | CDC serial-state notifications (idle in practice) |
| `0x02` | bulk | OUT (host→mount) | command bytes |
| `0x82` | bulk | IN (mount→host) | response bytes |

**Framing:** each LX200 command is sent as a single bulk-OUT transfer containing the
whole `:CMD#` string; each response arrives as a single bulk-IN transfer containing the
whole `#`-terminated reply. Clean 1:1 request/response, no chunking, no length prefix,
no checksum — the LX200 `#` terminator is the only framing.

**Baud rate is irrelevant.** Because this is USB-CDC, the line-coding baud is a no-op:
the mount responds identically at 9600 / 19200 / 57600 / 115200. A native driver may
open the port at any standard baud (9600 is the conventional choice).

## Application protocol — Meade LX200 ASCII

Commands: ASCII, `:` prefix, `#` terminator (`:GR#`). Responses: ASCII, `#`-terminated.
Coordinate format is LX200 high-precision sexagesimal; `*` (0x2A) is the degree
separator in Dec/Alt/Az/latitude.

### Observed GET commands (read-only sweep, verbatim responses)

| Command | Meaning | Response format | Example |
|---|---|---|---|
| `:GR#` | Get RA | `HH:MM:SS#` | `10:13:56#` |
| `:GD#` | Get Dec | `sDD*MM:SS#` | `+90*00:00#` |
| `:GA#` | Get Altitude | `sDD*MM:SS#` | `+37*07:51#` |
| `:GZ#` | Get Azimuth | `DDD*MM:SS#` | `360*00:00#` |
| `:Gt#` | Site latitude | `sDD*MM:SS#` | `+37*…#` (redacted) |
| `:Gg#` | Site longitude | `DDD*MM:SS#` (0–360°, W-positive) | `+122*…#` (redacted) |
| `:GG#` | UTC offset | `sHH:MM#` (hours to add to local→UTC) | `+08:00#` (⇒ local = UTC−8) |
| `:GL#` | Local time | `HH:MM:SS#` | `20:30:08#` |
| `:GS#` | Sidereal time | `HH:MM:SS#` | `16:13:58#` |
| `:GC#` | Calendar date | `MM/DD/YY#` | `07/19/26#` |
| `:GW#` | Alignment status | (empty on AM5N) | `#` |
| `:Gm#` | Meridian / pier side | `E#` \| `W#` | `E#` |
| `:GT#` | Tracking rate | `n#` | `0#` |
| `:GVP#` | Product name | string`#` | `AM5N#` |
| `:GV#` | Version | string`#` | `1.8.8#` |
| `:GVD#` | Firmware date | string`#` | `Jan 25 2026#` |
| `:GVT#` | Firmware time | string`#` | `14:01:29#` |
| `:GVN#` | Firmware number | (empty on AM5N) | `#` |
| `:GU#` | Extended global status | status word`#` | `nGM000000005#` |
| `:GdG#` | Guide rate | `sDD*MM:SS#` | `+00*00:00#` |

Not supported (no response): `:pS#`, `:D#`, `:V#`, bare `CR`.

### Write path & motion (partially captured)

The set and motion command **formats are all confirmed parsed** by the firmware —
their USB framing is identical to reads (each command, however long, is one bulk-OUT
transfer; e.g. `:Sr10:37:16#` = 12 B in a single transfer, ack on bulk-IN):

| Command | Meaning | Observed result |
|---|---|---|
| `:Sr HH:MM:SS#` | Set target RA | **`1`** (success) — works |
| `:Sd sDD*MM:SS#` | Set target Dec | **`1`** (success) — works |
| `:Gr#` / `:Gd#` | Read back target | echoes the set target exactly |
| `:RG#` `:RC#` `:RM#` `:RS#` | Select guide/center/move/slew rate | accepted, **no ack** (fire-and-forget) |
| `:Q#` | Stop / halt | accepted, no ack |
| `:MS#` | GoTo target | see note below |
| `:Mgn/s/e/w<ms>#` | Pulse guide N/S/E/W | see note below |
| `:Mn/s/e/w#` … `:Q#` | Manual move / stop | see note below |
| `:Te#` / `:Td#` | Enable / disable tracking | see note below |
| `:hR#` | Unpark | see note below |

**The LX200 serial port is telemetry + config only — it cannot command motion.**
With the mount confirmed **fully powered**, it answered every info query and accepted
set-target (`1`) and rate-sets, but **every command that moves an axis or changes motion
state was refused with `e14#`**: `:MS#` (goto to a verified-valid target), `:Te#`/`:TQ#`
(tracking), `:Mg*#` (pulse guide), `:Ms#` (move), and the entire home/park set
`:hR#`/`:hU#`/`:hN#`/`:hF#`/`:hW#`/`:hS#`/`:hP#`/`:I#`/`:PO#`/`:MP#`. Notably even `:hP#`
(**park**) is refused — so this is not a clearable "parked" state; the whole motion
command class is disabled on this interface. Dec stayed pinned at `+90*00:00` and `:GU#`
never left `nGM000000005#` through all of it. `e14#` is a ZWO "command refused in current
state" reply.

The reason: **the mount was PARKED, and the unpark command is ZWO-specific — `:Spu#`, not
the LX200/OnStep `:hR#`/`:hU#`/`:hP#` I had tried.** This was confirmed by capturing ZWO's
own ASIMount ASCOM driver driving the mount over COM3 (its trace log at
`Documents\ASCOM\ASIMount\Logs\ASCOM.ASIMount.*.txt` records every serial command). The
driver's log literally annotates the unlock:

```
--> :Spu#
<-- 1
Cancel Park success 1
--> :GU#
<-- nNGM000000000#     (was nGM000000005# — park bit cleared)
```

**Motion works over USB/COM3 once unparked** — this is a solved, viable native path. ZWO's
driver used exactly the LX200 CDC serial port (not Bluetooth) for this session. (The
ASIMount driver *can* also use Bluetooth — it ships `LibBle.dll` — which is why an
unconfigured/headless connect attempt earlier used BLE and touched COM3 zero times; but
configured for the COM port, it drives the mount entirely over USB serial.)

### The AM5N control recipe (captured from ZWO's driver, over USB/COM3)

Connect COM3 (8N1, baud irrelevant — USB-CDC), then:

| Phase | Commands | Response |
|---|---|---|
| Init/site/time | `:SGsHH:MM#` (UTC offset) `:SH0#` (DST flag) `:SCMM/DD/YY#` (date) `:SLHH:MM:SS#` (time) `:SMGE{sDD*MM:SS}&{sDDD*MM:SS}#` (geo combined, **longitude W-positive**) | each `1` |

> Format note (2026-07-20, re-extracted from the captured session): the init
> commands take **no space** after the verb (`:SC07/19/26#`, `:SL22:14:58#`),
> and `:SMGE` longitude is W-positive (AstroDeck stores East-positive — negate).
> The native driver (`server/astrodeck/devices/backends/zwo_am5.py` + codec
> `devices/lx200.py`) inits with the **UTC scheme**: `:SG+00:00#` + `:SH0#` +
> UTC date/time, so mount "local" time == UTC and sidereal stays correct with no
> DST bookkeeping. At-scope check: `:GS#` ≈ expected LST after init.
| **Unpark** | **`:Spu#`** ("Cancel Park") | **`1`** — clears the park bit; `:GU#` → `nNGM…` |
| Set rate | `:R0#`..`:R9#` (rate index; driver used `:R5#`/`:R6#`) | none (fire-and-forget) |
| Move axis | `:Mn#` `:Ms#` `:Me#` `:Mw#` | none |
| Stop axis | `:Qn#` `:Qs#` `:Qe#` `:Qw#` | none |
| Park status | `:Gps#` | `2#` = parked |

### Native-driver at-scope validation results (2026-07-20, fw 1.8.8)

Verified live via `devices/backends/zwo_am5.py` over COM3 (no ZWO software):

- **Park `:hP#` WORKS and is fire-and-forget** (motion class — an ack-read times
  out); `:Gps#` flips to `2#` ~1 s later. **`:Spu#` on an already-unparked mount
  replies `0`** (nothing to cancel) — treat park/unpark as idempotent state ops.
- **Tracking `:Te#`/`:Td#` verified** (ack `1`; `:GAT#` follows).
- **`:R<n>#` indices are sidereal-multiple presets, measured:** R1≈0.3×,
  R3≈1.9×, R5≈7.8×, R7≈60× sidereal; R8≈1.44°/s (R8/R9 measurements
  acceleration-ramp-limited over 0.3 s). A requested 0.25°/s mapped to R7
  measured 0.250°/s over 1 s — the driver's calibrated table is accurate.
- **`:GdG#` encodes the guide RATE as sidereal-fraction ×100 in the degrees
  field**: `+90*00:00#` = 0.90× sidereal (NOT 90°).
- **`:CM#` sync accepted** (sync-to-self round-trip clean). UTC-init scheme
  validated: `:GS#` sidereal matched computed LST within minutes.
- **Pulse guide `:Mg{n,s,e,w}<ms>#` parses but produces NO motion** on this
  firmware over serial (tried 5–10 s pulses, upper+lowercase directions,
  tracking on, GR-monitored: 0.0″). `:GFR1#`/`:GFD1#` are NOT live encoders
  (constant `22438`).
- **Working pulse EMULATION (hardware-calibrated, in the native driver):**
  `:M<dir>#` during tracking does NOT cleanly superimpose (Me@R1 read +1.5×
  sid, Mw@R1 read +0.5× — both eastward), so per-direction strategies:
  **east** = suspend tracking (`:Td#`…`:Te#`) → drifts east at EXACTLY 1.0×
  sidereal (+150″/10 s measured); **west** = `:R2#`+`:Mw#` → EXACTLY −1.0×
  sidereal (−150″/10 s); **north/south** = `:R1#`+`:Mn/:Ms#` → ±0.5× sidereal
  (±75″/10 s). Symmetric ±1× RA / ±0.5× dec — guider-ready. First real goto
  also verified in these sessions: `:Sr/:Sd/:MS#` + settle-poll landed dec
  +40° targets with 0.000° residual, and `:hP#` park slews home from
  anywhere (verified from dec +40).
- Coordinate note: dec-axis nudges at dec≈+90 cross the pole (RA flips 12 h) —
  cosmetic, but distance-based settle logic must use angular separation, not
  raw coordinate deltas, near the pole.

Other ZWO-specific gets seen: `:GMA#` → BT/MAC address (`48ca4357cab1#`), `:GP08#` → `0#`,
`:GAT#` → `0#` (at-target flag), `:GFR1#`/`:GFD1#` → `22438#` (axis encoder counts).

**Conclusion:** the AM5N is **fully drivable by a native USB/LX200 driver.** Every motion
command I earlier saw refused with `e14#` was refused solely because the mount was parked;
sending **`:Spu#`** first clears the park and motion executes (moves are fire-and-forget,
no ack). Standard LX200 goto (`:Sr#`/`:Sd#`/`:MS#`), pulse-guide (`:Mg*#`), and tracking
should likewise work post-unpark — capture/verify those in a follow-up (the driver session
logged here exercised only manual `:M<dir>#`/`:Q<dir>#` nudges). A native driver needs no
ZWO software: connect COM3, run the init+`:Spu#` recipe, then LX200 motion.

### `:GU#` extended status word

`:GU#` → e.g. `nGM000000005#`. This is an **OnStep/OnStepX-style composite status
string** (the AM5N firmware is LX200 with OnStep-flavored extensions). The leading
flags encode tracking/goto/mount-type state (`n` = not tracking, `G` = GEM-type
frame, etc.), followed by a numeric field. This one command is the most efficient
poll for driver state and should be preferred over polling many individual `:G*#`
commands. Full flag decoding: follow the OnStep `:GU#` spec and confirm each bit
against live states (tracking on/off, slewing, parked) before relying on it.

## Implications for the native AstroDeck driver

1. **No binary protocol work.** Implement against the LX200 ASCII set — a well-documented,
   widely-implemented standard. Reuse/port an existing LX200 command builder/parser.
2. **Transport = pyserial (or the native Rust serial layer).** Open `COMn` (Windows) /
   `/dev/ttyACMn` (Linux/macOS — same CDC device, no driver needed), 8N1, any baud.
3. **State poll:** prefer `:GU#` for a single-call status; fall back to individual
   `:GR#`/`:GD#`/`:Gm#`/`:GT#` for fields `:GU#` doesn't expose.
4. **Motion works over USB after unparking with `:Spu#`.** The mount powers up **parked**,
   and while parked it refuses every motion command with `e14#`. The unpark is the
   ZWO-specific `:Spu#` (not LX200 `:hR#`/`:hU#`/`:hP#`). After `:Spu#` → `1`, manual moves
   (`:R<n>#` + `:M<dir>#`/`:Q<dir>#`) execute fire-and-forget and the mount physically
   moves (verified end-to-end via ZWO's driver over COM3 + captured imagery). Do the init
   (`:SG#`/`:SH0#`/`:SC#`/`:SL#`/`:SMGE#`) then `:Spu#` then motion. Goto (`:Sr#`/`:Sd#`/
   `:MS#`), pulse-guide (`:Mg*#`), and tracking should work post-unpark — verify in a
   follow-up. Match `:SMGE#`/`:SG#`/`:SH0#` to correct current values (they set stored
   config).
5. **Both read AND write paths are usable over USB with no ZWO software.** Direct LX200 over
   the CDC port gives full telemetry (pointing, coords, site/time, pier, `:GU#`), and the
   init+`:Spu#`+motion recipe drives the mount. The AM5N is a fully viable native-driver
   target over a single USB cable. (ZWO's ASIMount driver *can* alternatively use Bluetooth,
   but it drove this mount entirely over the COM port when configured for it.)

## Reproducing the capture

Tools installed on astrotown: **USBPcap 1.5.4** (`C:\Program Files\USBPcap\USBPcapCMD.exe`);
analysis with `tshark`. Method:

1. Map the mount's root hub (single root hub on this box → `\\.\USBPcap1`).
2. Start capture: `USBPcapCMD.exe -d \\.\USBPcap1 -o mount.pcap -A`.
3. Drive the port (read-only): open `COMn`, write `:GR#` etc., read the `#`-terminated
   reply. (Scripts: `probe-serial.ps1`, `expanded-probe.ps1` in the session scratchpad.)
4. Decode: `tshark -r mount.pcap -Y 'usb.device_address==N && usb.capdata' -T fields
   -e usb.endpoint_address -e usb.capdata` → the bulk OUT/IN payloads are the raw
   LX200 ASCII.

Sample captures on the box: `C:\Users\James\AstroDeck\mountprobe.pcap` (the LX200 session).

> Site latitude/longitude returned by `:Gt#`/`:Gg#` are redacted here (the mount reports
> the observatory's precise location); the live values are visible only on the rig.
