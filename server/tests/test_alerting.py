# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Outbound alerting tests (Batch 4b §1.8 / alerting.py).

Covers: state-change alerts are NEVER deduped while repetitive warnings ARE;
ntfy/webhook/telegram delivery via a mocked transport; a failed send is logged +
queued (never raised) and later flushed; the round-trip ``test`` sets
``verified`` only on a real 2xx; the dead-man's-switch pings its URL.

#694 and #606 part A are at the end of the file. MUTANTS RUN for them, each
from a byte backup, restored byte-identically (sha256 compared) with the
mutant text grepped out afterwards, and what each printed:

  M1 (#694), keep ``parts.path`` in ``_scrub_url``'s return
  (``f"{scheme}://{host}/{_PATH_WITHHELD}"`` -> ``...{host}{parts.path}"``).
  3 failed: test_a_dead_man_url_path_secret_reaches_no_log_line (both ids),
  `assert '<path withheld>' in "dead-man's-switch url unreachable
  (https://hc.example.org:8443/ping/3f2a9c1e-0000-4abc-9def-secretuuid/start):
  ConnectError ..."` (the real warning line, secret and all), and
  test_scrub_url_keeps_scheme_and_host_and_withholds_everything_after.

  M2 (#606), drop the 4xx fallback (``r.status_code not in _POST_REFUSED`` ->
  ``True``): the POST's refusal becomes the monitor's answer. 6 failed, the
  five ids of test_a_monitor_that_refuses_the_post_is_pinged_with_a_get_and_remembered
  (`assert ['POST', 'POST'] == ['POST', 'GET', 'GET']`) and the
  does-not-fix case.

  M3 (#606), the source's exception escapes (``except Exception`` narrowed to
  ``except ZeroDivisionError``, the effect of calling ``beacon_source``
  outside the try). 2 failed: test_a_beacon_that_cannot_be_built_never_stops_the_ping[raises]
  (`RuntimeError: the source blew up: lat 11.1111 lon 22.2222`, out of
  ``deadman_ping``) and test_a_beacon_that_recovers_is_sent_again.

  M4 (#606), no closed-vocabulary gate in the dispatcher (``reason = None if
  is_closed_vocabulary(text) else ...`` -> ``None if True``). 3 failed:
  test_text_outside_the_closed_vocabulary_is_never_sent, `AssertionError: the
  dead-man ping sent text outside the closed vocabulary: [('POST',
  'https://hc-ping.com/abc', b'lat 11.1111 lon 22.2222', 'text/plain')]`, and
  the not-a-string / empty ids of
  test_a_beacon_that_cannot_be_built_never_stops_the_ping
  (`AttributeError: 'int' object has no attribute 'encode'`; an empty body
  POSTed twice).

  M5 (#606), the GET-only verdict is not per url (``== hash(url)`` -> ``is not
  None``). 1 failed, test_a_get_only_verdict_belongs_to_the_url_that_gave_it:
  `AssertionError: a new url inherited the old url's GET-only verdict`.

  M6 (#606), remember GET-only even when the GET failed (``if 200 <= r.status_code
  < 400:`` -> ``if True:``). 1 failed,
  test_a_refusal_the_get_does_not_fix_is_the_monitors_and_is_not_remembered:
  `assert ['POST', 'GET', 'GET'] == ['POST', 'GET', 'POST', 'GET']`.

  M7 (#606), every 4xx/5xx is a refusal (``_POST_REFUSED = frozenset(range(400,
  600))``). 1 failed, test_a_server_error_on_the_post_is_not_taken_for_a_refusal:
  `assert ['POST', 'GET'] == ['POST']`.

  M8 (#606), the beacon warning is said on every ping (the latch ``if
  self._beacon_warned != reason`` -> ``if True``). 3 failed, the three ids of
  test_a_beacon_that_cannot_be_built_never_stops_the_ping: `assert (2 == 1)`.

  (M7 of test_w15_rig_beacon.py, the alphabet-not-grammar gate, also fails
  test_text_outside_the_closed_vocabulary_is_never_sent here.)

