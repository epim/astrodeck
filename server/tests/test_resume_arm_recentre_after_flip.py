# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Auto-resume re-centres a flipped mosaic on a panel past the meridian (#312,
S3 orchestrator ruling 4; spec 5.7, 5.9).

WHAT WAS WRONG. ``recentre_candidates`` orders a group's live panels as the
run's panel order does (least complete first, spec 5.2), and knew nothing of
the group's pier state. After the group's one pier change the run shoots
only its panels past the meridian (5.7), and since #312 a restart tonight
reads that state back from ``Session.group_pier``; but the ladder, the step
before that run starts, still re-centred on the least complete panel, which
after a flip is as likely as not one before the meridian. Its goto lands the
mount on the side the group left, and the run's first hop then crosses the
pier again: two slews across the pier for no frame.

WHAT IT DOES NOW. With the site set and the ladder's clock given, a group
whose record for tonight's night key says it is flipped has its panels past
the meridian first, each part in the panel order (`_past_meridian_first`),
past the meridian as the run counts it: ``MERIDIAN_SIDE_MARGIN_S`` past the
crossing. With the site unset nothing is ordered by the meridian (#121: an
hour angle at the 0,0 default is somewhere else's), and another night's
record is history.

THE CASES. A 1x3 mosaic at Dec 40 at the fixture site (40 N 74 W, not
anybody's rig): at the clock, 1-1 stands 0.5 h east of the meridian, 1-2 is
5 s past its crossing (inside the band), and 1-3 is 0.5 h past it. 1-1 has
banked nothing, 1-2 one frame and 1-3 two, so the panel order is 1-1, 1-2,
1-3. The pure cases call ``recentre_candidates``; one case runs the real
``ResumeArm._recover`` on the simulator hub (``_simhub.sim_hub``) with its
blind solve and re-centring goto recorded in place, focus trusted, and the
slew-limit gate passing (the real one reads the wall clock), and grades the
goto it makes.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim. Every mutant was applied to ``astrodeck/sequence/resume_arm.py`` in
a private scratch copy of server/ (scratchpad s3-ec-mut-q7v2), never in the
shared tree (#254):

* "ignore the record": `recentre_candidates` without its
  ``flipped_tonight`` branch, the code before #312.
* "no band": `_past_meridian_first` counting a panel past the meridian at
  its crossing (``<= 0.0``) rather than ``MERIDIAN_SIDE_MARGIN_S`` past it.
* "the site not asked": the branch's ``gate and`` replaced by ``now is not
  None and``, so an unset site orders by the meridian too.
* "read regardless of night" (in ``session.py``): `Session.group_pier_on`
  returning the group's record whatever night it names.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.events import night_key
from astrodeck.sequence import SequenceEngine, schedule
from astrodeck.sequence.engine import MERIDIAN_SIDE_MARGIN_S
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.resume_arm import ResumeArm, recentre_candidates
from astrodeck.sequence.session import Session, SessionFrame

LAT, LON = 40.0, -74.0
SITE = {"name": "fixture", "latitude": LAT, "longitude": LON,
        "elevation_m": 10.0, "is_default": False}
#: Where the clock search starts; any instant would do.
T_SEARCH = 1_700_000_000.0
TWILIGHT = -12.0
GROUP = "g-m31"
#: Each panel's hour angle at the clock, in hours: 1-1 before the meridian,
#: 1-2 five seconds past its crossing (inside the band), 1-3 well past it.
HOUR_ANGLES = {"1-1": -0.5, "1-2": 5.0 / 3600.0, "1-3": 0.5}


def _dark_clock() -> float:
    """A clock reading inside the night at the fixture site, searched."""
    for i in range(3 * 24 * 12):
        t = T_SEARCH + i * 300.0
        if all(schedule.dark_enough(SITE, TWILIGHT, t + h * 3600.0)
               for h in range(3)):
            return t
    raise AssertionError("no dark clock reading in three days")


def _session(t: float) -> Session:
    """The 1x3 of the module docstring at clock ``t``, dormant, with 0, 1
    and 2 frames banked on 1-1, 1-2 and 1-3."""
    lst = schedule.hour_angle_h(0.0, LON, t) % 24.0
    group = TargetGroup(id=GROUP, name="M31 mosaic", order="least_complete",
                        geometry={"rows": 1, "cols": 3})
    targets, frames = [], []
    for c, (label, ha) in enumerate(HOUR_ANGLES.items()):
        tid = f"p-{label}"
        targets.append(Target(
            id=tid, name=f"M31 {label}", ra_hours=(lst - ha) % 24.0,
            dec_deg=40.0, center=False, autofocus_first=False,
            mosaic_group=GROUP, panel_row=0, panel_col=c,
            steps=[ExposureStep(id=f"s-{tid}", filter="L", exposure_s=0.05,
                                count=5)]))
        frames += [SessionFrame(ts=t - 3600.0, night="n1", target_id=tid,
                                step_id=f"s-{tid}") for _ in range(c)]
    plan = SequencePlan(name="flipped", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=True,
                        targets=targets, groups=[group])
    return Session(id="s-flipped", name="flipped", status="dormant",
                   plan=plan, frames=frames, auto_resume=True)


def _flipped(session: Session, night: str) -> Session:
    session.note_group_pier(GROUP, night=night, flipped=True, side="east",
                            verified=True)
    return session


def _labels(targets) -> list[str]:
    return [t.name.split(" ", 1)[1] for t in targets]


def _premise(session: Session, t: float) -> None:
    """Where each panel stands, computed: 1-1 before the meridian, 1-2
    inside the band past its crossing, 1-3 past the band."""
    h = {t_.name.split(" ", 1)[1]: schedule.hours_to_meridian_flip(
        t_.ra_hours, LON, t) * 3600.0 for t_ in session.plan.targets}
    band = MERIDIAN_SIDE_MARGIN_S
    assert h["1-1"] > 0.0 and -band < h["1-2"] <= 0.0 \
        and h["1-3"] <= -band, h


# --------------------------------------------------------- the pure order

def test_a_flipped_group_offers_its_panels_past_the_meridian_first():
    """Flipped tonight, with the site set: 1-3, past the band, comes first;
    1-1, before the meridian, and 1-2, still inside the band where the run
    too makes it wait, follow in the panel order. Without the record the
    order is the panel order, as before #312.

    MUTANT "ignore the record": RED (observed):
        AssertionError: assert ['1-1', '1-2', '1-3'] == ['1-3', '1-1', '1-2']
          At index 0 diff: '1-1' != '1-3'
    MUTANT "no band": RED (observed):
        AssertionError: assert ['1-2', '1-3', '1-1'] == ['1-3', '1-1', '1-2']
          At index 0 diff: '1-2' != '1-3'
    """
    t = _dark_clock()
    tonight = night_key(t)
    plain = _session(t)
    _premise(plain, t)
    kw = {"site": SITE, "twilight_deg": TWILIGHT, "now": t}
    assert _labels(recentre_candidates(plain, tonight, **kw)) == [
        "1-1", "1-2", "1-3"], "premise: the panel order"
    flipped = _flipped(_session(t), tonight)
    assert _labels(recentre_candidates(flipped, tonight, **kw)) == [
        "1-3", "1-1", "1-2"]


@pytest.mark.parametrize("case", ["site unset", "another night"])
def test_without_a_site_or_tonights_record_the_order_is_todays(case):
    """CONTROLS. The site unset (the same coordinates, but ``is_default``
    still true, #121): nothing is ordered by the meridian, whatever the
    record says. A record from another night: history, so the group is not
    flipped tonight. Both give the panel order.

    MUTANT "the site not asked": RED on "site unset" only (observed):
        AssertionError: assert ['1-3', '1-1', '1-2'] == ['1-1', '1-2', '1-3']
          At index 0 diff: '1-3' != '1-1'
    MUTANT "read regardless of night": RED on "another night" only
    (observed):
        AssertionError: assert ['1-3', '1-1', '1-2'] == ['1-1', '1-2', '1-3']
          At index 0 diff: '1-3' != '1-1'
    """
    t = _dark_clock()
    tonight = night_key(t)
    site = dict(SITE, is_default=True) if case == "site unset" else SITE
    written = tonight if case == "site unset" else night_key(t - 86400.0)
    assert written != tonight or case == "site unset", "premise"
    session = _flipped(_session(t), written)
    assert _labels(recentre_candidates(session, tonight, site=site,
                                       twilight_deg=TWILIGHT, now=t)) == [
        "1-1", "1-2", "1-3"]


# ------------------------------------------------- the ladder's own goto

@pytest.fixture
def rig(sim_hub, monkeypatch):
    """The simulator hub with the ladder's blind solve and re-centring goto
    recorded in place (``gotos`` holds each goto as ``(args, kwargs)``),
    focus trusted, and a real engine whose slew-limit gate passes."""
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))
    gotos: list[tuple[tuple, dict]] = []

    async def goto(*args, **kwargs):
        gotos.append((args, kwargs))
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1,
                "rotation": None}

    async def solve(*args, **kwargs):
        return {}

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve)
    engine = SequenceEngine(sim_hub)

    async def check_slew_limits(target, *, cfg=None, plan=None,
                                projected=True):
        return None

    monkeypatch.setattr(engine, "check_slew_limits", check_slew_limits)
    return SimpleNamespace(hub=sim_hub, gotos=gotos,
                           arm=lambda t: ResumeArm(engine, sim_hub,
                                                   clock=lambda: t))


