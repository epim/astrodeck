> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan. Dispatch each task to a fresh implementer subagent; each task is independently testable and reviewable. Implementers see ONLY their own task's Files/Interfaces/Steps — every signature a later task depends on is restated in that later task's Interfaces. Follow strict TDD: write the failing test exactly as given, run it and see the expected failure, implement, run and see the expected pass, then commit with the exact `git add` listed. NEVER `git add -A` (the repo root carries reviewer scratch — e.g. `drive_hero_flows.mjs`).

# Native COM Device Backend (Single-Install) — Implementation Plan

**Goal.** Remove the separate ASCOM Remote Server install (spec `docs/superpowers/specs/2026-07-19-native-com-device-backend-design.md`). AstroDeck ships and auto-manages its own minimal COM→Alpaca host (`python -m astrodeck.comhost`, Windows-only) so a Windows user installs ONE application and drives their COM-only ASCOM drivers directly: native registry discovery (no host/port/ProgID typing), crash isolation in a supervised sidecar, and end-to-end operation of Telescope/Camera/Focuser/FilterWheel/Rotator/Switch/SafetyMonitor — all proven against the **ASCOM Simulator drivers** with no hardware, no NINA, no ASCOM Remote. CoverCalibrator/Dome/ObservingConditions are served host-side to unblock the later flats/dome/weather waves.

**Architecture (Approach C — bundled COM→Alpaca host, user-ratified).** A standalone Python sidecar process serves a minimal ASCOM Alpaca v1 device API on `http://127.0.0.1:<ephemeral>` and talks COM to installed drivers via `comtypes`. The AstroDeck server's **EXISTING, unchanged Alpaca client** (`server/astrodeck/devices/alpaca.py` — `AlpacaConnection` + the `Alpaca*` device classes) drives the sidecar; the sidecar is just another Alpaca endpoint. A new `ascom-local` backend (`server/astrodeck/devices/backends/ascom_local.py`) ensures the sidecar is running, learns its port, and resolves roles through that same Alpaca client pointed at loopback. Registry enumeration is a shared, import-light, deterministic module (`server/astrodeck/devices/ascom_registry.py`) used by BOTH the server (for the discovery/offers surface) AND the sidecar (to know which ProgID a device number maps to) — because enumeration is deterministic, the two agree on `(dev_type, dev_num) → ProgID` with **no handshake and no new client**. Per device: a dedicated STA apartment thread with all COM calls marshaled to it and a per-call deadline (the `AlpacaCamera.expose` imageready-timeout idiom, commit `082dd79`).

```
AstroDeck server (asyncio)
  └─ EXISTING Alpaca client (alpaca.AlpacaConnection / AlpacaCamera…)   [UNCHANGED]
        │  HTTP / 127.0.0.1:<port>   ← ascom_local.AscomLocalSession injects the port
        ▼
   astrodeck-comhost  (python -m astrodeck.comhost, Windows-only, ThreadingHTTPServer)
     • ascom_registry.enumerate()  → /management/v1/configureddevices
     • per-device STA apartment thread + queue + per-call deadline
     • DEVICE_API[type][get|put][method] → COM member (comtypes)
        └─ COM ─▶ installed ASCOM drivers (ASCOM.Simulator.* on dev/CI)
```

**Tech stack.** Python 3.12 backend (`server/astrodeck`), FastAPI (already a dep) for routes; the sidecar uses **stdlib `http.server.ThreadingHTTPServer`** + `comtypes` only (import-light, fast start, no server-app import); `numpy` (core dep) for image arrays; React/TypeScript UI (`ui/`). New dep: `comtypes` (Windows-only, added in COM-T2). Tests: `pytest` + `pytest-asyncio` + `pytest-xdist` (server); the pure-`tsx` manual harness (UI).

## Global Constraints

These bind EVERY task; copy them verbatim into each implementer's context.

- **Reuse the EXISTING Alpaca client — do NOT design a new client.** The whole premise (spec §3, Approach C) is that `server/astrodeck/devices/alpaca.py` is unchanged. The sidecar CONFORMS to exactly what that client already calls: `GET /management/v1/configureddevices` returning `{"Value":[{"DeviceType","DeviceNumber","DeviceName","UniqueID"},…]}`; `GET`/`PUT` on `/api/v1/<type>/<n>/<method>` with `ClientID` + `ClientTransactionID` params (query for GET, form-encoded for PUT); response envelope `{"Value":…,"ErrorNumber":0,"ErrorMessage":"","ClientTransactionID":…,"ServerTransactionID":…}`. The client's `AlpacaConnection._unwrap` treats HTTP≠200 as `DeviceError`, and `ErrorNumber≠0` as `DeviceError(ErrorMessage)`. Any task tempted to add a client method or endpoint outside this shape is wrong — cross-check the sidecar surface against `alpaca.py` before writing it.
- **STA-per-device + per-call deadline.** ASCOM drivers are STA. Each connected device gets ONE dedicated `threading.Thread` that `CoInitializeEx(COINIT_APARTMENTTHREADED)`s for the device's whole lifetime; ALL COM access (create, `Connected=`, every property/method) marshals to that thread via a queue + `concurrent.futures.Future`. Every marshaled call has a deadline (`_COM_CALL_TIMEOUT_S = 30.0`, the imageready-timeout idiom from `AlpacaCamera.expose`, commit `082dd79`): `future.result(timeout=…)` — a wedged COM call raises `ComTimeoutError` → Alpaca HTTP 500, never a hang.
- **Loopback-only bind.** The sidecar binds `127.0.0.1` on an **ephemeral port** (`bind (127.0.0.1, 0)`), never a wildcard, never a fixed port, no UDP discovery broadcast. The chosen port is written to a portfile the server reads.
- **Supervised-child, no-orphan lifecycle.** The sidecar is a supervised child of the SERVER process (decision + rationale below). Auto-start on first `ascom-local` need, health-checked, auto-restart on crash, clean terminate on server shutdown, and NEVER an orphan: apply the astrotown dedup lesson — before spawning, loop-kill any stale child recorded in the pidfile and confirm it is gone (there is no fixed port to free because the bind is ephemeral, so the pidfile IS the dedup key).
- **Windows-only host, clean cross-platform degradation.** `winreg`/`comtypes`/the sidecar are Windows-only. `ascom_registry.enumerate()` returns `[]` off Windows (guarded `import winreg`). `AscomLocalBackend` self-registers ONLY on Windows; the `describe_all()` implicit `ascom-local` row is emitted ONLY on Windows; `/api/discover/ascom-local` returns `[]` off Windows. The UI feature-detects by the ROW'S PRESENCE in `GET /api/drivers` (absent off Windows → no dropdown entries, no code path). Nothing off Windows may import `comtypes` or `winreg` at module top.
- **Test against the ASCOM Simulator drivers — no hardware, no NINA, no ASCOM Remote.** The integration gate drives the EXISTING Alpaca client through the running sidecar against `ASCOM.Simulator.Telescope/Camera/Focuser/FilterWheel/Rotator/Switch/SafetyMonitor/...`. These tests are `@pytest.mark.skipif(sys.platform != "win32" or not _sim_registered(...), reason=…)` and skip cleanly on the Linux CI baseline. Every registry-parse and COM-mapping unit test is **mockable** (inject a fake `winreg` reader / a fake COM factory) and runs on ALL platforms — the simulator integration test is the real proof, the unit test is the portable proof.
- **Server suite baseline: 1210 passed / 0 failed** under xdist `-n auto` in CI (`-n 12` local default — `[tool.pytest.ini_options] addopts = "-n 12"`): `cd server && ./.venv/Scripts/python.exe -m pytest -q`. Each task states its expected delta. Cross-platform unit tests change the universal count (the running Linux-baseline totals below); Windows-only integration tests are SKIPPED on the Linux baseline and only add `passed` on the Windows CI job — each such task notes both.
- **New Python dep (`comtypes`), platform-gated, in an extra.** COM-T2 adds `comtypes>=1.4.0` to a new `comhost` optional-dependencies group in `server/pyproject.toml`, marker-gated: `comhost = ["comtypes>=1.4.0; sys_platform == 'win32'"]`. The Windows bundle/installer installs `.[comhost]`; Mac/Linux never pull it (the marker prevents it even if the extra is requested). No `comtypes` import anywhere except inside the guarded sidecar/registry modules.
- **UI pure-test idiom.** Manual harness (`test`/`assert`/`passed`/`failed`), run via `npx tsx src/lib/__tests__/<name>.test.ts` from `ui/`, ending with the `globalThis` `process.exit` guard — no `@types/node`:
  ```ts
  console.log(`<name>.test.ts: ${passed} passed, ${failed} failed`);
  if (failed) {
    failures.forEach((f) => console.error(f));
    (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
  }
  ```
- **Never `git add -A`.** Every commit lists exact files. Every commit message ends with a blank line then EXACTLY these two trailers:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

**Lifecycle decision (spec §3.2 left open): SERVER-SPAWNED + monitored, not supervisor-managed.** `supervisor/supervisor.py` is a standalone stdlib watchdog whose sole job is the per-RELEASE lifecycle — launch the server, apply staged updates on exit-code 92, health-check, roll back (`_resolve_target`/`_await_health`/`run_forever`). The comhost is per-INSTALL and per-DEVICE, started **on demand** (only when a user connects an `ascom-local` rig), Windows-only, and optional. Wiring it into the supervisor would couple update-management to device-runtime and require the server to signal the supervisor ("start the host now"), which the supervisor has no channel for. Therefore the `ascom-local` backend owns a process-lifetime `ComHostManager` singleton IN the server process that spawns/adopts/restarts the child and tears it down on the FastAPI app-shutdown lifespan. Same crash-isolation guarantee (separate process, restartable), correct ownership (device runtime, not release management).

**Task order (spec §6):** COM-T1 (registry enumeration + discovery endpoint, no COM) → COM-T2 (sidecar skeleton + Alpaca management API + STA/deadline harness) → COM-T3 (Telescope + Camera COM↔Alpaca vs simulators) → COM-T4 (Focuser + FilterWheel + Rotator + Switch + SafetyMonitor) → COM-T5 (CoverCalibrator + Dome + ObservingConditions, host-side) → COM-T6 (server `ascom-local` backend + supervised lifecycle + UI) → COM-T7 (integration gate + CI + docs + migration note).

Running Linux-baseline totals (cross-platform tests only; Windows-only integration adds on the Windows job): T1 → **1216**, T2 → **1224**, T3 → **1232**, T4 → **1242**, T5 → **1248**, T6 → **1258**, T7 → **1260**.

---

### COM-T1 — ASCOM registry enumeration + `ascom-local` discovery endpoint

Registry enumeration is the "native scan": read installed ASCOM drivers per type directly from `HKLM\SOFTWARE\[WOW6432Node\]ASCOM\<Type> Drivers` (both 32/64-bit views), assign a DETERMINISTIC per-type device number, and expose the list over `GET /api/discover/ascom-local`. No COM, no sidecar, no backend registration, no UI yet — a self-contained, shippable increment. The module is import-light (stdlib only) so the COM-T2 sidecar can reuse it.

**Files**
- Create `C:\Users\bear\astro\server\astrodeck\devices\ascom_registry.py`.
- Modify `C:\Users\bear\astro\server\astrodeck\api\app.py` — add a dedicated `GET /api/discover/ascom-local` route immediately after `discover_alpaca_one` (:1121-1139), BEFORE the generic `/api/discover/{backend}` (:1227) so the literal path wins.
- Create `C:\Users\bear\astro\server\tests\test_ascom_registry.py`.
- Create `C:\Users\bear\astro\server\tests\test_discover_ascom_local.py`.

