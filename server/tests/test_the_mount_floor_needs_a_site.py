"""#24: the altitude limits cannot be enforced from latitude 0, longitude 0.

`_enforce_mount_floor` is the guard in front of every slew: destination alt/az
against `max(min_alt_deg, horizon(az), nogo)` at the bottom and the zenith
keep-out at the top. It read `hub.site["latitude"]` and `["longitude"]`
directly, so at the unconfigured default it evaluated those limits for the Gulf
of Guinea.

THE FAILURE IS NOT A WRONG NUMBER, IT IS A GUARD THAT ANSWERS CONFIDENTLY IN
BOTH DIRECTIONS. A target genuinely under the floor can read as high and be
PERMITTED - the mount then drives into whatever the floor was drawn around -
and one safely high can read as low and abort the night. Both cases are below,
because a fix that only stops the false abort would leave the dangerous half.

It now fails CLOSED. That is the opposite of `schedule.dark_enough`, which
fails OPEN at a default site on purpose, and the two are not in tension:
`dark_enough` stops daylight imaging and refusing it would stop an
unconfigured rig from ever taking a frame, while this stops the mount from
hitting something. There is no reading of "we do not know where we are" that
makes a pier collision acceptable.

The refusal only reaches a rig that configured a limit. A rig with no floor, no
horizon, no no-go box and no ceiling returns earlier and never asks where it
is, which is the case below that keeps this from being a new obstacle for
someone who has not finished setting up.

MUTATIONS RUN, and what each printed:

  M1, delete the `latlon is None` refusal so the site is read directly again -
  the defect itself. 3 failed: the permitted-slew case, the ceiling-only case
  and the refusal's wording. The permitted-slew case is the one to read: with
  the site unset, a target really 44 degrees under the floor was judged at some
  other altitude entirely.

  M2, refuse whatever the site says (`if True:`). 1 failed: the configured-site
  case. A guard that refuses everything is not a guard, and nothing else
  notices - which is why that control is in the file.

  M3, move the refusal ABOVE the `has_floor or has_ceiling` early return. 2
  failed, including the no-limits case: a rig that has configured no limits at
  all would be told it cannot slew, which is a new obstacle for someone who has
  not finished setting up rather than a safety gain.

THIS FILE WAS A LEAK (#227). `_engine` handed the engine the live config and
wrote its limits onto it, and `_unset_the_site` replaced the live site, and
nothing put either back. The next engine test on the worker ran under a 30
degree floor, an 85 degree ceiling and an unset site, and two recovery tests in
test_flip_before_the_limit.py aborted 'unsafe' with 0 of 12 frames - but only
when xdist drew both files onto one worker and M42 was below the horizon.
Forced into one process with the three-test command in #227: 2 failed, 6
passed before; 17 passed (this file then had 15) with this file's fix alone,
before test_flip_before_the_limit.py got a store of its own. Now the limits go
onto a deep copy and the site goes through monkeypatch, and conftest's
`_a_test_leaves_the_config_as_it_found_it` fails, in the file that does it, any
test that leaves the config changed. That guard's own cases are at the bottom.

  M4, THE NAMED MUTANT, put one leak back: `_unset_the_site` assigns
  `cfg.site = Site(...)` instead of monkeypatching it. The guard errors at
  teardown - 22 passed, 1 error with the #227 command (observed, verbatim, up
  to the standing advice that ends every such message):

    _ ERROR at teardown of test_a_slew_under_the_floor_is_not_permitted_by_an_unsited_rig _
    E           AssertionError: tests/test_the_mount_floor_needs_a_site.py::
    test_a_slew_under_the_floor_is_not_permitted_by_an_unsited_rig left the
    process-wide config changed: site.name. Every later test on this worker
    reads that config, and fails or passes for reasons of its own (issue #227).

  Only the first test is named. The later ones write the same unset site over
  the leaked one, which changes nothing, so a leak is blamed on whichever test
  first moved the value - and the two victims passed, because their own file
  now isolates them.

  M5, the other leak put back: `_engine` hands the engine the live config
  (`.model_copy(deep=True)` dropped). 22 passed, 3 errors at teardown, naming
  `safety.min_alt_deg`, then `safety.max_alt_deg, safety.min_alt_deg`, then
  `safety.max_alt_deg` (observed).
"""
from __future__ import annotations

import math

import pytest

from astrodeck.config import Site
from astrodeck.sequence.engine import SafetyAbort
from test_the_flip_stops_paying_for_itself import (  # noqa: F401 - rootdir-relative
    sim_hub)
