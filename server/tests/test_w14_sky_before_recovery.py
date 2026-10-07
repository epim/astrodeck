# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Read the sky before spending a guiding recovery (#621, WP-91).

OBSERVED, astrotown 2026-10-01 (night 2026-09-30), NGC 7331 on 0.3.38. Cloud
closed in and for eighteen minutes the engine treated it as a
guiding fault: "guiding lost - attempting recovery (1/2)", two dither settle
failures read as "the field is walking; holding to re-centre and recalibrate",
a recalibration that "found no star in 14 frames", recovery (2/2), and only
then a frame-based sky verdict from an L frame entered the cloud hold.
Both recovery attempts and a calibration walk were spent against cloud, and
four narrowband subs, the frames least able to raise the sky verdict
themselves, were shot in between.

THE FIX. `_sky_closed_before_recovery` reads the sky at the two frame-boundary
entry points of the guiding-loss ladder, `_maybe_recover_guiding` and
`_hold_recentre_recalibrate` (which the re-lock and the dither-settle detectors
share), and again in the two failure tails after a recalibration found no star.
A cloudy reading enters the existing `_hold_for_clear` and charges NO recovery
attempt (backlog ruling, orchestrator, WP-91). The reading is one unsaved
frame through the plate-solve filter slot (the hub's borrow/return pair), never
the interrupted narrowband filter, and is never fed to the debounced vote.

Every case here drives the real engine on a simulator hub whose ``mode`` is
set to "native" (a simulated frame carries no stars and always reads cloudy,
which is why the check refuses a simulator; see
test_sky_stands_in_for_a_missing_monitor.py), with the camera's capture
scripted and the wheel replaced by a recording double.

NAMED MUTANTS, each applied from a byte backup, run, and restored
byte-identically (sha256 compared, the mutant text grepped gone). Observed
failures are quoted in each case's docstring.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import SafetyAbort
from astrodeck.sequence.models import ExposureStep, Target

CLOUDY = {"cloud": {"cloudy": True, "score": 0.9,
                    "reason": "cloudy: 8 bright stars, low contrast (3x noise)"},
          "stars": 8}
CLEAR = {"cloud": {"cloudy": False, "score": 0.1,
                   "reason": "clear (200 bright stars, 17x noise)"},
         "stars": 200}
#: A frame nobody could judge (no ``cloud`` key at all, as for a stretched
#: frame or one shot through a blackout slot).
UNJUDGED = {"stars": 3}
#: A frame the detector called clear that found no stars to measure, and one
#: that carries no star count at all: neither can show a walking field.
CLEAR_NO_STARS = {"cloud": {"cloudy": False, "score": 0.1,
                            "reason": "clear (0 bright stars, 9x noise)"},
                  "stars": 0}
CLEAR_NO_COUNT = {"cloud": {"cloudy": False, "score": 0.1,
                            "reason": "clear (40 bright stars, 12x noise)"}}

LOST_STAR = ("native guider: lost the calibration star - 0 pulse(s), the star "
             "walked 0.0px from where it started, and the last 14 frame(s) "
             "found no star.")


class _Wheel:
    """A filter wheel that records its moves and takes no time.

    Slot 0 is luminance, slot 1 the narrowband filter the run is on (as the
    Ha step the incident interrupted), slot 2 a blackout carrier.
    """
    connected = True

    def __init__(self, *, names=("L", "Ha", "Dark"),
                 narrowband=(False, True, False),
                 opaque=(False, False, True), slot=1) -> None:
        self.filter_names = list(names)
        self.filter_narrowband = list(narrowband)
        self.filter_opaque = list(opaque)
        self.slot = slot
        self.moves: list[int] = []

    async def get_position(self) -> int:
        return self.slot

    async def set_position(self, slot: int) -> None:
        self.moves.append(int(slot))
        self.slot = int(slot)

    def is_narrowband(self, slot: int) -> bool:
        return bool(self.filter_narrowband[slot])

    def is_opaque(self, slot: int) -> bool:
        return bool(self.filter_opaque[slot])


class _Rig:
    """The engine plus every recorder one case reads."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.holds: list[tuple[str, object]] = []
        self.probes: list[dict] = []


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(**kw) -> Target:
    base = dict(name="NGC 7331", ra_hours=22.6182, dec_deg=34.4098,
                center=True, autofocus_first=False,
                steps=[ExposureStep(filter="Ha", exposure_s=180.0, count=10)])
    base.update(kw)
    return Target(**base)


def _rig(hub, monkeypatch, frames, *, mode="native", fallback=True,
         start_raises=None, wheel=None, limit=2, step_exposure_s=180.0,
         real_hold=False) -> tuple[SequenceEngine, _Rig]:
    """A real engine on a hub that is not a simulator, with guiding LOST.

    ``frames`` scripts the probe frames in order (the last one repeats): a
    dict is the frame ``hub.capture`` returns, an exception is raised by it.
    ``_hold_for_clear`` is recorded rather than run unless ``real_hold``.
    """
    rig = _Rig()
    hub.mode = mode
    cfg = hub_module.config_store.cfg()
    monkeypatch.setattr(cfg.safety, "sky_fallback_hold", fallback)
    monkeypatch.setattr(cfg.guide, "dither_settle_fail_limit", limit)
    # The re-lock detector must not fire: these cases call the shared hold
    # body directly or through the dither detector.
    monkeypatch.setattr(cfg.guide, "relock_limit", 0)
    eng = SequenceEngine(hub)
    eng._cfg = cfg
    eng.plan = SequencePlan(name="p", guide=True, recover_guiding=True,
                            targets=[_target()])
    eng._hold_step = ExposureStep(filter="Ha", exposure_s=step_exposure_s,
                                  gain=111, offset=22, binning=2, count=10)

    the_wheel = wheel or _Wheel()
    hub.devices["filterwheel"] = the_wheel
    rig.wheel = the_wheel

    g = hub.guider

    async def _inactive():
        return False

    async def _stop():
        rig.calls.append("stop")

    def _clear():
        rig.calls.append("clear")
        return True

    async def _start():
        rig.calls.append("start")
        if start_raises is not None:
            raise DeviceError(start_raises)

    async def _center(ra, dec, rotation_deg=None, **kw):
        rig.calls.append("center")

    async def _quiet(*a, **k):
        return None

    monkeypatch.setattr(g, "is_active", _inactive)
    monkeypatch.setattr(g, "stop_guiding", _stop)
    monkeypatch.setattr(g, "clear_calibration", _clear, raising=False)
    monkeypatch.setattr(g, "start_guiding", _start)
    monkeypatch.setattr(hub, "goto_and_center", _center)
    monkeypatch.setattr(eng, "_await_guider_quiet", _quiet)

    script = list(frames)

    async def _capture(exp, gain, offset, binning, **kw):
        # The wheel as the shutter opens: the whole point of the borrow.
        slot = await the_wheel.get_position()
        n = len(rig.probes)
        rig.probes.append({"exp": exp, "gain": gain, "offset": offset,
                           "binning": binning, "slot": slot, **kw})
        rig.calls.append("probe")
        frame = script[min(n, len(script) - 1)]
        if isinstance(frame, BaseException):
            raise frame
        return dict(frame)

    monkeypatch.setattr(hub, "capture", _capture)

    if not real_hold:
        async def _hold(reason, target):
            rig.calls.append("hold")
            rig.holds.append((reason, target))

        monkeypatch.setattr(eng, "_hold_for_clear", _hold)
    return eng, rig


def _warnings(bus_lines, needle: str) -> list[str]:
    return [m for lvl, m, _s in bus_lines if needle in m]


# ---------------------------------------------------------------- (a) cloudy

async def test_a_cloudy_sky_enters_the_hold_and_charges_no_attempt(
        sim_hub, monkeypatch):
    """(a) The incident: guiding lost, the sky closed. The ladder is not
    entered at all: no re-centre, no calibration cleared, no guider start, no
    attempt charged, and the cloud hold is entered once, naming why.

    Mutant "the sky check before recovery removed" (the
    ``if await self._sky_closed_before_recovery(target, why="guiding was
    lost"):`` block deleted from `_maybe_recover_guiding`): RED (observed) -
        AssertionError: a cloudy sky entered the recovery ladder: calls =
        ['center', 'start'] (hold entered 0 time(s))
    Mutant "check moved after the attempt is charged" (the block deleted
    from its place and re-inserted after ``self._guiding_recoveries += 1``):
    RED (observed) -
        AssertionError: a cloudy reading charged a recovery attempt: 1
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY])
    t = eng.plan.targets[0]
    await eng._maybe_recover_guiding(t)

    assert rig.calls == ["probe", "hold"], (
        f"a cloudy sky entered the recovery ladder: calls = {rig.calls} "
        f"(hold entered {len(rig.holds)} time(s))")
    assert len(rig.holds) == 1
    reason, held_target = rig.holds[0]
    assert held_target is t
    assert "guiding was lost" in reason and "cloudy" in reason, reason
    assert eng._guiding_recoveries == 0, (
        f"a cloudy reading charged a recovery attempt: "
        f"{eng._guiding_recoveries}")


async def test_a_cloudy_sky_holds_even_when_the_attempts_are_spent(
        sim_hub, monkeypatch):
    """The check sits BEFORE the cap test. With both attempts spent, the
    ladder's answer to a lost star is the operator's ``guiding_action``,
    which under require_guiding + abort ends the NIGHT: cloud must not end a
    night over a guide star.

    Mutant "check moved after the cap test" (the block placed below the
    ``if self._guiding_recoveries >= _MAX_GUIDING_RECOVERIES:`` branch): RED
    (observed) -
        astrodeck.sequence.engine.SafetyAbort: guiding could not be kept after
        2 recovery attempts without a frame
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY])
    monkeypatch.setattr(eng._cfg.escalation, "require_guiding", True)
    monkeypatch.setattr(eng._cfg.escalation, "guiding_action", "abort")
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert rig.calls == ["probe", "hold"], rig.calls
    assert eng._guiding_recoveries == engine_mod._MAX_GUIDING_RECOVERIES


# ----------------------------------------------------- (b) clear, (c) unknown

async def test_a_clear_sky_lets_recovery_proceed_and_charges_one(
        sim_hub, monkeypatch, bus_lines):
    """(b) A clear reading is the old behaviour: re-centre, restart, 1/2.

    Mutant "any reading holds" (``if not cloudy:`` made ``if False:``, so a
    clear verdict falls through to the hold): RED (observed) -
        AssertionError: a clear sky held the run: calls = ['probe', 'hold']
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert rig.calls == ["probe", "center", "start"], (
        f"a clear sky held the run: calls = {rig.calls}")
    assert eng._guiding_recoveries == 1
    assert _warnings(bus_lines, "attempting recovery (1/2)")
    # The detector's reason opens with its own verdict, so the line must not
    # wrap it in a second one ("the sky reads clear (clear (200 bright ...").
    # Verifier-added, mutant "the reason wrapped again" (the ``said`` line
    # replaced by ``said = f"clear ({reason})"``): RED (observed) -
    #     AssertionError: the clear reading was reported twice over: [...]
    said = _warnings(bus_lines, "the sky reads")
    assert said and "clear (clear" not in said[0], (
        f"the clear reading was reported twice over: {said}")
    assert "the sky reads clear (200 bright stars" in said[0], said


@pytest.mark.parametrize("what", ["unjudged", "capture failed",
                                  "a narrowband frame"])
async def test_an_unknown_sky_lets_recovery_proceed(sim_hub, monkeypatch,
                                                    what):
    """(c) A frame nobody could judge, a capture that failed, and a frame that
    could only be shot through a narrowband filter (a wheel with no broadband
    slot to borrow, so the reading would be blind) are all UNKNOWN, and
    unknown is not a reason to block recovery.

    Mutant "unknown reads as cloudy" (``got = verdict_from_info(info)`` made
    ``got = verdict_from_info(info) or (True, None, "")``): RED for the
    unjudged case (observed) -
        AssertionError: unjudged: an unknown sky held the run: calls =
        ['probe', 'hold']
    Mutant "a blind reading is trusted" (``if through is not None:`` made
    ``if False:``, the narrowband-in-the-beam guard): RED for the narrowband
    case only (observed) -
        AssertionError: a narrowband frame: an unknown sky held the run:
        calls = ['probe', 'hold']
    """
    if what == "unjudged":
        eng, rig = _rig(sim_hub, monkeypatch, [UNJUDGED])
    elif what == "capture failed":
        eng, rig = _rig(sim_hub, monkeypatch,
                        [DeviceError("camera: the download failed")])
    else:
        # No luminance slot: the borrow has nothing better to move to, the
        # probe goes through Ha, and its "cloudy" is a blind frame's.
        wheel = _Wheel(names=("Ha", "Oiii", "Dark"),
                       narrowband=(True, True, False),
                       opaque=(False, False, True), slot=0)
        eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY], wheel=wheel)
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert not rig.holds, (
        f"{what}: an unknown sky held the run: calls = {rig.calls}")
    assert rig.calls[-2:] == ["center", "start"], rig.calls
    assert eng._guiding_recoveries == 1


