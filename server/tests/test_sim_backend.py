"""Simulator backend adapter (Stage A).

Pins the WRAPPING contract for ``SimBackend`` / ``SimSession``: it registers
itself, opens a session, and that session hands out the same sim devices the hub
uses today (camera/telescope/focuser, plus the dedicated guide_camera), exposes
the ``SimGuider`` as the native guider, and has no native solver / health.
Behavior-preserving: it does not change what the sim devices do.
"""
import pytest

from astrodeck.devices.backend import (
    BACKENDS,
    ROLES,
    Backend,
    BackendSession,
    ConnSpec,
    get_backend,
)

# Importing the package self-registers every concrete backend.
from astrodeck.devices.backends import sim_backend  # noqa: E402
from astrodeck.devices.backends.sim_backend import SimBackend, SimSession  # noqa: E402


def test_sim_backend_self_registered():
    """Importing the backends package registers 'sim' under the contract name."""
    assert "sim" in BACKENDS
    b = get_backend("sim")
    assert isinstance(b, SimBackend)
    assert b.name == "sim"
    assert b.label == "Simulator"
    assert b.roles == ROLES
    assert b.discoverable is False


def test_sim_backend_satisfies_protocol():
    assert isinstance(SimBackend(), Backend)


@pytest.mark.asyncio
async def test_open_returns_session_satisfying_protocol():
    session = await SimBackend().open(ConnSpec(backend="sim"))
    assert isinstance(session, SimSession)
    assert isinstance(session, BackendSession)
    assert session.name == "sim"


@pytest.mark.asyncio
async def test_get_device_returns_role_devices():
    """open -> get_device returns camera/telescope/focuser device objects."""
    session = await SimBackend().open(ConnSpec(backend="sim"))
    for role in ("camera", "telescope", "focuser"):
        dev = await session.get_device(role, ConnSpec(backend="sim", role=role))
        assert dev is not None
        # each is a real sim device with a name (duck-typed devices.base.Device)
        assert isinstance(dev.name, str) and dev.name


@pytest.mark.asyncio
async def test_get_device_is_stable_across_calls():
    """The rig is built once: the same role yields the same instance, and the
    devices share state (sim devices are wired to one SimRig)."""
    session = await SimBackend().open(ConnSpec(backend="sim"))
    cam1 = await session.get_device("camera", ConnSpec(backend="sim"))
    cam2 = await session.get_device("camera", ConnSpec(backend="sim"))
    assert cam1 is cam2


@pytest.mark.asyncio
async def test_guide_camera_exposed_as_extra():
    """The sim's dedicated guide_camera (not a ROLE) is reachable too."""
    session = await SimBackend().open(ConnSpec(backend="sim"))
    gc = await session.get_device("guide_camera", ConnSpec(backend="sim"))
    assert gc is not None
    assert isinstance(gc.name, str) and gc.name


@pytest.mark.asyncio
async def test_unknown_role_raises_keyerror():
    session = await SimBackend().open(ConnSpec(backend="sim"))
    with pytest.raises(KeyError):
        await session.get_device("nope", ConnSpec(backend="sim"))


@pytest.mark.asyncio
async def test_native_guider_not_none_and_sync():
    """native_guider() is SYNC and returns a SimGuider; cached across calls."""
    session = await SimBackend().open(ConnSpec(backend="sim"))
    g = session.native_guider()                     # NOT awaited -- sync by contract
    assert g is not None
    from astrodeck.guide import SimGuider
    assert isinstance(g, SimGuider)
    assert session.native_guider() is g             # cached / same instance


@pytest.mark.asyncio
async def test_native_solver_none_and_sync():
    session = await SimBackend().open(ConnSpec(backend="sim"))
    assert session.native_solver() is None          # NOT awaited -- sync by contract


@pytest.mark.asyncio
async def test_health_none_and_close_noop():
    session = await SimBackend().open(ConnSpec(backend="sim"))
    assert await session.health() is None
    assert await session.close() is None


@pytest.mark.asyncio
async def test_discover_empty():
    assert await SimBackend().discover() == []