from conftest import (  # rootdir-relative, like the import above
    _config_a_reader_would_see, _config_left_as_found)
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target


def _target(ra_hours: float, dec_deg: float) -> Target:
    return Target(name="T", ra_hours=ra_hours, dec_deg=dec_deg, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])


def _engine(sim_hub, *, min_alt_deg: float = 30.0):
    """An engine whose run snapshot is a DEEP COPY of the live config.

    The limits the tests below set are the engine's, read off ``e._cfg``, so
    they are written onto the copy and the process-wide config never sees
    them. This used to hand the engine the live config itself, and every
    limit written here stayed on it for the rest of the worker (#227).
    """
    import astrodeck.hub as hub_mod
    cfg = hub_mod.config_store.cfg().model_copy(deep=True)
    cfg.safety.min_alt_deg = min_alt_deg
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[], meridian_flip=False, guide=False)
    e._cfg = cfg
    return e, cfg


def _unset_the_site(sim_hub, monkeypatch):
    """The shipped default: 0,0 with `is_default` True.

    Set on the live config rather than through `set_site`, which forces
    `is_default` False - "a user-saved site is, by definition, no longer the
    default" (config.py:2115). There is no supported way to SAVE an unset site,
    which is right, and it means the state this guard exists for is reached
    only by never having saved one.

    The LIVE config, because the guard reads the site off the hub, not off
    the engine's snapshot - so this is the one write that cannot go onto
    `_engine`'s copy, and it goes through monkeypatch instead (#227).
    """
    import astrodeck.hub as hub_mod
    cfg = hub_mod.config_store.cfg()
    monkeypatch.setattr(cfg, "site", Site(name="", latitude=0.0, longitude=0.0,
                                          elevation_m=0.0, is_default=True))
    assert hub_mod.Hub.site.fget(sim_hub)["is_default"] is True, (
        "the fixture did not actually reach an unset site")


def _ra_at_altitude_zero(sim_hub, dec_deg: float) -> float:
    """An RA that is near the horizon HERE, at the fixture's real site."""
    from astrodeck.sequence import schedule
    lon = sim_hub.site["longitude"]
    # six hours from transit is on the horizon for a dec near the equator
    return (schedule.lst_hours(lon) + 6.0) % 24.0


# ------------------------------------------------ the dangerous half, first

async def test_a_slew_under_the_floor_is_not_permitted_by_an_unsited_rig(
        sim_hub, monkeypatch):
    """THE ONE THAT MATTERS. A target genuinely below the configured floor must
    not be waved through because the altitude was computed for the wrong
    hemisphere. This is the guard permitting exactly what it exists to refuse.
    """
    from astrodeck.catalog import altaz
    import time as _time

    e, _cfg = _engine(sim_hub, min_alt_deg=30.0)
    lat = sim_hub.site["latitude"]
    lon = sim_hub.site["longitude"]
    t = _target(_ra_at_altitude_zero(sim_hub, 0.0), 0.0)
    here, _az = altaz(t.ra_hours, t.dec_deg, lat, lon, _time.time())
    assert here < 30.0, (
        f"premise: this target must really be under the floor here, and it is "
        f"at {here:.1f} degrees")

    _unset_the_site(sim_hub, monkeypatch)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=False, target=t)
    assert "no observing site" in str(caught.value), str(caught.value)


async def test_the_refusal_names_what_to_do_about_it(sim_hub, monkeypatch):
    """A safety abort in the middle of a night has to say which of the two
    things the operator can change - save the site, or drop the limits."""
    e, _cfg = _engine(sim_hub, min_alt_deg=30.0)
    _unset_the_site(sim_hub, monkeypatch)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=True, target=_target(12.0, 45.0))
    message = str(caught.value)
    assert "Settings" in message and "latitude 0" in message, message


# ------------------------------------------------------------- the controls

async def test_a_configured_site_is_judged_normally(sim_hub):
    """The fixture's site is real, so a target well above the floor passes and
    one below it aborts for the ordinary reason. Without this the refusal above
    could be a guard that simply never lets anything through."""
    from astrodeck.sequence import schedule
    e, _cfg = _engine(sim_hub, min_alt_deg=30.0)
    lat = sim_hub.site["latitude"]
    overhead = _target((schedule.lst_hours(sim_hub.site["longitude"])) % 24.0,
                       lat)
    await e._enforce_mount_floor(projected=False, target=overhead)  # no raise

    low = _target(_ra_at_altitude_zero(sim_hub, 0.0), 0.0)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=False, target=low)
    assert "below safety floor" in str(caught.value), str(caught.value)