async def test_a_wedged_camera_still_escalates_as_a_safety_abort(
        sim_hub, monkeypatch):
    """A capture that TIMES OUT is a device wedge (P0-2), not a frame that
    failed to judge: it leaves through the same SafetyAbort every other stuck
    exposure does, rather than being read as an unknown sky. And the wheel
    the borrow moved still goes back.

    Mutant "a wedge is swallowed" (``except SafetyAbort: raise`` deleted from
    the probe): RED (observed) -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.SafetyAbort'>
    """
    eng, rig = _rig(sim_hub, monkeypatch, [SafetyAbort("capture timed out")])
    with pytest.raises(SafetyAbort):
        await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert rig.wheel.slot == 1, "the wheel was left on the borrowed slot"


# ------------------------------------------------ the reading itself (ruling 2)

async def test_the_probe_is_unsaved_broadband_and_capped(sim_hub,
                                                         monkeypatch):
    """The reading is ONE unsaved frame, at the interrupted step's gain,
    offset and binning, capped at SKY_PRECHECK_MAX_S (60 s), through the
    luminance slot; the wheel is back on the run's filter afterwards.

    Mutant "probe through the interrupted filter instead of the borrowed one"
    (``slot = await self.hub._borrow_wheel_for_solve()`` replaced with
    ``slot = None`` AND the narrowband guard ``if through is not None:`` made
    ``if False:``): RED (observed) -
        AssertionError: the probe was shot through slot 1, not the borrowed
        luminance slot 0
    With the borrow removed and the guard KEPT the run is safe by a second
    route (a blind frame is never taken, it is read as unknown): RED at the
    first assertion (observed) -
        assert 0 == 1    (``len(rig.probes) == 1``)
    Mutant "probe exposure uncapped" (``min(float(step.exposure_s),
    SKY_PRECHECK_MAX_S)`` replaced with ``float(step.exposure_s)``): RED
    (observed) -
        AssertionError: a 180 s step probed for 180.0 s
    """
    assert engine_mod.SKY_PRECHECK_MAX_S == 60.0
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    await eng._maybe_recover_guiding(eng.plan.targets[0])

    assert len(rig.probes) == 1
    p = rig.probes[0]
    assert p["slot"] == 0, (
        f"the probe was shot through slot {p['slot']}, not the borrowed "
        f"luminance slot 0")
    assert p["exp"] == 60.0, f"a 180 s step probed for {p['exp']} s"
    assert (p["gain"], p["offset"], p["binning"]) == (111, 22, 2)
    assert p["save"] is False, "the sky reading was saved as a frame"
    assert rig.wheel.moves == [0, 1], (
        f"the borrow was not symmetric: moves {rig.wheel.moves}")
    assert rig.wheel.slot == 1, "the wheel was not put back"


