"""No new consumer may read the site's coordinates without asking (#24).

The site defaults to latitude 0, longitude 0 with ``is_default`` True, and every
astronomical computation downstream then answers confidently for the Gulf of
Guinea. A preflight built on it put NGC 7331 four hours west when it was east
and rising, and printed bare GMST as local sidereal time. There is no sensible
default observing location, so the failure is not a bad answer, it is a
plausible one.

:mod:`astrodeck.site_gate` is the shared predicate the issue asks for. This is
the other half: the test that enumerates the consumers, so the thirty-seventh
cannot arrive unnoticed. It is a STATIC scan, deliberately - a runtime test can
only reach the consumers someone thought to call, and the whole defect is that
nobody thought about these.

Reading this when it fails: you have added, or moved, a function that reads a
latitude or longitude out of a site. Either ask :func:`site_is_set` (or take the
coordinates from :func:`site_lat_lon`, which answers None rather than 0,0), or
add the function to ``UNGUARDED`` below with a reason. The list is meant to
shrink. It is checked for staleness in both directions, so an entry that no
longer reads a site fails too rather than rotting. A call to an extractor such
as ``schedule._lat_lon`` is a read (``EXTRACTORS``, #527), and ``GUARDED`` pins
by name the consumers that once did not ask and now do.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "astrodeck"

# Anything that answers "is there a site", however it is spelled. `is_default`
# is here because five call sites asked it directly before site_gate existed and
# they are still correct; they are guarded, not exempt.
ASKS = {"site_is_set", "site_lat_lon", "is_default"}

# The site's coordinates, by every name they are read under in this tree.
COORDS = {"latitude", "longitude", "lat", "lon"}

# Functions that hand back a site's coordinates. A call to one is a read of the
# site exactly as `site["latitude"]` is, so its caller is a consumer (#527).
# Before this set the scan could not see `_lat_lon`'s callers at all: the
# extractor's own allowlist entry said the guard belonged at each caller, and
# nothing held any caller to it, which is how `resolve_window` resolved a dusk
# at 0,0. `site_lat_lon` is here too, although it asks by construction (it is
# in ASKS), so that its callers count as GUARDED consumers rather than as
# functions that never read a site.
EXTRACTORS = {"_lat_lon", "site_lat_lon"}


def _is_site_ish(node: ast.AST) -> bool:
    """Does this expression look like a site object rather than anything else?

    Deliberately loose on the name and strict on the key: `site`, `s`, `self.site`
    and `hub.site` are all real spellings here, and a false positive costs an
    allowlist entry with a reason, which is cheap. A false NEGATIVE costs the
    thirty-seventh consumer, which is the whole issue.
    """
    while isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr in {"site", "_site"} or _is_site_ish(node.value)
    if isinstance(node, ast.Name):
        return node.id in {"site", "s", "_site", "site_dict"}
    return False


def _reads_a_coordinate(node: ast.AST) -> bool:
    # site["latitude"]
    if isinstance(node, ast.Subscript) and _is_site_ish(node.value):
        key = node.slice
        if isinstance(key, ast.Constant) and key.value in COORDS:
            return True
    # site.get("latitude") / site.latitude
    if isinstance(node, ast.Attribute):
        if node.attr in COORDS and _is_site_ish(node.value):
            return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "get" and _is_site_ish(node.func.value):
            args = node.args
            if args and isinstance(args[0], ast.Constant) and args[0].value in COORDS:
                return True
    # _lat_lon(site) / schedule._lat_lon(self.hub.site): an extractor's call.
    if isinstance(node, ast.Call):
        callee = node.func
        name = (callee.id if isinstance(callee, ast.Name)
                else callee.attr if isinstance(callee, ast.Attribute) else None)
        if name in EXTRACTORS:
            return True
    return False


def _asks(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in ASKS:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in ASKS:
            return True
        if isinstance(sub, ast.Constant) and sub.value in ASKS:
            return True
    return False


def _consumers() -> dict[str, tuple[list[int], bool]]:
    """``{"module:function": ([line, ...], asks)}`` for every function that
    reads a coordinate out of a site, and whether it asks if there is one."""
    found: dict[str, tuple[list[int], bool]] = {}
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT).as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # A nested function is scanned on its own AND as part of its parent,
            # so a guard in the enclosing scope counts for it. That is right:
            # `if not site_is_set(site): return` above a closure does protect it.
            hits = [n.lineno for n in ast.walk(fn) if _reads_a_coordinate(n)]
            if hits:
                found[f"{rel}:{fn.name}"] = (sorted(set(hits)), _asks(fn))
    return found


def _scan() -> dict[str, list[int]]:
    """``{"module:function": [line, ...]}`` for every function that reads a
    coordinate out of a site and never asks whether there is one."""
    return {k: hits for k, (hits, asks) in _consumers().items() if not asks}


def _guarded() -> set[str]:
    """Every function that reads a coordinate out of a site and asks first."""
    return {k for k, (_hits, asks) in _consumers().items() if asks}


# Every consumer that still computes from the site without asking, with the
# reason it has not been fixed yet. This is #24's audit, frozen: the list may
# shrink freely, and may only grow by a deliberate edit that states why.
#
# They are NOT equally bad. The ones that publish a number a human will act on
# are the dangerous half; the ones that only feed a display already have an
# empty state a wrong number is no worse than.
UNGUARDED: dict[str, str] = {
    # --- POLAR ALIGNMENT left this list whole. Its five helpers were exempt
    # as "reachable only through `_drive`", which was true and unchecked; they
    # now read through `polar/native.py:_site_coords`, which refuses exactly as
    # `_site_dict` does. test_polar_refuses_without_a_site holds the refusal.

    # --- THE MOUNT MOVES. A wrong pier side or flip point is a cable wrap.
    #
    # `_maybe_meridian_flip` and the hub's `_compute_meridian` are fixed
    # outright and have left this list: the first declines the flip and says
    # why, the second publishes "unknown" instead of a countdown to a meridian
    # somewhere else. `test_the_meridian_needs_a_site.py` holds both.
    #
    # `_setup_target`'s latch (now `_arm_meridian_flip`), `_enforce_flip_owed`
    # and `_wait_for_flip_point` followed; test_the_flip_invariant_needs_a_site
    # holds them. The invariant was the one with a consequence: at 0,0 it held
    # a good target for twenty minutes and then abandoned it.
    #
    # The AM5 driver's two followed (test_the_am5_is_not_told_a_made_up_site).
    # `_site_latlon` was listed as reachable "only by a caller that goes around"
    # `hub.push_site_to_mount` - but `connect` is that caller, and it wrote 0,0
    # into the mount's firmware on every reconnect. A reason in this list is a
    # claim, and that one was never checked.

    # --- A PLAN OR A DISPLAY IS WRONG. Still wrong, but the operator is looking
    # at a screen rather than at a mount that is about to move.
    # `catalog/visibility.py` (the EarthLocation, the night scaffold and
    # compute_night) followed: it raises NoSite, its routes answer 409 and the
    # mosaic reports it per panel. test_visibility_needs_a_site holds them.

    # The comet row, /api/site/sky's default path, guided.sky_context and the
    # cloudmap refresh followed: test_the_display_consumers_need_a_site.

    # --- THE SCHEDULER JUDGES A TARGET. Visible only since the scan counts a
    # call to `_lat_lon` as a read (EXTRACTORS, #527). `resolve_window`,
    # `gating_status` and `constraint_gate` were here; all three are guarded
    # now and in GUARDED below (backlog WP-09, #191 collateral, 2026-09-30:
    # #540's altitude/hour-angle/Moon half closed alongside the DUSK window
    # fix). The remaining one is not reached at the placeholder.
    "sequence/resume_arm.py:_floor_eta_note":
        "reads `schedule._lat_lon(self.hub.site)` for the start-floor hold's "
        "ETA, but is called only after `engine._frame_altitude` read an "
        "altitude below the floor, and that answers None at a default site "
        "(#121), so the note is never computed at the placeholder. The claim "
        "rests on its one caller, in `ResumeArm`'s re-centre floor check; a "
        "second caller would need its own guard.",

    # --- NOT A REAL SITE BY CONSTRUCTION.
    "devices/sim.py:_side_for_ra":
        "the simulator's own pier-side model. There is no operator and no "
        "mount; 0,0 is as good a fiction as any.",
    "sequence/schedule.py:_lat_lon":
        "the shared extractor `gating_status`, `constraint_gate` and "
        "`ResumeArm._floor_eta_note` read through. It returns floats, never "
        "None, because `gating_status` and `constraint_gate` unpack it and "
        "compute at once, so a None would raise in the scheduler's tick "
        "rather than refuse. The guard "
        "belongs at each CALLER, and since #527 this scan holds each caller "
        "to that: a call to it is a read (EXTRACTORS), so every caller is "
        "guarded or listed here. (This reason used to name the sun and "
        "twilight helpers and `dark_enough` as its readers. None of them "
        "reads through it: `dark_enough` and `observing_night` read "
        "`site_gate.site_lat_lon`, and the sun helpers take bare numbers.)",
}

# Consumers that ask, pinned by name. The scan above finds a consumer that
# does not ask; it cannot notice one that stops being a consumer it can see,
# because a function whose read moved behind a helper the scan does not know
# drops out of both lists at once. Each entry here was once a consumer that
# did not ask, and must stay one that reads the site where the scan can see it
# and asks.
GUARDED: dict[str, str] = {
    "sequence/schedule.py:resolve_window":
        "#527: resolved a DUSK or DAWN boundary at the 0,0 placeholder, so a "
        "DUSK flow on a fresh rig waited twelve hours for Gulf of Guinea dusk "
        "and auto-resume opened on 0,0's night. It now reads "
        "`site_gate.site_lat_lon` and answers None for a sun boundary "
        "(test_h4_dusk_window_needs_a_site).",
    "sequence/schedule.py:gating_status":
        "#540, closed by backlog WP-09 (#191 collateral, 2026-09-30): judged "
        "a target's start altitude, its peak across the window and its rise "
        "estimate at the 0,0 placeholder, so a target could wait hours for a "
        "rise that was not the rig's. It now reads `site_gate.site_lat_lon` "
        "and, with no site, treats the gate the way `_frame_altitude`'s None "
        "is treated (#121): not a wait, and says \"no site\" in the reason "
        "instead of a number (test_idle_park_hold.py's no-site cases).",
    "sequence/schedule.py:constraint_gate":
        "#540, closed with `gating_status`, whose gating it is part of: "
        "judged the hour angle and the Moon's altitude at the 0,0 "
        "placeholder, so an hour-angle limit could close a target's window "
        "for the night at longitude 0. It now reads `site_gate.site_lat_lon` "
        "and skips a check that needs coordinates when there is no site, "
        "returning None (no constraint) exactly as it does when the "
        "schedule sets no limit at all.",
}


def test_no_site_consumer_is_unguarded_and_unlisted():
    """The gate. Anything reading a site coordinate is guarded or listed.

    MUTATION: delete the `if not site_is_set(...)` from
    `site_gate.site_lat_lon`'s caller in any guarded function. Observed: that
    function appears here as an unlisted consumer and this fails.
    """
    unlisted = {k: v for k, v in _scan().items() if k not in UNGUARDED}
    assert not unlisted, (
        "these read a site coordinate without asking whether a site is set, and "
        "at the 0,0 default they will answer for the Gulf of Guinea:\n"
        + "\n".join(f"  {k} (lines {v})" for k, v in sorted(unlisted.items()))
        + "\n\nGuard them with astrodeck.site_gate, or add each to UNGUARDED "
          "with the reason it is being left.")


def test_the_allowlist_does_not_rot():
    """An entry that no longer reads a site is a lie the next reader believes.

    MUTATION: add a bogus entry to `UNGUARDED`. Observed: this fails naming it.
    """
    stale = sorted(set(UNGUARDED) - set(_scan()))
    assert not stale, (
        "these are listed as unguarded consumers but no longer read a site "
        f"coordinate, or no longer exist: {stale}")


def test_the_guarded_set_still_reads_the_site_and_asks():
    """Each GUARDED consumer still reads the site where the scan sees it, and
    asks first (#527).

    MUTANT "guard removed: placeholder dusk returned" (H4-SCHED;
    `resolve_window` reading the site through `_lat_lon` again, never
    asking): RED here (observed):
        AssertionError: these are pinned as consumers that ask whether a
        site is set, and the scan no longer sees them read a site and ask:
        ['sequence/schedule.py:resolve_window']. If a read moved behind a
        new helper, add the helper to EXTRACTORS; if the guard went, put it
        back.
        assert not ['sequence/schedule.py:resolve_window']
    and the gate above goes red with it, naming `resolve_window` as an
    unlisted consumer (observed):
        AssertionError: these read a site coordinate without asking whether
        a site is set, and at the 0,0 default they will answer for the Gulf
        of Guinea:
            sequence/schedule.py:resolve_window (lines [472])
        [the "Guard them" hint]
        assert not {'sequence/schedule.py:resolve_window': [472]}

    MUTANT "the extractor arm dropped" (the `EXTRACTORS` arm of
    `_reads_a_coordinate` taken out): RED here (observed), since
    `resolve_window` reads through `site_lat_lon` and the scan no longer sees
    it read at all:
        AssertionError: these are pinned as consumers that ask whether a
        site is set, and the scan no longer sees them read a site and ask:
        ['sequence/schedule.py:resolve_window']. [the hint]
        assert not ['sequence/schedule.py:resolve_window']
    The staleness case goes red with it (observed), its three new entries
    no longer seen to read a site:
        AssertionError: these are listed as unguarded consumers but no
        longer read a site coordinate, or no longer exist:
        ['sequence/resume_arm.py:_floor_eta_note',
        'sequence/schedule.py:constraint_gate',
        'sequence/schedule.py:gating_status']
    """
    guarded = _guarded()
    lost = sorted(k for k in GUARDED if k not in guarded)
    assert not lost, (
        "these are pinned as consumers that ask whether a site is set, and "
        f"the scan no longer sees them read a site and ask: {lost}. If a read "
        "moved behind a new helper, add the helper to EXTRACTORS; if the "
        "guard went, put it back.")


def test_a_call_to_an_extractor_is_a_read():
    """The guard on the new arm. `_lat_lon` hands back 0,0 for a site nobody
    saved, so calling it is reading the site, however it is spelled; and a
    caller that asks `site_is_set` first is guarded.

    MUTATION "the extractor arm dropped": RED (observed):
        AssertionError: _lat_lon(site)
        assert False
         +  where False = any(<generator object
         test_a_call_to_an_extractor_is_a_read.<locals>.<genexpr> at ...>)
    """
    for call in ("_lat_lon(site)", "schedule._lat_lon(self.hub.site)",
                 "site_lat_lon(site)"):
        fn = ast.parse(
            f"def f(site, self, schedule):\n    return {call}\n").body[0]
        assert any(_reads_a_coordinate(n) for n in ast.walk(fn)), call
    unguarded = ast.parse(
        "def f(site):\n"
        "    lat, lon = _lat_lon(site)\n"
        "    return lat\n").body[0]
    assert not _asks(unguarded), "a bare `_lat_lon` call reads as asking"
    asks = ast.parse(
        "def f(site):\n"
        "    if not site_is_set(site):\n"
        "        return None\n"
        "    return _lat_lon(site)\n").body[0]
    assert _asks(asks), "the scan cannot see a guard before an extractor"


def test_the_scan_can_see_a_consumer_at_all():
    """The guard on the guard. A detector that matches nothing passes both cases
    above forever, and this whole file would be a test that cannot fail - which
    is a defect class this project has hit repeatedly.

    MUTATION: make `_reads_a_coordinate` return False unconditionally. Observed:
    this fails, while the two cases above stay green.
    """
    source = (
        "def f(site):\n"
        "    return site['latitude'] + site['longitude']\n")
    tree = ast.parse(source)
    fn = tree.body[0]
    hits = [n.lineno for n in ast.walk(fn) if _reads_a_coordinate(n)]
    assert hits, "the scan cannot see the plainest possible site consumer"
    assert not _asks(fn), "the scan thinks an unguarded function asks"

    guarded = ast.parse(
        "def g(site):\n"
        "    if not site_is_set(site):\n"
        "        return None\n"
        "    return site['latitude']\n").body[0]
    assert _asks(guarded), "the scan cannot see a guard that is right there"


@pytest.mark.parametrize("shape", [
    "site['latitude']",
    "site.get('longitude')",
    "self.site['latitude']",
    "hub.site['longitude']",
    "self.hub.site.get('latitude')",
    "s['latitude']",
])
def test_the_scan_sees_every_spelling_this_tree_uses(shape):
    """Each of these is a real line in the tree. A detector that misses one
    leaves a whole family of consumers invisible.

    MUTATION: drop the `ast.Attribute` arm of `_is_site_ish`. Observed: every
    spelling but the two bare names stops being seen and this fails for three
    of the six, and takes the staleness case with it.

    A `Subscript` arm was written here first and deleted: `self.hub.site` is
    already matched by the `Attribute` arm before recursion reaches it, and
    removing the arm changed no case in this file. It was reach for a spelling
    - a site nested inside another subscript - that this tree does not use.
    """
    fn = ast.parse(f"def f(site, s, hub, self):\n    return {shape}\n").body[0]
    assert any(_reads_a_coordinate(n) for n in ast.walk(fn)), shape
