"""Orchestrator: connect a live rig from a RigSpec (Stage A, W1.3.0/W1.3).

The T2 fault-injecting suite drives ``connect_profile`` over in-process
``FakeBackend``/``FakeSession`` fakes (NOT unittest.mock, per the repo test
convention) to pin the reshaped contract: one ``RoleResult`` per requested role,
endpoint-isolated sessions keyed by the ``(backend, host, port)`` tuple, hostless
coalescing, graceful per-role degrade, the guider/guide-camera/solver seams, and
a JSON-able ``to_dict``. The carried-forward ``to_dict``-shape and graceful-degrade
assertions prove the ``assemble->connect_profile`` rename is non-regressive.
"""
import pytest

# importing the backends package self-registers the "sim" backend (and the rest)
# in the global registry (the canonical adapters live under devices/backends/).
from astrodeck.devices.backends import sim_backend  # noqa: F401  (side-effect import)
from astrodeck.devices.backend import (
    BACKENDS,
    ConnSpec,
    RigSpec,
    register,
)
from astrodeck.devices.orchestrator import (
    ConnectResult,
    RoleResult,
    connect_profile,
    to_dict,
)


# --------------------------------------------------------------------- fakes

class FakeSession:
    """An in-process BackendSession with fault-injection hooks.

    ``device_for(role, conn)`` decides what get_device returns:
      - raise (a callable mapped to an exception) -> per-role failure;
      - None -> "role unavailable";
      - anything else -> placed in rig.
    Records open/close so tests can count call-fan-out."""

    def __init__(self, name, conn, *, devices=None, raise_roles=(),
                 none_roles=(), guider=None, guide_cam=None, solver=None,
                 opened_log=None):
        self.name = name
        self.conn = conn
        self._devices = devices or {}
        self._raise_roles = set(raise_roles)
        self._none_roles = set(none_roles)
        self._guider = guider
        self._guide_cam = guide_cam
        self._solver = solver
        self.closed = False
        self.get_device_calls = []

    async def get_device(self, role, conn):
        self.get_device_calls.append(role)
        if role in self._raise_roles:
            raise RuntimeError(f"{role} offline")
        if role in self._none_roles:
            return None
        return self._devices.get(role, ("dev", role, conn.host))

    def native_guider(self):
        return self._guider

    def guide_camera(self):
        return self._guide_cam

    def native_solver(self):
        return self._solver

    async def health(self):
        return None

    async def close(self):
        self.closed = True


class FakeBackend:
    """A registerable backend that opens FakeSessions and counts open() calls."""

    discoverable = False
    hostless = False

    def __init__(self, name, roles, *, hostless=False, raise_open=False,
                 raise_roles=(), none_roles=(), guider=None, guide_cam=None,
                 solver=None, devices=None):
        self.name = name
        self.label = name.title()
        self.roles = roles
        self.hostless = hostless
        self._raise_open = raise_open
        self._raise_roles = raise_roles
        self._none_roles = none_roles
        self._guider = guider
        self._guide_cam = guide_cam
        self._solver = solver
        self._devices = devices
        self.opens = []                 # list of representative ConnSpecs
        self.sessions = []              # the FakeSessions handed out

    async def open(self, conn):
        self.opens.append(conn)
        if self._raise_open:
            raise RuntimeError(f"{self.name} unreachable")
        s = FakeSession(self.name, conn, devices=self._devices,
                        raise_roles=self._raise_roles, none_roles=self._none_roles,
                        guider=self._guider, guide_cam=self._guide_cam,
                        solver=self._solver)
        self.sessions.append(s)
        return s

    async def discover(self):
        return []


@pytest.fixture
def registry():
    """Snapshot/restore the global registry so each test registers fakes cleanly
    without leaking into the real process registry."""
    saved = dict(BACKENDS)
    try:
        yield register
    finally:
        BACKENDS.clear()
        BACKENDS.update(saved)


def _result_for(result, role):
    return next(rr for rr in result.results if rr.role == role)


# ---------------------------------------------------- T2(9): one RoleResult/role

@pytest.mark.asyncio
async def test_every_requested_role_gets_exactly_one_roleresult(registry):
    be = FakeBackend("fakeprimary", ("camera", "telescope", "focuser"))
    registry(be)
    result = await connect_profile(RigSpec(primary="fakeprimary"))
    assert isinstance(result, ConnectResult)
    seen = [rr.role for rr in result.results]
    assert sorted(seen) == ["camera", "focuser", "telescope"]
    # exactly one per requested role (no duplicates)
    assert len(seen) == len(set(seen)) == 3


