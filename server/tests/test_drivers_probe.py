"""Driver probe engine (equipment-drivers spec §3.2): offers mapping, the 15s
TTL cache + invalidate(), disabled short-circuit, and the never-raise contract
(a probe bug becomes a status.error row, not a 500)."""
import asyncio

import pytest

from astrodeck import drivers as drv
from astrodeck.config import ConfigStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(drv, "config_store", s)
    drv.invalidate()
    return s


def _run(coro):
    return asyncio.run(coro)


def test_describe_all_merges_configured_and_implicit(store, monkeypatch):
    store.add_driver("nina", "h1")

    async def fake_nina(host, port):
        return drv._ok([{"role": "camera", "name": "cam"}],
                       ["autofocus", "polar_align"], detail="API 2.2.2.0")

    monkeypatch.setitem(drv._PROBES, "nina", fake_nina)
    out = _run(drv.describe_all())
    assert out["roles"] == list(drv.ROLES)
    ids = [d["id"] for d in out["drivers"]]
    assert ids[-3:] == ["sim", "astrodeck", "astap"]        # implicit rows appended
    nina = out["drivers"][0]
    assert nina["implicit"] is False and nina["host"] == "h1"
    assert nina["status"]["reachable"] is True
    assert nina["status"]["detail"] == "API 2.2.2.0"
    assert nina["offers"]["devices"] == [{"role": "camera", "name": "cam"}]
    sim = out["drivers"][-3]
    assert sim["status"]["reachable"] is True               # sim is always on
    assert {"role": "camera", "name": "Simulated camera"} in sim["offers"]["devices"]


def test_probe_cached_within_ttl_forced_and_invalidated(store, monkeypatch):
    d = store.add_driver("phd2", "127.0.0.1")
    calls = {"n": 0}

    async def fake(host, port):
        calls["n"] += 1
        return drv._ok([{"role": "guider", "name": "PHD2"}], [])

    monkeypatch.setitem(drv._PROBES, "phd2", fake)
    _run(drv.describe_all())
    _run(drv.describe_all())
    assert calls["n"] == 1                  # second read served from cache
    _run(drv.describe_all(force=True))
    assert calls["n"] == 2                  # force bypasses the TTL
    drv.invalidate(d.id)
    _run(drv.describe_all())
    assert calls["n"] == 3                  # invalidate drops the entry


def test_disabled_driver_reports_disabled_without_probing(store, monkeypatch):
    d = store.add_driver("nina", "h")
    store.update_driver(d.id, {"enabled": False})

    async def boom(host, port):
        raise AssertionError("must not probe a disabled driver")

    monkeypatch.setitem(drv._PROBES, "nina", boom)
    out = _run(drv.describe_all())
    row = out["drivers"][0]
    assert row["status"]["reachable"] is False
    assert row["status"]["error"] == "disabled"
    assert row["offers"] == {"devices": [], "tasks": []}


def test_probe_exception_becomes_error_row(store, monkeypatch):
    store.add_driver("alpaca", "h")

    async def boom(host, port):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(drv._PROBES, "alpaca", boom)
    out = _run(drv.describe_all())
    row = out["drivers"][0]
    assert row["status"]["reachable"] is False
    assert "kaboom" in row["status"]["error"]


def test_alpaca_offer_carries_dev_type_and_dev_num(store, monkeypatch):
    """Review finding 1: Alpaca offers MUST carry the ConnSpec addressing
    (dev_type + dev_num); unmapped DeviceTypes (rotator, until the role exists)
    are skipped, not errors."""
    store.add_driver("alpaca", "h")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"Value": [
                {"DeviceType": "Camera", "DeviceNumber": 0, "DeviceName": "ASI2600MM"},
                {"DeviceType": "Rotator", "DeviceNumber": 0, "DeviceName": "ZWO CAA"},
            ]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    out = _run(drv.describe_all())
    devs = out["drivers"][0]["offers"]["devices"]
    assert devs == [{"role": "camera", "name": "ASI2600MM",
                     "dev_type": "camera", "dev_num": 0}]