@pytest.mark.parametrize("flipped", [True, False], ids=["flipped", "control"])
async def test_the_ladder_recentres_on_the_side_the_group_is_on(rig, flipped):
    """The real ladder, on the simulator hub, at the fixture site: flipped
    tonight, it re-centres on 1-3, past the meridian, where the run will
    shoot first; the control, with no record, re-centres on 1-1, the least
    complete panel, as before #312.

    MUTANT "ignore the record": RED on "flipped" (observed):
        AssertionError: the ladder re-centred on 1-1
        assert '1-1' == '1-3'
    MUTANT "no band": RED on "flipped" (observed; 1-2, five seconds past
    its crossing, where the side a goto lands on is still in doubt):
        AssertionError: the ladder re-centred on 1-2
        assert '1-2' == '1-3'
    """
    assert rig.hub.site["is_default"] is False, "premise: the site is set"
    assert rig.hub.site["longitude"] == LON, "premise: the fixture site"
    t = _dark_clock()
    session = _session(t)
    _premise(session, t)
    if flipped:
        _flipped(session, night_key(t))
    why = await rig.arm(t)._recover(session)
    assert why is None, why
    (args, _kw), = rig.gotos
    where = next(p.name.split(" ", 1)[1] for p in session.plan.targets
                 if (p.ra_hours, p.dec_deg) == args[:2])
    expected = "1-3" if flipped else "1-1"
    assert where == expected, f"the ladder re-centred on {where}"