@pytest.mark.asyncio
async def test_unrequested_role_yields_no_roleresult(registry):
    # primary fills only camera/telescope; switch/safety/etc are NOT requested.
    be = FakeBackend("twobe", ("camera", "telescope"))
    registry(be)
    result = await connect_profile(RigSpec(primary="twobe"))
    roles = {rr.role for rr in result.results}
    assert roles == {"camera", "telescope"}
    assert "switch" not in roles and "safety" not in roles and "guider" not in roles


@pytest.mark.asyncio
async def test_requested_set_is_primary_roles_union_overrides(registry):
    # an explicit override for a role NOT in the primary's roles is STILL
    # requested (surfaces a failed RoleResult, never vanishes).
    primary = FakeBackend("nina_like", ("camera", "telescope", "guider"))
    guide = FakeBackend("phd2_like", ("guider",), hostless=True, guider="G")
    registry(primary)
    registry(guide)
    spec = RigSpec(primary="nina_like",
                   roles={"safety": ConnSpec(backend="nina_like", role="safety")})
    result = await connect_profile(spec)
    roles = {rr.role for rr in result.results}
    assert roles == {"camera", "telescope", "guider", "safety"}


# -------------------------------------- T2(1): open() raises -> endpoint down

@pytest.mark.asyncio
async def test_open_raise_fails_endpoint_roles_others_survive(registry):
    bad = FakeBackend("bad", ("camera", "focuser"), raise_open=True)
    good = FakeBackend("good", ("telescope",))
    registry(bad)
    registry(good)
    # camera+focuser on the bad endpoint; telescope overridden to good.
    spec = RigSpec(primary="bad",
                   roles={"telescope": ConnSpec(backend="good", role="telescope")})
    result = await connect_profile(spec)
    for role in ("camera", "focuser"):
        rr = _result_for(result, role)
        assert rr.ok is False and rr.attempted is True
        assert "unreachable" in rr.error
    # the other backend still came up.
    assert _result_for(result, "telescope").ok is True
    assert "telescope" in result.rig
    # the failed endpoint contributed no session.
    assert not any(k[0] == "bad" for k in result.sessions)


# ------------------------------------ T2(2): get_device raises -> role degraded

@pytest.mark.asyncio
async def test_get_device_raise_degrades_only_that_role(registry):
    be = FakeBackend("flaky", ("camera", "telescope", "focuser"),
                     raise_roles=("focuser",))
    registry(be)
    result = await connect_profile(RigSpec(primary="flaky"))
    foc = _result_for(result, "focuser")
    assert foc.ok is False and foc.attempted is True and "focuser offline" in foc.error
    assert "focuser" not in result.rig
    # siblings on the same session still came up.
    assert _result_for(result, "camera").ok is True
    assert _result_for(result, "telescope").ok is True
    assert "camera" in result.rig and "telescope" in result.rig


# ----------------------------- T2(3): get_device returns None -> role unavailable

@pytest.mark.asyncio
async def test_get_device_none_is_attempted_not_ok_no_crash(registry):
    be = FakeBackend("nonebe", ("camera", "telescope"), none_roles=("telescope",))
    registry(be)
    result = await connect_profile(RigSpec(primary="nonebe"))
    tel = _result_for(result, "telescope")
    assert tel.ok is False and tel.attempted is True and tel.error == "role unavailable"
    assert "telescope" not in result.rig
    assert _result_for(result, "camera").ok is True


# --------------------------------- T2(4): two roles on one endpoint -> one open()

@pytest.mark.asyncio
async def test_two_roles_one_endpoint_open_called_once(registry):
    be = FakeBackend("oneep", ("camera", "telescope"))
    registry(be)
    # both roles resolve to the primary with no host -> one endpoint.
    result = await connect_profile(RigSpec(primary="oneep"))
    assert len(be.opens) == 1
    assert len(result.sessions) == 1


# ------------------------------- T2(7): two native hosts -> two sessions

@pytest.mark.asyncio
async def test_distinct_hosts_open_separate_sessions(registry):
    be = FakeBackend("multi", ("camera", "telescope"))
    registry(be)
    spec = RigSpec(
        primary="multi",
        roles={
            "camera": ConnSpec(backend="multi", host="10.0.0.1", port=11111,
                               role="camera"),
            "telescope": ConnSpec(backend="multi", host="10.0.0.2", port=11111,
                                  role="telescope"),
        },
    )
    result = await connect_profile(spec)
    # two distinct hosts -> two opens / two sessions, keyed by the tuple.
    assert len(be.opens) == 2
    assert ("multi", "10.0.0.1", 11111) in result.sessions
    assert ("multi", "10.0.0.2", 11111) in result.sessions
    # correct device per host (FakeSession.get_device tags conn.host).
    assert result.rig["camera"][2] == "10.0.0.1"
    assert result.rig["telescope"][2] == "10.0.0.2"


