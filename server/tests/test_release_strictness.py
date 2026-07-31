"""A warning during an unattended build is not a control.

0.2.18 shipped with no survey pack AND online fetch off (the correct
offline-first default), so the Atlas on the deployed box was simply black. The
only evidence was a WARNING line in a build log nobody read — #100.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_release.py"


def _build(tmp_path, *extra):
    repo = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--version", "0.0.0-test",
         "--repo-root", str(repo), "--out", str(tmp_path), *extra],
        capture_output=True, text=True, timeout=600)


@pytest.mark.slow
def test_strict_refuses_when_a_requested_asset_is_absent(tmp_path):
    """The whole point: asking for the pack and not getting it must FAIL."""
    r = _build(tmp_path, "--strict", "--survey-pack", str(tmp_path / "nope"))
    assert r.returncode != 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "survey pack" in out
    # It must say what the omission COSTS, not just that a path was missing.
    assert "no image source" in out or "Atlas" in out


@pytest.mark.slow
def test_strict_names_every_missing_asset_not_just_the_first(tmp_path):
    """One rebuild per discovery is how a release takes an afternoon."""
    r = _build(tmp_path, "--strict",
               "--survey-pack", str(tmp_path / "nope"),
               "--astap-dir", str(tmp_path / "also-nope"))
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "survey pack" in out and "ASTAP" in out


@pytest.mark.slow
def test_without_strict_it_still_builds_and_says_what_is_missing(tmp_path):
    """Local builds stay convenient; the omission is still stated."""
    r = _build(tmp_path, "--survey-pack", str(tmp_path / "nope"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "missing" in (r.stdout + r.stderr).lower()
