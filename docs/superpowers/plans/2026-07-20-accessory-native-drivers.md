# Accessory Native Drivers Implementation Plan (sub-project C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Native drivers for the ZWO CAA (rotator) + EAF (focuser) via a bundled, ctypes-bound ZWO SDK, and the Wanderer Snowflake filter wheel via its ASCII serial stream — registered through entry points on the driver framework.

**Architecture:** One `zwo_sdk.py` loader/bindings module (export-verified vendored DLLs, all calls via `to_thread` under a lock) feeding a hostless `zwo-usb` backend whose single session serves `CaaRotator` + `EafFocuser`; a separate `wanderer-snowflake` backend built on a reader-first streaming link (DTR-low open, banner parser, pause-resume move completion). The framework gains the `transport="local"` branch.

**Tech Stack:** Python 3.11+, ctypes (stdlib), pyserial (already a dep), pytest. Protocol/API truth: `docs/hardware/rotator-focuser-filterwheel-native.md`; exact C signatures from `CAA_API.h`/`EAF_focuser.h` (session scratchpad, from indi-3rdparty `libasi`); wheel behavior hardware-verified 2026-07-20.

## Global Constraints

- **No unprompted hardware motion:** connect flows only READ (the wheel does NOT auto-calibrate; the CAA/EAF connect performs no moves). Halt-on-any-abnormal-exit for every waiting move (B's discipline). Rotator moves respect the base `MOVE_TIMEOUT_S=180`; focuser 120 s; wheel 40 s.
- **Never call `CAACurDegree`** (SDK sync) — sync lives client-side in the `Rotator` base (base.py:259 contract). Tests assert it.
- **Degrade honestly, never crash:** absent DLL → backend registers, `open` raises clear `DeviceError`; no attached unit → per-role `DeviceError` (red LED); wrong device on a serial port → identity refusal.
- **Only export-verified DLLs are committed** (an ASCOM wrapper must be rejected); vendor dir carries ZWO's MIT license verbatim + provenance README.
- All SDK calls via `asyncio.to_thread` under a per-session `asyncio.Lock` (SDKs not documented thread-safe).
- Tests hardware-free (fake SDK/stream doubles); DLL export test skips when the vendor dir is empty (CI-safe).
- Tests from `server/`: `python -m pytest tests/<file> -v -n0`; full suite at the end. Commit per task with the session trailer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

## File Structure

**Created**
- `server/astrodeck/vendor/zwo/{EAF_focuser.dll, CAA DLL, LICENSE.txt, README.md}` — verified binaries + legal basis.
- `server/astrodeck/devices/zwo_sdk.py` — `_find_dll`, `_verify_exports`, `ZwoSdkError`, `EafSdk`, `CaaSdk` (ctypes), factory seams `make_eaf`/`make_caa`.
- `server/astrodeck/devices/backends/zwo_usb.py` — `EafFocuser(Focuser)`, `CaaRotator(Rotator)`, `ZwoUsbSession`, `ZwoUsbBackend`, `register_all`.
- `server/astrodeck/devices/backends/wanderer_snowflake.py` — `Banner`, `SnowflakeLink`, `SnowflakeWheel(FilterWheel)`, `SnowflakeSession`, `SnowflakeBackend`, `register_all`.
- `server/tests/test_zwo_usb.py`, `server/tests/test_snowflake.py`.

**Modified**
- `server/astrodeck/config.py` — `_check_transport` + `add_driver` gain `transport="local"`.
- `server/pyproject.toml` — two new entry points.

---

## Task 1: SDK acquisition + verification + vendor dir + loader/bindings

**Files:** Create `vendor/zwo/*`, `devices/zwo_sdk.py`; Test `tests/test_zwo_usb.py` (loader section).

**Steps:**
- [ ] Pull candidates from astrotown (scp, IdentitiesOnly key): `ASIStudio\EAF_focuser.dll`, `ASIStudio\CAA_SRC.dll`, `Common Files\ASCOM\ZWO\CAA_ASCOM_x64.dll`, NINA `External\x64\ASI\EAF_focuser.dll` → scratchpad.
- [ ] Verify each with a throwaway script: `ctypes.WinDLL(path)` + `hasattr(dll, "EAFGetNum")`/`"CAAGetNum"`; also confirm 64-bit load succeeds (32-bit DLL raises OSError on load — automatic rejection). Commit ONLY verified SDK DLLs into `vendor/zwo/` with `LICENSE.txt` (verbatim `asi-license.txt` from indi-3rdparty) + `README.md` (versions per `CAAGetSDKVersion`/`EAFGetSDKVersion` if loadable, origin, update instructions). If no CAA candidate verifies: fetch ZWO's official CAA SDK 1.5.9 (developer downloads) and verify the same way before committing.
- [ ] Implement `zwo_sdk.py`:
  ```python
  class ZwoSdkError(Exception):        # carries .code and .fn
  def _find_dll(basename) -> Path|None # env ASTRODECK_ZWO_SDK_DIR > vendor dir > known installs; export-verified
  class EafSdk:                        # lazy WinDLL load; methods:
      get_num() count()->int; get_id(i)->int; open(id); close(id)
      get_property(id)->(name,max_step)      # EAF_INFO{int ID; char Name[64]; int MaxStep}
      move(id, step); stop(id); is_moving(id)->(moving,hand)
      get_position(id)->int; get_temp(id)->float
      get_firmware(id)->"a.b.c"; sdk_version()->str
  class CaaSdk:                        # same shape:
      get_num/get_id/open/close/get_property
      move_to_mechanical(id, deg); stop(id); is_moving(id)->(moving,hand)
      get_degree(id)->float; get_temp(id)->float
      get_reverse(id)->bool; set_reverse(id, bool)
      get_type(id)->str; get_firmware; sdk_version
  make_eaf = EafSdk; make_caa = CaaSdk # test seams
  ```
  Every wrapped call checks the returned `*_ERROR_CODE` and raises `ZwoSdkError(code, fn)` on non-zero; argtypes/restype declared per the headers (`float*` → `ctypes.c_float` byref; info structs as `ctypes.Structure`).
- [ ] Tests: `test_zwo_sdk_error_carries_code_fn` (pure); `test_vendored_dlls_export_api` — `pytest.mark.skipif(not vendor_dlls_present)`, loads each vendored DLL and asserts the export set (real protection on Windows dev/scope; skipped in empty-vendor CI).
- [ ] Run `-n0`, PASS → Commit `feat(devices): vendored ZWO SDK (export-verified, MIT) + ctypes bindings`.

## Task 2: `transport="local"` framework branch

**Files:** Modify `config.py`; Test `tests/test_zwo_usb.py`.

- [ ] Tests: `DriverEntry(id="zwo-usb-ab12", type="zwo-usb", transport="local")` validates with no host/port/port_path; `config_store.add_driver("zwo-usb", transport="local")` mints label `"ZWO-USB (USB)"`; network/serial branches unchanged (re-run their tests).
- [ ] Implement: `_check_transport` — `elif self.transport == "local": pass` (no addressing demands); `add_driver` — `local` branch mirrors serial's shape minus `port_path` (label default `f"{driver_type.upper()} (USB)"`).
- [ ] Run + affected suites (`test_config.py`, `test_zwo_am5.py -k add_driver`) → Commit `feat(config): transport=local for SDK-enumerated devices`.

## Task 3: EafFocuser over FakeEafSdk

**Files:** Create `backends/zwo_usb.py` (focuser first); Test `tests/test_zwo_usb.py`.

**Interfaces:** `EafFocuser(Focuser)` ctor `(sdk, dev_id, name="ZWO EAF")`; `connect()` = open + property (`max_position=MaxStep`) + firmware into describe; `get_position`; `move_to(pos)` = bounds-check → `sdk.move` → poll `is_moving` 0.5 s (timeout 120 s) with `sdk.stop` on ANY abnormal exit; `halt`; `get_temperature` (None on `ZwoSdkError`).
- [ ] FakeEafSdk: scripted positions/moving sequence/call log/raisable per-method. Tests: connect populates max/firmware; happy move polls to completion; cancel mid-move → `stop` called + Cancelled propagates; timeout → `stop` + DeviceError; stall error (`ZwoSdkError(code=E5-ish)`) surfaces named; temp None-on-error.
- [ ] Implement → PASS → Commit `feat(devices): EafFocuser (native EAF over SDK)`.

## Task 4: CaaRotator over FakeCaaSdk

**Files:** Modify `backends/zwo_usb.py`; Test `tests/test_zwo_usb.py`.

**Interfaces:** `CaaRotator(Rotator)` ctor `(sdk, dev_id, name="ZWO CAA")`; `can_reverse=True`; `connect()` = open + type/firmware (`"CAA-M54"`); `get_mechanical_position` = `get_degree`; `move_mechanical(deg)` = `move_to_mechanical` → poll `is_moving` 0.5 s under `MOVE_TIMEOUT_S` (base 180) with `stop` on ANY abnormal exit; hand-control motion reported in errors; `halt`; `is_moving`; `get/set_reverse`; STALL/OVER_LIMIT/OUT_RANGE → named DeviceErrors.
- [ ] Tests: mech move completion; **`"CAACurDegree" not in fake.calls` after connect+move+base-class `sync()`+`move_to()` sky-layer exercise** (the contract test); cancel→stop; stall surfaces "stall"; reverse round-trip; hand-control move error mentions hand controller.
- [ ] Implement → PASS → Commit `feat(devices): CaaRotator (native CAA, client-side sync only)`.

## Task 5: ZwoUsbSession/Backend + entry point + integration

**Files:** Modify `backends/zwo_usb.py`, `pyproject.toml`; Test `tests/test_zwo_usb.py`.

**Interfaces:** `ZwoUsbSession` (lazily builds sdks via `zwo_sdk.make_eaf/make_caa`; `get_device("rotator"/"focuser")` → first attached unit else `DeviceError("no CAA/EAF attached")`; other roles refused; `health` = counts; `close` disconnects both). `ZwoUsbBackend` manifest: `name="zwo-usb"`, `roles=("rotator","focuser")`, `hostless=True`, `transport="local"`, `hardware=True`, `driver_type="zwo-usb"`, `discoverable=True`; `discover()` lists attached units (guarded, [] on any error). `register_all()`. pyproject entry `zwo_usb = "astrodeck.devices.backends.zwo_usb:register_all"`.
- [ ] Tests: manifest row; no-device honesty (Fake sdks with count 0 → per-role DeviceError); `connect_profile` e2e — profile rotator+focuser on `zwo-usb` (monkeypatch make_eaf/make_caa) → both devices connected, `hardware=True` stamped, one session (hostless coalescing); entry-point discovery loads it (monkeypatched EP).
- [ ] Implement + `pip install -e .` + real `plugin_load_report()` shows both `zwo_am5` and `zwo_usb` loaded → Commit `feat(devices): zwo-usb backend (CAA+EAF) via entry point`.

## Task 6: Snowflake banner parser + streaming link

**Files:** Create `backends/wanderer_snowflake.py` (Banner + SnowflakeLink); Test `tests/test_snowflake.py`.

**Interfaces:**
```python
@dataclass Banner: model:str; fw_date:int; slot:int; letters:str; device_id:int; at:float
parse_banner(line)->Banner|None      # tolerant: None on garbage; A-split per verified decode
class SnowflakeLink:                 # DTR/RTS LOW deferred-open (no MCU reset), 19200 8N1
    async open(); async close()
    latest: Banner|None; last_line_at: float
    async send(cmd:str)              # appends '\r'; lock-guarded write
    async wait_banner(pred, timeout)->Banner  # newest banner satisfying pred
class FakeStream:                    # test double: scripted timed banner lines + sent log
```
Reader task loop: readline → parse → update latest (ignore None). Real link uses pyserial deferred-open (`Serial(); .dtr=False; .rts=False; .port=...; .open()`) in `to_thread`.
- [ ] Tests (parser + FakeStream-driven link semantics): banner decode of the live capture line (slot 1, letters `LXXXXXXX`, fw 20260124, id 0); garbage/partial-line resync (None, no crash); wait_banner predicate + timeout.
- [ ] Implement → PASS → Commit `feat(devices): Snowflake banner parser + DTR-low streaming serial link`.

## Task 7: SnowflakeWheel + backend + entry point + integration

**Files:** Modify `backends/wanderer_snowflake.py`, `pyproject.toml`; Test `tests/test_snowflake.py`.

**Interfaces:** `SnowflakeWheel(FilterWheel)` ctor `(link, name="Snowflake FW")`; `connect()` = open + first banner ≤5 s (else "no Snowflake stream on port") + model must be `WSFW508|WSFW368` + `fw_date>=20260124` (else refusal naming the found firmware) + seed `filter_names` (letter or `"Slot N"` for `X`); `get_position` = `latest.slot-1`; `set_position(slot)` = bounds 0..7 → `send(f"200{slot+1}")` → `wait_banner(newer-than-send AND slot==target, timeout=40)`; NO auto-calibrate at connect. `SnowflakeSession` (one link/wheel; close closes link), `SnowflakeBackend` (`name="wanderer-snowflake"`, `roles=("filterwheel",)`, `transport="serial"`, `hardware=True`, `driver_type="wanderer-snowflake"`, `discover()` = CH340 `1A86:7523` ports flagged `verified=False`), `register_all`, entry point `wanderer_snowflake = ...`.
- [ ] Tests: connect happy (names seeded); wrong model (`WandererRotatorLiteV2`-style line → parse None → 5 s timeout refusal, shortened via monkeypatched timeout); old firmware refusal; goto with scripted pause-resume banner sequence (0-based↔1-based asserted, `2003` on the wire); goto timeout → DeviceError; e2e `connect_profile` filterwheel row (`transport="serial"`, `port_path="COM8"`, monkeypatched link factory) with `hardware=True` stamp; entry-point + `discover()` tests.
- [ ] Implement + `pip install -e .` (all three entry points live) → Commit `feat(devices): wanderer-snowflake filter wheel backend (stream-native)`.

## Final verification

- [ ] Full suite `python -m pytest` green; whole-branch review (opus, read-only — priorities: no-unprompted-motion, halt-on-exit discipline, ctypes correctness/memory-safety, DTR handling, framework integration); fix wave if needed; merge to main; ledger + memory; runbook items appended (CAA ±5° validation, EAF ±200 steps, wheel cycle, SDK-version pin check).

## Self-Review

- Spec §1→T1, §2→T3/T4/T5, §3→T6/T7, §4→T2; testing strategy fully mapped; no-auto-calibrate + never-CAACurDegree carried as explicit tests; DLL verification is a committed-artifact gate (T1) + a skipif runtime test. Wheel/EAF/CAA timeouts match spec (40/120/180). Entry-point names consistent between tasks and pyproject. FakeStream/FakeEafSdk/FakeCaaSdk are per-file doubles mirroring B's FakeLink pattern.