async def test_a_rig_with_no_limits_at_all_is_not_asked_where_it_is(
        sim_hub, monkeypatch):
    """THE HALF THAT KEEPS THIS FROM BLOCKING A NEW RIG. No floor, no horizon,
    no no-go box, no ceiling: there is nothing to enforce, so the site is never
    needed and the slew goes ahead. Someone who has not finished setting up
    must not be stopped by a limit they never set."""
    e, cfg = _engine(sim_hub, min_alt_deg=0.0)
    cfg.safety.horizon = None
    cfg.safety.nogo_box = None
    cfg.safety.max_alt_deg = None
    _unset_the_site(sim_hub, monkeypatch)
    await e._enforce_mount_floor(projected=True, target=_target(12.0, 45.0))


async def test_a_ceiling_alone_still_needs_the_site(sim_hub, monkeypatch):
    """The zenith keep-out is a limit too, and it is checked after the floor.
    A rig with only a ceiling configured must not slip through the gap."""
    e, cfg = _engine(sim_hub, min_alt_deg=0.0)
    cfg.safety.horizon = None
    cfg.safety.nogo_box = None
    cfg.safety.max_alt_deg = 85.0
    _unset_the_site(sim_hub, monkeypatch)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=True, target=_target(12.0, 45.0))
    assert "no observing site" in str(caught.value)


def test_a_site_with_no_coordinates_cannot_EXIST_to_be_judged():
    """The door this guard would otherwise be walked through, and it turns out
    to be bricked up one layer below.

    `site_lat_lon` is careful about a half-saved site - `is_default` cleared but
    no coordinates yet - because `float(get("latitude", 0.0) or 0.0)`, which is
    what four call sites wrote, turns a null latitude into the equator. Tried to
    reach that state here and could not: `Site` types both coordinates as
    `float`, so pydantic refuses it outright and `cfg.site` can never hold one.

    Recorded rather than deleted, because it says where the protection actually
    lives. The null-coordinate case is closed by the MODEL; `site_lat_lon`'s
    care is the backstop for the other shape `hub.site` arrives in - a plain
    dict, which nothing validates.
    """
    with pytest.raises(Exception) as caught:
        Site(name="half", latitude=None, longitude=None, elevation_m=0.0,
             is_default=False)
    assert "latitude" in str(caught.value)

    # ...and the dict shape, which is the one that is NOT validated.
    from astrodeck.site_gate import site_lat_lon
    assert site_lat_lon({"latitude": None, "longitude": None,
                         "is_default": False}) is None


# ------------------------------------ #227: the guard this file's leak led to
#
# conftest's `_a_test_leaves_the_config_as_it_found_it` is graded here because
# a conftest cannot hold tests and this file is where the leak was found. The
# guard's body is `_config_left_as_found`, a plain generator, so the cases
# below drive it against a PRIVATE store and never have to leave a real leak
# on the process-wide one to watch it fire.

def _private_store(tmp_path):
    """A store in the state the session store is in after its first reader:
    materialised, and on disk."""
    from astrodeck.config import ConfigStore
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.cfg()
    return store


def _run_guard(store, between, nodeid="tests/test_x.py::test_y"):
    """Set the guard up, run ``between``, tear it down. The teardown's
    AssertionError is returned, and None when it raised nothing."""
    guard = _config_left_as_found(nodeid, store)
    next(guard)
    between()
    try:
        next(guard)
    except StopIteration:
        return None
    except AssertionError as exc:
        return exc
    raise AssertionError("the guard yielded twice")


def test_the_guard_names_the_test_and_the_field_an_in_place_write_changed(
        tmp_path):
    """#227's own shape: a write onto the cached config, which leaves the
    cached OBJECT the very same object. Only a comparison by value sees it,
    and the report names the test and the leaf that moved, not just the
    block it sits in.

    RED under mutant "the snapshot is the live object" (the dump replaced by
    ``return cfg``), and under "computes but never raises" (the ``raise``
    replaced by ``str(...)``), each (observed, verbatim):
        AssertionError: an in-place write went unreported
    RED under mutant "top level only" (no recursion into a block) (observed,
    verbatim):
        assert 'changed: safety.max_alt_deg. ' in 'tests/test_x.py::test_y
        left the process-wide config changed: safety. Every later test on
        this worker reads that conf...'
    """
    store = _private_store(tmp_path)

    def leak():
        store.cfg().safety.max_alt_deg = 85.0

    caught = _run_guard(store, leak)
    assert caught is not None, "an in-place write went unreported"
    message = str(caught)
    assert message.startswith("tests/test_x.py::test_y left"), message
    assert "changed: safety.max_alt_deg. " in message, message


