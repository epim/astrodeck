"""scripts/deploy_relay.ps1: the relay's deploy path, proven end to end (#462).

The relay had no working deploy path on 2026-09-28: the flyctl login had
expired and the Actions workflow was disabled with its minutes spent. Worse,
nothing on the running relay said which code it was, so a deploy could not be
verified and drift could not be noticed (#462 item 2 put the build identity in
``/healthz``). This script is item 3: in order, it runs the relay suite, runs
``flyctl deploy --remote-only`` from ``relay/`` with the commit as a build arg,
and polls ``/healthz`` until the relay reports that commit.

These drive the real script in powershell.exe with everything it reaches
stubbed, all under the test's own temp dir:

* ``git``, ``flyctl`` -- ``.cmd`` stubs on a PATH that holds ONLY the stub dir
  and the Windows system dirs. The real flyctl is on this box's PATH and is
  logged in, so the PATH is built rather than prepended to, and the fixture
  checks no system dir holds a flyctl or git of its own. ``FLY_API_TOKEN`` and
  ``FLY_ACCESS_TOKEN`` are set to a fake, so even a real flyctl reached by
  mistake could not authenticate. No test here calls Fly.
* the relay suite -- a ``.cmd`` stub passed as ``-Python``, so the real suite
  (graded on its own) does not run inside every case.
* ``/healthz`` -- a real HTTP server on loopback in this process, answering
  from a script (a list of answers, the last repeating), passed as
  ``-HealthUrl``. The script's own ``Invoke-RestMethod`` does the polling.

Every call, from the stubs and from the HTTP server, lands in one log in the
order it happened, which is what the order assertions read.

Nothing here contacts the rig or Fly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="drives powershell.exe and .cmd stubs")

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "deploy_relay.ps1"
_RELAY = _REPO / "relay"
_DOCKERFILE = _RELAY / "Dockerfile"
_SYSROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
_POWERSHELL = _SYSROOT / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

# The stubs. One Python file behind three .cmd shims; the first argument
# names the tool. State (what each tool answers) is a JSON file beside it.
_STUB = r'''
import json, os, sys
from pathlib import Path

here = Path(__file__).resolve().parent
state = json.loads((here / "state.json").read_text(encoding="utf-8"))
tool, args = sys.argv[1], sys.argv[2:]
with open(here / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"tool": tool, "args": args, "cwd": os.getcwd()}) + "\n")

if tool == "git":
    a = list(args)
    if a[:1] == ["-C"]:
        a = a[2:]
    if a[:2] == ["rev-parse", "HEAD"]:
        print(state["sha"])
        sys.exit(0)
    if a[:1] == ["status"]:
        sys.stdout.write(state["dirty"])
        sys.exit(0)
    if a[:1] == ["show"]:
        sys.stdout.write(state["pyproject"])
        sys.exit(0)
    sys.stderr.write("stub git: unexpected %r\n" % (a,))
    sys.exit(2)
if tool == "python":
    sys.stdout.write(state["suite_out"] + "\n")
    # A line on stderr, which must not stop the script: under 5.1 and a Stop
    # preference, the first stderr line of an unfolded native call is a
    # terminating error (deploy_common.ps1). flyctl writes progress there.
    sys.stderr.write("stub suite: some stderr chatter\n")
    sys.exit(state["suite_exit"])
if tool == "flyctl":
    for line in state["fly_out"]:
        print(line)
    if state["fly_exit"]:
        sys.stderr.write("Error: " + state["fly_err"] + "\n")
    sys.exit(state["fly_exit"])
sys.exit(3)
'''

_SHIM = '@echo off\r\n"{py}" "{stub}" {tool} %*\r\nexit /b %ERRORLEVEL%\r\n'

# A pyproject whose first ``version =`` line is NOT the project's: the
# script must read the [project] table's, the way the home's /healthz
# reports astrodeck.__version__.
_PYPROJECT = (
    '[build-system]\nrequires = ["setuptools"]\n\n'
    '[tool.decoy]\nversion = "9.9.9-decoy"\n\n'
    '[project]\nname = "astrodeck"\nversion = "{version}"\n'
    'requires-python = ">=3.11"\n\n[tool.pytest.ini_options]\naddopts = ""\n')


def _runtime_stage_args() -> set[str]:
    """The ARG names the Dockerfile's LAST stage declares: the only build
    args the image can see (an ARG does not cross a FROM)."""
    text = re.sub(r"\\\r?\n", " ", _DOCKERFILE.read_text(encoding="utf-8"))
    stages: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"(?i)FROM\s", line):
            stages.append([])
        elif stages and re.match(r"(?i)ARG\s", line):
            stages[-1].append(line.split()[1].split("=")[0])
    return set(stages[-1])


class _Health(BaseHTTPRequestHandler):
    """Answers GET /healthz from the box's script of answers."""

    def do_GET(self):  # noqa: N802 - http.server's name
        box = self.server.box  # type: ignore[attr-defined]
        with box.lock:
            i = min(box.health_hits, len(box.health) - 1)
            box.health_hits += 1
            status, body = box.health[i]
            with open(box.calls_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"tool": "healthz", "path": self.path}) + "\n")
        data = json.dumps(body).encode() if body is not None else b"down"
        self.send_response(status)
        self.send_header("Content-Type",
                         "application/json" if body is not None else "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # keep pytest's output clean
        pass


@dataclass
class Box:
    root: Path
    stubs: Path
    sha: str
    version: str
    marker: str
    calls_path: Path
    health: list = field(default_factory=list)
    health_hits: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    server: ThreadingHTTPServer | None = None

    def state(self, **kw) -> None:
        st = {
            "sha": self.sha, "dirty": "",
            "pyproject": _PYPROJECT.format(version=self.version),
            "suite_exit": 0, "suite_out": "146 passed, 5 warnings in 3.84s",
            "fly_exit": 0, "fly_err": "",
            "fly_out": ["==> Building image with Depot",
                        "--> Pushing image done",
                        "Visit your newly deployed app at "
                        "https://astrodeck-relay.fly.dev/"],
        }
        st.update(kw)
        (self.stubs / "state.json").write_text(json.dumps(st), encoding="utf-8")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/healthz"

    def calls(self) -> list[dict]:
        text = self.calls_path.read_text(encoding="utf-8")
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]

    def tools(self) -> list[str]:
        return [c["tool"] for c in self.calls()]

    def of(self, tool: str) -> list[dict]:
        return [c for c in self.calls() if c["tool"] == tool]

    def run(self, *, timeout_s: float = 3.0,
            interval_s: float = 0.2) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in {"PATH", "PSMODULEPATH"}}
        dirs = [self.stubs, _SYSROOT / "System32", _SYSROOT,
                _SYSROOT / "System32" / "WindowsPowerShell" / "v1.0"]
        env["PATH"] = os.pathsep.join(str(d) for d in dirs)
        env["FLY_API_TOKEN"] = self.marker
        env["FLY_ACCESS_TOKEN"] = self.marker
        return subprocess.run(
            [str(_POWERSHELL), "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPT),
             "-HealthUrl", self.url,
             "-Python", str(self.stubs / "suite-python.cmd"),
             "-PollIntervalS", str(interval_s),
             "-HealthTimeoutS", str(timeout_s)],
            capture_output=True, text=True, timeout=120, env=env,
            cwd=str(self.root))


