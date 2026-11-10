# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/ci/already_green.py and the ratchet that keeps ci.yml reading it (#1260).

Run with: python -B -m unittest discover -s tools/ci -p 'test_*.py'
The decision tests run over recorded API answers and make no network call. The
real-git tests build throwaway repositories. The ratchet class needs PyYAML (the
`changes` job installs it).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import already_green  # noqa: E402
import changed_areas  # noqa: E402
from test_changed_areas import load_ci  # noqa: E402

REPO_NAME = "owner/name"
GATED = sorted(changed_areas.load_config(HERE / "areas.json")["areas"])
MERGE = "b" * 40
MAIN_PARENT = "d" * 40
PR_HEAD = "a" * 40
TREE = "c" * 40


def facts(event="push", ref="refs/heads/main", parents=(MAIN_PARENT, PR_HEAD),
          head_tree=TREE, second_parent_tree=TREE):
    return already_green.MergeFacts(event, ref, tuple(parents), head_tree, second_parent_tree)


def run(number, head=PR_HEAD, status="completed", conclusion="success", attempt=1,
        event="pull_request", run_id=None):
    run_id = run_id or 1000 + number
    return {"id": run_id, "run_number": number, "run_attempt": attempt, "status": status,
            "conclusion": conclusion, "event": event, "head_sha": head,
            "html_url": "https://github.com/%s/actions/runs/%d" % (REPO_NAME, run_id)}


def job(name, conclusion="success", status="completed", attempt=1):
    return {"name": name, "status": status, "conclusion": conclusion, "run_attempt": attempt}


def passing_jobs(**overrides):
    """What a green run reports: every gated job a success, two matrix legs, windows-com skipped."""
    jobs = []
    for name in GATED:
        names = ["container-security (amd64)", "container-security (arm64)"] \
            if name == "container-security" else [name]
        jobs.extend(overrides.get(n, job(n)) for n in names)
    jobs.append(job("windows-com", conclusion="skipped"))
    return jobs


def runs_answer(*runs):
    return {"total_count": len(runs), "workflow_runs": list(runs)}


def jobs_answer(jobs, total=None):
    return {"total_count": len(jobs) if total is None else total, "jobs": list(jobs)}


