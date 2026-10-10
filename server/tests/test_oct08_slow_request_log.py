# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The slow-request log (#858) and GET /api/plans off the event loop (#858 N1).

On 2026-10-07 the UI said "server not responding" while the server logged no
slow request at all, so the route that ran past the UI's 15 s budget could not
be named. ``astrodeck.api.slow_requests.SlowRequestLog`` now writes one line
for every HTTP request whose first byte takes 3.0 s or more, by route
TEMPLATE, with the outcome, whether the event loop itself was stalled, relay
or LAN, and how many other requests were in flight.

S1-S12, S16, S17 and S4b wrap a tiny ASGI app in the real middleware with a
fake clock the inner app advances (values chosen to be exact in binary float).
S13 and S18 drive the real ``create_app()``. S14 and S15 use the real clock
and a real blocked or yielding event loop; their margins (0.75 s and about
1 s) are far wider than the 15.6 ms Windows timer resolution. S19-S21 (fix
round 1) use the fake clock for durations and the real loop for the probe:
S19 relies on asyncio running callbacks already queued before a timer that
fell due meanwhile (BaseEventLoop._run_once), and S20 sleeps 0.6 s of real
time per step so the 0.25 s probe ticks at least twice.

Each case names the mutant of the production line that turns it red; the
mutant runs are recorded in the P6 report.
"""
from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.api import slow_requests
from astrodeck.api.slow_requests import SlowRequestLog

ROUTE = "/items/{item_id}"


def _fake_clock(start: float = 100.0):
    t = [start]
    return t, (lambda: t[0])


def _scope(path: str = "/items/7", method: str = "GET", query: bytes = b"",
           state: dict | None = None) -> dict:
    scope = {"type": "http", "method": method, "path": path,
             "query_string": query, "headers": []}
    if state is not None:
        scope["state"] = state
    return scope


def _inner(t, *, route: str | None = ROUTE, elapsed: float = 4.0,
           status: int = 200, body_advance: float = 0.0):
    async def app(scope, receive, send):
        if route is not None:
            scope["route"] = SimpleNamespace(path=route)
        t[0] += elapsed
        await send({"type": "http.response.start", "status": status,
                    "headers": []})
        t[0] += body_advance
        await send({"type": "http.response.body", "body": b"ok",
                    "more_body": False})
    return app


async def _drive(mw, scope) -> list[dict]:
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)
    return sent


def _slow(lines):
    return [ln for ln in lines if ln[1].startswith("slow request ")]


# --------------------------------------------------------------- S1 - S12

async def test_a_request_past_the_threshold_is_logged_by_route_template(bus_lines):
    """S1. Mutant M1: delete the events.bus.log call in _maybe_report."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, elapsed=4.0), clock=clock)
    await _drive(mw, _scope())
    assert _slow(bus_lines) == [
        ("info", "slow request GET /items/{item_id}: 200 after 4.0 s", "http")]


async def test_a_fast_request_logs_nothing(bus_lines):
    """S2. Mutant M2: SLOW_REQUEST_S = 0.0."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, elapsed=0.25), clock=clock)
    await _drive(mw, _scope())
    assert _slow(bus_lines) == []


async def test_the_threshold_is_inclusive(bus_lines):
    """S3. Mutant M3: `elapsed < SLOW_REQUEST_S` -> `elapsed <= SLOW_REQUEST_S`."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, route="/a", elapsed=3.0), clock=clock)
    await _drive(mw, _scope())
    t2, clock2 = _fake_clock()
    mw2 = SlowRequestLog(_inner(t2, route="/b", elapsed=2.75), clock=clock2)
    await _drive(mw2, _scope())
    assert [ln[1] for ln in _slow(bus_lines)] == [
        "slow request GET /a: 200 after 3.0 s"]