def _show(r: subprocess.CompletedProcess) -> str:
    return f"exit={r.returncode}\n--- stdout\n{r.stdout}\n--- stderr\n{r.stderr}"


@pytest.fixture
def box(tmp_path: Path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "stub.py").write_text(_STUB, encoding="utf-8")
    for tool, name in (("git", "git.cmd"), ("flyctl", "flyctl.cmd"),
                       ("python", "suite-python.cmd")):
        (stubs / name).write_text(
            _SHIM.format(py=sys.executable, stub=stubs / "stub.py", tool=tool),
            encoding="ascii")
    for d in (_SYSROOT / "System32", _SYSROOT,
              _SYSROOT / "System32" / "WindowsPowerShell" / "v1.0"):
        for exe in ("flyctl.exe", "fly.exe", "git.exe"):
            assert not (d / exe).exists(), (
                f"{d / exe} would let a stub-less run reach a real tool")
    b = Box(root=tmp_path, stubs=stubs,
            sha=hashlib.sha1(uuid.uuid4().bytes).hexdigest(),
            version="0.3." + str(uuid.uuid4().int % 1000),
            marker="fake-fly-token-" + uuid.uuid4().hex,
            calls_path=stubs / "calls.jsonl")
    b.calls_path.write_text("", encoding="utf-8")
    b.state()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    srv.box = b  # type: ignore[attr-defined]
    b.server = srv
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield b
    finally:
        srv.shutdown()
        srv.server_close()