def test_an_entry_added_to_or_taken_from_a_map_is_named(tmp_path):
    """A map-shaped setting (``planning.quick.exp``, keyed by wheel slot) can
    leak by GROWING, and then no leaf that was there before has moved: the
    key is simply absent from one side. That leak is named down to the key,
    and so is the one that takes an entry away.

    RED under mutant "a key on one side only is not a change" (the
    ``key not in before or key not in after`` branch appends nothing)
    (observed, verbatim):
        AssertionError: an entry added to a map went unreported
    and under "a key only BEFORE had is never looked at" (the key loop run
    over ``set(after)`` alone) (observed, verbatim):
        AssertionError: an entry taken from a map went unreported
    """
    store = _private_store(tmp_path)

    def grow():
        store.cfg().planning.quick.exp["Ha"] = 300.0

    caught = _run_guard(store, grow)
    assert caught is not None, "an entry added to a map went unreported"
    assert "changed: planning.quick.exp.Ha. " in str(caught), str(caught)

    # ...and back the other way, from the store the leak above was left on.
    def shrink():
        del store.cfg().planning.quick.exp["Ha"]

    caught = _run_guard(store, shrink)
    assert caught is not None, "an entry taken from a map went unreported"
    assert "changed: planning.quick.exp.Ha. " in str(caught), str(caught)


def test_a_write_that_is_put_back_is_not_a_leak(tmp_path):
    """The control for the case above: the same write, undone before the
    test ends, is what a monkeypatched edit looks like at teardown.

    RED under mutant "leaves compared by identity" (``before is after`` for
    ``before == after``), which reports equal lists as changed (observed):
        assert AssertionError("tests/test_x.py::test_y left the process-wide
        config changed: alerts, auth.methods, auth.revoked_jti, drivers,
        planning.pool. ...") is None
    """
    store = _private_store(tmp_path)

    def write_and_restore():
        safety = store.cfg().safety
        kept = safety.max_alt_deg
        safety.max_alt_deg = 85.0
        safety.max_alt_deg = kept

    assert _run_guard(store, write_and_restore) is None


def test_a_reset_that_reloads_the_same_config_is_not_a_leak(tmp_path):
    """``_cfg = None`` over a file that says what the cache said: the next
    reader loads the same config, so nothing leaked.

    RED under mutant "a cold cache counts as changed" (the peek replaced by
    ``return "cold"``) (observed, verbatim):
        assert AssertionError("tests/test_x.py::test_y left the process-wide
        config changed: the whole config. Every later test on th...") is None
    """
    store = _private_store(tmp_path)

    def reset():
        store._cfg = None

    assert _run_guard(store, reset) is None


def test_a_reset_over_a_file_that_says_something_else_is_a_leak(tmp_path):
    """The cache is put back by value but the FILE was changed under it and the
    cache then dropped, so the next reader loads the changed value.

    RED under mutant "a reset is never compared" (``if store._cfg is None:
    return`` after the yield) (observed, verbatim):
        AssertionError: a reset over a changed file went unreported
    """
    store = _private_store(tmp_path)

    def save_a_change_then_reset():
        safety = store.cfg().safety
        kept = safety.max_alt_deg
        safety.max_alt_deg = 85.0
        store._save()
        safety.max_alt_deg = kept
        store._cfg = None

    caught = _run_guard(store, save_a_change_then_reset)
    assert caught is not None, "a reset over a changed file went unreported"
    assert "changed: safety.max_alt_deg. " in str(caught), str(caught)


def test_looking_at_a_cold_store_does_not_load_it(tmp_path):
    """The guard must not force the load it is standing in for: that would
    fill the cache and write a file the next test was never handed, and take
    the cold path away from the tests that exist to reach it.

    RED under mutant "the peek loads the store" (``store.cfg()`` in place of
    the scratch copy) (observed, verbatim):
        AssertionError: looking at the store loaded it
    and under "a cold cache counts as changed":
        AssertionError: a store with no file must read as the defaults its
        first load makes
    """
    from astrodeck.config import AppConfig, ConfigStore
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    seen = _config_a_reader_would_see(store)
    assert store._cfg is None, "looking at the store loaded it"
    assert not store._path.exists(), "looking at the store wrote its file"
    assert seen == AppConfig().model_dump(mode="json"), (
        "a store with no file must read as the defaults its first load makes")


