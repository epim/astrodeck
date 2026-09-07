"""The binary smoke test has to be able to START the binary it just built.

The release workflow's windows-latest leg built dist/astrodeck.exe and then
failed, every time, at the smoke step: hosted GitHub Windows runners hand every
process an elevated Administrator token, and the server refuses to run elevated
(runtime_security.require_unprivileged_runtime, OPEN-005) with no override --
correctly, because that refusal is what a consumer double-clicking the .exe
relies on.

The first fix, `runas /trustlevel:0x20000`, did not work: proof run 34067961326
captured the child's output and it still died in require_unprivileged_runtime.
The SAFER-restricted token that /trustlevel produces keeps TokenIsElevated set,
which is what _windows_is_elevated keys on. So the smoke test now does what the
interlock's own message asks for: it creates a throwaway unprivileged local
account, grants it the narrowest access that lets it run the binary, launches
through CreateProcessWithLogonW, and deletes the account afterwards whatever
happened.

None of that path can run here. This box is not elevated, so the plain launch
is the only branch a local build ever takes, and it cannot create users anyway.
These tests grade the pieces a machine can grade -- which branch is chosen, the
account and grants and wrapper the elevated path builds, that the password
never reaches stdout or an exception, that the account is deleted even when the
launch fails, the process lifecycle around the real handle, and the two output
parsers -- plus the dispatch-only CI job that exists so the whole thing can be
proven on a hosted runner without cutting a release.
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
# the netstat parser: which PID is LISTENING on the smoke port

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
# the tasklist parser: the onefile bootloader and its child, by image name

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
# the launch strategy


class FakePopen:
    def __init__(self, argv, env=None):
        self.argv = argv
        self.env = env
        self.pid = 4242

    def poll(self):
        return None


class FakeSubprocess:
    """Stands in for the subprocess module inside build_binary, so net user,
    icacls, taskkill, netstat and tasklist are all recorded and none run."""

    list2cmdline = staticmethod(subprocess.list2cmdline)

    def __init__(self):
        self.popens: list[FakePopen] = []
        self.runs: list[tuple[list[str], dict]] = []
        self.stdout_map: dict[str, str] = {}
        self.returncode_map: dict[str, int] = {}
        #: tools whose stdout repeats their own command line, so a test can
        #: check that a message we did not write cannot leak the password
        self.echo_argv: set[str] = set()

    def Popen(self, argv, env=None, **kw):  # noqa: N802 - mirrors subprocess
        proc = FakePopen(argv, env)
        self.popens.append(proc)
        return proc

    def run(self, argv, **kw):
        argv = list(argv)
        self.runs.append((argv, kw))
        stdout = (subprocess.list2cmdline(argv) if argv[0] in self.echo_argv
                  else self.stdout_map.get(argv[0], ""))
        return subprocess.CompletedProcess(
            argv, self.returncode_map.get(argv[0], 0), stdout, "")

    def calls(self, tool: str) -> list[list[str]]:
        return [argv for argv, _ in self.runs if argv and argv[0] == tool]


@pytest.fixture
def smoke_env(monkeypatch, tmp_path):
    """The two directory variables the smoke test redirects, with sentinels in
    os.environ so a leak either way is visible -- and undone afterwards."""
    env = {"ASTRODECK_CONFIG_DIR": str(tmp_path / "state" / "config"),
           "ASTRODECK_CAPTURE_DIR": str(tmp_path / "state" / "captures")}
    for key in env:
        monkeypatch.setenv(key, "sentinel-real-directory")
    return env


@pytest.fixture
def elevated(monkeypatch):
    """An elevated Windows build with every OS call faked."""
    fake = FakeSubprocess()
    fake.launches: list[tuple] = []
    fake.create_error: BaseException | None = None
    monkeypatch.setattr(bb, "subprocess", fake)
    monkeypatch.setattr(bb.sys, "platform", "win32")
    monkeypatch.setattr(bb, "_is_elevated", lambda: True)

    def fake_create(name, password, cmdline, cwd):
        fake.launches.append((name, password, cmdline, cwd))
        if fake.create_error is not None:
            raise fake.create_error
        return 0xABC, 4321

    monkeypatch.setattr(bb, "_create_process_as_user", fake_create)
    monkeypatch.setattr(bb, "_close_handle", lambda handle: None)
    monkeypatch.setattr(bb, "_port_in_use", lambda port: False)

    # The real one rewrites the DACL of the window station this very test
    # process is attached to. Faked, so the suite records the call.
    fake.stations: list[str] = []
    fake.stations_restored: list[str] = []

    def fake_station(name):
        fake.stations.append(name)
        return lambda: fake.stations_restored.append(name)

    monkeypatch.setattr(bb, "_grant_station_access", fake_station)
    return fake


def _net_user_add(fake) -> list[str]:
    for argv in fake.calls("net"):
        if argv[1:2] == ["user"] and "/add" in argv:
            return argv
    raise AssertionError(f"no `net user ... /add` among {fake.calls('net')}")


def _grants(fake) -> list[tuple[str, str]]:
    return [(argv[1], argv[3]) for argv in fake.calls("icacls")
            if "/grant" in argv]


def test_a_plain_popen_when_this_process_is_not_elevated(monkeypatch, tmp_path, smoke_env):
    """The path every local build and both the Linux and macOS legs take. It
    must not change: nothing else exercises the smoke test day to day."""
    fake = FakeSubprocess()
    monkeypatch.setattr(bb, "subprocess", fake)
    monkeypatch.setattr(bb, "_is_elevated", lambda: False)
    exe = tmp_path / "dist" / "astrodeck.exe"

    handle = bb._launch_smoke(exe, 8811, smoke_env)

    assert fake.runs == [], "an unelevated build must not create an account"
    assert len(fake.popens) == 1
    assert fake.popens[0].argv == [str(exe), "run", "--host", "127.0.0.1",
                                   "--port", "8811"]
    assert fake.popens[0].env == smoke_env
    assert handle.poll() is None
    assert os.environ["ASTRODECK_CONFIG_DIR"] == "sentinel-real-directory", (
        "the plain path passes the directories through env=, so it has no "
        "business rewriting this process's environment")


def test_an_elevated_build_runs_the_binary_as_a_throwaway_account(
        elevated, tmp_path, smoke_env, capsys):
    """What the hosted runner needs, end to end: an unprivileged account, the
    access it needs, a wrapper carrying the environment, and a logon launch."""
    exe = tmp_path / "dist" / "astrodeck.exe"
    state = tmp_path / "state"

    handle = bb._launch_smoke(exe, 8811, smoke_env)

    # 1. the account. Local SAM names cap at 20 characters, so the name is
    # short by design -- a longer prefix is rejected by `net user` itself.
    argv = _net_user_add(elevated)
    name, password = argv[2], argv[3]
    assert argv == ["net", "user", name, password, "/add", "/y"]
    assert re.fullmatch(r"astrodeck-sm-[0-9a-f]{6}", name), name
    assert len(name) <= 20, f"{name} is {len(name)} characters; SAM caps at 20"
    assert len(password) >= 24
    assert not password.startswith("-"), "net would read that as a switch"

    # 2. the grants, and nothing wider. `icacls /T` over the checkout would
    # walk node_modules and take minutes.
    ancestors = list(reversed((tmp_path / "dist").parents))
    assert _grants(elevated) == (
        [(str(p), f"{name}:RX") for p in ancestors]
        + [(str(tmp_path / "dist"), f"{name}:(OI)(CI)RX"),
           (str(exe), f"{name}:RX"),
           (str(state), f"{name}:(OI)(CI)M")])
    assert not any("/T" in argv for argv in elevated.calls("icacls"))

    # 2b. the window station. A process attaches to one before it runs a
    # single instruction, and an account with no access to this build's
    # station cannot even load user32.
    assert elevated.stations == [name]

    # 3. the wrapper: the environment carrier and the log capture in one.
    wrapper, log = state / "smoke.cmd", state / "smoke.log"
    body = wrapper.read_text(encoding="utf-8")
    assert body.startswith("@echo off")
    assert f"set ASTRODECK_CONFIG_DIR={state / 'config'}" in body
    assert f"set ASTRODECK_CAPTURE_DIR={state / 'captures'}" in body
    assert (f"{subprocess.list2cmdline([str(exe), 'run', '--host', '127.0.0.1', '--port', '8811'])}"
            f" > \"{log}\" 2>&1") in body

    # 4. the launch itself.
    assert len(elevated.launches) == 1
    launched_name, launched_password, cmdline, cwd = elevated.launches[0]
    assert (launched_name, launched_password) == (name, password)
    assert cmdline == f'cmd /c "{wrapper}"'
    assert cwd == state
    assert handle.pid == 4321
    assert handle.log_path == log

    # 5. the child gets its own profile environment, so nothing is smuggled
    # through this process's -- the old runas path had to and it is gone.
    assert os.environ["ASTRODECK_CONFIG_DIR"] == "sentinel-real-directory"
    assert password not in capsys.readouterr().out


def test_the_smoke_password_reaches_neither_stdout_nor_an_exception(
        elevated, tmp_path, smoke_env, capsys):
    """Including through an error message we did not write: `net` is told to
    echo its own command line back, password and all."""
    elevated.returncode_map["net"] = 2
    elevated.echo_argv.add("net")

    with pytest.raises(SystemExit) as excinfo:
        bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)

    password = _net_user_add(elevated)[3]
    assert password not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)
    assert password not in capsys.readouterr().out


def test_a_failed_grant_names_the_path_it_could_not_reach(elevated, tmp_path, smoke_env):
    elevated.returncode_map["icacls"] = 5
    with pytest.raises(SystemExit) as excinfo:
        bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)
    assert "could not grant" in str(excinfo.value)


def test_a_launch_that_fails_still_deletes_the_account(elevated, tmp_path, smoke_env):
    """Create, fail, clean up. An orphaned local account on a runner is only
    untidy; on a maintainer's box it would be a real one."""
    elevated.create_error = SystemExit("CreateProcessWithLogonW failed")

    with pytest.raises(SystemExit):
        bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)

    name = _net_user_add(elevated)[2]
    assert ["net", "user", name, "/delete"] in elevated.calls("net")
    removes = [argv for argv in elevated.calls("icacls") if "/remove" in argv]
    assert removes, "the ACEs the failed launch added must come off too"
    assert all(argv[-1] == name for argv in removes)


