# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#881: the pre-slew pier guard asks a JNOW Alpaca mount about the point the
slew goes to, not about the J2000 pair.

Everything above the device layer is J2000; a JNOW Alpaca mount (ASCOM
``EquatorialSystem`` 1, the usual case) reads whatever it is sent as JNOW, and
``Hub.to_mount_frame`` converts at the call site (#861). The slew gate's pier
guard (`SequenceEngine._mount_floor_verdict`) asked ``DestinationSideOfPier``
with the catalogue pair unconverted, so the guard and the slew that follows
it asked about two points up to 0.38 deg (about 92 s of RA) apart. A target
within that margin of the mount's flip boundary was guarded on the wrong
side: a safe slew refused, or a pier flip with flips off passed.

The mount here is a REAL ``AlpacaTelescope`` over the real ``AlpacaConnection``
on an in-memory transport (``test_862_sync_read_back.FakeAlpacaMount``), whose
``DestinationSideOfPier`` answers from the RA it is asked about: EAST at or
past ``BOUNDARY_RA``, WEST before it. Precession is stubbed with an
unmistakable shift (RA +0.25 h, Dec +1 deg): the tests are about WHICH
coordinates reach the mount, not about astropy. The target sits on one side of
the boundary in J2000 and on the other in JNOW. Every coordinate here is
fictional.

Each case names the mutant it was shown RED under; mutants were applied to a
byte copy of the file and the file was restored from that copy (md5 checked).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import SafetyAbort, SlewRefused

from test_862_sync_read_back import FakeAlpacaMount, _alpaca_body, alpaca_tel
from test_centring_settings_reach_goto import _Hub, _target

#: The fictional flip boundary of the fake mount, in the frame it is asked in.
BOUNDARY_RA = 10.1
#: The target's catalogue RA: WEST of the boundary as J2000, and 10.20, EAST
#: of it, once the stubbed precession has added 0.25 h.
TARGET_RA = 9.95
TARGET_DEC = 40.0

#: ASCOM SideOfPier: 0 = pierEast, 1 = pierWest (the driver's own mapping).
EAST, WEST = 0, 1


class PierMount(FakeAlpacaMount):
    """A fake Alpaca mount that answers the two pier reads. ``pier`` is the
    side it is on now; ``DestinationSideOfPier`` is EAST at or past
    ``BOUNDARY_RA`` and WEST before it, whatever frame the RA is in."""

    def __init__(self, *, pier: int, **kw):
        super().__init__(**kw)
        self.pier = pier

    def _get(self, method: str) -> httpx.Response:
        if method == "sideofpier":
            return httpx.Response(200, json=_alpaca_body(self.pier))
        if method == "destinationsideofpier":
            ra = float(self.requests[-1][2]["RightAscension"])
            return httpx.Response(200, json=_alpaca_body(
                EAST if ra >= BOUNDARY_RA else WEST))
        return super()._get(method)

    def destination_asks(self) -> list[tuple[float, float]]:
        return [(float(p["RightAscension"]), float(p["Declination"]))
                for v, m, p in self.requests
                if v == "GET" and m == "destinationsideofpier"]

    def slew_puts(self) -> list[tuple[float, float]]:
        return [(float(p["RightAscension"]), float(p["Declination"]))
                for v, m, p in self.requests
                if v == "PUT" and m == "slewtocoordinatesasync"]


def _stub_precession(monkeypatch) -> None:
    monkeypatch.setattr(hub_module, "precess_j2000_to_jnow",
                        lambda ra, dec, when=None: ((ra + 0.25) % 24.0,
                                                    dec + 1.0))
    monkeypatch.setattr(hub_module, "precess_jnow_to_j2000",
                        lambda ra, dec, when=None: ((ra - 0.25) % 24.0,
                                                    dec - 1.0))


def _guarded_engine(fake: PierMount, *, flips: bool = False):
    """The real engine, the pier guard armed, over a hub double that carries
    the REAL frame conversion and a real Alpaca telescope on ``fake``."""
    hub = _Hub()
    tel = alpaca_tel(fake)
    # What `AlpacaTelescope.connect` leaves after its DestinationSideOfPier
    # probe answers; the guard is armed by this flag.
    tel.reports_destination_pier_side = True
    hub.tel = tel
    hub.devices = {"telescope": tel}
    hub._mount_wants_jnow = None
    hub._mount_expects_jnow = Hub._mount_expects_jnow.__get__(hub)
    hub.to_mount_frame = Hub.to_mount_frame.__get__(hub)
    e = SequenceEngine(hub)
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False,
                                           enforce_pier_limits=True))
    e.plan = SequencePlan(name="pier", guide=False, meridian_flip=flips,
                          safety_check=False, targets=[])
    return e, hub


