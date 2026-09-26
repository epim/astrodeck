"""Auto-resume's start-floor hold tells a viewer nothing about the site (#233;
mosaic slice H3 task T1; H3 orchestrator ruling 1 (spec, Still waiting on the
owner, item 10); spec 6.9; the owner's role-visibility ruling of 2026-09-22:
admin and operator may see site-derived facts, a viewer never).

WHAT LEAKED. ``ResumeArm._recover`` refuses to re-centre on a target below
its plan's start floor, and it said so in one sentence: "NGC 604 is at 9 deg,
below its 30 deg start floor; not slewing yet (it reaches 30 deg in about
2.1 h)". That sentence was the hold's ``reason``, which
``GET /api/sequence/resume-arm`` serves to anyone holding CAP_VIEW_STATUS, a
viewer included, and it was the "auto-resume held: ..." warning, which
``GET /api/logs`` serves to the same viewer. A known object's altitude at a
known time is a circle of latitudes and longitudes on the Earth (#140), the
ETA is a second one, and the two together are a point.

WHAT CHANGED. The refusal's ``reason`` is words: "NGC 604 is below its start
floor; not slewing yet". The numbers moved to ``hold.site_detail``, which
``_redact_resume_arm_for`` removes (absent, not null) for a principal without
CAP_VIEW_SITE_DERIVED. The ``limits`` refusal does the same with the engine's
gate sentence.

THE SCAN. One tick whose ladder refuses on the start floor is driven at the
same clock under two synthetic sites (``_site_tracking.SITE_A``/``SITE_B``,
the #19 scanner's), with the same session, plan and ids. What a VIEWER can
read is collected: the route's body, ``GET /api/logs``, and every bus line the
tick logged (the night log file is written from the same lines). A number
counts only if it moves when only the site moves (``_site_tracking``), so a
session id, the injected clock's timestamps and a catalogue number do not.

``ts`` on each ``/api/logs`` row is left out, and not because it is safe:
it is the wall clock of the test run, which moves between the two ticks for a
reason that has nothing to do with the site, and a comparison that kept it
would call every run a leak. The time a hold is logged is set by the tick's
60 s cadence and its 10 min backoff, not by a site computation; the timing
channel spec 6.9 names (a line whose TIME a site computation sets) is a
separate rule.

THE KNOWN POSITIVE, in the same file, because a scan that finds nothing may
be a scan that cannot see (``test_the_allowlisted_route_is_actually_scanned``
for #19): an OPERATOR reading the same route under the same two ticks DOES
get numbers that track the site, in ``hold.site_detail``, and among them the
target's altitude as ``_frame_altitude`` computes it under each site.

Each mutant was applied to a byte-for-byte backup of the file it changes and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failures are quoted as observed (``--tb=short``).
"""
from __future__ import annotations

import functools
import json
from collections import deque
from types import SimpleNamespace

import pytest

import astrodeck.api.app as app_module
from astrodeck import events
from astrodeck.api.redact import _redact_resume_arm_for
from astrodeck.auth import (CAP_VIEW_SITE_DERIVED, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.auth.principal import Principal
from astrodeck.sequence.engine import _frame_altitude
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target)
from astrodeck.sequence.resume_arm import RETRY_INTERVAL_S, ResumeArm
from astrodeck.sequence.session import Session, session_store
from _site_tracking import (SITE_A, SITE_B, any_tracking, numeric_tokens,
                            site, tracking_tokens)
from test_flows_continue import rig  # noqa: F401 (fixture)
from test_resume_ladder_stops import _ParkedHub, ladder  # noqa: F401 (fixture)

#: The start floor the held target's plan asks for.
FLOOR_DEG = 30.0

#: Where the search for a clock reading starts. Any instant would do; the
#: search below is what makes the chosen one mean "below the floor at both
#: sites".
T0 = 1_700_000_000.0

#: The roles, in the order the route is read. VIEWER FIRST, on purpose: the
#: helper must copy the hold before it strips it, and a strip made in place
#: on ``resume_arm.hold`` would show as a missing ``site_detail`` for the
#: operator read after it.
ROLES = ("viewer", "operator", "admin")


