# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#811 and #812: the dead-man's-switch says when its own timer work fails, and
refuses at SAVE a url it could never ping.

#811. ``AlertDispatcher._wallclock_loop`` wrapped ``deadman_ping`` and
``emit_heartbeat`` in ``except Exception: pass``. That is how #735 stayed
invisible: the ``InvalidURL`` went into the pass and nothing was said. #735's
fix made the ping itself never raise, so the pass was no longer reached by
that failure, but anything that escaped later (the heartbeat, ``get_config()``
at the top of the ping, a bug nobody has written yet) was again lost without a
trace, and a ping that runs on its own task (every ping, once ``run()`` has
started the pipeline) lost it on a task nobody awaits. Now each is said once
per distinct failure, by exception TYPE and never text (the text can quote the
url, whose path is the ping secret, #694), and the loop goes on.

#812. ``ConfigStore.set_deadman`` stored any string, and the one guard,
``_url_is_safe``, reads the scheme and the host and never the port or the
encoding. Four shapes passed it and made httpx refuse to build a request (a
non-numeric port, a control character, a malformed IDNA label, a lone
surrogate); the owner learned only from a one-shot log line on a later night.
Now ``deadman_url_problem`` is run at save, by ``set_deadman`` and by
``POST /api/config`` before ANY block of the body is written, and the 422
names the problem without echoing the url.

MUTANTS RUN, each from a byte backup restored byte-identically (md5sum
compared), and what each printed (counts are over this file's 49 cases):

  #811, alerting.py
  M1  the loop's dead-man arm is a ``pass`` again. 3 failed: the one-line
      case, the repeat case and the bus-cannot-log case (`AssertionError: []`,
      nothing said).
  M2  the loop's heartbeat arm is a ``pass`` again. 2 failed: the one-line
      case and the works-again case (`AssertionError: (3, [])`).
  M3  no latch (``if kind in said:`` -> ``if False:``). 4 failed: the same
      warning once per tick, `['alert dispatcher: the dead-man ping raised
      RuntimeError ...', ...]` (four cases).
  M4  a stage that works again keeps its set (the heartbeat's ``pop`` ->
      ``pass``). 1 failed, `AssertionError: (3, ['alert dispatcher: the
      heartbeat raised ValueError ...'])`: three failures, one line.
  M5  the pipelined task unguarded (``_deadman_ping_reporting`` ->
      ``_deadman_ping_now``). 1 failed, the `RuntimeError` out of the ping's
      own task, which nothing reads.
  M6  ``bus.log`` unguarded (``except Exception`` -> ``except
      ZeroDivisionError``). 1 failed, `AssertionError: the loop ended on its
      own` with `OSError: the night log is gone`.
  M7  the exception's text in the line (``{kind}`` -> ``{kind}: {exc}``).
      4 failed, `the fallback line carried the ping secret`.

  #812, alerting.py, config.py, api/app.py
  M8  no request build (``httpx.Request("GET", url)`` -> ``pass``). 10
      failed: control character, bad idna label and lone surrogate each in
      the reason, set_deadman and route cases (`'control character' was
      accepted`).
  M9  no length bound. 4 failed (`'too long' was accepted`, the boundary).
  M10 the reason echoes the url (the no-host reason + ``: {url}``). 3 failed,
      `the reason carried part of the url: ['3f2a9c1e', 'secretuuid',
      '/ping/']`.
  M11 no whitespace check. 3 failed (`'leading space' was accepted`).
  M12 no scheme check. 4 failed: ftp and no-scheme are still refused, for the
      WRONG reason (`'ftp scheme' was refused for the wrong reason: the url
      points at an address ...`), which is what the reason hints grade.
  M13 the port never read (``parts.port`` dropped). 3 failed, `'port out of
      range' was accepted`.
  M14 no ``_url_is_safe`` (unspecified and multicast literals). 5 failed.
  M15 ``set_deadman`` does not validate. 1 failed, `DID NOT RAISE <class
      'ValueError'>`.
  M16 the route does not check before writing. 15 failed: every route case
      (the `ValueError` from ``set_deadman`` surfaces as a 500, after the
      blocks before it were written) and the atomicity case.
  M17 the clear no longer wins (``body.clear_deadman_url or`` dropped). 1
      failed, a 422 where the clear was asked for.
  M18 the blank echo is validated (``or not body.deadman_url`` dropped). 1
      failed, a 422 for ``deadman_url: ""``.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

import astrodeck.alerting as alerting_mod
import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from _deadline import wait_until
from astrodeck.alerting import DEADMAN_URL_MAX, AlertDispatcher, deadman_url_problem
from astrodeck.config import AppConfig, ConfigStore
from astrodeck.events import EventBus
from astrodeck.flows.store import FlowStore

#: A healthchecks-shaped path: the PATH is the ping secret (#694), so a line
#: or a response that carries any of these pieces has leaked it.
_SECRET = "3f2a9c1e-0000-4abc-9def-secretuuid"
_SECRET_PIECES = ("3f2a9c1e", "secretuuid", "/ping/", "hc.example.org", "xn--",
                  "RUNSECRET", "user:pw")
_SECRET_URL = f"https://user:pw@hc.example.org:8443/ping/{_SECRET}?rid=RUNSECRET"
_GOOD = f"https://hc-ping.com/{_SECRET}"

# ------------------------------------------------------------------ #811


def _bare(cfg: AppConfig | None = None):
    """A dispatcher with no ``run()`` pipeline, whose bus is its own."""
    bus = EventBus(persist=False)
    disp = AlertDispatcher(bus, lambda: cfg if cfg is not None else AppConfig())
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200)))
    return disp, bus


