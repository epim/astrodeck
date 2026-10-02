# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""No test reads the developer's real config (#341).

test_flows_wizard_route.py's fixture isolated only the flow library, and
the wizard route injects the rig's optics, so its answer to a mosaic turned
on a config it never set up. The fix gave the file a store of its own; this
is the rest of the issue's shape of a fix:

* ONE SHARED ISOLATION, conftest's ``isolated_config``: a store swept into
  every ``astrodeck`` module that holds one, ``CONFIG_DIR``, the profile
  library, the hub's cached profile, the capture root, ``hub.last_sky_angle``
  and the hub's camera and rotator. The wizard, rig-facts and factory-reset
  files use it.
* A GUARD, conftest's ``_no_test_reads_the_real_config``, which fails at
  teardown any test that read or wrote the developer's real config file or
  profiles directory (``_RealConfig``: the LOCATIONS ``astrodeck.config``
  resolved at import). It is #309's guard on ``captures/``, applied to reads.

What "real" is, measured before it was chosen. A probe of the whole suite
(scratchpad s4-testhyg-mut, 10776 tests passing) wrapped every optics and
profile read: 854 tests in 129 files read optics or a profile through the
process store, which the session fixture has pointed at a throwaway file
since 2026-07-29, so every one of them read the same defaults on every
machine; none read the real config file; and 8 tests in 3 files
(test_connect_rig_guard.py, test_hub_solve.py,
test_no_route_leaks_the_site_coordinates.py) read the developer's real
``profiles/``, which nothing moved. The session fixture now moves the
profile library too, and the guard refuses the real locations for good.

