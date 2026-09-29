"""Every probe route file a TRACKED test names is tracked (#415).

test_probe_s4.py was committed (da875537, mosaic slice S4) reading
routes_s4_frame.json, and the route file was not: a commit made with explicit
pathspecs, the project's rule for parallel agents, left one path out. On a
fresh clone, a CI checkout or another worktree the committed test has no file
to grade, and three server spec-claims tests that read the same file fail
there with FileNotFoundError (#415, S5-SIM's comment). This guard names every
such pair, so the next commit that leaves a route file behind is red here
rather than on someone else's checkout.

It was first written beside the probe, in tools/ui_probe (S5), where only the
probe's own unittest run graded it, and that run is manual: run.ps1 starts
it before a walk and nothing else does, so a commit could leave a route file
behind and every automatic run stay green (#479). S7 orchestrator ruling 8
moved it here, so that every server suite run, CI's included, grades it. It needs git
and nothing else: no Playwright, no server, no UI build. The probe's
Playwright walks stay where they were, manual, and skip where Playwright is
not importable.

It asks about the tracked test_*.py files of two directories, because route
files have readers in both: the probe's own tests (test_probe_s4.py reads
routes_s4_frame.json, test_probe_s5_s6.py reads routes_s5_s6.json) and this
suite's (test_mosaic_spec_claims.py reads routes_s4_frame.json to hold the
spec to the probe, and #415's FileNotFoundErrors were its). A route file lives
in tools/ui_probe whichever test names it.

The repository is found by asking git (`rev-parse --show-toplevel`), not by
counting parent directories, because what the case asks is git's answer about
that work tree. Where git is missing, or this file is not in a work tree (an
exported archive, a copy), there is nothing to ask, and the case says so as a
skip.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git is not on PATH, so nothing says what is tracked")

#: Where the route files live, whichever test names one.
PROBE_DIR = "tools/ui_probe"

#: The directories whose tracked test_*.py files are asked about: the probe's,
#: and this suite's (see the module docstring for the reader each one has).
TEST_DIRS = (PROBE_DIR, "server/tests")

# A route file is named by its file name in the test's source: in
# `HERE / "routes_s4_frame.json"`, and in a docstring that cites it, alike. A
# name in prose is held to the same rule on purpose: a test that cites a route
# file it does not read is rare, and a guard that argued with it would be a
# guard with an exception list.
ROUTE_NAME = re.compile(r"\broutes_[A-Za-z0-9_-]+\.json\b")

_TEST_PATH = re.compile(
    "(?:" + "|".join(re.escape(d) for d in TEST_DIRS) + r")/test_[^/]*\.py")

# The one exception is this file. Its cases write route files that exist only
# in a throwaway repository (`routes_x.json` and the like) and its docstrings
# quote them, so asked like any other test it would name itself the moment it
# was committed, with route files no tree will ever hold, and stay red for
# good (found by the S56-PROBE verifier, 2026-09-28, committing the probe
# folder as the operator is asked to, in a scratch repository). Its names are
# data, not files it reads: it is not asked, and
# test_the_guard_committed_does_not_name_itself holds that it is not. The
# path is spelled out because the guard answers about any repository, the
# throwaway ones included; the real-tree case holds that it is this file.
SELF = f"server/tests/{Path(__file__).name}"

#: The checkout this file sits in, for READING the real probe sources, which
#: needs no git. The git question itself goes to `rev-parse`.
REPO = Path(__file__).resolve().parents[2]


def named_route_files(source: str) -> set[str]:
    """The route file names `source` mentions."""
    return set(ROUTE_NAME.findall(source))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def route_file_uses(repo: Path) -> tuple[list[tuple[str, str]], set[str]]:
    """(test, route file) for every route file a tracked test in TEST_DIRS
    names, read from the working tree the test is in, and the set of paths
    git tracks there. A test git does not track is not asked about: it is not
    committed, so it cannot yet leave a committed test without its file."""
    listed = _git(repo, "ls-files", "-z", "--", *TEST_DIRS)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {listed.stderr.strip()}")
    tracked = {p for p in listed.stdout.split("\0") if p}
    tests = sorted(p for p in tracked if _TEST_PATH.fullmatch(p) and p != SELF)
    uses: list[tuple[str, str]] = []
    for test in tests:
        path = repo / test
        # A test deleted from the working tree is still in the index until
        # the deletion is committed. It has no source to read, and the commit
        # that carries its deletion cannot leave it without a route file.
        # This guard's own first home, tools/ui_probe's copy, was exactly that
        # the day it moved here. Read, it crashed the real-tree case (mutation
        # "a deleted test read", in the scratch repository with that copy
        # committed and then deleted; S7-PROBEGUARD-mut, 2026-09-28):
        #   FileNotFoundError: [Errno 2] No such file or directory:
        #   '...\\tree\\tools\\ui_probe\\test_probe_route_files_tracked.py'
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for name in sorted(named_route_files(source)):
            uses.append((test, f"{PROBE_DIR}/{name}"))
    return uses, tracked


def untracked_route_files(repo: Path) -> list[tuple[str, str]]:
    """(test, route file) for every route file a tracked test names that git
    does not track."""
    uses, tracked = route_file_uses(repo)
    return [(test, route) for test, route in uses if route not in tracked]


def _work_tree(path: Path) -> tuple[Path | None, str]:
    """The git work tree `path` is in, or None and git's reason."""
    top = _git(path, "rev-parse", "--show-toplevel")
    if top.returncode == 0 and top.stdout.strip():
        return Path(top.stdout.strip()), ""
    return None, top.stderr.strip() or "git answered no work tree"