async def test_time_runs_to_the_first_byte_not_the_end_of_a_stream(bus_lines):
    """S4. Mutant M4: `end = self._clock()` in place of the first-byte time."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, elapsed=0.5, body_advance=10.0), clock=clock)
    sent = await _drive(mw, _scope())
    assert [m["type"] for m in sent] == ["http.response.start",
                                         "http.response.body"]
    assert _slow(bus_lines) == []


async def test_a_slow_client_socket_is_not_counted(bus_lines):
    """S4b: the first byte is stamped before the head is handed to the
    socket, so a client that takes 10 s to accept it is not the server being
    slow. Mutant RX5: stamp the first byte after `await send(message)`."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, elapsed=0.5), clock=clock)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def slow_socket(message):
        if message["type"] == "http.response.start":
            t[0] += 10.0
        sent.append(message)

    await mw(_scope(), receive, slow_socket)
    assert [m["type"] for m in sent] == ["http.response.start",
                                         "http.response.body"]
    assert _slow(bus_lines) == []


async def test_the_line_carries_the_template_never_the_path_or_query(bus_lines):
    """S5. Mutants M5a: route_label returns scope["path"] first; M5b: the label
    is scope["path"] + "?" + the query string."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, elapsed=4.0), clock=clock)
    await _drive(mw, _scope(path="/items/7", query=b"token=w-marker-97531"))
    lines = _slow(bus_lines)
    assert len(lines) == 1
    msg = lines[0][1]
    assert "/items/{item_id}" in msg
    assert "97531" not in msg
    assert "/items/7" not in msg


async def test_an_unrouted_request_gets_the_fixed_label(bus_lines):
    """S6. Mutant M6: the fallback returns scope["path"]."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, route=None, elapsed=4.0), clock=clock)
    await _drive(mw, _scope(path="/nope/zz-unrouted-31415"))
    lines = _slow(bus_lines)
    assert [ln[1] for ln in lines] == [
        "slow request GET (unrouted): 200 after 4.0 s"]
    assert "31415" not in lines[0][1]


async def test_a_request_the_ui_gave_up_on_is_a_warning(bus_lines):
    """S7. Mutants M7a: level always "info"; M7b: `>=` -> `>` in the level test."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, route="/gave-up", elapsed=15.0), clock=clock)
    await _drive(mw, _scope(path="/gave-up"))
    t2, clock2 = _fake_clock()
    mw2 = SlowRequestLog(_inner(t2, route="/nearly", elapsed=14.75), clock=clock2)
    await _drive(mw2, _scope(path="/nearly"))
    assert [(ln[0], ln[1]) for ln in _slow(bus_lines)] == [
        ("warning", "slow request GET /gave-up: 200 after 15.0 s"),
        ("info", "slow request GET /nearly: 200 after 14.8 s"),
    ]


async def test_long_budget_routes_warn_only_past_their_own_budget(bus_lines):
    """S8. Mutant M8: ui_budget_s returns UI_REQUEST_BUDGET_S unconditionally."""
    for elapsed in (20.0, 130.0):
        t, clock = _fake_clock()
        mw = SlowRequestLog(_inner(t, route="/api/connect/nina", elapsed=elapsed),
                            clock=clock)
        await _drive(mw, _scope(path="/api/connect/nina", method="POST"))
    assert [ln[0] for ln in _slow(bus_lines)] == ["info", "warning"]


async def test_repeats_within_the_cooldown_collapse_and_are_counted(bus_lines):
    """S9. Mutants M9a: delete the cooldown `if ... return` block; M9b:
    `earlier = 0` in place of `self._held.pop(key, 0)`."""
    t, clock = _fake_clock(0.0)
    mw = SlowRequestLog(_inner(t, elapsed=4.0), clock=clock)
    for start in (0.0, 10.0, 20.0, 70.0):
        t[0] = start
        await _drive(mw, _scope())
    assert [ln[1] for ln in _slow(bus_lines)] == [
        "slow request GET /items/{item_id}: 200 after 4.0 s",
        "slow request GET /items/{item_id}: 200 after 4.0 s (+2 earlier)",
    ]


async def test_a_cancelled_request_is_logged_and_stays_cancelled(bus_lines):
    """S10: the relay's REQ_ABORT at its 30 s upstream timeout cancels the
    home task. Mutants M10a: delete the `except asyncio.CancelledError` arm
    (the outcome reads "failed"); M10b: drop its `raise`."""
    t, clock = _fake_clock()
    entered = asyncio.Event()
    never = asyncio.Event()

    async def inner(scope, receive, send):
        scope["route"] = SimpleNamespace(path=ROUTE)
        t[0] += 30.0
        entered.set()
        await never.wait()

    mw = SlowRequestLog(inner, clock=clock)
    task = asyncio.ensure_future(_drive(mw, _scope()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [(ln[0], ln[1]) for ln in _slow(bus_lines)] == [
        ("warning", "slow request GET /items/{item_id}: cancelled after 30.0 s")]


async def test_a_handler_error_is_logged_and_propagates(bus_lines):
    """S11. Mutant M11: the report moved out of `finally` into the success path."""
    t, clock = _fake_clock()

    async def inner(scope, receive, send):
        scope["route"] = SimpleNamespace(path=ROUTE)
        t[0] += 5.0
        raise RuntimeError("handler broke")

    mw = SlowRequestLog(inner, clock=clock)
    with pytest.raises(RuntimeError, match="handler broke"):
        await _drive(mw, _scope())
    assert [ln[1] for ln in _slow(bus_lines)] == [
        "slow request GET /items/{item_id}: failed after 5.0 s"]


async def test_a_relay_request_says_relay(bus_lines):
    """S12. Mutant M12: `remote=False` passed to format_slow_line."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, elapsed=4.0), clock=clock)
    await _drive(mw, _scope(state={"astrodeck_remote": True}))
    assert [ln[1] for ln in _slow(bus_lines)] == [
        "slow request GET /items/{item_id}: 200 after 4.0 s (relay)"]


