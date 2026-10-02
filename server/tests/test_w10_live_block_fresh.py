# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#665: ``GET /api/sequence/state``'s ``live`` chip (meridian ETA, sensor
temperature) must be recomputed FRESH on every read, not served from
whatever ``SequenceEngine._set_state`` cached at the last PUBLISHED
transition.

``_live_block`` only ever runs inside ``_set_state``, i.e. only on an
actual transition (a frame boundary, a hold starting or ending, ...).
During any open-ended wait with no transition in the middle -- a meridian
wait, a cloud hold, an idle park-hold -- ``engine.state["live"]`` is simply
whatever that one instant cached, unrefreshed for the whole span of the
wait. Found on the harness's clocked meridian-straddle night
(test_s7_sim_meridian_straddle.py, #189 S7): a GET read at +1000.0 s inside
a 720.0 s-to-1384.079 s meridian wait returned ``live.meridian_eta_s: 795``,
the exact value cached at the wait's start, even though ``hub.
last_meridian`` read FRESH at that same instant already said
``{"status": "not tracking", "hours_to_flip": None}`` (#519's own fix,
confirmed live and correct -- only the served ``live`` chip lagged it).

The fix asks the engine's own ``_live_block`` (already documented
"best-effort, sync, no device I/O") again at ``_sequence_envelope``, the
one seam both polling consumers (this route and the monitor snapshot)
share, rather than editing ``engine.py`` (WP-56 owns it this wave) or
teaching every wait to re-publish on a timer.

A REAL ``SequenceEngine`` is used throughout (not a double), bound to a
bare ``SimpleNamespace`` hub: ``_live_block`` touches only
``self.plan``, ``self.hub.last_meridian``, ``self.hub.last_frame`` and
``self._policy`` (its own docstring: "no device I/O"), so this drives the
production code under test directly rather than asserting a fake agrees
with itself.
"""
from __future__ import annotations

from types import SimpleNamespace

import astrodeck.api.app as app_module
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.policy import resolve_policy


def _held_engine(*, cached_live: dict | None) -> SequenceEngine:
    """A ``SequenceEngine`` parked mid-hold: ``plan`` and ``_policy`` set as
    ``start()`` would leave them, ``state`` set directly to what the LAST
    ``_set_state`` call published (the way test_sequence_envelope_agrees.py's
    own fakes are built) -- never run through the real async loop, since
    only the envelope's read side is under test here."""
    hub = SimpleNamespace(last_meridian=None, last_frame=None)
    plan = SequencePlan(name="hold", guide=False, meridian_flip=True,
                        targets=[Target(
                            name="NGC 7331", ra_hours=22.6167, dec_deg=34.4167,
                            steps=[ExposureStep(filter="L", exposure_s=60.0,
                                                count=1)])])
    engine = SequenceEngine(hub)
    engine.plan = plan
    engine._policy = resolve_policy(plan, None)   # meridian_flip_warn_min=15
    engine.state = {"state": "holding", "hold": "meridian"}
    if cached_live is not None:
        engine.state["live"] = cached_live
    return engine


def test_a_published_live_block_goes_stale_through_an_unrepublished_wait():
    """Reproduces #665's own premise directly: ``_live_block`` called again,
    fresh, disagrees with what a prior publish cached, because the mount's
    reported meridian state moved on (tracking stopped, #519) with nothing
    re-publishing in between -- exactly test_s7_sim_meridian_straddle.py's
    664-second gap with zero republishes. This is the STALE READ the fix
    below removes; it is not itself the regression test (that needs the
    route/seam), but it pins the premise so a change to ``_live_block``
    that broke this would be caught here first."""
    engine = _held_engine(cached_live={"meridian_eta_s": 795})
    stale = dict(engine.state["live"])
    engine.hub.last_meridian = {"status": "not tracking", "hours_to_flip": None}
    fresh = engine._live_block()
    assert fresh is None, (
        "hub.last_meridian already says not tracking; _live_block must not "
        "still report a countdown")
    assert stale == {"meridian_eta_s": 795}, "sanity: the cached object itself is untouched"


def test_the_envelope_serves_live_fresh_not_the_cached_transition():
    """The actual fix, at the seam ``_sequence_envelope`` owns (#665): a GET
    minutes into the wait must reflect ``hub.last_meridian`` AS OF THE GET,
    not as of the transition that started the wait.

    ``engine.running`` is a read-only property (``self._task is not None``)
    and is left False here (this engine is never started) -- irrelevant to
    what is under test, since the live recompute is unconditional and
    ``state`` ("holding") is never rewritten regardless of ``running``.

    MUTANT (verified): reverting ``_sequence_envelope`` to
    ``payload = engine.state | {"running": engine.running, "paused":
    engine.paused}`` with the live-recompute block removed turns this red
    with:
        AssertionError: {'meridian_eta_s': 795} != None : the envelope
        served the countdown cached at the last transition through a wait
        where hub.last_meridian already says 'not tracking'
    """
    engine = _held_engine(cached_live={"meridian_eta_s": 795})
    # The wait's whole point (#665): the mount moved on, nothing republished.
    engine.hub.last_meridian = {"status": "not tracking", "hours_to_flip": None}

    env = app_module._sequence_envelope(engine)
    assert "live" not in env, (
        f"{env.get('live')!r} != None : the envelope served the countdown "
        f"cached at the last transition through a wait where "
        f"hub.last_meridian already says 'not tracking'")


def test_the_envelope_tracks_a_changing_countdown_not_just_a_clear():
    """The fresh value is not a special "clear to empty" case: a GET later
    in the SAME wait, with the flip still ahead but closer, must show the
    NEW number, not the one cached when the wait began."""
    engine = _held_engine(cached_live={"meridian_eta_s": 720})
    engine.hub.last_meridian = {"flip_enabled": True, "hours_to_flip": 300 / 3600.0}

    env = app_module._sequence_envelope(engine)
    assert env["live"]["meridian_eta_s"] == 300, env.get("live")


def test_sensor_temperature_is_also_recomputed_fresh():
    """The other half of ``live`` (#665 names both chips): a camera reading
    taken during the hold must replace a stale cached temperature too."""
    engine = _held_engine(cached_live={"sensor_temp_c": -10.0})
    engine.hub.last_frame = SimpleNamespace(temperature_c=-9.4)

    env = app_module._sequence_envelope(engine)
    assert env["live"]["sensor_temp_c"] == -9.4, env.get("live")


def test_an_engine_double_with_no_live_block_is_unaffected():
    """test_sequence_envelope_agrees.py's lightweight ``_Engine`` fake
    exposes only ``state``/``running``/``paused`` -- no ``_live_block`` --
    and this fix must not require every caller of ``_sequence_envelope`` to
    grow one."""
    class _Bare:
        def __init__(self):
            self.state = {"state": "idle", "progress": {"frames_done": 3}}
            self.running = False
            self.paused = False

    env = app_module._sequence_envelope(_Bare())
    assert "live" not in env
    assert env["progress"] == {"frames_done": 3}
