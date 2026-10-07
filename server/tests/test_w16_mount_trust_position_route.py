# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-126 (#144, the route half of the UI package): ``POST
/api/mount/trust-position``.

WHY THE ROUTE EXISTS. After a power cycle the AM5 reports its home position
wherever the tube is, and WP-103 latches ``Telescope.position_known`` False on
that signature. Only two things clear the latch: a plate-solved sync, and the
operator saying the tube is really at its home or park position
(``Telescope.trust_position``). The driver half existed and nothing could reach
it. Home and park read the pole, so a mount powered up parked at home is
flagged at the start of EVERY night until its first sync; without this route
the ordinary start of a night has no way to say "I know, it is at home" short
of running a solve.

WHAT THE ROUTE MUST DO, and what each case below holds it to:

* reach the driver and report what the driver then says (``position_known``);
* be an attestation and nothing else: no wire traffic, no position read, no
  motion (a route that read the position to "confirm" would be confirming the
  very number the latch says is wrong);
* be gated by ``control.mount`` like every other verb on the mount, because it
  lifts the guard that stops a nudge computing from a believed position;
* leave a line in the audit trail that says WHO attested, since the operator's
  word is the only evidence behind the clear;
* be offered to every mount: a driver with nothing to trust answers as a no-op,
  and no mount at all is the ordinary 409.

PRIVACY. The home position is the pole, so any angle read from it is a latitude
oracle (#140). Nothing here prints or asserts a coordinate, and the audit-line
case asserts that no coordinate reaches it. Every declination below is made up.

Named mutants, each run from a byte backup inside the worktree and restored
byte-identically (sha256 compared, mutant text grepped out); every one turned at
least one case red. The first failing assertion is quoted verbatim.

* m1_route_does_not_reach_the_driver -- ``app.py`` ``mount_trust_position``:
  ``await tel.trust_position()`` made ``pass``.
  ``test_the_operators_word_clears_a_reset_mount``, ``AssertionError:
  position_known must read True after the operator's attestation, and it read
  False``.
* m2_route_not_audit_logged -- ``app.py``: ``auth_audit.record(...)`` made a
  no-op lambda call. ``test_the_attestation_is_in_the_audit_trail_with_who_said_it``,
  ``AssertionError: the operator's word cleared a safety latch and left no line
  in the audit trail; audit lines: []``.
* m3_route_gated_by_a_weaker_cap -- ``app.py``: the route's ``@declare`` and its
  ``Depends(require(...))`` both made ``CAP_VIEW_STATUS`` (two lines: the
  boot assertion grades the label against the dependency, so they move
  together). ``test_a_viewer_may_not_vouch_for_the_tube``, ``AssertionError: a
  viewer attested the tube's position (answered 200, not 403)``.
* m4_route_reads_the_position -- ``app.py``: ``await tel.get_position()`` added
  before the attestation. ``test_the_attestation_is_not_a_measurement``,
  ``AssertionError: an attestation went to the mount: ['GR', 'GD']``.
* m5_answer_assumes_known -- ``app.py``: the answer's
  ``bool(getattr(tel, "position_known", True))`` made the constant ``True``.
  ``test_the_answer_says_what_the_driver_says_not_what_was_asked``,
  ``AssertionError: the answer must carry the driver's own verdict, not an
  assumption: {'ok': True, 'position_known': True}``.
* m6_device_error_not_mapped -- ``app.py``: ``except DeviceError as e:`` made
  ``except KeyError as e:``. ``test_no_mount_connected_is_the_ordinary_409`` and
  ``test_a_driver_that_cannot_say_is_a_409_not_a_500`` error with
  ``astrodeck.devices.base.DeviceError: no telescope connected`` /
  ``...: the mount is busy`` escaping the route instead of a 409.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.devices.backends.zwo_am5 as am5
import astrodeck.events as events_mod
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import SimTelescope
from test_rbac_enforcement import FakeAuthProvider, _make_client
from test_zwo_am5 import FakeLink, _connect_script, fixed_env  # noqa: F401

#: Made-up declination: the home pole signature, and somewhere that is not.
_POLE = "+90*00:00"


async def _reset_am5() -> tuple[FakeLink, am5.ZwoAm5Telescope]:
    """A real AM5 driver whose (re)open read the home pole, so it is latched."""
    link = FakeLink(_connect_script(GD=_POLE))
    tel = am5.ZwoAm5Telescope(link, name="Mount")
    await tel.connect()
    assert tel.position_known is False, "fixture: the pole read must latch"
    return link, tel


@pytest.fixture
def rig(monkeypatch, fixed_env):
    """The shipped route with ``hub.require`` answering whatever the case sets,
    and every bus line recorded."""
    lines: list[tuple[str, str, str]] = []

    def record(level, message, source="hub", **_kw):
        lines.append((level, message, source))
    monkeypatch.setattr(events_mod.bus, "log", record)

    class Rig:
        pass
    r = Rig()
    r.lines = lines

    def use(tel):
        r.tel = tel
        monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    r.use = use
    return r


@pytest.fixture(autouse=True)
def _provider_restored():
    yield
    reset_active_provider()


def _post():
    with TestClient(app_module.create_app()) as c:
        return c.post("/api/mount/trust-position")


async def test_the_operators_word_clears_a_reset_mount(rig):
    """m1. The tube is at home by eye, the operator says so, and the latch that
    refused every nudge lifts. Answers 200 with the driver's own verdict."""
    link, tel = await _reset_am5()
    rig.use(tel)

    r = _post()

    assert r.status_code == 200, r.text
    assert tel.position_known is True, (
        "position_known must read True after the operator's attestation, "
        "and it read False")
    assert r.json() == {"ok": True, "position_known": True}


async def test_the_attestation_is_not_a_measurement(rig):
    """m4. Reading the position to confirm it would confirm the number the
    latch says is wrong. The route is a statement by the operator: it goes to
    the driver and nowhere near the wire."""
    link, tel = await _reset_am5()
    rig.use(tel)
    handshake = len(link.sent)

    r = _post()

    assert r.status_code == 200, r.text
    assert link.sent[handshake:] == [], (
        f"an attestation went to the mount: {link.sent[handshake:]}")


async def test_the_attestation_is_in_the_audit_trail_with_who_said_it(rig):
    """m2. The operator's word is the only evidence behind the clear, so who
    said it is written down, on the audit source the sign-in events use. The
    line names the actor by role or account and carries no coordinate."""
    link, tel = await _reset_am5()
    rig.use(tel)

    r = _post()

    assert r.status_code == 200, r.text
    audit = [m for (_lvl, m, src) in rig.lines if src == "audit"]
    assert any("trust_position" in m for m in audit), (
        "the operator's word cleared a safety latch and left no line in the "
        f"audit trail; audit lines: {audit}")
    line = next(m for m in audit if "trust_position" in m)
    assert " ok " in f" {line} ", line
    assert "user=" in line and "user=-" not in line, (
        f"the line must say who attested: {line}")


async def test_a_driver_with_nothing_to_trust_answers_as_a_no_op(rig):
    """The route is offered to every mount. A driver with no reason to doubt
    its frame has nothing to clear, so this is not an error: it answers true."""
    rig.use(SimTelescope("bare"))

    r = _post()

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "position_known": True}


