"""RotatorConfig validation + the /api/config/rotator route (422 contract)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)   # force fresh load from tmp
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_defaults():
    from astrodeck.config import RotatorConfig
    rc = RotatorConfig()
    assert (rc.range_type, rc.range_start_deg, rc.tolerance_deg) == ("full", 0.0, 1.0)


def test_set_rotator_validates(tmp_path, monkeypatch):
    from astrodeck.config import RotatorConfig, config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(range_type="diagonal"))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(range_start_deg=360.0))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(range_start_deg=-1.0))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(tolerance_deg=0.0))
    with pytest.raises(ValueError):
        config_store.set_rotator(RotatorConfig(tolerance_deg=45.1))
    cfg = config_store.set_rotator(
        RotatorConfig(range_type="half", range_start_deg=245.0, tolerance_deg=2.0))
    assert cfg.rotator.range_type == "half"


def test_route_422_on_junk(client):
    r = client.post("/api/config/rotator",
                    json={"range_type": "diagonal", "range_start_deg": 0.0,
                          "tolerance_deg": 1.0})
    assert r.status_code == 422


def test_route_persists_and_returns_config(client):
    r = client.post("/api/config/rotator",
                    json={"range_type": "quarter", "range_start_deg": 245.0,
                          "tolerance_deg": 1.5})
    assert r.status_code == 200
    assert r.json()["rotator"] == {"range_type": "quarter",
                                   "range_start_deg": 245.0,
                                   "tolerance_deg": 1.5}
    r2 = client.get("/api/config")
    assert r2.json()["rotator"]["range_type"] == "quarter"
