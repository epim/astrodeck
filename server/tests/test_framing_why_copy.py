# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A panel's missing peak altitude is explained in the operator's words (#405
item 1).

``framing._why`` wrote every panel's ``transit_alt_error`` as
``"<ExceptionType>: <message>"``, because some exceptions stringify to
nothing (the ``FileNotFoundError`` of the config-store cold-load race) and
an empty reason is the silence the field exists to end. ``visibility.NoSite``
carries a sentence written for the operator, so on every rig with no saved
site, which is every fresh install, the PANELS night card read "No panel of
this mosaic has a peak altitude - NoSite: no observing site is saved, ...".
The Sky hub's framing card and the classic Atlas's mosaic summary showed the
same text. Now an operator-worded exception is its own message, and the type
is still the reason when the message is empty.

Every mutant below was run in a private copy of ``server/`` under the
session scratchpad (``s5-srvsmall-mut``), from a byte backup, never in the
shared tree.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrodeck.catalog import framing
from astrodeck.catalog.visibility import _NO_SITE_MSG, NoSite


def test_an_operator_worded_exception_is_its_own_message():
    """RED under mutant "type prefix always" (``_why``'s operator-worded
    return taken out), observed:

        E       AssertionError: assert 'NoSite: no o...e in Settings' ==
        'no observing...e in Settings'
        E         - no observing site is saved, so tonight's windows cannot
        be computed - save the site in Settings
        E         + NoSite: no observing site is saved, so tonight's windows
        cannot be computed - save the site in Settings
    """
    assert _NO_SITE_MSG.startswith("no observing site")
    assert framing._why(NoSite(_NO_SITE_MSG)) == _NO_SITE_MSG


def test_the_type_is_the_reason_when_the_message_is_empty():
    """The reason the prefix exists, for NoSite as for anything else: an
    empty message leaves the type as the one thing to say.

    RED under mutant "empty message unprefixed" (the operator-worded return
    moved above the empty-message test), observed:

        E       AssertionError: assert '' == 'NoSite'
    """
    assert framing._why(NoSite()) == "NoSite"
    assert framing._why(NoSite("   ")) == "NoSite"
    assert framing._why(FileNotFoundError()) == "FileNotFoundError"


def test_any_other_exception_keeps_its_type():
    """The control: an exception nobody worded for the operator is still
    named, so an astropy fault or an OSError says what it was.

    RED under mutant "every message unprefixed" (the last return made
    ``return detail``), observed:

        E       AssertionError: assert 'ephemeris table unreadable' ==
        'OSError: eph...le unreadable'
    """
    assert (framing._why(OSError("ephemeris table unreadable"))
            == "OSError: ephemeris table unreadable")
    assert framing._why(ValueError("x")) == "ValueError: x"


def test_the_mosaic_route_answers_the_missing_site_in_words(isolated_config):
    """#405's own case: ``POST /api/framing/mosaic`` with ``transit_alt``
    (tonight, the modal's night card) on a config of the test's own, which
    has no site saved. Every panel says why in the operator's words, and no
    panel names the class.

    RED under mutant "type prefix always", observed:

        E           AssertionError: NoSite: no observing site is saved, so
        tonight's windows cannot be computed - save the site in Settings
        E           assert False
    """
    assert isolated_config.store.cfg().site.is_default, "the fixture has a site"
    app = FastAPI()
    app.include_router(framing.router)
    with TestClient(app) as c:
        r = c.post("/api/framing/mosaic", json={
            "ra_hours": 0.7122, "dec_deg": 41.269, "rows": 2, "cols": 3,
            "overlap": 0.25, "rotation_deg": 30.0,
            "fov_x_deg": 2.0, "fov_y_deg": 1.33, "transit_alt": True})
    assert r.status_code == 200, r.text[:300]
    panels = r.json()["panels"]
    assert len(panels) == 6
    for p in panels:
        assert "transit_alt" not in p, p
        why = p["transit_alt_error"]
        assert why.startswith("no observing site"), why
        assert "NoSite" not in why, why
