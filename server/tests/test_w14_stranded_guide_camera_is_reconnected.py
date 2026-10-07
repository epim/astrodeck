# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-89 (#16): a stranded guide camera is reconnected at the frame-boundary
gate, and an idle removed camera reads disconnected.

Two halves of the same incident (the 2026-09-12 calibration-walk strand, where
the ASI guide camera was left holding a dead handle while every flag said it
was fine):

THE GATE. ``SequenceEngine._reconnect_gate`` healed the imaging camera, the
wheel, the guider, the focuser and the mount, and never the dedicated guide
camera -- the one device that was actually stranded. It now does, with ONE
deliberate difference (the wave-14 ruling for #16): a guide camera that cannot be
reopened never ends the night. Every other role raises ``SafetyAbort`` on
exhaustion because the run cannot shoot without it; the run CAN shoot without
a guide camera, and what guiding then does is the owner's existing
``escalation.guiding_action`` policy, so the gate warns, names that setting,
and carries on. The silent-camera rule stays imaging-camera only.

THE IDLE CAMERA. ``NativeCamera.connected`` was measured only by the hooks an
exposure drives (WP-47). A camera unplugged while nothing was exposing kept
reading connected, because the one thing that polls an idle camera -- the
sensor temperature -- returned None for EVERY failure and so said nothing. The
ZWO and Player One adapters now raise ``CameraGone`` for the SDK's own
"closed/removed" codes, and ``NativeCamera`` marks the drop and says so once,
at warning, so alerting hears it.

SDK codes, verified 2026-10-07 against the in-repo error-name tables
(``zwo_asi_sdk.ERROR_NAMES`` and ``player_one_sdk.ERROR_NAMES``, pinned by
``test_the_assumed_sdk_codes_are_the_ones_the_tables_name`` below) and against
the vendored ASICamera2.dll's own immediate ``4 CAMERA_CLOSED`` cited at
``AsiSdk.ALIGN_H_BINNED``. No ASICamera2.h / PlayerOneCamera.h is vendored
(only EAF_focuser.h), so there is no header to check against; the daytime
rig check (backlog ruling D-10, owner-approved 2026-09-30) is what confirms the
live codes.

Named mutants, each run from a byte backup inside the worktree and restored
byte-identically (sha256 compared); the first failing assertion is quoted.

Gate (``sequence/engine.py``):

* m1 -- ``needed.append("guide_camera")`` replaced by ``pass``. Four cases fail,
  the first being ``test_a_dropped_guide_camera_is_reconnected_at_the_gate``:
  ``AssertionError: the gate must reconnect a guide camera that reads
  disconnected``.
* m10 -- the soft-fail branch disabled (``if role == "guide_camera"`` in the
  exhaustion clause made ``if False``). Three cases fail, the first being
  ``test_a_guide_camera_that_will_not_reopen_does_not_end_the_night``:
  ``SafetyAbort: guide_camera dropped out and did not come back after 2
  reconnect attempts``.
* m16 -- the warning's ``escalation.guiding_action`` text replaced by prose.
  ``test_a_guide_camera_that_will_not_reopen_does_not_end_the_night`` FAILS:
  ``AssertionError: []`` (no warning names the policy key).
* m7 -- the ``role == "camera"`` guard dropped from the silent-camera test.
  ``test_the_silent_camera_rule_stays_imaging_camera_only`` FAILS:
  ``assert ['camera', 'guide_camera'] == ['camera']``.
* m6 -- the cool-off test replaced by ``False``.
  ``test_a_guide_camera_that_gave_up_is_not_retried_at_every_frame_boundary``
  FAILS: ``AssertionError: a camera that just failed every attempt must not be
  retried at the very next frame boundary``.
* m9 -- the cool-off reset in the healthy branch replaced by ``pass``.
  ``test_a_guide_camera_that_comes_back_on_its_own_clears_the_cool_off`` FAILS:
  ``AssertionError: a fresh outage after a recovery must be retried
  immediately``.
* m25 -- the soft-fail condition swapped to the IMAGING camera (``if role ==
  "camera":`` in the exhaustion clause, so the guide camera aborts and the
  imaging camera does not). Added by the independent verifier.
  ``test_a_failing_guide_camera_does_not_mask_the_imaging_camera_abort`` FAILS:
  ``Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.SafetyAbort'>``. (The
  case's ``match`` is anchored for this: an unanchored "camera dropped out"
  also matches "guide_camera dropped out".)
