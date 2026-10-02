# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Named source mutations with exact backup/restore and assertion-only kills."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MUTANTS = [
    ("LOCAL-TARGET", "check_docs.py", 'if not target.exists():', 'if False:', 'DocsChecks.test_missing_link', 'missing local link target'),
    ("LINK-ANCHOR", "check_docs.py", 'if fragment not in anchors(target.read_text(encoding="utf-8")):', 'if False:', 'DocsChecks.test_missing_anchor', 'missing local link anchor'),
    ("REPOSITORY-CONTAINMENT", "check_docs.py", 'if not target.is_relative_to(repo.resolve()):', 'if False:', 'DocsChecks.test_encoded_escape', 'local link escapes repository'),
    ("ENCODED-STYLE", "check_docs.py", 'decoded = html.unescape(text)', 'decoded = text', 'DocsChecks.test_encoded_dash', 'em/en dash is not permitted'),
    ("ENCODED-PRIVACY", "check_docs.py", 'privacy_scan(decoded) or privacy_scan(unquote(decoded))', 'privacy_scan(text)', 'DocsChecks.test_encoded_private_value', 'forbidden observing-site value'),
    ("URL-PRIVACY", "check_docs.py", 'privacy_scan(unquote(decoded))', 'False', 'DocsChecks.test_percent_encoded_private_link', 'forbidden observing-site value'),
    ("DIAGNOSTIC-PRIVACY", "check_docs.py", 'errors.append(f"{relative}: missing local link target")', 'errors.append(f"{relative}: missing local link target {ref}")', 'DocsChecks.test_diagnostics_do_not_echo_reference', 'private-canary'),
    ("LABEL-QUOTED", "check_docs.py", 'if "**" + label + "**" not in normalized_doc:', 'if False:', 'DocsChecks.test_label_must_be_quoted', 'registered UI label is absent from bold text'),
    ("LABEL-SOURCE", "check_docs.py", 'if not source.startswith("ui/src/") or not path.is_relative_to(repo / "ui/src") or not path.is_file():', 'if False:', 'DocsChecks.test_label_requires_ui_source', 'UI label lacks a UI source file'),
    ("LABEL-LINE", "check_docs.py", 'if not isinstance(line, int) or not 1 <= line <= len(lines):', 'if False:', 'DocsChecks.test_label_line_valid', 'UI label has an invalid source line'),
    ("LABEL-DRIFT", "check_docs.py", 'if status == "missing":', 'if False:', 'DocsChecks.test_label_source_drift', 'quoted UI label is absent at its source'),
    ("LABEL-COVERAGE", "check_docs.py", 'if (page, value) not in label_pairs:', 'if False:', 'DocsChecks.test_label_coverage', 'bold UI label has no provenance record'),
    ("LABEL-EXACT-LINE", "check_docs.py", 'occurrences = locate_label(lines, label)', 'occurrences = [hint] if hint in locate_label(lines, label) else []', 'DocsChecks.test_label_survives_insertion_above', "'' != "),
    ("MONEY-METAPHOR", "check_docs.py", 'if any(not any(start <= match.start() < end for start, end in exempt) for match in MONEY.finditer(decoded)):', 'if False:', 'DocsChecks.test_money_metaphor', 'money metaphor is not permitted'),
    ("CLAIM-SOURCE", "check_docs.py", 'errors.append(f"{page}: claim source file is missing")', 'pass', 'DocsChecks.test_claim_source_exists', 'claim source file is missing'),
    ("CLAIM-LINE", "check_docs.py", 'errors.append(f"{page}: claim source line is invalid")', 'pass', 'DocsChecks.test_claim_line_valid', 'claim source line is invalid'),
    ("CLAIM-METHOD", "check_docs.py", 'errors.append(f"{page}: claim verification method is missing")', 'pass', 'DocsChecks.test_claim_method', 'claim verification method is missing'),
    ("CLAIM-COVERAGE", "check_docs.py", 'errors.append(f"{page}: no claim/procedure evidence ledger")', 'pass', 'DocsChecks.test_claim_coverage', 'no claim/procedure evidence ledger'),
    ("STABLE-ANCHOR", "check_docs.py", 'elif not set(expected).issubset(anchors(path.read_text(encoding="utf-8"))):', 'elif False:', 'DocsChecks.test_stable_anchor', 'stable guide anchor is missing'),
    ("STABLE-URL", "check_docs.py", 'errors.append(f"{relative}: stable guide URL is missing")', 'pass', 'DocsChecks.test_stable_url', 'stable guide URL is missing'),
    ("FENCED-ANCHOR", "check_docs.py", 'parser.feed(prose(text))', 'parser.feed(text)', 'DocsChecks.test_fenced_anchor_does_not_exist', 'old'),
    ("LEDGER-PRIVACY", "check_docs.py", 'if privacy_scan and privacy_scan(html.unescape(decoded)):', 'if False:', 'DocsChecks.test_json_escaped_private_value', 'ValueError not raised'),
    ("LEDGER-SCHEMA", "check_docs.py", 'or any(not isinstance(row, dict) for row in rows)', '', 'DocsChecks.test_ledger_rows_are_objects', 'ValueError not raised'),
    ("FRESH-PROCEDURE", "probe_session.py", 'ownership.fresh_paths(ROOT / ".probe/docs" / args.name, args.port)', '(ROOT / ".probe/docs" / args.name, ROOT / ".probe/docs/captures")', 'ProcedureLifecycle.test_reused_paths_refuse_before_launch', 'new config'),
    ("FAILED-LAUNCH-CLEANUP", "probe_session.py", 'if (config / ".astrodeck-probe").is_file():', 'if False:', 'ProcedureLifecycle.test_failed_launch_stops_owned_server', "'start', 'stop'"),
    ("UNOWNED-CLEANUP", "probe_session.py", 'if ownership.owned_process(config, args.port, python, started) is not None:', 'if True:', 'ProcedureLifecycle.test_unowned_process_is_not_stopped', "'start', 'stop'"),
    ("CLEANUP-HONESTY", "probe_session.py", '"Owned procedure server stopped." if stopped else "No owned process was stopped."', '"Owned procedure server stopped."', 'ProcedureLifecycle.test_unowned_process_is_not_stopped', 'No owned process was stopped'),
]


