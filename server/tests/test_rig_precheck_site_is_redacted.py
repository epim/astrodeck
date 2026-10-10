# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#128: the rig's site can be checked without printing any of it.

WHY THIS EXISTS. Nothing on the rig reported whether a site was saved.
`rig_precheck.py --report` printed devices, mount, polar, sequence and lanes and
said nothing about the site, so the only way to answer "is `is_default` false"
was to read `config/astrodeck.json` over ssh - and that file carries the label
and the coordinates in clear. On 2026-09-21 I did exactly that, selecting
`name` alongside `is_default`, and put the site's LABEL (one of the three
privacy needles) into an agent transcript. The question was safe; the only
available way to ask it was not.

So the question gets an answer of its own, and the answer is a boolean and an
elevation. The cases below are mostly about what the function must NOT be able
to say, because that is the property that matters: a helper that is usually
redacted is not a redacted helper.

MUTATIONS RUN, and what each printed:

  M1, include the site name in the configured line - the exact mistake this
  issue is about. 3 failed, and the one to read is
  `test_the_real_needles_cannot_escape_either`: the scan against the ACTUAL
  secret catches it, rather than anyone remembering the rule.

  M2, `if site.get("is_default", True)` -> `if site.get("is_default", False)`,
  so an absent key reads as CONFIGURED. 1 failed: the missing-key case. That
  default is the safe direction - a config too old or too broken to say must
  not be reported as a real site.

  M3, drop the coordinate type check, so a site with null coordinates reports
  as configured. 1 failed: the half-saved case.

#140 EXTENDS THIS FILE with the mount's pointing. A reset AM5 (#133) comes up
believing it is parked at home, on the celestial pole - and a mount pointing
at the pole reports an altitude equal to the SITE LATITUDE, to the tenth of a
degree `hub.py` rounds it to. `_mount_line` must never read `alt`/`az` off the
mount block, the same as `redact.py`'s `_MOUNT_DERIVED_KEYS` already withholds
them from every non-admin API caller - rig_precheck runs with the admin token,
so nothing upstream does that for it.

  M4, add `alt={mount.get('alt')} az={mount.get('az')}` to `_mount_line`'s
  format string, the exact mistake #140 is about. 2 failed:

    test_mount_line_never_prints_altitude_or_azimuth -
      AssertionError: 41.2 reached the mount line: mount: slewing=False
      tracking=False parked=True ra=00:00:00 dec=+90:00:00 alt=41.2 az=187.6

    test_no_script_or_tool_prints_mount_altitude_or_azimuth -
      AssertionError: mount alt/az field read found in:
      ['scripts\\rig_precheck.py']

  Both fired: the unit test on the rendered line, the static scan on the
  source shape of the mutant itself - the two are independent defences and
  this is evidence neither is a tautology.

#883 REMOVES the sky coordinate from the same line. `_mount_line` used to print
`ra=` and `dec=` on the reasoning that a pointing is not a geolocator, which
holds for a tracking mount and not for a stopped one: a stationary mount holds a
fixed hour angle, so its RA follows the site's sidereal clock, and the pole
parked mount the precheck normally meets reports exactly that clock. The line
now carries slewing, tracking, parked and position_known and no number.

  M5, restore `ra={mount.get('ra_str')} dec={mount.get('dec_str')}` to the
  format string. 6 failed, 9 passed: the parked and the tracking mount on the
  rendered line, on the whole `--report`, the exact-line check, and the tree
  scan, which names the file:

    test_mount_line_never_prints_a_sky_coordinate[parked-at-home] -
      AssertionError: '00:00:00' reached the mount line: mount: slewing=False
      tracking=False parked=True position_known=False ra=00:00:00
      dec=+90:00:00

    test_no_script_or_tool_reads_a_mount_sky_coordinate -
      AssertionError: mount RA/Dec field read found in:
      {'scripts/rig_precheck.py': ("mount.get('ra_str'", "mount.get('dec_str'"),
      ...}

  The same 6 fail on the unmodified pre-#883 file.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rig_precheck.py"
_REPO_ROOT = _SCRIPT.parent.parent
_spec = importlib.util.spec_from_file_location("astrodeck_rig_precheck", _SCRIPT)
precheck = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_rig_precheck"] = precheck
_spec.loader.exec_module(precheck)

NEEDLES = Path("C:/Users/bear/.astrodeck/privacy-needles.txt")

#: A site shaped like a real one, with values that are NOT the real ones. The
#: label is deliberately the kind of thing a real label is - a place a person
#: would type - so a function that prints "the name" fails here too.
A_SITE = {"name": "Rivendell", "latitude": 41.2345678, "longitude": -73.9876543,
          "elevation_m": 143.0, "is_default": False}