# ------------------------------------------------------------ the real tree

def test_every_route_file_a_tracked_test_names_is_tracked():
    r"""THE GUARD. Red on 2026-09-28 in its first home, as it had to be until
    the operator committed the route file with an explicit pathspec (#415):
        AssertionError: Lists differ: [('tools/ui_probe/test_probe_s4.py',
        'tools/ui_probe/routes_s4_frame.json')] != []
    and red the same way here, in a scratch repository holding this tree's
    probe folder, its server tests and this file, all committed but
    routes_s4_frame.json (scratchpad S7-PROBEGUARD-mut, 2026-09-28), which
    names both readers:
        AssertionError: a tracked test names a route file git does not
        track; commit the route file with an explicit pathspec (#415):
        [('server/tests/test_mosaic_spec_claims.py',
        'tools/ui_probe/routes_s4_frame.json'),
        ('tools/ui_probe/test_probe_s4.py',
        'tools/ui_probe/routes_s4_frame.json')]

    In the same scratch repository with everything committed, where this
    case passes, the two CONTROLS were each seen red:
    Mutation "the scan finds nothing" (ROUTE_NAME -> a pattern that never
    matches), so a clean answer cannot come from asking nothing:
        AssertionError: not named by any tracked test:
        ['tools/ui_probe/routes_s4_frame.json',
        'tools/ui_probe/routes_s5_s6.json']
    Mutation "SELF names another path" (SELF -> tools/ui_probe/ and this
    file's name, where the guard first lived):
        AssertionError: SELF (tools/ui_probe/test_probe_route_files_tracked.py)
        is not this file (...\tree\server\tests\test_probe_route_files_tracked.py)
        in the work tree at ...\tree: update SELF with the move
    (the scratch repository's path shortened to `...`).
    """
    top, why = _work_tree(Path(__file__).resolve().parent)
    if top is None:
        pytest.skip(f"not a git work tree (an exported archive or a copy?): "
                    f"nothing to ask. git said: {why}")
    # CONTROL: SELF is this file, so the one exception excuses this file and
    # no other; a move that left SELF behind would excuse nothing and turn
    # red below the day the moved file was committed, or excuse a stranger.
    assert (top / SELF).resolve() == Path(__file__).resolve(), (
        f"SELF ({SELF}) is not this file ({Path(__file__).resolve()}) in the "
        f"work tree at {top}: update SELF with the move")
    uses, tracked = route_file_uses(top)
    real = {f"{PROBE_DIR}/routes_s4_frame.json", f"{PROBE_DIR}/routes_s5_s6.json"}
    # CONTROL: the tree's two probe route files are named by tracked tests,
    # so the clean answer below is not clean for want of anything to ask.
    assert real <= {route for _, route in uses}, (
        f"not named by any tracked test: {sorted(real - {r for _, r in uses})}")
    missing = untracked_route_files(top)
    assert missing == [], (
        f"a tracked test names a route file git does not track; commit the "
        f"route file with an explicit pathspec (#415): {missing}")
    # And so, both tracked (acceptance's control, spelled out).
    assert real <= tracked


# ------------------------------------------------------- the guard's logic
# On a throwaway repository, so each case holds whatever the real tree's
# state is today.

