# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A stored flow run end to end on the clocked simulator (#189 S7 item 1,
spec section 8 S7, 5.1 to 5.10, 6).

THE APP IS THE REAL ONE. `create_app` over ``httpx.ASGITransport`` on the
test's own event loop, with no lifespan (so no background service ticks),
isolated the way tests/test_flows_progress_route.py isolates it: a
throwaway config store swept into every astrodeck module that holds one
(``conftest._sweep_config_store``, before and after the app is built -- a
shared helper since backlog WP-62, #497, 2026-09-30; this file kept a
private copy before that), a throwaway flow library under both names the
code reads (``app.flow_store`` for the routes, ``flows.store.flow_store``
for the engine's finalize), and a throwaway captures directory, which holds
the sessions, the reports and the night log.

THE APP'S HUB AND ENGINE ARE A NIGHT'S. ``app.hub`` is a clocked night's
simulator hub (`_group_harness.night_hub`) and ``app.engine`` that night's
engine (`_group_harness.Night`), so ``POST /api/flows`` saves a real stored
flow and ``POST /api/flows/{id}/run`` compiles it and starts the clocked
engine through every guard the route runs: the rig guard, the structural
validation, the compile's losses (the TARGET is framed for the simulator's
own field, so there are none), the identity and quota checks, the horizon
and Sun pre-flight, the resume-ladder refusal, and CONTINUE's write-locked
section. Everything the night harness scripts it scripts here too (the
goto's answer, the sky angle each centring solve measured, the frames' star
counts), and nothing else.

