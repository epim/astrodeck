"""Per-device STA apartment thread + per-call deadline (COM-T2).

ASCOM drivers are STA: each ComDevice owns ONE thread that CoInitializeEx's an
apartment for the device's whole lifetime, and every COM access (create,
Connected=, property/method) marshals to it via a queue + Future. Every
marshaled call has a deadline (the AlpacaCamera.expose imageready-timeout idiom,
commit 082dd79): future.result(timeout=...) -> a wedged COM call raises
ComTimeoutError instead of hanging the host.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as _FutureTimeout
from typing import Any, Callable

try:  # comtypes is Windows-only + optional; guarded so import never fails.
    import comtypes  # noqa: F401
    import comtypes.client  # noqa: F401
    _HAVE_COMTYPES = True
except Exception:  # pragma: no cover - non-Windows / wheel absent
    comtypes = None  # type: ignore
    _HAVE_COMTYPES = False

# Per COM-call deadline (s). Mirrors alpaca._IMAGEREADY_POLL_MARGIN_S (082dd79).
_COM_CALL_TIMEOUT_S = 30.0


class ComTimeoutError(Exception):
    """A marshaled COM call exceeded its per-call deadline."""


def _co_init() -> None:
    if _HAVE_COMTYPES:  # STA apartment for this device's lifetime
        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)


def _co_uninit() -> None:
    if _HAVE_COMTYPES:
        comtypes.CoUninitialize()


def _create_com(progid: str):  # pragma: no cover - real COM path (Windows)
    return comtypes.client.CreateObject(progid)


class ComDevice:
    """One ASCOM COM driver pinned to a dedicated STA thread."""

    def __init__(self, progid: str, *, create: Callable[[str], Any] = _create_com,
                 timeout_s: "float | None" = None):
        # NOTE: default is the sentinel None (NOT `timeout_s=_COM_CALL_TIMEOUT_S`),
        # resolved to the *current* module-level _COM_CALL_TIMEOUT_S here at
        # construction time. A literal default expression binds ONCE at def-time,
        # so it could never see a monkeypatched _COM_CALL_TIMEOUT_S (the harness
        # test patches the module global then constructs a ComDevice). The
        # documented default value (30.0) is preserved; only the binding time
        # moves from def-time to call-time.
        self.progid = progid
        self._create = create
        self._timeout_s = _COM_CALL_TIMEOUT_S if timeout_s is None else timeout_s
        self._q: "queue.Queue" = queue.Queue()
        self._obj: Any = None
        self.connected = False
        self._started = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"comdev-{progid}", daemon=True)
        self._thread.start()
        self._started.wait()

    def _run(self) -> None:
        _co_init()
        self._started.set()
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                fn, fut = item
                if fut.set_running_or_notify_cancel():
                    try:
                        fut.set_result(fn(self._obj))
                    except BaseException as e:  # marshal the error back
                        fut.set_exception(e)
        finally:
            _co_uninit()

    def submit(self, fn: Callable[[Any], Any]) -> Any:
        """Run fn(self._obj) on the STA thread with the per-call deadline."""
        fut: Future = Future()
        self._q.put((fn, fut))
        try:
            return fut.result(timeout=self._timeout_s)
        except _FutureTimeout:
            raise ComTimeoutError(
                f"COM call on {self.progid} exceeded "
                f"{self._timeout_s:.0f}s deadline") from None

    def connect(self) -> None:
        def _do(_obj):
            self._obj = self._create(self.progid)
            self._obj.Connected = True
        # _obj is None until created, so run against self, not the arg:
        fut: Future = Future()
        self._q.put((lambda _ignored: _do(_ignored), fut))
        fut.result(timeout=self._timeout_s)
        self.connected = True

    def disconnect(self) -> None:
        if self._obj is not None:
            try:
                self.submit(lambda o: setattr(o, "Connected", False))
            except Exception:  # best-effort; we are tearing down anyway
                pass
        self.connected = False
        self._q.put(None)  # stop the STA loop
