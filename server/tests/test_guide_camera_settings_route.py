"""PUT /api/guide/camera-settings actually accepts a JSON body.

Measured on the rig 2026-08-08, mid guide-calibration: reaching for a longer
guide exposure — the exact situation this endpoint was built for — answered

    422 {"detail":[{"type":"missing","loc":["query","body"],
                    "msg":"Field required"}]}

``GuideCameraSettingsBody`` was the one body model in ``app.py`` declared INSIDE
``create_app()``. The module runs under ``from __future__ import annotations``,
so FastAPI resolves every route signature from a STRING against the MODULE
globals; a class local to ``create_app`` is not there, the annotation did not
resolve to a BaseModel, and FastAPI demoted ``body`` to a query parameter. So
the endpoint could never be called with a body at all.

Nothing caught it: the guide dials had a UI test asserting the client PUTs to
this path (``opStripsDom.test.tsx``) and the server had no test for the route.
Both halves were tested and the seam between them never was — the project's
dominant defect class.

The class-scope test is the general detector; the HTTP tests are the specific
one. Keep both: the detector alone would not prove the route works, and the
route tests alone would not stop the next model being declared in the wrong
scope.
"""
from __future__ import annotations

import inspect
import re

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import ConfigStore
    store = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(app_module, "config_store", store, raising=False)
    app = app_module.create_app()
    with TestClient(app) as c:
        c.post("/api/connect/sim")
        yield c


# ------------------------------------------------------------ the specific bug

def test_put_accepts_a_json_body(client):
    r = client.put("/api/guide/camera-settings", json={"exposure_s": 3.5})
    assert r.status_code != 422, (
        "the body was rejected as a missing QUERY parameter — the annotation "
        f"did not resolve to a BaseModel: {r.text[:300]}")
    assert r.status_code == 200, r.text
    assert float(r.json()["exposure_s"]) == pytest.approx(3.5)


def test_put_applies_gain_and_binning_too(client):
    r = client.put("/api/guide/camera-settings",
                   json={"gain": 250, "binning": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert int(body["gain"]) == 250
    assert int(body["binning"]) == 2


def test_the_get_reads_back_what_the_put_wrote(client):
    assert client.put("/api/guide/camera-settings",
                      json={"exposure_s": 4.0, "gain": 300}).status_code == 200
    got = client.get("/api/guide/camera-settings")
    assert got.status_code == 200, got.text
    assert float(got.json()["exposure_s"]) == pytest.approx(4.0)
    assert int(got.json()["gain"]) == 300


def test_validation_still_bites_on_the_body_not_the_query(client):
    # An out-of-range exposure must fail as a BODY field. Before the fix every
    # request failed on ["query", "body"], so a passing "422 for bad input"
    # assertion would have proved nothing at all.
    r = client.put("/api/guide/camera-settings", json={"exposure_s": 999})
    assert r.status_code == 422
    locs = [tuple(d.get("loc", ())) for d in r.json()["detail"]]
    assert any(loc[:1] == ("body",) for loc in locs), (
        f"the failure must be about the body, not a query param: {locs}")


def test_an_empty_patch_is_refused(client):
    assert client.put("/api/guide/camera-settings", json={}).status_code == 422


# ------------------------------------------------- the detector for the class

def test_no_request_body_model_is_declared_inside_create_app():
    """Every ``BaseModel`` used as a route body lives at MODULE scope.

    Under postponed annotations a model declared inside ``create_app`` cannot be
    resolved by FastAPI, and the failure is silent at import time: the app
    starts, the route exists, the OpenAPI schema lists it, and only a real
    request finds out. So the scope is the thing to assert.
    """
    src = inspect.getsource(app_module.create_app)
    offenders = re.findall(r"^\s+class\s+(\w+)\s*\(\s*BaseModel\s*\)", src,
                           flags=re.MULTILINE)
    assert offenders == [], (
        "these body models are declared inside create_app(); under "
        "`from __future__ import annotations` FastAPI cannot resolve them and "
        f"demotes the parameter to a query field: {offenders}")


def test_the_body_model_resolves_to_a_pydantic_model_at_module_scope():
    from pydantic import BaseModel
    model = getattr(app_module, "GuideCameraSettingsBody", None)
    assert model is not None, (
        "GuideCameraSettingsBody must be importable from the module — that is "
        "exactly what FastAPI's annotation resolution needs")
    assert issubclass(model, BaseModel)
