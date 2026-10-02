# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The start guard refuses a mosaic group only when every panel is blocked,
and names the blocked panels (spec 6.3; #132; mosaic slice S2 task T17).

A mosaic's panels span the sky between them, so some can be below the
horizon while the rest are up. The start pre-flight refused the whole start
for the first target behind the horizon, a panel included, so a group with
one panel down could only be started with ``force``, and ``force`` waives
the check for every other target in the plan as well. Now:

* A member of a group (a non-calibration target whose ``mosaic_group``
  names a group the plan carries, as ``SequenceEngine._group_of`` reads it)
  counts toward its group. The group refuses, unless forced, only when every
  one of its panels is below the horizon now, and the 409 lists them.
  Otherwise the start goes ahead, and the panels below the horizon are named
  in the response (``below_horizon``) and, in words, in the log.
* Every other target, a classic Plan mosaic with no ``groups`` entry
  included, refuses alone exactly as before.
* The Sun is unchanged: any panel in the cone refuses, forced or not.

One helper does it for both start paths that run the pre-flight,
``POST /api/sequence/start`` and ``POST /api/flows/{id}/run``, and every
case here runs through both.

THE HARNESS is test_flows_continue.py's ``rig`` (the real app over ASGI on
the test's own loop, a real ``SequenceEngine`` whose imaging loop alone is
replaced, the Sun check stubbed off unless a case turns it on). The flow
route's compile is replaced (``app_module.to_sequence_plan``) to return the
case's plan, as test_continue_skipped_panels.py does, since no compile
emits ``groups`` before S3.

THE SKY is real: the hub's own ``_check_horizon`` at a FIXTURE site, the
same made-up "Backyard" test_app_preflight.py uses, never the rig's site.
Panels near the poles make the answer independent of the time of day: at
51.5 degrees north, a declination of +88 or more never sets (altitude at
least 49.5) and one of -88 or less never rises (altitude at most -49.5),
and each case asserts that premise from ``altaz`` before it starts.

MUTATIONS. Each was applied to a byte-for-byte copy of ``api/app.py`` in a
private copy of ``server/`` under the session scratchpad; only this file was
run there, and the copy was restored and SHA-256 compared after every
mutant. The shared tree was never written. Failures are quoted from
``--tb=short``.
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
from astrodeck.catalog import altaz
from astrodeck.config import Site
from astrodeck.devices.base import DeviceError
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from test_flows_continue import LR, rig  # noqa: F401 (fixture)

#: A made-up site (test_app_preflight.py's), never the rig's.
FIXTURE_SITE = Site(name="Backyard", latitude=51.5, longitude=-0.1)
#: Declinations that never set and never rise at FIXTURE_SITE.
UP_DEC, DOWN_DEC = 88.0, -88.0

GROUP, MOSAIC = "g-guard", "Guard mosaic"
OTHER_GROUP, OTHER_MOSAIC = "g-other", "Other mosaic"
ALL_FOUR = {(0, 0), (0, 1), (1, 0), (1, 1)}

ROUTES = ("plan", "flow")


# ------------------------------------------------------------------ plans

def _step() -> ExposureStep:
    return ExposureStep(filter="L", exposure_s=1.0, count=1)


def _panel(row: int, col: int, *, down: bool, group: str = GROUP,
           mosaic: str = MOSAIC, ra0: float = 1.0) -> Target:
    """Panel ``row``, ``col`` (0-based) of a 2x2, up or down at the fixture
    site. The name is the Plan's ``<name> r-c``."""
    dec = (DOWN_DEC - 0.2 * row) if down else (UP_DEC + 0.2 * row)
    return Target(name=f"{mosaic} {row + 1}-{col + 1}",
                  ra_hours=ra0 + 0.1 * col, dec_deg=dec, mosaic_group=group,
                  panel_row=row, panel_col=col, steps=[_step()])


def _grid(down: set, **kw) -> list[Target]:
    return [_panel(r, c, down=(r, c) in down, **kw)
            for r in range(2) for c in range(2)]


