"""#225 — the gallery renders nothing on a desktop, and the cause is a burst.

MEASURED against the relay 2026-08-10, 1920x1080 @2x: the grid put 41 tiles in
the observer's margin, set 41 `src` attributes in one frame, and the relay's
per-IP token bucket (RELAY_HTTP_BURST 40 at RELAY_HTTP_RATE 20/s) answered 19 of
them 429. Zero tiles decoded — a wall of empty boxes, one reading PREVIEW FAILED.

Two halves to the server's share of the fix, and this file pins both:

  * WARM ON CAPTURE, so the common case is a small disk read rather than a
    ~1.5 s stretch over 26 megapixels (measured on this laptop; the rig is
    slower).
  * BACKFILL, so the frames shot before the write-through existed are not
    permanently the slow case.

The lazy render stays exactly where it was. It is the fallback that makes a
missed warm a non-event, which is why the warm path is allowed to drop work.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from astrodeck import gallery
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits


@pytest.fixture
def cap(tmp_path, monkeypatch):
    import astrodeck.hub as hub_mod
    root = tmp_path / "captures"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", root)
    gallery.clear_meta_cache()
    return root


def _write(root, rel, *, value=100, size=64):
    frame = CameraFrame(data=np.full((size, size), value, np.uint16),
                        exposure_s=60.0, gain=125, offset=30, binning=1,
                        bayer_pattern=None, temperature_c=-5.0,
                        timestamp=datetime(2026, 8, 10, 1, 30).timestamp())
    return save_fits(frame, root / rel, target="NGC 6946",
                     filter_name="L", frame_type="Light")


REL = "NGC 6946/Light_NGC 6946_L_2026-08-10_013032_0051.fits"


class TestPrecompute:
    def test_warming_makes_the_thumbnail_cached(self, cap):
        _write(cap, REL)
        assert gallery.thumb_is_cached(REL, 256) is False
        assert gallery.precompute(REL, (256,)) == 1
        assert gallery.thumb_is_cached(REL, 256) is True

    def test_warming_twice_renders_nothing_the_second_time(self, cap):
        _write(cap, REL)
        assert gallery.precompute(REL, (256,)) == 1
        assert gallery.precompute(REL, (256,)) == 0

    def test_the_warmed_bytes_are_what_the_route_serves(self, cap):
        """THE test for this whole change. A warm cache the reader cannot find
        is indistinguishable from a cold one — and that is exactly what a
        private fast-path renderer would produce. Warm, then read, and require
        the same bytes."""
        _write(cap, REL)
        gallery.precompute(REL, (256,))
        served = gallery.thumbnail(REL, width=256)
        cached = gallery._thumb_cache_path(
            REL, (cap / REL).stat().st_mtime, 256).read_bytes()
        assert served == cached

    def test_the_two_render_paths_produce_identical_bytes(self, cap):
        """precompute stretches ONCE for all widths (the 1.44 s is the stretch,
        not the encode) while the lazy route goes through to_thumb. Two encoders
        for one picture is only safe while they agree — otherwise a tile changes
        appearance depending on whether anyone scrolled past it first."""
        _write(cap, REL, size=512)
        gallery.precompute(REL, (256,))
        warm = gallery.thumbnail(REL, width=256)      # reads the warm cache
        # Now force the lazy path for the same frame+width by clearing the cache.
        gallery._thumb_cache_path(REL, (cap / REL).stat().st_mtime, 256).unlink()
        lazy = gallery.thumbnail(REL, width=256)      # renders through to_thumb
        assert warm == lazy, "the warm and lazy renderers disagree"

    def test_warms_every_width_the_grid_asks_for(self, cap):
        _write(cap, REL)
        assert gallery.precompute(REL) == len(gallery.PRECOMPUTE_WIDTHS)
        for w in gallery.PRECOMPUTE_WIDTHS:
            assert gallery.thumb_is_cached(REL, w) is True

    def test_a_rewritten_frame_is_cold_again(self, cap):
        """Derived, not remembered: the key carries mtime, so nothing has to
        remember to invalidate when a path is re-captured."""
        _write(cap, REL)
        gallery.precompute(REL, (256,))
        assert gallery.thumb_is_cached(REL, 256) is True
        import os
        st = (cap / REL).stat()
        os.utime(cap / REL, (st.st_atime + 60, st.st_mtime + 60))
        assert gallery.thumb_is_cached(REL, 256) is False

    def test_a_missing_frame_is_not_an_error(self, cap):
        assert gallery.precompute("NGC 6946/nope.fits") == 0

    def test_a_path_outside_the_library_is_not_an_error(self, cap):
        assert gallery.precompute("../../etc/passwd") == 0
        assert gallery.thumb_is_cached("../../etc/passwd", 256) is False


class TestBackfill:
    def test_warms_the_whole_library(self, cap):
        for i in range(5):
            _write(cap, f"NGC 6946/Light_NGC 6946_L_2026-08-10_0130{i:02d}_000{i}.fits")
        out = gallery.backfill(widths=(256,))
        assert out["frames"] == 5
        assert out["rendered"] == 5
        assert out["unrenderable"] == 0

    def test_is_idempotent(self, cap):
        _write(cap, REL)
        gallery.backfill(widths=(256,))
        again = gallery.backfill(widths=(256,))
        assert again["rendered"] == 0
        assert again["unrenderable"] == 0

    def test_limit_bounds_the_work(self, cap):
        for i in range(6):
            _write(cap, f"NGC 6946/Light_NGC 6946_L_2026-08-10_0130{i:02d}_000{i}.fits")
        out = gallery.backfill(widths=(256,), limit=2)
        assert out["frames"] == 2

    def test_reports_progress_so_a_long_run_is_not_silent(self, cap):
        for i in range(30):
            _write(cap, f"NGC 6946/Light_NGC 6946_L_2026-08-10_0130{i:02d}_00{i:02d}.fits")
        seen = []
        gallery.backfill(widths=(256,), progress=lambda d, t, m: seen.append((d, t)))
        assert seen, "a 30-frame run reported nothing"
        assert seen[-1][0] == seen[-1][1] == 30

    def test_an_unrenderable_frame_does_not_stop_the_run(self, cap):
        _write(cap, "NGC 6946/good.fits")
        (cap / "NGC 6946" / "bad.fits").write_bytes(b"not a FITS file at all")
        out = gallery.backfill(widths=(256,))
        assert out["frames"] == 2
        assert out["rendered"] == 1
        assert out["unrenderable"] == 1


class TestTheWidthTheGridAsksFor:
    def test_the_clamp_admits_the_hidpi_width(self, cap):
        """A 2x desktop tile wants ~400 real pixels; the old clamp stopped at
        512 and the client asked for 256, so the picture arrived with fewer
        pixels than the tile had. That is the 'pixelated mess'."""
        _write(cap, REL, size=1024)
        jpeg = gallery.thumbnail(REL, width=768)
        from PIL import Image
        import io
        assert Image.open(io.BytesIO(jpeg)).width == 768

    def test_an_over_wide_request_clamps_rather_than_failing(self, cap):
        _write(cap, REL, size=1024)
        jpeg = gallery.thumbnail(REL, width=99999)
        from PIL import Image
        import io
        assert Image.open(io.BytesIO(jpeg)).width == gallery.THUMB_MAX_WIDTH


class TestCaptureWarmsTheThumbnail:
    """The write-through has to be WIRED, not merely written. A warm function
    nothing calls is the same defect class as a route with no caller."""

    def test_the_save_path_enqueues_a_warm(self):
        import inspect
        from astrodeck.hub import Hub
        src = inspect.getsource(Hub._capture_once) if hasattr(Hub, "_capture_once") \
            else inspect.getsource(Hub)
        assert "_enqueue_thumb(" in src, "nothing warms a thumbnail on save"

    async def test_the_worker_warms_a_real_frame(self, cap, monkeypatch):
        """End to end through the queue, with no HTTP and no camera."""
        import asyncio
        from astrodeck.hub import Hub
        _write(cap, REL)
        monkeypatch.setattr(gallery, "capture_root", lambda: cap)
        h = Hub()
        h._enqueue_thumb(cap / REL)
        for _ in range(200):
            await asyncio.sleep(0.02)
            if gallery.thumb_is_cached(REL, gallery.PRECOMPUTE_WIDTHS[0]):
                break
        assert gallery.thumb_is_cached(REL, gallery.PRECOMPUTE_WIDTHS[0])
        if h._thumb_task:
            h._thumb_task.cancel()

    async def test_a_broken_warm_never_reaches_the_caller(self, cap, monkeypatch):
        """A capture must not be able to fail because a convenience did."""
        from astrodeck.hub import Hub
        monkeypatch.setattr(gallery, "precompute",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        h = Hub()
        h._enqueue_thumb(cap / REL)          # must not raise
        import asyncio
        await asyncio.sleep(0.05)
        if h._thumb_task:
            h._thumb_task.cancel()

    async def test_the_backlog_is_bounded(self, cap):
        from astrodeck.hub import Hub, THUMB_WARM_QUEUE_MAX
        h = Hub()
        for i in range(THUMB_WARM_QUEUE_MAX * 4):
            h._enqueue_thumb(cap / f"f{i}.fits")
        assert h._thumb_queue.qsize() <= THUMB_WARM_QUEUE_MAX
        if h._thumb_task:
            h._thumb_task.cancel()
