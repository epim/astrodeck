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
                     [{"role": "focuser", "name": "EAF", "verified": True},
                      {"role": "rotator", "name": "CAA", "verified": True}]))
    d = store.add_driver("fake-usb", transport="local")
    row = _probe(d)
    assert row["status"]["reachable"] is True
    assert {o["role"] for o in row["offers"]["devices"]} == {"rotator", "focuser"}


# ------------------------------------- #97: offer what is THERE, named for itself
# The probe used to build its offers from the backend CLASS's ``roles`` tuple and
# name every one of them with the backend's generic label, using ``discover()``
# only as a boolean "anything attached?". Both halves of that lied.

def test_local_offers_only_the_roles_actually_discovered(store, register):
    """One EAF attached, no CAA: a rotator must NOT be offered.

    zwo-usb declares roles=("rotator","focuser") because it CAN serve both, but
    the two roles are backed by two separate physical devices. Offering the
    rotator anyway sends the user to a dropdown entry whose connect dies with
    "no CAA attached (SDK enumerated 0 units)" — in the dark, mid-session."""
    register(_FakeHw("only-eaf", "local", ("rotator", "focuser"),
                     [{"role": "focuser", "name": "ZWO EAF (USB)", "verified": True}]))
    d = store.add_driver("only-eaf", transport="local")
    row = _probe(d)
    assert row["status"]["reachable"] is True
    assert [o["role"] for o in row["offers"]["devices"]] == ["focuser"]


def test_verified_devices_are_named_for_themselves_not_the_backend(store, register):
    """A rotator dropdown must read "ZWO CAA (USB)", not "ZWO USB accessories".

    discover() returns the per-unit name the SDK confirmed; the probe used to
    throw it away, so both roles of a two-device backend rendered under one
    indistinguishable label."""
    register(_FakeHw("named-usb", "local", ("rotator", "focuser"),
                     [{"role": "focuser", "name": "ZWO EAF (USB)", "verified": True},
                      {"role": "rotator", "name": "ZWO CAA (USB)", "verified": True}]))
    d = store.add_driver("named-usb", transport="local")
    row = _probe(d)
    by_role = {o["role"]: o["name"] for o in row["offers"]["devices"]}
    assert by_role == {"focuser": "ZWO EAF (USB)", "rotator": "ZWO CAA (USB)"}


def test_unverified_devices_fall_back_to_the_backend_label(store, register):
    """A guess must not be dressed as an identity.

    The Wanderer sits behind a generic CH340 (VID 1A86:7523 — the same chip in
    every Wanderer product), so its discover() name is the hedge "CH340 serial
    (Wanderer?)" and it sets verified=False. Putting that string in a filter-wheel
    dropdown reads as a device name; the backend's own label is the honest one."""
    register(_FakeHw("unverified-serial", "serial", ("filterwheel",),
                     [{"role": "filterwheel", "name": "CH340 serial (Wanderer?)",
                       "port_path": "COM8", "verified": False}]))
    d = store.add_driver("unverified-serial", transport="serial", port_path="COM8")
    row = _probe(d)
    assert [o["name"] for o in row["offers"]["devices"]] == [
        "UNVERIFIED-SERIAL device"]          # _FakeHw.label


def test_serial_offers_come_from_the_matching_port_only(store, register):
    """Two CH340s on one bus: a driver bound to COM8 must not offer COM9's device."""
    register(_FakeHw("two-serial", "serial", ("filterwheel", "rotator"),
                     [{"role": "filterwheel", "name": "Wheel", "port_path": "COM8",
                       "verified": True},
                      {"role": "rotator", "name": "Rotator", "port_path": "COM9",
                       "verified": True}]))
    d = store.add_driver("two-serial", transport="serial", port_path="COM8")
    row = _probe(d)
    assert [(o["role"], o["name"]) for o in row["offers"]["devices"]] == [
        ("filterwheel", "Wheel")]


