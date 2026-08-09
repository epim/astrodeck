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
    """The whole point: asking for an asset and not getting it must FAIL.

    The survey pack was this test's example until #198 — a complete release now
    ships no DSS2 tiles, so demanding them here would block every release on
    the thing the fix removed. ASTAP carries the property instead: same
    mechanism, on an asset we may actually distribute."""
    r = _build(tmp_path, "--strict", "--astap-dir", str(tmp_path / "nope"))
    assert r.returncode != 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "ASTAP" in out
    # It must say what the omission COSTS, not just that a path was missing.
    assert "plate solving" in out
    assert "survey pack" not in out, (
        "strict still demands the tiles #198 stopped us shipping")


@pytest.mark.slow
def test_the_real_cli_refuses_and_says_what_the_omission_costs(tmp_path):
    """The end-to-end half: the actual script, actual argv, actual exit code.

    This used to assert TWO assets were named in one pass. It cannot any more —
    it builds the REAL repo, which has a built ui/dist, and since #198 removed
    the survey pack from the required set, ASTAP is the only asset that can be
    absent here. The multi-asset property is not lost: it is covered against a
    fake repo (where both the UI and ASTAP are genuinely missing) by
    test_build_release.py::test_strict_names_every_missing_asset_in_one_pass.
    What only THIS test can show is that the property survives the command
    line, and that the process exits non-zero rather than warning."""
    r = _build(tmp_path, "--strict", "--astap-dir", str(tmp_path / "also-nope"))
    out = r.stdout + r.stderr
    assert r.returncode != 0, "the CLI warned instead of refusing — that is #100"
    assert "ASTAP" in out and "plate solving" in out


@pytest.mark.slow
def test_without_strict_it_still_builds_and_says_what_is_missing(tmp_path):
    """Local builds stay convenient; the omission is still stated."""
    r = _build(tmp_path, "--survey-pack", str(tmp_path / "nope"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "missing" in (r.stdout + r.stderr).lower()