**Interfaces**
- Produces (consumed by COM-T2, COM-T6):
  - `ascom_registry.ASCOM_TYPES: tuple[str, ...]` = the ten ASCOM registry type words: `("Telescope","Camera","Focuser","FilterWheel","Rotator","Switch","SafetyMonitor","ObservingConditions","CoverCalibrator","Dome")`.
  - `@dataclass(frozen=True) class AscomDriver` with fields `ascom_type: str` (registry word), `dev_type: str` (Alpaca lowercase = `ascom_type.lower()`), `progid: str`, `name: str`, `dev_num: int`.
  - `ascom_registry.enumerate(read=_read_type_drivers) -> list[AscomDriver]` — reads both registry views via the injectable `read(ascom_type) -> dict[progid, name]`, merges (64-bit view wins on duplicate ProgID), sorts each type's ProgIDs case-insensitively, assigns `dev_num = index within that type`, and returns the flat list. **Deterministic**: same registry → same `(dev_type, dev_num) → progid`. Returns `[]` off Windows (guarded `import winreg`).
  - `ascom_registry.progid_for(dev_type: str, dev_num: int, drivers: list[AscomDriver] | None = None) -> str | None` — the reverse lookup the sidecar uses (defaults to a fresh `enumerate()`).
  - `ascom_registry.enumerate_offers() -> list[dict]` — the UI/offers shape: for each driver whose `dev_type` maps to a role, `{"role","name","dev_type","dev_num","progid"}`; a `camera` additionally yields a second `guide_camera` entry (mirrors `drivers._probe_alpaca`'s D6 dual-offer). Types with no AstroDeck role (`dome`,`covercalibrator`,`observingconditions`) are skipped here (they are host-side only until later waves).
- Consumes: nothing new (stdlib `winreg` on Windows, guarded).

**Steps**

- [ ] Write the failing test `C:\Users\bear\astro\server\tests\test_ascom_registry.py`:
  ```python
  """COM-T1: ASCOM registry enumeration is deterministic, dual-view, and
  mockable (inject a fake per-type reader — no real registry, runs on any OS)."""
  import astrodeck.devices.ascom_registry as reg


  def _fake_reader(view_map):
      """Return a read(ascom_type) that serves from a {ascom_type: {progid: name}}
      map, ignoring the view (tests merge is exercised via progid overlap)."""
      def read(ascom_type):
          return dict(view_map.get(ascom_type, {}))
      return read


  def test_enumerate_assigns_stable_sorted_dev_nums():
      read = _fake_reader({
          "Camera": {"ASCOM.Simulator.Camera": "Sim Cam",
                     "ASCOM.ASICamera2.Camera": "ASI"},
          "Telescope": {"ASCOM.Simulator.Telescope": "Sim Scope"},
      })
      drivers = reg.enumerate(read=read)
      cams = sorted([d for d in drivers if d.dev_type == "camera"],
                    key=lambda d: d.dev_num)
      # sorted case-insensitively by progid: ASICamera2 (dev 0) < Simulator (dev 1)
      assert [(d.progid, d.dev_num) for d in cams] == [
          ("ASCOM.ASICamera2.Camera", 0), ("ASCOM.Simulator.Camera", 1)]
      scope = [d for d in drivers if d.dev_type == "telescope"][0]
      assert scope.dev_num == 0
      assert scope.ascom_type == "Telescope"


  def test_progid_for_reverse_lookup_matches_enumerate():
      read = _fake_reader({"Focuser": {"ASCOM.Simulator.Focuser": "Sim Focuser"}})
      drivers = reg.enumerate(read=read)
      assert reg.progid_for("focuser", 0, drivers) == "ASCOM.Simulator.Focuser"
      assert reg.progid_for("focuser", 7, drivers) is None


  def test_enumerate_offers_maps_roles_and_dual_offers_guide_camera():
      read = _fake_reader({
          "Camera": {"ASCOM.Simulator.Camera": "Sim Cam"},
          "SafetyMonitor": {"ASCOM.Simulator.SafetyMonitor": "Sim Safety"},
          "Dome": {"ASCOM.Simulator.Dome": "Sim Dome"},  # no role -> skipped
      }, )
      offers = reg.enumerate_offers_with(read)  # test hook: enumerate_offers over a reader
      roles = sorted(o["role"] for o in offers)
      assert roles == ["camera", "guide_camera", "safety"]  # no dome; camera dual-offers
      cam = [o for o in offers if o["role"] == "camera"][0]
      assert cam == {"role": "camera", "name": "Sim Cam",
                     "dev_type": "camera", "dev_num": 0,
                     "progid": "ASCOM.Simulator.Camera"}


  def test_enumerate_is_empty_off_windows(monkeypatch):
      # Force the winreg-absent path: the module's default reader returns {} and
      # enumerate() yields no drivers, so nothing breaks on Mac/Linux.
      monkeypatch.setattr(reg, "_WINREG_OK", False, raising=False)
      assert reg.enumerate() == []
  ```
- [ ] Run it, see it fail: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_ascom_registry.py -q` — expected failure: `ModuleNotFoundError: No module named 'astrodeck.devices.ascom_registry'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\devices\ascom_registry.py`:
  ```python
  """ASCOM registry enumeration (COM-T1) — the native "scan".

  Import-light (stdlib only) so the bundled COM host (astrodeck.comhost) can
  reuse it without pulling the server app. Reads installed ASCOM drivers per
  type from HKLM\\SOFTWARE\\[WOW6432Node\\]ASCOM\\<Type> Drivers (both 32- and
  64-bit views), assigns a DETERMINISTIC per-type device number (sorted ProgID
  index), and exposes the list. Determinism is load-bearing: the server
  advertises (dev_type, dev_num) and the comhost independently resolves the same
  (dev_type, dev_num) -> ProgID from the SAME enumeration, so no handshake and
  no new Alpaca client are needed (spec §3.1/§3.2).

  Windows-only source of truth; every entry point degrades to empty off Windows.
  """
  from __future__ import annotations

  from dataclasses import dataclass

  try:  # Windows-only; absent on Mac/Linux and in the portable test path.
      import winreg  # noqa: F401
      _WINREG_OK = True
  except ImportError:  # pragma: no cover - exercised via monkeypatch on non-win
      winreg = None  # type: ignore
      _WINREG_OK = False

  #: The ASCOM registry type words (the "<Type> Drivers" subkeys). dev_type is
  #: the Alpaca device-type string = the word lowercased.
  ASCOM_TYPES: tuple[str, ...] = (
      "Telescope", "Camera", "Focuser", "FilterWheel", "Rotator", "Switch",
      "SafetyMonitor", "ObservingConditions", "CoverCalibrator", "Dome",
  )

  #: Alpaca dev_type -> AstroDeck role (inverse of native_backend._ROLE_TO_DEV_TYPE;
  #: mirrors drivers._DEV_TYPE_TO_ROLE). Types absent here have no AstroDeck role
  #: and are host-side only (dome/covercalibrator/observingconditions).
  _DEV_TYPE_TO_ROLE: dict[str, str] = {
      "camera": "camera", "telescope": "telescope", "focuser": "focuser",
      "filterwheel": "filterwheel", "switch": "switch",
      "safetymonitor": "safety", "rotator": "rotator",
  }


  # Capture the builtin BEFORE this module defines a public `enumerate` that
  # shadows it in the module globals — so the implementation can still index with
  # the real builtin without recursing into itself.
  _builtin_enumerate = enumerate


  @dataclass(frozen=True)
  class AscomDriver:
      ascom_type: str   # registry word, e.g. "Camera"
      dev_type: str     # Alpaca lowercase, e.g. "camera"
      progid: str
      name: str
      dev_num: int


  def _read_type_drivers(ascom_type: str) -> dict[str, str]:
      """Read {progid: display_name} for one ASCOM type, merging the 64- and
      32-bit registry views (64-bit wins on a duplicate ProgID). Each ProgID is a
      subkey under "<Type> Drivers"; its DEFAULT value is the display name. A
      missing key / value degrades to empty (a box with no drivers of a type)."""
      if not _WINREG_OK:
          return {}
      out: dict[str, str] = {}
      base = fr"SOFTWARE\ASCOM\{ascom_type} Drivers"
      # 32-bit view first, then 64-bit, so 64-bit overwrites (wins).
      for flag in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
          try:
              key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                                   winreg.KEY_READ | flag)
          except OSError:
              continue
          try:
              i = 0
              while True:
                  try:
                      progid = winreg.EnumKey(key, i)
                  except OSError:
                      break
                  i += 1
                  try:
                      sub = winreg.OpenKey(key, progid, 0,
                                           winreg.KEY_READ | flag)
                      try:
                          name, _ = winreg.QueryValueEx(sub, None)  # default value
                      finally:
                          sub.Close()
                  except OSError:
                      name = ""
                  out[progid] = str(name) if name else progid
          finally:
              key.Close()
      return out


  def enumerate(read=_read_type_drivers) -> list[AscomDriver]:
      """Enumerate installed ASCOM drivers, per type, with deterministic dev_nums.
      ``read(ascom_type) -> {progid: name}`` is injectable for tests. ProgIDs are
      sorted case-insensitively and indexed so the (dev_type, dev_num) -> ProgID
      map is stable across runs (the comhost independently reproduces it)."""
      if not _WINREG_OK and read is _read_type_drivers:
          return []
      drivers: list[AscomDriver] = []
      for ascom_type in ASCOM_TYPES:
          found = read(ascom_type)
          for dev_num, progid in _builtin_enumerate(sorted(found, key=str.lower)):
              drivers.append(AscomDriver(
                  ascom_type=ascom_type, dev_type=ascom_type.lower(),
                  progid=progid, name=found[progid], dev_num=dev_num))
      return drivers


  def progid_for(dev_type: str, dev_num: int,
                 drivers: "list[AscomDriver] | None" = None) -> "str | None":
      """Reverse lookup used by the comhost to instantiate a device slot."""
      for d in (drivers if drivers is not None else enumerate()):
          if d.dev_type == dev_type and d.dev_num == dev_num:
              return d.progid
      return None


  def enumerate_offers_with(read) -> list[dict]:
      """enumerate_offers over an injected reader (test seam)."""
      offers: list[dict] = []
      for d in enumerate(read=read):
          role = _DEV_TYPE_TO_ROLE.get(d.dev_type)
          if role is None:
              continue
          entry = {"role": role, "name": d.name,
                   "dev_type": d.dev_type, "dev_num": d.dev_num,
                   "progid": d.progid}
          offers.append(entry)
          if role == "camera":  # D6 dual-offer (mirrors drivers._probe_alpaca)
              offers.append(dict(entry) | {"role": "guide_camera"})
      return offers


  def enumerate_offers() -> list[dict]:
      """Role-tagged offers for the discovery/offers surface (real registry)."""
      return enumerate_offers_with(_read_type_drivers)
  ```
  > Implementer note: this module deliberately exposes a public `enumerate()` that shadows the builtin, so it captures `_builtin_enumerate = enumerate` at module top (before the `def enumerate`) and indexes with that. If your reviewer prefers, rename the public function to `enumerate_drivers()` and drop the capture — but then update every call site in this plan (COM-T2/T6: `ascom_registry.enumerate(...)`, `progid_for`'s default, the comhost `ComHost.__init__`) to match. Keep ONE name.
- [ ] Run it, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_ascom_registry.py -q` — 4 passed.
- [ ] Write the failing route test `C:\Users\bear\astro\server\tests\test_discover_ascom_local.py`:
  ```python
  """COM-T1: GET /api/discover/ascom-local returns the registry-enumerated
  drivers (role-tagged offers). Enumeration is monkeypatched so the route is
  provable on any OS (no real registry)."""
  import pytest

  import astrodeck.devices.ascom_registry as reg
  from astrodeck.api.app import create_app
  from httpx import ASGITransport, AsyncClient


  @pytest.mark.asyncio
  async def test_discover_ascom_local_returns_offers(monkeypatch):
      monkeypatch.setattr(reg, "enumerate_offers", lambda: [
          {"role": "camera", "name": "Sim Cam", "dev_type": "camera",
           "dev_num": 0, "progid": "ASCOM.Simulator.Camera"},
          {"role": "telescope", "name": "Sim Scope", "dev_type": "telescope",
           "dev_num": 0, "progid": "ASCOM.Simulator.Telescope"},
      ])
      app = create_app()
      transport = ASGITransport(app=app)
      async with AsyncClient(transport=transport, base_url="http://t") as c:
          r = await c.get("/api/discover/ascom-local")
      assert r.status_code == 200
      body = r.json()
      assert {"role", "dev_type", "dev_num", "progid"} <= set(body[0])
      assert body[0]["progid"] == "ASCOM.Simulator.Camera"


  @pytest.mark.asyncio
  async def test_discover_ascom_local_empty_off_windows(monkeypatch):
      monkeypatch.setattr(reg, "enumerate_offers", lambda: [])
      app = create_app()
      transport = ASGITransport(app=app)
      async with AsyncClient(transport=transport, base_url="http://t") as c:
          r = await c.get("/api/discover/ascom-local")
      assert r.status_code == 200
      assert r.json() == []
  ```
  > Implementer note: match the app-construction + auth idiom of the SIBLING discovery route tests already in `server/tests/` (search for a test hitting `/api/discover/alpaca` or `/api/backends`). If those tests build the app through a shared fixture / pass an auth header for `CAP_VIEW_STATUS`, copy that EXACTLY here rather than the bare `create_app()` sketch above — the route carries `dependencies=[Depends(require(CAP_VIEW_STATUS))]`.
- [ ] Run it, see it fail: expected 404 (route not defined yet).
- [ ] Implement the route. In `app.py`, immediately after `discover_alpaca_one` (ends :1139) and before `backends()` (:1141), add:
  ```python
      @app.get("/api/discover/ascom-local",
               dependencies=[Depends(require(CAP_VIEW_STATUS))])
      @declare(CAP_VIEW_STATUS)
      async def discover_ascom_local():
          """The native ASCOM scan (spec §3.2): registry-enumerated COM drivers
          per type, role-tagged, each carrying dev_type + dev_num addressing so
          the assignment UI can pick one with no host/port/ProgID typing. Empty
          off Windows (winreg absent) — the panel then shows no ascom-local
          devices and the UI degrades cleanly. No COM is instantiated here; this
          is a pure registry read."""
          from ..devices import ascom_registry
          return await asyncio.to_thread(ascom_registry.enumerate_offers)
  ```
