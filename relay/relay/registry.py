"""Device registration + the per-home tunnel multiplexer (W3.3.4 / W3.3.5).

This is the relay's in-memory routing core. It is intentionally TRANSPORT-FREE:
a ``ScopeTunnel`` is any object exposing ``async def send_frame(Frame)`` and an
identity, so the unit tests drive it with a fake in-memory channel (NO WSS on
the wire) and the production relay wraps a real ``websockets`` connection.

Responsibilities:

  * **Device registration + generation fencing** (W3.3.4). ``HELLO{device_token,
    home_id, generation}`` validates the token, maps it to a stable ``home_id``,
    and pins the live tunnel. A SECOND tunnel for the same home with a HIGHER
    generation EVICTS the older one (the fencing token, reconnect-safe); an
    EQUAL-or-lower generation is REJECTED. A bad/unknown token is refused. One
    scope per token.
  * **stream_id / ws_id allocation.** The RELAY allocates every ``stream_id``
    (browser HTTP exchange) and every ``ws_id`` (browser ``/ws``); the home only
    echoes them back. Ids are unique per home so two browsers never collide.
  * **Routing maps.** ``stream_id -> browser-request waiter`` and
    ``ws_id -> browser-socket`` so a ``RESP_*`` / ``WS_DATA`` frame off the
    tunnel fans out to exactly the right browser (fan-out isolation, W3.3.5(4)).

Affinity (W3.5/§T7(6)): single-instance to start -- this registry is the
``home_id -> live tunnel`` lookup any browser request resolves through. A
multi-instance deploy swaps this for a shared pub/sub without changing the
multiplexer contract.
"""
from __future__ import annotations

import itertools
import secrets
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from .protocol import Frame


class RegistrationError(Exception):
    """A ``HELLO`` was rejected (bad token, stale generation, duplicate)."""


@dataclass
class HomeRegistration:
    """One registered home: the live scope tunnel + its allocators + routing
    maps. There is at most ONE live tunnel per ``home_id`` (the highest
    generation wins)."""

    home_id: str
    generation: int
    tunnel: "ScopeTunnel"
    registered_at: float = field(default_factory=time.time)

    # The relay allocates ALL ids. Start at 1 so 0 stays reserved for control.
    _stream_ids: "itertools.count" = field(
        default_factory=lambda: itertools.count(1)
    )
    _ws_seq: "itertools.count" = field(default_factory=lambda: itertools.count(1))

    # Routing maps. The values are opaque callables/objects the proxy layer
    # registers; the registry only stores + looks them up so it stays
    # transport-free and unit-testable.
    #   stream_id -> a callback that consumes a response frame for that exchange
    #   ws_id     -> a callback that consumes a WS_DATA/WS_CLOSE for that browser
    req_routes: dict = field(default_factory=dict)
    ws_routes: dict = field(default_factory=dict)
    # A deferred registration retains the previous committed route until its
    # HELLO_ACK has actually reached the new home. Hidden from repr/equality so
    # it cannot recursively expose live connection state in logs.
    previous: Optional["HomeRegistration"] = field(
        default=None, repr=False, compare=False)

    def next_stream_id(self) -> int:
        """Allocate a fresh (relay-owned) ``stream_id`` for a browser HTTP
        exchange. Never 0 (reserved control id)."""
        return next(self._stream_ids)

    def next_ws_id(self) -> str:
        """Allocate a fresh ``ws_id`` for a browser ``/ws`` socket. A string so
        it never collides with the integer ``stream_id`` namespace even though
        each WS also occupies one ``stream_id``."""
        return f"ws{next(self._ws_seq)}"


