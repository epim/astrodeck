"""D-RIG-5: a protected switch port is refused SERVER-SIDE, and the per-port
settings store behind that decision (``astrodeck/power_guard.py``).

The lock used to be a regex in a React sheet, which meant curl could cut power to
the mount mid-sequence and the server would do it without comment. These tests
pin the two halves of moving that decision here:

1. the POLICY - the name heuristic is a default, a stored flag beats it in both
   directions, and a partial write cannot erase the half it was not editing;
2. the ENFORCEMENT - ``POST /api/switch/set`` 409s BEFORE the device write, a
   paused run still counts, and the write cap on the settings route is
   ``config.safety`` (so the shipped operator, who holds neither that nor
   ``control.power``, cannot re-point which ports the engine protects).

THE POLICY HALF IS MIRRORED; THE REFUSALS ARE NOT. The three routes are declared
here on a throwaway FastAPI app, verbatim in body and in
``require(...)``/``@declare(...)``, because that is what lets the guard's ORDER
be graded at all (the fake switch keeps a call log, so "refused before the
write" is an assertion rather than a status code). What a mirror cannot grade is
that the patch LANDED - a copy cannot fail when the original does - so the
section at the bottom of this file drives the SHIPPED routes out of
``api/app.py`` with the same fake box behind them, and every refusal that is
about the request rather than about the policy is asserted there.
"""
from __future__ import annotations