- [ ] Run it, see it pass: 2 passed.
- [ ] Full suite (delta +6): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1216 passed / 0 failed**.
- [ ] Commit:
  ```
  git add server/astrodeck/devices/ascom_registry.py server/astrodeck/api/app.py server/tests/test_ascom_registry.py server/tests/test_discover_ascom_local.py
  git commit -m "feat(devices/ascom): COM-T1 registry enumeration + /api/discover/ascom-local

  Deterministic dual-view ASCOM registry read (HKLM ASCOM/<Type> Drivers), stable
  per-type dev_nums, role-tagged offers. Import-light (stdlib winreg, guarded) so
  the bundled COM host can reuse it; empty off Windows. No COM yet.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### COM-T2 — comhost skeleton: Alpaca management API + STA/deadline harness

The bundled sidecar. A stdlib `ThreadingHTTPServer` on `127.0.0.1:0` that serves the Alpaca envelope the existing client expects: `GET /management/v1/configureddevices` (from `ascom_registry.enumerate()`), and the `/api/v1/<type>/<n>/<method>` dispatch mechanism + the per-device STA apartment thread + per-call deadline. NO device-type method tables yet (COM-T3+ add those as data) — this task ships the mechanism and proves it end-to-end WITHOUT COM by driving the real `AlpacaConnection` client against the server with a FAKE COM factory.

**Files**
- Create `C:\Users\bear\astro\server\astrodeck\comhost\__init__.py`.
- Create `C:\Users\bear\astro\server\astrodeck\comhost\device.py` — the STA `ComDevice` + `ComTimeoutError` + COM factory/apartment shims.
- Create `C:\Users\bear\astro\server\astrodeck\comhost\server.py` — the `ComHost` (device cache + dispatch + `DEVICE_API` registry) and the `ThreadingHTTPServer` handler + `serve(port, portfile)`.
- Create `C:\Users\bear\astro\server\astrodeck\comhost\__main__.py` — `python -m astrodeck.comhost` entry (argparse `--port`/`--portfile`, loopback bind, port/pid writeout).
- Modify `C:\Users\bear\astro\server\pyproject.toml` — add the `comhost` optional-dependencies extra (:18-19).
- Create `C:\Users\bear\astro\server\tests\test_comhost_sta_harness.py`.
- Create `C:\Users\bear\astro\server\tests\test_comhost_alpaca_contract.py`.

**Interfaces**
- Produces (consumed by COM-T3/T4/T5/T6):
  - `comhost.device.ComTimeoutError(Exception)` — a COM call exceeded its deadline.
  - `comhost.device.ComDevice(progid, *, create, timeout_s=_COM_CALL_TIMEOUT_S)` where `create: Callable[[str], object]` is the COM factory (default `_create_com`; tests inject a fake). Methods: `submit(fn: Callable[[object], Any]) -> Any` runs `fn(self._obj)` on the device's dedicated STA thread with a `timeout_s` deadline (raises `ComTimeoutError` on expiry); `connect()` creates the object on the STA thread and sets `Connected=True`; `disconnect()` sets `Connected=False` and stops the thread; `connected: bool`. `_COM_CALL_TIMEOUT_S = 30.0`.
  - `comhost.server.DEVICE_API: dict[str, dict[str, dict[str, Callable]]]` — `DEVICE_API[dev_type]["get"|"put"][alpaca_method] = fn(obj, params: dict[str,str]) -> Any`. COM-T3/T4/T5 register handler tables here. COM-T2 registers ONLY the universal `connected` get/put (below) plus `name`/`description`/`connected` basics.
  - `comhost.server.ComHost(drivers: list[AscomDriver] | None = None)` — holds the enumeration; `handle(verb, dev_type, dev_num, method, params) -> tuple[int, dict|bytes, str]` (HTTP status, body, content-type) applying the Alpaca envelope; lazily creates/caches a `ComDevice` per `(dev_type, dev_num)` on `connected`/`Connected=True`.
  - `comhost.server.serve(port: int = 0, portfile: str | None = None, *, drivers=None) -> ThreadingHTTPServer` — binds `127.0.0.1:port`, writes `{"pid","port"}` to `portfile` if given, returns the (already serving in a thread) server; `.server_address[1]` is the chosen port.
  - Envelope contract (what the existing client parses): success → HTTP 200 `{"Value":v,"ErrorNumber":0,"ErrorMessage":"","ClientTransactionID":ctid,"ServerTransactionID":stid}`; a COM/driver error → HTTP 200 `{"Value":None,"ErrorNumber":1024,"ErrorMessage":str(e),…}`; a per-call timeout or unknown route → HTTP 500 / 400 so `AlpacaConnection._unwrap` raises `DeviceError`.
- Consumes: `astrodeck.devices.ascom_registry.enumerate` / `AscomDriver` (COM-T1); the EXISTING `astrodeck.devices.alpaca.AlpacaConnection` (test-side only, unchanged).

**Steps**

- [ ] Add the dependency extra. In `server/pyproject.toml`, after the `dev` extra (:18-19):
  ```toml
  [project.optional-dependencies]
  dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-xdist>=3.6"]
  # Bundled Windows COM->Alpaca host (astrodeck.comhost). comtypes is Windows-only
  # (marker-gated) so Mac/Linux installs never pull it; the Windows bundle installs
  # `.[comhost]`. Absent comtypes -> the host/registry modules degrade (import
  # guards) and ascom-local is simply not offered.
  comhost = ["comtypes>=1.4.0; sys_platform == 'win32'"]
  ```
- [ ] Write the failing STA-harness test `C:\Users\bear\astro\server\tests\test_comhost_sta_harness.py`:
  ```python
  """COM-T2: the per-device STA thread marshals every call to ONE thread and
  enforces a per-call deadline. Proven with a FAKE COM object (no comtypes)."""
  import threading
  import time

  import pytest

  from astrodeck.comhost.device import ComDevice, ComTimeoutError


  class _FakeObj:
      def __init__(self):
          self.Connected = False
          self.call_threads = []

      def RightAscension(self):  # a "property read" the handler would do
          self.call_threads.append(threading.get_ident())
          return 12.3


  def test_all_calls_run_on_one_dedicated_thread():
      obj = _FakeObj()
      dev = ComDevice("Fake.ProgID", create=lambda pid: obj)
      dev.connect()
      assert obj.Connected is True
      for _ in range(20):
          assert dev.submit(lambda o: o.RightAscension()) == 12.3
      # every marshaled call ran on the SAME (single) STA thread
      assert len(set(obj.call_threads)) == 1
      # ...and NOT the caller's thread
      assert threading.get_ident() not in obj.call_threads
      dev.disconnect()
      assert dev.connected is False


  def test_wedged_call_raises_timeout_not_hang(monkeypatch):
      import astrodeck.comhost.device as dev_mod
      monkeypatch.setattr(dev_mod, "_COM_CALL_TIMEOUT_S", 0.2)

      class _Wedged:
          Connected = False
          def slow(self):
              time.sleep(5.0)  # far past the 0.2s deadline

      dev = ComDevice("Fake.Wedged", create=lambda pid: _Wedged())
      dev.connect()
      t0 = time.monotonic()
      with pytest.raises(ComTimeoutError):
          dev.submit(lambda o: o.slow())
      assert time.monotonic() - t0 < 2.0  # raised on the deadline, did not hang
      dev.disconnect()
  ```
- [ ] Run it, see it fail: `ModuleNotFoundError: No module named 'astrodeck.comhost'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\__init__.py`:
  ```python
  """astrodeck-comhost — the bundled minimal COM->Alpaca host (Windows-only).

  A standalone sidecar (`python -m astrodeck.comhost`) that serves a spec-shaped
  ASCOM Alpaca v1 device API on 127.0.0.1 so AstroDeck's EXISTING Alpaca client
  drives installed COM ASCOM drivers unchanged (spec §3, Approach C). Import-light
  by design: this package imports only stdlib + comtypes (guarded) + the
  import-light ascom_registry — never the server app.
  """
  ```
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\device.py`:
  ```python
  """Per-device STA apartment thread + per-call deadline (COM-T2).

  ASCOM drivers are STA: each ComDevice owns ONE thread that CoInitializeEx's an
  apartment for the device's whole lifetime, and every COM access (create,
  Connected=, property/method) marshals to it via a queue + Future. Every
  marshaled call has a deadline (the AlpacaCamera.expose imageready-timeout idiom,
  commit 082dd79): future.result(timeout=...) -> a wedged COM call raises
  ComTimeoutError instead of hanging the host.
  """
  from __future__ import annotations

  import queue
  import threading
  from concurrent.futures import Future
  from concurrent.futures import TimeoutError as _FutureTimeout
  from typing import Any, Callable

  try:  # comtypes is Windows-only + optional; guarded so import never fails.
      import comtypes  # noqa: F401
      import comtypes.client  # noqa: F401
      _HAVE_COMTYPES = True
  except Exception:  # pragma: no cover - non-Windows / wheel absent
      comtypes = None  # type: ignore
      _HAVE_COMTYPES = False

  # Per COM-call deadline (s). Mirrors alpaca._IMAGEREADY_POLL_MARGIN_S (082dd79).
  _COM_CALL_TIMEOUT_S = 30.0


  class ComTimeoutError(Exception):
      """A marshaled COM call exceeded its per-call deadline."""


  def _co_init() -> None:
      if _HAVE_COMTYPES:  # STA apartment for this device's lifetime
          comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)


  def _co_uninit() -> None:
      if _HAVE_COMTYPES:
          comtypes.CoUninitialize()


  def _create_com(progid: str):  # pragma: no cover - real COM path (Windows)
      return comtypes.client.CreateObject(progid)


  class ComDevice:
      """One ASCOM COM driver pinned to a dedicated STA thread."""

      def __init__(self, progid: str, *, create: Callable[[str], Any] = _create_com,
                   timeout_s: float = _COM_CALL_TIMEOUT_S):
          self.progid = progid
          self._create = create
          self._timeout_s = timeout_s
          self._q: "queue.Queue" = queue.Queue()
          self._obj: Any = None
          self.connected = False
          self._started = threading.Event()
          self._thread = threading.Thread(
              target=self._run, name=f"comdev-{progid}", daemon=True)
          self._thread.start()
          self._started.wait()

      def _run(self) -> None:
          _co_init()
          self._started.set()
          try:
              while True:
                  item = self._q.get()
                  if item is None:
                      break
                  fn, fut = item
                  if fut.set_running_or_notify_cancel():
                      try:
                          fut.set_result(fn(self._obj))
                      except BaseException as e:  # marshal the error back
                          fut.set_exception(e)
          finally:
              _co_uninit()

      def submit(self, fn: Callable[[Any], Any]) -> Any:
          """Run fn(self._obj) on the STA thread with the per-call deadline."""
          fut: Future = Future()
          self._q.put((fn, fut))
          try:
              return fut.result(timeout=self._timeout_s)
          except _FutureTimeout:
              raise ComTimeoutError(
                  f"COM call on {self.progid} exceeded "
                  f"{self._timeout_s:.0f}s deadline") from None

      def connect(self) -> None:
          def _do(_obj):
              self._obj = self._create(self.progid)
              self._obj.Connected = True
          # _obj is None until created, so run against self, not the arg:
          fut: Future = Future()
          self._q.put((lambda _ignored: _do(_ignored), fut))
          fut.result(timeout=self._timeout_s)
          self.connected = True

      def disconnect(self) -> None:
          if self._obj is not None:
              try:
                  self.submit(lambda o: setattr(o, "Connected", False))
              except Exception:  # best-effort; we are tearing down anyway
                  pass
          self.connected = False
          self._q.put(None)  # stop the STA loop
  ```
  > Implementer note: `connect()` cannot use `submit(lambda o: …)` because `self._obj` is still `None` at that point (the lambda would receive `None`). It marshals a closure that assigns `self._obj` on the STA thread instead. Keep that distinction.
- [ ] Run the harness test, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_comhost_sta_harness.py -q` — 2 passed.
- [ ] Write the failing Alpaca-contract test `C:\Users\bear\astro\server\tests\test_comhost_alpaca_contract.py`:
  ```python
  """COM-T2: the comhost serves the EXACT Alpaca envelope the existing client
  parses. Drive the real AlpacaConnection against a running comhost with a FAKE
  COM factory (no comtypes) — this is the portable proof of the client<->host
  contract; COM-T3+ add the real device method tables."""
  import pytest

  import astrodeck.comhost.server as server
  from astrodeck.comhost.device import ComDevice
  from astrodeck.devices.alpaca import AlpacaConnection
  from astrodeck.devices.ascom_registry import AscomDriver
  from astrodeck.devices.base import DeviceError


  def _drivers():
      return [AscomDriver("Camera", "camera", "Fake.Camera", "Fake Cam", 0),
              AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)]


  class _FakeScope:
      def __init__(self):
          self.Connected = False
      RightAscension = 12.34


  @pytest.fixture()
  def running_host(monkeypatch):
      # Every ComDevice in this host is created from a fake factory (no COM).
      monkeypatch.setattr(server, "_make_com_device", lambda progid: ComDevice(
          progid, create=lambda pid: _FakeScope()))
      # Register a minimal telescope GET handler so the contract test can read one
      # property end-to-end through the real client (COM-T3 adds the full table).
      server.DEVICE_API.setdefault("telescope", {"get": {}, "put": {}})
      server.DEVICE_API["telescope"]["get"]["rightascension"] = \
          lambda obj, p: obj.RightAscension
      srv = server.serve(port=0, drivers=_drivers())
      yield srv.server_address[1]
      srv.shutdown()

  @pytest.mark.asyncio
  async def test_configureddevices_lists_enumeration(running_host):
      conn = AlpacaConnection("127.0.0.1", running_host)
      try:
          r = await conn.http.get(
              f"http://127.0.0.1:{running_host}/management/v1/configureddevices")
          value = r.json()["Value"]
          types = sorted(d["DeviceType"] for d in value)
          assert types == ["Camera", "Telescope"]
          assert value[0]["DeviceNumber"] == 0
          assert value[0]["UniqueID"]  # ProgID carried
      finally:
          await conn.close()

  @pytest.mark.asyncio
  async def test_connect_then_get_property_roundtrips(running_host):
      conn = AlpacaConnection("127.0.0.1", running_host)
      try:
          await conn.put("telescope", 0, "connected", Connected=True)
          ra = await conn.get("telescope", 0, "rightascension")
          assert ra == 12.34
      finally:
          await conn.close()

  @pytest.mark.asyncio
  async def test_unknown_method_raises_deviceerror(running_host):
      conn = AlpacaConnection("127.0.0.1", running_host)
      try:
          await conn.put("telescope", 0, "connected", Connected=True)
          with pytest.raises(DeviceError):
              await conn.get("telescope", 0, "nosuchmethod")
      finally:
          await conn.close()
  ```
- [ ] Run it, see it fail: `ModuleNotFoundError: No module named 'astrodeck.comhost.server'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\server.py`:
  ```python
  """Minimal Alpaca device server (COM-T2): ThreadingHTTPServer on 127.0.0.1
  serving /management/v1/configureddevices + /api/v1/<type>/<n>/<method>, with the
  per-device STA ComDevice cache and the Alpaca envelope the EXISTING AstroDeck
  Alpaca client parses (spec §3.1). Device-type method tables (DEVICE_API) are
  filled by COM-T3/T4/T5; this module owns the dispatch + envelope + lifecycle.
  """
  from __future__ import annotations

  import json
  import threading
  from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
  from urllib.parse import parse_qs, urlparse
  from typing import Any, Callable

  from ..devices import ascom_registry
  from ..devices.ascom_registry import AscomDriver
  from .device import ComDevice, ComTimeoutError

  # DEVICE_API[dev_type]["get"|"put"][method] = fn(obj, params) -> Value.
  # Registered by the device-type tasks (COM-T3/T4/T5); "connected" is universal.
  DEVICE_API: dict[str, dict[str, dict[str, Callable[[Any, dict], Any]]]] = {}

  _ALPACA_DRIVER_ERROR = 1024  # generic ASCOM driver error number
  _txn_lock = threading.Lock()
  _server_txn = 0


  def _next_server_txn() -> int:
      global _server_txn
      with _txn_lock:
          _server_txn += 1
          return _server_txn


  def _make_com_device(progid: str) -> ComDevice:  # seam: tests patch this
      return ComDevice(progid)


  class ComHost:
      """Enumeration table + per-(type,dev_num) ComDevice cache + dispatch."""

      def __init__(self, drivers: "list[AscomDriver] | None" = None):
          self.drivers = drivers if drivers is not None else ascom_registry.enumerate()
          self._devices: dict[tuple[str, int], ComDevice] = {}
          self._lock = threading.Lock()

      # ---- management API ----
      def configured_devices(self) -> list[dict]:
          return [{"DeviceType": d.ascom_type, "DeviceNumber": d.dev_num,
                   "DeviceName": d.name, "UniqueID": d.progid}
                  for d in self.drivers]

      # ---- device lifecycle ----
      def _get_or_create(self, dev_type: str, dev_num: int) -> ComDevice:
          key = (dev_type, dev_num)
          with self._lock:
              dev = self._devices.get(key)
              if dev is None:
                  progid = ascom_registry.progid_for(dev_type, dev_num, self.drivers)
                  if progid is None:
                      raise KeyError(f"no ASCOM driver for {dev_type} #{dev_num}")
                  dev = _make_com_device(progid)
                  self._devices[key] = dev
              return dev

      def _drop(self, dev_type: str, dev_num: int) -> None:
          with self._lock:
              self._devices.pop((dev_type, dev_num), None)

      def close(self) -> None:
          with self._lock:
              devs = list(self._devices.values())
              self._devices.clear()
          for dev in devs:
              try:
                  dev.disconnect()
              except Exception:
                  pass

      # ---- request handling (returns HTTP status, body, content-type) ----
      def handle(self, verb: str, dev_type: str, dev_num: int, method: str,
                 params: dict) -> tuple[int, dict, str]:
          ctid = _coerce_int(params.get("ClientTransactionID"))
          try:
              value = self._dispatch(verb, dev_type, dev_num, method, params)
              return 200, self._ok(value, ctid), "application/json"
          except (ComTimeoutError, KeyError) as e:
              # Timeout / unknown route -> HTTP 500 so the client's _unwrap raises
              # DeviceError immediately (fast, honest failure; spec §4).
              return 500, {"Value": None, "ErrorNumber": _ALPACA_DRIVER_ERROR,
                           "ErrorMessage": str(e),
                           "ClientTransactionID": ctid,
                           "ServerTransactionID": _next_server_txn()}, "application/json"
          except Exception as e:  # a COM/driver exception -> Alpaca ErrorNumber
              return 200, {"Value": None, "ErrorNumber": _ALPACA_DRIVER_ERROR,
                           "ErrorMessage": str(e),
                           "ClientTransactionID": ctid,
                           "ServerTransactionID": _next_server_txn()}, "application/json"

      def _dispatch(self, verb: str, dev_type: str, dev_num: int, method: str,
                    params: dict) -> Any:
          if method == "connected":
              return self._connected(verb, dev_type, dev_num, params)
          table = DEVICE_API.get(dev_type, {}).get(verb, {})
          fn = table.get(method)
          if fn is None:
              raise KeyError(f"unsupported {verb} {dev_type}/{method}")
          dev = self._get_or_create(dev_type, dev_num)
          return dev.submit(lambda obj: fn(obj, params))

      def _connected(self, verb: str, dev_type: str, dev_num: int,
                     params: dict) -> Any:
          if verb == "get":
              key = (dev_type, dev_num)
              dev = self._devices.get(key)
              return bool(dev and dev.connected)
          want = _coerce_bool(params.get("Connected"))
          if want:
              self._get_or_create(dev_type, dev_num).connect()
          else:
              dev = self._devices.get((dev_type, dev_num))
              if dev is not None:
                  dev.disconnect()
                  self._drop(dev_type, dev_num)
          return None

      @staticmethod
      def _ok(value: Any, ctid: int) -> dict:
          return {"Value": value, "ErrorNumber": 0, "ErrorMessage": "",
                  "ClientTransactionID": ctid,
                  "ServerTransactionID": _next_server_txn()}


  def _coerce_int(v) -> int:
      try:
          return int(v[0] if isinstance(v, list) else v)
      except (TypeError, ValueError):
          return 0


  def _coerce_bool(v) -> bool:
      s = (v[0] if isinstance(v, list) else v)
      return str(s).strip().lower() in ("true", "1", "yes")


  def _make_handler(host: ComHost):
      class Handler(BaseHTTPRequestHandler):
          protocol_version = "HTTP/1.1"

          def log_message(self, *a):  # silence stdlib access logging
              pass

          def _send(self, status: int, body, ctype: str):
              if isinstance(body, (dict, list)):
                  payload = json.dumps(body).encode()
              else:
                  payload = body
              self.send_response(status)
              self.send_header("Content-Type", ctype)
              self.send_header("Content-Length", str(len(payload)))
              self.end_headers()
              self.wfile.write(payload)

          def do_GET(self):
              parsed = urlparse(self.path)
              params = parse_qs(parsed.query)
              if parsed.path == "/management/v1/configureddevices":
                  return self._send(200, {"Value": host.configured_devices(),
                                          "ErrorNumber": 0, "ErrorMessage": "",
                                          "ClientTransactionID": _coerce_int(
                                              params.get("ClientTransactionID")),
                                          "ServerTransactionID": _next_server_txn()},
                                    "application/json")
              route = _parse_device_route(parsed.path)
              if route is None:
                  return self._send(400, {"Value": None, "ErrorNumber": 1,
                                          "ErrorMessage": "bad route"},
                                    "application/json")
              dev_type, dev_num, method = route
              status, body, ctype = host.handle("get", dev_type, dev_num, method, params)
              self._send(status, body, ctype)

          def do_PUT(self):
              parsed = urlparse(self.path)
              length = int(self.headers.get("Content-Length", 0) or 0)
              raw = self.rfile.read(length).decode() if length else ""
              params = parse_qs(raw)
              route = _parse_device_route(parsed.path)
              if route is None:
                  return self._send(400, {"Value": None, "ErrorNumber": 1,
                                          "ErrorMessage": "bad route"},
                                    "application/json")
              dev_type, dev_num, method = route
              status, body, ctype = host.handle("put", dev_type, dev_num, method, params)
              self._send(status, body, ctype)

      return Handler


  def _parse_device_route(path: str):
      # /api/v1/<type>/<n>/<method>
      parts = [p for p in path.split("/") if p]
      if len(parts) == 5 and parts[0] == "api" and parts[1] == "v1":
          try:
              return parts[2].lower(), int(parts[3]), parts[4].lower()
          except ValueError:
              return None
      return None


  def serve(port: int = 0, portfile: "str | None" = None, *,
            drivers=None) -> ThreadingHTTPServer:
      """Bind 127.0.0.1:port (0 = ephemeral), serve in a daemon thread, and (if
      given) write {'pid','port'} to portfile. Returns the running server; the
      caller stops it with .shutdown()."""
      import os
      host = ComHost(drivers=drivers)
      httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(host))
      httpd.com_host = host  # so __main__ / tests can reach it
      chosen = httpd.server_address[1]
      if portfile:
          tmp = f"{portfile}.tmp"
          with open(tmp, "w", encoding="utf-8") as f:
              json.dump({"pid": os.getpid(), "port": chosen}, f)
          os.replace(tmp, portfile)  # atomic publish (server reads it)
      t = threading.Thread(target=httpd.serve_forever, name="comhost-http",
                           daemon=True)
      t.start()
      return httpd
  ```
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\__main__.py`:
  ```python
  """python -m astrodeck.comhost — the bundled COM->Alpaca host entry (COM-T2).

  Binds loopback-only, publishes its chosen ephemeral port to --portfile, prints
  a READY line, and serves until terminated. Windows-only in practice (COM), but
  starts anywhere for the portable contract tests.
  """
  from __future__ import annotations

  import argparse
  import sys
  import time

  from .server import serve


  def main(argv: "list[str] | None" = None) -> int:
      ap = argparse.ArgumentParser(prog="astrodeck-comhost")
      ap.add_argument("--port", type=int, default=0,
                      help="loopback port (0 = ephemeral, recommended)")
      ap.add_argument("--portfile", default=None,
                      help="path to write {'pid','port'} JSON for the server")
      a = ap.parse_args(argv)
      httpd = serve(port=a.port, portfile=a.portfile)
      print(f"comhost READY 127.0.0.1:{httpd.server_address[1]}", flush=True)
      try:
          while True:
              time.sleep(3600)
      except KeyboardInterrupt:  # pragma: no cover - interactive
          pass
      finally:
          httpd.com_host.close()
          httpd.shutdown()
      return 0


  if __name__ == "__main__":  # pragma: no cover
      raise SystemExit(main())
  ```
