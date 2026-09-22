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
longer reads a site fails too rather than rotting.
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


def _scan() -> dict[str, list[int]]:
    """``{"module:function": [line, ...]}`` for every function that reads a
    coordinate out of a site and never asks whether there is one."""
    found: dict[str, list[int]] = {}
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
            if hits and not _asks(fn):
                found[f"{rel}:{fn.name}"] = sorted(set(hits))
    return found


# Every consumer that still computes from the site without asking, with the
# reason it has not been fixed yet. This is #24's audit, frozen: the list may
# shrink freely, and may only grow by a deliberate edit that states why.
#
# They are NOT equally bad. The ones that publish a number a human will act on
# are the dangerous half; the ones that only feed a display already have an
# empty state a wrong number is no worse than.
UNGUARDED: dict[str, str] = {
    # --- POLAR ALIGNMENT, guarded upstream rather than five more times.
    # `_site_dict` refuses at a default site and `_drive` calls it first, before
    # any device is even resolved, so none of these five is reachable without a
    # site. They stay listed because the scan is static and cannot know that,
    # and because a future caller that goes around `_drive` would make each of
    # them live again. `test_polar_refuses_without_a_site.py` holds the gate.
    #
    # Why this path got the guard first: its whole output is an instruction to a
    # human standing at the mount - turn the azimuth knob this way, this far -
    # and nothing in that instruction tells the operator it was computed for the
    # Gulf of Guinea.
    "polar/native.py:_refuse_low_arc":
        "whether the measured arc is high enough to trust; latitude-dependent.",
    "polar/native.py:_refuse_if_no_longer_measurable":
        "the target's altitude, used to stop a run that has sunk too low.",
    "polar/native.py:_reject_implausible_fit":
        "the plausibility bound on the fitted axis error, latitude-dependent.",
    "polar/native.py:_ra_step_hours":
        "hour angle from longitude, which sets the step between points.",
    "polar/native.py:_log_measurement":
        "hour angle again, into the durable log, so a wrong site would also "
        "poison the forensics.",

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

    # --- NOT A REAL SITE BY CONSTRUCTION.
    "devices/sim.py:_side_for_ra":
        "the simulator's own pier-side model. There is no operator and no "
        "mount; 0,0 is as good a fiction as any.",
    "sequence/schedule.py:_lat_lon":
        "the shared extractor the sun and twilight helpers read through. It "
        "cannot answer None: dark_enough is the one deliberate fail-open in "
        "this whole audit (see site_gate's module docstring), and a raising "
        "extractor underneath it would turn that exception into a crash. The "
        "guard for this family belongs at each CALLER, not here.",
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