class Api:
    """A recorded API: answers by request path, remembers every request, raises for the rest."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, path):
        self.requests.append(path)
        if path not in self.answers:
            raise already_green.ApiError("no recorded answer for %s" % path)
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer


def recorded(runs, jobs, *, run_id=None):
    run_id = run_id or runs["workflow_runs"][0]["id"]
    return Api({
        already_green.runs_path(REPO_NAME, PR_HEAD): runs,
        already_green.jobs_path(REPO_NAME, run_id): jobs,
    })


def decide(api, merge=None, jobs=GATED, **kwargs):
    return already_green.decide(jobs, merge or facts(), api, repo=REPO_NAME, **kwargs)


def greens(verdicts):
    return sorted(job for job, v in verdicts.items() if v.green)


class DecisionTests(unittest.TestCase):
    def green_api(self):
        return recorded(runs_answer(run(5)), jobs_answer(passing_jobs()))

    def test_an_identical_tree_and_a_green_run_make_every_job_green(self):
        verdicts = decide(self.green_api())
        self.assertEqual(greens(verdicts), GATED)

    def test_a_skip_names_the_pull_request_run(self):
        verdict = decide(self.green_api())["server"]
        self.assertEqual(verdict.run_url, "https://github.com/%s/actions/runs/1005" % REPO_NAME)
        self.assertIn("run 5", verdict.reason)

    def test_the_two_requests_are_the_documented_ones(self):
        api = self.green_api()
        decide(api)
        self.assertEqual(api.requests, [
            "/repos/owner/name/actions/workflows/ci.yml/runs?event=pull_request&head_sha=" + PR_HEAD + "&per_page=100",
            "/repos/owner/name/actions/runs/1005/jobs?filter=latest&per_page=100",
        ])

    def test_a_failed_job_runs_and_the_others_stay_green(self):
        api = recorded(runs_answer(run(5, conclusion="failure")),
                       jobs_answer(passing_jobs(server=job("server", conclusion="failure"))))
        verdicts = decide(api)
        self.assertFalse(verdicts["server"].green)
        self.assertIn("failure", verdicts["server"].reason)
        self.assertEqual(greens(verdicts), [g for g in GATED if g != "server"])

    def test_a_run_that_was_only_cancelled_makes_nothing_green(self):
        cancelled = [job(n, conclusion="cancelled") for n in GATED] \
            + [job("container-security (arm64)", conclusion="cancelled")]
        api = recorded(runs_answer(run(5, conclusion="cancelled")), jobs_answer(cancelled))
        self.assertEqual(greens(decide(api)), [])

    def test_a_run_still_in_progress_makes_nothing_green_and_reads_no_jobs(self):
        api = recorded(runs_answer(run(5, status="in_progress", conclusion=None)),
                       jobs_answer(passing_jobs()))
        verdicts = decide(api)
        self.assertEqual(greens(verdicts), [])
        self.assertIn("in_progress", verdicts["server"].reason)
        self.assertEqual(len(api.requests), 1)

    def test_a_job_the_pull_request_run_skipped_is_not_green(self):
        # The area filter turned relay off in the pull request: nothing tested it.
        api = recorded(runs_answer(run(5)),
                       jobs_answer(passing_jobs(relay=job("relay", conclusion="skipped"))))
        verdicts = decide(api)
        self.assertFalse(verdicts["relay"].green)
        self.assertIn("skipped", verdicts["relay"].reason)
        self.assertEqual(greens(verdicts), [g for g in GATED if g != "relay"])

    def test_a_job_missing_from_the_run_is_not_green(self):
        jobs = [j for j in passing_jobs() if j["name"] != "native"]
        verdicts = decide(recorded(runs_answer(run(5)), jobs_answer(jobs)))
        self.assertFalse(verdicts["native"].green)

    def test_a_tree_that_differs_runs_everything_and_asks_the_api_nothing(self):
        api = self.green_api()
        verdicts = decide(api, facts(second_parent_tree="e" * 40))
        self.assertEqual(greens(verdicts), [])
        self.assertIn("tree differs", verdicts["server"].reason)
        self.assertEqual(api.requests, [])

    def test_a_squash_merge_has_one_parent_and_runs_everything(self):
        api = self.green_api()
        verdicts = decide(api, facts(parents=(MAIN_PARENT,), second_parent_tree=""))
        self.assertEqual(greens(verdicts), [])
        self.assertIn("not a two-parent merge", verdicts["server"].reason)
        self.assertEqual(api.requests, [])

    def test_a_direct_push_of_a_root_or_shallow_commit_runs_everything(self):
        self.assertEqual(greens(decide(self.green_api(), facts(parents=(), second_parent_tree=""))), [])

    def test_an_octopus_merge_runs_everything(self):
        merge = facts(parents=(MAIN_PARENT, PR_HEAD, "f" * 40))
        self.assertEqual(greens(decide(self.green_api(), merge)), [])

    def test_a_missing_tree_never_counts_as_equal(self):
        merge = facts(head_tree="", second_parent_tree="")
        self.assertEqual(greens(decide(self.green_api(), merge)), [])

    def test_only_a_push_to_main_can_skip(self):
        for merge in (facts(event="pull_request"), facts(event="workflow_dispatch"),
                      facts(ref="refs/heads/feature"), facts(ref="refs/tags/v1.0.0"), facts(ref="")):
            api = self.green_api()
            self.assertEqual(greens(decide(api, merge)), [], merge)
            self.assertEqual(api.requests, [], merge)

    def test_an_api_error_runs_everything_and_says_why(self):
        for error in (already_green.ApiError("GitHub API answered HTTP 403"), RuntimeError("boom")):
            api = Api({already_green.runs_path(REPO_NAME, PR_HEAD): error})
            verdicts = decide(api)
            self.assertEqual(greens(verdicts), [], error)
        self.assertIn("HTTP 403", decide(Api({already_green.runs_path(REPO_NAME, PR_HEAD):
                                              already_green.ApiError("GitHub API answered HTTP 403")}))["server"].reason)

    def test_an_error_on_the_jobs_request_runs_everything(self):
        api = Api({already_green.runs_path(REPO_NAME, PR_HEAD): runs_answer(run(5)),
                   already_green.jobs_path(REPO_NAME, 1005): already_green.ApiError("HTTP 500")})
        self.assertEqual(greens(decide(api)), [])

    def test_a_malformed_answer_runs_everything(self):
        for runs in ({}, {"workflow_runs": None}, [], {"workflow_runs": [{"event": "pull_request", "head_sha": PR_HEAD}]}):
            api = Api({already_green.runs_path(REPO_NAME, PR_HEAD): runs})
            self.assertEqual(greens(decide(api)), [], runs)
        api = Api({already_green.runs_path(REPO_NAME, PR_HEAD): runs_answer(run(5)),
                   already_green.jobs_path(REPO_NAME, 1005): {"jobs": []}})
        self.assertEqual(greens(decide(api)), [])

    def test_no_pull_request_run_for_the_head_runs_everything(self):
        api = recorded(runs_answer(), jobs_answer([]), run_id=1)
        verdicts = decide(api)
        self.assertEqual(greens(verdicts), [])
        self.assertIn("no ci.yml pull request run", verdicts["server"].reason)

    def test_a_run_of_another_head_or_event_is_not_evidence(self):
        runs = runs_answer(run(9, head="9" * 40), run(8, event="push"), run(7, event="workflow_dispatch"))
        self.assertEqual(greens(decide(recorded(runs, jobs_answer(passing_jobs()), run_id=1009))), [])

    def test_the_newest_run_decides_when_a_head_has_several(self):
        # Rule: newest by run number, completed or not. A re-run is the same run
        # with a higher attempt, so it is already the newest evidence; a later run
        # that went wrong or is still going outranks an older green one.
        old_green, new_failed = run(5), run(6, conclusion="failure")
        failed_jobs = jobs_answer(passing_jobs(ui=job("ui", conclusion="failure")))
        api = Api({already_green.runs_path(REPO_NAME, PR_HEAD): runs_answer(old_green, new_failed),
                   already_green.jobs_path(REPO_NAME, 1006): failed_jobs,
                   already_green.jobs_path(REPO_NAME, 1005): jobs_answer(passing_jobs())})
        self.assertFalse(decide(api)["ui"].green)
        self.assertTrue(decide(api)["server"].green)

    def test_an_older_failed_run_does_not_hide_a_newer_green_one(self):
        api = Api({already_green.runs_path(REPO_NAME, PR_HEAD):
                   runs_answer(run(6), run(5, conclusion="failure")),
                   already_green.jobs_path(REPO_NAME, 1006): jobs_answer(passing_jobs())})
        self.assertEqual(greens(decide(api)), GATED)

    def test_a_newer_run_still_going_outranks_an_older_green_one(self):
        api = Api({already_green.runs_path(REPO_NAME, PR_HEAD):
                   runs_answer(run(5), run(6, status="queued", conclusion=None)),
                   already_green.jobs_path(REPO_NAME, 1005): jobs_answer(passing_jobs())})
        self.assertEqual(greens(decide(api)), [])

    def test_a_matrix_job_needs_every_leg(self):
        failed_leg = passing_jobs()
        failed_leg = [job("container-security (arm64)", conclusion="failure")
                      if j["name"] == "container-security (arm64)" else j for j in failed_leg]
        verdicts = decide(recorded(runs_answer(run(5)), jobs_answer(failed_leg)))
        self.assertFalse(verdicts["container-security"].green)
        self.assertIn("arm64", verdicts["container-security"].reason)
        self.assertEqual(greens(verdicts), [g for g in GATED if g != "container-security"])

    def test_a_matrix_job_with_one_leg_skipped_is_not_green(self):
        legs = [job("container-security (amd64)"), job("container-security (arm64)", conclusion="skipped")]
        verdicts = decide(recorded(runs_answer(run(5)), jobs_answer(legs)), jobs=["container-security"])
        self.assertFalse(verdicts["container-security"].green)

    def test_a_job_name_that_only_shares_a_prefix_is_not_a_leg(self):
        jobs = [job("server-extra"), job("server (x)", conclusion="failure")]
        verdicts = decide(recorded(runs_answer(run(5)), jobs_answer(jobs)), jobs=["server"])
        self.assertFalse(verdicts["server"].green)
        verdicts = decide(recorded(runs_answer(run(5)), jobs_answer([job("server-extra")])), jobs=["server"])
        self.assertIn("no job server", verdicts["server"].reason)

    def test_a_job_from_an_earlier_attempt_is_not_green(self):
        api = recorded(runs_answer(run(5, attempt=2)), jobs_answer(passing_jobs()))
        self.assertEqual(greens(decide(api)), [])

    def test_an_incomplete_job_list_is_not_trusted(self):
        api = recorded(runs_answer(run(5)), jobs_answer(passing_jobs(), total=150))
        self.assertEqual(greens(decide(api)), [])

    def test_a_job_in_never_already_green_runs_whatever_the_run_says(self):
        verdicts = decide(self.green_api(), never=("server",))
        self.assertFalse(verdicts["server"].green)
        self.assertIn("only part", verdicts["server"].reason)
        self.assertEqual(greens(verdicts), [g for g in GATED if g != "server"])

    def test_never_already_green_names_only_jobs_that_exist(self):
        self.assertLessEqual(set(already_green.NEVER_ALREADY_GREEN), set(GATED))


# Recorded answers of the real API (slimmed to the fields the tool reads) for two
# merges that ran on main. The first merge's tree equals its pull request head's
# tree; the second's does not, though its pull request run was green too.
REAL_SAME = {
    "merge": "d96cfef00250cd23ca3340b2ca3265f4e8488b98",
    "head": "be65c5fcf3b9f5fa72d99a66489eef864077a754",
    "tree": "d3a263a7a22139c56b15fe284a6c0b9523d6c29d",
    "runs": '{"total_count": 1, "workflow_runs": [{"id": 42695961395, "run_number": 616, "run_attempt": 1,'
            ' "status": "completed", "conclusion": "success", "event": "pull_request",'
            ' "head_sha": "be65c5fcf3b9f5fa72d99a66489eef864077a754",'
            ' "html_url": "https://github.com/epim/astrodeck/actions/runs/42695961395"}]}',
    "jobs": '{"total_count": 10, "jobs": ['
            '{"name": "orangepi-security", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "ui", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "relay", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "server", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "windows-private-state", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "container-security (arm64)", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "native", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "container-security (amd64)", "status": "completed", "conclusion": "success", "run_attempt": 1},'
            ' {"name": "binary-smoke-windows", "status": "completed", "conclusion": "skipped", "run_attempt": 1},'
            ' {"name": "windows-com", "status": "completed", "conclusion": "skipped", "run_attempt": 1}]}',
}
REAL_DIFFERENT = {
    "merge": "c078c99a376bd9528f579fc493200602811de8e5",
    "head": "bddb71ff6aa15345c9d8f4b23f4e6c37c2f968f9",
    "merge_tree": "1742db1f136cc693afd7948e57622d416b8710b0",
    "head_tree": "2763346a65744bf8c5d73b665c2cc2e49743808f",
    "runs": '{"total_count": 1, "workflow_runs": [{"id": 42681411649, "run_number": 610, "run_attempt": 1,'
            ' "status": "completed", "conclusion": "success", "event": "pull_request",'
            ' "head_sha": "bddb71ff6aa15345c9d8f4b23f4e6c37c2f968f9",'
            ' "html_url": "https://github.com/epim/astrodeck/actions/runs/42681411649"}]}',
}


class RealAnswersTests(unittest.TestCase):
    def test_a_real_merge_on_an_identical_tree_skips_every_job_its_run_passed(self):
        api = Api({
            already_green.runs_path("epim/astrodeck", REAL_SAME["head"]): json.loads(REAL_SAME["runs"]),
            already_green.jobs_path("epim/astrodeck", 42695961395): json.loads(REAL_SAME["jobs"]),
        })
        merge = facts(parents=("0" * 40, REAL_SAME["head"]), head_tree=REAL_SAME["tree"],
                      second_parent_tree=REAL_SAME["tree"])
        verdicts = already_green.decide(GATED, merge, api, repo="epim/astrodeck")
        self.assertEqual(greens(verdicts), GATED)
        self.assertEqual(verdicts["server"].run_url, "https://github.com/epim/astrodeck/actions/runs/42695961395")

    def test_a_real_merge_whose_tree_differs_skips_nothing_though_its_run_was_green(self):
        api = Api({})
        merge = facts(parents=("0" * 40, REAL_DIFFERENT["head"]), head_tree=REAL_DIFFERENT["merge_tree"],
                      second_parent_tree=REAL_DIFFERENT["head_tree"])
        verdicts = already_green.decide(GATED, merge, api, repo="epim/astrodeck")
        self.assertEqual(greens(verdicts), [])
        self.assertEqual(api.requests, [])


def git(cwd, *args):
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                          "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def write_commit(cwd, name, text="1\n"):
    Path(cwd, name).write_text(text, encoding="utf-8")
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", "add " + name)
    return git(cwd, "rev-parse", "HEAD")


class GitFixture(unittest.TestCase):
    """An origin with a merge on main, and a one-commit-deep clone of it as CI makes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.origin = os.path.join(self.tmp.name, "origin")
        os.mkdir(self.origin)
        git(self.origin, "init", "-q", "-b", "main")
        write_commit(self.origin, "base.txt")
        git(self.origin, "checkout", "-q", "-b", "feature")
        self.pr_head = write_commit(self.origin, "feature.txt")
        git(self.origin, "checkout", "-q", "main")

    def merge(self, main_moved=False):
        if main_moved:
            write_commit(self.origin, "other.txt")
        git(self.origin, "merge", "-q", "--no-ff", "-m", "merge", "feature")
        return git(self.origin, "rev-parse", "HEAD")

    def clone_at(self, sha):
        work = os.path.join(self.tmp.name, "work-" + sha[:8])
        git(self.tmp.name, "clone", "-q", "--depth", "1", "--no-checkout",
            Path(self.origin).as_uri(), work)
        git(work, "fetch", "-q", "--no-tags", "--depth=1", "origin", sha)
        git(work, "checkout", "-q", "--detach", sha)
        return work

    def facts_in(self, cwd, sha, **kwargs):
        previous = os.getcwd()
        os.chdir(cwd)
        try:
            return already_green.gather_facts(kwargs.get("event", "push"), "refs/heads/main", sha)
        finally:
            os.chdir(previous)