async def test_a_short_step_is_probed_for_its_own_exposure(sim_hub,
                                                           monkeypatch):
    """The cap is a ceiling, not a floor: a 30 s step is not stretched to 60.

    Mutant "probe always at the cap" (``min(float(step.exposure_s),
    SKY_PRECHECK_MAX_S)`` replaced with ``SKY_PRECHECK_MAX_S``): RED
    (observed) -
        AssertionError: a 30 s step probed for 60.0 s
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR], step_exposure_s=30.0)
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert rig.probes[0]["exp"] == 30.0, (
        f"a 30 s step probed for {rig.probes[0]['exp']} s")


async def test_the_probe_is_a_light_frame_whatever_step_was_interrupted(
        sim_hub, monkeypatch):
    """A step that is not a light (a dark shot on a light target) is the step
    the loop was on when guiding was lost, and a reading taken with its frame
    type would be a closed-shutter frame that shows no sky.

    Verifier-added. Mutant "the frame type not forced" (``"frame_type":
    "Light",`` deleted from the probe step's ``update``): RED (observed) -
        AssertionError: the reading was taken as a 'Dark' frame
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    eng._hold_step = ExposureStep(filter="", exposure_s=180.0, gain=111,
                                  offset=22, binning=2, count=10,
                                  frame_type="Dark")
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert len(rig.probes) == 1
    assert rig.probes[0]["frame_type"] == "Light", (
        f"the reading was taken as a {rig.probes[0]['frame_type']!r} frame")


async def test_the_wheel_goes_back_when_the_capture_fails(sim_hub,
                                                          monkeypatch):
    """The return is in a ``finally``: a frame that fails still leaves the
    wheel where the run had it, so the next science frame's focuser-offset
    bookkeeping sees no change.

    Mutant "the wheel returned only on success" (the return moved out of the
    ``finally:`` and into the try, after the capture): RED (observed) -
        AssertionError: the wheel was left on slot 0 after a failed probe
    (and the wedged-camera case above goes red as well: "the wheel was left
    on the borrowed slot")
    """
    eng, rig = _rig(sim_hub, monkeypatch,
                    [DeviceError("camera: the download failed")])
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert rig.wheel.slot == 1, (
        f"the wheel was left on slot {rig.wheel.slot} after a failed probe")


async def test_the_probe_is_never_fed_to_the_debounced_vote(sim_hub,
                                                            monkeypatch):
    """A broadband probe fed to ``_clouds`` would pollute the debounced vote
    the science filter's frames build (the `_probe_step` docstring says why).

    The vote itself cannot show it: one observation is not yet a verdict (the
    state debounces, so the first version of this case, which read
    ``_clouds.cloudy()``, stayed green under the mutant). So the feed is
    spied on, for a clear reading and a cloudy one.

    Mutant "probe fed to the vote" (``self._observe_clouds(info)`` added after
    the capture): RED (observed) -
        AssertionError: the sky reading was fed to the debounced vote 1
        time(s)
    """
    for frame in (CLOUDY, CLEAR):
        eng, rig = _rig(sim_hub, monkeypatch, [frame])
        fed: list[tuple] = []
        real = eng._clouds.observe

        def _spy(*a, _real=real, **k):
            fed.append(a)
            return _real(*a, **k)

        monkeypatch.setattr(eng._clouds, "observe", _spy)
        await eng._maybe_recover_guiding(eng.plan.targets[0])
        assert rig.probes, "premise: a reading was taken"
        assert not fed, (
            f"the sky reading was fed to the debounced vote {len(fed)} "
            f"time(s)")


# -------------------------------------------------------- when it stands aside

@pytest.mark.parametrize("why", ["a simulator", "the switch is off",
                                 "already holding", "a calibration target",
                                 "no target", "no step to copy"])
async def test_it_stands_aside(sim_hub, monkeypatch, why):
    """The preconditions that return False at once, as the two existing
    frame-verdict fallbacks do. Each asserts BOTH that no frame was taken and
    that recovery went on as it always did.

    Mutants, each condition deleted from the helper's guard, RED (observed):
    "a simulator is a sky" (the ``mode == "sim"`` line), "no-target guard
    removed" (``target is None or``), "calibration guard removed",
    "already-holding guard removed" (``self._holding_for_clear``) and "switch
    guard removed" (``sky_fallback_hold``), each with the words
        AssertionError: <why>: the sky was read when it must not be (1
        frame(s))
    and "no-step guard removed" (``if step is None: return False``), which
    the unguarded line turns into
        AttributeError: 'NoneType' object has no attribute 'model_copy'
    """
    kw: dict = {}
    if why == "a simulator":
        kw["mode"] = "sim"
    if why == "the switch is off":
        kw["fallback"] = False
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY], **kw)
    target = eng.plan.targets[0]
    if why == "already holding":
        eng._holding_for_clear = True
    elif why == "a calibration target":
        target = _target(calibration=True)
    elif why == "no target":
        target = None
    elif why == "no step to copy":
        eng._hold_step = None
    await eng._maybe_recover_guiding(target)
    assert not rig.probes, (
        f"{why}: the sky was read when it must not be "
        f"({len(rig.probes)} frame(s))")
    assert not rig.holds, f"{why}: held on no reading: {rig.calls}"
    assert eng._guiding_recoveries == 1, (
        f"{why}: recovery did not go on as before ({rig.calls})")


