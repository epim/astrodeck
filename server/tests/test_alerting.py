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
  test_text_outside_the_closed_vocabulary_is_never_sent here.)"""
from __future__ import annotations

import asyncio
import json

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