def test_a_file_left_unloadable_is_blamed_on_the_test_that_broke_it(tmp_path):
    """A config file that no longer loads, under an empty cache, is a change:
    the next reader gets a RuntimeError instead of a config. It is reported on
    the test that broke it - and NOT again on every later test, which found
    the file already broken and left it as it was.

    RED under mutant "unloadable raises out" (no try around the scratch
    ``_load``): the next test's guard cannot even set up (observed,
    verbatim):
        RuntimeError: configuration is corrupt and no valid backup is available
    and under "a reset is never compared":
        AssertionError: a config left unloadable went unreported
    """
    store = _private_store(tmp_path)

    def break_it():
        store._path.write_text("{ not json", encoding="utf-8")
        store._bak_path().unlink(missing_ok=True)
        store._cfg = None

    caught = _run_guard(store, break_it)
    assert caught is not None, "a config left unloadable went unreported"
    assert "changed: the whole config. " in str(caught), str(caught)

    assert _run_guard(store, lambda: None) is None, (
        "a test that found the file already broken was blamed for it")


def test_a_reset_over_a_corrupt_file_with_a_good_backup_is_not_a_leak(
        tmp_path):
    """The next reader of a corrupt file with a good ``.bak`` gets the
    backup - ``_load`` restores from it - so that is what a reset over such a
    file compares as, and a backup that says what the cache said is not a
    leak. This is the backup recovery the peek claims to run for real, on a
    scratch copy of both files.

    RED under mutant "the peek copies the file but not its backup" (the
    ``.bak`` pair dropped from the scratch copy) (observed, verbatim):
        assert AssertionError("tests/test_x.py::test_y left the process-wide
        config changed: the whole config. Every later test on th...") is None
    """
    store = _private_store(tmp_path)
    store._save()                  # the .bak now holds what the cache holds
    assert store._bak_path().is_file(), "premise: there is a backup"

    def corrupt_then_reset():
        store._path.write_text("{ not json", encoding="utf-8")
        store._cfg = None

    assert _run_guard(store, corrupt_then_reset) is None


def test_a_save_that_put_every_value_back_is_not_a_leak(tmp_path):
    """``version`` is exempt (conftest's ``_NOT_A_LEAK`` says why): a value
    restored through the product's own setter moves the save counter and
    nothing else.

    RED under mutant "version compared" (dropped from ``_NOT_A_LEAK``)
    (observed, verbatim):
        assert AssertionError("tests/test_x.py::test_y left the process-wide
        config changed: version. Every later test on this worker...") is None
    """
    store = _private_store(tmp_path)
    start = store.cfg().version

    def set_and_put_back():
        kept = store.cfg().cooling.setpoint_c
        store.set_cooling_setpoint(-10.0)
        store.set_cooling_setpoint(kept)

    assert _run_guard(store, set_and_put_back) is None
    assert store.cfg().version == start + 2, (
        "premise: both saves moved the counter")


def test_a_change_saved_beside_the_counter_is_still_reported(tmp_path):
    """The exemption covers the counter, not the save: a setting changed in
    the same save is reported, and the counter is not named beside it.

    RED under mutant "an exempt field excuses the report" (any exempt field
    among the changes empties the list) (observed, verbatim):
        AssertionError: a saved change went unreported
    """
    store = _private_store(tmp_path)

    def set_and_keep():
        store.set_cooling_setpoint(-10.0)

    caught = _run_guard(store, set_and_keep)
    assert caught is not None, "a saved change went unreported"
    assert "changed: cooling.setpoint_c. " in str(caught), str(caught)


def test_the_hub_learning_a_camera_can_cool_is_not_blamed_on_the_test(
        tmp_path):
    """``camera_can_cool_seen`` is exempt (conftest says why): the hub writes
    it on the first camera connect on a worker, whichever test that is.

    RED under mutant "the latch compared" (dropped from ``_NOT_A_LEAK``)
    (observed, verbatim):
        assert AssertionError("tests/test_x.py::test_y left the process-wide
        config changed: camera_can_cool_seen. Every later test o...") is None
    """
    store = _private_store(tmp_path)
    assert store.cfg().camera_can_cool_seen is False, (
        "premise: no camera has connected to this store")
    assert _run_guard(store, lambda: store.remember_camera_can_cool(True)) is None
    assert store.cfg().camera_can_cool_seen is True, (
        "premise: the hub's write took")