def _old() -> str:
    return hashlib.sha1(b"the commit v8 was built from").hexdigest()


# ---------------------------------------------------------------------------
# the order, and the commit it proves
# ---------------------------------------------------------------------------

def test_the_suite_runs_then_flyctl_deploys_the_commit_then_healthz_proves_it(box):
    """The whole path, the way a real deploy goes: /healthz is down for a
    moment while the machine is replaced, then answers the OLD commit, then
    the new one. The script must poll through the first two and succeed on
    the third.

    MUTATION "deploy before the suite" (the flyctl step moved above the
    suite step). Observed, run from a private copy on 2026-09-28:
      AssertionError: flyctl deployed before the suite ran: ['git', 'git',
      'git', 'flyctl', 'python', 'healthz', 'healthz', 'healthz']
    MUTATION "one look, no poll" (the deadline test -> ``if ($true)``, so
    the first answer is the verdict). Observed:
      AssertionError: exit=1 ... RELAY DEPLOY FAILED: the relay's /healthz
      did not report commit e57809a6... within 20 s; it last answered no
      answer (The remote server returned an error: (503) Server
      Unavailable.). ...
      assert 1 == 0
    (and every case of the never-reports test below red with "expected the
    script to keep polling, got 1 look(s)").
    MUTATION "first version line anywhere" (``Get-ProjectVersion`` ignores
    the table). Observed:
      AssertionError: expected the commit and the [project] version as
      build args: ['deploy', '--remote-only', '--build-arg',
      'RELAY_BUILD_COMMIT=2674878f...', '--build-arg',
      'RELAY_BUILD_VERSION=9.9.9-decoy']
    """
    box.health = [(503, None), (200, {"ok": True, "commit": _old(),
                                      "version": "0.3.35"}),
                  (200, {"ok": True, "commit": box.sha,
                         "version": box.version})]

    r = box.run(timeout_s=20)

    assert r.returncode == 0, _show(r)
    tools = [t for t in box.tools() if t != "git"]
    assert tools.index("python") < tools.index("flyctl"), (
        f"flyctl deployed before the suite ran: {box.tools()}")
    assert tools.index("flyctl") < tools.index("healthz"), box.tools()
    assert tools.count("flyctl") == 1 and tools.count("python") == 1, tools
    assert box.health_hits == 3, (box.health_hits, _show(r))

    # The version is read from the commit, not the working tree. The
    # argument is spelled ``"${Sha}:server/..."`` because in
    # ``"$Sha:server/pyproject.toml"`` 5.1 reads ``$Sha:server`` as a
    # drive-qualified variable, which is empty, and expands the whole string
    # to "/pyproject.toml" without an error (checked 2026-09-28). The stub
    # answers any ``show``, so only this assertion sees that. MUTATION
    # "drive-qualified rev" (``"${Sha}:`` -> ``"$Sha:``). Observed:
    #   AssertionError: {'args': ['-C', ...
    #   At index 3 diff: '/pyproject.toml' !=
    #   'b5cd877cbfa87d739b2d7dea35b0359145a454c6:server/pyproject.toml'
    (show,) = [c for c in box.of("git") if "show" in c["args"]]
    assert show["args"] == ["-C", str(_REPO), "show",
                            f"{box.sha}:server/pyproject.toml"], show

    (suite,) = box.of("python")
    assert suite["args"][:3] == ["-m", "pytest", "-q"], suite
    assert Path(suite["cwd"]) == _RELAY, suite

    (fly,) = box.of("flyctl")
    assert Path(fly["cwd"]) == _RELAY, (
        f"flyctl must run from relay/, where fly.toml and the Dockerfile "
        f"are: {fly}")
    args = fly["args"]
    assert args[:2] == ["deploy", "--remote-only"], args
    build_args = [args[i + 1] for i, a in enumerate(args) if a == "--build-arg"]
    assert sorted(build_args) == sorted([f"RELAY_BUILD_COMMIT={box.sha}",
                                         f"RELAY_BUILD_VERSION={box.version}"]), (
        f"expected the commit and the [project] version as build args: {args}")
    assert f"relay deployed: /healthz reports commit {box.sha}" in r.stdout, (
        _show(r))