@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway work tree, git's identity set locally, never globally."""
    r = tmp_path / "repo"
    r.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "probe@example.invalid"),
                 ("config", "user.name", "probe"), ("config", "core.autocrlf", "false")):
        done = _git(r, *args)
        assert done.returncode == 0, done.stderr
    return r


def _write(repo: Path, rel: str, text: str) -> None:
    # The folders come from the path written, never from TEST_DIRS: a fixture
    # built from TEST_DIRS made "server tests not asked" red with a
    # FileNotFoundError from this helper, not from the guard it mutates.
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, *paths: str) -> None:
    done = _git(repo, "add", "--", *paths)
    assert done.returncode == 0, done.stderr
    done = _git(repo, "commit", "-q", "-m", "fixture", "--", *paths)
    assert done.returncode == 0, done.stderr


def test_the_work_tree_is_found_from_inside_it_and_not_from_outside(
        repo, tmp_path, monkeypatch):
    """The real-tree case asks `_work_tree` first and SKIPS when it answers
    None, so a `_work_tree` that never found a tree would turn THE GUARD into
    a skip in every run, CI's included, with nothing red. This case holds both
    answers on throwaway folders, so that skip is only ever git's.

    Mutation "work tree never found" (`_work_tree`: the `returncode == 0`
    test -> `False`), observed red (scratchpad S7-PROBEGUARD-verify-mut,
    2026-09-28):
        AssertionError: git answered no work tree
        assert None is not None
    Before this case the same mutant left the file green, 7 passed and the
    guard skipped: "not a git work tree (an exported archive or a copy?):
    nothing to ask. git said: git answered no work tree".
    Mutation "any answer is a work tree" (the same test -> `True`), observed
    red on the outside arm:
        AssertionError: assert WindowsPath('.') is None
    """
    inside = repo / "server" / "tests"
    inside.mkdir(parents=True)
    top, why = _work_tree(inside)
    assert top is not None, why
    assert top.resolve() == repo.resolve()
    # Outside: git is stopped at tmp_path, so a checkout that happens to hold
    # the temporary folder cannot answer for it. The reason is git's own
    # words, which are localised, so only its presence is held.
    loose = tmp_path / "loose"
    loose.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    top, why = _work_tree(loose)
    assert top is None
    assert why


def test_a_committed_test_whose_route_file_is_not_committed_is_named(repo):
    """#415 itself, in miniature.

    Mutation "tracked check always true" (`untracked_route_files`: the
    `route not in tracked` test -> `False`), observed red (scratchpad
    S7-PROBEGUARD-mut, 2026-09-28):
        AssertionError: assert [] == [('tools/ui_p...utes_x.json')]
          Right contains one more item: ('tools/ui_probe/test_x.py',
          'tools/ui_probe/routes_x.json')
    and the server-test case below red the same way.
    """
    _write(repo, "tools/ui_probe/test_x.py", 'ROUTES = HERE / "routes_x.json"\n')
    _write(repo, "tools/ui_probe/routes_x.json", "{}\n")
    _commit(repo, "tools/ui_probe/test_x.py")
    assert untracked_route_files(repo) == [
        ("tools/ui_probe/test_x.py", "tools/ui_probe/routes_x.json")]


def test_a_committed_server_test_whose_route_file_is_not_committed_is_named(repo):
    """The server suite's readers are asked too: #415's FileNotFoundErrors
    were test_mosaic_spec_claims.py's, reading the probe's route file.

    Mutation "server tests not asked" (TEST_DIRS -> the probe's directory
    alone), observed red (scratchpad S7-PROBEGUARD-mut, 2026-09-28):
        AssertionError: assert [] == [('server/tes...utes_s.json')]
          Right contains one more item: ('server/tests/test_s.py',
          'tools/ui_probe/routes_s.json')
    """
    _write(repo, "server/tests/test_s.py", '_PROBE / "routes_s.json"\n')
    _write(repo, "tools/ui_probe/routes_s.json", "{}\n")
    _commit(repo, "server/tests/test_s.py")
    assert untracked_route_files(repo) == [
        ("server/tests/test_s.py", "tools/ui_probe/routes_s.json")]


def test_both_committed_is_clean(repo):
    """CONTROL: the same pairs, both halves committed, from either directory."""
    _write(repo, "tools/ui_probe/test_x.py", 'ROUTES = HERE / "routes_x.json"\n')
    _write(repo, "server/tests/test_s.py", '_PROBE / "routes_x.json"\n')
    _write(repo, "tools/ui_probe/routes_x.json", "{}\n")
    _commit(repo, "tools/ui_probe/test_x.py", "server/tests/test_s.py",
            "tools/ui_probe/routes_x.json")
    assert untracked_route_files(repo) == []


