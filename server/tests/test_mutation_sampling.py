"""Break the guard, and prove its test notices.

THE PROMISE: the suite protects the expensive behaviours.

THE DEFECT CLASS (broken promises, class F — verification that verifies
nothing). This is the class that decides whether any of the others can be
believed, because every other detector in this tree is itself a test. Four
shapes of no-op test have already been found here, two of them ours:

  * an assertion scanning ``group.slice(0, chip.length + 800)`` where the index
    was ``-1``, so it covered one and a half of three chips and passed by
    arithmetic;
  * tests appended BELOW a file's summary block: they ran, their failures were
    discarded, and the count never moved;
  * anonymous-access tests written against a client with no auth configured,
    where every route returns 200 and the assertion passes for the wrong reason;
  * a test whose stop condition was satisfied by the simulator's unrelated
    failure path, so it graded nothing it claimed to grade.

None of those is visible by reading the test. All four are visible the moment
you break the thing the test is for and the test stays green.

HOW THIS RUNS. Each mutant is applied to a COPY of the tree — ``astrodeck``,
``tests`` and the pytest ini, ~0.2 s to make — and its covering tests are run in
a child pytest from inside that copy. The working tree is never modified: the
suite runs 12-way parallel by default, and an in-place edit would corrupt every
other worker's run and leave a broken tree behind if the session died.

WHY IT IS IN CI RATHER THAN A SCRIPT. A script nobody runs is a document, and
this project has already learned what documents are worth. The cost is bounded
by construction and the bounds are asserted below: a fixed SAMPLE (eight), each
with NARROW covering tests rather than a whole suite, ``-x`` on the child so a
correctly-caught mutant stops at its first failure, and a hard per-child
timeout. It is parametrized so xdist spreads the children instead of making one
worker the tail. Set ``ASTRODECK_SKIP_MUTATION=1`` to skip it in the inner loop.

SAMPLED, NOT EXHAUSTIVE. Full mutation testing over 3900 tests is not worth the
wall clock. The sample is chosen by what a failure COSTS: the sun cone, path
containment, site privacy, the safety gate, the dawn park, the warm ramp, a
focuser move, and the provider offer — every one of them a guard that, when it
silently stops working, is discovered by an operator at night or not at all.

READING A FAILURE. A mutant that stays GREEN does not always mean a vacuous
test: it can also mean a second guard caught the same case (defence in depth).
Check that first. But the burden is on the reader to show which, because the
other explanation is that nothing is watching an expensive guard at all.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1]
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")

#: Per-child wall clock. Generous enough for an interpreter start plus a small
#: test file on a loaded box, small enough that a hung child cannot become the
#: run. A child that times out is reported as an ERROR, never as "caught".
CHILD_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class Mutant:
    id: str
    #: Path relative to ``server/``.
    path: str
    #: An EXACT substring of that file, present exactly once (asserted).
    old: str
    #: What it becomes. Must stay syntactically valid.
    new: str
    #: What the guard protects, and what it costs when it silently stops
    #: working. This is the field that decides whether the mutant belongs here.
    costs: str
    #: The covering tests, as pytest node ids. Narrow on purpose.
    tests: tuple[str, ...] = field(default_factory=tuple)


_MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        id="solar-cone",
        path="astrodeck/hub.py",
        old="        if sep < cone:",
        new="        if False:",
        costs="a daytime slew into the Sun through a telescope: sensor, "
              "shutter and, on a visual eyepiece, an eye. There is no undo and "
              "no second chance to notice",
        tests=("tests/test_sun_guard.py",),
    ),
    Mutant(
        id="path-component",
        path="astrodeck/persist.py",
        old='    return (name in (".", "..")\n'
            '            or ":" in name                      # drive prefix AND NTFS ADS\n'
            '            or name != name.rstrip(" .")        # Windows strips these silently\n'
            '            or name.split(".")[0].upper() in _WIN_RESERVED)',
        new="    return False",
        costs="the containment shared by every client-supplied id and path. "
              "With it gone a FITS FILTER card named ../../../../pwned became "
              "an arbitrary file write, and the SPA catch-all served "
              "Windows/win.ini with no credential at all",
        tests=("tests/test_path_traversal.py::test_safe_id_path_refuses_the_same_corpus",),
    ),
    Mutant(
        id="site-derived-keys",
        path="astrodeck/api/redact.py",
        old='_MOUNT_DERIVED_KEYS = ("alt", "az")',
        new="_MOUNT_DERIVED_KEYS = ()",
        costs="the observatory's physical location. Alt/az plus the RA/Dec a "
              "viewer already holds pins the observer to a circle on the Earth; "
              "a second sample collapses it to a point. The audit recovered "
              "this rig to 2.9 km from three authorized requests",
        tests=("tests/test_rbac_enforcement.py::test_a_viewer_gets_no_mount_altaz",
               "tests/test_rbac_enforcement.py::"
               "test_the_ws_push_strips_altaz_without_mutating_the_shared_event"),
    ),
    Mutant(
        id="focuser-arrival",
        path="astrodeck/devices/alpaca.py",
        old="ARRIVAL_TOLERANCE_STEPS = 2",
        new="ARRIVAL_TOLERANCE_STEPS = 100000",
        costs="the difference between a move that happened and a move that did "
              "not. Without it a refused move reports success, which is how an "
              "EAF against a mechanical stop swallowed thirteen autofocus "
              "steps in silence",
        tests=("tests/test_focuser_move_contract.py::"
               "test_a_move_that_did_not_happen_is_an_error",),
    ),
    Mutant(
        id="dawn-park",
        path="astrodeck/dawn_park.py",
        old="        if alt < threshold:",
        new="        if True:",
        costs="the only thing that parks an idle mount at sunrise. Every other "
              "park path hangs off a RUN; a night that ends without one tracked "
              "straight through dawn on 2026-08-05 with the optics pointed up",
        tests=("tests/test_dawn_park.py",),
    ),
    Mutant(
        id="unsafe-confirm",
        path="astrodeck/sequence/engine.py",
        old="                if await self._confirm_unsafe(cfg):",
        new="                if False:",
        costs="the response to a safety monitor reporting rain or cloud on "
              "open gear. Neutered, the run continues and the night's escalation "
              "policy — abort, park, warm — never fires",
        tests=("tests/test_engine_safety.py",),
    ),
    Mutant(
        id="warm-ramp",
        path="astrodeck/cooling.py",
        old="    return min(float(ambient_c), float(setpoint_c) + rate_c_per_min * (step_s / 60.0))",
        new="    return float(ambient_c)",
        costs="the ramp Settings promises in so many words. Cutting a cooled "
              "sensor straight to ambient is the thermal shock the copy says it "
              "avoids, and it happens on the unattended path",
        tests=("tests/test_cooler_warm_ramp.py",),
    ),
    Mutant(
        id="guide-offer-predicate",
        path="astrodeck/providers.py",
        old='    if override == "astrodeck" and _guide_native_blocker(hub) is None:',
        new='    if override == "astrodeck" and native_ok:',
        costs="the agreement between the guide dropdown and the guide "
              "resolver. Each time these drifted the screen argued with itself "
              "and a user's pick was discarded with nothing saying so",
        tests=("tests/test_offer_matches_resolver.py",),
    ),
)


# ------------------------------------------------------------------- the runner

def _mutant_tree(tmp_path: Path) -> Path:
    """A throwaway copy of the tree the child will run in.

    Copies the package, the tests and the pytest ini (which carries
    ``asyncio_mode = "auto"``; without it every async test would ERROR and every
    mutant would look 'caught'). The child runs with this as its cwd, so
    ``import astrodeck`` binds the copy — no PYTHONPATH games, and the real tree
    is never touched."""
    root = tmp_path / "mutant"
    root.mkdir()
    shutil.copytree(_SERVER / "astrodeck", root / "astrodeck", ignore=_IGNORE)
    shutil.copytree(_SERVER / "tests", root / "tests", ignore=_IGNORE)
    shutil.copy(_SERVER / "pyproject.toml", root / "pyproject.toml")
    return root


def _apply(root: Path, m: Mutant) -> None:
    target = root / m.path
    src = target.read_text(encoding="utf-8")
    assert src.count(m.old) == 1, m.id
    target.write_text(src.replace(m.old, m.new), encoding="utf-8")


def _run_child(root: Path, tests: tuple[str, ...]) -> subprocess.CompletedProcess:
    """The covering tests, run serially inside ``root``.

    ``-o addopts=""`` drops the project's ``-n 12`` (a child spawning twelve
    more processes per mutant is how this becomes a forty-minute job), and
    ``-x`` stops a correctly-caught mutant at its first failure."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-x", "-q",
         "-o", "addopts=", "-p", "no:cacheprovider"],
        cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=CHILD_TIMEOUT_S)