#735 and #736 (WP-142) are after those. MUTANTS RUN, same procedure, and what
each printed:

  M9 (#735), the arm catches nothing that escapes (``except Exception as e:``
  in ``_deadman_ping_now`` -> ``except ZeroDivisionError as e:``). 6 failed,
  the exception out of ``deadman_ping`` each time: the four ids of
  test_a_dead_man_url_httpx_cannot_build_a_request_for_warns_once_and_never_raises
  (`httpx.InvalidURL: Invalid port: 'abc'`, `httpx.InvalidURL: Invalid
  non-printable ASCII character in URL, '\\n' at position 62.`,
  `idna.core.IDNAError: Malformed A-label, no Punycode eligible content found`,
  `UnicodeEncodeError: 'utf-8' codec can't encode character '\\ud800' in
  position 0: surrogates not allowed`), the pipelined id (`httpx.InvalidURL:
  Invalid port: 'abc'`, out of the ping's own task) and
  test_an_unexpected_failure_inside_the_ping_is_said_once_and_never_escapes
  (`RuntimeError: the monitor object blew up: ...`).

  M10 (#735), the arm only knows a refused url (``except Exception as e:`` ->
  ``except (httpx.InvalidURL, ValueError) as e:``). 1 failed,
  test_an_unexpected_failure_inside_the_ping_is_said_once_and_never_escapes:
  `RuntimeError: the monitor object blew up: ...` out of ``deadman_ping``.

  M11 (#735), the latch is never stamped (``self._deadman_warned = url``
  dropped from the arm). 6 failed: the second ping says it again (`AssertionError:
  ["dead-man's-switch url is not one the HTTP client can build a request for
  (<url>): InvalidURL ... it is being SKIPPED ...", "..." ]`, four ids and the
  unexpected-failure id) and the badge stays green (`assert True is False` on
  ``health()["deadman"]["healthy"]``, the pipelined id).

  M12 (#735), the url is not scrubbed (``self._scrub_url(url)`` -> ``url`` in
  the refused branch). 4 failed, the four ids of the first test above:
  `AssertionError: the warning carried the ping secret: ['3f2a9c1e',
  'secretuuid', '/ping/']: dead-man's-switch url is not one the HTTP client
  can build a request for (https://hc.example.org:abc/ping/3f2a9c1e-0000-4abc-9def-secretuuid): InvalidURL ...`.

  M13 (#735), the exception's text rather than its type (``{type(e).__name__}``
  -> ``{e}`` in the generic branch). 1 failed,
  test_an_unexpected_failure_inside_the_ping_is_said_once_and_never_escapes:
  `AssertionError: the warning carried the ping secret: [... 'RUNSECRET',
  'QUERYSECRET', 'user:pw', 'pw@']: dead-man's-switch ping failed before it
  could be sent (https://hc.example.org:8443/<path withheld>): the monitor
  object blew up: https://user:pw@hc.example.org:8443/ping/...`.

  M14 (#736), httpx's logger left on (``_HTTP_CLIENT_LOGGERS = ("httpx",
  "httpcore")`` -> ``("httpcore",)``). 2 failed,
  test_a_process_logging_at_info_never_writes_the_dead_man_path
  (`AssertionError: the dead-man path reached a log at INFO: httpx: HTTP
  Request: GET http://127.0.0.1:56932/ping/3f2a9c1e-0000-4abc-9def-secretuuid
  "HTTP/1.1 200 OK"`) and test_httpcores_own_trace_stays_out_of_a_process_logging_at_debug
  (`http client loggers wrote at DEBUG: ['httpx']`).

  M15 (#736), httpcore's logger left on (``("httpx",)``). 1 failed,
  test_httpcores_own_trace_stays_out_of_a_process_logging_at_debug:
  `AssertionError: http client loggers wrote at DEBUG: ['httpcore.connection',
  'httpcore.http11']: httpcore.connection: connect_tcp.started host='127.0.0.1'
  port=61486 ...`.

  M16 (#736), the level too lenient (``setLevel(logging.WARNING)`` ->
  ``setLevel(logging.INFO)``). 2 failed, the two ids of M14, the same
  `httpx: HTTP Request: GET http://127.0.0.1:61616/ping/...secretuuid` line.

  M17 (#736), never applied (the ``_quiet_http_client_logs()`` call at import
  removed). 2 failed, the two ids of M14 (the DEBUG one with
  `['httpcore.connection', 'httpcore.http11', 'httpx']`).

  Both #736 tests first switch the logger ON and assert the capture then holds
  the secret path (and httpcore's trace): a harness that cannot see the line
  would pass whatever the production code did.

  Added in review: M9-M17 all failed, yet the wording of the two branches of
  the new arm still passed under both of these:

  M18 (#735), a refused url is said as an unforeseen failure (``if
  isinstance(e, (httpx.InvalidURL, ValueError)):`` -> ``if False:``). 4 failed,
  the four ids of
  test_a_dead_man_url_httpx_cannot_build_a_request_for_warns_once_and_never_raises:
  `assert 'SKIPPED' in "dead-man's-switch ping failed before it could be sent
  (https://xn--/<path withheld>): IDNAError ... no ping left, and this is not
  the monitor's doing"` (and the same for the other three urls).

  M19 (#735), an unforeseen failure is said as a url problem (the same line ->
  ``if True:``). 1 failed,
  test_an_unexpected_failure_inside_the_ping_is_said_once_and_never_escapes:
  `assert 'SKIPPED' not in "dead-man's-switch url is not one the HTTP client
  can build a request for (https://hc.example.org:8443/<path withheld>):
  RuntimeError ... it is being SKIPPED and no pings are being sent; check the
  port and any stray characters in it"`."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import httpx
import pytest

from _deadline import wait_until
from astrodeck import hub as hub_mod
from astrodeck.alerting import AlertDispatcher, AlertEvent
from astrodeck.config import AlertSink, AppConfig, redacted
from astrodeck.events import EventBus


def _dispatcher(cfg: AppConfig, handler, bus: EventBus | None = None):
    """Build a dispatcher whose httpx client uses a MockTransport ``handler``."""
    bus = bus if bus is not None else EventBus()
    disp = AlertDispatcher(bus, lambda: cfg)
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return disp, bus


# ------------------------------------------------------------------ delivery

async def test_ntfy_webhook_telegram_send_ok():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url)))
        return httpx.Response(200, text="ok")

    cfg = AppConfig(alerts=[
        AlertSink(id="n", kind="ntfy", url="https://ntfy.sh/topic",
                  events=["run_end"], min_level="warning"),
        AlertSink(id="w", kind="webhook", url="https://example/hook",
                  events=["run_end"], min_level="warning"),
        AlertSink(id="t", kind="telegram", token="BOT", chat_id="42",
                  events=["run_end"], min_level="warning"),
    ])
    disp, _bus = _dispatcher(cfg, handler)
    await disp._dispatch(AlertEvent("run_end", "error", "Run aborted: unsafe"))
    urls = [u for _, u in seen]
    assert "https://ntfy.sh/topic" in urls
    assert "https://example/hook" in urls
    assert any("api.telegram.org/botBOT/sendMessage" in u for u in urls)
    await disp._client.aclose()


async def test_min_level_filters_below_threshold():
    sent = []

    def handler(req):
        sent.append(str(req.url))
        return httpx.Response(200)

    cfg = AppConfig(alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y",
                                      events=["error"], min_level="error")])
    disp, _bus = _dispatcher(cfg, handler)
    # a warning is below the sink's min_level=error -> not sent
    await disp._dispatch(AlertEvent("error", "warning", "minor"))
    assert sent == []
    await disp._client.aclose()


# ------------------------------------------------------------------ dedupe

