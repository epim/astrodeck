# Native COM Device Backend (Single-Install) — Design

Status: ratified by user 2026-07-19 ("com device is a go"). First sub-project of the
platform-expansion campaign (native device layer → plugin system → NINA-compat A+B →
resume parity wave). Builds on the pluggable-backend architecture
(`docs/superpowers/specs/2026-06-16-pluggable-backends-rbac-remote-design.md`) and the
vendor-neutral direction.

## §0 Purpose

Remove the separate **ASCOM Remote Server** install. Today AstroDeck's device layer speaks
Alpaca (HTTP), so driving a Windows rig's COM-only ASCOM drivers required installing ASCOM
Remote as a COM→Alpaca bridge (this is exactly the friction the user hit configuring the real
rig on astrotown at 127.0.0.1:11111). This sub-project makes that bridge **native and
bundled**: AstroDeck ships and auto-manages its own minimal COM→Alpaca host, so a Windows
user installs ONE application and sees their gear. Mission frame: "single install for a
newcomer and for an observatory."

## §1 Goals / Non-Goals

Goals:
1. A fresh AstroDeck install on Windows can enumerate, assign, connect, and operate installed
   ASCOM COM drivers with **no other software installed** (no ASCOM Remote, no NINA). The
   ASCOM Platform itself (which provides the drivers) is the user's own prerequisite, as it is
   for any ASCOM app — we do not bundle vendor drivers.
2. **Native discovery**: AstroDeck reads the ASCOM registry directly and presents installed
   drivers per device type for one-click role assignment — no host/port/ProgID typing.
3. **Crash isolation**: a hung or crashing ASCOM driver is contained in a supervised sidecar
   and cannot freeze the guide/sequence loop; the sidecar is restartable.
4. Reuse the EXISTING, proven Alpaca client backend unchanged — the COM host speaks Alpaca on
   loopback (Approach C, user-ratified).
5. Testable end-to-end with NO hardware and NO NINA, against the ASCOM Simulator drivers
   already registered on dev/test boxes.

Non-Goals:
- Not bundling vendor ASCOM drivers or the ASCOM Platform (user prerequisite, standard).
- Not Windows-cross-platform for COM: the host is Windows-only. Mac/Linux keep using native
  Rust drivers + networked Alpaca (the shared Alpaca client already covers networked Alpaca on
  all platforms).
- Not the plugin system (next sub-project) — though the host is designed so it can later be a
  built-in plugin (device-backend plugin category).
- Not removing the NINA bridge or the networked-Alpaca backend — both remain.

## §2 Existing seams

- `server/astrodeck/devices/base.py` — device ABCs (Camera/Telescope/Focuser/FilterWheel/
  Rotator/Switch/SafetyMonitor + CameraFrame/PierSide). Target contract; unchanged.
- `server/astrodeck/devices/backends/native_backend.py` + the Alpaca client it uses — the
  PROVEN client that drives Alpaca devices by (host, port, dev_num) per role. This is what the
  COM host will serve; the client stays as-is (the COM host is just another Alpaca endpoint).
- `server/astrodeck/devices/backend.py` — Backend/BackendSession registry, `ROLES`
  (now includes `guide_camera`). New `ascom-local` backend registers here.
- `supervisor/supervisor.py` — the process supervisor already manages the server + versions;
  extended (or paralleled) to manage the COM host child lifecycle.
- `server/astrodeck/api/app.py` — `/api/discover/{backend}` + Backend Drivers config routes;
  a new `ascom-local` discovery returns registry-enumerated drivers.
- `ui/src/components/settings/DriversPanel.tsx` + `ui/src/views/EquipmentView.tsx` — the
  add/scan/assign surfaces; `ascom-local` appears automatically.
- Test infra: the ASCOM Simulator drivers (`ASCOM.Simulator.*`) + OmniSim are registered on
  the boxes — the COM host is tested against them.

## §3 Architecture (Approach C — bundled COM→Alpaca host)

```
AstroDeck server (asyncio)
  └─ existing Alpaca client backend ──HTTP/loopback──┐
                                                     ▼
                                    astrodeck-comhost  (bundled Python sidecar, Windows-only)
                                      • ASCOM registry enumeration (discovery)
                                      • STA-threaded COM lifecycle per device
                                      • minimal Alpaca device API on 127.0.0.1:<port>
                                      └─COM (comtypes)─▶ installed ASCOM drivers
```

### §3.1 `astrodeck-comhost` (new component)
A standalone Python process (module `astrodeck.comhost`, entry `python -m astrodeck.comhost`)
that:
- **Enumerates** installed ASCOM drivers from the registry (per type, both 32/64-bit views —
  same source proven in the astrotown inventory: `HKLM\SOFTWARE\[WOW6432Node\]ASCOM\<Type>
  Drivers`). Exposes this list over its management API.
- **Instantiates + connects** a driver on demand via `comtypes.client.CreateObject(progID)`,
  on a **dedicated STA apartment thread per device** (ASCOM drivers are STA; a per-device
  thread avoids cross-device head-of-line blocking). All COM calls for a device marshal to its
  thread. Blocking COM calls run with a **per-call deadline** (reusing the imageready-timeout
  pattern from guider-hardening A-T1) → a wedged call raises rather than hanging the host.
- **Serves Alpaca**: a minimal, spec-focused Alpaca device API (ASCOM Alpaca API v1) over
  `http://127.0.0.1:<port>/api/v1/<type>/<n>/...` + `/management/v1/configureddevices`, so the
  EXISTING Alpaca client drives it unchanged. Only the device types AstroDeck uses are
  implemented (see §3.4). Alpaca ClientTransactionID/ErrorNumber semantics honored enough for
  the AstroDeck client (not a general-purpose Alpaca server — internal contract with our own
  client, but spec-shaped so a real Alpaca device is interchangeable).