def _plan(targets: list[Target], groups: list[TargetGroup]) -> SequencePlan:
    return SequencePlan(name="guard", guide=False, dither_every=0,
                        meridian_flip=False, targets=targets, groups=groups)


def _mosaic(down: set) -> SequencePlan:
    """A 2x2 in one group the plan carries, with the panels in ``down``
    below the horizon."""
    return _plan(_grid(down), [TargetGroup(id=GROUP, name=MOSAIC)])


def _single(name: str, *, down: bool) -> Target:
    return Target(name=name, ra_hours=3.0, dec_deg=DOWN_DEC if down else UP_DEC,
                  steps=[_step()])


def _entry(row: int, col: int) -> dict:
    return {"group": GROUP, "mosaic": MOSAIC, "panel": f"{row + 1}-{col + 1}",
            "target": f"{MOSAIC} {row + 1}-{col + 1}"}


# ------------------------------------------------------------------ harness

@pytest.fixture
def sky(rig, monkeypatch):
    """The rig at the fixture site, and a replaced flow compile that
    returns whatever plan the case sets on ``compile.plan``."""
    rig.store.set_site(FIXTURE_SITE, expected_version=None)
    assert app_module.hub.site["is_default"] is False, "premise: a real site"

    class _Compile:
        plan: SequencePlan | None = None
        calls = 0

        def __call__(self, compiled, graph, **_kw):
            self.calls += 1
            assert self.plan is not None, "the case set no plan"
            return self.plan.model_copy(deep=True), []

    fake = _Compile()
    monkeypatch.setattr(app_module, "to_sequence_plan", fake)
    rig.compile = fake
    return rig


def _premise(plan: SequencePlan, down_names: set[str]) -> None:
    """Every light target is where the case says, at the fixture site, now."""
    for t in plan.targets:
        alt, _az = altaz(t.ra_hours, t.dec_deg, FIXTURE_SITE.latitude,
                         FIXTURE_SITE.longitude)
        if t.name in down_names:
            assert alt < -40, f"premise: {t.name} is down, alt {alt:.1f}"
        else:
            assert alt > 40, f"premise: {t.name} is up, alt {alt:.1f}"


async def _start(rig, route: str, plan: SequencePlan, *, force: bool = False):
    if route == "plan":
        return await rig.client.post(
            "/api/sequence/start",
            json={**plan.model_dump(mode="json"), "force": force})
    rig.compile.plan = plan
    fid = await rig.save_flow(LR)
    r = await rig.client.post(f"/api/flows/{fid}/run", json={"force": force})
    assert rig.compile.calls >= 1, "premise: the route compiled through the fake"
    return r


def _horizon_lines(bus_lines) -> list[tuple[str, str, str]]:
    return [line for line in bus_lines if "below the horizon" in line[1]]


def _won(rig) -> int:
    return sum(1 for s in rig.starts if s.won)


# ============================================================== the cases

