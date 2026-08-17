"""Stage A (#239): the standards block is reachable, and gated.

A setting nobody can change is not a setting. This covers the route half of
"move rig policy into Settings" - and the RBAC half, because the config patch
body is `extra="forbid"` with an explicit capability map, so a new block that
nobody added a capability for fails closed with a 403 rather than merging.
"""
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import config_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.hub_module, "CAPTURE_DIR", tmp_path)
    with TestClient(app_module.create_app()) as c:
        yield c


def test_the_standards_block_round_trips(client):
    body = {"standards": {
        "apply_filter_offsets": True,
        "refocus_on_temp_delta_c": 2.0,
        "min_stars": 40,
        "max_guide_rms": 1.5,
        "max_eccentricity": 0.6,
        "max_consecutive_rejects": 8,
        "max_consecutive_rejects_night": 15,
    }}
    r = client.post("/api/config", json=body)
    assert r.status_code == 200, r.text
    cfg = config_store.cfg()
    assert cfg.standards.min_stars == 40
    assert cfg.standards.max_guide_rms == 1.5
    assert cfg.standards.refocus_on_temp_delta_c == 2.0


def test_it_does_not_disturb_its_neighbours(client):
    """Partial merge: sending standards must not reset safety or cooling."""
    client.post("/api/config", json={"cooling": {"warm_rate_c_per_min": 3.0}})
    client.post("/api/config", json={"standards": {"min_stars": 12}})
    cfg = config_store.cfg()
    assert cfg.standards.min_stars == 12
    assert cfg.cooling.warm_rate_c_per_min == 3.0


def test_an_out_of_range_threshold_is_refused(client):
    """max_eccentricity is 0..1 - a 5 would silently disable the gate by being
    unreachable, so it must 422 rather than persist."""
    r = client.post("/api/config", json={"standards": {"max_eccentricity": 5}})
    assert r.status_code == 422