async def test_the_answer_says_what_the_driver_says_not_what_was_asked(rig):
    """m5. A driver that declines to clear (it keeps its own evidence) must not
    be reported as cleared: the UI unlocks off this answer."""
    class Stubborn(SimTelescope):
        position_known = False

        async def trust_position(self) -> None:
            return None
    rig.use(Stubborn("stubborn"))

    r = _post()

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "position_known": False}, (
        "the answer must carry the driver's own verdict, not an assumption: "
        f"{r.json()}")


async def test_no_mount_connected_is_the_ordinary_409(monkeypatch, fixed_env):
    """No telescope: the same refusal every other mount verb gives, in the
    same shape (a plain detail string), not a 500."""
    def none(_role):
        raise DeviceError("no telescope connected")
    monkeypatch.setattr(app_module.hub, "require", none)

    r = _post()

    assert r.status_code == 409, r.text
    assert "telescope" in r.json()["detail"]


async def test_a_driver_that_cannot_say_is_a_409_not_a_500(rig):
    """A driver whose attestation raises its own error is refused the way the
    mount's other verbs refuse, with its sentence."""
    class Broken(SimTelescope):
        async def trust_position(self) -> None:
            raise DeviceError("the mount is busy")
    rig.use(Broken("broken"))

    r = _post()

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "the mount is busy"


async def test_a_viewer_may_not_vouch_for_the_tube(rig, tmp_path, monkeypatch):
    """m3. The route lifts the guard that stops a nudge computing from a
    believed position, so it takes the same capability a nudge does. A viewer is
    refused and the latch stays."""
    link, tel = await _reset_am5()
    rig.use(tel)
    _store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))

    with TestClient(app) as c:
        r = c.post("/api/mount/trust-position")

    assert r.status_code == 403, (
        f"a viewer attested the tube's position (answered {r.status_code}, "
        "not 403)")
    assert tel.position_known is False, "a refused attestation must not clear it"
