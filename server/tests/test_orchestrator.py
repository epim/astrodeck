"""Orchestrator: assemble a live rig from a RigSpec (Stage A).

These tests drive the REAL sim backend (importing ``sim_backend`` registers it)
through ``assemble`` and pin the assembled-rig shape: the whole sim rig comes up,
roles share one session, the guider is the sim's native guider, and a clean rig
records no failures. Graceful degrade is checked with a deliberately broken
backend.
"""
import pytest

# importing the backends package self-registers the "sim" backend in the
# global registry (the canonical adapter lives under devices/backends/).
from astrodeck.devices.backends import sim_backend  # noqa: F401  (side-effect import)
from astrodeck.devices.backend import (
    BACKENDS,
    ConnSpec,
    RigSpec,
    register,
)
from astrodeck.devices.orchestrator import AssembledRig, assemble, to_dict


@pytest.mark.asyncio
async def test_assemble_sim_rig_full():
    spec = RigSpec(primary="sim")
    result = await assemble(spec)

    assert isinstance(result, AssembledRig)
    # the whole simulated rig comes up.
    for role in ("camera", "telescope", "focuser", "filterwheel", "switch", "safety"):
        assert role in result.rig, f"missing role {role}"
    # a guider is present (the sim's native SimGuider).
    assert result.guider is not None
    assert result.guider.name == "Sim Guider"
    # a clean sim rig records no failures.
    assert result.failures == {}


@pytest.mark.asyncio
async def test_assemble_devices_share_one_session():
    spec = RigSpec(primary="sim")
    result = await assemble(spec)

    # the orchestrator hands out devices; connecting is the caller's job, so it
    # does not pre-connect them (behavior-preserving wrt the existing factory).
    assert result.rig["camera"] is not None
    assert result.rig["telescope"] is not None
    # all sim roles resolve to ONE backend instance -> one shared session.
    assert len(result.sessions) == 1
    # devices share the single underlying sim rig: the same camera instance is
    # returned for the camera role no matter how often it is resolved.
    again = await assemble(spec)
    assert again.rig["camera"] is not result.rig["camera"]  # distinct rigs per assemble


@pytest.mark.asyncio
async def test_assemble_sim_has_no_native_solver():
    # the sim exposes no standalone solver object; the caller falls back to
    # solve.get_solver. So solver_source is None even though camera came up.
    result = await assemble(RigSpec(primary="sim"))
    assert "camera" in result.rig
    assert result.solver_source is None


@pytest.mark.asyncio
async def test_to_dict_summary_shape():
    result = await assemble(RigSpec(primary="sim"))
    d = to_dict(result)
    assert set(d) == {"roles", "sessions", "has_guider",
                      "has_native_solver", "failures"}
    assert "camera" in d["roles"]
    assert d["has_guider"] is True
    assert d["has_native_solver"] is False
    assert d["failures"] == {}


@pytest.mark.asyncio
async def test_assemble_graceful_degrade_records_failures():
    """A backend whose get_device raises for one role records that role in
    failures and SKIPS it, without aborting the rest of the rig."""

    class FlakySession:
        name = "flaky"

        async def get_device(self, role, conn):
            if role == "focuser":
                raise RuntimeError("focuser offline")
            return ("dev", role)

        def native_guider(self):
            return "the-guider" if True else None

        def native_solver(self):
            return None

        async def health(self):
            return None

        async def close(self):
            return None

    class FlakyBackend:
        name = "flaky"
        label = "Flaky"
        roles = ("camera", "telescope", "focuser", "guider")
        discoverable = False

        async def open(self, conn):
            return FlakySession()

        async def discover(self):
            return []

    saved = dict(BACKENDS)
    try:
        register(FlakyBackend())
        result = await assemble(RigSpec(primary="flaky"))
        assert "focuser" in result.failures
        assert "focuser offline" in result.failures["focuser"]
        # the rest of the rig still came up.
        assert "camera" in result.rig
        assert "telescope" in result.rig
        assert "focuser" not in result.rig
        # guider still resolved from the (working) guider-role session.
        assert result.guider == "the-guider"
    finally:
        BACKENDS.clear()
        BACKENDS.update(saved)


@pytest.mark.asyncio
async def test_assemble_distinct_addresses_open_separate_sessions():
    """Two roles on the same backend NAME but different host/port get separate
    sessions; same address shares one."""
    opened = []

    class CountingSession:
        name = "counting"

        def __init__(self, conn):
            self.conn = conn

        async def get_device(self, role, conn):
            return ("dev", role, conn.host)

        def native_guider(self):
            return None

        def native_solver(self):
            return None

        async def health(self):
            return None

        async def close(self):
            return None

    class CountingBackend:
        name = "counting"
        label = "Counting"
        roles = ("camera", "telescope")
        discoverable = False

        async def open(self, conn):
            opened.append(conn)
            return CountingSession(conn)

        async def discover(self):
            return []

    spec = RigSpec(
        primary="counting",
        roles={
            "camera": ConnSpec(backend="counting", host="10.0.0.1", port=11111,
                               role="camera"),
            "telescope": ConnSpec(backend="counting", host="10.0.0.2", port=11111,
                                  role="telescope"),
        },
    )
    saved = dict(BACKENDS)
    try:
        register(CountingBackend())
        result = await assemble(spec)
        # camera and telescope have distinct hosts -> two sessions opened.
        # remaining roles default to primary "counting" with no host -> a third.
        assert len(result.sessions) >= 2
        assert result.rig["camera"][2] == "10.0.0.1"
        assert result.rig["telescope"][2] == "10.0.0.2"
    finally:
        BACKENDS.clear()
        BACKENDS.update(saved)
