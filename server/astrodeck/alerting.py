"""Outbound push/ntfy alerting (Batch 4b §1.8).

A single long-running bus subscriber (started in the app lifespan) that maps the
*unattended-anxiety* event set — run started, run ended, safety transition,
errors, heartbeat, reconnect attempt — onto the user's configured
:class:`~astrodeck.config.AlertSink` channels (ntfy / webhook / Telegram).

The hard-won rules from the adversarial UX critiques are encoded here:

* **Never dedupe a state-change alert** (run_end / safety / reconnect): the alert
  that matters most must always go out (C1-18). Only repetitive *warning* logs are
  deduped, within a short window.
* **Fail soft, never raise** (C1-16/C2-10): a delivery failure is logged via
  ``bus.log("warning", …)`` and the event is enqueued to a persistent undelivered
  retry queue; it is *never* allowed to propagate and kill the subscriber loop.
* **A real round-trip test** sets ``sink.verified`` only on a genuine 2xx — a POST
  that 404s does not count as "verified" (C1-16).
* **External dead-man's-switch** (C2-9): :meth:`deadman_ping` GETs the user's
  healthchecks-style URL each frame; *its absence* is what pages them.

One :class:`httpx.AsyncClient` is reused for the dispatcher's lifetime.
"""
from __future__ import annotations

import asyncio
import ipaddress
import time
from collections import OrderedDict
from collections import deque
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from .events import bus

# State-change event types are NEVER deduped (C1-18). These are subscribed by
# event *type*, not severity, so they also bypass the per-sink min_level gate
# (a default warning sink must still receive an info-level run_start/run_end).
_NEVER_DEDUPE = {"run_start", "run_end", "safety", "reconnect"}
# A repeated warning with the same dedupe-key inside this window is suppressed.
_DEDUPE_WINDOW_S = 60.0
# Bounded undelivered queue (oldest dropped when full so a dead endpoint can't
# grow memory without bound on an all-night run).
_UNDELIVERED_MAX = 200
# Cap the dedupe map so variable-text warning/error logs can't grow it without
# bound on an all-night run (mirrors the bounded _undelivered deque).
_DEDUPE_MAX = 512
_HTTP_TIMEOUT_S = 10.0

# Wall-clock dead-man's-switch cadence (P0-3). The deadman ping and the
# wall-clock heartbeat are driven from a timer in run() — NOT from the engine
# frame loop — so they keep firing through a legitimate safety pause or a
# scheduler wait. Pinging on the engine frame loop falsely pages "rig dead" the
# moment a multi-hour cloud-pause stops producing frames.
#   * the deadman is GET-pinged every DEADMAN_INTERVAL_S; pick a value comfortably
#     under a typical healthchecks/Uptime-Kuma grace window.
#   * the wall-clock task wakes on the GCD-ish tick below and fires whatever is due.
DEADMAN_INTERVAL_S = 60.0
# How often the wall-clock task wakes to check what is due. Small relative to the
# deadman interval and the (minutes-granularity) heartbeat cadence.
_WALLCLOCK_TICK_S = 5.0

# Source tag on the dispatcher's own diagnostic logs so they are NOT routed back
# through the alert pipeline (would otherwise self-feed a failure loop).
_ALERT_LOG_SOURCE = "alert"


def _url_is_safe(url: str, *, allow_private: bool = False) -> bool:
    """Pragmatic SSRF guard for a user-configured outbound URL (ntfy / webhook /
    deadman). Requires an http(s) scheme with a host, and rejects an *IP literal*
    host that is loopback/private/link-local (incl. the cloud-metadata address)/
    reserved/multicast/unspecified.

    This is deliberately lightweight: these URLs are operator-configured (not an
    unauthenticated per-request param like the Alpaca scan), so we do not resolve
    hostnames here (which would add a network round-trip / break offline tests).
    The literal-IP + scheme checks block the obvious internal targets; a hostname
    that resolves to an internal IP is out of scope for this pragmatic guard.

    ``allow_private=True`` (used only for the DEAD-MAN's-SWITCH url) permits a
    loopback/private/link-local host: a self-hosted Uptime-Kuma / healthchecks on
    ``192.168.x.x`` is the *user's own* monitor and an extremely common setup
    (P0-3). The unspecified/multicast/reserved guards still apply — those are
    never a legitimate monitor target.
    """
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a (non-literal) hostname — allowed under the pragmatic guard
    blocked = ip.is_reserved or ip.is_multicast or ip.is_unspecified
    if not allow_private:
        blocked = blocked or ip.is_loopback or ip.is_private or ip.is_link_local
    return not blocked

