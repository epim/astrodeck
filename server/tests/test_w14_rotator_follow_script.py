# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-88 (#594, the tooling half): scripts/rig_rotator_follow.py.

The measurement that showed the CAA does not turn the camera (#594: a 103
degree move turned it 2.8) lived only on the rig as ``bl_measure.py``. This is
its repeatable replacement, and the part of it a test can reach is the grading:
the follow fraction, the verdict, and the reversal exemption are pure functions
of a run's raw angles, so they are tested here on canned JSONL, with the plan
and the refusals beside them. The HTTP half runs on the rig and nowhere else.

The numbers below are the 2026-09-29 measurement's own where it had them
(1 degree steps followed 0.98-1.02, solved PA running OPPOSITE to the
mechanical angle; a 103 degree move turned the camera 2.83), and a healthy
rotator's where it did not (a 0.2 degree reversal loss, large moves followed
1:1).

MUTANTS RUN (each from a byte backup of the script inside this worktree,
restored byte-identically, sha256 compared, the mutant text grepped out):

  S1 "reversal step not exempted" (``if small and reversal:`` made
  ``if False:`` in ``grade``). Red on
  test_a_healthy_rotator_passes_and_reports_the_reversal_loss: the first
  step after each reversal turned the camera 0.30 of the 0.5 degrees
  commanded (0.60, a 0.2 degree backlash) and failed a healthy rotator.

  S2 "large moves exempt after a reversal" (``if small and reversal:`` made
  ``if reversal:``). Red on
  test_a_slipping_103_degree_move_fails_even_after_a_reversal.

  S3 "direction ignored" (the mixed-direction check removed). Red on
  test_a_camera_that_turns_both_ways_fails_even_in_range.

  S4 "sign ignored" (``sign = rot.get("sky_sign") or 1`` made ``sign = 1``).
  Red on test_the_script_commands_the_planned_mechanical_change_whatever_the_
  sign[-1]: the moves come out as the opposite turns.

  S5 "direct dropped" (``"direct": True`` removed from the move body). Red on
  the same test, on every published sign: a move was posted without direct.

The failing assertion each produced is recorded verbatim on the test that
caught it.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rig_rotator_follow.py"
_spec = importlib.util.spec_from_file_location("astrodeck_rig_rotator_follow",
                                               _SCRIPT)
follow = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_rig_rotator_follow"] = follow
_spec.loader.exec_module(follow)


def _run(solved: list[float], *, start_pa: float = 92.55,
         noise: tuple[float, ...] = (92.55, 92.60, 92.52),
         moves: tuple[float, ...] | None = None) -> list[dict]:
    """The raw records of a run: one solved PA change per move, applied to a
    running PA (so wrapping through 0/360 is exercised by the caller's start).
    """
    moves = follow.MOVES_DEG if moves is None else moves
    assert len(solved) == len(moves)
    records = [{"kind": "noise", "pa_deg": p, "mech_deg": 150.0} for p in noise]
    pa = start_pa
    for i, (cmd, change) in enumerate(zip(moves, solved), start=1):
        nxt = (pa + change) % 360.0
        records.append({"kind": "move", "index": i, "commanded_deg": cmd,
                        "pa_before_deg": pa, "pa_after_deg": nxt,
                        "mech_before_deg": 150.0, "mech_after_deg": 151.0})
        pa = nxt
    return records


#: A healthy rotator on a train whose solved PA runs OPPOSITE the mechanical
#: angle (the rig's sign, #145), with 0.2 degrees of reversal loss: every move
#: followed 1:1 except the first step after each reversal, which loses 0.2.
HEALTHY = [
    -1.01, -0.98, -1.02,                      # +1 x3
    0.30, 0.50, 0.49, 0.51, 0.50, 0.48, 0.50, 0.50,   # -0.5 x8 (first: reversal)
    -0.30, -0.52, -0.48, -0.49,               # +0.5 x4 (first: reversal)
    -20.02, 19.95, -102.8, 103.1,             # +20, -20, +103, -103
]


def test_the_plan_is_the_594_plan():
    """The moves are the ones #594 specifies, in order: 3 x +1, 8 x -0.5,
    4 x +0.5, then +-20 and +-103."""
    assert follow.MOVES_DEG == (
        (1.0,) * 3 + (-0.5,) * 8 + (0.5,) * 4 + (20.0, -20.0, 103.0, -103.0))
    assert follow.NOISE_SOLVES == 3


