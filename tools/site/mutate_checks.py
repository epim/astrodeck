# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Prove selected site gates fail closed, restoring exact file bytes after each mutant."""
from pathlib import Path
import os
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
MUTANTS = [
    ("LINK-EXISTS", "check_site.py", "if not target.is_file():", "if False:  # LINK-EXISTS", "SiteChecks.test_missing_asset"),
    ("FRAGMENT-EXISTS", "check_site.py", "elif fragment and target.suffix", "elif False and fragment and target.suffix", "SiteChecks.test_missing_fragment"),
    ("STYLE-DASH", "check_site.py", 'if "\\u2013" in visible or "\\u2014" in visible:', "if False:  # STYLE-DASH", "SiteChecks.test_dash_entity"),
    ("HTML-PARSER", "check_site.py", "in parser.errors:", "in []:  # HTML-PARSER", "SiteChecks.test_invalid_html"),
    ("PRIVACY-SVG", "check_site.py", 'if scanner["scan_text"](html.unescape(text), patterns) or scanner["scan_text"](text, patterns):', "if False:  # PRIVACY-SVG", "SiteChecks.test_svg_privacy"),
    ("SIM-MARKER-PORT", "capture_sim.py", 'if marker.get("port") != port or Path(marker.get("dir", "")).resolve() != config:', "if False:  # SIM-MARKER-PORT", "CaptureBoundary.test_refuses_wrong_probe_port"),
    ("CSS-RESOURCES", "check_site.py", "refs, problems = css_references(css_source)", "refs, problems = [], []", "SiteChecks.test_css_uppercase_url"),
    ("ENTITY-PRIVACY", "check_site.py", "html.unescape(text)", "text", "SiteChecks.test_encoded_privacy"),
    ("DIAGNOSTIC-PRIVACY", "check_site.py", 'errors.append(f"{rel}: missing local reference")', 'errors.append(f"{rel}: missing local reference {ref}")', "SiteChecks.test_diagnostics_do_not_echo_references"),
    ("MONEY-SITE", "check_site.py", "if MONEY.search(masked):", "if False:  # MONEY-SITE", "SiteChecks.test_money_metaphor"),
    ("FRESH-CONFIG", "capture_sim.py", "if config.exists() or captures.exists():", "if False:  # FRESH-CONFIG", "CaptureBoundary.test_refuses_reused_config"),
    ("FAILED-LAUNCH-CLEANUP", "capture_sim.py", 'if (config / ".astrodeck-probe").is_file():', "if False:  # FAILED-LAUNCH-CLEANUP", "CaptureLifecycle.test_failed_launch_stops_owned_process"),
    ("PARENT-PID-REUSE", "capture_sim.py", 'if info is None or info["created"] > child_created:', "if info is None:", "CaptureLifecycle.test_refuses_parent_pid_reuse"),
    ("JS-EXIT", "../../.github/workflows/pages.yml", 'node --check "$file" || exit 1', 'node --check "$file" || true', "WorkflowChecks.test_javascript_syntax_failure_stops_step"),

    ("MONITOR-ONLY-PLAN", "capture_sim.py", '[item for item in CAPTURE_PLAN if item[0] == MONITOR_FILE] if monitor_only else list(CAPTURE_PLAN)', 'list(CAPTURE_PLAN)', "MonitorCapture.test_monitor_plan_excludes_other_images"),
    ("CAPTURE-PROVENANCE", "capture_sim.py", 'record.setdefault("provenance", copy.deepcopy(inherited))', 'record.setdefault("provenance", copy.deepcopy(context))', "MonitorCapture.test_old_images_keep_old_provenance"),
    ("POINTING-READY", "capture_sim.py", 'ra_error < 0.01 and dec_error < 0.1', 'dec_error < 0.1', "MonitorCapture.test_pointing_mismatch_is_not_capture_ready"),

    ("HANLE-DECLINATION", "capture_sim.py", '"elevation_m": 0.0}, 20.0)', '"elevation_m": 0.0}, -45.0)', "MonitorCapture.test_hanle_target_is_above_horizon"),


]


def main():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    report = ["# Site check mutation evidence", "", "Each source file was restored from its byte backup in a finally block.", ""]
    for name, filename, before, after, target in MUTANTS:
        path = HERE / filename
        backup = path.read_bytes()
        source = backup.decode("utf-8")
        if before not in source:
            raise RuntimeError(f"{name}: source anchor missing")
        try:
            path.write_bytes(source.replace(before, after, 1).encode("utf-8"))
            result = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_site_checks." + target],
                                    cwd=HERE, env=env, capture_output=True, text=True)
            assertion = next((line.strip() for line in result.stderr.splitlines() if line.startswith("AssertionError:")), "")
            if result.returncode == 0 or not assertion:
                raise RuntimeError(f"{name}: survived or failed for a reason other than its assertion")
            print(f"{name}: KILLED; {assertion}")
            report += [f"- {name}: `{assertion}`", f"  Test: `{target}`."]
        finally:
            path.write_bytes(backup)
            if path.read_bytes() != backup:
                raise RuntimeError(f"{name}: byte restoration failed")
    clean = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_site_checks"], cwd=HERE, env=env, capture_output=True, text=True)
    print(clean.stderr, end="")
    if clean.returncode:
        return clean.returncode
    count = re.search(r"Ran (\d+) tests", clean.stderr).group(1)
    report += ["", f"Unmutated suite: {count} tests passed. No mutant source remains.", ""]
    (HERE / "mutation-evidence.md").write_text("\n".join(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
