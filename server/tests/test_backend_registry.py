"""Pluggable-backend contract: registry + RigSpec resolution (Stage A).

These tests pin the CONTRACT only — they register a dummy backend (no real
device I/O) and assert the registry/RigSpec/ConnSpec shapes the other Stage-A
agents must match.
"""
import pytest

from astrodeck.devices.backend import (
    BACKENDS,
    ROLES,
    Backend,
    BackendSession,
    ConnSpec,
    RigSpec,
    get_backend,
    list_backends,
    register,
)


# --------------------------------------------------------------------- dummies

class DummySession:
    """A minimal BackendSession — enough to satisfy the runtime_checkable
    Protocol and to be handed back from DummyBackend.open()."""

    name = "dummy"

    async def get_device(self, role, conn):
        return ("device", role, conn)

    def native_guider(self):
        return None

    def native_solver(self):
        return None

    async def health(self):
        return {"ok": True}

    async def close(self):
        return None


class DummyBackend:
    name = "dummy"
    label = "Dummy Backend"
    roles = ("camera", "telescope")
    discoverable = False

    async def open(self, conn):
        return DummySession()

    async def discover(self):
        return []


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot/restore the global registry so each test starts clean and never
    leaks a dummy into the real process registry."""
    saved = dict(BACKENDS)
    BACKENDS.clear()
    try:
        yield
    finally:
        BACKENDS.clear()
        BACKENDS.update(saved)


# ----------------------------------------------------------------------- tests

def test_register_and_get_backend_returns_same_object():
    b = DummyBackend()
    ret = register(b)
    assert ret is b                      # register returns the backend (decorator-friendly)
    assert get_backend("dummy") is b
    assert BACKENDS["dummy"] is b


def test_get_backend_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        get_backend("nope")


def test_register_satisfies_protocols():
    b = DummyBackend()
    s = DummySession()
    assert isinstance(b, Backend)
    assert isinstance(s, BackendSession)


def test_list_backends_shape_and_ordering():
    register(DummyBackend())

    class Other(DummyBackend):
        name = "aaa"
        label = "A Backend"
        roles = ("focuser",)
        discoverable = True

    register(Other())
    listed = list_backends()
    assert [b["name"] for b in listed] == ["aaa", "dummy"]   # sorted by name
    entry = next(b for b in listed if b["name"] == "dummy")
    assert set(entry) == {"name", "label", "roles", "discoverable"}
    assert entry["label"] == "Dummy Backend"
    assert entry["roles"] == ("camera", "telescope")
    assert entry["discoverable"] is False


def test_roles_constant_matches_device_roles():
    assert ROLES == ("camera", "telescope", "focuser", "guider",
                     "filterwheel", "switch", "safety")


def test_rigspec_resolve_default_uses_primary():
    rs = RigSpec("sim")
    spec = rs.resolve("camera")
    assert spec.backend == "sim"
    assert spec.role == "camera"
    # default-resolved spec carries no address
    assert spec.host is None and spec.port is None


def test_rigspec_resolve_explicit_override_wins():
    override = ConnSpec(backend="native", host="10.0.0.5", port=11111,
                        dev_type="camera", dev_num=0, role="camera")
    rs = RigSpec("sim", roles={"camera": override})
    assert rs.resolve("camera") is override
    # a role without an override still falls back to the primary
    assert rs.resolve("telescope").backend == "sim"


def test_connspec_roundtrip():
    spec = ConnSpec(backend="native", host="h", port=1, dev_type="focuser",
                    dev_num=2, role="focuser", extra={"k": "v"})
    d = spec.to_dict()
    assert d["extra"] == {"k": "v"}
    back = ConnSpec.from_dict(d)
    assert back == spec
    # from_dict tolerates a minimal dict (only required key present)
    minimal = ConnSpec.from_dict({"backend": "sim"})
    assert minimal == ConnSpec(backend="sim")


def test_rigspec_roundtrip():
    rs = RigSpec("nina", roles={"camera": ConnSpec(backend="nina", host="h", port=1888)})
    back = RigSpec.from_dict(rs.to_dict())
    assert back == rs
    assert back.resolve("camera").host == "h"
    assert back.resolve("focuser").backend == "nina"