def test_every_build_arg_the_script_passes_is_one_the_image_can_see(box):
    """The seam between the script and the Dockerfile. A build arg the
    runtime stage does not declare is dropped without a word, and /healthz
    would then answer "unknown" to a poll the script can never win.

    MUTATION "a build arg the Dockerfile does not declare" (the script's
    ``RELAY_BUILD_COMMIT`` renamed ``BUILD_COMMIT``). Observed:
      AssertionError: the script passes build args the Dockerfile's runtime
      stage never declares: ['BUILD_COMMIT']
    (and the happy path red with it, on its build-arg assertion).
    """
    box.health = [(200, {"ok": True, "commit": box.sha, "version": box.version})]

    box.run()

    (fly,) = box.of("flyctl")
    names = [fly["args"][i + 1].split("=")[0]
             for i, a in enumerate(fly["args"]) if a == "--build-arg"]
    assert names, fly
    missing = sorted(set(names) - _runtime_stage_args())
    assert not missing, (
        f"the script passes build args the Dockerfile's runtime stage never "
        f"declares: {missing}")


@pytest.mark.parametrize("answers", [
    [(200, {"ok": True, "commit": "OLD", "version": "0.3.35"})],
    [(200, {"ok": True, "commit": "unknown", "version": "unknown"})],
    [(200, {"ok": True, "ts": 1.0})],
    [(503, None)],
], ids=["another-commit", "unknown", "pre-462-relay", "never-up"])
def test_a_healthz_that_never_reports_the_commit_fails_loudly(box, answers):
    """flyctl said the deploy finished, and the running relay is not the
    code just built: a failed deploy, whatever the exit code of flyctl.
    [another-commit] is the v8-still-running case; [unknown] an image built
    without the args; [pre-462-relay] a relay from before /healthz carried an
    identity; [never-up] a relay that did not come back.

    MUTATION "no commit assertion" (the poll accepts the first 200 answer,
    ``if ($got -eq $Sha)`` -> ``if ($true)``). Observed, [another-commit],
    [unknown] and [pre-462-relay] red, [never-up] green (no 200 ever comes):
      AssertionError: exit=0 ... relay deployed: /healthz reports commit
      ebdced80d0e3e462de19feb2f86650004ab73c85, version 0.3.35 ...
      assert 0 != 0
      AssertionError: exit=0 ... relay deployed: /healthz reports commit
      unknown, version unknown ... assert 0 != 0
    and the happy path above red with it (``assert 2 == 3``: it stopped at
    the old commit's answer).
    """
    answers = [(s, dict(b, commit=_old()) if b and b.get("commit") == "OLD"
                else b) for s, b in answers]
    box.health = answers

    r = box.run(timeout_s=2.5, interval_s=0.25)

    out = r.stdout + r.stderr
    assert r.returncode != 0, _show(r)
    assert "RELAY DEPLOY FAILED" in out, _show(r)
    assert f"did not report commit {box.sha}" in out, _show(r)
    assert "relay deployed" not in out, _show(r)
    assert box.health_hits >= 3, (
        f"expected the script to keep polling, got {box.health_hits} look(s)")
    if answers[0][1] and "commit" in answers[0][1]:
        # The last answer is named, so the operator sees what IS running.
        assert answers[0][1]["commit"] in out, _show(r)


