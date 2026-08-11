"""The thing that CALLS push_once — the seam Phase 2 deliberately left empty.

Phase 2's core was built, tested and then reached by nothing. So the properties
under test here are not "does a copy work" (test_sync_push.py owns that); they
are the ones that decide whether the feature is real on a rig:

* it does nothing at all until a destination is configured, and starts the
  moment one is, with no restart;
* it offers THE GALLERY'S frames, not a directory walk of the capture root —
  otherwise the first night pushes the trash bin and the thumbnail cache;
* a pass in flight and a "Push now" cannot run at once;
* a frame that lands DURING a pass still gets a pass of its own;
* a run of failures says so once, loudly, and says so again when it recovers.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from astrodeck.config import SyncPushConfig, config_store
from astrodeck.sync import manifest as m
from astrodeck.sync import push as push_mod
from astrodeck.sync.destination import LocalDirDestination
from astrodeck.sync.runner import PushRunner, build_destination


def _frame(root: Path, rel: str, payload: bytes = b"FITS" * 200,
           *, age_s: float = 60.0) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    old = time.time() - age_s
    os.utime(p, (old, old))              # settled: older than DEFAULT_SETTLE_S
    return p


@pytest.fixture
def captures(tmp_path, monkeypatch):
    """A capture root the gallery and the runner both resolve to.

    Patching ``hub.CAPTURE_DIR`` and not ``gallery.capture_root`` on purpose:
    that is the seam every consumer already reads through, and a maintenance
    script that resolved its own root is how a backfill once reported success
    over an empty directory.
    """
    root = tmp_path / "captures"
    root.mkdir()
    from astrodeck import hub as hub_module
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", root)
    return root


@pytest.fixture
def nas(tmp_path):
    return tmp_path / "nas"


@pytest.fixture
def cfg_push(monkeypatch):
    """Point the config store's sync_push block wherever a test wants."""
    def _set(**kw):
        cfg = config_store.cfg()
        monkeypatch.setattr(cfg, "sync_push", SyncPushConfig(**kw), raising=False)
        return cfg
    return _set


class TestBuildDestination:
    def test_none_when_disabled(self, tmp_path):
        cfg = type("C", (), {"sync_push": SyncPushConfig(
            enabled=False, path=str(tmp_path))})()
        assert build_destination(cfg) is None

    def test_none_when_path_is_blank(self):
        cfg = type("C", (), {"sync_push": SyncPushConfig(
            enabled=True, path="   ")})()
        assert build_destination(cfg) is None

    def test_none_for_an_unknown_kind(self, tmp_path):
        sp = SyncPushConfig(enabled=True, path=str(tmp_path))
        object.__setattr__(sp, "kind", "sftp")       # a future kind, not built yet
        cfg = type("C", (), {"sync_push": sp})()
        assert build_destination(cfg) is None, \
            "an unrecognised kind must yield NO destination, never a guess"

    def test_label_falls_back_to_the_path(self, tmp_path):
        cfg = type("C", (), {"sync_push": SyncPushConfig(
            enabled=True, path=str(tmp_path))})()
        d = build_destination(cfg)
        assert isinstance(d, LocalDirDestination)
        assert d.label == str(tmp_path)

    def test_missing_block_entirely(self):
        """An old config object that predates the field is not a crash."""
        assert build_destination(type("C", (), {})()) is None


class TestTickDoesNothingUntilConfigured:
    @pytest.mark.asyncio
    async def test_disabled_runs_no_pass(self, captures, cfg_push):
        _frame(captures, "NGC 6946/a.fits")
        cfg_push(enabled=False)
        r = PushRunner()
        r.note_saved()
        await r._tick()
        assert r.state.passes == 0

    @pytest.mark.asyncio
    async def test_a_pending_poke_is_dropped_while_disabled(self, captures, cfg_push):
        """...because the sweep is unconditional: when the feature is turned on,
        the next pass finds everything anyway. Holding the poke would only fire
        one redundant pass at enable time."""
        cfg_push(enabled=False)
        r = PushRunner()
        r.note_saved()
        await r._tick()
        assert r._pending_event is False

    @pytest.mark.asyncio
    async def test_enabling_needs_no_restart(self, captures, nas, cfg_push):
        _frame(captures, "NGC 6946/a.fits")
        r = PushRunner()
        cfg_push(enabled=False)
        await r._tick()
        assert r.state.passes == 0
        cfg_push(enabled=True, path=str(nas))       # operator saves the panel
        r.note_saved()
        r._last_event_at = time.time() - push_mod.DEBOUNCE_S - 1
        await r._tick()
        assert r.state.passes == 1
        assert (nas / "NGC 6946/a.fits").exists()


