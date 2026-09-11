"""D-RIG-4: the touch pad's ceiling is the DRIVER's number.

Before this, ``POST /api/mount/move`` clamped every mount to
``TOUCH_MAX_RATE_DEG_S`` (0.6 deg/s) - a constant chosen for the slowest thing
that might be on the other end. The AM5 delivers 1.44 deg/s at R8 and has the
hardware calibration to prove it, so the clamp now asks the telescope and only
falls back to 0.6 when the telescope cannot say.

WHAT IS TESTED WHERE. The clamp lives in app.py's ``/api/mount/move`` handler
(task S7L owns that file), so the route tests here mount a THROWAWAY FastAPI
app whose handler carries the two clamp lines VERBATIM from the patch S7g
delivers, plus the real ``hub.note_move`` call in its real position - before
the move await, so the deadman is armed with the clamped rate. One test at the
bottom drives the REAL ``/api/mount/move`` for the fallback half, which holds
both before and after the patch lands.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import CAP_CONTROL_MOUNT, require
from astrodeck.auth.rbac import assert_route_capabilities, declare
from astrodeck.devices.backends.zwo_am5 import (_RATE_TABLE, ZwoAm5Telescope)
from astrodeck.devices.base import Telescope
from astrodeck.hub import TOUCH_MAX_RATE_DEG_S

# --------------------------------------------------------------- the capability


def test_a_telescope_that_cannot_say_reports_none():
    """The base class default. None is UNKNOWN, not "no limit" - see the
    field's own comment; the clamp turns it into 0.6, never into infinity."""
    assert Telescope.max_rate_deg_s is None


def test_the_am5_publishes_its_measured_r8_rate():
    assert ZwoAm5Telescope.max_rate_deg_s == 1.44


def test_the_am5_ceiling_is_the_r8_multiple_the_rate_table_records():
    """``_RATE_TABLE``'s calibration note says R8 ~ 344x sidereal = 1.44 deg/s.

    Tying the published ceiling back to the multiple means the number cannot
    drift away from the measurement it came from without this going red.
    """
    sidereal = ZwoAm5Telescope._SIDEREAL_DEG_S
    assert ZwoAm5Telescope.max_rate_deg_s == pytest.approx(344 * sidereal,
                                                           abs=0.005)


def test_the_am5_ceiling_lands_on_the_top_row_of_the_rate_table():
    """The ceiling has to BE a rate the driver can select, not a number beside
    the table: at 1.44 the lookup falls through to R8, the top row."""
    for bound, rate_cmd in _RATE_TABLE:
        if abs(ZwoAm5Telescope.max_rate_deg_s) <= bound:
            break
    assert rate_cmd == "R8"
    # R9 exists on the mount and is deliberately NOT here: the same note says
    # its measurement was acceleration-ramp-limited, so nobody knows what it
    # sustains. An unmeasured ceiling is a number, not a limit.
    assert "R9" not in [cmd for _, cmd in _RATE_TABLE]


class _RecordingLink:
    """Minimal SerialLink double: records fire-and-forget commands."""

    is_open = True

    def __init__(self):
        self.sent: list[str] = []

    async def request(self, cmd: str, *, reply: str = "hash",
                      timeout: float = 1.5):
        self.sent.append(cmd)
        return None


async def test_the_am5_actually_selects_r8_at_its_own_ceiling():
    """The published ceiling is a rate the driver DELIVERS, not one it clips.
    Asking for exactly ``max_rate_deg_s`` must reach the wire as R8 + Me."""
    link = _RecordingLink()
    tel = ZwoAm5Telescope(link)
    await tel.move_axis("ra", ZwoAm5Telescope.max_rate_deg_s)
    assert link.sent == ["R8", "Me"]


# ------------------------------------------------------------------ the clamp


class _Tel:
    """A mount that records what the clamp handed ``move_axis``."""

    connected = True

    def __init__(self, ceiling=...):
        self.moves: list[tuple[str, float]] = []
        if ceiling is not ...:
            self.max_rate_deg_s = ceiling

    async def get_position(self):
        return 6.0, 40.0

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        self.moves.append((axis, rate_deg_s))


class _Hub:
    """Records the deadman arming. Only the surface the clamp path touches."""

    def __init__(self):
        self.armed: list[tuple[str, float]] = []

    def note_move(self, axis: str, rate: float) -> None:
        self.armed.append((axis, rate))