# ---------------------------------------------------------------------------
# what stops a deploy before it starts
# ---------------------------------------------------------------------------

def test_a_failed_suite_deploys_nothing(box):
    """The gate. A red suite: no flyctl call, no poll, exit 1, and the
    suite's own words on the screen.

    MUTATION "deploy before the suite". Observed, with the happy path and
    the skipped-suite case red beside it:
      AssertionError: flyctl ran although the suite failed: [{'tool':
      'flyctl', 'args': ['deploy', '--remote-only', '--build-arg',
      'RELAY_BUILD_COMMIT=2a70855a...', '--build-arg',
      'RELAY_BUILD_VERSION=0.3.685'], 'cwd': ...}]
    MUTATION "suite exit code ignored" (the suite's ``ExitCode -ne 0``
    test -> ``if ($false)``). Observed, this case alone red:
      AssertionError: flyctl ran although the suite failed: [{'tool':
      'flyctl', 'args': ['deploy', '--remote-only', '--build-arg',
      'RELAY_BUILD_COMMIT=1f2028f9...', '--build-arg',
      'RELAY_BUILD_VERSION=0.3.773'], 'cwd': ...}]
    """
    box.state(suite_exit=1, suite_out="FAILED tests/test_proxy.py::test_x - "
              "AssertionError\n1 failed, 145 passed in 3.9s")
    box.health = [(200, {"ok": True, "commit": box.sha, "version": box.version})]

    r = box.run()

    out = r.stdout + r.stderr
    assert box.of("flyctl") == [], (
        f"flyctl ran although the suite failed: {box.of('flyctl')}")
    assert box.health_hits == 0, box.health_hits
    assert r.returncode != 0, _show(r)
    assert "1 failed, 145 passed" in out, _show(r)
    assert "nothing was deployed" in out, _show(r)


def test_a_suite_that_skipped_tests_deploys_nothing(box):
    """The relay suite skips its on-wire tests when the wire deps are not
    importable, and pytest exits 0. A gate that passes by skipping the only
    on-wire test is not a gate.

    MUTATION "skips allowed" (the skipped-summary test -> ``if ($false)``).
    Observed, this case alone red:
      AssertionError: flyctl ran after a suite that skipped tests: ['git',
      'git', 'git', 'python', 'flyctl', 'healthz']
    Also red under "deploy before the suite" (``['git', 'git', 'git',
    'flyctl', 'python']``).
    """
    box.state(suite_out="140 passed, 6 skipped, 5 warnings in 3.1s")
    box.health = [(200, {"ok": True, "commit": box.sha, "version": box.version})]

    r = box.run()

    out = r.stdout + r.stderr
    assert box.of("flyctl") == [], (
        f"flyctl ran after a suite that skipped tests: {box.tools()}")
    assert r.returncode != 0, _show(r)
    assert "6 skipped" in out, _show(r)


def test_a_relay_tree_with_uncommitted_changes_deploys_nothing(box):
    """flyctl builds from the WORKING tree, so an uncommitted change in
    relay/ would ship under a commit that does not contain it, and /healthz
    would then name the wrong code with full confidence.

    MUTATION "dirty tree allowed" (the dirty-tree test -> ``if ($false)``).
    Observed, this case alone red:
      AssertionError: the suite or flyctl ran on a dirty relay/ tree:
      ['git', 'git', 'git', 'python', 'flyctl', 'healthz']
    """
    box.state(dirty=" M relay/relay/server.py\n?? relay/relay/new.py\n")
    box.health = [(200, {"ok": True, "commit": box.sha, "version": box.version})]

    r = box.run()

    out = r.stdout + r.stderr
    assert not ({"python", "flyctl", "healthz"} & set(box.tools())), (
        f"the suite or flyctl ran on a dirty relay/ tree: {box.tools()}")
    assert r.returncode != 0, _show(r)
    assert "relay/relay/server.py" in out, _show(r)
    (status,) = [c for c in box.of("git") if "status" in c["args"]]
    assert status["args"][:2] == ["-C", str(_REPO)], status
    assert status["args"][-2:] == ["--", "relay"], status


