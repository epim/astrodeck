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
    m = drivers.driver_type_to_backend()
    # The three historical built-ins map byte-identically…
    assert {k: m[k] for k in ("nina", "alpaca", "phd2")} == {
        "nina": "nina", "alpaca": "native", "phd2": "phd2"}
    # …and the registry-derived vocabulary now ALSO carries the first
    # entry-point citizen (sub-project B) when its dist metadata is installed.
    assert {"nina", "alpaca", "phd2"} <= drivers.configurable_driver_types()
    if "zwo-am5" in m:                      # editable install with entry point
        assert m["zwo-am5"] == "zwo-am5"


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


# --- Task 5: DriverEntry + ProfileDevice serial addressing ---
def test_driverentry_serial_and_network_validation():
    import pytest
    from astrodeck.config import DriverEntry
    ser = DriverEntry(id="zwo-am5-ab12", type="zwo-am5", transport="serial",
                      port_path="COM3")
    assert ser.transport == "serial" and ser.port_path == "COM3"
    net = DriverEntry(id="nina-ab12", type="nina", host="localhost", port=1888)
    assert net.transport == "network"
    with pytest.raises(ValueError):
        DriverEntry(id="x", type="zwo-am5", transport="serial")         # no port_path
    with pytest.raises(ValueError):
        DriverEntry(id="y", type="nina", transport="network", host="")  # no host


def test_driverentry_legacy_loads_as_network():
    from astrodeck.config import DriverEntry
    e = DriverEntry(id="nina-cd34", type="nina", host="h", port=1888)
    assert e.transport == "network" and e.port_path == ""


def test_profiledevice_serial_carries_into_rigspec():
    from astrodeck.profiles import Profile, ProfileDevice
    p = Profile(name="serial rig", primary_backend="zwo-am5", devices=[
        ProfileDevice(role="telescope", backend="zwo-am5",
                      transport="serial", port_path="COM3")])
    rig = p.to_rigspec()
    cs = rig.resolve("telescope")
    assert cs.transport == "serial" and cs.port_path == "COM3"


# --- Task 6: entry-point plugin discovery ---
import astrodeck.devices.backends._discovery as disc  # noqa: E402


class _FakeEP:
    def __init__(self, name, fn, dist_name="demo-dist"):
        import types
        self.name = name
        self._fn = fn
        self.dist = types.SimpleNamespace(name=dist_name)

    def load(self):
        return self._fn


def _make_plugin_backend(name, *, min_app="0", version="1.0", hardware=True):
    b = type("_PB", (), {})()
    b.name = name; b.label = name; b.roles = ("telescope",)
    b.discoverable = False; b.hostless = True
    b.min_app_version = min_app; b.version = version
    b.hardware = hardware; b.driver_type = ""; b.transport = "network"
    return b


def test_discovery_loads_plugin(monkeypatch):
    def reg(): register(_make_plugin_backend("demo-plugin"))
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("demo-plugin", reg)])
    try:
        disc.discover_plugin_backends(app_version="0.2.5")
        assert "demo-plugin" in BACKENDS
        assert any(r["name"] == "demo-plugin" and r["status"] == "loaded"
                   for r in disc.plugin_load_report())
    finally:
        BACKENDS.pop("demo-plugin", None)


def test_discovery_guarded_on_raise(monkeypatch):
    def boom(): raise RuntimeError("bad plugin")
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("boom", boom)])
    disc.discover_plugin_backends(app_version="0.2.5")   # must not raise
    assert "sim" in BACKENDS                              # built-ins intact
    assert any(r["status"] == "failed" for r in disc.plugin_load_report())


def test_discovery_version_gate(monkeypatch):
    def reg(): register(_make_plugin_backend("future-plugin", min_app="99.0"))
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("future-plugin", reg)])
    disc.discover_plugin_backends(app_version="0.2.5")
    assert "future-plugin" not in BACKENDS
    assert any(r["name"] == "future-plugin" and r["status"] == "incompatible"
               for r in disc.plugin_load_report())


def test_discovery_refuses_driver_type_shadowing(monkeypatch):
    """A plugin may register a NEW backend name whose ``driver_type`` collides
    with a built-in's. That is an ``added`` (not overwritten) backend, so the
    name guard lets it through — and ``driver_type_to_backend()`` would then
    point every config row of type "nina" at the plugin. Discovery must refuse
    it, and the resolver must be first-claimer-wins regardless."""
    from astrodeck import drivers

    def reg():
        b = _make_plugin_backend("evil-mount")
        b.driver_type = "nina"                 # shadows the built-in NINA route
        register(b)

    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("evil", reg)])
    try:
        disc.discover_plugin_backends(app_version="0.2.5")
        assert "evil-mount" not in BACKENDS                 # dropped
        assert any(r["status"] == "failed" and "driver_type" in (r["detail"] or "")
                   for r in disc.plugin_load_report())
        assert drivers.driver_type_to_backend()["nina"] == "nina"

        # Layer 2: even if a collision reaches the registry by another path, the
        # first claimer (a built-in — they register first) keeps the type.
        register(_registered := _make_plugin_backend("sneaky"))
        _registered.driver_type = "nina"
        assert drivers.driver_type_to_backend()["nina"] == "nina"
    finally:
        BACKENDS.pop("evil-mount", None)
        BACKENDS.pop("sneaky", None)


def test_discovery_collision_guard(monkeypatch):
    original_sim = BACKENDS["sim"]
    def overwrite(): register(_make_plugin_backend("sim"))
    monkeypatch.setattr(disc.md, "entry_points",
                        lambda group=None: [_FakeEP("evil", overwrite)])
    disc.discover_plugin_backends(app_version="0.2.5")
    assert BACKENDS["sim"] is original_sim                # built-in preserved
    assert any(r["status"] == "failed" and "built-in" in (r["detail"] or "")
               for r in disc.plugin_load_report())


# --- Task 7: plugin-report API ---
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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


def test_backends_plugins_endpoint(client):
    r = client.get("/api/backends/plugins")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    for row in body:
        assert {"name", "dist", "version", "status", "detail"} <= set(row)
