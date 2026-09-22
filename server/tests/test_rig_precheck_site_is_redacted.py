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
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rig_precheck.py"
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
