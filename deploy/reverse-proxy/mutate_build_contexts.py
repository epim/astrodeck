# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Named context-guard mutants; fixture assertions, exact-byte restore, green rerun."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CHECKER = "deploy/reverse-proxy/check_build_contexts.py"
MUTANTS = [
    ("COPY-MISSING", CHECKER, 'if not matches:', 'if False:', 'test_missing_copy_fails', 'COPY source is missing'),
    ("COPY-IGNORED", CHECKER, 'if not ignored(relative, patterns):', 'if True:', 'test_dockerignore_excluded_fails', 'COPY source is excluded'),
    ("COPY-STAGE", CHECKER, 'if source_stage not in previous and not (source_stage.isdecimal() and int(source_stage) < len(previous)):', 'if False:', 'test_unknown_stage_fails', 'COPY references an unknown'),
    ("COPY-UNSUPPORTED", CHECKER, 'raise ValueError("unsupported COPY flag")', 'pass', 'test_unhandled_syntax_fails_closed', 'unsupported COPY flag'),
    ("COPY-TAB", CHECKER, 'parts = instruction.split(None, 1)', 'parts = instruction.split(" ", 1)', 'test_tab_delimited_copy_is_checked', 'COPY source is missing'),
    ("IGNORE-PRECEDENCE", CHECKER, 'specific if specific.is_file() else build.context / ".dockerignore"', 'build.context / ".dockerignore"', 'test_specific_ignore_overrides_context_ignore', 'COPY source is excluded'),
    ("IGNORE-NEGATION", CHECKER, 'result = not negate', 'result = True', 'test_ignore_exception_and_zero_directory_glob', 'True is not false'),
    ("IGNORE-SEGMENT", CHECKER, '"[^/]*" if c == "*"', '".*" if c == "*"', 'test_ignore_wildcard_does_not_cross_directory', 'False is not true'),
    ("COPY-RECURSIVE", CHECKER, 'if not recursive or part != "**":', 'if part != "**":', 'test_copy_recursive_glob_needs_review', 'unsupported recursive path pattern'),
    ("RELAY-CONTEXT", "deploy/reverse-proxy/docker-compose.relay.yml", 'context: ../../relay\n      dockerfile: Dockerfile', 'context: ../..\n      dockerfile: relay/Dockerfile', 'test_real_compose_and_fly_contexts', 'Lists differ'),
]

def run(target=None):
    cmd = [sys.executable, "-B", "-m", "unittest", "test_release_build_contexts" + (".BuildContexts." + target if target else "")]
    return subprocess.run(cmd,cwd=ROOT/"server/tests",text=True,capture_output=True)

def main():
    baseline=run()
    if baseline.returncode:
        raise RuntimeError("Unmutated context suite must pass first")
    evidence=["# Build-context mutation evidence", "", "Run " + datetime.now(timezone.utc).isoformat(), "", "Each fixture is local. No Docker daemon or network is used. Mutations are killed by the named assertion, then exact source bytes are restored in finally and SHA-256 checked.", ""]
    for name,relative,before,after,target,expected in MUTANTS:
        path=ROOT/relative
        backup=path.read_bytes()
        source=backup.decode("utf-8").replace("\r\n","\n")
        if source.count(before)!=1:
            raise RuntimeError(name+": ambiguous mutation anchor")
        try:
            path.write_text(source.replace(before,after,1),encoding="utf-8")
            result=run(target)
            assertion=next((s for s in result.stderr.splitlines() if s.startswith("AssertionError:")),"")
            if result.returncode!=1 or expected not in assertion or "FAILED (failures=1)" not in result.stderr:
                raise RuntimeError(name+": survived or failed outside intended assertion\n"+result.stderr)
            evidence += ["- " + name + ": `BuildContexts." + target + "`", "  `" + assertion + "`", ""]
            print(name+": KILLED")
        finally:
            path.write_bytes(backup)
            if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(backup).digest():
                raise RuntimeError(name+": restore failed")
    restored=run()
    if restored.returncode:
        raise RuntimeError("Restored context suite failed")
    evidence += ["## Restored suite", "", "```text", restored.stderr.strip(), "```", ""]
    (ROOT/"deploy/reverse-proxy/build-context-mutations.md").write_text("\n".join(evidence),encoding="utf-8")
    print(restored.stderr,end="")

if __name__=="__main__":
    main()
