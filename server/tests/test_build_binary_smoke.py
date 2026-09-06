"""The binary smoke test has to be able to START the binary it just built.

The release workflow's windows-latest leg built dist/astrodeck.exe and then
failed, every time, at the smoke step: hosted GitHub Windows runners hand every
process an elevated Administrator token, and the server refuses to run elevated
(runtime_security.require_unprivileged_runtime, OPEN-005) with no override --
correctly, because that refusal is what a consumer double-clicking the .exe
relies on. So the fix belongs in the harness: launch the binary de-elevated
with `runas /trustlevel:0x20000`, and then FIND it, because runas returns as
soon as it has spawned the child and leaves no handle behind.

None of that path can run here. This box is not elevated, so the plain launch
is the only branch a local build ever takes, and the release workflow only runs
on a tag. These tests grade the pieces that a machine can grade -- the two
output parsers, which branch is chosen, the exact runas command line, and the
find/kill lifecycle -- plus the dispatch-only CI job that exists so the real
thing can be proven on a hosted runner without cutting a release.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "packaging" / "build_binary.py"
CI = REPO / ".github" / "workflows" / "ci.yml"


def _load_build_binary():
    """Import packaging/build_binary.py by path: it is a script, not a module
    on sys.path, and it must stay runnable as `python packaging/build_binary.py`.
    Importing it must not build or smoke anything -- main() is guarded."""
    spec = importlib.util.spec_from_file_location("astrodeck_build_binary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bb = _load_build_binary()


def test_the_script_still_guards_main_so_importing_it_builds_nothing():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source, (
        "build_binary.py is invoked as `python packaging/build_binary.py`; "
        "without the __main__ guard, importing it would start a build")


# --------------------------------------------------------------------------
# a) netstat: which PID is LISTENING on the smoke port

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8811         127.0.0.1:54312        ESTABLISHED     1111
  TCP    127.0.0.1:9000         0.0.0.0:0              LISTENING       2222
  TCP    [::1]:8811             [::]:0                 LISTENING       3333
  TCP    127.0.0.1:8811         0.0.0.0:0              LISTENING       4321
  UDP    127.0.0.1:8811         *:*                                    5555
"""

NETSTAT_NO_LISTENER = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8811         127.0.0.1:54312        ESTABLISHED     1111
  TCP    127.0.0.1:9000         0.0.0.0:0              LISTENING       2222
