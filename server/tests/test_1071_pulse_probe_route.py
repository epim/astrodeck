# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071: ``POST /api/mount/pulse-probe``, the operator's pulse probe.

Does a guide pulse leave the RA drive tracking? The route runs ten stages
through the AM5 driver's REAL ``pulse_guide`` (rest, one west pulse, one east
pulse, ten west and ten east at the guide cadence, the same in north and
south, rest), each followed by a
probe of the mount's own reported RA rate, with no camera and no star. It is
refused (409, a plain sentence) while a sequence runs or is armed to start
within ten minutes, while the guider guides, while the mount is parked or
slewing, and when the driver cannot probe; ``POST /api/mount/stop`` ends it.

The mount is the real ``ZwoAm5Telescope`` over the ``test_zwo_am5`` FakeLink
(RA model from ``test_1071_probe_ra_drift``); the app is the real
``create_app()`` under a TestClient. The protocol is shrunk by patching the
module constants (3 pulses in a train, 60 ms pulses, 0.15 s cadence, 0.3 s
probes) and nothing else.

Each test names the mutant it was shown RED under, from a byte backup of the
production file restored afterwards.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.devices.backends.zwo_am5 as am5
import astrodeck.hub as hub_module
import astrodeck.pulse_probe as pp
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.events import night_key
from astrodeck.sequence import resume_arm
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.session import session_store

from test_1071_probe_ra_drift import RaLink
from test_api_mount import FakeAuthProvider

PULSE_CMDS = ("R2", "Mw", "Qw", "Td", "Te", "R1", "Me", "Qe")


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(pp, "PULSE_MS", 60)
    monkeypatch.setattr(pp, "TRAIN_PULSES", 3)
    monkeypatch.setattr(pp, "CADENCE_S", 0.15)
    monkeypatch.setattr(pp, "PROBE_SECONDS", 0.3)
    monkeypatch.setattr(am5, "PROBE_MIN_S", 0.1)
    monkeypatch.setattr(session_store, "armed", lambda: None)
    link = RaLink(10 * 3600 + 5, 0.0)
    tel = am5.ZwoAm5Telescope(link)
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "guider", None, raising=False)
    app = app_module.create_app()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, tel=tel, link=link,
                              tmp=tmp_path, monkeypatch=monkeypatch)
    for t in list(app_module.hub._busy.values()):
        t.cancel()


