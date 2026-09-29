"""deploy_common.ps1: the native-wheel step and the launcher's log (#403).

Every deploy_<ver>.ps1 is copied from the one before it, so a trap in one is
inherited by every script after it. Two reached 0.3.35 (#403):

1. The native wheel was force-reinstalled BEFORE the server was stopped. The
   running server has astrodeck_native's .pyd loaded, Windows will not let pip
   replace a loaded DLL, and the step failed.
2. The detached run's log ended at that step's header with no error text: the
   terminating error propagated out of ``& script *> log`` instead of through
   it, so the failure read as a silent exit.

These drive the real ``scripts/deploy_common.ps1`` in powershell.exe. What is
stubbed, all under the test's own temp dir:

* python -- a throwaway venv made ``--without-pip``. The interpreter is real,
  so the snippet that reads the installed wheel's recorded hash really runs,
  through Windows PowerShell 5.1's native argument passing, against a real
  ``.dist-info`` that importlib.metadata has to find.
* pip -- a fake ``pip`` package in that venv. It logs every call and writes
  the ``direct_url.json`` real pip writes: ``archive_info.hash`` and
  ``archive_info.hashes.sha256``, the shape pip 25.0.1 and 26.2.1 were both
  seen writing on 2026-09-28, for a plain path and for a ``#sha256=`` URL.
* Get-NetTCPConnection -- a PowerShell function defined after the dot-source,
  which shadows the real one. Each call is logged, and every case that needs
  the port consulted asserts that it was, so a harness that silently reached
  the real cmdlet (and whatever this dev box has on 8800) cannot pass.

Nothing here contacts the rig.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="drives powershell.exe and a Windows venv layout")

_COMMON = Path(__file__).resolve().parents[2] / "scripts" / "deploy_common.ps1"
_DIST = "astrodeck_native"
_WHEEL_NAME = "astrodeck_native-0.1.0-cp311-abi3-win_amd64.whl"

# The fake pip. It runs as ``python -m pip`` inside the throwaway venv, so
# ``sys.prefix`` is that venv and the stub's state sits beside it.
_FAKE_PIP = r'''
import hashlib, json, sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

state = Path(sys.prefix) / "stub-state"
with open(state / "pip_calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
mode = (state / "pip_mode").read_text(encoding="utf-8").strip()
if mode == "fail":
    # Shaped like pip's report of a file it could not replace.
    sys.stderr.write("ERROR: Could not install packages due to an OSError: "
                     "[WinError 5] Access is denied: 'astrodeck_native.pyd'\n")
    sys.exit(1)
if mode == "noop":
    # Exit 0 and change nothing, which is what pip does when the version
    # already matches and --force-reinstall is missing.
    print("Requirement already satisfied: astrodeck_native")
    sys.exit(0)
req = [a for a in sys.argv[1:] if not a.startswith("-") and a != "install"][-1]
if " @ " in req:
    name, _, url = req.partition(" @ ")
    parts = urlsplit(url)
    path, fragment = Path(url2pathname(unquote(parts.path))), parts.fragment
    url = parts._replace(fragment="").geturl()
else:
    path, fragment = Path(req), ""
    name, url = path.name.split("-")[0], path.resolve().as_uri()
digest = hashlib.sha256(path.read_bytes()).hexdigest()
if fragment and fragment != "sha256=" + digest:
    sys.stderr.write("ERROR: THESE PACKAGES DO NOT MATCH THE HASHES\n")
    sys.exit(1)
dist = Path(sys.prefix) / "Lib" / "site-packages" / (name + "-0.1.0.dist-info")
dist.mkdir(exist_ok=True)
(dist / "METADATA").write_text(
    "Metadata-Version: 2.1\nName: %s\nVersion: 0.1.0\n" % name)
(dist / "direct_url.json").write_text(json.dumps({
    "archive_info": {"hash": "sha256=" + digest, "hashes": {"sha256": digest}},
    "url": url}))
print("Successfully installed %s-0.1.0" % name)
'''

# What the Get-NetTCPConnection stub does for each port state. "stopped"
# answers the way the real cmdlet does when nothing listens: an ERROR, not an
# empty result, with category ObjectNotFound and FullyQualifiedErrorId
# "CmdletizationQuery_NotFound,Get-NetTCPConnection" (both read off the real
# one on this box, and this stub's error reports the same two). "missing" is
# a cmdlet that is not there or failed to load: CommandNotFoundException,
# whose category is ObjectNotFound as well, so only the id tells it apart.
_NET_STUB = {
    "listening": ("[pscustomobject]@{ LocalPort = $LocalPort; "
                  "State = 'Listen'; OwningProcess = 4242 }"),
    "stopped": ("Write-Error -Category ObjectNotFound "
                "-ErrorId 'CmdletizationQuery_NotFound' -Message "
                "(\"No matching MSFT_NetTCPConnection objects found by CIM \" + "
                "\"query ... WHERE ((LocalPort = $LocalPort)) AND ((State = 2))\")"),
    "broken": ("Write-Error -Category PermissionDenied -Message "
               "'Access denied (the CIM provider refused the query)'"),
    "missing": "Get-NoSuchNetCmdlet -LocalPort $LocalPort",
}

# Every marker a case looks for in a log is COMPUTED in the body, e.g.
# ``Write-Output ('step one ' + 'ran')``. 5.1 can render an error record with
# the source of the scriptblock that raised it, and a literal marker would
# then be in the log whether or not its line ever ran.
_HARNESS = r"""
$ErrorActionPreference = '@EAP@'
. '@COMMON@'
function Get-NetTCPConnection {
    [CmdletBinding()]
    param([int]$LocalPort, [string]$State)
    Add-Content -LiteralPath '@NETLOG@' -Value "$LocalPort $State"
    @NET@
}
$Py = '@PY@'
$Wheel = '@WHEEL@'
@BODY@
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_log(path: Path) -> str:
    """The log as text, whatever Windows PowerShell wrote it in (``*>>`` and
    Out-File write UTF-16LE with a BOM in 5.1, UTF-8 in 7)."""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", errors="replace")


