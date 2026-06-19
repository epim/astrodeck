"""Update service: preconditions/safety gate, check, and the apply pipeline."""
import json
from pathlib import Path

import pytest

from astrodeck.config import UpdateConfig
from astrodeck.update import download, github, service as SVC, stage, verify
from astrodeck.update.service import UpdateError, UpdateService
from astrodeck.update.state import update_state


class FakeHub:
    def __init__(self, blocker=None):
        self._blocker = blocker

    @property
    def restart_blocker(self):
        return self._blocker


def _cfg(**kw):
    base = dict(enabled=True, auto_check=False, channel="stable",
                repo="epim/astrodeck", signing_pubkey="PUB", health_timeout_s=60)
    base.update(kw)
    return lambda: UpdateConfig(**base)


@pytest.fixture(autouse=True)
def _clean_state():
    update_state.current = "0.1.0"
    update_state.set_available(None, "")
    update_state.set_phase("idle")
    update_state.set_result(None)
    SVC.reset_service()
    SVC.reset_exit_state()
    yield


def _info():
    return github.ReleaseInfo(
        version="0.2.0", tag="v0.2.0", notes_md="hello",
        artifact_url="http://x/a.tar.gz", sha256_url="http://x/a.sha256",
        sig_url="http://x/a.sig", prerelease=False)


# --------------------------------------------------------------- preconditions

def test_blocked_when_not_supervised():
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=None)
    update_state.set_available("0.2.0", "n")
    ok, reason = svc.apply_preconditions()
    assert not ok and "supervisor" in reason
    assert svc.supervised is False


def test_blocked_without_pubkey(tmp_path):
    svc = UpdateService(cfg_getter=_cfg(signing_pubkey=""), hub=FakeHub(),
                        install_root=tmp_path)
    update_state.set_available("0.2.0", "n")
    ok, reason = svc.apply_preconditions()
    assert not ok and "public key" in reason


def test_blocked_when_no_update(tmp_path):
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=tmp_path)
    ok, reason = svc.apply_preconditions()
    assert not ok and "no update" in reason


def test_safety_gate_blocks_when_rig_busy(tmp_path):
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(blocker="rig is slewing"),
                        install_root=tmp_path)
    update_state.set_available("0.2.0", "n")
    ok, reason = svc.apply_preconditions()
    assert not ok and "slewing" in reason


def test_preconditions_pass_when_ready(tmp_path):
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=tmp_path)
    update_state.set_available("0.2.0", "n")
    ok, reason = svc.apply_preconditions()
    assert ok, reason


# ----------------------------------------------------------------------- check

async def test_check_marks_available(monkeypatch, tmp_path):
    async def fake_latest(repo, *, channel, current, **kw):
        return _info()
    monkeypatch.setattr(github, "latest_release", fake_latest)
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=tmp_path,
                        now=lambda: 123.0)
    snap = await svc.check()
    assert snap["update_available"] is True
    assert snap["latest"] == "0.2.0"
    assert snap["notes_md"] == "hello"
    assert snap["last_check_ts"] == 123.0


async def test_check_survives_network_error(monkeypatch, tmp_path):
    async def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(github, "latest_release", boom)
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=tmp_path)
    snap = await svc.check()
    assert snap["update_available"] is False


# ----------------------------------------------------------------------- apply

def _patch_pipeline(monkeypatch, *, verify_ok=True):
    async def fake_latest(*a, **k):
        return _info()

    async def fake_download(url, dest, **k):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"artifact-bytes")
        if k.get("on_progress"):
            k["on_progress"](1.0)
        return dest

    async def fake_text(url, **k):
        return "deadbeef  a.tar.gz\n"

    monkeypatch.setattr(github, "latest_release", fake_latest)
    monkeypatch.setattr(download, "download", fake_download)
    monkeypatch.setattr(download, "fetch_text", fake_text)
    monkeypatch.setattr(verify, "verify_download",
                        lambda *a, **k: (verify_ok, "ok" if verify_ok else "bad sig"))
    monkeypatch.setattr(stage, "stage_release", lambda tb, rel, v: rel / v)
    monkeypatch.setattr(stage, "reconcile_deps", lambda d, p: (True, "deps ok"))


async def test_apply_full_pipeline_writes_pending_and_signals(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch)
    signalled = {"v": False}
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=tmp_path,
                        server_signal=lambda: signalled.__setitem__("v", True),
                        now=lambda: 1.0)
    update_state.set_available("0.2.0", "hello")
    res = await svc.apply()
    assert res["applying"] == "0.2.0"
    assert signalled["v"] is True
    pend = json.loads((tmp_path / "state" / "pending-update.json").read_text())
    assert pend["version"] == "0.2.0"


async def test_apply_rejects_bad_signature(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch, verify_ok=False)
    signalled = {"v": False}
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=tmp_path,
                        server_signal=lambda: signalled.__setitem__("v", True))
    update_state.set_available("0.2.0", "hello")
    with pytest.raises(UpdateError) as ei:
        await svc.apply()
    assert "verification failed" in str(ei.value)
    assert signalled["v"] is False
    assert not (tmp_path / "state" / "pending-update.json").exists()


async def test_apply_refuses_when_safety_blocks(tmp_path):
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(blocker="a sequence is running"),
                        install_root=tmp_path)
    update_state.set_available("0.2.0", "hello")
    with pytest.raises(UpdateError) as ei:
        await svc.apply()
    assert "sequence is running" in str(ei.value)


# --------------------------------------------------------------- boot result

def test_load_boot_result_surfaces_and_clears(tmp_path):
    from astrodeck.update.protocol import InstallLayout
    layout = InstallLayout(tmp_path)
    layout.state.mkdir(parents=True)
    layout.write_pending("0.2.0")  # unrelated
    layout.result.write_text(json.dumps({"ok": True, "version": "0.2.0"}))
    svc = UpdateService(cfg_getter=_cfg(), hub=FakeHub(), install_root=tmp_path)
    svc.load_boot_result()
    assert update_state.snapshot()["last_result"] == {"ok": True, "version": "0.2.0"}
    assert not layout.result.exists()  # cleared after surfacing


# ---------------------------------------------------- graceful-exit handoff

def test_exit_code_handoff():
    SVC.reset_exit_state()
    assert SVC.consume_exit_code() == 0

    class FakeServer:
        should_exit = False

    s = FakeServer()
    SVC.bind_server(s)
    SVC.request_apply_exit()
    assert s.should_exit is True
    assert SVC.consume_exit_code() == 92