def _warnings(sub) -> list[str]:
    out = []
    while not sub.empty():
        ev = sub.get_nowait()
        if ev.type == "log" and ev.data.get("level") == "warning":
            out.append(ev.data["message"])
    return out


@pytest.fixture
def fast_clock(monkeypatch):
    """The wall-clock cadence shrunk so a handful of ticks is milliseconds."""
    monkeypatch.setattr(alerting_mod, "DEADMAN_INTERVAL_S", 0.0)
    monkeypatch.setattr(alerting_mod, "_WALLCLOCK_TICK_S", 0.01)


async def _drive(disp: AlertDispatcher, enough) -> asyncio.Task:
    """Run the real wall-clock loop until ``enough()``, then stop it. Returns
    the finished task; fails if the loop died before it was asked to stop."""
    task = asyncio.create_task(disp._wallclock_loop())
    try:
        reached = await wait_until(enough, timeout_s=3.0, interval_s=0.01)
        assert not task.done(), f"the loop ended on its own: {task!r}"
        assert reached, "the loop did not keep ticking"
    finally:
        await disp.stop()
        await asyncio.wait_for(task, timeout=2.0)
    return task


async def test_a_dead_man_ping_that_raises_is_said_once_and_the_loop_keeps_going(
        fast_clock, monkeypatch):
    disp, bus = _bare()
    sub = bus.subscribe()
    calls: list[int] = []

    async def boom():
        calls.append(1)
        raise RuntimeError(f"the monitor object blew up: {_SECRET_URL}")

    monkeypatch.setattr(disp, "deadman_ping", boom)
    await _drive(disp, lambda: len(calls) >= 4)
    warnings = _warnings(sub)
    assert len(warnings) == 1, warnings
    said = warnings[0]
    assert "dead-man ping" in said and "RuntimeError" in said, said
    leaked = [p for p in _SECRET_PIECES if p in said]
    assert not leaked, f"the line carried the ping secret: {leaked}: {said}"
    await disp._client.aclose()


async def test_a_heartbeat_that_raises_is_said_once_and_the_loop_keeps_going(
        fast_clock, monkeypatch):
    disp, bus = _bare()
    sub = bus.subscribe()
    calls: list[int] = []

    async def boom(message):
        calls.append(1)
        raise KeyError(f"sink token SEKRET-TOKEN missing from {_SECRET_URL}")

    monkeypatch.setattr(disp, "emit_heartbeat", boom)
    await _drive(disp, lambda: len(calls) >= 4)
    warnings = _warnings(sub)
    assert len(warnings) == 1, warnings
    said = warnings[0]
    assert "heartbeat" in said and "KeyError" in said, said
    leaked = [p for p in (*_SECRET_PIECES, "SEKRET-TOKEN") if p in said]
    assert not leaked, f"the line carried a secret: {leaked}: {said}"
    await disp._client.aclose()