def _write(tmp_path, site) -> None:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "astrodeck.json").write_text(
        json.dumps({"site": site}), encoding="utf-8")
    precheck.ROOT = str(tmp_path)


# ------------------------------------------------- what it must never print

def test_no_part_of_the_site_reaches_the_line(tmp_path, monkeypatch):
    """THE POINT OF THE FILE. Not "it happens to say something short" - the
    label, the latitude and the longitude must each be absent, in every
    rendering a float prints in."""
    _write(tmp_path, A_SITE)
    line = precheck._site_line()

    assert A_SITE["name"] not in line, line
    for value in (A_SITE["latitude"], A_SITE["longitude"]):
        for form in {repr(value), str(value), f"{value:.2f}", f"{value:.4f}",
                     f"{abs(value):.2f}", f"{abs(value):.4f}"}:
            assert form not in line, f"{form} reached the output: {line}"
    assert line == "configured (143 m)", line


@pytest.mark.skipif(not NEEDLES.exists(),
                    reason="the needles file is not on this machine")
def test_the_real_needles_cannot_escape_either(tmp_path):
    """The synthetic case above grades the shape. This grades the actual
    secret, on a machine that has it, and reports only whether it matched -
    never what matched. It is the same two-scan split `#19`'s route scanner
    uses, and for the same reason: a check that only ever runs against invented
    values is not evidence about the real one."""
    needles = [n.strip() for n in
               io.open(NEEDLES, encoding="utf-8", errors="replace")
               .read().splitlines() if n.strip()]
    assert needles, "the needles file is present but empty"
    # A site built FROM the real needles, which is the worst case: if any part
    # of the input can reach the output, this finds it.
    site = dict(A_SITE)
    site["name"] = needles[0]
    _write(tmp_path, site)
    line = precheck._site_line()
    assert not any(n.lower() in line.lower() for n in needles), (
        "a needle reached rig_precheck's site line")


# ------------------------------------------------------ and what it must say

def test_an_unset_site_says_so_and_says_what_it_costs(tmp_path):
    """The whole reason the question gets asked. "NOT SET" alone would be a
    status; naming the consequence is what makes someone act on it."""
    _write(tmp_path, dict(A_SITE, is_default=True))
    line = precheck._site_line()
    assert line.startswith("NOT SET")
    assert "latitude 0, longitude 0" in line, line


def test_a_config_that_does_not_say_is_treated_as_not_set(tmp_path):
    """`is_default` absent - an older config, or a truncated write. The safe
    default is NOT SET: a file too old to say must not be reported as a real
    site."""
    _write(tmp_path, {"latitude": 41.0, "longitude": -73.0})
    assert precheck._site_line().startswith("NOT SET")


def test_a_half_saved_site_is_not_reported_as_configured(tmp_path):
    """`is_default` cleared, coordinates absent. The dict shape is the one
    nothing validates (`Site` types both as float and pydantic refuses null),
    so this is the path where it can actually happen."""
    _write(tmp_path, {"name": "Rivendell", "is_default": False})
    line = precheck._site_line()
    assert "not numbers" in line and "not set" in line.lower(), line


def test_an_unreadable_config_is_not_silently_fine(tmp_path):
    """No file at all. A helper that answered "configured" here would be worse
    than no helper, and one that raised would take the precheck down with it."""
    precheck.ROOT = str(tmp_path / "nothing-here")
    line = precheck._site_line()
    assert "UNREADABLE" in line and "not set" in line.lower(), line


def test_a_zero_elevation_site_is_still_configured(tmp_path):
    """0 m is a real elevation and must not read as missing - the rig itself
    has elevation 0 saved."""
    _write(tmp_path, dict(A_SITE, elevation_m=0.0))
    assert precheck._site_line() == "configured (0 m)"


# ---------------------------------------------------- the mount line (#140)

#: `hub.py` publishes the mount's alt/az rounded to a tenth of a degree
#: alongside ra/dec, tracking and parked. This is what `/api/status` would
#: hand back for a mount that has just reset and believes it is parked at
#: home, on the pole (#133): the altitude equals A_SITE's fake latitude,
#: because that is exactly the shape of the leak in #140 - not a coincidence
#: this test constructs, the mechanism the issue is about. az is an arbitrary
#: distinctive value so a leak of either key is caught on its own.
A_MOUNT_AT_HOME = {
    "slewing": False, "tracking": False, "parked": True, "position_known": False,
    "ra_str": "00:00:00", "dec_str": "+90:00:00",
    "ra_hours": 0.0, "dec_deg": 90.0,
    "alt": round(A_SITE["latitude"], 1), "az": 187.6,
}