def test_indexed_unit_offers_only_its_own_unit(store, register):
    """Two identical USB cameras: the driver pinned to index 1 must offer unit 1.

    Each camera gets its own driver row carrying extra.index; without filtering,
    both rows offered an identical generic entry and neither said which camera."""
    register(_FakeHw("two-cams", "local", ("camera", "guide_camera"),
                     [{"role": "camera", "name": "Cam A", "index": 0, "verified": True},
                      {"role": "guide_camera", "name": "Cam A", "index": 0,
                       "verified": True},
                      {"role": "camera", "name": "Cam B", "index": 1, "verified": True},
                      {"role": "guide_camera", "name": "Cam B", "index": 1,
                       "verified": True}]))
    d = store.add_driver("two-cams", transport="local", extra={"index": 1})
    row = _probe(d)
    assert {o["name"] for o in row["offers"]["devices"]} == {"Cam B"}
    assert {o["role"] for o in row["offers"]["devices"]} == {"camera", "guide_camera"}


def test_a_role_free_discovery_still_offers_the_backend_roles(store, register):
    """Back-compat: a backend whose discover() reports units without a ``role``
    keeps offering its declared roles rather than going silently empty."""
    register(_FakeHw("roleless", "local", ("switch",),
                     [{"name": "Some box", "verified": True}]))
    d = store.add_driver("roleless", transport="local")
    row = _probe(d)
    assert [o["role"] for o in row["offers"]["devices"]] == ["switch"]


def test_local_detail_names_every_unit_found(store, register):
    """The status line said "ZWO EAF (USB)" for a bus holding an EAF AND a CAA,
    because it read found[0] only."""
    register(_FakeHw("detail-usb", "local", ("rotator", "focuser"),
                     [{"role": "focuser", "name": "ZWO EAF (USB)", "verified": True},
                      {"role": "rotator", "name": "ZWO CAA (USB)", "verified": True}]))
    d = store.add_driver("detail-usb", transport="local")
    row = _probe(d)
    assert row["status"]["detail"] == "ZWO EAF (USB), ZWO CAA (USB)"


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


# ---------------------------------- B follow-up B: transport/port_path/index

def test_probe_row_echoes_transport_and_port_path(store, register):
    """The /api/drivers row (built in _probe_configured) carries the
    configured driver's transport + port_path so the client can dedupe native
    drivers by port/index and show the COM port."""
    register(_FakeHw("fake-serial3", "serial", ("telescope",),
                     [{"role": "telescope", "name": "Fake AM", "port_path": "COM9"}]))
    d = store.add_driver("fake-serial3", transport="serial", port_path="COM9")
    row = _probe(d)
    assert row["transport"] == "serial"
    assert row["port_path"] == "COM9"


def test_probe_row_echoes_index_when_set(store, register):
    """A per-unit index (e.g. a picked USB camera, B follow-up A) rides the
    driver's ``extra`` and is echoed on the row for the client to dedupe on."""
    register(_FakeHw("fake-usb3", "local", ("camera",),
                     [{"role": "camera", "name": "Cam 1", "index": 1}]))
    d = store.add_driver("fake-usb3", transport="local", extra={"index": 1})
    row = _probe(d)
    assert row["index"] == 1


def test_probe_row_index_is_none_when_unset(store, register):
    register(_FakeHw("fake-usb4", "local", ("camera",), [{"role": "camera", "name": "Cam"}]))
    d = store.add_driver("fake-usb4", transport="local")
    row = _probe(d)
    assert row["index"] is None


def test_patched_port_path_reprobes_against_new_port(store, register):
    """B follow-up C: after patching a serial driver's port_path, the (cache-
    invalidated) probe reflects reachability against the NEW port, not the
    one it was created with."""
    register(_FakeHw("fake-serial4", "serial", ("telescope",),
                     [{"role": "telescope", "name": "Fake AM", "port_path": "COM7"}]))
    d = store.add_driver("fake-serial4", transport="serial", port_path="COM3")
    row = _probe(d)
    assert row["status"]["reachable"] is False   # COM3 configured, device on COM7

    updated = store.update_driver(d.id, {"port_path": "COM7"})
    drv.invalidate(d.id)
    row = _probe(updated)
    assert row["status"]["reachable"] is True
    assert row["port_path"] == "COM7"