def _wait(pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _result_file(tmp):
    folder = tmp / "guide" / night_key()
    return sorted(folder.glob("*-pulse-probe*.json")) if folder.exists() else []


def _idle(rig):
    t = app_module.hub._busy.get("pulse_probe")
    return t is None or t.done()


def _no_motion(rig):
    sent = [c for c in rig.link.sent if c in PULSE_CMDS or c == "Q"]
    assert sent == [], f"the mount was commanded: {sent}"


# ----------------------------------------------------------- the refusals


def test_it_runs_the_six_stages_in_order_through_pulse_guide(rig, bus_lines):
    """The wire shows the protocol: probe, one west pulse, probe, one east
    (tracking suspend), probe, three west, probe, three east, probe, one
    north, probe, one south, probe, three north, probe, three south, probe,
    probe (round 2 added the Dec stages). The result and the file carry the
    same ten steps, the pier side, and the sidereal fractions; the night log
    says each stage and the end.

    MUTANT 1 (the stage list ``plan`` reordered: east before west): RED, the
    wire order.
    MUTANT 2 (``_pulses`` sends ``n - 1`` pulses): RED, the counts.
    MUTANT 3 (``write_result`` not called): RED, no file."""
    r = rig.client.post("/api/mount/pulse-probe")
    assert r.status_code == 200, r.text
    assert r.json()["started"] == "pulse_probe"
    # The quoted time is computed from the constants (the unshrunk protocol:
    # 241 s), not written down.
    assert r.json()["note"].startswith(f"{pp.expected_minutes_words()};")
    assert _wait(lambda: _result_file(rig.tmp) and _idle(rig))

    # Collapse the wire into pulses (Mw = west, Td = east, Mn, Ms) and the
    # probe, which ends with its pier-side read.
    names = {"Gm": "probe", "Mw": "W", "Td": "E", "Mn": "N", "Ms": "S"}
    tokens = [names[c] for c in rig.link.sent if c in names]
    assert tokens == ["probe", "W", "probe", "E", "probe", "W", "W", "W",
                      "probe", "E", "E", "E", "probe", "N", "probe", "S",
                      "probe", "N", "N", "N", "probe", "S", "S", "S", "probe",
                      "probe"], tokens

    data = json.loads(_result_file(rig.tmp)[0].read_text(encoding="utf-8"))
    assert [s["name"] for s in data["steps"]] == [
        "rest", "west x1", "east x1", "west x3", "east x3", "north x1",
        "south x1", "north x3", "south x3", "rest again"]
    assert [s["pulses"] for s in data["steps"]] == [0, 1, 1, 3, 3, 1, 1, 3, 3,
                                                   0]
    assert [s["pulse_total_ms"] for s in data["steps"]] == [
        0, 60, 60, 180, 180, 60, 60, 180, 180, 0]
    assert data["completed"] is True and data["aborted"] is None
    assert data["pier_side"] == "east" and data["tracking_at_start"] is True
    for s in data["steps"]:
        assert s["probe"]["sidereal_fraction"] == 0.0
        assert "ra_before" not in s["probe"]

    msgs = [m for _lv, m, _s in bus_lines]
    assert any(m.startswith("pulse probe started: 10 stages") for m in msgs)
    assert len([m for m in msgs if m.startswith(
        "tracking probe (pulse probe: ")]) == 10
    assert any(m.startswith("pulse probe finished (pier side east)")
               and "wrote guide/" in m for m in msgs)


def test_pulses_in_a_train_are_one_cadence_apart(rig):
    """Start to start, not end to start: three pulses of 60 ms at 0.15 s take
    about 0.3 s from the first start to the third, not 0.18.

    MUTANT 4 (the cadence wait taken after each pulse as ``sleep(cadence)``):
    RED, the span is 0.39 s or more."""
    rig.client.post("/api/mount/pulse-probe")
    assert _wait(lambda: _result_file(rig.tmp) and _idle(rig))
    starts = [t for c, t in rig.link.sent_at if c == "Mw"]
    # starts[0] is the single pulse; the train is starts[1:4].
    train = starts[1:4]
    assert len(train) == 3
    assert 0.27 <= train[2] - train[0] <= 0.37, train


@pytest.mark.parametrize("why", ["running", "recovering", "armed", "guiding",
                                 "parked", "slewing", "cannot_probe", "busy"])
def test_it_refuses_in_each_unsafe_state_and_moves_nothing(rig, why,
                                                           monkeypatch):
    """A 409 with a plain sentence, and not one byte of motion on the wire.

    MUTANT 5..11 (each check in ``pulse_probe.refusal`` removed in turn, one
    mutant per case id): RED for that case, the route answers 200 and the
    mount is commanded."""
    sentences = {
        "running": "a sequence is running",
        "recovering": "a run is being recovered",
        "armed": "armed and may start within ten minutes",
        "guiding": "the guider is guiding",
        "parked": "the mount is parked",
        "slewing": "the mount is slewing",
        "cannot_probe": "cannot measure its own RA rate",
        "busy": "already running",
    }
    if why == "running":
        monkeypatch.setattr(SequenceEngine, "running",
                            property(lambda self: True))
    elif why == "recovering":
        monkeypatch.setattr(type(app_module.resume_arm), "recovering",
                            property(lambda self: True))
    elif why == "armed":
        monkeypatch.setattr(session_store, "armed",
                            lambda: SimpleNamespace(name="n"))
        monkeypatch.setattr(resume_arm, "window_open",
                            lambda s, site, tw, now: True)
        monkeypatch.setattr("astrodeck.site_gate.site_is_set", lambda s: True)
    elif why == "guiding":
        async def active():
            return True
        monkeypatch.setattr(app_module.hub, "guider", SimpleNamespace(
            connected=True, is_active=active))
    elif why == "parked":
        rig.link.script["Gps"] = "2"
    elif why == "slewing":
        rig.tel._slewing = True
    elif why == "cannot_probe":
        monkeypatch.setattr(rig.tel, "can_probe_ra_drift", False)
    elif why == "busy":
        assert rig.client.post("/api/mount/pulse-probe").status_code == 200
        rig.link.sent.clear()
    r = rig.client.post("/api/mount/pulse-probe")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert sentences[why] in detail["detail"], detail
    assert detail["code"] == "pulse_probe_refused"
    if why != "busy":
        _no_motion(rig)


def test_a_run_armed_inside_ten_minutes_refuses_and_one_beyond_does_not(
        rig, monkeypatch):
    """The window is ten minutes: a session whose window opens in 9 minutes
    refuses, one that opens in 12 does not.

    MUTANT 12 (``ARMED_WITHIN_S`` 600 -> 0): RED, the 9 minute case passes."""
    monkeypatch.setattr(session_store, "armed",
                        lambda: SimpleNamespace(name="n"))
    monkeypatch.setattr("astrodeck.site_gate.site_is_set", lambda s: True)
    base = time.time()
    monkeypatch.setattr(resume_arm, "window_open",
                        lambda s, site, tw, now: now >= base + 9 * 60)
    assert rig.client.post("/api/mount/pulse-probe").status_code == 409
    monkeypatch.setattr(resume_arm, "window_open",
                        lambda s, site, tw, now: now >= base + 12 * 60)
    r = rig.client.post("/api/mount/pulse-probe")
    assert r.status_code == 200, r.text
    assert _wait(lambda: _result_file(rig.tmp) and _idle(rig))


def test_a_viewer_is_403_and_an_operator_may_run_it(rig):
    """Gated by the mount-control capability, like every route that moves the
    mount (the RBAC boot assertion also needs the declared ``reaches``).

    MUTANT 13 (the ``Depends(require(CAP_CONTROL_MOUNT))`` removed): RED, the
    viewer starts a probe."""
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))
    assert rig.client.post("/api/mount/pulse-probe").status_code == 403
    _no_motion(rig)
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    assert rig.client.post("/api/mount/pulse-probe").status_code == 200
    assert _wait(lambda: _result_file(rig.tmp) and _idle(rig))


