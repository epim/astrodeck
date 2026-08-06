"""The ``update`` WS frame must carry the apply gate, not a hole where it was.

UX review finding 22. ``supervised`` / ``can_apply`` / ``apply_blocked_reason``
were stamped on by GET /api/update/status ALONE, while every ``update`` WS event
is ``update_state.snapshot()`` verbatim -- and the client REPLACES its whole
update slice on that event. So each phase tick and each poller check arrived
with the three gate keys absent and erased them: the Upgrade button re-armed in
the middle of a sequence, and both the blocked-reason line and the unsupervised
warning vanished.

Exactly the partial-overwrite class ``Hub.publish_safety`` documents for the
``safety`` event ("the cached reading fills in any field the caller omitted --
never a partial overwrite"). Same cure, one layer down: make the payload whole
so there is nothing to clobber.
"""
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.events import bus
from astrodeck.update import github, service as SVC
from astrodeck.update.state import update_state

GATE_KEYS = ("supervised", "can_apply", "apply_blocked_reason")


@pytest.fixture(autouse=True)
def _clean_state():
    update_state.set_available(None, "")
    update_state.set_phase("idle")
    update_state.set_result(None)
    SVC.reset_service()
    SVC.reset_exit_state()
    yield
    SVC.reset_service()
    SVC.reset_exit_state()


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    with TestClient(app_module.create_app()) as c:
        yield c


def test_the_snapshot_carries_the_same_gate_the_route_computes(client):
    route = client.get("/api/update/status").json()
    # PRECONDITION: if the ROUTE stopped answering the gate there is no
    # difference left to detect and the comparison below proves nothing.
    for k in GATE_KEYS:
        assert k in route, f"/api/update/status no longer reports {k}"

    snap = update_state.snapshot()
    for k in GATE_KEYS:
        assert k in snap, (
            f"snapshot() drops {k!r}, so the WS event that IS this snapshot "
            "erases it on every client that applies the frame")
        assert snap[k] == route[k], (
            f"the WS payload and the route disagree about {k!r}: "
            f"{snap[k]!r} vs {route[k]!r}")


def test_a_published_update_frame_does_not_erase_the_gate(client, monkeypatch):
    """The real vector: a phase/poller frame, not a REST read."""
    route = client.get("/api/update/status").json()
    assert route["can_apply"] is False and route["apply_blocked_reason"], (
        "this fixture is supposed to be BLOCKED (unsupervised, no pinned key) -- "
        "with nothing to lose, a clobber would be invisible")

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(bus, "publish",
                        lambda type, **data: published.append((type, data)))

    async def fake_latest(*a, **k):
        return github.ReleaseInfo("0.2.0", "v0.2.0", "notes", "http://a",
                                  "http://s", "http://g", False)
    monkeypatch.setattr("astrodeck.update.github.latest_release", fake_latest)

    assert client.post("/api/update/check").status_code == 200
    frames = [d for t, d in published if t == "update"]
    assert frames, "the check published no update frame -- nothing under test"

    frame = frames[-1]
    for k in GATE_KEYS:
        assert k in frame, (
            f"the update WS frame omits {k!r} -- applying it deletes the gate "
            "the client was holding")
    assert frame["can_apply"] is False, (
        "the WS frame re-arms Upgrade on a server that just said it cannot apply")
    assert frame["apply_blocked_reason"], (
        "blocked with no reason: the client can only say 'no' and not why")


def test_an_unbound_gate_omits_the_keys_rather_than_inventing_them():
    """No provider -> no claim.

    ``false`` would park Upgrade behind a block nobody can explain, and ``null``
    reads as present-but-unknown in JS and re-arms it. An ABSENT key is the one
    answer the client already handles: UpdatePanel re-asks the route."""
    SVC.reset_service()
    snap = update_state.snapshot()
    assert not any(k in snap for k in GATE_KEYS), snap
    # ...and the rest of the snapshot is untouched by the gate's absence.
    for k in ("current", "phase", "progress", "channel", "last_result"):
        assert k in snap
