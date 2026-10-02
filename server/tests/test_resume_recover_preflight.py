# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Resume and recover run the start's horizon and Sun pre-flight (#291; mosaic
slice S3 task S3-H; spec 6.3, 5.9 "every existing guard still applies", 3.5).

``POST /api/sessions/{id}/resume`` and ``POST /api/sequence/recover`` start the
engine on a dormant session's stored plan, and ran neither of the start's sky
checks. The same plan through ``/api/sequence/start`` or
``/api/flows/{id}/run`` was refused for a target in the Sun's cone, and the
resume of it was not: a resume was the door around the one check that costs a
sensor rather than a night. The stored plan was last checked when it first
started, which can be nights ago, so the sky it was checked against is gone.

Now both routes run ``_start_preflight`` after their identity and quota guards
and before ``engine.start``, over the targets of the session's plan that still
owe frames (``Session.remaining``, which counts as the frozen plan's
``count_mode`` does):

* The Sun refuses, forced or not: 409 ``sun_exclusion``.
* A target outside a mosaic group below the horizon refuses unless forced,
  with the start's 409 ``below_horizon``. An optional body ``{"force": true}``
  waives it, as a start's ``force`` does.
* A group refuses only when every panel that still owes is below; otherwise
  the session resumes and the panels below are named in the response
  (``below_horizon``) and, in words, in the log, as a start names them.
* A target that owes nothing is not looked at. A finished target that has set
  must not keep a session from resuming the targets it still owes.
* A caller that sends no body resumes exactly as before.

THE HARNESS is test_flows_continue.py's ``rig``: the real app over ASGI on the
test's own loop, and a real ``SequenceEngine`` whose imaging loop alone is
replaced, with a recorder on ``engine.start`` (``rig.starts``). Its Sun check
is stubbed off unless a case puts one target in the cone (``_sun_on``). The
dormant session is written to the store directly (``_dormant``), not made by a
night, so a mutant in a route fails that route and never the setup.
``/recover`` takes the most recently updated dormant session holding frames,
and the test's captures directory holds only this one, so both routes start
the same session.

THE SKY is test_group_start_guard.py's: the hub's own ``_check_horizon`` at a
made-up "Backyard" fixture site, never the rig's. At 51.5 degrees north a
declination of +88 or more never sets and one of -88 or less never rises, so
no answer here depends on the time of day, and each case asserts that premise
from ``altaz`` before it resumes.

MUTATIONS. Each was applied to a byte-for-byte copy of ``api/app.py`` in a
private copy of ``server/`` under the session scratchpad
(``s3h-291-resume-preflight-mut``); only this file was run there, and the copy
was restored and SHA-256 compared after every mutant. The shared tree was
never written. Failures are quoted from ``--tb=short``. The console printed
the degree sign as a replacement character, and it is written here as the
route sent it. The altitude in a quoted horizon sentence is the hour's (-50 on
the day these were observed): no assertion reads it. Pytest's filler lines
in an assertion diff (a blank ``E``, "Omitting 2 identical items", "Use -v
to get more diff") are left out of the quotes; every line quoted is as
printed.

* "resume drops the call" / "recover drops the call": that route's
  ``_start_preflight`` call replaced by ``below_horizon = []``.
* "check every target, owed or not": ``_owed_plan`` returns the session's
  whole plan.
* "force never reaches the helper": both routes call the helper with
  ``force=False``.
* "force skips the pre-flight": both routes skip the helper when forced.
* "the body is required": both routes take ``body: ResumeBody`` with no
  default.
* "the response does not name them": both routes' ``if below_horizon:``
  made ``if False:``.
* "the response always names them": both routes' ``if below_horizon:`` made
  ``if True:``, so an empty ``below_horizon`` rides on every answer.
* "the log is not written": both routes' ``_name_panels_below`` call removed.
* "the panels are named before the start": both routes' ``_name_panels_below``
  call moved from after ``engine.start`` to just below ``_start_preflight``.
* "owed only if every step owes": ``_owed_plan``'s ``any`` made ``all``.
* "count every frame taken, accepted or not": ``_owed_plan``'s
  ``s.remaining()`` replaced by each step's count less
  ``s.recorded_by_step()``, so a rejected frame counts in accepted mode.

The last two were found by the task's verifier, in its own private copy
(``s3h-291-verify-mut``), when "owed only if every step owes" survived every
case then in this file: each gave a target one step and counted attempts.
``test_a_target_is_owed_by_the_plans_own_count`` was added for them.

Every mutant was RED in at least one case and every restore matched. "The
body is required" fails every case that sends no body, 18 of the 28, each
with the 422 quoted in the control case, and passes every case that sends
one; it is pinned by the control's ``[*-none]`` halves, and those send no
body for exactly that reason.
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
from astrodeck.catalog import altaz
from astrodeck.config import Site
from astrodeck.devices.base import DeviceError
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.session import Session, SessionFrame, session_store
from test_flows_continue import rig  # noqa: F401 (fixture)

#: A made-up site (test_app_preflight.py's), never the rig's.
FIXTURE_SITE = Site(name="Backyard", latitude=51.5, longitude=-0.1)
#: Declinations that never set and never rise at FIXTURE_SITE.
UP_DEC, DOWN_DEC = 88.0, -88.0

GROUP, MOSAIC = "g-resume", "Resume mosaic"
ALL_FOUR = {(0, 0), (0, 1), (1, 0), (1, 1)}
#: Frames each step asks for. A step holding one of them still owes one.
COUNT = 2

ROUTES = ("resume", "recover")
#: ``_resume``'s marker for a request sent with no body at all.
NO_BODY = object()


# ------------------------------------------------------------------ plans

def _step() -> ExposureStep:
    return ExposureStep(filter="L", exposure_s=1.0, count=COUNT)


def _single(name: str, *, down: bool, ra: float = 3.0) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=DOWN_DEC if down else UP_DEC,
                  steps=[_step()])


