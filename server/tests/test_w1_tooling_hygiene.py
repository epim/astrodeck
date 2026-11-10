# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Test tooling hygiene (#1122 members #740, #802, #776).

Each case drives the real tool module and fails on the old behaviour:

  * #740 `scripts/mutate.py` with too few arguments printed only the `apply`
    usage line, because it indexed the docstring's tail by position.
  * #802 `deploy/reverse-proxy/mutate_build_contexts.py` rewrote a tracked
    evidence file on every run; the report now goes to `.probe/` unless the
    operator passes `--record`.
  * #776 the Tonight Budget fixture recipe rewrote a CRLF working copy with
    LF even when nothing changed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mutate = _load("astrodeck_w1_hygiene_mutate", _ROOT / "scripts" / "mutate.py")
contexts = _load("astrodeck_w1_hygiene_contexts",
                 _ROOT / "deploy" / "reverse-proxy" / "mutate_build_contexts.py")


# ------------------------------------------------------------------- #740

def test_bare_mutate_prints_all_three_usage_lines():
    """A bare `python scripts/mutate.py` must show snapshot, apply and
    restore, not the one line the docstring's tail happened to put at -6.

    MUTATION: `main` raises `SystemExit(__doc__.strip().splitlines()[-6])`
    again. Observed: only the `apply` line is printed.
    """
    with pytest.raises(SystemExit) as exc:
        mutate.main(["mutate.py"])
    message = str(exc.value)
    for command in ("snapshot", "apply", "restore"):
        assert f"python scripts/mutate.py {command}" in message, (
            f"the usage message omits {command!r}:\n{message}")
    assert len(message.splitlines()) == 3, message


# ------------------------------------------------------------------- #802

class _Done:
    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode, self.stderr = returncode, stderr


def _one_mutant_tree(tmp_path: Path, monkeypatch) -> Path:
    """A throwaway tree holding one mutable file and the tracked report, with
    `run` answering as the unittest module would: green unmutated, one named
    assertion failing when mutated."""
    (tmp_path / "deploy" / "reverse-proxy").mkdir(parents=True)
    (tmp_path / "thing.txt").write_text("keep = 1\n", encoding="utf-8")
    tracked = tmp_path / "deploy" / "reverse-proxy" / "build-context-mutations.md"
    tracked.write_text("tracked evidence\n", encoding="utf-8")
    monkeypatch.setattr(contexts, "ROOT", tmp_path)
    monkeypatch.setattr(contexts, "MUTANTS", [
        ("ONE", "thing.txt", "keep = 1", "keep = 2", "test_one", "wrong value")])

    def fake_run(target=None):
        if target is None:
            return _Done(0, "OK")
        return _Done(1, "AssertionError: wrong value\nFAILED (failures=1)")

    monkeypatch.setattr(contexts, "run", fake_run)
    return tracked


def test_the_mutation_harness_leaves_the_tracked_report_alone(tmp_path, monkeypatch):
    """A verifier must not dirty tracked state (#802): by default the report
    goes to `.probe/` and the tracked evidence file keeps its bytes.

    MUTATION: write the report to the tracked path unconditionally. Observed:
    the tracked file's bytes changed and the `.probe/` report was missing.
    """
    tracked = _one_mutant_tree(tmp_path, monkeypatch)
    before = tracked.read_bytes()
    contexts.main([])
    assert tracked.read_bytes() == before, "the harness rewrote a tracked file"
    probe = tmp_path / ".probe" / "build-context-mutations.md"
    assert "ONE" in probe.read_text(encoding="utf-8"), "the report was not written"


def test_the_mutation_harness_records_the_tracked_report_on_request(tmp_path, monkeypatch):
    """`--record` is the explicit way to refresh the committed evidence.

    MUTATION: ignore `--record`. Observed: the tracked file kept its old
    bytes.
    """
    tracked = _one_mutant_tree(tmp_path, monkeypatch)
    contexts.main(["--record"])
    assert "ONE" in tracked.read_text(encoding="utf-8")
    assert not (tmp_path / ".probe").exists()


# ------------------------------------------------------------------- #776

def test_the_budget_recipe_keeps_a_crlf_copy_it_would_not_change(tmp_path):
    """The recipe moves a file outside its owner's list only when the content
    changes (#776): an unchanged CRLF working copy keeps its bytes.

    MUTATION: write unconditionally with LF line endings. Observed: the
    CRLF copy came back as LF.
    """
    from test_h4_budget_for_these_targets import write_fixture_preserving_eol
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(b'{\r\n "a": 1\r\n}\r\n')
    assert write_fixture_preserving_eol(fixture, '{\n "a": 1\n}\n') is False
    assert fixture.read_bytes() == b'{\r\n "a": 1\r\n}\r\n'