- Binds **loopback only** (127.0.0.1), an ephemeral or configured port, no discovery broadcast
  needed (AstroDeck reads the host's management API directly, not UDP discovery).

### §3.2 Server-side wiring
- New backend `ascom-local` (`devices/backends/ascom_local.py`) that: on activation, ensures
  the comhost child is running (spawns/adopts it), learns its port, and resolves roles through
  the existing Alpaca client pointed at `127.0.0.1:<port>`. In effect `ascom-local` = "managed
  local COM host + Alpaca client to it." NINA/networked-Alpaca/native/sim backends unchanged.
- **Lifecycle**: the comhost is a supervised child. Options weighed in the plan: (a) the
  existing `supervisor/` manages it alongside the server; (b) the server process spawns +
  monitors it. Decision deferred to the plan, but the REQUIREMENT is: auto-start on first
  need, auto-restart on crash, clean shutdown, and never leave an orphan on 8800-style port
  reuse (apply the dedup/port-free lessons from the astrotown deploy incident).
- **Discovery endpoint**: `GET /api/discover/ascom-local` returns the registry-enumerated
  drivers (type, ProgID, display name) from the comhost — this is the native "scan."

### §3.3 UI
- Backend Drivers: `ascom-local` ("ASCOM (local)") is **always available** on Windows (like
  the built-in Simulator/native/ASTAP) — no add-driver step. Its **Scan** lists installed
  drivers per type; picking one assigns that ProgID to the role. No host/port/ProgID entry.
- Equipment: role dropdowns offer the ascom-local devices; assign + Connect Rig as today.
- Non-Windows: `ascom-local` is absent (COM unavailable); the panel shows native/Alpaca/NINA
  as before. UI must degrade cleanly (feature-detect via a capability flag in status).

### §3.4 Device-type coverage
Map each ASCOM type ↔ AstroDeck ABC ↔ Alpaca device API: Telescope, Camera, Focuser,
FilterWheel, Rotator, Switch, SafetyMonitor, ObservingConditions, **CoverCalibrator** (flat
panel — also unblocks the flats wave B), **Dome** (unblocks dome wave C). Camera is the
richest (binning, gain/offset, cooler, image download as the Alpaca `imagearray`); implement
the subset AstroDeck's `Camera` ABC needs. Each type ships behind a golden test vs its ASCOM
Simulator driver.

## §4 Error handling
- Driver connect failure → Alpaca error → existing per-role `RoleResult` "attempted+failed"
  (no crash); the honest surfaces already handle this.
- Wedged COM call → per-call deadline → Alpaca 500/timeout → the guider's exposure-retry
  envelope + the imaging paths handle it; repeated failure → the host marks the device faulted.
- comhost crash → supervisor restarts it; in-flight requests fail cleanly; the client
  reconnects. Never an orphaned host on the port (port-free gate before restart).
- Single-client reality: a device already held by NINA/another app → COM connect fails with a
  clear "device in use" surfaced to the UI (not a hang).

## §5 Testing / acceptance gate
- Unit: registry enumeration parser; per-type COM↔Alpaca mapping (mock COM); the STA-thread
  marshaling + per-call deadline.
- Integration (the gate): against the **ASCOM Simulator drivers** on a Windows CI/dev box —
  comhost enumerates them, the AstroDeck Alpaca client connects Telescope+Camera+Focuser+
  FilterWheel through the host, slews/exposes/moves/changes filter, and a frame round-trips —
  **no ASCOM Remote, no NINA, no hardware**. This is the "single-install works" proof.
- Non-Windows: the suite skips COM tests but asserts `ascom-local` is absent + UI degrades.
- Full server suite green; the COM gate wired into the Windows CI job.

## §6 Phasing (one plan)
T1 registry enumeration + discovery endpoint (no COM yet) → T2 comhost skeleton + Alpaca
management API + STA-thread/per-call-deadline harness → T3 Telescope+Camera COM↔Alpaca (the
core imaging pair) vs simulators → T4 Focuser+FilterWheel+Rotator+Switch+SafetyMonitor →
T5 CoverCalibrator+Dome+ObservingConditions → T6 server `ascom-local` backend + supervised
lifecycle + UI wiring → T7 integration gate (simulators, no-NINA) + CI + docs +
astrotown migration note (retire the manual :11111 ASCOM Remote).

## §7 Migration
On astrotown, `ascom-local` replaces the manually-installed ASCOM Remote (:11111): same nine
devices, now served by the bundled host. Keep the ASCOM Remote install until the native path
is validated on the real rig, then it can be uninstalled. The device MAP (ProgIDs) is already
known (memory `astrodeck-project`), so migration is re-assigning roles to `ascom-local`.

---

## Delivered (COM-T1..T7)

Delivered by the plan `docs/superpowers/plans/2026-07-19-native-com-device-backend.md`
(tasks COM-T1..T7). Operator + developer runbook, the astrotown ASCOM-Remote
retirement note, and the **on-ASCOM-box validation checklist** (the real gate run,
the first real comhost subprocess launch, the first real `comtypes` connect,
fault-eviction vs a real wedged driver, and `Park()`/`SyncToCoordinates()` vs the
30s deadline on a real mount) live in `docs/comhost.md`. The single-install
acceptance gate is `server/tests/test_ascom_local_gate.py` (skips cleanly without
the ASCOM Platform + simulators; the real proof is the on-ASCOM-box run). The
`ascom-local` Windows CI job (`.github/workflows/ci.yml`, job `windows-com`) runs
the portable COM surface on a real Windows interpreter and skips the
simulator-exercising gate on hosted runners (no Platform) — see the doc's CI
honesty note.