async def test_a_flip_the_jnow_point_needs_is_refused_though_the_j2000_point_needs_none(
        monkeypatch):
    """The dangerous direction. The mount is on the WEST side with meridian
    flips off. The J2000 pair is west of the boundary (WEST, the side it is
    on: no flip), but the point the slew goes to, in JNOW, is east of it
    (EAST: a flip). The guard must refuse, and must have asked the mount
    about the JNOW pair.

    MUTANT M881-1 "the guard asks the J2000 pair" (the guard's read in
    `_mount_floor_verdict` changed back to ``tel.destination_pier_side(
    target.ra_hours, target.dec_deg)``): RED (observed), ``assert (None is
    not None)``: the flip passed unrefused.
    """
    _stub_precession(monkeypatch)
    fake = PierMount(pier=WEST, equatorial_system=1)
    e, _hub = _guarded_engine(fake)
    t = _target(name="Fictional A", ra_hours=TARGET_RA, dec_deg=TARGET_DEC)

    verdict = await e._mount_floor_verdict(t)

    assert verdict is not None and verdict.kind == "pier", verdict
    assert fake.destination_asks() == [(pytest.approx(TARGET_RA + 0.25),
                                        pytest.approx(TARGET_DEC + 1.0))]


async def test_a_slew_the_jnow_point_does_not_need_flipping_is_not_refused(
        monkeypatch):
    """The needless refusal. The mount is on the EAST side with meridian flips
    off. The J2000 pair is west of the boundary (WEST: a flip from EAST), but
    the point the slew goes to is east of it (EAST: the side the mount is
    on). Nothing flips, so the guard must let the slew through.

    MUTANT M881-1 "the guard asks the J2000 pair": RED (observed),
    ``AssertionError: ReachVerdict(tag='refuse', kind='pier', ...)``, a
    refusal of a slew that needs no flip."""
    _stub_precession(monkeypatch)
    fake = PierMount(pier=EAST, equatorial_system=1)
    e, _hub = _guarded_engine(fake)
    t = _target(name="Fictional B", ra_hours=TARGET_RA, dec_deg=TARGET_DEC)

    verdict = await e._mount_floor_verdict(t)

    assert verdict is None, verdict
    assert fake.destination_asks() == [(pytest.approx(TARGET_RA + 0.25),
                                        pytest.approx(TARGET_DEC + 1.0))]


async def test_a_j2000_alpaca_mount_is_asked_the_catalogue_pair(monkeypatch):
    """CONTROL. A mount that reports J2000 (``EquatorialSystem`` 2) is never
    sent a precessed pair, by the guard or by anything else: the mount is on
    the EAST side, the J2000 pair is west of the boundary (WEST: a flip), and
    the guard refuses on the pair it was given.

    MUTANT M881-2 "the guard precesses unconditionally" (the conversion in
    `destination_pier_side_in_mount_frame` replaced by ``await
    asyncio.to_thread(precess_j2000_to_jnow, ra_hours, dec_deg)``): RED
    (observed), ``assert (None is not None)``: the precessed point is on the
    side the mount is on, and the refusal the J2000 pair earns is gone."""
    _stub_precession(monkeypatch)
    fake = PierMount(pier=EAST, equatorial_system=2)
    e, _hub = _guarded_engine(fake)
    t = _target(name="Fictional C", ra_hours=TARGET_RA, dec_deg=TARGET_DEC)

    verdict = await e._mount_floor_verdict(t)

    assert verdict is not None and verdict.kind == "pier", verdict
    assert fake.destination_asks() == [(pytest.approx(TARGET_RA),
                                        pytest.approx(TARGET_DEC))]


