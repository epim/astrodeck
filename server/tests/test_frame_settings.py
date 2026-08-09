"""Frame settings by PURPOSE — one home per (camera, purpose), not per screen.

THE NIGHT THIS CLOSES (2026-08-08): the operator set FILT=R on the Align
screen. Capture then showed Oiii. Capture was correct — Align was rendering a
server-side pin the polar session only acts on inside a solve frame, and with
polar idle nothing had moved. Two surfaces, one wheel, two answers, and neither
said which question it was answering.

Every camera setting behind those screens had the same shape: a private copy
per surface, seeded from a hardcoded constant, invisible to every other
surface and lost on reload. ``GET /api/polar/solve-settings`` existed and no
client had ever called it.

So the assertions here are all one of two forms:

  * SET ON ONE SURFACE, READ ON ANOTHER — the two paths that reach a scope
    must be the same store, not two stores that happen to agree today.
  * SET, THEN RELOAD — a value the server is acting on must be a value the
    server can still tell you about after a restart.

The scopes stay SEPARATE, and that is graded too: a guide camera's exposure is
legitimately not the imaging camera's. Folding them would be as wrong as
splitting them per screen.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
from astrodeck.config import (FRAME_SCOPES, ConfigStore, FrameSettingsConfig,
                              frames_payload, set_frame_settings)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A throwaway store, patched at BOTH names the code reaches it by:
    ``api.app`` bound it at import time, and ``config``'s own helpers read the
    module global. Patching one and not the other is how a suite goes green
    while writing the developer's real config (conftest, 2026-07-29)."""
    s = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", s, raising=False)
    monkeypatch.setattr(app_module, "config_store", s, raising=False)
    return s


@pytest.fixture()
def client(store):
    app = app_module.create_app()
    with TestClient(app) as c:
        c.post("/api/connect/sim")
        yield c


def _wheel_names(client) -> list[str]:
    fw = app_module.hub.devices.get("filterwheel")
    return list(getattr(fw, "filter_names", []) or [])


# ------------------------------------------------------------- the four scopes

def test_every_scope_is_reachable_and_complete(client):
    """One vocabulary. A client that has to special-case a scope's shape will
    grow a private copy for it, which is where this all started."""
    r = client.get("/api/camera/frame-settings")
    assert r.status_code == 200, r.text
    frames = r.json()["frames"]
    assert sorted(frames) == sorted(FRAME_SCOPES), frames
    for scope, fs in frames.items():
        assert sorted(fs) == ["binning", "exposure_s", "filter", "gain",
                              "offset"], f"{scope}: {sorted(fs)}"


def test_the_ws_hello_cold_seeds_every_client(client):
    """Half the defect was that no client ever LEARNED the server's values: the
    Align screen's numbers arrived only on a ``polar`` event, so a reload over a
    live pin showed mirrored defaults while the engine used something else."""
    summary = app_module.hub.summary()
    assert "frames" in summary, (
        "the hello snapshot carries no frame settings, so a freshly-loaded "
        "client can only render constants")
    assert sorted(summary["frames"]) == sorted(FRAME_SCOPES)


def test_a_write_to_one_scope_leaves_the_others_alone(client):
    """The inverse guard, and it matters as much as the agreement ones: a
    0.3 s L solve under a narrowband imaging plan is CORRECT, and a fix that
    folded the scopes together would break it."""
    before = client.get("/api/camera/frame-settings").json()["frames"]
    r = client.put("/api/camera/frame-settings?scope=solve",
                   json={"exposure_s": 12.0, "gain": 411})
    assert r.status_code == 200, r.text
    after = client.get("/api/camera/frame-settings").json()["frames"]
    assert after["solve"]["exposure_s"] == 12.0
    assert after["solve"]["gain"] == 411
    for other in ("capture", "focus", "guide"):
        assert after[other] == before[other], (
            f"{other} moved when solve was set — the scopes are not separate")


def test_an_unknown_scope_is_refused(client):
    r = client.put("/api/camera/frame-settings?scope=bahtinov",
                   json={"gain": 100})
    assert r.status_code == 422, r.text


def test_an_empty_patch_is_refused(client):
    assert client.put("/api/camera/frame-settings?scope=capture",
                      json={}).status_code == 422


# ------------------------------------------- set on one surface, read on another