skip_mutation = pytest.mark.skipif(
    os.environ.get("ASTRODECK_SKIP_MUTATION") == "1",
    reason="ASTRODECK_SKIP_MUTATION=1 (inner loop); CI always runs these")


# ------------------------------------------------- guards on the sample itself

def test_every_mutant_still_matches_the_source():
    """A mutation whose target text has moved silently stops mutating anything,
    and then the child passes for the most boring reason there is. Cheap, runs
    on every suite, and fails the moment a guard is rewritten."""
    for m in _MUTANTS:
        src = (_SERVER / m.path).read_text(encoding="utf-8")
        assert src.count(m.old) == 1, (
            f"[{m.id}] its target text appears {src.count(m.old)} times in "
            f"{m.path} — the guard was rewritten and this mutant now proves "
            f"nothing. Re-point it at the line that enforces the rule.")
        assert m.new not in src, (
            f"[{m.id}] the MUTATED form is already in the source — the guard is "
            f"broken in the tree right now")


def test_every_mutant_names_the_cost_and_a_covering_test():
    for m in _MUTANTS:
        assert m.costs.strip() and len(m.costs) > 40, (
            f"[{m.id}] a mutant with no stated cost is a mutant nobody can "
            f"prioritise — say what a user loses when this guard stops working")
        assert m.tests, f"[{m.id}] names no covering test"
        for node in m.tests:
            f = _SERVER / node.split("::")[0]
            assert f.exists(), f"[{m.id}] covering test file missing: {f}"