def test_the_window_station_goes_back_the_way_it_was(elevated, tmp_path, smoke_env):
    """The grant outlives the launch and must not outlive the smoke test. The
    account is deleted either way, but a DACL entry naming a deleted account
    is exactly the orphaned SID the icacls cleanup exists to avoid."""
    handle = bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)
    name = _net_user_add(elevated)[2]
    assert elevated.stations_restored == [], "not while the child is running"

    handle.stop()

    assert elevated.stations_restored == [name]
    assert ["net", "user", name, "/delete"] in elevated.calls("net")


def test_a_launch_that_fails_also_puts_the_window_station_back(
        elevated, tmp_path, smoke_env):
    elevated.create_error = SystemExit("CreateProcessWithLogonW failed")

    with pytest.raises(SystemExit):
        bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)

    assert elevated.stations_restored == elevated.stations


def test_a_window_station_that_cannot_be_granted_is_not_fatal(monkeypatch, capsys):
    """Where the station already admits the account the grant is unnecessary,
    and where it cannot be made the launch fails next with an exit code we
    name. Neither is a reason to abandon the build here."""
    def boom():
        raise OSError(5, "Access is denied")

    monkeypatch.setattr(bb, "_window_station_and_desktop", boom)

    restore = bb._grant_station_access("astrodeck-sm-abc123")

    assert restore() is None, "the undo is still callable"
    assert "could not grant" in capsys.readouterr().out


