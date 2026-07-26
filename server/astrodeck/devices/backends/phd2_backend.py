"""PHD2 guider backend adapter (Stage A) -- GUIDER ONLY.

WRAPS the existing ``guide.phd2.PHD2Guider`` (JSON-RPC over the PHD2 event socket,
default 127.0.0.1:4400) behind the ``Backend`` / ``BackendSession`` Protocols.
Purely additive and behavior-preserving: it does NOT change what the guider does
-- it just connects one ``PHD2Guider`` and hands it back as both the role device
("guider") and the native guider, so choosing PHD2 for the guider role becomes
DATA in a ``RigSpec`` instead of an ``if/elif`` in the hub.

PHD2 is guider-only: ``roles = ("guider",)``. It exposes no native plate solver
(``native_solver()`` returns None -- the hub picks a solver via ``solve.get_solver``).
It is not auto-discovered over the network (``discoverable = False``): the PHD2
socket is a fixed local endpoint, not a UDP-discoverable Alpaca/NINA service.

NOT BUILDING the managed/supervised PHD2 lifecycle (decided 2026-07-26). The
Stage-A note here used to carry it as a follow-on: auto-launch a headless phd2
process, sync its equipment profile from the device config, auto-restart on
crash. That is now deliberately closed as won't-do, because the premise it was
written under has been inverted:

* The native Rust guider SHIPPED and is the DEFAULT wherever it can run. In
  ``providers._resolve_guide`` PHD2 is the last-resort *degradation* path --
  "anything else degrades to the PHD2 bridge" -- not the normal one. A sim rig
  and an Alpaca/native rig both run the native engine.
* The project's direction is explicitly vendor-neutral, no external software to
  install or babysit. Supervising a third-party GUI process is the opposite of
  that, and it is a real surface: process launch, profile sync, crash detection,
  restart backoff, and version skew against whatever phd2 the user happens to
  have.
* So the work would add that surface to the ONE path we are retiring, and buy
  nothing for the path everyone actually uses.

If PHD2 supervision is ever genuinely wanted (say a user insists on PHD2 for a
mount the native engine cannot calibrate), the cheap version is a documented
external supervisor -- systemd unit / Task Scheduler entry -- not code in here.
Stage A's behaviour stands unchanged: this WRAPS an already-running guider plus
the existing in-client reconnect loop, and ``open()`` assumes PHD2 is up and
listening, exactly as ``hub.connect_phd2`` does.

The ``PHD2Guider`` import is deferred into ``open()`` so merely importing this
module (to populate the registry) does not drag in the guide/event stack.
"""
from __future__ import annotations

from astrodeck import __version__ as _app_version

from ..backend import BackendSession, ConnSpec, register

#: PHD2's default event-socket endpoint when a ConnSpec leaves host/port unset.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4400


