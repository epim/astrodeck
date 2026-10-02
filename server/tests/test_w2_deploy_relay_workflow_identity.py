# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The deploy-relay.yml workflow's own deploy path carries build identity
too, and never regains the push trigger that used to make it an automatic,
unverified deploy (#486).

Before this, the workflow's deploy job ran a bare `flyctl deploy
--remote-only`, so an image built this way had `RELAY_BUILD_VERSION` and
`RELAY_BUILD_COMMIT` both empty, and `/healthz` answered "unknown" for each.
Nothing checked that the deploy it just ran reached the commit that triggered
it. `scripts/deploy_relay.ps1` already passed both as `--build-arg` and
polled `/healthz` until it agreed; this static test holds the Actions path
to the same two things, since a YAML workflow file has no test runner of its
own to execute it against Fly.

This reads the workflow file as YAML and text -- it does not run the
workflow (GitHub Actions is not available in this suite), and it does not
call Fly or the network.

MUTATION "flyctl deploy loses its build args", 2026-09-30, run in a byte
backup of this worktree (sha256-verified restore): the deploy step's `run`
reverted to the bare `flyctl deploy --remote-only` with no `--build-arg`
lines. 1 failed, test_the_deploy_step_passes_the_build_identity_args:
    AssertionError: the deploy job's deploy step does not pass
    --build-arg RELAY_BUILD_COMMIT=...; the image it builds would report
    /healthz commit "unknown" (#486)
Mutant text confirmed absent after restore (grep -c "flyctl deploy --remote-only$" .github/workflows/deploy-relay.yml -> the bare, no-build-arg form == 0 lines).
"""
from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOW = (Path(__file__).resolve().parents[2] / ".github" / "workflows"
             / "deploy-relay.yml")


def _load():
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _deploy_job_steps():
    data = _load()
    return data["jobs"]["deploy"]["steps"]


def test_workflow_file_exists_and_has_a_deploy_job():
    """Control: if this fails, every other case here grades nothing."""
    assert _WORKFLOW.is_file(), f"missing {_WORKFLOW}"
    data = _load()
    assert "deploy" in data["jobs"], "no 'deploy' job in deploy-relay.yml"


def test_the_push_trigger_is_not_present():
    """#486's warning: a push trigger on relay/** would make this an
    automatic deploy of whatever the build-identity decision below produces,
    and the plan says never to re-add it (removed in ae12bcad)."""
    data = _load()
    # YAML parses the bare key `on` as the boolean True unless quoted; PyYAML
    # keeps it as the string key "on" only when written that way, so accept
    # either spelling the file might use.
    triggers = data.get("on", data.get(True, {})) or {}
    assert "push" not in triggers, (
        "deploy-relay.yml has regained a push trigger; #486's deploy job "
        "must stay workflow_dispatch-only")
    assert "workflow_dispatch" in triggers, (
        "deploy-relay.yml lost its manual dispatch trigger entirely")


def test_the_deploy_step_passes_the_build_identity_args():
    """Same two `--build-arg` keys `scripts/deploy_relay.ps1` passes, so an
    Actions-driven deploy produces an image whose /healthz does not answer
    "unknown"."""
    steps = _deploy_job_steps()
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "flyctl deploy" in runs, "no flyctl deploy call in the deploy job"
    assert "--build-arg" in runs and "RELAY_BUILD_COMMIT=" in runs, (
        "the deploy job's deploy step does not pass --build-arg "
        "RELAY_BUILD_COMMIT=...; the image it builds would report /healthz "
        "commit \"unknown\" (#486)")
    assert "RELAY_BUILD_VERSION=" in runs, (
        "the deploy job's deploy step does not pass --build-arg "
        "RELAY_BUILD_VERSION=...; the image it builds would report "
        "/healthz version \"unknown\" (#486)")


def test_a_step_verifies_healthz_reports_the_built_commit():
    """flyctl's own exit code only says the rollout finished, not which code
    is serving (deploy_relay.ps1's header comment); this job must ask the
    relay itself, the same way."""
    steps = _deploy_job_steps()
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "healthz" in runs, (
        "no step in the deploy job reads /healthz back; a deploy that "
        "finished but shipped the wrong code (or no identity) would pass "
        "silently (#486)")
    assert "commit" in runs, (
        "the /healthz step does not appear to compare the reported commit")