def test_the_loader_failure_that_says_nothing_is_named(elevated, monkeypatch,
                                                       tmp_path, smoke_env):
    """0xC0000142 is what a child that never reached its first instruction
    exits with: no traceback, no log, nothing but the number. Measured on an
    elevated Windows 11 box before the window station was granted."""
    monkeypatch.setattr(bb, "_process_exit_code", lambda handle: 0xC0000142)
    monkeypatch.setattr(bb, "_source_version", lambda: "9.9.9")
    monkeypatch.setattr(bb, "ROOT", tmp_path)
    exe = tmp_path / "dist" / "astrodeck.exe"

    with pytest.raises(SystemExit) as excinfo:
        bb.smoke(exe)

    assert "STATUS_DLL_INIT_FAILED" in str(excinfo.value)
    assert "window station" in str(excinfo.value)


def test_the_handle_path_polls_the_real_process(elevated, monkeypatch, tmp_path, smoke_env):
    """`cmd /c wrapper` waits for the bootloader, which waits for the server,
    so the wrapper's exit code IS the server's -- no port-watching guesswork."""
    codes = [None, None, 3]
    monkeypatch.setattr(bb, "_process_exit_code", lambda handle: codes.pop(0))
    handle = bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)
    assert handle.poll() is None
    assert handle.poll() is None
    assert handle.poll() == 3


