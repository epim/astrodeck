# ZWO AM5N Native Serial Mount Driver Implementation Plan (sub-project B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A native `Telescope` driver for the ZWO AM5N speaking LX200 ASCII over USB CDC serial, registered through the driver framework's entry-point path as its first real citizen.

**Architecture:** Three new modules — a pure LX200 codec (`devices/lx200.py`), an asyncio-safe pyserial transport (`devices/serial_link.py`), and the backend/session/device trio (`devices/backends/zwo_am5.py`) — mirroring the `ascom_local`/`native_backend` session template. Motion is fire-and-forget on this mount, so all completion detection is poll-based with wall-clock timeouts and `:Q#` on cancel. Two sub-project A minors are closed here (serial driver creation via `add_driver`; SimSolver real-motion defense).

**Tech Stack:** Python 3.11+, pyserial>=3.5 (new dep), pytest. Protocol source of truth: `docs/hardware/zwo-am5-lx200-protocol.md` + init formats extracted from the captured ASIMount log.

## Global Constraints

- **Site privacy:** the observatory's real lat/long must NEVER appear in code, tests, or docs — all golden vectors use fictional coordinates (e.g. `+40*00:00`, `+100*30:00`).
- **Honest parked UX:** every motion path maps the mount's `e14#` refusal to `DeviceError("mount is parked — unpark first")`. Connect does NOT auto-unpark.
- **No hangs:** every wire wait has a timeout; slew settle has a 120 s wall-clock cap; `asyncio.CancelledError` during a slew sends `:Q#` before re-raising.
- **`can_pulse_guide` stays `False`** until at-scope validation (spec §validation); the pulse-guide method is implemented but the capability flag is not flipped.
- **Wire facts (from capture):** commands `:CMD#`, replies `#`-terminated; init commands take NO space (`:SC07/19/26#`); acks are the bare byte `1` (no `#`) for `:S*#`/`:Spu#`; `e14#` = refused-in-state; moves/rates/stops are fire-and-forget (no reply); `:Gps#` → `2#` parked; `*` (0x2A) is the degree separator; `:SMGE` longitude is **W-positive** (AstroDeck stores East-positive → negate).
- **Time init scheme:** send `:SG+00:00#` + `:SH0#` + `:SC`/`:SL` = **current UTC** (offset zero ⇒ mount local == UTC ⇒ correct sidereal). Validation item: `:GS#` ≈ expected LST at scope.
- Tests from `server/`: `python -m pytest tests/<file> -v -n0` per-task; full suite `python -m pytest` at the end. Commit per task with the session trailer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

## File Structure

**Created**
- `server/astrodeck/devices/lx200.py` — pure codec: frame build, RA/Dec/geo parse+format, `:GU#` status word, ack classification. No I/O.
- `server/astrodeck/devices/serial_link.py` — `SerialLink` (pyserial via `asyncio.to_thread`, one `asyncio.Lock`, read-until-`#` / ack / fire-and-forget modes) + `LinkError`.
- `server/astrodeck/devices/backends/zwo_am5.py` — `ZwoAm5Telescope(Telescope)`, `ZwoAm5Session`, `ZwoAm5Backend`, `register_all()`.
- `server/tests/test_lx200.py` — codec golden tests.
- `server/tests/test_zwo_am5.py` — driver over `FakeLink`, backend/framework integration.

