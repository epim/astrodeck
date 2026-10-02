# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``server_ctl.py start --fresh`` refuses to wipe a directory a probe start
did not mark (#541).

``--fresh`` used to call ``shutil.rmtree(path, ignore_errors=True)`` on
whatever ``--config-dir`` and ``--capture-dir`` name, with no check that
either belongs to a probe server: the only refusal before the wipe was the
rig's own port (8800). A mistyped or pasted path -- a real install's
``ASTRODECK_CONFIG_DIR`` or its captures -- was deleted recursively and
without a prompt, and ``ignore_errors=True`` hid a partial delete on top of
that.

D-02 (backlog ruling D-02, owner-approved 2026-09-30) rules that ``--fresh``
requires the probe marker (``.astrodeck-probe``, #539) everywhere, with a
one-time exception: an unmarked directory under the checkout's own
``<repo>/.probe/`` is accepted once (that default predates the marker) and
marked afterwards like any other directory ``--fresh`` just cleared. Both
directories are checked before either is deleted, so a refusal on one must
not leave the other already wiped.

This file lives beside the WP-12 issues' other new cases per the plan
(docs/superpowers/plans/2026-09-30-open-issue-backlog.md, WP-12 fix shape):
"New cases go in the two listed test files or a new test_w1_ file." It does
not live in server/tests/test_h4_seed_session_allow_list.py because that
file's whole shape is #539's seed_session.py guard; this is #541's --fresh
guard in server_ctl.py, a different script and a different script's own new
function. It needs only the server's venv: server_ctl.py is a standalone
script with no astrodeck import at all, loaded from its file exactly as
test_h4_seed_session_allow_list.py loads it, under a private module name so
no other test can import it by accident from tools/ui_probe being added to
sys.path.

Each case names the mutation that turns it red and the failure observed
when that mutation was run in a private byte-copy of this file and
server_ctl.py laid out as in the checkout (mutate-and-restore, verified
byte-identical afterwards by sha256; see this WP's return for the exact
run).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = REPO_ROOT / "tools" / "ui_probe"


def _load(name: str):
    """``tools/ui_probe/<name>.py`` as a module of its own, under a name no
    other test uses, and never registered on ``sys.path`` (the same
    arrangement as test_h4_seed_session_allow_list.py's ``_load``)."""
    spec = importlib.util.spec_from_file_location(f"w1_ui_probe_{name}",
                                                  PROBE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server_ctl = _load("server_ctl")

#: Any port but the rig's, as a probe server's always is.
PROBE_PORT = 8871
FAKE_PID = 424242


@pytest.fixture
def started(monkeypatch, capsys):
    """``server_ctl.main(argv)`` run in this process with the server spawn
    and the health wait replaced (nothing launched, no port touched), and
    the start's captured stdout+the log lines it prints with ``_log``
    (which go to stdout too). Spawn records the port it was asked for, so a
    test can assert it was never called when a start should have refused
    before reaching it."""
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


def _fake_venv(tmp_path: Path) -> Path:
    """A file that exists where ``--venv-python`` is pointed, so
    ``cmd_start``'s ``is_file()`` check (which runs before the ``--fresh``
    guard this file tests) passes without launching anything real."""
    py = tmp_path / "fake-python.exe"
    py.write_bytes(b"")
    return py


# ------------------------------------------------------- the refusal itself

def test_fresh_refuses_an_unmarked_directory_outside_the_probe_default(
        tmp_path, monkeypatch, capsys):
    """A config directory that exists, outside any checkout's ``.probe/``,
    with no marker, and a file in it: ``--fresh`` refuses the whole start
    before wiping anything, names the directory and #541, and the file
    survives byte for byte. The capture directory (also unmarked, also
    outside ``.probe/``) is refused too, and named as well, in the same
    run -- so BOTH are checked, not just the first.

    Mutation "fresh wipes unconditionally" (server_ctl.py `cmd_start`: the
    `if args.fresh:` block's guard call replaced by nothing, restoring the
    original unconditional `_rmtree_if_exists` pair), observed red -- the
    refusal never fires, so `cmd_start` reaches the spawn this test pins to
    fail loudly instead of silently launching something:
        AssertionError: server_ctl spawned a server after a --fresh refusal
    (Mutation ".probe default matches everywhere", below, produces the
    identical failure here: either way the guard lets the wipe through.)"""
    monkeypatch.setattr(server_ctl, "REPO_ROOT", (tmp_path / "unrelated-repo").resolve())
    cfg, cap = tmp_path / "cfg", tmp_path / "cap"
    cfg.mkdir()
    cap.mkdir()
    (cfg / "keep.txt").write_text("a real config's file", encoding="utf-8")
    (cap / "keep.txt").write_text("a real capture's file", encoding="utf-8")

    def refuse_spawn(*a, **k):
        raise AssertionError("server_ctl spawned a server after a --fresh refusal")
    monkeypatch.setattr(server_ctl, "_spawn_server", refuse_spawn)

    rc = server_ctl.main(["start", "--fresh", "--no-sim", "--port", str(PROBE_PORT),
                          "--config-dir", str(cfg), "--capture-dir", str(cap),
                          "--venv-python", str(_fake_venv(tmp_path))])
    out = capsys.readouterr().out

    assert rc == 2, out
    assert "ERROR" in out and "#541" in out
    assert str(cfg) in out and str(cap) in out, out
    assert (cfg / "keep.txt").read_text(encoding="utf-8") == "a real config's file"
    assert (cap / "keep.txt").read_text(encoding="utf-8") == "a real capture's file"
    assert not (cfg / server_ctl.PROBE_MARKER).exists()
    assert not (cap / server_ctl.PROBE_MARKER).exists()


def test_fresh_refuses_the_whole_start_when_only_one_directory_is_unmarked(
        tmp_path, monkeypatch, started):
    """The config directory is marked (a probe directory by its own claim);
    the capture directory is unmarked and outside ``.probe/``. The start
    still refuses -- and, critically, never wipes the marked config
    directory either, since both are checked before anything is deleted.

    Mutation "checked one at a time" (server_ctl.py `cmd_start`: the
    `--fresh` block wipes `config_dir` right after clearing it, then checks
    and wipes `capture_dir` -- two independent if/wipe pairs instead of
    checking both before either is deleted), observed red:
        AssertionError: the marked directory was wiped too
        assert False
         +  where False = exists()
         +    where exists = (WindowsPath('<tmp>/cfg') / '.astrodeck-probe').exists
         +      where '.astrodeck-probe' = server_ctl.PROBE_MARKER"""
    monkeypatch.setattr(server_ctl, "REPO_ROOT", (tmp_path / "unrelated-repo").resolve())
    cfg, cap = tmp_path / "cfg", tmp_path / "cap"
    cfg.mkdir()
    cap.mkdir()
    server_ctl.write_probe_marker(cfg, PROBE_PORT, FAKE_PID)
    (cap / "keep.txt").write_text("a real capture's file", encoding="utf-8")

    rc, out = started("--fresh", "--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))

    assert rc == 2, out
    assert started.spawned == []
    assert (cfg / server_ctl.PROBE_MARKER).exists(), "the marked directory was wiped too"
    assert (cap / "keep.txt").read_text(encoding="utf-8") == "a real capture's file"


# --------------------------------------------------------------- the control

def test_fresh_wipes_a_marked_directory(tmp_path, monkeypatch, started):
    """CONTROL for the refusal above: a directory a probe start marked, well
    outside ``.probe/``, is wiped and remade with a fresh marker, exactly as
    before this change. `--fresh` on a legitimate probe directory must keep
    working."""
    monkeypatch.setattr(server_ctl, "REPO_ROOT", (tmp_path / "unrelated-repo").resolve())
    cfg, cap = (tmp_path / "cfg").resolve(), (tmp_path / "cap").resolve()
    cfg.mkdir()
    cap.mkdir()
    server_ctl.write_probe_marker(cfg, PROBE_PORT, 1)
    server_ctl.write_probe_marker(cap, PROBE_PORT, 1)
    (cfg / "stale.txt").write_text("from an earlier probe run", encoding="utf-8")

    rc, out = started("--fresh", "--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))

    assert rc == 0, out
    assert started.spawned == [PROBE_PORT]
    assert not (cfg / "stale.txt").exists()
    for d in (cfg, cap):
        assert json.loads((d / server_ctl.PROBE_MARKER).read_text(encoding="utf-8")) == {
            "port": PROBE_PORT, "pid": FAKE_PID, "dir": str(d)}


# --------------------------------------------------- the one-time exception

def test_fresh_accepts_an_unmarked_directory_under_the_repos_own_probe_once(
        tmp_path, monkeypatch, started):
    """An unmarked config and capture directory under a (fake) repo's own
    ``.probe/``, holding a file from before the marker existed: `--fresh`
    wipes them without refusing, and the new directories come out marked --
    the ordinary post-wipe marking every `--fresh` run already does, not
    special-cased code, since `_dirs_to_mark` sees them as gone once the
    wipe has run.

    Mutation ".probe default matches everywhere" (server_ctl.py
    `_under_probe_default`: body replaced by `return True`), observed red
    on test_fresh_refuses_an_unmarked_directory_outside_the_probe_default
    and test_fresh_refuses_the_whole_start_when_only_one_directory_is_unmarked
    (neither directory in those tests is under any `.probe/`, so both would
    wrongly be accepted), and on
    test_probe_default_matches_the_directory_itself_and_only_its_own_tree
    directly:
        AssertionError: assert True is False
    This test itself stays green under that mutant (a true positive would
    still be accepted); it exists to pin the ACCEPTING side of the
    exception, which the refusal tests do not exercise."""
    fake_repo = (tmp_path / "fake-repo").resolve()
    monkeypatch.setattr(server_ctl, "REPO_ROOT", fake_repo)
    cfg, cap = fake_repo / ".probe" / "cfg", fake_repo / ".probe" / "captures"
    cfg.mkdir(parents=True)
    cap.mkdir(parents=True)
    (cfg / "predates-the-marker.txt").write_text("legacy", encoding="utf-8")

    rc, out = started("--fresh", "--config-dir", str(cfg), "--capture-dir", str(cap),
                      "--ui-dir", str(tmp_path))

    assert rc == 0, out
    assert started.spawned == [PROBE_PORT]
    assert not (cfg / "predates-the-marker.txt").exists()
    for d in (cfg, cap):
        assert json.loads((d / server_ctl.PROBE_MARKER).read_text(encoding="utf-8")) == {
            "port": PROBE_PORT, "pid": FAKE_PID, "dir": str(d)}


def test_probe_default_matches_the_directory_itself_and_only_its_own_tree(tmp_path, monkeypatch):
    """`_under_probe_default` is true for the checkout's own `.probe/`
    itself and anything under it, and false for a directory that merely
    starts with the same characters (`.probe-evil` is not `.probe`) or is
    unrelated entirely.

    Mutation "prefix match" (server_ctl.py `_under_probe_default`: the
    parents comparison replaced by `str(resolved).startswith(str(probe_root))`),
    observed red:
        AssertionError: assert True is False
         +  where True = <function _under_probe_default at ...>(WindowsPath(
             '<tmp>/fake-repo/.probe-evil'))
         +    where <function _under_probe_default at ...> = server_ctl._under_probe_default"""
    fake_repo = (tmp_path / "fake-repo").resolve()
    monkeypatch.setattr(server_ctl, "REPO_ROOT", fake_repo)
    probe_root = fake_repo / ".probe"
    nested = probe_root / "cfg" / "deep"
    evil = fake_repo / ".probe-evil"
    unrelated = tmp_path / "elsewhere"

    assert server_ctl._under_probe_default(probe_root) is True
    assert server_ctl._under_probe_default(nested) is True
    assert server_ctl._under_probe_default(evil) is False
    assert server_ctl._under_probe_default(unrelated) is False