def test_stop_kills_the_wrapper_tree_then_deletes_the_account(
        elevated, tmp_path, smoke_env):
    elevated.stdout_map["tasklist"] = TASKLIST
    handle = bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)
    name = _net_user_add(elevated)[2]

    handle.stop()

    kills = elevated.calls("taskkill")
    assert ["taskkill", "/PID", "4321", "/T", "/F"] in kills
    assert ["taskkill", "/PID", "4322", "/T", "/F"] in kills, (
        "a onefile bootloader's child has outlived its parent here before, and "
        "a survivor would answer the next build's checks")
    assert all("/T" in argv and "/F" in argv for argv in kills)
    assert ["net", "user", name, "/delete"] in elevated.calls("net")


def test_stop_after_a_reported_death_still_deletes_the_account(
        elevated, monkeypatch, tmp_path, smoke_env):
    """The failure path: /healthz never answers, smoke() raises, and the
    finally: still has to take the account away."""
    monkeypatch.setattr(bb, "_process_exit_code", lambda handle: 1)
    handle = bb._launch_smoke(tmp_path / "dist" / "astrodeck.exe", 8811, smoke_env)
    name = _net_user_add(elevated)[2]
    assert handle.poll() == 1

    handle.stop()
    assert ["net", "user", name, "/delete"] in elevated.calls("net")

    # ...and only once, however many times stop() is reached.
    handle.stop()
    assert elevated.calls("net").count(["net", "user", name, "/delete"]) == 1


def test_the_popen_path_delegates_poll_and_stop(monkeypatch):
    stopped: list[object] = []
    monkeypatch.setattr(bb, "_stop_tree", lambda proc: stopped.append(proc))
    proc = FakePopen(["astrodeck.exe"])
    handle = bb.SmokeProcess(8811, popen=proc)
    assert handle.poll() is None
    assert handle.pid == 4242
    handle.stop()
    assert stopped == [proc], "the plain path must still stop the process tree"


def test_output_tail_reports_what_the_child_wrote(tmp_path):
    """The whole reason the wrapper redirects: the first hosted proof run died
    eight seconds in and the log said nothing at all."""
    log = tmp_path / "smoke.log"
    handle = bb.SmokeProcess(8811, log_path=log)
    assert handle.output_tail() == "", "nothing written yet"
    log.write_text("Traceback\nRuntimeError: it refused\n", encoding="utf-8")
    assert handle.output_tail().endswith("RuntimeError: it refused")


def test_elevation_detection_never_raises(monkeypatch):
    """A detection failure means "not elevated": the plain launch then fails
    loudly on the interlock, which is a better outcome than creating a local
    user on every ordinary build on the strength of a broken probe."""
    # None in sys.modules makes the import raise, which is the shape of the
    # real failure: --skip-install, or a server that will not import.
    monkeypatch.setitem(sys.modules, "astrodeck.runtime_security", None)
    monkeypatch.setattr(bb.sys, "platform", "linux")
    assert bb._is_elevated() is False


# --------------------------------------------------------------------------
# the CI job that can prove this on a hosted runner without a release


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