@pytest.mark.parametrize("route", ROUTES)
async def test_one_panel_behind_the_horizon_starts_and_is_named(
        sky, bus_lines, route):
    """A 2x2 in a group, panel 1-2 below the horizon. The start goes ahead,
    the response names the panel, and one log line says it in words: the
    panel's label and the mosaic's name, no altitude.

    RED under mutant "any blocked panel refuses" (today's loop: the helper
    refuses on the first target behind the horizon, a member or not; its
    ``if g is None:`` made ``if True:``), both routes, observed verbatim
    (the console printed the degree sign as a replacement character; it is
    written here as the route sent it):

        [plan]
        E   AssertionError: {"detail":{"detail":"target is below the visible horizon (alt -51°)","code":"below_horizon","target":"Guard mosaic 1-2"}}
        E   assert 409 == 200
        [flow] the same two lines

    RED under mutant "run_flow keeps today's loop" (``run_flow``'s call to
    the helper replaced by the two loops it had), ``[flow]`` only, the same
    two lines.

    RED under mutant "the log carries the hub's detail" (the line appends
    the panel's 409 detail, which carries its altitude), both routes:

        E     At index 0 diff: ('info', "mosaic 'Guard mosaic': panel 1-2 is below the horizon now (target is below the visible horizon (alt -51°)); started, since 3 of its 4 panels are not", 'sequence') != ('info', "mosaic 'Guard mosaic': panel 1-2 is below the horizon now; started, since 3 of its 4 panels are not", 'sequence')

    RED under mutant "the response does not name them" (the
    ``below_horizon`` key never added, both routes' ``if below_horizon:``
    made ``if False:``), both routes:

        E   KeyError: 'below_horizon'
    """
    plan = _mosaic({(0, 1)})
    _premise(plan, {f"{MOSAIC} 1-2"})
    r = await _start(sky, route, plan)
    assert r.status_code == 200, r.text
    assert r.json()["below_horizon"] == [_entry(0, 1)], r.json()
    assert _won(sky) == 1, "the route answered 200 but nothing started"
    assert _horizon_lines(bus_lines) == [(
        "info",
        f"mosaic '{MOSAIC}': panel 1-2 is below the horizon now; started, "
        f"since 3 of its 4 panels are not", "sequence")]


@pytest.mark.parametrize("route", ROUTES)
async def test_every_panel_behind_the_horizon_refuses_and_lists_them(
        sky, bus_lines, route):
    """All four panels below the horizon: 409 ``below_horizon``, the detail
    in words naming every panel, and the panels listed. Nothing starts and
    nothing is logged: a refused start says nothing.

    RED under mutant "a group never refuses" (the every-panel refusal's
    ``if all(...)`` made ``if False:``), both routes, observed verbatim:

        [plan]
        E   AssertionError: {"started":true,"frames":4,"below_horizon":[{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 1-1","panel":"1-1"},{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 1-2","panel":"1-2"},{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 2-1","panel":"2-1"},{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 2-2","panel":"2-2"}]}
        E   assert 200 == 409
        [flow] the same two lines, the body carrying the flow route's keys
        (flow_id, unmapped, session) ahead of the same below_horizon

    RED under mutant "any blocked panel refuses", both routes, and under
    "run_flow keeps today's loop", ``[flow]``: the first panel refuses with
    today's single-target 409, observed verbatim (degree sign as sent):

        E   AssertionError: {'detail': {'code': 'below_horizon', 'detail': 'target is below the visible horizon (alt -51°)', 'target': 'Guard mosaic 1-1'}}
    """
    plan = _mosaic(ALL_FOUR)
    _premise(plan, {t.name for t in plan.targets})
    r = await _start(sky, route, plan)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "detail": f"every panel of mosaic '{MOSAIC}' is below the horizon "
                  f"now: 1-1, 1-2, 2-1, 2-2",
        "code": "below_horizon", "group": GROUP, "mosaic": MOSAIC,
        "panels": [{"panel": e["panel"], "target": e["target"]}
                   for e in (_entry(0, 0), _entry(0, 1), _entry(1, 0),
                             _entry(1, 1))]}, r.json()
    assert _won(sky) == 0
    assert _horizon_lines(bus_lines) == []


@pytest.mark.parametrize("route", ROUTES)
async def test_forced_every_panel_behind_the_horizon_starts_and_names_them(
        sky, bus_lines, route):
    """The same four panels, forced: it starts, as a forced start always
    has, and still names every panel in the response and the log, so the
    operator who forced it is told what they forced.

    RED under mutant "force looks at no panel" (under ``force`` the helper
    skips the horizon for members too, as it does for every other target:
    ``if force and group_of(t) is None:`` made ``if force:``), both routes,
    and under "the response does not name them", both routes, and "run_flow
    keeps today's loop", ``[flow]``, observed verbatim:

        E   KeyError: 'below_horizon'
    """
    plan = _mosaic(ALL_FOUR)
    _premise(plan, {t.name for t in plan.targets})
    r = await _start(sky, route, plan, force=True)
    assert r.status_code == 200, r.text
    assert r.json()["below_horizon"] == [
        _entry(0, 0), _entry(0, 1), _entry(1, 0), _entry(1, 1)], r.json()
    assert _won(sky) == 1
    assert _horizon_lines(bus_lines) == [(
        "info",
        f"mosaic '{MOSAIC}': all 4 panels are below the horizon now (1-1, "
        f"1-2, 2-1, 2-2); started because the start was forced",
        "sequence")]