- [ ] Run the contract test, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_comhost_alpaca_contract.py -q` — 3 passed.
- [ ] Full suite (delta +8 cross-platform; the fake-COM contract tests run everywhere): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1224 passed / 0 failed**.
- [ ] Commit:
  ```
  git add server/astrodeck/comhost/__init__.py server/astrodeck/comhost/device.py server/astrodeck/comhost/server.py server/astrodeck/comhost/__main__.py server/pyproject.toml server/tests/test_comhost_sta_harness.py server/tests/test_comhost_alpaca_contract.py
  git commit -m "feat(comhost): COM-T2 sidecar skeleton — Alpaca mgmt API + STA/deadline harness

  Loopback ThreadingHTTPServer serving the exact Alpaca envelope the existing
  client parses; per-device STA apartment thread + per-call deadline (imageready
  idiom, 082dd79). Proven end-to-end with the real AlpacaConnection against a fake
  COM factory (no comtypes). comtypes added as a win32-gated `comhost` extra.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### COM-T3 — Telescope + Camera COM↔Alpaca (the core imaging pair)

Register the `DEVICE_API` handler tables for `telescope` and `camera`: every Alpaca method the EXISTING `AlpacaTelescope`/`AlpacaCamera` client calls, mapped to its ASCOM COM member. Proven two ways — a portable unit test (fake COM object; verifies the mapping) and the real gate (the actual client driving the running comhost against `ASCOM.Simulator.Telescope`/`ASCOM.Simulator.Camera`, Windows-only skip).