async def test_state_change_never_deduped():
    count = {"n": 0}

    def handler(req):
        count["n"] += 1
        return httpx.Response(200)

    cfg = AppConfig(alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y",
                                      events=["safety"], min_level="warning")])
    disp, _bus = _dispatcher(cfg, handler)
    ev = AlertEvent("safety", "error", "UNSAFE: cloud")
    await disp._dispatch(ev)
    await disp._dispatch(ev)        # identical safety event again
    assert count["n"] == 2          # never deduped (C1-18)
    await disp._client.aclose()


async def test_repeated_warning_is_deduped():
    count = {"n": 0}

    def handler(req):
        count["n"] += 1
        return httpx.Response(200)

    cfg = AppConfig(alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y",
                                      events=["warning"], min_level="warning")])
    disp, _bus = _dispatcher(cfg, handler)
    ev = AlertEvent("warning", "warning", "focuser slow")
    await disp._dispatch(ev)
    await disp._dispatch(ev)        # same warning within the window
    assert count["n"] == 1          # deduped
    await disp._client.aclose()


# ------------------------------------------------------------------ failure path

async def test_failed_send_is_logged_queued_then_flushed():
    state = {"fail": True}
    logs = []

    def handler(req):
        if state["fail"]:
            raise httpx.ConnectError("offline")
        return httpx.Response(200)

    cfg = AppConfig(alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y",
                                      events=["run_end"], min_level="warning")])
    disp, bus = _dispatcher(cfg, handler)
    q = bus.subscribe()
    # delivery fails -> logged + queued, NEVER raised
    await disp._dispatch(AlertEvent("run_end", "error", "Run aborted"))
    assert disp.undelivered_count == 1
    # a warning log + an alert(ok=False) event were published
    types = []
    while not q.empty():
        types.append(q.get_nowait().type)
    assert "log" in types and "alert" in types
    # connectivity returns -> retry flushes the queue
    state["fail"] = False
    await disp._retry_undelivered()
    assert disp.undelivered_count == 0
    await disp._client.aclose()


# ------------------------------------------------------------------ test() + deadman

async def test_round_trip_test_sets_verified_only_on_2xx():
    def ok_handler(req):
        return httpx.Response(200)

    cfg = AppConfig(alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y")])
    disp, _bus = _dispatcher(cfg, ok_handler)
    res = await disp.test("n")
    assert res["ok"] is True and res["verified"] is True
    assert cfg.alerts[0].verified is True
    await disp._client.aclose()

    def bad_handler(req):
        return httpx.Response(404)

    cfg2 = AppConfig(alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y",
                                       verified=True)])
    disp2, _b2 = _dispatcher(cfg2, bad_handler)
    res2 = await disp2.test("n")
    assert res2["ok"] is False
    assert res2["verified"] is False       # a 404 does NOT verify
    assert cfg2.alerts[0].verified is False
    await disp2._client.aclose()


async def test_test_unknown_sink():
    cfg = AppConfig(alerts=[])
    disp, _bus = _dispatcher(cfg, lambda req: httpx.Response(200))
    res = await disp.test("missing")
    assert res["ok"] is False and "no such" in res["error"]
    await disp._client.aclose()


async def test_deadman_ping_hits_url():
    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(200)

    cfg = AppConfig(deadman_url="https://hc-ping.com/abc")
    disp, _bus = _dispatcher(cfg, handler)
    await disp.deadman_ping()
    assert hits == ["https://hc-ping.com/abc"]
    await disp._client.aclose()


async def test_deadman_ping_no_url_is_noop():
    cfg = AppConfig(deadman_url="")
    disp, _bus = _dispatcher(cfg, lambda req: httpx.Response(200))
    await disp.deadman_ping()      # must not raise / must not need the client
    if disp._client:
        await disp._client.aclose()


# ----------------------------------------------- deadman: LAN allow + loud warn

async def test_deadman_allows_private_lan_host():
    """P0-3: a self-hosted Uptime-Kuma / healthchecks on 192.168.x.x is the
    user's OWN monitor — the deadman url MUST be allowed to be a private/LAN host
    (only the deadman gets allow_private; ntfy/webhook keep the SSRF block)."""
    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(200)

    cfg = AppConfig(deadman_url="http://192.168.1.50:3001/api/push/abc")
    disp, _bus = _dispatcher(cfg, handler)
    await disp.deadman_ping()
    assert hits == ["http://192.168.1.50:3001/api/push/abc"]
    await disp._client.aclose()


async def test_outbound_webhook_still_blocks_lan_host():
    """The SSRF block must REMAIN on outbound alert sinks — only the deadman is
    exempt. A webhook pointed at a LAN host is refused, not delivered."""
    sent = []

    def handler(req):
        sent.append(str(req.url))
        return httpx.Response(200)

    cfg = AppConfig(alerts=[AlertSink(id="w", kind="webhook",
                                      url="http://192.168.1.50/hook",
                                      events=["run_end"], min_level="warning")])
    disp, _bus = _dispatcher(cfg, handler)
    ok, err = await disp._send(cfg.alerts[0], AlertEvent("run_end", "error", "x"))
    assert ok is False
    assert "blocked" in (err or "")
    assert sent == []          # never left the box
    await disp._client.aclose()


async def test_deadman_blocked_url_warns_loudly_once():
    """P0-3: a deadman url that can never be a monitor (e.g. an unspecified
    0.0.0.0 target) must LOG A LOUD WARNING — never silently skip — so the user
    isn't lulled into a false sense of monitoring. The warning is one-shot per url."""
    cfg = AppConfig(deadman_url="http://0.0.0.0/ping")
    disp, bus = _dispatcher(cfg, lambda req: httpx.Response(200))
    q = bus.subscribe()
    await disp.deadman_ping()
    await disp.deadman_ping()       # second call: must NOT warn again (one-shot)
    warns = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log" and ev.data.get("level") == "warning":
            warns.append(ev.data.get("message", ""))
    assert len(warns) == 1
    assert "dead-man" in warns[0].lower() or "deadman" in warns[0].lower()
    await disp._client.aclose()