def _panel(row: int, col: int, *, down: bool) -> Target:
    """Panel ``row``, ``col`` (0-based) of a 2x2, up or down at the fixture
    site. The name is the Plan's ``<name> r-c``."""
    dec = (DOWN_DEC - 0.2 * row) if down else (UP_DEC + 0.2 * row)
    return Target(name=f"{MOSAIC} {row + 1}-{col + 1}",
                  ra_hours=1.0 + 0.1 * col, dec_deg=dec, mosaic_group=GROUP,
                  panel_row=row, panel_col=col, steps=[_step()])


def _grid(down: set) -> list[Target]:
    return [_panel(r, c, down=(r, c) in down)
            for r in range(2) for c in range(2)]


def _name(row: int, col: int) -> str:
    return f"{MOSAIC} {row + 1}-{col + 1}"


def _entry(row: int, col: int) -> dict:
    """A start's ``below_horizon`` entry for panel ``row``, ``col``."""
    return {"group": GROUP, "mosaic": MOSAIC, "panel": f"{row + 1}-{col + 1}",
            "target": _name(row, col)}


def _dormant(targets: list[Target], *, grouped: bool = False,
             finished: set[str] = frozenset()) -> Session:
    """A dormant session over ``targets``, saved in the store. Every step
    holds one frame of its ``COUNT``, so it still owes one, except the steps
    of the targets named in ``finished``, which hold all of theirs. Every
    session holds frames, so ``/recover`` finds it. ``grouped``: the plan
    carries the mosaic's group, so its panels are members of it."""
    plan = SequencePlan(
        name="resume me", guide=False, dither_every=0, meridian_flip=False,
        targets=targets,
        groups=[TargetGroup(id=GROUP, name=MOSAIC)] if grouped else [])
    frames = [SessionFrame(target_id=t.id, step_id=st.id)
              for t in plan.targets for st in t.steps
              for _ in range(COUNT if t.name in finished else 1)]
    s = Session(status="dormant", plan=plan, frames=frames, origin="plan")
    left = s.remaining()
    for t in plan.targets:
        owes = sum(left[st.id] for st in t.steps)
        assert (owes == 0) == (t.name in finished), (
            f"premise: {t.name} owes {owes} frames")
    session_store.save(s)
    return s


# ------------------------------------------------------------------ harness

@pytest.fixture
def sky(rig):
    """The rig at the fixture site."""
    rig.store.set_site(FIXTURE_SITE, expected_version=None)
    assert app_module.hub.site["is_default"] is False, "premise: a real site"
    return rig


def _premise(s: Session, down_names: set[str]) -> None:
    """Every target of ``s`` is where the case says, at the fixture site,
    now."""
    for t in s.plan.targets:
        alt, _az = altaz(t.ra_hours, t.dec_deg, FIXTURE_SITE.latitude,
                         FIXTURE_SITE.longitude)
        if t.name in down_names:
            assert alt < -40, f"premise: {t.name} is down, alt {alt:.1f}"
        else:
            assert alt > 40, f"premise: {t.name} is up, alt {alt:.1f}"


async def _resume(rig, route: str, s: Session, body=NO_BODY):
    """POST the route ``route`` names for session ``s``."""
    path = (f"/api/sessions/{s.id}/resume" if route == "resume"
            else "/api/sequence/recover")
    if body is NO_BODY:
        return await rig.client.post(path)
    return await rig.client.post(path, json=body)