class ScopeTunnel:
    """The relay's view of ONE home-side scope WSS. Transport-free base: the
    production relay subclasses/wraps a real ``websockets`` connection; the unit
    tests use ``FakeScopeTunnel`` (an in-memory channel). The contract is just
    ``async send_frame(frame)`` and a stable ``id``."""

    def __init__(self, send_frame: Callable[[Frame], Awaitable[None]],
                 *, conn_id: str = ""):
        self._send_frame = send_frame
        self.id = conn_id or f"tunnel-{id(self):x}"
        self.closed = False
        self.evicted = False
        self.last_pong = time.monotonic()

    async def send_frame(self, frame: Frame) -> None:
        """Send one tunnel frame to the home. Raises if the tunnel is closed."""
        if self.closed:
            raise RegistrationError(f"tunnel {self.id} is closed")
        await self._send_frame(frame)

    def mark_pong(self) -> None:
        """Record that a PONG arrived (keepalive liveness)."""
        self.last_pong = time.monotonic()

    def is_stale(self, *, ping_interval_s: float, max_misses: int) -> bool:
        """True iff no PONG has arrived within ``max_misses`` ping intervals
        (a half-open NAT'd residential socket sitting dead). The caller tears
        the tunnel down and waits for the scope to re-dial."""
        deadline = ping_interval_s * max_misses
        return (time.monotonic() - self.last_pong) > deadline


