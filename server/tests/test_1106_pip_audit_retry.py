# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The dependency audit retries an outage and still fails a finding (#1106, #823).

On 2026-10-20 the relay check on PR #1105 failed its ``pip-audit`` step with no
vulnerability found: PyPI answered 503 for a package's JSON record. A red check
that means "the index was down" teaches people to re-run red checks, which is
how a real finding gets re-run until it goes away.

``tools/pip_audit_retry.py`` runs the audit up to three times and separates
three outcomes: a finding fails at once (retrying cannot change it), an index or
network outage is retried and then passes with a loud warning, and any other
failure fails. Workflow YAML cannot be unit-tested, so the logic lives in that
script and these tests feed it the transcripts pip-audit prints.

The workflow files are read back too: the relay deploy gate must call the
script, and the Pages publish group must cancel a superseded publish (#823).

NAMED MUTANT "treat a 503 as a finding": in ``classify`` the outage branch
returns FINDING. ``test_an_index_outage_after_three_tries_warns_and_passes``
then fails, because the run exits 1 after one attempt instead of 0 after three.

Nothing here contacts PyPI: the audit command is injected or is a stub script.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "pip_audit_retry.py"

# The transcript the 2026-10-20 failure printed (run 39493467226), shortened to
# its last frames.
OUTAGE_503 = """\
Traceback (most recent call last):
  File "/opt/hostedtoolcache/Python/3.12.7/x64/lib/python3.12/site-packages/pip_audit/_service/pypi.py", line 62, in get_dependency
    response.raise_for_status()
requests.exceptions.HTTPError: 503 Server Error: Service Unavailable for url: https://pypi.org/pypi/astrodeck-relay/0.1.0/json

During handling of the above exception, another exception occurred:

pip_audit._service.interface.ServiceError
"""

OUTAGE_CONNECTION = """\
requests.exceptions.ConnectionError: HTTPSConnectionPool(host='pypi.org', port=443): Max retries exceeded with url: /pypi/httpx/0.28.1/json (Caused by NewConnectionError('Failed to establish a new connection: [Errno -3] Temporary failure in name resolution'))
pip_audit._service.interface.ServiceError
"""

FINDING = """\
Found 1 known vulnerability in 1 package
Name    Version ID             Fix Versions
------- ------- -------------- ------------
example 1.0.0   PYSEC-2099-001 1.0.1
"""

# A finding and an outage in one transcript: the finding is the news.
FINDING_THEN_OUTAGE = FINDING + OUTAGE_503

NOT_FOUND_404 = """\
requests.exceptions.HTTPError: 404 Client Error: Not Found for url: https://pypi.org/pypi/example/9.9.9/json
pip_audit._service.interface.ServiceError
"""

UNRECOGNISED = "pip-audit: error: unrecognized arguments: --frobnicate\n"

CLEAN = "No known vulnerabilities found\n"


def _load():
    """Import tools/pip_audit_retry.py by path: it is a script, not a package."""
    spec = importlib.util.spec_from_file_location("pip_audit_retry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Audit:
    """A stand-in for the audit command: answers from a script, records calls."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.sleeps = []

    def run(self, command):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def _go(answers, capsys, attempts=3):
    module = _load()
    audit = Audit(answers)
    code = module.run(["pip-audit"], attempts=attempts, delay=7.0,
                      runner=audit.run, sleep=audit.sleep)
    return code, audit, capsys.readouterr().out


def test_a_clean_audit_passes_without_retrying(capsys):
    code, audit, _ = _go([(0, CLEAN)], capsys)
    assert code == 0
    assert audit.calls == 1
    assert audit.sleeps == []


def test_a_finding_fails_at_once_without_retrying(capsys):
    code, audit, out = _go([(1, FINDING)], capsys)
    assert code == 1
    assert audit.calls == 1
    assert "PYSEC-2099-001" in out


def test_a_finding_wins_over_an_outage_in_the_same_transcript(capsys):
    code, audit, _ = _go([(1, FINDING_THEN_OUTAGE)], capsys)
    assert code == 1
    assert audit.calls == 1


def test_an_index_outage_after_three_tries_warns_and_passes(capsys):
    code, audit, out = _go([(1, OUTAGE_503)], capsys)
    assert code == 0
    assert audit.calls == 3
    assert audit.sleeps == [7.0, 7.0]
    assert "WARNING" in out
    assert "not audited" in out
    assert "503" in out


def test_an_outage_that_clears_passes_on_the_retry(capsys):
    code, audit, out = _go([(1, OUTAGE_503), (0, CLEAN)], capsys)
    assert code == 0
    assert audit.calls == 2
    assert "WARNING" not in out


def test_an_outage_then_a_finding_still_fails(capsys):
    code, audit, _ = _go([(1, OUTAGE_503), (1, FINDING)], capsys)
    assert code == 1
    assert audit.calls == 2


def test_a_network_error_is_retried_like_a_5xx(capsys):
    code, audit, out = _go([(1, OUTAGE_CONNECTION)], capsys)
    assert code == 0
    assert audit.calls == 3
    assert "WARNING" in out


def test_a_4xx_from_the_index_is_not_an_outage(capsys):
    code, audit, _ = _go([(1, NOT_FOUND_404)], capsys)
    assert code == 1
    assert audit.calls == 1


def test_an_unrecognised_failure_fails_without_retrying(capsys):
    code, audit, out = _go([(2, UNRECOGNISED)], capsys)
    assert code == 1
    assert audit.calls == 1
    assert "unrecognised" in out


def test_the_attempt_count_is_honoured(capsys):
    code, audit, _ = _go([(1, OUTAGE_503)], capsys, attempts=5)
    assert code == 0
    assert audit.calls == 5


def _stub(tmp_path: Path, transcript: str, code: int) -> list[str]:
    """A command that prints a transcript and exits with a code."""
    fixture = tmp_path / "transcript.txt"
    fixture.write_text(transcript, encoding="utf-8")
    script = tmp_path / "stub.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write(open(sys.argv[1], encoding='utf-8').read())\n"
        "sys.exit(int(sys.argv[2]))\n",
        encoding="utf-8")
    return [sys.executable, str(script), str(fixture), str(code)]


def _cli(tmp_path: Path, transcript: str, code: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--delay", "0", "--",
         *_stub(tmp_path, transcript, code)],
        capture_output=True, text=True, check=False)


def test_the_command_line_passes_an_outage_with_a_warning(tmp_path):
    result = _cli(tmp_path, OUTAGE_503, 1)
    assert result.returncode == 0
    assert "WARNING" in result.stdout


def test_the_command_line_fails_a_finding(tmp_path):
    result = _cli(tmp_path, FINDING, 1)
    assert result.returncode == 1


def _workflow(name: str) -> dict:
    text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def test_the_relay_deploy_gate_runs_the_audit_through_the_retry_script():
    steps = _workflow("deploy-relay.yml")["jobs"]["test"]["steps"]
    audit = [s for s in steps if "pip-audit" in s.get("name", "")]
    assert len(audit) == 1
    run = audit[0]["run"]
    assert "tools/pip_audit_retry.py" in run
    assert not any(line.strip() == "pip-audit" for line in run.splitlines())


def test_a_superseded_pages_publish_is_cancelled():
    concurrency = _workflow("pages.yml")["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "pages-" in concurrency["group"]
