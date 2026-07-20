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
4. **Motion / control commands** (slew `:MS#`/`:Mn#`…, sync `:CM#`, park, rate set,
   pulse-guide `:Mgn####`, site/time set `:St#`/`:Sg#`/`:SL#`…) follow the same LX200
   framing but were **not** exercised here (read-only capture — no motion). Capture
   those live from a known-good client (or issue them directly) when building the
   write path, and verify each against the mount before trusting it.
5. **ZWO's own ASCOM driver is bypassable.** During capture, `ASCOM.ASIMount.Telescope`
   (v6.5.24) reported `Connected=False` and returned zeroed coordinates, yet direct
   LX200 over the CDC port worked perfectly — confirming the native path is both viable
   and independent of ZWO's driver stack.

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
