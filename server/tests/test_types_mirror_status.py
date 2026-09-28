"""``ui/src/types.ts`` mirrors a mosaic panel and the status frame's sky angle
(#174, spec 3.7's last bullet, I-25).

Two payloads reached the UI with nothing comparing them to its types:

* A panel of ``POST /api/framing/mosaic``. The route has put
  ``transit_alt_error`` on a panel it could not find an altitude for since
  the silent-panel fix, and ``MosaicPanel`` never gained it, so
  ``mosaicNightSummary.ts`` declared the field itself and said so in a
  comment. It now reads ``MosaicPanel``'s own (``PanelNight`` is a ``Pick``
  of it), so this file is what keeps that field honest.
* ``status.sky_angle``, the record ``sky_angle.note_solved_rotation`` stores
  on ``hub.last_sky_angle`` and ``poll_status`` publishes. Nothing in
  ``ui/src`` typed it, and the mosaic modal's USE MEASURED chip (spec 2.4)
  has to read it.

Both are compared with what the server really sends, both ways round, in the
style of test_types_mirror_groups.py (whose parser and JSON-type check this
file imports; the parser's own guards live there): a key the server sends
that types.ts lacks is data no screen can read, and a key types.ts declares
that the server never sends reads undefined for ever. Neither payload is a
pydantic model, so the server side is always a real answer, never a list
copied here:

* the panels of three real route answers, one for each shape a panel takes
  (no night asked for, an altitude, and a stated reason why there is none),
  so a key present in only some of them is exactly a key types.ts must mark
  optional;
* two real records off the status frame, from the centring solve
  (``Hub.solve_and_sync``) on the sim rig, one that calibrated the rotator
  and one taken with no rotator connected on a mount that cannot say its
  pier side, so every field the record can carry as null is seen null once
  and seen as a value once.

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced, observed in a private copy of the tree (a copy
of ``server/`` beside a copy of ``ui/src/types.ts``), from a byte-for-byte
backup of the file under test, with the copy's sha256 compared against the
backup afterwards.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.catalog import framing
from astrodeck.devices.base import DeviceError
from test_types_mirror_groups import (TYPES_TS, _interface, _mask_strings,
                                      _optional, _ts_admits)

pytestmark = pytest.mark.skipif(
    not TYPES_TS.exists(), reason="ui/ not present (server-only checkout)")

#: A 1x2 at the fixture's M31 centre: two panels, so each answer has more
#: than one panel to disagree about, and cheap enough to ask for tonight.
SPEC = {"ra_hours": 0.71, "dec_deg": 41.27, "rows": 1, "cols": 2,
        "overlap": 0.2, "rotation_deg": 0.0,
        "fov_x_deg": 1.2, "fov_y_deg": 0.8}


def _drift(what: str, sent: set[str], declared: set[str]) -> str:
    return (f"{what} drifted: the server sends {sorted(sent - declared)}, "
            f"which types.ts does not declare, and types.ts declares "
            f"{sorted(declared - sent)}, which the server never sends")


def _wrong(record: dict, ts: dict[str, str]) -> dict:
    """The members of ``record`` whose value its TS type does not admit."""
    return {k: (v, ts[k]) for k, v in record.items()
            if k in ts and not _ts_admits(v, ts[k])}


def _words(ts_type: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _mask_strings(ts_type)))


# ------------------------------------------------------------ MosaicPanel

@pytest.fixture
def client(monkeypatch):
    """The route alone, on a saved site overlaid on whatever the store holds
    (test_framing.py's fixture, and its reason: the visibility module refuses
    the 0,0 default since #24). 40 N 74 W, and it is not anybody's rig."""
    from astrodeck.hub import Hub
    real = Hub.site
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        **real.fget(self), "latitude": 40.0, "longitude": -74.0,
        "is_default": False}))
    app = FastAPI()
    app.include_router(framing.router)
    with TestClient(app) as c:
        yield c


def _panels(client, **extra) -> list[dict]:
    r = client.post("/api/framing/mosaic", json={**SPEC, **extra})
    assert r.status_code == 200, r.text
    return r.json()["panels"]