def test_route_declares_its_capability_and_reach():
    from astrodeck.auth import CAP_CONTROL_MOUNT
    from astrodeck.auth.rbac import CAP_ATTR, REACHES_ATTR
    app = app_module.create_app()
    route = next(r for r in app.routes
                 if getattr(r, "path", None) == "/api/mount/pulse-probe")
    assert "POST" in route.methods
    assert getattr(route.endpoint, CAP_ATTR) == {CAP_CONTROL_MOUNT}
    assert getattr(route.endpoint, REACHES_ATTR) == {
        "Telescope.pulse_guide", "Telescope.probe_ra_drift"}


# ------------------------------------------------------------------ stop


def test_a_stop_request_aborts_it_at_once_and_leaves_the_mount_stopped(
        rig, bus_lines):
    """``POST /api/mount/stop`` during the protocol: the job ends, no further
    pulse starts, every pulse that started was stopped (Mw/Qw, Td/Te), the
    partial result is written with ``aborted`` saying why.

    The protocol is stretched (probes of 1.5 s) so the stop lands inside the
    first probe.

    MUTANT 14 ("pulse_probe" removed from the lanes ``mount_stop`` cancels):
    RED, the job runs on to the end and the file says completed.
    MUTANT 15 (the ``except asyncio.CancelledError`` arm removed from
    ``run_protocol``): RED, no partial file."""
    rig.monkeypatch.setattr(pp, "PROBE_SECONDS", 1.5)
    assert rig.client.post("/api/mount/pulse-probe").status_code == 200
    assert _wait(lambda: "GR" in rig.link.sent)
    r = rig.client.post("/api/mount/stop")
    assert r.status_code == 200
    assert _wait(lambda: _idle(rig), 5.0), "the job did not end at once"
    pulses_before = [c for c in rig.link.sent if c in ("Mw", "Td")]
    time.sleep(1.2)
    assert [c for c in rig.link.sent if c in ("Mw", "Td")] == pulses_before
    assert _wait(lambda: _result_file(rig.tmp), 2.0)
    data = json.loads(_result_file(rig.tmp)[0].read_text(encoding="utf-8"))
    assert data["completed"] is False
    assert data["aborted"] == "stopped by request"
    assert len(data["steps"]) < 10
    assert rig.link.sent.count("Mw") == rig.link.sent.count("Qw")
    assert rig.link.sent.count("Td") == rig.link.sent.count("Te")
    assert "Q" in rig.link.sent
    assert any(m.startswith("pulse probe stopped") for _lv, m, _s in bus_lines)


