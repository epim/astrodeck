# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The deploy restart step must refuse to be a silent no-op (#103).

Every deploy script up to 0.3.33 found the server to kill by filtering
``Win32_Process`` on ``CommandLine``. On astrotown that property is empty for
those processes, so the filter matched nothing, ``Stop-Process`` never ran,
and the script carried on and "verified" the new release against the server
still running the old one.

These drive ``scripts/lib/RigRestart.ps1`` against a REAL listener on this
machine rather than asserting on its source text, because the defect was
never in what the script said -- it was in what PowerShell actually matched.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Get-NetTCPConnection is Windows-only")

_HELPER = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "RigRestart.ps1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run_ps(body: str, timeout: int = 120) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        $ErrorActionPreference = 'Stop'
        . '{_HELPER.as_posix()}'
        {body}
    """)
    # -ExecutionPolicy Bypass because dot-sourcing a repo .ps1 is otherwise
    # refused by the default machine policy, and that refusal exits non-zero
    # too -- which would satisfy a test that only checked the exit code. The
    # cases below assert on the MESSAGE for exactly that reason.
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout)


def _listener_snippet(port: int) -> str:
    """A PowerShell scriptblock that starts a throwaway listener on ``port``."""
    py = sys.executable.replace("\\", "/")
    return (f"{{ Start-Process -FilePath '{py}' -ArgumentList "
            f"'-m','http.server','{port}','--bind','127.0.0.1' "
            f"-WindowStyle Hidden }}")


def test_the_helper_refuses_when_nothing_is_listening():
    """The whole defect in one case: a restart that matches nothing must FAIL,
    not print progress and continue.

    MUTATION: delete the ``if (-not $before) { throw ... }`` guard from
    Restart-RigServer. Observed under it: the command exits 0 with no
    'nothing is listening' text, and this case fails on both assertions.
    """
    port = _free_port()          # bound then released: nothing is there now
    out = _run_ps(f"Restart-RigServer -Port {port} -Start {{ }}")

    assert out.returncode != 0, (
        "a restart with nothing to restart exited 0 - this is exactly the "
        f"silent no-op of #103. stdout={out.stdout!r}")
    assert "nothing is listening" in (out.stdout + out.stderr), (
        f"the failure does not say what went wrong: {out.stdout + out.stderr!r}")


def test_the_helper_replaces_a_real_listener_and_reports_both_pids():
    """The positive case, driven against a process actually holding the port.

    MUTATION: make Restart-RigServer return before calling Stop-Process.
    Observed: the old pid is still listening, the helper's own
    'still served by pid' guard fires, and the command exits non-zero.
    """
    port = _free_port()
    started = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait for it to actually hold the port before asking for a restart.
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
        else:                                      # pragma: no cover
            pytest.skip("the fixture listener never came up")

        # The pid ACTUALLY holding the port, which is what the helper contracts
        # to stop. Not Popen's pid: those differ here, and asserting Popen's
        # would be testing an assumption about process spawning rather than
        # the behaviour under test -- finding the owner BY PORT is the whole
        # point of the fix.
        owner = _run_ps(
            f"Write-Output \"OWNER=$(Get-RigServerPid -Port {port})\"")
        assert "OWNER=" in owner.stdout, owner.stdout + owner.stderr
        listening_pid = int(owner.stdout.split("OWNER=")[1].split()[0])

        out = _run_ps(
            f"$p = Restart-RigServer -Port {port} "
            f"-Start {_listener_snippet(port)}; Write-Output \"NEWPID=$p\"")

        assert out.returncode == 0, f"restart failed: {out.stdout}{out.stderr}"
        assert f"stopping pid {listening_pid}" in out.stdout, (
            f"the helper did not stop the process holding the port: {out.stdout!r}")
        assert "NEWPID=" in out.stdout and "restarted: pid" in out.stdout, (
            f"the helper did not report the replacement: {out.stdout!r}")

        new_pid = int(out.stdout.split("NEWPID=")[1].split()[0])
        assert new_pid != listening_pid, "the same pid is still serving the port"
    finally:
        started.kill()
        started.wait(timeout=30)
        # The helper's replacement listener is a detached process holding the
        # port; take it down by port so the fixture leaves nothing behind.
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
             f"Get-NetTCPConnection -LocalPort {port} -State Listen "
             "-ErrorAction SilentlyContinue | ForEach-Object "
             "{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, text=True, timeout=60)
