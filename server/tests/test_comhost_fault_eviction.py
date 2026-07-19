"""COM-T6 host-side obligations (carried from the COM-T2 review):

  1. Fault-eviction (Medium): a COM call that times out FAULT-EVICTS its device
     — the slot + STA thread are dropped so the NEXT call to the SAME device runs
     on a FRESH thread and succeeds. Recovery is per-device, not "restart the
     whole host" (the wedge no longer head-of-line-blocks the device forever).
  2. Thread-teardown (Low): the STA thread is actually joined/gone after a normal
     disconnect() — the clean-path reap was previously unproven.
  3. Connect-state ownership (nit): connect() is idempotent under a concurrent
     double-connect — N overlapping connects create EXACTLY ONE COM object.

All proven with FAKE COM objects (no comtypes), so they run on every platform.
"""
import threading
import time

import pytest

import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.ascom_registry import AscomDriver


# ------------------------------------------------------------- obligation 1
def test_timeout_fault_evicts_then_fresh_thread_succeeds(monkeypatch):
    """A device wedges once (COM call > deadline) -> HTTP 500 + eviction; the
    NEXT call to the same (dev_type, dev_num) builds a fresh ComDevice and
    succeeds on a DIFFERENT STA thread than the wedged one."""
    threads_seen: list[int] = []
    state = {"wedge": True}

    class _SlowThenFast:
        Connected = False

        def read(self):
            threads_seen.append(threading.get_ident())
            if state["wedge"]:
                time.sleep(5.0)          # far past the 0.2s deadline -> timeout
            return 99

    # Fresh ComDevice (fresh STA thread) each _make_com_device, short deadline.
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _SlowThenFast(),
                                 timeout_s=0.2))
    # A fake device-type method table so the dispatch has a route to call.
    monkeypatch.setitem(server.DEVICE_API, "faketype",
                        {"get": {"read": lambda obj, params: obj.read()},
                         "put": {}})
    host = server.ComHost(drivers=[
        AscomDriver("FakeType", "faketype", "Fake.Dev", "Fake", 0)])

    # Connect the device (creates the COM object on its STA thread), as the real
    # Alpaca client does before any property read.
    host.handle("put", "faketype", 0, "connected", {"Connected": "true"})

    # First read wedges past the deadline -> ComTimeoutError -> HTTP 500 + the
    # device is FAULT-EVICTED (slot dropped, wedged thread abandoned).
    status, body, _ = host.handle("get", "faketype", 0, "read", {})
    assert status == 500
    assert body["ErrorNumber"] != 0
    assert ("faketype", 0) not in host._devices  # slot was fault-evicted

    # Recovery is per-device, NOT "restart the whole host": reconnecting rebuilds
    # a FRESH ComDevice (fresh STA thread) and the same call now succeeds.
    state["wedge"] = False
    host.handle("put", "faketype", 0, "connected", {"Connected": "true"})
    status, body, _ = host.handle("get", "faketype", 0, "read", {})
    assert status == 200
    assert body["Value"] == 99
    assert threads_seen[0] != threads_seen[-1]  # fresh STA thread, not the wedge

    host.close()


# ------------------------------------------------------------- obligation 2
def test_sta_thread_joined_and_gone_after_disconnect():
    """Normal-path close reaps the apartment thread (join/is_alive)."""
    class _Fake:
        Connected = False

    dev = ComDevice("Fake.ProgID", create=lambda pid: _Fake())
    dev.connect()
    t = dev._thread
    assert t.is_alive()          # STA thread is up for the device's lifetime
    dev.disconnect()
    assert not t.is_alive()      # ...and actually joined/gone on close
    assert dev.connected is False


# ------------------------------------------------------------- obligation 3
def test_connect_idempotent_under_concurrent_double_connect():
    """N overlapping connect()s create EXACTLY ONE COM object (no double-create
    race). The sidecar is threaded, so two Connected=true PUTs can land at once."""
    created: list[object] = []

    def _create(progid):
        obj = type("_Obj", (), {"Connected": False})()
        created.append(obj)
        return obj

    dev = ComDevice("Fake.ProgID", create=_create)
    n = 8
    barrier = threading.Barrier(n)

    def _go():
        barrier.wait()           # maximize the overlap
        dev.connect()

    workers = [threading.Thread(target=_go) for _ in range(n)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=10)

    assert dev.connected is True
    assert len(created) == 1     # idempotent: one COM object, not n
    dev.disconnect()
