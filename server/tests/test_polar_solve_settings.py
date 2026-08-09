"""Operator-tunable imaging settings for the native TPPA's solve frames.

Until 2026-08-07 every solve frame was hardcoded at the capture call:
``cam.expose(0.3, 200, 30, binning=1)``, wheel untouched. On a night of
marginal solves — six failures in eleven minutes on 2026-08-06 — the operator
had no move at all: not a longer exposure, not more gain, not the L filter.

The settings are read PER FRAME, so a PUT mid-run applies to the very next
solve. That is the point: when solves start failing behind thin cloud, the fix
is a longer exposure NOW, not a restarted session.

2026-08-08 (#176): they no longer live on the SESSION. They are the ``solve``
scope of the persisted frame settings, and the session is one reader of them —
because a private dict on a session object died with the process, was published
as a field of the ``polar`` event (which ``start()`` resets), and was never read
back by any client. The route and its contract are unchanged; this file still
grades that contract, and now also grades that the value survives the session.
"""
from __future__ import annotations

import numpy as np
import pytest

from astrodeck.config import FrameSettingsConfig, config_store
from astrodeck.events import bus
from astrodeck.polar import native as nat
from astrodeck.polar.session import PolarAlignSession

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_frames():
    """The scope is PERSISTED now, so a test that sets it would otherwise leak
    into whatever runs next in this worker. Reset before and after."""
    config_store.set_frames(FrameSettingsConfig())
    yield
    config_store.set_frames(FrameSettingsConfig())


class _Frame:
    timestamp = 1000.0
    data = np.zeros((4, 6), dtype="u2")


class _Cam:
    name = "cam"

    def __init__(self):
        self.exposures: list[tuple] = []

    async def expose(self, exposure_s, gain, offset, binning=1):
        self.exposures.append((exposure_s, gain, offset, binning))
        return _Frame()


class _Wheel:
    connected = True
    filter_names = ["L", "R", "G", "B", "S", "Ha", "Oiii", "Dark"]

    def __init__(self):
        self.position = 7
        self.moves: list[int] = []

    async def get_position(self):
        return self.position

    async def set_position(self, slot):
        self.moves.append(slot)
        self.position = slot


class _Tel:
    async def get_position(self):
        return 5.0, 40.0


class _Solver:
    async def solve(self, path, **kw):
        class _R:
            success = True
            ra_hours, dec_deg = 5.0, 40.0
            rotation_deg, pixel_scale_arcsec = 0.0, 1.55
            message = "ok"
        return _R()


class _Hub:
    def __init__(self):
        self.cam, self.tel, self.wheel = _Cam(), _Tel(), _Wheel()
        self.devices = {"filterwheel": self.wheel}
        self.site = {"latitude": 45.0, "longitude": -122.0}

    def require(self, role):
        return self.cam if role == "camera" else self.tel

    async def from_mount_frame(self, tel, ra, dec):
        return ra, dec

    def effective_optics(self):
        return {"fov_h_deg": 1.2, "image_scale_arcsec_px": 1.55}

    def exposure_guard(self, what):
        import contextlib
        return contextlib.nullcontext()

    async def _publish_preview(self, frame):
        pass


class _Session(PolarAlignSession):
    def __init__(self):
        super().__init__(hub=None)
        self.published: list[dict] = []

    def _publish(self, **kw):
        self.published.append(dict(kw))
        super()._publish(**kw)


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch, tmp_path):
    import astrodeck.hub as hub_module
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    import astrodeck.imaging as imaging
    monkeypatch.setattr(imaging, "save_fits",
                        lambda frame, path, **kw: None)


# ------------------------------------------------------------------ the model

def test_the_defaults_are_the_values_that_were_hardcoded():
    """Nothing set = exactly the historical behaviour, field for field."""
    s = _Session()
    assert s.solve_settings == {"exposure_s": 0.3, "gain": 200, "offset": 30,
                                "binning": 1, "filter": None}


def test_a_partial_set_merges_and_a_none_clears():
    s = _Session()
    s.set_solve_settings(exposure_s=2.0, gain=300)
    assert s.solve_settings["exposure_s"] == 2.0
    assert s.solve_settings["gain"] == 300
    assert s.solve_settings["binning"] == 1, "unset fields keep their defaults"

    s.set_solve_settings(exposure_s=None)
    assert s.solve_settings["exposure_s"] == 0.3, "None clears to the default"
    assert s.solve_settings["gain"] == 300, "...without touching the others"


def test_unknown_keys_are_ignored_not_stored():
    """The route already validates; this is the second wall, so a future caller
    cannot smuggle an arbitrary field into the capture call."""
    s = _Session()
    s.set_solve_settings(exposure_s=1.0, roi="bogus")
    assert "roi" not in s.solve_settings


