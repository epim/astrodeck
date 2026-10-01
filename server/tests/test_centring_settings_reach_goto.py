"""A target's centring settings reach `goto_and_center` (#170), and a slew
that cannot rotate says so (#160, the engine half).

#170: `hub.goto_and_center` has always taken `tolerance_deg` and
`max_attempts`, and the engine never passed either, so every target centred
to the hub's own 1.2 arcmin and 3 attempts whatever its SLEW card said. The
Target now carries `center_tolerance_arcmin` and `center_attempts` (S1-01),
and `_setup_target` passes each ONLY WHEN IT IS SET. Unset, the call must be
exactly today's: the same two positional args and `rotation_deg` as the only
keyword, so no saved plan changes behaviour. The tracking-refusal recovery's
own re-centre builds its keywords through the same helper; that case lives in
test_recovery_centring_is_measured.py, which already drives the real
recovery to its re-centre. So do the three other re-centres of a target (after
the unguided initial sweep, after a lost guide star, after a walking field),
pinned below.

#160: only the centred branch hands `rotation_deg` to the hub. A target with
`center` off is slewed straight to its coordinates and nothing turns the
camera, so a planned angle was dropped in silence. The engine now says so,
once per setup, in words.

The hub here is a recording double: `goto_and_center` records its call and
answers centred, and the telescope records its commands.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


class _Tel:
    connected = True

    def __init__(self) -> None:
        self.calls: list = []

    async def is_parked(self) -> bool:
        return False

    async def unpark(self) -> None:
        self.calls.append("unpark")

    async def set_tracking(self, on: bool) -> None:
        self.calls.append(("tracking", on))

    async def get_tracking(self) -> bool:
        return True

    async def slew(self, ra: float, dec: float) -> None:
        self.calls.append(("slew", ra, dec))


class _Hub:
    """Just enough hub for `_setup_target`: a mount, no guider, no site."""

    def __init__(self) -> None:
        self.tel = _Tel()
        self.devices = {"telescope": self.tel}
        self.guider = None
        self.site: dict = {}
        self.gotos: list[tuple[tuple, dict]] = []

    def _check_solar(self, ra, dec, *, force=False) -> None:
        return None

    def require(self, role: str):
        return self.devices[role]

    async def goto_and_center(self, *args, **kwargs) -> dict:
        self.gotos.append((args, dict(kwargs)))
        return {"centered": True, "error_arcmin": 0.1}


def _setup(target: Target) -> tuple[SequenceEngine, _Hub]:
    hub = _Hub()
    e = SequenceEngine(hub)
    e._cfg = None
    e.plan = SequencePlan(name="centring", guide=False, meridian_flip=False,
                          safety_check=False, targets=[target])
    return e, hub


def _target(**kw) -> Target:
    base = dict(name="NGC 7331", ra_hours=22.6182, dec_deg=34.4098,
                center=True, autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
    base.update(kw)
    return Target(**base)


# ---------------------------------------------------------------- #170


async def test_a_tolerance_in_arcmin_reaches_the_hub_in_degrees():
    """0.5 arcmin is 0.5/60 of a degree, which is the hub's unit.

    Mutant "pass arcmin unconverted" (the helper passes
    ``tolerance_deg=target.center_tolerance_arcmin``): RED (and
    `test_both_settings_together` with it) -
        AssertionError: the hub was asked for 0.5 deg, a 30 arcmin
        tolerance, when the target said 0.5 arcmin: {'rotation_deg': None,
        'tolerance_deg': 0.5}
    Mutant "always pass the hub defaults" (the helper always returns
    ``tolerance_deg=0.02, max_attempts=3``): RED -
        AssertionError: the hub was asked for 0.02 deg, a 1.2 arcmin
        tolerance, when the target said 0.5 arcmin: {'rotation_deg': None,
        'tolerance_deg': 0.02, 'max_attempts': 3}
    """
    t = _target(center_tolerance_arcmin=0.5)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    assert len(hub.gotos) == 1, f"premise: one centring call: {hub.gotos}"
    args, kw = hub.gotos[0]
    tol = kw.get("tolerance_deg")
    assert tol == pytest.approx(0.5 / 60), (
        f"the hub was asked for {tol} deg, a {tol * 60 if tol else tol:g} "
        f"arcmin tolerance, when the target said 0.5 arcmin: {kw}")
    assert "max_attempts" not in kw, (
        f"attempts were not set, so none may be passed: {kw}")
    assert args == (t.ra_hours, t.dec_deg), f"positional args moved: {args}"


async def test_an_attempt_count_reaches_the_hub_as_is():
    """5 attempts is 5.

    Mutant "always pass the hub defaults": RED -
        AssertionError: the target asked for 5 attempts and the hub was
        asked for 3: {'rotation_deg': None, 'tolerance_deg': 0.02,
        'max_attempts': 3}
    """
    t = _target(center_attempts=5)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    _args, kw = hub.gotos[0]
    assert kw.get("max_attempts") == 5, (
        f"the target asked for 5 attempts and the hub was asked for "
        f"{kw.get('max_attempts')}: {kw}")
    assert "tolerance_deg" not in kw, (
        f"no tolerance was set, so none may be passed: {kw}")


@pytest.mark.parametrize("rotation", [None, 30.0], ids=["no angle", "PA 30"])
async def test_control_unset_settings_keep_todays_exact_call(rotation):
    """CONTROL, and the one every saved plan depends on. Unset, the call is
    byte-identical to the one the engine has always made: (ra, dec) and
    ``rotation_deg`` as the only keyword.

    Mutant "always pass the hub defaults": RED, both cases -
        AssertionError: an unset target changed the call: args (22.6182,
        34.4098), keywords {'rotation_deg': None, 'tolerance_deg': 0.02,
        'max_attempts': 3}
    (and ``'rotation_deg': 30.0`` in the PA 30 case.)
    """
    t = _target(rotation_deg=rotation)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    args, kw = hub.gotos[0]
    assert args == (t.ra_hours, t.dec_deg) and kw == {"rotation_deg": rotation}, (
        f"an unset target changed the call: args {args}, keywords {kw}")


async def test_both_settings_together():
    """Both set: both passed, each converted as its own case says."""
    t = _target(center_tolerance_arcmin=2.0, center_attempts=4,
                rotation_deg=12.5)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    _args, kw = hub.gotos[0]
    assert kw == {"rotation_deg": 12.5, "tolerance_deg": pytest.approx(2.0 / 60),
                  "max_attempts": 4}, kw


# ------------------------------------------ #170, every other centring
#
# The setup is not the target's last centring. An initial sweep that ran
# longer than RECENTRE_AFTER_UNGUIDED_S (60 s; a sweep on this rig takes
# minutes) is followed by a re-centre, and that is the pointing the first
# frame is shot at; a lost guide star and a walking field each re-centre too.
# Found in review: all three still called the hub with its defaults, so an
# autofocus-first target was centred to its SLEW card's tolerance and then
# straight away re-centred to 1.2 arcmin and 3 attempts, and #170 held only
# for targets that never swept.


class _LostGuider:
    """A connected guider whose star is gone: enough for both guiding
    re-centres to reach `goto_and_center`. ``stats()`` answers None, so the
    guider phase reads "" and the quiet wait after the restart returns at
    once."""
    connected = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def is_active(self) -> bool:
        return False

    async def stop_guiding(self) -> None:
        self.calls.append("stop")

    async def start_guiding(self) -> None:
        self.calls.append("start")

    def stats(self):
        return None


def _guided(target: Target) -> tuple[SequenceEngine, _Hub]:
    from types import SimpleNamespace
    e, hub = _setup(target)
    e.plan.guide = True
    e._policy = SimpleNamespace(recover_guiding=True)
    hub.guider = _LostGuider()
    return e, hub


async def _after_the_sweep(e: SequenceEngine, t: Target) -> None:
    from astrodeck.sequence import engine as engine_mod
    await e._recentre_after_unguided_focus(
        t, engine_mod.RECENTRE_AFTER_UNGUIDED_S + 1.0, guided=False)


async def _after_a_lost_star(e: SequenceEngine, t: Target) -> None:
    await e._maybe_recover_guiding(t)


async def _after_a_walking_field(e: SequenceEngine, t: Target) -> None:
    await e._hold_recentre_recalibrate("the field walked", t)


_RECENTRES = pytest.mark.parametrize(
    "recentre", [_after_the_sweep, _after_a_lost_star, _after_a_walking_field],
    ids=["after the unguided sweep", "after a lost star",
         "after a walking field"])


@_RECENTRES
async def test_every_re_centre_of_a_target_uses_its_own_settings(recentre):
    """Each re-centre carries the target's tolerance and attempts, exactly as
    its setup does.

    Mutant "re-centre after the sweep on the hub's defaults" (the
    ``**self._centring_kwargs(target)`` removed from
    `_recentre_after_unguided_focus`'s call only): RED, that case alone -
        AssertionError: after the unguided sweep, the target said 0.5 arcmin
        and 5 attempts and the hub was asked for {'rotation_deg': 30.0}
    Mutant "guiding-loss re-centre on the hub's defaults" (the same line
    removed from `_maybe_recover_guiding`): RED, that case alone -
        AssertionError: after a lost star, the target said 0.5 arcmin and 5
        attempts and the hub was asked for {'rotation_deg': 30.0}
    Mutant "walking-field re-centre on the hub's defaults" (removed from
    `_hold_recentre_recalibrate`): RED, that case alone -
        AssertionError: after a walking field, the target said 0.5 arcmin and
        5 attempts and the hub was asked for {'rotation_deg': 30.0}
    """
    t = _target(center_tolerance_arcmin=0.5, center_attempts=5,
                rotation_deg=30.0)
    e, hub = _guided(t)
    await recentre(e, t)
    assert len(hub.gotos) == 1, f"premise: one re-centre: {hub.gotos}"
    args, kw = hub.gotos[0]
    assert args == (t.ra_hours, t.dec_deg), f"positional args moved: {args}"
    label = {_after_the_sweep: "after the unguided sweep",
             _after_a_lost_star: "after a lost star",
             _after_a_walking_field: "after a walking field"}[recentre]
    assert kw == {"rotation_deg": 30.0,
                  "tolerance_deg": pytest.approx(0.5 / 60),
                  "max_attempts": 5}, (
        f"{label}, the target said 0.5 arcmin and 5 attempts and the hub was "
        f"asked for {kw}")


@_RECENTRES
@pytest.mark.parametrize("rotation", [None, 30.0], ids=["no angle", "PA 30"])
async def test_control_an_unset_target_re_centres_with_todays_exact_call(
        recentre, rotation):
    """CONTROL. Unset, each re-centre makes the call it has always made:
    (ra, dec) and ``rotation_deg`` as the only keyword.

    Mutant "always pass the hub defaults" (the helper always returns
    ``tolerance_deg=0.02, max_attempts=3``): RED, all six cases (and the
    three above) -
        AssertionError: an unset target changed the re-centre: args
        (22.6182, 34.4098), keywords {'rotation_deg': 30.0,
        'tolerance_deg': 0.02, 'max_attempts': 3}
    (``'rotation_deg': None`` in the no-angle cases.)
    """
    t = _target(rotation_deg=rotation)
    e, hub = _guided(t)
    await recentre(e, t)
    assert len(hub.gotos) == 1, f"premise: one re-centre: {hub.gotos}"
    args, kw = hub.gotos[0]
    assert args == (t.ra_hours, t.dec_deg) and kw == {"rotation_deg": rotation}, (
        f"an unset target changed the re-centre: args {args}, keywords {kw}")


# ---------------------------------------------------------------- #160


def _angle_warnings(lines) -> list[str]:
    return [m for lvl, m, _src in lines
            if lvl == "warning" and "PA" in m and "angle" in m]


def _any_rotation_warning(lines) -> list[str]:
    """The controls' net, wider than `_angle_warnings`: ANY warning about
    rotation, whatever its wording. A control that only recognises today's
    sentence passes a reworded warning on the wrong branch (verifier's mutant
    "warn on both branches", a warning reading "rotation to PA 30 was asked
    for, but not applied" hoisted above the branch split, stayed green
    against the `_angle_warnings` filter)."""
    return [m for lvl, m, _src in lines
            if lvl == "warning" and ("rotat" in m.lower() or "PA " in m)]


async def test_a_slew_without_centring_says_the_angle_was_not_applied(bus_lines):
    """`center` off and an angle planned: the slew cannot turn the camera, and
    one warning says so in words.

    Mutant "no warning" (the new warning in the non-centred branch deleted):
    RED -
        AssertionError: a planned angle was dropped in silence: []
    """
    t = _target(center=False, rotation_deg=30.0)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    assert ("slew", t.ra_hours, t.dec_deg) in hub.tel.calls, (
        f"premise: the non-centred branch slewed: {hub.tel.calls}")
    assert hub.gotos == [], f"premise: no centring call: {hub.gotos}"
    said = _angle_warnings(bus_lines)
    assert said, "a planned angle was dropped in silence: []"
    assert len(said) == 1, f"one warning per setup, got {len(said)}: {said}"
    assert "PA 30" in said[0] and "not" in said[0], (
        f"the warning must name the angle and say it was not applied: "
        f"{said[0]!r}")


async def test_control_no_angle_no_warning(bus_lines):
    """CONTROL. No angle planned: nothing was dropped, nothing is said.

    Mutant "warn whatever the angle" (the ``rotation_deg is not None`` test
    dropped): RED, and not quietly - formatting the absent angle kills the
    setup before any assertion is reached:
        TypeError: unsupported format string passed to
        NoneType.__format__
    """
    t = _target(center=False, rotation_deg=None)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    assert ("slew", t.ra_hours, t.dec_deg) in hub.tel.calls, "premise"
    assert _any_rotation_warning(bus_lines) == [], (
        f"a slew with no planned angle warned about one: "
        f"{_any_rotation_warning(bus_lines)}")


async def test_control_the_centred_branch_does_not_repeat_the_hubs_word(
        bus_lines):
    """CONTROL. The centred branch hands the angle to the hub, which reports
    for itself (S1-03's ``rotation_unavailable``), so the engine adds nothing.

    Mutant "real warning on both branches" (the #160 warning, ungated on
    ``center``, hoisted above the branch split): RED -
        AssertionError: ['NGC 7331: rotation to PA 30 was asked for, but
        only a centred slew turns the rotator and this target is not centred;
        slewing without rotating, so the frame keeps whatever angle the
        camera is at']
    Mutant "warn on both branches" (a REWORDED warning, "rotation to PA 30
    was asked for, but not applied", hoisted above the split for centred
    targets): RED -
        AssertionError: ['NGC 7331: rotation to PA 30 was asked for, but not
        applied']
    """
    t = _target(center=True, rotation_deg=30.0)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    assert hub.gotos, "premise: the centred branch ran"
    assert _any_rotation_warning(bus_lines) == [], (
        _any_rotation_warning(bus_lines))