async def test_deadman_unreachable_warns_then_recovers():
    """An unreachable monitor warns once; once a ping succeeds the warn latch
    clears so a later outage re-warns (not permanently silenced)."""
    state = {"fail": True}

    def handler(req):
        if state["fail"]:
            raise httpx.ConnectError("no route to host")
        return httpx.Response(200)

    cfg = AppConfig(deadman_url="http://monitor.local/ping")
    disp, bus = _dispatcher(cfg, handler)
    q = bus.subscribe()
    await disp.deadman_ping()       # fails -> warns once
    await disp.deadman_ping()       # still failing -> no repeat warn
    warns = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log" and ev.data.get("level") == "warning":
            warns.append(ev.data["message"])
    assert len(warns) == 1
    # monitor comes back -> a healthy ping clears the latch.
    state["fail"] = False
    await disp.deadman_ping()
    assert disp._deadman_warned is None
    await disp._client.aclose()


async def test_deadman_url_scrubbed_in_warning_log():
    """A deadman url's userinfo + query (which can hold a ping secret) must be
    scrubbed out of the warning log line."""
    def handler(req):
        raise httpx.ConnectError("x")

    cfg = AppConfig(deadman_url="http://user:pw@monitor.local/ping?token=SEKRET")
    disp, bus = _dispatcher(cfg, handler)
    q = bus.subscribe()
    await disp.deadman_ping()
    msgs = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            msgs.append(ev.data.get("message", ""))
    blob = " ".join(msgs)
    assert "SEKRET" not in blob
    assert "user:pw" not in blob
    await disp._client.aclose()


# ----------------------------------------------- wall-clock deadman (P0-3 core)

async def test_wallclock_pings_deadman_through_a_pause():
    """THE P0-3 fix: the deadman keeps pinging from the WALL-CLOCK task even when
    the engine produces NO frames (a legitimate multi-hour safety pause). We drive
    the wall-clock loop directly with the interval shrunk so the test is fast, and
    assert it pings repeatedly without any engine/frame activity."""
    import astrodeck.alerting as alerting_mod

    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(200)

    cfg = AppConfig(deadman_url="https://hc-ping.com/abc")
    disp, _bus = _dispatcher(cfg, handler)

    # Shrink the wall-clock cadence so several "intervals" elapse in a tick.
    monkey_interval = 0.0   # every wake is "due"
    monkey_tick = 0.01
    orig_i, orig_t = alerting_mod.DEADMAN_INTERVAL_S, alerting_mod._WALLCLOCK_TICK_S
    alerting_mod.DEADMAN_INTERVAL_S = monkey_interval
    alerting_mod._WALLCLOCK_TICK_S = monkey_tick
    try:
        task = asyncio.create_task(disp._wallclock_loop())
        # Let it tick several times with ZERO frame-loop involvement (the engine
        # never runs here — this is purely the wall-clock driver). A wall-clock
        # deadline (#610), not a round count: 50 x sleep(0.01) is 0.5 s on
        # Linux but 0.8 s on Windows (each sleep rounds up to the 15.6 ms
        # timer), so a fixed round count gives the two platforms different
        # real patience for the same wait.
        await wait_until(lambda: len(hits) >= 3, timeout_s=2.0,
                         interval_s=0.01)
        await disp.stop()
        await task
    finally:
        alerting_mod.DEADMAN_INTERVAL_S = orig_i
        alerting_mod._WALLCLOCK_TICK_S = orig_t
    assert len(hits) >= 3, f"wall-clock deadman did not keep pinging: {hits}"
    assert all(u == "https://hc-ping.com/abc" for u in hits)
    await disp._client.aclose()


# ------------------------------------------------------ SMTP fields + redaction

def test_redacted_marks_token_configured_and_blanks_secret():
    cfg = AppConfig(alerts=[
        AlertSink(id="d", kind="discord", token="https://discord.com/api/webhooks/1/xyz"),
        AlertSink(id="e", kind="email", smtp_host="smtp.x", smtp_from="a@x",
                  smtp_to="b@x", token="pw"),
        AlertSink(id="n", kind="ntfy", url="https://ntfy.sh/t"),
    ])
    out = redacted(cfg)
    by = {s["id"]: s for s in out["alerts"]}
    assert by["d"]["token"] == "" and by["d"]["token_configured"] is True
    assert by["e"]["token"] == "" and by["e"]["token_configured"] is True
    # non-secret SMTP fields stay visible
    assert by["e"]["smtp_host"] == "smtp.x" and by["e"]["smtp_to"] == "b@x"
    assert by["n"]["token_configured"] is False  # no secret set


# ------------------------------------------------------ discord / slack / email

async def test_discord_and_slack_post_to_token_webhook():
    seen = []
    def handler(req):
        seen.append((str(req.url), req.read().decode()))
        return httpx.Response(204)
    cfg = AppConfig(alerts=[
        AlertSink(id="d", kind="discord",
                  token="https://discord.com/api/webhooks/1/xyz",
                  events=["run_end"], min_level="warning"),
        AlertSink(id="s", kind="slack",
                  token="https://hooks.slack.com/services/T/B/xyz",
                  events=["run_end"], min_level="warning"),
    ])
    disp, _bus = _dispatcher(cfg, handler)
    await disp._dispatch(AlertEvent("run_end", "error", "Run aborted"))
    urls = [u for u, _ in seen]
    assert any("discord.com/api/webhooks" in u for u in urls)
    assert any("hooks.slack.com/services" in u for u in urls)
    assert any('"content"' in b for _, b in seen)  # discord shape
    assert any('"text"' in b for _, b in seen)     # slack shape
    await disp._client.aclose()


async def test_discord_slack_block_internal_host():
    disp, _bus = _dispatcher(AppConfig(), lambda r: httpx.Response(204))
    ok, err = await disp._send(
        AlertSink(id="d", kind="discord", token="http://192.168.1.5/hook"),
        AlertEvent("run_end", "error", "x"))
    assert ok is False and "blocked" in (err or "")
    await disp._client.aclose()


