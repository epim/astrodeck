import pytest
import astrodeck.devices.backends.zwo_asi as zab


def test_backend_manifest_complete():
    b = zab.ZwoAsiBackend()
    for f in ("name", "label", "roles", "version", "author", "min_app_version",
              "transport", "hardware", "driver_type", "discoverable", "hostless"):
        assert hasattr(b, f)
    assert b.name == "zwo-asi"
    assert set(b.roles) == {"camera", "guide_camera"}
    assert b.transport == "local" and b.hardware is True and b.hostless is True


async def test_session_builds_guide_camera(monkeypatch):
    from test_zwo_asi_adapter import FakeAsiSdk
    from astrodeck.devices.cameras import zwo_asi
    monkeypatch.setattr(zwo_asi, "make_asi", lambda: FakeAsiSdk(8, 6))
    sess = zab.ZwoAsiSession()

    class Conn:  # minimal ConnSpec stand-in
        extra = {"name": "ZWO ASI220MM"}

    dev = await sess.get_device("guide_camera", Conn())
    assert dev is not None
    assert sess.guide_camera() is dev
    # cached: second call returns the same instance
    assert await sess.get_device("guide_camera", Conn()) is dev
    await sess.close()


async def test_session_rejects_non_camera_role():
    from test_zwo_asi_adapter import FakeAsiSdk
    from astrodeck.devices.cameras import zwo_asi
    from astrodeck.devices.base import DeviceError
    zwo_asi_make = zwo_asi.make_asi
    zwo_asi.make_asi = lambda: FakeAsiSdk(8, 6)
    try:
        sess = zab.ZwoAsiSession()

        class Conn:
            extra = {}
        with pytest.raises(DeviceError):
            await sess.get_device("focuser", Conn())
    finally:
        zwo_asi.make_asi = zwo_asi_make
