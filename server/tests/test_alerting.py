"""Outbound alerting tests (Batch 4b §1.8 / alerting.py).

Covers: state-change alerts are NEVER deduped while repetitive warnings ARE;
ntfy/webhook/telegram delivery via a mocked transport; a failed send is logged +
queued (never raised) and later flushed; the round-trip ``test`` sets
``verified`` only on a real 2xx; the dead-man's-switch pings its URL."""
from __future__ import annotations

import httpx
import pytest

from astrodeck.alerting import AlertDispatcher, AlertEvent
from astrodeck.config import AlertSink, AppConfig
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
