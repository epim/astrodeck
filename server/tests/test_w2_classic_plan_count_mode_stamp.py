"""#141 (backlog WP-19(c), owner-approved 2026-09-30): a classic Plan-tab
plan defaults to counting ACCEPTED subs, not every attempt.

``SequencePlan.count_mode``'s bare pydantic default stays "attempts": the
model is constructed at 377 sites across the tree, and flipping the default
itself regressed two of a 17-file sample the coder tried it against. The
classic UI's own new-plan default already sends ``count_mode: "accepted"``
explicitly (a prior UX review, #30), so the only gap is a body that omits
the field entirely -- an older client, a hand-built request, or a saved
plan from before that UI fix. ``/api/plans`` (save) and ``/api/sequence/
start`` (the classic Plan tab's direct start) both stamp "accepted" onto
such a body, via `app.py`'s `_accepted_count_mode_if_omitted`.

Each case names the mutant it kills: `_accepted_count_mode_if_omitted`'s
stamp removed (``save_plan`` reverted to ``body.plan`` directly) for the
save route, and the ``dumped["count_mode"] = "accepted"`` line deleted from
``sequence_start`` for the start route. Run in a byte backup of this
worktree, restored byte-identically (sha256-verified) after.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore


def _target(name: str = "M31") -> dict:
    return {"name": name, "ra_hours": 0.71, "dec_deg": 41.27,
            "steps": [{"exposure_s": 5.0, "count": 1}]}


# --------------------------------------------------------------- the save route


@pytest.fixture
def save_client(tmp_path, monkeypatch):
    """Mirrors test_app_preflight.py's ``client`` fixture: an isolated
    TestClient exercising the real ``/api/plans`` save and read routes."""
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
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_save_plan_stamps_accepted_when_the_body_omits_count_mode(save_client):
    """RED under mutant "the save stamp removed" (``save_plan``'s
    ``plan = _accepted_count_mode_if_omitted(body.plan)`` reverted to
    ``plan = body.plan``), observed:

        AssertionError: a saved plan whose body never named count_mode must
        default to "accepted"
        assert 'attempts' == 'accepted'
    """
    payload = {"plan": {"name": "No Count Mode Named", "guide": False,
                        "targets": [_target()]}}
    r = save_client.post("/api/plans", json=payload)
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    read = save_client.get(f"/api/plans/{plan_id}")
    assert read.status_code == 200, read.text
    assert read.json()["count_mode"] == "accepted", (
        "a saved plan whose body never named count_mode must default to "
        "\"accepted\"")


def test_save_plan_keeps_an_explicit_count_mode(save_client):
    """Control: a body that DOES name count_mode is never overridden, in
    either direction -- the stamp is for an omission only."""
    for explicit in ("attempts", "accepted"):
        payload = {"plan": {"name": f"Explicit {explicit}", "guide": False,
                            "count_mode": explicit, "targets": [_target()]}}
        r = save_client.post("/api/plans", json=payload)
        assert r.status_code == 200, r.text
        read = save_client.get(f"/api/plans/{r.json()['id']}")
        assert read.json()["count_mode"] == explicit, (
            f"an explicit count_mode={explicit!r} was overridden")


# -------------------------------------------------------------- the start route


@pytest.fixture
def start_client(tmp_path, monkeypatch):
    """Mirrors test_session_quota.py's ``api_client`` fixture: the real
    ``/api/sequence/start`` route, with the camera/engine.start stubbed and
    solar bypassed, capturing the exact plan object the route hands the
    engine."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))
    app = app_module.create_app()
    with TestClient(app) as c:
        monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
        monkeypatch.setattr(app_module.hub, "require", lambda role: object())
        started: dict = {"plan": None}

        def _start(plan, **kw):
            started["plan"] = plan

        monkeypatch.setattr(app_module.engine, "start", _start)
        c.started = started
        yield c


def _start_payload(**extra) -> dict:
    return {"name": "q", "guide": False, "targets": [_target()], **extra}


def test_sequence_start_stamps_accepted_when_the_body_omits_count_mode(
        start_client):
    """RED under mutant "the start stamp removed" (``sequence_start``'s
    ``dumped["count_mode"] = "accepted"`` line deleted), observed:

        AssertionError: a started plan whose body never named count_mode
        must default to "accepted"
        assert 'attempts' == 'accepted'
    """
    r = start_client.post("/api/sequence/start", json=_start_payload())
    assert r.status_code == 200, r.text
    plan = start_client.started["plan"]
    assert plan is not None, "premise: the route reached engine.start"
    assert plan.count_mode == "accepted", (
        "a started plan whose body never named count_mode must default to "
        "\"accepted\"")


def test_sequence_start_keeps_an_explicit_count_mode(start_client):
    """Control: a body that DOES name count_mode is never overridden."""
    for explicit in ("attempts", "accepted"):
        r = start_client.post(
            "/api/sequence/start", json=_start_payload(count_mode=explicit))
        assert r.status_code == 200, r.text
        plan = start_client.started["plan"]
        assert plan.count_mode == explicit, (
            f"an explicit count_mode={explicit!r} was overridden")
