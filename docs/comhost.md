# The bundled COM host (`ascom-local`) — operator & developer guide

AstroDeck talks to observatory gear over **ASCOM Alpaca** (REST). Many real
drivers, though, are **ASCOM COM** drivers (in-process Windows COM servers), which
an Alpaca client cannot call directly. Historically the bridge was a **separately
installed ASCOM Remote Server** listening on `127.0.0.1:11111`. The **bundled COM
host** (`astrodeck.comhost`, surfaced as the **`ascom-local`** backend) removes
that separate install: AstroDeck ships its own minimal COM→Alpaca bridge and
starts it on demand.

**This is the single-install milestone.** One AstroDeck install, plus the user's
ASCOM Platform, now drives COM gear end-to-end — **no ASCOM Remote, no NINA, no
hardware-in-the-loop bridge process** to install, configure, or keep running. The
acceptance proof is `server/tests/test_ascom_local_gate.py` (below).

---

## Prerequisite: the ASCOM Platform (you install it)

AstroDeck bundles the *bridge*, **not** the vendor drivers. You still install:

- **The ASCOM Platform** (`https://ascom-standards.org`). Installing it registers
  the **ASCOM Simulator** drivers (Telescope/Camera/Focuser/FilterWheel/Rotator/
  Switch/SafetyMonitor/CoverCalibrator/Dome/ObservingConditions) — those are what
  the acceptance gate drives.
- **Your gear's COM drivers** (ZWO, Player One, Pegasus/Wanderer, EAF, ASICAA…).
  These self-register under `HKLM\SOFTWARE\[WOW6432Node\]ASCOM\<Type> Drivers`.

Windows-only: `winreg` / `comtypes` / the host are Windows-only. Off Windows the
`ascom-local` backend is simply **absent** (not registered, not in `/api/backends`,
not in the drivers surface, `/api/discover/ascom-local` returns `[]`) — clean
degradation, no code path. Install the COM support with:

```
pip install -e "server/.[comhost]"     # pulls comtypes>=1.4.0 (win32 marker-gated)
```

---

## How it connects (discovery)

`ascom_registry.enumerate()` reads the installed COM drivers per type from the
registry (both the 32- and 64-bit views) and assigns each a **deterministic**
`dev_num` = its index in the case-insensitively sorted ProgID list for that type.
Determinism is load-bearing: the server advertises `(dev_type, dev_num)` and the
host independently resolves the same `(dev_type, dev_num) → ProgID` from the *same*
enumeration — no handshake, and **no new Alpaca client** (the existing
`devices/alpaca.py` client is reused unchanged; the host conforms to exactly what
it already calls).

In the UI the `ascom-local` backend appears as **"ASCOM (local)"** on the Equipment
tab, with a **Scan** that lists the enumerated COM drivers per role. Feature
detection is purely by the row's presence in `GET /api/drivers` (absent off
Windows).

## Lifecycle (server-spawned, loopback, no-orphan)

The `ascom-local` backend owns a **process-lifetime `ComHostManager` singleton**
in the server process (not the release supervisor — device runtime ≠ release
management). On the first `ascom-local` rig-open it:

1. **Spawns** `python -m astrodeck.comhost --port 0 --portfile <pf>` as a
   supervised child.
2. Binds **loopback-only on an ephemeral port** (`bind(127.0.0.1, 0)`) — never a
   wildcard, never a fixed port, no UDP discovery broadcast. The chosen port is
   written to the **portfile** (`astrodeck-comhost.json`, the pidfile too), which
   the server reads to learn where to point the Alpaca client.
3. **Health-checks** the management API (`GET /management/v1/configureddevices`)
   before handing back the port.
4. **Auto-restarts** lazily: a crashed child is detected and respawned on the next
   `ensure()` (driven by a user rig-open, not a background poller), throttled by a
   short spawn-backoff so a caller retry loop cannot thrash a crash-looping child.
5. **Never orphans.** The portfile is the dedup key. A leftover portfile only
   exists after an *unclean* crash (a clean `stop()` removes it); before spawning,
   the manager kills the recorded PID **only if a live command-line check confirms
   it is genuinely our comhost** (an OS PID-recycle must never force-kill an
   innocent process), then confirms it is gone. Teardown happens on the FastAPI
   app-shutdown lifespan.

Every marshaled COM call has a **per-call deadline** (`_COM_CALL_TIMEOUT_S = 30s`):
each connected device runs one dedicated STA thread (`CoInitializeEx`
apartment-threaded for its whole lifetime) and all COM access marshals to it via a
queue + `Future`; `future.result(timeout=30)` turns a **wedged COM call** into a
`ComTimeoutError` → Alpaca HTTP 500 → the client's `DeviceError`, **never a hang**.
A timeout also **fault-evicts** the device (drops its slot, abandons the blocked
STA thread) so the *next* call rebuilds a fresh device on a fresh thread — recovery
is per-device, not "restart the whole host".

## The "device in use" reality (single-client)

Most ASCOM COM devices are **single-client**: only one app may hold a device at a
time. If NINA (or any other app) already holds a device, the host's COM connect
**fails with a clear surfaced error** — it does not hang and it does not silently
succeed. Close the other app's connection to that device first. This is why the
astrotown cutover disconnects each device in NINA before connecting it here.

---

## RUNBOOK — running the acceptance gate on a real Windows + ASCOM box

The gate (`server/tests/test_ascom_local_gate.py`) is the **single-install proof**.
It **skips cleanly** anywhere the ASCOM Simulators are not registered (the dev box,
hosted CI) and only truly *exercises* on a machine with the ASCOM Platform +
`comtypes`. To run it for real:

1. **Install the ASCOM Platform** (registers the Simulator drivers).
2. **Install the COM extra** into the server venv:
   ```
   pip install -e "server/.[dev,comhost]"
   ```