def test_follow_fraction_is_the_ratio_of_sizes_whatever_the_signs():
    assert follow.follow_fraction(103.0, -2.83) == pytest.approx(2.83 / 103.0)
    assert follow.follow_fraction(-0.5, 0.39) == pytest.approx(0.78)
    assert follow.follow_fraction(0.0, 0.0) == 1.0, "no division by zero"


def test_wrap180_folds_the_shortest_way():
    assert follow.wrap180(359.0 - 1.0) == pytest.approx(-2.0)
    assert follow.wrap180(1.0 - 359.0) == pytest.approx(2.0)
    assert follow.wrap180(-190.0) == pytest.approx(170.0)


def test_a_healthy_rotator_passes_and_reports_the_reversal_loss():
    """The first step after each reversal turned the camera 0.30 of the 0.5
    degrees commanded (0.60: a 0.2 degree backlash, the true figure #594
    measured), which is outside the small-step band of 0.7-1.3 and must not
    fail a healthy rotator: it is the exempt step, reported as the reversal
    loss.

    Mutation S1 'reversal step not exempted' (``if small and reversal:`` made
    ``if False:``) went red here:

        >       assert result["verdict"] == "PASS", result["failures"]
        E       AssertionError: ['move 4 (-0.5 deg): followed 0.60, outside
        0.7-1.3', 'move 12 (+0.5 deg): followed 0.60, outside 0.7-1.3']
    """
    result = follow.grade(_run(HEALTHY))

    assert result["verdict"] == "PASS", result["failures"]
    assert result["sign"] == -1, "the solved PA runs opposite to the mechanical"
    assert result["noise_deg"] == pytest.approx(0.08, abs=1e-6)
    # Reported for every reversal: the 0.5 steps lose 0.2, and the large
    # moves that follow a reversal lose almost nothing.
    losses = result["reversal_losses_deg"]
    assert losses[0] == pytest.approx(0.2, abs=1e-6), losses
    assert losses[1] == pytest.approx(0.2, abs=1e-6), losses
    exempt = [r for r in result["moves"] if r["exempt"]]
    assert [r["commanded_deg"] for r in exempt] == [-0.5, 0.5], exempt


def test_the_first_move_of_a_run_is_not_a_reversal():
    assert follow.is_reversal(None, 1.0) is False
    assert follow.is_reversal(1.0, 1.0) is False
    assert follow.is_reversal(1.0, -0.5) is True
    assert follow.is_reversal(-0.5, 0.5) is True


def test_a_slipping_103_degree_move_fails_even_after_a_reversal():
    """#594 as it was measured: 1:1 on the small steps, and a 103 degree move
    that turned the camera 2.83 degrees (0.03). That move follows a +-20 one
    (a reversal), and a large move is never exempt, or the 103 degree question
    the whole measurement exists to ask would go ungraded.

    Mutation S2 'large moves exempt after a reversal' (``if small and
    reversal:`` made ``if reversal:``) went red here:

        >       assert result["verdict"] == "FAIL"
        E       AssertionError: assert 'PASS' == 'FAIL'
    """
    slipping = list(HEALTHY)
    slipping[-2] = 2.83          # +103 turned the camera 2.83 degrees
    slipping[-1] = -2.5          # and -103 turned it back about as far

    result = follow.grade(_run(slipping))

    assert result["verdict"] == "FAIL"
    said = " ".join(result["failures"])
    assert "move 18 (+103 deg)" in said and "0.03" in said, said
    assert "move 19 (-103 deg)" in said, said


def test_a_camera_that_turns_both_ways_fails_even_in_range():
    """Every fraction can be in range while the camera turns the wrong way on
    some moves; that is not following. One -20 move comes back with the
    opposite direction at 1:1.

    Mutation S3 'direction ignored' (the mixed-direction check removed) went
    red here:

        >       assert result["verdict"] == "FAIL"
        E       AssertionError: assert 'PASS' == 'FAIL'
    """
    mixed = list(HEALTHY)
    mixed[-3] = -mixed[-3]       # the -20 move turned the camera the other way

    result = follow.grade(_run(mixed))

    assert result["verdict"] == "FAIL"
    assert any("both ways" in f for f in result["failures"]), result
    assert result["sign"] is None


