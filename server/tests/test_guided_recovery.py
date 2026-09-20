import time

import pytest

from astrodeck.guided_recovery import GuidedCheckpoint
from astrodeck.events import EventBus


def setup_checkpoint():
    checkpoint = GuidedCheckpoint()
    identity = {"night": "2026-09-16", "site": {"latitude": 35}, "horizon": [[0, 20]],
                "devices": {"mount": 1}, "profile": "test", "mode": "sim", "providers": "native"}
    checkpoint.observe(identity, None, {})
    checkpoint.complete("location", None, {})
    checkpoint.complete("horizon", None, {})
    return checkpoint, identity


def focus_done():
    return {"state": "done", "best": {"position": 1000, "hfr": 2.2}, "_observed_at": time.time()}


def polar_done():
    return {"state": "idle", "phase": "adjusting", "reading_ts": time.time(), "total_error": 1.2,
            "source": "astrodeck", "flags": [], "stale_updates": 0}


def test_review_checkpoint_is_shared_but_contains_no_coordinates():
    c, identity = setup_checkpoint()
    c.observe(identity, None, {})
    recovered = c.snapshot()
    assert recovered["facts"] == {"location": True, "horizon": True, "focus": False, "alignment": False}
    assert "latitude" not in str(recovered)
    assert len(recovered["context"]) == 64
    recovered["facts"]["focus"] = True
    assert c.facts["focus"] is False


@pytest.mark.parametrize("key,value", [("night", "tomorrow"), ("site", {"latitude": 40}),
    ("devices", {"mount": 2}), ("profile", "different"), ("mode", "real")])
def test_night_site_and_equipment_changes_clear_checks(key, value):
    c, identity = setup_checkpoint()
    c.observe({**identity, key: value}, None, {})
    assert not any(c.facts.values())


def test_changed_horizon_keeps_the_already_reviewed_location():
    c, identity = setup_checkpoint()
    c.observe({**identity, "horizon": [[0, 30]]}, None, {})
    assert c.facts["location"] and not c.facts["horizon"]
    c.complete("horizon", None, {})
    assert c.facts["horizon"]


def test_new_controller_never_adopts_an_old_checkpoint():
    c, identity = setup_checkpoint()
    replacement = GuidedCheckpoint()
    replacement.observe(identity, None, {})
    assert replacement.boot_id != c.boot_id and replacement.context != c.context
    assert not any(replacement.facts.values())


@pytest.mark.parametrize("focus,position", [(None, 1000), ({"state":"running"},1000),
    ({"state":"done","best":{"hfr":float("nan"),"position":1000}},1000),
    ({"state":"done","best":{"hfr":2,"position":1000},"_observed_at":1},1000)])
def test_posted_focus_claim_needs_fresh_engine_evidence(focus, position):
    c, _ = setup_checkpoint()
    with pytest.raises(ValueError): c.complete("focus", focus, {}, focuser_position=position)


def test_focus_recovery_requires_the_same_current_focuser_position():
    c, identity = setup_checkpoint()
    focus = focus_done()
    c.complete("focus", focus, {}, focuser_position=1000)
    c.observe(identity, focus, {}, focuser_position=1000)
    assert c.facts["focus"]
    c.observe(identity, focus, {}, focuser_position=1001)
    assert not c.facts["focus"]


def test_new_field_invalidates_prior_focus_evidence_even_at_same_focuser_position():
    c, _ = setup_checkpoint()
    focus = focus_done()
    c.complete("focus", focus, {}, focuser_position=1000)
    c.invalidate("focus")
    focus["_observed_at"] = c._focus_after - 1
    with pytest.raises(ValueError): c.complete("focus", focus, {}, focuser_position=1000)


def test_new_success_between_polls_can_be_certified_without_an_extra_autofocus():
    c, identity = setup_checkpoint()
    old = focus_done()
    c.complete("focus", old, {}, focuser_position=1000)
    new = {**focus_done(), "points": [{"position":1000,"hfr":2.2}]}
    c.observe(identity, new, {}, focuser_position=1000)
    c.complete("focus", new, {}, focuser_position=1000)
    assert c.facts["focus"]


@pytest.mark.parametrize("change", [{"state":"running"}, {"phase":"measuring"}, {"total_error":3},
    {"total_error":float("nan")}, {"flags":["bad_fit"]}, {"stale_updates":1},
    {"reading_ts":1}, {"reading_ts":time.time()+1000}, {"source":"sim"}])
def test_alignment_claim_requires_good_stopped_measurement(change):
    c, _ = setup_checkpoint()
    with pytest.raises(ValueError): c.complete("alignment", None, {**polar_done(),**change})


def test_alignment_is_invalidated_when_a_new_run_starts():
    c, identity = setup_checkpoint()
    p = polar_done()
    c.complete("alignment", None, p)
    c.observe(identity, None, p)
    assert c.facts["alignment"]
    c.observe(identity, None, {**p,"state":"running"})
    assert not c.facts["alignment"]


def test_measurement_before_a_physical_setup_change_cannot_certify_new_setup():
    c, _ = setup_checkpoint()
    p = polar_done()
    c.invalidate("horizon")
    p["reading_ts"] = c._alignment_after - 1
    c.complete("horizon", None, {})
    with pytest.raises(ValueError): c.complete("alignment", None, p)


def test_operation_cache_is_bounded_copied_and_contains_terminal_results():
    bus = EventBus(persist=False)
    points = [{"position":1}]
    bus.publish("focus",state="running",points=points)
    points[0]["position"] = 999
    assert bus.operation_snapshots["focus"]["points"][0]["position"] == 1
    bus.publish("focus",state="done",best={"hfr":2})
    bus.publish("status",mode="sim")
    assert list(bus.operation_snapshots) == ["focus"]
    assert bus.operation_snapshots["focus"]["state"] == "done"


async def test_checkpoint_routes_use_actual_hub_context_and_reject_forged_focus(tmp_path, monkeypatch):
    import astrodeck.api.app as appmod
    from astrodeck.hub import Hub
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    hub = Hub()
    monkeypatch.setattr(appmod, "hub", hub)
    monkeypatch.setattr(appmod.bus, "operation_snapshots", {})
    app = appmod.create_app()
    # Direct endpoint calls test the real route's state logic without creating
    # a server/listener or running startup's hardware auto-connect.
    get = next(r.endpoint for r in app.routes if getattr(r,"path",None) == "/api/guided/checkpoint" and "GET" in r.methods)
    post = next(r.endpoint for r in app.routes if getattr(r,"path",None) == "/api/guided/checkpoint" and "POST" in r.methods)
    initial = await get()
    again = await get()
    assert initial["context"] == again["context"]
    payload = {"context":initial["context"],"revision":initial["revision"],"action":"complete","fact":"location"}
    reviewed = await post(appmod.GuidedCheckpointBody(**payload))
    assert reviewed["facts"]["location"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as stale:
        await post(appmod.GuidedCheckpointBody(**payload))
    assert stale.value.status_code == 409
    reviewed = await post(appmod.GuidedCheckpointBody(**{**payload,"revision":reviewed["revision"],"fact":"horizon"}))
    with pytest.raises(HTTPException) as forged:
        await post(appmod.GuidedCheckpointBody(**{**payload,"revision":reviewed["revision"],"fact":"focus"}))
    assert forged.value.status_code == 409