class GatherFactsTests(GitFixture):
    def test_a_checkout_one_commit_deep_does_not_show_the_merge_parents(self):
        merge = self.merge()
        shallow = self.facts_in(self.clone_at(merge), merge)
        self.assertEqual(shallow.parents, ())
        self.assertIsNotNone(already_green.merge_refusal(shallow))

    def test_fetching_depth_two_brings_both_parents_and_their_trees(self):
        merge = self.merge()
        work = self.clone_at(merge)
        git(work, "fetch", "-q", "--no-tags", "--depth=2", "origin", merge)
        got = self.facts_in(work, merge)
        self.assertEqual(len(got.parents), 2)
        self.assertEqual(got.parents[1], self.pr_head)
        self.assertEqual(got.head_tree, git(self.origin, "rev-parse", merge + "^{tree}"))
        self.assertEqual(got.second_parent_tree, git(self.origin, "rev-parse", self.pr_head + "^{tree}"))
        self.assertIsNone(already_green.merge_refusal(got))

    def test_a_merge_after_main_moved_has_a_different_tree(self):
        merge = self.merge(main_moved=True)
        work = self.clone_at(merge)
        git(work, "fetch", "-q", "--no-tags", "--depth=2", "origin", merge)
        got = self.facts_in(work, merge)
        self.assertNotEqual(got.head_tree, got.second_parent_tree)
        self.assertEqual(already_green.merge_refusal(got), "tree differs from the pull request head")

    def test_a_squash_commit_has_one_parent(self):
        git(self.origin, "merge", "-q", "--squash", "feature")
        git(self.origin, "commit", "-q", "-m", "squash")
        squash = git(self.origin, "rev-parse", "HEAD")
        work = self.clone_at(squash)
        git(work, "fetch", "-q", "--no-tags", "--depth=2", "origin", squash)
        got = self.facts_in(work, squash)
        self.assertEqual(len(got.parents), 1)
        self.assertIn("not a two-parent merge", already_green.merge_refusal(got))

    def test_a_head_git_cannot_find_gives_facts_that_refuse(self):
        got = self.facts_in(self.origin, "1" * 40)
        self.assertEqual((got.parents, got.head_tree), ((), ""))
        self.assertIsNotNone(already_green.merge_refusal(got))