"""


def test_netstat_gives_the_listening_pid_and_not_a_neighbour():
    """A client connected TO the port, a listener on another port, and an IPv6
    listener that happens to share the number are all somebody else."""
    assert bb._netstat_listener_pid(NETSTAT, 8811) == 4321


def test_netstat_with_nothing_listening_finds_no_pid():
    assert bb._netstat_listener_pid(NETSTAT_NO_LISTENER, 8811) is None
    assert bb._netstat_listener_pid("", 8811) is None


# --------------------------------------------------------------------------
# b) tasklist: the onefile bootloader parent and its child, by image name

TASKLIST = (
    '"astrodeck.exe","4321","Console","1","98,765 K"\n'
    '"chrome.exe","777","Console","1","12,000 K"\n'
    '"astrodeck.exe","4322","Console","1","10,000 K"\n'
)
TASKLIST_NONE = "INFO: No tasks are running which match the specified criteria.\n"


def test_tasklist_returns_every_astrodeck_pid_and_ignores_the_rest():
    assert bb._tasklist_pids(TASKLIST) == [4321, 4322]


def test_tasklist_with_no_match_returns_nothing():
    assert bb._tasklist_pids(TASKLIST_NONE) == []
    assert bb._tasklist_pids("") == []


# --------------------------------------------------------------------------
# c) which launch strategy, and the exact command line


class FakePopen:
    def __init__(self, argv, env=None):
        self.argv = argv
        self.env = env
        self.pid = 4242

    def poll(self):
        return None


class FakeSubprocess:
    """Stands in for the subprocess module inside build_binary."""

    list2cmdline = staticmethod(subprocess.list2cmdline)

    def __init__(self, returncode: int = 0):
        self.popens: list[FakePopen] = []
        self.runs: list[tuple[list[str], dict]] = []
        self._returncode = returncode

    def Popen(self, argv, env=None, **kw):  # noqa: N802 - mirrors subprocess
        proc = FakePopen(argv, env)
        self.popens.append(proc)
        return proc

    def run(self, argv, **kw):
        self.runs.append((list(argv), kw))
        return subprocess.CompletedProcess(argv, self._returncode, "", "")


@pytest.fixture
def smoke_env(monkeypatch, tmp_path):
    """The two directory variables the smoke test redirects, with sentinels in
    os.environ so a leak either way is visible -- and undone afterwards."""
    env = {"ASTRODECK_CONFIG_DIR": str(tmp_path / "state" / "config"),
           "ASTRODECK_CAPTURE_DIR": str(tmp_path / "state" / "captures")}
    for key in env:
        monkeypatch.setenv(key, "sentinel-real-directory")
    return env


def test_a_plain_popen_when_this_process_is_not_elevated(monkeypatch, tmp_path, smoke_env):
    """The path every local build and both the Linux and macOS legs take. It
    must not change: nothing else exercises the smoke test day to day."""
    fake = FakeSubprocess()
    monkeypatch.setattr(bb, "subprocess", fake)
    monkeypatch.setattr(bb, "_is_elevated", lambda: False)
    exe = tmp_path / "dist" / "astrodeck.exe"

    handle = bb._launch_smoke(exe, 8811, smoke_env)

    assert fake.runs == [], "an unelevated build must not shell out to runas"
    assert len(fake.popens) == 1
    assert fake.popens[0].argv == [str(exe), "run", "--host", "127.0.0.1",
                                   "--port", "8811"]
    assert fake.popens[0].env == smoke_env
    assert handle.poll() is None
    assert os.environ["ASTRODECK_CONFIG_DIR"] == "sentinel-real-directory", (
        "the plain path passes the directories through env=, so it has no "
        "business rewriting this process's environment")


def test_an_elevated_windows_build_relaunches_de_elevated(monkeypatch, tmp_path, smoke_env):
    """What the hosted runner needs. /trustlevel:0x20000 is a basic-user token
    and needs no password; the whole child command line is ONE argument."""
    fake = FakeSubprocess()
    monkeypatch.setattr(bb, "subprocess", fake)
    monkeypatch.setattr(bb.sys, "platform", "win32")
    monkeypatch.setattr(bb, "_is_elevated", lambda: True)
    # A space in the path, because runas re-parses the string we hand it.
    exe = tmp_path / "Program Files" / "astrodeck.exe"

    handle = bb._launch_smoke(exe, 8811, smoke_env)

    assert fake.popens == [], "the elevated path must not Popen the binary"
    assert len(fake.runs) == 1
    argv, _kw = fake.runs[0]
    assert argv[0] == "runas"
    assert argv[1] == "/trustlevel:0x20000"
    assert len(argv) == 3, ("runas takes the child's whole command line as one "
                            f"argument, got {argv!r}")
    assert argv[2] == f'"{exe}" run --host 127.0.0.1 --port 8811'
    # runas starts the child with the CALLER's environment, so a variable that
    # existed only in the env= dict would never reach it and the smoke server
    # would write to the operator's real config and capture directories.
    assert os.environ["ASTRODECK_CONFIG_DIR"] == smoke_env["ASTRODECK_CONFIG_DIR"]
    assert os.environ["ASTRODECK_CAPTURE_DIR"] == smoke_env["ASTRODECK_CAPTURE_DIR"]
    assert handle.pid is None, "runas detaches: the server has yet to be found"


def test_a_runas_that_cannot_start_the_binary_fails_the_build(monkeypatch, tmp_path, smoke_env):
    fake = FakeSubprocess(returncode=1)
    monkeypatch.setattr(bb, "subprocess", fake)
    monkeypatch.setattr(bb.sys, "platform", "win32")
    monkeypatch.setattr(bb, "_is_elevated", lambda: True)
    with pytest.raises(SystemExit) as excinfo:
        bb._launch_smoke(tmp_path / "astrodeck.exe", 8811, smoke_env)
    assert "runas" in str(excinfo.value)


def test_elevation_detection_never_raises(monkeypatch):
    """A detection failure means "not elevated": the plain launch then fails
    loudly on the interlock, which is a better outcome than de-elevating every
    ordinary build on the strength of a broken probe."""
    # None in sys.modules makes the import raise, which is the shape of the
    # real failure: --skip-install, or a server that will not import.
    monkeypatch.setitem(sys.modules, "astrodeck.runtime_security", None)
    monkeypatch.setattr(bb.sys, "platform", "linux")
    assert bb._is_elevated() is False


# --------------------------------------------------------------------------
# d) the runas handle: find the server, then reap it

LISTENING = ("  TCP    127.0.0.1:8811         0.0.0.0:0              "
             "LISTENING       4321\n")
NOTHING = ("  TCP    0.0.0.0:135            0.0.0.0:0              "
           "LISTENING       900\n")


class FakeWindows:
    """netstat / tasklist / taskkill, with a world they actually describe."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.listening = False
        self.images: list[int] = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        if cmd[0] == "netstat":
            return LISTENING if self.listening else NOTHING
        if cmd[0] == "tasklist":
            if not self.images:
                return TASKLIST_NONE
            return "".join(f'"astrodeck.exe","{pid}","Console","1","1 K"\n'
                           for pid in self.images)
        if cmd[0] == "taskkill":
            self.listening = False
            self.images = []
            return ""
        raise AssertionError(f"unexpected command {cmd!r}")

    def kills(self):
        return [c for c in self.calls if c[0] == "taskkill"]