def _target() -> Target:
    return Target(id="t-m42", name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                  center=False, autofocus_first=False,
                  schedule=Schedule(min_altitude_deg=FLOOR_DEG),
                  steps=[ExposureStep(id="s-l", filter="L", exposure_s=1.0,
                                      count=2)])


@functools.lru_cache(maxsize=None)
def _when() -> float:
    """A clock reading at which the target is between 3 deg and 5 deg under
    its floor at BOTH sites, and its whole-degree altitude differs between
    them.

    Searched rather than hardcoded, the way test_resume_arm.py's
    ``_when_altitude_between`` is, so the premise is computed, not assumed.
    The second condition is what lets the "floor reason keeps the altitude"
    mutant show: an altitude that rounds the same under both sites does not
    move, and the scan could not tell it from a constant."""
    t = _target()
    for i in range(2 * 24 * 12):
        when = T0 + i * 300.0
        a = _frame_altitude(t, site(SITE_A), when)
        b = _frame_altitude(t, site(SITE_B), when)
        if (a is not None and b is not None
                and 3.0 <= a <= FLOOR_DEG - 5.0 and 3.0 <= b <= FLOOR_DEG - 5.0
                and f"{a:.0f}" != f"{b:.0f}"):
            return when
    raise AssertionError("no clock reading in two days puts the target under "
                         "its floor at both synthetic sites")


class _FixedPrincipal:
    """Every request resolves to one identity (the #19 scanner's pattern)."""

    name = "fake"

    def __init__(self, principal) -> None:
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _restore_provider():
    yield
    reset_active_provider()


def _held_session() -> Session:
    """One session, the same ids under both sites, armed and dormant."""
    plan = SequencePlan(name="floor", guide=False,
                        dither_every=0, meridian_flip=False,
                        targets=[_target()])
    return Session(id="s-floor-held", name="held on its floor",
                   status="dormant", plan=plan, auto_resume=True,
                   created_ts=T0, updated_ts=T0)


async def _held_tick(lad, monkeypatch, where: tuple[float, float]):
    """One tick whose ladder refuses on the start floor under site ``where``,
    and what each reader was shown.

    A fresh ResumeArm per site, on a parked hub whose ``site`` is ``where``,
    at the one clock reading ``_when``, installed as the app's
    ``resume_arm``: the two ticks share nothing but the session file and the
    engine, and neither of those moves. The log ring is emptied and the
    storm limiter's window closed before the tick, so ``/api/logs`` holds
    this tick's lines and no earlier ones."""
    hub = _ParkedHub()
    hub.site = site(where)
    arm = ResumeArm(lad.rig.engine, hub, clock=_when)
    monkeypatch.setattr(arm, "_window_open", lambda s, t: True)
    monkeypatch.setattr(arm, "_can_solve", lambda: True)
    monkeypatch.setattr(arm, "_can_autofocus", lambda: True)
    monkeypatch.setattr(app_module, "resume_arm", arm)
    session_store.save(_held_session())
    bus = events.bus
    bus.flush()
    monkeypatch.setattr(bus, "_history", deque(maxlen=200))
    monkeypatch.setattr(bus, "_history_unflagged", deque(maxlen=200))
    monkeypatch.setattr(bus, "night_log", None)
    lines: list[tuple[str, str, str]] = []

    def log(level, message, source="hub"):
        lines.append((level, message, source))
        events.EventBus.log(bus, level, message, source)

    monkeypatch.setattr(bus, "log", log)
    await arm.tick()
    assert arm.hold is not None and "below its start floor" in \
        arm.hold["reason"], (
            f"premise: the tick held on the target's start floor: {arm.hold}; "
            f"lines {lines}")
    route = {}
    for role in ROLES:
        set_active_provider(_FixedPrincipal(principal_for_role(role)))
        r = await lad.rig.client.get("/api/sequence/resume-arm")
        assert r.status_code == 200, (role, r.text)
        route[role] = r.json()
    set_active_provider(_FixedPrincipal(principal_for_role("viewer")))
    r = await lad.rig.client.get("/api/logs")
    assert r.status_code == 200, r.text
    reset_active_provider()
    return SimpleNamespace(route=route, logs=[row.get("data") for row in
                                              r.json()],
                           lines=lines, hold=arm.hold)