# ------------------------ T2(10)/(11): hostless coalescing (sim + generic hostless)

@pytest.mark.asyncio
async def test_sim_override_with_stray_host_opens_simrig_once():
    # primary=sim (real sim backend, hostless) with a sim override carrying a
    # stray non-None host: opens the SimRig EXACTLY once and shares its state.
    spec = RigSpec(
        primary="sim",
        roles={"camera": ConnSpec(backend="sim", host="10.9.9.9", port=12345,
                                  role="camera")},
    )
    result = await connect_profile(spec)
    assert len(result.sessions) == 1
    assert ("sim", None, None) in result.sessions
    # the override role and a primary role share the SAME SimRig shared state.
    session = result.sessions[("sim", None, None)]
    assert session.shared_state is not None


@pytest.mark.asyncio
async def test_generic_hostless_backend_coalesces_despite_stray_hosts(registry):
    # a NEW hostless FakeBackend (NOT named sim/phd2) with stray hosts on >=2
    # roles still coalesces to ONE session -- proving _group keys off
    # Backend.hostless, not a literal {sim, phd2} name set.
    be = FakeBackend("hostless_fake", ("camera", "telescope"), hostless=True)
    registry(be)
    spec = RigSpec(
        primary="hostless_fake",
        roles={
            "camera": ConnSpec(backend="hostless_fake", host="1.1.1.1", port=1,
                               role="camera"),
            "telescope": ConnSpec(backend="hostless_fake", host="2.2.2.2", port=2,
                                  role="telescope"),
        },
    )
    result = await connect_profile(spec)
    assert len(be.opens) == 1
    assert len(result.sessions) == 1
    assert ("hostless_fake", None, None) in result.sessions


# ----------------------------------- T2(6): guider from the guider-role session

@pytest.mark.asyncio
async def test_guider_picked_from_guider_session_not_a_device(registry):
    # primary fills imaging roles; guider overridden to a guider-only backend.
    primary = FakeBackend("img", ("camera", "telescope"))
    phd2like = FakeBackend("phd2x", ("guider",), hostless=True, guider="THE-GUIDER")
    registry(primary)
    registry(phd2like)
    spec = RigSpec(primary="img",
                   roles={"guider": ConnSpec(backend="phd2x", role="guider")})
    result = await connect_profile(spec)
    assert result.guider == "THE-GUIDER"
    gr = _result_for(result, "guider")
    assert gr.ok is True and gr.attempted is True
    # the guider was NOT placed as a device in rig.
    assert "guider" not in result.rig


# --------------- T2(13): stray-host guider override on a hostless backend resolves

@pytest.mark.asyncio
async def test_stray_host_guider_override_on_hostless_still_resolves(registry):
    phd2like = FakeBackend("phd2local", ("guider",), hostless=True, guider="GG")
    primary = FakeBackend("img2", ("camera",))
    registry(primary)
    registry(phd2like)
    # the guider override carries a stray non-None host; _pick_guider must key off
    # the normalized (phd2local, None, None) session, NOT the raw addressing.
    spec = RigSpec(
        primary="img2",
        roles={"guider": ConnSpec(backend="phd2local", host="7.7.7.7", port=4400,
                                  role="guider")},
    )
    result = await connect_profile(spec)
    assert result.guider == "GG"
    assert _result_for(result, "guider").ok is True


# ------------------------------------------ T2(5)/(12)/solver + guide_camera seams

@pytest.mark.asyncio
async def test_solver_prefers_session_native_solver(registry):
    be = FakeBackend("solvebe", ("camera",), solver="NATIVE-SOLVER")
    registry(be)
    result = await connect_profile(RigSpec(primary="solvebe"))
    assert result.solver == "NATIVE-SOLVER"


@pytest.mark.asyncio
async def test_solver_falls_back_to_get_solver_when_session_has_none(registry):
    # a real-camera session returns None native_solver -> _pick_solver delegates
    # to solve.get_solver, which (no ASTAP) yields a SimSolver carrying the
    # camera-session-derived mode. For a "native" session that mode == "alpaca".
    from astrodeck.solve import SimSolver

    be = FakeBackend("native", ("camera",), solver=None)
    registry(be)
    result = await connect_profile(RigSpec(primary="native"))
    # with no ASTAP installed in CI, get_solver returns a refusing SimSolver.
    if isinstance(result.solver, SimSolver):
        assert result.solver.mode == "alpaca"


@pytest.mark.asyncio
@pytest.mark.parametrize("guide_cam, expected", [
    pytest.param("GUIDE-CAM", "GUIDE-CAM", id="surfaced_from_camera_session"),
    pytest.param(None, None, id="none_when_backend_has_no_guide_cam"),
])
async def test_guide_camera_from_camera_session(registry, guide_cam, expected):
    be = FakeBackend("gcbe", ("camera",), guide_cam=guide_cam)
    registry(be)
    result = await connect_profile(RigSpec(primary="gcbe"))
    if expected is None:
        assert result.guide_camera is None
    else:
        assert result.guide_camera == expected


