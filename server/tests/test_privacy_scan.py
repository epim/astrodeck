"""The scanner that keeps the real observing site out of the repository.

Graded with FAKE needles, planted through the environment, against throwaway
git repositories in ``tmp_path``. That is not a convenience: the real values
live outside the tree precisely so nothing in it can reconstruct them, and a
test that reached for the developer's own needle file would have put the whole
scan back inside the repository by the back door -- the failure the 2026-09-08
history rewrite existed to undo.

So every test here sets BOTH ``ASTRODECK_PRIVACY_NEEDLES`` and
``ASTRODECK_PRIVACY_NEEDLES_FILE``: the second points at a path that does not
exist, so the "nothing is configured" cases cannot silently pick up a real
needle file on the developer's box and pass for the wrong reason.

What is pinned:

* a planted value is found, and the output names the line WITHOUT echoing it;
* its truncation to three decimals is found (~100 m is still the site);
* the two-decimal form is NOT found (~1 km, and it occurs legitimately in
  catalogs and flip-geometry fixtures -- a scanner that cried wolf there would
  be turned off);
* a text needle matches case-insensitively;
* a clean tree exits 0;
* no needles exits 0 (a fork has no secret) but 2 under ``--require``.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "privacy_scan.py"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="the scanner asks git which files exist"
)

# Invented, and nothing like any real site. LAT/LON have six decimals so the
# three-decimal truncation rule has something to truncate.
FAKE_LAT = "12.345678"
FAKE_LON = "-98.765432"
FAKE_LABEL = "Nowhere Mesa"


def _load_module():
    """Import tools/privacy_scan.py by path: it is a script, not a package."""
    spec = importlib.util.spec_from_file_location("privacy_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway work tree with git's identity set locally, never globally."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "test@example.invalid")
    _git(work, "config", "user.name", "privacy scan test")
    return work


def _write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _env(tmp_path: Path, needles: str | None) -> dict:
    env = dict(os.environ)
    # Point the file at something that does not exist, so an unconfigured run
    # cannot fall through to the developer's real needles and pass for the
    # wrong reason.
    env["ASTRODECK_PRIVACY_NEEDLES_FILE"] = str(tmp_path / "no-such-needles.txt")
    if needles is None:
        env.pop("ASTRODECK_PRIVACY_NEEDLES", None)
    else:
        env["ASTRODECK_PRIVACY_NEEDLES"] = needles
    return env


def _run(repo: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(repo), env=env, capture_output=True, text=True,
    )


ALL_THREE = "\n".join((FAKE_LAT, FAKE_LON, FAKE_LABEL))


# --------------------------------------------------------------- the hits

def test_a_planted_value_is_found_and_never_echoed(repo, tmp_path):
    _write(repo, "notes.md", "site notes\nlatitude " + FAKE_LAT + "\ntail\n")
    _git(repo, "add", "notes.md")

    done = _run(repo, _env(tmp_path, ALL_THREE))

    assert done.returncode == 1, done.stdout + done.stderr
    assert "notes.md:2: contains a forbidden site value" in done.stdout
    blob = done.stdout + done.stderr
    assert FAKE_LAT not in blob, (
        "the scanner printed the value it exists to hide -- its output goes to "
        "CI logs, which are as public as the repository")


def test_a_backslash_escaped_value_is_found(repo, tmp_path):
    """THE CLASS THAT GOT THROUGH THE REWRITE, 2026-09-08.

    A plan document's own "grep the diff before you commit" checklist wrote the
    coordinates as a regex with the dots escaped. filter-repo's literal
    replacement did not match it, the first version of this scanner did not
    match it, and it survived three passes of a history rewrite whose entire
    purpose was to remove it. The file that leaked was the one telling
    everybody not to leak -- the same shape as the original 26-document leak.
    """
    escaped = FAKE_LAT.replace(".", "\\.")
    _write(repo, "plan.md",
           'Run: `git diff | grep -nE "' + escaped + '" || echo CLEAN`\n')
    _git(repo, "add", "plan.md")

    done = _run(repo, _env(tmp_path, ALL_THREE))

    assert done.returncode == 1, done.stdout + done.stderr
    assert "plan.md:1: contains a forbidden site value" in done.stdout
    assert FAKE_LAT not in done.stdout + done.stderr


def test_an_escaped_label_is_found_too(repo, tmp_path):
    """Escaping is not only a numeric trick; a shell-quoted label escapes too."""
    _write(repo, "notes.md", FAKE_LABEL.replace(" ", "\\ ") + "\n")
    _git(repo, "add", "notes.md")

    assert _run(repo, _env(tmp_path, ALL_THREE)).returncode == 1


def test_a_line_without_backslashes_is_searched_once():
    """The de-escaped pass is IN ADDITION to the raw line, never instead of it:
    a value with no escaping must still be found the ordinary way."""
    mod = _load_module()
    patterns = mod.patterns_for(["12.345678"])
    assert mod.scan_text("x = 12.345678\n", patterns) == [1]
    assert mod.scan_text("x = 12\\.345678\n", patterns) == [1]
    assert mod.scan_text("nothing here\n", patterns) == []


def test_the_three_decimal_truncation_is_found(repo, tmp_path):
    """A three-decimal prefix still places the rig to about 100 m."""
    _write(repo, "fixture.py", "LAT = 12.345\n")
    _git(repo, "add", "fixture.py")

    done = _run(repo, _env(tmp_path, ALL_THREE))

    assert done.returncode == 1, done.stdout + done.stderr
    assert "fixture.py:1: contains a forbidden site value" in done.stdout


def test_two_decimals_are_not_forbidden(repo, tmp_path):
    """~1 km, and the shape is everywhere: catalogs, flip-geometry fixtures.

    Forbidding it would make the scan cry wolf until somebody turned it off,
    which is a worse outcome than the kilometre.
    """
    _write(repo, "catalog.py", "LAT = 12.34\nLON = -98.76\n")
    _git(repo, "add", "catalog.py")

    done = _run(repo, _env(tmp_path, ALL_THREE))

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_text_needle_matches_case_insensitively(repo, tmp_path):
    _write(repo, "ui/copy.ts", 'export const SITE = "nOWHERE mESA";\n')
    _git(repo, "add", "ui/copy.ts")

    done = _run(repo, _env(tmp_path, ALL_THREE))

    assert done.returncode == 1, done.stdout + done.stderr
    assert "ui/copy.ts:1: contains a forbidden site value" in done.stdout


def test_a_clean_tree_exits_zero(repo, tmp_path):
    _write(repo, "clean.py", 'SITE = "My Observatory"\nLAT = 0.0\nLON = 0.0\n')
    _write(repo, "docs/readme.md", "Nothing to see.\n")
    _git(repo, "add", "-A")

    done = _run(repo, _env(tmp_path, ALL_THREE))

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == ""


def test_commas_separate_needles_too(repo, tmp_path):
    _write(repo, "notes.md", "the label is " + FAKE_LABEL + "\n")
    _git(repo, "add", "notes.md")

    done = _run(repo, _env(tmp_path, FAKE_LAT + "," + FAKE_LON + "," + FAKE_LABEL))

    assert done.returncode == 1, done.stdout + done.stderr


def test_a_binary_suffix_is_never_read(repo, tmp_path):
    """The suffix list is the whole filter, and a FITS file is not text."""
    _write(repo, "frame.fits", "SITELAT = " + FAKE_LAT + "\n")
    _git(repo, "add", "frame.fits")

    done = _run(repo, _env(tmp_path, ALL_THREE))

    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------- what --staged reads

def test_staged_grades_the_index_and_not_the_working_tree(repo, tmp_path):
    """Both directions, because getting this wrong fails silently either way.

    An unstaged leak must not block a clean commit, and an unstaged fix must
    not excuse a staged one.
    """
    _write(repo, "seed.md", "seed\n")
    _git(repo, "add", "seed.md")
    _git(repo, "commit", "-q", "-m", "seed")

    # Leak in the working tree only: the commit would not carry it.
    _write(repo, "draft.md", "latitude " + FAKE_LAT + "\n")
    done = _run(repo, _env(tmp_path, ALL_THREE), "--staged")
    assert done.returncode == 0, done.stdout + done.stderr

    # Now it is staged, and it must be refused.
    _git(repo, "add", "draft.md")
    done = _run(repo, _env(tmp_path, ALL_THREE), "--staged")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "draft.md:1: contains a forbidden site value" in done.stdout

    # Cleaning the working tree without staging the fix does NOT excuse it.
    _write(repo, "draft.md", "latitude 0.0\n")
    done = _run(repo, _env(tmp_path, ALL_THREE), "--staged")
    assert done.returncode == 1, done.stdout + done.stderr


# ------------------------------------------------ when nothing is configured

def test_no_needles_exits_zero_without_require(repo, tmp_path):
    """A fork has no secret and a fresh clone has no file. Neither is a fault."""
    _write(repo, "notes.md", "latitude " + FAKE_LAT + "\n")
    _git(repo, "add", "notes.md")

    done = _run(repo, _env(tmp_path, None))

    assert done.returncode == 0, done.stdout + done.stderr
    assert "no needles configured" in done.stdout


def test_no_needles_exits_two_with_require(repo, tmp_path):
    """Where the needles are supposed to be there, absent is a misconfiguration.

    This is the branch that stops "the gate quietly stopped grading" from
    happening a second time.
    """
    _write(repo, "notes.md", "latitude " + FAKE_LAT + "\n")
    _git(repo, "add", "notes.md")

    done = _run(repo, _env(tmp_path, None), "--require")

    assert done.returncode == 2, done.stdout + done.stderr
    assert "ASTRODECK_PRIVACY_NEEDLES" in done.stderr
    assert "ASTRODECK_PRIVACY_NEEDLES_FILE" in done.stderr


def test_an_empty_variable_falls_through_to_the_file(repo, tmp_path):
    """An unset secret expands to the empty string in a CI env block."""
    needle_file = tmp_path / "needles.txt"
    needle_file.write_text(ALL_THREE + "\n", encoding="utf-8")
    env = _env(tmp_path, "")
    env["ASTRODECK_PRIVACY_NEEDLES_FILE"] = str(needle_file)

    _write(repo, "notes.md", "latitude " + FAKE_LAT + "\n")
    _git(repo, "add", "notes.md")

    done = _run(repo, env, "--require")

    assert done.returncode == 1, done.stdout + done.stderr
    assert "notes.md:1: contains a forbidden site value" in done.stdout


# ------------------------------------------------------------- the internals

def test_patterns_expand_numbers_but_not_labels():
    mod = _load_module()
    patterns = mod.patterns_for([FAKE_LAT, FAKE_LABEL])

    assert (FAKE_LAT, False) in patterns
    assert ("12.345", False) in patterns, "the three-decimal truncation"
    assert ("12.34", False) not in patterns, "two decimals stay legal"
    assert (FAKE_LABEL.casefold(), True) in patterns, "labels fold case"


def test_a_short_number_has_no_truncation_to_add():
    mod = _load_module()
    assert mod.patterns_for(["12.34"]) == [("12.34", False)]
    assert mod.patterns_for(["12.345"]) == [("12.345", False)]


def test_needles_are_split_on_either_separator_and_deduplicated():
    mod = _load_module()
    assert mod._split_needles(" a , b \n c \n\n a ") == ["a", "b", "c"]


def test_the_exempt_set_holds_only_the_scanner_itself():
    """An exemption is how a guard stops guarding. There must be exactly one."""
    mod = _load_module()
    assert mod.EXEMPT == frozenset({"tools/privacy_scan.py"})