def _viewer_reads(t) -> dict[str, str]:
    """Everything a viewer could read of one tick, per reader."""
    return {
        "GET /api/sequence/resume-arm": json.dumps(t.route["viewer"],
                                                   sort_keys=True),
        "GET /api/logs": json.dumps(t.logs, sort_keys=True),
        "the tick's bus lines (the night log's source)": "\n".join(
            f"[{lvl}] {msg} ({src})" for lvl, msg, src in t.lines),
    }


async def _both_sites(lad, monkeypatch):
    a = await _held_tick(lad, monkeypatch, SITE_A)
    b = await _held_tick(lad, monkeypatch, SITE_B)
    return a, b


async def test_a_viewer_reads_no_number_that_tracks_the_site(ladder,
                                                              monkeypatch):
    """The scan. As a VIEWER, the route, ``/api/logs`` and the tick's bus
    lines, under SITE_A and under SITE_B at the same clock: no numeric token
    may differ between the two. The fixture's own session is disarmed so the
    one armed session is the floor-held one.

    RED under mutant "route returns resume_arm.hold unredacted"
    (``sequence_resume_arm`` returns its payload without
    ``_redact_resume_arm_for``), the route only: the altitude (25, 10) and
    the ETA in hours (18.0, 15.0) under each site. Observed verbatim:

        E   AssertionError: a VIEWER can read numbers that move with the site:
        E       GET /api/sequence/resume-arm: under SITE_A ['18.0', '25'], under SITE_B ['10', '15.0']
        E   assert not {'GET /api/sequence/resume-arm': {'a': ['18.0', '25'], 'b': ['10', '15.0']}}

    RED under mutant "the held warning logs site_detail" (the tick's "auto-
    resume held: ..." warning formats ``self._refusal_site_detail`` after
    the reason), the log ring and the bus lines, observed verbatim:

        E   AssertionError: a VIEWER can read numbers that move with the site:
        E       GET /api/logs: under SITE_A ['18.0', '25'], under SITE_B ['10', '15.0']
        E       the tick's bus lines (the night log's source): under SITE_A ['18.0', '25'], under SITE_B ['10', '15.0']

    RED under mutant "floor reason keeps the altitude" (the floor refusal's
    words-only reason becomes ``f"{tgt.name} is at {alt:.0f} deg, below its
    start floor; not slewing yet"``), all three readers, observed verbatim:

        E   AssertionError: a VIEWER can read numbers that move with the site:
        E       GET /api/sequence/resume-arm: under SITE_A ['25'], under SITE_B ['10']
        E       GET /api/logs: under SITE_A ['25'], under SITE_B ['10']
        E       the tick's bus lines (the night log's source): under SITE_A ['25'], under SITE_B ['10']

    Under SITE_B the altitude, 10, is also the "retrying in 10 min" of the
    same line. The count caught it: ``tracking_tokens`` compares how often a
    token appears, not whether it does.
    """
    lad = ladder
    lad.session.auto_resume = False
    session_store.save(lad.session)
    a, b = await _both_sites(lad, monkeypatch)
    ra, rb = _viewer_reads(a), _viewer_reads(b)
    leaks = {reader: found for reader in ra
             if any_tracking(found := tracking_tokens([ra[reader]],
                                                      [rb[reader]]))}
    assert not leaks, (
        "a VIEWER can read numbers that move with the site:\n"
        + "\n".join(f"  {reader}: under SITE_A {f['a']}, under SITE_B {f['b']}"
                    for reader, f in leaks.items()))
    # Not vacuous: the viewer was shown the hold, in words, and its line.
    for t in (a, b):
        assert "below its start floor" in t.route["viewer"]["hold"]["reason"]
        assert any("auto-resume held:" in m for _l, m, _s in t.lines), t.lines
        assert any("auto-resume held:" in (row or {}).get("message", "")
                   for row in t.logs), t.logs


