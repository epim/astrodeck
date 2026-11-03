# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Run pip-audit, retrying when the package index is down (#1106).

A dependency audit that fails because PyPI answered 503 has found nothing, yet
it turns the check red and trains people to re-run red checks. This wrapper
keeps the two apart:

* a vulnerability finding fails at once; retrying cannot change it;
* an index or network outage (a 5xx from the index, a refused or timed-out
  connection, a failed name lookup) is retried, up to ``--attempts`` times with
  ``--delay`` seconds between tries; if every try meets an outage, the wrapper
  prints a WARNING that the dependencies were not audited and exits 0;
* any other failure (a 4xx from the index, a bad argument, a crash) fails,
  because an error nobody recognises must not pass as an outage.

A transcript that holds both a finding and an outage is a finding.

Usage, with the audit command after ``--``::

    python tools/pip_audit_retry.py -- pip-audit

Standard library only, so it runs before anything is installed.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from typing import Callable, Sequence

PASS = "pass"
FINDING = "finding"
OUTAGE = "outage"
ERROR = "error"

# pip-audit's own summary line when it reports vulnerabilities.
_FINDING = re.compile(r"^Found \d+ known vulnerabilit", re.MULTILINE)

# A 5xx from the index, or a failure to reach it at all.
_OUTAGE = re.compile(
    r"\b5\d\d Server Error"
    r"|requests\.exceptions\.(?:ConnectionError|Timeout|ConnectTimeout"
    r"|ReadTimeout|ChunkedEncodingError)"
    r"|Max retries exceeded"
    r"|Temporary failure in name resolution",
)

_OUTAGE_DETAIL = re.compile(r"^.*(?:\b5\d\d Server Error|Max retries exceeded"
                            r"|Temporary failure in name resolution"
                            r"|requests\.exceptions\.\w+).*$", re.MULTILINE)

Runner = Callable[[Sequence[str]], "tuple[int, str]"]


def classify(returncode: int, output: str) -> str:
    """Name what one audit run meant: PASS, FINDING, OUTAGE or ERROR."""
    if returncode == 0:
        return PASS
    if _FINDING.search(output):
        return FINDING
    if _OUTAGE.search(output):
        return OUTAGE
    return ERROR


def _run_command(command: Sequence[str]) -> "tuple[int, str]":
    """Run the audit, merging stderr into stdout so one transcript is judged."""
    done = subprocess.run(command, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", check=False)
    return done.returncode, done.stdout


def _outage_detail(output: str) -> str:
    match = _OUTAGE_DETAIL.search(output)
    return match.group(0).strip() if match else "the package index was unreachable"


def run(command: Sequence[str], attempts: int, delay: float,
        runner: Runner = _run_command,
        sleep: Callable[[float], None] = time.sleep) -> int:
    """Audit with retries; return the exit code the workflow step should have."""
    detail = ""
    for attempt in range(1, attempts + 1):
        returncode, output = runner(command)
        print(output, end="" if output.endswith("\n") else "\n")
        verdict = classify(returncode, output)
        if verdict == PASS:
            return 0
        if verdict == FINDING:
            print("pip-audit: a known vulnerability was found; failing.")
            return 1
        if verdict == ERROR:
            print(f"pip-audit: failed for an unrecognised reason "
                  f"(exit {returncode}); failing.")
            return 1
        detail = _outage_detail(output)
        print(f"pip-audit: attempt {attempt} of {attempts} met an index "
              f"outage: {detail}")
        if attempt < attempts:
            sleep(delay)
    print(f"WARNING: the package index was unavailable on all {attempts} "
          f"attempts, so the dependencies were not audited. Last error: "
          f"{detail}. This is an outage, not a vulnerability; re-run the "
          f"check when the index is back.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--attempts", type=int, default=3,
                        help="audit runs before an outage is tolerated")
    parser.add_argument("--delay", type=float, default=20.0,
                        help="seconds to wait between attempts")
    parser.add_argument("command", nargs="+",
                        help="the audit command, after --")
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    return run(args.command, attempts=args.attempts, delay=args.delay)


if __name__ == "__main__":
    sys.exit(main())