def test_the_sample_stays_a_sample():
    """The bound that keeps this in CI. Eight children at a few seconds each is
    a tail nobody notices; eighty is a job people start skipping, and a skipped
    gate is a gate that is not there."""
    assert len(_MUTANTS) <= 10, (
        "the mutation sample has grown past ten. Either drop the cheapest ones "
        "or move the whole file behind an explicit nightly marker — do NOT let "
        "it grow into a job the team routes around.")
    assert len({m.id for m in _MUTANTS}) == len(_MUTANTS)


# --------------------------------------------------------------- the control

@skip_mutation
def test_an_unmutated_tree_passes(tmp_path):
    """THE POSITIVE CONTROL, and the most important test in this file.

    Every assertion below is "the child failed". A harness that fails for its
    OWN reasons — a missing ini, an unimportable copy, a bad cwd — reports every
    guard as perfectly protected while grading nothing at all. That is precisely
    the class of defect this file exists to find, so the harness is made to
    prove itself first, on the same copy machinery and one of the same test
    files.
    """
    root = _mutant_tree(tmp_path)
    r = _run_child(root, ("tests/test_cooler_warm_ramp.py",))
    assert r.returncode == 0, (
        "the mutation harness cannot run an UNMUTATED copy of the tree, so "
        "every 'the test went red' result below is meaningless:\n"
        + r.stdout[-3000:] + r.stderr[-2000:])


# ------------------------------------------------------------- the sample

@skip_mutation
@pytest.mark.slow
@pytest.mark.parametrize("mutant", _MUTANTS, ids=lambda m: m.id)
def test_breaking_the_guard_makes_its_test_go_red(mutant, tmp_path):
    root = _mutant_tree(tmp_path)
    _apply(root, mutant)
    r = _run_child(root, mutant.tests)
    # A non-zero exit is not enough: a collection error, an import failure or a
    # missing fixture also exits non-zero, and would let a mutant read as
    # "caught" while nothing graded it. Require a FAILED TEST.
    assert " failed" in r.stdout, (
        f"[{mutant.id}] the child did not report a failing test — it exited "
        f"{r.returncode} without one, so this mutant proves nothing about "
        f"coverage:\n{r.stdout[-3000:]}{r.stderr[-1500:]}")
    assert r.returncode != 0, (
        f"[{mutant.id}] {mutant.path} was broken on purpose and "
        f"{' '.join(mutant.tests)} STAYED GREEN.\n\n"
        f"What was disarmed: {mutant.costs}.\n\n"
        f"So either those tests do not actually exercise this guard — write one "
        f"that does, and check the existing ones are not passing for an "
        f"unrelated reason — or a second guard covers the same case, in which "
        f"case say so here and re-point the mutant at the line that is really "
        f"load-bearing.\n\n"
        f"child output:\n{r.stdout[-3000:]}")