ONE STUB, THE ONE THE PROGRESS TESTS MAKE: ``Hub._check_solar`` answers
"clear" (`night_hub` sets it on the class, which covers the app's pre-flight
and the engine's slews alike). The fixture instants are night-time, so a
real check would answer the same; the stub keeps the Sun ephemeris out of a
test about mosaics. The progress tests also stub ``hub.require`` and
``hub.last_frame``, for an app hub with no rig: this hub is a connected
simulator, whose ``require("camera")`` answers the real camera and whose
``last_frame`` no capture here sets, so neither is stubbed.

WHAT IS READ, AND WHEN. A test holds the night at a fake instant
(`Night.hold`, from ``on_capture`` at the start of an exposure it picks, or
`Night.until` at a clock time) and `FlowRig.read` then asks, with every
engine task parked there: ``GET /api/flows/{id}/progress``, ``GET
/api/sequence/state`` as an operator and as a viewer, ``GET
/api/reports/{id}`` for every run the session holds, and the session file
itself. A report snapshot is written off the loop after each frame, so the
read waits, briefly and in real time, for the file to hold as many frames
of its run as the ledger does; it never waits on the night's clock.

NIGHT KEYS ANSWER THE SAME IN EVERY ZONE. A night key is local time with a
noon rollover (``events.night_key``), a report id carries the run's local
start (``report.SessionReporter._make_id``), and a session counts its nights
by turning that stamp back into an instant (``session.report_night``). The
harness's night is a fixed UTC instant, 01:48 UTC, which is the small hours
in UTC and the evening in the Pacific, but is past local noon from UTC+10
east, where a night of hours would straddle two keys. So the four modules
that read local time for these (``events``, ``sequence.session``,
``sequence.report`` and ``api.app``) are handed `ZonedTime`, whose local
zone is ``ZONE_S`` (UTC-4, the fixture site's own summer time at 74 W) on
every machine: the first night is key 2026-09-01 from 21:48 local, and a
night may run 14 h before the next key. ``report`` and ``app`` read the
night's clock through it, so a CONTINUE's night and a report's end are the
night's; ``events`` and ``session`` keep the real clock for ``time()``,
since only their zone was ever the problem.

THE FLOWS ARE DRAWN IN THE S5 PROBE'S SHAPE (tools/ui_probe/routes_s5_s6.json
``probe-s5-*``): a TARGET 2x2 "Rotate to PA" at PA 55, 25% overlap, framed
for the simulator's field; a FILTER CYCLE; the loop wire from its "pass
done" to the TARGET's "next panel"; and a REPORT. Only the centre and the
cycle differ between scenarios, and each says why.
"""
from __future__ import annotations

import asyncio
import calendar
import dataclasses
import time
from typing import Any, Callable

import httpx
import pytest

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.events as events_mod
import astrodeck.flows.store as flow_store_module
import astrodeck.sequence.report as report_mod
import astrodeck.sequence.session as session_mod
# T0 is imported by the scenarios from here too: the night's instant.
from _group_harness import T0, Night, close_night_hub, night_hub
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore
from astrodeck.flows.store import FlowStore
from astrodeck.sequence.session import Session, session_store
from conftest import _sweep_config_store  # rootdir-relative, as test_capture_root_isolated does

NAME = "M31 2x2"
TARGET = "M31"
#: The simulator camera's field at bin 1, as ``hub.effective_optics`` rounds
#: it and the S5 probe framed its TARGET for: the compile's M5 check then
#: finds no loss, so ``/run`` needs no ``accept_unmapped``. `FlowRig.save_flow`
#: asks the compile and fails loudly if the simulator's field ever moves.
SIM_FIELD = (0.494, 0.371)
PA = 55.0
OVERLAP = 25
#: The harness's local zone, seconds east of UTC (see the module docstring).
ZONE_S = -4 * 3600.0
#: A day on the night's clock: night two is the same instant a day later.
DAY_S = 86400.0
#: REAL seconds a scenario may take, from `FlowRig.open`, before its next
#: read fails it. `Night.settle` bounds each wait, but nothing bounded a
#: loop of them: a scenario that read night 1 without grading it ran for
#: over ten minutes under a ledger mutant, each read waiting out
#: `FlowRig._report`'s two seconds (test_s7_sim_solve_failure.py
#: ``night_one``, #523). A scenario takes 3 to 12 s here; the margin is for
#: a loaded parallel run.
#:
#: RED under that ledger mutant with ``night_one``'s grading taken out again
#: (harness mutants "ledger write skipped for a cycle slot" and "night one
#: ungraded" together), where the scenario would otherwise run on, observed
#: after 182 s:
#:
#:     at +1010.0 s of night 1: the scenario has run past its budget of 180
#:     s of real time; a loop of reads that never ends (#523)
WALL_BUDGET_S = 180.0


# ------------------------------------------------------------------ the flow

def fmt_ra(hours: float) -> str:
    """``hours`` as the typed text a TARGET carries, "22h 37m 04.12s"."""
    total = round((hours % 24.0) * 3600.0, 2)
    h, rest = divmod(total, 3600.0)
    m, s = divmod(rest, 60.0)
    return f"{int(h):02d}h {int(m):02d}m {s:05.2f}s"


def fmt_dec(deg: float) -> str:
    sign = "-" if deg < 0 else "+"
    total = round(abs(deg) * 3600.0, 1)
    d, rest = divmod(total, 3600.0)
    m, s = divmod(rest, 60.0)
    return f"{sign}{int(d):02d}° {int(m):02d}' {s:04.1f}\""


def mosaic_flow(*, ra_hours: float, dec_deg: float = 41.0,
                plan: str = "L 10, R 10", cycles: int = 3,
                per_cycle: int = 1) -> dict:
    """The S5 probe's flow with its centre at ``ra_hours``/``dec_deg`` and
    its FILTER CYCLE's ``plan`` and ``cycles``: TARGET -> FILTER CYCLE ->
    REPORT, and the loop wire from the cycle's "pass done" (``pass``) to the
    TARGET's "next panel" (``next``), which compiles to one rotating group
    at one pass a visit."""
    return {
        "nodes": [
            {"id": "n2", "type": "target", "x": 30, "y": 60,
             "params": {"name": TARGET, "ra": fmt_ra(ra_hours),
                        "dec": fmt_dec(dec_deg), "rows": 2, "cols": 2,
                        "overlap": OVERLAP, "fovX": SIM_FIELD[0],
                        "fovY": SIM_FIELD[1], "angle": "Rotate to PA",
                        "rotation": PA}},
            {"id": "n7", "type": "cycle", "x": 270, "y": 60,
             "params": {"plan": plan, "cycles": cycles,
                        "perCycle": per_cycle}},
            {"id": "n12", "type": "report", "x": 510, "y": 60, "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "n2", "fromPort": "target", "to": "n7",
             "toPort": "run"},
            {"id": "e2", "from": "n7", "fromPort": "complete", "to": "n12",
             "toPort": "session"},
            {"id": "e3", "from": "n7", "fromPort": "pass", "to": "n2",
             "toPort": "next"},
        ],
        "settings": {},
    }


def moved(graph: dict, dx: int = 40) -> dict:
    """The same flow with its REPORT node moved on the canvas: a save that
    changes nothing the compile reads, so every id stays where it was."""
    out = {**graph, "nodes": [dict(n) for n in graph["nodes"]]}
    for n in out["nodes"]:
        if n["type"] == "report":
            n["x"] = int(n["x"]) + dx
    return out


def panel_label(name: str) -> str:
    """"M31 2-1" -> "2-1"."""
    return name.rsplit(" ", 1)[-1]


# ------------------------------------------------------------------ the zone

class ZonedTime:
    """A ``time`` module whose LOCAL ZONE IS FIXED, ``offset_s`` east of UTC
    with no daylight saving, on every machine, and whose ``time()`` is
    ``now()``. Everything else is the real module's. See the module
    docstring for why the night's modules read it."""

    def __init__(self, now: Callable[[], float], offset_s: float) -> None:
        self._now = now
        self._offset = float(offset_s)

    def time(self) -> float:
        return self._now()

    def localtime(self, secs: float | None = None) -> time.struct_time:
        t = self._now() if secs is None else float(secs)
        return time.gmtime(t + self._offset)

    def mktime(self, fields) -> float:
        return float(calendar.timegm(tuple(fields)[:6])) - self._offset

    def strftime(self, fmt: str, fields=None) -> str:
        return time.strftime(fmt, self.localtime() if fields is None
                             else fields)

    def ctime(self, secs: float | None = None) -> str:
        return time.asctime(self.localtime(secs))

    def __getattr__(self, name: str):
        return getattr(time, name)


# ------------------------------------------------------------------ the rig

class _Fixed:
    """Every request resolves to one principal (the RBAC suite's pattern)."""

    name = "fake"

    def __init__(self, principal) -> None:
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@dataclasses.dataclass
class Reading:
    """Everything a test reads at one fake instant, as the routes answered.

    ``t`` is the instant, seconds from the night's ``t0``; ``at`` is the
    phrase every assertion message carries ("at +612.0 s of night 1")."""

    t: float
    at: str
    progress: dict
    state: dict
    viewer: dict
    reports: dict[str, dict]
    session: Session

    def ledger(self) -> dict[tuple[str, str], int]:
        """Frames the session holds, by ``(target_id, step_id)``."""
        out: dict[tuple[str, str], int] = {}
        for f in self.session.frames:
            key = (f.target_id, f.step_id)
            out[key] = out.get(key, 0) + 1
        return out

    def banked(self) -> dict[tuple[str, str], int]:
        """The progress route's ``banked`` by ``(target_id, step_id)``."""
        return {(p["target_id"], s["step_id"]): s["banked"]
                for b in self.progress["blocks"] for p in b["panels"]
                for s in p["steps"]}

    def owed(self) -> dict[str, int]:
        """The progress route's ``owed`` by panel label."""
        return {panel_label(p["name"]): p["owed"]
                for b in self.progress["blocks"] for p in b["panels"]}


class FlowRig:
    """The isolated app and the nights it runs. See the module docstring."""

    def __init__(self, store: ConfigStore, tmp_path, monkeypatch) -> None:
        self.store = store
        self.tmp = tmp_path
        self.mp = monkeypatch
        self.client: httpx.AsyncClient | None = None
        self.nights: list[Night] = []
        self._hubs: list[tuple[Any, list]] = []
        self.night_no = 0
        #: The loop time past which `read` fails the scenario (WALL_BUDGET_S).
        self._deadline: float | None = None

    # ---- set-up and tear-down

    async def open(self) -> "FlowRig":
        self._deadline = asyncio.get_running_loop().time() + WALL_BUDGET_S
        mp = self.mp
        _sweep_config_store(mp, self.store)
        mp.setattr(config_mod, "CONFIG_DIR", self.tmp)
        mp.delenv(app_module.AUTH_ENV_VAR, raising=False)
        flows = FlowStore(self.tmp / "flows")
        # BOTH names: the routes read app_module's, and the engine's finalize
        # imports the module's at call time to write the card's last result.
        mp.setattr(app_module, "flow_store", flows)
        mp.setattr(flow_store_module, "flow_store", flows)
        reset_active_provider()
        # Zone-pinned with the real clock until a night hands its own over.
        mp.setattr(events_mod, "time", ZonedTime(time.time, ZONE_S))
        mp.setattr(session_mod, "time", ZonedTime(time.time, ZONE_S))
        app = app_module.create_app()
        # Again after the app is built: a module first imported by
        # create_app would otherwise keep the real store.
        _sweep_config_store(mp, self.store)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        return self

    async def close(self) -> None:
        try:
            for night in self.nights:
                await night.close()
        finally:
            for hub, popped in self._hubs:
                await close_night_hub(hub, popped)
            if self.client is not None:
                await self.client.aclose()
            reset_active_provider()

    async def night(self, *, day: int = 0, goto=None, sky=None,
                    stars=None, spin_bound_s: float | None = None) -> Night:
        """A new clocked night ``day`` days after the harness's ``T0``, on a
        fresh simulator hub, made the app's hub and engine, with the spin
        watchdog armed. The previous night must be over; the session store
        is the same one.

        ``sky`` defaults to "the centring solve measured the angle the
        group is laid out at" (``pa``, set once the flow is compiled): a
        "Rotate to PA" group checks the measured angle on every hop, and a
        hop with no measurement is deferred (spec 5.6 step 4).
        ``spin_bound_s`` is the watchdog's bound, the harness's own unless a
        test of the watchdog shortens it."""
        if self.nights and self.nights[-1].engine.running:
            raise AssertionError("night: the previous night is still running")
        hub, popped = await night_hub(self.mp)
        self._hubs.append((hub, popped))
        self.night_no += 1
        bound = {} if spin_bound_s is None else {"spin_bound_s": spin_bound_s}
        night = Night(hub, self.mp, t0=T0 + day * DAY_S, goto=goto,
                      sky=sky if sky is not None else self._sky, stars=stars,
                      **bound)
        night.number = self.night_no
        night.pier_slews = []
        night.published = []
        self._watch_the_pier(night)
        self._watch_the_states(night)
        self.mp.setattr(app_module, "hub", hub)
        self.mp.setattr(app_module, "engine", night.engine)
        self.mp.setattr(report_mod, "time",
                        ZonedTime(night.clock.time, ZONE_S))
        self.mp.setattr(app_module, "time",
                        ZonedTime(night.clock.time, ZONE_S))
        self.nights.append(night)
        night.arm()
        return night

    #: The layout angle the compile gave the group (``plan.groups[0].pa_deg``),
    #: set by `save_flow`; the default sky script answers it.
    pa: float | None = None

    def _sky(self, who: str, attempt: int):
        return self.pa

    def _watch_the_pier(self, night: Night) -> None:
        """Record the mount's side after every slew while the night records
        (``pier_slews``: ``(t, side)``), read off the simulator's own latch
        (`SimTelescope._latched_side`): a pier change is a slew that lands
        on the other side, whether or not a frame follows it, which a count
        over frames alone cannot see."""
        tel = night.hub.devices["telescope"]
        harness_slew = tel.slew

        async def slew(ra_hours, dec_deg):
            try:
                await harness_slew(ra_hours, dec_deg)
            finally:
                side = getattr(tel, "_latched_side", None)
                if night._recording and side is not None:
                    night.pier_slews.append((night.clock.t, str(side.value)
                                             if hasattr(side, "value")
                                             else str(side)))

        self.mp.setattr(tel, "slew", slew)

    def _watch_the_states(self, night: Night) -> None:
        """Record every ``sequence`` state the night publishes while it
        records (``published``): the fake time, where the mount points at
        that moment (``ra``, the simulator's own), and the payload's
        ``state``, ``live`` (None when the key is absent) and ``group``.
        Where the mount points says whether a state is past the crossing,
        as tests/test_s7_meridian_chip_cleared.py reads it: the hub's
        meridian block, which the chip comes from, is for the pointing."""
        from astrodeck import events
        tel = night.hub.devices["telescope"]
        inner = events.bus.publish

        def publish(topic, **payload):
            if topic == "sequence" and night._recording:
                night.published.append({
                    "t": night.clock.t, "ra": tel.rig.ra_hours,
                    "state": payload.get("state"),
                    "live": (dict(payload["live"]) if "live" in payload
                             else None),
                    "group": (dict(payload["group"])
                              if isinstance(payload.get("group"), dict)
                              else None)})
            return inner(topic, **payload)

        self.mp.setattr(events.bus, "publish", publish)

    # ---- the routes

    def as_role(self, role: str | None) -> None:
        """Requests from now resolve to ``role``'s principal, or to the open
        default for None."""
        if role is None:
            reset_active_provider()
        else:
            set_active_provider(_Fixed(principal_for_role(role)))

    async def save_flow(self, graph: dict, *,
                        at: str = "before the run") -> str:
        """``POST /api/flows`` as the open default; the compile of the stored
        flow is asked too, and must list no loss (the TARGET is framed for
        the simulator's field) and one rotating group. ``at`` names the
        moment for the failure message, as every helper's does."""
        self.as_role(None)
        r = await self.client.post("/api/flows", json={
            "flow": {"name": NAME, "graph": graph}})
        assert r.status_code == 200, f"{at}: POST /api/flows: {r.text}"
        fid = r.json()["id"]
        comp = await self.client.post(f"/api/flows/{fid}/compile", json={})
        assert comp.status_code == 200, (
            f"{at}: POST /api/flows/{fid}/compile: {comp.text}")
        lost = [u for u in comp.json().get("unmapped") or []
                if u.get("level") != "note"]
        assert lost == [], (
            f"premise, {at}: the flow runs without a question: {lost}")
        plan = self.plan_for(fid, graph)
        (group,) = plan.groups
        assert group.mode == "rotate" and group.rotate, (
            f"premise, {at}: one rotating group that turns the camera: "
            f"{group}")
        self.pa = group.pa_deg
        return fid

    @staticmethod
    def plan_for(fid: str, graph: dict):
        """The plan ``/run`` compiles for ``graph`` stored at ``fid``: the
        same compile, name and ``flow_id``, so the same ids (the progress
        tests' ``_compiled``)."""
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.models import FlowGraph
        from astrodeck.flows.to_plan import to_sequence_plan
        g = FlowGraph.model_validate(graph)
        plan, _ = to_sequence_plan(compile_plan(g, NAME), g, flow_id=fid)
        return plan

    async def put_flow(self, fid: str, graph: dict, *, at: str) -> dict:
        self.as_role(None)
        r = await self.client.put(f"/api/flows/{fid}", json={
            "flow": {"name": NAME, "graph": graph}})
        assert r.status_code == 200, f"{at}: PUT /api/flows/{fid}: {r.text}"
        return r.json()

    async def flow(self, fid: str, *, at: str) -> dict:
        self.as_role(None)
        r = await self.client.get(f"/api/flows/{fid}")
        assert r.status_code == 200, f"{at}: GET /api/flows/{fid}: {r.text}"
        return r.json()

    async def run(self, fid: str, **body) -> httpx.Response:
        """``POST /api/flows/{id}/run`` as the open default."""
        self.as_role(None)
        return await self.client.post(f"/api/flows/{fid}/run", json=body)

    async def abort(self, *, at: str) -> None:
        """``POST /api/sequence/abort`` as the open default: the operator's
        STOP. The run's wind-down is on the night's clock."""
        self.as_role(None)
        r = await self.client.post("/api/sequence/abort")
        assert r.status_code == 200, (
            f"{at}: POST /api/sequence/abort: {r.text}")

    async def get(self, path: str, role: str | None = "operator", *,
                  at: str = "") -> dict:
        """``GET path`` as ``role``; ``at`` names the fake instant for the
        failure message."""
        self.as_role(role)
        try:
            r = await self.client.get(path)
        finally:
            self.as_role(None)
        assert r.status_code == 200, f"{at}: GET {path} as {role}: {r.text}"
        return r.json()

    @staticmethod
    def at(night: Night) -> str:
        """The phrase every assertion message carries: the fake instant the
        night stands at, "at +612.0 s of night 1"."""
        return f"at +{night.rel(night.clock.t):.1f} s of night {night.number}"

    async def read(self, night: Night, fid: str) -> Reading:
        """Every answer a test grades, at the instant the night stands at.

        Refused once the scenario has run WALL_BUDGET_S of real time: the
        read loops are where a scenario that cannot end spends its time."""
        t = night.rel(night.clock.t)
        at = self.at(night)
        loop = asyncio.get_running_loop()
        if self._deadline is not None and loop.time() > self._deadline:
            pytest.fail(f"{at}: the scenario has run past its budget of "
                        f"{WALL_BUDGET_S:g} s of real time; a loop of reads "
                        f"that never ends (#523)", pytrace=False)
        progress = await self.get(f"/api/flows/{fid}/progress", at=at)
        state = await self.get("/api/sequence/state", "operator", at=at)
        viewer = await self.get("/api/sequence/state", "viewer", at=at)
        sid = (progress.get("session") or {}).get("id")
        assert sid, f"{at}: the progress route names no session: {progress}"
        session = await asyncio.to_thread(session_store.load, sid)
        reports: dict[str, dict] = {}
        for rid in session.nights:
            reports[rid] = await self._report(rid, session, at)
        return Reading(t=t, at=at, progress=progress, state=state,
                       viewer=viewer, reports=reports, session=session)

    async def _report(self, rid: str, session: Session, at: str) -> dict:
        """``GET /api/reports/{rid}``, waiting up to 2 s of real time for the
        snapshot that holds as many frames of that run as the ledger does:
        the snapshot after a frame is written on a worker thread, and a read
        that raced it would grade the write, not the report.

        A 404 is waited out the same way and then fails: since #517 the
        reporter writes its first file at the run's start, when
        ``engine.start`` names the id in the session, so a run that has
        banked nothing yet still has a report, with no frames."""
        want = sum(1 for f in session.frames if f.night == rid)
        loop = asyncio.get_running_loop()
        end = loop.time() + 2.0
        while True:
            self.as_role("operator")
            try:
                r = await self.client.get(f"/api/reports/{rid}")
            finally:
                self.as_role(None)
            body = r.json() if r.status_code == 200 else None
            got = (None if body is None else
                   body["frames_captured"] + body["frames_rejected"])
            if got == want or loop.time() >= end:
                assert body is not None, (
                    f"{at}: GET /api/reports/{rid}, a run the session names, "
                    f"holding {want} frame(s) in the ledger: {r.text}")
                return body
            await asyncio.sleep(0.01)


@pytest.fixture
async def flow_rig(group_store, tmp_path, monkeypatch):
    rig = await FlowRig(group_store, tmp_path, monkeypatch).open()
    try:
        yield rig
    finally:
        await rig.close()


# ------------------------------------------------------------ what agrees

def steps_by_panel(plan) -> dict[tuple[str, str], tuple[str, str]]:
    """``(panel label, filter) -> (target_id, step_id)`` for every step."""
    return {(panel_label(t.name), s.filter): (t.id, s.id)
            for t in plan.targets for s in t.steps}


def shot_counts(shots: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """``Night.shots`` counted by ``(panel label, filter)``."""
    out: dict[tuple[str, str], int] = {}
    for target, filt in shots:
        key = (panel_label(target), filt)
        out[key] = out.get(key, 0) + 1
    return out


def assert_the_three_agree(reading: Reading, plan) -> None:
    """The ledger, the reports and the progress route agree (every
    scenario's closing check).

    * Each run's report: its ``by_filter`` and ``targets`` frame counts are
      the ledger's frames of that run (``SessionFrame.night`` is the run's
      report id), by filter and by panel and filter.
    * The progress route: every step's ``banked`` IS the ledger's count for
      that step. Every frame here is accepted, so the count mode does not
      enter, and the route's cap at the step's count is left out on
      purpose: a run that shot a step past its count would bank no more
      than the count on the route, and the equality then fails, which is
      the point.
    """
    at = reading.at
    ledger = reading.ledger()
    by_step = steps_by_panel(plan)
    names = {tid: t.name for t in plan.targets for tid in [t.id]}
    filters = {sid: s.filter for t in plan.targets for s in t.steps
               for sid in [s.id]}
    for rid, rep in reading.reports.items():
        mine: dict[tuple[str, str], int] = {}
        for f in reading.session.frames:
            if f.night == rid:
                key = (names[f.target_id], filters[f.step_id])
                mine[key] = mine.get(key, 0) + 1
        by_filter: dict[str, int] = {}
        for (_n, filt), n in mine.items():
            by_filter[filt] = by_filter.get(filt, 0) + n
        assert {b["filter"]: b["frames"] for b in rep["by_filter"]} == \
            by_filter, (f"{at}: report {rid}'s by_filter is not the ledger's "
                        f"frames of that run")
        assert {(t["name"], b["filter"]): b["frames"]
                for t in rep["targets"] for b in t["by_filter"]} == mine, (
            f"{at}: report {rid}'s targets are not the ledger's frames of "
            f"that run")
    banked = reading.banked()
    expected = {key: ledger.get(key, 0) for key in by_step.values()}
    assert banked == expected, (f"{at}: the progress route's banked is not "
                                f"the ledger's")