@pytest.mark.parametrize("commanded,solved,ok", [
    (10.0, 9.1, True), (10.0, 8.9, False),        # large: the 0.9 edge
    (10.0, 10.9, True), (10.0, 11.1, False),      # large: the 1.1 edge
    (1.0, 0.71, True), (1.0, 0.69, False),        # small: the 0.7 edge
    (1.0, 1.29, True), (1.0, 1.31, False),        # small: the 1.3 edge
    # The line between them is 3 degrees, inclusive for SMALL: 2.5 of 3.0 is
    # 0.83, in the small band and outside the large one; 3.0 of 3.5 is 0.86,
    # outside the large band and inside the small one.
    (3.0, 2.5, True), (3.5, 3.0, False),
])
def test_the_band_edges(commanded, solved, ok):
    """Moves over 3 degrees are graded 0.9-1.1, smaller ones 0.7-1.3. Each
    edge is probed 0.01 inside and outside, not on it: the PA is folded
    through a float mod, and a value ON an edge would test the rounding."""
    recs = [{"kind": "move", "commanded_deg": commanded,
             "pa_before_deg": 10.0, "pa_after_deg": 10.0 + solved}]
    assert follow.grade(recs)["verdict"] == ("PASS" if ok else "FAIL")


def test_a_move_with_no_solve_is_not_a_pass():
    recs = _run(HEALTHY)
    recs[-1]["pa_after_deg"] = None

    result = follow.grade(recs)

    assert result["verdict"] == "FAIL"
    assert any("no solve landed" in f for f in result["failures"])


def test_an_empty_run_is_not_a_pass():
    assert follow.grade([])["verdict"] == "FAIL"
    only_noise = _run([], moves=())
    assert follow.grade(only_noise)["verdict"] == "FAIL"


def test_the_angles_wrap_through_north():
    """A PA that crosses 0/360 between solves is a small change, not 359."""
    result = follow.grade(_run(HEALTHY, start_pa=1.0))
    assert result["verdict"] == "PASS", result["failures"]


# ---------------------------------------------------------- the CLI, offline


def _write(tmp_path, records) -> str:
    path = tmp_path / "run.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                    encoding="utf-8")
    return str(path)


