"""Native serial/local hardware driver probe (native-hardware on-ramp, 2026-07-21).

A configured driver whose type is a registered ``hardware=True`` backend is probed
by running the backend's OWN non-invasive ``discover()`` — so a present device
reads reachable + offers its roles (and thus becomes eligible in the Equipment
dropdowns), instead of sitting permanently unreachable as an un-probed serial type.
"""
import asyncio

import pytest

from astrodeck import drivers as drv
from astrodeck.config import ConfigStore
from astrodeck.devices import backend as backend_mod


class _FakeHw:
    """A duck-typed hardware backend whose discover() is scripted per-test."""
    def __init__(self, name, transport, roles, found, raises=False):
        self.name = name
        self.label = f"{name.upper()} device"
        self.roles = roles
        self.discoverable = True
        self.hostless = transport == "local"
        self.hardware = True
        self.transport = transport
        self.driver_type = name
        self._found = found
        self._raises = raises

    async def discover(self):
        if self._raises:
            raise RuntimeError("boom")
        return list(self._found)

    async def open(self, conn):  # not exercised by the probe
        raise NotImplementedError


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(drv, "config_store", s)
    drv.invalidate()
    return s


@pytest.fixture()
def register():
    added = []

    def _add(b):
        backend_mod.register(b)
        added.append(b.name)
        return b
    yield _add
    for n in added:
        backend_mod.BACKENDS.pop(n, None)


def _probe(entry):
    return asyncio.run(drv._probe_configured(entry, force=True))


def test_serial_present_is_reachable_and_offers_roles(store, register):
    register(_FakeHw("fake-serial", "serial", ("telescope",),
                     [{"role": "telescope", "name": "Fake AM", "port_path": "COM9"}]))
    d = store.add_driver("fake-serial", transport="serial", port_path="COM9")
    row = _probe(d)
    assert row["status"]["reachable"] is True
    assert row["status"]["error"] is None
    assert [o["role"] for o in row["offers"]["devices"]] == ["telescope"]


def test_serial_wrong_port_is_unreachable(store, register):
    register(_FakeHw("fake-serial2", "serial", ("telescope",),
                     [{"role": "telescope", "name": "Fake AM", "port_path": "COM9"}]))
    d = store.add_driver("fake-serial2", transport="serial", port_path="COM7")
    row = _probe(d)
    assert row["status"]["reachable"] is False
    assert "COM7" in (row["status"]["error"] or "")
    assert row["offers"]["devices"] == []


def test_local_present_is_reachable(store, register):
    register(_FakeHw("fake-usb", "local", ("rotator", "focuser"),
                     [{"role": "focuser", "name": "EAF"}, {"role": "rotator", "name": "CAA"}]))
    d = store.add_driver("fake-usb", transport="local")
    row = _probe(d)
    assert row["status"]["reachable"] is True
    assert {o["role"] for o in row["offers"]["devices"]} == {"rotator", "focuser"}


def test_local_absent_is_unreachable(store, register):
    register(_FakeHw("fake-usb2", "local", ("camera",), []))
    d = store.add_driver("fake-usb2", transport="local")
    row = _probe(d)
    assert row["status"]["reachable"] is False
    assert row["offers"]["devices"] == []


def test_discover_raising_never_escapes(store, register):
    register(_FakeHw("fake-boom", "serial", ("telescope",), [], raises=True))
    d = store.add_driver("fake-boom", transport="serial", port_path="COM3")
    row = _probe(d)  # must not raise
    assert row["status"]["reachable"] is False