async def test_the_scan_sees_the_site_in_an_operator_s_site_detail(
        ladder, monkeypatch):
    """THE KNOWN POSITIVE. The same two ticks, read as an OPERATOR, who is
    entitled to site-derived facts: ``hold.site_detail`` carries numbers that
    track the site, and among them the target's whole-degree altitude as
    ``_frame_altitude`` computes it under each site. So the scan above can
    see a site-derived number when one is there, and its silence about the
    viewer's reads is a measurement.

    RED under mutant "the detail is dropped" (``_recover``'s floor branch
    stops writing ``_refusal_site_detail``, so no hold carries one; the
    words-only reason is unchanged), and under "the tick drops the detail"
    (``tick``'s ``_set_hold`` passes ``site_detail=None``), "strip in place"
    and "strip for everybody" (below), observed verbatim:

        E   KeyError: 'site_detail'

    RED under mutant "the rule cannot see" (``tracking_tokens`` returns
    ``{"a": [], "b": []}``), this case and the pure rule case below,
    observed verbatim:

        E   AssertionError: the scan found no site-tracking number in an operator's site_detail: {'a': [], 'b': []}
        E   assert False
        E    +  where False = any_tracking({'a': [], 'b': []})

    RED under mutant "one-sided rule" (``"b"`` computed as an empty list),
    which the viewer scan above cannot see (it finds nothing either way),
    observed verbatim:

        E   AssertionError: the operator's site_detail does not carry the altitude under each site (25, 10): {'a': ['18.0', '25'], 'b': []}; 'M42 is at 25 deg, below its 30 deg start floor (it reaches 30 deg in about 18.0 h)' / 'M42 is at 10 deg, below its 30 deg start floor (it reaches 30 deg in about 15.0 h)'
        E   assert ('25' in ['18.0', '25'] and '10' in [])
    """
    lad = ladder
    lad.session.auto_resume = False
    session_store.save(lad.session)
    a, b = await _both_sites(lad, monkeypatch)
    da = a.route["operator"]["hold"]["site_detail"]
    db = b.route["operator"]["hold"]["site_detail"]
    found = tracking_tokens([da], [db])
    assert any_tracking(found), (
        f"the scan found no site-tracking number in an operator's "
        f"site_detail: {found}")
    alt_a = _frame_altitude(_target(), site(SITE_A), _when())
    alt_b = _frame_altitude(_target(), site(SITE_B), _when())
    assert f"{alt_a:.0f}" in found["a"] and f"{alt_b:.0f}" in found["b"], (
        f"the operator's site_detail does not carry the altitude under each "
        f"site ({alt_a:.0f}, {alt_b:.0f}): {found}; {da!r} / {db!r}")


