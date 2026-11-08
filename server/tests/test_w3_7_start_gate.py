# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.7 (#1076): every HTTP start path refuses a night Windows may restart
the PC under, and the operator can acknowledge it.

``Hub.arm_refusal`` decides (see ``test_w3_7_arm_gate.py``). This file holds
that the four start routes ask it, and that the answer reaches the operator as
a 409 ``restart_pending`` carrying the refusal's own sentence:

* BEHAVIOUR, on the five doors ``test_start_paths_refuse_camera_owned.py``
  uses (run fresh, run CONTINUE, resume, recover, sequence start): with a
  restart pending and the active hours at their default, each is refused and
  ``engine.start`` is never called; with ``acknowledge_restart`` each starts;
  with nothing pending each starts as it always did.
* STRUCTURE: the four route functions call ``_refuse_start_while_restart_pending``
  once, straight after ``_refuse_start_while_rig_is_held`` and before anything
  that pre-flights the plan.

The registry is a fake (``bootcause._registry_read``); nothing here reads the
real one.

NAMED MUTANT: the routes' call to the gate is dropped (the helper's body made a
no-op). Every refusal test below turns red.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

import astrodeck.api.app as app_module
from astrodeck import bootcause
from _simhub import _TEST_SITE
from test_flows_continue import rig  # noqa: F401 (fixture)
from test_start_paths_refuse_camera_owned import (HELPER, PREFLIGHT, ROUTES,
                                                  START_ROUTES, _bytes,
                                                  _calls, _dormant,
                                                  _route_functions)

_WU = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate"
       r"\Auto Update\RebootRequired")
GATE = "_refuse_start_while_restart_pending"


@pytest.fixture
def pending(monkeypatch):
    """Windows holds a restart, and tonight runs outside the active hours. A
    function, called AFTER ``_dormant``: the setup starts a night through the
    real route, and must not meet the gate it is about to grade.

    "Outside the active hours" is stated, not computed: the gate compares only
    what is left of tonight, from the real now to dawn, with the hours in the
    PC's own zone, so near dawn in the zone the suite runs in (UTC on CI) the
    remainder falls inside Windows' default 08:00 to 17:00 and the gate rightly
    refuses nothing. The hours arithmetic is test_w3_7_restart_state's, against
    an explicit zone."""
    def stage():
        staged = {(_WU, None): True}
        monkeypatch.setattr(bootcause, "_registry_read",
                            lambda key, value=None: staged.get((key, value)))
        monkeypatch.setattr(bootcause, "night_outside_active_hours",
                            lambda *a, **k: True)
    return stage


@pytest.fixture
def nothing_pending(monkeypatch):
    monkeypatch.setattr(bootcause, "_registry_read", lambda key, value=None: None)


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_a_pending_restart_refuses_every_start(rig, pending, name, request_):
    fid, sid = await _dormant(rig)
    pending()
    starts_before = len(rig.starts)
    session_before = _bytes(sid)

    r = await request_(rig, fid, sid)

    assert r.status_code == 409, f"{name} answered {r.status_code}: {r.text}"
    detail = r.json()["detail"]
    assert detail["code"] == "restart_pending", detail
    assert detail["detail"] == app_module.hub.arm_refusal(), detail
    assert "restart pending" in detail["detail"] and "ACKNOWLEDGE" in detail["detail"]
    assert len(rig.starts) == starts_before, f"{name} reached engine.start anyway"
    assert _bytes(sid) == session_before, f"{name}'s refusal wrote the session"


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_an_acknowledged_pending_restart_starts(rig, pending, name, request_):
    fid, sid = await _dormant(rig)
    pending()
    starts_before = len(rig.starts)

    r = await request_(rig, fid, sid, acknowledge_restart=True)

    assert r.status_code == 200, f"{name} answered {r.status_code}: {r.text}"
    assert len(rig.starts) == starts_before + 1, f"{name} did not start the engine"


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_control_nothing_pending_starts_as_it_did(
        rig, nothing_pending, name, request_):
    fid, sid = await _dormant(rig)
    starts_before = len(rig.starts)

    r = await request_(rig, fid, sid)

    assert r.status_code == 200, f"{name} answered {r.status_code}: {r.text}"
    assert len(rig.starts) == starts_before + 1


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_the_acknowledgement_does_not_waive_the_camera_guard(
        rig, pending, monkeypatch, name, request_):
    """A recording owns the camera: acknowledging a pending restart must not
    start a run under it. The two guards are separate on purpose."""
    from types import SimpleNamespace
    fid, sid = await _dormant(rig)
    pending()
    monkeypatch.setattr(app_module.video_recorder, "_current",
                        SimpleNamespace(state="recording"))
    starts_before = len(rig.starts)

    r = await request_(rig, fid, sid, acknowledge_restart=True)

    assert r.status_code == 409 and r.json()["detail"]["code"] == "video_owns_camera"
    assert len(rig.starts) == starts_before