def test_grade_flag_regrades_a_saved_run_and_exits_by_the_verdict(
        tmp_path, capsys):
    good = _write(tmp_path, _run(HEALTHY))
    assert follow.main(["--grade", good]) == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "PASS", out
    assert "reversal loss" in out and "OPPOSITE" in out, out

    bad = list(HEALTHY)
    bad[-2] = 2.83
    assert follow.main(["--grade", _write(tmp_path, _run(bad))]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_every_printed_line_is_angles_and_words_only(tmp_path, capsys):
    """Nothing but the commanded and solved angles, the fraction and words:
    no coordinates, no cookie, no token. The lines are built from the graded
    rows alone, which never held anything else."""
    follow.main(["--grade", _write(tmp_path, _run(HEALTHY))])
    out = capsys.readouterr().out
    allowed = re.compile(r"^[A-Za-z0-9 +\-.,:()/_]+$")
    for line in out.splitlines():
        assert allowed.match(line), line


def test_the_script_reads_no_coordinate_or_secret_keys():
    """The only status keys it reads are the rotator's angles and the sky
    angle's PA and exposure time, plus booleans about what is busy."""
    text = _SCRIPT.read_text(encoding="utf-8")
    for key in ("ra_str", "dec_str", "ra_hours", "dec_deg", "latitude",
                "longitude", "alt_deg", "az_deg", "admin_token",
                "device_token", "pier_side"):
        assert f'"{key}"' not in text and f"'{key}'" not in text, key


# --------------------------------------------------------------- refusals


def _ready_status(**over) -> dict:
    status = {"rotator": {"mech_deg": 10.0, "sky_deg": 20.0},
              "connected": {"camera": {"connected": True}},
              "mount": {"tracking": True, "slewing": False, "parked": False},
              "busy_lanes": [], "looping": False}
    status.update(over)
    return status


def test_a_ready_rig_is_not_refused():
    assert follow._refusals(_ready_status(), {"running": False,
                                              "state": "idle"},
                            {"rotator": {"range_type": "full"}}) == []


@pytest.mark.parametrize("over,needle", [
    ({"mount": {"tracking": False}}, "not tracking"),
    ({"mount": {"tracking": True, "slewing": True}}, "slewing"),
    ({"mount": {"tracking": True, "parked": True}}, "parked"),
    ({"busy_lanes": ["goto"]}, "busy lanes"),
    ({"looping": True}, "looping"),
    ({"rotator": None}, "no rotator"),
    ({"connected": {}}, "no camera"),
])
def test_what_the_rig_must_not_be_doing(over, needle):
    why = follow._refusals(_ready_status(**over), {"running": False},
                           {"rotator": {"range_type": "full"}})
    assert any(needle in w for w in why), why


def test_a_running_sequence_and_a_limited_range_are_refused():
    why = follow._refusals(_ready_status(), {"running": True,
                                             "state": "running"},
                           {"rotator": {"range_type": "half"}})
    assert any("sequence" in w for w in why), why
    assert any("range" in w and "half" in w for w in why), why


# ------------------------------------------- the whole run, on a fake rig
#
# The HTTP half runs on the rig and nowhere else, but its ORCHESTRATION (which
# position the move route is asked for, in which order the solves and the moves
# happen, what is refused and when the rotator is halted) is plain Python that
# a typo can break and nothing else would notice until dusk. ``_FakeRig`` is a
# model of the five routes the script uses, with a camera that turns by a
# chosen fraction of what the rotator does, on a virtual clock so the 150 s
# solve timeout and the 15 s grace cost the test nothing.


class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class _FakeRig:
    """A model of the rig: the CAA's mechanical angle, a camera behind it whose
    solved PA runs ``train_sign`` against the mechanical angle and follows a
    ``follow(delta)`` fraction of it, and the routes the script posts to. The
    status carries canaries (a coordinate string, a pier side) that nothing the
    script prints or writes may contain."""

    def __init__(self, clock, *, train_sign=-1, published_sign=None,
                 follow=lambda delta: 1.0, solves=True, tracking=True,
                 fold=False):
        self.clock = clock
        self.train_sign = train_sign
        self.published_sign = published_sign
        self.follow = follow
        self.solves = solves
        self.tracking = tracking
        self.fold = fold
        self.root = "."
        self.mech = 150.0
        self.pa = 92.55
        self.sky_deg = 92.55          # the rotator's own idea, set by a sync
        self.sky_angle = None
        self.move_busy = 0
        self.sync_busy = 0
        self.posts: list[tuple[str, dict]] = []
        self.mech_changes: list[float] = []
        self.halted = False

    # ---- the routes
    def get(self, path):
        if path == "/api/sequence/state":
            return {"running": False, "state": "idle"}
        if path == "/api/config":
            return {"rotator": {"range_type": "full"}, "site": {"name": "CANARY-SITE"}}
        assert path == "/api/status", path
        busy = []
        if self.move_busy:
            busy.append("rotator")
            self.move_busy -= 1
        if self.sync_busy:
            busy.append("rotate_to_pa")
            self.sync_busy -= 1
            if self.solves:
                self.sky_angle = {"pa_deg": self.pa, "exposed_at": self.clock.time(),
                                  "pier_side": "CANARY-PIER", "calibrated": True}
                self.sky_deg = self.pa
        return {
            "rotator": {"mech_deg": self.mech, "sky_deg": self.sky_deg,
                        "moving": bool(self.move_busy),
                        "sky_sign": self.published_sign},
            "connected": {"camera": {"connected": True}},
            "mount": {"tracking": self.tracking, "slewing": False,
                      "parked": False, "ra_str": "CANARY-RA", "dec_str": "CANARY-DEC"},
            "busy_lanes": busy, "looping": False,
            "sky_angle": self.sky_angle,
        }

    def post(self, path, body=None):
        body = body or {}
        self.posts.append((path, body))
        if path == "/api/rotator/move":
            eff = self.published_sign or 1
            delta = follow.wrap180(body["position_deg"] - self.sky_deg) * eff
            self.mech_changes.append(delta)
            self.mech = (self.mech + delta) % 360.0
            self.pa = (self.pa + self.train_sign * self.follow(delta) * delta) % 360.0
            self.move_busy = 2
            return {"started": "rotator", "target_deg": body["position_deg"],
                    "adjusted": self.fold}
        if path == "/api/rotator/sync-to-sky":
            self.sync_busy = 1
            return {"started": "rotate_to_pa"}
        if path == "/api/rotator/halt":
            self.halted = True
            return {"ok": True}
        raise AssertionError(path)

    def halt(self):
        self.post("/api/rotator/halt")


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    """``run(**rig_kw)`` -> (exit code, the fake rig, the JSONL path)."""
    def go(**kw):
        clock = _Clock()
        rig = _FakeRig(clock, **kw)
        monkeypatch.setattr(follow, "time", clock)
        monkeypatch.setattr(follow, "Rig", lambda: rig)
        out = tmp_path / "captures" / "backlash" / "run.jsonl"
        return follow.run(str(out)), rig, out
    return go


@pytest.mark.parametrize("published_sign", [None, -1, 1])
def test_the_script_commands_the_planned_mechanical_change_whatever_the_sign(
        fake_run, published_sign):
    """The move route takes a SKY angle, and maps it through the sign the rig
    published (or +1 when it has none). The script must ask for the sky angle
    that is the PLANNED mechanical change under that same sign: sky + sign *
    delta. A script that ignored the sign would command the opposite turn
    under a measured -1, and a measurement of how far the camera follows a move
    in the wrong direction is not the measurement.

    Mutation S4 'sign ignored' (``sign = rot.get("sky_sign") or 1`` made
    ``sign = 1``) went red on the -1 case, not on None or +1:

        >       assert rig.mech_changes == pytest.approx(list(follow.MOVES_DEG), abs=1e-6)
        E       assert [-1.0, -1.0, ...0.5, 0.5, ...] == approx([1.0 ...])
        E         comparison failed. Mismatched elements: 19 / 19:
        E         Max absolute difference: 206.0
    """
    code, rig, out = fake_run(published_sign=published_sign)

    assert rig.mech_changes == pytest.approx(list(follow.MOVES_DEG), abs=1e-6)
    assert all(body.get("direct") is True
               for path, body in rig.posts if path == "/api/rotator/move"), \
        "a move was posted without direct: the one-sided approach would add travel"


def test_a_healthy_fake_rig_passes_end_to_end(fake_run, capsys):
    code, rig, out = fake_run()

    assert code == 0, capsys.readouterr().out
    records = follow.read_jsonl(str(out))
    assert [r["kind"] for r in records].count("noise") == follow.NOISE_SOLVES
    assert [r["kind"] for r in records].count("move") == len(follow.MOVES_DEG)
    result = follow.grade(records)
    assert result["verdict"] == "PASS" and result["sign"] == -1, result
    text = capsys.readouterr().out
    assert text.strip().splitlines()[-1] == "PASS", text


def test_a_slipping_coupling_fails_end_to_end(fake_run, capsys):
    """#594's rig: moves over a few degrees turn the camera 3 percent."""
    code, rig, out = fake_run(follow=lambda d: 0.03 if abs(d) > 3.0 else 1.0)

    assert code == 1
    text = capsys.readouterr().out
    assert "OUT OF RANGE" in text and "FAIL" in text, text


def test_a_rig_that_is_not_ready_is_refused_before_anything_moves(
        fake_run, capsys):
    code, rig, out = fake_run(tracking=False)

    assert code == 3
    assert rig.posts == [], rig.posts
    assert "REFUSED" in capsys.readouterr().out
    assert not out.exists()


def test_a_solve_that_never_lands_stops_the_run_and_halts_the_rotator(
        fake_run, capsys):
    """The sync lane ends with no new position angle (clouds): the script says
    so after its grace period, rather than waiting out the whole timeout, and
    halts whatever is moving."""
    code, rig, out = fake_run(solves=False)

    assert code == 2
    assert rig.halted is True
    assert not any(p == "/api/rotator/move" for p, _ in rig.posts), rig.posts
    assert "could not measure" in capsys.readouterr().out


def test_a_move_the_route_folded_into_the_range_stops_the_run(fake_run, capsys):
    code, rig, out = fake_run(fold=True)

    assert code == 2
    assert rig.halted is True
    assert len([p for p, _ in rig.posts if p == "/api/rotator/move"]) == 1


def test_nothing_the_script_prints_or_writes_carries_a_coordinate_or_a_site(
        fake_run, capsys):
    """The status and the config the script reads carry canaries: the mount's
    coordinate strings, a pier side and a site name. The only keys
    it takes are the rotator's angles and the sky angle's PA and exposure time,
    so none of them can reach the terminal or the JSONL."""
    code, rig, out = fake_run()

    shown = capsys.readouterr().out + out.read_text(encoding="utf-8")
    assert "CANARY" not in shown, shown