async def test_admin_and_operator_see_site_detail_and_a_viewer_never_does(
        ladder, monkeypatch):
    """THE ROLE CONTROL, and the shape. Admin and operator hold
    CAP_VIEW_SITE_DERIVED and are shown ``hold.site_detail``; a viewer does
    not hold it and the key is ABSENT, not null. Nothing else in the payload
    differs: the viewer's body is the operator's with that one key removed.
    The route is read viewer first (``ROLES``), so a strip made in place on
    the service's own hold would take the operator's key too.

    The numbers are off the reason and the log: the reason carries no number
    at all, and the held warning carries only the retry interval's.

    RED under mutant "route returns resume_arm.hold unredacted", observed
    verbatim:

        E   AssertionError: a viewer was shown site_detail: {'reason': 'M42 is below its start floor; not slewing yet', 'since': 1700043200.0, 'retry_at': 1700043800.0, 'session_id': 's-floor-held', 'session_name': 'held on its floor', 'owed': 2, 'site_detail': 'M42 is at 25 deg, below its 30 deg start floor (it reaches 30 deg in about 18.0 h)'}
        E   assert 'site_detail' not in {'owed': 2, 'reason': 'M42 is below its start floor; not slewing yet', 'retry_at': 1700043800.0, 'session_id': 's-floor-held', ...}

    RED under mutant "strip in place" (``_redact_resume_arm_for`` pops the
    key off the hold it was handed and returns that, in place of a copy),
    and under "the detail is dropped", "the tick drops the detail" and
    "strip for everybody", observed verbatim:

        E   AssertionError: operator was not shown site_detail: {'reason': 'M42 is below its start floor; not slewing yet', 'since': 1700043200.0, 'retry_at': 1700043800.0, 'session_id': 's-floor-held', 'session_name': 'held on its floor', 'owed': 2}
        E   assert 'site_detail' in {'owed': 2, 'reason': 'M42 is below its start floor; not slewing yet', 'retry_at': 1700043800.0, 'session_id': 's-floor-held', ...}

    RED under mutant "the helper nulls the key" (``{**hold, key: None}`` in
    place of the copy without it), observed verbatim:

        E   AssertionError: a viewer was shown site_detail: {'reason': 'M42 is below its start floor; not slewing yet', 'since': 1700043200.0, 'retry_at': 1700043800.0, 'session_id': 's-floor-held', 'session_name': 'held on its floor', 'owed': 2, 'site_detail': None}

    RED under mutant "floor reason keeps the altitude", observed verbatim:

        E   AssertionError: the floor reason carries a number: 'M42 is at 25 deg, below its start floor; not slewing yet'
        E   assert Counter({'25': 1}) == Counter()

    RED under mutant "the held warning logs site_detail", observed
    verbatim:

        E   AssertionError: the held warning carries a number beyond the retry interval: 'auto-resume held: M42 is below its start floor; not slewing yet (M42 is at 25 deg, below its 30 deg start floor (it reaches 30 deg in about 18.0 h)) — retrying in 10 min'
    """
    lad = ladder
    lad.session.auto_resume = False
    session_store.save(lad.session)
    t = await _held_tick(lad, monkeypatch, SITE_A)
    viewer = t.route["viewer"]["hold"]
    assert "site_detail" not in viewer, f"a viewer was shown site_detail: {viewer}"
    for role in ("operator", "admin"):
        held = t.route[role]["hold"]
        assert "site_detail" in held, f"{role} was not shown site_detail: {held}"
        assert held["site_detail"] == t.hold["site_detail"], (role, held)
        assert "30 deg start floor" in held["site_detail"], held
    stripped = dict(t.route["operator"])
    stripped["hold"] = {k: v for k, v in stripped["hold"].items()
                        if k != "site_detail"}
    assert t.route["viewer"] == stripped, (
        "the viewer's payload differs from the operator's by more than "
        f"site_detail:\n  viewer   {t.route['viewer']}\n  operator {stripped}")
    reason = t.hold["reason"]
    assert numeric_tokens(reason) == numeric_tokens(""), (
        f"the floor reason carries a number: {reason!r}")
    held_lines = [m for lvl, m, _s in t.lines
                  if lvl == "warning" and m.startswith("auto-resume held:")]
    assert len(held_lines) == 1, t.lines
    assert numeric_tokens(held_lines[0]) == numeric_tokens(
        f"retrying in {int(RETRY_INTERVAL_S / 60)} min"), (
            f"the held warning carries a number beyond the retry interval: "
            f"{held_lines[0]!r}")


# ------------------------------------------------------------ the helper

def _payload(hold) -> dict:
    return {"armed": {"id": "s1", "name": "n", "owed": 3}, "hold": hold,
            "recovering": False, "recovery": None}


