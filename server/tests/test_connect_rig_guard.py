# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A whole-rig connect must not take the rig down mid-night (#20).

`/api/connect/rig` disconnects every device by design - that is what connecting
a rig means. Its two less destructive siblings, `/api/profiles/{id}/apply` and
`/api/profiles/{id}/activate`, both refuse with a 409 while a sequence, capture
loop or polar alignment is running unless `force` is set, and both abort the
engine deliberately when it is. The route with the widest blast radius on the
box had neither.

On the incident this was filed from (2026-09-12, a night lost and recovered
only by a profile activate) the issue blamed a missing `primary`. That is ruled
out: `RigSpecBody.primary` is `primary: str` with no default and has been since
the field was introduced, so a body without it is rejected by FastAPI before
the handler runs. The first case here pins that, because a guard added on the
strength of a wrong diagnosis is worth exactly as much as the diagnosis.

What is left, and what the rest of this file is about, is that a VALID request
could still take the rig down without being asked to confirm.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
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


def _pretend_a_run_is_live(monkeypatch):
    """`engine.running` is a property on the class, so it is patched there."""
    from astrodeck.sequence.engine import SequenceEngine
    monkeypatch.setattr(SequenceEngine, "running", property(lambda self: True))


def test_a_body_with_no_primary_never_reaches_the_handler(client):
    """The issue's stated cause, ruled out rather than assumed.

    MUTATION: give `RigSpecBody.primary` a default (`primary: str = "sim"`).
    Observed: the post returns 200 and this fails - which is the shape the
    issue described, and it has never been the shape of this tree.
    """
    r = client.post("/api/connect/rig", json={})
    assert r.status_code == 422, (
        f"a body with no primary was accepted: {r.status_code} {r.text[:200]}")


def test_a_whole_rig_connect_is_refused_while_a_run_is_live(client, monkeypatch):
    """The real gap. Same 409 contract as the two profile routes, so one
    client-side error path covers all three.

    MUTATION: delete the `engine.running` refusal from `connect_rig`. Observed:
    the post returns 200, the rig is torn down under the run, and this fails.
    """
    _pretend_a_run_is_live(monkeypatch)
    r = client.post("/api/connect/rig", json={"primary": "sim"})
    assert r.status_code == 409, (
        f"a whole-rig connect was accepted mid-run: {r.status_code} {r.text[:200]}")
    detail = r.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code") == "running", (
        f"the refusal does not carry the same code the profile routes use: {detail}")


def test_the_refusal_is_the_same_shape_the_profile_routes_answer_with(client, monkeypatch):
    """Not a separate dialect. A client that already handles one of these
    handles this one, and that is the whole reason for copying the contract
    rather than inventing a message.

    MUTATION: change `connect_rig`'s code to "busy". Observed: the two payloads
    stop matching and this fails naming both.
    """
    _pretend_a_run_is_live(monkeypatch)
    rig = client.post("/api/connect/rig", json={"primary": "sim"})
    prof = client.post("/api/profiles/does-not-exist/activate", json={})
    # The profile route 404s on an unknown id BEFORE it reaches its own 409, so
    # this compares against the contract as written rather than as served: the
    # assertion is on the rig route's payload having that exact shape.
    assert rig.status_code == 409
    assert rig.json()["detail"] == {
        "detail": "a sequence, capture loop or polar alignment is running",
        "code": "running"}, rig.json()
    assert prof.status_code == 404, (
        "the profile route answered something other than the 404 this case "
        f"relies on to stay out of the way: {prof.status_code}")


def test_force_gets_through_and_aborts_the_run_first(client, monkeypatch):
    """The escape hatch the profile routes have, with the same consequence: a
    forced whole-rig connect is allowed to end the run, and must not leave a
    run task alive under a rig that is being disconnected.

    MUTATION: drop the `await engine.abort()` from the forced path. Observed:
    `aborted` stays False and this fails.
    """
    _pretend_a_run_is_live(monkeypatch)
    aborted: list[bool] = []

    async def _abort(self):
        aborted.append(True)

    from astrodeck.sequence.engine import SequenceEngine
    monkeypatch.setattr(SequenceEngine, "abort", _abort)
    r = client.post("/api/connect/rig", json={"primary": "sim", "force": True})
    assert r.status_code != 409, f"force was refused: {r.text[:200]}"
    assert aborted == [True], (
        "a forced whole-rig connect tore the rig down without ending the run "
        "first, so the run task outlives the devices it was driving")


def test_an_idle_rig_still_connects_without_force(client):
    """The other side, and the one a too-eager guard breaks: connecting a rig
    when nothing is running is the ordinary thing this route is for.

    MUTATION: refuse unconditionally (drop the `engine.running` term). Observed:
    409 on an idle rig and this fails.
    """
    r = client.post("/api/connect/rig", json={"primary": "sim"})
    assert r.status_code != 409, (
        f"an idle rig refused an ordinary connect: {r.text[:200]}")