async def test_email_missing_fields_is_soft_error():
    disp, _bus = _dispatcher(AppConfig(), lambda r: httpx.Response(200))
    ok, err = await disp._send(
        AlertSink(id="e", kind="email", smtp_host="", smtp_from="", smtp_to=""),
        AlertEvent("run_end", "error", "x"))
    assert ok is False and "email needs" in (err or "")  # never raised
    await disp._client.aclose()


async def test_email_send_ok_via_monkeypatched_smtp(monkeypatch):
    sent = {}
    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent["addr"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, msg, to_addrs=None): sent["to"] = to_addrs
    import astrodeck.alerting as A
    monkeypatch.setattr(A.smtplib, "SMTP", FakeSMTP) if hasattr(A, "smtplib") else None
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    cfg = AppConfig(alerts=[AlertSink(id="e", kind="email", smtp_host="smtp.x",
                    smtp_port=587, smtp_user="a@x", token="pw",
                    smtp_from="a@x", smtp_to="b@x, c@x")])
    disp, _bus = _dispatcher(cfg, lambda r: httpx.Response(200))
    ok, err = await disp._send(cfg.alerts[0], AlertEvent("test", "info", "hi"))
    assert ok is True and err is None
    assert sent["addr"] == ("smtp.x", 587) and sent["tls"] is True
    assert sent["login"] == ("a@x", "pw") and sent["to"] == ["b@x", "c@x"]
    await disp._client.aclose()


# ------------------------------------------------------------------------ health

async def test_health_reports_queue_and_deadman():
    cfg = AppConfig(deadman_url="https://hc-ping.com/abc",
                    alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y",
                                      events=["run_end"], min_level="warning")])
    disp, _bus = _dispatcher(cfg, lambda r: httpx.ConnectError("offline"))
    await disp._dispatch(AlertEvent("run_end", "error", "boom"))  # fails -> queued
    h = disp.health()
    assert h["undelivered"] == 1 and h["undelivered_by_sink"]["n"] == 1
    assert h["deadman"]["configured"] is True
    assert h["deadman"]["healthy"] is True   # not yet warned
    await disp._client.aclose()


# ----------------------------------------------- #694: the path is the ping secret

#: A healthchecks-shaped url: the PATH is the secret, and so is the query.
SECRET_URL = ("https://user:pw@hc.example.org:8443/ping/3f2a9c1e-0000-4abc-9def-secretuuid"
              "/start?rid=RUNSECRET&token=QUERYSECRET")
SECRET_PIECES = ("3f2a9c1e", "secretuuid", "/ping/", "/start", "RUNSECRET", "QUERYSECRET",
                 "user:pw", "pw@")


def _said_everywhere(bus: EventBus, sub, night_dir) -> tuple[str, list[str], str]:
    """Every place a log line the dispatcher wrote can be READ: the live
    subscription, the ring /api/logs serves (both of its views), and the
    durable night file. Returns the lot as one blob, the warnings on their own
    (so a caller can assert the check had something to check), and the night
    file's text alone."""
    warnings: list[str] = []
    blob: list[str] = []
    while not sub.empty():
        ev = sub.get_nowait()
        blob.append(json.dumps(ev.to_json()))
        if ev.type == "log" and ev.data.get("level") == "warning":
            warnings.append(ev.data.get("message", ""))
    blob.append(json.dumps(bus.log_history))
    blob.append(json.dumps(bus.log_history_unflagged))
    night = "\n".join(f.read_text(encoding="utf-8") for f in sorted(night_dir.glob("*.jsonl")))
    blob.append(night)
    return "\n".join(blob), warnings, night


@pytest.mark.parametrize("failure", ["unreachable", "http-404"])
async def test_a_dead_man_url_path_secret_reaches_no_log_line(monkeypatch, tmp_path, failure):
    """#694: ``_scrub_url`` kept the PATH, and for a healthchecks-style url the
    path is the ping secret. Whoever holds it can ping the check as healthy or
    pause it, which silences the alert set up for a dead rig. Both warning
    lines carry the scrubbed url, and a log line is read by anyone who can read
    logs: the night file, the ring, every sink that forwards a warning."""
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)

    def handler(req):
        if failure == "unreachable":
            raise httpx.ConnectError("no route")
        return httpx.Response(404)

    # persist=True said outright: the default follows ASTRODECK_LOG_PERSIST,
    # and with the night file off there would be nothing to read back.
    disp, bus = _dispatcher(AppConfig(deadman_url=SECRET_URL), handler,
                            EventBus(persist=True))
    sub = bus.subscribe()
    await disp.deadman_ping()
    blob, warnings, night = _said_everywhere(bus, sub, tmp_path / "logs")
    await disp._client.aclose()

    # The check is only worth anything if the line it checks WAS written, in
    # all three places: a harness that cannot reach the branch passes anything.
    assert len(warnings) == 1, warnings
    assert "dead-man" in warnings[0] and "hc.example.org:8443" in warnings[0], warnings
    assert "<path withheld>" in warnings[0], warnings
    assert "hc.example.org" in night, "the warning never reached the night file"
    leaked = [p for p in SECRET_PIECES if p in blob]
    assert not leaked, f"the ping secret reached the log: {leaked}"


def test_scrub_url_keeps_scheme_and_host_and_withholds_everything_after():
    scrub = AlertDispatcher._scrub_url
    assert scrub("https://hc-ping.com/abc") == "https://hc-ping.com/<path withheld>"
    assert scrub("http://192.168.1.50:3001/api/push/abc?status=up&msg=OK") == \
        "http://192.168.1.50:3001/<path withheld>"
    assert scrub("https://u:p@host.example/x#frag") == "https://host.example/<path withheld>"