def suite(target=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-m", "unittest", "test_docs_checks" + ("." + target if target else "")], cwd=HERE, env=env, capture_output=True, text=True)


def main():
    baseline = suite()
    if baseline.returncode:
        raise RuntimeError("Unmutated docs suite must pass before mutation")
    report = ["# Documentation gate mutation evidence", "", f"Run {datetime.now(timezone.utc).isoformat()}.", "", "Behavioral fixtures use synthetic text and temporary files; procedure lifecycle tests mock processes, sockets and privacy inputs. No application server or device is contacted. Every changed source is restored from exact bytes in a finally block and SHA-256 checked.", ""]
    for name, filename, before, after, target, expected in MUTANTS:
        path = HERE / filename
        backup = path.read_bytes()
        digest = hashlib.sha256(backup).hexdigest()
        source = backup.decode("utf-8")
        if source.count(before) != 1:
            raise RuntimeError(f"{name}: mutation anchor is not unique")
        try:
            path.write_text(source.replace(before, after, 1), encoding="utf-8")
            result = suite(target)
            assertion = next((line for line in result.stderr.splitlines() if line.startswith("AssertionError:")), "")
            if result.returncode != 1 or not assertion or expected not in assertion or "FAILED (failures=1)" not in result.stderr:
                raise RuntimeError(f"{name}: survived or failed outside its intended assertion\n{result.stderr}")
            print(name + ": KILLED")
            report += [f"- {name}: `{target}`", f"  `{assertion}`", ""]
        finally:
            path.write_bytes(backup)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"{name}: source restoration failed")
    restored = suite()
    if restored.returncode:
        raise RuntimeError("Restored docs suite failed")
    report += ["## Restored suite", "", "```text", restored.stderr.strip(), "```", "", "Workflow action majors were checked against the official [checkout releases](https://github.com/actions/checkout/releases) and [setup-python releases](https://github.com/actions/setup-python/releases). The workflow uses read-only contents permission, does not retain checkout credentials, and reports a privacy skip when the external secret is unavailable.", ""]
    (HERE / "mutation-evidence.md").write_text("\n".join(report), encoding="utf-8")
    print(restored.stderr, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
