# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
* **External dead-man's-switch** (C2-9, #542): :meth:`deadman_ping` GETs the
  user's healthchecks-style URL on a wall-clock cadence
  (:meth:`_wallclock_loop`, independent of the engine's frames) and again,
  best-effort, whenever the engine's frame path calls it; *its absence* is
  what pages them.
* **The dead-man ping carries a status line, and never its own url** (#606
  part A, #694). With :attr:`AlertDispatcher.beacon_source` set the ping is a
  POST whose body is the closed-vocabulary line ``rig_beacon`` renders (state,
  frames, last log level, boot cause), so the owner's monitor shows what the
  rig was doing when it went dark. A monitor that will not take it is pinged
  with a plain GET as before: the beacon may never turn a working dead-man
  into a failing one. And the url's PATH is the ping secret for a
  healthchecks-style monitor, so no log line carries it
  (:meth:`AlertDispatcher._scrub_url`).
* **The reader never awaits a sink** (#538). The bus subscription is bounded
  and drops its oldest event when full, and it used to be read by a loop that
  awaited every send inline, up to ``_HTTP_TIMEOUT_S`` per sink. A hung
  webhook and a warning more often than once per timeout backed it up past
  ``SUBSCRIBER_MAX``, and the bus dropped the oldest: an UNSAFE edge, a
  run_end, in silence. Now the reader maps each event and puts the alert on
  an outbox of its own, bounded apart and drained by a sender task, so the
  subscription is read at bus speed whatever the sinks do. A full outbox
  gives up a warning before a state-change alert, never the reverse; and the
  #444 ``relay_gap`` marker, which says the subscription did drop, is logged
  once, at warning, with the count.
* **A hung sink cannot delay a different sink, and an eviction is not
  silent** (#549). The outbox's single sender used to send an alert to each
  of its sinks IN TURN, so a hung webhook held a healthy ntfy sink's copy of
  the SAME alert behind it — and held every later, already-queued alert
  behind it too, an UNSAFE edge arriving after a burst of warnings among
  them, minutes late. :meth:`_fan_out` now hands each alert to every
  targeted sink's own bounded lane at once (:class:`_SinkLane`), and each
  lane is drained by its own task, so only that sink's own backlog can ever
  delay it. A lane still gives a warning up to keep a state change (never
  the reverse, unchanged from the shared outbox's rule), and now COUNTS what
  it gives up and says so once it has room, the ``relay_gap`` pattern
  applied to a sink instead of the bus subscription.
* **Neither the dead-man ping nor the heartbeat holds the frame loop**
  (#542). ``SequenceEngine._frame_alerts_tick`` awaits :meth:`deadman_ping`
  and :meth:`emit_heartbeat` after every frame's safety gate; both used to
  await the actual send, so a monitor or sink that took the connection and
  never answered held every frame up to ``_HTTP_TIMEOUT_S``, twice over when
  a heartbeat was also due. While the outbox pipeline is live (:meth:`run`
  has started it), :meth:`deadman_ping` fires the real GET as its own task
  (a single-flight guard means a still-running ping just absorbs the next
  tick) and :meth:`emit_heartbeat` only dedupes and enqueues; neither awaits
  a sink. A bare dispatcher (no :meth:`run`, the shape most of this module's
  own tests build) keeps the old inline behaviour, so a direct caller still
  sees a real result.

One :class:`httpx.AsyncClient` is reused for the dispatcher's lifetime.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections import OrderedDict
from collections import deque
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from .aio import reap
from .events import RELAY_GAP, SITE_DERIVED_KEY, bus
from .rig_beacon import is_closed_vocabulary

# State-change event types are NEVER deduped (C1-18). These are subscribed by
# event *type*, not severity, so they also bypass the per-sink min_level gate
# (a default warning sink must still receive an info-level run_start/run_end).
# They are also what a full outbox keeps (#538): it gives up anything else
# first.
_NEVER_DEDUPE = {"run_start", "run_end", "safety", "reconnect"}
# Alerts built and not yet sent (#538). Bounded apart from the bus
# subscription: a hung sink fills this, while the reader still drains the
# subscription at bus speed. Small, because an alert queued at its back waits
# that many sends, each up to _HTTP_TIMEOUT_S on a sink that never answers.
_OUTBOX_MAX = 32
# Free slots the reader's subscription must have before the relay_gap line
# is said (#538, #548). The line is one bus.log, and one bus.log can put
# three events on the subscription: the night-log writer's owed notice, the
# summary of the storm window the line closes, and the line. Said into
# fewer, it drops the oldest event still queued, which can be the UNSAFE
# edge that survived the drop the line is about.
_GAP_ROOM = 3
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
# What a monitor says to a POST it will not take, as opposed to one that is down
# or that really lacks the check (#606): any 4xx, because 405 is the textbook
# answer but 404 is what an Express route registered for GET alone gives a POST
# (an Uptime Kuma push monitor on a version before it took any method) and
# 400/413/415 are a body or a content type refused; and 501, "method not
# implemented". Any of these is retried ONCE as a plain GET, and remembered
# only if that GET is accepted (a check that is really gone fails both). Not in
# the set: 408 and 429, which are the monitor's load rather than a verdict on
# the method, and every 5xx but 501, a server having a bad minute: treating
# those as a verdict would drop the beacon for the rest of the process.
_POST_REFUSED = (frozenset(range(400, 500)) - {408, 429}) | {501}
# The word a log line carries in place of a dead-man url's path (#694).
_PATH_WITHHELD = "<path withheld>"
# The longest dead-man url the settings route will save (#812). A healthchecks or
# Uptime-Kuma ping url is under a hundred characters; 2048 is the ceiling a
# browser, proxy or monitor will carry, and past it the field holds a paste of
# something that is not a url.
DEADMAN_URL_MAX = 2048
# The two things the dispatcher does on a timer, as the failure latch names them
# (``AlertDispatcher._failures_said``).
_STAGE_DEADMAN = "dead-man ping"
_STAGE_HEARTBEAT = "heartbeat"
# The same two calls as the engine's frame loop makes them (#936,
# ``SequenceEngine._frame_alerts_tick``), under stages of their own: a heartbeat
# that works on the frame path must not forget what the timer's heartbeat has
# already said (and the reverse), or one failing path would say itself again
# every time the other one worked.
STAGE_FRAME_DEADMAN = "dead-man ping on the frame path"
STAGE_FRAME_HEARTBEAT = "heartbeat on the frame path"

# Source tag on the dispatcher's own diagnostic logs so they are NOT routed back
# through the alert pipeline (would otherwise self-feed a failure loop).
_ALERT_LOG_SOURCE = "alert"

# The loggers of the HTTP client every sink and the dead-man ping go through (#736).
# httpx writes ``HTTP Request: GET <the whole url> "HTTP/1.1 200 OK"`` at INFO on
# 'httpx', and httpcore traces each connection it opens at DEBUG. For a
# healthchecks-style monitor the url's path IS the ping secret (#694), and the same
# line carries a Telegram bot token or a Slack/Discord webhook path for every other
# sink. Nothing configures a handler today, so nothing is written; a later
# ``logging.basicConfig(level=INFO)``, a debug flag or a wrapper process that
# captures root logs would put them in stderr and every log file. A level on the
# named logger holds against a root level set later (a logger's own level beats
# the root's), so this is said once, at import, before any client exists.
_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


def _quiet_http_client_logs() -> None:
    """Hold httpx's and httpcore's own loggers at WARNING, so a process that is
    configured at INFO or DEBUG never writes a request line carrying a url."""
    for name in _HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


_quiet_http_client_logs()


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


def deadman_url_problem(url: str) -> str | None:
    """Why ``url`` can never be the dead-man's-switch target, in words fit for
    a 422 body, or None when the ping can be sent to it (#812).

    The same test the ping applies when it fires, run when the url is SAVED,
    so the owner learns at the moment of saving and not from a one-shot log
    line on some later night: a length bound, no stray whitespace, an
    http(s) scheme with a host and a port that is a port, the literal-address
    guard of :func:`_url_is_safe`, and finally that httpx will BUILD a request
    for it (a control character, a malformed IDNA label and a lone surrogate
    are refused there and nowhere above).

    The reason NEVER carries any part of ``url``: its path is the ping secret
    (#694) and a refusal is handed back to whoever sent it and logged. For a
    request httpx refuses it names the exception's TYPE and never its text,
    which quotes the url."""
    if len(url) > DEADMAN_URL_MAX:
        return f"the url is longer than {DEADMAN_URL_MAX} characters"
    if url != url.strip():
        return "the url has leading or trailing whitespace"
    try:
        parts = urlsplit(url)
        host, _port = parts.hostname, parts.port   # .port raises on a bad port
    except ValueError:
        return "the url is not well formed (check the host and the port)"
    if parts.scheme not in ("http", "https"):
        return "the url must start with http:// or https://"
    if not host:
        return "the url has no host name"
    if not _url_is_safe(url, allow_private=True):
        return ("the url points at an address that can never be a monitor "
                "(unspecified, multicast or reserved)")
    try:
        httpx.Request("GET", url)
    except Exception as e:  # noqa: BLE001 - however httpx refuses, the url is unusable
        return (f"the HTTP client cannot build a request for the url "
                f"({type(e).__name__}): check the port and any stray characters")
    return None


def _smtp_send_blocking(sink: Any, ev: "AlertEvent") -> tuple[bool, str | None]:
    """Blocking SMTP send (runs in a worker thread). Returns (ok, err);
    never raises. STARTTLS + optional login; password is sink.token."""
    import smtplib
    from email.message import EmailMessage
    recipients = [a.strip() for a in sink.smtp_to.split(",") if a.strip()]
    if not recipients:
        return False, "email has no valid recipients"
    msg = EmailMessage()
    msg["Subject"] = f"AstroDeck: {ev.type}"
    msg["From"] = sink.smtp_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(ev.message)
    try:
        with smtplib.SMTP(sink.smtp_host, sink.smtp_port or 587,
                          timeout=_HTTP_TIMEOUT_S) as s:
            if sink.smtp_starttls:
                s.starttls()
            if sink.smtp_user and sink.token:
                s.login(sink.smtp_user, sink.token)
            s.send_message(msg, to_addrs=recipients)
        return True, None
    except (OSError, smtplib.SMTPException) as e:
        return False, f"{type(e).__name__}: {e}"


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


class _SinkLane:
    """One configured sink's own outbound queue (#549). A hung sink's
    backlog lives only here, so it can never delay a DIFFERENT sink's
    delivery of the same alert — that sink has its own lane and its own
    draining task.

    Bounded and evicted by the same rule as the shared outbox
    (:meth:`AlertDispatcher._enqueue`): full, it gives up its oldest
    non-state-change alert; full of state changes, it gives up its oldest
    for a newer one; a plain alert arriving to a lane already full of state
    changes is refused outright. Unlike the outbox, an eviction here is
    COUNTED (``evicted``) and flagged (``eviction_owed``) so the dispatcher
    can say it once there is room, mirroring ``_say_gap`` (#444) for a sink
    instead of the bus subscription.

    Queuing (:meth:`push`) is plain FIFO — arrival order, unchanged. DRAINING
    (:meth:`pop`, #549 follow-up) is by severity once a backlog has formed: a
    state change (run_start/run_end/safety incl. UNSAFE/reconnect) jumps
    ahead of whatever plain warnings are already waiting, so a burst of
    queued warnings can never sit an UNSAFE edge behind them a second time —
    once at the sink's own lane, not only at the shared outbox
    (:meth:`AlertDispatcher._enqueue` already did this at the outbox). Two
    state changes, or two plain alerts, keep the order they arrived in. With
    nothing else queued a lane drains one at a time and simply returns
    whatever just arrived, so a healthy sink still sees publish order."""

    __slots__ = ("sink_id", "queue", "ready", "evicted", "evicted_said",
                 "eviction_owed")

    def __init__(self, sink_id: str):
        self.sink_id = sink_id
        self.queue: deque[AlertEvent] = deque()
        self.ready = asyncio.Event()
        self.evicted = 0
        self.evicted_said = 0
        self.eviction_owed = False

    def push(self, alert: AlertEvent) -> None:
        if len(self.queue) >= _OUTBOX_MAX:
            victim = next((a for a in self.queue if a.type not in _NEVER_DEDUPE),
                          None)
            if victim is not None:
                self.queue.remove(victim)
            elif alert.type in _NEVER_DEDUPE:
                self.queue.popleft()
            else:
                self.evicted += 1
                self.eviction_owed = True
                return
            self.evicted += 1
            self.eviction_owed = True
        self.queue.append(alert)
        self.ready.set()

    def pop(self) -> AlertEvent | None:
        """The next alert this lane's own drain task should send: the
        OLDEST state change still queued (run_start/run_end/safety incl.
        UNSAFE/reconnect), ahead of any plain alert waiting in front of it;
        with none queued, the oldest plain alert. Two alerts of the same
        tier come off in the order :meth:`push` put them on. ``None`` if the
        lane is empty."""
        for alert in self.queue:
            if alert.type in _NEVER_DEDUPE:
                self.queue.remove(alert)
                return alert
        if self.queue:
            return self.queue.popleft()
        return None


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
        # When the monitor last ACCEPTED a ping (2xx/3xx), and which url it
        # accepted it for (#125). ``_last_deadman`` above is the last
        # ATTEMPT, and ``_deadman_warned`` is only 'no failure said yet', so
        # neither can tell a monitor that answers from one nothing has been
        # sent to. The url is held as a hash, not text: it carries a per-ping
        # secret and nothing that prints this object may be able to leak it.
        self._last_deadman_ok: float | None = None
        self._last_deadman_ok_for: int | None = None
        # The status line the ping carries (#606 part A): a callable returning
        # the text, or None for the plain GET the ping always was. Set by the
        # app once the engine exists (``rig_beacon.make_source``); the
        # dispatcher is built before it, so this cannot be a constructor
        # argument.
        self.beacon_source: Callable[[], str] | None = None
        # The url (as a hash, for the same reason as ``_last_deadman_ok_for``)
        # whose monitor refused a POST and then accepted a GET: it is pinged
        # with a GET from then on. Per url, not per process, so pasting a new
        # monitor that does take a POST gets the beacon again.
        self._deadman_get_only_for: int | None = None
        # The reason the beacon was last left off a ping, so it is said once per
        # reason and not once a minute; None while the beacon is going out.
        self._beacon_warned: str | None = None
        # The outbox (#538): alerts the reader built, oldest first, for the
        # sender task. ``_outbox_ready`` wakes the sender; it is made by run()
        # on the loop that runs it, since an asyncio.Event binds to the first
        # loop that waits on it and the dispatcher outlives any one loop.
        self._outbox: deque[AlertEvent] = deque()
        self._outbox_ready: asyncio.Event | None = None
        # The #444 marker's count: the subscription's ``dropped`` when the
        # last gap was said, and whether one is owed (see _say_gap).
        self._gap_said = 0
        self._gap_owed = False
        # Per-sink lanes (#549): sink id -> its own queue, and sink id -> its
        # own draining task. Populated lazily by _fan_out as sinks are first
        # targeted; cleared at the start and end of run() (a fresh run gets
        # fresh lanes rather than replaying a previous run's backlog).
        self._lanes: dict[str, _SinkLane] = {}
        self._lane_tasks: dict[str, asyncio.Task] = {}
        # The dead-man ping's own in-flight task while the outbox pipeline is
        # live (#542): a single-flight guard so a still-running ping absorbs
        # the next tick instead of piling another request up behind it.
        self._deadman_task: asyncio.Task | None = None
        # What the timer-driven work has already said it failed with (#811):
        # stage -> the exception TYPES said for it. A failure of a type already
        # said is not said again; a stage that works again forgets its set, so
        # the next failure is news. Type names only: the text of whatever
        # raised can carry the dead-man url or a sink's token.
        self._failures_said: dict[str, set[str]] = {}

    # -- lifecycle -------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
        return self._client

    async def run(self) -> None:
        """Long-running subscriber loop. Cancellable; closes the HTTP client on
        exit. Never raises out of the per-event handler (failures are logged +
        queued).

        The loop READS; it never sends (#538). Each event is mapped to its
        alert and put on the outbox (:meth:`_enqueue`), and the sender task
        (:meth:`_send_loop`) fans it out to each targeted sink's own lane
        (:meth:`_fan_out`, #549) without awaiting a send either, so a sink
        that takes ``_HTTP_TIMEOUT_S`` to fail holds only that sink's own
        lane task, never this loop, the bus subscription (which drops its
        oldest event when it falls ``SUBSCRIBER_MAX`` behind), or a
        different sink. A ``relay_gap`` marker, the subscription saying it
        did drop (#444), is logged (:meth:`_say_gap`).

        Also owns a WALL-CLOCK dead-man's-switch + heartbeat task (P0-3) so those
        pings keep firing through a legitimate safety pause / scheduler wait —
        driving them from the engine frame loop falsely pages "rig dead" the
        moment a multi-hour cloud-pause stops producing frames."""
        await self._ensure_client()
        q = self.bus.subscribe()
        self._outbox_ready = asyncio.Event()
        if self._outbox:
            self._outbox_ready.set()
        self._gap_said, self._gap_owed = 0, False
        self._lanes = {}
        self._lane_tasks = {}
        self._deadman_task = None
        wallclock = asyncio.create_task(self._wallclock_loop())
        sender = asyncio.create_task(self._send_loop())
        try:
            while not self._stop.is_set():
                if self._gap_owed and q.maxsize - q.qsize() >= _GAP_ROOM:
                    self._say_gap(q)
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if ev.type == RELAY_GAP:
                    # Said once there is room for the line (see _say_gap).
                    self._gap_owed = True
                    continue
                try:
                    self._take(ev)
                except Exception as e:  # never kill the loop
                    self.bus.log("warning", f"alert dispatch error: {e}", "alert")
        except asyncio.CancelledError:
            raise
        finally:
            for task in (sender, wallclock):
                task.cancel()
            for task in (sender, wallclock):
                await reap(task)
            # sender is done, so _fan_out cannot start a new lane task past
            # this point (#549) — safe to cancel the whole set now.
            lane_tasks = list(self._lane_tasks.values())
            for task in lane_tasks:
                task.cancel()
            for task in lane_tasks:
                await reap(task)
            self._lane_tasks = {}
            self._lanes = {}
            if self._deadman_task is not None:
                self._deadman_task.cancel()
                await reap(self._deadman_task)
                self._deadman_task = None
            self.bus.unsubscribe(q)
            self._outbox_ready = None
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    def _take(self, ev: Any) -> None:
        """One bus event, on the reading loop: its alert, if it has one and it
        is not a repeat, goes on the outbox. Nothing here awaits (#538)."""
        alert = self._alert_for(ev)
        if alert is None or self._should_dedupe(alert):
            return
        self._enqueue(alert)

    def _enqueue(self, alert: AlertEvent) -> None:
        """Put ``alert`` on the outbox for the sender, making room if it is
        full (#538).

        Room is made by giving up the OLDEST alert that is not a state change
        (``_NEVER_DEDUPE``: run_start, run_end, safety, reconnect); a warning
        is given up before an UNSAFE edge, never the reverse. An outbox full
        of state changes turns a warning away, and gives up its oldest state
        change for a newer one, since the newest says what the rig is doing
        now. The reader's dedupe has run already, so a slot holds only an
        alert that will be sent."""
        box = self._outbox
        if len(box) >= _OUTBOX_MAX:
            victim = next((a for a in box if a.type not in _NEVER_DEDUPE), None)
            if victim is not None:
                box.remove(victim)
            elif alert.type in _NEVER_DEDUPE:
                box.popleft()
            else:
                return
        box.append(alert)
        if self._outbox_ready is not None:
            self._outbox_ready.set()

    async def _send_loop(self) -> None:
        """The outbox's alerts, oldest first, each handed to its sinks' own
        lanes (:meth:`_fan_out`, #549) — this loop never sends, so a hung
        sink holds only its own lane, never this loop and never a
        different sink. Idle for a second, it retries what failed before
        (:meth:`_retry_undelivered`), which gives way to a fresh alert.
        Never raises out of the loop."""
        ready = self._outbox_ready
        while not self._stop.is_set():
            if not self._outbox:
                ready.clear()
                try:
                    await asyncio.wait_for(ready.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    await self._retry_undelivered()
                continue
            alert = self._outbox.popleft()
            try:
                self._fan_out(alert)
            except Exception as e:  # never kill the sender
                self.bus.log("warning", f"alert dispatch error: {e}",
                             _ALERT_LOG_SOURCE)

    def _fan_out(self, alert: AlertEvent) -> None:
        """Hand ``alert`` to every sink that currently wants it, each onto
        that sink's OWN lane (#549). Synchronous — it only resolves the sink
        list, appends to a deque and wakes an ``asyncio.Event``, so handing
        off never waits on a slow sink. A sink's lane and draining task are
        created the first time that sink is targeted."""
        for sink in self._sinks_for(alert):
            lane = self._lanes.get(sink.id)
            if lane is None:
                lane = self._lanes[sink.id] = _SinkLane(sink.id)
            task = self._lane_tasks.get(sink.id)
            if task is None or task.done():
                self._lane_tasks[sink.id] = asyncio.create_task(
                    self._lane_loop(lane))
            lane.push(alert)

    async def _lane_loop(self, lane: "_SinkLane") -> None:
        """One sink's own dedicated sender (#549). Drains ``lane`` one send
        at a time, by severity when it has a backlog (:meth:`_SinkLane.pop`)
        — a state change never sits behind a plain warning queued ahead of
        it, though two alerts of the same severity stay in the order they
        arrived; a hung send here can delay only more of this same lane,
        never a different sink's lane, which has its own task. Idle, it says
        an owed eviction once there is room for the line (mirrors
        :meth:`_say_gap` for a sink instead of the bus subscription)."""
        try:
            while not self._stop.is_set():
                if lane.eviction_owed and len(lane.queue) < _OUTBOX_MAX:
                    self._say_lane_eviction(lane)
                if not lane.queue:
                    lane.ready.clear()
                    try:
                        await asyncio.wait_for(lane.ready.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                    continue
                alert = lane.pop()
                sink = next((s for s in getattr(self.get_config(), "alerts", [])
                            if s.id == lane.sink_id), None)
                if sink is None:
                    continue  # the sink was removed from config while queued
                try:
                    await self._send_and_track(sink, alert)
                except Exception as e:  # never kill the lane
                    self.bus.log("warning", f"alert dispatch error: {e}",
                                 _ALERT_LOG_SOURCE)
        except asyncio.CancelledError:
            raise

    def _say_lane_eviction(self, lane: "_SinkLane") -> None:
        """Say once that one sink's own lane dropped alerts because it could
        not keep up (#549): the ``_say_gap`` pattern (#444), scoped to a
        sink instead of the bus subscription. Source ``alert``, so the line
        is not itself turned into a new alert."""
        lane.eviction_owed = False
        missed = lane.evicted - lane.evicted_said
        lane.evicted_said = lane.evicted
        if missed <= 0:
            return
        self.bus.log(
            "warning",
            f"alert dispatcher dropped {missed} queued alert(s) for sink "
            f"{lane.sink_id}: it could not keep up, {lane.evicted} since it "
            f"started",
            _ALERT_LOG_SOURCE)

    def _say_gap(self, q: Any) -> None:
        """Say that the reader's subscription dropped events (#444, #538):
        once per ``relay_gap`` marker, at warning, with the subscription's own
        count (``Subscription.dropped``). Source ``alert``, so the line is
        not itself turned into an alert.

        Said when the subscription has room for it, not when the marker is
        read. The marker comes off a queue that was full, and it takes no
        entry off it; this line is published to the same bus, into that same
        queue, where it would drop the oldest event and open a new hole,
        whose marker would say it again, one event at a time through
        everything queued, the UNSAFE edge among them, and the reader would
        never yield. Room for ``_GAP_ROOM`` events, not one: the line can
        bring a storm summary and a night-log notice with it, and in one
        free slot the summary took the slot and the line dropped the next
        event, the UNSAFE edge in the case that showed it. The reads after
        the marker make the room."""
        self._gap_owed = False
        total = getattr(q, "dropped", 0)
        missed, self._gap_said = total - self._gap_said, total
        self.bus.log("warning",
                     f"alert dispatcher missed {missed} bus event(s), {total} "
                     f"since it started; any alert among them was not sent",
                     _ALERT_LOG_SOURCE)

    async def _wallclock_loop(self) -> None:
        """Independent wall-clock driver for the dead-man's-switch + heartbeat
        (P0-3). Wakes every ``_WALLCLOCK_TICK_S`` and fires whatever is due,
        regardless of whether the engine is producing frames — so a paused or
        waiting (but alive) rig keeps its monitor green. Never raises out of the
        loop; both helpers are hardened, but guard anyway, and SAY what the
        guard caught (:meth:`_say_failure`, #811): it used to be a bare ``pass``,
        which is how #735 stayed invisible."""
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now - self._last_deadman >= DEADMAN_INTERVAL_S:
                    self._last_deadman = now
                    try:
                        await self.deadman_ping()
                    except Exception as e:  # noqa: BLE001 - the loop outlives any one failure
                        self._say_failure(_STAGE_DEADMAN, e)
                try:
                    await self.emit_heartbeat("rig alive (wall-clock)")
                except Exception as e:  # noqa: BLE001 - as above
                    self._say_failure(_STAGE_HEARTBEAT, e)
                else:
                    self._failures_said.pop(_STAGE_HEARTBEAT, None)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=_WALLCLOCK_TICK_S)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    def _say_failure(self, stage: str, exc: BaseException) -> None:
        """Say, once, that ``stage`` raised ``exc`` and was skipped (#811).

        One warning per distinct failure: the exception's TYPE is the key, so a
        stage that raises the same thing every tick says it on the first and
        stays quiet, a different type is news, and a stage that works again
        (``_failures_said`` forgets it) says its next failure afresh. The line
        carries the type and never the text, which can quote the dead-man url
        (#694) or a sink's token. Source ``alert``, so it is not itself turned
        into an alert. The latch is stamped before the line is published, and a
        bus that cannot take it falls back to the module's logger, so saying
        this can neither repeat itself nor end the loop it reports on."""
        kind = type(exc).__name__
        said = self._failures_said.setdefault(stage, set())
        if kind in said:
            return
        said.add(kind)
        line = (f"alert dispatcher: the {stage} raised {kind} and was skipped; "
                f"it is tried again at the next tick")
        try:
            self.bus.log("warning", line, _ALERT_LOG_SOURCE)
        except Exception:  # noqa: BLE001 - the bus is what failed; the loop goes on
            logging.getLogger(__name__).warning(line)

    def report_failure(self, stage: str, exc: BaseException) -> None:
        """:meth:`_say_failure` for a caller outside this module that guards a
        call into the dispatcher and must go on whatever it raises (#936: the
        engine's per-frame dead-man ping and heartbeat). ``stage`` is one of the
        ``STAGE_FRAME_*`` names; the rules are :meth:`_say_failure`'s own."""
        self._say_failure(stage, exc)

    def report_recovery(self, stage: str) -> None:
        """``stage`` worked: forget what it has said, so its next failure is
        news again (:meth:`_say_failure`'s latch is 'nothing said yet', never
        'never again')."""
        self._failures_said.pop(stage, None)

    async def stop(self) -> None:
        self._stop.set()

    # -- bus event -> alert mapping --------------------------------------------

    async def _on_bus_event(self, ev: Any) -> None:
        """Translate one bus event into its alert and send it now, inline.

        The run loop does not call this: it maps with :meth:`_alert_for` and
        queues the alert for the sender (#538), because awaiting the send
        here is what backed the bus subscription up. It stays for a caller
        that wants the whole path in one await, through the same mapping,
        dedupe and sinks, as the no-light alert's test does."""
        alert = self._alert_for(ev)
        if alert is not None:
            await self._dispatch(alert)

    def _alert_for(self, ev: Any) -> AlertEvent | None:
        """The alert one bus :class:`~astrodeck.events.Event` maps to, or
        None. Synchronous, so the reader can map at bus speed; it keeps the
        safety edge tracker, so each event must pass through it once."""
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
                # PHRASED FROM THE REASON, NOT THE STATE. `state` is "complete"
                # for a dawn cutoff and for a night that ended owing frames, so
                # the old "Run {state}: {reason}" read "Run complete: incomplete"
                # — a push notification contradicting itself in five words.
                #
                # #565 AUDIT: a polite server shutdown now finalizes the report
                # with end_reason="shutdown" (engine.py's `except
                # CancelledError` arm), distinct from an operator's STOP
                # ("aborted"). This branch is gated on `state`, not on
                # end_reason, and `state` is set to "aborted" only by the
                # engine's own `abort()` — the operator-driven path — never by
                # the bare-cancellation teardown a polite shutdown takes: that
                # arm publishes no terminal `state` at all before the process
                # exits, so there is no live client left to alert. Nothing
                # here conflates the two; if that ever changes, this reads
                # `end_reason` verbatim, so the text would say "shutdown"
                # and not lie either way.
                alert = AlertEvent("run_end", "info" if state == "complete" else "error",
                                   f"Run ended: {reason}",
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
              and data.get("source") != _ALERT_LOG_SOURCE
              and not data.get(SITE_DERIVED_KEY)):
            # NB: skip the dispatcher's OWN failure diagnostics (source="alert").
            # They are logged from _dispatch on a delivery failure; re-mapping
            # them into a new alert would self-feed an alert-failure loop (#13).
            #
            # NB: skip a line flagged ``site_derived`` (#166, #302): its
            # MOMENT was set by a site computation (a flip taken at the
            # crossing, the idle hold at a flip point), the same fact
            # ``api.redact`` withholds from a viewer on every other seam. An
            # admin configures the sinks, but the channel itself (a shared
            # Discord or Slack room, an ntfy topic) is not a principal
            # `view.site_derived` can be checked against, so the safe
            # default is to never forward one. The line still reaches a
            # holder through the UI (`/api/logs`, `_redact_log_rows_for`)
            # and the durable night-log file, which this never touches.
            alert = AlertEvent(data["level"], data["level"],
                               data.get("message", ""),
                               source=data.get("source", ""))
        return alert

    async def emit_heartbeat(self, message: str) -> None:
        """Engine helper: emit a progress heartbeat to sinks whose
        ``heartbeat_min`` has elapsed.

        While the outbox pipeline is live (:meth:`run` has started it), this
        only dedupes and enqueues (#542): the caller — the engine's own
        per-frame path is one — must never wait on a sink. With no pipeline
        running (a bare dispatcher, as most of this module's tests build),
        it falls back to sending inline so the alert is not silently
        dropped on the floor."""
        alert = AlertEvent("heartbeat", "info", message)
        if self._outbox_ready is not None:
            if not self._should_dedupe(alert):
                self._enqueue(alert)
            return
        await self._dispatch(alert)

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
        """Dedupe, then send now. For a caller that awaits the send directly
        (``test()``, and a bare dispatcher's fallback paths); the reader
        dedupes and queues instead (#538), and a live dispatcher's
        :meth:`emit_heartbeat` does the same (#542)."""
        if self._should_dedupe(alert):
            return
        await self._send_all(alert)

    async def _send_all(self, alert: AlertEvent) -> None:
        """Send one alert to each sink that wants it, now, one at a time.
        For a caller that awaits the whole round trip; the queued path
        (:meth:`_fan_out`, #549) hands each sink its own lane instead, so a
        hung one here cannot delay this caller's other sinks — that
        guarantee is theirs, not this method's."""
        for sink in self._sinks_for(alert):
            await self._send_and_track(sink, alert)

    async def _send_and_track(self, sink: Any, alert: AlertEvent) -> None:
        """Send one alert to one sink; on failure, log, publish and queue
        for retry. Shared by the immediate :meth:`_send_all` path and each
        sink's own lane task (:meth:`_lane_loop`, #549), never raised."""
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
            if sink.kind == "discord":
                return await self._send_discord(client, sink, ev)
            if sink.kind == "slack":
                return await self._send_slack(client, sink, ev)
            if sink.kind == "email":
                return await self._send_email(sink, ev)
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

    async def _send_discord(self, client: httpx.AsyncClient, sink: Any,
                            ev: AlertEvent) -> tuple[bool, str | None]:
        url = sink.token  # the whole webhook URL is a bearer secret -> stored in token
        if not url:
            return False, "no discord webhook url"
        if not _url_is_safe(url):
            return False, "blocked discord url (require http(s); no internal host)"
        content = f"**AstroDeck: {ev.type}** — {ev.message}"
        r = await client.post(url, json={"content": content[:1900]})
        return self._ok(r)  # Discord returns 204 on success (within 2xx)

    async def _send_slack(self, client: httpx.AsyncClient, sink: Any,
                          ev: AlertEvent) -> tuple[bool, str | None]:
        url = sink.token
        if not url:
            return False, "no slack webhook url"
        if not _url_is_safe(url):
            return False, "blocked slack url (require http(s); no internal host)"
        r = await client.post(url, json={"text": f"AstroDeck [{ev.type}] {ev.message}"})
        return self._ok(r)

    async def _send_email(self, sink: Any, ev: AlertEvent) -> tuple[bool, str | None]:
        if not (sink.smtp_host and sink.smtp_from and sink.smtp_to):
            return False, "email needs smtp host, from, and to"
        ok, err = await asyncio.to_thread(_smtp_send_blocking, sink, ev)
        return ok, (self._scrub(err, sink) if err else None)

    @staticmethod
    def _ok(r: httpx.Response) -> tuple[bool, str | None]:
        if 200 <= r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}"

    # -- retry queue -----------------------------------------------------------

    async def _retry_undelivered(self) -> None:
        """Flush the undelivered queue when connectivity returns. Each item gets
        one attempt per pass; a still-failing item is re-queued at the back.

        The pass stops for a fresh alert on the outbox (#538). On a sink that
        never answers each attempt takes ``_HTTP_TIMEOUT_S``, so a full pass
        is ``_UNDELIVERED_MAX`` of them, over half an hour at the defaults,
        and an UNSAFE edge arriving at its start would wait for its end. It
        waits for the one attempt in flight; what the pass did not reach
        stays queued for the next."""
        if not self._undelivered:
            return
        n = len(self._undelivered)
        for _ in range(n):
            if self._outbox:
                break
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

    def health(self) -> dict[str, Any]:
        """Pure read of in-memory dispatcher state (no I/O): retry-queue depth
        (global + per-sink) and dead-man's-switch state, for the settings panel."""
        by_sink: dict[str, int] = {}
        for sink, _ev in self._undelivered:
            by_sink[sink.id] = by_sink.get(sink.id, 0) + 1
        dm_url = getattr(self.get_config(), "deadman_url", "") or ""
        last_age = (time.monotonic() - self._last_deadman
                    if self._last_deadman else None)
        # Only for the url configured NOW: a ping accepted for a previous url
        # says nothing about this one, and a freshly pasted typo must not
        # read as answering because the old check was.
        ok_age = (time.monotonic() - self._last_deadman_ok
                  if (self._last_deadman_ok is not None and dm_url
                      and self._last_deadman_ok_for == hash(dm_url))
                  else None)
        return {
            "undelivered": len(self._undelivered),
            "undelivered_by_sink": by_sink,
            "deadman": {
                "configured": bool(dm_url),
                "healthy": bool(dm_url) and self._deadman_warned is None,
                "last_ping_age_s": last_age,
                # Seconds since the monitor last ACCEPTED a ping, None if it
                # never has. ``healthy`` is "nothing has failed yet", which is
                # also true before the first request leaves, so only this says
                # the rig is actually being watched (#125). Ages and booleans
                # only: never the url.
                "last_ok_age_s": ok_age,
            },
        }

    # -- dead-man's-switch -----------------------------------------------------

    async def deadman_ping(self) -> None:
        """Trigger a ping of the configured external healthcheck URL (#542): a
        POST carrying the status beacon when :attr:`beacon_source` is set
        (#606), else a GET.

        While the outbox pipeline is live (:meth:`run` has started it — the
        wall-clock task and the engine's frame path are both such callers),
        the real ping runs on its own task and this returns at once: a
        stalled monitor must never hold up either caller. A ping already in
        flight absorbs this call rather than piling another request up
        behind it. With no pipeline running (a bare dispatcher, as this
        module's own tests below build), it pings inline so a direct caller
        sees the real result — see :meth:`_deadman_ping_now` for what the
        ping itself does and why."""
        if self._outbox_ready is None:
            await self._deadman_ping_now()
            return
        if self._deadman_task is not None and not self._deadman_task.done():
            return
        self._deadman_task = asyncio.create_task(self._deadman_ping_reporting())

    async def _deadman_ping_reporting(self) -> None:
        """The pipelined ping's task body (#811): :meth:`_deadman_ping_now`,
        with whatever escapes it said (:meth:`_say_failure`) instead of left on
        a task nobody awaits. ``_deadman_ping_now`` answers for the failures it
        foresaw; what is left here is what it cannot, such as ``get_config()``
        at its top, which sits outside its own try."""
        try:
            await self._deadman_ping_now()
        except Exception as e:  # noqa: BLE001 - a task nobody awaits is a swallow too
            self._say_failure(_STAGE_DEADMAN, e)
        else:
            self._failures_said.pop(_STAGE_DEADMAN, None)

    async def _deadman_ping_now(self) -> None:
        """The dead-man ping itself. Absence of these pings is what triggers
        *their* alert (C2-9). A transport failure is not surfaced — a missed
        ping is the signal, not an error to page on.

        Two P0-3 fixes vs. the original silent path:

        * the deadman url is allowed to be a LAN/private host — a self-hosted
          Uptime-Kuma / healthchecks on ``192.168.x.x`` is the user's OWN monitor
          and the common case; only this url gets ``allow_private`` (ntfy/webhook
          outbound alerts keep the full SSRF block).
        * if the url is *blocked* (e.g. a literal ``0.0.0.0``/multicast/reserved
          target that can never be a monitor), we LOG A LOUD ONE-SHOT WARNING so
          the user is not lulled into a false sense of monitoring. Without this,
          a user who configured a deadman believes they are covered when nothing
          is ever pinged.

        And it never raises (#735): a url httpx will not build a request for
        (a non-numeric port, say) takes the same one-shot warning as an
        unreachable one, instead of escaping into a caller that swallows it."""
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
            r = await self._send_deadman(client, url)
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
        except Exception as e:  # noqa: BLE001 - the ping never raises out of here (#735)
            # Everything above is a transport failure. What is left is httpx
            # refusing to BUILD a request for the url (``InvalidURL``, which is an
            # ``Exception`` and not an ``HTTPError``, plus the IDNA and encoding
            # ``ValueError``s; ``_url_is_safe`` reads the scheme and the host and
            # never the port, so a typo in it gets this far) and anything nobody
            # foresaw. Left to escape, it was eaten by the wall-clock loop's own
            # ``except Exception: pass`` (or sat unretrieved on the pipelined
            # ping's task), ``_deadman_warned`` stayed None, and the settings
            # badge read 'waiting' for good: the owner believed the rig was
            # watched and nothing was ever pinged. Said once per url, by the
            # exception's TYPE and never its text, and with the scrubbed url (#694).
            if self._deadman_warned != url:
                self._deadman_warned = url
                if isinstance(e, (httpx.InvalidURL, ValueError)):
                    what = ("url is not one the HTTP client can build a request for "
                            f"({self._scrub_url(url)}): {type(e).__name__} — it is "
                            "being SKIPPED and no pings are being sent; check the "
                            "port and any stray characters in it")
                else:
                    what = (f"ping failed before it could be sent "
                            f"({self._scrub_url(url)}): {type(e).__name__} — no "
                            "ping left, and this is not the monitor's doing")
                self.bus.log("warning", f"dead-man's-switch {what}", _ALERT_LOG_SOURCE)
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
        # Stamped HERE and nowhere earlier: a transport error, a blocked url
        # and a 4xx/5xx all returned above, and none of them is a ping the
        # monitor accepted (#125).
        self._last_deadman_ok = time.monotonic()
        self._last_deadman_ok_for = hash(url)

    async def _send_deadman(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        """One dead-man ping, as a POST carrying the beacon when there is one,
        else the plain GET it always was (#606 part A).

        A monitor that refuses the POST (:data:`_POST_REFUSED`) is asked again
        as a GET, and if THAT is accepted the refusal is remembered for this
        url, so a GET-only monitor costs one extra request, once. The GET's
        answer is what the caller judges, so a check that is really gone
        warns exactly as it did before the beacon existed. A transport error
        propagates to the caller untouched: it says nothing about the method."""
        body = self._beacon_body(url)
        if body is None:
            return await client.get(url)
        r = await client.post(url, content=body, headers={"Content-Type": "text/plain"})
        if r.status_code not in _POST_REFUSED:
            return r
        r = await client.get(url)
        if 200 <= r.status_code < 400:
            self._deadman_get_only_for = hash(url)
        return r

    def _beacon_body(self, url: str) -> bytes | None:
        """The beacon as a POST body, or None when this ping should be a plain
        GET: no source, a monitor already known to refuse a POST, or a source
        that failed or returned text outside the closed vocabulary.

        Never raises. The ping is the one thing this exists for, so a beacon
        bug costs the beacon and nothing else, and says so once per reason (an
        owner whose monitor never shows a status must be able to learn why).
        The log line names the exception's TYPE and never its text: the text
        is whatever the source's author put in it."""
        source = self.beacon_source
        if source is None or self._deadman_get_only_for == hash(url):
            return None
        try:
            text = source()
            reason = None if is_closed_vocabulary(text) else (
                "its text is outside the closed vocabulary, so it was not sent")
        except Exception as e:            # noqa: BLE001 - a beacon bug never stops the ping
            text = None
            reason = f"the source raised {type(e).__name__}"
        if reason is not None:
            if self._beacon_warned != reason:
                self._beacon_warned = reason
                self.bus.log("warning",
                             f"dead-man's-switch beacon left off the ping: {reason}",
                             _ALERT_LOG_SOURCE)
            return None
        self._beacon_warned = None
        return text.encode("ascii")

    @staticmethod
    def _scrub_url(url: str) -> str:
        """The scheme and host of a url, for a log line, and nothing after them.

        Userinfo, query and fragment are dropped, and so is the PATH, replaced
        by ``_PATH_WITHHELD`` (#694): for a healthchecks-style monitor
        (``https://<host>/<uuid>``) the path IS the ping secret, and whoever
        holds it can ping the check as healthy or pause it, which silences the
        alert set up for a dead rig. A log line is read by anyone who can read
        logs: the night file, the ``/api/logs`` ring, every sink that forwards
        a warning. Anything that does not parse as scheme://host comes back as
        a bare ``<url>``, never as the text it was given."""
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
        except (ValueError, TypeError, AttributeError):
            return "<url>"
        if not parts.scheme or not host:
            return "<url>"
        return f"{parts.scheme}://{host}/{_PATH_WITHHELD}"

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
