"""Two bounds the runner applies, and the rule that neither may be silent.

Both are small, and both are the shape that has bitten this project before: a
limit that is correct, invisible, and therefore indistinguishable from "we sent
everything".
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from astrodeck.config import SyncPushConfig, config_store
from astrodeck.sync import push as push_mod
from astrodeck.sync.runner import DEFAULT_PASS_LIMIT, PushRunner, build_destination


def _frame(root: Path, rel: str, *, age_s: float = 60.0) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FITS" * 50)
    old = time.time() - age_s
    os.utime(p, (old, old))
    return p


@pytest.fixture
def captures(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    root.mkdir()
    from astrodeck import hub as hub_module
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", root)
    return root


@pytest.fixture
def cfg_push(monkeypatch):
    def _set(**kw):
        cfg = config_store.cfg()
        monkeypatch.setattr(cfg, "sync_push", SyncPushConfig(**kw), raising=False)
        return cfg
    return _set


class TestPerPassBound:
    @pytest.mark.asyncio
    async def test_config_zero_means_the_runner_cap_not_unbounded(
            self, captures, tmp_path, cfg_push, monkeypatch):
        """A genuinely unbounded first pass would hold the disk for a 9 GB
        backlog with a guider running. 0 means "use the built-in cap"; the
        config docstring says the same thing, and this is what keeps them
        agreeing."""
        cfg_push(enabled=True, path=str(tmp_path / "nas"), limit_per_pass=0)
        seen: list[int] = []
        monkeypatch.setattr(push_mod, "push_once",
                            lambda *a, **kw: (seen.append(kw["limit"]),
                                              push_mod.PushResult())[1])
        r = PushRunner()
        cfg = config_store.cfg()
        await r._pass(build_destination(cfg), cfg)
        assert seen == [DEFAULT_PASS_LIMIT]

    @pytest.mark.asyncio
    async def test_a_configured_limit_is_honoured(
            self, captures, tmp_path, cfg_push, monkeypatch):
        cfg_push(enabled=True, path=str(tmp_path / "nas"), limit_per_pass=7)
        seen: list[int] = []
        monkeypatch.setattr(push_mod, "push_once",
                            lambda *a, **kw: (seen.append(kw["limit"]),
                                              push_mod.PushResult())[1])
        r = PushRunner()
        cfg = config_store.cfg()
        await r._pass(build_destination(cfg), cfg)
        assert seen == [7]

    @pytest.mark.asyncio
    async def test_what_a_bounded_pass_leaves_is_sent_by_the_next_one(
            self, captures, tmp_path, cfg_push):
        """No bookkeeping resumes it — the next diff simply still shows it
        missing, which is the whole reason a bound is safe here."""
        nas = tmp_path / "nas"
        for i in range(5):
            _frame(captures, f"NGC 6946/f{i}.fits")
        cfg_push(enabled=True, path=str(nas), limit_per_pass=2)
        r = PushRunner()
        cfg = config_store.cfg()
        dest = build_destination(cfg)
        await r._pass(dest, cfg)
        assert len(list(Path(nas).rglob("*.fits"))) == 2
        await r._pass(dest, cfg)
        await r._pass(dest, cfg)
        assert len(list(Path(nas).rglob("*.fits"))) == 5


class TestTruncatedScan:
    @pytest.mark.asyncio
    async def test_a_capped_library_scan_says_so(
            self, captures, tmp_path, cfg_push, monkeypatch):
        """The gallery scan stops at SCAN_MAX_FILES. A pass that quietly offers
        a subset reads exactly like a pass that offered everything."""
        from astrodeck import gallery as gallery_mod
        from astrodeck.events import bus
        lines: list[tuple[str, str]] = []
        monkeypatch.setattr(bus, "log",
                            lambda level, msg, src="": lines.append((level, msg)))
        monkeypatch.setattr(gallery_mod, "scan", lambda *a, **kw: ([], True))
        cfg_push(enabled=True, path=str(tmp_path / "nas"))
        r = PushRunner()
        cfg = config_store.cfg()
        await r._pass(build_destination(cfg), cfg)
        assert any(lvl == "warning" and "NOT being" in msg for lvl, msg in lines), \
            f"a truncated scan was silent; log was {lines}"

    @pytest.mark.asyncio
    async def test_an_untruncated_scan_does_not_warn(
            self, captures, tmp_path, cfg_push, monkeypatch):
        from astrodeck import gallery as gallery_mod
        from astrodeck.events import bus
        lines: list[tuple[str, str]] = []
        monkeypatch.setattr(bus, "log",
                            lambda level, msg, src="": lines.append((level, msg)))
        monkeypatch.setattr(gallery_mod, "scan", lambda *a, **kw: ([], False))
        cfg_push(enabled=True, path=str(tmp_path / "nas"))
        r = PushRunner()
        cfg = config_store.cfg()
        await r._pass(build_destination(cfg), cfg)
        assert not any("library scan hit its cap" in msg for _lvl, msg in lines)
