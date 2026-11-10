# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/ci/shard_tests.py and the ratchet that keeps the `server` job's shards in step (WP 8.11).

Run with: python -B -m unittest discover -s tools/ci -p 'test_*.py'
The partition cases run over the real server/tests tree and need no install;
the workflow ratchet needs PyYAML (the `changes` job installs it).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import shard_tests  # noqa: E402
from test_changed_areas import load_ci  # noqa: E402

TESTS_DIR = REPO / "server" / "tests"
SHARD_COUNTS = (1, 2, 3, 4)

# A small recorded table: weights in seconds, two heavy files and a tail.
TABLE = {
    "tests/test_a.py": 120.0, "tests/test_b.py": 110.0, "tests/test_c.py": 60.0,
    "tests/test_d.py": 50.0, "tests/test_e.py": 20.0, "tests/test_f.py": 10.0,
    "tests/test_g.py": 5.0, "tests/test_h.py": 1.0,
}


def shards_of(files, n, weights):
    """{file: shard} as the tool prints it: one pass over every shard."""
    placed = {}
    for k in range(1, n + 1):
        for f in shard_tests.shard_files(files, k, n, weights):
            placed.setdefault(f, []).append(k)
    return placed


def real_weights():
    return shard_tests.load_weights(shard_tests.DEFAULT_WEIGHTS)


class RealTreePartitionTests(unittest.TestCase):
    """Every test file under server/tests lands in exactly one shard."""

    @classmethod
    def setUpClass(cls):
        cls.files = shard_tests.test_files(TESTS_DIR)
        cls.weights = real_weights()

    def assert_partition(self, n, weights):
        placed = shards_of(self.files, n, weights)
        lost = sorted(set(self.files) - set(placed))
        self.assertEqual(lost, [], "test files in no shard (n=%d)" % n)
        twice = sorted(f for f, ks in placed.items() if len(ks) != 1)
        self.assertEqual(twice, [], "test files in more than one shard (n=%d)" % n)
        self.assertEqual(sum(len(shard_tests.shard_files(self.files, k, n, weights))
                             for k in range(1, n + 1)), len(self.files))

    def test_each_file_is_in_exactly_one_shard_for_every_shard_count(self):
        for n in SHARD_COUNTS:
            self.assert_partition(n, self.weights)

    def test_the_partition_holds_with_no_recorded_weights_at_all(self):
        # Every file takes the hash fallback.
        for n in SHARD_COUNTS:
            self.assert_partition(n, {})

    def test_the_partition_holds_with_half_the_files_unrecorded(self):
        half = {f: w for i, (f, w) in enumerate(sorted(self.weights.items())) if i % 2 == 0}
        for n in SHARD_COUNTS:
            self.assert_partition(n, half)

    def test_the_files_are_the_ones_pytest_would_collect_from_the_tree(self):
        # pytest's default patterns, found without the tool's own walker.
        expected = sorted({"tests/" + p.relative_to(TESTS_DIR).as_posix()
                           for pattern in ("test_*.py", "*_test.py")
                           for p in TESTS_DIR.rglob(pattern)
                           if "__pycache__" not in p.parts})
        self.assertEqual(self.files, expected)
        self.assertGreater(len(self.files), 1000)

    def test_a_single_shard_is_the_whole_tree(self):
        self.assertEqual(shard_tests.shard_files(self.files, 1, 1, self.weights), self.files)

    def test_no_shard_is_empty(self):
        for n in SHARD_COUNTS:
            for k in range(1, n + 1):
                self.assertTrue(shard_tests.shard_files(self.files, k, n, self.weights), (k, n))


class StabilityTests(unittest.TestCase):
    def test_adding_a_file_moves_no_other_file(self):
        before = ["tests/test_%s.py" % c for c in "abcdefgh"]
        for n in SHARD_COUNTS:
            old = shards_of(before, n, TABLE)
            for new_file in ("tests/test_new.py", "tests/test_aaa_first.py", "tests/test_zzz_last.py"):
                new = shards_of(before + [new_file], n, TABLE)
                for f in before:
                    self.assertEqual(new[f], old[f], (n, new_file, f))

    def test_removing_a_file_moves_no_other_file(self):
        files = ["tests/test_%s.py" % c for c in "abcdefgh"]
        for n in SHARD_COUNTS:
            full = shards_of(files, n, TABLE)
            for gone in files:
                rest = shards_of([f for f in files if f != gone], n, TABLE)
                for f, ks in rest.items():
                    self.assertEqual(ks, full[f], (n, gone, f))

    def test_a_recorded_file_does_not_depend_on_which_unrecorded_files_exist(self):
        recorded = list(TABLE)
        for n in SHARD_COUNTS:
            alone = shards_of(recorded, n, TABLE)
            crowded = shards_of(recorded + ["tests/test_x%d.py" % i for i in range(50)], n, TABLE)
            for f in recorded:
                self.assertEqual(crowded[f], alone[f], (n, f))

    def test_a_file_with_no_recorded_weight_does_not_fail_and_lands_in_a_real_shard(self):
        for n in SHARD_COUNTS:
            placed = shards_of(["tests/test_brand_new.py"], n, TABLE)
            self.assertEqual(len(placed["tests/test_brand_new.py"]), 1)
            self.assertIn(placed["tests/test_brand_new.py"][0], range(1, n + 1))

    def test_the_fallback_is_a_pure_function_of_the_path(self):
        # Pinned: sha1 is the same in every process and on every platform,
        # where Python's own hash() is salted per process.
        for path, expected in (("tests/test_brand_new.py", [2, 2, 2]),
                               ("tests/test_other_new.py", [1, 2, 1]),
                               ("tests/sub/test_deep.py", [2, 2, 4])):
            self.assertEqual([shard_tests.fallback_shard(path, n) for n in (2, 3, 4)], expected, path)

    def test_the_assignment_does_not_depend_on_the_order_the_files_arrive_in(self):
        files = ["tests/test_%s.py" % c for c in "abcdefgh"]
        for n in SHARD_COUNTS:
            self.assertEqual(shards_of(files, n, TABLE), shards_of(list(reversed(files)), n, TABLE))