* m17 -- the cool-off check applied to every role, not only the guide camera.
  Added by the independent verifier. The focuser, which follows the guide
  camera in the gate's order, is skipped, and
  ``test_a_guide_camera_that_will_not_reopen_does_not_end_the_night`` FAILS:
  ``AssertionError: ['guide_camera', 'guide_camera']``.

Adapters (``zwo_asi.py``, ``player_one.py``):

* m2 -- ``zwo_asi.get_temperature``'s ``if e.code in _CAMERA_GONE_CODES`` made
  ``if False`` (back to "every failure is None").
  ``test_asi_temperature_raises_camera_gone_for_a_closed_or_removed_handle[4]``
  and ``[5]`` FAIL: ``Failed: DID NOT RAISE <class
  'astrodeck.devices.cameras.adapter.CameraGone'>``.
* m5 -- the ASI code set widened to ``{3, 4, 5}``.
  ``test_a_camera_with_no_temperature_sensor_still_reads_none[exc0]`` FAILS:
  ``CameraGone: ASI camera is gone: INVALID_CONTROL_TYPE (code 3) from
  ASIGetControlValue``.
* m11 -- the Player One code set narrowed to ``{5}``.
  ``test_player_one_temperature_raises_camera_gone_for_an_unopened_or_missing_
  device[6]`` FAILS: ``Failed: DID NOT RAISE ...CameraGone``.
* m12 -- the Player One code set widened to ``{5, 6, 14}``.
  ``test_a_player_one_that_cannot_read_a_temperature_still_reads_none[exc1]``
  FAILS: ``PlayerOneSdkError: Player One SDK POAGetConfig failed
  (CONF_CANNOT_READ, code 14)``.

``NativeCamera`` (``cameras/engine.py``):

* m3 -- the ``_mark_dropped(str(e))`` call in ``get_temperature``'s ``except
  CameraGone`` replaced by ``pass`` (the exception still propagates, the flag
  never moves). ``test_a_removed_camera_polled_while_idle_reads_disconnected_
  and_warns_once`` FAILS: ``AssertionError: an idle camera that reports itself
  removed must read disconnected``.
* m4 -- ``_mark_dropped`` logs on every call (``if was_connected`` made ``if
  True``). The same case FAILS: ``AssertionError: ten polls of one removed
  camera must write exactly one warning, got 10``.
* m8 -- ``expose``'s ``except CameraGone`` around the post-download temperature
  read made ``except ZeroDivisionError``.
  ``test_a_good_frame_is_not_lost_to_a_failed_temperature_read`` FAILS:
  ``CameraGone: ASI SDK ASIGetControlValue failed (CAMERA_REMOVED, code 5)``
  propagates out of ``expose``.
* m13 / m14 / m15 -- the ``_run`` hook site, the ``_poll`` hook site and the
  imageready-timeout site each reduced to a bare ``self.connected = False``.
  ``test_every_measured_drop_site_says_so_once[start_exposure]``, ``[image_ready]``
  and ``test_an_imageready_timeout_says_so_once`` FAIL respectively:
  ``AssertionError: []`` (the drop was measured and nobody was told).