def test_a_failed_flyctl_deploy_is_reported_and_not_polled_for(box):
    """flyctl's own words are the evidence, and nothing afterwards may read
    as success.

    MUTATION "flyctl exit code ignored" (the deploy's ``ExitCode -ne 0``
    test -> ``if ($false)``). Observed, this case alone red: the poll ran
    and passed, because a relay already on this commit hides a failed
    deploy:
      AssertionError: /healthz was polled after flyctl failed:
        exit=0
    """
    box.state(fly_exit=1, fly_err="no access token available. Please login "
              "with 'flyctl auth login'")
    box.health = [(200, {"ok": True, "commit": box.sha, "version": box.version})]

    r = box.run()

    out = r.stdout + r.stderr
    assert box.health_hits == 0, (
        f"/healthz was polled after flyctl failed:\n{_show(r)}")
    assert r.returncode != 0, _show(r)
    assert "no access token available" in out, _show(r)
    assert "flyctl deploy exited 1" in out, _show(r)


# ---------------------------------------------------------------------------
# tokens, and the file itself
# ---------------------------------------------------------------------------

def test_no_token_reaches_the_output(box):
    """The script never reads a token, so it cannot print one of its own;
    this pins that, and that a token-shaped string in flyctl's output is
    withheld rather than trusted never to appear (deploy output gets pasted
    into issues). The env tokens are a fake set by this test.

    MUTATION "flyctl output printed raw" (``Hide-FlyToken`` returns its
    input). Observed, this case alone red:
      AssertionError: a token-shaped string reached the output
      assert 'fm2_f6d07cb...e0a171188f77' not in 'deploying t...on 0.3.895...'
    """
    token = "FlyV1 fm2_" + hashlib.sha256(uuid.uuid4().bytes).hexdigest() * 2
    box.state(fly_out=["Using token " + token, "--> done"])
    box.health = [(200, {"ok": True, "commit": box.sha, "version": box.version})]

    r = box.run()

    out = r.stdout + r.stderr
    assert r.returncode == 0, _show(r)
    assert box.marker not in out, "the env token reached the output"
    assert token.split()[1] not in out, "a token-shaped string reached the output"
    assert "--> done" in out, _show(r)


def _code_lines(text: str) -> list[str]:
    """Non-comment lines, with block comments (<# #>) removed."""
    text = re.sub(r"(?s)<#.*?#>", "", text)
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def test_the_file_is_ascii_dot_sources_deploy_common_and_names_no_fly_secret():
    """UTF-8 without a BOM, ASCII in practice (5.1 reads a BOM-less file as
    ANSI), the shared helpers dot-sourced rather than copied, and no code
    line that could open flyctl's config or read a Fly token variable.

    MUTATION "BOM added" (EF BB BF prepended). Observed:
      AssertionError: deploy_relay.ps1 has a BOM
    MUTATION "reads the config" (a line opening flyctl's config file added
    inside ``if ($false) { }``, so the mutant could never open it, and run
    with ``-k`` on this case alone). Observed:
      AssertionError: code names flyctl's config or a token variable:
      ['if ($false) { Get-Content "$HOME\\.fly\\config.yml" | Out-Null }']
    """
    raw = _SCRIPT.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "deploy_relay.ps1 has a BOM"
    bad = [i for i, b in enumerate(raw) if b > 0x7F]
    assert not bad, f"non-ASCII bytes at offsets {bad[:10]}"
    code = _code_lines(raw.decode("ascii"))
    assert any(re.match(
        r"""\s*\.\s+\(Join-Path\s+\$PSScriptRoot\s+['"]deploy_common\.ps1['"]\)""",
        ln) for ln in code), "deploy_common.ps1 is not dot-sourced"
    hits = [ln.strip() for ln in code
            if re.search(r"(?i)config\.yml|\.fly[\\/]|\$env:FLY_|FLY_API_TOKEN"
                         r"|FLY_ACCESS_TOKEN", ln)]
    assert not hits, f"code names flyctl's config or a token variable: {hits}"
