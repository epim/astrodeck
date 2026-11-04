# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""NEW FLOW asks how the night ends and whether to auto-resume (#718, WP 2.10,
owner ruling D-G).

Both UIs' NEW FLOW sheets post the three original answers (``kind``,
``options``, ``target``) to ``POST /api/flows/wizard`` and now two more: the
stop condition (``stop`` and, for "Clock time", ``stop_clock``) and
``auto_resume``. The route took all of them since WP-100 (#196); this file
holds the NEW FLOW shape of that post to its three promises:

  * a post carrying an explicit stop and auto_resume creates a flow whose one
    DUSK WINDOW carries both;
  * a post without them creates exactly today's defaults, which is what an
    unanswered NEW FLOW sheet posts;
  * an invalid stop or auto_resume is refused by the wizard's own checks
    (``checked_stop``, ``checked_auto_resume``) in their own words.

The UI half is ``ui/src/next/hubs/session/flows/create/__tests__/
w2_10NewFlowNight.test.tsx`` and ``ui/src/components/flows/__tests__/
w2_10NewFlowNight.test.tsx``, which read the request body each sheet sends.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.flows import wizard
from astrodeck.flows.nodes import create_params
from astrodeck.flows.store import flow_store

#: The three answers NEW FLOW has always posted.
BASE = dict(kind=wizard.KIND_DEEP_SKY, options=["Guiding", "HFR watchdog"],
            target="M16")


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(flow_store, "_dir", tmp_path / "flows", raising=False)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        c.store = isolated_config.store
        yield c


def _post(c, **body):
    return c.post("/api/flows/wizard", json=body)


def _dusk_params(rec: dict) -> dict:
    found = [n for n in rec["graph"]["nodes"] if n["type"] == "dusk"]
    assert len(found) == 1, f"expected one DUSK WINDOW, got {len(found)}"
    return found[0]["params"]


def _flat(r) -> str:
    return json.dumps(r.json(), ensure_ascii=False)


def test_an_explicit_clock_stop_and_auto_resume_off_reach_the_flow(client):
    r = _post(client, **BASE, stop="Clock time", stop_clock="03:30",
              auto_resume="Off")
    assert r.status_code == 200, r.text[:300]
    params = _dusk_params(r.json())
    assert params["stop"] == "Clock time"
    assert params["stopClock"] == "03:30"
    assert params["autoResume"] == "Off"
    saved = client.get(f"/api/flows/{r.json()['id']}").json()
    assert _dusk_params(saved) == params, "the saved flow is not the one made"


def test_an_explicit_dawn_stop_and_auto_resume_on_are_carried_too(client):
    r = _post(client, **BASE, stop="Dawn", auto_resume="On")
    assert r.status_code == 200, r.text[:300]
    params = _dusk_params(r.json())
    assert params["stop"] == "Dawn"
    assert params["autoResume"] == "On"


def test_a_post_without_them_creates_exactly_todays_defaults(client):
    r = _post(client, **BASE)
    assert r.status_code == 200, r.text[:300]
    assert _dusk_params(r.json()) == create_params("dusk"), (
        "the three-answer post no longer leaves DUSK WINDOW as it was created")


@pytest.mark.parametrize("answers, check", [
    ({"stop": "None"}, wizard.checked_stop),
    ({"stop": "Midnight"}, wizard.checked_stop),
    ({"stop": True}, wizard.checked_stop),
    ({"auto_resume": "Maybe"}, wizard.checked_auto_resume),
    ({"auto_resume": "on"}, wizard.checked_auto_resume),
    ({"auto_resume": True}, wizard.checked_auto_resume),
])
def test_an_invalid_answer_is_refused_in_the_existing_checks_words(
        client, answers, check):
    (value,) = answers.values()
    with pytest.raises(ValueError) as e:
        check(value)
    before = len(client.get("/api/flows").json())
    r = _post(client, **BASE, **answers)
    assert r.status_code == 422, (answers, r.status_code, r.text[:300])
    assert str(e.value) in _flat(r), (
        f"the 422 does not carry the check's own text {str(e.value)!r}: "
        f"{_flat(r)[:300]}")
    assert len(client.get("/api/flows").json()) == before, (
        "a refused answer saved a flow")


def test_a_clock_stop_with_no_time_is_refused(client):
    r = _post(client, **BASE, stop="Clock time")
    assert r.status_code == 422, r.text[:300]
    assert "stop_clock" in _flat(r)
