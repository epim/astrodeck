import pytest
from astrodeck.devices.cameras.adapter import CameraAdapter
from astrodeck.devices.cameras import registry


class _A(CameraAdapter):
    def capabilities(self): raise NotImplementedError
    def open(self, i): pass
    def close(self): pass
    def start_exposure(self, **k): pass
    def image_ready(self): return True
    def read_frame(self): return b""
    def abort(self): pass


def test_register_and_iter():
    registry.clear_registry()
    registry.register_adapter("brandx", _A)
    names = [n for n, _ in registry.iter_adapters()]
    assert "brandx" in names


def test_duplicate_vendor_rejected():
    registry.clear_registry()
    registry.register_adapter("brandx", _A)
    with pytest.raises(ValueError):
        registry.register_adapter("brandx", _A)