# --------------------------------------------------------- (e) the minimum gap

async def test_a_second_loss_inside_the_gap_does_not_probe_again(
        sim_hub, monkeypatch):
    """(e) A loss/recovery cycle must not probe on every frame: a second
    guiding loss within CLOUD_PROBE_EVERY_S of the last reading goes straight
    to recovery, and once the gap has passed it reads the sky again.

    Mutant "the gap ignored" (``time.monotonic() - last <
    CLOUD_PROBE_EVERY_S`` made ``False``): RED (observed) -
        AssertionError: a second loss inside the gap probed again: 2 probes
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    await eng._maybe_recover_guiding(t)
    await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == 1, (
        f"a second loss inside the gap probed again: {len(rig.probes)} probes")
    assert eng._guiding_recoveries == 2, "the second loss was not recovered"

    # The gap passes (the marker is the engine's own monotonic stamp).
    eng._guiding_recoveries = 0
    eng._sky_precheck_at -= engine_mod.CLOUD_PROBE_EVERY_S + 1.0
    await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == 2, "the sky was never read again after the gap"


@pytest.mark.parametrize("what", ["capture failed", "unjudged"])
async def test_a_reading_that_gave_no_verdict_still_starts_the_gap(
        sim_hub, monkeypatch, what):
    """The gap runs from a reading's END, success or not. A camera that keeps
    failing, or a frame nobody can judge, must not be asked again at every
    frame boundary: that would spend up to a minute of exposure per boundary
    on a reading that has just said it cannot read.

    Verifier-added. Mutant "the stamp only on success" (the
    ``self._sky_precheck_at = time.monotonic()`` moved out of the ``finally``
    and to the line after the capture): RED for the failed-capture case
    (observed) -
        AssertionError: capture failed: a second loss asked a camera that had
        just failed: 2 probes
    (an unjudged frame still returns through the success path, so its case
    passes under that mutant and is the control)
    """
    frames = ([DeviceError("camera: the download failed"), CLEAR]
              if what == "capture failed" else [UNJUDGED, CLEAR])
    eng, rig = _rig(sim_hub, monkeypatch, frames)
    t = eng.plan.targets[0]
    await eng._maybe_recover_guiding(t)
    await eng._maybe_recover_guiding(t)
    assert len(rig.probes) == 1, (
        f"{what}: a second loss asked a camera that had just failed: "
        f"{len(rig.probes)} probes")
    assert eng._guiding_recoveries == 2, "recovery did not go on as before"


# ------------------------------------------- (d) the dither-settle detector

async def test_two_failed_settles_under_cloud_hold_instead_of_recalibrating(
        sim_hub, monkeypatch, bus_lines):
    """(d) The second incident entry: two consecutive dither settles fail
    ("settle timed out", which is what cloud does to a guide loop) and the
    detector reaches `_hold_recentre_recalibrate`. Under a cloudy reading it
    holds for clear sky: the calibration is NOT cleared, nothing slews, the
    guider is not restarted, and the walking-field claim is never made.

    Mutant "check removed from `_hold_recentre_recalibrate`" (the
    ``if await self._sky_closed_before_recovery(target, why=why):`` block
    deleted): RED (observed) -
        AssertionError: two failed settles under a cloudy sky recalibrated:
        calls = ['stop', 'clear', 'center', 'start']
    (the re-lock case, the restart-tail case and the blind-wording case below
    go red under it as well)
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY])
    t = eng.plan.targets[0]
    eng._note_dither_failure(DeviceError(
        "native guider: dither settle failed (settle timed out)"))
    eng._note_dither_failure(DeviceError(
        "native guider: dither settle failed (settle timed out)"))
    await eng._maybe_hold_for_dither_failures(t)

    assert rig.calls == ["probe", "hold"], (
        f"two failed settles under a cloudy sky recalibrated: "
        f"calls = {rig.calls}")
    assert "2 consecutive dither settles failed" in rig.holds[0][0]
    assert eng._dither_settle_fails == 0, "the streak was not spent"
    assert not _warnings(bus_lines, "the field is walking")


