"""UX-25: the autofocus route moves the filter wheel to the requested slot and
threads binning into run_autofocus (per-filter / per-binning autofocus). Uses
the in-process app + sim rig, pumping the background _spawn task via status GETs
(the test_app_route_concurrency idiom)."""
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def _wait(predicate, c, tries=300) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        c.get("/api/status")     # pump the app loop so the bg _spawn task runs
        time.sleep(0.02)
    return predicate()


def test_autofocus_route_moves_filter_and_threads_binning(client, monkeypatch):
    assert client.post("/api/connect/sim").status_code == 200

    seen: dict = {}

    async def fake_run_autofocus(cam, foc, **kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(app_module, "run_autofocus", fake_run_autofocus)

    # sim wheel is L R G B Ha OIII SII → slot 4 = Ha.
    r = client.post("/api/focuser/autofocus", json={"binning": 3, "filter": 4})
    assert r.status_code == 200, r.text
    assert r.json().get("started") == "autofocus"

    def wheel_at_4() -> bool:
        fw = client.get("/api/status").json().get("filterwheel")
        return bool(fw and fw.get("position") == 4)

    # _af moves the wheel BEFORE calling run_autofocus, so once the wheel shows
    # slot 4 the binning must already have been threaded through.
    assert _wait(wheel_at_4, client), "autofocus did not move the wheel to the requested slot"
    assert seen.get("binning") == 3


def test_autofocus_route_without_filter_leaves_wheel(client, monkeypatch):
    assert client.post("/api/connect/sim").status_code == 200
    start = client.get("/api/status").json()["filterwheel"]["position"]

    seen: dict = {}

    async def fake_run_autofocus(cam, foc, **kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(app_module, "run_autofocus", fake_run_autofocus)

    r = client.post("/api/focuser/autofocus", json={"binning": 2})  # no filter
    assert r.status_code == 200, r.text
    assert _wait(lambda: seen.get("binning") == 2, client)
    # the wheel was never moved (no filter requested)
    assert client.get("/api/status").json()["filterwheel"]["position"] == start