# ntfy priority mapping by level.
_NTFY_PRIORITY = {"error": "urgent", "warning": "high", "info": "default"}
_LEVEL_RANK = {"info": 0, "warning": 1, "error": 2}


class AlertEvent:
    """A normalized alert payload derived from a bus event."""

    __slots__ = ("type", "level", "message", "source", "ts", "plan", "extra")

    def __init__(self, type: str, level: str, message: str, *, source: str = "",
                 plan: str = "", extra: dict[str, Any] | None = None,
                 ts: float | None = None):
        self.type = type
        self.level = level
        self.message = message
        self.source = source
        self.ts = ts if ts is not None else time.time()
        self.plan = plan
        self.extra = extra or {}

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "level": self.level, "message": self.message,
                "source": self.source, "ts": self.ts, "plan": self.plan,
                **self.extra}

    def dedupe_key(self) -> str:
        return f"{self.type}:{self.level}:{self.message}"


class AlertDispatcher:
    """Maps bus events to outbound alerts. Construct with the bus and a
    ``get_config`` callable returning the live :class:`~astrodeck.config.AppConfig`
    (so a config change is picked up without a restart)."""

    def __init__(self, bus_obj: Any, get_config: Callable[[], Any]):
        self.bus = bus_obj
        self.get_config = get_config
        self._client: httpx.AsyncClient | None = None
        # OrderedDict so old keys can be evicted (bounded size) on a long run.
        self._dedupe: OrderedDict[str, float] = OrderedDict()
        self._undelivered: deque[tuple[Any, AlertEvent]] = deque(maxlen=_UNDELIVERED_MAX)
        self._last_safe: bool | None = None          # safety transition tracker
        self._last_heartbeat: dict[str, float] = {}  # sink id -> last send ts
        self._stop = asyncio.Event()
        # Wall-clock dead-man's-switch state (P0-3). ``_last_deadman`` is the
        # monotonic ts of the last attempted ping; the wall-clock loop fires the
        # next ping once DEADMAN_INTERVAL_S has elapsed, independent of the engine.
        self._last_deadman: float = 0.0
        # One-shot loud warning when a configured deadman url is blocked/unreachable
        # so the user is not lulled into a false sense of monitoring. Keyed by the
        # url so a *changed* url re-warns; reset when a ping succeeds.
        self._deadman_warned: str | None = None

    # -- lifecycle -------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
        return self._client

    async def run(self) -> None:
        """Long-running subscriber loop. Cancellable; closes the HTTP client on
        exit. Never raises out of the per-event handler (failures are logged +
        queued).

        Also owns a WALL-CLOCK dead-man's-switch + heartbeat task (P0-3) so those
        pings keep firing through a legitimate safety pause / scheduler wait —
        driving them from the engine frame loop falsely pages "rig dead" the
        moment a multi-hour cloud-pause stops producing frames."""
        await self._ensure_client()
        q = self.bus.subscribe()
        wallclock = asyncio.create_task(self._wallclock_loop())
        try:
            while not self._stop.is_set():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    await self._retry_undelivered()
                    continue
                try:
                    await self._on_bus_event(ev)
                except Exception as e:  # never kill the loop
                    self.bus.log("warning", f"alert dispatch error: {e}", "alert")
        except asyncio.CancelledError:
            raise
        finally:
            wallclock.cancel()
            try:
                await wallclock
            except (asyncio.CancelledError, Exception):
                pass
            self.bus.unsubscribe(q)
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    async def _wallclock_loop(self) -> None:
        """Independent wall-clock driver for the dead-man's-switch + heartbeat
        (P0-3). Wakes every ``_WALLCLOCK_TICK_S`` and fires whatever is due,
        regardless of whether the engine is producing frames — so a paused or
        waiting (but alive) rig keeps its monitor green. Never raises out of the
        loop; both helpers are hardened, but guard anyway."""
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now - self._last_deadman >= DEADMAN_INTERVAL_S:
                    self._last_deadman = now
                    try:
                        await self.deadman_ping()
                    except Exception:
                        pass
                try:
                    await self.emit_heartbeat("rig alive (wall-clock)")
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=_WALLCLOCK_TICK_S)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        self._stop.set()

    # -- bus event -> alert mapping --------------------------------------------

    async def _on_bus_event(self, ev: Any) -> None:
        """Translate one bus :class:`~astrodeck.events.Event` into zero or more
        outbound alerts."""
        t = ev.type
        data = ev.data or {}
        alert: AlertEvent | None = None

        if t == "sequence":
            state = data.get("state")
            if state == "running" and data.get("_first_running"):
                alert = AlertEvent("run_start", "info",
                                   f"Run started: {data.get('plan_name', '')}",
                                   plan=data.get("plan_name", ""))
            elif state in ("complete", "aborted", "error"):
                reason = data.get("end_reason") or state
                alert = AlertEvent("run_end", "info" if state == "complete" else "error",
                                   f"Run {state}: {reason}",
                                   plan=data.get("plan_name", ""),
                                   extra={"end_reason": reason})
        elif t == "safety":
            is_safe = data.get("is_safe")
            # only fire on a genuine transition (C1-18 says never dedupe, but we
            # still only emit on a real safe<->unsafe edge, not every poll).
            if is_safe is not None and is_safe != self._last_safe:
                self._last_safe = is_safe
                if is_safe:
                    alert = AlertEvent("safety", "info", "Conditions safe again")
                else:
                    reason = data.get("reason") or "unsafe condition"
                    alert = AlertEvent("safety", "error", f"UNSAFE: {reason}",
                                       extra={"action": data.get("action")})
        elif t == "reconnect":
            alert = AlertEvent("reconnect", "warning",
                               f"Reconnect attempt: {data.get('role', '')} "
                               f"({data.get('attempt', '?')})",
                               extra={"role": data.get("role")})
        elif t == "heartbeat":
            alert = AlertEvent("heartbeat", "info", data.get("message", "heartbeat"))
        elif (t == "log" and data.get("level") in ("warning", "error")
              and data.get("source") != _ALERT_LOG_SOURCE):
            # NB: skip the dispatcher's OWN failure diagnostics (source="alert").
            # They are logged from _dispatch on a delivery failure; re-mapping
            # them into a new alert would self-feed an alert-failure loop (#13).
            alert = AlertEvent(data["level"], data["level"],
                               data.get("message", ""),
                               source=data.get("source", ""))

        if alert is None:
            return
        await self._dispatch(alert)

    async def emit_heartbeat(self, message: str) -> None:
        """Engine helper: emit a progress heartbeat to sinks whose
        ``heartbeat_min`` has elapsed."""
        await self._dispatch(AlertEvent("heartbeat", "info", message))

    # -- dispatch + send -------------------------------------------------------

    def _sinks_for(self, alert: AlertEvent) -> list[Any]:
        cfg = self.get_config()
        out = []
        for sink in getattr(cfg, "alerts", []):
            if not sink.enabled:
                continue
            if alert.type == "heartbeat":
                # heartbeat is gated by per-sink cadence, not the events list.
                if sink.heartbeat_min <= 0:
                    continue
                last = self._last_heartbeat.get(sink.id, 0.0)
                if time.time() - last < sink.heartbeat_min * 60.0:
                    continue
                self._last_heartbeat[sink.id] = time.time()
            else:
                if alert.type not in sink.events:
                    continue
                # State-change alerts (run_start/run_end/safety/reconnect) are
                # subscribed by event TYPE, not severity — a default warning sink
                # must still receive an info-level run_start/run_end (#7). Only
                # generic warning/error *log* alerts honor min_level.
                if (alert.type not in _NEVER_DEDUPE
                        and _LEVEL_RANK.get(alert.level, 0)
                        < _LEVEL_RANK.get(sink.min_level, 1)):
                    continue
            out.append(sink)
        return out

    def _should_dedupe(self, alert: AlertEvent) -> bool:
        if alert.type in _NEVER_DEDUPE:
            return False
        key = alert.dedupe_key()
        now = time.time()
        last = self._dedupe.get(key)
        # Record this hit as the most-recent (move_to_end keeps insertion order
        # = recency for the LRU eviction below).
        self._dedupe[key] = now
        self._dedupe.move_to_end(key)
        # Bound the map (#14): drop entries older than the window, then cap size
        # by evicting the oldest, so variable-text logs can't grow it unbounded
        # on an all-night run.
        cutoff = now - _DEDUPE_WINDOW_S
        while self._dedupe:
            oldest_key, oldest_ts = next(iter(self._dedupe.items()))
            if oldest_ts >= cutoff and len(self._dedupe) <= _DEDUPE_MAX:
                break
            if oldest_key == key:
                break  # never evict the entry we just recorded
            self._dedupe.popitem(last=False)
        return last is not None and (now - last) < _DEDUPE_WINDOW_S

    async def _dispatch(self, alert: AlertEvent) -> None:
        if self._should_dedupe(alert):
            return
        for sink in self._sinks_for(alert):
            ok, err = await self._send(sink, alert)
            if not ok:
                self.bus.log("warning",
                             f"{sink.kind} alert failed: {err}", "alert")
                self.bus.publish("alert", sink=sink.id, ok=False, error=str(err))
                self._undelivered.append((sink, alert))

    async def _send(self, sink: Any, ev: AlertEvent) -> tuple[bool, str | None]:
        """Deliver one alert to one sink. Returns ``(ok, error)``; never raises."""
        try:
            client = await self._ensure_client()
            if sink.kind == "ntfy":
                return await self._send_ntfy(client, sink, ev)
            if sink.kind == "webhook":
                return await self._send_webhook(client, sink, ev)
            if sink.kind == "telegram":
                return await self._send_telegram(client, sink, ev)
            return False, f"unknown sink kind {sink.kind!r}"
        except (httpx.HTTPError, OSError) as e:
            return False, self._scrub(str(e), sink)
        except Exception as e:  # belt-and-braces — a send never propagates
            return False, self._scrub(str(e), sink)

    @staticmethod
    def _scrub(msg: str, sink: Any) -> str:
        """Keep a Telegram bot token out of error/log strings (#23): an httpx
        error can stringify the request URL, which embeds the token."""
        token = getattr(sink, "token", "") or ""
        if token and token in msg:
            return msg.replace(token, "***")
        return msg

    async def _send_ntfy(self, client: httpx.AsyncClient, sink: Any,
                         ev: AlertEvent) -> tuple[bool, str | None]:
        if not sink.url:
            return False, "no ntfy topic url"
        if not _url_is_safe(sink.url):
            return False, "blocked ntfy url (require http(s); no internal host)"
        headers = {
            "Title": f"AstroDeck: {ev.type}",
            "Priority": _NTFY_PRIORITY.get(ev.level, "default"),
            "Tags": ev.type,
        }
        r = await client.post(sink.url, content=ev.message.encode("utf-8"),
                              headers=headers)
        return self._ok(r)

    async def _send_webhook(self, client: httpx.AsyncClient, sink: Any,
                            ev: AlertEvent) -> tuple[bool, str | None]:
        if not sink.url:
            return False, "no webhook url"
        if not _url_is_safe(sink.url):
            return False, "blocked webhook url (require http(s); no internal host)"
        r = await client.post(sink.url, json=ev.as_dict())
        return self._ok(r)

    async def _send_telegram(self, client: httpx.AsyncClient, sink: Any,
                             ev: AlertEvent) -> tuple[bool, str | None]:
        if not sink.token or not sink.chat_id:
            return False, "telegram needs bot token + chat id"
        url = f"https://api.telegram.org/bot{sink.token}/sendMessage"
        r = await client.post(url, json={"chat_id": sink.chat_id,
                                         "text": ev.message})
        return self._ok(r)

    @staticmethod
    def _ok(r: httpx.Response) -> tuple[bool, str | None]:
        if 200 <= r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}"

    # -- retry queue -----------------------------------------------------------

    async def _retry_undelivered(self) -> None:
        """Flush the undelivered queue when connectivity returns. Each item gets
        one attempt per pass; a still-failing item is re-queued at the back."""
        if not self._undelivered:
            return
        n = len(self._undelivered)
        for _ in range(n):
            try:
                sink, ev = self._undelivered.popleft()
            except IndexError:
                break
            ok, _err = await self._send(sink, ev)
            if not ok:
                self._undelivered.append((sink, ev))

    @property
    def undelivered_count(self) -> int:
        return len(self._undelivered)

    # -- dead-man's-switch -----------------------------------------------------

    async def deadman_ping(self) -> None:
        """GET the configured external healthcheck URL. Absence of these pings is
        what triggers *their* alert (C2-9). A transport failure is not surfaced —
        a missed ping is the signal, not an error to page on.

        Two P0-3 fixes vs. the original silent path:

        * the deadman url is allowed to be a LAN/private host — a self-hosted
          Uptime-Kuma / healthchecks on ``192.168.x.x`` is the user's OWN monitor
          and the common case; only this url gets ``allow_private`` (ntfy/webhook
          outbound alerts keep the full SSRF block).
        * if the url is *blocked* (e.g. a literal ``0.0.0.0``/multicast/reserved
          target that can never be a monitor), we LOG A LOUD ONE-SHOT WARNING so
          the user is not lulled into a false sense of monitoring. Without this,
          a user who configured a deadman believes they are covered when nothing
          is ever pinged."""
        url = getattr(self.get_config(), "deadman_url", "") or ""
        if not url:
            return
        if not _url_is_safe(url, allow_private=True):
            # Cannot ever be a legitimate monitor (unspecified/multicast/reserved/
            # bad scheme). Warn ONCE per distinct url so the user knows their
            # deadman is doing nothing — never silently skip (P0-3).
            if self._deadman_warned != url:
                self._deadman_warned = url
                self.bus.log(
                    "warning",
                    "dead-man's-switch url is not a valid http(s) monitor target "
                    "and is being SKIPPED — no pings are being sent",
                    _ALERT_LOG_SOURCE,
                )
            return
        try:
            client = await self._ensure_client()
            r = await client.get(url)
        except (httpx.HTTPError, OSError) as e:
            # Unreachable monitor: the missed ping IS the signal to the external
            # service, but warn ONCE locally so a user who set up a monitor we
            # can't reach (typo'd host, monitor down, no LAN route) finds out.
            if self._deadman_warned != url:
                self._deadman_warned = url
                self.bus.log(
                    "warning",
                    f"dead-man's-switch url unreachable ({self._scrub_url(url)}): "
                    f"{type(e).__name__} — the external monitor will see a missed "
                    f"ping; verify the url/host is reachable",
                    _ALERT_LOG_SOURCE,
                )
            return
        # A reachable-but-error status (e.g. 404 from a deleted healthcheck) also
        # warrants a one-shot warning: the user thinks they have a deadman, but
        # the monitor is rejecting the ping.
        if not (200 <= r.status_code < 400):
            if self._deadman_warned != url:
                self._deadman_warned = url
                self.bus.log(
                    "warning",
                    f"dead-man's-switch url returned HTTP {r.status_code} "
                    f"({self._scrub_url(url)}) — check the monitor still exists",
                    _ALERT_LOG_SOURCE,
                )
            return
        # Healthy ping: clear the warn latch so a later failure re-warns.
        self._deadman_warned = None

    @staticmethod
    def _scrub_url(url: str) -> str:
        """Drop any ``user:pass@`` userinfo and query string from a url before it
        goes into a log line (a deadman url can carry a ping secret in the path or
        query — keep scheme+host+path only, sans userinfo)."""
        try:
            parts = urlsplit(url)
        except (ValueError, TypeError):
            return "<url>"
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return f"{parts.scheme}://{host}{parts.path}"

    # -- test ------------------------------------------------------------------

    async def test(self, sink_id: str) -> dict[str, Any]:
        """Send a real test alert to one sink and report the round-trip result.

        On a genuine 2xx the sink's ``verified`` flag is set True (and persisted by
        the caller via ConfigStore). Returns ``{ok, error?, verified}``."""
        cfg = self.get_config()
        sink = next((s for s in getattr(cfg, "alerts", []) if s.id == sink_id), None)
        if sink is None:
            return {"ok": False, "error": "no such alert sink", "verified": False}
        ev = AlertEvent("test", "info",
                        "AstroDeck test alert — your channel works.")
        ok, err = await self._send(sink, ev)
        sink.verified = bool(ok)
        return {"ok": ok, "error": err, "verified": sink.verified}