class HomeRegistry:
    """The relay's ``home_id -> live tunnel`` table with generation fencing.

    ``validate_token`` resolves a ``device_token`` to a ``home_id`` (the relay
    operator provisions this map out-of-band; a leaked token is the documented
    blast radius, mitigated by mTLS on the dial in production). The registry
    itself only enforces the fencing + one-scope-per-home rules."""

    def __init__(self, token_to_home: Optional[dict] = None,
                 *, ping_interval_s: float = 10.0, max_ping_misses: int = 3):
        # device_token -> home_id (provisioning table; swap for a DB lookup).
        self._token_to_home = dict(token_to_home or {})
        self._homes: dict[str, HomeRegistration] = {}
        self.ping_interval_s = ping_interval_s
        self.max_ping_misses = max_ping_misses

    # -- provisioning ---------------------------------------------------------

    def provision(self, device_token: str, home_id: str) -> None:
        """Register a valid ``device_token -> home_id`` mapping (out-of-band)."""
        self._token_to_home[device_token] = home_id

    def _evict_home(self, home_id: str) -> bool:
        """Fence ``home_id`` at the REGISTRY layer: drop it from the routing
        table and flag its tunnel evicted/closed.

        This alone does NOT close the physical socket, and the relay server
        resolves browser traffic through its own ``connections`` affinity table,
        not this registry -- so a caller that wants the live session gone must
        also run ``RelayState.evict_home`` (the SIGHUP reload does). The registry
        is transport-free by design; it cannot close a WebSocket. Returns True if
        a registration was removed."""
        reg = self._homes.pop(home_id, None)
        if reg is None:
            return False
        reg.tunnel.evicted = True
        reg.tunnel.closed = True
        return True

    def revoke(self, device_token: str) -> bool:
        """Invalidate a device token IMMEDIATELY (OPEN-002).

        Removes the mapping and evicts the home's live tunnel so a leaked token
        cannot keep or resume a session. If the home still holds another valid
        token it re-dials and reconnects; otherwise it stays offline until an
        operator provisions a fresh one. Returns True if a mapping was removed."""
        home_id = self._token_to_home.pop(device_token, None)
        if home_id is None:
            return False
        self._evict_home(home_id)
        return True

    def rotate(self, old_token: str, new_token: str) -> str:
        """Swap a home's device token WITHOUT dropping its live tunnel.

        The planned-rotation path: the new token authenticates future HELLOs,
        the old one stops working, and the current session is undisturbed (for a
        compromised token use ``revoke``). Atomic: a bad ``new_token`` leaves the
        old mapping intact. Returns the affected ``home_id``. Never echoes token
        material in errors."""
        from .config import valid_device_token

        home_id = self._token_to_home.get(old_token)
        if home_id is None:
            raise RegistrationError("unknown device_token")
        if not valid_device_token(new_token):
            raise RegistrationError(
                "new device_token must be 32-256 printable ASCII characters")
        bound = self._token_to_home.get(new_token)
        if bound is not None and bound != home_id:
            raise RegistrationError(
                "new device_token is already bound to another home")
        self._token_to_home[new_token] = home_id
        if new_token != old_token:
            del self._token_to_home[old_token]
        return home_id

    def replace_tokens(self, token_to_home: dict) -> list:
        """Replace the whole provisioning map (a reloaded token file).

        Validates every token BEFORE mutating (atomic), then evicts any home
        whose id no longer has ANY accepted token -- durable revocation without a
        relay restart. Returns the sorted list of evicted home ids."""
        from .config import valid_device_token

        cleaned: dict = {}
        for tok, home in dict(token_to_home).items():
            if not valid_device_token(tok) or not str(home):
                raise RegistrationError("invalid device-token map")
            cleaned[str(tok)] = str(home)
        self._token_to_home = cleaned
        live = set(cleaned.values())
        evicted = [h for h in list(self._homes) if h not in live]
        for home_id in evicted:
            self._evict_home(home_id)
        return sorted(evicted)

    def validate_token(self, device_token: str, claimed_home_id: str) -> str:
        """Resolve a ``device_token`` to its ``home_id``, fail-closed.

        The token is authoritative for the ``home_id`` (the ``claimed_home_id``
        in the HELLO is advisory and, if it disagrees with the provisioned
        mapping, the provisioned value wins -- a home cannot claim another
        home's id by sending a different ``home_id`` in HELLO). Raises
        ``RegistrationError`` on an unknown/blank token."""
        if not device_token:
            raise RegistrationError("missing device_token")
        home_id = None
        if isinstance(device_token, str):
            for provisioned, candidate_home in self._token_to_home.items():
                try:
                    matched = secrets.compare_digest(device_token, provisioned)
                except (TypeError, UnicodeError):
                    matched = False
                if matched:
                    home_id = candidate_home
                    break
        if home_id is None:
            raise RegistrationError("unknown device_token")
        return home_id

    # -- registration / fencing ----------------------------------------------

    def register(self, hello: Frame, tunnel: ScopeTunnel) -> HomeRegistration:
        """Process a ``HELLO`` frame: validate the token, fence by generation,
        and pin the tunnel. Returns the new ``HomeRegistration``.

        Generation rules (W3.3.4): a higher generation EVICTS the prior tunnel
        for the same home (the older ``ScopeTunnel.evicted`` flag is set so the
        proxy layer can close it); an EQUAL-or-lower generation is REJECTED
        (raises) so a stale re-dial can't displace a live newer socket. A
        restart re-dials with a monotonic, restart-surviving generation."""
        device_token = hello.header.get("device_token", "")
        claimed_home = hello.header.get("home_id", "")
        generation = int(hello.header.get("generation", 0))
        home_id = self.validate_token(device_token, claimed_home)

        existing = self._homes.get(home_id)
        if existing is not None:
            if generation <= existing.generation:
                raise RegistrationError(
                    f"stale generation {generation} <= live "
                    f"{existing.generation} for home {home_id}"
                )
        reg = HomeRegistration(home_id=home_id, generation=generation,
                               tunnel=tunnel, previous=existing)
        self._homes[home_id] = reg
        return reg

    def commit(self, reg: HomeRegistration) -> bool:
        """Commit ``reg`` after its positive HELLO_ACK is delivered.

        The previous socket is fenced only now.  This prevents a failed ACK
        write from destroying an otherwise healthy active route.
        """

        if self._homes.get(reg.home_id) is not reg:
            return False
        previous = reg.previous
        reg.previous = None
        if previous is not None:
            previous.tunnel.evicted = True
            previous.tunnel.closed = True
        return True

    def rollback(self, reg: HomeRegistration) -> bool:
        """Undo an uncommitted registration whose ACK was not delivered."""

        if self._homes.get(reg.home_id) is not reg:
            return False
        previous = reg.previous
        reg.previous = None
        if previous is None:
            del self._homes[reg.home_id]
        else:
            self._homes[reg.home_id] = previous
        return True

    def get(self, home_id: str) -> Optional[HomeRegistration]:
        """The live registration for ``home_id`` (or None if no home is
        connected -- a browser request then gets a 502/retry)."""
        return self._homes.get(home_id)

    def unregister(self, home_id: str, tunnel: ScopeTunnel) -> bool:
        """Drop ``home_id`` IFF the live tunnel is still ``tunnel`` (so a tunnel
        that was already evicted by a newer generation does NOT clobber the
        winner on its way out). Returns True if it removed the registration."""
        reg = self._homes.get(home_id)
        if reg is not None and reg.tunnel is tunnel:
            del self._homes[home_id]
            return True
        return False

    def homes(self) -> list[str]:
        """The currently-connected home ids (for status/metrics)."""
        return sorted(self._homes)