def _build_app(tel, fake_hub) -> FastAPI:
    app = FastAPI()

    # --- BEGIN mirror of the S7L app.py clamp patch (app.py:5727-5728) -------
    @app.post("/api/mount/move",
              dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.move_axis"})
    async def move_axis(body: app_module.MoveAxisBody):
        ceiling = getattr(tel, "max_rate_deg_s", None) or TOUCH_MAX_RATE_DEG_S
        rate = max(-ceiling, min(ceiling, body.rate_deg_s))
        # ...the sun-cone gate sits here in the real handler; it is not what
        # this file is about and it is unchanged by the patch.
        fake_hub.note_move(body.axis, rate)
        await tel.move_axis(body.axis, rate)
        return {"ok": True, "rate_deg_s": rate}
    # --- END mirror ----------------------------------------------------------

    return app


@pytest.fixture
def clamp():
    def _make(ceiling=...):
        tel, fake_hub = _Tel(ceiling), _Hub()
        return tel, fake_hub, TestClient(_build_app(tel, fake_hub))
    return _make


def test_the_mirrored_route_passes_the_rbac_boot_assertion(clamp):
    tel, fake_hub, client = clamp(1.44)
    assert_route_capabilities(client.app)


def test_a_mount_reporting_1_44_is_clamped_to_1_44_not_to_0_6(clamp):
    tel, fake_hub, client = clamp(1.44)
    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 5.0})
    assert r.status_code == 200, r.text
    assert r.json()["rate_deg_s"] == pytest.approx(1.44)
    assert tel.moves == [("ra", pytest.approx(1.44))]
    # The deadman is armed with the CLAMPED rate, not the requested one: a
    # watchdog sized off 5.0 deg/s would be describing a move that never
    # happened, and one sized off 0.6 would under-describe this one.
    assert fake_hub.armed == [("ra", pytest.approx(1.44))]
    assert fake_hub.armed != [("ra", pytest.approx(TOUCH_MAX_RATE_DEG_S))]


def test_the_clamp_is_symmetric_westward(clamp):
    tel, fake_hub, client = clamp(1.44)
    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": -5.0})
    assert r.json()["rate_deg_s"] == pytest.approx(-1.44)
    assert fake_hub.armed == [("ra", pytest.approx(-1.44))]


def test_a_mount_that_cannot_say_falls_back_to_0_6(clamp):
    """None is UNKNOWN. A backend that cannot report its rates does not get a
    faster pad than one that can - it gets the conservative old constant."""
    tel, fake_hub, client = clamp(None)
    r = client.post("/api/mount/move", json={"axis": "dec", "rate_deg_s": 5.0})
    assert r.json()["rate_deg_s"] == pytest.approx(TOUCH_MAX_RATE_DEG_S)
    assert tel.moves == [("dec", pytest.approx(0.6))]
    assert fake_hub.armed == [("dec", pytest.approx(0.6))]


def test_a_backend_with_no_such_attribute_at_all_falls_back(clamp):
    """A driver written before the field existed must behave exactly as it did
    - the getattr default is what makes the field additive."""
    tel, fake_hub, client = clamp()          # attribute absent entirely
    assert not hasattr(tel, "max_rate_deg_s")
    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 9.0})
    assert r.json()["rate_deg_s"] == pytest.approx(TOUCH_MAX_RATE_DEG_S)


def test_a_rate_inside_the_ceiling_is_passed_through_untouched(clamp):
    tel, fake_hub, client = clamp(1.44)
    r = client.post("/api/mount/move", json={"axis": "dec", "rate_deg_s": 0.3})
    assert r.json()["rate_deg_s"] == pytest.approx(0.3)


def test_a_zero_is_still_a_stop(clamp):
    """The clamp must not turn a STOP into a move. 0.0 is falsy on the way in
    and has to stay 0.0 on the way out."""
    tel, fake_hub, client = clamp(1.44)
    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 0.0})
    assert r.json()["rate_deg_s"] == 0.0
    assert tel.moves == [("ra", 0.0)]


def test_a_ceiling_of_zero_would_be_a_dead_pad_so_it_falls_back(clamp):
    """``or TOUCH_MAX_RATE_DEG_S`` treats a reported 0.0 as "cannot say".

    That is the right reading and it is worth pinning: a driver that reports a
    zero ceiling is not describing a mount that refuses to move, it is
    answering badly - and a pad clamped to 0.0 is a control that does nothing,
    which is the one outcome worse than a slow one.
    """
    tel, fake_hub, client = clamp(0.0)
    r = client.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 5.0})
    assert r.json()["rate_deg_s"] == pytest.approx(TOUCH_MAX_RATE_DEG_S)


# ------------------------------------------------------ the REAL move route