def test_an_uncommitted_test_is_not_asked_about(repo):
    """CONTROL: a test nobody committed cannot leave a commit without its
    file; the guard asks only about tracked tests.

    Mutation "every test asked" (`route_file_uses`: tests read from the
    directory listing, not `git ls-files`), observed red (scratchpad
    S7-PROBEGUARD-mut, 2026-09-28):
        AssertionError: assert [('tools/ui_p...utes_y.json')] == []
          Left contains one more item: ('tools/ui_probe/test_y.py',
          'tools/ui_probe/routes_y.json')
    """
    _write(repo, "tools/ui_probe/test_y.py", 'ROUTES = HERE / "routes_y.json"\n')
    _write(repo, "tools/ui_probe/other.txt", "tracked, so ls-files lists the folder\n")
    _commit(repo, "tools/ui_probe/other.txt")
    assert untracked_route_files(repo) == []


def test_a_tracked_test_deleted_from_the_working_tree_is_not_read(repo):
    r"""A committed test whose deletion is not yet committed: ls-files still
    names it, and there is nothing on disk to read.

    Mutation "a deleted test read" (`route_file_uses`: the `is_file` test
    deleted), observed red (scratchpad S7-PROBEGUARD-mut, 2026-09-28):
        FileNotFoundError: [Errno 2] No such file or directory:
        '...\\repo\\tools\\ui_probe\\test_gone.py'
    (pytest's temporary path shortened to `...`).
    """
    _write(repo, "tools/ui_probe/test_gone.py", 'ROUTES = HERE / "routes_gone.json"\n')
    _commit(repo, "tools/ui_probe/test_gone.py")
    (repo / "tools/ui_probe/test_gone.py").unlink()
    # CONTROL: git still lists it, so the case is not passing on a folder
    # git has forgotten.
    assert "tools/ui_probe/test_gone.py" in _git(repo, "ls-files").stdout
    assert untracked_route_files(repo) == []


def test_the_guard_committed_does_not_name_itself(repo):
    """This file, committed where the operator will commit it: the route files
    its cases write into throwaway repositories are not files it reads, and
    must not make the guard red for good (see SELF).

    Mutation "the guard asks about itself" (`route_file_uses`: the
    `p != SELF` test deleted), observed red (scratchpad S7-PROBEGUARD-mut,
    2026-09-28):
        AssertionError: assert [('server/tes...6.json'), ...] == []
          Left contains 8 more items, first extra item:
          ('server/tests/test_probe_route_files_tracked.py',
          'tools/ui_probe/routes_a.json')
    and, in the scratch repository with this file committed where it lives,
    the real-tree case red too, for the six names only this file's cases use:
        AssertionError: a tracked test names a route file git does not
        track; commit the route file with an explicit pathspec (#415):
        [('server/tests/test_probe_route_files_tracked.py',
        'tools/ui_probe/routes_a.json'), ...]
    """
    source = Path(__file__).read_text(encoding="utf-8")
    # CONTROL: the source does name route files nobody will commit, so the
    # case below is not clean for want of anything to find.
    assert named_route_files(source) - {"routes_s4_frame.json", "routes_s5_s6.json"}
    _write(repo, SELF, source)
    _commit(repo, SELF)
    assert untracked_route_files(repo) == []


def test_the_scan_finds_the_names_the_real_tests_use():
    """The name scan against the real sources: test_probe_s4.py reads
    routes_s4_frame.json, and test_probe_isolation.py reads no route file.

    Mutation "the scan finds nothing" (ROUTE_NAME -> a pattern that never
    matches), observed red (scratchpad S7-PROBEGUARD-mut, 2026-09-28):
        assert 'routes_s4_frame.json' in set()
         +  where set() = named_route_files(<test_probe_s4.py's source>)
    (the source, which pytest printed in full, abbreviated here).
    """
    probe = REPO / PROBE_DIR
    s4 = (probe / "test_probe_s4.py").read_text(encoding="utf-8")
    assert "routes_s4_frame.json" in named_route_files(s4)
    iso = (probe / "test_probe_isolation.py").read_text(encoding="utf-8")
    assert named_route_files(iso) == set()
    # A name in code and a name ending a sentence alike.
    assert named_route_files('HERE / "routes_a.json", as routes_b.json says.') == {
        "routes_a.json", "routes_b.json"}