import json
import logging

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from astrodeck import power_guard
from astrodeck.auth import (CAP_CONFIG_SAFETY, CAP_CONTROL_POWER,
                            CAP_VIEW_STATUS, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.auth.deps import require
from astrodeck.auth.rbac import declare
from astrodeck.devices.base import DeviceError, SwitchPort
from astrodeck.locations import UNCHANGED


# --------------------------------------------------------------------- fixtures

def _port(port_id: int, name: str, value: float = 1.0) -> SwitchPort:
    """A boolean switch port as a driver hands one back: the three D-RIG-5 fields
    are at their dataclass defaults, because no driver reports them."""
    return SwitchPort(id=port_id, name=name, can_write=True, is_boolean=True,
                      value=value)


class FakeSwitch:
    """A power box that REMEMBERS. ``calls`` is the whole point: a guard that
    refuses after the write is indistinguishable from one that refuses before it
    if all you check is the status code."""

    def __init__(self, ports: list[SwitchPort]):
        self.ports = ports
        self.calls: list[tuple[int, float]] = []

    async def get_ports(self) -> list[SwitchPort]:
        return self.ports

    async def set_port(self, port_id: int, value: float) -> None:
        self.calls.append((port_id, value))
        for p in self.ports:
            if p.id == port_id:
                p.value = value


class FakeEngine:
    """``running`` stays True through a pause (``sequence/engine.py``), which is
    why a paused run is refused without the guard knowing what a pause is."""

    def __init__(self, running: bool = False, paused: bool = False):
        self.running = running
        self.paused = paused


class FakeHub:
    def __init__(self, switch, engine):
        self._switch = switch
        self.engine = engine

    def require(self, kind: str):
        if self._switch is None:
            raise DeviceError(f"no {kind} connected")
        return self._switch


class SwitchBody(BaseModel):
    port_id: int
    value: float


class SwitchPortSettingsBody(BaseModel):
    """Mirror of the module-scope body model S7L adds beside ``SwitchBody``."""
    protect_during_run: bool | None = None
    follow_dew: bool = False


def _make_app(hub) -> FastAPI:
    """The three routes, verbatim from the S7L patch."""
    app = FastAPI()

    def _profile_id():
        from astrodeck.config import config_store
        return config_store.cfg().active_profile_id

    @app.get("/api/switch/ports", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def switch_ports():
        try:
            sw = hub.require("switch")
            return [p.__dict__ for p in power_guard.annotate(
                await sw.get_ports(),
                run_active=bool(getattr(hub.engine, "running", False)),
                profile_id=_profile_id())]
        except DeviceError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/switch/set", dependencies=[Depends(require(CAP_CONTROL_POWER))])
    @declare(CAP_CONTROL_POWER)
    async def switch_set(body: SwitchBody):
        try:
            sw = hub.require("switch")
            profile_id = _profile_id()
            run_active = bool(getattr(hub.engine, "running", False))
            ports = await sw.get_ports()
            target = next((p for p in ports if p.id == body.port_id), None)
            if target is None:
                raise HTTPException(404, detail={
                    "detail": f"this power box has no port {body.port_id}",
                    "code": "unknown_port", "port_id": body.port_id})
            why = power_guard.refusal(target, run_active=run_active,
                                      profile_id=profile_id)
            if why is not None:
                raise HTTPException(409, detail={
                    "detail": why, "code": "port_protected",
                    "port_id": target.id, "port_name": target.name})
            await sw.set_port(body.port_id, body.value)
            return [p.__dict__ for p in power_guard.annotate(
                await sw.get_ports(), run_active=run_active,
                profile_id=profile_id)]
        except (DeviceError, RuntimeError) as e:
            raise HTTPException(409, str(e))

    @app.put("/api/switch/ports/{port_id}",
             dependencies=[Depends(require(CAP_CONFIG_SAFETY))])
    @declare(CAP_CONFIG_SAFETY)
    async def switch_port_settings(port_id: int, body: SwitchPortSettingsBody):
        try:
            sw = hub.require("switch")
            ports = await sw.get_ports()
        except DeviceError as e:
            raise HTTPException(409, str(e))
        if not any(getattr(p, "id", None) == port_id for p in ports):
            raise HTTPException(404, detail={
                "detail": f"this power box has no port {port_id}",
                "code": "unknown_port", "port_id": port_id})
        present = body.model_fields_set
        try:
            power_guard.set_port_settings(
                port_id,
                protect_during_run=(body.protect_during_run
                                    if "protect_during_run" in present
                                    else UNCHANGED),
                follow_dew=(body.follow_dew if "follow_dew" in present
                            else UNCHANGED),
                profile_id=_profile_id())
        except ValueError as e:
            raise HTTPException(422, detail={"detail": str(e),
                                             "code": "invalid_port_setting",
                                             "port_id": port_id})
        return [p.__dict__ for p in power_guard.annotate(
            ports, run_active=bool(getattr(hub.engine, "running", False)),
            profile_id=_profile_id())]

    return app


class FakeAuthProvider:
    """A fixed principal for every request (mirrors tests/test_rbac_enforcement)."""

    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """A per-TEST config dir and config store.

    ``tests/conftest.py::_never_touch_the_real_config`` is session-scoped: it
    guarantees nothing writes the developer's real ``server/config/``, but every
    test in the run shares ONE throwaway directory. A store keyed per port would
    then carry a decision from one test into the next (and, worse, the
    log-once-per-corrupt-file marker would make the second corrupt-store test
    assert on a warning the first one already consumed). So repoint both here.
    """
    import astrodeck.config as config_mod
    from astrodeck.config import ConfigStore
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "config_store",
                        ConfigStore(path=tmp_path / "astrodeck.json"))
    power_guard._corrupt_warned.clear()
    yield
    power_guard._corrupt_warned.clear()


def _client(*, ports=None, running=False, paused=False, role=None):
    sw = FakeSwitch(ports if ports is not None else
                    [_port(1, "Mount"), _port(2, "Dew A")])
    hub = FakeHub(sw, FakeEngine(running=running, paused=paused))
    if role is not None:
        set_active_provider(FakeAuthProvider(principal_for_role(role)))
    else:
        reset_active_provider()
    return TestClient(_make_app(hub)), sw


# ------------------------------------------------------------------- the policy

def test_the_name_is_the_default_when_nobody_has_decided():
    """A fresh rig with an empty store behaves exactly as the shipped UI did."""
    assert power_guard.is_protected(_port(1, "Mount")) is True
    assert power_guard.is_protected(_port(2, "Dew A")) is False
    # ...and the field the driver hands us stays at its default: the store is the
    # only place a decision lives.
    assert _port(1, "Mount").protect_during_run is None


def test_a_stored_true_beats_a_harmless_name():
    """A dew port a user pinned is protected even though nothing matches /mount|camera|usb/."""
    power_guard.set_port_settings(2, protect_during_run=True)
    assert power_guard.is_protected(_port(2, "Dew A")) is True


def test_an_explicit_false_beats_the_name():
    """The direction that a naive implementation gets wrong.

    SABOTAGE (run red, restored): ``_is_protected`` returning
    ``bool(stored) or bool(_PROTECTED_NAME.search(...))`` instead of returning
    the stored flag when it is set. An ``or`` cannot express a veto - it only
    ever ADDS protection - so an operator who deliberately unlocked a port named
    "Camera USB hub" would find it locked again on the next tap, with nothing
    saying why.
    """
    power_guard.set_port_settings(1, protect_during_run=False)
    assert power_guard.is_protected(_port(1, "Mount")) is False
    # and the veto survives a rename, which is the whole reason False is not None
    assert power_guard.is_protected(_port(1, "Main Camera")) is False


def test_a_dew_edit_does_not_erase_the_protection_decision():
    """The UNCHANGED sentinel, at the store's own boundary.

    SABOTAGE (run red, restored): ``set_port_settings(..., protect_during_run:
    object = None)``. With ``None`` as the default, the ``is not UNCHANGED``
    guard is always true and every dew-only write stamps ``None`` over the flag -
    the exact bug ``locations.py:45-53`` documents for horizon polylines, and
    invisible, because ``None`` is a legal value that simply re-enables the name
    heuristic.
    """
    power_guard.set_port_settings(1, protect_during_run=False)
    out = power_guard.set_port_settings(1, follow_dew=True)
    assert out == {"protect_during_run": False, "follow_dew": True}
    assert power_guard.port_settings(1)["protect_during_run"] is False
    # ...and the mirror: a protection-only edit leaves follow_dew alone.
    power_guard.set_port_settings(1, protect_during_run=True)
    assert power_guard.port_settings(1) == {"protect_during_run": True,
                                            "follow_dew": True}


def test_none_is_writable_and_means_follow_the_name_again():
    """Clearing a decision is a real edit, not the absence of one."""
    power_guard.set_port_settings(1, protect_during_run=False)
    power_guard.set_port_settings(1, protect_during_run=None)
    assert power_guard.port_settings(1)["protect_during_run"] is None
    assert power_guard.is_protected(_port(1, "Mount")) is True


def test_settings_are_keyed_per_profile():
    """Two rigs, one config dir: a decision made on one profile is not the other's."""
    power_guard.set_port_settings(1, protect_during_run=False, profile_id="rig-a")
    assert power_guard.port_settings(1, profile_id="rig-a")["protect_during_run"] is False
    assert power_guard.port_settings(1, profile_id="rig-b")["protect_during_run"] is None


# --------------------------------------------------------------------- totality

def test_port_settings_is_total_for_an_unknown_port():
    """S7f's dew loop calls this for every port on the box, every tick."""
    assert power_guard.port_settings(99) == {"protect_during_run": None,
                                             "follow_dew": False}
    power_guard.set_port_settings(1, follow_dew=True)
    assert power_guard.port_settings(99) == {"protect_during_run": None,
                                             "follow_dew": False}


def test_a_corrupt_store_falls_back_to_the_name_and_logs_once(caplog):
    """A hand-edited JSON file must not stop the night.

    SABOTAGE (run red, restored): ``_load_store`` letting ``ValueError`` out of
    ``read_json`` instead of catching it. The read then raises through
    ``is_protected`` -> ``refusal`` -> the route, and a corrupt sidecar turns
    every switch read into a 500 and (S7f) stops the dew heaters.
    """
    power_guard.switch_ports_path().write_text("{not json at all",
                                               encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="astrodeck.power_guard"):
        assert power_guard.is_protected(_port(1, "Mount")) is True
        assert power_guard.is_protected(_port(2, "Dew A")) is False
        assert power_guard.port_settings(1) == {"protect_during_run": None,
                                                "follow_dew": False}
    warnings = [r for r in caplog.records
                if r.levelno >= logging.WARNING
                and r.name == "astrodeck.power_guard"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "switch_ports.json" in warnings[0].getMessage()


def test_a_store_that_parses_to_a_list_is_also_survivable():
    """Valid JSON, wrong shape. ``read_json_or`` would hand this straight back."""
    power_guard.switch_ports_path().write_text("[1, 2, 3]", encoding="utf-8")
    assert power_guard.port_settings(1) == {"protect_during_run": None,
                                            "follow_dew": False}
    assert power_guard.is_protected(_port(1, "Mount")) is True


def test_a_write_over_a_corrupt_store_recovers_it():
    power_guard.switch_ports_path().write_text("garbage", encoding="utf-8")
    power_guard.set_port_settings(1, protect_during_run=True)
    data = json.loads(power_guard.switch_ports_path().read_text(encoding="utf-8"))
    assert data["__default__"]["1"]["protect_during_run"] is True


# -------------------------------------------------------------------- annotate

def test_annotate_fills_the_derived_fields_without_touching_the_driver():
    """``protected_now`` is the server's answer, and the driver's objects are the
    driver's. Copies, for the reason ``api/redact.py:340-343`` gives: a driver's
    port list can alias its own cache, so writing a derived field in place is a
    way to poison device state from a read-only route."""
    ports = [_port(1, "Mount"), _port(2, "Dew A")]
    out = power_guard.annotate(ports, run_active=True)
    assert [p.protected_now for p in out] == [True, False]
    assert all(o is not p for o, p in zip(out, ports))
    assert all(p.protected_now is False for p in ports)
    assert all(p.protect_during_run is None for p in ports)
    # and with no run live, nothing is protected RIGHT NOW
    assert [p.protected_now for p in
            power_guard.annotate(ports, run_active=False)] == [False, False]


def test_annotate_reports_the_stored_tri_state_not_the_effective_answer():
    """The client has to be able to tell "unset, following the name" from
    "someone pinned this", or the settings toggle cannot show what it changes."""
    out = power_guard.annotate([_port(1, "Mount")], run_active=True)
    assert out[0].protect_during_run is None      # nobody has decided
    assert out[0].protected_now is True           # but the name says protected
    power_guard.set_port_settings(1, protect_during_run=True, follow_dew=True)
    out = power_guard.annotate([_port(1, "Mount")], run_active=False)
    assert out[0].protect_during_run is True
    assert out[0].follow_dew is True
    assert out[0].protected_now is False          # no run: not protected NOW


def test_refusal_is_silent_when_no_run_is_live():
    assert power_guard.refusal(_port(1, "Mount"), run_active=False) is None
    assert power_guard.refusal(_port(2, "Dew A"), run_active=True) is None
    why = power_guard.refusal(_port(1, "Mount"), run_active=True)
    assert why is not None
    assert why.startswith("Mount is protected while a run is live:")
    assert "Power settings" in why


# ---------------------------------------------------------------- the enforcement

def test_the_route_switches_a_protected_port_when_no_run_is_live():
    client, sw = _client(running=False)
    r = client.post("/api/switch/set", json={"port_id": 1, "value": 0.0})
    assert r.status_code == 200, r.text
    assert sw.calls == [(1, 0.0)]


def test_the_route_refuses_a_protected_port_during_a_run():
    """409 ``port_protected`` AND no write.

    SABOTAGE (run red, restored): moving the ``power_guard.refusal`` block below
    ``await sw.set_port(...)`` in the mirrored route. The status code stays 409
    and the message is word-for-word the same - the ONLY witness that the guard
    ran too late is ``sw.calls``, which is why the fake keeps one. A guard that
    refuses after the write has already cut power to the mount is not a guard.
    """
    client, sw = _client(running=True)
    r = client.post("/api/switch/set", json={"port_id": 1, "value": 0.0})
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "port_protected"
    assert body["port_id"] == 1 and body["port_name"] == "Mount"
    assert body["detail"].startswith("Mount is protected while a run is live:")
    assert sw.calls == [], "the device was written BEFORE the refusal"
    assert sw.ports[0].value == 1.0


def test_an_unprotected_port_is_still_switchable_during_a_run():
    """The dew heater is exactly what you reach for mid-run."""
    client, sw = _client(running=True)
    r = client.post("/api/switch/set", json={"port_id": 2, "value": 0.0})
    assert r.status_code == 200, r.text
    assert sw.calls == [(2, 0.0)]


def test_a_paused_run_still_refuses():
    """A pause is a frame boundary, not a release: the run still owns the camera
    and still intends to continue, so cutting the mount loses the alignment the
    resume needs. ``engine.running`` stays True through a pause, which is why the
    guard gets this right without knowing what a pause is."""
    client, sw = _client(running=True, paused=True)
    r = client.post("/api/switch/set", json={"port_id": 1, "value": 0.0})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "port_protected"
    assert sw.calls == []


def test_a_cleared_port_is_switchable_during_a_run():
    """End to end: the PUT is what makes the 409 go away."""
    client, sw = _client(running=True)
    assert client.put("/api/switch/ports/1",
                      json={"protect_during_run": False}).status_code == 200
    r = client.post("/api/switch/set", json={"port_id": 1, "value": 0.0})
    assert r.status_code == 200, r.text
    assert sw.calls == [(1, 0.0)]


def test_get_carries_protected_now_for_the_mount_during_a_run():
    client, sw = _client(running=True)
    rows = client.get("/api/switch/ports").json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[1]["protected_now"] is True
    assert by_id[2]["protected_now"] is False
    assert by_id[1]["protect_during_run"] is None
    assert by_id[1]["follow_dew"] is False
    # ...and the driver's own objects were never written, on the SAME box that
    # just answered `protected_now: true` - the annotation is per-response.
    assert all(p.protected_now is False for p in sw.ports)
    assert all(p.protect_during_run is None for p in sw.ports)
    # the same read with no run live
    idle_client, _ = _client(running=False)
    assert all(r["protected_now"] is False
               for r in idle_client.get("/api/switch/ports").json())


def test_the_put_leaves_the_protection_flag_alone_when_it_sends_only_follow_dew():
    """``model_fields_set``, at the API boundary. An absent key is not ``null``."""
    client, _ = _client(running=False)
    assert client.put("/api/switch/ports/1",
                      json={"protect_during_run": False}).status_code == 200
    rows = client.put("/api/switch/ports/1", json={"follow_dew": True}).json()
    row = next(r for r in rows if r["id"] == 1)
    assert row["protect_during_run"] is False, "the dew edit erased the decision"
    assert row["follow_dew"] is True


# ---------------------------------------------------------------------- the caps

def test_a_viewer_is_refused_the_settings_put():
    client, _ = _client(role="viewer")
    assert client.put("/api/switch/ports/1",
                      json={"protect_during_run": True}).status_code == 403


def test_an_operator_is_refused_the_settings_put():
    """Deciding which ports the engine refuses during a run is protection policy,
    not power control - so it carries ``config.safety`` (the ``cooling`` /
    ``standards`` family at ``app.py:3244-3248``), which the shipped operator
    does not hold. The operator does not hold ``control.power`` either
    (``capabilities.py:118-122``), so choosing ``control.power`` here would have
    made this route admin-only BY ACCIDENT rather than on purpose."""
    from astrodeck.auth import caps_for_role
    assert CAP_CONFIG_SAFETY not in caps_for_role("operator")
    assert CAP_CONTROL_POWER not in caps_for_role("operator")
    client, _ = _client(role="operator")
    assert client.put("/api/switch/ports/1",
                      json={"protect_during_run": True}).status_code == 403


def test_the_put_grades_config_safety_and_not_control_power():
    """The cap CHOICE, graded - and it needs its own test, because no role in the
    shipped table holds exactly one of the two.

    SABOTAGE (run red, restored): wiring the mirrored PUT to
    ``require(CAP_CONTROL_POWER)`` / ``@declare(CAP_CONTROL_POWER)``. The
    viewer/operator/admin tests below stayed GREEN through it, and that is the
    argument for ``config.safety`` rather than an accident of the role table: the
    operator holds NEITHER cap (``capabilities.py:118-122``), so both wirings
    happen to be admin-only today. They stop agreeing the moment somebody hands
    an operator ``control.power`` so they can switch a dew heater - at which
    point that operator would silently gain the ability to un-protect the mount
    port mid-run. Deciding what the engine refuses is protection policy, the
    ``cooling`` / ``standards`` family at ``app.py:3244-3248``.

    Two synthetic principals, each holding exactly one of the caps, are the only
    way to tell the two wirings apart.
    """
    from astrodeck.auth import Principal
    powered = Principal(role="custom", caps=frozenset({CAP_CONTROL_POWER,
                                                       CAP_VIEW_STATUS}))
    safetied = Principal(role="custom", caps=frozenset({CAP_CONFIG_SAFETY,
                                                        CAP_VIEW_STATUS}))
    client, _ = _client()
    set_active_provider(FakeAuthProvider(powered))
    assert client.put("/api/switch/ports/1",
                      json={"protect_during_run": False}).status_code == 403
    set_active_provider(FakeAuthProvider(safetied))
    assert client.put("/api/switch/ports/1",
                      json={"protect_during_run": False}).status_code == 200
    # ...and control.power is still what SWITCHING needs, unchanged.
    set_active_provider(FakeAuthProvider(safetied))
    assert client.post("/api/switch/set",
                       json={"port_id": 2, "value": 0.0}).status_code == 403
    set_active_provider(FakeAuthProvider(powered))
    assert client.post("/api/switch/set",
                       json={"port_id": 2, "value": 0.0}).status_code == 200


def test_an_admin_may_set_the_flag():
    client, _ = _client(role="admin")
    r = client.put("/api/switch/ports/1", json={"protect_during_run": False})
    assert r.status_code == 200, r.text
    assert power_guard.port_settings(1)["protect_during_run"] is False


def test_a_viewer_may_still_read_the_ports():
    """``view.status``, unchanged - the lock is information a watcher needs."""
    client, _ = _client(role="viewer", running=True)
    rows = client.get("/api/switch/ports").json()
    assert next(r for r in rows if r["id"] == 1)["protected_now"] is True


# ------------------------------------------------------------------- the fence

def test_the_settings_put_is_behind_the_relay_fence_and_the_switch_itself_is_not():
    """S7L adds ``"/api/switch/ports"`` to ``_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES``,
    which is matched with ``startswith``. That is the whole design: the PUT is
    config-shaped (it decides what the engine refuses) and joins the fence, while
    ``POST /api/switch/set`` does not start with the prefix, so operating a
    switch from the relay - which is the product - stays open.

    The check that matters is the SECOND one. A prefix of ``/api/switch`` would
    read as the same intent and would silently kill remote power control, which
    nothing else in the suite would notice. Skips until S7L lands the prefix,
    because ``api/app.py`` belongs to that task; ``tests/test_api_fence.py`` is
    where the fence's own coverage lives.
    """
    import astrodeck.api.app as app_module
    prefixes = tuple(app_module._REMOTE_LOCAL_ONLY_MUTATION_PREFIXES)
    if "/api/switch/ports" not in prefixes:
        pytest.skip("S7L has not landed the /api/switch/ports fence prefix yet")
    assert any("/api/switch/ports/1".startswith(p) for p in prefixes)
    assert not any("/api/switch/set".startswith(p) for p in prefixes)


# ============================================== the SHIPPED routes, not a mirror
#
# Everything above drives the copy at the top of this file, and a copy cannot
# fail when the original does. These drive ``api/app.py`` itself with the same
# fake power box behind ``hub.require``, and they grade the refusals that are
# about the REQUEST rather than about the policy:
#
#   POST /api/switch/set          an unknown port id used to SKIP the whole
#                                 protection guard (``target is None`` meant
#                                 "no row to check") and write anyway
#   PUT  /api/switch/ports/{id}   an unknown port id used to persist a policy
#                                 under itself and answer 200
#
# Both are the guard failing open on the one input it cannot reason about. On a
# Pegasus UPB the id next to the dew strap is the mount.

def _real_client(monkeypatch, *, ports=None, running=False, role="admin"):
    """The shipped app, with one fake power box and one fixed principal."""
    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod

    sw = FakeSwitch(ports if ports is not None else
                    [_port(1, "Mount"), _port(2, "Dew A")])
    app = app_module.create_app()
    # app.py bound ``config_store`` by name at import; the isolated store has to
    # be pointed at there too or ``_switch_profile_id`` reads the real one.
    monkeypatch.setattr(app_module, "config_store", config_mod.config_store)
    monkeypatch.setattr(app_module.hub, "require", lambda kind: sw)
    # ``SequenceEngine.running`` is a read-only property, so the run state is
    # supplied by swapping the engine rather than by writing through it.
    monkeypatch.setattr(app_module.hub, "engine", FakeEngine(running=running))
    set_active_provider(FakeAuthProvider(principal_for_role(role)))
    return TestClient(app), sw


def test_the_real_set_route_404s_an_unknown_port_and_writes_nothing(monkeypatch):
    """SABOTAGE (run red, restored): put ``if target is not None:`` back around
    the protection check in ``switch_set``. The post answers 200 and the driver
    has the write."""
    client, sw = _real_client(monkeypatch)
    r = client.post("/api/switch/set", json={"port_id": 99, "value": 1.0})
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "unknown_port"
    assert detail["port_id"] == 99
    assert sw.calls == [], (
        "an id this box does not report reached the driver: the protection "
        f"guard was skipped for it: {sw.calls}")


def test_the_real_set_route_still_switches_a_port_that_exists(monkeypatch):
    """The other half: the 404 must be about the id and not about the route."""
    client, sw = _real_client(monkeypatch)
    r = client.post("/api/switch/set", json={"port_id": 2, "value": 0.0})
    assert r.status_code == 200, r.text
    assert sw.calls == [(2, 0.0)]


def test_the_real_set_route_still_refuses_a_protected_port_during_a_run(
        monkeypatch):
    """And the 404 is inserted BEFORE the protection check without displacing
    it: a real port that is protected is still a 409, not a 404."""
    client, sw = _real_client(monkeypatch, running=True)
    r = client.post("/api/switch/set", json={"port_id": 1, "value": 0.0})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "port_protected"
    assert sw.calls == []


def test_the_real_settings_put_404s_an_unknown_port_and_persists_nothing(
        monkeypatch):
    """SABOTAGE (run red, restored): drop the ``any(... p.id == port_id ...)``
    check. The put answers 200 and writes an orphan row - which is not inert,
    because the store is keyed per profile per port id and the row comes back
    to life the day a box with that many ports is plugged in."""
    client, _sw = _real_client(monkeypatch)
    r = client.put("/api/switch/ports/99", json={"follow_dew": True})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "unknown_port"
    assert power_guard.port_settings(99)["follow_dew"] is False, (
        "a policy was persisted for a port no box reports")


def test_the_real_settings_put_still_writes_a_port_that_exists(monkeypatch):
    client, _sw = _real_client(monkeypatch)
    r = client.put("/api/switch/ports/2", json={"follow_dew": True})
    assert r.status_code == 200, r.text
    assert power_guard.port_settings(2)["follow_dew"] is True


def test_the_real_settings_put_422s_with_a_machine_code(monkeypatch):
    """Every 4xx on this server carries a ``code``. This one answered a bare
    string, so a client had nothing to branch on but the status - and 422 is
    also what a schema rejection looks like, which is a different bug with a
    different fix."""
    client, _sw = _real_client(monkeypatch)
    r = client.put("/api/switch/ports/2",
                   json={"protect_during_run": "sometimes"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # A SCHEMA rejection (pydantic) also lands here, and it is a list of field
    # errors rather than our sentence. Either way the body must be structured
    # and carry a code the client can read.
    code = r.json().get("code") if isinstance(detail, list) else detail["code"]
    assert code in ("invalid_request", "invalid_port_setting"), r.text


def test_the_real_settings_put_422s_the_stores_own_refusal_with_its_code(
        monkeypatch):
    """The branch above that is OURS: a value that clears the schema and is
    refused by ``power_guard.set_port_settings``."""
    import astrodeck.api.app as app_module

    client, _sw = _real_client(monkeypatch)

    def _boom(*a, **kw):
        raise ValueError("protect_during_run must be true, false or null")

    monkeypatch.setattr(app_module.power_guard, "set_port_settings", _boom)
    r = client.put("/api/switch/ports/2", json={"follow_dew": True})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_port_setting"
    assert detail["port_id"] == 2
    assert "must be true, false or null" in detail["detail"]
