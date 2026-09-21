"""`state` and `running` must not contradict each other (#117).

Between `SequenceEngine.start()` returning and `_run` reaching its first
`_set_state`, `engine.state` still says `state: "idle"` while `engine.running`
is already True. That window holds the safety gates, the cooling wait, the
slew, the autofocus and the plate solve -- minutes on this rig -- and a client
choosing between the two fields gets a different answer depending on which one
it trusts. My own night supervisor keys on `state` against a working set, so a
starting run read as not-working and the guard meant to notice a stalled run
was watching a sequence it believed was not running at all.

One instance of the recurring class: a verdict field contradicting the
evidence field beside it, the verdict wearing the obvious name. #111 was
`is_valid: true` over an advisory saying the axes were questionable; #112 was
`cloudy: false` over a reason string saying cloudy.

The fix is at the SEAM, so these drive the seam. The last two cases go
through the real routes with the app's own engine, because a helper agreeing
with itself proves nothing about what a client is served -- and there are two
routes, each of which had its own copy of the merge.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


class _Engine:
    """Only what the envelope reads. A real engine cannot be parked in the
    start window on demand -- the window closes when its task takes a turn --
    and a test that raced it would be the flakiest guard in the suite."""

    def __init__(self, state: dict, running: bool, paused: bool = False):
        self.state = state
        self.running = running
        self.paused = paused


def test_a_starting_run_is_not_served_as_idle():
    """The defect, exactly as `start()` leaves it.

    MUTATION: `if False and payload.get("state") == "idle":`. Observed:
    3 failed / 7 passed - this case and the two route cases, all three
    reading "idle" beside running true.
    """
    env = app_module._sequence_envelope(
        _Engine({"state": "idle", "progress": {"frames_done": 0}}, running=True))
    assert env["running"] is True
    assert env["state"] != "idle", (
        "a run whose task is live was served as idle; a supervisor keyed on "
        "`state` sees no run at all")
    assert env["state"] == "running"


def test_an_idle_rig_is_still_idle():
    """The other half, and the one a too-eager rewrite breaks: with no task,
    `idle` is the honest answer and must survive.

    MUTATION: drop the `engine.running` term from the condition. Observed:
    this case alone fails, 1 failed / 9 passed - a rig with no task was
    served "running".
    """
    env = app_module._sequence_envelope(
        _Engine({"state": "idle", "progress": {"frames_done": 12}}, running=False))
    assert env["state"] == "idle"
    assert env["running"] is False
    assert env["progress"] == {"frames_done": 12}, (
        "the envelope dropped a field the engine published")


@pytest.mark.parametrize("state", ["aborting", "aborted", "complete", "error",
                                   "paused", "holding"])
def test_a_state_the_engine_published_is_never_overwritten(state):
    """`running` stays True through the teardown an abort waits on, so a
    rewrite keyed on liveness alone would relabel "aborting" as "running" --
    the same lie this removes from the start path, moved to the abort path.
    `holding` matters for the same reason and has its own history: a
    per-frame publish once overwrote it and a cloud hold read as a normal
    night (see `_set_state`).

    MUTATION: rewrite whenever `engine.running`, without the `== "idle"`
    test. Observed: 6 failed / 4 passed - every parameter here, each naming
    the state it lost.
    """
    env = app_module._sequence_envelope(
        _Engine({"state": state}, running=True, paused=state == "paused"))
    assert env["state"] == state, (
        f"the engine published {state!r} and the seam served {env['state']!r}")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated app: config and reports under tmp, no real rig touched."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_the_route_serves_the_agreed_envelope(client, monkeypatch):
    """Through the real route, so the seam is graded and not just the helper.

    The engine is the app's own; only its two fields are set, to the exact
    pair `start()` leaves behind. `running` is a property on the class, so it
    is patched there rather than on the instance.

    MUTATION: revert the route body to `engine.state | {...}`. Observed:
    this case alone fails, 1 failed / 9 passed - the response carried "idle"
    beside running true.
    """
    from astrodeck.sequence.engine import SequenceEngine
    engine = app_module.engine
    monkeypatch.setattr(engine, "state",
                        {"state": "idle", "progress": {"frames_done": 3}})
    monkeypatch.setattr(SequenceEngine, "running", property(lambda self: True))
    r = client.get("/api/sequence/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["running"] is True
    assert body["state"] == "running", (
        f"the route served {body['state']!r} beside running true")
    assert body["progress"] == {"frames_done": 3}


def test_the_monitor_snapshot_serves_the_same_envelope(client, monkeypatch):
    """The second seam. It had its own copy of the merge, and a fix applied to
    one route only would leave the cold-load hydration disagreeing with the
    poll it is supposed to stand in for.

    MUTATION: revert only the monitor snapshot's line. Observed: this case
    alone fails, 1 failed / 9 passed - the route case above stayed green,
    which is the split this case exists to catch.
    """
    from astrodeck.sequence.engine import SequenceEngine
    engine = app_module.engine
    monkeypatch.setattr(engine, "state", {"state": "idle"})
    monkeypatch.setattr(SequenceEngine, "running", property(lambda self: True))
    r = client.get("/api/monitor/snapshot")
    assert r.status_code == 200, r.text
    seq = r.json()["sequence"]
    assert seq["running"] is True
    assert seq["state"] == "running", (
        f"the monitor snapshot served {seq['state']!r} beside running true")
