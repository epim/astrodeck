# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Say which jobs a push to main may skip because the pull request already ran them (#1260).

    python tools/ci/already_green.py --event <name> --ref <ref> --head <sha> --areas-on-json <json>

where <json> is the `areas` step's outputs. It prints one `already-green-<job>=true|false` line per job in tools/ci/areas.json,
plus `already-green-jobs=<comma list>`, which the `changes` job appends to
$GITHUB_OUTPUT. The reasoning, one line per job with the pull request run's URL
for a skip, goes to stderr.

A job is already green only when ALL of these hold:
  - the event is a push to refs/heads/main;
  - HEAD is a merge commit with exactly two parents (a squash merge or a direct
    push has one);
  - HEAD^{tree} equals HEAD^2^{tree}: the pull request branch already held what
    main held, so the tree its run tested is the tree now on main;
  - the API lists a completed ci.yml run for event pull_request whose head_sha is
    HEAD^2 (when several exist, the newest by run number decides);
  - in that run's latest attempt the same job concluded success. A matrix job
    needs every leg to be success. A job the run skipped is not green.

The rule is fail open: a missing token, an API error or a malformed answer,
a job that is not in the answer, a run still in progress: the job runs. A job
that does not run because of this tool is one that demonstrably passed on this
exact tree, never one the tool could not judge.

Standard library only: it runs before anything is installed on the runner.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

import changed_areas

WORKFLOW = "ci.yml"
MAIN_REF = "refs/heads/main"
OUTPUT_PREFIX = "already-green-"
API_ROOT = "https://api.github.com"

# Jobs a push to main must run even when the pull request's run was green,
# because that run did not run them in full. "ui": a pull request runs only the
# test files its change can reach (#1258), so main runs the whole UI suite.
NEVER_ALREADY_GREEN: tuple[str, ...] = ("ui",)

Fetch = Callable[[str], object]


class ApiError(Exception):
    """A GitHub API call that did not produce an answer. The text never holds the token."""


@dataclass(frozen=True)
class MergeFacts:
    event: str
    ref: str
    parents: tuple[str, ...]
    head_tree: str
    second_parent_tree: str


@dataclass(frozen=True)
class Verdict:
    green: bool
    reason: str
    run_url: str = ""


def output_name(job: str) -> str:
    return OUTPUT_PREFIX + job


def merge_refusal(facts: MergeFacts) -> str | None:
    """Why the pull request's run cannot speak for this push, or None when it can."""
    if facts.event != "push":
        return "event is %s, not a push to main" % (facts.event or "unknown")
    if facts.ref != MAIN_REF:
        return "ref is %s, not %s" % (facts.ref or "unknown", MAIN_REF)
    if len(facts.parents) != 2:
        return "head is not a two-parent merge (%d parents)" % len(facts.parents)
    if not facts.head_tree or facts.head_tree != facts.second_parent_tree:
        return "tree differs from the pull request head"
    return None


def runs_path(repo: str, pr_head: str) -> str:
    return "/repos/%s/actions/workflows/%s/runs?event=pull_request&head_sha=%s&per_page=100" % (
        repo, WORKFLOW, pr_head)


def jobs_path(repo: str, run_id: object) -> str:
    return "/repos/%s/actions/runs/%s/jobs?filter=latest&per_page=100" % (repo, run_id)


def pull_request_run(answer: object, pr_head: str) -> tuple[dict | None, str]:
    """The run that speaks for the pull request head, and why there is none.

    Several runs can share one head (a reopened pull request, a second pull
    request from the same branch). The newest by run number decides, completed
    or not: it is the evidence of the last test of this head, and a newer run
    that is still going, or went wrong, outranks an older green one. A re-run
    of a run is the same run with a higher attempt, and the API reports its
    latest attempt, so the rule needs no attempt logic of its own.
    """
    runs = [r for r in answer["workflow_runs"]
            if r.get("event") == "pull_request" and r.get("head_sha") == pr_head]
    if not runs:
        return None, "no ci.yml pull request run for %s" % pr_head[:12]
    newest = max(runs, key=lambda r: r["run_number"])
    if newest.get("status") != "completed":
        return None, "pull request run %s is %s" % (newest["run_number"], newest.get("status"))
    return newest, ""


def job_refusal(job: str, run: dict, answer: object) -> str | None:
    """Why this job's result in the run is not a pass, or None when every leg passed."""
    jobs = answer["jobs"]
    if len(jobs) != answer["total_count"]:
        return "the run's job list is incomplete"
    legs = [j for j in jobs if j["name"] == job or j["name"].startswith(job + " (")]
    if not legs:
        return "the pull request run has no job %s" % job
    for leg in legs:
        if leg.get("run_attempt") != run.get("run_attempt"):
            return "%s is from an earlier attempt" % leg["name"]
        if leg.get("status") != "completed" or leg.get("conclusion") != "success":
            return "%s: %s" % (leg["name"], leg.get("conclusion") or leg.get("status"))
    return None