def _today(route: str, owed: int) -> dict:
    """The answer each route gave a resume that went ahead before #291."""
    key = "remaining" if route == "resume" else "frames_remaining"
    return {"resumed": True, key: owed}


def _started(rig, s: Session) -> int:
    """How many starts of session ``s`` the engine took."""
    return sum(1 for st in rig.starts if st.won and st.session_id == s.id)


def _untouched(rig, s: Session) -> None:
    """A refused resume started nothing, and the session is still dormant
    on disk for the operator to resume later."""
    assert not any(st.won for st in rig.starts), rig.starts
    assert session_store.load(s.id).status == "dormant"


def _horizon_lines(bus_lines) -> list[tuple[str, str, str]]:
    return [line for line in bus_lines if "below the horizon" in line[1]]


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


# ============================================================== the cases

@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("route", ROUTES)
async def test_the_only_owed_target_in_the_suns_cone_refuses_forced_or_not(
        sky, bus_lines, monkeypatch, route, force):
    """A dormant session whose one owed target is up but inside the Sun's
    cone, beside a finished target: 409 ``sun_exclusion`` naming it, forced
    or not, and nothing starts.

    RED under mutant "resume drops the call", ``[resume-False]`` and
    ``[resume-True]``, and under "recover drops the call" the two
    ``[recover-*]`` cases, observed verbatim:

        [resume-False] and [resume-True]
        E   AssertionError: {"resumed":true,"remaining":1}
        E   assert 200 == 409
        [recover-False] and [recover-True]
        E   AssertionError: {"resumed":true,"frames_remaining":1}
        E   assert 200 == 409

    RED under mutant "force skips the pre-flight", ``[resume-True]`` and
    ``[recover-True]`` only, the same two lines each. The unforced cases are
    green under it: this case's forced half is what pins "the Sun is never
    waived" on these routes.
    """
    s = _dormant([_single("Finished", down=False, ra=5.0),
                  _single("In the cone", down=False)],
                 finished={"Finished"})
    _premise(s, set())
    _sun_on(monkeypatch, s.plan.targets[1])
    r = await _resume(sky, route, s, {"force": force})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "sun_exclusion", detail
    assert detail["target"] == "In the cone", detail
    _untouched(sky, s)
    assert _horizon_lines(bus_lines) == []