**Modified**
- `server/pyproject.toml` — `pyserial>=3.5` dep + `[project.entry-points."astrodeck.backends"]`.
- `server/astrodeck/config.py` — `add_driver` transport-aware (A-minor #1).
- `server/astrodeck/api/app.py` — `DriverCreateBody` gains `transport`/`port_path`; route passes through.
- `server/astrodeck/devices/orchestrator.py` + `server/astrodeck/solve/__init__.py` + `server/astrodeck/solve/simsolver.py` — real-motion defense (A-minor #2).

---

## Task 1: LX200 codec

**Files:** Create `server/astrodeck/devices/lx200.py`, `server/tests/test_lx200.py`.

**Interfaces (Produces):**
```python
build(cmd: str) -> bytes                     # "GR" -> b":GR#"
parse_ra(s: str) -> float                    # "10:13:56#"/"10:13:56" -> hours
parse_dec(s: str) -> float                   # "+90*00:00#" -> degrees (also lat)
format_ra(hours: float) -> str               # -> "HH:MM:SS"
format_dec(deg: float) -> str                # -> "sDD*MM:SS"
format_lon_wpos(lon_east_deg: float) -> str  # -> "sDDD*MM:SS", W-positive (negated)
smge(lat_deg: float, lon_east_deg: float) -> str  # -> "SMGE{lat}&{lon}"
utc_init_cmds(now_utc: datetime) -> list[str]     # ["SG+00:00","SH0","SCMM/DD/YY","SLHH:MM:SS"]
parse_status(word: str) -> Lx200Status       # ":GU#" word -> flags dataclass
ACK_OK = "1"; REFUSED = "e14"
class Lx200Status: raw: str; tracking: bool; parked: bool  # 'n'=not tracking; park via 'P' flag/:Gps# fallback
```
Parsing is tolerant of a trailing `#`. `parse_status` decodes conservatively: `tracking = not raw.startswith("n")`, `parked = "P" in flag-prefix` — anything uncertain stays a raw-string passthrough (the driver keys parked on `:Gps#`, tracking on `:GAT#`, using `:GU#` only as the cheap poll).

- [ ] **Step 1:** Write `tests/test_lx200.py` — golden vectors from the capture (fictional geo):
```python
from datetime import datetime, timezone
from astrodeck.devices import lx200


def test_build_and_ack_constants():
    assert lx200.build("GR") == b":GR#"
    assert lx200.build("Spu") == b":Spu#"
    assert lx200.ACK_OK == "1" and lx200.REFUSED == "e14"


def test_parse_ra_dec_capture_vectors():
    assert abs(lx200.parse_ra("10:13:56#") - (10 + 13 / 60 + 56 / 3600)) < 1e-9
    assert lx200.parse_dec("+90*00:00#") == 90.0
    assert abs(lx200.parse_dec("-05*30:15") - -(5 + 30 / 60 + 15 / 3600)) < 1e-9


def test_format_roundtrip():
    assert lx200.format_ra(10 + 13 / 60 + 56 / 3600) == "10:13:56"
    assert lx200.format_dec(-5.5041666667) == "-05*30:15"
    assert abs(lx200.parse_dec(lx200.format_dec(37.1308)) - 37.1308) < 1 / 3600


def test_smge_uses_w_positive_longitude():
    # East-positive -100.5083 (i.e. 100°30:30 W) -> W-positive +100*30:30
    s = lx200.smge(40.0, -100.5083333)
    assert s == "SMGE+40*00:00&+100*30:30"


def test_utc_init_cmds_shape():
    t = datetime(2026, 7, 19, 22, 14, 58, tzinfo=timezone.utc)
    assert lx200.utc_init_cmds(t) == ["SG+00:00", "SH0", "SC07/19/26", "SL22:14:58"]


def test_parse_status_capture_words():
    st = lx200.parse_status("nGM000000005#")
    assert st.tracking is False and st.raw == "nGM000000005"
    assert lx200.parse_status("NGM000000000#").tracking is True
```
- [ ] **Step 2:** Run `python -m pytest tests/test_lx200.py -v -n0` — expect FAIL (module absent).
- [ ] **Step 3:** Implement `lx200.py`: sexagesimal helpers (`_sex(value) -> (sign, d, m, s)` with rounding carry), the functions above (~90 lines, pure).
- [ ] **Step 4:** Re-run — expect PASS. **Step 5:** Commit `feat(devices): LX200 codec for the AM5N native driver`.

---

## Task 2: SerialLink + pyserial

**Files:** Create `server/astrodeck/devices/serial_link.py`; modify `server/pyproject.toml`; tests in `server/tests/test_zwo_am5.py` (FakeLink lives here for reuse).

**Interfaces (Produces):**
```python
class LinkError(Exception): ...
class SerialLink:
    def __init__(self, port_path: str, baud: int = 9600): ...
    async def open(self) -> None                       # raises LinkError
    async def request(self, cmd: str, *, reply: str = "hash",
                      timeout: float = 1.5) -> str | None
    # reply="hash" -> read until '#' (returns without '#')
    # reply="ack"  -> read exactly 1 byte ('1'/'0'; 'e14#' style refusals read to '#')
    # reply="none" -> fire-and-forget, returns None
    async def close(self) -> None
class FakeLink:  # test double, same surface: scripted {cmd: reply} + call log
```
All pyserial calls run in `asyncio.to_thread`; ONE `asyncio.Lock` serializes request/response pairs. `reply="ack"` reads one byte; if that byte is `e`, keep reading to `#` (refusal form `e14#`) and return e.g. `"e14"`.

- [ ] **Step 1:** Add `"pyserial>=3.5",` to `[project] dependencies` in `server/pyproject.toml`; run `python -m pip install pyserial>=3.5` in the venv; verify `python -c "import serial; print(serial.__version__)"`.
- [ ] **Step 2:** In `tests/test_zwo_am5.py`, write `FakeLink` (dict of cmd→reply or list for sequences, `sent: list[str]` log, optional per-cmd delay) + a test that `SerialLink` is importable and `FakeLink` request/logging semantics hold (unit-testing the double so driver tests can trust it).
- [ ] **Step 3:** Implement `serial_link.py` (~70 lines): `open()` = `serial.Serial(port, baud, timeout=...)` in a thread; `request()` under the lock: write `lx200.build(cmd)`, then per `reply` mode read loop with deadline; `close()` best-effort.
- [ ] **Step 4:** Run the file's tests `-n0` — PASS. **Step 5:** Commit `feat(devices): asyncio-safe serial link (pyserial) + test double`.

---

## Task 3: ZwoAm5Telescope — connect, state, park honesty

**Files:** Create `server/astrodeck/devices/backends/zwo_am5.py` (telescope class first); tests append to `test_zwo_am5.py`.

**Interfaces (Produces):**
```python
class ZwoAm5Telescope(Telescope):
    backend = "zwo-am5"; hardware = True
    def __init__(self, link, name="ZWO AM5"): ...
    # implements every Telescope ABC method over the link (motion in Task 4)
async def _connect_flow(link) -> dict   # identity+firmware; raises DeviceError on non-AM5
```
Connect flow (in `connect()`): `GVP` must contain `"AM5"` → `GV` firmware stored → `utc_init_cmds(now_utc)` each expecting ack `1` → `smge(site.lat, site.lon)` ack `1` (site read lazily from `config_store.cfg().site`) → initial `Gps`/`GU` poll. `is_parked()` = `:Gps#` reply startswith `"2"`. `unpark()` = `Spu` ack `1`. `park()` = `hP` ack; on `e14` raise `DeviceError` naming it unverified-when-parked. `get_position()` = `GR`+`GD` parsed. `get_tracking()` = `GAT` reply `"1"`. `_refused(reply)` helper: if reply == `e14` → `DeviceError("mount is parked — unpark first (AM5 e14)")`.

- [ ] **Step 1:** Tests (FakeLink-scripted): successful connect (asserts exact init command sequence in `link.sent`, no `Spu` in it); identity mismatch (`GVP`→`Prototype#`) raises `DeviceError` and closes; `is_parked` true/false on `Gps` `2#`/`0#`; `unpark` sends `Spu`; `get_position` parses capture vectors; parked-refusal mapping (`Te`→`e14`) raises the honest message.
- [ ] **Step 2:** Run — FAIL (class absent). **Step 3:** Implement (~150 lines). **Step 4:** Run — PASS. **Step 5:** Commit `feat(devices): ZwoAm5Telescope connect/state/park over LX200`.

---

## Task 4: Motion — slew settle, sync, move_axis, stop, tracking, pulse-guide

**Files:** Modify `zwo_am5.py`; tests append.

**Wire mapping:** `slew(ra,dec)`: `Sr{format_ra}` ack, `Sd{format_dec}` ack, `MS` reply `"0"`=accepted (`e14`→parked error) → settle loop: every 0.5 s read `GR`/`GD`+`GAT`; settled when coord delta < 0.05° across two consecutive polls; 120 s cap → `Q` + `DeviceError`; on `CancelledError` → `Q` + re-raise. `sync`: `Sr`/`Sd` acks then `CM` (any non-refused reply = ok; `e14` → parked error). `set_tracking(True/False)`: `Te`/`Td` ack-or-silent (accept `1` or timeout-none; `e14` → error). `move_axis(axis, rate_deg_s)`: map |rate| to R-index via the table `[(0.0,None),(0.25,'R1'),(1.0,'R3'),(4.0,'R5'),(16.0,'R7'),(inf,'R9')]`, then `Mn/Ms/Me/Mw` fire-and-forget (`ra`+ → `Me`, `ra`− → `Mw`, `dec`+ → `Mn`, `dec`− → `Ms`); rate 0 → `Qe`/`Qw`/`Qn`/`Qs` both directions of that axis. `stop()`: `Q` then re-assert prior tracking state. `is_slewing()`: coord-delta heuristic vs last poll + `GAT` at-target flag. `pulse_guide(dir, ms)`: `Mg{n|s|e|w}{ms:04d}` fire-and-forget; class attr `can_pulse_guide = False` (flip at-scope). `guide_rates()`: `GdG` parsed via `parse_dec` → (v, v). `pier_side()`: `Gm` → `E`/`W` → `PierSide`.

- [ ] **Step 1:** Tests: slew happy path (scripted convergence: `GR` returns target-approaching sequence; assert settle returns + exact `Sr`/`Sd`/`MS` in log); slew-cancel sends `Q` (start slew with never-converging coords, cancel the task, assert `Q` logged + `CancelledError` propagates); slew timeout raises after cap (monkeypatch the cap to 1 s); parked `MS`→`e14` honest error; move_axis rate map (0.5°/s → `R3`+`Me` for `('ra', +)`), rate 0 → `Qe`+`Qw`; stop reasserts tracking; pulse_guide format `Mgn0500`; pier side parse.
- [ ] **Step 2:** FAIL → **Step 3:** implement (~120 lines) → **Step 4:** PASS → **Step 5:** Commit `feat(devices): AM5N motion — cancel-safe slew settle, moves, tracking, pulse-guide`.

---

## Task 5: Backend + Session + entry-point registration + framework integration

**Files:** Modify `zwo_am5.py` (add Session/Backend/register_all), `server/pyproject.toml`; tests append.

**Produces:**
```python
class ZwoAm5Session:   # name="zwo-am5"; owns ONE SerialLink
    async def get_device(self, role, conn) -> ZwoAm5Telescope   # role=="telescope" else DeviceError
    def native_guider/guide_camera/native_solver -> None
    async def health(self) -> dict | None; async def close(self) -> None  # :Q# then close link
class ZwoAm5Backend:
    name="zwo-am5"; label="ZWO AM5 (native serial)"; roles=("telescope",)
    discoverable=True; hostless=False; transport="serial"; hardware=True
    driver_type="zwo-am5"; version=<app>; min_app_version="0"; author=""
    async def open(self, conn) -> ZwoAm5Session   # requires conn.port_path else DeviceError
    async def discover(self) -> list[dict]        # serial.tools.list_ports: vid==0x03C3&pid==0x4001
def register_all() -> None                        # register(ZwoAm5Backend())
```
pyproject:
```toml
[project.entry-points."astrodeck.backends"]
zwo_am5 = "astrodeck.devices.backends.zwo_am5:register_all"
```
pyserial import is guarded at module top (`serial = None` on ImportError; `open()` raises a clear DeviceError) so the module always imports.

- [ ] **Step 1:** Tests: `register_all()` puts `zwo-am5` in `BACKENDS` with the manifest (transport serial, hardware True, driver_type zwo-am5); discovery path — monkeypatch `_discovery.md.entry_points` with a fake EP whose loader calls `register_all`, assert loaded report; end-to-end `connect_profile`: profile `ProfileDevice(role="telescope", backend="zwo-am5", transport="serial", port_path="COM9")` → monkeypatch the session's link factory to `FakeLink` (module-level `_make_link = SerialLink` seam; tests swap it) → rig["telescope"] connected AND `hardware is True` (the A-safety stamp end-to-end); `discover()` with monkeypatched `list_ports.comports` returns the VID:PID match.
- [ ] **Step 2:** FAIL → **Step 3:** implement (~90 lines) + pyproject entry → **Step 4:** PASS → **Step 5:** `python -m pip install -e .` (refreshes stale metadata — `pip show astrodeck` said 0.1.0) and verify real discovery: `python -c "import astrodeck.devices.backends as b; print([r for r in b.plugin_load_report()])"` shows `zwo_am5` loaded. **Step 6:** Commit `feat(devices): zwo-am5 native serial backend via entry-point discovery`.

---

## Task 6: Serial driver creation (closes A-minor #1)

**Files:** Modify `server/astrodeck/config.py` (`add_driver`), `server/astrodeck/api/app.py` (`DriverCreateBody`, `add_driver` route); tests append to `test_zwo_am5.py`.

`config_store.add_driver` gains `transport: str = "network"`, `port_path: str = ""` params. Serial branch: no host/port/`DRIVER_DEFAULT_PORTS` requirement — requires `port_path`; label default `f"{driver_type.upper()} @ {port_path}"`; mints `DriverEntry(transport="serial", port_path=...)`. Network branch unchanged EXCEPT an unknown-to-`DRIVER_DEFAULT_PORTS` type is now allowed **when an explicit port is given** (plugin network types; the API layer already gates the vocabulary against the registry). `DriverCreateBody` gains `transport: str = "network"`, `port_path: str = ""`, `host: str = ""` (default — serial needs none); route passes both through.

- [ ] **Step 1:** Tests: `add_driver("zwo-am5", transport="serial", port_path="COM9")` mints a valid serial entry (id prefix `zwo-am5-`); serial without port_path raises ValueError; API POST `/api/config/drivers` `{type:"zwo-am5", transport:"serial", port_path:"COM9"}` → 200 with the registered backend present, and a bogus type still 422s; legacy network create (`{type:"nina", host:"h"}`) unchanged.
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS + re-run `tests/test_drivers_api.py tests/test_config.py -n0` → **Step 5:** Commit `feat(config): transport-aware add_driver — serial driver creation (closes A-minor 1)`.

---

## Task 7: SimSolver real-motion defense (closes A-minor #2)

**Files:** Modify `server/astrodeck/devices/orchestrator.py` (`_pick_solver`), `server/astrodeck/solve/__init__.py` (`get_solver`), `server/astrodeck/solve/simsolver.py`; tests append.

`connect_profile` computes `real_motion = any(getattr(rig.get(r), "hardware", False) for r in ("telescope", "focuser", "rotator"))` after rig assembly and passes it to `_pick_solver(...)` → `get_solver(None, mode=..., real_motion=real_motion)` → `SimSolver(mode=..., real_motion=...)`. `SimSolver.solve` refuses when `self.mode in _REAL_MODES` **or `self.real_motion`** (message names the native rig case). Defaults (`real_motion=False`) keep every existing construction/test working.

- [ ] **Step 1:** Tests: a rig with a `hardware=True` telescope (the Task 5 fake zwo-am5 rig) + sim camera → the picked solver REFUSES to solve (`SolveResult.ok is False`, message mentions refusing); a pure sim rig still fake-solves.
- [ ] **Step 2:** FAIL → **Step 3:** implement (three small diffs) → **Step 4:** PASS + `tests -k "solve or solver" -n0` → **Step 5:** Commit `fix(solve): SimSolver refuses on any real-motion rig (device hardware flag) — closes A-minor 2`.

---

## Final verification

- [ ] Full suite `python -m pytest` — all green.
- [ ] Update `docs/hardware/zwo-am5-lx200-protocol.md`: note the no-space init format + the driver's UTC-init scheme; cross-link the driver module.
- [ ] Ledger + memory updates; at-scope validation runbook remains open (park/`:CM#`/tracking/pulse-guide verification → then flip `can_pulse_guide`).

## Self-Review

- **Spec coverage:** codec (T1), transport (T2), connect/park honesty (T3), motion/settle/cancel (T4), backend+entry-point+discovery+integration (T5), A-minor #1 (T6), A-minor #2 (T7), validation items preserved as runbook. ✓
- **No placeholders:** each task carries interfaces + concrete wire mappings + named test cases; core formulas (rate table, settle criteria, W-positive negation, UTC scheme) are explicit. ✓
- **Type consistency:** `FakeLink`/`SerialLink` share the `request(cmd, reply=..., timeout=...)` surface; `register_all` name matches the pyproject entry point; `transport="serial"`+`port_path` names match sub-project A's schema. ✓
- **Privacy:** all coordinates fictional; real site values only ever read at runtime from config. ✓