"""
from __future__ import annotations

import numpy as np
import pytest

import astrodeck.config as config_mod
import astrodeck.events as events_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import (AppConfig, ConfigStore, EscalationConfig, SafetyConfig,
                              Site)
from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.adapter import (CameraAdapter, CameraCapabilities,
                                               CameraGone)
from astrodeck.devices.cameras.engine import NativeCamera
from astrodeck.devices.cameras.player_one import PlayerOneAdapter
from astrodeck.devices.cameras.player_one_sdk import PlayerOneSdkError
from astrodeck.devices.cameras.player_one_sdk import ERROR_NAMES as POA_ERROR_NAMES
from astrodeck.devices.cameras.zwo_asi import AsiCameraAdapter
from astrodeck.devices.cameras.zwo_asi_sdk import AsiSdkError
from astrodeck.devices.cameras.zwo_asi_sdk import ERROR_NAMES as ASI_ERROR_NAMES
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import SafetyAbort


# ===========================================================================
# the gate
# ===========================================================================


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=45.0, longitude=-116.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "RECONNECT_BACKOFF_S", 0.01)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="reconnect", guide=True, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="Darks", ra_hours=0, dec_deg=0, calibration=True,
                        autofocus_first=False,
                        steps=[ExposureStep(exposure_s=0.05, count=2,
                                            frame_type="Dark")])])
    return SequencePlan(**(defaults | overrides))


def _engine(hub, *, retries: int = 1, guiding_action: str = "warn",
            **plan_overrides) -> SequenceEngine:
    eng = SequenceEngine(hub)
    eng._cfg = AppConfig(
        escalation=EscalationConfig(reconnect_resume=True,
                                    reconnect_retries=retries,
                                    guiding_action=guiding_action),
        safety=SafetyConfig(enabled=False))
    eng.plan = _plan(**plan_overrides)
    return eng


def _record_reconnects(hub, monkeypatch, *, fail=()):
    """Replace ``reconnect_role`` with a recorder; roles in ``fail`` answer
    False (the device did not come back)."""
    tried: list[str] = []

    async def record(role):
        tried.append(role)
        return role not in fail
    monkeypatch.setattr(hub, "reconnect_role", record)
    return tried


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    """Capture (level, message, source) of every bus.log line."""
    lines: list[tuple[str, str, str]] = []

    def record(level, message, source="hub", **_kw):
        lines.append((level, message, source))
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


async def test_a_dropped_guide_camera_is_reconnected_at_the_gate(sim_hub):
    """The whole point: the camera that was actually stranded on 2026-09-12 is
    now a role the gate heals, through the real ``hub.reconnect_role`` (the
    same NativeCamera object re-opens itself, so the guider's held reference
    stays valid).

    Named mutant m1: ``needed.append("guide_camera")`` deleted from
    ``_reconnect_gate``.
    """
    gcam = sim_hub.devices["guide_camera"]
    await gcam.disconnect()
    assert gcam.connected is False, "precondition"
    eng = _engine(sim_hub)

    await eng._reconnect_gate()

    assert gcam.connected is True, (
        "the gate must reconnect a guide camera that reads disconnected")


async def test_a_guide_camera_that_will_not_reopen_does_not_end_the_night(
        sim_hub, monkeypatch):
    """A guide camera that fails every attempt is a warning, never
    a ``SafetyAbort``. The warning names the policy that decides what guiding
    does next, and the gate keeps going to the roles after it.

    Named mutant m10: the soft-fail branch disabled (the ``else`` clause
    raising for every role). FAILS: ``SafetyAbort: guide_camera dropped out and
    did not come back after 2 reconnect attempts``. Named mutant m16: the
    warning stops naming the policy key. FAILS on the ``guiding_action`` match.
    """
    tried = _record_reconnects(sim_hub, monkeypatch, fail={"guide_camera"})
    logs = _record_logs(monkeypatch)
    await sim_hub.devices["guide_camera"].disconnect()
    await sim_hub.devices["focuser"].disconnect()
    # autofocus in the plan puts the focuser AFTER the guide camera in the
    # gate's order, so reaching it proves the gate went on past the soft-fail.
    eng = _engine(sim_hub, retries=2, guiding_action="abort", autofocus_every=1)

    await eng._reconnect_gate()          # must not raise

    assert tried == ["guide_camera", "guide_camera", "focuser"], tried
    warned = [m for (lvl, m, _src) in logs
              if lvl == "warning" and "escalation.guiding_action" in m]
    assert len(warned) == 1, warned
    assert "guide_camera" in warned[0]
    assert "abort" in warned[0], "the configured policy is named, not just the key"


async def test_a_failing_guide_camera_does_not_mask_the_imaging_camera_abort(
        sim_hub, monkeypatch):
    """The soft-fail is for the guide camera ONLY: the imaging camera dropping
    out in the same gate still ends the run through ``SafetyAbort``."""
    _record_reconnects(sim_hub, monkeypatch, fail={"camera", "guide_camera"})
    await sim_hub.devices["camera"].disconnect()
    await sim_hub.devices["guide_camera"].disconnect()
    eng = _engine(sim_hub)

    # Anchored: an unanchored "camera dropped out" also matches the text of
    # "guide_camera dropped out", so a gate that soft-failed the IMAGING camera
    # and aborted on the guide camera would still have passed.
    with pytest.raises(SafetyAbort, match=r"^camera dropped out"):
        await eng._reconnect_gate()


async def test_a_plan_that_does_not_guide_leaves_the_guide_camera_alone(
        sim_hub, monkeypatch):
    """Only roles the RUN needs: a plan with ``guide=False`` never uses the
    guide camera, so a dropped one is not on its critical path."""
    tried = _record_reconnects(sim_hub, monkeypatch)
    await sim_hub.devices["guide_camera"].disconnect()
    eng = _engine(sim_hub, guide=False)

    await eng._reconnect_gate()

    assert tried == [], tried


async def test_a_rig_with_no_guide_camera_device_is_skipped(sim_hub, monkeypatch):
    """When the guider shares the imaging sensor there is no ``guide_camera``
    device at all (native_backend.py's OAG fallback), so there is nothing to
    reconnect and nothing to fail."""
    tried = _record_reconnects(sim_hub, monkeypatch)
    del sim_hub.devices["guide_camera"]
    eng = _engine(sim_hub)

    await eng._reconnect_gate()

    assert tried == [], tried


async def test_a_healthy_guide_camera_is_left_alone(sim_hub, monkeypatch):
    """Sanity control: a guide camera that reads connected is not reopened
    (that would close a handle a live guide loop is exposing through)."""
    tried = _record_reconnects(sim_hub, monkeypatch)
    assert sim_hub.devices["guide_camera"].connected is True
    eng = _engine(sim_hub)

    await eng._reconnect_gate()

    assert tried == [], tried


async def test_the_silent_camera_rule_stays_imaging_camera_only(
        sim_hub, monkeypatch):
    """The silent-camera rule ("claims connected but has produced no frame")
    is a statement about the IMAGING camera's frame clock. The guide camera
    produces no imaging frames, so it must never be judged silent by that clock.

    Named mutant m7: the ``role == "camera"`` guard dropped from the gate's
    skip condition.
    """
    tried = _record_reconnects(sim_hub, monkeypatch)
    eng = _engine(sim_hub)
    monkeypatch.setattr(eng, "_camera_is_silent", lambda: True)

    await eng._reconnect_gate()

    assert tried == ["camera"], tried


async def test_a_guide_camera_that_gave_up_is_not_retried_at_every_frame_boundary(
        sim_hub, monkeypatch):
    """The cost of a soft-fail that retries: a guide camera that is really gone
    would be reopened at EVERY frame boundary, and each attempt publishes a
    ``reconnect`` event that alerting never dedupes (``_NEVER_DEDUPE``), so the
    owner would be paged once per frame for a camera that is not coming back.
    After it gives up, the gate leaves it alone for a cool-off.

    Named mutant m6: the cool-off check dropped.
    """
    tried = _record_reconnects(sim_hub, monkeypatch, fail={"guide_camera"})
    await sim_hub.devices["guide_camera"].disconnect()
    eng = _engine(sim_hub)

    await eng._reconnect_gate()
    assert tried == ["guide_camera"], "precondition: the first boundary tries"

    await eng._reconnect_gate()
    assert tried == ["guide_camera"], (
        "a camera that just failed every attempt must not be retried at the "
        "very next frame boundary")

    eng._guide_camera_retry_at = 0.0      # the cool-off has run out
    await eng._reconnect_gate()
    assert tried == ["guide_camera", "guide_camera"], (
        "once the cool-off has passed the gate must try again, or a replugged "
        "camera is never picked up")


async def test_a_guide_camera_that_comes_back_on_its_own_clears_the_cool_off(
        sim_hub, monkeypatch):
    """A camera that was given up on, then comes back by other means (a
    profile activate, a replug the gate did not do), is healthy again; if it
    drops a second time the gate must try at once, not sit out a cool-off left
    over from the first outage.

    Named mutant m9: the cool-off reset in the gate's healthy branch deleted.
    """
    tried = _record_reconnects(sim_hub, monkeypatch, fail={"guide_camera"})
    gcam = sim_hub.devices["guide_camera"]
    await gcam.disconnect()
    eng = _engine(sim_hub)

    await eng._reconnect_gate()                   # gives up; cool-off starts
    assert tried == ["guide_camera"], "precondition"

    await gcam.connect()                          # back, by other means
    await eng._reconnect_gate()                   # healthy: leave it alone
    assert tried == ["guide_camera"]

    await gcam.disconnect()                       # and gone again
    await eng._reconnect_gate()
    assert tried == ["guide_camera", "guide_camera"], (
        "a fresh outage after a recovery must be retried immediately")


# ===========================================================================
# the adapters
# ===========================================================================


class _AsiSdkRaising:
    """Just enough ASI SDK for ``get_temperature``: ``get_control`` raises."""

    def __init__(self, exc: Exception):
        self.exc = exc

    def get_control(self, cam_id, ctrl):
        raise self.exc


class _PoaSdkRaising:
    """Just enough Player One SDK for ``get_temperature``."""

    def __init__(self, exc: Exception):
        self.exc = exc

    def get_config(self, cam_id, config):
        raise self.exc


def test_the_assumed_sdk_codes_are_the_ones_the_tables_name():
    """The idle-camera probe relies on four numbers. Pin each to the name the in-repo
    error table gives it, so a table edit cannot silently re-aim the check."""
    assert ASI_ERROR_NAMES[4] == "CAMERA_CLOSED"
    assert ASI_ERROR_NAMES[5] == "CAMERA_REMOVED"
    assert ASI_ERROR_NAMES[3] == "INVALID_CONTROL_TYPE"
    assert POA_ERROR_NAMES[5] == "NOT_OPENED"
    assert POA_ERROR_NAMES[6] == "DEVICE_NOT_FOUND"


@pytest.mark.parametrize("code", [4, 5])
def test_asi_temperature_raises_camera_gone_for_a_closed_or_removed_handle(code):
    """Named mutant m2: ``except Exception: return None`` restored."""
    a = AsiCameraAdapter(sdk=_AsiSdkRaising(AsiSdkError(code, "ASIGetControlValue")))

    with pytest.raises(CameraGone) as info:
        a.get_temperature()

    # the line is the camera's report, so it carries the SDK's own code name
    assert ASI_ERROR_NAMES[code] in str(info.value)


@pytest.mark.parametrize("exc", [
    AsiSdkError(3, "ASIGetControlValue"),      # INVALID_CONTROL_TYPE: no sensor
    AsiSdkError(11, "ASIGetControlValue"),     # TIMEOUT
    AsiSdkError(16, "ASIGetControlValue"),     # GENERAL_ERROR
    RuntimeError("not an SDK error at all"),
])
def test_a_camera_with_no_temperature_sensor_still_reads_none(exc):
    """A camera with no temperature control answers INVALID_CONTROL_TYPE (3);
    that is a missing sensor, not a missing camera. Every code outside the
    closed/removed pair, and every non-SDK exception, keeps returning None.

    Named mutant m5: the ASI code set widened to include 3.
    """
    a = AsiCameraAdapter(sdk=_AsiSdkRaising(exc))

    assert a.get_temperature() is None


def test_asi_temperature_still_reads_a_healthy_sensor():
    class Healthy:
        def get_control(self, cam_id, ctrl):
            return 215
    assert AsiCameraAdapter(sdk=Healthy()).get_temperature() == 21.5


@pytest.mark.parametrize("code", [5, 6])
def test_player_one_temperature_raises_camera_gone_for_an_unopened_or_missing_device(
        code):
    a = PlayerOneAdapter(sdk=_PoaSdkRaising(PlayerOneSdkError(code, "POAGetConfig")))

    with pytest.raises(CameraGone) as info:
        a.get_temperature()

    assert POA_ERROR_NAMES[code] in str(info.value)


@pytest.mark.parametrize("exc", [
    PlayerOneSdkError(3, "POAGetConfig"),       # INVALID_CONFIG
    PlayerOneSdkError(14, "POAGetConfig"),      # CONF_CANNOT_READ
    PlayerOneSdkError(9, "POAGetConfig"),       # TIMEOUT
    RuntimeError("not an SDK error at all"),
])
def test_a_player_one_that_cannot_read_a_temperature_still_reads_none(exc):
    a = PlayerOneAdapter(sdk=_PoaSdkRaising(exc))

    assert a.get_temperature() is None


def test_camera_gone_is_a_device_error():
    """Callers that already guard a camera call with ``except DeviceError``
    (and the ones that guard with ``except Exception``) keep working."""
    assert issubclass(CameraGone, DeviceError)


# ===========================================================================
# NativeCamera
# ===========================================================================


class _Adapter(CameraAdapter):
    """A minimal native adapter whose temperature read and one other named
    call can be made to fail."""

    def __init__(self, *, gone_on_temperature: bool = False,
                 fail_on: str | None = None, never_ready: bool = False,
                 read_modes: tuple[str, ...] = ()):
        self.gone_on_temperature = gone_on_temperature
        self.fail_on = fail_on
        self.never_ready = never_ready
        self._read_modes = read_modes

    def _mark(self, name: str) -> None:
        if name == self.fail_on:
            raise DeviceError(f"{name} failed")

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            sensor_width=4, sensor_height=2, pixel_size_um=3.76, bit_depth=16,
            bayer_pattern=None, gain_range=(0, 600), offset_range=(0, 255),
            bin_modes=(1,), roi_supported=True, has_cooler=False,
            has_dew_heater=False, max_adu=65535, read_modes=self._read_modes)

    def open(self, index: int) -> None:
        self._mark("open")

    def close(self) -> None:
        pass

    def start_exposure(self, *, seconds, gain, offset, roi, light) -> None:
        self._mark("start_exposure")

    def image_ready(self) -> bool:
        self._mark("image_ready")
        return not self.never_ready

    def read_frame(self) -> bytes:
        return np.zeros(4 * 2, dtype="<u2").tobytes()

    def abort(self) -> None:
        pass

    def set_read_mode(self, mode: str) -> None:
        raise DeviceError("camera has no selectable read modes")

    def get_temperature(self):
        if self.gone_on_temperature:
            raise CameraGone("ASI SDK ASIGetControlValue failed (CAMERA_REMOVED, "
                             "code 5)")
        return None


def _drops(lines) -> list[tuple[str, str, str]]:
    return [ln for ln in lines if "no longer answering" in ln[1]]


async def test_a_removed_camera_polled_while_idle_reads_disconnected_and_warns_once(
        monkeypatch):
    """The idle strand. Nothing is exposing; the 2 s status poll reads the
    temperature; the SDK says the camera is gone. ``connected`` must follow,
    and the owner must be told ONCE, at warning (the only level alerting
    hears), however many more polls land on the same dead camera.

    Named mutant m3: ``get_temperature``'s ``except CameraGone`` clause
    replaced by a bare swallow. Named mutant m4: ``_mark_dropped`` logs on
    every call, not on the True -> False transition.
    """
    logs = _record_logs(monkeypatch)
    cam = NativeCamera(_Adapter(gone_on_temperature=True), name="Guide ASI")
    await cam.connect()
    assert cam.connected is True, "precondition"

    for _ in range(10):
        with pytest.raises(CameraGone):
            await cam.get_temperature()
        assert cam.connected is False, (
            "an idle camera that reports itself removed must read disconnected")

    drops = _drops(logs)
    assert len(drops) == 1, (
        f"ten polls of one removed camera must write exactly one warning, "
        f"got {len(drops)}")
    level, message, source = drops[0]
    assert level == "warning"
    assert source == "camera"
    assert "Guide ASI" in message
    assert "CAMERA_REMOVED" in message


async def test_a_camera_that_drops_twice_is_reported_twice(monkeypatch):
    """Once per TRANSITION, not once per life: reconnect it, lose it again,
    and the second loss is news."""
    logs = _record_logs(monkeypatch)
    adapter = _Adapter(gone_on_temperature=True)
    cam = NativeCamera(adapter, name="Guide ASI")
    await cam.connect()
    with pytest.raises(CameraGone):
        await cam.get_temperature()

    await cam.connect()                      # the gate reopened it
    assert cam.connected is True
    with pytest.raises(CameraGone):
        await cam.get_temperature()

    assert len(_drops(logs)) == 2


@pytest.mark.parametrize("fail_on", ["start_exposure", "image_ready"])
async def test_every_measured_drop_site_says_so_once(fail_on, monkeypatch):
    """The hooks an exposure drives (WP-47) measure the same drop; they now say
    it too, once, instead of flipping a flag nobody hears about."""
    logs = _record_logs(monkeypatch)
    cam = NativeCamera(_Adapter(fail_on=fail_on), name="Guide ASI")
    await cam.connect()

    for _ in range(3):
        with pytest.raises(DeviceError):
            await cam.expose(0.01, gain=0, offset=0)

    assert cam.connected is False
    assert len(_drops(logs)) == 1, _drops(logs)
    assert _drops(logs)[0][0] == "warning"


async def test_an_imageready_timeout_says_so_once(monkeypatch):
    logs = _record_logs(monkeypatch)
    cam = NativeCamera(_Adapter(never_ready=True), name="Guide ASI")
    await cam.connect()
    cam.EXPOSURE_POLL_MARGIN_S = 0.0

    with pytest.raises(DeviceError, match="imageready timeout"):
        await cam.expose(0.01, gain=0, offset=0)

    assert cam.connected is False
    assert len(_drops(logs)) == 1, _drops(logs)


async def test_an_unsupported_read_mode_is_not_reported_as_a_drop(monkeypatch):
    """``set_read_mode`` opts out of measuring (a brand with no read modes
    raises there by design); it must not warn either."""
    logs = _record_logs(monkeypatch)
    cam = NativeCamera(_Adapter(), name="Guide ASI")
    await cam.connect()

    with pytest.raises(DeviceError, match="no selectable read modes"):
        await cam.expose(0.01, gain=0, offset=0, read_mode="LowNoise")

    assert cam.connected is True
    assert _drops(logs) == []


async def test_a_good_frame_is_not_lost_to_a_failed_temperature_read(monkeypatch):
    """``expose`` reads the temperature AFTER the download. A camera that
    vanishes in that instant has already handed over a complete frame; the
    frame is kept (temperature None), the drop is still marked and said, and
    the next exposure is what fails.

    Named mutant m8: the ``except CameraGone`` around that read deleted.
    """
    logs = _record_logs(monkeypatch)
    cam = NativeCamera(_Adapter(gone_on_temperature=True), name="Guide ASI")
    await cam.connect()

    frame = await cam.expose(0.01, gain=0, offset=0)

    assert frame.data.shape == (2, 4)
    assert frame.temperature_c is None
    assert cam.connected is False
    assert len(_drops(logs)) == 1


async def test_a_healthy_camera_polled_while_idle_stays_connected(monkeypatch):
    """Sanity control: a camera that answers (None, a camera with no sensor)
    is not marked dropped by a temperature poll."""
    logs = _record_logs(monkeypatch)
    cam = NativeCamera(_Adapter(), name="Guide ASI")
    await cam.connect()

    for _ in range(5):
        assert await cam.get_temperature() is None

    assert cam.connected is True
    assert _drops(logs) == []
