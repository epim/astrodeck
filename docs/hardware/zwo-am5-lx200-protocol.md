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
| `:GR#` | Get RA | `HH:MM:SS#` | `HH:MM:SS#` (example redacted: read at the home position these give the site, #140 #166) |
| `:GD#` | Get Dec | `sDD*MM:SS#` | `+90*00:00#` |
| `:GA#` | Get Altitude | `sDD*MM:SS#` | `sDD*MM:SS#` (example redacted: read at the home position these give the site, #140 #166) |
| `:GZ#` | Get Azimuth | `DDD*MM:SS#` | `360*00:00#` |
| `:Gt#` | Site latitude | `sDD*MM:SS#` | `sDD*MM:SS#` (redacted) |
| `:Gg#` | Site longitude | `DDD*MM:SS#` (0–360°, W-positive) | `DDD*MM:SS#` (redacted) |
| `:GG#` | UTC offset | `sHH:MM#` (hours to add to local→UTC) | `sHH:MM#` (example redacted: read at the home position these give the site, #140 #166) |
| `:GL#` | Local time | `HH:MM:SS#` | `HH:MM:SS#` (example redacted: read at the home position these give the site, #140 #166) |
| `:GS#` | Sidereal time | `HH:MM:SS#` | `HH:MM:SS#` (example redacted: read at the home position these give the site, #140 #166) |
| `:GC#` | Calendar date | `MM/DD/YY#` | `MM/DD/YY#` (example redacted: read at the home position these give the site, #140 #166) |
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
| `:CM#` | Sync to the set target | `N/A#` when taken (away from the pole); with the tube at the home position (bench, 2026-10-08) every sync was refused, all but one with `e11#`. **The reply is not proof**: see "Sync (`:CM#`) is verified by reading back" below |

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

**`e6#` = outside the mount's own slew limits (observed 2026-09-06, fw 1.8.8).**
The post-restart re-centre asked for NGC 604 at **~9 degrees altitude** and `:MS#` answered
`e6#`; the solve-and-sync's re-slew to the pole region a minute earlier was accepted, and
the identical goto succeeded later the same night once the target had risen. That is the
AM5's own horizon/altitude limit refusing the destination — not AstroDeck's safety floor,
which had already passed it. **The rest of the `eN` table is UNVERIFIED**: ZWO publishes no
e-code list, and `e14` and `e6` are the only two this project has seen on the wire, so the
driver (`_GOTO_REFUSALS` in `server/astrodeck/devices/backends/zwo_am5.py`) puts words to
those two and says only "altitude, meridian or park limits are the usual reasons" for any
other code. Do not add a meaning here without a capture to back it.

**`e11#` = seen in answer to `:CM#` (sync) at the HOME position (bench, 2026-10-08,
fw 1.8.8).** Every sync sent with the tube at home was refused: all but one answered
`e11#` and moved nothing, sync-to-self included, tracking on or off; one answered `N/A#`
and did not move. Its meaning beyond "where it was seen" is unknown, so the driver never
reads it as proof that the tube is at home, and never answers it with a slew. It picks its
words from the refusal's read-back: with the mount's opinion within 5 deg of the synced
coordinates (or unread), "if the tube really is at home, use Trust position; a sync away
from the pole then works"; further out, "tube not where the mount thinks: bring it home by
eye with a pad key, then Trust position" (`SYNC_E11_ELSEWHERE_DEG`, which mirrors the
resume ladder's `RECOVERY_REFUSED_SYNC_MAX_DEG`). The codes seen on the wire are now
`e6`, `e11` and `e14`.

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
  > **Caveat (2026-10-08):** this held away from the pole only. With the tube
  > at the HOME position (bench) sync-to-self answered `e11#` and the mount took
  > nothing; see
  > "Sync (`:CM#`) is verified by reading back" below.
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
`:GAT#` → `0#` (tracking flag; see the validation results above; NOT an at-target or
arrival signal), `:GFR1#`/`:GFD1#` → `22438#` (axis encoder counts).

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

## Sync (`:CM#`) is verified by reading back (#850, bench 2026-10-08)

**The incident, 2026-10-07.** Three centring syncs of 2.2 to 2.8 degrees at Dec +34,
near the meridian, changed nothing. The hub logged "solved & synced", the next goto
was zero length, the next solve showed the field unmoved, and the run imaged the wrong
field for hours (#852). The driver of the day treated every `:CM#` reply except `e14`
as success, threw the reply away and never read the position back, so **what the mount
answered that night is unknown**.

**The bench, real AM5N, fw 1.8.8, 2026-10-08:**

| Where the tube was | `:CM#` reply (the link strips the `#`) | `:GR#`/`:GD#` read 1 s later |
|---|---|---|
| Away from the pole (Dec +35, hour angle +2.5 h, tracking on); 0.5 to 5 deg offsets in Dec and in RA | `N/A` every time | exactly the synced coordinates, within 0.002 deg |
| HOME position (tube at the pole), tracking on or off | every sync there was refused: all but one answered `e11`, sync-to-self included | did not move |
| HOME position, the one exception: a 5 deg sync | `N/A` | did NOT move |

So the reply is a hint and the read-back is the test. How soon after the reply the
report updates has not been measured: every read on the bench was taken 1 s after.

**What the driver does** (`ZwoAm5Telescope.sync`, `server/astrodeck/devices/backends/zwo_am5.py`):

1. `:Sr#`, `:Sd#`, `:CM#`. An `eNN` (or any answer but `1`) to the target is a refusal
   (`SyncRefused`, `e14` with the parked probe); a link failure there is
   `SyncUnverified` ("the link failed before the sync was sent"). `:CM#` is never
   resent blind after a reopen: a mount that restarted under the dropped link has lost
   its target and would sync to whatever it holds. On a link failure the driver sets
   the target again and sends `:CM#` once more (harmless if the first was taken and
   only its reply lost); a second failure is `SyncUnverified`.
2. A reply matching `[eE]\d+` is a refusal (`SyncRefused`). The position is read
   back once anyway, best effort, so the refusal carries how far the mount's own
   opinion is from the synced coordinates (`residual_deg`).
3. Any other reply (`N/A`, or anything a firmware might say instead) is judged by the
   read-back: within `SYNC_VERIFY_DEG` (0.05 deg) of the synced coordinates, by angular
   separation, it is taken; otherwise the driver re-reads up to twice more, 0.5 s
   apart. A read that fails (no answer, unreadable, not finite) is retried the same
   way. The LAST read decides: a good read still outside the tolerance raises
   `SyncRefused` with the reply; a failed one raises `SyncUnverified` ("the mount did
   not answer the position read after the sync"), not a refusal. A reply other than
   `N/A` that the read-back confirms is logged once.
4. The reply is quoted in a text only when it matches `[A-Za-z0-9/]{1,8}`. An empty
   reply is "an empty reply" and anything else "an unrecognised reply", never quoted:
   a desynchronised link can hand the sync a `:GR#`-shaped answer, and at home that
   is local sidereal time. The link's own timeout text reports how many bytes arrived,
   never the bytes.
5. Only a verified sync clears the reset latch (`position_known`, #144).

No message carries the read-back's RA or Dec: at home it is the pole, and its RA
follows local sidereal time, so either is a site oracle (#140, #166). Separations
in degrees are allowed.

**Consequence for a reset mount.** After a power cycle the mount reports home wherever
the tube is, so its position is unknown, and with the tube at home the bench saw every
sync refused. Do not slew or go to a target from there: a goto is aimed from the position
the mount believes, and with the tube elsewhere it lands somewhere unknown. The safe
order is:

1. If the tube really is at home, use Trust position.
2. If it is not, hold a pad key and bring it home by eye (hold-to-move computes no
   destination), then use Trust position.
3. Only then does a goto away from the pole, followed by a solve and sync there, refine
   the pointing.

A mount whose own position disagrees with the sky and refuses the correction stops the
target (the read-back above). Away from the pole the bench has since seen syncs taken up
to at least 5 deg (#857), so a refusal there has a cause nobody has captured yet; the
driver now keeps the reply.

**An observation, cause unknown (2026-10-08 run).** The run's four centring syncs, of
167.7', 167.9', 168.4' and 168.4', again "changed nothing". In all four the target had
just crossed the meridian and the solved field had not: the two lay on opposite sides of
it. The refused syncs of 2026-10-07 did NOT fit that pattern; there the target and the
field were on the same side. Recorded only; nothing in the driver is built on it.

## GoTo (`:MS#`) arrival is verified, not assumed (#860)

**The old rule, and why it was a claim nothing kept.** `ZwoAm5Telescope.slew` used to
return when two consecutive 0.5 s position reads differed by less than `SETTLE_DEG`
(0.05 deg). It never compared the settled report with the commanded target. A mount that
never started read as settled at its third read, about 1.5 s after `:MS#`; a mount halted
part way (a Stop, or the hub's manual-move deadman sending `:Q#`) read as arrived. The step
also had no RA wrap, so a still mount reading 23:59:59 then 00:00:00 looked like 360 deg
of slew.

**The new rule** (`server/astrodeck/devices/backends/zwo_am5.py`). ARRIVED is two things
at once:

- the report is still: two consecutive poll-to-poll steps under `SETTLE_DEG`, measured
  the short way round in RA (`_moved_deg`);
- the report is within `GOTO_ARRIVE_DEG + GOTO_DRIFT_DEG_S x elapsed` of the commanded
  target, by angular separation (`coords.angular_sep_deg`), so a report near the pole
  that differs by hours of RA is measured as the small distance it is.

Still but short is not an arrival. Polling goes on until the mount arrives or is STUCK
for `GOTO_STALL_S`, which raises `GotoNotArrived` with the fixed reason "the mount
stopped short of the target". Stuck is also two things at once: every step across the
window under `SETTLE_DEG`, AND the separation fallen by less than `GOTO_PROGRESS_DEG`
across it. Each half alone fails:

- small steps alone would halt a healthy slow final approach: the AM5's R5 rate, about
  7.8x sidereal = 0.0326 deg/s, steps 0.016 deg per poll, under `SETTLE_DEG`;
- no progress alone would halt a healthy pier-flip goto, whose separation GROWS at slew
  speed for some seconds while the Dec axis swings through the pole; those steps are far
  over `SETTLE_DEG`, so the small-step half keeps the stall from being declared.

A whole-mount halt sent while the goto runs (the driver counts them in `_halt_gen`)
raises `GotoNotArrived` with the reason "a stop was sent during the goto", unless the
mount is already at the target. The `SLEW_TIMEOUT_S` deadline (120 s) is unchanged and
still a plain `DeviceError`; the stall window runs inside it. Every abnormal exit halts
the mount with `:Q#` first. No message carries a coordinate; the separation in degrees
is allowed.

**The constants, with their arithmetic:**

| constant | value | derivation |
|---|---|---|
| `GOTO_ARRIVE_DEG` | 0.10 deg | `:Sr#` RA rounding 0.0021 + `:Sd#` Dec rounding 0.0003 + `:GR#` read 0.0021 + `:GD#` read 0.0003 + a guide pulse still running (1.0 s cap x 0.004178 deg/s) 0.0042 = about 0.009 deg; 0.10 is 2 x `SETTLE_DEG`, an 11x margin |
| `GOTO_DRIFT_DEG_S` | 0.0041781 deg/s | the sidereal rate, 15.041 arcsec/s / 3600; covers a firmware that goes to a fixed hour angle with tracking off. At the 120 s deadline the tolerance is 0.10 + 0.0041781 x 120 = 0.60 deg |
| `GOTO_STALL_S` | 10 s | outlasts the `:MS#` start latency and any pause in a healthy goto, neither measured; a `:hP#` the mount took is moving within 1-2 s, so 10 s is 5x that bound. Counted as ceil(10 / 0.5) = 20 poll comparisons |
| `GOTO_PROGRESS_DEG` | 0.10 deg | what a stuck mount can fake across one window: tracking-off drift 0.0041781 x 10 = 0.0418 + two residuals' read quantization 2 x 0.0024 = 0.0048, 0.0466 deg in all; 0.10 is 2.1x that. R5 makes 0.33 deg per window, 3.3x the floor |

Limits stated: a final approach under 2x sidereal (R3 is 1.9x, 0.079 deg per window)
cannot be told from drift by any window, and would read as stuck. Nothing says a goto
uses R3 (those are the jog presets); the bench below measures it. The 0.10 deg floor
holds while a window lasts under (0.10 - 0.0048) / 0.0041781 = 22.8 s, i.e. a `:GR#` plus
`:GD#` pair under 0.64 s; past that a drifting stuck mount can read as progress and the
deadline ends it instead: late, never silent.

**Refusals.** `e14` to `:MS#` is now `GotoRefused`, like `e6`, with the parked probe
choosing the words ("the mount is parked; unpark first", or "not in a state to move: a
limit, or a slew already running"). The code stays in the message, never in the reason,
because the resume ladder writes the reason into a hold reason (#618). An `eN` code not in
the driver's table keeps today's message, code included, and its reason is fixed words
with no code.

**`:MS#` is sent once.** It is never re-sent after a reopen: a mount that took the goto
and is slewing would answer a re-send `e14` and leave the first goto unwatched. A link
failure on `:MS#` halts the mount best-effort (`:Q#`, which may reopen the port) and
raises `GotoNotArrived` ("the link to the mount failed during the goto") with the same
message as before. Before `retry=False` a dropped exchange was re-sent and the goto went
on; the typed raise keeps that one dropped reply from ending a run, because the centring
loop solves where the mount is instead. The 120 s deadline stays a plain `DeviceError`: a
goto that neither arrives nor stalls in that time ends the run with a named error.

**Tracking after a halted goto.** Every `GotoNotArrived` comes after a `:Q#`, and whether
`:Q#` stops sidereal tracking on this firmware is not measured (item 3 of the at-scope
checks below). After a miss, `goto_and_center` turns tracking back on (best effort,
idempotent) before it solves, so a centred return never images on a mount the halt left
untracked, whichever way the bench answers.

**A re-sent `:MS#` to a power-cycled mount is not a hazard.** The AM5 powers up PARKED
and refuses every motion command with `e14` until `:Spu#`, and the driver's connect and
reopen never unpark, so a re-send would be answered `e14` and raise `GotoRefused` with the
parked words.

**Measured fact the constants rest on:** the 2026-07-20 at-scope gotos landed Dec +40
targets with a 0.000 deg residual (validation results above).

**Bench questions still open (HARDWARE-PENDING, #860).** With the server stopped, at night
(Sun below -6 deg), with the script's own Sun-cone and horizon gates and the position known:
the `:MS#` start latency; any still pause inside a healthy goto, a pier-flip goto included;
how long a flip goto's separation grows; the final residual with tracking on; where the
report sits after a goto with tracking off; and the approach-speed profile from 1.0 deg
in, including the least fall of the separation across any 10 s window, the exact quantity
the stall rule tests; and, with tracking on, whether `:GAT#` still reads tracking after a
`:Q#`. Print separations, rates, elapsed seconds and the tracking flag only.

## Home (`:hP#`) is not a pointing-model reset (#857)

`:hP#` homes AND parks. The AM5 has no home sensor: it drives to where its pointing MODEL
places home, so a model that is off homes off by about the same amount. On 2026-10-07 the
model was about 2.8 deg off before a home, and about 4.25 deg off at the next centring,
after it had kept walking on a paused guided run. Homing neither resets nor corrects the
model.

What does set the frame:

- a verified sync from a solved frame away from the pole (the read-back above);
- a power-up with the tube at true home (a powered-up AM5 reports home wherever the tube
  is, so the report is right only if the tube really is there).

The bench, 2026-10-08: at home every sync was refused (`e11`, and one `N/A` that moved
nothing). Away from the pole (Dec +35, hour angle +2.5 h) syncs of up to at least 5 deg on
both axes read back exactly and undid cleanly.

Recovery:

- **position known:** the normal goto-and-centre away from the pole (goto, solve, one
  sync read back). That repairs a model off by up to at least 5 deg, home or no home.
- **position unknown:** the safe order. If the tube really is at home, Trust position.
  If not, bring it home by eye with a pad key, then Trust position. Only then a goto away
  from the pole, a solve and a sync.

**A set-home or zero command:** none is captured and the driver uses none. Its only home
command is `:hP#`. The 2026-07-19 sweep tried `:hR#`/`:hU#`/`:hN#`/`:hF#`/`:hW#`/`:hS#`/`:hP#`/
`:I#`/`:PO#`/`:MP#` only while PARKED, where all answered `e14`, so nothing is known about
them unparked. Untested candidate: `:hF#`, which in the OnStep family (whose `:GU#` style
this firmware follows) is "reset the mount at the home position". Its meaning on the AM5
is unknown and no code sends it (HARDWARE-PENDING, #857: read the ZWO ASIMount driver's
trace logs for `:h` commands first, then a supervised bench with a hand on the power
switch). The ZWO app's zero-position function runs over Bluetooth/Wi-Fi and has not been
seen on this serial port. Sync acceptance above 5 deg away from the pole is unmeasured.

## The reported RA/Dec walks during a run (measured 2026-08-21 and 2026-09-06)

The coordinates the AM5N reports (`:GR#`/`:GD#`, and the alt/az it derives from
them) drift away from where the telescope is actually pointing while a guided
run is in progress. The tube does not move: star fields matched frame to frame
within a dither while the report walked. This is the mount's model of itself
diverging, not a transport or parsing fault (reported alt/az stay
self-consistent with the reported dec throughout).

| night | window | what the report did | what the field did |
|---|---|---|---|
| 2026-08-21 | autofocus runs + guider calibration and start | +12.9 arcmin/min in dec | held (two L frames 36 min apart match star for star) |
| 2026-08-21 | plain imaging with dithers | -10.3 arcsec/min, smooth | held |
| 2026-09-06 | guided cycle, 30 min | +50 arcmin north (OBJCTDEC +30 47 -> +31 51 in consecutive subs) | held within a dither (field_shift star matching) |

Mechanism SUSPECTED, not proven: the emulated pulse guide (`:Mn#`/`:Ms#`/`:Mw#`
rate moves plus the tracking suspend used for east) is applied by the firmware
to its own coordinate model at a rate that does not match the physical motion.
The fast regime coincides with the phases that pulse the most.

What AstroDeck does about it (shipped):

- the meridian flip, the altitude floor and the horizon gates are scheduled
  from the TARGET's coordinates and the clock, never from the report
  (`sequence/engine.py`, since 0.3.2x);
- sub headers carry the last plate-solved pointing in `OBJCTRA`/`OBJCTDEC`
  and the raw report in `MOUNTRA`/`MOUNTDEC`, with `PNTGSRC` naming the source
  (2026-09-06, spec GN-07);
- when the field identification is cleared because "the mount has moved" by
  more than the field, the right response is a re-solve and re-sync, which the
  rig can do on demand.

At-scope checks still owed (do these with the guider idle, tracking on):

1. `:Gm#` pier-side semantics across a real meridian flip. On
   2026-09-06 the mount still answered `W` after the flip goto had completed
   and re-centred, so either the report lags the flip or the goto did not
   change sides. Read `:Gm#` and `:GU#` before the goto, after it settles, and
   again 60 s later; log all three with `:GR#`/`:GD#`. The guider's
   pier-change recalibration (spec GN-01) no longer depends on this answer,
   but the doctor and the flip scheduler would like to know it.
2. Bench test of the walk itself: park, unpark, sync to a plate solve, then
   drive a known count of 500 ms pulses per direction with the guider stopped
   and read `:GR#`/`:GD#` after each block. Compare the reported displacement
   to the physical one measured by a second solve. This decides whether the
   model drift is proportional to pulse count (firmware bookkeeping) or to
   time (a clock or rate error).
3. Whether `:Q#` disturbs tracking on this firmware (open runbook item from
   the halt-window work), since the same suspend-and-resume pattern is what
   the east pulse uses.