class MainTests(GitFixture):
    """The command line: outputs, log lines, and what happens without a token."""

    def setUp(self):
        super().setUp()
        self.areas = Path(self.tmp.name, "areas.json")
        self.areas.write_text(json.dumps({"always_all": [], "inert": [],
                                          "areas": {"server": ["server/**"], "relay": ["relay/**"]}}),
                              encoding="utf-8")
        self.merge_sha = self.merge()
        self.work = self.clone_at(self.merge_sha)
        git(self.work, "fetch", "-q", "--no-tags", "--depth=2", "origin", self.merge_sha)

    def main(self, areas_on='{"server": "true", "relay": "true"}', env=None, fetch=None):
        out, err = io.StringIO(), io.StringIO()
        previous = os.getcwd()
        os.chdir(self.work)
        patches = [mock.patch.dict(os.environ, env or {}, clear=False)]
        if fetch is not None:
            patches.append(mock.patch.object(already_green, "github_fetch", lambda *a, **k: fetch))
        try:
            with contextlib.ExitStack() as stack, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                for p in patches:
                    stack.enter_context(p)
                code = already_green.main(["--event", "push", "--ref", "refs/heads/main",
                                           "--head", self.merge_sha, "--repo", REPO_NAME,
                                           "--areas-on-json", areas_on, "--areas", str(self.areas)])
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0)
        return dict(line.split("=", 1) for line in out.getvalue().split()), err.getvalue()

    def green_api(self):
        jobs = [job("server"), job("relay")]
        return Api({already_green.runs_path(REPO_NAME, self.pr_head): runs_answer(run(5, head=self.pr_head)),
                    already_green.jobs_path(REPO_NAME, 1005): jobs_answer(jobs)})

    def test_a_green_run_on_an_identical_tree_skips_both_and_logs_the_run_url_per_job(self):
        got, log = self.main(fetch=self.green_api())
        self.assertEqual(got, {"already-green-relay": "true", "already-green-server": "true",
                               "already-green-jobs": "relay,server"})
        for job_name in ("relay", "server"):
            self.assertIn("%s: skipped, already green" % job_name, log)
        self.assertEqual(log.count("https://github.com/owner/name/actions/runs/1005"), 2)

    def test_a_job_whose_area_is_off_is_not_judged_and_is_not_reported_skipped(self):
        got, log = self.main(areas_on='{"server": "true", "relay": "false"}', fetch=self.green_api())
        self.assertEqual(got["already-green-relay"], "false")
        self.assertEqual(got["already-green-jobs"], "server")
        self.assertIn("relay: runs (its area did not change)", log)

    def test_an_unreadable_areas_output_judges_every_job(self):
        got, _ = self.main(areas_on="not json", fetch=self.green_api())
        self.assertEqual(got["already-green-jobs"], "relay,server")

    def test_no_token_means_every_job_runs_and_no_request_is_made(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("GITHUB_TOKEN", None)
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                got, log = self.main()
        self.assertEqual(got["already-green-jobs"], "")
        self.assertIn("GITHUB_TOKEN is not set", log)

    def test_an_unexpected_crash_still_prints_every_output_false(self):
        with mock.patch.object(already_green, "gather_facts", side_effect=OSError("git vanished")):
            got, log = self.main(fetch=self.green_api())
        self.assertEqual(got, {"already-green-relay": "false", "already-green-server": "false",
                               "already-green-jobs": ""})
        self.assertIn("already_green failed (OSError)", log)


class GithubFetchTests(unittest.TestCase):
    TOKEN = "tok-0123456789-not-a-real-token"

    def test_the_token_travels_only_in_the_authorization_header(self):
        seen = {}

        def urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["auth"] = request.get_header("Authorization")
            return io.BytesIO(b'{"ok": true}')

        with mock.patch("urllib.request.urlopen", urlopen):
            answer = already_green.github_fetch(self.TOKEN)("/repos/o/n/actions/runs/1/jobs?filter=latest")
        self.assertEqual(answer, {"ok": True})
        self.assertEqual(seen["url"], "https://api.github.com/repos/o/n/actions/runs/1/jobs?filter=latest")
        self.assertEqual(seen["auth"], "Bearer " + self.TOKEN)
        self.assertNotIn(self.TOKEN, seen["url"])

    def test_an_http_error_is_reported_without_the_token_or_the_response_body(self):
        error = urllib.error.HTTPError("https://api.github.com/x", 403, "rate limited", {}, io.BytesIO(b"body"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(already_green.ApiError) as caught:
                already_green.github_fetch(self.TOKEN)("/x")
        self.assertEqual(str(caught.exception), "GitHub API answered HTTP 403")

    def test_a_network_or_json_failure_is_an_api_error_with_only_its_type(self):
        for failure in (OSError("connection reset by peer " + self.TOKEN), ValueError("bad json")):
            with mock.patch("urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(already_green.ApiError) as caught:
                    already_green.github_fetch(self.TOKEN)("/x")
            self.assertNotIn(self.TOKEN, str(caught.exception))

    def test_no_token_raises_before_any_request(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            with self.assertRaises(already_green.ApiError):
                already_green.github_fetch("")("/x")


class JobsOnTests(unittest.TestCase):
    CONFIG = {"areas": {"server": [], "relay": [], "ui": []}}

    def test_only_areas_that_are_true_are_judged(self):
        self.assertEqual(already_green.jobs_on('{"server": "true", "relay": "false", "ui": "true"}', self.CONFIG),
                         ["server", "ui"])

    def test_an_output_that_is_missing_or_not_json_judges_every_job(self):
        for text in ("", "null", "{}", '{"server": "true"}', "[1]"):
            self.assertEqual(already_green.jobs_on(text, self.CONFIG), ["relay", "server", "ui"], text)


class WorkflowRatchetTests(unittest.TestCase):
    """ci.yml must read both outputs for every gated job and keep the main-only jobs alone."""

    def setUp(self):
        self.jobs = load_ci()["jobs"]

    def test_every_gated_job_runs_only_when_its_area_is_on_and_it_is_not_already_green(self):
        for name in GATED:
            condition = str(self.jobs[name]["if"])
            self.assertIn("needs.changes.outputs.%s == 'true'" % name, condition, name)
            self.assertIn("needs.changes.outputs.already-green-%s != 'true'" % name, condition, name)

    def test_the_main_only_jobs_are_never_skipped_by_it(self):
        for name in ("windows-com", "binary-smoke-windows"):
            self.assertNotIn("already-green", str(self.jobs[name]["if"]), name)

    def test_changes_exposes_one_already_green_flag_per_gated_job_and_the_list(self):
        outputs = self.jobs["changes"]["outputs"]
        for name in GATED:
            self.assertIn("steps.green.outputs.already-green-%s" % name, outputs["already-green-" + name])
        self.assertIn("steps.green.outputs.already-green-jobs", outputs["already-green-jobs"])

    def test_changes_has_the_narrowest_permissions_the_api_call_needs(self):
        self.assertEqual(self.jobs["changes"]["permissions"], {"contents": "read", "actions": "read"})

    def test_changes_fetches_the_merge_parents_before_it_decides(self):
        steps = self.jobs["changes"]["steps"]
        ids = [s.get("id") for s in steps]
        fetch = next(i for i, s in enumerate(steps) if "--depth=2" in s.get("run", ""))
        self.assertLess(ids.index("areas"), fetch)
        self.assertLess(fetch, ids.index("green"))

    def test_the_green_step_gets_the_token_by_environment_and_never_by_argument(self):
        step = next(s for s in self.jobs["changes"]["steps"] if s.get("id") == "green")
        self.assertEqual(step["env"]["GITHUB_TOKEN"], "${{ github.token }}")
        self.assertNotIn("github.token", step["run"])
        self.assertIn("already_green.py", step["run"])

    def test_ci_ok_logs_which_jobs_were_skipped_as_already_green(self):
        step = self.jobs["ci-ok"]["steps"][0]
        self.assertEqual(step["env"]["SKIPPED_GREEN"], "${{ needs.changes.outputs.already-green-jobs }}")
        self.assertIn("SKIPPED_GREEN", step["run"])

    def test_ci_ok_needs_changes_and_still_ignores_skips(self):
        self.assertIn("changes", self.jobs["ci-ok"]["needs"])
        self.assertNotIn("'skipped'", json.dumps(self.jobs["ci-ok"]))


if __name__ == "__main__":
    unittest.main()
