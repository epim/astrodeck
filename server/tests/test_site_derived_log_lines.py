# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A line whose TIME is the site reaches only a holder of the site-derived
view, and so does the panel a mosaic waits on at the meridian (#166, spec
6.9; #189, spec 5.10; mosaic slice S2 task T8).

WHAT LEAKS WITHOUT THIS. ``/api/logs`` is CAP_VIEW_STATUS, so a viewer reads
it. A line logged when a mosaic panel is acquired at its computed meridian
crossing timestamps the transit of a known RA. That is the local sidereal
time, and the LST at a known instant is the longitude: the arithmetic that
made ``hours_to_flip`` a longitude (``redact.py``), done by the reader with a
clock instead of a field. The words can be clean ("1-3 waits for the
meridian") and the line still carries the site in its ``ts``. The same holds
for ``state.group``: a viewer who watches ``panel`` change at the crossing has
read the same transit.

THE RULE (spec 6.9). A line whose moment a site computation set is logged
``bus.log(..., site_derived=True)`` and carries ``data.site_derived=True``.
For a principal without CAP_VIEW_SITE_DERIVED it is ABSENT from the ring
(``GET /api/logs``), the night read (``?night=``), both exports (txt and
jsonl) and both WS lanes (``_redact_ws_event``). The night log FILE keeps it.
Nor does anything else such a principal reads move at the flagged moment:
the ring's eviction, a storm summary, a night-log notice or tonight's file
size (section 5). An unflagged line is byte-identical to what it was before
the flag existed.
While ``group.meridian_wait`` is true, ``group.panel`` and ``group.pass`` are
absent (not null) for the same principal on ``GET /api/sequence/state``, the
monitor snapshot's ``sequence`` and the WS ``sequence`` event (spec 5.10).

THE ROLES. A viewer and a syncer hold view.status and not view.site_derived,
so they are the principals the rule answers for. An operator and an admin
hold it, as the owner's role-visibility ruling of 2026-09-22 allows, and get
everything. ``test_the_roles_are_the_ones_this_file_grades`` fails the day
that stops being true, because every other case here would then grade the
wrong principal.

THE #19 CHECK (the last section). The same night is played under two
synthetic sites (``_site_tracking.SITE_A``/``SITE_B``, the #19 scanner's,
never the real one), with the wall clock pinned: the flagged line is published
at each site's own computed crossing and names the altitude there, and the
waiting group's panel and pass are a function of the site. Everything a
VIEWER reads from these paths must be identical under the two sites. The
known positive is in the same case: an OPERATOR's reads of the same paths do
move with the site, so the scan can see what it is looking for.

The engine starts setting the flag in T18; nothing here depends on it.

MUTATIONS. Each was applied to a private scratch copy of ``server/`` (never
the shared tree) and the named test run there, ``-p no:randomly -n0
--tb=short``. The observed failures are quoted in the test each one grades.
"""
from __future__ import annotations

import asyncio
import functools
import json
from collections import deque
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
from astrodeck import events
from astrodeck.api.redact import (_redact_log_rows_for, _redact_sequence_for,
                                  _redact_ws_event)
from astrodeck.auth import (CAP_VIEW_SITE_DERIVED, CAP_VIEW_STATUS,
                            principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.catalog.coords import altaz, hour_angle_h, lst_hours
from astrodeck.config import ConfigStore
from astrodeck.events import STORM_PASS, EventBus, NightLogWriter, night_key
from astrodeck.remote.protocol import FrameType
from _site_tracking import SITE_A, SITE_B, any_tracking, tracking_tokens
from test_no_route_leaks_the_site_coordinates import _client as _scan_client
from test_remote_relay import (FakeChannel, _make_relay_client,
                               _wait_for_frame, _ws_data_payloads)

#: 2026-09-25T04:30:00Z. The wall clock every event in this file is published
#: at unless a case says otherwise, so ``ts`` is not a moving input.
T = 1_790_310_600.0
NIGHT = night_key(T)

NON_HOLDERS = ("viewer", "syncer")
HOLDERS = ("operator", "admin")

#: A line in words, whose moment nothing about the site set.
WORDS = "M31 mosaic: 1-3 waits for the meridian"
#: A line whose moment the crossing set (spec 6.9's "a meridian wait ending").
TIMED = "M31 mosaic: 1-3 meridian wait over"
#: The last line of a burst, so a WS reader knows it has seen the burst.
MARKER = "M31 mosaic: end of burst"

#: Every path ``/api/logs`` serves a line through.
READS = {
    "ring": "/api/logs",
    "night": f"/api/logs?night={NIGHT}",
    "export-txt": f"/api/logs/export?night={NIGHT}&format=txt",
    "export-jsonl": f"/api/logs/export?night={NIGHT}&format=jsonl",
}


# ---------------------------------------------------------------- harness

class _FixedPrincipal:
    """Every request resolves to one identity (the #19 scanner's pattern).
    ``name`` is not "none", so the relay lane's remote hard-deny treats it as
    a real provider."""

    name = "fake"

    def __init__(self, principal) -> None:
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _as(role: str) -> None:
    set_active_provider(_FixedPrincipal(principal_for_role(role)))


@pytest.fixture(autouse=True)
def _restore_provider():
    yield
    reset_active_provider()


class _Wall:
    """The wall clock ``events._wall`` reads: ``ts`` on every event."""

    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _fresh_bus(monkeypatch, t: float) -> _Wall:
    """The process bus with an empty ring, a fresh night-log writer (under
    whatever ``hub.CAPTURE_DIR`` is now), a closed storm window and the wall
    clock pinned at ``t``. The app's routes read this bus."""
    bus = events.bus
    bus.flush()
    monkeypatch.setattr(bus, "_history", deque(maxlen=200))
    monkeypatch.setattr(bus, "_history_unflagged", deque(maxlen=200))
    monkeypatch.setattr(bus, "night_log", NightLogWriter())
    wall = _Wall(t)
    monkeypatch.setattr(events, "_wall", wall)
    return wall


@pytest.fixture()
def served(tmp_path, monkeypatch):
    """An isolated app (test_remote_relay's ``_make_client`` shape) over the
    process bus, reset."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    for mod in (config_mod, hub_mod, app_module):
        monkeypatch.setattr(mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    app = app_module.create_app()
    wall = _fresh_bus(monkeypatch, T)
    return SimpleNamespace(app=app, client=TestClient(app), wall=wall)


@pytest.fixture()
def private_bus(tmp_path, monkeypatch):
    """A private bus whose night log writes under ``tmp_path``, wall clock
    pinned at ``T``."""
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(events, "_wall", _Wall(T))
    return EventBus()


@pytest.fixture()
def storm_clock(monkeypatch):
    """The storm limiter's monotonic clock, held still: every line lands
    inside one window."""
    monkeypatch.setattr(events, "_now", lambda: 1000.0)


def _log_pair() -> None:
    events.bus.log("info", WORDS, "sequence")
    events.bus.log("info", TIMED, "sequence", site_derived=True)


def _messages(rows) -> list[str]:
    return [(r.get("data") or {}).get("message") for r in rows]


def test_the_roles_are_the_ones_this_file_grades():
    """Premise. If a viewer or syncer came to hold view.site_derived, every
    "absent for a non-holder" case below would be asking a principal entitled
    to the line, and if an operator lost it every holder case would be asking
    one that is not."""
    for role in NON_HOLDERS + HOLDERS:
        assert principal_for_role(role).has(CAP_VIEW_STATUS), (
            f"{role} cannot read /api/logs at all, so it grades nothing here")
    for role in NON_HOLDERS:
        assert not principal_for_role(role).has(CAP_VIEW_SITE_DERIVED), role
    for role in HOLDERS:
        assert principal_for_role(role).has(CAP_VIEW_SITE_DERIVED), role


# ------------------------------------------------------- (1) the flag itself

def test_an_unflagged_line_is_byte_identical_to_what_it_was(private_bus):
    """``bus.log(level, message, source)``, and the same with
    ``site_derived=False`` spelled out, publish exactly the three keys they
    always did, and the night file line is byte for byte the line written
    before the flag existed. Every existing reader of a log payload is a
    reader of this shape.

    RED under mutant "flag key always written" (``EventBus.log`` publishes
    ``site_derived=site_derived`` whatever its value). Observed:

        E   At index 0 diff: {'level': 'info', 'message': '2-1 first: sets
            soonest', 'source': 'sequence', 'site_derived': False} !=
            {'level': 'info', 'message': '2-1 first: sets soonest',
            'source': 'sequence'}
    """
    q = private_bus.subscribe()
    private_bus.log("info", "2-1 first: sets soonest", "sequence")
    private_bus.log("warning", "1-3 waits for the meridian", "sequence",
                    site_derived=False)
    got = [q.get_nowait().data, q.get_nowait().data]
    assert got == [
        {"level": "info", "message": "2-1 first: sets soonest",
         "source": "sequence"},
        {"level": "warning", "message": "1-3 waits for the meridian",
         "source": "sequence"},
    ]
    assert [r["data"] for r in private_bus.log_history] == got
    text = NightLogWriter.path_for(NIGHT).read_text(encoding="utf-8")
    assert text == (
        '{"type":"log","data":{"level":"info","message":"2-1 first: sets '
        'soonest","source":"sequence"},"ts":1790310600.0}\n'
        '{"type":"log","data":{"level":"warning","message":"1-3 waits for '
        'the meridian","source":"sequence"},"ts":1790310600.0}\n')


def test_a_flagged_line_carries_the_flag_and_the_night_file_keeps_it(
        private_bus):
    """``site_derived=True`` puts ``data.site_derived = True`` on the event a
    subscriber gets, the ring row and the night file row; the file keeps the
    line; the night reader serves it by default and leaves it out when asked
    to (``include_site_derived=False``), in both ``read`` and
    ``export_text``.

    RED under mutant "flag dropped at the source" (``EventBus.log`` ignores
    ``site_derived``). Observed:

        E   AssertionError: assert {'level': 'in...': 'sequence'} ==
            {'level': 'in...': 'sequence'}
        E     Right contains 1 more item:
        E     {'site_derived': True}
    """
    q = private_bus.subscribe()
    private_bus.log("info", WORDS, "sequence")
    private_bus.log("info", TIMED, "sequence", site_derived=True)
    q.get_nowait()
    assert q.get_nowait().data == {"level": "info", "message": TIMED,
                                   "source": "sequence", "site_derived": True}
    assert private_bus.log_history[-1]["data"]["site_derived"] is True
    text = NightLogWriter.path_for(NIGHT).read_text(encoding="utf-8")
    assert TIMED in text and '"site_derived":true' in text, (
        "the night log file keeps everything a line says (spec 6.9)")
    store = private_bus.night_log
    assert _messages(store.read(NIGHT)) == [WORDS, TIMED]
    assert _messages(store.read(NIGHT, include_site_derived=False)) == [WORDS]
    assert TIMED in store.export_text(NIGHT)
    held_back = store.export_text(NIGHT, include_site_derived=False)
    assert WORDS in held_back and TIMED not in held_back


def test_a_storm_of_a_flagged_line_is_summarised_flagged(private_bus,
                                                         storm_clock):
    """The storm limiter's summary line is published when the window closes,
    so its moment is the flagged line's moment. It must carry the flag, or a
    viewer reads "(repeated N more times)" at the crossing.

    RED under mutant "storm summary unflagged" (``flush`` builds the summary
    from level, message and source only). Observed:

        E   assert [True, True, True, None] == [True, True, True, True]
        E     At index 3 diff: None != True
    """
    for _ in range(STORM_PASS + 2):
        private_bus.log("info", TIMED, "sequence", site_derived=True)
    private_bus.flush()
    rows = private_bus.log_history
    assert "repeated 2 more times" in rows[-1]["data"]["message"], rows
    assert [r["data"].get("site_derived") for r in rows] == \
        [True] * (STORM_PASS + 1)


def test_a_flagged_line_is_never_counted_into_an_unflagged_storm(
        private_bus, storm_clock):
    """Same words, one flagged: two different lines to the limiter. Counted
    as one, the flagged line would vanish into the unflagged run's summary,
    and that summary, unflagged and published after the flagged line
    arrived, would tell a viewer that something was said then.

    RED under mutant "storm key ignores the flag" (the key is level, message
    and source only). The flagged line is gone and an unflagged summary
    stands in its place. Observed:

        E     At index 3 diff: ('M31 mosaic: holding (repeated 1 more times
            in 1.0 s)', None) != ('M31 mosaic: holding', True)
    """
    same = "M31 mosaic: holding"
    for _ in range(STORM_PASS):
        private_bus.log("info", same, "sequence")
    private_bus.log("info", same, "sequence", site_derived=True)
    private_bus.flush()
    rows = private_bus.log_history
    assert [(r["data"]["message"], r["data"].get("site_derived"))
            for r in rows] == [(same, None)] * STORM_PASS + [(same, True)]


# -------------------------------------------------- (2) the /api/logs paths

@pytest.mark.parametrize("role", NON_HOLDERS)
@pytest.mark.parametrize("read", list(READS))
def test_a_non_holder_does_not_get_a_flagged_line(served, role, read):
    """Ring, night read, txt export, jsonl export: the flagged line is
    absent for a viewer and a syncer, and the line in words is there (the
    control: the path works, and the filter is not "drop everything").

    RED under mutant "ring filtered only" (the ``?night=`` read passes
    ``include_site_derived=True`` whatever the principal): 2 failed,
    ``[night-viewer]`` and ``[night-syncer]``. Observed:

        E   AssertionError: night: a viewer can read a line whose time is
            the site: [{"type":"log","data":{"level":"info","message":"M31
            mosaic: 1-3 waits for the meridian",...
        E     'M31 mosaic: 1-3 meridian wait over' is contained here:
        E       message":"M31 mosaic: 1-3 meridian wait over","source":
            "sequence","site_derived":true},"ts":1790310600.0}]

    RED under mutant "export not filtered" (``/api/logs/export`` reads and
    formats every row whatever the principal): 4 failed, both formats for
    both roles. Observed:

        E   AssertionError: export-txt: a viewer can read a line whose time
            is the site: 2026-09-24 21:30:00 [info · sequence] M31 mosaic:
            1-3 waits for the meridian
        E     2026-09-24 21:30:00 [info · sequence] M31 mosaic: 1-3
            meridian wait over
        E   AssertionError: export-jsonl: a viewer can read a line whose
            time is the site: ...
        E     {"type":"log","data":{"level":"info","message":"M31 mosaic:
            1-3 meridian wait over","source":"sequence","site_derived":
            true},"ts":1790310600.0}

    (The printed local time is the test machine's zone.)
    """
    _log_pair()
    _as(role)
    r = served.client.get(READS[read])
    assert r.status_code == 200, r.text
    assert WORDS in r.text, f"{read}: the control line is missing: {r.text}"
    assert TIMED not in r.text, (
        f"{read}: a {role} can read a line whose time is the site: {r.text}")


@pytest.mark.parametrize("role", HOLDERS)
@pytest.mark.parametrize("read", list(READS))
def test_a_holder_gets_the_flagged_line_on_every_path(served, role, read):
    """An operator and an admin read the flagged line on all four paths, and
    the JSON paths carry the flag, so a client can tell it apart.

    RED under mutant "holders filtered too" (``_redact_log_rows_for`` and
    the night reader's flag ignore the principal): 8 failed, every path for
    both roles. Observed, first of them:

        E   AssertionError: ring: [{"type":"log","data":{"level":"info",
            "message":"M31 mosaic: 1-3 waits for the meridian","source":
            "sequence"},"ts":1790310600.0}]
    """
    _log_pair()
    _as(role)
    r = served.client.get(READS[read])
    assert r.status_code == 200, r.text
    assert WORDS in r.text and TIMED in r.text, f"{read}: {r.text}"
    if read in ("ring", "night"):
        flagged = [row for row in r.json()
                   if row["data"]["message"] == TIMED]
        assert flagged and flagged[0]["data"]["site_derived"] is True


@pytest.mark.parametrize("read", ["ring", "night"])
def test_limit_counts_only_what_the_reader_may_see(served, read):
    """``?limit=1`` when the newest line is flagged: the viewer gets the
    newest line it may read, not an empty list. Filtered after the limit, a
    viewer polling ``limit=1`` would see the answer go empty at the moment
    a flagged line lands, which is the moment the flag withholds.

    RED under mutant "limit before filter" (the ring route slices
    ``rows[-limit:]`` before it drops the flagged rows), ``[ring]``; and
    under its twin in ``NightLogWriter.read`` (the in-loop filter moved to
    after the slice), ``[night]``. Observed, the same for both:

        E   AssertionError: assert [] == ['M31 mosaic:...the meridian']
        E     Right contains one more item: 'M31 mosaic: 1-3 waits for the
            meridian'
    """
    _log_pair()
    sep = "&" if "?" in READS[read] else "?"
    _as("viewer")
    rows = served.client.get(f"{READS[read]}{sep}limit=1").json()
    assert _messages(rows) == [WORDS]
    _as("operator")
    rows = served.client.get(f"{READS[read]}{sep}limit=1").json()
    assert _messages(rows) == [TIMED]


# --------------------------------------------------- (2) the two WS lanes

def _ws_collect(ws, marker: str = MARKER) -> list[dict]:
    """Every ``log`` and ``sequence`` frame up to the marker line.
    Unrelated frames may interleave and are skipped."""
    got = []
    while True:
        ev = ws.receive_json()
        if ev.get("type") in ("log", "sequence"):
            got.append(ev)
        if (ev.get("type") == "log"
                and (ev.get("data") or {}).get("message") == marker):
            return got


def _group(*, waiting: bool) -> dict:
    """The 5.10 shape plus ``meridian_wait``."""
    return {"id": "g-m31", "name": "M31 mosaic", "mode": "panel",
            "pass": 2, "panel": "2-3", "visit_elapsed_s": 312,
            "panels_done": 4, "panels_total": 9,
            "set_aside": [{"panel": "1-1", "reason": "its window closed"}],
            "meridian_wait": waiting}


def _state(group=None) -> dict:
    s = {"state": "running", "plan_name": "M31 mosaic",
         "progress": {"frames_done": 12, "frames_total": 90}}
    if group is not None:
        s["group"] = group
    return s


def _withheld(group: dict) -> dict:
    return {k: v for k, v in group.items() if k not in ("panel", "pass")}


def test_the_lan_ws_drops_a_flagged_line_for_a_viewer(served):
    """The on-LAN ``/ws`` lane, a viewer and an operator connected to the
    same burst: the viewer never receives the flagged line and the operator
    does, flag and all. The same burst's ``sequence`` event, published while
    the group waits on the meridian, reaches the viewer without ``panel``
    and ``pass`` and the operator with them.

    RED under mutant "WS not filtered" (``_redact_ws_event`` no longer drops
    a flagged event). Observed:

        E   assert ['M31 mosaic:...end of burst'] == ['M31 mosaic:...end
            of burst']
        E     At index 1 diff: 'M31 mosaic: 1-3 meridian wait over' !=
            'M31 mosaic: end of burst'
        E     Left contains one more item: 'M31 mosaic: end of burst'
    """
    c = served.client
    _as("viewer")
    with c.websocket_connect("/ws") as ws_v:
        assert ws_v.receive_json()["type"] == "hello"
        _as("operator")
        with c.websocket_connect("/ws") as ws_o:
            assert ws_o.receive_json()["type"] == "hello"
            _log_pair()
            events.bus.publish("sequence", **_state(_group(waiting=True)))
            events.bus.log("info", MARKER, "sequence")
            viewer = _ws_collect(ws_v)
            operator = _ws_collect(ws_o)
    v_logs = _messages([e for e in viewer if e["type"] == "log"])
    o_logs = [e["data"] for e in operator if e["type"] == "log"]
    assert v_logs == [WORDS, MARKER], viewer
    assert {"level": "info", "message": TIMED, "source": "sequence",
            "site_derived": True} in o_logs, operator
    v_seq = [e["data"]["group"] for e in viewer if e["type"] == "sequence"]
    o_seq = [e["data"]["group"] for e in operator if e["type"] == "sequence"]
    assert v_seq == [_withheld(_group(waiting=True))]
    assert o_seq == [_group(waiting=True)]


async def _relay_log_lines(app, role: str, marker: str) -> list[dict]:
    """One relay-tunneled ``/ws`` (test_remote_relay's harness), opened as
    ``role``, across one burst: the ``log`` payloads it sent."""
    _as(role)
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    channel.push_frame(FrameType.WS_OPEN, 7,
                       {"path": "/ws", "query": "", "ws_id": "wsA"})
    task = asyncio.create_task(client._serve_once(client._config()))
    try:
        await _wait_for_frame(channel,
                              lambda f: f.type == FrameType.WS_DATA)  # hello
        _log_pair()
        events.bus.log("info", marker, "sequence")
        await _wait_for_frame(channel, lambda f: (
            f.type == FrameType.WS_DATA and marker.encode() in f.payload))
    finally:
        channel.finish()
        await asyncio.wait_for(task, timeout=5.0)
    return [p["data"] for p in await _ws_data_payloads(channel)
            if p.get("type") == "log"]


def test_the_relay_lane_drops_a_flagged_line_for_a_viewer(served):
    """The relay-tunneled ``/ws`` lane: the same rule as the LAN lane,
    because both send what ``_redact_ws_event`` returns and skip a None.

    RED under mutant "WS not filtered". Observed:

        E   assert ['M31 mosaic:...arker viewer'] == ['M31 mosaic:...arker
            viewer']
        E     At index 1 diff: 'M31 mosaic: 1-3 meridian wait over' !=
            'relay marker viewer'
        E     Left contains one more item: 'relay marker viewer'
    """
    viewer = asyncio.run(_relay_log_lines(served.app, "viewer",
                                          "relay marker viewer"))
    operator = asyncio.run(_relay_log_lines(served.app, "operator",
                                            "relay marker operator"))
    assert _messages([{"data": d} for d in viewer]) == [
        WORDS, "relay marker viewer"], viewer
    assert {"level": "info", "message": TIMED, "source": "sequence",
            "site_derived": True} in operator, operator


@pytest.mark.parametrize("etype", ["log", "sequence", "status"])
def test_the_ws_seam_drops_any_flagged_event_for_a_non_holder(etype):
    """The flag says the event's MOMENT is the site, whatever the event is,
    and a stripped event still arrives at that moment. So a flagged event of
    any type is dropped whole for a viewer and a syncer and passes verbatim
    to an operator and an admin; the same event unflagged is not dropped.

    RED under mutant "drop only log events" (the drop is keyed on
    ``type == "log"``): ``[sequence]`` and ``[status]`` failed, ``[log]``
    passed. Observed:

        E   AssertionError: viewer
        E   assert {'data': {'site_derived': True, 'state': 'running'},
            'ts': 1790310600.0, 'type': 'sequence'} is None

    RED under mutant "WS not filtered", all three types, the same shape.
    """
    flagged = {"type": etype, "ts": T,
               "data": {"state": "running", "site_derived": True}}
    plain = {"type": etype, "ts": T, "data": {"state": "running"}}
    for role in NON_HOLDERS:
        p = principal_for_role(role)
        assert _redact_ws_event(flagged, p) is None, role
        assert _redact_ws_event(plain, p) == plain, role
    for role in HOLDERS:
        assert _redact_ws_event(flagged, principal_for_role(role)) == \
            flagged, role


# ------------------------------------------------ (3) state.group on the wire

def _seq_read(served, monkeypatch, seam: str, role: str, state: dict):
    """The sequence state ``role`` is served through one seam."""
    if seam == "ws":
        out = _redact_ws_event({"type": "sequence", "data": state, "ts": T},
                               principal_for_role(role))
        return out["data"]
    monkeypatch.setattr(app_module.engine, "state", state)
    _as(role)
    if seam == "rest":
        r = served.client.get("/api/sequence/state")
        assert r.status_code == 200, r.text
        return r.json()
    r = served.client.get("/api/monitor/snapshot")
    assert r.status_code == 200, r.text
    return r.json()["sequence"]


SEAMS = ("rest", "monitor", "ws")


@pytest.mark.parametrize("role", NON_HOLDERS)
@pytest.mark.parametrize("seam", SEAMS)
def test_a_non_holder_reads_no_panel_or_pass_while_the_group_waits(
        served, monkeypatch, seam, role):
    """While ``group.meridian_wait`` is true, ``panel`` and ``pass`` are
    ABSENT, not null, for a viewer and a syncer on the GET route, the
    monitor snapshot and the WS ``sequence`` event. Every other group field
    is served unchanged: the sheet still shows the group, its progress and
    its set-aside panels.

    RED under mutant "withhold on WS only" (``sequence_state`` returns
    ``_sequence_envelope(engine)`` without ``_redact_sequence_for``):
    ``[rest-viewer]`` and ``[rest-syncer]`` failed, the monitor and WS
    cases passed. Observed:

        E   AssertionError: {'id': 'g-m31', 'meridian_wait': True, 'mode':
            'panel', 'name': 'M31 mosaic', ...}
        E   assert ('panel' not in {'id': 'g-m31', 'meridian_wait': True,
            'mode': 'panel', 'name': 'M31 mosaic', ...})

    RED under mutant "nulled, not absent" (the withheld keys are set to
    None): all six cases, the same assertion as above, because a key set to
    None is still ``in`` the group.

    RED under mutant "monitor snapshot not withheld" (the snapshot's
    ``sequence`` is ``_sequence_envelope(engine)`` alone): ``[monitor-
    viewer]`` and ``[monitor-syncer]``, the same assertion.
    """
    got = _seq_read(served, monkeypatch, seam, role,
                    _state(_group(waiting=True)))["group"]
    assert "panel" not in got and "pass" not in got, got
    assert got == _withheld(_group(waiting=True))


@pytest.mark.parametrize("role", NON_HOLDERS + HOLDERS)
@pytest.mark.parametrize("seam", SEAMS)
def test_panel_and_pass_are_present_when_the_group_is_not_waiting(
        served, monkeypatch, seam, role):
    """The control: with ``meridian_wait`` false every role reads the whole
    group, so the viewer's sheet names the panel it is watching.

    RED under mutant "withheld whatever meridian_wait says" (the withholding
    drops the two keys for every group): the six non-holder cases failed,
    the six holder cases passed. Observed:

        E   AssertionError: assert {'id': 'g-m31... mosaic', ...} ==
            {'id': 'g-m31... mosaic', ...}
        E     Right contains 2 more items:
        E     {'panel': '2-3', 'pass': 2}
    """
    got = _seq_read(served, monkeypatch, seam, role,
                    _state(_group(waiting=False)))["group"]
    assert got == _group(waiting=False)


@pytest.mark.parametrize("role", HOLDERS)
@pytest.mark.parametrize("seam", SEAMS)
def test_a_holder_always_reads_panel_and_pass(served, monkeypatch, seam,
                                              role):
    """An operator and an admin read ``panel`` and ``pass`` through the
    wait.

    RED under mutant "holders withheld too" (``_redact_sequence_for`` and
    the WS seam withhold whatever the principal holds): 5 of 6 failed.
    ``[ws-admin]`` passed, because an admin holds all three caps and the WS
    seam returns its event before any stripping. Observed:

        E   AssertionError: assert {'id': 'g-m31... mosaic', ...} ==
            {'id': 'g-m31... mosaic', ...}
        E     Right contains 2 more items:
        E     {'panel': '2-3', 'pass': 2}
    """
    got = _seq_read(served, monkeypatch, seam, role,
                    _state(_group(waiting=True)))["group"]
    assert got == _group(waiting=True)


def test_withholding_never_writes_through_to_the_shared_state(served,
                                                              monkeypatch):
    """``engine.state`` is the engine's own dict and a WS event's ``data``
    is shared by every subscriber. A viewer's read must copy before it
    withholds, or the next operator (and the engine) lose the panel too.

    RED under mutant "withheld in place" (the two keys are popped from the
    group dict itself). Observed:

        E   assert '{"group": {"...": "running"}' == '{"group":
            {"...": "running"}'
        E     - c", "panel": "2-3", "panels_done": 4, "panels_total": 9,
            "pass": 2, "set_aside": ...
        E     + c", "panels_done": 4, "panels_total": 9, "set_aside": ...
    """
    state = _state(_group(waiting=True))
    before = json.dumps(state, sort_keys=True)
    ev = {"type": "sequence", "data": state, "ts": T}
    _redact_ws_event(ev, principal_for_role("viewer"))
    _seq_read(served, monkeypatch, "rest", "viewer", state)
    _seq_read(served, monkeypatch, "monitor", "viewer", state)
    assert json.dumps(state, sort_keys=True) == before
    assert _redact_ws_event(ev, principal_for_role("operator"))["data"][
        "group"] == _group(waiting=True)
    assert _seq_read(served, monkeypatch, "rest", "operator", state)[
        "group"] == _group(waiting=True)


def test_a_state_with_no_group_is_byte_identical_for_every_role(
        served, monkeypatch):
    """The control the acceptance names: a state with no ``group`` key is
    served byte for byte the same to every role, on the GET route, and the
    WS seam hands every role the event unchanged.

    RED under mutant "group rebuilt unconditionally" (the withholding writes
    ``group`` back for every state, ``None`` when there was none): two
    different bodies across the four roles. Observed:

        E   AssertionError: {'admin': b'{"state":"running","plan_name":"M31
            mosaic","progress":{"frames_done":12,"frames_total":90},
            "running":fals...
        E   assert 2 == 1
    """
    state = _state()
    monkeypatch.setattr(app_module.engine, "state", state)
    bodies = {}
    for role in NON_HOLDERS + HOLDERS:
        _as(role)
        bodies[role] = served.client.get("/api/sequence/state").content
    assert len(set(bodies.values())) == 1, bodies
    assert b'"group"' not in bodies["viewer"]
    ev = {"type": "sequence", "data": state, "ts": T}
    for role in NON_HOLDERS + HOLDERS:
        out = _redact_ws_event(ev, principal_for_role(role))
        assert json.dumps(out) == json.dumps(ev), role
    assert _redact_sequence_for(state, principal_for_role("viewer")) == state


@pytest.mark.parametrize("seam", SEAMS)
def test_a_group_of_an_unexpected_shape_fails_closed(served, monkeypatch,
                                                     seam):
    """A ``group`` that is not a dict cannot be key-stripped, so a viewer
    gets no ``group`` at all (this module's rule for a shape it does not
    recognise); an operator still gets it as it is.

    RED under mutant "unexpected shape passes through" (a non-dict group is
    returned untouched): all three seams. Observed:

        E   AssertionError: assert 'group' not in {'group': ['2-3', 2],
            'paused': False, 'plan_name': 'M31 mosaic', 'progress':
            {'frames_done': 12, 'frames_total': 90}, ...}
    """
    state = _state(["2-3", 2])
    assert "group" not in _seq_read(served, monkeypatch, seam, "viewer",
                                    state)
    assert _seq_read(served, monkeypatch, seam, "operator",
                     state)["group"] == ["2-3", 2]


# --------------------------------- (4) the #19 check over two fixture sites

#: M31, a known RA and Dec. Its meridian crossing at a site is the moment the
#: flagged line is published, which is what makes that line's ``ts`` a
#: function of the longitude.
RA_M31, DEC_M31 = 0.7123, 41.269
#: Where the search for the night's fixed clock starts.
T_SEARCH = 1_790_280_000.0
#: Sidereal hours per solar hour.
SIDEREAL = 1.00273790935


def _crossing(lon: float, t: float) -> float:
    """The first meridian crossing of M31 after ``t`` at longitude ``lon``."""
    ha = hour_angle_h(RA_M31, lon, t)
    return t + ((-ha) % 24.0) * 3600.0 / SIDEREAL


@functools.lru_cache(maxsize=None)
def _t_fix() -> float:
    """A wall-clock reading whose night also holds M31's next crossing at
    BOTH sites, the two crossings minutes apart. Searched, so the premise is
    computed rather than assumed: the operator's night read must contain the
    flagged line under both sites for the known positive to mean anything."""
    for i in range(2 * 288):
        t = T_SEARCH + i * 300.0
        ca, cb = _crossing(SITE_A[1], t), _crossing(SITE_B[1], t)
        if (night_key(t) == night_key(ca) == night_key(cb)
                and abs(ca - cb) > 600.0):
            return t
    raise AssertionError("no clock reading in two days puts M31's crossing "
                         "at both sites in the same night")


def _waiting_group(lon: float, t: float) -> dict:
    """The group waiting on the meridian, whose panel and pass stand in for
    the engine's choice. Which panel the meridian rule holds is decided by
    hour angles, so by the longitude; here the choice is read off the local
    sidereal time directly, which is the same dependence made obvious."""
    lst = lst_hours(lon, t)
    return {**_group(waiting=True),
            "panel": f"{1 + int(lst) % 3}-{1 + int(lst * 10) % 3}",
            "pass": 1 + int(lst) % 5}


def _night_at(where, root, monkeypatch) -> dict[str, dict[str, str]]:
    """One night at synthetic site ``where``, and what each role read of it.

    The app is the #19 scanner's (``_client``: a private store saved at the
    site, swept into every module), over the process bus reset with the wall
    clock at ``_t_fix()``. The burst: a line in words, the waiting group's
    ``sequence`` event and a second line in words at the fixed clock; the
    flagged line at M31's crossing at this site, naming M31's altitude
    there; the marker at the fixed clock. A viewer and an operator are on
    the LAN ``/ws`` throughout, and a bus subscriber stands in for the
    relay lane, whose sends are ``_redact_ws_event`` of exactly these
    events."""
    root.mkdir()
    lat, lon = where
    t_fix = _t_fix()
    client = _scan_client(root, monkeypatch, lat, lon, role="viewer")
    wall = _fresh_bus(monkeypatch, t_fix)
    crossing = _crossing(lon, t_fix)
    alt = altaz(RA_M31, DEC_M31, lat, lon, crossing)[0]
    state = _state(_waiting_group(lon, t_fix))
    monkeypatch.setattr(app_module.engine, "state", state)
    seam = events.bus.subscribe()
    ws_frames = {}
    try:
        _as("viewer")
        with client.websocket_connect("/ws") as ws_v:
            assert ws_v.receive_json()["type"] == "hello"
            _as("operator")
            with client.websocket_connect("/ws") as ws_o:
                assert ws_o.receive_json()["type"] == "hello"
                events.bus.log("info", "M31 mosaic: 2-1 first: sets soonest",
                               "sequence")
                events.bus.publish("sequence", **state)
                events.bus.log("info", WORDS, "sequence")
                wall.t = crossing
                events.bus.log("info", f"M31 mosaic: 1-3 crossed the meridian "
                               f"at {alt:.1f} deg", "sequence",
                               site_derived=True)
                wall.t = t_fix
                events.bus.log("info", MARKER, "sequence")
                ws_frames["viewer"] = _ws_collect(ws_v)
                ws_frames["operator"] = _ws_collect(ws_o)
    finally:
        events.bus.unsubscribe(seam)
    published = []
    while not seam.empty():
        ev = seam.get_nowait()
        if ev.type in ("log", "sequence"):
            published.append(ev.to_json())
    night = night_key(t_fix)
    paths = {
        "GET /api/logs": "/api/logs",
        "GET /api/logs?night=": f"/api/logs?night={night}",
        "GET /api/logs/export txt": f"/api/logs/export?night={night}&format=txt",
        "GET /api/logs/export jsonl":
            f"/api/logs/export?night={night}&format=jsonl",
        "GET /api/sequence/state": "/api/sequence/state",
    }
    out = {}
    for role in ("viewer", "operator"):
        _as(role)
        p = principal_for_role(role)
        reads = {name: client.get(path).text for name, path in paths.items()}
        reads["GET /api/monitor/snapshot (sequence)"] = json.dumps(
            client.get("/api/monitor/snapshot").json()["sequence"],
            sort_keys=True)
        reads["/ws (LAN)"] = json.dumps(ws_frames[role], sort_keys=True)
        reads["_redact_ws_event (the relay lane's seam)"] = json.dumps(
            [e for e in (_redact_ws_event(ev, p) for ev in published)
             if e is not None], sort_keys=True)
        out[role] = reads
    out["premise"] = {"crossing": crossing, "alt": f"{alt:.1f}",
                      "group": state["group"]}
    return out


def test_nothing_a_viewer_reads_on_these_paths_moves_with_the_site(
        tmp_path, monkeypatch):
    """The #19 check. The same night under SITE_A and SITE_B, the wall clock
    pinned, the flagged line at each site's own crossing and naming M31's
    altitude there, the waiting group's panel and pass chosen from each
    site's sidereal time. What a VIEWER reads from every path this task
    touches must be byte-identical between the two sites, and no numeric
    token may track the site (``_site_tracking``).

    The known positive is here too, because a scan that finds nothing may be
    a scan that cannot see: an OPERATOR's reads of every one of those paths
    DO carry numbers that move with the site (the crossing's timestamp, the
    altitude, the panel and pass).

    Under each named mutant the scan names the path and the numbers that
    moved: the crossing's timestamp and M31's altitude there (90.0 at
    SITE_A, whose latitude is M31's declination to 0.04 deg, and 61.1 at
    SITE_B) for a leaked line; the panel and pass for a leaked group.

    RED under mutant "ring filtered only". Observed:

        E   AssertionError: a VIEWER can read numbers that move with the site:
        E         GET /api/logs?night=: under SITE_A ['1790313749.456974',
            '90.0'], under SITE_B ['1790306973.9070716', '61.1']

    RED under mutant "export not filtered". Observed:

        E   AssertionError: a VIEWER can read numbers that move with the site:
        E         GET /api/logs/export txt: under SITE_A ['22', '22',
            '90.0'], under SITE_B ['20', '33', '61.1']
        E         GET /api/logs/export jsonl: under SITE_A
            ['1790313749.456974', '90.0'], under SITE_B
            ['1790306973.9070716', '61.1']

    RED under mutant "WS not filtered". Observed:

        E   AssertionError: a VIEWER can read numbers that move with the site:
        E         /ws (LAN): under SITE_A ['1790313749.456974', '90.0'],
            under SITE_B ['1790306973.9070716', '61.1']
        E         _redact_ws_event (the relay lane's seam): under SITE_A
            ['1790313749.456974', '90.0'], under SITE_B
            ['1790306973.9070716', '61.1']

    RED under mutant "withhold on WS only". Observed:

        E   AssertionError: a VIEWER can read numbers that move with the site:
        E         GET /api/sequence/state: under SITE_A ['1', '1'], under
            SITE_B ['3', '3']
    """
    a = _night_at(SITE_A, tmp_path / "a", monkeypatch)
    b = _night_at(SITE_B, tmp_path / "b", monkeypatch)
    # Premises: the site really moved everything this scan says it hides.
    assert abs(a["premise"]["crossing"] - b["premise"]["crossing"]) > 600.0
    for pair, got in ((SITE_A, a), (SITE_B, b)):
        assert abs(hour_angle_h(RA_M31, pair[1],
                                got["premise"]["crossing"])) < 1e-3
    assert a["premise"]["alt"] != b["premise"]["alt"]
    assert ((a["premise"]["group"]["panel"], a["premise"]["group"]["pass"])
            != (b["premise"]["group"]["panel"],
                b["premise"]["group"]["pass"]))
    moved = {}
    for name in a["viewer"]:
        found = tracking_tokens([a["viewer"][name]], [b["viewer"][name]])
        if any_tracking(found):
            moved[name] = found
    assert not moved, "a VIEWER can read numbers that move with the site:\n" \
        + "\n".join(f"    {n}: under SITE_A {f['a']}, under SITE_B {f['b']}"
                    for n, f in moved.items())
    differ = [n for n in a["viewer"] if a["viewer"][n] != b["viewer"][n]]
    assert not differ, f"a VIEWER's reads differ between the sites: {differ}"
    blind = [n for n in a["operator"] if not any_tracking(
        tracking_tokens([a["operator"][n]], [b["operator"][n]]))]
    assert not blind, (
        "the known positive failed: an OPERATOR's reads of these paths carry "
        f"nothing that moves with the site, so the scan cannot see: {blind}")


# ------------------------------- (5) channels that carry no withheld row
# Each case below withheld every flagged row and still told a viewer WHEN one
# arrived, through something other than the row: the ring's eviction, a storm
# summary, a night-log notice, the night file's size. Found by the S2
# verifier and posted on #166; this is the fix's grading. A rule for rows
# alone cannot see any of them, because no row a viewer is served changes.


def test_a_flagged_line_does_not_move_a_viewers_full_ring(served):
    """The ring holds 200 rows and fills within an hour of any night. A
    viewer polling ``GET /api/logs`` over a full ring must read the SAME 200
    rows before and after a flagged line lands. Filtering the shared ring is
    not enough: the flagged line takes a slot there and evicts the oldest
    row, so the viewer's first row vanished with no new last row, at the
    flagged line's moment. The operator's read is the known positive: the
    full ring did evict, and the flagged line is its last row.

    RED under mutant "one ring for every reader" (the route serves
    ``_redact_log_rows_for(bus.log_history, principal)`` whoever asks, the
    code before this fix). Observed:

        E   AssertionError: a flagged line moved a viewer's ring: 200 rows
            became 199, first row ['routine line 0'] became ['routine
            line 1']
    """
    for i in range(200):
        events.bus.log("info", f"routine line {i}", "sequence")
    _as("viewer")
    before = served.client.get("/api/logs").json()
    assert len(before) == 200, "premise: the ring is full"
    events.bus.log("info", TIMED, "sequence", site_derived=True)
    after = served.client.get("/api/logs").json()
    assert after == before, (
        f"a flagged line moved a viewer's ring: {len(before)} rows became "
        f"{len(after)}, first row {_messages(before[:1])} became "
        f"{_messages(after[:1])}")
    _as("operator")
    rows = served.client.get("/api/logs").json()
    assert _messages([rows[0], rows[-1]]) == ["routine line 1", TIMED], (
        "the known positive: a full ring evicts for a holder")


def test_a_flagged_line_that_closes_an_unflagged_storm_flags_its_summary(
        private_bus, storm_clock):
    """The storm window has no timer: its summary is published by whatever
    line closes it, at that line's moment. When a flagged line closes an
    unflagged storm, the summary is published at the flagged moment, so it
    carries the flag and a viewer's seam drops it with the line. The
    control: an unflagged line closing a storm leaves the summary in the
    three keys it always had.

    RED under mutant "the summary keeps only the storm's own flag" (``flush``
    flags the summary only when the storm itself was flagged). Observed:

        E   AssertionError: {'data': {'level': 'info', 'message': 'M31
            mosaic: holding (repeated 3 more times in 1.0 s)', 'source':
            'sequence'}, 'ts': 1790310600.0, 'type': 'log'}
        E   assert None is True

    RED under mutant "every summary flagged" (``flush`` flags every
    summary), the control. Observed:

        E   AssertionError: {'level': 'info', 'message': 'M31 mosaic:
            settling (repeated 2 more times in 1.0 s)', 'site_derived':
            True, 'source': 'sequence'}
        E     Left contains 1 more item:
        E     {'site_derived': True}
    """
    same = "M31 mosaic: holding"
    for _ in range(STORM_PASS + 3):
        private_bus.log("info", same, "sequence")
    private_bus.log("info", TIMED, "sequence", site_derived=True)
    rows = private_bus.log_history
    summary, line = rows[-2], rows[-1]
    assert summary["data"]["message"].startswith(
        same + " (repeated 3 more times"), rows
    assert line["data"]["message"] == TIMED
    assert summary["data"].get("site_derived") is True, summary
    viewer = principal_for_role("viewer")
    assert [_redact_ws_event(r, viewer) for r in (summary, line)] == \
        [None, None]
    # The control: an unflagged storm closed by an unflagged line.
    other = "M31 mosaic: settling"
    for _ in range(STORM_PASS + 2):
        private_bus.log("info", other, "sequence")
    private_bus.log("info", WORDS, "sequence")
    control = private_bus.log_history[-2]["data"]
    assert control == {"level": "info", "source": "sequence",
                       "message": f"{other} (repeated 2 more times in "
                                  f"{events.STORM_WINDOW_S:.1f} s)"}, control


def test_a_night_log_notice_is_not_carried_out_by_a_flagged_line(
        private_bus, monkeypatch):
    """The night-log writer's owed notice (paused, resumed) is published
    just before the next event, at that event's moment. Carried out by a
    flagged line, it would be an unflagged line in front of a viewer at the
    flagged moment. It waits for the next event that is not flagged.

    RED under mutant "the notice drains on every publish" (the flag check in
    ``publish`` removed). Observed:

        E   AssertionError: ['night log file writer paused: OSError: disk
            full; retrying in 60 s', 'M31 mosaic: 1-3 meridian wait over']
    """
    owed = [("warning", "night log file writer paused: OSError: disk full; "
                        "retrying in 60 s")]
    monkeypatch.setattr(private_bus.night_log, "take_notice",
                        lambda: owed.pop() if owed else None)
    q = private_bus.subscribe()
    private_bus.log("info", TIMED, "sequence", site_derived=True)
    first = [q.get_nowait().data["message"] for _ in range(q.qsize())]
    assert first == [TIMED], first
    private_bus.log("info", WORDS, "sequence")
    then = [q.get_nowait().data["message"] for _ in range(q.qsize())]
    assert then == ["night log file writer paused: OSError: disk full; "
                    "retrying in 60 s", WORDS], then


def test_a_flagged_line_does_not_grow_tonights_size_for_a_viewer(
        served, monkeypatch):
    """``GET /api/logs/nights`` is view.status and reports each night file's
    size. The file keeps flagged lines, so tonight's size grew at the
    flagged line's moment while every row stayed withheld. For a viewer,
    tonight's ``bytes`` is now the size of the lines it may read: it does
    not move on a flagged line, and does move on a line the viewer may read
    (the control). The operator's size is the file's (the known positive).

    The route's "tonight" is pinned to the night this file's clock writes
    to, because the route asks the real clock.

    RED under mutant "tonight's size is the file's for every reader" (the
    route passes ``unflagged_bytes_for=None`` whoever asks). Observed:

        E   AssertionError: a flagged line grew tonight's size for a viewer:
            129 -> 274

    RED under mutant "the count keeps flagged lines" (``_unflagged_bytes``
    counts every line). Observed: the same line.
    """
    monkeypatch.setattr(app_module, "night_key", lambda ts=None: NIGHT)

    def size(role: str) -> int:
        _as(role)
        body = served.client.get("/api/logs/nights").json()
        assert body["current"] == NIGHT, body
        return {n["night"]: n["bytes"] for n in body["nights"]}[NIGHT]

    events.bus.log("info", WORDS, "sequence")
    v0, o0 = size("viewer"), size("operator")
    assert v0 == o0 > 0, (v0, o0)
    events.bus.log("info", TIMED, "sequence", site_derived=True)
    v1, o1 = size("viewer"), size("operator")
    assert v1 == v0, (
        f"a flagged line grew tonight's size for a viewer: {v0} -> {v1}")
    assert o1 > o0, "the known positive: the file grew"
    events.bus.log("info", MARKER, "sequence")
    v2 = size("viewer")
    assert v2 > v1, "the control: a line the viewer may read grows the size"
    # Exactly the file's bytes less the flagged line's, newline and all (the
    # file is written in text mode, so a newline is two bytes on Windows).
    lines = NightLogWriter.path_for(NIGHT).read_bytes().splitlines(
        keepends=True)
    assert [TIMED.encode() in ln for ln in lines] == [False, True, False]
    assert v2 == sum(len(ln) for ln in lines if TIMED.encode() not in ln)
    assert o1 + len(lines[2]) == sum(len(ln) for ln in lines)


# ------------------------------------------- (6) fail closed, as promised
# ``is_site_derived``, ``_withhold_group_timing``, ``_redact_ws_event`` and
# ``_redact_sequence_for`` each promise, in their docstrings, to fail closed
# on a value or shape nobody planned for. Four mutants that broke those
# promises left every case above green; each has its case here.


@pytest.mark.parametrize("flag", [1, "yes", ["x"]],
                         ids=["one", "text", "list"])
def test_a_flag_of_any_truthy_spelling_is_a_flag(flag):
    """A flag some future writer spells ``1`` must fail closed: the line is
    flagged, so a viewer's WS seam drops it and the ring filter leaves it
    out. The control: the falsy spellings flag nothing.

    RED under mutant "only True is a flag" (``is_site_derived`` asks
    ``data.get(SITE_DERIVED_KEY) is True``), all three. Observed:

        E   AssertionError: assert False
        E    +  where False = <function is_site_derived at 0x...>({'data':
            {'level': 'info', 'message': 'M31 mosaic: 1-3 meridian wait
            over', 'site_derived': 1, 'source': 'sequence'}, 'ts':
            1790310600.0, 'type': 'log'})
    """
    viewer = principal_for_role("viewer")
    ev = {"type": "log", "ts": T,
          "data": {"level": "info", "message": TIMED, "source": "sequence",
                   "site_derived": flag}}
    assert events.is_site_derived(ev)
    assert _redact_ws_event(ev, viewer) is None
    assert _redact_log_rows_for([ev], viewer) == []
    for falsy in (0, False, "", None):
        plain = {**ev, "data": {**ev["data"], "site_derived": falsy}}
        assert not events.is_site_derived(plain), falsy


@pytest.mark.parametrize("waiting", [1, "yes"], ids=["one", "text"])
def test_a_truthy_meridian_wait_withholds_panel_and_pass(waiting):
    """``meridian_wait`` spelled ``1`` or ``"yes"`` is a wait: a viewer
    reads no ``panel`` and no ``pass``, on the GET route's helper and on the
    WS seam.

    RED under mutant "only True is a wait" (``_withhold_group_timing``
    returns the state unless ``meridian_wait is True``), both spellings.
    Observed:

        E   AssertionError: {'id': 'g-m31', 'meridian_wait': 1, 'mode':
            'panel', 'name': 'M31 mosaic', ...}
        E   assert ('panel' not in {'id': 'g-m31', 'meridian_wait': 1,
            'mode': 'panel', 'name': 'M31 mosaic', ...})
    """
    group = {**_group(waiting=True), "meridian_wait": waiting}
    viewer = principal_for_role("viewer")
    rest = _redact_sequence_for(_state(group), viewer)["group"]
    ws = _redact_ws_event({"type": "sequence", "data": _state(group),
                           "ts": T}, viewer)["data"]["group"]
    for got in (rest, ws):
        assert "panel" not in got and "pass" not in got, got


class _GroupThatRaises(dict):
    """A group every read of which raises, standing in for any error the
    withholding can hit on a shape nobody planned for."""

    def get(self, *args, **kwargs):
        raise RuntimeError("a group this module cannot read")


def test_a_group_that_cannot_be_read_is_dropped_on_every_seam():
    """Both except branches: a group whose read raises is dropped whole for
    a viewer on the GET route's helper and on the WS seam, rather than
    served as it is or raised as a 500. An operator's read never looks at
    it, and gets the state itself.

    RED under mutant "the WS except keeps the group" (the ``safe.pop(
    "group")`` of ``_redact_ws_event``'s except branch removed). Observed:

        E   AssertionError: ['group', 'plan_name', 'progress', 'state']
        E   assert ({'data': {'group': {'id': 'g-m31', ...} is not None
            and 'group' not in {'group': {'id': 'g-m31', ...

    RED under mutant "the REST except serves the state whole"
    (``_redact_sequence_for``'s except returns ``payload``). Observed:

        E   AssertionError: ['group', 'plan_name', 'progress', 'state']
        E   assert 'group' not in {'group': {'id': 'g-m31', ...
    """
    state = _state(_GroupThatRaises(_group(waiting=True)))
    viewer = principal_for_role("viewer")
    rest = _redact_sequence_for(state, viewer)
    assert "group" not in rest, sorted(rest)
    assert rest["plan_name"] == "M31 mosaic"
    ws = _redact_ws_event({"type": "sequence", "data": state, "ts": T},
                          viewer)
    assert ws is not None and "group" not in ws["data"], sorted(ws["data"])
    assert _redact_sequence_for(state, principal_for_role("operator")) is \
        state