def test_the_mosaic_panel_type_is_what_the_route_answers(client, monkeypatch):
    """Every key a real panel carries is declared on ``MosaicPanel`` and
    every declared key is carried by some real panel; the keys only some
    panels carry are exactly the ones types.ts marks optional; and every
    value is one its TS type admits.

    RED against types.ts as it was at 812fcf9e (``MosaicPanel`` without the
    field), and under the named mutation "drop transit_alt_error from
    types.ts" (its line deleted from ``MosaicPanel`` in a scratch copy of
    ui/src/types.ts), observed:

        E   AssertionError: MosaicPanel drifted: the server sends
            ['transit_alt_error'], which types.ts does not declare, and
            types.ts declares [], which the server never sends

    RED under the named mutation "add a TS field the server never sends"
    (``transit_alt_at?: number;`` added to ``MosaicPanel``), observed:

        E   AssertionError: MosaicPanel drifted: the server sends [], which
            types.ts does not declare, and types.ts declares
            ['transit_alt_at'], which the server never sends

    RED under "transit_alt_error required in types.ts" (``transit_alt_error:
    string;``), observed:

        E   AssertionError: types.ts must mark optional exactly the keys a
            panel sends only sometimes
        E   assert {'transit_alt'} == {'transit_alt...it_alt_error'}
        E     Extra items in the right set:
        E     'transit_alt_error'

    RED under "transit_alt_error typed number in types.ts", observed:

        E   AssertionError: types.ts types these otherwise:
            {'transit_alt_error': ('OSError: ephemeris table unreadable',
            'number')}

    The premise, RED under the route mutant "no reason on a lost panel"
    (``_stamp_transit_alt`` leaving ``transit_alt_error`` off, in a scratch
    copy of server/): the three shapes must really be three, or the
    comparison grades fewer than the route sends. Observed:

        E   AssertionError: premise: a panel the route could not answer for
            carries its reason: [{'row': 0, 'col': 0, 'ra_hours':
            0.6674264689814583, 'dec_deg': 41.268235608920776,
            'rotation_deg': 0.0}, {'row': 0, 'col': 1, ...}]
    """
    plain = _panels(client)
    tonight = _panels(client, transit_alt=True)

    from astrodeck.catalog import visibility

    def _unreadable(ra_hours, dec_deg, *, date=None, site=None):
        raise OSError("ephemeris table unreadable")

    monkeypatch.setattr(visibility, "transit_alt_for", _unreadable)
    lost = _panels(client, transit_alt=True)

    assert plain and all("transit_alt" not in p and "transit_alt_error"
                         not in p for p in plain), (
        f"premise: a mosaic nobody asked a night about carries neither: "
        f"{plain}")
    assert tonight and all(isinstance(p.get("transit_alt"), float)
                           for p in tonight), (
        f"premise: tonight's panels carry an altitude: {tonight}")
    assert lost and all(isinstance(p.get("transit_alt_error"), str)
                        for p in lost), (
        f"premise: a panel the route could not answer for carries its "
        f"reason: {lost}")

    panels = plain + tonight + lost
    sent = set().union(*map(set, panels))
    always = set.intersection(*map(set, panels))
    ts = _interface("MosaicPanel")
    assert sent == set(ts), _drift("MosaicPanel", sent, set(ts))
    assert _optional("MosaicPanel") == sent - always, (
        "types.ts must mark optional exactly the keys a panel sends only "
        "sometimes")
    for p in panels:
        wrong = _wrong(p, ts)
        assert not wrong, f"types.ts types these otherwise: {wrong}"


# ------------------------------------------------------- status.sky_angle

async def _status_record(hub) -> dict | None:
    """``sky_angle`` off the status frame, as JSON carries it to the UI."""
    status = await hub.poll_status()
    assert "sky_angle" in status, "premise: the status frame names sky_angle"
    return json.loads(json.dumps(status["sky_angle"]))