# ---------------------------------------------- T2(8): teardown on unexpected raise

@pytest.mark.asyncio
async def test_unexpected_raise_closes_opened_sessions(registry):
    """A NON-Exception (BaseException) raised AFTER a session opened must trigger
    the teardown-on-failure path: every already-opened session is close()d, then
    the error re-raises (transport-leak safety, W1.3 teardown contract)."""
    opened_sessions = []

    class BoomSession(FakeSession):
        async def get_device(self, role, conn):
            if role == "telescope":
                # BaseException is NOT caught by the per-role except Exception, so
                # it propagates to the outer except BaseException (teardown).
                raise KeyboardInterrupt("boom")
            return await super().get_device(role, conn)

    class BoomBackend(FakeBackend):
        async def open(self, conn):
            s = BoomSession(self.name, conn)
            self.sessions.append(s)
            opened_sessions.append(s)
            return s

    # ONE endpoint with both roles, so the session is opened (and recorded) before
    # the get_device loop hits the telescope role that explodes.
    be = BoomBackend("boomep", ("camera", "telescope"))
    registry(be)
    with pytest.raises(KeyboardInterrupt):
        await connect_profile(RigSpec(primary="boomep"))
    # the session opened before the boom must have been closed.
    assert opened_sessions and all(s.closed for s in opened_sessions)


# ------------------------------------------ carried-forward: sim happy path + shape

@pytest.mark.asyncio
async def test_sim_rig_full_comes_up():
    result = await connect_profile(RigSpec(primary="sim"))
    for role in ("camera", "telescope", "focuser", "filterwheel", "switch", "safety"):
        assert role in result.rig, f"missing role {role}"
        assert _result_for(result, role).ok is True
    # the sim's guider is present: the native guider by default (P2-T3 flip)
    # when the engine wheel is installed, else the legacy SimGuider fallback.
    from astrodeck.providers import NATIVE_AVAILABLE
    assert result.guider is not None
    assert result.guider.name == ("AstroDeck native" if NATIVE_AVAILABLE
                                  else "Sim Guider")
    # all sim roles share ONE session (hostless coalescing).
    assert len(result.sessions) == 1
    # the sim guide camera flows through the Protocol accessor.
    assert result.guide_camera is not None


@pytest.mark.asyncio
async def test_to_dict_summary_shape_carried_forward():
    result = await connect_profile(RigSpec(primary="sim"))
    d = to_dict(result)
    # back-compat superset: the legacy keys are preserved, results added.
    assert {"roles", "sessions", "has_guider", "has_native_solver", "failures"} <= set(d)
    assert "results" in d
    assert "camera" in d["roles"]
    assert d["has_guider"] is True
    # the sim session now exposes a guarded native_solver, so has_native_solver
    # is True (it carries the SimSolver bound to the sim camera session).
    assert d["has_native_solver"] is True
    assert d["failures"] == {}


@pytest.mark.asyncio
async def test_to_dict_sessions_jsonable_and_none_safe_sort(registry):
    # C1 gate: two SAME-host endpoints differing in port-presence
    # (('native','host',11111) and ('native','host',None)) must NOT raise a
    # TypeError on sort and must emit JSON-able strings.
    be = FakeBackend("native", ("camera", "telescope"))
    registry(be)
    spec = RigSpec(
        primary="native",
        roles={
            "camera": ConnSpec(backend="native", host="host", port=11111,
                               role="camera"),
            "telescope": ConnSpec(backend="native", host="host", port=None,
                                  role="telescope"),
        },
    )
    result = await connect_profile(spec)
    # two distinct endpoint keys (same host, differing port-presence).
    assert len(result.sessions) == 2
    d = to_dict(result)               # must NOT raise TypeError
    import json
    json.dumps(d["sessions"])         # JSON-able
    assert d["sessions"] == sorted(d["sessions"])
    assert all(isinstance(s, str) for s in d["sessions"])


@pytest.mark.asyncio
async def test_graceful_degrade_records_failure_carried_forward(registry):
    be = FakeBackend("flaky2", ("camera", "telescope", "focuser"),
                     raise_roles=("focuser",))
    registry(be)
    result = await connect_profile(RigSpec(primary="flaky2"))
    d = to_dict(result)
    assert "focuser" in d["failures"]
    assert "focuser offline" in d["failures"]["focuser"]
    assert "camera" in result.rig and "telescope" in result.rig
    assert "focuser" not in result.rig