def _show(r: subprocess.CompletedProcess) -> str:
    return f"exit={r.returncode}\n--- stdout\n{r.stdout}\n--- stderr\n{r.stderr}"


def _ps(argv_tail: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    # -ExecutionPolicy Bypass because dot-sourcing a repo .ps1 is otherwise
    # refused by the default machine policy, and that refusal exits non-zero
    # too, which would satisfy a case that only checked the exit code. Every
    # case below also asserts on words or on the stub logs for that reason.
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", *argv_tail],
        capture_output=True, text=True, timeout=timeout)


@dataclass
class Box:
    root: Path
    py: Path
    site: Path
    state: Path
    wheel: Path
    want: str
    net_log: Path

    @property
    def dist_info(self) -> Path:
        return self.site / f"{_DIST}-0.1.0.dist-info"

    def seed_installed(self, sha256: str | None,
                       keys: tuple[str, ...] = ("hash", "hashes")) -> None:
        """Leave a dist-info in the venv as pip would have. ``None`` leaves
        one with no ``direct_url.json``: installed, but no hash recorded.
        ``keys`` picks which of pip's two hash keys are written."""
        self.dist_info.mkdir(exist_ok=True)
        (self.dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {_DIST}\nVersion: 0.1.0\n")
        if sha256 is not None:
            info = {"hash": f"sha256={sha256}", "hashes": {"sha256": sha256}}
            (self.dist_info / "direct_url.json").write_text(json.dumps({
                "archive_info": {k: info[k] for k in keys},
                "url": self.wheel.as_uri()}))

    def recorded(self) -> str | None:
        f = self.dist_info / "direct_url.json"
        if not f.exists():
            return None
        info = json.loads(f.read_text())["archive_info"]
        if "hashes" in info:
            return info["hashes"]["sha256"]
        return info["hash"].partition("sha256=")[2]

    def pip_mode(self, mode: str) -> None:
        (self.state / "pip_mode").write_text(mode)

    def pip_calls(self) -> list[list[str]]:
        text = (self.state / "pip_calls.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def net_calls(self) -> list[str]:
        if not self.net_log.exists():
            return []
        return [ln.strip() for ln in self.net_log.read_text().splitlines()
                if ln.strip()]

    def run(self, body: str, *, port: str = "stopped",
            caller_eap: str = "Stop") -> subprocess.CompletedProcess:
        return _ps(["-File", str(self.harness(body, port=port,
                                              caller_eap=caller_eap))])

    def harness(self, body: str, *, port: str = "stopped",
                caller_eap: str = "Stop") -> Path:
        text = (_HARNESS
                .replace("@EAP@", caller_eap)
                .replace("@COMMON@", str(_COMMON))
                .replace("@NETLOG@", str(self.net_log))
                .replace("@NET@", _NET_STUB[port])
                .replace("@PY@", str(self.py))
                .replace("@WHEEL@", str(self.wheel))
                .replace("@BODY@", body))
        path = self.root / f"harness-{uuid.uuid4().hex[:8]}.ps1"
        # A BOM on this throwaway file (not on the script under test) so
        # Windows PowerShell 5.1 decodes it right whatever the temp path holds.
        path.write_text(text, encoding="utf-8-sig")
        return path


@pytest.fixture
def box(tmp_path: Path) -> Box:
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   check=True, capture_output=True, timeout=120)
    site = venv / "Lib" / "site-packages"
    (site / "pip").mkdir()
    (site / "pip" / "__init__.py").write_text("")
    (site / "pip" / "__main__.py").write_text(_FAKE_PIP)
    state = venv / "stub-state"
    state.mkdir()
    (state / "pip_mode").write_text("install")
    (state / "pip_calls.jsonl").write_text("")
    wheel = tmp_path / _WHEEL_NAME
    data = b"not a real wheel; only its sha256 matters " + uuid.uuid4().bytes
    wheel.write_bytes(data)
    return Box(root=tmp_path, py=venv / "Scripts" / "python.exe", site=site,
               state=state, wheel=wheel, want=_sha(data),
               net_log=tmp_path / "net_calls.txt")


_INSTALL = "Install-NativeWheelIfChanged -Python $Py -Wheel $Wheel"
_OTHER = _sha(b"the wheel 0.3.34 installed")


# ---------------------------------------------------------------------------
# (a) the native-wheel step
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("port", ["stopped", "listening"])
def test_the_same_wheel_is_not_reinstalled(box, port):
    """The 0.3.35 case: the wheel on the box is the one already installed.
    No pip call, whether or not the server is up, and no refusal either,
    because there is nothing to replace.

    MUTATION "reinstall on equal hash" (the equal-hash early return made
    unreachable: ``if ($have -eq $want)`` -> ``if ($false)``). Observed,
    run from a private copy on 2026-09-28:
      [stopped]   AssertionError: pip ran for a wheel the venv already has:
                  exit=0 ... native wheel changed: the venv records
                  29a8be051d8518eb..., the wheel is 29a8be051d8518eb...
      [listening] AssertionError: exit=1 ... refusing to reinstall the
                  native wheel while port 8800 is listening (pid 4242)
    """
    box.seed_installed(box.want)

    r = box.run(_INSTALL, port=port)

    assert r.returncode == 0, _show(r)
    assert box.pip_calls() == [], (
        f"pip ran for a wheel the venv already has:\n{_show(r)}")
    assert "unchanged" in r.stdout, _show(r)
    assert box.recorded() == box.want


@pytest.mark.parametrize("key", ["hash", "hashes"])
def test_the_recorded_hash_is_read_from_either_key(box, key):
    """The same wheel, recorded under only ONE of pip's two keys. The pips
    seen here write both, which is all the other cases seed, so under them
    either branch of the reader could go and nothing would notice. The rig's
    pip version is unverified, an older pip wrote only ``hash``, and the
    spec's current form is ``hashes``. A key the reader cannot see makes
    every deploy read as "changed": a reinstall each time after the stop,
    and a refusal wherever the step sits before it.

    MUTATION "hash key ignored" (``str(a.get('hash') or '')`` ->
    ``str('')`` in the reader). Observed, [hash] only:
      AssertionError: the venv records this wheel under 'hash' alone and
      pip ran anyway: exit=0 ... native wheel changed: the venv records no
      hash, the wheel is e093796da36f23de...
        | Successfully installed astrodeck_native-0.1.0
    MUTATION "hashes key ignored" (``(a.get('hashes') or {}).get('sha256')
    or`` deleted from the reader). Observed, [hashes] only:
      AssertionError: the venv records this wheel under 'hashes' alone and
      pip ran anyway: exit=0 ... native wheel changed: the venv records no
      hash, the wheel is 086a4737a16d33c0...
        | Successfully installed astrodeck_native-0.1.0
    Both cases also go red under "reinstall on equal hash".
    """
    box.seed_installed(box.want, keys=(key,))

    r = box.run(_INSTALL, port="stopped")

    assert r.returncode == 0, _show(r)
    assert box.pip_calls() == [], (
        f"the venv records this wheel under {key!r} alone and pip ran "
        f"anyway:\n{_show(r)}")
    assert "unchanged" in r.stdout, _show(r)


def test_a_changed_wheel_is_refused_in_words_while_the_port_listens(box):
    """The trap itself: a new wheel, and the server still up. The step must
    refuse, say why, and never reach pip.

    MUTATION "reinstall before the stop" (the pip call moved ahead of the
    listener gate, so pip runs and the refusal comes after it). Observed:
      AssertionError: pip ran while port 8800 was listening: exit=1
      ... native wheel changed: the venv records 73b947d3cf037a59..., the
      wheel is 4cfcdaf5963101ab...
    The exit code is still non-zero and the refusal still prints under this
    mutant, which is why the case asserts on the pip log and not only on
    the words.
    """
    box.seed_installed(_OTHER)

    r = box.run(_INSTALL, port="listening")

    out = r.stdout + r.stderr
    assert r.returncode != 0, _show(r)
    assert box.pip_calls() == [], (
        f"pip ran while port 8800 was listening:\n{_show(r)}")
    assert "refusing" in out and "8800" in out and "4242" in out, _show(r)
    assert box.net_calls() == ["8800 Listen"], (
        "the stub was not consulted, so the real cmdlet answered: "
        f"{box.net_calls()!r}")
    assert box.recorded() == _OTHER


@pytest.mark.parametrize("installed", ["other hash", "no hash recorded",
                                       "not installed"])
def test_a_changed_wheel_with_the_server_stopped_is_installed_exactly_once(
        box, installed):
    """The positive case, from each state the venv can be in: exactly one
    pip call, pinned to this wheel's hash, and the venv then records it.

    "no hash recorded" and "not installed" are unknowns, and an unknown is
    not "the same": both reinstall, which is safe once the server is down.

    Control for "reinstall before the stop": with nothing listening the
    moved pip call changes nothing, and this case stays green under it.
    """
    if installed == "other hash":
        box.seed_installed(_OTHER)
    elif installed == "no hash recorded":
        box.seed_installed(None)

    r = box.run(_INSTALL, port="stopped")

    assert r.returncode == 0, _show(r)
    calls = box.pip_calls()
    assert len(calls) == 1, f"expected exactly one pip call, got {calls!r}"
    (args,) = calls
    assert args[0] == "install", args
    assert "--force-reinstall" in args and "--no-deps" in args, args
    assert args[-1].startswith(f"{_DIST} @ file:///"), args
    assert args[-1].endswith(f"#sha256={box.want}"), args
    assert box.recorded() == box.want
    assert box.net_calls() == ["8800 Listen"], box.net_calls()
    assert "reinstalled" in r.stdout, _show(r)


@pytest.mark.parametrize("port", ["broken", "missing"])
def test_a_port_query_that_fails_is_not_read_as_stopped(box, port):
    """"Is the server stopped?" must be answered, not assumed. The real
    cmdlet reports "nothing listens" as a CmdletizationQuery_NotFound error;
    any OTHER error is no answer, and reading it as "stopped" opens the gate
    on a guess. [missing] is a cmdlet that is not there or did not load:
    its error shares the ObjectNotFound category with "nothing listens".

    MUTATION "port query errors read as stopped" (``-ErrorAction Stop`` ->
    ``SilentlyContinue`` on the query, so the catch never sees the error).
    Observed, [broken]:
      AssertionError: exit=0 ... native wheel changed: the venv records
      73b947d3cf037a59..., the wheel is 822a648888966b38...
        | Successfully installed astrodeck_native-0.1.0
    Also red under "reinstall before the stop" (observed: "AssertionError:
    pip ran on a guess: exit=1").

    MUTATION "gate keyed on the category" (the catch's test put back to
    ``$_.CategoryInfo.Category -eq 'ObjectNotFound'``, as first written).
    Observed, [missing] only:
      AssertionError: exit=0 ... native wheel changed: the venv records
      73b947d3cf037a59..., the wheel is bd2c9d5c05723a58...
        | Successfully installed astrodeck_native-0.1.0
      assert 0 != 0
    """
    box.seed_installed(_OTHER)

    r = box.run(_INSTALL, port=port)

    out = r.stdout + r.stderr
    assert r.returncode != 0, _show(r)
    assert box.pip_calls() == [], f"pip ran on a guess:\n{_show(r)}"
    assert "could not tell whether port 8800" in out, _show(r)
    assert box.net_calls() == ["8800 Listen"], box.net_calls()


def test_a_pip_failure_stops_the_step_with_pips_own_words(box):
    """pip's own report is the evidence, so it must survive into the error.

    MUTATION "pip exit code ignored" (the ``ExitCode -ne 0`` throw deleted).
    Observed: the post-install check threw instead, so the verdict named the
    wrong cause:
      AssertionError: exit=1 ... pip exited 0 but the venv still records
      sha256 '73b947d3cf037a59e2a6...', not ...
      assert 'pip install exited 1' in ...

    MUTATION "stderr is fatal inside Invoke-DeployNative" (its local
    ``$ErrorActionPreference = 'Continue'`` deleted, so the caller's Stop
    applies to the folded stderr). Observed: pip's first stderr line became
    the error, which is the 0.3.35 shape exactly:
      AssertionError: exit=1 ... python.exe : ERROR: Could not install
      packages due to an OSError: [WinError 5] Access is denied:
      'astrodeck_native.pyd' ... FullyQualifiedErrorId : NativeCommandError
      assert 'pip install exited 1' in ...
    """
    box.seed_installed(_OTHER)
    box.pip_mode("fail")

    r = box.run(_INSTALL, port="stopped")

    out = r.stdout + r.stderr
    assert r.returncode != 0, _show(r)
    assert "pip install exited 1" in out, _show(r)
    assert "Access is denied" in out, _show(r)
    assert len(box.pip_calls()) == 1


def test_pip_claiming_success_without_installing_is_caught(box):
    """A pip that exits 0 and replaces nothing (a later copy that loses
    --force-reinstall gets exactly that) must not pass for an install.

    MUTATION "no post-install check" (the re-read after pip deleted).
    Observed:
      AssertionError: exit=0 ... native wheel changed: the venv records
      73b947d3cf037a59..., the wheel is c5dede46c2a8e340...
        | Requirement already satisfied: astrodeck_native
    """
    box.seed_installed(_OTHER)
    box.pip_mode("noop")

    r = box.run(_INSTALL, port="stopped")

    out = r.stdout + r.stderr
    assert r.returncode != 0, _show(r)
    assert "still records" in out, _show(r)
    assert len(box.pip_calls()) == 1


# ---------------------------------------------------------------------------
# (b) the logged launcher
# ---------------------------------------------------------------------------

def test_a_body_that_throws_leaves_its_error_in_the_log_and_exits_1(box):
    """The second half of #403: the error that stops the deploy is in the
    log, after everything the body printed, and the process exits 1.

    MUTATION "catch writes nothing" (the catch's Out-File deleted, leaving
    only ``exit 1``). Observed: the #403 log, output and no error:
      AssertionError: the error that stopped the body is not in the log:
        step one ran
      assert 'boom bfb86c0c5f15435e8b0d0f920e5aa64a' in 'step one ran\\r\\n'

    MUTATION "catch does not exit" (the catch's ``exit 1`` deleted).
    Observed:
      AssertionError: exit=0 --- stdout AFTER THE WRAPPER
    """
    marker = uuid.uuid4().hex
    log = box.root / "deploy.log"

    r = box.run(
        f"Invoke-LoggedDeploy -Log '{log}' -Body {{\n"
        f"    Write-Output ('step one ' + 'ran')\n"
        f"    throw ('boom ' + '{marker}')\n"
        f"}}\n"
        f"Write-Output ('AFTER THE ' + 'WRAPPER')\n")

    assert r.returncode == 1, _show(r)
    text = _read_log(log)
    assert "step one ran" in text, text
    assert f"boom {marker}" in text, (
        f"the error that stopped the body is not in the log:\n{text}")
    assert "DEPLOY FAILED" in text, text
    assert text.index("step one ran") < text.index(f"boom {marker}"), text
    assert "AFTER THE WRAPPER" not in r.stdout + text, _show(r)


def test_a_body_that_succeeds_is_logged_and_the_script_carries_on(box):
    """Control: nothing fails, so nothing says it did, the exit code is 0,
    and the script goes on past the wrapper.

    MUTATION "exit 1 outside the catch" (``exit 1`` moved after the
    try/catch, so it runs on success too). Observed: only this case went
    red, as a control should:
      AssertionError: exit=1

    MUTATION "body not redirected into the log" (``& $Body *>> $Log`` ->
    ``& $Body``). Observed: the log was never created:
      FileNotFoundError: [Errno 2] No such file or directory: '...deploy.log'
    (and in the throwing case: ``assert "step one ran" in text`` failed on
    a log holding only the DEPLOY FAILED record).

    The log is seeded with an earlier attempt's line, because the body's
    redirection APPENDS: a rerun after a failed deploy keeps the first
    attempt's record. MUTATION "the body truncates the log" (``*>>`` ->
    ``*>``). Observed, only this case red:
      AssertionError: an earlier attempt's record was overwritten:
        step one ran
        a host line
        DEPLOY_DONE
    """
    log = box.root / "deploy.log"
    # In the encoding `*>>` itself writes in 5.1 (UTF-16LE with a BOM), so
    # the appended lines continue the same file rather than a mixed one.
    log.write_bytes("an earlier attempt\r\n".encode("utf-16"))

    r = box.run(
        f"Invoke-LoggedDeploy -Log '{log}' -Body {{\n"
        f"    Write-Output ('step one ' + 'ran')\n"
        f"    Write-Host ('a host ' + 'line')\n"
        f"    Write-Output ('DEPLOY_' + 'DONE')\n"
        f"}}\n"
        f"Write-Output ('AFTER THE ' + 'WRAPPER')\n")

    assert r.returncode == 0, _show(r)
    text = _read_log(log)
    assert text.startswith("an earlier attempt"), (
        f"an earlier attempt's record was overwritten:\n{text}")
    for line in ("step one ran", "a host line", "DEPLOY_DONE"):
        assert line in text, f"{line!r} is not in the log:\n{text}"
    assert "DEPLOY FAILED" not in text, text
    assert "AFTER THE WRAPPER" in r.stdout, _show(r)


def test_a_non_terminating_error_stops_the_body_even_under_continue(box):
    """The wrapper, not the caller's preference, decides that the first
    error stops the deploy: a Write-Error must not be logged and stepped
    over on the way to flipping 'current'.

    MUTATION "no Stop preference in the wrapper" (its
    ``$ErrorActionPreference = 'Stop'`` deleted). Observed:
      AssertionError: exit=0
    Also red under "catch writes nothing" (``assert 'soft ...' in ''``: the
    Write-Error became terminating and nothing wrote it down) and under
    "catch does not exit" (``AssertionError: exit=0``).
    """
    marker = uuid.uuid4().hex
    log = box.root / "deploy.log"

    # The line after the error is built by concatenation because 5.1 renders
    # a Write-Error record with the SOURCE of the scriptblock that raised it,
    # so a literal would be in the log whether or not the line ever ran.
    r = box.run(
        f"Invoke-LoggedDeploy -Log '{log}' -Body {{\n"
        f"    Write-Error 'soft {marker}'\n"
        f"    Write-Output ('stepped over ' + 'the error')\n"
        f"}}\n",
        caller_eap="Continue")

    text = _read_log(log)
    assert r.returncode == 1, _show(r)
    assert f"soft {marker}" in text, text
    assert "stepped over the error" not in text, text


def test_the_403_shape_the_refusal_reaches_the_log(box):
    """Both halves together, in the shape that failed on 2026-09-27: the
    wheel step inside the logged body, a changed wheel, the server still
    up. The refusal's words are in the log, pip never ran, exit 1.

    Red under "catch writes nothing", observed:
      AssertionError: the refusal is not in the log:
        == the native wheel ==
           native wheel changed: the venv records 73b947d3cf037a59..., the
           wheel is a6a69a965e9eec51...
    and under "reinstall before the stop", observed:
      AssertionError: [['install', '--force-reinstall', '--no-deps',
      '--no-index', '--disable-pip-version-check', '--no-input', ...]]
    """
    box.seed_installed(_OTHER)
    log = box.root / "deploy_0336.log"

    r = box.run(
        f"Invoke-LoggedDeploy -Log '{log}' -Body {{\n"
        f"    Write-Output ('== the native ' + 'wheel ==')\n"
        f"    {_INSTALL}\n"
        f"    Write-Output ('NOT ' + 'REACHED')\n"
        f"}}\n",
        port="listening")

    text = _read_log(log)
    assert r.returncode == 1, _show(r)
    assert "== the native wheel ==" in text, text
    assert "refusing" in text, f"the refusal is not in the log:\n{text}"
    assert "NOT REACHED" not in text, text
    assert box.pip_calls() == [], box.pip_calls()


def test_the_documented_detached_launch_keeps_the_error(box):
    """The header's launch recipe, run the way the rig runs it: through
    ``Invoke-CimMethod Win32_Process Create``, with no console and no
    redirection of its own. The log is the only witness, so it must hold
    the error.

    Red under "catch writes nothing", observed:
      AssertionError: the detached run's log lost the error, as in #403:
        detached step ran
      assert 'detached boom 468d7edffbbd4431b77ddc7b47984d58' in
          'detached step ran\\r\\n'
    """
    marker = uuid.uuid4().hex
    log = box.root / "detached.log"
    harness = box.harness(
        f"Invoke-LoggedDeploy -Log '{log}' -Body {{\n"
        f"    Write-Output ('detached step ' + 'ran')\n"
        f"    throw ('detached boom ' + '{marker}')\n"
        f"}}\n")
    launcher = box.root / "launch.ps1"
    launcher.write_text(
        "$cmd = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy "
        f"Bypass -File \"{harness}\"'\n"
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        "-Arguments @{ CommandLine = $cmd }\n"
        "if ($r.ReturnValue -ne 0) { throw \"Create returned $($r.ReturnValue)\" }\n"
        "$deadline = (Get-Date).AddSeconds(90)\n"
        "while ((Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue) "
        "-and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }\n"
        "if (Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue) "
        "{ throw 'the detached run did not finish' }\n"
        "Write-Output \"DETACHED_PID=$($r.ProcessId)\"\n",
        encoding="utf-8-sig")

    r = _ps(["-File", str(launcher)], timeout=150)

    assert r.returncode == 0 and "DETACHED_PID=" in r.stdout, _show(r)
    text = _read_log(log)
    assert "detached step ran" in text, text
    assert f"detached boom {marker}" in text, (
        f"the detached run's log lost the error, as in #403:\n{text}")


# ---------------------------------------------------------------------------
# the file itself
# ---------------------------------------------------------------------------

def test_the_file_is_ascii_without_a_bom_and_says_to_dot_source_it():
    """UTF-8 without a BOM (the project's rule for source), and ASCII in
    practice: Windows PowerShell 5.1 reads a BOM-less file as ANSI, so any
    non-ASCII character in it would be decoded as something else.

    MUTATION "BOM added" (EF BB BF prepended). Observed:
      AssertionError: deploy_common.ps1 has a BOM
    MUTATION "one em dash in a comment" (in the first line). Observed:
      AssertionError: non-ASCII bytes at offsets [20, 21, 22]
    """
    raw = _COMMON.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "deploy_common.ps1 has a BOM"
    bad = [i for i, b in enumerate(raw) if b > 0x7F]
    assert not bad, f"non-ASCII bytes at offsets {bad[:10]}"
    header = []
    for line in raw.decode("ascii").splitlines():
        if not line.startswith("#"):
            break
        header.append(line)
    assert "dot-source" in "\n".join(header), (
        "the header does not tell the next per-version script to dot-source "
        "this file")
