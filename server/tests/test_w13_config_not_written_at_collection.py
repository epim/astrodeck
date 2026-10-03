# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#675 part 2 (backlog WP-H2, owner-approved plan 2026-09-30): two xdist
workers raced to create the default ``server/config/astrodeck.json`` at
collection time. Traced to ground (see conftest.py's
``_arm_real_config_guard_before_collection``): test_w1_cooling_restore_
daylight.py's `NIGHT_TS = _night_midpoint()` is a module-level statement; it
calls astrodeck.sequence.schedule.observing_night, which calls
``config_store.cfg()``. On a fresh worktree (``server/config/`` is
gitignored -- every WP worktree in this backlog plan starts without a file)
that reaches ``ConfigStore._load``'s ``FileNotFoundError`` branch, which
falls through to ``_save()``, writing the default config to the REAL path.
Several xdist workers racing to do that at once is the observed intermittent
failure: ``FileNotFoundError``, then ``PermissionError``, on
``astrodeck.json`` or its ``.tmp`` rename.

test_w1_cooling_restore_daylight.py is outside this WP's owned-files list, so
its own statement is not editable here (filed separately -- see this WP's
return). What IS testable, and owned by this WP, is the two-part guard conftest.py now
installs before any test module is collected:

1. ``astrodeck.config`` itself was always lazy at bare import (``config_store
   = ConfigStore()`` only records a path; ``ConfigStore.__init__`` never
   loads) -- ``test_importing_astrodeck_config_creates_nothing`` pins that,
   so a FUTURE change that adds an eager ``.cfg()`` call at config.py's own
   import is caught immediately.
2. The actual fix: conftest.py's ``_arm_real_config_guard_before_collection``
   points the shared ``config_store`` at a throwaway directory as soon as
   conftest.py is imported -- before any test module's import can reach it --
   so a module-level statement shaped exactly like the traced defect lands on
   a private file, never the real one, regardless of how many xdist workers
   do it at once. ``test_module_level_cfg_call_is_redirected_before_
   collection`` reproduces the traced shape directly (a module-level
   ``config_store.cfg()``) against a throwaway worktree-shaped tree with a
   real copy of conftest.py, and asserts the stand-in "real" location is
   never touched.

Both run in a subprocess (a fresh interpreter under its own ASTRODECK_CONFIG_
DIR), so a passing run says nothing about whatever real config this machine
already has, and a failing one cannot leave anything behind that matters."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import astrodeck

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"


def test_importing_astrodeck_config_creates_nothing(tmp_path):
    """Regression pin (not the issue's actual mechanism -- see the module
    docstring): a bare ``import astrodeck.config``, in a fresh interpreter,
    with ``ASTRODECK_CONFIG_DIR`` pointing at an empty directory this test
    owns, must create nothing in it. ``ConfigStore.__init__`` only records a
    path; it never loads, so this has always held -- what the issue's own
    title suggested (an eager ``config_store.cfg()`` at config.py's own
    import) was never the actual cause, but pinning it means a FUTURE change
    shaped like that is caught immediately rather than becoming a fourth
    occurrence.

    Named mutant: an eager ``config_store.cfg()`` statement added right
    after ``config_store = ConfigStore()`` in config.py (the "eager create
    restored"). RED, observed:

        AssertionError: importing astrodeck.config created something in a
        directory that had nothing: astrodeck.json
    """
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    probe = tmp_path / "probe_import_only.py"
    probe.write_text("import astrodeck.config\n", encoding="utf-8")
    env = dict(os.environ, ASTRODECK_CONFIG_DIR=str(cfg_dir))
    subprocess.run([sys.executable, str(probe)], env=env, check=True,
                   capture_output=True, text=True, timeout=60)
    left = sorted(p.name for p in cfg_dir.iterdir())
    assert left == [], (
        "importing astrodeck.config created something in a directory that "
        "had nothing: " + ", ".join(left))


def test_module_level_cfg_call_is_redirected_before_collection(tmp_path):
    """The traced shape (#675 part 2) reproduced directly: a module-level
    statement in a test file reaches the shared ``config_store`` at its OWN
    import, in a throwaway tree laid out like a real worktree (its own
    ``conftest.py``, a stand-in "real" ``server/config/`` that starts
    missing, same as every fresh WP worktree in this backlog plan). Asserts
    the stand-in real directory is never created and collection succeeds.

    ``config_store.cfg()`` alone is the minimal reproduction of the traced
    defect's mechanism (test_w1_cooling_restore_daylight.py's `NIGHT_TS =
    _night_midpoint()` reaches the identical ``ConfigStore._load -> _save``
    fallthrough through astrodeck.sequence.schedule.observing_night) -- this
    WP owns conftest.py, not that test file, so the probe stands in for it
    rather than importing or copying it.

    Named mutant: inside ``_arm_real_config_guard_before_collection``, the
    three lines that move ``config_store._path``/``CONFIG_DIR`` commented out
    (the early redirect skipped -- "the eager create restored" for the
    collection half; the directory is still made and returned, so the rest
    of conftest.py's session fixture does not also crash on a missing
    directory). RED, observed -- not from THIS test's own assertion, but
    from ``_never_touch_the_real_config``'s own "fails loudly" check one
    layer up, firing first, for every test in THIS process's session (the
    outer conftest.py is the same file the mutant is in, so its own fixture
    sees the un-redirected store before this test's subprocess ever runs):

        tests/conftest.py:211: AssertionError
        assert config_mod.config_store._path != real
        AssertionError: assert WindowsPath('.../server/config/astrodeck.json')
        != WindowsPath('.../server/config/astrodeck.json')

    Two independent guards catching the same regression, the outer one
    louder than this test needs to be: the session fixture's own assertion
    (``_never_touch_the_real_config``, same file) fires before this test's
    body even starts, for the whole file, not just this case. The inner
    subprocess assertion below (``left == []``) is the backstop if that
    outer one is ALSO removed -- not exercised by this particular mutant run,
    since the outer guard got there first.
    """
    real = tmp_path / "real-config"      # NOT created -- see the docstring
    repo = tmp_path / "repo"
    tests_dir = repo / "server" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_bytes(_CONFTEST.read_bytes())
    (tests_dir / "test_w13_module_level_probe.py").write_text(
        "from astrodeck.config import config_store\n"
        "AT_IMPORT = config_store.cfg().version\n"
        "\n"
        "def test_trivial():\n"
        "    assert AT_IMPORT == 1\n",
        encoding="utf-8")
    ini = repo / "server" / "pytest.ini"
    ini.write_text("[pytest]\naddopts =\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["ASTRODECK_CONFIG_DIR"] = str(real)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(astrodeck.__file__).resolve().parents[1]),
         *filter(None, [env.get("PYTHONPATH")])])
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         "-p", "no:randomly", "-n", "0", "-q", "-c", str(ini),
         "--rootdir", str(repo / "server"),
         str(tests_dir / "test_w13_module_level_probe.py")],
        cwd=repo / "server", env=env, capture_output=True, text=True,
        timeout=120)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    left = sorted(p.name for p in real.rglob("*")) if real.exists() else []
    assert left == [], (
        "the module-level cfg() call reached server/config/: " + str(left))