def test_the_runas_handle_finds_the_server_then_kills_the_whole_tree(monkeypatch):
    fake = FakeWindows()
    monkeypatch.setattr(bb, "_port_in_use", lambda port: False)
    handle = bb.SmokeProcess(8811, runner=fake)

    assert handle.poll() is None, "nothing listening yet is still starting up"
    assert handle.pid is None

    fake.listening = True
    fake.images = [4321, 4322]
    assert handle.poll() is None
    assert handle.pid == 4321

    handle.stop()
    kills = fake.kills()
    assert ["taskkill", "/PID", "4321", "/T", "/F"] in kills
    assert ["taskkill", "/PID", "4322", "/T", "/F"] in kills, (
        "the onefile bootloader parent outlives its child; killing only the "
        "listener leaves the port held for the next build to find")
    assert all("/T" in c and "/F" in c for c in kills)


def test_a_server_that_vanishes_reports_that_it_exited(monkeypatch):
    """The Popen path reports "exited during startup" from returncode; the
    runas path has no handle, so disappearing IS the exit."""
    fake = FakeWindows()
    fake.listening = True
    fake.images = [4321]
    handle = bb.SmokeProcess(8811, runner=fake)
    assert handle.poll() is None

    fake.listening = False
    fake.images = []
    assert handle.poll() is not None


def test_a_child_that_never_listens_gives_up_rather_than_hanging():
    fake = FakeWindows()
    handle = bb.SmokeProcess(8811, runner=fake, find_timeout=-1)
    assert handle.poll() is not None


def test_the_popen_path_delegates_poll_and_stop(monkeypatch):
    stopped: list[object] = []
    monkeypatch.setattr(bb, "_stop_tree", lambda proc: stopped.append(proc))
    proc = FakePopen(["astrodeck.exe"])
    handle = bb.SmokeProcess(8811, popen=proc)
    assert handle.poll() is None
    assert handle.pid == 4242
    handle.stop()
    assert stopped == [proc], "the plain path must still stop the process tree"


# --------------------------------------------------------------------------
# e) the CI job that can prove this on a hosted runner without a release


def _ci_text() -> str:
    # Normalised: a Windows checkout of this file has CRLF endings, and the
    # block slicing below anchors on "\n".
    return CI.read_text(encoding="utf-8").replace("\r\n", "\n")


def _job_block(name: str) -> str:
    """A job's own YAML, from its key to the next top-level job key."""
    text = _ci_text()
    start = text.find(f"\n  {name}:\n")
    assert start != -1, f"ci.yml declares no {name} job"
    rest = text[start + 1:]
    following = re.search(r"^  [A-Za-z_][\w-]*:\s*$", rest[1:], re.M)
    return rest if following is None else rest[:following.start() + 1]


def test_ci_offers_a_binary_smoke_dispatch_input():
    text = _ci_text()
    dispatch = text[text.index("workflow_dispatch:"):text.index("\n  push:")]
    assert "binary_smoke:" in dispatch, (
        "the Windows binary smoke path only exists on a hosted runner, and "
        "without a dispatch input the only way to run it is to cut a release")
    assert "type: boolean" in dispatch
    assert "default: false" in dispatch


def test_the_binary_smoke_job_is_dispatch_only_and_builds_on_windows():
    block = _job_block("binary-smoke-windows")
    assert "github.event_name == 'workflow_dispatch'" in block
    assert "inputs.binary_smoke" in block, (
        "Windows bills at 2x: this job is a proof to be asked for, not a gate "
        "on every push")
    assert "runs-on: windows-latest" in block
    assert "python packaging/build_binary.py" in block
    assert "upload-artifact" not in block


def test_the_binary_smoke_job_mirrors_the_release_build_environment():
    """A proof run that builds differently from the release leg proves nothing
    about the release leg."""
    block = _job_block("binary-smoke-windows")
    assert "actions/checkout@v4" in block
    assert "actions/setup-node@v4" in block
    assert "node-version: '20'" in block
    assert "cache-dependency-path: ui/package-lock.json" in block
    assert "actions/setup-python@v5" in block
    assert "python-version: '3.12'" in block