async def test_the_relock_detector_shares_the_check(sim_hub, monkeypatch):
    """The GN-03 re-lock rate reaches the same body, so the same check."""
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY])
    await eng._hold_recentre_recalibrate("guiding re-locked 3 times in 10 min",
                                         eng.plan.targets[0])
    assert rig.calls == ["probe", "hold"], rig.calls
    assert "re-locked 3 times" in rig.holds[0][0]


# ------------------------------------------------------------ the failure tails

async def test_a_recalibration_that_finds_no_star_asks_the_sky_again(
        sim_hub, monkeypatch):
    """The first reading said clear; the calibration then found no star in 14
    frames. That is new evidence, so the sky is read AGAIN (inside the gap: a
    failure tail is not a per-frame cycle) and, if it has closed, the run
    holds and the attempt that was spent against cloud is given back, so
    recovery 2/2 is not spent against cloud either.

    Mutant "recovery tail without the re-ask" (the
    ``if await self._sky_closed_before_recovery(..., after_failure=True):``
    block replaced by ``pass`` after ``guiding recovery failed``): RED
    (observed) -
        AssertionError: the failure tail did not ask the sky again: calls =
        ['probe', 'center', 'start']
    Mutant "the spent attempt kept" (``self._guiding_recoveries = max(0,
    self._guiding_recoveries - 1)`` replaced by ``pass``): RED (observed) -
        AssertionError: an attempt spent against cloud was kept: 1
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR, CLOUDY],
                    start_raises=LOST_STAR)
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert rig.calls == ["probe", "center", "start", "probe", "hold"], (
        f"the failure tail did not ask the sky again: calls = {rig.calls}")
    assert "guiding recovery failed" in rig.holds[0][0]
    assert eng._guiding_recoveries == 0, (
        f"an attempt spent against cloud was kept: {eng._guiding_recoveries}")


async def test_a_failed_recovery_under_a_clear_sky_keeps_its_attempt(
        sim_hub, monkeypatch):
    """The control for the case above: the second reading is clear too, so
    nothing is held and the attempt stays charged (the bound still ends a
    genuine loss)."""
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR], start_raises=LOST_STAR)
    await eng._maybe_recover_guiding(eng.plan.targets[0])
    assert not rig.holds
    assert len(rig.probes) == 2
    assert eng._guiding_recoveries == 1


async def test_a_failed_restart_after_the_hold_asks_the_sky_again(
        sim_hub, monkeypatch):
    """The same re-ask in `_hold_recentre_recalibrate`'s tail ("guiding
    restart after the hold failed"): head reading clear, calibration finds no
    star, tail reading cloudy -> the cloud hold.

    Mutant "restart tail without the re-ask" (the ``await
    self._sky_closed_before_recovery(..., after_failure=True)`` replaced by
    ``pass`` after ``guiding restart after the hold failed``): RED
    (observed) -
        AssertionError: the restart tail did not ask the sky again: calls =
        ['probe', 'stop', 'clear', 'center', 'start']
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR, CLOUDY],
                    start_raises=LOST_STAR)
    await eng._hold_recentre_recalibrate("2 consecutive dither settles failed",
                                         eng.plan.targets[0])
    assert rig.calls == ["probe", "stop", "clear", "center", "start", "probe",
                         "hold"], (
        f"the restart tail did not ask the sky again: calls = {rig.calls}")
    assert "guiding restart after the hold failed" in rig.holds[0][0]


