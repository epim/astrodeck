# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#595, backlog ruling D-04 (owner-approved 2026-09-30): starting a run
disarms every OTHER session's auto_resume through engine.start's singleton,
and this used to happen in silence -- no log line, no response field. The
owner's explicitly-armed NGC 1499 mosaic lost its auto-resume on 2026-09-29
when a 7331 starter began a run, and the only way to notice was a manual
read of /api/sessions five minutes later.

WP-31 part (a) is the server half of the fix D-04 rules: a bus.log warning
naming each disarmed session, and a ``disarmed`` list in ``engine.start``'s
own return value, which ``api/app.py``'s start routes fold into their JSON
response as ``disarmed``. The UI half (showing it) is WP-65, wave 5.

Real sim hub + engine (test_resume_arm.py's precedent), no FakeHub -- the
session store writes for real, so the singleton under test is the real one.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def _wait_for(predicate, timeout=10.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def _plan(name: str) -> SequencePlan:
    # No centring/focus/guiding: this test is about the singleton, not the
    # capture loop, and the fewer sim calls a hop makes the faster it banks
    # its first frame.
    return SequencePlan(name=name, guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name=name, ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=3)])])


def _warning_lines(fragment_a: str, fragment_b: str) -> list[dict]:
    """Every warning log line carrying both fragments, from the whole ring
    (never an index slice: the ring is a deque(maxlen=200) shared by the
    whole test session, so a captured "before" length can equal "after"
    once the ring is full, and a slice from it would silently see
    nothing)."""
    return [e for e in bus.log_history
            if e["data"].get("level") == "warning"
            and fragment_a in e["data"].get("message", "")
            and fragment_b in e["data"].get("message", "")]


async def test_start_returns_and_logs_the_session_it_disarmed(sim_hub):
    """The #595 shape exactly: an armed dormant session ('mosaic'), then a
    fresh start of something else ('nightly'). The mosaic's auto_resume
    must still end up False (today's singleton, unchanged) -- but now the
    start's return value names it, and a warning says so.

    NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    delete the ``disarmed.append({"id": other.id, "name": ...})`` line
    inside ``SequenceEngine.start``'s singleton loop (engine.py), leaving
    ``other.auto_resume = False`` / ``session_store.save(other)`` in place.
    This test then fails with:

        AssertionError: assert [] == [{'id': '...', 'name': 'mosaic'}]

    -- the exact pre-fix bug (#595): the session is disarmed just the same,
    but nothing says so."""
    engine = SequenceEngine(sim_hub)

    # A real dormant, armed session: start it, bank a frame, abort, arm it
    # (test_resume_arm.py's own `_dormant_armed` shape).
    engine.start(_plan("mosaic"))
    mosaic_id = engine._session.id
    assert await _wait_for(lambda: engine._frames_done >= 1)
    await engine.abort()
    mosaic = session_store.load(mosaic_id)
    assert mosaic.status == "dormant"
    mosaic.auto_resume = True
    session_store.save(mosaic)

    disarmed = engine.start(_plan("nightly"))

    assert disarmed == [{"id": mosaic_id, "name": "mosaic"}]
    assert session_store.load(mosaic_id).auto_resume is False
    # The new session is armed by default, same rule as before (#189
    # hardening): the singleton is about which ONE session is armed, not
    # whether anything is.
    assert engine._session.auto_resume is True

    hits = _warning_lines("mosaic", "nightly")
    assert len(hits) == 1, bus.log_history
    assert "disarmed" in hits[0]["data"]["message"]


async def test_start_returns_empty_list_when_nothing_was_armed(sim_hub):
    """No armed session anywhere -> no disarm, no claim of one. A start
    route (app.py) only adds ``disarmed`` to its response when this list is
    non-empty, so an empty list here is what keeps every existing response
    byte-identical."""
    engine = SequenceEngine(sim_hub)
    assert engine.start(_plan("solo")) == []


async def test_start_does_not_name_itself_or_a_dormant_unarmed_session(sim_hub):
    """A dormant session that was never armed is not news, and a run never
    disarms the session it is itself making."""
    engine = SequenceEngine(sim_hub)
    engine.start(_plan("earlier"))
    earlier_id = engine._session.id
    assert await _wait_for(lambda: engine._frames_done >= 1)
    await engine.abort()
    earlier = session_store.load(earlier_id)
    assert earlier.status == "dormant"
    assert earlier.auto_resume is False       # never armed

    disarmed = engine.start(_plan("later"))

    assert disarmed == []
    assert session_store.load(earlier_id).auto_resume is False


# --------------------------------------------------------------- app.py route

@pytest.fixture
def client(tmp_path, monkeypatch):
    """``/api/sequence/start``'s own wiring (app.py), with ``engine.start``
    stubbed so this exercises the ROUTE's ``disarmed`` plumbing in
    isolation from the singleton itself (covered above against the real
    session store) -- the same split test_app_preflight.py's fixture uses."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)

    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def _cal_plan_payload():
    # Calibration-only: skips the below-horizon preflight entirely (it never
    # slews), so this route test needs no configured site at all.
    return {
        "name": "darks",
        "guide": False,
        "targets": [{
            "name": "darks", "ra_hours": 0.0, "dec_deg": 0.0,
            "calibration": True,
            "steps": [{"exposure_s": 1.0, "count": 3, "frame_type": "Dark"}],
        }],
    }


def test_sequence_start_route_adds_disarmed_when_the_engine_disarmed_someone(
        client, monkeypatch):
    """NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``sequence_start`` (app.py), change ``if disarmed:`` to ``if False:``
    around the ``out["disarmed"] = disarmed`` line. This test then fails
    with:

        KeyError: 'disarmed'

    -- the route never learns the engine disarmed anyone."""
    monkeypatch.setattr(
        app_module.engine, "start",
        lambda plan, **kw: [{"id": "abc123", "name": "mosaic"}])

    r = client.post("/api/sequence/start", json=_cal_plan_payload())

    assert r.status_code == 200, r.text
    assert r.json()["disarmed"] == [{"id": "abc123", "name": "mosaic"}]


def test_sequence_start_route_omits_disarmed_when_nothing_was(
        client, monkeypatch):
    monkeypatch.setattr(app_module.engine, "start", lambda plan, **kw: [])

    r = client.post("/api/sequence/start", json=_cal_plan_payload())

    assert r.status_code == 200, r.text
    # Absent, not an empty list: "every other answer is unchanged" (the same
    # convention `below_horizon` already keeps on this exact route).
    assert "disarmed" not in r.json()