async def test_a_different_failure_is_said_again_and_a_repeat_is_not(
        fast_clock, monkeypatch):
    """One line per DISTINCT failure: A, A, B, B, A, ... is two lines, not
    five, and not one."""
    disp, bus = _bare()
    sub = bus.subscribe()
    errors = [RuntimeError("a"), RuntimeError("a"), KeyError("b"), KeyError("b"),
              RuntimeError("a")]
    calls: list[int] = []

    async def flaky():
        n = len(calls)
        calls.append(n)
        raise errors[n % len(errors)]

    monkeypatch.setattr(disp, "deadman_ping", flaky)
    await _drive(disp, lambda: len(calls) >= 2 * len(errors))
    warnings = _warnings(sub)
    assert len(warnings) == 2, warnings
    assert "RuntimeError" in warnings[0] and "KeyError" in warnings[1], warnings
    await disp._client.aclose()


async def test_a_stage_that_works_again_says_its_next_failure_afresh(
        fast_clock, monkeypatch):
    """The latch is 'no failure said yet', not 'never again': a heartbeat that
    fails, works, and fails again is two events, so the second night's trouble
    is not lost behind the first night's line."""
    disp, bus = _bare()
    sub = bus.subscribe()
    calls: list[int] = []

    async def alternates(message):
        n = len(calls)
        calls.append(n)
        if n % 2 == 0:
            raise ValueError("fails on every other tick")

    monkeypatch.setattr(disp, "emit_heartbeat", alternates)
    await _drive(disp, lambda: len(calls) >= 6)
    failed = (len(calls) + 1) // 2          # calls 0, 2, 4, ... raised
    warnings = _warnings(sub)
    assert failed >= 3 and len(warnings) == failed, (failed, warnings)
    await disp._client.aclose()


async def test_a_bus_that_cannot_take_the_line_does_not_end_the_loop(
        fast_clock, monkeypatch, caplog):
    """Saying the failure must not be able to kill the loop it reports on: a
    bus.log that raises would take the whole wall-clock task, and with it the
    dead-man ping, in the same silence this is meant to end. The line goes to
    the module's logger instead."""
    disp, bus = _bare()
    calls: list[int] = []

    async def boom():
        calls.append(1)
        raise RuntimeError(f"the monitor object blew up: {_SECRET_URL}")

    def broken(*args, **kwargs):
        raise OSError("the night log is gone")

    monkeypatch.setattr(disp, "deadman_ping", boom)
    monkeypatch.setattr(bus, "log", broken)
    with caplog.at_level(logging.WARNING, logger="astrodeck.alerting"):
        await _drive(disp, lambda: len(calls) >= 4)
    lines = [r.getMessage() for r in caplog.records if r.name == "astrodeck.alerting"]
    assert len(lines) == 1 and "RuntimeError" in lines[0], lines
    leaked = [p for p in _SECRET_PIECES if p in lines[0]]
    assert not leaked, f"the fallback line carried the ping secret: {leaked}"
    await disp._client.aclose()


async def test_a_pipelined_ping_task_that_dies_outside_its_own_guard_is_said():
    """Once ``run()`` has started the pipeline the ping runs on its OWN task,
    so what escapes it never reaches the wall-clock loop's guard at all: it
    sat on a task nobody awaits (asyncio's 'never retrieved' at shutdown, if
    the loop was still there to print it). ``get_config()`` is the case the
    ping's own try does not cover: it is the first line of the ping."""
    outcomes = iter([RuntimeError(f"config unreadable: {_SECRET_URL}"), None,
                     RuntimeError("config unreadable again"), None])

    def get_config():
        out = next(outcomes)
        if out is not None:
            raise out
        return AppConfig()                  # no deadman url: the ping returns

    bus = EventBus(persist=False)
    sub = bus.subscribe()
    disp = AlertDispatcher(bus, get_config)
    disp._outbox_ready = asyncio.Event()    # what run() sets: the pipeline is live

    async def ping_to_the_end():
        await disp.deadman_ping()
        task = disp._deadman_task
        assert task is not None
        await task                          # raised before the fix
        assert task.exception() is None

    await ping_to_the_end()                 # raises
    first = _warnings(sub)
    assert len(first) == 1, first
    assert "dead-man ping" in first[0] and "RuntimeError" in first[0], first
    leaked = [p for p in _SECRET_PIECES if p in first[0]]
    assert not leaked, f"the line carried the ping secret: {leaked}: {first[0]}"
    await ping_to_the_end()                 # works again: nothing said
    assert _warnings(sub) == []
    await ping_to_the_end()                 # fails again, after working: news
    again = _warnings(sub)
    assert len(again) == 1 and "RuntimeError" in again[0], again


