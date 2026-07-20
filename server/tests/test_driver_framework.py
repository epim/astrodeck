"""Driver framework: manifest, safety flag, serial addressing, registry-derived
driver types, and entry-point discovery."""
from astrodeck.devices import backends as _backends  # noqa: F401  (registration)
from astrodeck.devices.backend import BACKENDS, list_backends


def _by_name():
    return {b["name"]: b for b in list_backends()}


def test_list_backends_emits_manifest_fields():
    rows = list_backends()
    assert rows, "built-in backends must be registered"
    keys = {"name", "label", "roles", "discoverable", "version", "author",
            "min_app_version", "transport", "hardware", "driver_type"}
    for row in rows:
        assert keys <= set(row), f"missing manifest keys in {row['name']}"


def test_builtin_hardware_flags():
    m = _by_name()
    assert m["native"]["hardware"] is True
    assert m["nina"]["hardware"] is True
    assert m["sim"]["hardware"] is False
    assert m["phd2"]["hardware"] is False


def test_builtin_driver_types_reproduce_todays_map():
    m = _by_name()
    assert m["nina"]["driver_type"] == "nina"
    assert m["native"]["driver_type"] == "alpaca"
    assert m["phd2"]["driver_type"] == "phd2"
    assert m["sim"]["driver_type"] == ""


# --- Task 2: device-borne hardware safety flag ---
import astrodeck.providers as providers
from astrodeck.devices.backend import ConnSpec, RigSpec, register  # noqa: E402
from astrodeck.devices.base import Telescope  # noqa: E402
from astrodeck.devices.orchestrator import connect_profile  # noqa: E402


class _FakeTelescope(Telescope):
    async def connect(self): ...
    async def disconnect(self): ...
    async def get_position(self): return (0.0, 0.0)
    async def slew(self, ra, dec): ...
    async def sync(self, ra, dec): ...
    async def set_tracking(self, on): ...
    async def get_tracking(self): return False
    async def park(self): ...
    async def unpark(self): ...
    async def is_parked(self): return True
    async def move_axis(self, axis, rate): ...
    async def is_slewing(self): return False


def test_device_hardware_defaults_false():
    assert _FakeTelescope("t").hardware is False


def test_providers_has_no_real_backends_tuple():
    assert not hasattr(providers, "_REAL_BACKENDS")


async def test_orchestrator_stamps_hardware_from_backend():
    # sim rig: camera device is stamped False
    res = await connect_profile(RigSpec("sim"))
    assert res.rig["camera"].hardware is False

    class _Session:
        name = "fakehw"
        async def get_device(self, role, conn): return _FakeTelescope("scope")
        def native_guider(self): return None
        def guide_camera(self): return None
        def native_solver(self): return None
        async def health(self): return None
        async def close(self): ...

    class _Backend:
        name = "fakehw"; label = "Fake HW"; roles = ("telescope",)
        discoverable = False; hostless = True; hardware = True
        async def open(self, conn): return _Session()
        async def discover(self): return []

    register(_Backend())
    try:
        res2 = await connect_profile(RigSpec("fakehw"))
        assert res2.rig["telescope"].hardware is True
    finally:
        BACKENDS.pop("fakehw", None)


# --- Task 3: ConnSpec serial addressing ---
def test_connspec_serial_roundtrip_and_legacy():
    s = ConnSpec(backend="zwo-am5", transport="serial", port_path="COM3",
                 role="telescope")
    d = s.to_dict()
    assert d["transport"] == "serial" and d["port_path"] == "COM3"
    assert ConnSpec.from_dict(d).port_path == "COM3"
    # legacy dict (no transport/port_path) -> network defaults
    legacy = ConnSpec.from_dict({"backend": "native", "host": "h", "port": 11111})
    assert legacy.transport == "network" and legacy.port_path is None


# --- Task 4: registry-derived driver types ---
def test_registry_derived_driver_type_map_matches_today():
    from astrodeck import drivers
    assert drivers.driver_type_to_backend() == {
        "nina": "nina", "alpaca": "native", "phd2": "phd2"}
    assert drivers.configurable_driver_types() == {"nina", "alpaca", "phd2"}


def test_driver_type_map_extends_with_plugin():
    from astrodeck import drivers

    class _B:
        name = "demo-be"; label = "Demo"; roles = ("telescope",)
        discoverable = False; hostless = True; driver_type = "demo"; hardware = True
        async def open(self, conn): ...
        async def discover(self): return []
    register(_B())
    try:
        assert drivers.driver_type_to_backend()["demo"] == "demo-be"
    finally:
        BACKENDS.pop("demo-be", None)


def test_config_driver_type_is_open_str():
    from astrodeck.config import DriverType
    assert DriverType is str