async def test_the_sky_angle_type_is_the_record_the_status_frame_carries(
        sim_hub, monkeypatch):
    """Two real records off the status frame, each with exactly the keys of
    types.ts's ``SkyAngleRecord`` both ways round, none of them optional (the
    recorder writes every key every time, null where it has no value), and
    every value one its TS type admits. The first calibrated the rotator on
    a mount that reported its side; the second was taken with no rotator
    connected, on a mount that cannot say which side it is on (a fork, or a
    link that stopped answering), so every field that can be null is null in
    one record and a value in the other, and a field typed without ``| null``
    goes red on one of the two.

    RED against types.ts as it was at 812fcf9e, observed:

        E   AssertionError: types.ts has no 'export interface SkyAngleRecord'

    RED under "drop a key from the TS record" (``mechanical_deg`` deleted
    from ``SkyAngleRecord`` in a scratch copy of ui/src/types.ts), observed:

        E   AssertionError: SkyAngleRecord drifted: the server sends
            ['mechanical_deg'], which types.ts does not declare, and types.ts
            declares [], which the server never sends

    RED under "add a TS field the hub never sends" (``fresh: boolean;``
    added to ``SkyAngleRecord``), observed:

        E   AssertionError: SkyAngleRecord drifted: the server sends [], which
            types.ts does not declare, and types.ts declares ['fresh'], which
            the server never sends

    RED under "offset_deg not nullable in types.ts" (``offset_deg:
    number;``), observed:

        E   AssertionError: types.ts types these otherwise: {'offset_deg':
            (None, 'number')}

    RED under "pier_side not nullable in types.ts" (``pier_side: "east" |
    "west";``), which only the second record can catch (the sim's mount
    reports east on the first), observed:

        E   AssertionError: types.ts types these otherwise: {'pier_side':
            (None, '"east" | "west"')}

    RED under "reason optional in types.ts" (``reason?: string | null;``),
    observed:

        E   AssertionError: every key of the record is always sent
        E   assert {'reason'} == set()

    RED under the engine mutant "the record gains a key" (``"night": None``
    added to the dict ``note_solved_rotation`` builds, in a scratch copy of
    server/), observed:

        E   AssertionError: SkyAngleRecord drifted: the server sends
            ['night'], which types.ts does not declare, and types.ts declares
            [], which the server never sends
    """
    rot = sim_hub.require("rotator")
    await sim_hub.solve_and_sync(0.05)
    calibrated = await _status_record(sim_hub)

    await rot.disconnect()
    tel = sim_hub.require("telescope")

    async def no_side():
        raise DeviceError("this mount reports no pier side")

    monkeypatch.setattr(tel, "pier_side", no_side)
    # And no earlier answer to repeat: the cache `pier_side_cached` reads is
    # what a solve with no rotator falls back to (sky_angle._pier_side).
    monkeypatch.setattr(sim_hub, "_pier_side_seen", None)
    await sim_hub.solve_and_sync(0.05)
    bare = await _status_record(sim_hub)

    assert calibrated and calibrated["calibrated"] is True and isinstance(
        calibrated["pier_side"], str), (
        f"premise: the first solve calibrated the rotator on a mount that "
        f"reported its side: {calibrated}")
    assert bare and bare["calibrated"] is False and all(
        bare[k] is None for k in ("pier_side", "mechanical_deg",
                                  "rotator_before_deg", "offset_deg")), (
        f"premise: the second solve had no rotator to calibrate and no pier "
        f"side: {bare}")

    ts = _interface("SkyAngleRecord")
    for record in (calibrated, bare):
        assert set(record) == set(ts), _drift("SkyAngleRecord", set(record),
                                              set(ts))
        wrong = _wrong(record, ts)
        assert not wrong, f"types.ts types these otherwise: {wrong}"
    assert _optional("SkyAngleRecord") == set(), (
        "every key of the record is always sent")


async def test_the_status_type_carries_the_record_and_its_null(sim_hub):
    """``RigStatus.sky_angle`` is the record type, and admits the null the
    frame carries until the first solve of the night.

    RED against types.ts as it was at 812fcf9e, observed:

        E   KeyError: 'sky_angle'

    RED under "sky_angle not nullable in types.ts" (``sky_angle?:
    SkyAngleRecord;`` on ``RigStatus``), observed:

        E   AssertionError: the status frame carries sky_angle None before
            any solve, and types.ts says 'SkyAngleRecord'
    """
    before = await _status_record(sim_hub)
    assert before is None, f"premise: no solve yet, no record: {before}"
    member = _interface("RigStatus")["sky_angle"]
    assert "SkyAngleRecord" in _words(member), member
    assert _ts_admits(before, member), (
        f"the status frame carries sky_angle None before any solve, and "
        f"types.ts says {member!r}")