THE KNOWN POSITIVE is a deliberately leaky fixture, #341's fixture given a
store the careless way (``ConfigStore()``, no path, which is the
developer's own file) and a ``ProfileLibrary()`` with no directory. It runs
in a throwaway pytest session over a copy of this conftest, as
test_capture_root_isolated.py's probes do, with ``ASTRODECK_CONFIG_DIR``
pointing at a stand-in "real" config that has optics, an active profile
and a rotator: the leaky tests pass, answering from the stand-in, and the
guard names each. Its control is the same mosaic through
``isolated_config``, clean. No case here reads this machine's config.

Every mutant was run in a private copy of ``server/`` under the session
scratchpad (s4-testhyg-mut2), from a byte backup, restored and compared by
SHA-256 after each run, never in the shared tree.

VERIFIED, AND WHAT THE VERIFICATION FOUND (2026-09-28, S5, #361; filed
as #436). The
mutant "the library left on the real profiles/" was run again in a private
copy (scratchpad s5-srvsmall-mut) and gave what #361 records, ``1 failed,
20 passed, 8 errors`` over this file and the three it names. #361 also
said a new singleton that the session fixture does not move "will fail
loudly instead of reading quietly". That held only for the two classes
the guard watched. A scan of every ``astrodeck`` module
(``conftest._built_on_the_real_config``, all of them imported) found,
beside the store and the library, three more singletons built on
``CONFIG_DIR`` at import: ``plans.plan_library`` (``plans/``),
``locations.location_store`` (``locations.json``, the saved sites) and
``auth.users.user_store`` (``users.json``). It also found five module
globals read at call time: ``flows.store.CONFIG_DIR`` (``flow_store.dir``'s
comment says it is resolved live, but the name was bound at import),
``config``'s ``FILTER_CONFIG_FILE``, ``EGAIN_CONFIG_FILE`` and
``FOCUSER_STATE_FILE``, and ``licensing.CONSENT_FILE``; and eleven more
bindings of those paths used only at import, as a default argument or not
at all (``config.PLANS_DIR``, ``locations.LOCATIONS_FILE``, the ephemeris
package's re-exports among them). Nothing moved or watched any of them. A probe of the suite against a
stand-in real config (scratchpad s5-srvsmall-probe, an audit hook recording
every file touched in it by test id; two runs that ended at 6,200 and
7,190 tests when the machine ran out of memory, and one run of the three
files that read the rarest entries) counted at least: ``egain.json`` 856
tests in 115 files and ``filter_names.json`` 851 in 113, both through the
sim rig's connect; ``plans/`` 725 tests in 76 files, through the app's
startup; ``flows/`` 8 in 3; ``locations.json`` 2 and ``users.json`` 2,
each once through the site leak scanner's own known positive. None of them
wrote there. The session fixture now sweeps them all off
(``_sweep_off_the_real_config``), and the guard watches the directory
itself, so what #361 promised is now true for anything that touches a file
in it.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import astrodeck
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
import astrodeck.profiles as profiles_mod
from astrodeck.auth.users import UserStore
from astrodeck.config import ConfigStore, Optics
from astrodeck.locations import LocationStore
from astrodeck.persist import read_json_or
from astrodeck.plans import PlanLibrary
from astrodeck.profiles import Profile, ProfileDevice, ProfileLibrary
from astrodeck.sequence.models import SequencePlan
from conftest import (  # rootdir-relative, as test_capture_root_isolated does
    IsolatedConfig, _built_on_the_real_config, _RealConfig)

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"

#: The stand-in "real" config's optics: an IMX571 at 1000 mm, 1.346 x 0.900
#: deg. The telescope name is a marker the guard's output must never carry:
#: it reports seams, never a value it saw.
STANDIN_OPTICS = Optics(focal_length_mm=1000.0, pixel_size_um=3.76,
                        sensor_width_px=6248, sensor_height_px=4176,
                        telescope_name="STANDIN-SCOPE-4b1e")


# --------------------------------------------------------------- in process

def test_the_guard_watches_the_real_locations_and_the_net_is_off_them():
    """The guard is armed in THIS run: its real locations are the config
    file and profiles directory ``astrodeck.config`` resolved at import
    (``CONFIG_FILE``, ``PROFILES_DIR``); the disk methods of both classes
    are watched, ``cfg`` included; and no shared seam sits on either (the
    process store's file, and the profile library, which the session
    fixture moves since S4).

    RED under mutant "cached reads unseen" (``cfg`` not wrapped), observed:

        E           AssertionError: ConfigStore.cfg is not watched
        E           assert False
        E            +  where False = hasattr(<function ConfigStore.cfg at
        0x...>, '__wrapped__')

    RED under mutant "the library left on the real profiles/" (the session
    fixture's ``profiles_mod.profiles._dir = ...`` line removed), observed
    (the copy's path elided):

        E       AssertionError: the process profile library reads the
        developer's real profiles/
        E       assert '...\\\\server\\\\config\\\\profiles' not in
        frozenset({'...\\\\server\\\\config\\\\profiles'})

    and, in the same run, the guard errored exactly the 8 tests the probe
    had found, and nothing else (``1 failed, 20 passed, 8 errors`` over
    this file and those three), each at its own teardown, as:

        E           AssertionError: tests/test_hub_solve.py::
        test_effective_optics_active_profile_is_cached reached the
        developer's real config: ProfileLibrary.get (the real profiles
        directory). ...

    (``ProfileLibrary.get`` for test_connect_rig_guard.py's one test,
    ``ProfileLibrary._all`` for test_no_route_leaks_the_site_coordinates'
    six). With the net in place those three files pass, 17 of 17.

    SINCE S5 the session fixture's sweep moves the library as well (it
    finds ``profiles.profiles._dir``), so that mutant alone is equivalent:
    run again in scratchpad s5-srvsmall-mut it left the four files green,
    ``23 passed``. With the sweep skipped too (mutant "library line and
    sweep both gone") this case fails as quoted above, and the four files
    give ``3 failed, 20 passed, 1 warning, 11 errors``: the 8, and three
    connects that read ``filter_names.json`` and ``egain.json``
    (test_connect_rig_guard.py's force and idle cases,
    test_hub_solve.py's vertical hint)."""
    key = _RealConfig._key
    assert key(config_mod.CONFIG_FILE) in _RealConfig.files
    assert key(config_mod.PROFILES_DIR) in _RealConfig.profile_dirs
    assert key(config_mod.CONFIG_FILE.parent) in _RealConfig.dirs
    for cls, name in ((ConfigStore, "_load"), (ConfigStore, "_save"),
                      (ConfigStore, "cfg"),
                      (ProfileLibrary, "_all"), (ProfileLibrary, "get"),
                      (ProfileLibrary, "save"), (ProfileLibrary, "delete")):
        assert hasattr(cls.__dict__[name], "__wrapped__"), (
            f"{cls.__name__}.{name} is not watched")
    assert key(config_mod.config_store._path) not in _RealConfig.files, (
        "the process store reads the developer's real config file")
    assert key(profiles_mod.profiles._dir) not in _RealConfig.profile_dirs, (
        "the process profile library reads the developer's real profiles/")


def test_the_guard_body_names_a_reach_and_passes_a_clean_test(tmp_path,
                                                              monkeypatch):
    """The guard's body, driven in process against a stand-in real
    location: a store and a library built on it are named, by seam, and a
    store elsewhere is not. Names only, never the path's contents.

    The second half is a store loaded in one test's window and read from
    its cache in the next, which the watchers on the disk methods alone
    would miss.

    RED under mutant "the check skipped" (``_config_reads_stay_off_the_real_one``
    returns straight after its ``yield``), observed at the leaky probe:

        >               next(guard)
        E               StopIteration

    RED under mutant "cached reads unseen" (``cfg`` not wrapped in
    ``_watch_the_real_config``), observed at the cached probe:

            assert leaky.cfg().optics.focal_length_mm == 1000.0
            with pytest.raises(AssertionError) as cached:
        >               next(guard)
        E               StopIteration

    RED under mutant "repeats dropped across windows" (``_RealConfig.note``
    dropping a line equal to the last one whatever window it was in, the
    first version of it), observed the same way at the cached probe:

        >               next(guard)
        E               StopIteration

    That defect was found by a stand-in run of test_flows_wizard_route.py
    with the session net removed and #341's own fixture put back: 1 of 5
    tests reading the stand-in's file was named.

    (Mutant "guard disabled", the autouse fixture's body made a bare
    ``yield``, leaves this body alone and this case green; the throwaway
    run below is the case it turns red.)
    """
    from conftest import _config_reads_stay_off_the_real_one
    real = tmp_path / "real"
    # Undone inside the test, not at its teardown: this run's own guard
    # tears down BEFORE monkeypatch does, and would otherwise read the
    # stand-in's reach as this test's.
    with monkeypatch.context() as m:
        m.setattr(_RealConfig, "files", frozenset(
            {_RealConfig._key(real / "astrodeck.json")}))
        m.setattr(_RealConfig, "profile_dirs", frozenset(
            {_RealConfig._key(real / "profiles")}))
        m.setattr(_RealConfig, "reads", [])

        guard = _config_reads_stay_off_the_real_one("probe::clean")
        next(guard)
        ConfigStore(path=tmp_path / "own" / "astrodeck.json").cfg()
        with pytest.raises(StopIteration):
            next(guard)

        guard = _config_reads_stay_off_the_real_one("probe::leaky")
        next(guard)
        ProfileLibrary(real / "profiles").list()
        leaky = ConfigStore(path=real / "astrodeck.json")
        leaky.set_optics(STANDIN_OPTICS)
        leaky.cfg()
        with pytest.raises(AssertionError) as verdict:
            next(guard)

        # The same store, loaded in that window and read from its cache in
        # the next, as a module-scoped fixture's store would be; the same
        # seam the last window ended on, so a repeat dropped across windows
        # would leave this one empty.
        guard = _config_reads_stay_off_the_real_one("probe::cached")
        next(guard)
        assert leaky.cfg().optics.focal_length_mm == 1000.0
        with pytest.raises(AssertionError) as cached:
            next(guard)
    text = str(verdict.value)
    assert text.startswith("probe::leaky reached the developer's real "
                           "config: ProfileLibrary._all (the real profiles "
                           "directory), ConfigStore._load (the real config "
                           "file), ConfigStore._save (the real config "
                           "file), ConfigStore.cfg (the real config "
                           "file)."), text
    assert "STANDIN" not in text and str(real) not in text, text
    assert str(cached.value).startswith(
        "probe::cached reached the developer's real config: ConfigStore.cfg "
        "(the real config file)."), str(cached.value)


#: The modules the S5 scan found singletons in, and the app, which binds
#: most of them: imported before the scan below, so it cannot pass on a run
#: that never loaded them.
_SCANNED = ("astrodeck.plans", "astrodeck.locations", "astrodeck.auth.users",
            "astrodeck.flows.store", "astrodeck.licensing", "astrodeck.api.app")


def test_nothing_loaded_is_left_on_the_real_config_directory(monkeypatch):
    """#361's scan as a case (S5): no global of a loaded ``astrodeck``
    module, and no attribute of an ``astrodeck`` object one holds, is a
    path in the real config directory, except the two ``_RealConfig``
    records (``CONFIG_FILE``, ``PROFILES_DIR``). A seam built on
    ``CONFIG_DIR`` at import that the session fixture's sweep did not move
    is named here, by module and attribute, and never by value.

    Its known positive first: a path planted as a module global, and a
    ``PlanLibrary`` on the real ``plans/`` planted as one, are both found.

    RED under mutant "the sweep skipped" (the session fixture's
    ``put_back = _sweep_off_the_real_config(...)`` made ``put_back = lambda:
    None``), run alone, observed (pytest cut the list; 15 in all):

        E       AssertionError: ['astrodeck.auth.users.user_store._path ->
        users.json', 'astrodeck.catalog.ephemeris.COMET_FILE ->
        ephemeris\\\\comets.j...json',
        'astrodeck.catalog.ephemeris.elements.CONFIG_DIR -> .',
        'astrodeck.config.EGAIN_CONFIG_FILE -> egain.json', ...]
        E       assert ['astrodeck.a...in.json', ...] == []
        E         Left contains 15 more items, first extra item:
        'astrodeck.auth.users.user_store._path -> users.json'

    Since #436 (S7) ``user_store`` holds no ``_path`` of its own: it reads
    ``config.CONFIG_DIR`` at each call, so the scan cannot list it. The same
    mutant in the S7 integration's private copy (the session scratchpad's
    ``S7-INTEG-r2-mut``, from a byte backup, sha256 checked after), run
    alone, observed:

        E       AssertionError: ['astrodeck.catalog.ephemeris.COMET_FILE ->
        ephemeris\\\\comets.json', 'astrodeck.catalog.ephemeris.ELEMENTS_DIR ->
        ephe...', 'astrodeck.config.EGAIN_CONFIG_FILE -> egain.json',
        'astrodeck.config.FILTER_CONFIG_FILE -> filter_names.json', ...]
        E       assert ['astrodeck.c...es.json', ...] == []
        E         Left contains 14 more items, first extra item:
        'astrodeck.catalog.ephemeris.COMET_FILE -> ephemeris\\\\comets.json'

    RED under mutant "the scan looks at globals only" (the object loop in
    ``_built_on_the_real_config`` given nothing to walk), not here but at
    the session fixture, whose known positives errored all six cases of
    this file at setup, observed:

        E                   AssertionError: astrodeck.plans.plan_library._dir
        is still on the developer's real config: the sweep missed it
    """
    for name in _SCANNED:
        importlib.import_module(name)
    real = config_mod.CONFIG_FILE.parent
    with monkeypatch.context() as m:
        m.setattr(profiles_mod, "S5_PLANTED_FILE", real / "planted.json",
                  raising=False)
        m.setattr(profiles_mod, "S5_PLANTED_LIBRARY",
                  PlanLibrary(real / "plans"), raising=False)
        planted = {name for _, _, name, _ in _built_on_the_real_config(real)}
    assert {"astrodeck.profiles.S5_PLANTED_FILE",
            "astrodeck.profiles.S5_PLANTED_LIBRARY._dir"} <= planted, planted
    left = sorted(f"{name} -> {where}"
                  for _, _, name, where in _built_on_the_real_config(real))
    assert left == [], left


def _stand_in_directory(root: Path) -> Path:
    """A config directory with the entries the S5 scan found no class
    watching: an empty ``plans/`` and a location store, a user store and an
    egain table with nothing in them."""
    (root / "plans").mkdir(parents=True)
    (root / "locations.json").write_text('{"locations": []}', encoding="utf-8")
    (root / "users.json").write_text('{"users": []}', encoding="utf-8")
    (root / "egain.json").write_text("{}", encoding="utf-8")
    return root


def _read_the_directory(root: Path) -> None:
    """Read each entry the way the product does: the three singletons'
    classes, and a module global's file read through no class at all, as
    ``config.load_egain_config`` reads ``egain.json``."""
    assert PlanLibrary(root / "plans").list() == []
    assert LocationStore(root / "locations.json").list() == []
    assert UserStore(root / "users.json").is_empty()
    assert read_json_or(root / "egain.json", None) == {}


def test_the_guard_names_what_was_touched_in_the_real_directory(
        tmp_path, monkeypatch):
    """The directory watcher, the session's own audit hook, driven against
    a stand-in real directory: each entry read is named as the event and
    the entry, never the path or what was in it, and the same reads of a
    directory of the test's own are not named at all.

    ``plans/`` is empty on purpose: a library listing an empty real
    directory answers "no plans" on this machine and some on the next, and
    only the listing touches it.

    What it cannot see: a store that asks whether its entry exists and
    finds nothing opens nothing (``persist.list_json`` checks ``is_dir``
    first). So a machine with no ``plans/`` runs such a test clean, and a
    machine with one names it: in the scratch copy, which has no config
    directory, "the sweep skipped" left the leak scanner's six listings of
    ``plans/`` and ``flows/`` unnamed, which the probe against a stand-in
    directory that had both had counted.

    RED under mutant "the directory unwatched" (``sys.addaudithook(audited)``
    removed from ``_watch_the_real_config``), observed:

        >               next(guard)
        E               StopIteration

    RED under mutant "listings unseen" (``os.scandir`` and ``os.listdir``
    taken out of ``_DIRECTORY_EVENTS``), observed:

        E       AssertionError: probe::dir reached the developer's real
        config: open of locations.json (the real config directory), open of
        users.json (the real config directory), open of egain.json (the
        real config directory). The session fixture moves ...
    """
    from conftest import _config_reads_stay_off_the_real_one
    real = _stand_in_directory(tmp_path / "real")
    own = _stand_in_directory(tmp_path / "own")
    with monkeypatch.context() as m:
        m.setattr(_RealConfig, "dirs", frozenset(
            {_RealConfig._key(real), os.path.normcase(os.path.abspath(real))}))
        m.setattr(_RealConfig, "reads", [])

        guard = _config_reads_stay_off_the_real_one("probe::own")
        next(guard)
        _read_the_directory(own)
        with pytest.raises(StopIteration):
            next(guard)

        guard = _config_reads_stay_off_the_real_one("probe::dir")
        next(guard)
        _read_the_directory(real)
        with pytest.raises(AssertionError) as verdict:
            next(guard)
    text = str(verdict.value)
    assert text.startswith(
        "probe::dir reached the developer's real config: os.scandir of "
        "plans (the real config directory), open of locations.json (the "
        "real config directory), open of users.json (the real config "
        "directory), open of egain.json (the real config directory)."), text
    assert str(real) not in text, text


class _Camera:
    """A connected camera, as a test earlier on the worker may leave one on
    the hub: ``effective_optics`` reads its sensor when the optics are not
    set, and the default focal length (530 mm) turns that into a field."""
    connected = True
    pixel_size_um = 3.76
    sensor_width = 6248
    sensor_height = 4176


def test_isolated_config_is_what_the_rig_facts_read(tmp_path, monkeypatch):
    """What a stale worker leaves behind, planted BEFORE the isolation (a
    connected camera and rotator on the hub, a recorded sky angle, a cached
    profile of another rig), and none of it is what the rig facts read
    after it: a fresh store has no optics and no camera, so
    ``effective_optics`` has no field. Then a profile saved through the
    library, with its own optics, and made active on the test's store IS
    what they read, and its file lands under the test's
    ``config/profiles``. Nothing reached the real config.

    RED under mutant "the helper leaves the camera" (the ``delitem`` loop
    removed), observed:

        AssertionError: a camera left on the hub is not the test's
        assert True is False

    RED under mutant "the helper leaves the profile library" (the
    ``profiles._dir`` patch removed), observed (the pytest root elided):

        E       AssertionError: the profile was saved outside the test's
        config
        E       assert False
        E        +  where False = is_file()
        E        +    where is_file = ((WindowsPath('.../
        test_isolated_config_is_what_t0/config') / 'profiles') /
        's4-guard-profile.json').is_file

    RED under mutant "the helper keeps the cached profile" (its two
    ``_profile_cache`` patches removed), observed:

        E       assert (True, 2.692, 1.799) == (True, 1.346, 0.9)
        E         At index 1 diff: 2.692 != 1.346

    RED under mutant "the helper leaves the sky angle" (its
    ``last_sky_angle`` patch removed), observed:

        E       AssertionError: assert {'pa_deg': 12.0, 'source':
        'centring'} is None

    RED under mutant "the sweep looks in the wrong place" (it takes
    modules under ``astrodeck.flows.`` only), at the known positive in
    ``IsolatedConfig.sweep``, and so at the setup of every test that uses
    ``isolated_config``, observed:

        E           AssertionError: astrodeck.config reads a config store
        other than this test's: the sweep missed it
        E           assert <astrodeck.config.ConfigStore object at 0x...>
        is <astrodeck.config.ConfigStore object at 0x...>
    """
    hub = hub_mod.hub
    monkeypatch.setitem(hub.devices, "camera", _Camera())
    monkeypatch.setitem(hub.devices, "rotator", _Camera())
    monkeypatch.setattr(hub, "last_sky_angle",
                        {"pa_deg": 12.0, "source": "centring"}, raising=False)
    monkeypatch.setattr(hub, "_profile_cache_id", "s4-guard-profile")
    monkeypatch.setattr(hub, "_profile_cache", Profile(
        id="s4-guard-profile", name="another rig",
        optics=STANDIN_OPTICS.model_copy(update={"focal_length_mm": 500.0})))
    reads = len(_RealConfig.reads)

    iso = IsolatedConfig(monkeypatch, tmp_path)
    assert config_mod.config_store is iso.store
    assert hub_mod.config_store is iso.store
    assert hub.last_sky_angle is None
    assert hub.effective_optics()["have_optics"] is False, (
        "a camera left on the hub is not the test's")
    assert "rotator" not in hub.devices

    profile = Profile(id="s4-guard-profile", name="Refractor",
                      primary_backend="native", optics=STANDIN_OPTICS,
                      devices=[ProfileDevice(role="rotator",
                                             backend="native")])
    profiles_mod.profiles.save(profile)
    assert (iso.dir / "profiles" / "s4-guard-profile.json").is_file(), (
        "the profile was saved outside the test's config")
    iso.store.cfg().active_profile_id = "s4-guard-profile"
    live = hub.effective_optics()
    assert (live["have_optics"], live["fov_w_deg"], live["fov_h_deg"]) == (
        True, 1.346, 0.9)
    assert _RealConfig.reads[reads:] == []


# ------------------------------------------- a throwaway run of the conftest

_PROBE = '''\
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
from astrodeck.config import ConfigStore
from astrodeck.flows import wizard
from astrodeck.flows.store import flow_store
from astrodeck.profiles import ProfileLibrary

MOSAIC = {"kind": wizard.KIND_MOSAIC, "target": "M31", "rows": 2, "cols": 3,
          "angle_mode": wizard.CAMERA_FIXED_AT_PA, "pa_deg": 30}


@pytest.fixture
def leaky_client(tmp_path, monkeypatch):
    # #341's fixture, given a store the careless way: with no path, a
    # ConfigStore is the developer's own file.
    store = ConfigStore()
    for mod in (config_mod, hub_mod, app_module):
        monkeypatch.setattr(mod, "config_store", store)
    monkeypatch.setattr(flow_store, "_dir", tmp_path, raising=False)
    with TestClient(app_module.create_app()) as c:
        yield c


def _grid(answer):
    [t] = [n for n in answer["graph"]["nodes"] if n["type"] == "target"]
    return (t["params"]["rows"], t["params"]["cols"])


def test_a_mosaic_through_a_store_with_no_path(leaky_client):
    r = leaky_client.post("/api/flows/wizard", json=MOSAIC)
    assert r.status_code == 200, r.text
    assert _grid(r.json()) == (2, 3)     # tiled from the stand-in's optics


def test_the_profiles_through_a_library_with_no_dir():
    assert len(ProfileLibrary().list()) == 1


def test_the_plans_through_a_library_with_no_dir():
    from astrodeck.plans import PlanLibrary
    assert len(PlanLibrary().list()) == 1


def test_control_the_same_mosaic_through_isolated_config(
        isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(flow_store, "_dir", tmp_path / "flows", raising=False)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        r = c.post("/api/flows/wizard", json=MOSAIC)
    assert r.status_code == 200, r.text
    assert _grid(r.json()) == (1, 1)
    assert r.json()["notes"] == [wizard.NO_OPTICS_REASON]
'''


def _standin_real_config(root: Path) -> Path:
    """A "real" config directory as a developer's machine may have one:
    optics set, an active profile with its own optics and a rotator, and a
    saved plan (S5: the app's startup lists ``plans/``, so the control
    below is clean only if the session fixture's sweep moved the plan
    library). Built through the product's own writers, from this process,
    where the directory is not a real location (so the guard stays silent
    here)."""
    store = ConfigStore(path=root / "astrodeck.json")
    store.set_optics(STANDIN_OPTICS)
    store.cfg().active_profile_id = "standin"
    store.bump_and_save()
    ProfileLibrary(root / "profiles").save(Profile(
        id="standin", name="Stand-in rig", primary_backend="native",
        optics=STANDIN_OPTICS,
        devices=[ProfileDevice(role="rotator", backend="native")]))
    PlanLibrary(root / "plans").save(SequencePlan(name="STANDIN plan"))
    return root


def _throwaway_run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run ``_PROBE`` under a copy of this conftest in a throwaway tree laid
    out like the repo, with ``ASTRODECK_CONFIG_DIR`` naming the stand-in
    "real" config and ``ASTRODECK_CAPTURE_DIR`` a stand-in captures root.
    The child imports the same ``astrodeck`` as this process."""
    repo = tmp_path / "repo"
    tests = repo / "server" / "tests"
    tests.mkdir(parents=True)
    real = _standin_real_config(tmp_path / "real-config")
    (tests / "conftest.py").write_bytes(_CONFTEST.read_bytes())
    (tests / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    ini = repo / "server" / "pytest.ini"
    ini.write_text("[pytest]\naddopts =\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_")}
    env["ASTRODECK_CONFIG_DIR"] = str(real)
    env["ASTRODECK_CAPTURE_DIR"] = str(repo / "captures")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(astrodeck.__file__).resolve().parents[1]),
         *filter(None, [env.get("PYTHONPATH")])])
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p",
         "no:randomly", "-n", "0", "-q", "-c", str(ini), "--rootdir",
         str(repo / "server"), str(tests / "test_probe.py")],
        cwd=repo / "server", env=env, capture_output=True, text=True,
        timeout=300)


@pytest.mark.integration
def test_a_leaky_fixture_is_named_and_the_isolated_one_is_not(tmp_path):
    """THE KNOWN POSITIVE and its control, in one throwaway run. The two
    leaky tests PASS: one tiles a 3x2 from the stand-in's optics, which a
    machine with none would have answered as one target, and the other
    counts the stand-in's profile. So nothing but the guard can see them,
    and it errors each at teardown, naming the seam it came through. The
    control, the same mosaic through ``isolated_config``, answers one
    target and the reason, with no verdict. The stand-in's telescope name
    appears nowhere in the output.

    RED under mutant "guard disabled" (``_no_test_reads_the_real_config``'s
    body a bare ``yield``): the leaky fixture passes, observed (the
    warnings block elided):

        E       AssertionError: ...                              [100%]
        ...
        E         3 passed, 1 warning in 2.89s
        E
        E       assert 'ERROR at teardown of
        test_a_mosaic_through_a_store_with_no_path' in '...  [100%]\\n...
        FAILED tests/test_real_config_guard.py::
        test_a_leaky_fixture_is_named_and_the_isolated_one_is_not
        1 failed, 3 passed in 5.97s

    Red too under "the check skipped" (the child's ``3 passed, 1 warning
    in 3.63s``).

    THE THIRD LEAKY TEST (S5) is a plan library with no directory, which
    no class watcher covers: it is named by the directory watcher. And the
    stand-in now holds a plan, so the control is clean only because the
    session fixture swept the plan library off the real directory before
    the app's startup listed it.

    RED under mutant "the directory unwatched", observed: the plan library
    passes unnamed, the child's progress ``.E.E..``, and

        E       assert 'ERROR at teardown of
        test_the_plans_through_a_library_with_no_dir' in '.E.E..
        [100%]\\n...

    RED under mutant "listings unseen": the child's ``4 passed, 1 warning,
    3 errors``, the library named only by what it opened, observed:

        E       assert "test_probe.py::test_the_plans_through_a_library_with_
        no_dir reached the developer's real config: os.scandir of plans (t...

    RED under mutant "the sweep skipped": the child's ``4 passed, 1
    warning, 4 errors``, the fourth the control's, observed in the child's
    output:

        tests/test_probe.py::test_control_the_same_mosaic_through_isolated_
        config reached the developer's real config: os.scandir of plans (the
        real config directory), open of plans/ (the real config directory).
    """
    done = _throwaway_run(tmp_path)
    out = done.stdout + done.stderr
    assert ("ERROR at teardown of test_a_mosaic_through_a_store_with_no_path"
            in out), out
    assert ("test_probe.py::test_a_mosaic_through_a_store_with_no_path "
            "reached the developer's real config: ConfigStore._load (the "
            "real config file)" in out), out
    assert ("ERROR at teardown of test_the_profiles_through_a_library_with_"
            "no_dir" in out), out
    assert ("test_probe.py::test_the_profiles_through_a_library_with_no_dir "
            "reached the developer's real config: ProfileLibrary._all (the "
            "real profiles directory)" in out), out
    assert ("ERROR at teardown of test_the_plans_through_a_library_with_"
            "no_dir" in out), out
    assert ("test_probe.py::test_the_plans_through_a_library_with_no_dir "
            "reached the developer's real config: os.scandir of plans (the "
            "real config directory)" in out), out
    assert "ERROR at teardown of test_control" not in out, out
    assert "4 passed" in out and "3 errors" in out, out
    assert "STANDIN" not in out, "the guard printed a value it read"
    assert done.returncode == 1, (done.returncode, out)