# ------------------------------------------------------------------ #812

_BAD_URLS = {
    "non-numeric port": f"https://hc.example.org:abc/ping/{_SECRET}",
    "port out of range": f"https://hc.example.org:99999/ping/{_SECRET}",
    # In the MIDDLE, so the whitespace check does not take it: only building
    # the request refuses a control character.
    "control character": f"https://hc.example.org/ping/{_SECRET}\nx",
    "bad idna label": f"https://xn--/ping/{_SECRET}",
    "lone surrogate": f"https://hc.example.org/ping/{_SECRET}\ud800",
    "unclosed ipv6 literal": f"https://[fd00::1/ping/{_SECRET}",
    "ftp scheme": f"ftp://hc.example.org/ping/{_SECRET}",
    "no scheme": f"hc.example.org/ping/{_SECRET}",
    "no host": f"https:///ping/{_SECRET}",
    "unspecified address": f"http://0.0.0.0/ping/{_SECRET}",
    "multicast address": f"http://224.0.0.1/ping/{_SECRET}",
    "leading space": f" https://hc.example.org/ping/{_SECRET}",
    "trailing newline": f"https://hc.example.org/ping/{_SECRET}\n",
    "too long": "https://hc.example.org/ping/" + _SECRET * 100,
}

#: What each reason has to tell the owner, so a refusal that names the wrong
#: problem (a scheme blamed on the address, say) is not a pass.
_REASON_HINT = {
    "non-numeric port": "port",
    "port out of range": "port",
    "control character": "stray characters",
    "bad idna label": "stray characters",
    "lone surrogate": "stray characters",
    "unclosed ipv6 literal": "well formed",
    "ftp scheme": "http:// or https://",
    "no scheme": "http:// or https://",
    "no host": "no host name",
    "unspecified address": "address",
    "multicast address": "address",
    "leading space": "whitespace",
    "trailing newline": "whitespace",
    "too long": "longer than",
}
assert set(_REASON_HINT) == set(_BAD_URLS)

_GOOD_URLS = {
    "healthchecks": _GOOD,
    "lan monitor with a query": "http://192.168.1.20:3001/api/push/abc?status=up&msg=OK",
    "userinfo and port": _SECRET_URL,
    "ipv6 literal": "http://[fd00::1]:8080/ping",
}


@pytest.mark.parametrize("what", list(_BAD_URLS))
def test_a_url_the_ping_could_never_use_is_refused_without_echoing_it(what):
    problem = deadman_url_problem(_BAD_URLS[what])
    assert isinstance(problem, str) and problem, f"{what!r} was accepted"
    leaked = [p for p in _SECRET_PIECES if p in problem]
    assert not leaked, f"the reason carried part of the url: {leaked}: {problem}"
    assert _REASON_HINT[what] in problem, f"{what!r} was refused for the wrong reason: {problem}"


@pytest.mark.parametrize("what", list(_GOOD_URLS))
def test_a_url_the_ping_can_use_is_accepted(what):
    assert deadman_url_problem(_GOOD_URLS[what]) is None


def test_the_length_bound_is_exact():
    base = "https://hc.example.org/"
    at_bound = base + "a" * (DEADMAN_URL_MAX - len(base))
    assert len(at_bound) == DEADMAN_URL_MAX
    assert deadman_url_problem(at_bound) is None
    assert deadman_url_problem(at_bound + "a") is not None


@pytest.mark.parametrize("what", ["non-numeric port", "bad idna label",
                                   "lone surrogate", "control character"])
async def test_the_save_check_agrees_with_what_the_ping_cannot_send(what):
    """The two halves of #735/#812 say the same thing: a url the ping warns it
    SKIPS is one the save refuses, and a url the save takes is one the ping
    sends to (the accepted ones, below, with the real request on a mock)."""
    url = _BAD_URLS[what]
    disp, bus = _bare(AppConfig(deadman_url=url))
    sub = bus.subscribe()
    await disp.deadman_ping()
    assert any("SKIPPED" in w for w in _warnings(sub)), "the ping did not skip it"
    assert deadman_url_problem(url) is not None
    await disp._client.aclose()