class Phd2Session:
    """A live connection to one PHD2 instance.

    Holds the single connected ``PHD2Guider`` built in ``Phd2Backend.open()`` and
    hands it back for the only role PHD2 fills ("guider"), as well as via
    ``native_guider()``. The guider object is the very same instance the hub uses
    today, with its own reconnect loop -- this session is a thin pass-through.
    """

    name = "phd2"

    def __init__(self, guider: object) -> None:
        # ``guider`` is a connected guide.phd2.PHD2Guider (duck-typed here to keep
        # the contract import-light and to let tests pass a fake).
        self._guider = guider

    async def get_device(self, role: str, conn: ConnSpec) -> object:
        """Return the connected guider for ``role == "guider"``.

        ``conn`` is accepted for interface parity but unused -- the guider was
        already opened with its address in ``open()``. Any other role raises
        ``KeyError``: PHD2 is guider-only."""
        if role != "guider":
            raise KeyError(
                f"phd2 backend only provides role 'guider', not {role!r}"
            )
        return self._guider

    def native_guider(self) -> object | None:
        """The PHD2 guider itself. SYNC by contract."""
        return self._guider

    def guide_camera(self) -> object | None:
        """PHD2 owns no dedicated guide-camera device (it guides via its own
        socket); None per the Protocol default. SYNC by contract."""
        return None

    def native_solver(self) -> object | None:
        """PHD2 has no plate solver -- the hub chooses one via
        ``solve.get_solver``. SYNC by contract."""
        return None

    async def health(self) -> dict | None:
        """A small link-health snapshot: connected flag plus current guide stats.

        ``stats()`` is the guider's own ``GuideStats`` dataclass; we expose its
        dict form so the API/UI can show RMS/SNR/guiding without reaching into the
        guider. Never raises -- a missing/oddly-shaped ``stats`` degrades to just
        the connected flag."""
        connected = bool(getattr(self._guider, "connected", False))
        out: dict = {"connected": connected}
        stats = getattr(self._guider, "stats", None)
        if callable(stats):
            try:
                s = stats()
            except Exception:
                s = None
            if s is not None:
                # GuideStats is a dataclass; fall back to vars() for a fake.
                out["stats"] = dict(getattr(s, "__dict__", {})) or _as_dict(s)
        return out

    async def close(self) -> None:
        """Disconnect AstroDeck's view of PHD2.

        Calls the guider's ``disconnect()`` (idempotent in ``PHD2Guider``). Bridge
        semantics: this closes our socket only -- it never shuts down the external
        PHD2 application itself."""
        disconnect = getattr(self._guider, "disconnect", None)
        if callable(disconnect):
            await disconnect()


def _as_dict(s: object) -> dict:
    """Best-effort dict view of a stats object lacking ``__dict__`` (defensive)."""
    try:
        return dict(vars(s))
    except Exception:
        return {}


class Phd2Backend:
    """The PHD2 guider vendor adapter.

    ``open()`` constructs a ``PHD2Guider`` at the ConnSpec's host/port (defaulting
    to 127.0.0.1:4400), connects it, and wraps it in a ``Phd2Session``. Guider-only
    and not discoverable.
    """

    name = "phd2"
    label = "PHD2"
    roles = ("guider",)
    discoverable = False
    #: Endpoint-less in unmanaged-local mode: the PHD2 socket is a fixed local
    #: endpoint, so the orchestrator normalizes host/port -> None and a
    #: stray-addressed phd2-local guider override still resolves to its session
    #: under ``("phd2", None, None)`` (W1.3).
    hostless = True
    version = _app_version
    #: Explicit (equal to Protocol defaults) so isinstance(Backend) still holds
    #: -- see native_backend.py's note.
    author = ""
    min_app_version = "0"
    transport = "network"
    hardware = False
    driver_type = "phd2"

    async def open(self, conn: ConnSpec) -> BackendSession:
        """Connect a ``PHD2Guider`` and return a session over it.

        Deferred import of ``PHD2Guider`` so registering this backend at import
        time stays cheap and cycle-free. The pixel scale, if supplied, rides in
        ``conn.extra['pixel_scale_arcsec']`` (the guider's existing kwarg); absent
        it, the guider's own default applies -- behavior-preserving."""
        from ...guide.phd2 import PHD2Guider

        host = conn.host or DEFAULT_HOST
        port = conn.port or DEFAULT_PORT
        kwargs: dict = {}
        scale = (conn.extra or {}).get("pixel_scale_arcsec")
        if scale is not None:
            kwargs["pixel_scale_arcsec"] = scale
        guider = PHD2Guider(host=host, port=port, **kwargs)
        await guider.connect()
        return Phd2Session(guider)

    async def discover(self) -> list[dict]:
        """PHD2's socket is a fixed local endpoint, not UDP-discoverable."""
        return []


# Self-register at import (last-registration-wins). ``register`` returns the
# instance; keep a module-level handle for convenience/introspection.
PHD2_BACKEND = register(Phd2Backend())