def test_every_start_route_calls_the_gate_right_after_the_held_rig_guard():
    routes = _route_functions()
    assert set(routes) == set(START_ROUTES)
    for name, fn in routes.items():
        calls = _calls(fn)
        gate = [c for c in calls if c[2] == GATE]
        assert len(gate) == 1, f"{name} calls {GATE} {len(gate)} times"
        held = next(c for c in calls if c[2] == HELPER)
        assert (held[0], held[1]) < gate[0][:2], f"{name} asks {GATE} before {HELPER}"
        assert len(gate[0][3].args) == 1 and not gate[0][3].keywords, name
        first_preflight = next(c for c in calls if c[2] in PREFLIGHT)
        assert gate[0][:2] < first_preflight[:2], (
            f"{name} calls {first_preflight[2]} before {GATE}")


# ------------------------------------------------- the stored acknowledgement
# The banner says "acknowledge the pending restart", and the quick flow and
# auto-resume cannot carry a request flag. So the acknowledgement is STORED, in
# the hub's memory, tied to the restart it acknowledged: POST
# /api/rig/restart/acknowledge once, and every start route starts with no
# field in its body.
#
# NAMED MUTANT: ``arm_refusal`` ignores the stored acknowledgement (the
# ``stored`` term is dropped from its first test). Every route-then-start test
# below turns red.

ACK = "/api/rig/restart/acknowledge"


@pytest.fixture(autouse=True)
def _reset_acknowledgement(monkeypatch):
    """The hub is a process singleton: an acknowledgement stored by one test
    must not start the next test's night."""
    monkeypatch.setattr(app_module.hub, "_restart_acknowledged", False, raising=False)


class _Fixed:
    name = "fixed"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


async def test_a_viewer_cannot_acknowledge(rig, pending, monkeypatch):
    from astrodeck.auth import (principal_for_role, reset_active_provider,
                                set_active_provider)
    pending()
    set_active_provider(_Fixed(principal_for_role("viewer")))
    try:
        r = await rig.client.post(ACK)
    finally:
        reset_active_provider()
    assert r.status_code == 403, r.text
    assert app_module.hub.arm_refusal() is not None, "a viewer's refusal stored an ack"


async def test_acknowledging_with_nothing_pending_is_a_409(rig, nothing_pending):
    r = await rig.client.post(ACK)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "no_restart_pending"
    assert app_module.hub._restart_acknowledged is False


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_once_acknowledged_every_start_route_starts_with_no_body_field(
        rig, pending, name, request_):
    fid, sid = await _dormant(rig)
    pending()
    refused = await request_(rig, fid, sid)
    assert refused.status_code == 409, "premise: refused before the acknowledgement"
    starts_before = len(rig.starts)

    ack = await rig.client.post(ACK)
    assert ack.status_code == 200 and ack.json() == {"acknowledged": True}, ack.text
    r = await request_(rig, fid, sid)

    assert r.status_code == 200, f"{name} answered {r.status_code}: {r.text}"
    assert len(rig.starts) == starts_before + 1


async def test_once_acknowledged_the_quick_flow_starts(rig, pending):
    """The route that cannot pass the flag at all."""
    await _dormant(rig)
    app_module.config_store.set_site(_TEST_SITE)     # the quick flow's dusk/dawn target
    pending()
    quick = {"target": {"name": "NGC 6946", "ra": "20h 34m 52s", "dec": "+60 09 14"},
             "subs": 2, "filters": [], "run": True}
    refused = await rig.client.post("/api/flows/quick", json=quick)
    assert refused.status_code == 409, refused.text
    assert "restart pending" in refused.json()["detail"]["detail"], refused.text
    starts_before = len(rig.starts)

    assert (await rig.client.post(ACK)).status_code == 200
    r = await rig.client.post("/api/flows/quick", json=quick)

    assert r.status_code == 200, r.text
    assert len(rig.starts) == starts_before + 1


def test_the_acknowledgement_clears_when_the_pending_flag_clears(monkeypatch):
    hub = app_module.hub
    staged = {(_WU, None): True}
    monkeypatch.setattr(bootcause, "_registry_read",
                        lambda key, value=None: staged.get((key, value)))
    assert hub.acknowledge_restart() is True
    assert hub.arm_refusal() is None

    staged.clear()                                   # the restart happened
    assert hub.arm_refusal() is None
    assert hub._restart_acknowledged is False, "the acknowledgement outlived its restart"

    staged[(_WU, None)] = True                       # the NEXT update stages one
    assert hub.arm_refusal() is not None, "an old acknowledgement covered a new restart"


async def test_the_status_block_says_whether_it_is_acknowledged(rig, pending):
    pending()
    before = (await app_module.hub.poll_status())["restart"]
    assert before["pending"] is True and before["acknowledged"] is False
    assert before["arm_refusal"] is not None
    await rig.client.post(ACK)
    after = (await app_module.hub.poll_status())["restart"]
    assert after["acknowledged"] is True and after["arm_refusal"] is None