@pytest.mark.parametrize("who", ["viewer", "syncer", "none"])
def test_the_helper_removes_site_detail_for_a_principal_without_the_cap(who):
    """``_redact_resume_arm_for``, for every principal that lacks
    CAP_VIEW_SITE_DERIVED: a viewer, a syncer, and no principal at all (fail
    closed). The key is absent, the rest of the hold and of the payload is
    unchanged, and the payload handed in is not mutated: it holds the
    service's own live hold.

    RED under mutant "fail open on no principal" (the helper returns the
    payload untouched when ``principal is None``), ``[none]`` only, observed
    verbatim:

        E   AssertionError: assert 'site_detail' not in {'reason': 'M42 is below its start floor', 'since': 1.0, 'site_detail': 'M42 is at 12 deg'}

    RED under mutant "the helper nulls the key", every case, observed
    verbatim:

        E   AssertionError: assert 'site_detail' not in {'reason': 'M42 is below its start floor', 'since': 1.0, 'site_detail': None}

    RED under mutant "strip in place", every case, at the live hold:

        E   KeyError: 'site_detail'
    """
    principal = None if who == "none" else principal_for_role(who)
    assert principal is None or not principal.has(CAP_VIEW_SITE_DERIVED)
    hold = {"reason": "M42 is below its start floor", "since": 1.0,
            "site_detail": "M42 is at 12 deg"}
    payload = _payload(hold)
    out = _redact_resume_arm_for(payload, principal)
    assert "site_detail" not in out["hold"]
    assert out["hold"] == {"reason": "M42 is below its start floor",
                           "since": 1.0}
    assert {k: v for k, v in out.items() if k != "hold"} == \
        {k: v for k, v in payload.items() if k != "hold"}
    assert hold["site_detail"] == "M42 is at 12 deg", "the live hold was mutated"


@pytest.mark.parametrize("who", ["operator", "admin"])
def test_control_the_helper_keeps_site_detail_for_a_holder(who):
    """CONTROL: a holder of CAP_VIEW_SITE_DERIVED gets the payload as it
    was built.

    RED under mutant "strip for everybody" (the holder's early return
    removed), both cases, observed verbatim:

        E   AssertionError: assert {'armed': {'id': 's1', 'name': 'n', 'owed': 3}, 'hold': {'reason': 'r'}, 'recovering': False, 'recovery': None} is {'armed': {'id': 's1', 'name': 'n', 'owed': 3}, 'hold': {'reason': 'r', 'site_detail': 'd'}, 'recovering': False, 'recovery': None}
    """
    principal = principal_for_role(who)
    assert principal.has(CAP_VIEW_SITE_DERIVED)
    payload = _payload({"reason": "r", "site_detail": "d"})
    assert _redact_resume_arm_for(payload, principal) is payload


@pytest.mark.parametrize("hold", [None, {"reason": "cloud cover 100%"}])
def test_control_a_hold_without_site_detail_is_untouched(hold):
    """CONTROL: no hold, or a hold that never had a site detail (a weather
    veto, a plan refusal), passes through for a viewer unchanged."""
    payload = _payload(hold)
    out = _redact_resume_arm_for(payload, principal_for_role("viewer"))
    assert out == _payload(hold)


def test_an_unexpected_hold_shape_fails_closed():
    """A hold that is not a dict cannot be key-stripped, so a viewer gets
    none rather than a shape the helper could not inspect (this module's
    fail-closed rule, ``_scrub_site_node``)."""
    out = _redact_resume_arm_for(_payload(["M42 is at 12 deg"]),
                                 Principal(role="viewer", caps=frozenset()))
    assert out["hold"] is None


# -------------------------------------------------------------- the rule

def test_the_rule_counts_a_number_that_moves_and_not_one_that_stays():
    """``tracking_tokens`` on fabricated texts, both halves of the rule: a
    number the same under both sites (a floor, a session's digits, the
    retry interval) is not a hit, and one that differs is, under each site.
    Digits glued to letters (a catalogue id, a uuid's hex) are not tokens.

    RED under mutant "one-sided rule" (``"b"`` computed as an empty list),
    observed verbatim:

        E   AssertionError: assert [] == ['6']

    RED under mutant "the rule cannot see", observed verbatim:

        E   AssertionError: assert [] == ['21']
    """
    a = "M42 is at 21 deg, below its 30 deg floor; retrying in 10 min; id 3fa9c2e1"
    b = "M42 is at 6 deg, below its 30 deg floor; retrying in 10 min; id 3fa9c2e1"
    found = tracking_tokens([a], [b])
    assert found["a"] == ["21"]
    assert found["b"] == ["6"]
    assert not any_tracking(tracking_tokens([a], [a]))
    assert "42" not in numeric_tokens(a) and "3" not in numeric_tokens(a)