# -------------------------------------------------------------- the diagnosis

async def test_the_walking_claim_needs_stars_that_are_present(
        sim_hub, monkeypatch, bus_lines):
    """"The field is walking" is a diagnosis of displaced stars. It is said
    when the sky reading found stars, and it is NOT said when the reading
    found none to measure: the same hold goes ahead, under a wording that
    claims only what is known.

    Mutant "the claim never made" (``if getattr(self, "_pre_recovery_blind",
    False):`` made ``if True:``): RED (observed) -
        AssertionError: a reading that found 200 stars did not support the
        diagnosis
    (and the no-reading case below goes red too, which is what keeps the
    detectors' own tests green)
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    await eng._hold_recentre_recalibrate("2 consecutive dither settles failed",
                                         eng.plan.targets[0])
    assert _warnings(bus_lines, "the field is walking"), (
        "a reading that found 200 stars did not support the diagnosis")
    assert rig.calls[-1] == "start"


@pytest.mark.parametrize("what", ["unjudged", "capture failed",
                                  "a narrowband frame", "clear, no stars",
                                  "clear, no star count"])
async def test_a_blind_reading_does_not_claim_a_walking_field(
        sim_hub, monkeypatch, bus_lines, what):
    """A reading that shows no stars to measure cannot say the field is
    walking; the hold still runs, under a wording that claims less. Every way
    a reading ends up blind is a case: a frame nobody could judge, a capture
    that failed, a wheel with no broadband slot (the frame would be blind),
    and a clear verdict on a frame with no stars or no star count.

    Mutant "the claim always made" (``if getattr(self, "_pre_recovery_blind",
    False):`` made ``if False:``): RED for every case (observed) -
        AssertionError: unjudged: the hold claimed a walking field on a
        reading that found no stars: ['2 consecutive dither settles failed:
        the field is walking; holding to re-centre and recalibrate']
    Verifier-added, one mutant per way of being blind, each deleting the
    ``self._pre_recovery_blind = True`` of that way (or, for the clear
    verdict, making it ``= False``), RED for that case only (observed):
    "unjudged" for the ``got is None`` branch, "capture failed" for the
    ``except Exception`` branch, "a narrowband frame" for the
    ``through is not None`` branch, and "clear, no stars" and "clear, no star
    count" together for the clear-verdict branch.
    """
    wheel = None
    if what == "unjudged":
        frame = UNJUDGED
    elif what == "capture failed":
        frame = DeviceError("camera: the download failed")
    elif what == "a narrowband frame":
        frame = CLOUDY
        wheel = _Wheel(names=("Ha", "Oiii", "Dark"),
                       narrowband=(True, True, False),
                       opaque=(False, False, True), slot=0)
    elif what == "clear, no stars":
        frame = CLEAR_NO_STARS
    else:
        frame = CLEAR_NO_COUNT
    eng, rig = _rig(sim_hub, monkeypatch, [frame], wheel=wheel)
    await eng._hold_recentre_recalibrate("2 consecutive dither settles failed",
                                         eng.plan.targets[0])
    claimed = _warnings(bus_lines, "the field is walking")
    assert not claimed, (
        f"{what}: the hold claimed a walking field on a reading that found "
        f"no stars: {claimed}")
    said = _warnings(bus_lines, "the sky could not say why")
    assert said and "holding to re-centre and recalibrate" in said[0], said
    # The hold itself still ran: only the diagnosis changed.
    assert rig.calls[-4:] == ["stop", "clear", "center", "start"], rig.calls


async def test_with_no_reading_asked_the_detectors_claim_stands(
        sim_hub, monkeypatch, bus_lines):
    """A simulator (or a switch turned off) asks the sky nothing, so there is
    no reading to contradict the detector: its own wording is unchanged
    (test_dither_settle_hold.py and test_native_guider_relocks.py pin it)."""
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY], mode="sim")
    await eng._hold_recentre_recalibrate("2 consecutive dither settles failed",
                                         eng.plan.targets[0])
    assert not rig.probes
    assert _warnings(bus_lines, "the field is walking")


# ------------------------------------------------------- the real cloud hold

async def test_the_real_hold_takes_over_and_releases(sim_hub, monkeypatch):
    """The hold this enters is the engine's own, not a stub: the REAL
    `_hold_for_clear` and the REAL `_safety_gate` on a rig with no safety
    monitor, the shape the recursion bug (2026-09-06) needed. The hold's own
    gate runs every pass and does not nest a second hold; the hold's probes
    then clear and it releases. No recovery attempt was charged and the
    ladder never ran.
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLOUDY], real_hold=True)
    sim_hub.devices.pop("safety", None)
    cfg = eng._cfg
    monkeypatch.setattr(cfg.safety, "enabled", True)
    monkeypatch.setattr(cfg.escalation, "require_safety_monitor", False)
    t = eng.plan.targets[0]
    eng._tracked_target = t
    sim_hub.devices["telescope"].rig.tracking = True

    probes = {"n": 0}

    async def _none(*a, **k):
        return None

    async def _no_dark(*a, **k):
        return False

    async def _probe(*a, **k):
        probes["n"] += 1
        return probes["n"] <= 1          # cloudy once, then clear

    for name in ("_checkpoint", "_frame_alerts_tick", "_stand_down_guider",
                 "_cooler_gate", "_setup_target", "_restore_beam"):
        monkeypatch.setattr(eng, name, _none)
    monkeypatch.setattr(eng, "_enforce_stop_boundary", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_hold_darks", _no_dark)
    monkeypatch.setattr(eng, "_cloud_probe", _probe)
    monkeypatch.setattr(eng, "_index_of_target", lambda _t: 0)
    monkeypatch.setattr(eng, "_set_state", lambda **kw: None)
    # The hold's own pacing, shrunk. The pre-recovery gap is a different
    # question and is left alone by there being one reading only.
    monkeypatch.setattr(engine_mod, "CLOUD_PROBE_EVERY_S", 0.001)

    await eng._maybe_recover_guiding(t)

    assert probes["n"] >= engine_mod.CLOUD_RESUME_CLEAR_PROBES, (
        "the hold released without the clear streak it requires")
    assert eng._holding_for_clear is False, "the hold flag outlived the hold"
    assert rig.calls == ["probe"], (
        f"the recovery ladder ran under a hold: {rig.calls}")
    assert eng._guiding_recoveries == 0
