"""COM-T2: the per-device STA thread marshals every call to ONE thread and
enforces a per-call deadline. Proven with a FAKE COM object (no comtypes)."""
import threading
import time

import pytest

from astrodeck.comhost.device import ComDevice, ComTimeoutError


class _FakeObj:
    def __init__(self):
        self.Connected = False
        self.call_threads = []

    def RightAscension(self):  # a "property read" the handler would do
        self.call_threads.append(threading.get_ident())
        return 12.3


def test_all_calls_run_on_one_dedicated_thread():
    obj = _FakeObj()
    dev = ComDevice("Fake.ProgID", create=lambda pid: obj)
    dev.connect()
    assert obj.Connected is True
    for _ in range(20):
        assert dev.submit(lambda o: o.RightAscension()) == 12.3
    # every marshaled call ran on the SAME (single) STA thread
    assert len(set(obj.call_threads)) == 1
    # ...and NOT the caller's thread
    assert threading.get_ident() not in obj.call_threads
    dev.disconnect()
    assert dev.connected is False


def test_wedged_call_raises_timeout_not_hang(monkeypatch):
    import astrodeck.comhost.device as dev_mod
    monkeypatch.setattr(dev_mod, "_COM_CALL_TIMEOUT_S", 0.2)

    class _Wedged:
        Connected = False
        def slow(self):
            time.sleep(5.0)  # far past the 0.2s deadline

    dev = ComDevice("Fake.Wedged", create=lambda pid: _Wedged())
    dev.connect()
    t0 = time.monotonic()
    with pytest.raises(ComTimeoutError):
        dev.submit(lambda o: o.slow())
    assert time.monotonic() - t0 < 2.0  # raised on the deadline, did not hang
    dev.disconnect()