def _no_group_plans() -> dict[str, tuple[SequencePlan, str]]:
    """Plans with one target behind the horizon that is NOT a member, and
    that target's name. The 409 for each is today's."""
    classic = _plan(_grid({(0, 1)}), [])            # mosaic_group, no groups entry
    beside = _plan(_grid({(0, 1)}) + [_single("Lone down", down=True)],
                   [TargetGroup(id=GROUP, name=MOSAIC)])
    return {
        "no mosaic": (_plan([_single("Lone up", down=False),
                             _single("Lone down", down=True)], []),
                      "Lone down"),
        "classic mosaic": (classic, f"{MOSAIC} 1-2"),
        "beside a group": (beside, "Lone down"),
    }


@pytest.mark.parametrize("variant", ["no mosaic", "classic mosaic",
                                     "beside a group"])
@pytest.mark.parametrize("route", ROUTES)
async def test_control_a_target_outside_a_group_still_refuses_alone(
        sky, bus_lines, route, variant):
    """CONTROL: a target that is not a member of a group the plan carries
    refuses the start on its own, with today's 409 (the hub's sentence,
    ``below_horizon``, the target's name), and a forced start waives it
    without a look, so its response names nothing. ``no mosaic``: two
    single targets. ``classic mosaic``: the same 2x2 with ``mosaic_group``
    set and no ``groups`` entry, which is every mosaic the classic Plan
    made. ``beside a group``: a single target behind the horizon after a
    group with one panel down, which alone would start.

    ``no mosaic`` is green on the code and under every mutant in this file,
    and every variant is green under "a group never refuses", "force skips
    the sun", "the sun skips members" and "the log carries the hub's
    detail".

    RED under mutant "a classic mosaic counts as a group" (membership read
    from ``mosaic_group`` alone, not from ``plan.groups``: ``group_of``
    makes a nameless group for any ``mosaic_group``), ``classic mosaic`` on
    both routes, observed verbatim:

        [plan]
        E   AssertionError: {"started":true,"frames":4,"below_horizon":[{"group":"g-guard","mosaic":"g-guard","target":"Guard mosaic 1-2","panel":"1-2"}]}
        E   assert 200 == 409
        [flow] the same two lines, with the flow route's keys

    RED under mutant "any blocked panel refuses", ``beside a group`` on
    both routes, and under "run_flow keeps today's loop", ``[flow-beside a
    group]``: the group's panel 1-2 comes first in the plan and refuses in
    the single target's place, observed verbatim:

        E   AssertionError: assert 'Guard mosaic 1-2' == 'Lone down'

    ``beside a group``'s forced half is RED under "force looks at no panel"
    and "the response does not name them", both routes, observed verbatim:

        E   KeyError: 'below_horizon'
    """
    plan, blocked_name = _no_group_plans()[variant]
    _premise(plan, {blocked_name} | ({f"{MOSAIC} 1-2"}
                                     if variant != "no mosaic" else set()))
    r = await _start(sky, route, plan)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert set(detail) == {"detail", "code", "target"}, detail
    assert detail["code"] == "below_horizon"
    assert detail["target"] == blocked_name
    assert detail["detail"].startswith("target is below the visible horizon")
    assert _won(sky) == 0

    forced = await _start(sky, route, plan, force=True)
    assert forced.status_code == 200, forced.text
    if variant == "beside a group":
        # The group's own panel is still named: a forced start looks at its
        # members, and at nothing else.
        assert forced.json()["below_horizon"] == [_entry(0, 1)]
    else:
        assert "below_horizon" not in forced.json(), forced.json()