def decide(jobs: Collection[str], facts: MergeFacts, fetch: Fetch, *, repo: str,
           never: Collection[str] = NEVER_ALREADY_GREEN) -> dict[str, Verdict]:
    """One verdict per job. `fetch(path)` returns the parsed JSON answer or raises."""
    refusal = merge_refusal(facts)
    if refusal is None:
        verdicts = _judge_against_run(jobs, facts, fetch, repo)
    else:
        verdicts = {job: Verdict(False, refusal) for job in jobs}
    for job in never:
        if job in verdicts:
            verdicts[job] = Verdict(False, "the pull request runs only part of this job")
    return verdicts


def _judge_against_run(jobs: Collection[str], facts: MergeFacts, fetch: Fetch,
                       repo: str) -> dict[str, Verdict]:
    try:
        run, why = pull_request_run(fetch(runs_path(repo, facts.parents[1])), facts.parents[1])
        if run is None:
            return {job: Verdict(False, why) for job in jobs}
        answer = fetch(jobs_path(repo, run["id"]))
        url = str(run.get("html_url", ""))
        verdicts = {}
        for job in jobs:
            reason = job_refusal(job, run, answer)
            if reason is None:
                verdicts[job] = Verdict(True, "passed in pull request run %s" % run["run_number"], url)
            else:
                verdicts[job] = Verdict(False, reason, url)
        return verdicts
    except ApiError as e:
        return {job: Verdict(False, str(e)) for job in jobs}
    except Exception as e:  # noqa: BLE001 - fail open: whatever went wrong, the job runs
        return {job: Verdict(False, "could not read the answer (%s)" % type(e).__name__)
                for job in jobs}


def git_out(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def gather_facts(event: str, ref: str, head: str) -> MergeFacts:
    """Read the head commit's parents and trees from the repository in the cwd.

    A checkout one commit deep shows no parents, so the head is not a merge to
    it; the workflow fetches depth 2 first, which brings both parents.
    """
    parents = tuple(git_out("rev-list", "--parents", "-n", "1", head).split()[1:])
    second = git_out("rev-parse", parents[1] + "^{tree}") if len(parents) == 2 else ""
    return MergeFacts(event, ref, parents, git_out("rev-parse", head + "^{tree}"), second)


def github_fetch(token: str, api_root: str = API_ROOT) -> Fetch:
    """A GET against the GitHub API. The token goes into the request header and nowhere else."""
    def fetch(path: str) -> object:
        if not token:
            raise ApiError("GITHUB_TOKEN is not set")
        request = urllib.request.Request(api_root + path, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrodeck-ci-already-green",
        })
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raise ApiError("GitHub API answered HTTP %d" % e.code) from None
        except (OSError, ValueError) as e:
            raise ApiError("GitHub API call failed (%s)" % type(e).__name__) from None
    return fetch


def jobs_on(areas_on_json: str, config: dict) -> list[str]:
    """The jobs whose area the `areas` step turned on; every job when that is unreadable."""
    try:
        outputs = json.loads(areas_on_json)
        return sorted(name for name in config["areas"] if outputs[name] == "true")
    except (ValueError, KeyError, TypeError):
        return sorted(config["areas"])


def verdicts_for(args: argparse.Namespace, config: dict) -> dict[str, Verdict]:
    all_jobs = sorted(config["areas"])
    try:
        running = jobs_on(args.areas_on_json, config)
        facts = gather_facts(args.event, args.ref, args.head)
        fetch = github_fetch(os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_API_URL", API_ROOT))
        verdicts = decide(running, facts, fetch, repo=args.repo)
    except Exception as e:  # noqa: BLE001 - fail open: whatever went wrong, every job runs
        return {job: Verdict(False, "already_green failed (%s)" % type(e).__name__) for job in all_jobs}
    for job in all_jobs:
        verdicts.setdefault(job, Verdict(False, "its area did not change"))
    return verdicts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True, help="github.event_name")
    parser.add_argument("--ref", default="", help="github.ref")
    parser.add_argument("--head", default="HEAD", help="the pushed commit")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/name")
    parser.add_argument("--areas-on-json", default="", help="steps.areas.outputs as JSON; jobs whose area is off are not judged")
    parser.add_argument("--areas", type=Path, default=changed_areas.DEFAULT_AREAS)
    args = parser.parse_args(argv)

    config = changed_areas.load_config(args.areas)
    verdicts = verdicts_for(args, config)
    for job in sorted(verdicts):
        v = verdicts[job]
        if v.green:
            print("%s: skipped, already green (%s): %s" % (job, v.reason, v.run_url), file=sys.stderr)
        else:
            print("%s: runs (%s)" % (job, v.reason), file=sys.stderr)
        print("%s=%s" % (output_name(job), "true" if v.green else "false"))
    skipped = [job for job in sorted(verdicts) if verdicts[job].green]
    print("%sjobs=%s" % (OUTPUT_PREFIX, ",".join(skipped)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