async def test_the_guard_and_the_setup_slew_ask_about_the_same_point(
        monkeypatch):
    """The real `_setup_target`, uncentred, on a JNOW mount already on the
    side the slew lands on. The pier guard's question and the slew carry the
    SAME pair, and it is the converted one: the two cannot disagree about
    where the mount is going.

    MUTANT M881-1 "the guard asks the J2000 pair": RED (observed),
    ``SlewRefused: slew to Fictional D would require a pier flip but meridian
    flip is disabled``: the guard read the J2000 side and refused a slew the
    JNOW point does not need."""
    _stub_precession(monkeypatch)
    fake = PierMount(pier=EAST, equatorial_system=1)
    e, hub = _guarded_engine(fake)
    t = _target(name="Fictional D", ra_hours=TARGET_RA, dec_deg=TARGET_DEC,
                center=False)
    e.plan.targets = [t]

    await e._setup_target(0, t)

    asked = fake.destination_asks()
    slewed = fake.slew_puts()
    assert slewed == [(pytest.approx(TARGET_RA + 0.25),
                       pytest.approx(TARGET_DEC + 1.0))], slewed
    assert asked == slewed, (asked, slewed)


async def test_a_refused_setup_slew_never_reaches_the_mount(monkeypatch):
    """The refusal ends the setup before the mount moves: the real
    `_setup_target` on the mount that is WEST now, the JNOW point EAST of the
    boundary, flips off. A `SlewRefused` (a limit, not a dead link), and no
    slew PUT.

    MUTANT M881-1 "the guard asks the J2000 pair": RED (observed),
    ``Failed: DID NOT RAISE SlewRefused``, and the slew goes to the mount."""
    _stub_precession(monkeypatch)
    fake = PierMount(pier=WEST, equatorial_system=1)
    e, hub = _guarded_engine(fake)
    t = _target(name="Fictional E", ra_hours=TARGET_RA, dec_deg=TARGET_DEC,
                center=False)
    e.plan.targets = [t]

    with pytest.raises(SlewRefused):
        await e._setup_target(0, t)

    assert fake.slew_puts() == []


async def test_the_conversion_runs_inside_the_guards_bound(monkeypatch):
    """A frame conversion that never answers is bounded by the pier read's
    own bound and ends as the dead-link SafetyAbort every bounded mount read
    ends as, naming the read. Never a hang, and never a guard that reads the
    side as merely unknown and lets the slew through.

    MUTANT M881-3 "conversion outside the bound" (the guard's lambda replaced
    by ``conv_ra, conv_dec = await self.hub.to_mount_frame(...)`` before the
    ``_pier_guard_read`` call): RED (observed), the test's own ``wait_for``
    expires (``TimeoutError``), not a SafetyAbort."""
    monkeypatch.setattr(engine_mod, "MOUNT_QUERY_TIMEOUT_S", 0.05)
    fake = PierMount(pier=WEST, equatorial_system=1)
    e, hub = _guarded_engine(fake)
    never = asyncio.Event()

    async def to_mount_frame(tel, ra_hours, dec_deg):
        await never.wait()
        return ra_hours, dec_deg

    hub.to_mount_frame = to_mount_frame
    t = _target(name="Fictional F", ra_hours=TARGET_RA, dec_deg=TARGET_DEC)

    with pytest.raises(SafetyAbort) as err:
        await asyncio.wait_for(e._mount_floor_verdict(t), 5.0)

    assert not isinstance(err.value, SlewRefused)
    assert "destination pier side" in str(err.value), str(err.value)
    assert fake.destination_asks() == []


async def test_a_hub_with_no_frame_conversion_asks_the_pair_as_given():
    """A bare hub double has no ``to_mount_frame`` (the engine's own tests
    drive the guard with them), and the guard asks the pair unchanged, as
    every non-Alpaca mount is asked.

    MUTANT M881-4 "the helper requires the conversion" (``getattr(hub,
    "to_mount_frame", None)`` in `_target_in_mount_frame` replaced by
    ``hub.to_mount_frame``): RED (observed), ``assert (None is not None)``:
    the guard's read raises AttributeError, reads as an unknown side, and the
    refusal is gone."""
    fake = PierMount(pier=EAST, equatorial_system=1)
    e, hub = _guarded_engine(fake)
    del hub.to_mount_frame
    t = _target(name="Fictional G", ra_hours=TARGET_RA, dec_deg=TARGET_DEC)

    verdict = await e._mount_floor_verdict(t)

    assert verdict is not None and verdict.kind == "pier", verdict
    assert fake.destination_asks() == [(pytest.approx(TARGET_RA),
                                        pytest.approx(TARGET_DEC))]