class BalanceTests(unittest.TestCase):
    def loads(self, n):
        weights = real_weights()
        assigned = shard_tests.assignment(weights, n)
        totals = [0.0] * n
        for f, w in weights.items():
            totals[assigned[f] - 1] += w
        return totals

    def test_the_recorded_weights_balance_every_shard_count_within_two_percent(self):
        for n in (2, 3, 4):
            totals = self.loads(n)
            mean = sum(totals) / n
            self.assertLessEqual(max(totals), mean * 1.02, (n, [round(t) for t in totals]))
            self.assertGreaterEqual(min(totals), mean * 0.98, (n, [round(t) for t in totals]))

    def test_the_table_records_time_not_a_count(self):
        weights = real_weights()
        self.assertGreater(len(weights), 1000)
        self.assertGreater(max(weights.values()), 60.0)
        self.assertTrue(all(w > 0 for w in weights.values()))
        self.assertTrue(all(f.startswith("tests/") and f.endswith(".py") for f in weights))


class CommandLineTests(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, "-B", str(HERE / "shard_tests.py"), *args],
                              capture_output=True, text=True)

    def test_a_shard_prints_its_files_one_per_line_relative_to_server(self):
        out = self.run_tool("--shard", "2", "--of", "3")
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.split()
        self.assertGreater(len(lines), 100)
        self.assertTrue(all(re.fullmatch(r"tests/\S+\.py", line) for line in lines))
        self.assertEqual(lines, sorted(lines))

    def test_the_three_shards_printed_by_the_tool_are_the_whole_tree(self):
        got = []
        for k in (1, 2, 3):
            got.extend(self.run_tool("--shard", str(k), "--of", "3").stdout.split())
        self.assertEqual(sorted(got), shard_tests.test_files(TESTS_DIR))

    def test_a_shard_outside_one_to_the_total_is_refused_and_prints_nothing(self):
        for args in (("--shard", "0", "--of", "3"), ("--shard", "4", "--of", "3"),
                     ("--shard", "1", "--of", "0")):
            out = self.run_tool(*args)
            self.assertNotEqual(out.returncode, 0, args)
            self.assertEqual(out.stdout, "", args)

    def test_files_missing_from_the_table_are_counted_on_stderr_and_do_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            tests = Path(tmp) / "server" / "tests"
            tests.mkdir(parents=True)
            for name in ("test_one.py", "test_two.py", "helper.py"):
                (tests / name).write_text("", encoding="utf-8")
            weights = Path(tmp) / "weights.json"
            weights.write_text(json.dumps({"weights": {"tests/test_one.py": 3.0}}), encoding="utf-8")
            out = self.run_tool("--shard", "1", "--of", "1", "--tests-dir", str(tests),
                                "--weights", str(weights))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.split(), ["tests/test_one.py", "tests/test_two.py"])
            self.assertIn("1 of 2 test files have no recorded weight", out.stderr)


DURATIONS = """\
============================== slowest durations ===============================
29.83s call     tests/test_slow.py::test_one
10.00s setup    tests/test_slow.py::test_one
 2.50s call     tests/test_slow.py::TestK::test_two[a b-1]
1.25s teardown tests/test_slow.py::test_one
0.50s call     tests/test_quick.py::test_x
0.01s setup    tests/sub/test_deep.py::test_y

(22486 durations < 0.005s hidden.  Use -vv to show these durations.)
19179 passed, 584 skipped in 1030.40s (0:17:10)
"""