@pytest.mark.parametrize("bad", ["http://host:abc/secret", "http://host:99999/secret",
                                 "not a url secret", "", "://secret"])
def test_scrub_url_never_raises_and_never_echoes_what_it_cannot_parse(bad):
    out = AlertDispatcher._scrub_url(bad)
    assert "secret" not in out, out


# ----------------------------------------------- #606 part A: the beacon on the ping

BEACON = "v=1 state=running frames=3 level=none up=60 boot=normal"
URL = "https://hc-ping.com/abc"


def _recorder(post_status=200, get_status=200):
    """A handler that records (method, url, body, content-type) and answers
    the POST and the GET with their own statuses."""
    seen: list[tuple[str, str, bytes, str | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), req.content, req.headers.get("content-type")))
        return httpx.Response(post_status if req.method == "POST" else get_status)

    return seen, handler


def _warnings(sub) -> list[str]:
    out = []
    while not sub.empty():
        ev = sub.get_nowait()
        if ev.type == "log" and ev.data.get("level") == "warning":
            out.append(ev.data["message"])
    return out


async def test_the_dead_man_ping_carries_the_beacon_as_a_post_body():
    seen, handler = _recorder()
    disp, bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    disp.beacon_source = lambda: BEACON
    await disp.deadman_ping()
    assert seen == [("POST", URL, BEACON.encode(), "text/plain")], seen
    assert disp.health()["deadman"]["last_ok_age_s"] is not None     # the monitor ACCEPTED it
    await disp._client.aclose()


async def test_with_no_beacon_source_the_ping_is_the_plain_get_it_always_was():
    seen, handler = _recorder()
    disp, _bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    assert disp.beacon_source is None
    await disp.deadman_ping()
    assert [m for m, *_ in seen] == ["GET"], seen
    await disp._client.aclose()


@pytest.mark.parametrize("refusal", [405, 404, 400, 415, 501])
async def test_a_monitor_that_refuses_the_post_is_pinged_with_a_get_and_remembered(refusal):
    """A GET-only monitor must keep working: the beacon may never turn a
    working dead-man into a failing one. 405 is the textbook answer; 404 is
    what an Express route registered for GET alone returns to a POST (Uptime
    Kuma's push route on versions before it took any method), and the owner
    who set that up would otherwise be told their monitor does not exist."""
    seen, handler = _recorder(post_status=refusal)
    disp, bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    sub = bus.subscribe()
    disp.beacon_source = lambda: BEACON
    await disp.deadman_ping()
    await disp.deadman_ping()
    # POST refused, the retry as GET, then GET alone: the refusal is remembered.
    assert [m for m, *_ in seen] == ["POST", "GET", "GET"], seen
    assert _warnings(sub) == [], "a refused POST that the GET fixed is not a warning"
    assert disp.health()["deadman"]["last_ok_age_s"] is not None
    assert disp._deadman_warned is None
    await disp._client.aclose()


async def test_a_refusal_the_get_does_not_fix_is_the_monitors_and_is_not_remembered():
    """POST 404 and GET 404: the check really is gone. That is the existing
    warning (once), carrying the GET's status, and the next tick asks again."""
    seen, handler = _recorder(post_status=404, get_status=404)
    disp, bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    sub = bus.subscribe()
    disp.beacon_source = lambda: BEACON
    await disp.deadman_ping()
    await disp.deadman_ping()
    assert [m for m, *_ in seen] == ["POST", "GET", "POST", "GET"], seen
    warns = _warnings(sub)
    assert len(warns) == 1 and "HTTP 404" in warns[0], warns
    assert disp.health()["deadman"]["last_ok_age_s"] is None
    await disp._client.aclose()


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
async def test_a_server_error_on_the_post_is_not_taken_for_a_refusal(status):
    """A 500 is the monitor having a bad minute, not a verdict on the method:
    remembering GET-only after it would drop the beacon until the next restart.
    408 and 429 are the monitor's load, the two 4xx the refusal set leaves out
    (verifier mutant X-M1, ``- {408, 429}`` dropped from ``_POST_REFUSED``,
    survived until these ids: `assert ['POST', 'GET'] == ['POST']`)."""
    seen, handler = _recorder(post_status=status)
    disp, bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    sub = bus.subscribe()
    disp.beacon_source = lambda: BEACON
    await disp.deadman_ping()
    assert [m for m, *_ in seen] == ["POST"], seen
    warns = _warnings(sub)
    assert len(warns) == 1 and f"HTTP {status}" in warns[0], warns
    await disp._client.aclose()


async def test_a_get_only_verdict_belongs_to_the_url_that_gave_it():
    seen, handler = _recorder(post_status=405)
    cfg = AppConfig(deadman_url=URL)
    disp, _bus = _dispatcher(cfg, handler)
    disp.beacon_source = lambda: BEACON
    await disp.deadman_ping()                               # POST, 405, GET: remembered
    cfg.deadman_url = "https://other.example.org/xyz"       # the owner pastes a new monitor
    seen.clear()
    await disp.deadman_ping()
    assert seen[0][0] == "POST", "a new url inherited the old url's GET-only verdict"
    await disp._client.aclose()


@pytest.mark.parametrize("how", ["raises", "not-a-string", "empty"])
async def test_a_beacon_that_cannot_be_built_never_stops_the_ping(how):
    """A beacon bug must not cost the one thing the ping exists for."""
    def source():
        if how == "raises":
            raise RuntimeError("the source blew up: lat 11.1111 lon 22.2222")
        return 42 if how == "not-a-string" else ""

    seen, handler = _recorder()
    disp, bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    sub = bus.subscribe()
    disp.beacon_source = source
    await disp.deadman_ping()
    await disp.deadman_ping()
    assert [m for m, *_ in seen] == ["GET", "GET"], seen        # still pinged, plain
    assert disp.health()["deadman"]["last_ok_age_s"] is not None
    # Said once, not silently: an owner whose monitor never shows a status
    # would otherwise never learn why. The exception's TEXT is not logged.
    warns = _warnings(sub)
    assert len(warns) == 1 and "beacon" in warns[0], warns
    assert "11.1111" not in warns[0] and "blew up" not in warns[0], warns
    await disp._client.aclose()


