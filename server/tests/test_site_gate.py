"""One question about the site, asked one way (#24).

The site defaults to 0,0 with `is_default` True, and every astronomical
computation downstream answers confidently for the Gulf of Guinea. The audit on
#24 found 36 consumers reading the coordinates without asking whether there is
a site, against 23 that ask - and, more to the point, WHY: there was no single
predicate. Five call sites had each written their own check, returning five
different shapes, and the dict-vs-pydantic accessor was copy-pasted at four of
them.

This file holds the predicate honest and holds the four call sites to it.
"""
from __future__ import annotations

import pytest

from astrodeck.config import Site
from astrodeck.site_gate import site_get, site_is_set, site_lat_lon


def _dict(**over) -> dict:
    """What `Hub.site` builds."""
    base = {"name": "", "latitude": 0.0, "longitude": 0.0, "elevation_m": 0,
            "is_default": True, "horizon_min_deg": 20}
    base.update(over)
    return base


REAL = dict(latitude=40.0, longitude=-74.0, is_default=False, name="Test")


# ------------------------------------------------------------- the predicate

def test_the_default_site_is_not_a_site():
    """MUTATION: `return True` from `site_is_set`. Observed: this fails, and so
    does every call-site case below."""
    assert site_is_set(_dict()) is False


def test_a_saved_site_is_a_site():
    """The other half, which a guard that refuses everything would break.

    MUTATION: `return False` from `site_is_set`. Observed: this fails, and the
    whole product stops computing anything about the sky.
    """
    assert site_is_set(_dict(**REAL)) is True


def test_both_shapes_answer_the_same_question():
    """`hub.site` is a dict, `cfg.site` is a pydantic Site. Four copies of this
    shim existed; a fifth is how they drift.

    MUTATION: drop the `isinstance(site, dict)` branch from `site_get`.
    Observed: the dict case raises AttributeError and this fails.
    """
    assert site_is_set(Site(name="T", latitude=40.0, longitude=-74.0,
                            is_default=False)) is True
    assert site_is_set(Site(name="", latitude=0.0, longitude=0.0)) is False
    assert site_get(_dict(**REAL))("latitude") == 40.0
    assert site_get(Site(name="T", latitude=40.0, longitude=-74.0))("latitude") == 40.0


def test_a_missing_flag_reads_as_configured():
    """Several hand-built site dicts omit `is_default` entirely. They are
    written precisely because they mean a real site, and a KeyError there would
    turn this predicate into a crash in best-effort code.

    MUTATION: `site_get(site)("is_default")` with no default, i.e. None ->
    falsy -> still configured... which passes. So the mutation that matters is
    `site_get(site)["is_default"]`: observed TypeError, and this fails.
    """
    bare = {"latitude": 40.0, "longitude": -74.0}
    assert site_is_set(bare) is True


def test_nothing_at_all_is_not_a_site():
    assert site_is_set(None) is False


# ------------------------------------------------------------ the coordinates

def test_coordinates_come_back_only_for_a_real_site():
    assert site_lat_lon(_dict()) is None
    assert site_lat_lon(_dict(**REAL)) == (40.0, -74.0)


def test_the_equator_is_a_real_place_when_somebody_saved_it():
    """Zero is a legal latitude. The refusal above is about the DEFAULT and
    about absent numbers, not about the value 0.0, and a guard that cannot tell
    those apart is the falsy-zero mistake this codebase has made before.

    MUTATION: refuse on `not lat`. Observed: this fails, and a rig genuinely on
    the equator can never compute anything.
    """
    assert site_lat_lon(_dict(latitude=0.0, longitude=6.5,
                              is_default=False)) == (0.0, 6.5)


@pytest.mark.parametrize("bad", ["north", None, object()])
def test_unreadable_coordinates_are_the_same_answer_as_no_site(bad):
    """A caller can do nothing different about "no site" and "the numbers are
    nonsense", and every one of them was already collapsing the two.

    The `None` parameter found a real one while this case was being written.
    All four call sites wrote `float(get("latitude", 0.0) or 0.0)`, and that
    idiom turns a null latitude into 0.0 - so a half-saved site with
    `is_default` already cleared and no coordinates yet read as a real location
    on the equator, which is the exact failure this predicate exists to
    prevent, arriving by another door.

    MUTATION: restore the `or 0.0` idiom. Observed: the `None` parameter comes
    back as (0.0, 0.0) and this fails.
    MUTATION: drop the `except (TypeError, ValueError)`. Observed: the string
    parameter raises out of a helper whose whole contract is to answer - and
    the `None` one too, which is why there is no separate `is None` branch:
    one was written and removing it again changed nothing.
    """
    assert site_lat_lon(_dict(latitude=bad, is_default=False)) is None


# ------------------------------------------------- the call sites, held to it

def test_observing_night_refuses_without_a_site():
    from astrodeck.sequence.schedule import observing_night
    assert observing_night(_dict()) is None
    assert observing_night(_dict(**REAL)) is not None


def test_dark_enough_is_the_one_deliberate_fail_open():
    """It returns True at a default site, on purpose: its job is to stop
    DAYLIGHT imaging, not to enforce configuration, and refusing to call it
    dark would stop an unconfigured rig taking any frame at all.

    This case exists so that the exception is a decision with a test on it
    rather than an oversight someone later "fixes". If the policy changes, this
    is the case that has to change with it.
    """
    from astrodeck.sequence.schedule import dark_enough
    assert dark_enough(_dict()) is True


def test_tonight_refuses_with_a_reason():
    from astrodeck.flows.tonight import _site_dict
    site, why = _site_dict(_dict())
    assert site is None
    assert why, "the refusal carries no reason for the user"
    site, why = _site_dict(_dict(**REAL))
    assert site is not None and site["latitude"] == 40.0


def test_every_check_asks_the_shared_predicate():
    """THE POINT OF THE EXERCISE, and the part no behavioural case covers: the
    four call sites must not grow their own copy back.

    Each of them reads its answer from `site_gate`. A fifth private
    `is_default` test in any of these files is the state #24 documented, and it
    would pass every case above.

    MUTATION: put the inline `get("is_default", ...)` back in any one of them.
    Observed: this fails naming that file.
    """
    import inspect

    from astrodeck.flows import tonight as tonight_mod
    from astrodeck.sequence import resume_arm as resume_mod
    from astrodeck.sequence import schedule as schedule_mod

    for mod in (schedule_mod, resume_mod, tonight_mod):
        src = inspect.getsource(mod)
        assert "site_gate" in src, (
            f"{mod.__name__} no longer asks the shared predicate")
        assert 'lambda k, d=None: getattr(site, k, d)' not in src, (
            f"{mod.__name__} has grown its own copy of the site accessor back; "
            f"four copies is what issue #24 is about")