class RecordTests(unittest.TestCase):
    def test_the_durations_of_a_file_are_summed_over_calls_setups_and_teardowns(self):
        got = shard_tests.parse_durations(DURATIONS)
        self.assertEqual(got, {"tests/test_slow.py": 43.58, "tests/test_quick.py": 0.5,
                               "tests/sub/test_deep.py": 0.01})

    def test_a_file_that_has_no_listed_duration_still_gets_a_weight(self):
        weights = shard_tests.weights_from(["tests/test_slow.py", "tests/test_unlisted.py"],
                                           {"tests/test_slow.py": 43.58})
        self.assertEqual(weights["tests/test_slow.py"], 43.6)
        self.assertEqual(weights["tests/test_unlisted.py"], shard_tests.FLOOR_S)
        self.assertGreater(shard_tests.FLOOR_S, 0)

    def test_the_durations_of_several_shards_of_one_run_are_added_file_by_file(self):
        a = shard_tests.parse_durations("1.0s call     tests/test_a.py::t\n")
        b = shard_tests.parse_durations("2.0s call     tests/test_b.py::t\n")
        self.assertEqual(shard_tests.merge_durations([a, b]), {"tests/test_a.py": 1.0, "tests/test_b.py": 2.0})

    def test_record_writes_a_table_the_loader_reads_back_and_ends_in_a_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tests = Path(tmp) / "server" / "tests"
            tests.mkdir(parents=True)
            (tests / "test_slow.py").write_text("", encoding="utf-8")
            (tests / "test_unlisted.py").write_text("", encoding="utf-8")
            log = Path(tmp) / "pytest-output.txt"
            log.write_text(DURATIONS, encoding="utf-8")
            out = Path(tmp) / "weights.json"
            code = shard_tests.main(["--record", str(log), "--label", "a test run",
                                     "--tests-dir", str(tests), "--weights", str(out)])
            self.assertEqual(code, 0)
            raw = out.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertNotIn(b"\r", raw)
            self.assertEqual(json.loads(raw)["recorded_from"], "a test run")
            self.assertEqual(shard_tests.load_weights(out),
                             {"tests/test_slow.py": 43.6, "tests/test_unlisted.py": shard_tests.FLOOR_S})


class WorkflowRatchetTests(unittest.TestCase):
    """The `server` job runs every shard, once, with one total."""

    def setUp(self):
        self.ci = load_ci()
        self.job = self.ci["jobs"]["server"]
        self.steps = {s.get("name", ""): s for s in self.job["steps"]}

    def total(self):
        return int(self.job["env"]["SHARD_TOTAL"])

    def test_the_job_keeps_its_id_and_names_each_leg_as_the_already_green_rule_reads_it(self):
        self.assertEqual(self.job["name"], "server (${{ matrix.shard }})")

    def test_the_matrix_covers_every_shard_index_from_one_to_the_total(self):
        self.assertEqual(self.job["strategy"]["matrix"], {"shard": list(range(1, self.total() + 1))})

    def test_the_matrix_does_not_stop_the_other_shards_on_a_failure(self):
        self.assertIs(self.job["strategy"]["fail-fast"], False)

    def test_the_total_is_more_than_one_and_at_most_four(self):
        self.assertIn(self.total(), (2, 3, 4))

    def test_pytest_runs_only_the_files_of_its_shard_and_the_tool_gets_the_same_total(self):
        script = self.steps["pytest"]["run"]
        self.assertIn('python ../tools/ci/shard_tests.py --shard "$SHARD" --of "$SHARD_TOTAL"', script)
        self.assertEqual(self.steps["pytest"]["env"]["SHARD"], "${{ matrix.shard }}")
        self.assertIn('test -n "$FILES"', script)
        self.assertRegex(script, r"pytest -q -n auto --durations=\"\$DURATIONS\" -o faulthandler_timeout=300 \$FILES 2>&1 \| tee pytest-output\.txt")
        self.assertIn("set -o pipefail", script)

    def test_no_other_step_calls_the_tool_with_a_total_of_its_own(self):
        scripts = "\n".join(str(step.get("run", "")) for step in self.job["steps"])
        self.assertEqual(re.findall(r'--of ("[^"]*"|\S+)', scripts), ['"$SHARD_TOTAL"'])

    def test_pip_audit_runs_in_one_shard_only(self):
        audit = [s for n, s in self.steps.items() if n.startswith("pip-audit")]
        self.assertEqual(len(audit), 1)
        self.assertEqual(str(audit[0]["if"]).strip(), "matrix.shard == 1")

    def test_each_shard_keeps_its_own_duration_list_on_main(self):
        keep = self.steps["Keep the whole duration list (main only)"]
        self.assertIn("refs/heads/main", keep["if"])
        self.assertEqual(keep["with"]["name"], "server-durations-${{ matrix.shard }}")

    def test_the_job_still_runs_only_when_its_area_is_on_and_it_is_not_already_green(self):
        condition = str(self.job["if"])
        self.assertIn("needs.changes.outputs.server == 'true'", condition)
        self.assertIn("needs.changes.outputs.already-green-server != 'true'", condition)

    def test_ci_ok_waits_for_the_server_job_so_a_failed_shard_fails_it(self):
        needs = self.ci["jobs"]["ci-ok"]["needs"]
        self.assertIn("server", needs)


class LegNamesTests(unittest.TestCase):
    def test_the_legs_the_workflow_creates_are_named_server_k(self):
        job = load_ci()["jobs"]["server"]
        legs = [job["name"].replace("${{ matrix.shard }}", str(k)) for k in job["strategy"]["matrix"]["shard"]]
        self.assertEqual(legs, ["server (%d)" % k for k in range(1, len(legs) + 1)])


if __name__ == "__main__":
    unittest.main()