def test_the_real_move_route_falls_back_to_0_6_for_a_mount_that_cannot_say(
        monkeypatch):
    """Drives the shipped ``/api/mount/move``, not a mirror.

    This is the half of the invariant that holds both before and after the S7L
    clamp patch, so it stays a live regression guard on the real handler: a
    mount with no ``max_rate_deg_s`` must keep the 0.6 deg/s pad it has always
    had, and the deadman must be armed with that same clamped number.
    """
    tel = _Tel(None)
    armed: list[tuple[str, float]] = []
    app = app_module.create_app()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: armed.append((axis, rate)))
    with TestClient(app) as c:
        r = c.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 5.0})
    assert r.status_code == 200, r.text
    assert tel.moves == [("ra", pytest.approx(TOUCH_MAX_RATE_DEG_S))]
    assert armed == [("ra", pytest.approx(TOUCH_MAX_RATE_DEG_S))]


def test_the_real_move_route_hands_a_1_44_mount_its_own_ceiling(monkeypatch):
    """The other half, on the SHIPPED handler rather than the mirror.

    Everything above this line about 1.44 was graded against the throwaway app
    at the top of the file, so the whole point of the change - a driver that
    CAN say gets its own measured figure - was only ever asserted against a
    copy of the two clamp lines. A copy cannot fail when the original does:
    deleting the ``getattr(tel, "max_rate_deg_s", ...)`` from app.py left every
    test in this file green and every AM5 jog back at 0.6 deg/s.
    """
    tel = _Tel(1.44)
    armed: list[tuple[str, float]] = []
    app = app_module.create_app()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: armed.append((axis, rate)))
    with TestClient(app) as c:
        r = c.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 5.0})
    assert r.status_code == 200, r.text
    # The real route answers ``{"ok": true}`` and not the rate, so the DRIVER
    # is the only witness - which is the right one anyway: what matters is the
    # number that reached the mount, not the number in the reply.
    assert tel.moves == [("ra", pytest.approx(1.44))], (
        "the shipped route clamped an AM5 to something other than its own "
        f"measured R8 rate: {tel.moves}")
    assert armed == [("ra", pytest.approx(1.44))], (
        "the deadman was armed with a rate the mount is not moving at")


# ------------------------------------------------------------------ NOT A NUMBER
# The clamp is ``max(-ceiling, min(ceiling, rate))``, and EVERY comparison with
# a NaN is False - so ``min(ceiling, nan)`` returns ``ceiling``'s partner by
# position and ``max`` does the same, and the pair comes out as the FULL driver
# ceiling. A body that named no rate at all therefore drove an AM5 at
# 1.44 deg/s with the deadman armed to match. ``json.loads`` accepts the bare
# token ``NaN``, so nothing before validation can stop it.

def test_a_literal_nan_rate_is_a_422(monkeypatch):
    """SABOTAGE (run red, restored): drop ``allow_inf_nan=False`` from
    ``MoveAxisBody.rate_deg_s``. The post then answers 200 and the mount
    receives 1.44."""
    tel = _Tel(1.44)
    armed: list[tuple[str, float]] = []
    app = app_module.create_app()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "note_move",
                        lambda axis, rate: armed.append((axis, rate)))
    with TestClient(app) as c:
        r = c.post("/api/mount/move",
                   content=b'{"axis": "ra", "rate_deg_s": NaN}',
                   headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    # THE CLAMP NEVER SAW IT. A 422 with the mount already moving would be a
    # refusal after the fact, which is the shape this whole file is about.
    assert tel.moves == [], f"a NaN reached the driver: {tel.moves}"
    assert armed == [], f"the deadman was armed off a NaN: {armed}"


@pytest.mark.parametrize("token", ["Infinity", "-Infinity"])
def test_an_infinite_rate_is_a_422_too(monkeypatch, token):
    """The same hole with the sign attached: ``min(1.44, inf)`` is 1.44 and
    ``min(1.44, -inf)`` is -inf, which the ``max`` then lifts to -1.44. Both
    are the full ceiling from a body that asked for something impossible."""
    tel = _Tel(1.44)
    app = app_module.create_app()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "note_move", lambda axis, rate: None)
    with TestClient(app) as c:
        r = c.post("/api/mount/move",
                   content=f'{{"axis": "ra", "rate_deg_s": {token}}}'.encode(),
                   headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert tel.moves == []


def test_a_nan_is_what_the_clamp_would_have_done_with_it(clamp):
    """The finding, as arithmetic, so the 422 above is visibly a fix and not a
    coincidence. This is the ONE place the old behaviour is written down."""
    ceiling = 1.44
    nan = float("nan")
    assert max(-ceiling, min(ceiling, nan)) == pytest.approx(ceiling), (
        "if this stops being true the validator is still right, but the "
        "sentence above it is not")