async def test_setting_publishes_so_every_client_renders_the_same_numbers():
    """On the ``frames`` event, NOT as a field of ``polar``.

    Piggybacking on the session event is what made ``start()`` — which resets
    ``state`` to ``_idle()``, a dict with no ``solve_settings`` key — wipe the
    numbers off every Align screen the instant an alignment began, while the
    engine went on solving at the operator's values. A setting outlives the
    session that reads it, so it gets its own event.
    """
    q = bus.subscribe()
    try:
        s = _Session()
        s.set_solve_settings(gain=250)
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        frames = [e for e in events if e.type == "frames"]
        assert frames, [e.type for e in events]
        assert frames[-1].data["solve"]["gain"] == 250, frames[-1].data
        assert not any(
            "solve_settings" in p for p in s.published), (
            "solve settings rode the polar event again — start() erases that")
    finally:
        bus.unsubscribe(q)


def test_the_setting_outlives_the_session_object():
    """The reload half of the defect. A pin set in one session was invisible to
    the next one and to every client that reconnected — the server-side value
    was alive and driving the wheel while the screen showed a default."""
    _Session().set_solve_settings(exposure_s=7.5, gain=333)
    assert _Session().solve_settings["exposure_s"] == 7.5
    assert _Session().solve_settings["gain"] == 333


def test_starting_a_run_does_not_erase_the_settings():
    """``start()`` resets ``state`` to ``_idle()``. That used to take the
    solve settings with it, on every client, mid-alignment."""
    s = _Session()
    s.set_solve_settings(exposure_s=4.0, filter=None)
    s.state = s._idle()          # exactly what start() does before dispatch
    assert s.solve_settings["exposure_s"] == 4.0


# ------------------------------------------------------------ the capture path

async def test_the_capture_uses_the_sessions_settings():
    hub, session = _Hub(), _Session()
    session.set_solve_settings(exposure_s=2.5, gain=120, binning=2)
    await nat._capture_and_solve(hub, _Solver(), session)
    assert hub.cam.exposures == [(2.5, 120, 30, 2)]


async def test_no_session_keeps_the_historical_hardcoded_values():
    """Callers that pass no session — the older tests, mostly — see exactly the
    pre-2026-08-07 exposure."""
    hub = _Hub()
    await nat._capture_and_solve(hub, _Solver())
    assert hub.cam.exposures == [(0.3, 200, 30, 1)]


async def test_a_named_filter_moves_the_wheel_before_the_exposure():
    hub, session = _Hub(), _Session()
    session.set_solve_settings(filter="L")
    await nat._capture_and_solve(hub, _Solver(), session)
    assert hub.wheel.moves == [0], "Dark (slot 7) -> L (slot 0), once"
    # and a second frame does not command a move the wheel already made
    await nat._capture_and_solve(hub, _Solver(), session)
    assert hub.wheel.moves == [0]


async def test_no_filter_setting_leaves_the_wheel_alone():
    hub, session = _Hub(), _Session()
    await nat._capture_and_solve(hub, _Solver(), session)
    assert hub.wheel.moves == []


async def test_a_wheel_failure_does_not_end_the_solve(monkeypatch):
    """Best-effort: an alignment that never needed the wheel must not die of
    one."""
    hub, session = _Hub(), _Session()
    session.set_solve_settings(filter="L")

    async def broken(slot):
        raise RuntimeError("wheel jam")
    monkeypatch.setattr(hub.wheel, "set_position", broken)
    frame, result, _geom = await nat._capture_and_solve(hub, _Solver(), session)
    assert result.success


# ----------------------------------------------------------------- activity

async def test_the_activity_field_narrates_the_frame_and_always_clears():
    """The status strip's input: exposing → solving → None. A solve can take
    15 s, and a screen that changes nothing for 15 s reads as hung."""
    hub, session = _Hub(), _Session()
    await nat._capture_and_solve(hub, _Solver(), session)
    acts = [p["activity"] for p in session.published if "activity" in p]
    assert acts == ["exposing", "solving", None], acts
    assert session.state["activity"] is None


async def test_activity_clears_even_when_the_solve_fails():
    class _NoStars(_Solver):
        async def solve(self, path, **kw):
            class _R:
                success = False
                message = "Not enough stars."
            return _R()

    hub, session = _Hub(), _Session()
    from astrodeck.devices.base import DeviceError
    with pytest.raises(DeviceError):
        await nat._capture_and_solve(hub, _NoStars(), session)
    assert session.state["activity"] is None, (
        "a failed solve leaving 'solving' on screen is the exact lie the "
        "field exists to remove")
