"""Outbound alerting tests (Batch 4b §1.8 / alerting.py).

Covers: state-change alerts are NEVER deduped while repetitive warnings ARE;
ntfy/webhook/telegram delivery via a mocked transport; a failed send is logged +
queued (never raised) and later flushed; the round-trip ``test`` sets
``verified`` only on a real 2xx; the dead-man's-switch pings its URL."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from astrodeck.alerting import AlertDispatcher, AlertEvent
from astrodeck.config import AlertSink, AppConfig, redacted
from astrodeck.events import EventBus


def _dispatcher(cfg: AppConfig, handler):
    """Build a dispatcher whose httpx client uses a MockTransport ``handler``."""
    bus = EventBus()
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
        # never runs here — this is purely the wall-clock driver).
        for _ in range(50):
            if len(hits) >= 3:
                break
            await asyncio.sleep(0.01)
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