async def test_text_outside_the_closed_vocabulary_is_never_sent():
    """The dispatcher is the door the text leaves by, so it checks the text
    itself rather than trusting whoever set ``beacon_source``."""
    seen, handler = _recorder()
    disp, bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    sub = bus.subscribe()
    # lower-case letters and digits only: a character-class check would pass it
    hostile = "lat 11.1111 lon 22.2222"
    disp.beacon_source = lambda: hostile
    await disp.deadman_ping()
    assert all(hostile.encode() not in body for _m, _u, body, _c in seen), (
        f"the dead-man ping sent text outside the closed vocabulary: {seen}")
    assert [m for m, *_ in seen] == ["GET"], seen
    warns = _warnings(sub)
    assert len(warns) == 1 and "11.1111" not in warns[0], warns
    await disp._client.aclose()


async def test_a_beacon_that_recovers_is_sent_again():
    state = {"good": False}

    def source():
        if not state["good"]:
            raise RuntimeError("x")
        return BEACON

    seen, handler = _recorder()
    disp, bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    sub = bus.subscribe()
    disp.beacon_source = source
    await disp.deadman_ping()
    state["good"] = True
    await disp.deadman_ping()
    assert [m for m, *_ in seen] == ["GET", "POST"], seen
    assert len(_warnings(sub)) == 1
    state["good"] = False                  # a later failure is said again
    await disp.deadman_ping()
    assert len(_warnings(sub)) == 1
    await disp._client.aclose()


async def test_the_pipelined_ping_posts_the_beacon_too():
    """While the outbox pipeline is live the ping runs on its own task; the
    beacon rides that path as well, not only the inline one the tests above
    build."""
    seen, handler = _recorder()
    disp, _bus = _dispatcher(AppConfig(deadman_url=URL), handler)
    disp.beacon_source = lambda: BEACON
    disp._outbox_ready = asyncio.Event()          # what run() sets: the pipeline is live
    await disp.deadman_ping()
    assert await wait_until(lambda: len(seen) >= 1, timeout_s=2.0, interval_s=0.01)
    await disp._deadman_task
    assert seen[0][0] == "POST" and seen[0][2] == BEACON.encode(), seen
    await disp._client.aclose()


# ----------------------------------------------- #735: a url httpx will not build a request for

#: Each of these is one httpx refuses while it BUILDS the request, before any
#: socket exists, and none is an ``httpx.HTTPError``: ``InvalidURL`` derives
#: from ``Exception``, and the IDNA and encoding failures are ``ValueError``s.
#: ``_url_is_safe`` passes all four (it reads the scheme and the host, never
#: the port or the encoding), so each reaches the client. Every one carries
#: the same path secret, so a warning that echoed the url would show it.
_PATH_SECRET = "3f2a9c1e-0000-4abc-9def-secretuuid"
_UNBUILDABLE = {
    "non-numeric port": f"https://hc.example.org:abc/ping/{_PATH_SECRET}",
    "control character": f"https://hc.example.org/ping/{_PATH_SECRET}\n",
    "bad idna label": f"https://xn--/ping/{_PATH_SECRET}",
    "lone surrogate": f"https://hc.example.org/ping/{_PATH_SECRET}\ud800",
}


@pytest.mark.parametrize("what", list(_UNBUILDABLE))
async def test_a_dead_man_url_httpx_cannot_build_a_request_for_warns_once_and_never_raises(what):
    """#735: ``InvalidURL`` escaped ``except (httpx.HTTPError, OSError)``, so
    ``_deadman_warned`` stayed None and nothing was ever said: an owner who
    pasted a url with a typo in the port believed the rig was watched, the
    settings badge read 'waiting' forever with ``healthy`` true, and the
    wall-clock loop's own ``except Exception: pass`` ate the error. Now the
    ping says so ONCE, with the scrubbed url and the exception's type, marks
    the dead-man unhealthy, never raises, and recovers when the url is fixed."""
    url = _UNBUILDABLE[what]
    with pytest.raises(Exception) as refused:
        httpx.Request("GET", url)           # the precondition: httpx itself refuses it
    assert not isinstance(refused.value, httpx.HTTPError)

    seen, handler = _recorder()
    cfg = AppConfig(deadman_url=url)
    disp, bus = _dispatcher(cfg, handler)
    sub = bus.subscribe()
    await disp.deadman_ping()               # before the fix this raised
    await disp.deadman_ping()               # a second ping must not say it again
    warnings = _warnings(sub)
    assert len(warnings) == 1, warnings
    said = warnings[0]
    leaked = [p for p in ("3f2a9c1e", "secretuuid", "/ping/") if p in said]
    assert not leaked, f"the warning carried the ping secret: {leaked}: {said}"
    assert "dead-man" in said and type(refused.value).__name__ in said, said
    # The branch that tells the owner what to fix: a url httpx refuses is NOT pinged,
    # and the line says so, where a failure nobody foresaw must not claim it is the url.
    assert "SKIPPED" in said, said
    assert "<path withheld>" in said or "<url>" in said, said
    assert seen == [], "a ping left for a url that cannot be built"
    dm = disp.health()["deadman"]
    assert dm["configured"] is True and dm["healthy"] is False, dm
    assert dm["last_ok_age_s"] is None, dm

    cfg.deadman_url = URL                   # the owner fixes the url
    await disp.deadman_ping()
    dm = disp.health()["deadman"]
    assert dm["healthy"] is True and dm["last_ok_age_s"] is not None, dm
    assert [m for m, *_ in seen] == ["GET"], seen
    await disp._client.aclose()