#: #883: a mount that is NOT at the pole and NOT parked. Its RA and Dec are
#: distinctive on purpose (nothing else on a precheck line looks like them), and
#: it is tracking, so the one place a sky coordinate would be defensible is the
#: place the line still must not print one: the fix is "no coordinate", not "no
#: coordinate while parked", because a stopped tracker reads the same.
A_MOUNT_TRACKING = {
    "slewing": False, "tracking": True, "parked": False, "position_known": True,
    "ra_str": "13:22:19", "dec_str": "-17:26:32\N{DEGREE SIGN}",
    "ra_hours": 13.3719, "dec_deg": -17.4421,
    "alt": 61.7, "az": 233.4,
}


def _forms(value) -> set[str]:
    """Every rendering a coordinate value reaches a line in."""
    if isinstance(value, str):
        return {value, value.replace("\N{DEGREE SIGN}", " deg")}
    return {repr(value), str(value), f"{value:.1f}", f"{value:.2f}",
            f"{value:.3f}", f"{value:.4f}", f"{abs(value):.1f}", f"{abs(value):.2f}"}


def _coordinate_forms(mount: dict) -> set[str]:
    return {form for key in ("ra_str", "dec_str", "ra_hours", "dec_deg", "alt", "az")
            for form in _forms(mount[key])}


def test_mount_line_never_prints_altitude_or_azimuth():
    """THE POINT OF #140. The fake mount's alt/az must not reach the line in
    any numeric rendering, the same way the site tests above check every form a
    float prints in."""
    line = precheck._mount_line(A_MOUNT_AT_HOME)
    for value in (A_MOUNT_AT_HOME["alt"], A_MOUNT_AT_HOME["az"]):
        for form in {repr(value), str(value), f"{value:.1f}", f"{value:.2f}",
                     f"{abs(value):.1f}"}:
            assert form not in line, f"{form} reached the mount line: {line}"


@pytest.mark.parametrize("mount", [A_MOUNT_AT_HOME, A_MOUNT_TRACKING],
                         ids=["parked-at-home", "tracking"])
def test_mount_line_never_prints_a_sky_coordinate(mount):
    """THE POINT OF #883. A mount that is not tracking holds a fixed hour angle,
    so its reported RA advances with the site's sidereal clock and the RA plus
    the time of the run is the longitude; a mount parked at home reports a Dec
    that is a constant and an RA that IS that clock. So the line prints no RA,
    no Dec and no number at all, for the parked mount the precheck normally
    meets and for a tracking one alike.

    Graded three ways because they fail differently: the value in each form it
    prints in, the `ra=`/`dec=` labels (a renamed or reformatted field still
    needs a label), and the absence of any digit on the line, which is true of
    four booleans and false of every coordinate."""
    line = precheck._mount_line(mount)
    for form in _coordinate_forms(mount):
        assert form not in line, f"{form!r} reached the mount line: {line}"
    for label in ("ra=", "dec=", "alt=", "az="):
        assert label not in line, f"{label} reached the mount line: {line}"
    assert not re.search(r"\d", line), f"a number reached the mount line: {line}"


def test_mount_line_still_says_what_it_is_for():
    """Not "it happens to print nothing" - slewing, tracking, parked and
    whether the position is trusted must still be there, with the value the
    mount reported, or the line stopped doing its job. The booleans are not
    site-derived."""
    line = precheck._mount_line(A_MOUNT_AT_HOME)
    assert line == ("mount: slewing=False tracking=False parked=True "
                    "position_known=False"), line
    line = precheck._mount_line(A_MOUNT_TRACKING)
    assert line == ("mount: slewing=False tracking=True parked=False "
                    "position_known=True"), line


def _run_report(monkeypatch, tmp_path, capsys, mount: dict) -> str:
    """Run the whole `--report` against a fake rig and return what it printed,
    which is what gets pasted into a chat or a deploy transcript. The unit tests
    above grade `_mount_line`; this grades every line `main` writes, so a
    coordinate printed from somewhere else in `main` is caught too."""
    _write(tmp_path, A_SITE)
    monkeypatch.setattr(precheck, "_session_cookie", lambda: "ad_session=x")
    replies = {
        "/api/status": {"mount": mount,
                        "connected": {"telescope": {"connected": True}}},
        "/api/polar/state": {"state": "idle", "running": False, "message": ""},
        "/api/sequence/state": {"state": "idle", "running": False},
        "/api/alerts/health": {"deadman": None},
    }
    monkeypatch.setattr(precheck, "_get", lambda cookie, path: replies[path])
    assert precheck.main(["--report"]) == 0
    out = capsys.readouterr().out
    # Guard against passing on a report that stopped early: the line under test
    # and the verdict at the end must both be there.
    assert "mount: slewing=" in out and "IDLE" in out, out
    return out