# ---------------------------------------------------------------- S13

def test_create_app_installs_the_slow_request_log(bus_lines, monkeypatch):
    """S13, against the real create_app(). Mutant M13: delete
    `app.add_middleware(SlowRequestLog)` in create_app."""
    monkeypatch.setattr(slow_requests, "SLOW_REQUEST_S", 0.0)
    client = TestClient(app_module.create_app())
    r = client.get("/api/version")
    assert r.status_code == 200
    assert any(ln[1].startswith("slow request GET /api/version: 200 after ")
               for ln in bus_lines), _slow(bus_lines)


# ---------------------------------------------------------- S14 - S15

def _stall_of(line: str) -> float | None:
    m = re.search(r"loop stall (\d+\.\d) s", line)
    return float(m.group(1)) if m else None


async def test_a_blocked_event_loop_is_named(bus_lines, monkeypatch):
    """S14, real clock: the inner app blocks the loop for 2.0 s, so the 0.25 s
    tick is about 1.75 s late. Mutant M14: _leave returns 0.0 for the stall."""
    monkeypatch.setattr(slow_requests, "SLOW_REQUEST_S", 0.5)

    async def inner(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/blocked")
        time.sleep(2.0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    mw = SlowRequestLog(inner)
    await _drive(mw, _scope(path="/blocked"))
    lines = _slow(bus_lines)
    assert len(lines) == 1, lines
    stall = _stall_of(lines[0][1])
    assert stall is not None and stall >= 1.0, lines[0][1]


async def test_an_await_that_yields_is_not_a_stall(bus_lines, monkeypatch):
    """S15, real clock: a 2.0 s await lets the ticks run on time. Mutant M15:
    the stall taken as `end - started`."""
    monkeypatch.setattr(slow_requests, "SLOW_REQUEST_S", 0.5)

    async def inner(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/yields")
        await asyncio.sleep(2.0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    mw = SlowRequestLog(inner)
    await _drive(mw, _scope(path="/yields"))
    lines = _slow(bus_lines)
    assert len(lines) == 1, lines
    assert lines[0][1].startswith("slow request GET /yields: 200 after ")
    assert "loop stall" not in lines[0][1], lines[0][1]
    # The probe is released once nothing is in flight.
    assert mw._in_flight == 0 and mw._tick_handle is None


# ---------------------------------------------------------- S16 - S17

async def test_a_failing_enter_never_breaks_the_request(bus_lines, monkeypatch):
    """S16. Mutant M16: remove the try/except around self._enter()."""
    def raising(self):
        raise RuntimeError("bookkeeping broke")

    monkeypatch.setattr(SlowRequestLog, "_enter", raising)
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, elapsed=4.0), clock=clock)
    sent = await _drive(mw, _scope())
    assert sent[0] == {"type": "http.response.start", "status": 200,
                       "headers": []}
    assert sent[1]["body"] == b"ok"
    assert [ln[1] for ln in _slow(bus_lines)] == [
        "slow request GET /items/{item_id}: 200 after 4.0 s"]


async def test_a_failing_leave_never_breaks_the_request_and_releases_the_count(
        bus_lines):
    """S17: _leave's stall arithmetic raises (an entry that cannot be
    unpacked). Mutants M17a: remove the try/except around self._leave(token);
    M17b: the in-flight decrement moved out of _leave's `finally` into its
    body (the count stays 1 and the probe never stops)."""
    t, clock = _fake_clock()
    holder: dict = {}

    async def inner(scope, receive, send):
        scope["route"] = SimpleNamespace(path=ROUTE)
        mw_ = holder["mw"]
        for k in list(mw_._watch):
            mw_._watch[k] = object()
        t[0] += 4.0
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    mw = SlowRequestLog(inner, clock=clock)
    holder["mw"] = mw
    sent = await _drive(mw, _scope())
    assert sent[0]["status"] == 200
    assert [ln[1] for ln in _slow(bus_lines)] == [
        "slow request GET /items/{item_id}: 200 after 4.0 s"]
    assert mw._in_flight == 0
    assert mw._tick_handle is None


# ---------------------------------------------------------------- S18

def test_the_plans_list_is_read_off_the_event_loop(monkeypatch):
    """S18 (#858 N1), against the real create_app(). Mutant M18:
    `return plan_library.list()` (the base line, on the event loop)."""
    seen: list[bool] = []

    def probe():
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return []

    monkeypatch.setattr(app_module.plan_library, "list", probe)
    client = TestClient(app_module.create_app())
    r = client.get("/api/plans")
    assert r.status_code == 200
    assert r.json() == []
    assert seen == [False], "the plan library was read ON the event loop"


# ---------------------------------------------------- S19 - S21 (fix round 1)

async def test_a_stall_that_began_before_the_request_is_left_out_of_its_figure(
        bus_lines):
    """S19: request A blocks the loop for 2.0 s and then starts request B,
    which runs whole before the overdue probe tick has a turn. The stall
    happened before B entered, so B's line names none; A's line does (the
    stall is real, so B's case is not vacuous). Mutant RX2: `pending = now -
    self._tick_due` in place of the `min(...)` in _leave."""
    t, clock = _fake_clock()
    holder: dict = {}

    async def inner(scope, receive, send):
        path = scope["path"]
        scope["route"] = SimpleNamespace(path=path)
        if path == "/a":
            time.sleep(2.0)
            holder["b"] = asyncio.ensure_future(
                _drive(holder["mw"], _scope(path="/b")))
            await asyncio.sleep(0)
        else:
            t[0] += 4.0
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    mw = SlowRequestLog(inner, clock=clock)
    holder["mw"] = mw
    await _drive(mw, _scope(path="/a"))
    await holder["b"]
    lines = {ln[1].split(":")[0]: ln[1] for ln in _slow(bus_lines)}
    assert set(lines) == {"slow request GET /a", "slow request GET /b"}, lines
    assert lines["slow request GET /b"] == (
        "slow request GET /b: 200 after 4.0 s (1 in flight)")
    stall_a = _stall_of(lines["slow request GET /a"])
    assert stall_a is not None and stall_a >= 1.0, lines["slow request GET /a"]


async def test_a_request_that_never_answers_is_named_while_it_waits(bus_lines):
    """S20: the probe names a request still waiting at the UI's budget, once,
    and its closing line is written past the cooldown that first line started.
    Mutants RW1: delete the `self._report_waiting()` call in _tick (no line
    while it waits); RW2: `force=False` for the closing line (held by the
    cooldown); RW3: delete `if w.checked: continue` (the probe asks again on
    every tick, and the held count surfaces as "+N earlier")."""
    t, clock = _fake_clock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def inner(scope, receive, send):
        scope["route"] = SimpleNamespace(path=ROUTE)
        entered.set()
        await release.wait()
        t[0] = 130.0
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    mw = SlowRequestLog(inner, clock=clock)
    task = asyncio.ensure_future(_drive(mw, _scope()))
    await entered.wait()
    t[0] = 114.0
    await asyncio.sleep(0.6)
    assert _slow(bus_lines) == [], "named before the UI's 15 s budget"
    t[0] = 115.0
    await asyncio.sleep(0.6)
    assert _slow(bus_lines) == [
        ("warning", "slow request GET /items/{item_id}: still waiting after 15.0 s",
         "http")]
    t[0] = 116.0
    await asyncio.sleep(0.6)
    release.set()
    await task
    # A later slow request on the same route, past the cooldown.
    t[0] = 200.0
    mw.app = _inner(t, elapsed=15.0)
    await _drive(mw, _scope())
    assert [(ln[0], ln[1]) for ln in _slow(bus_lines)] == [
        ("warning", "slow request GET /items/{item_id}: still waiting after 15.0 s"),
        ("warning", "slow request GET /items/{item_id}: 200 after 30.0 s"),
        ("warning", "slow request GET /items/{item_id}: 200 after 15.0 s"),
    ]


async def test_an_open_stream_is_not_counted_in_flight(bus_lines):
    """S21: a stream whose head has gone is settled: it leaves the in-flight
    count, so a slow request beside it does not say "1 in flight". Mutant
    RS1: timed_send neither leaves nor reports at the head (the stream is
    settled only when its body ends, as before fix round 1)."""
    t, clock = _fake_clock()
    streaming = asyncio.Event()
    end_stream = asyncio.Event()

    async def inner(scope, receive, send):
        if scope["path"] == "/stream":
            scope["route"] = SimpleNamespace(path="/stream")
            await send({"type": "http.response.start", "status": 200,
                        "headers": []})
            streaming.set()
            await end_stream.wait()
            await send({"type": "http.response.body", "body": b"",
                        "more_body": False})
            return
        scope["route"] = SimpleNamespace(path=ROUTE)
        t[0] += 4.0
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    mw = SlowRequestLog(inner, clock=clock)
    stream = asyncio.ensure_future(_drive(mw, _scope(path="/stream")))
    await streaming.wait()
    await _drive(mw, _scope())
    end_stream.set()
    sent = await stream
    assert [m["type"] for m in sent] == ["http.response.start",
                                         "http.response.body"]
    assert _slow(bus_lines) == [
        ("info", "slow request GET /items/{item_id}: 200 after 4.0 s", "http")]


# ------------------------------------------- int-review finding 6 (humanizer)

#: Ports of humanize.ts ``HTTP_5XX``, ``PLATE_SOLVE_FAILED`` and
#: ``GUIDING_LOST`` as they stand after #792 and #960: the rules that read a
#: report, not two words.
_HTTP_5XX = re.compile(
    r"\b5\d\d\b(?![.,]\d)(?!\s*(?:(?:ms|s|sec|secs|bytes|kb|mb|gb)\b|%))")
_PLATE_SOLVE_FAILED = re.compile(
    r"^\s*(?:[^\s:;][^:;]{0,29}:\s*)*(?:(?:the|a)\s+)?plate[- ]?solv(?:e|ing)"
    r"\s+(?:failed|failure|error|timed out)\b[\s.!]*$")
_GUIDING_LOST = re.compile(
    r"^(?:[^:;]{0,30}:\s*)?(?:native\s+)?guid(?:ing|er|e)"
    r"(?:\s+(?:was|has been|is))?\s+lost\b(?:\s+the\s+guide\s+star)?\s*"
    r"(?:\([^)]*\))?[\s.!]*$")


def _humanizer_rewrite(line: str) -> str | None:
    """The humanizeLog rule (ui/src/lib/humanize.ts) that would replace
    ``line`` whole, or None. A copy of its tests as they stand after #792,
    for a line from source "http" (the camera rule's source arm is "capture",
    so it never applies here)."""
    m = line.lower()
    if "camera" in m and any(w in m for w in
                             ("not responding", "timeout", "disconnect")):
        return "camera"
    if "nina" in m and (_HTTP_5XX.search(m) or "http" in m or "error" in m):
        return "nina"
    if _PLATE_SOLVE_FAILED.search(m):
        return "plate"
    if _GUIDING_LOST.search(m):
        return "guid"
    return None


def _every_route_label() -> list[str]:
    """Every label ``route_label`` can return in the real app: each route
    template ``create_app()`` serves (behind include_router too) plus the two
    fixed labels."""
    from astrodeck.auth.rbac import iter_app_routes
    paths = {getattr(r, "path", None)
             for r in iter_app_routes(app_module.create_app())}
    labels = {p for p in paths if isinstance(p, str) and p}
    labels |= {slow_requests.UNROUTED_LABEL, "/assets/*"}
    return sorted(labels)


def test_no_slow_line_trips_a_humanizer_pair_on_any_route():
    """Finding 6: a slow line for a NINA route held "nina" beside the 5 in
    its own figures, so the UI showed "NINA reported an error" for a connect
    that answered 200. #792 narrowed that rule to an HTTP 5xx token, so the
    figures are safe now; a NINA route that answered 500 is not. Every route
    template of the real app, every outcome, with figures full of 5s and a 500
    status: no line may hold a humanizer pair. Mutant H6: format_slow_line
    interpolates ``label`` instead of ``unpaired_label(label)``. Mutant H6b:
    drop ``nina`` from _HUMANIZER_KEYS."""
    labels = _every_route_label()
    # The harness still sees the routes it was built for, and its copy of the
    # rule still fires on a 5xx status line, and no longer on the figures.
    assert {"/api/connect/nina", "/api/nina/health",
            "/api/discover/nina"} <= set(labels), labels
    assert _humanizer_rewrite(
        "slow request POST /api/connect/nina: 500 after 15.2 s") == "nina"
    assert _humanizer_rewrite(
        "slow request POST /api/connect/nina: 200 after 15.2 s") is None
    tripped = []
    for label in labels:
        for method in sorted(slow_requests._METHODS | {"OTHER"}):
            for outcome in ("500", "200", "still waiting", "no answer",
                            "cancelled", "failed"):
                line = slow_requests.format_slow_line(
                    method=method, label=label, outcome=outcome,
                    elapsed_s=155.5, stall_s=5.5, remote=True, others=5,
                    earlier=55)
                assert "5" in line, line
                rule = _humanizer_rewrite(line)
                if rule is not None:
                    tripped.append((rule, line))
    assert tripped == [], tripped[:5]


async def test_a_slow_nina_connect_reads_as_a_slow_request(bus_lines):
    """Finding 6, through the middleware: the 15.5 s NINA connect that
    answered 200 is logged with the route key broken, at info (inside its
    130 s UI budget). Mutant H6 turns this red too."""
    t, clock = _fake_clock()
    mw = SlowRequestLog(_inner(t, route="/api/connect/nina", elapsed=15.5),
                        clock=clock)
    await _drive(mw, _scope(path="/api/connect/nina", method="POST"))
    assert _slow(bus_lines) == [
        ("info", "slow request POST /api/connect/ni-na: 200 after 15.5 s",
         "http")]
    assert _humanizer_rewrite(bus_lines[-1][1]) is None


def test_a_route_with_plate_or_guid_in_it_is_written_as_it_is():
    """#961: the plate-solve and guiding rules read a whole report since #792,
    which a route label never is, so the label is left alone. The guiding
    routes used to be logged as ``/api/gu-ide/start``. The keys that still
    pair (``nina``, ``camera``) stay broken: the test above pins that.
    Mutant: _HUMANIZER_KEYS back to ``nina|camera|plate|guid``."""
    labels = [lab for lab in _every_route_label()
              if re.search(r"plate|guid", lab, re.IGNORECASE)
              and not re.search(r"nina|camera", lab, re.IGNORECASE)]
    # The real app has guiding routes; a scan that found none proves nothing.
    assert "/api/guide/start" in labels and len(labels) >= 5, labels
    for label in labels:
        line = slow_requests.format_slow_line(
            method="POST", label=label, outcome="200", elapsed_s=15.5,
            stall_s=0.0, remote=False, others=0, earlier=0)
        assert f" {label}: " in line, line
        assert _humanizer_rewrite(line) is None, line