def test_a_stop_in_the_middle_of_a_pulse_still_stops_the_mount(rig):
    """The stop lands while a pulse is in flight (long pulses, stop as soon
    as the move command is on the wire): the pulse's own cancel path sends
    its stop, so the axis is not left moving.

    MUTANT 16 (``pulse.abort.set()`` removed from ``pulse_guide``'s cancel
    arm): RED, the stop waits out the pulse. (Existing pulse-stop tests grade
    that arm; this grades that the probe route reaches it.)"""
    rig.monkeypatch.setattr(pp, "PULSE_MS", 900)
    rig.monkeypatch.setattr(pp, "PROBE_SECONDS", 0.2)
    assert rig.client.post("/api/mount/pulse-probe").status_code == 200
    assert _wait(lambda: "Mw" in rig.link.sent)
    t0 = time.monotonic()
    assert rig.client.post("/api/mount/stop").status_code == 200
    assert _wait(lambda: _idle(rig), 5.0)
    assert rig.link.sent.count("Qw") == 1
    stop_t = [t for c, t in rig.link.sent_at if c == "Qw"][0]
    assert stop_t - t0 < 0.6, f"the pulse ran on {stop_t - t0:.2f} s"


def test_a_park_during_the_probe_ends_it_without_more_pulses(rig):
    """If the mount is parked under the job (the park route does not cancel
    this lane), the next stage notices and the job ends, writing what it has.

    MUTANT 17 (``parked_too`` ignored in ``_ensure_ok``): RED, the job keeps
    pulsing a parked mount."""
    rig.monkeypatch.setattr(pp, "PROBE_SECONDS", 0.6)
    assert rig.client.post("/api/mount/pulse-probe").status_code == 200
    assert _wait(lambda: "GR" in rig.link.sent)
    rig.link.script["Gps"] = "2"
    assert _wait(lambda: _idle(rig) and _result_file(rig.tmp), 10.0)
    data = json.loads(_result_file(rig.tmp)[0].read_text(encoding="utf-8"))
    assert data["completed"] is False
    assert "parked" in data["aborted"]
    assert [c for c in rig.link.sent if c in ("Mw", "Td")] == []


def test_tracking_off_at_the_start_is_said_and_it_runs_anyway(rig, bus_lines):
    """That is data too.

    MUTANT 18 (the tracking-off warning removed, or the run refused): RED."""
    rig.link.script["GAT"] = "0"
    assert rig.client.post("/api/mount/pulse-probe").status_code == 200
    assert _wait(lambda: _result_file(rig.tmp) and _idle(rig))
    data = json.loads(_result_file(rig.tmp)[0].read_text(encoding="utf-8"))
    assert data["tracking_at_start"] is False and data["completed"] is True
    assert any("tracking is off at the start; running anyway" in m
               for _lv, m, _s in bus_lines)