3. **Run the gate** (and the per-driver simulator gates):
   ```
   cd server
   pytest tests/test_comhost_simulators.py tests/test_ascom_local_gate.py -q
   ```

**What a pass proves:** a fresh AstroDeck install spawned its *own* bundled COM
host, enumerated the Simulator drivers, and drove **Telescope + Camera + Focuser +
FilterWheel** through the **existing** Alpaca client — slew, expose (frame
round-trip), focuser move, filter change — with **no ASCOM Remote, no NINA, no
hardware, no bridge process the operator had to install**. That is the
single-install milestone, demonstrated.

### On-ASCOM-box validation checklist (what only the real run can prove)

The dev box and hosted CI have no ASCOM Platform / no `comtypes`, so the following
**skip everywhere automated** and are validated **only** on a real Windows + ASCOM
box (e.g. astrotown). Enumerate and check each off on the first real run so nothing
is forgotten:

- [ ] **The acceptance gate itself** — `test_ascom_local_gate.py` PASSES (not
      skips): four roles end-to-end against the Simulators, no NINA/Remote/hardware.
- [ ] **First real comhost subprocess launch** — `ComHostManager.ensure()` spawns
      the real `python -m astrodeck.comhost` child, learns its ephemeral port from
      the portfile, health-checks it, and tears it down without orphaning.
- [ ] **First real `comtypes` connect** — a COM device is actually instantiated on
      its STA thread and `Connected=True` succeeds against a real registered driver
      (this is the first time `comtypes` runs the real COM path, absent on CI).
- [ ] **Fault-eviction against a real wedged driver** (COM-T2 obligation) — a COM
      call that exceeds the **30s** per-call deadline raises `ComTimeoutError` →
      HTTP 500, the device is **evicted**, and the *next* call rebuilds a fresh
      device on a fresh STA thread (the whole host is not restarted). Verify against
      a driver that can be made to block.
- [ ] **`Park()` / `SyncToCoordinates()` vs the 30s deadline on a real mount**
      (COM-T3 obligation) — the real-mount slow-op paths complete within, or
      time-out cleanly against, the per-call deadline (no hang, honest 500 on the
      slow-driver case).

Until those are ticked on a real box, native COM connectivity is proven only by the
**portable** unit/contract/mapping tests (which run on every job) plus this
runbook — the real end-to-end proof is the manual on-ASCOM-box gate run above.

## CI honesty note

The hosted GitHub Windows runner has **no ASCOM Platform, no simulator drivers, no
`comtypes`**, so the gate + simulator tests **SKIP** there — the CI job runs them
but they add coverage only where the Platform is actually installed. The Windows CI
job therefore proves the **portable** surface (registry-parse, COM↔Alpaca mapping
with a fake COM factory, the STA/deadline harness, the Alpaca contract, the
manager lifecycle with an in-process spawn, and the off-Windows degrade). The
simulator-exercising run is the **operator's on-ASCOM-box run** documented above —
that machine, not hosted CI, is where the gate truly executes. Do not read a green
"skipped" gate on hosted CI as simulator coverage.

---

## astrotown migration (spec §7)

`ascom-local` replaces the manually-installed **ASCOM Remote at `127.0.0.1:11111`**:
the same nine devices, now served by the bundled host. **Keep the ASCOM Remote
install until the native path is validated on the real rig** (walk the checklist
above), then uninstall it. Migration is re-assigning each role from the Alpaca
driver (pointing at `:11111`) to **`ascom-local`** on the Equipment tab — the
ProgID map is already known, so there is **no profile addressing to retype**: pick
the **ASCOM (local)** device per role and **Connect Rig**.

The nine devices (ground-truthed from NINA's active profile + PHD2 config,
2026-07-19; `dev_num` here is the ASCOM Remote slot — the bundled host recomputes a
deterministic `dev_num` from the sorted ProgID list, so re-pick per role rather
than assuming the number carries over):

| role (AstroDeck)      | ASCOM COM driver (ProgID-ish)         |
|-----------------------|---------------------------------------|
| telescope             | ASIMount                              |
| camera (main)         | Player One (Poseidon-M PRO)           |
| guide_camera          | ASICamera2 (ZWO ASI220MM Mini)        |
| focuser               | EAF                                   |
| filterwheel           | WandererSnowflake1 (NINA's choice)    |
| rotator               | ASICAA                                |
| covercalibrator       | WandererCover1 (flat panel)           |
| switch                | WandererBox1                          |
| observingconditions   | WandererBoxEnvironment                |

**Single-client caveat:** these physical devices are single-client — close NINA's
connection to each device before connecting it in AstroDeck (NINA's autostart task
stays enabled until you decide to retire it). The OmniSim Alpaca server stays on
`:32323` and is unaffected.

---

## Troubleshooting

- **"device in use" / connect fails immediately** — another app (usually NINA)
  holds that single-client COM device. Disconnect it there, then Connect Rig.
- **A device hangs mid-session** — it cannot: a wedged COM call trips the 30s
  per-call deadline, surfaces as an error, and the device is fault-evicted so the
  next call gets a fresh instance.
- **No `ascom-local` row in the UI** — you are off Windows, or the server was not
  installed with `.[comhost]`, or (on Windows) `comtypes` failed to import. Check
  `GET /api/drivers` for the `ascom-local` row.
- **Nothing enumerates under a role** — no COM driver of that type is registered;
  install the vendor's ASCOM driver (and confirm the correct 32-/64-bit registry
  view).
- **Stale host after a crash** — harmless: the ephemeral bind means there is no
  fixed port to free, and the next rig-open reaps the recorded PID (only if it is
  genuinely our comhost) before spawning a fresh one.
