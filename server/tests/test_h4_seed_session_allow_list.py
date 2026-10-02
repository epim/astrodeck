# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""seed_session.py writes only a directory the probe's own server made (#539).

tools/ui_probe/seed_session.py moves a flow's dormant session onto an earlier
night, and with ``--arm`` it turns that session's ``auto_resume`` on and every
other session's off. It exists for the UI probe's private simulator server
(#189 S7 item 1). Its guard was a deny-list: it refused the checkout's own
``server/`` and ``captures/`` and accepted every other directory, a real
install's config and captures among them. Pointed at those, ``--arm`` hands
ResumeArm a dormant armed session to start at its next tick after dark, which
is unattended mount motion (spec 2026-09-23, Revision 2, ruling 7), and the
docstring's safety argument (a daylight site, so no window opens) holds only
for the probe's own server.

The guard is now an allow-list. ``server_ctl.py start`` writes a marker,
``.astrodeck-probe``, holding the port and pid of the server it started and
the directory itself, into each config and capture directory it creates.
seed_session.py refuses a directory without one before it reads anything in
it, a marker naming the rig's port 8800, and a marker written for another
directory (a probe directory copied somewhere else). The guard reads the
marker from disk and asks no server anything.

It lives in the server suite, not beside the probe, because the probe's own
unittest run is manual (#479, S7 orchestrator ruling 8). It needs only the
server's venv: a refused run imports nothing of astrodeck, and the store the
script is pointed at is written here, by the store's own code.

The two scripts are loaded from their files, not imported by name: they are
standalone scripts, and putting tools/ui_probe on this worker's ``sys.path``
would let every later test import ``probe`` or ``server_ctl`` by accident.

Each case names the mutation of seed_session.py or server_ctl.py that turns it
red and the failure observed when that mutation was run in a private copy of
this file, its conftest and the two scripts, laid out as in the checkout
(session scratchpad H4-PROBE-GUARD-mut, 2026-09-29, whose mutate.py applies
each from a byte backup). The copy ran under the real venv, and its children
ran the copy's scripts. The acceptance's named mutant is "deny-list restored".
The verifier's mutations (of `_dirs_to_mark` and `_marked_here`, and the
server_ctl.py fix the last case guards) ran the same way in the scratchpad's
H4-PROBE-GUARD-verify-mut.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = REPO_ROOT / "tools" / "ui_probe"
SEED_SCRIPT = PROBE_DIR / "seed_session.py"


def _load(name: str):
    """``tools/ui_probe/<name>.py`` as a module of its own, under a name no
    other test uses, and never registered on ``sys.path``."""
    spec = importlib.util.spec_from_file_location(f"h4_ui_probe_{name}",
                                                  PROBE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_session = _load("seed_session")
server_ctl = _load("server_ctl")

#: Any port but the rig's, as a probe server's always is.
PROBE_PORT = 8871
#: A pid no marker check may care about beyond its shape.
FAKE_PID = 424242
FLOW = "h4-flow"


# ------------------------------------------------------------------ helpers

def _stored_night(cfg: Path, cap: Path) -> dict:
    """A config file and a capture store as an install holds them: a dormant
    flow session of one run started now, with two frames and its report, and
    an armed session of another flow. Written by the store's own code, with
    ``hub.CAPTURE_DIR`` pointed at ``cap`` for the writes alone, so what the
    script would find is what a server would have left."""
    import astrodeck.hub as hub_mod
    from astrodeck.persist import write_json_atomic
    from astrodeck.sequence import report as report_mod
    from astrodeck.sequence.models import SequencePlan
    from astrodeck.sequence.session import Session, SessionFrame, session_store

    now = time.time()
    run = "h4-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    (cfg / "astrodeck.json").write_text('{"note": "stands in for a real config"}',
                                        encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(hub_mod, "CAPTURE_DIR", cap)
        other = Session(name="other", status="dormant", auto_resume=True,
                        created_ts=now - 9e5, plan=SequencePlan(name="other"),
                        origin="flow", origin_id="h4-other")
        session_store.save(other)
        s = Session(name="h4", status="dormant", created_ts=now - 60,
                    plan=SequencePlan(name="h4"), nights=[run], origin="flow",
                    origin_id=FLOW,
                    frames=[SessionFrame(ts=now - 30, night=run, step_id="a"),
                            SessionFrame(ts=now - 10, night=run, step_id="a")])
        session_store.save(s)
        write_json_atomic(report_mod._reports_dir() / f"{run}.json",
                          report_mod.SessionReport(
                              id=run, plan_name="h4", started_at=now - 60,
                              frames=[report_mod.FrameRecord(ts=now - 30, target="t1")],
                          ).model_dump())
    return {"session": s.id, "other": other.id, "run": run}


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """Every file under ``root``: its mtime in ns and a digest of its bytes."""
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns,
                                       hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*")) if p.is_file()}


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


#: Run by the server's venv: seed_session.py as `python seed_session.py`
#: runs it (its own folder first on the path, ``__main__``), with every
#: ``open`` of a path under the watched folder written down: builtins.open,
#: io.open (pathlib's read_text, write_text and open) and os.open
#: (tempfile's, so write_json_atomic's). The list goes to a file outside the
#: watched folder once the script exits, however it exits.
SPY = r'''
import builtins, io, json, os, runpy, sys
watch, report, script = sys.argv[1], sys.argv[2], sys.argv[3]
watch = os.path.normcase(os.path.abspath(watch))
seen = []
def note(path):
    try:
        p = os.path.normcase(os.path.abspath(os.fspath(path)))
    except TypeError:
        return
    if p == watch or p.startswith(watch + os.sep):
        seen.append(p)
real_open, real_io_open, real_os_open = builtins.open, io.open, os.open
def spy_open(file, *a, **k):
    note(file)
    return real_open(file, *a, **k)
def spy_os_open(path, *a, **k):
    note(path)
    return real_os_open(path, *a, **k)
builtins.open = io.open = spy_open
os.open = spy_os_open
sys.argv = [script] + sys.argv[4:]
sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code
finally:
    builtins.open, io.open, os.open = real_open, real_io_open, real_os_open
    with open(report, "w", encoding="utf-8") as f:
        json.dump(seen, f)
sys.exit(code)
'''


def _run_seed(tmp_path: Path, install: Path, cfg: Path, cap: Path,
              *extra: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    """seed_session.py under this venv against ``cfg`` and ``cap``, and every
    path under ``install`` it opened."""
    report = tmp_path / "opened.json"
    done = subprocess.run(
        [sys.executable, "-c", SPY, str(install), str(report), str(SEED_SCRIPT),
         "--config-dir", str(cfg), "--capture-dir", str(cap), "--flow", FLOW,
         "--nights-ago", "1", *extra],
        capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    opened = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    assert opened is not None, f"the spy wrote no report: {done.stderr[-800:]}"
    return done, opened


@pytest.fixture
def install(tmp_path: Path) -> tuple[Path, Path, Path]:
    """``(root, cfg, cap)``: a config and a capture directory OUTSIDE the
    checkout holding a stored night, and no marker in either."""
    root = (tmp_path / "install").resolve()
    cfg, cap = root / "cfg", root / "cap"
    cfg.mkdir(parents=True)
    cap.mkdir()
    assert not root.is_relative_to(REPO_ROOT.resolve()), root
    return root, cfg, cap


# ------------------------------------------------------ the script, end to end

@pytest.mark.parametrize("marked,extra", [
    ((), ()),
    ((), ("--arm",)),
    (("cfg",), ("--arm",)),
    (("cap",), ("--arm",)),
], ids=["neither-marked", "neither-marked-arm", "config-only-arm", "capture-only-arm"])
def test_an_unmarked_directory_is_refused_before_anything_in_it_is_read(
        tmp_path, install, marked, extra):
    """A config and a capture directory outside the checkout, holding a
    dormant session and an armed one as a real install would, with the
    marker in neither (or in one of the two only): the script exits 2 and
    says why, prints nothing on stdout, opens no file but a marker (and none
    at all in an unmarked directory), and every file keeps its bytes and its
    mtime. With ``--arm`` the same: the armed session stays the armed one.

    Mutation "deny-list restored" (seed_session.py `_private` given back its
    old body, the `_DEVELOPERS_OWN` loop, and the allow-list left unused),
    observed red on all four cases, each with the session moved; one:
        AssertionError: seed_session exited 0: ''
        assert 0 == 2
         +  where 0 = CompletedProcess(args=[...], ...n_saved_ts": null,
         "reports": [{"id": "h4-20260929-063851", "moved": true,
         "to": "h4-20260928-063851"}]}\\n', stderr='').returncode
    (HEAD's own code, before this change, failed the same way.)
    Mutation "only --config-dir checked" (seed_session.py `main`:
    ``cap = Path(args.capture_dir).resolve()`` in place of `_private`),
    observed red on config-only-arm alone, the same failure; mutation
    "only --capture-dir checked" (the same for ``cfg``), on capture-only-arm
    alone, the same failure.
    Mutation "store read before the guard" (seed_session.py `main`: the two
    directories set in the environment and ``session_store.load_all()``
    called before `_private`), observed red on all four at the spy, the
    exit code and the refusal being right; one:
        AssertionError: assert ['c:\\\\users\\\\...cb0380e.json'] == []
          Left contains 2 more items, first extra item: '<tmp>\\\\install\\\\cap
          \\\\sessions\\\\61859b446aa04caa801b3bff29c82da2.json'"""
    root, cfg, cap = install
    _stored_night(cfg, cap)
    dirs = {"cfg": cfg, "cap": cap}
    for name in marked:
        server_ctl.write_probe_marker(dirs[name], PROBE_PORT, FAKE_PID)
    unmarked = [d for name, d in dirs.items() if name not in marked]
    before = _snapshot(root)

    done, opened = _run_seed(tmp_path, root, cfg, cap, *extra)

    assert done.returncode == 2, f"seed_session exited {done.returncode}: {done.stderr[-600:]!r}"
    assert "seed_session: REFUSED" in done.stderr, done.stderr[-600:]
    assert seed_session.PROBE_MARKER in done.stderr, done.stderr[-600:]
    assert done.stdout == ""
    assert [p for p in opened
            if any(p.startswith(_norm(d) + os.sep) for d in unmarked)] == []
    assert [p for p in opened if Path(p).name != seed_session.PROBE_MARKER] == []
    assert _snapshot(root) == before


def test_a_directory_server_ctl_marked_is_accepted_and_the_spy_sees_the_store(
        tmp_path, install):
    """CONTROL for the case above: the same store, both directories marked
    as ``server_ctl.py start`` marks them. The script moves the session and
    arms it, and the spy, the one that saw nothing above, saw the session
    files opened, so its silence there is the script's and not its own. The
    markers are read and never rewritten."""
    root, cfg, cap = install
    stored = _stored_night(cfg, cap)
    for d in (cfg, cap):
        server_ctl.write_probe_marker(d, PROBE_PORT, FAKE_PID)
    before = _snapshot(root)

    done, opened = _run_seed(tmp_path, root, cfg, cap, "--arm")

    assert done.returncode == 0, done.stderr[-800:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert (out["session"], out["armed"]) == (stored["session"], True)
    assert out["runs_before"] == [stored["run"]]
    assert out["runs_after"] != out["runs_before"]
    assert any(p.startswith(_norm(cap / "sessions") + os.sep) for p in opened), opened
    after = _snapshot(root)
    session_file = str(Path("cap") / "sessions" / f"{stored['session']}.json")
    assert after[session_file] != before[session_file]
    for d in ("cfg", "cap"):
        marker = str(Path(d) / seed_session.PROBE_MARKER)
        assert after[marker] == before[marker]


# ------------------------------------------------------------ the marker's rules

def _refusal(path: Path, flag: str = "--capture-dir") -> str:
    with pytest.raises(seed_session.SeedRefused) as caught:
        seed_session._private(str(path), flag)
    return str(caught.value)


def test_a_marker_naming_the_rigs_port_is_refused(tmp_path, monkeypatch, capsys):
    """A marker that names port 8800 is refused, through ``main`` as a caller
    runs it, before the store is imported or the environment is set: the
    rig's server is never a probe server, and server_ctl.py refuses to start
    one there, so such a marker was not written by a probe start. CONTROL:
    the same directory, the marker naming a probe port, is accepted.

    ``seed`` is replaced by a recorder, so a mutant that lets the marker
    through reaches the recorder, not this worker's store.

    Mutation "rig port accepted" (seed_session.py `_private`: the
    `port == RIG_PORT` test made `if False:`), observed red:
        AssertionError: seed_session let a marker naming port 8800 through:
        rc=0, seed called with [('h4-flow', 1, True)]
        assert 0 == 2"""
    cfg, cap = tmp_path / "cfg", tmp_path / "cap"
    cfg.mkdir()
    cap.mkdir()
    server_ctl.write_probe_marker(cfg, PROBE_PORT, FAKE_PID)
    server_ctl.write_probe_marker(cap, 8800, FAKE_PID)
    called: list[tuple] = []
    monkeypatch.setattr(seed_session, "seed",
                        lambda flow, days, arm: called.append((flow, days, arm)) or {})
    # main() sets these two when it lets a directory through; recorded here
    # so a mutant's writes are undone at teardown.
    for name in ("ASTRODECK_CONFIG_DIR", "ASTRODECK_CAPTURE_DIR"):
        monkeypatch.setenv(name, os.environ.get(name, ""))

    rc = seed_session.main(["--config-dir", str(cfg), "--capture-dir", str(cap),
                            "--flow", FLOW, "--arm"])
    err = capsys.readouterr().err

    assert rc == 2, (f"seed_session let a marker naming port 8800 through: "
                     f"rc={rc}, seed called with {called}")
    assert called == []
    assert "8800" in err and "rig" in err, err
    assert seed_session.RIG_PORT == server_ctl.RIG_PORT == 8800

    server_ctl.write_probe_marker(cap, PROBE_PORT, FAKE_PID)
    assert seed_session._private(str(cap), "--capture-dir") == cap.resolve()


@pytest.mark.parametrize("content", [
    b"",
    b"not json",
    b"[]",
    b'{"port": "8871", "pid": 424242, "dir": "%s"}',
    b'{"port": true, "pid": 424242, "dir": "%s"}',
    b'{"port": 0, "pid": 424242, "dir": "%s"}',
    b'{"port": 70000, "pid": 424242, "dir": "%s"}',
    b'{"port": 8871, "dir": "%s"}',
    b'{"port": 8871, "pid": 0, "dir": "%s"}',
    b'{"port": 8871, "pid": true, "dir": "%s"}',
    b'{"port": 8871, "pid": 424242}',
    b'{"port": 8871, "pid": 424242, "dir": ""}',
    b'{"port": 8871, "pid": 424242, "dir": "%s", "pad": "' + b"x" * 5000 + b'"}',
], ids=["empty", "not-json", "not-an-object", "port-a-string", "port-a-bool",
        "port-zero", "port-too-big", "no-pid", "pid-zero", "pid-a-bool", "no-dir",
        "dir-empty", "oversized"])
def test_a_marker_the_script_cannot_read_is_refused(tmp_path, content):
    """A marker is server_ctl.py's JSON or it is no marker: an empty file,
    a file of the right name holding anything else, or one naming no usable
    port, pid or directory, is refused for what is wrong with it. CONTROL:
    server_ctl.py's own marker in the same directory is accepted.

    Mutation "any file of that name is a marker" (seed_session.py
    `_marker`: returns ``{"port": 0, "pid": 0, "dir": str(path)}`` as soon
    as the file exists, reading nothing), observed red on all thirteen,
    each:
        Failed: DID NOT RAISE <class 'h4_ui_probe_seed_session.SeedRefused'>
    Mutation "marker fields unchecked" (seed_session.py `_marker`: the
    port, pid and directory checks deleted), observed red on the eight
    cases about a field; seven with the failure above, and no-dir with
        TypeError: argument should be a str or an os.PathLike object where
        __fspath__ returns a str, not 'NoneType'
    (empty, not-json, not-an-object and oversized are refused before the
    fields are read, and dir-empty by the directory comparison.)"""
    d = (tmp_path / "cap").resolve()
    d.mkdir()
    # JSON's escaping of a Windows path, so "%s" holds the directory itself.
    escaped = json.dumps(str(d))[1:-1].encode("utf-8")
    (d / seed_session.PROBE_MARKER).write_bytes(content.replace(b"%s", escaped))
    assert "marker" in _refusal(d)
    server_ctl.write_probe_marker(d, PROBE_PORT, FAKE_PID)
    assert seed_session._private(str(d), "--capture-dir") == d


def test_a_marker_copied_into_another_directory_is_refused(tmp_path):
    """The marker names the directory it was written into. A probe directory
    copied (or its marker copied) somewhere else, say over a real install's
    captures, carries a marker naming the old place, and is refused there:
    no probe start created the copy. CONTROL: the original is accepted.

    Mutation "copied marker accepted" (seed_session.py `_private`: the
    directory comparison made `if False:`), observed red:
        Failed: DID NOT RAISE <class 'h4_ui_probe_seed_session.SeedRefused'>"""
    made, real = (tmp_path / "probe-cap").resolve(), (tmp_path / "real-cap").resolve()
    made.mkdir()
    real.mkdir()
    server_ctl.write_probe_marker(made, PROBE_PORT, FAKE_PID)
    shutil.copy2(made / seed_session.PROBE_MARKER, real / seed_session.PROBE_MARKER)
    why = _refusal(real)
    assert str(made) in why and "another directory" in why, why
    assert seed_session._private(str(made), "--capture-dir") == made


def test_the_guard_makes_no_network_call(tmp_path, monkeypatch):
    """The guard reads a file and asks no server anything, for a directory
    it refuses and one it accepts alike: a check that asked whether the
    rig's server answers would itself be a call to the rig's port, and the
    probe tooling never makes one. Every way a connection starts is
    replaced by a recorder that refuses.

    Mutation "the guard asks the port" (seed_session.py `_private`, after
    the marker is read: ``urllib.request.urlopen(f"http://127.0.0.1:{port}
    /healthz", timeout=0.2)`` inside a ``try`` that swallows any error; run
    against this case alone, where every connection is refused), observed
    red:
        AssertionError: the guard opened a connection: [('create_connection',
        ('127.0.0.1', 8800)), ('create_connection', ('127.0.0.1', 8871))]"""
    calls: list[tuple] = []

    def refuse(kind):
        def _record(*args, **kwargs):
            calls.append((kind, args[0] if args else kwargs))
            raise OSError(f"{kind}: this test allows no network call")
        return _record

    monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
    monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))

    unmarked, rig, probe = (tmp_path / n for n in ("unmarked", "rig", "probe"))
    for d in (unmarked, rig, probe):
        d.mkdir()
    server_ctl.write_probe_marker(rig, 8800, FAKE_PID)
    server_ctl.write_probe_marker(probe, PROBE_PORT, FAKE_PID)
    _refusal(unmarked)
    _refusal(rig)
    assert seed_session._private(str(probe), "--capture-dir") == probe.resolve()
    assert calls == [], f"the guard opened a connection: {calls}"


# -------------------------------------------------------- server_ctl.py start

@pytest.fixture
def started(monkeypatch, capsys):
    """``server_ctl.main(argv)`` run in this process with the server spawn
    and the health wait replaced (nothing launched, no port touched), and
    the start's stdout. The spawn answers ``FAKE_PID``."""
    spawned: list[int] = []

    def spawn(venv_python, port, env, log_path):
        spawned.append(port)
        return FAKE_PID

    monkeypatch.setattr(server_ctl, "_spawn_server", spawn)
    monkeypatch.setattr(server_ctl, "_wait_healthz", lambda client, timeout_s: None)

    def run(*argv: str) -> tuple[int, str]:
        rc = server_ctl.main(["start", "--no-sim", "--port", str(PROBE_PORT),
                              "--venv-python", sys.executable, *argv])
        return rc, capsys.readouterr().out
    run.spawned = spawned
    return run


def test_server_ctl_start_marks_each_directory_it_creates(tmp_path, started):
    """``server_ctl.py start`` leaves the marker in the config and the
    capture directory it made: the server's port and pid and the directory
    itself, the JSON seed_session.py reads. The two scripts agree on it:
    what the start wrote, the guard accepts, under the one file name.

    Mutation "no marker written" (server_ctl.py `cmd_start`: the
    `write_probe_marker` call made `pass`), observed red:
        AssertionError: no marker in <tmp>\\cfg
        assert False
         +  where False = is_file()"""
    cfg, cap = (tmp_path / "cfg").resolve(), (tmp_path / "cap").resolve()
    rc, out = started("--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))
    assert rc == 0, out
    assert started.spawned == [PROBE_PORT]
    assert server_ctl.PROBE_MARKER == seed_session.PROBE_MARKER == ".astrodeck-probe"
    for d in (cfg, cap):
        marker = d / server_ctl.PROBE_MARKER
        assert marker.is_file(), f"no marker in {d}"
        assert json.loads(marker.read_text(encoding="utf-8")) == {
            "port": PROBE_PORT, "pid": FAKE_PID, "dir": str(d)}
        assert seed_session._private(str(d), "--config-dir") == d


def test_server_ctl_start_never_marks_a_directory_it_did_not_create(tmp_path, started):
    """A config directory that was there before the start, unmarked (a real
    install's, say, passed without ``--fresh``), is used but never marked,
    so seed_session.py still refuses it, and the start says so; its files
    are left as they were. The capture directory the same start created is
    marked. CONTROL: a directory an earlier start marked is marked again
    with the new start's pid, so a probe server restarted without
    ``--fresh`` keeps its directories.

    Mutation "every directory marked" (server_ctl.py `cmd_start`:
    ``to_mark`` made both directories, whatever was there before), observed
    red:
        AssertionError: assert not True
         +  where True = exists()
         +    where exists = (WindowsPath('<tmp>/cfg') / '.astrodeck-probe').exists
    Mutation "a marked directory not marked again" (server_ctl.py
    `_dirs_to_mark`: only a directory that is not there yet), observed red
    at the CONTROL, the restart leaving the earlier start's pid:
        assert 1 == 424242"""
    cfg, cap = (tmp_path / "cfg").resolve(), (tmp_path / "cap").resolve()
    cfg.mkdir()
    (cfg / "astrodeck.json").write_text('{"note": "a real config"}', encoding="utf-8")
    before = _snapshot(cfg)

    rc, out = started("--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))

    assert rc == 0, out
    assert not (cfg / server_ctl.PROBE_MARKER).exists()
    assert "WARNING" in out and str(cfg) in out and server_ctl.PROBE_MARKER in out, out
    assert "marker" in _refusal(cfg, "--config-dir")
    after = _snapshot(cfg)
    assert {k: after[k] for k in before} == before
    assert seed_session._private(str(cap), "--capture-dir") == cap

    # CONTROL: a restart over the directory it marked re-marks it.
    started.spawned.clear()
    server_ctl.write_probe_marker(cap, PROBE_PORT, 1)
    rc, out = started("--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))
    assert rc == 0, out
    assert json.loads((cap / server_ctl.PROBE_MARKER).read_text(encoding="utf-8"))["pid"] == FAKE_PID


@pytest.mark.parametrize("kind", ["copied", "not-a-marker"])
def test_server_ctl_start_never_marks_again_a_marker_it_did_not_write_there(
        tmp_path, started, kind):
    """A capture directory that holds a file of the marker's name which no
    start wrote for it, a marker copied in from another directory (a probe
    directory copied over a real install's captures, say, which
    seed_session.py refuses) or a file that is no marker at all, is not
    marked by a start without ``--fresh``: marking it would write this
    directory's name into the marker, and so let the copy through
    seed_session.py's directory comparison. The file is left as it was, the
    start says so, and seed_session.py still refuses the directory. CONTROL:
    once the marker there is one a start wrote for this directory, the next
    start marks it again with its own pid.

    Mutation "any file of that name marked again" (server_ctl.py
    `_dirs_to_mark`: ``(d / PROBE_MARKER).is_file()`` in place of
    ``_marked_here(d)``, which is how this change was first written, and
    that code failed the same way), observed red on both; copied:
        assert b'{"port": 88...\\\\\\\\cap"}\\r\\n' == b'{"port": 88...obe-cap"}\\r\\n'
          At index 22 diff: b'4' != b'1'
    not-a-marker:
        assert b'{"port": 88...\\\\\\\\cap"}\\r\\n' == b'not json'
          At index 0 diff: b'{' != b'n'
    Mutation "the marker's directory not compared" (server_ctl.py
    `_marked_here`: ``return True`` in place of the comparison), observed
    red on copied alone, with the failure above; not-a-marker is turned
    away before its directory is read. Mutation "a marked directory not
    marked again" (the case above) is red at this CONTROL too, on both:
        assert 1 == 424242"""
    cfg, cap = (tmp_path / "cfg").resolve(), (tmp_path / "cap").resolve()
    cap.mkdir()
    if kind == "copied":
        made = (tmp_path / "probe-cap").resolve()
        made.mkdir()
        server_ctl.write_probe_marker(made, PROBE_PORT, 1)
        shutil.copy2(made / server_ctl.PROBE_MARKER, cap / server_ctl.PROBE_MARKER)
    else:
        (cap / server_ctl.PROBE_MARKER).write_bytes(b"not json")
    was = (cap / server_ctl.PROBE_MARKER).read_bytes()

    rc, out = started("--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))

    assert rc == 0, out
    assert (cap / server_ctl.PROBE_MARKER).read_bytes() == was
    assert "WARNING" in out and str(cap) in out, out
    assert "marker" in _refusal(cap)
    assert seed_session._private(str(cfg), "--config-dir") == cfg

    # CONTROL: the marker a start wrote for this directory is marked again.
    server_ctl.write_probe_marker(cap, PROBE_PORT, 1)
    rc, out = started("--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))
    assert rc == 0, out
    assert json.loads((cap / server_ctl.PROBE_MARKER).read_text(encoding="utf-8"))["pid"] == FAKE_PID
    assert seed_session._private(str(cap), "--capture-dir") == cap