@pytest.mark.parametrize("mount", [A_MOUNT_AT_HOME, A_MOUNT_TRACKING],
                         ids=["parked-at-home", "tracking"])
def test_the_whole_report_carries_no_mount_coordinate(
        monkeypatch, tmp_path, capsys, mount):
    """The pasteable thing is the report, not the function. No part of it may
    carry the mount's RA, Dec, altitude or azimuth, in any rendering."""
    out = _run_report(monkeypatch, tmp_path, capsys, mount)
    for form in _coordinate_forms(mount):
        assert form not in out, f"{form!r} reached the report"


#: The shape a leak actually took in the codebase (`hub.py` line 7802):
#: `mount.get('alt')`/`mount['az']` etc, read off a local named ``mount``.
#: Matched with or without the leading assignment, so a fresh script that
#: writes ``mount = status.get("mount") or {}`` and then reads a key off it
#: is caught the moment it does, not only inside rig_precheck.py.
_MOUNT_ALTAZ_FIELDS = ("alt", "az", "alt_deg", "az_deg",
                       "altitude", "azimuth", "altitude_deg", "azimuth_deg")
_MOUNT_ALTAZ_PATTERN = re.compile(
    r"\bmount\b\s*(?:\.\s*get\(\s*|\[\s*)['\"](?:"
    + "|".join(_MOUNT_ALTAZ_FIELDS) + r")['\"]"
)


def test_no_script_or_tool_prints_mount_altitude_or_azimuth():
    """A STATIC scan, not a runtime one (#140's fix shape). The risk is not
    confined to rig_precheck.py: any ad hoc helper under scripts/ or tools/
    that reads `/api/status` and prints `mount.get('alt')` recreates the same
    latitude leak. This scans for the SHAPE of that read (a `mount` variable
    indexed or `.get()`-ed by an alt/az field name), not the word "altitude" -
    which appears safely in `_site_line`'s own prose about the cost of an
    unset site, and would false-positive a naive word scan."""
    hits = []
    for base in ("scripts", "tools"):
        base_dir = _REPO_ROOT / base
        if not base_dir.is_dir():
            continue
        for path in base_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if _MOUNT_ALTAZ_PATTERN.search(text):
                hits.append(str(path.relative_to(_REPO_ROOT)))
    assert not hits, f"mount alt/az field read found in: {hits}"


#: #883: the same shape for the sky coordinate. A stationary mount's RA follows
#: the site's sidereal clock, so a script that reads one off the status and
#: prints it prints a site coordinate. `ra_str`/`dec_str` exist only to be shown.
_MOUNT_SKY_FIELDS = ("ra", "dec", "ra_str", "dec_str", "ra_hours", "dec_deg",
                     "ra_deg")
_MOUNT_SKY_PATTERN = re.compile(
    r"\bmount\b\s*(?:\.\s*get\(\s*|\[\s*)['\"](?:"
    + "|".join(_MOUNT_SKY_FIELDS) + r")['\"]"
)

#: Code that reads a mount coordinate and prints none of it. The value is the
#: exact reads the file may make, so a NEW read in an allow-listed file is
#: still a failure, and an entry whose reads are gone is a failure too.
_MOUNT_SKY_READS_ALLOWED = {
    # Compares the simulator mount to the target it was just slewed to and
    # returns a bool; the simulator has a synthetic public site.
    "tools/site/capture_sim.py": ('mount["ra_hours"', 'mount["dec_deg"'),
}


def test_no_script_or_tool_reads_a_mount_sky_coordinate():
    """A static scan for #883, the sibling of the alt/az scan above and with
    the same limits: it finds the SHAPE of the read (a `mount` variable indexed
    or `.get()`-ed by an RA/Dec field name), not the word "ra". Code that
    compares a coordinate and prints nothing is allow-listed by name together
    with its exact reads, so adding a print to it does not slip through."""
    found = {}
    for base in ("scripts", "tools"):
        base_dir = _REPO_ROOT / base
        if not base_dir.is_dir():
            continue
        for path in base_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            reads = tuple(m.group(0) for m in _MOUNT_SKY_PATTERN.finditer(text))
            if reads:
                found[path.relative_to(_REPO_ROOT).as_posix()] = reads
    assert found == _MOUNT_SKY_READS_ALLOWED, (
        f"mount RA/Dec field read found in: {found}; allowed: "
        f"{_MOUNT_SKY_READS_ALLOWED}")