def _sun_on(monkeypatch, target: Target) -> None:
    """Put ``target``'s coordinates in the Sun's exclusion cone, and nothing
    else: the hub's ``_check_solar`` raises for them, as it raises inside
    the cone."""
    def check(ra, dec, *, force=False):
        if (ra, dec) == (target.ra_hours, target.dec_deg):
            raise DeviceError("target is within 12 deg of the Sun "
                              "(exclusion 30 deg) - enable a solar session "
                              "(config.solar_override) to override")
    monkeypatch.setattr(app_module.hub, "_check_solar", check)


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("route", ROUTES)
async def test_the_sun_refuses_a_panel_forced_or_not(
        sky, bus_lines, monkeypatch, route, force):
    """A group that would start (one panel behind the horizon) with another
    panel, one that is up, inside the Sun's cone: 409 ``sun_exclusion``
    naming that panel, forced or not, and nothing starts.

    RED under mutant "force skips the sun" (the Sun loop run over nothing
    when forced), ``[plan-True]`` and ``[flow-True]``, observed verbatim:

        [plan-True]
        E   AssertionError: {"started":true,"frames":4,"below_horizon":[{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 1-2","panel":"1-2"}]}
        E   assert 200 == 409
        [flow-True] the same two lines, with the flow route's keys

    RED under mutant "the sun skips members" (members left out of the Sun
    loop), all four cases, the same two lines.

    RED under mutant "any blocked panel refuses", ``[plan-False]`` and
    ``[flow-False]``, and "run_flow keeps today's loop", ``[flow-False]``:
    panel 1-2 refuses on the horizon before the Sun is asked, observed
    verbatim (degree sign as sent):

        E   AssertionError: {'code': 'below_horizon', 'detail': 'target is below the visible horizon (alt -51°)', 'target': 'Guard mosaic 1-2'}
        E   assert 'below_horizon' == 'sun_exclusion'
    """
    plan = _mosaic({(0, 1)})
    in_cone = plan.targets[2]                  # panel 2-1, which is up
    _premise(plan, {f"{MOSAIC} 1-2"})
    _sun_on(monkeypatch, in_cone)
    r = await _start(sky, route, plan, force=force)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "sun_exclusion", detail
    assert detail["target"] == f"{MOSAIC} 2-1", detail
    assert _won(sky) == 0
    assert _horizon_lines(bus_lines) == []


@pytest.mark.parametrize("route", ROUTES)
async def test_a_start_the_engine_refuses_names_nothing(
        sky, bus_lines, route):
    """A group that passes the pre-flight (one panel behind the horizon) but
    whose start the ENGINE then refuses, because a run is already live: the
    route answers 409 with the engine's words, and neither the response nor
    the log names the panel. The log line says "started"; a start refused
    after the pre-flight must not say it, so the panels are named only once
    ``engine.start`` has returned.

    The run already live is a single target that is up, started through
    ``/api/sequence/start``; it logs no horizon line of its own.

    RED under mutant "the panels are named before the start" (the
    ``_name_panels_below`` call moved from after ``engine.start`` to just
    below ``_start_preflight``), applied to sequence_start alone it fails
    ``[plan]``, and to run_flow alone ``[flow]``, observed verbatim (the
    rest of the case green under both):

        [plan]
        E   AssertionError: a refused start logged its panels: [('info', "mosaic 'Guard mosaic': panel 1-2 is below the horizon now; started, since 3 of its 4 panels are not", 'sequence')]
        E   assert [('info', "mo..., 'sequence')] == []
        [flow] the same two lines

    RED under mutant "any blocked panel refuses", both routes: the panel
    refuses on the horizon before the engine is asked, observed verbatim
    (degree sign as sent):

        E     {'detail': {'code': 'below_horizon', 'detail': 'target is below the visible horizon (alt -51°)', 'target': 'Guard mosaic 1-2'}} != {'detail': 'a sequence is already running'}
    """
    first = _plan([_single("Lone up", down=False)], [])
    _premise(first, set())
    r0 = await _start(sky, "plan", first)
    assert r0.status_code == 200, r0.text
    assert sky.engine.running, "premise: a run is live"
    plan = _mosaic({(0, 1)})
    _premise(plan, {f"{MOSAIC} 1-2"})
    r = await _start(sky, route, plan)
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": "a sequence is already running"}, r.json()
    assert _won(sky) == 1, "premise: only the first start went ahead"
    lines = _horizon_lines(bus_lines)
    assert lines == [], f"a refused start logged its panels: {lines}"