@pytest.mark.parametrize("what", ["healthchecks", "lan monitor with a query"])
async def test_a_url_the_save_takes_is_one_the_ping_sends_to(what):
    url = _GOOD_URLS[what]
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(str(req.url))
        return httpx.Response(200)

    disp = AlertDispatcher(EventBus(persist=False), lambda: AppConfig(deadman_url=url))
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert deadman_url_problem(url) is None
    await disp.deadman_ping()
    assert hits == [url], hits
    assert disp.health()["deadman"]["healthy"] is True
    await disp._client.aclose()


def test_set_deadman_refuses_what_the_ping_could_not_use_and_stores_nothing(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_deadman(_GOOD)
    version = store.cfg().version
    for what, url in _BAD_URLS.items():
        with pytest.raises(ValueError) as refused:
            store.set_deadman(url)
        message = str(refused.value)
        leaked = [p for p in _SECRET_PIECES if p in message]
        assert not leaked, f"{what}: the error carried part of the url: {leaked}"
        assert store.cfg().deadman_url == _GOOD, f"{what}: the url was stored anyway"
        assert store.cfg().version == version, f"{what}: the version moved"
    assert store.reload().deadman_url == _GOOD
    store.set_deadman("")                       # the clear still works
    assert store.cfg().deadman_url == ""


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    with TestClient(app_module.create_app()) as c:
        c.config_store = store
        yield c


def _post(client, body: dict):
    """POST /api/config with the JSON ASCII-escaped, as a browser sends it: the
    test client's own encoder cannot carry a lone surrogate."""
    return client.post("/api/config", content=json.dumps(body),
                       headers={"Content-Type": "application/json"})


@pytest.mark.parametrize("what", list(_BAD_URLS))
def test_the_settings_route_answers_422_with_a_plain_reason_and_saves_nothing(client, what):
    assert _post(client, {"deadman_url": _GOOD}).status_code == 200
    r = _post(client, {"deadman_url": _BAD_URLS[what]})
    assert r.status_code == 422, (what, r.status_code, r.text)
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_deadman_url", detail
    assert detail["detail"].startswith("dead-man's-switch url not saved: "), detail
    assert _REASON_HINT[what] in detail["detail"], detail
    leaked = [p for p in _SECRET_PIECES if p in r.text]
    assert not leaked, f"the response carried part of the url: {leaked}: {r.text}"
    assert client.config_store.cfg().deadman_url == _GOOD
    assert client.config_store.reload().deadman_url == _GOOD


def test_a_refused_deadman_url_leaves_every_other_block_of_the_body_unwritten(client):
    """``_persist_config_patch`` writes block by block, so a refusal found at
    the deadman's turn would leave the blocks before it saved under an error
    the caller reads as 'nothing happened'."""
    sink = {"id": "n1", "kind": "ntfy", "url": "https://ntfy.sh/topic"}
    r = _post(client, {"alerts": [sink], "deadman_url": _BAD_URLS["non-numeric port"]})
    assert r.status_code == 422, r.text
    assert client.config_store.cfg().alerts == [], "the alerts block was written"
    assert client.config_store.cfg().deadman_url == ""
    # and the same body with a usable url is saved whole
    r = _post(client, {"alerts": [sink], "deadman_url": _GOOD})
    assert r.status_code == 200, r.text
    assert len(client.config_store.cfg().alerts) == 1
    assert client.config_store.cfg().deadman_url == _GOOD


def test_the_explicit_clear_is_not_held_up_by_an_unusable_url_in_the_same_body(client):
    """The clear wins over any url in the body (the destructive reading is
    the one typed on purpose), so there is nothing to validate."""
    _post(client, {"deadman_url": _GOOD})
    r = _post(client, {"clear_deadman_url": True,
                       "deadman_url": _BAD_URLS["non-numeric port"]})
    assert r.status_code == 200, r.text
    assert client.config_store.cfg().deadman_url == ""


def test_the_blank_echo_of_the_redacted_url_is_still_unchanged_and_not_validated(client):
    _post(client, {"deadman_url": _GOOD})
    r = _post(client, {"deadman_url": ""})
    assert r.status_code == 200, r.text
    assert client.config_store.cfg().deadman_url == _GOOD