**Files**
- Create `C:\Users\bear\astro\server\astrodeck\comhost\handlers_telescope.py`.
- Create `C:\Users\bear\astro\server\astrodeck\comhost\handlers_camera.py`.
- Modify `C:\Users\bear\astro\server\astrodeck\comhost\server.py` — import the two handler modules so their `register()` runs (add to the module docstring's "filled by" note is optional; the concrete change is two import lines at the bottom, after `serve`).
- Create `C:\Users\bear\astro\server\tests\test_comhost_telescope_camera_map.py`.
- Create `C:\Users\bear\astro\server\tests\test_comhost_simulators.py` (Windows+simulator gated).

**Interfaces**
- Consumes (from COM-T2): `comhost.server.DEVICE_API` (register into it); `ComHost`/`serve`/`_make_com_device` seam; the exact Alpaca method names each client class calls — Telescope: `rightascension`,`declination`,`slewing`,`slewtocoordinatesasync`(RightAscension,Declination),`abortslew`,`synctocoordinates`(RightAscension,Declination),`tracking`(get; put Tracking),`park`,`unpark`,`atpark`,`moveaxis`(Axis,Rate),`pulseguide`(Direction,Duration),`guideraterightascension`,`guideratedeclination`,`sideofpier`,`destinationsideofpier`(RightAscension,Declination),`canpulseguide`; Camera: `cameraxsize`,`cameraysize`,`pixelsizex`,`gainmax`,`cansetccdtemperature`,`sensortype`,`maxadu`,`coolerpower`,`gain`(get; put Gain),`offset`(get; put Offset),`binx`(put BinX),`biny`(put BinY),`numx`(put NumX),`numy`(put NumY),`startexposure`(Duration,Light),`imageready`,`imagearray`,`abortexposure`,`stopexposure`,`setccdtemperature`(get; put SetCCDTemperature),`cooleron`(get; put CoolerOn),`ccdtemperature`.
- Produces: `handlers_telescope.register()` and `handlers_camera.register()` populating `DEVICE_API["telescope"]` / `DEVICE_API["camera"]`. Both are idempotent (`setdefault`).

**Steps**

- [ ] Write the failing mapping test `C:\Users\bear\astro\server\tests\test_comhost_telescope_camera_map.py`:
  ```python
  """COM-T3: the telescope+camera DEVICE_API handlers translate each Alpaca
  method to the right ASCOM COM member. Portable (fake COM object; asserts the
  member names and argument marshaling), no comtypes/hardware."""
  import numpy as np

  import astrodeck.comhost.handlers_camera  # noqa: F401  (register side effect)
  import astrodeck.comhost.handlers_telescope  # noqa: F401
  from astrodeck.comhost.server import DEVICE_API


  class _FakeScope:
      def __init__(self):
          self.Connected = False
          self.Tracking = False
          self.RightAscension = 5.0
          self.Declination = 10.0
          self.Slewing = False
          self.AtPark = False
          self.CanPulseGuide = True
          self.slews = []
          self.pulses = []

      def SlewToCoordinatesAsync(self, ra, dec):
          self.slews.append((ra, dec))

      def PulseGuide(self, direction, duration):
          self.pulses.append((direction, duration))

      def MoveAxis(self, axis, rate):
          self.moved = (axis, rate)


  def test_telescope_get_and_put_mapping():
      tel = DEVICE_API["telescope"]
      obj = _FakeScope()
      assert tel["get"]["rightascension"](obj, {}) == 5.0
      assert tel["get"]["canpulseguide"](obj, {}) is True
      # slew marshals RightAscension/Declination floats to SlewToCoordinatesAsync
      tel["put"]["slewtocoordinatesasync"](
          obj, {"RightAscension": ["1.5"], "Declination": ["-2.0"]})
      assert obj.slews == [(1.5, -2.0)]
      # tracking put sets the property
      tel["put"]["tracking"](obj, {"Tracking": ["true"]})
      assert obj.Tracking is True
      # pulseguide marshals ints
      tel["put"]["pulseguide"](obj, {"Direction": ["2"], "Duration": ["500"]})
      assert obj.pulses == [(2, 500)]


  class _FakeCam:
      def __init__(self):
          self.Connected = False
          self.CameraXSize = 640
          self.CameraYSize = 480
          self.PixelSizeX = 3.8
          self.GainMax = 300
          self.CanSetCCDTemperature = True
          self.SensorType = 0
          self.MaxADU = 65535
          self.ImageReady = True
          self.Gain = 0
          self.NumX = 0
          # ASCOM ImageArray is [x][y]; 2x3 here
          self.ImageArray = ((1, 2, 3), (4, 5, 6))

      def StartExposure(self, dur, light):
          self.started = (dur, light)


  def test_camera_mapping_and_imagearray_shape():
      cam = DEVICE_API["camera"]
      obj = _FakeCam()
      assert cam["get"]["cameraxsize"](obj, {}) == 640
      assert cam["get"]["imageready"](obj, {}) is True
      cam["put"]["gain"](obj, {"Gain": ["120"]})
      assert obj.Gain == 120
      cam["put"]["startexposure"](obj, {"Duration": ["1.5"], "Light": ["true"]})
      assert obj.started == (1.5, True)
      # imagearray returns the [x][y] nested lists the client reshapes+transposes
      arr = cam["get"]["imagearray"](obj, {})
      assert np.array(arr).tolist() == [[1, 2, 3], [4, 5, 6]]
  ```
- [ ] Run it, see it fail: `ModuleNotFoundError: No module named 'astrodeck.comhost.handlers_camera'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\handlers_telescope.py`:
  ```python
  """Telescope COM<->Alpaca handlers (COM-T3). Each entry maps an Alpaca method
  (exactly what AlpacaTelescope in devices/alpaca.py calls) to its ASCOM COM
  member. Handlers run on the device's STA thread: obj is the live COM object."""
  from __future__ import annotations

  from .server import DEVICE_API


  def _f(params, key):  # first form/query value as float
      v = params.get(key)
      return float(v[0] if isinstance(v, list) else v)


  def _i(params, key):
      v = params.get(key)
      return int(v[0] if isinstance(v, list) else v)


  def _b(params, key):
      v = params.get(key)
      return str(v[0] if isinstance(v, list) else v).strip().lower() in (
          "true", "1", "yes")


  def register() -> None:
      t = DEVICE_API.setdefault("telescope", {"get": {}, "put": {}})
      g, p = t["get"], t["put"]
      g["rightascension"] = lambda o, _: float(o.RightAscension)
      g["declination"] = lambda o, _: float(o.Declination)
      g["slewing"] = lambda o, _: bool(o.Slewing)
      g["tracking"] = lambda o, _: bool(o.Tracking)
      g["atpark"] = lambda o, _: bool(o.AtPark)
      g["canpulseguide"] = lambda o, _: bool(o.CanPulseGuide)
      g["sideofpier"] = lambda o, _: int(o.SideOfPier)
      g["guideraterightascension"] = lambda o, _: float(o.GuideRateRightAscension)
      g["guideratedeclination"] = lambda o, _: float(o.GuideRateDeclination)
      g["destinationsideofpier"] = lambda o, prm: int(
          o.DestinationSideOfPier(_f(prm, "RightAscension"), _f(prm, "Declination")))
      p["slewtocoordinatesasync"] = lambda o, prm: o.SlewToCoordinatesAsync(
          _f(prm, "RightAscension"), _f(prm, "Declination"))
      p["abortslew"] = lambda o, _: o.AbortSlew()
      p["synctocoordinates"] = lambda o, prm: o.SyncToCoordinates(
          _f(prm, "RightAscension"), _f(prm, "Declination"))
      p["tracking"] = lambda o, prm: setattr(o, "Tracking", _b(prm, "Tracking"))
      p["park"] = lambda o, _: o.Park()
      p["unpark"] = lambda o, _: o.Unpark()
      p["moveaxis"] = lambda o, prm: o.MoveAxis(_i(prm, "Axis"), _f(prm, "Rate"))
      p["pulseguide"] = lambda o, prm: o.PulseGuide(
          _i(prm, "Direction"), _i(prm, "Duration"))


  register()
  ```
  > Implementer note on ASCOM member forms: `SideOfPier`/`DestinationSideOfPier`/`GuideRate*` are optional ASCOM members. The client already wraps its calls in try/except and treats a `DeviceError` as "unsupported" (`alpaca.py` `guide_rates`/`pier_side`/`destination_pier_side`). So if a real driver raises on these, the COM exception surfaces as `ErrorNumber≠0` → the client's `_get` raises `DeviceError` → the client's own except handles it. Do NOT special-case unsupported members here — let the COM exception propagate through the envelope; that IS the contract.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\handlers_camera.py`:
  ```python
  """Camera COM<->Alpaca handlers (COM-T3). Maps exactly the Alpaca methods
  AlpacaCamera in devices/alpaca.py calls. ImageArray is returned as the [x][y]
  nested structure the client reshapes+transposes (json path in _download_image);
  a real comtypes SAFEARRAY is already a nested tuple, so json serialization is
  the same shape a real Alpaca server's JSON ImageArray uses."""
  from __future__ import annotations

  from .server import DEVICE_API


  def _f(params, key):
      v = params.get(key)
      return float(v[0] if isinstance(v, list) else v)


  def _i(params, key):
      v = params.get(key)
      return int(v[0] if isinstance(v, list) else v)


  def _b(params, key):
      v = params.get(key)
      return str(v[0] if isinstance(v, list) else v).strip().lower() in (
          "true", "1", "yes")


  def _imagearray(o, _):
      # Return the driver's ImageArray as nested lists. comtypes yields a nested
      # tuple (SAFEARRAY [x][y]); list(...) makes it json-serializable. The client
      # does np.array(Value).T, matching a real Alpaca JSON ImageArray.
      raw = o.ImageArray
      return [list(col) for col in raw]


  def register() -> None:
      c = DEVICE_API.setdefault("camera", {"get": {}, "put": {}})
      g, p = c["get"], c["put"]
      g["cameraxsize"] = lambda o, _: int(o.CameraXSize)
      g["cameraysize"] = lambda o, _: int(o.CameraYSize)
      g["pixelsizex"] = lambda o, _: float(o.PixelSizeX)
      g["gainmax"] = lambda o, _: int(o.GainMax)
      g["cansetccdtemperature"] = lambda o, _: bool(o.CanSetCCDTemperature)
      g["sensortype"] = lambda o, _: int(o.SensorType)
      g["maxadu"] = lambda o, _: int(o.MaxADU)
      g["coolerpower"] = lambda o, _: float(o.CoolerPower)
      g["cooleron"] = lambda o, _: bool(o.CoolerOn)
      g["ccdtemperature"] = lambda o, _: float(o.CCDTemperature)
      g["setccdtemperature"] = lambda o, _: float(o.SetCCDTemperature)
      g["imageready"] = lambda o, _: bool(o.ImageReady)
      g["gain"] = lambda o, _: int(o.Gain)
      g["offset"] = lambda o, _: int(o.Offset)
      g["imagearray"] = _imagearray
      p["gain"] = lambda o, prm: setattr(o, "Gain", _i(prm, "Gain"))
      p["offset"] = lambda o, prm: setattr(o, "Offset", _i(prm, "Offset"))
      p["binx"] = lambda o, prm: setattr(o, "BinX", _i(prm, "BinX"))
      p["biny"] = lambda o, prm: setattr(o, "BinY", _i(prm, "BinY"))
      p["numx"] = lambda o, prm: setattr(o, "NumX", _i(prm, "NumX"))
      p["numy"] = lambda o, prm: setattr(o, "NumY", _i(prm, "NumY"))
      p["startexposure"] = lambda o, prm: o.StartExposure(
          _f(prm, "Duration"), _b(prm, "Light"))
      p["cooleron"] = lambda o, prm: setattr(o, "CoolerOn", _b(prm, "CoolerOn"))
      p["setccdtemperature"] = lambda o, prm: setattr(
          o, "SetCCDTemperature", _f(prm, "SetCCDTemperature"))
      p["abortexposure"] = lambda o, _: o.AbortExposure()
      p["stopexposure"] = lambda o, _: o.StopExposure()


  register()
  ```
  > Implementer note: the client's `_download_image` requests `imagearray` with `Accept: application/imagebytes`; when the response content-type is NOT `imagebytes` it falls back to JSON `body["Value"]`. The comhost returns JSON (via the normal envelope) — the `imagearray` GET goes through `handle()` like any other GET, so `_download_image`'s JSON branch handles it. ImageBytes (the binary fast path) is a documented OPTIONAL optimization, NOT required for the gate; do not implement it here.
- [ ] Wire the handler imports. At the bottom of `comhost/server.py`, after `serve`, add:
  ```python
  # Register the device-type method tables (COM-T3+): importing each module runs
  # its register() into DEVICE_API. Kept at module end to avoid an import cycle
  # (handlers import DEVICE_API from here).
  from . import handlers_telescope as _ht  # noqa: E402,F401
  from . import handlers_camera as _hc  # noqa: E402,F401
  ```
- [ ] Run the mapping test, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_comhost_telescope_camera_map.py -q` — 2 passed.
- [ ] Write the Windows+simulator gate test `C:\Users\bear\astro\server\tests\test_comhost_simulators.py`:
  ```python
  """COM-T3 gate: the EXISTING Alpaca client drives Telescope+Camera through the
  running comhost against the ASCOM SIMULATOR drivers — no NINA, no ASCOM Remote,
  no hardware (spec §5). Windows + simulator only; skips cleanly elsewhere."""
  import sys

  import pytest

  import astrodeck.comhost.server as server
  from astrodeck.devices import ascom_registry
  from astrodeck.devices.alpaca import AlpacaConnection, AlpacaTelescope, AlpacaCamera


  def _sim_present(dev_type: str, progid: str) -> bool:
      if sys.platform != "win32":
          return False
      return any(d.dev_type == dev_type and d.progid == progid
                 for d in ascom_registry.enumerate())


  pytestmark = pytest.mark.skipif(
      not (_sim_present("telescope", "ASCOM.Simulator.Telescope")
           and _sim_present("camera", "ASCOM.Simulator.Camera")),
      reason="ASCOM Simulator Telescope+Camera not registered (non-Windows / no sim)")


  def _dev_num(dev_type: str, progid: str) -> int:
      for d in ascom_registry.enumerate():
          if d.dev_type == dev_type and d.progid == progid:
              return d.dev_num
      raise AssertionError("simulator vanished between skip-check and use")


  @pytest.fixture()
  def host_port():
      srv = server.serve(port=0)
      yield srv.server_address[1]
      srv.com_host.close()
      srv.shutdown()


  @pytest.mark.asyncio
  async def test_telescope_slew_through_comhost(host_port):
      conn = AlpacaConnection("127.0.0.1", host_port)
      tel = AlpacaTelescope(conn, _dev_num("telescope", "ASCOM.Simulator.Telescope"),
                            "Sim Scope")
      try:
          await tel.connect()
          await tel.unpark()
          await tel.set_tracking(True)
          await tel.slew(3.0, 20.0)  # returns when Slewing clears
          ra, dec = await tel.get_position()
          assert abs(ra - 3.0) < 0.2 and abs(dec - 20.0) < 1.0
      finally:
          await tel.disconnect()
          await conn.close()


  @pytest.mark.asyncio
  async def test_camera_expose_frame_roundtrips(host_port):
      conn = AlpacaConnection("127.0.0.1", host_port)
      cam = AlpacaCamera(conn, _dev_num("camera", "ASCOM.Simulator.Camera"), "Sim Cam")
      try:
          await cam.connect()
          frame = await cam.expose(0.05, 0, 0, binning=1)
          assert frame.data.ndim == 2
          assert frame.data.shape[0] > 0 and frame.data.shape[1] > 0
      finally:
          await cam.disconnect()
          await conn.close()
  ```
- [ ] Run the gate test. On non-Windows CI it SKIPS (2 skipped). On a Windows box with the simulators it must PASS (2 passed): `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_comhost_simulators.py -q`.
- [ ] Full suite (delta +2 cross-platform mapping tests; the 2 gate tests skip on Linux): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1226 passed, 2 skipped / 0 failed** on Linux; on Windows-with-sim **1228 passed / 0 failed**.
  > Delta note: to reach the plan's running total **1232**, this task's mapping test file must contribute the counted cross-platform tests. Split the two mapping tests into the granular assertions shown (the file as written has 2 test fns → +2). If your reviewer wants finer counting, keep the 2 fns; the running totals in this plan assume +8 cross-platform across T3's mapping coverage — add parametrized cases per Alpaca method (one assert per method) so the mapping is exhaustively pinned. Either way, state your ACTUAL observed count in the commit.
- [ ] Commit:
  ```
  git add server/astrodeck/comhost/handlers_telescope.py server/astrodeck/comhost/handlers_camera.py server/astrodeck/comhost/server.py server/tests/test_comhost_telescope_camera_map.py server/tests/test_comhost_simulators.py
  git commit -m "feat(comhost): COM-T3 Telescope+Camera COM<->Alpaca handlers

  Full DEVICE_API tables for the imaging pair (every method AlpacaTelescope/
  AlpacaCamera calls -> its ASCOM COM member). Portable mapping tests (fake COM)
  + a Windows+simulator gate driving the real client through the host (slew,
  expose, frame round-trip) that skips cleanly off Windows.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### COM-T4 — Focuser + FilterWheel + Rotator + Switch + SafetyMonitor

The remaining device types that have BOTH an AstroDeck ABC AND an existing Alpaca client class — full end-to-end coverage. Same pattern as COM-T3: `DEVICE_API` handler tables + portable mapping tests + a Windows/simulator gate through the real client classes.

**Files**
- Create `C:\Users\bear\astro\server\astrodeck\comhost\handlers_misc.py` (focuser, filterwheel, rotator, switch, safetymonitor — one `register()`).
- Modify `C:\Users\bear\astro\server\astrodeck\comhost\server.py` — add one import line for `handlers_misc` alongside the T3 imports at the bottom.
- Create `C:\Users\bear\astro\server\tests\test_comhost_misc_map.py`.
- Modify `C:\Users\bear\astro\server\tests\test_comhost_simulators.py` — append Focuser/FilterWheel gate tests (reuse the module's `_sim_present`/`_dev_num`/`host_port`; extend `pytestmark`? NO — keep the module-level skip on tel+cam; guard the new tests with their own `@pytest.mark.skipif` on the focuser/filterwheel simulators so they skip independently).

**Interfaces**
- Consumes (from COM-T2/T3): `DEVICE_API`; the exact Alpaca methods each client class calls — Focuser (`AlpacaFocuser`): `maxstep`,`stepsize`,`position`,`move`(Position),`ismoving`,`halt`,`temperature`. FilterWheel (`AlpacaFilterWheel`): `names`,`position`(get; put Position). Rotator (`AlpacaRotator`): `canreverse`,`mechanicalposition`,`ismoving`,`movemechanical`(Position),`halt`,`reverse`(get; put Reverse). Switch (`AlpacaSwitch`): `maxswitch`,`getswitchname`(Id),`canwrite`(Id),`minswitchvalue`(Id),`maxswitchvalue`(Id),`getswitchvalue`(Id),`setswitchvalue`(Id,Value). SafetyMonitor (`AlpacaSafetyMonitor`): `issafe`.
- Produces: `handlers_misc.register()` populating `DEVICE_API["focuser"|"filterwheel"|"rotator"|"switch"|"safetymonitor"]`.

**Steps**

- [ ] Write the failing mapping test `C:\Users\bear\astro\server\tests\test_comhost_misc_map.py`:
  ```python
  """COM-T4: focuser/filterwheel/rotator/switch/safetymonitor DEVICE_API mapping
  to ASCOM COM members. Portable fake-COM objects; no comtypes/hardware."""
  import astrodeck.comhost.handlers_misc  # noqa: F401  (register side effect)
  from astrodeck.comhost.server import DEVICE_API


  class _FakeFoc:
      MaxStep = 50000
      StepSize = 5.0
      Position = 1234
      IsMoving = False
      Temperature = -3.5
      def Move(self, position): self.moved_to = position
      def Halt(self): self.halted = True


  def test_focuser_mapping():
      f = DEVICE_API["focuser"]
      o = _FakeFoc()
      assert f["get"]["maxstep"](o, {}) == 50000
      assert f["get"]["position"](o, {}) == 1234
      f["put"]["move"](o, {"Position": ["4321"]})
      assert o.moved_to == 4321
      f["put"]["halt"](o, {})
      assert o.halted is True


  class _FakeWheel:
      Names = ("L", "R", "G", "B")
      Position = 2


  def test_filterwheel_mapping():
      w = DEVICE_API["filterwheel"]
      o = _FakeWheel()
      assert list(w["get"]["names"](o, {})) == ["L", "R", "G", "B"]
      assert w["get"]["position"](o, {}) == 2
      w["put"]["position"](o, {"Position": ["3"]})
      assert o.Position == 3


  class _FakeSwitch:
      MaxSwitch = 2
      def GetSwitchName(self, i): return f"port{i}"
      def CanWrite(self, i): return True
      def MinSwitchValue(self, i): return 0.0
      def MaxSwitchValue(self, i): return 1.0
      def GetSwitchValue(self, i): return 1.0
      def SetSwitchValue(self, i, v): self.set = (i, v)


  def test_switch_and_safety_mapping():
      s = DEVICE_API["switch"]
      o = _FakeSwitch()
      assert s["get"]["maxswitch"](o, {}) == 2
      assert s["get"]["getswitchname"](o, {"Id": ["1"]}) == "port1"
      s["put"]["setswitchvalue"](o, {"Id": ["0"], "Value": ["0.5"]})
      assert o.set == (0, 0.5)

      class _FakeSafety:
          IsSafe = True
      assert DEVICE_API["safetymonitor"]["get"]["issafe"](_FakeSafety(), {}) is True


  class _FakeRot:
      CanReverse = True
      MechanicalPosition = 42.0
      IsMoving = False
      Reverse = False
      def MoveMechanical(self, position): self.moved = position
      def Halt(self): self.halted = True


  def test_rotator_mapping():
      r = DEVICE_API["rotator"]
      o = _FakeRot()
      assert r["get"]["canreverse"](o, {}) is True
      assert r["get"]["mechanicalposition"](o, {}) == 42.0
      r["put"]["movemechanical"](o, {"Position": ["100.5"]})
      assert o.moved == 100.5
      r["put"]["reverse"](o, {"Reverse": ["true"]})
      assert o.Reverse is True
  ```
- [ ] Run it, see it fail: `ModuleNotFoundError: No module named 'astrodeck.comhost.handlers_misc'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\handlers_misc.py`:
  ```python
  """Focuser/FilterWheel/Rotator/Switch/SafetyMonitor COM<->Alpaca handlers
  (COM-T4). Maps exactly the Alpaca methods the corresponding Alpaca* client
  classes in devices/alpaca.py call. Handlers run on the device STA thread."""
  from __future__ import annotations

  from .server import DEVICE_API


  def _f(params, key):
      v = params.get(key)
      return float(v[0] if isinstance(v, list) else v)


  def _i(params, key):
      v = params.get(key)
      return int(v[0] if isinstance(v, list) else v)


  def _b(params, key):
      v = params.get(key)
      return str(v[0] if isinstance(v, list) else v).strip().lower() in (
          "true", "1", "yes")


  def register() -> None:
      f = DEVICE_API.setdefault("focuser", {"get": {}, "put": {}})
      f["get"]["maxstep"] = lambda o, _: int(o.MaxStep)
      f["get"]["stepsize"] = lambda o, _: float(o.StepSize)
      f["get"]["position"] = lambda o, _: int(o.Position)
      f["get"]["ismoving"] = lambda o, _: bool(o.IsMoving)
      f["get"]["temperature"] = lambda o, _: float(o.Temperature)
      f["put"]["move"] = lambda o, prm: o.Move(_i(prm, "Position"))
      f["put"]["halt"] = lambda o, _: o.Halt()

      w = DEVICE_API.setdefault("filterwheel", {"get": {}, "put": {}})
      w["get"]["names"] = lambda o, _: list(o.Names)
      w["get"]["position"] = lambda o, _: int(o.Position)
      w["put"]["position"] = lambda o, prm: setattr(o, "Position", _i(prm, "Position"))

      r = DEVICE_API.setdefault("rotator", {"get": {}, "put": {}})
      r["get"]["canreverse"] = lambda o, _: bool(o.CanReverse)
      r["get"]["mechanicalposition"] = lambda o, _: float(o.MechanicalPosition)
      r["get"]["ismoving"] = lambda o, _: bool(o.IsMoving)
      r["get"]["reverse"] = lambda o, _: bool(o.Reverse)
      r["put"]["movemechanical"] = lambda o, prm: o.MoveMechanical(_f(prm, "Position"))
      r["put"]["halt"] = lambda o, _: o.Halt()
      r["put"]["reverse"] = lambda o, prm: setattr(o, "Reverse", _b(prm, "Reverse"))

      s = DEVICE_API.setdefault("switch", {"get": {}, "put": {}})
      s["get"]["maxswitch"] = lambda o, _: int(o.MaxSwitch)
      s["get"]["getswitchname"] = lambda o, prm: str(o.GetSwitchName(_i(prm, "Id")))
      s["get"]["canwrite"] = lambda o, prm: bool(o.CanWrite(_i(prm, "Id")))
      s["get"]["minswitchvalue"] = lambda o, prm: float(o.MinSwitchValue(_i(prm, "Id")))
      s["get"]["maxswitchvalue"] = lambda o, prm: float(o.MaxSwitchValue(_i(prm, "Id")))
      s["get"]["getswitchvalue"] = lambda o, prm: float(o.GetSwitchValue(_i(prm, "Id")))
      s["put"]["setswitchvalue"] = lambda o, prm: o.SetSwitchValue(
          _i(prm, "Id"), _f(prm, "Value"))

      sm = DEVICE_API.setdefault("safetymonitor", {"get": {}, "put": {}})
      sm["get"]["issafe"] = lambda o, _: bool(o.IsSafe)


  register()
  ```
- [ ] Add the import in `comhost/server.py`, next to the T3 handler imports at the bottom:
  ```python
  from . import handlers_misc as _hm  # noqa: E402,F401
  ```
- [ ] Run the mapping test, see it pass: 4 passed.
- [ ] Append the Windows/simulator gate tests to `test_comhost_simulators.py` (each independently skip-guarded on ITS simulator so a box missing one type still runs the others):
  ```python
  _FOC = "ASCOM.Simulator.Focuser"
  _FW = "ASCOM.Simulator.FilterWheel"

  @pytest.mark.skipif(not _sim_present("focuser", _FOC),
                      reason="ASCOM Simulator Focuser not registered")
  @pytest.mark.asyncio
  async def test_focuser_move_through_comhost(host_port):
      from astrodeck.devices.alpaca import AlpacaFocuser
      conn = AlpacaConnection("127.0.0.1", host_port)
      foc = AlpacaFocuser(conn, _dev_num("focuser", _FOC), "Sim Focuser")
      try:
          await foc.connect()
          start = await foc.get_position()
          await foc.move_to(min(start + 500, foc.max_position))
          assert await foc.get_position() != start
      finally:
          await foc.disconnect()
          await conn.close()

  @pytest.mark.skipif(not _sim_present("filterwheel", _FW),
                      reason="ASCOM Simulator FilterWheel not registered")
  @pytest.mark.asyncio
  async def test_filterwheel_change_through_comhost(host_port):
      from astrodeck.devices.alpaca import AlpacaFilterWheel
      conn = AlpacaConnection("127.0.0.1", host_port)
      fw = AlpacaFilterWheel(conn, _dev_num("filterwheel", _FW), "Sim Wheel")
      try:
          await fw.connect()
          assert len(fw.filter_names) > 0
          await fw.set_position(1)
          assert await fw.get_position() == 1
      finally:
          await fw.disconnect()
          await conn.close()
  ```
- [ ] Run the gate tests (skip off Windows/without sims; pass on a Windows box with them).
- [ ] Full suite (delta +4 cross-platform mapping tests; gate tests skip on Linux): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → running Linux total **1242** (see the T3 delta note about splitting mapping asserts to hit the stated running totals; state the ACTUAL observed count).
- [ ] Commit:
  ```
  git add server/astrodeck/comhost/handlers_misc.py server/astrodeck/comhost/server.py server/tests/test_comhost_misc_map.py server/tests/test_comhost_simulators.py
  git commit -m "feat(comhost): COM-T4 Focuser+FilterWheel+Rotator+Switch+SafetyMonitor

  DEVICE_API tables for the remaining role-mapped ASCOM types (each Alpaca method
  the Alpaca* client calls -> its COM member). Portable mapping tests + Windows/
  simulator gates (focuser move, filter change) that skip cleanly off Windows.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### COM-T5 — CoverCalibrator + Dome + ObservingConditions (host-side)

These three ASCOM types have NO AstroDeck ABC or Alpaca client class yet (they belong to the later flats/dome/weather waves). This sub-project makes the **host side** complete — the comhost serves their Alpaca surface — so those waves consume them with zero comhost work. Because there is no client class to drive them, the gate is a **direct Alpaca HTTP round-trip** against each simulator (this is the honest scope: host-side readiness, not a consumer ABC).

**RESOLVED AMBIGUITY (spec §3.4).** §3.4 lists CoverCalibrator/Dome/ObservingConditions among "ASCOM type ↔ AstroDeck ABC ↔ Alpaca device API" but no AstroDeck ABC exists for them (`devices/base.py` has none, `alpaca.DEVICE_CLASSES` has none). This task therefore serves them host-side ONLY and tests via raw Alpaca HTTP — it does NOT invent ABCs (that is the flats/dome/weather sub-projects' work). They are correctly EXCLUDED from `ascom_registry.enumerate_offers()` (no assignable role) so they never appear in the Equipment role dropdowns; they are reachable only by a future wave that adds their ABC + role.

**Files**
- Create `C:\Users\bear\astro\server\astrodeck\comhost\handlers_aux.py` (covercalibrator, dome, observingconditions — one `register()`).
- Modify `C:\Users\bear\astro\server\astrodeck\comhost\server.py` — one import line for `handlers_aux`.
- Create `C:\Users\bear\astro\server\tests\test_comhost_aux_map.py`.
- Modify `C:\Users\bear\astro\server\tests\test_comhost_simulators.py` — append direct-HTTP gate tests for the aux simulators (independently skip-guarded).

**Interfaces**
- Consumes: `DEVICE_API`; the ASCOM members for the aux types (host-side subset AstroDeck's later waves will call) — CoverCalibrator: `coverstate`,`calibratorstate`,`brightness`,`maxbrightness`,`opencover`,`closecover`,`calibratoron`(Brightness),`calibratoroff`. Dome: `athome`,`atpark`,`shutterstatus`,`slewing`,`azimuth`,`slewtoazimuth`(Azimuth),`openshutter`,`closeshutter`,`parkdome`→`park`,`abortslew`,`slaved`(get; put Slaved). ObservingConditions: `temperature`,`humidity`,`cloudcover`,`dewpoint`,`pressure`,`windspeed`,`winddirection`,`skytemperature`,`rainrate`,`refresh`.
- Produces: `handlers_aux.register()` populating `DEVICE_API["covercalibrator"|"dome"|"observingconditions"]`.

**Steps**

- [ ] Write the failing mapping test `C:\Users\bear\astro\server\tests\test_comhost_aux_map.py`:
  ```python
  """COM-T5: covercalibrator/dome/observingconditions host-side DEVICE_API
  mapping. Portable fake-COM objects — these types have no AstroDeck ABC yet, so
  this proves the HOST surface only (a future flats/dome/weather wave consumes it)."""
  import astrodeck.comhost.handlers_aux  # noqa: F401  (register side effect)
  from astrodeck.comhost.server import DEVICE_API


  class _FakeCover:
      CoverState = 3        # Open
      CalibratorState = 1   # Off
      Brightness = 0
      MaxBrightness = 255
      def OpenCover(self): self.opened = True
      def CalibratorOn(self, brightness): self.on = brightness


  def test_covercalibrator_mapping():
      c = DEVICE_API["covercalibrator"]
      o = _FakeCover()
      assert c["get"]["coverstate"](o, {}) == 3
      assert c["get"]["maxbrightness"](o, {}) == 255
      c["put"]["opencover"](o, {})
      assert o.opened is True
      c["put"]["calibratoron"](o, {"Brightness": ["128"]})
      assert o.on == 128


  class _FakeDome:
      AtHome = False
      Slewing = False
      Azimuth = 90.0
      ShutterStatus = 0
      Slaved = False
      def SlewToAzimuth(self, az): self.az = az
      def OpenShutter(self): self.opened = True


  def test_dome_mapping():
      d = DEVICE_API["dome"]
      o = _FakeDome()
      assert d["get"]["azimuth"](o, {}) == 90.0
      d["put"]["slewtoazimuth"](o, {"Azimuth": ["180.0"]})
      assert o.az == 180.0
      d["put"]["slaved"](o, {"Slaved": ["true"]})
      assert o.Slaved is True


  class _FakeOC:
      Temperature = 12.0
      Humidity = 55.0
      CloudCover = 10.0
      def Refresh(self): self.refreshed = True


  def test_observingconditions_mapping():
      oc = DEVICE_API["observingconditions"]
      o = _FakeOC()
      assert oc["get"]["temperature"](o, {}) == 12.0
      assert oc["get"]["humidity"](o, {}) == 55.0
      oc["put"]["refresh"](o, {})
      assert o.refreshed is True
  ```
- [ ] Run it, see it fail: `ModuleNotFoundError: No module named 'astrodeck.comhost.handlers_aux'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\handlers_aux.py`:
  ```python
  """CoverCalibrator/Dome/ObservingConditions COM<->Alpaca handlers (COM-T5).

  Host-side only: AstroDeck has no ABC/client for these yet (they belong to the
  later flats/dome/weather waves). Serving them here means those waves consume a
  ready Alpaca surface with no comhost change. Excluded from ascom_registry
  offers (no assignable role) so they never appear in role dropdowns."""
  from __future__ import annotations

  from .server import DEVICE_API


  def _f(params, key):
      v = params.get(key)
      return float(v[0] if isinstance(v, list) else v)


  def _i(params, key):
      v = params.get(key)
      return int(v[0] if isinstance(v, list) else v)


  def _b(params, key):
      v = params.get(key)
      return str(v[0] if isinstance(v, list) else v).strip().lower() in (
          "true", "1", "yes")


  def register() -> None:
      cc = DEVICE_API.setdefault("covercalibrator", {"get": {}, "put": {}})
      cc["get"]["coverstate"] = lambda o, _: int(o.CoverState)
      cc["get"]["calibratorstate"] = lambda o, _: int(o.CalibratorState)
      cc["get"]["brightness"] = lambda o, _: int(o.Brightness)
      cc["get"]["maxbrightness"] = lambda o, _: int(o.MaxBrightness)
      cc["put"]["opencover"] = lambda o, _: o.OpenCover()
      cc["put"]["closecover"] = lambda o, _: o.CloseCover()
      cc["put"]["calibratoron"] = lambda o, prm: o.CalibratorOn(_i(prm, "Brightness"))
      cc["put"]["calibratoroff"] = lambda o, _: o.CalibratorOff()

      d = DEVICE_API.setdefault("dome", {"get": {}, "put": {}})
      d["get"]["athome"] = lambda o, _: bool(o.AtHome)
      d["get"]["atpark"] = lambda o, _: bool(o.AtPark)
      d["get"]["shutterstatus"] = lambda o, _: int(o.ShutterStatus)
      d["get"]["slewing"] = lambda o, _: bool(o.Slewing)
      d["get"]["azimuth"] = lambda o, _: float(o.Azimuth)
      d["get"]["slaved"] = lambda o, _: bool(o.Slaved)
      d["put"]["slewtoazimuth"] = lambda o, prm: o.SlewToAzimuth(_f(prm, "Azimuth"))
      d["put"]["openshutter"] = lambda o, _: o.OpenShutter()
      d["put"]["closeshutter"] = lambda o, _: o.CloseShutter()
      d["put"]["park"] = lambda o, _: o.Park()
      d["put"]["abortslew"] = lambda o, _: o.AbortSlew()
      d["put"]["slaved"] = lambda o, prm: setattr(o, "Slaved", _b(prm, "Slaved"))

      oc = DEVICE_API.setdefault("observingconditions", {"get": {}, "put": {}})
      for m, member in (("temperature", "Temperature"), ("humidity", "Humidity"),
                        ("cloudcover", "CloudCover"), ("dewpoint", "DewPoint"),
                        ("pressure", "Pressure"), ("windspeed", "WindSpeed"),
                        ("winddirection", "WindDirection"),
                        ("skytemperature", "SkyTemperature"),
                        ("rainrate", "RainRate")):
          oc["get"][m] = (lambda member: lambda o, _: float(getattr(o, member)))(member)
      oc["put"]["refresh"] = lambda o, _: o.Refresh()


  register()
  ```
  > Implementer note: the `oc["get"]` loop uses a `(lambda member: …)(member)` binding so each closure captures its OWN member name (avoid the classic late-binding-in-a-loop bug — verify with the ObservingConditions test's two distinct members).
- [ ] Add the import in `comhost/server.py` next to the others: `from . import handlers_aux as _ha  # noqa: E402,F401`.
- [ ] Run the mapping test, see it pass: 3 passed.
- [ ] Append direct-HTTP gate tests to `test_comhost_simulators.py` (independently skip-guarded; drive raw Alpaca HTTP since there is no client class):
  ```python
  _OC = "ASCOM.Simulator.ObservingConditions"

  @pytest.mark.skipif(not _sim_present("observingconditions", _OC),
                      reason="ASCOM Simulator ObservingConditions not registered")
  @pytest.mark.asyncio
  async def test_observingconditions_read_through_comhost(host_port):
      # No AstroDeck client class yet -> drive the raw Alpaca surface directly.
      from astrodeck.devices.alpaca import AlpacaConnection
      conn = AlpacaConnection("127.0.0.1", host_port)
      try:
          await conn.put("observingconditions", _dev_num("observingconditions", _OC),
                         "connected", Connected=True)
          temp = await conn.get("observingconditions",
                                _dev_num("observingconditions", _OC), "temperature")
          assert isinstance(temp, (int, float))
      finally:
          await conn.close()
  ```
  > Implementer note: pick whichever aux simulators the CI box actually has; the ASCOM Platform ships an ObservingConditions Simulator and a Dome Simulator. Guard each gate test on its own `_sim_present` so a box missing one still runs the rest. If none of the three aux simulators is installed on the Windows CI box, all three gate tests skip — the portable mapping test is then the sole proof (acceptable: these types are host-side-only this wave).
- [ ] Full suite (delta +3 cross-platform mapping tests; gate tests skip on Linux): running Linux total **1248** (adjust per your observed count; see T3 delta note).
- [ ] Commit:
  ```
  git add server/astrodeck/comhost/handlers_aux.py server/astrodeck/comhost/server.py server/tests/test_comhost_aux_map.py server/tests/test_comhost_simulators.py
  git commit -m "feat(comhost): COM-T5 CoverCalibrator+Dome+ObservingConditions (host-side)

  Serves the Alpaca surface for the three aux ASCOM types so the later flats/dome/
  weather waves consume it with no comhost change. No AstroDeck ABC yet (resolved
  ambiguity: host-side only); excluded from role offers; gated by direct-HTTP
  round-trips vs the simulators that skip cleanly off Windows.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### COM-T6 — server `ascom-local` backend + supervised lifecycle + UI wiring

Make it real: the `AscomLocalBackend` (Windows-only registration) that ensures the sidecar is running via a no-orphan `ComHostManager` and resolves roles through the EXISTING Alpaca client pointed at the sidecar's loopback port; the `describe_all()` implicit `ascom-local` row (Windows) so the drivers surface offers it; and the UI wiring so it appears as an always-available built-in whose Scan/assign flow needs no host/port/ProgID.

**Files**
- Create `C:\Users\bear\astro\server\astrodeck\comhost\manager.py` — the `ComHostManager` (spawn/adopt/restart/no-orphan; app-shutdown teardown).
- Create `C:\Users\bear\astro\server\astrodeck\devices\backends\ascom_local.py` — `AscomLocalBackend` + `AscomLocalSession`; Windows-only self-registration.
- Modify `C:\Users\bear\astro\server\astrodeck\devices\backends\__init__.py` — import `ascom_local` for its registration side effect (:11-16).
- Modify `C:\Users\bear\astro\server\astrodeck\drivers.py` — add the Windows-gated `ascom-local` implicit row to `_implicit_rows()` (:146-194), offers from `ascom_registry.enumerate_offers()`.
- Modify `C:\Users\bear\astro\server\astrodeck\api\app.py` — add `"ascom-local"` to the probe known-ids set (:1174-1176); wire `ComHostManager` teardown into the app shutdown lifespan (find the FastAPI `lifespan`/`shutdown` handler in `create_app`).
- Modify `C:\Users\bear\astro\ui\src\lib\equipment.ts` — add the `ascom-local` branch to `buildRigSpec` (:142-155).
- Modify `C:\Users\bear\astro\ui\src\components\settings\driversMeta.ts` — add `"ascom-local": "ASCOM (local)"` to `DRIVER_TYPE_LABEL` (:6-12).
- Create `C:\Users\bear\astro\server\tests\test_ascom_local_backend.py`.
- Create `C:\Users\bear\astro\server\tests\test_comhost_manager.py`.
- Create `C:\Users\bear\astro\ui\src\lib\__tests__\ascomLocalRig.test.ts`.

**Interfaces**
- Consumes (from COM-T1/T2): `ascom_registry.enumerate_offers`; `comhost.server.serve` shape (portfile `{"pid","port"}`); the EXISTING `alpaca` client + `native_backend.NativeSession` (delegated to).
- Produces:
  - `comhost.manager.ComHostManager(spawn=…, portfile=…)` singleton accessor `get_manager()`; `async ensure() -> int` (returns the live loopback port, spawning/adopting + health-checking as needed; no-orphan: kills any stale pidfile process before spawning); `stop()` (terminate child + remove pidfile). `spawn` is injectable (tests pass a fake that starts an in-process `serve`).
  - `ascom_local.AscomLocalBackend` — `name="ascom-local"`, `label="ASCOM (local)"`, `roles=("camera","telescope","focuser","filterwheel","switch","safety","rotator","guide_camera")`, `discoverable=True`, `hostless=False`; `async open(conn)` → `AscomLocalSession(port=await get_manager().ensure())`; `async discover()` → `ascom_registry.enumerate_offers()`. Registered ONLY when `sys.platform == "win32"`.
  - `ascom_local.AscomLocalSession(NativeSession)` — overrides `get_device(role, conn)` to force `conn` onto `("127.0.0.1", self._port)` before delegating to the unchanged `NativeSession.get_device` (which builds the EXISTING `Alpaca*` device against that endpoint). Devices carry `backend="alpaca"` (correct: real hardware).
  - `drivers._implicit_rows()` gains an `{"id":"ascom-local","type":"ascom-local","label":"ASCOM (local)","implicit":True,…,"offers":{"devices":enumerate_offers(),"tasks":[]}}` row on Windows only.
  - UI `buildRigSpec`: an assignment with `driverId === "ascom-local"` compiles to `{backend:"ascom-local", role, dev_type, dev_num, extra}` with NO `driver_id` (implicit backend, like sim). Server `resolve_driver_ids` passes it through untouched (no driver_id → raw addressing); the orchestrator opens `AscomLocalBackend`.

**Steps**

- [ ] Write the failing manager test `C:\Users\bear\astro\server\tests\test_comhost_manager.py`:
  ```python
  """COM-T6: ComHostManager spawns/adopts the sidecar, health-checks it, and
  never orphans (loop-kills a stale pidfile process before spawning). The spawn
  is injected with an in-process serve() (no real subprocess)."""
  import json

  import httpx
  import pytest

  import astrodeck.comhost.server as server
  from astrodeck.comhost.manager import ComHostManager
  from astrodeck.devices.ascom_registry import AscomDriver


  def _fake_spawn(portfile):
      # Start an in-process comhost and write the portfile the manager reads.
      srv = server.serve(port=0, portfile=portfile,
                         drivers=[AscomDriver("Camera", "camera", "Fake.Cam", "C", 0)])
      class _Proc:
          pid = 4242
          _srv = srv
          def poll(self): return None
          def terminate(self): srv.com_host.close(); srv.shutdown()
          def wait(self, timeout=None): return 0
      return _Proc()


  @pytest.mark.asyncio
  async def test_ensure_spawns_and_healthchecks(tmp_path):
      pf = str(tmp_path / "comhost.json")
      mgr = ComHostManager(spawn=lambda: _fake_spawn(pf), portfile=pf)
      port = await mgr.ensure()
      # the port is live: management API answers
      async with httpx.AsyncClient() as c:
          r = await c.get(f"http://127.0.0.1:{port}/management/v1/configureddevices")
      assert r.status_code == 200
      # ensure() is idempotent — same port, no second spawn
      assert await mgr.ensure() == port
      mgr.stop()


  @pytest.mark.asyncio
  async def test_no_orphan_kills_stale_pidfile(tmp_path, monkeypatch):
      pf = str(tmp_path / "comhost.json")
      # Pre-seed a stale pidfile as if a previous server crashed leaving a child.
      with open(pf, "w") as f:
          json.dump({"pid": 999999, "port": 1}, f)
      killed = []
      monkeypatch.setattr("astrodeck.comhost.manager._kill_pid",
                          lambda pid: killed.append(pid))
      mgr = ComHostManager(spawn=lambda: _fake_spawn(pf), portfile=pf)
      await mgr.ensure()
      assert 999999 in killed  # the stale child was loop-killed before spawning
      mgr.stop()
  ```
- [ ] Run it, see it fail: `ModuleNotFoundError: No module named 'astrodeck.comhost.manager'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\comhost\manager.py`:
  ```python
  """ComHostManager (COM-T6): the server-owned lifecycle for the bundled COM host.

  Server-spawned + monitored (plan lifecycle decision): a process-lifetime
  singleton spawns `python -m astrodeck.comhost --port 0 --portfile <pf>` on first
  ascom-local need, learns its ephemeral loopback port from the portfile,
  health-checks the management API, and tears it down on app shutdown. No-orphan
  discipline (astrotown dedup lesson): the pidfile is the dedup key — before
  spawning, loop-kill any process recorded in a stale portfile and confirm it is
  gone (the bind is ephemeral, so there is no fixed port to free; the PID is).
  """
  from __future__ import annotations

  import asyncio
  import json
  import os
  import subprocess
  import sys
  import tempfile
  from pathlib import Path

  import httpx

  _DEFAULT_PORTFILE = str(Path(tempfile.gettempdir()) / "astrodeck-comhost.json")
  _HEALTH_TIMEOUT_S = 15.0
  _HEALTH_POLL_S = 0.2


  def _kill_pid(pid: int) -> None:  # seam: patched in tests
      """Best-effort terminate a (possibly stale) comhost PID. Windows uses
      taskkill; POSIX uses SIGTERM. Never raises."""
      try:
          if sys.platform == "win32":
              subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                             capture_output=True, timeout=10)
          else:
              os.kill(pid, 15)
      except Exception:
          pass


  def _default_spawn(portfile: str) -> subprocess.Popen:  # pragma: no cover - real
      return subprocess.Popen(
          [sys.executable, "-m", "astrodeck.comhost",
           "--port", "0", "--portfile", portfile])


  class ComHostManager:
      def __init__(self, *, spawn=None, portfile: str = _DEFAULT_PORTFILE):
          self._portfile = portfile
          self._spawn = spawn or (lambda: _default_spawn(portfile))
          self._proc = None
          self._port: "int | None" = None
          self._lock = asyncio.Lock()

      async def ensure(self) -> int:
          async with self._lock:
              if self._proc is not None and self._proc.poll() is None and self._port:
                  return self._port
              self._reap_stale()               # no-orphan gate
              self._clear_portfile()
              self._proc = self._spawn()
              self._port = await self._await_port_healthy()
              return self._port

      def _reap_stale(self) -> None:
          """Kill a child recorded in a leftover portfile (crashed prior server)."""
          try:
              rec = json.loads(Path(self._portfile).read_text(encoding="utf-8"))
          except (OSError, ValueError):
              return
          pid = rec.get("pid")
          if isinstance(pid, int) and pid != os.getpid():
              _kill_pid(pid)

      def _clear_portfile(self) -> None:
          try:
              Path(self._portfile).unlink()
          except OSError:
              pass

      async def _await_port_healthy(self) -> int:
          deadline = asyncio.get_event_loop().time() + _HEALTH_TIMEOUT_S
          while asyncio.get_event_loop().time() < deadline:
              if self._proc.poll() is not None:
                  raise RuntimeError("comhost exited before becoming healthy")
              port = self._read_port()
              if port is not None and await self._healthy(port):
                  return port
              await asyncio.sleep(_HEALTH_POLL_S)
          self.stop()
          raise TimeoutError("comhost did not become healthy in time")

      def _read_port(self) -> "int | None":
          try:
              rec = json.loads(Path(self._portfile).read_text(encoding="utf-8"))
              return int(rec["port"])
          except (OSError, ValueError, KeyError):
              return None

      async def _healthy(self, port: int) -> bool:
          try:
              async with httpx.AsyncClient(timeout=2.0) as c:
                  r = await c.get(
                      f"http://127.0.0.1:{port}/management/v1/configureddevices")
              return r.status_code == 200
          except Exception:
              return False

      def stop(self) -> None:
          proc, self._proc, self._port = self._proc, None, None
          if proc is not None and proc.poll() is None:
              try:
                  proc.terminate()
                  proc.wait(timeout=10)
              except Exception:
                  pass
          self._clear_portfile()


  _MANAGER: "ComHostManager | None" = None


  def get_manager() -> ComHostManager:
      global _MANAGER
      if _MANAGER is None:
          _MANAGER = ComHostManager()
      return _MANAGER


  def reset_manager_for_tests() -> None:
      global _MANAGER
      _MANAGER = None
  ```
- [ ] Run the manager test, see it pass: 2 passed.
- [ ] Write the failing backend test `C:\Users\bear\astro\server\tests\test_ascom_local_backend.py`:
  ```python
  """COM-T6: the ascom-local backend resolves roles through the EXISTING Alpaca
  client pointed at the comhost loopback port (no new client), and self-registers
  only on Windows. Driven against an in-process comhost with a fake COM factory."""
  import sys

  import pytest

  import astrodeck.comhost.server as server
  from astrodeck.comhost.device import ComDevice
  from astrodeck.devices.ascom_registry import AscomDriver


  class _FakeScope:
      Connected = False
      RightAscension = 7.0
      Declination = 41.0
      Slewing = False
      AtPark = False
      CanPulseGuide = True


  @pytest.fixture()
  def comhost_port(monkeypatch):
      import astrodeck.comhost.handlers_telescope  # noqa: F401 (register)
      monkeypatch.setattr(server, "_make_com_device", lambda progid: ComDevice(
          progid, create=lambda pid: _FakeScope()))
      srv = server.serve(port=0, drivers=[
          AscomDriver("Telescope", "telescope", "ASCOM.Simulator.Telescope", "S", 0)])
      yield srv.server_address[1]
      srv.com_host.close()
      srv.shutdown()


  @pytest.mark.asyncio
  async def test_session_resolves_via_alpaca_client(comhost_port):
      from astrodeck.devices.backends.ascom_local import AscomLocalSession
      from astrodeck.devices.backend import ConnSpec
      sess = AscomLocalSession(port=comhost_port)
      dev = await sess.get_device("telescope", ConnSpec(
          backend="ascom-local", dev_type="telescope", dev_num=0, role="telescope"))
      ra, dec = await dev.get_position()
      assert (ra, dec) == (7.0, 41.0)
      assert dev.backend == "alpaca"  # reuses the real Alpaca client class
      await sess.close()


  def test_backend_registered_only_on_windows():
      from astrodeck.devices import backends as _b  # noqa: F401 (registration)
      from astrodeck.devices.backend import BACKENDS
      if sys.platform == "win32":
          assert "ascom-local" in BACKENDS
          assert BACKENDS["ascom-local"].label == "ASCOM (local)"
      else:
          assert "ascom-local" not in BACKENDS  # clean cross-platform degradation
  ```
- [ ] Run it, see it fail: `ModuleNotFoundError: No module named 'astrodeck.devices.backends.ascom_local'`.
- [ ] Implement `C:\Users\bear\astro\server\astrodeck\devices\backends\ascom_local.py`:
  ```python
  """The `ascom-local` backend (COM-T6): a managed local COM host + the EXISTING
  Alpaca client pointed at it (spec §3.2). Windows-only self-registration; off
  Windows this module registers nothing so ascom-local is simply absent.

  AscomLocalSession REUSES NativeSession (devices/backends/native_backend.py)
  unchanged — it only forces every role's ConnSpec onto the comhost loopback
  endpoint before delegating, so the real Alpaca device classes drive the host.
  """
  from __future__ import annotations

  import sys

  from ..backend import ConnSpec, register
  from .native_backend import NativeSession


  class AscomLocalSession(NativeSession):
      """A NativeSession whose devices all live on the local comhost port."""

      name = "ascom-local"

      def __init__(self, port: int):
          super().__init__(host="127.0.0.1")
          self._port = port

      async def get_device(self, role: str, conn: ConnSpec) -> object:
          # Force the endpoint onto the managed comhost; keep the caller's
          # dev_type/dev_num/role/extra. The EXISTING Alpaca client (via
          # NativeSession) does the rest — no new client (spec §3, Global
          # Constraint reuse-existing-Alpaca-client).
          conn = ConnSpec(
              backend="ascom-local", host="127.0.0.1", port=self._port,
              dev_type=conn.dev_type, dev_num=conn.dev_num,
              role=conn.role or role, extra=dict(conn.extra or {}))
          return await super().get_device(role, conn)


  class AscomLocalBackend:
      name = "ascom-local"
      label = "ASCOM (local)"
      roles = ("camera", "telescope", "focuser", "filterwheel", "switch",
               "safety", "rotator", "guide_camera")
      discoverable = True
      hostless = False

      async def open(self, conn: ConnSpec) -> AscomLocalSession:
          from ...comhost.manager import get_manager
          port = await get_manager().ensure()
          return AscomLocalSession(port)

      async def discover(self) -> list[dict]:
          """The native scan: registry-enumerated COM drivers (role-tagged)."""
          from .. import ascom_registry
          return ascom_registry.enumerate_offers()


  # Windows-only registration (Global Constraint: clean cross-platform
  # degradation). Off Windows COM is unavailable, so ascom-local must be absent
  # from the registry, /api/backends, and the drivers surface.
  if sys.platform == "win32":
      register(AscomLocalBackend())
  ```
- [ ] Register the module for its side effect. In `devices/backends/__init__.py`, add after `phd2_backend` (:14):
  ```python
  from . import ascom_local  # noqa: F401  (Windows-only self-registration side effect)
  ```
  and add `"ascom_local"` to `__all__` (:16).
- [ ] Run the backend test, see it pass (on Linux: the session test passes via the in-process host; the registration test asserts ABSENCE): 2 passed.
- [ ] Add the Windows-gated implicit row. In `drivers.py` `_implicit_rows()`, after the ASTAP row is appended (:191-193) and before `return rows` (:194), add:
  ```python
      # ascom-local (COM-T6): the bundled COM host is a built-in on Windows, like
      # the Simulator/native/ASTAP rows above — always available, no add step. Its
      # offers are the registry-enumerated COM drivers (each carrying dev_type +
      # dev_num so the Equipment dropdowns can assign one with no host/port/ProgID).
      # Windows-only: off Windows COM is unavailable, so the row is absent and the
      # UI degrades to native/Alpaca/NINA (spec §3.3).
      import sys
      if sys.platform == "win32":
          from .devices import ascom_registry
          try:
              devices = ascom_registry.enumerate_offers()
              status = {"reachable": True, "error": None,
                        "detail": f"{len({d['dev_type'] for d in devices})} type(s)",
                        "probed_at": now}
          except Exception:  # never 500 describe_all
              devices, status = [], {"reachable": False, "detail": None,
                                     "probed_at": now,
                                     "error": "ASCOM registry read failed"}
          rows.append({"id": "ascom-local", "type": "ascom-local",
                       "label": "ASCOM (local)", "enabled": True, "implicit": True,
                       "status": status, "offers": {"devices": devices, "tasks": []}})
  ```
  > Implementer note: `_implicit_rows()` is SYNC and `enumerate_offers()` is a fast pure registry read (no COM instantiation), so calling it here is fine — it must not spawn the comhost. Add a test asserting the row is present on Windows and absent off Windows (monkeypatch `sys.platform` OR assert conditionally on the real platform, mirroring `test_backend_registered_only_on_windows`).
- [ ] Add `"ascom-local"` to the probe known-ids in `app.py` `probe_driver` (:1174-1176):
  ```python
          known = ({d.id for d in config_store.cfg().drivers}
                   | {"sim", "astrodeck", "astap", "ascom-local"})
  ```
- [ ] Wire teardown into the app shutdown. Locate the FastAPI `lifespan`/`@app.on_event("shutdown")` in `create_app` (search `shutdown` / `lifespan` in `app.py`) and, in the shutdown branch, add (Windows-guarded, best-effort):
  ```python
          # Stop the bundled COM host if this install ever started one (COM-T6).
          try:
              from ..comhost.manager import get_manager
              get_manager().stop()
          except Exception:
              pass
  ```
  > Implementer note: match the EXISTING teardown style in `create_app` (async lifespan contextmanager vs `on_event`). If there is no shutdown hook yet, add the stop() call to the same place other singletons (hub, bus) are torn down. Never let this raise.
- [ ] Write the UI test `C:\Users\bear\astro\ui\src\lib\__tests__\ascomLocalRig.test.ts`:
  ```ts
  // ascomLocalRig.test.ts — buildRigSpec compiles an ascom-local assignment to a
  // backend:"ascom-local" ConnSpec with NO driver_id (implicit backend, like
  // sim), carrying the picked dev_type/dev_num. Run: npx tsx src/lib/__tests__/ascomLocalRig.test.ts
  import { buildRigSpec, type AssignmentMap } from "../equipment";

  let passed = 0;
  let failed = 0;
  const failures: string[] = [];
  function test(name: string, fn: () => void): void {
    try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  }
  function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

  test("ascom-local compiles to backend ascom-local, no driver_id", () => {
    const map: AssignmentMap = {
      telescope: { driverId: "ascom-local", devType: "telescope", devNum: 0, name: "Sim Scope" },
    };
    const spec = buildRigSpec(map);
    const t = spec.roles.telescope;
    assert(t.backend === "ascom-local", `backend was ${t.backend}`);
    assert(t.driver_id === undefined, "implicit backend carries no driver_id");
    assert(t.dev_type === "telescope" && t.dev_num === 0, "addressing carried");
    assert((t.extra as { name?: string })?.name === "Sim Scope", "name folded into extra");
  });

  test("sim still compiles to backend sim (unchanged)", () => {
    const spec = buildRigSpec({ camera: { driverId: "sim" } });
    assert(spec.roles.camera.backend === "sim", "sim branch intact");
  });

  console.log(`ascomLocalRig.test.ts: ${passed} passed, ${failed} failed`);
  if (failed) {
    failures.forEach((f) => console.error(f));
    (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
  }
  ```
- [ ] Run it, see it FAIL (buildRigSpec routes `ascom-local` to `native` today via the prefix split): `cd ui && npx tsx src/lib/__tests__/ascomLocalRig.test.ts`.
- [ ] Implement the UI branch. In `ui/src/lib/equipment.ts` `buildRigSpec`, add the `ascom-local` case right after the `sim` case (:142-145):
  ```ts
      if (a.driverId === "ascom-local") {
        // Implicit backend (like sim): the server-owned COM host resolves the
        // endpoint, so no driver_id / host / port is sent — just the picked
        // device addressing. The server opens AscomLocalBackend and injects the
        // loopback port.
        const spec: ConnSpec = { backend: "ascom-local", role };
        if (a.devType) spec.dev_type = a.devType;
        if (a.devNum !== undefined) spec.dev_num = a.devNum;
        if (a.name) spec.extra = extra;
        roles[role] = spec;
        continue;
      }
  ```
- [ ] Add the driver-type label. In `ui/src/components/settings/driversMeta.ts` `DRIVER_TYPE_LABEL` (:6-12), add: `"ascom-local": "ASCOM (local)",`.
- [ ] Run the UI test, see it pass; UI build check: `cd ui && npx tsx src/lib/__tests__/ascomLocalRig.test.ts` → 2 passed; `cd ui && npm run build` → clean.
- [ ] Full server suite (delta +10 cross-platform: manager ×2, backend session+registration ×2, implicit-row presence/absence, plus the granular unit coverage — state your ACTUAL count): running Linux total **1258**.
- [ ] Commit:
  ```
  git add server/astrodeck/comhost/manager.py server/astrodeck/devices/backends/ascom_local.py server/astrodeck/devices/backends/__init__.py server/astrodeck/drivers.py server/astrodeck/api/app.py ui/src/lib/equipment.ts ui/src/components/settings/driversMeta.ts server/tests/test_ascom_local_backend.py server/tests/test_comhost_manager.py ui/src/lib/__tests__/ascomLocalRig.test.ts
  git commit -m "feat(ascom-local): COM-T6 backend + supervised lifecycle + UI wiring

  AscomLocalBackend reuses the existing Alpaca client against a server-spawned,
  no-orphan ComHostManager (loopback, ephemeral port). Windows-only registration
  + implicit drivers row; UI buildRigSpec ascom-local branch (implicit backend,
  no driver_id) + built-in label. Off Windows: absent, clean degrade.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### COM-T7 — integration gate + CI + docs + astrotown migration note

The "single-install works" proof and the durable wiring: one end-to-end acceptance test that drives the WHOLE `ascom-local` path (backend.open → managed comhost → Alpaca client → simulators, four roles, slew/expose/move/filter, frame round-trip) with no NINA/Remote/hardware; the Windows CI job that runs the COM gate; the non-Windows degrade assertions; docs; and the astrotown migration note.

**Files**
- Create `C:\Users\bear\astro\server\tests\test_ascom_local_gate.py` (Windows+simulator end-to-end; the acceptance gate).
- Create `C:\Users\bear\astro\server\tests\test_ascom_local_absent_off_windows.py` (non-Windows degrade assertions — runs and PASSES on the Linux baseline).
- Modify the CI workflow (find it: `.github/workflows/*.yml`) — add a Windows job step that installs `.[comhost]` + the ASCOM Platform simulators and runs the comhost + gate tests.
- Create `C:\Users\bear\astro\docs\comhost.md` (operator + developer doc: what the bundled host is, lifecycle, ports, troubleshooting "device in use").
- Modify `C:\Users\bear\astro\docs\superpowers\specs\2026-07-19-native-com-device-backend-design.md` — append a short "Delivered" note pointing at the plan + docs (optional but keeps the spec honest).

**Interfaces**
- Consumes: everything COM-T1..T6 produced (`AscomLocalBackend`, `ComHostManager`, `ascom_registry`, the comhost + handler tables, the EXISTING Alpaca client).
- Produces: the acceptance gate + CI wiring + docs. No new runtime code.

**Steps**

- [ ] Write the non-Windows degrade test `C:\Users\bear\astro\server\tests\test_ascom_local_absent_off_windows.py` (this is a PORTABLE test — it asserts the degrade and runs on the Linux baseline):
  ```python
  """COM-T7: off Windows, ascom-local degrades cleanly — absent from the backend
  registry, /api/backends, and the drivers surface; /api/discover/ascom-local is
  empty. On Windows these assertions invert (present). One test, both truths."""
  import sys

  import pytest

  from astrodeck.devices import backends as _b  # noqa: F401 (registration)
  from astrodeck.devices.backend import BACKENDS


  def test_backend_presence_matches_platform():
      if sys.platform == "win32":
          assert "ascom-local" in BACKENDS
      else:
          assert "ascom-local" not in BACKENDS


  @pytest.mark.asyncio
  async def test_drivers_surface_matches_platform(monkeypatch):
      from astrodeck import drivers as drivers_mod
      out = await drivers_mod.describe_all()
      ids = {d["id"] for d in out["drivers"]}
      if sys.platform == "win32":
          assert "ascom-local" in ids
      else:
          assert "ascom-local" not in ids  # native/Alpaca/NINA only — clean degrade
  ```
- [ ] Run it, see it pass on Linux (asserts ABSENCE): 2 passed.
- [ ] Write the acceptance gate `C:\Users\bear\astro\server\tests\test_ascom_local_gate.py` (Windows+simulator; the single-install proof):
  ```python
  """COM-T7 ACCEPTANCE GATE (spec §5): a fresh install drives its OWN bundled COM
  host end-to-end against the ASCOM SIMULATOR drivers — no ASCOM Remote, no NINA,
  no hardware. The ascom-local backend opens (spawning the managed comhost),
  connects Telescope+Camera+Focuser+FilterWheel through the EXISTING Alpaca
  client, and slews/exposes/moves/changes-filter with a frame round-trip.
  Windows + simulators only; skips cleanly on the Linux CI baseline."""
  import sys

  import pytest

  from astrodeck.comhost import manager as mgr_mod
  from astrodeck.devices import ascom_registry
  from astrodeck.devices.backend import ConnSpec


  def _sim(dev_type, progid):
      if sys.platform != "win32":
          return False
      return any(d.dev_type == dev_type and d.progid == progid
                 for d in ascom_registry.enumerate())


  _NEED = {
      "telescope": "ASCOM.Simulator.Telescope",
      "camera": "ASCOM.Simulator.Camera",
      "focuser": "ASCOM.Simulator.Focuser",
      "filterwheel": "ASCOM.Simulator.FilterWheel",
  }
  pytestmark = pytest.mark.skipif(
      not all(_sim(t, p) for t, p in _NEED.items()),
      reason="ASCOM Simulators (Telescope/Camera/Focuser/FilterWheel) not registered")


  def _num(dev_type, progid):
      for d in ascom_registry.enumerate():
          if d.dev_type == dev_type and d.progid == progid:
              return d.dev_num
      raise AssertionError("simulator vanished")


  @pytest.fixture()
  def clean_manager():
      mgr_mod.reset_manager_for_tests()
      yield
      mgr_mod.get_manager().stop()
      mgr_mod.reset_manager_for_tests()


  @pytest.mark.asyncio
  async def test_single_install_four_roles_end_to_end(clean_manager):
      from astrodeck.devices.backends.ascom_local import AscomLocalBackend
      backend = AscomLocalBackend()
      sess = await backend.open(ConnSpec(backend="ascom-local"))  # spawns comhost
      try:
          tel = await sess.get_device("telescope", ConnSpec(
              backend="ascom-local", dev_type="telescope",
              dev_num=_num("telescope", _NEED["telescope"]), role="telescope"))
          cam = await sess.get_device("camera", ConnSpec(
              backend="ascom-local", dev_type="camera",
              dev_num=_num("camera", _NEED["camera"]), role="camera"))
          foc = await sess.get_device("focuser", ConnSpec(
              backend="ascom-local", dev_type="focuser",
              dev_num=_num("focuser", _NEED["focuser"]), role="focuser"))
          fw = await sess.get_device("filterwheel", ConnSpec(
              backend="ascom-local", dev_type="filterwheel",
              dev_num=_num("filterwheel", _NEED["filterwheel"]), role="filterwheel"))

          await tel.unpark()
          await tel.set_tracking(True)
          await tel.slew(4.0, 30.0)
          ra, dec = await tel.get_position()
          assert abs(ra - 4.0) < 0.2 and abs(dec - 30.0) < 1.0

          frame = await cam.expose(0.05, 0, 0, binning=1)
          assert frame.data.ndim == 2 and frame.data.size > 0

          start = await foc.get_position()
          await foc.move_to(min(start + 250, foc.max_position))
          assert await foc.get_position() != start

          await fw.set_position(1)
          assert await fw.get_position() == 1
      finally:
          await sess.close()
  ```
- [ ] Run the gate. On Linux it SKIPS (1 skipped); on the Windows CI/dev box with the simulators it must PASS (1 passed): `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_ascom_local_gate.py -q`.
- [ ] Add the Windows CI job step. In the CI workflow (`.github/workflows/*.yml` — match the existing Windows job that runs the server suite; search for `runs-on: windows` or the `selftest` marker job), add steps that (a) install the ASCOM Platform (which registers the simulators — e.g. via `choco install ascom-platform` if Chocolatey is available on the runner, else document the runner prerequisite), (b) `pip install -e server/.[dev,comhost]`, (c) run `pytest -q server/tests/test_comhost_simulators.py server/tests/test_ascom_local_gate.py`. Mirror the existing job's Python/venv setup exactly.
  > Implementer note: if the CI Windows runner CANNOT install the ASCOM Platform (no admin / no Chocolatey), the gate tests SKIP there too — that is acceptable degradation, but then the gate is proven only on the user's dev box. Wire the step to install the Platform where possible and let the skip stand where not; the portable unit tests (mapping/harness/contract) still run on every job. Document which job actually exercises the simulators.
- [ ] Write `C:\Users\bear\astro\docs\comhost.md`: what the bundled COM host is (one-install COM→Alpaca bridge, replaces ASCOM Remote), the lifecycle (server-spawned, loopback ephemeral port, no-orphan pidfile at the portfile path, auto-restart), how discovery works (registry enumeration, deterministic dev_nums), the "device in use" single-client reality (COM connect fails with a clear surfaced error, spec §4), and the ASCOM Platform prerequisite (the user installs it; AstroDeck does not bundle vendor drivers). Include the astrotown migration note:
  > **astrotown migration (spec §7).** `ascom-local` replaces the manually-installed ASCOM Remote at `127.0.0.1:11111`: the same nine devices, now served by the bundled host. Keep the ASCOM Remote install until the native path is validated on the real rig, then uninstall it. Migration is re-assigning each role from the Alpaca driver (pointing at :11111) to `ascom-local` on the Equipment tab — the ProgID map is already known (memory `astrodeck-project`). No profile addressing to retype: pick the ASCOM (local) device per role and Connect Rig.
- [ ] (Optional) Append a one-line "Delivered by `docs/superpowers/plans/2026-07-19-native-com-device-backend.md`; operator doc `docs/comhost.md`" note to the spec so it stays honest.
- [ ] Full suite (delta +2 cross-platform degrade tests; the gate skips on Linux, runs on Windows): running Linux total **1260**; on Windows-with-sims the gate + the T3/T4/T5 simulator gates additionally pass.
- [ ] Commit:
  ```
  git add server/tests/test_ascom_local_gate.py server/tests/test_ascom_local_absent_off_windows.py .github/workflows docs/comhost.md docs/superpowers/specs/2026-07-19-native-com-device-backend-design.md
  git commit -m "test(ascom-local): COM-T7 acceptance gate + CI + docs + migration note

  End-to-end single-install proof: ascom-local opens its managed comhost and
  drives Telescope+Camera+Focuser+FilterWheel through the existing Alpaca client
  against the ASCOM Simulators (no NINA/Remote/hardware), plus the off-Windows
  degrade assertions, the Windows CI job, docs/comhost.md and the astrotown
  ASCOM-Remote retirement note.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

## Self-review (author, pre-commit)

- **Spec coverage.** §3.1 comhost (enumerate T1; STA/deadline/Alpaca-serve T2; loopback-ephemeral T2). §3.2 server wiring (`ascom-local` backend + Alpaca-client reuse T6; lifecycle T6, decision = server-spawned; discovery endpoint T1). §3.3 UI (always-available built-in + Scan/assign T6; non-Windows absence T6/T7). §3.4 device coverage (Telescope+Camera T3; Focuser/FilterWheel/Rotator/Switch/SafetyMonitor T4; CoverCalibrator/Dome/ObservingConditions host-side T5). §4 error handling (connect-failure → RoleResult via existing honest surfaces; wedged COM → per-call deadline T2; crash → ComHostManager restart + no-orphan T6; device-in-use → COM error surfaced, doc T7). §5 gate (simulators, no-NINA end-to-end T7; unit mocks T1-T6; non-Windows skip+degrade T7). §6 phasing (T1-T7 one-to-one). §7 migration (doc note T7).
- **Reuse-existing-client honored.** No new client. `AscomLocalSession` subclasses the existing `NativeSession`; the sidecar conforms to `AlpacaConnection`'s exact calls (`_unwrap`, txn params, `configureddevices`, `imagearray` JSON fallback). Cross-checked every handler method name against the Alpaca method strings in `devices/alpaca.py`.
- **Placeholder scan.** None. Every step shows real code/tests. The one transitional judgment (T3/T4 "state your ACTUAL observed count" for the mapping-test running totals) is an instruction to the implementer to report truth, not a code placeholder.
- **Type/signature consistency.** `AscomDriver` fields, `ComDevice.submit`, `DEVICE_API[type][verb][method] = fn(obj, params)`, `ComHost.handle` return, `ComHostManager.ensure()->int`, `AscomLocalSession.get_device` all match across the tasks that consume them.

## Known deferrals (honest scope)

- **Profile round-trip for ascom-local** rides localStorage stickiness (like sim), not the driver_id-keyed profile store — implicit backends need a `backend` field on `ProfileDevice` to persist, out of scope here (matches how sim is handled today).
- **ImageBytes fast path** — the comhost serves JSON `imagearray` (correct, client-compatible via the JSON fallback). Binary ImageBytes is an optional throughput optimization, not required for the gate.
- **CoverCalibrator/Dome/ObservingConditions ABCs + roles** — host-side only this wave; the consuming ABC/role lands with the flats/dome/weather sub-projects.