class TestWhatGetsOffered:
    @pytest.mark.asyncio
    async def test_the_trash_and_thumbnail_caches_are_never_pushed(
            self, captures, nas, cfg_push):
        """The bug a directory walk of the capture root would have shipped.

        ``push_once``'s default source is ``walk_facts``, which is the PULL
        AGENT's tool and sweeps every file under the root. On a rig that root
        also holds the trash bin, the thumbnail cache, session records and
        logs. The runner has to offer the gallery's rows instead — which is
        also what keeps the two sync directions agreeing about what a night is.
        """
        from astrodeck import gallery as gallery_mod
        _frame(captures, "NGC 6946/light.fits")
        _frame(captures, f"{gallery_mod.TRASH_DIRNAME}/deleted.fits")
        _frame(captures, f"{gallery_mod.THUMBS_DIRNAME}/light_256.jpg")
        _frame(captures, "logs/2026-08-11.jsonl")

        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        r.note_saved()
        r._last_event_at = time.time() - push_mod.DEBOUNCE_S - 1
        await r._tick()

        assert (nas / "NGC 6946/light.fits").exists()
        sent = {p.name for p in Path(nas).rglob("*") if p.is_file()}
        assert "deleted.fits" not in sent, "the trash bin was pushed to the NAS"
        assert "light_256.jpg" not in sent, "the thumbnail cache was pushed"
        assert "2026-08-11.jsonl" not in sent, "the log directory was pushed"
        assert sent == {"light.fits"}

    @pytest.mark.asyncio
    async def test_a_second_pass_reuses_the_shared_hash_cache(
            self, captures, nas, cfg_push):
        """One cache for the pull route and the push runner. Without it, a
        15-minute sweep re-reads the whole library every time."""
        m.SHARED_HASH_CACHE.clear()
        _frame(captures, "NGC 6946/a.fits")
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        await r._pass(build_destination(config_store.cfg()), config_store.cfg())
        assert m.SHARED_HASH_CACHE, "the pass hashed without populating the shared cache"


class TestCadenceAndConcurrency:
    @pytest.mark.asyncio
    async def test_a_frame_landing_mid_pass_still_gets_a_pass(
            self, captures, nas, cfg_push, monkeypatch):
        """The poke is cleared BEFORE the work, so a frame that arrives while a
        pass is running leaves the flag set for the next tick. Clearing it
        afterwards would swallow exactly that frame until the 15-minute sweep."""
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()

        def _slow(dest, limit):
            r.note_saved()                        # a frame lands mid-pass
            return push_mod.PushResult()
        monkeypatch.setattr(r, "_pass_blocking", _slow)

        await r._pass(build_destination(config_store.cfg()), config_store.cfg())
        assert r._pending_event is True

    @pytest.mark.asyncio
    async def test_push_now_and_a_sweep_cannot_overlap(
            self, captures, nas, cfg_push, monkeypatch):
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        concurrent = []
        depth = 0

        def _watched(dest, limit):
            nonlocal depth
            depth += 1
            concurrent.append(depth)
            time.sleep(0.05)
            depth -= 1
            return push_mod.PushResult()
        monkeypatch.setattr(r, "_pass_blocking", _watched)

        cfg = config_store.cfg()
        dest = build_destination(cfg)
        await asyncio.gather(r._pass(dest, cfg), r._pass(dest, cfg),
                             r._pass(dest, cfg))
        assert max(concurrent) == 1, "two passes ran at once"

    @pytest.mark.asyncio
    async def test_a_tick_before_the_debounce_does_not_push(
            self, captures, nas, cfg_push):
        _frame(captures, "NGC 6946/a.fits")
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        # A sweep is NOT due — otherwise this would test the sweep, not the
        # debounce. A FRESH runner always sweeps immediately (last_attempt_at
        # is 0), which is the startup sweep and is correct.
        r.state.last_attempt_at = time.time()
        r.note_saved()                            # just now — inside the debounce
        await r._tick()
        assert r.state.passes == 0

    @pytest.mark.asyncio
    async def test_the_sweep_runs_with_no_event_at_all(
            self, captures, nas, cfg_push):
        """A dropped poke, a restart or an offline hour must heal by itself."""
        _frame(captures, "NGC 6946/a.fits")
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        assert r._pending_event is False
        await r._tick()                           # last_attempt_at == 0 -> due
        assert r.state.passes == 1