@pytest.mark.parametrize("route", ROUTES)
async def test_a_target_below_the_horizon_and_in_the_sun_refuses_on_the_horizon(
        sky, bus_lines, monkeypatch, route):
    """A single target both behind the horizon and in the Sun's cone.
    Unforced, it refuses on the horizon, as the two loops the helper replaced
    did (every horizon refusal before any Sun refusal); forced, the horizon
    is waived and the Sun refuses. Pins the helper's ordering claim.

    RED under mutant "the Sun first" (the helper's Sun loop moved above its
    horizon checks), both routes, the unforced half, observed verbatim:

        [plan]
        E   AssertionError: {'code': 'sun_exclusion', 'detail': 'target is within 12 deg of the Sun (exclusion 30 deg) - enable a solar session (config.solar_override) to override', 'target': 'Lone down'}
        E   assert 'sun_exclusion' == 'below_horizon'
        [flow] the same two lines

    RED under mutant "force skips the sun", both routes, the forced half,
    observed verbatim:

        [plan]
        E   AssertionError: {"started":true,"frames":1}
        [flow] the same, with the flow route's keys

    Green under "any blocked panel refuses": the target is in no group.
    """
    down = _single("Lone down", down=True)
    plan = _plan([down], [])
    _premise(plan, {"Lone down"})
    _sun_on(monkeypatch, down)
    r = await _start(sky, route, plan)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "below_horizon", detail
    forced = await _start(sky, route, plan, force=True)
    assert forced.status_code == 409, forced.text
    assert forced.json()["detail"]["code"] == "sun_exclusion", forced.json()
    assert forced.json()["detail"]["target"] == "Lone down"
    assert _won(sky) == 0


async def test_a_wholly_blocked_group_refuses_beside_one_that_is_up(
        sky, bus_lines):
    """Two groups in one plan: the first wholly behind the horizon, the
    second wholly up. The first refuses the start, as a single target
    behind the horizon refuses it beside others that are up: the rule is
    per group, never per plan.

    RED under mutant "a group never refuses", observed verbatim:

        E   AssertionError: {"started":true,"frames":8,"below_horizon":[{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 1-1","panel":"1-1"},{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 1-2","panel":"1-2"},{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 2-1","panel":"2-1"},{"group":"g-guard","mosaic":"Guard mosaic","target":"Guard mosaic 2-2","panel":"2-2"}]}
        E   assert 200 == 409

    RED under mutant "any blocked panel refuses": panel 1-1 refuses alone,
    with today's 409, which names no group, observed verbatim:

        E   KeyError: 'group'
    """
    plan = _plan(_grid(ALL_FOUR)
                 + _grid(set(), group=OTHER_GROUP, mosaic=OTHER_MOSAIC,
                         ra0=5.0),
                 [TargetGroup(id=GROUP, name=MOSAIC),
                  TargetGroup(id=OTHER_GROUP, name=OTHER_MOSAIC)])
    _premise(plan, {t.name for t in plan.targets
                    if t.mosaic_group == GROUP})
    r = await _start(sky, "plan", plan)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert (detail["code"], detail["group"]) == ("below_horizon", GROUP), detail
    assert _won(sky) == 0