def test_the_polar_route_and_the_solve_scope_are_ONE_store(client):
    """``/api/polar/solve-settings`` is a delegate, not a second home.

    Two routes onto two stores that happen to agree today is precisely how the
    Align screen and the Capture screen came to answer the same question
    differently. Set through each door, read through the other."""
    assert client.put("/api/polar/solve-settings",
                      json={"exposure_s": 9.0, "gain": 275}).status_code == 200
    frames = client.get("/api/camera/frame-settings").json()["frames"]
    assert frames["solve"]["exposure_s"] == 9.0, (
        "the polar route wrote somewhere the frame-settings route cannot see")
    assert frames["solve"]["gain"] == 275

    assert client.put("/api/camera/frame-settings?scope=solve",
                      json={"exposure_s": 1.5}).status_code == 200
    back = client.get("/api/polar/solve-settings").json()["solve_settings"]
    assert back["exposure_s"] == 1.5, (
        "the frame-settings route wrote somewhere the polar route cannot see")
    assert back["gain"] == 275, "a partial patch clobbered a field it never named"


def test_the_guide_route_and_the_guide_scope_are_ONE_store(client):
    assert client.put("/api/guide/camera-settings",
                      json={"exposure_s": 3.5, "gain": 260}).status_code == 200
    frames = client.get("/api/camera/frame-settings").json()["frames"]
    assert frames["guide"]["exposure_s"] == pytest.approx(3.5)
    assert frames["guide"]["gain"] == 260

    assert client.put("/api/camera/frame-settings?scope=guide",
                      json={"exposure_s": 1.5}).status_code == 200
    back = client.get("/api/guide/camera-settings").json()
    assert back["exposure_s"] == pytest.approx(1.5)


def test_the_engine_reads_the_same_solve_scope_the_route_wrote(client):
    """The last seam: the ROUTE agrees with the STORE, and the store agrees
    with the thing that actually opens the shutter."""
    client.put("/api/camera/frame-settings?scope=solve",
               json={"exposure_s": 6.25, "gain": 333, "binning": 2})
    from astrodeck.polar import native as nat
    cfg = nat._solve_config(app_module.hub.polar)
    assert cfg["exposure_s"] == 6.25
    assert cfg["gain"] == 333
    assert cfg["binning"] == 2


# ------------------------------------------------------------ set, then reload

def test_a_setting_survives_a_restart(client, tmp_path):
    """The un-called GET was half the defect: the value was alive server-side,
    driving the wheel on the next run, and no screen could say so."""
    client.put("/api/camera/frame-settings?scope=capture",
               json={"exposure_s": 47.0, "gain": 33, "offset": 7, "binning": 3})
    reloaded = ConfigStore(tmp_path / "astrodeck.json").cfg()
    assert reloaded.frames.capture.exposure_s == 47.0
    assert reloaded.frames.capture.gain == 33
    assert reloaded.frames.capture.offset == 7
    assert reloaded.frames.capture.binning == 3


def test_an_old_config_without_the_block_loads_and_gets_the_old_constants(
        tmp_path):
    """Additive, like every other appended block. And the defaults are the
    numbers the screens used to hardcode, so an existing rig's first load
    behaves exactly as it did — what changed is where the number lives."""
    import json
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({"version": 3}), encoding="utf-8")
    cfg = ConfigStore(path).cfg()
    assert cfg.frames.capture.exposure_s == 2.0
    assert cfg.frames.capture.gain == 120
    assert cfg.frames.focus.gain == 200, "a focus frame's higher gain is deliberate"
    assert cfg.frames.solve.exposure_s == 0.3
    assert cfg.guide.offset == 30


# ------------------------------------------------------------------ the filter

def test_a_filter_the_wheel_does_not_have_is_refused(client):
    r = client.put("/api/camera/frame-settings?scope=solve",
                   json={"filter": "Ultraviolet"})
    assert r.status_code == 422, r.text
    assert "Ultraviolet" in r.text


def test_a_pinned_filter_does_NOT_move_the_wheel(client):
    """Deliberate, and asserted rather than assumed: the pin is an intent that
    the SOLVE FRAME applies. A fix that made the route move the wheel is also a
    defensible fix — it would have to update this test on purpose."""
    names = _wheel_names(client)
    assert len(names) > 1, names
    before = client.get("/api/status").json()["filterwheel"]["position"]
    target = next(i for i, n in enumerate(names) if i != before)
    assert client.put("/api/camera/frame-settings?scope=solve",
                      json={"filter": names[target]}).status_code == 200
    after = client.get("/api/status").json()["filterwheel"]["position"]
    assert after == before, (
        "the pin moved the wheel — if that is now the intent, this test says "
        "so out loud instead of the UI quietly disagreeing with the rig")


def test_the_guide_camera_refuses_a_filter(client):
    """It has no wheel. Storing the intent would be a setting nothing will ever
    act on — the shipped-dead-setting shape this whole change exists to close."""
    r = client.put("/api/camera/frame-settings?scope=guide",
                   json={"filter": _wheel_names(client)[0]})
    assert r.status_code == 422, r.text