def test_the_budget_recipe_writes_a_changed_fixture_in_the_files_own_eol(tmp_path):
    """A real change is written, and a CRLF copy stays CRLF.

    MUTATION: write the changed text with LF. Observed: the CRLF copy became
    LF.
    """
    from test_h4_budget_for_these_targets import write_fixture_preserving_eol
    crlf = tmp_path / "crlf.json"
    crlf.write_bytes(b'{\r\n "a": 1\r\n}\r\n')
    assert write_fixture_preserving_eol(crlf, '{\n "a": 2\n}\n') is True
    assert crlf.read_bytes() == b'{\r\n "a": 2\r\n}\r\n'
    lf = tmp_path / "lf.json"
    lf.write_bytes(b'{\n "a": 1\n}\n')
    assert write_fixture_preserving_eol(lf, '{\n "a": 2\n}\n') is True
    assert lf.read_bytes() == b'{\n "a": 2\n}\n'


# ------------------------------------------------------------------- #802 (release workflow harness)

releasewf = _load("astrodeck_w75_hygiene_releasewf",
                  _ROOT / "tools" / "licence" / "mutate_release_workflow.py")

_WORKFLOW_BYTES = b"name: release\r\nneeds: [release, binaries]\r\n"


def _release_tree(tmp_path: Path, monkeypatch, killed: bool = True):
    """A throwaway tree holding a CRLF workflow and the tracked report, with
    `run` answering as the unittest module would."""
    (tmp_path / "tools" / "licence").mkdir(parents=True)
    workflow = tmp_path / "release.yml"
    workflow.write_bytes(_WORKFLOW_BYTES)
    tracked = tmp_path / "tools" / "licence" / "release-workflow-mutations.md"
    tracked.write_text("tracked evidence\n", encoding="utf-8")
    monkeypatch.setattr(releasewf, "ROOT", tmp_path)
    monkeypatch.setattr(releasewf, "WORKFLOW", workflow)
    monkeypatch.setattr(releasewf, "MUTANTS", [
        ("ONE", "needs: [release, binaries]", "needs: [release]",
         "test_one", "wrong value")])

    def fake_run(target=None):
        if target is None or not killed:
            return _Done(0, "OK")
        return _Done(1, "AssertionError: wrong value\nFAILED (failures=1)")

    monkeypatch.setattr(releasewf, "run", fake_run)
    return workflow, tracked


def test_the_release_harness_leaves_every_tracked_file_as_it_found_it(tmp_path, monkeypatch):
    """A verifier must not dirty tracked state (#802): the report goes to
    `.probe/`, and the mutated workflow comes back byte for byte (CRLF kept).

    MUTATION: write the report to the tracked path unconditionally, or drop
    the restore in `finally`. Observed: a tracked file's bytes changed.
    """
    workflow, tracked = _release_tree(tmp_path, monkeypatch)
    report_before = tracked.read_bytes()
    releasewf.main([])
    assert workflow.read_bytes() == _WORKFLOW_BYTES, "the workflow was left mutated"
    assert tracked.read_bytes() == report_before, "the harness rewrote a tracked file"
    probe = tmp_path / ".probe" / "release-workflow-mutations.md"
    assert "ONE" in probe.read_text(encoding="utf-8"), "the report was not written"


def test_the_release_harness_restores_the_workflow_when_a_mutant_survives(tmp_path, monkeypatch):
    """A mutant that is not killed aborts the run; the abort must not leave
    the workflow mutated.

    MUTATION: restore after the loop instead of in `finally`. Observed: the
    workflow kept `needs: [release]`.
    """
    workflow, tracked = _release_tree(tmp_path, monkeypatch, killed=False)
    with pytest.raises(RuntimeError, match="survived"):
        releasewf.main([])
    assert workflow.read_bytes() == _WORKFLOW_BYTES
    assert not (tmp_path / ".probe").exists()


def test_the_release_harness_records_the_tracked_report_on_request(tmp_path, monkeypatch):
    """`--record` is the explicit way to refresh the committed evidence, and
    it writes LF whatever the platform.

    MUTATION: ignore `--record`. Observed: the tracked file kept its old
    bytes.
    """
    workflow, tracked = _release_tree(tmp_path, monkeypatch)
    releasewf.main(["--record"])
    assert "ONE" in tracked.read_text(encoding="utf-8")
    assert b"\r\n" not in tracked.read_bytes()
    assert workflow.read_bytes() == _WORKFLOW_BYTES
    assert not (tmp_path / ".probe").exists()


def test_the_release_harness_refuses_an_unknown_argument(tmp_path, monkeypatch):
    _release_tree(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        releasewf.main(["--recrod"])


def test_the_context_harness_writes_its_report_with_lf_endings(tmp_path, monkeypatch):
    """Evidence recorded on Windows and on Linux must be the same bytes (#802
    comment, class of #776).

    MUTATION: write the report without `newline="\n"`. Observed on Windows:
    the tracked report held CRLF.
    """
    tracked = _one_mutant_tree(tmp_path, monkeypatch)
    contexts.main(["--record"])
    assert b"\r\n" not in tracked.read_bytes()
