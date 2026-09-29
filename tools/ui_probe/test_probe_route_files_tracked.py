"""Every route file a TRACKED tools/ui_probe/test_*.py names is tracked (#415).

test_probe_s4.py was committed (da875537, mosaic slice S4) reading
routes_s4_frame.json, and the route file was not: a commit made with explicit
pathspecs, the project's rule for parallel agents, left one path out. On a
fresh clone, a CI checkout or another worktree the committed test has no file
to grade, and three server spec-claims tests that read the same file fail
there with FileNotFoundError (#415, S5-SIM's comment). This guard names every
such pair, so the next commit that leaves a route file behind is red here
rather than on someone else's checkout.

Plain unittest and git, no Playwright, no server: run it with the rest of the
probe's tests (`cd tools/ui_probe && python -m unittest discover -p
"test_*.py"`, which run.ps1 does). Outside a git work tree (an exported
archive) there is nothing to ask, and the case says so as a skip.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent

# A route file is named by its file name in the test's source: in
# `HERE / "routes_s4_frame.json"`, and in a docstring that cites it, alike. A
# name in prose is held to the same rule on purpose: a test that cites a route
# file it does not read is rare, and a guard that argued with it would be a
# guard with an exception list.
ROUTE_NAME = re.compile(r"\broutes_[A-Za-z0-9_-]+\.json\b")

# The one exception is this file. Its cases write route files that exist only
# in a throwaway repository (`routes_x.json` and the like) and its docstrings
# quote them, so asked like any other test it would name itself the moment it
# was committed, four route files no tree will ever hold, and stay red for
# good (found by the S56-PROBE verifier, 2026-09-28, committing the probe
# folder as the operator is asked to, in a scratch repository). Its names are
# data, not files it reads: it is not asked, and
# test_the_guard_committed_does_not_name_itself holds that it is not.
SELF = Path(__file__).name


def named_route_files(source: str) -> set[str]:
    """The route file names `source` mentions."""
    return set(ROUTE_NAME.findall(source))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def untracked_route_files(repo: Path, probe_dir: str = "tools/ui_probe") -> list[tuple[str, str]]:
    """(test, route file) for every route file a tracked `probe_dir/test_*.py`
    names that git does not track, read from the working tree the test is in.
    A test git does not track is not asked about: it is not committed, so it
    cannot yet leave a committed test without its file."""
    listed = _git(repo, "ls-files", "--", probe_dir)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {listed.stderr.strip()}")
    tracked = {line.strip() for line in listed.stdout.splitlines() if line.strip()}
    tests = sorted(p for p in tracked
                   if re.fullmatch(re.escape(probe_dir) + r"/test_[^/]*\.py", p)
                   and p != f"{probe_dir}/{SELF}")
    missing: list[tuple[str, str]] = []
    for test in tests:
        source = (repo / test).read_text(encoding="utf-8", errors="replace")
        for name in sorted(named_route_files(source)):
            if f"{probe_dir}/{name}" not in tracked:
                missing.append((test, f"{probe_dir}/{name}"))
    return missing


def _work_tree(path: Path) -> Path | None:
    """The git work tree `path` is in, or None."""
    top = _git(path, "rev-parse", "--show-toplevel")
    return Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else None


class RealTreeTest(unittest.TestCase):

    def test_every_route_file_a_tracked_probe_test_names_is_tracked(self):
        """THE GUARD. Red on 2026-09-28, as it must be until the operator
        commits the route file with an explicit pathspec (#415):
            AssertionError: Lists differ: [('tools/ui_probe/test_probe_s4.py',
            'tools/ui_probe/routes_s4_frame.json')] != []
        Once test_probe_s5_s6.py is committed, routes_s5_s6.json must go with
        it, and this case names it if it does not."""
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH, so nothing says what is tracked")
        top = _work_tree(HERE)
        if top is None:
            self.skipTest("not a git work tree (an exported archive?): nothing to ask")
        probe_dir = HERE.relative_to(top).as_posix()
        self.assertEqual(untracked_route_files(top, probe_dir), [])


class GuardLogicTest(unittest.TestCase):
    """The guard's own logic, on a throwaway repository, so each case holds
    whatever the real tree's state is today."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH")
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for args in (("init", "-q"), ("config", "user.email", "probe@example.invalid"),
                     ("config", "user.name", "probe"), ("config", "core.autocrlf", "false")):
            self.assertEqual(_git(self.repo, *args).returncode, 0)
        (self.repo / "tools" / "ui_probe").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        (self.repo / rel).write_text(text, encoding="utf-8")

    def commit(self, *paths: str) -> None:
        self.assertEqual(_git(self.repo, "add", "--", *paths).returncode, 0)
        done = _git(self.repo, "commit", "-q", "-m", "fixture", "--", *paths)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_committed_test_whose_route_file_is_not_committed_is_named(self):
        """#415 itself, in miniature.

        Mutation "tracked check always true" (`untracked_route_files`: the
        `not in tracked` test -> `False`), observed red (scratchpad
        S56-PROBE-mut, 2026-09-28):
            AssertionError: Lists differ: [] != [('tools/ui_probe/test_x.py',
            'tools/ui_probe/routes_x.json')]"""
        self.write("tools/ui_probe/test_x.py", 'ROUTES = HERE / "routes_x.json"\n')
        self.write("tools/ui_probe/routes_x.json", "{}\n")
        self.commit("tools/ui_probe/test_x.py")
        self.assertEqual(untracked_route_files(self.repo),
                         [("tools/ui_probe/test_x.py", "tools/ui_probe/routes_x.json")])

    def test_both_committed_is_clean(self):
        """CONTROL: the same pair, both committed."""
        self.write("tools/ui_probe/test_x.py", 'ROUTES = HERE / "routes_x.json"\n')
        self.write("tools/ui_probe/routes_x.json", "{}\n")
        self.commit("tools/ui_probe/test_x.py", "tools/ui_probe/routes_x.json")
        self.assertEqual(untracked_route_files(self.repo), [])

    def test_an_uncommitted_test_is_not_asked_about(self):
        """CONTROL: a test nobody committed cannot leave a commit without its
        file; the guard asks only about tracked tests.

        Mutation "every test asked" (`untracked_route_files`: tests read from
        the directory listing, not `git ls-files`), observed red:
            AssertionError: Lists differ: [('tools/ui_probe/test_y.py',
            'tools/ui_probe/routes_y.json')] != []"""
        self.write("tools/ui_probe/test_y.py", 'ROUTES = HERE / "routes_y.json"\n')
        self.write("tools/ui_probe/other.txt", "tracked, so ls-files lists the folder\n")
        self.commit("tools/ui_probe/other.txt")
        self.assertEqual(untracked_route_files(self.repo), [])

    def test_the_guard_committed_does_not_name_itself(self):
        """This file, committed as the operator will commit it: the route files
        its cases write into throwaway repositories are not files it reads,
        and must not make the guard red for good (see SELF).

        Mutation "the guard asks about itself" (`untracked_route_files`: the
        `p != f"{probe_dir}/{SELF}"` test deleted), observed red (scratchpad
        S56-PROBE-verify-mut, 2026-09-28):
            AssertionError: Lists differ: [('tools/ui_probe/test_probe_route_files_t[480
            chars]on')] != [] ... First extra element 0:
            ('tools/ui_probe/test_probe_route_files_tracked.py', 'tools/ui_probe/routes_a.json')"""
        source = (HERE / SELF).read_text(encoding="utf-8")
        # CONTROL: the source does name route files nobody will commit, so
        # the case below is not clean for want of anything to find.
        self.assertTrue(named_route_files(source) - {"routes_s4_frame.json", "routes_s5_s6.json"})
        self.write(f"tools/ui_probe/{SELF}", source)
        self.commit(f"tools/ui_probe/{SELF}")
        self.assertEqual(untracked_route_files(self.repo), [])

    def test_the_scan_finds_the_names_the_real_tests_use(self):
        """The name scan against the real sources: test_probe_s4.py reads
        routes_s4_frame.json, and test_probe_isolation.py reads no route file.

        Mutation "the scan finds nothing" (ROUTE_NAME -> a pattern that never
        matches), observed red:
            AssertionError: 'routes_s4_frame.json' not found in set()"""
        s4 = (HERE / "test_probe_s4.py").read_text(encoding="utf-8")
        self.assertIn("routes_s4_frame.json", named_route_files(s4))
        iso = (HERE / "test_probe_isolation.py").read_text(encoding="utf-8")
        self.assertEqual(named_route_files(iso), set())
        # A name in code and a name ending a sentence alike.
        self.assertEqual(named_route_files('HERE / "routes_a.json", as routes_b.json says.'),
                         {"routes_a.json", "routes_b.json"})


if __name__ == "__main__":
    unittest.main()