async def test_the_pipelined_ping_of_an_unbuildable_url_warns_instead_of_raising_into_its_task():
    """While the outbox pipeline is live the ping runs on its own task, where
    the exception sat unretrieved: nothing awaited it, so nothing ever read it
    and the only trace was asyncio's 'exception was never retrieved' at
    shutdown, if the loop was still around to print it."""
    disp, bus = _dispatcher(AppConfig(deadman_url=_UNBUILDABLE["non-numeric port"]),
                            lambda req: httpx.Response(200))
    sub = bus.subscribe()
    disp._outbox_ready = asyncio.Event()      # what run() sets: the pipeline is live
    await disp.deadman_ping()
    task = disp._deadman_task
    assert task is not None
    await task                                # raised InvalidURL before the fix
    assert task.exception() is None
    warnings = _warnings(sub)
    assert len(warnings) == 1 and "dead-man" in warnings[0], warnings
    assert disp.health()["deadman"]["healthy"] is False
    await disp._client.aclose()


async def test_an_unexpected_failure_inside_the_ping_is_said_once_and_never_escapes(monkeypatch):
    """The ping is the one thing this exists for, so nothing it can hit may
    leave it silently. A failure that is neither a transport error nor a url
    httpx refuses is still said once, by type (the text is whatever raised
    it, and here it carries the url)."""
    disp, bus = _dispatcher(AppConfig(deadman_url=SECRET_URL), lambda req: httpx.Response(200))
    sub = bus.subscribe()

    async def boom(client, url):
        raise RuntimeError(f"the monitor object blew up: {url}")

    monkeypatch.setattr(disp, "_send_deadman", boom)
    await disp.deadman_ping()
    await disp.deadman_ping()
    warnings = _warnings(sub)
    assert len(warnings) == 1, warnings
    leaked = [p for p in SECRET_PIECES if p in warnings[0]]
    assert not leaked, f"the warning carried the ping secret: {leaked}: {warnings[0]}"
    assert "dead-man" in warnings[0] and "RuntimeError" in warnings[0], warnings
    assert "SKIPPED" not in warnings[0], warnings   # not a url problem, and not said as one
    assert disp.health()["deadman"]["healthy"] is False
    await disp._client.aclose()


# ----------------------------------------------- #736: httpx's own logger and the ping secret

_LOOPBACK_PATH = f"/ping/{_PATH_SECRET}"


@contextlib.asynccontextmanager
async def _loopback_monitor():
    """A real listening socket that answers 200 to whatever asks. The ping has
    to leave through httpx's real transport and httpcore: ``MockTransport``
    skips httpcore altogether, so its logger would never be exercised."""
    async def serve(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def _ping_a_loopback_monitor(caplog, level) -> list[logging.LogRecord]:
    """One real dead-man ping while the ROOT logger is at ``level``, which is
    what ``logging.basicConfig(level=...)`` does to a process. Returns every
    record the capture saw."""
    caplog.clear()
    caplog.set_level(level)
    async with _loopback_monitor() as port:
        disp = AlertDispatcher(
            EventBus(),
            lambda: AppConfig(deadman_url=f"http://127.0.0.1:{port}{_LOOPBACK_PATH}"))
        disp._client = httpx.AsyncClient(trust_env=False, timeout=5.0)
        await disp.deadman_ping()
        assert disp.health()["deadman"]["last_ok_age_s"] is not None, \
            "the ping never reached the monitor, so there is nothing to check"
        await disp._client.aclose()
    return list(caplog.records)


def _said_in(records: list[logging.LogRecord]) -> str:
    return "\n".join(f"{r.name}: {r.getMessage()} {r.args!r}" for r in records)


async def test_a_process_logging_at_info_never_writes_the_dead_man_path(caplog):
    """#736: httpx writes ``HTTP Request: GET <the whole url> "HTTP/1.1 200
    OK"`` at INFO on the ``httpx`` logger. For a healthchecks-style monitor
    the path IS the ping secret, and whoever holds it can ping the check as
    healthy or pause it (#694). Nothing configures a handler today, so nothing
    is written; a ``logging.basicConfig(level=INFO)``, a debug flag or a
    wrapper that captures root logs would put it in stderr and every log file.
    The same line carries a Telegram bot token or a Slack/Discord webhook
    path for every other sink, so the cure is on the logger, not the ping."""
    logger = logging.getLogger("httpx")
    prior = logger.level
    logger.setLevel(logging.INFO)
    try:
        control = await _ping_a_loopback_monitor(caplog, logging.INFO)
    finally:
        logger.setLevel(prior)
    # A check is only worth anything if its harness can SEE the line: with
    # httpx's logger switched on, the capture must hold the secret path.
    assert any(_LOOPBACK_PATH in r.getMessage() for r in control), _said_in(control)

    records = await _ping_a_loopback_monitor(caplog, logging.INFO)
    said = _said_in(records)
    leaked = [p for p in (_PATH_SECRET, _LOOPBACK_PATH) if p in said]
    assert not leaked, f"the dead-man path reached a log at INFO: {said}"
    assert not [r for r in records if r.name == "httpx"], said


async def test_httpcores_own_trace_stays_out_of_a_process_logging_at_debug(caplog):
    """The same cure for ``httpcore``: its DEBUG trace names the host and port
    of every connection it opens (and any later version may add more), so it
    stays at WARNING with httpx's. At root DEBUG nothing from either may be
    written, and the secret path is in no record at all."""
    logger = logging.getLogger("httpcore")
    prior = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        control = await _ping_a_loopback_monitor(caplog, logging.DEBUG)
    finally:
        logger.setLevel(prior)
    assert any(r.name.startswith("httpcore") for r in control), _said_in(control)

    records = await _ping_a_loopback_monitor(caplog, logging.DEBUG)
    said = _said_in(records)
    noisy = sorted({r.name for r in records if r.name.split(".")[0] in ("httpx", "httpcore")})
    assert not noisy, f"http client loggers wrote at DEBUG: {noisy}: {said}"
    assert _PATH_SECRET not in said, said