class TestPushNow:
    @pytest.mark.asyncio
    async def test_refuses_with_no_destination(self, captures, cfg_push):
        cfg_push(enabled=False)
        r = PushRunner()
        out = await r.push_now()
        assert out["ok"] is False
        assert r.state.passes == 0, "'push now' must not be a way around the enable"

    @pytest.mark.asyncio
    async def test_runs_immediately_ignoring_the_cadence(
            self, captures, nas, cfg_push):
        _frame(captures, "NGC 6946/a.fits")
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        r.state.last_attempt_at = time.time()      # a sweep is NOT due
        out = await r.push_now()
        assert out["passes"] == 1
        assert out["last"]["sent"] == 1
        assert (nas / "NGC 6946/a.fits").exists()

    @pytest.mark.asyncio
    async def test_reports_an_unwritable_destination_rather_than_raising(
            self, captures, cfg_push, tmp_path):
        """The operator typed a UNC path that does not resolve. That is the
        exact moment this button exists for, and it has to come back as a
        result, not a 500."""
        _frame(captures, "NGC 6946/a.fits")
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("I am a file")          # cannot be a destination root
        cfg_push(enabled=True, path=str(blocker))
        r = PushRunner()
        out = await r.push_now()
        assert out["last"]["error"] or out["last"]["failed"], \
            "an unwritable destination reported success"


class TestSayingSo:
    @pytest.mark.asyncio
    async def test_a_run_of_failures_is_shouted_once_then_recovers(
            self, captures, nas, cfg_push, monkeypatch):
        from astrodeck.events import bus
        lines: list[tuple[str, str]] = []
        monkeypatch.setattr(bus, "log",
                            lambda level, msg, src="": lines.append((level, msg)))
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        cfg = config_store.cfg()
        dest = build_destination(cfg)

        monkeypatch.setattr(r, "_pass_blocking",
                            lambda d, l: push_mod.PushResult(error="nas asleep"))
        for _ in range(push_mod.FAIL_LOUD_AFTER):
            await r._pass(dest, cfg)
        loud = [m for lvl, m in lines if lvl == "error"]
        assert len(loud) == 1, f"expected exactly one loud line, got {loud}"
        assert "NOT leaving the rig" in loud[0]

        # A fourth failure must NOT shout again — one fault, one line.
        await r._pass(dest, cfg)
        assert len([m for lvl, m in lines if lvl == "error"]) == 1

        # ...and recovery says so, because a silent recovery leaves the operator
        # believing the alarm is still true.
        monkeypatch.setattr(r, "_pass_blocking",
                            lambda d, l: push_mod.PushResult(sent=1))
        await r._pass(dest, cfg)
        assert any("working again" in msg for _lvl, msg in lines)

    @pytest.mark.asyncio
    async def test_a_quiet_pass_does_not_spam_the_log(
            self, captures, nas, cfg_push, monkeypatch):
        """96 lines of 'nothing to send' is how a log stops being read (the
        dawn-park lesson, #210)."""
        from astrodeck.events import bus
        lines: list[tuple[str, str]] = []
        monkeypatch.setattr(bus, "log",
                            lambda level, msg, src="": lines.append((level, msg)))
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        cfg = config_store.cfg()
        monkeypatch.setattr(r, "_pass_blocking", lambda d, l: push_mod.PushResult())
        await r._pass(build_destination(cfg), cfg)
        assert [lvl for lvl, _ in lines] == ["debug"]

    @pytest.mark.asyncio
    async def test_the_destination_is_announced_once_not_every_tick(
            self, captures, nas, cfg_push, monkeypatch):
        from astrodeck.events import bus
        lines: list[str] = []
        monkeypatch.setattr(bus, "log",
                            lambda level, msg, src="": lines.append(msg))
        cfg_push(enabled=True, path=str(nas))
        r = PushRunner()
        await r._tick()
        await r._tick()
        await r._tick()
        assert sum("pushing frames to" in m for m in lines) == 1


class TestStatusPayload:
    def test_reports_off_without_guessing(self, cfg_push):
        cfg_push(enabled=False)
        s = PushRunner().status()
        assert s["enabled"] is False and s["configured"] is False
        assert s["last"] is None and s["passes"] == 0

    def test_carries_the_cadences_the_panel_quotes(self, cfg_push, nas):
        cfg_push(enabled=True, path=str(nas))
        s = PushRunner().status()
        assert s["debounce_s"] == push_mod.DEBOUNCE_S
        assert s["sweep_interval_s"] == push_mod.SWEEP_INTERVAL_S
        assert s["configured"] is True


class TestNoteSaved:
    def test_never_raises_into_a_capture(self, monkeypatch):
        r = PushRunner()
        monkeypatch.setattr(r, "_clock", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        r.note_saved()                            # must not propagate

    def test_the_capture_path_calls_it(self):
        """The seam itself. A runner nothing pokes is the shape this task
        existed to close."""
        import ast
        import inspect
        from astrodeck import hub as hub_module
        src = inspect.getsource(hub_module.Hub.capture)
        tree = ast.parse(src.lstrip())
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "_note_frame_saved" in called
