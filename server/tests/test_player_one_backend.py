import pytest
import astrodeck.devices.backends.player_one as pob


def test_backend_manifest_complete():
    b = pob.PlayerOneBackend()
    for f in ("name", "label", "roles", "version", "author", "min_app_version",
              "transport", "hardware", "driver_type", "discoverable", "hostless"):
        assert hasattr(b, f)
    assert b.name == "player-one"
    assert set(b.roles) == {"camera", "guide_camera"}
    assert b.transport == "local" and b.hardware is True and b.hostless is True


async def test_session_builds_camera(monkeypatch):
    from test_player_one_adapter import FakePoaSdk
    from astrodeck.devices.cameras import player_one
    monkeypatch.setattr(player_one, "make_player_one", lambda: FakePoaSdk(8, 6))
    sess = pob.PlayerOneSession()

    class Conn:
        extra = {"name": "Poseidon-M Pro"}

    dev = await sess.get_device("camera", Conn())
    assert dev is not None
    assert dev.sensor_width == 8
    # cached
    assert await sess.get_device("camera", Conn()) is dev
    await sess.close()