@pytest.mark.parametrize("route", ROUTES)
async def test_a_lone_target_below_the_horizon_refuses_unless_forced(
        sky, bus_lines, route):
    """A single target (no group) below the horizon, owing one frame. Unforced:
    the start's 409 for a lone target (the hub's sentence, ``below_horizon``,
    the target's name) and nothing starts. Forced: it resumes, with the
    route's answer as it always was, since a lone target is waived without
    a look and so named nowhere.

    RED under mutant "resume drops the call", ``[resume]``, and "recover
    drops the call", ``[recover]``, the unforced half, observed verbatim:

        [resume]
        E   AssertionError: {"resumed":true,"remaining":1}
        E   assert 200 == 409
        [recover]
        E   AssertionError: {"resumed":true,"frames_remaining":1}
        E   assert 200 == 409

    RED under mutant "force never reaches the helper", both routes, the
    forced half, observed verbatim:

        E   AssertionError: {"detail":{"detail":"target is below the visible horizon (alt -50°)","code":"below_horizon","target":"Lone down"}}
        E   assert 409 == 200

    RED under mutant "the response always names them", both routes, the
    forced half, observed verbatim:

        [resume]
        E   AssertionError: {'below_horizon': [], 'remaining': 1, 'resumed': True}
        E   assert {'below_horiz...esumed': True} == {'remaining':...esumed': True}
        [recover]
        E   AssertionError: {'below_horizon': [], 'frames_remaining': 1, 'resumed': True}
    """
    s = _dormant([_single("Lone down", down=True)])
    _premise(s, {"Lone down"})
    r = await _resume(sky, route, s, {"force": False})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert set(detail) == {"detail", "code", "target"}, detail
    assert detail["code"] == "below_horizon"
    assert detail["target"] == "Lone down"
    assert detail["detail"].startswith("target is below the visible horizon")
    _untouched(sky, s)

    forced = await _resume(sky, route, s, {"force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json() == _today(route, 1), forced.json()
    assert _started(sky, s) == 1
    assert _horizon_lines(bus_lines) == []


@pytest.mark.parametrize("route", ROUTES)
async def test_a_group_with_some_panels_down_resumes_and_names_them(
        sky, bus_lines, route):
    """A 2x2 in a group, every panel owing a frame, panel 1-2 below the
    horizon. The session resumes, the answer names the panel as a start's
    does, and one log line says it in words, the start's line exactly.

    RED under mutant "resume drops the call", ``[resume]``, "recover drops
    the call", ``[recover]``, and "the response does not name them", both
    routes, the answer lacking the panel, observed verbatim:

        [resume]
        E   AssertionError: {'remaining': 4, 'resumed': True}
        E   assert {'remaining':...esumed': True} == {'below_horiz...esumed': True}
        E     Right contains 1 more item:
        E     {'below_horizon': [{'group': 'g-resume',
        E                         'mosaic': 'Resume mosaic',
        E                         'panel': '1-2',
        E                         'target': 'Resume mosaic 1-2'}]}
        [recover]
        E   AssertionError: {'frames_remaining': 4, 'resumed': True}
        E   assert {'frames_rema...esumed': True} == {'below_horiz...esumed': True}
        (the same four lines naming the panel)

    RED under mutant "the log is not written", both routes, observed
    verbatim:

        E   assert [] == [('info', "mo..., 'sequence')]
        E     Right contains one more item: ('info', "mosaic 'Resume mosaic': panel 1-2 is below the horizon now; started, since 3 of its 4 panels are not", 'sequence')
    """
    s = _dormant(_grid({(0, 1)}), grouped=True)
    _premise(s, {_name(0, 1)})
    r = await _resume(sky, route, s)
    assert r.status_code == 200, r.text
    assert r.json() == {**_today(route, 4),
                        "below_horizon": [_entry(0, 1)]}, r.json()
    assert _started(sky, s) == 1
    assert _horizon_lines(bus_lines) == [(
        "info",
        f"mosaic '{MOSAIC}': panel 1-2 is below the horizon now; started, "
        f"since 3 of its 4 panels are not", "sequence")]


@pytest.mark.parametrize("route", ROUTES)
async def test_a_group_with_every_panel_down_refuses_and_forced_names_them(
        sky, bus_lines, route):
    """Every panel of the group below the horizon: the start's group 409,
    listing every panel, and nothing starts or is logged. Forced, it resumes
    and names every panel in the answer and the log, as a forced start does.

    RED under mutant "resume drops the call", ``[resume]``, and "recover
    drops the call", ``[recover]``, the unforced half, observed verbatim:

        [resume]
        E   AssertionError: {"resumed":true,"remaining":4}
        E   assert 200 == 409
        [recover]
        E   AssertionError: {"resumed":true,"frames_remaining":4}
        E   assert 200 == 409

    RED under mutant "force never reaches the helper", both routes, the
    forced half, observed verbatim:

        E   AssertionError: {"detail":{"detail":"every panel of mosaic 'Resume mosaic' is below the horizon now: 1-1, 1-2, 2-1, 2-2","code":"below_horizon","group":"g-resume","mosaic":"Resume mosaic","panels":[{"panel":"1-1","target":"Resume mosaic 1-1"},{"panel":"1-2","target":"Resume mosaic 1-2"},{"panel":"2-1","target":"Resume mosaic 2-1"},{"panel":"2-2","target":"Resume mosaic 2-2"}]}}
        E   assert 409 == 200

    RED under mutants "the response does not name them" and "force skips
    the pre-flight" (a forced resume that looks at nothing names nothing),
    both routes, the forced half, observed verbatim:

        E   KeyError: 'below_horizon'

    RED under mutant "the log is not written", both routes, the forced half,
    observed verbatim:

        E   assert [] == [('info', "mo..., 'sequence')]
        E     Right contains one more item: ('info', "mosaic 'Resume mosaic': all 4 panels are below the horizon now (1-1, 1-2, 2-1, 2-2); started because the start was forced", 'sequence')
    """
    s = _dormant(_grid(ALL_FOUR), grouped=True)
    _premise(s, {t.name for t in s.plan.targets})
    r = await _resume(sky, route, s, {"force": False})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "detail": f"every panel of mosaic '{MOSAIC}' is below the horizon "
                  f"now: 1-1, 1-2, 2-1, 2-2",
        "code": "below_horizon", "group": GROUP, "mosaic": MOSAIC,
        "panels": [{"panel": e["panel"], "target": e["target"]}
                   for e in (_entry(0, 0), _entry(0, 1), _entry(1, 0),
                             _entry(1, 1))]}, r.json()
    _untouched(sky, s)
    assert _horizon_lines(bus_lines) == []

    forced = await _resume(sky, route, s, {"force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["below_horizon"] == [
        _entry(0, 0), _entry(0, 1), _entry(1, 0), _entry(1, 1)], forced.json()
    assert _started(sky, s) == 1
    assert _horizon_lines(bus_lines) == [(
        "info",
        f"mosaic '{MOSAIC}': all 4 panels are below the horizon now (1-1, "
        f"1-2, 2-1, 2-2); started because the start was forced",
        "sequence")]


def _finished_and_set() -> dict[str, tuple[list[Target], bool, set[str]]]:
    """Sessions with a finished target that has set and owed targets that
    are up: ``(targets, grouped, finished)``."""
    return {
        "lone": ([_single("Done and set", down=True),
                  _single("Owed up", down=False, ra=5.0)], False,
                 {"Done and set"}),
        "panel": (_grid({(0, 0)}), True, {_name(0, 0)}),
    }


@pytest.mark.parametrize("variant", ["lone", "panel"])
@pytest.mark.parametrize("route", ROUTES)
async def test_a_finished_target_that_has_set_does_not_refuse(
        sky, bus_lines, route, variant):
    """A target that owes nothing is not looked at. ``lone``: a finished
    single target below the horizon beside an owed one that is up.
    ``panel``: panel 1-1 of a group finished and below the horizon, the
    other three owed and up. Each resumes with the route's answer as it
    always was, names nothing and logs nothing.

    RED under mutant "check every target, owed or not", every case. ``lone``
    refuses on the finished target, observed verbatim:

        [resume-lone] and [recover-lone]
        E   AssertionError: {"detail":{"detail":"target is below the visible horizon (alt -50°)","code":"below_horizon","target":"Done and set"}}
        E   assert 409 == 200

    and ``panel`` names the finished panel, observed verbatim:

        [resume-panel]
        E   AssertionError: {'below_horizon': [{'group': 'g-resume', 'mosaic': 'Resume mosaic', 'panel': '1-1', 'target': 'Resume mosaic 1-1'}], 'remaining': 3, 'resumed': True}
        E   assert {'below_horiz...esumed': True} == {'remaining':...esumed': True}
        [recover-panel]
        E   AssertionError: {'below_horizon': [{'group': 'g-resume', 'mosaic': 'Resume mosaic', 'panel': '1-1', 'target': 'Resume mosaic 1-1'}], 'frames_remaining': 3, 'resumed': True}
        E   assert {'below_horiz...esumed': True} == {'frames_rema...esumed': True}

    RED under mutant "the response always names them", every case, observed
    verbatim:

        [resume-lone]
        E   AssertionError: {'below_horizon': [], 'remaining': 1, 'resumed': True}
        [resume-panel]
        E   AssertionError: {'below_horizon': [], 'remaining': 3, 'resumed': True}
        [recover-*] the same, with 'frames_remaining'

    Green under both "drops the call" mutants: a route that looks at
    nothing refuses nothing, which is why this case needs the mutant above.
    """
    targets, grouped, finished = _finished_and_set()[variant]
    s = _dormant(targets, grouped=grouped, finished=finished)
    _premise(s, finished)
    r = await _resume(sky, route, s)
    assert r.status_code == 200, r.text
    assert r.json() == _today(route, len(targets) - 1), r.json()
    assert _started(sky, s) == 1
    assert _horizon_lines(bus_lines) == []


@pytest.mark.parametrize("route", ROUTES)
async def test_finished_panels_that_are_up_do_not_carry_a_group_that_owes_only_below(
        sky, bus_lines, route):
    """Panels 1-1 and 1-2 finished and up, 2-1 and 2-2 owed and below the
    horizon. Every panel the session still owes is down, so the group
    refuses with the start's 409, listing the owed panels only: a finished
    panel is nothing the run would shoot tonight, so being up does not make
    the group shootable.

    RED under mutant "check every target, owed or not", both routes: the
    finished panels count as up and the group resumes, observed verbatim:

        [resume]
        E   AssertionError: {"resumed":true,"remaining":2,"below_horizon":[{"group":"g-resume","mosaic":"Resume mosaic","target":"Resume mosaic 2-1","panel":"2-1"},{"group":"g-resume","mosaic":"Resume mosaic","target":"Resume mosaic 2-2","panel":"2-2"}]}
        E   assert 200 == 409
        [recover]
        E   AssertionError: {"resumed":true,"frames_remaining":2,"below_horizon":[{"group":"g-resume","mosaic":"Resume mosaic","target":"Resume mosaic 2-1","panel":"2-1"},{"group":"g-resume","mosaic":"Resume mosaic","target":"Resume mosaic 2-2","panel":"2-2"}]}
        E   assert 200 == 409

    RED under mutant "resume drops the call", ``[resume]``, and "recover
    drops the call", ``[recover]``, observed verbatim:

        [resume]
        E   AssertionError: {"resumed":true,"remaining":2}
        E   assert 200 == 409
        [recover]
        E   AssertionError: {"resumed":true,"frames_remaining":2}
        E   assert 200 == 409
    """
    s = _dormant(_grid({(1, 0), (1, 1)}), grouped=True,
                 finished={_name(0, 0), _name(0, 1)})
    _premise(s, {_name(1, 0), _name(1, 1)})
    r = await _resume(sky, route, s)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "detail": f"every panel of mosaic '{MOSAIC}' is below the horizon "
                  f"now: 2-1, 2-2",
        "code": "below_horizon", "group": GROUP, "mosaic": MOSAIC,
        "panels": [{"panel": e["panel"], "target": e["target"]}
                   for e in (_entry(1, 0), _entry(1, 1))]}, r.json()
    _untouched(sky, s)
    assert _horizon_lines(bus_lines) == []


#: ``shape`` -> (the panels finished, the panels below, the body, the owed
#: frames, the panels the answer names, the log line after "mosaic '...': ").
#: Every finished panel is up: it is what a count over the whole plan would
#: take for a panel the run can still shoot.
_OWED_COUNT_SHAPES = {
    "partly down": ({(0, 0)}, {(1, 0), (1, 1)}, NO_BODY, 3,
                    [(1, 0), (1, 1)],
                    "panels 2-1 and 2-2 are below the horizon now; started, "
                    "since 1 of its 3 panels is not"),
    "all owed down, forced": ({(0, 0), (0, 1)}, {(1, 0), (1, 1)},
                              {"force": True}, 2, [(1, 0), (1, 1)],
                              "all 2 panels are below the horizon now (2-1, "
                              "2-2); started because the start was forced"),
}


@pytest.mark.parametrize("shape", list(_OWED_COUNT_SHAPES))
@pytest.mark.parametrize("route", ROUTES)
async def test_the_logged_count_is_over_the_panels_still_owed(
        sky, bus_lines, route, shape):
    """The line a resume logs for its group's panels below the horizon counts
    the panels the session still OWES, the plan the pre-flight judged
    (``_owed_plan``), never the whole plan: a finished panel is nothing the
    run will shoot tonight, so it is neither one of the group's panels nor
    one of those "not" below.

    "partly down": 1-1 finished, 1-2 owed and up, 2-1 and 2-2 owed and
    below. It resumes, "since 1 of its 3 panels is not". "all owed down,
    forced": 1-1 and 1-2 finished, the two owed panels below, forced. The
    line is the forced start's "all 2 panels", where a count over the whole
    plan says "since 2 of its 4 panels are not" and tells the operator the
    run has panels to shoot that it will never visit.

    Until this case the count was held only by test_mosaic_spec_claims's
    reading of the routes' source (test_6_3), which a rename passes and a
    behaviour change with the same spelling does not see.

    RED under mutant "resume names over the whole plan" (the resume route's
    ``_name_panels_below(owed, below_horizon)`` handed ``s.plan``), both
    ``[resume-*]`` cases, and under "recover names over the whole plan" (the
    same in ``sequence_recover``), both ``[recover-*]`` cases; every other
    case in this file stayed green under both. Each was run in a private
    copy of ``server/`` (scratchpad s3-tcf-review-mut3), never the shared
    tree, and restored and SHA-256 compared. Observed verbatim (the filler
    ``E`` lines left out):

        [resume-partly down]
        E   At index 0 diff: ('info', "mosaic 'Resume mosaic': panels 2-1 and 2-2 are below the horizon now; started, since 2 of its 4 panels are not", 'sequence') != ('info', "mosaic 'Resume mosaic': panels 2-1 and 2-2 are below the horizon now; started, since 1 of its 3 panels is not", 'sequence')
        [recover-all owed down, forced]
        E   At index 0 diff: ('info', "mosaic 'Resume mosaic': panels 2-1 and 2-2 are below the horizon now; started, since 2 of its 4 panels are not", 'sequence') != ('info', "mosaic 'Resume mosaic': all 2 panels are below the horizon now (2-1, 2-2); started because the start was forced", 'sequence')
    """
    finished, down, body, owed, named, line = _OWED_COUNT_SHAPES[shape]
    s = _dormant(_grid(down), grouped=True,
                 finished={_name(r, c) for r, c in finished})
    _premise(s, {_name(r, c) for r, c in down})
    r = await _resume(sky, route, s, body)
    assert r.status_code == 200, r.text
    assert r.json() == {**_today(route, owed),
                        "below_horizon": [_entry(*rc) for rc in named]}, (
        r.json())
    assert _started(sky, s) == 1
    assert _horizon_lines(bus_lines) == [
        ("info", f"mosaic '{MOSAIC}': {line}", "sequence")]


def _owes_by_its_own_count(shape: str) -> tuple[Session, str | None]:
    """A dormant session with one target in the Sun's cone (its name is
    returned with the session), shaped so whether that target still owes
    turns on how the plan counts, not on whether it holds frames:

    * ``part-done``: two filters, L finished and R one frame short. It owes.
    * ``rejected``: ``count_mode="accepted"``, one step holding all ``COUNT``
      of its frames, every one rejected. It owes all of them.
    * ``accepted-done`` (the control): the same, every frame accepted, beside
      a target that is up and owes. The one in the cone owes nothing, so the
      only target owed is the one that is up.

    Each shape's premise is asserted, including the half that makes it
    discriminate: ``part-done`` has a finished step (else "every step owes"
    would keep it too), and ``rejected`` holds its full count of frames
    taken (else a raw count would call it owed too).
    """
    accepted = shape != "part-done"
    cone = Target(name="In the cone", ra_hours=3.0, dec_deg=UP_DEC,
                  steps=[ExposureStep(filter=f, exposure_s=1.0, count=COUNT)
                         for f in (("L", "R") if shape == "part-done"
                                   else ("L",))])
    targets = [cone]
    if shape == "accepted-done":
        targets.append(_single("Owed up", down=False, ra=5.0))
    plan = SequencePlan(
        name="resume me", guide=False, dither_every=0, meridian_flip=False,
        count_mode="accepted" if accepted else "attempts", targets=targets)
    cone = plan.targets[0]
    if shape == "part-done":
        held = [(cone.steps[0], COUNT, True), (cone.steps[1], 1, True)]
    else:
        held = [(cone.steps[0], COUNT, shape == "accepted-done")]
    frames = [SessionFrame(target_id=cone.id, step_id=st.id, auto_accepted=ok)
              for st, n, ok in held for _ in range(n)]
    s = Session(status="dormant", plan=plan, frames=frames, origin="plan")
    left = [s.remaining()[st.id] for st in cone.steps]
    want = {"part-done": [0, 1], "rejected": [COUNT],
            "accepted-done": [0]}[shape]
    assert left == want, f"premise: {shape}: the cone's steps owe {left}"
    taken = [s.recorded_by_step().get(st.id, 0) for st in cone.steps]
    assert taken == [n for _st, n, _ok in held], (
        f"premise: {shape}: the cone's steps hold {taken} frames")
    session_store.save(s)
    return s, None if shape == "accepted-done" else cone.name


@pytest.mark.parametrize("shape", ["part-done", "rejected", "accepted-done"])
@pytest.mark.parametrize("route", ROUTES)
async def test_a_target_is_owed_by_the_plans_own_count(
        sky, monkeypatch, route, shape):
    """"Owed" is ``Session.remaining``: per STEP, counted as the frozen
    plan's ``count_mode`` counts. A target owes while ANY of its steps does,
    so a filter finished on night one does not take the target out of the
    Sun check; and in accepted mode a rejected frame is not a frame, so a
    target that holds its full count of rejects still owes all of it. Both
    refuse 409 ``sun_exclusion`` with nothing started. CONTROL
    ``accepted-done``: the same accepted-mode target in the same cone, its
    frames accepted, owes nothing and resumes with the route's answer as it
    always was.

    Every other case here gives each target ONE step and counts attempts, so
    none of them can tell "any step owes" from "every step owes", or the
    plan's count from a raw count of frames taken. A verifier's mutant
    ("owed only if every step owes") survived all 22 of them.

    RED under mutant "owed only if every step owes" (``_owed_plan``'s
    ``any`` made ``all``), ``[*-part-done]`` only, observed verbatim:

        [resume-part-done]
        E   AssertionError: {"resumed":true,"remaining":1}
        E   assert 200 == 409
        [recover-part-done]
        E   AssertionError: {"resumed":true,"frames_remaining":1}
        E   assert 200 == 409

    RED under mutant "count every frame taken, accepted or not"
    (``_owed_plan``'s ``s.remaining()`` replaced by each step's count less
    ``s.recorded_by_step()``), ``[*-rejected]`` only, observed verbatim:

        [resume-rejected]
        E   AssertionError: {"resumed":true,"remaining":2}
        E   assert 200 == 409
        [recover-rejected]
        E   AssertionError: {"resumed":true,"frames_remaining":2}
        E   assert 200 == 409

    RED under mutant "check every target, owed or not", ``[*-accepted-done]``
    only, the control refusing on the finished target, observed verbatim:

        [resume-accepted-done] and [recover-accepted-done]
        E   AssertionError: {"detail":{"detail":"target is within 12 deg of the Sun (exclusion 30 deg) - enable a solar session (config.solar_override) to override","code":"sun_exclusion","target":"In the cone"}}
        E   assert 409 == 200

    Also RED under "resume drops the call" and "recover drops the call",
    that route's ``[*-part-done]`` and ``[*-rejected]``, with the same
    200 == 409 lines as above; under "the body is required", every case, the
    422 the control quotes; and under "the response always names them",
    ``[*-accepted-done]``, e.g. ``AssertionError: {'below_horizon': [],
    'remaining': 2, 'resumed': True}``.
    """
    s, owed_in_cone = _owes_by_its_own_count(shape)
    _premise(s, set())
    _sun_on(monkeypatch, s.plan.targets[0])
    r = await _resume(sky, route, s)
    if owed_in_cone is None:
        assert r.status_code == 200, r.text
        assert r.json() == _today(route, COUNT), r.json()
        assert _started(sky, s) == 1
        return
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "sun_exclusion", detail
    assert detail["target"] == owed_in_cone, detail
    _untouched(sky, s)


@pytest.mark.parametrize("body", ["none", "empty"])
@pytest.mark.parametrize("route", ROUTES)
async def test_control_a_session_whose_targets_are_up_resumes_as_today(
        sky, bus_lines, route, body):
    """CONTROL: every target up, one of them a group of four panels. The
    session resumes with the route's answer exactly as it was before #291,
    names nothing and logs nothing, whether the caller sends no body at all
    (``none``, every client before #291) or an empty one (``empty``).

    Green on the code and under every mutant in this file but two.

    RED under mutant "the body is required", the two ``[*-none]`` cases (the
    ``[*-empty]`` ones stay green), observed verbatim:

        [resume-none] and [recover-none]
        E   AssertionError: {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}],"code":"invalid_request"}
        E   assert 422 == 200

    RED under mutant "the response always names them", every case, observed
    verbatim:

        [resume-none] and [resume-empty]
        E   AssertionError: {'below_horizon': [], 'remaining': 5, 'resumed': True}
        E   assert {'below_horiz...esumed': True} == {'remaining':...esumed': True}
        [recover-none] and [recover-empty]
        E   AssertionError: {'below_horizon': [], 'frames_remaining': 5, 'resumed': True}
        E   assert {'below_horiz...esumed': True} == {'frames_rema...esumed': True}
    """
    s = _dormant(_grid(set()) + [_single("Lone up", down=False, ra=5.0)],
                 grouped=True)
    _premise(s, set())
    r = await _resume(sky, route, s, NO_BODY if body == "none" else {})
    assert r.status_code == 200, r.text
    assert r.json() == _today(route, 5), r.json()
    assert _started(sky, s) == 1
    assert _horizon_lines(bus_lines) == []


@pytest.mark.parametrize("route", ROUTES)
async def test_a_resume_the_engine_refuses_names_nothing(
        sky, bus_lines, route):
    """A group that passes the pre-flight (panel 1-2 below the horizon) but
    whose resume the ENGINE then refuses, because a run is already live: the
    route answers 409 with the engine's words, and neither the answer nor
    the log names the panel. The log line says "started", so the panels are
    named only once ``engine.start`` has returned.

    The live run is a fresh plan of one target that is up, started on the
    engine directly, so its session is active and ``/recover`` still takes
    the dormant one.

    RED under mutant "the panels are named before the start", both routes,
    observed verbatim:

        [resume] and [recover]
        E   AssertionError: a refused resume logged its panels: [('info', "mosaic 'Resume mosaic': panel 1-2 is below the horizon now; started, since 3 of its 4 panels are not", 'sequence')]
        E   assert [('info', "mo..., 'sequence')] == []

    Green under every other mutant here but "the body is required".
    """
    s = _dormant(_grid({(0, 1)}), grouped=True)
    _premise(s, {_name(0, 1)})
    sky.engine.start(SequencePlan(
        name="live", guide=False, dither_every=0, meridian_flip=False,
        targets=[_single("Live up", down=False, ra=7.0)]), origin="plan")
    assert sky.engine.running, "premise: a run is live"
    r = await _resume(sky, route, s)
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": "a sequence is already running"}, r.json()
    assert _started(sky, s) == 0
    assert session_store.load(s.id).status == "dormant"
    lines = _horizon_lines(bus_lines)
    assert lines == [], f"a refused resume logged its panels: {lines}"