def test_the_guard_grades_the_store_it_started_with(monkeypatch, tmp_path):
    """The singleton captured at setup is the one compared at teardown, not
    whatever ``astrodeck.config.config_store`` names by then. A test that
    rebinds the name and writes the real store would otherwise be graded on
    the stand-in it left behind.

    Everything here goes through monkeypatch, so the real guard around this
    test finds the store as it was.

    RED under mutant "the store re-read at teardown" (``store =
    config_mod.config_store`` after the yield): the stand-in is graded
    instead (observed, verbatim):
        assert 'changed: safety.max_alt_deg. ' in 'tests/test_x.py::test_y
        left the process-wide config changed: site.name. Every later test on
        this worker reads that c...'
    """
    import astrodeck.config as config_mod
    real = config_mod.config_store
    stand_in = _private_store(tmp_path)
    stand_in.cfg().site.name = "the stand-in"
    guard = _config_left_as_found("tests/test_x.py::test_y")
    next(guard)
    monkeypatch.setattr(config_mod, "config_store", stand_in)
    # A value that differs from whatever is there: a fixed 85 went unseen
    # whenever an earlier leak had already left 85 behind.
    moved = (real.cfg().safety.max_alt_deg or 90.0) - 1.0
    monkeypatch.setattr(real.cfg().safety, "max_alt_deg", moved)
    with pytest.raises(AssertionError) as caught:
        next(guard)
    assert "changed: safety.max_alt_deg. " in str(caught.value), (
        str(caught.value))


def test_a_config_edit_through_monkeypatch_is_not_a_leak(monkeypatch):
    """THE CONTROL THAT PINS THE TEARDOWN ORDER. The same writes the floor
    tests above used to make, onto the live config, through monkeypatch. The
    real guard around this test must compare only after monkeypatch has put
    them back.

    RED under mutant "set up after monkeypatch" (the guard renamed
    ``_z_test_leaves_the_config_as_it_found_it``, which sorts after
    ``_fast_sim_delays``) (observed, verbatim):
        __ ERROR at teardown of test_a_config_edit_through_monkeypatch_is_not_a_leak __
        E           AssertionError: tests/test_the_mount_floor_needs_a_site.py::
        test_a_config_edit_through_monkeypatch_is_not_a_leak left the
        process-wide config changed: safety.max_alt_deg, safety.min_alt_deg,
        site.name. Every later test on this worker read...
    and so did every other test here that patches the config through
    monkeypatch: seven errors at teardown, this one included.
    """
    from astrodeck.config import config_store
    cfg = config_store.cfg()
    monkeypatch.setattr(cfg.safety, "min_alt_deg", 30.0)
    monkeypatch.setattr(cfg.safety, "max_alt_deg", 85.0)
    monkeypatch.setattr(cfg, "site", Site(name="", latitude=0.0, longitude=0.0,
                                          elevation_m=0.0, is_default=True))
    assert config_store.cfg().safety.max_alt_deg == 85.0, "the edit did not take"


@pytest.fixture(scope="class")
def _a_cold_cache():
    """Empty the process-wide cache BEFORE the guard sets up, which only a
    fixture of wider scope than the guard can do. The file is written from the
    cache first, so the reload the test below forces equals what it held, and
    the object is put back after."""
    import astrodeck.config as config_mod
    store = config_mod.config_store
    kept = store._cfg
    if kept is not None:
        store._save()
    store._cfg = None
    yield store
    store._cfg = kept


class TestAColdCache:
    def test_a_test_that_only_reads_the_config_is_not_a_leak(
            self, _a_cold_cache):
        """The other control: a read, from an empty cache, fills it. The
        guard must treat that as the same config, because it is.

        RED under mutant "a cold cache counts as changed" (observed,
        verbatim):
            _ ERROR at teardown of TestAColdCache.test_a_test_that_only_reads_the_config_is_not_a_leak _
            E           AssertionError: tests/test_the_mount_floor_needs_a_site.py::
            TestAColdCache::test_a_test_that_only_reads_the_config_is_not_a_leak
            left the process-wide config changed: the whole config. ...
        and under "the peek loads the store", whose guard filled the cache
        before the test began:
            AssertionError: premise: the cache starts empty
        """
        assert _a_cold_cache._cfg is None, "premise: the cache starts empty"
        _a_cold_cache.cfg().safety
        assert _a_cold_cache._cfg is not None, "premise: the read filled it"