def test_a_blank_filter_name_is_not_no_filter(store):
    """``""`` would read back as "a filter is pinned" and pin nothing."""
    frames = FrameSettingsConfig()
    frames.solve.filter = "   "
    with pytest.raises(ValueError):
        store.set_frames(frames)


# ------------------------------------------------------- #187: the guide offset

def test_the_guide_offset_round_trips(client):
    """#187. The guide loop has applied ``self._offset`` to every exposure
    since it was written; ``GuideConfig`` had no field for it and the route
    answered a LITERAL 30, so the constructor default was the only value it
    could ever have. Same shape as the constructor-frozen exposure that shipped
    dead beside it."""
    r = client.put("/api/guide/camera-settings", json={"offset": 55})
    assert r.status_code == 200, r.text
    assert r.json()["offset"] == 55
    assert client.get("/api/guide/camera-settings").json()["offset"] == 55
    assert client.get("/api/camera/frame-settings").json()[
        "frames"]["guide"]["offset"] == 55


def test_the_guide_offset_reaches_the_running_guider():
    """Not just the file: the number the next guide exposure will use."""
    from astrodeck.guide.native import NativeGuider
    g = object.__new__(NativeGuider)
    g._exposure_s, g._gain, g._offset, g._binning = 2.0, 100, 30, 1
    g._phase_hint = None
    eff = NativeGuider.set_camera_settings(g, offset=77)
    assert g._offset == 77
    assert eff["offset"] == 77


def test_the_guide_scope_reports_the_LIVE_guider_not_the_file(store):
    """A binning change is refused mid-session, so the file can promise what
    the loop refused. The loop decides what the next frame looks like."""
    class _Live:
        def camera_settings(self):
            return {"exposure_s": 9.0, "gain": 111, "offset": 12, "binning": 2}

    assert frames_payload()["guide"]["gain"] == 100
    assert frames_payload(_Live())["guide"] == {
        "exposure_s": 9.0, "gain": 111, "offset": 12, "binning": 2,
        "filter": None}


# ------------------------------------------------------------------ the event

def test_a_write_announces_itself_on_its_OWN_event(client):
    """Not as a field of ``polar``. ``PolarAlignSession.start()`` resets state
    to ``_idle()``, which carries no ``solve_settings``, and the client applies
    polar events wholesale — so beginning an alignment wiped the operator's
    numbers off every Align screen while the engine went on using them."""
    from astrodeck.events import bus
    q = bus.subscribe()
    try:
        client.put("/api/camera/frame-settings?scope=focus", json={"gain": 175})
        seen = []
        while not q.empty():
            seen.append(q.get_nowait())
        frames = [e for e in seen if e.type == "frames"]
        assert frames, [e.type for e in seen]
        assert frames[-1].data["focus"]["gain"] == 175
    finally:
        bus.unsubscribe(q)


# -------------------------------------------------------------------- the caps

class _FixedPrincipal:
    """Returns one principal for every request (test_rbac_enforcement's idiom)."""
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def test_the_scope_keeps_its_own_capability(client):
    """Unifying where a setting LIVES must not quietly unify who may move the
    mount. A caller with imaging control but no mount control may set the
    capture scope and not the solve scope."""
    from astrodeck.auth import (Principal, reset_active_provider,
                                set_active_provider)
    from astrodeck.auth.capabilities import (CAP_CONTROL_CAPTURE,
                                             CAP_VIEW_STATUS)
    set_active_provider(_FixedPrincipal(Principal(
        role="custom", email=None,
        caps=frozenset({CAP_VIEW_STATUS, CAP_CONTROL_CAPTURE}))))
    try:
        assert client.put("/api/camera/frame-settings?scope=capture",
                          json={"gain": 90}).status_code == 200
        r = client.put("/api/camera/frame-settings?scope=solve",
                       json={"gain": 90})
        assert r.status_code == 403, r.text
        assert "control.mount" in r.text
        # ...and the refused write left nothing behind.
        assert client.get("/api/camera/frame-settings").json()[
            "frames"]["solve"]["gain"] == 200
    finally:
        reset_active_provider()


# ------------------------------------------------- the merge rule, second wall

def test_null_clears_a_field_back_to_its_default(store):
    set_frame_settings("solve", {"exposure_s": 30.0})
    assert frames_payload()["solve"]["exposure_s"] == 30.0
    set_frame_settings("solve", {"exposure_s": None})
    assert frames_payload()["solve"]["exposure_s"] == 0.3


def test_unknown_keys_are_ignored_not_stored(store):
    set_frame_settings("capture", {"exposure_s": 3.0, "roi": "bogus"})
    assert "roi" not in frames_payload()["capture"]
