"""Restart, concurrent render, and changing-library regressions (#79-81, #84)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import os
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile

import pytest
from astropy.io import fits

from astrodeck import gallery, gallery_index, gallery_listing
from astrodeck.imaging import processing
from test_gallery import cap, env, _write  # noqa: F401


def write(root, name, ts=1700000000, **kw):
    return _write(root, name, ts=ts, **kw)


def test_metadata_survives_real_process_restart(cap):
    write(cap, "one.fits")
    write(cap, "two.fits")
    gallery.scan()
    script = """from pathlib import Path
import sys
from astrodeck import gallery
def forbidden(path):
    raise AssertionError('reopened an unchanged FITS header')
gallery._read_header_meta = forbidden
rows, truncated = gallery.scan(Path(sys.argv[1]))
assert len(rows) == 2 and not truncated
assert rows[0]['width'] == 16
"""
    result = subprocess.run([sys.executable, "-c", script, str(cap)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_reconcile_add_replace_rename_delete_and_limit(cap, monkeypatch):
    write(cap, "one.fits", exposure=60)
    write(cap, "two.fits", exposure=120)
    gallery.scan()
    original_stat = (cap / "one.fits").stat()
    write(cap, "replacement.fits", exposure=30)
    os.utime(cap / "replacement.fits", ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    (cap / "replacement.fits").replace(cap / "one.fits")
    (cap / "two.fits").rename(cap / "renamed.fits")
    gallery.clear_meta_cache()
    rows, _ = gallery.scan()
    assert {r["path"]: r["exposure_s"] for r in rows} == {"one.fits": 30, "renamed.fits": 120}
    gallery.scan(limit=1)
    gallery.clear_meta_cache()
    monkeypatch.setattr(gallery, "_read_header_meta", lambda _: pytest.fail("bounded scan discarded valid metadata"))
    assert len(gallery.scan()[0]) == 2
    (cap / "one.fits").unlink()
    assert [r["path"] for r in gallery.scan()[0]] == ["renamed.fits"]
    with sqlite3.connect(cap / gallery_index.DIRECTORY / "metadata-v1.sqlite3") as db:
        assert db.execute("SELECT path FROM metadata").fetchall() == [("renamed.fits",)]


def test_corrupt_index_recovers_without_hiding_frames(cap, monkeypatch):
    write(cap, "one.fits")
    cache = cap / gallery_index.DIRECTORY / "metadata-v1.sqlite3"
    cache.parent.mkdir()
    cache.write_bytes(b"not a database")
    assert len(gallery.scan()[0]) == 1
    gallery.clear_meta_cache()
    monkeypatch.setattr(gallery, "_read_header_meta", lambda _: pytest.fail("corrupt index was never rebuilt"))
    assert len(gallery.scan()[0]) == 1


def test_unwritable_index_falls_back_to_headers(cap, monkeypatch):
    write(cap, "one.fits")
    def unavailable(*a, **kw):
        raise sqlite3.OperationalError("read-only filesystem")
    monkeypatch.setattr(gallery_index.sqlite3, "connect", unavailable)
    assert gallery.scan()[0][0]["width"] == 16


def test_transient_header_failure_does_not_poison_persistent_cache(cap, monkeypatch):
    write(cap, "one.fits", target="Recovered target")
    original = fits.getheader
    calls = 0
    def flaky(*a, **kw):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("writer still owns frame")
        return original(*a, **kw)
    monkeypatch.setattr(fits, "getheader", flaky)
    assert gallery.scan()[0][0]["width"] is None
    gallery.clear_meta_cache()
    assert gallery.scan()[0][0]["target"] == "Recovered target"


def test_snapshot_pages_ignore_arrivals_deletions_and_tied_timestamps(env, monkeypatch):
    client, root = env
    for name in ("a.fits", "b.fits", "c.fits", "d.fits"):
        write(root, name)
    first = client.get("/api/gallery/frames", params={"limit": 2}).json()
    assert [r["path"] for r in first["frames"]] == ["d.fits", "c.fits"]
    write(root, "new.fits", ts=1800000000)
    (root / "d.fits").unlink()
    monkeypatch.setattr(gallery, "scan", lambda *a, **kw: pytest.fail("pagination rescanned the library"))
    second = client.get("/api/gallery/frames", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [r["path"] for r in second["frames"]] == ["b.fits", "a.fits"]
    assert second["total"] == 4 and second["next_cursor"] is None
    enumeration = client.get("/api/gallery/frames", params={"cursor": first["snapshot"] + ":0"}).json()
    assert len(enumeration["frames"]) == 4


def test_snapshot_download_and_delete_never_include_new_captures(env):
    client, root = env
    write(root, "a.fits")
    snapshot = client.get("/api/gallery/frames").json()["snapshot"]
    write(root, "new.fits", ts=1800000000)
    response = client.get("/api/gallery/download.zip", params={"snapshot": snapshot})
    assert response.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == ["a.fits"]
    result = client.post("/api/gallery/trash", json={"paths": ["a.fits"], "snapshot": snapshot})
    assert result.status_code == 200
    assert (root / "new.fits").exists()


def test_changed_selection_requires_refresh_before_download_or_delete(env):
    client, root = env
    write(root, "a.fits")
    snapshot = client.get("/api/gallery/frames").json()["snapshot"]
    write(root, "a.fits", exposure=20)
    assert client.get("/api/gallery/download.zip", params={"snapshot": snapshot}).status_code == 409
    assert client.post("/api/gallery/trash", json={"paths": ["a.fits"], "snapshot": snapshot}).status_code == 409
    assert (root / "a.fits").exists()


def test_expired_and_wrong_filter_cursors_refuse_instead_of_restarting(env, monkeypatch):
    client, root = env
    write(root, "a.fits")
    snapshot = client.get("/api/gallery/frames").json()["snapshot"]
    assert client.get("/api/gallery/frames", params={"cursor": snapshot + ":0", "q": "other"}).status_code == 409
    assert client.get("/api/gallery/frames", params={"cursor": "garbage"}).status_code == 422
    monkeypatch.setattr(gallery_listing, "TTL_SECONDS", -1)
    assert client.get("/api/gallery/frames", params={"cursor": snapshot + ":0"}).status_code == 409


def test_snapshot_storage_is_bounded(cap, monkeypatch):
    write(cap, "a.fits")
    monkeypatch.setattr(gallery_listing, "MAX_LISTINGS", 2)
    entries = [gallery_listing.create() for _ in range(3)]
    with pytest.raises(gallery_listing.ListingExpired):
        gallery_listing.get(entries[0].token)
    assert gallery_listing.get(entries[-1].token).total == 1
    # A reader holding a reference survives LRU eviction.
    assert entries[0].rows()[0]["path"] == "a.fits"


def test_identical_concurrent_thumbnails_share_one_render(cap, monkeypatch):
    write(cap, "a.fits")
    started, release = threading.Event(), threading.Event()
    calls = []
    def render(*a, **kw):
        calls.append(1)
        started.set()
        assert release.wait(3)
        return b"jpeg"
    monkeypatch.setattr(processing, "to_thumb", render)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(gallery.thumbnail, "a.fits") for _ in range(8)]
        try:
            assert started.wait(2)
        finally:
            release.set()
        assert [f.result() for f in futures] == [b"jpeg"] * 8
    assert len(calls) == 1


def test_thumbnail_view_and_precompute_share_render_budget(cap, monkeypatch):
    for name in ("a.fits", "b.fits", "c.fits"):
        write(cap, name)
    real = processing.auto_stretch
    active = peak = 0
    lock = threading.Lock()
    def stretch(*args, **kw):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(.02)
            return real(*args, **kw)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(processing, "auto_stretch", stretch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(gallery.thumbnail, "a.fits"), pool.submit(gallery.view, "b.fits", width=640),
                   pool.submit(gallery.precompute, "c.fits")]
        assert all(f.result() for f in futures)
    assert peak == 1


async def test_busy_renderer_keeps_warm_hits_and_default_workers_available(cap, monkeypatch):
    write(cap, "warm.fits")
    gallery.thumbnail("warm.fits")
    write(cap, "cold.fits")
    write(cap, "extra.fits")
    started, release = threading.Event(), threading.Event()
    def render(*a, **kw):
        started.set()
        assert release.wait(3)
        return b"jpeg"
    monkeypatch.setattr(processing, "to_thumb", render)
    monkeypatch.setattr(gallery, "MAX_PENDING_RENDERS", 1)
    pending = asyncio.create_task(gallery.thumbnail_async("cold.fits"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        with pytest.raises(gallery.RenderBusy):
            await gallery.thumbnail_async("extra.fits")
        assert await asyncio.wait_for(gallery.thumbnail_async("warm.fits"), .5)
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "free"), .5) == "free"
    finally:
        release.set()
        await pending


def test_subsecond_replacement_invalidates_thumbnail_and_precompute(cap, monkeypatch):
    write(cap, "a.fits")
    os.utime(cap / "a.fits", (1700000000.1, 1700000000.1))
    monkeypatch.setattr(processing, "to_thumb", lambda *a, **kw: b"old")
    assert gallery.thumbnail("a.fits") == b"old"
    write(cap, "a.fits", value=900)
    os.utime(cap / "a.fits", (1700000000.2, 1700000000.2))
    monkeypatch.setattr(processing, "to_thumb", lambda *a, **kw: b"new")
    assert not gallery.thumb_is_cached("a.fits", 256)
    assert gallery.thumbnail("a.fits") == b"new"


async def test_cancelled_request_does_not_cancel_shared_render(cap, monkeypatch):
    write(cap, "a.fits")
    started, release = threading.Event(), threading.Event()
    def render(*a, **kw):
        started.set()
        assert release.wait(3)
        return b"jpeg"
    monkeypatch.setattr(processing, "to_thumb", render)
    first = asyncio.create_task(gallery.thumbnail_async("a.fits"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        second = asyncio.create_task(gallery.thumbnail_async("a.fits"))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    finally:
        release.set()
    assert await second == b"jpeg"
    assert gallery.thumbnail("a.fits") == b"jpeg"


async def test_failed_render_releases_ownership_for_retry(cap, monkeypatch):
    write(cap, "a.fits")
    def fail(*a, **kw):
        raise ValueError("bad image")
    monkeypatch.setattr(processing, "to_thumb", fail)
    with pytest.raises(ValueError, match="bad image"):
        await gallery.thumbnail_async("a.fits")
    monkeypatch.setattr(processing, "to_thumb", lambda *a, **kw: b"recovered")
    assert await asyncio.wait_for(gallery.thumbnail_async("a.fits"), 1) == b"recovered"


def test_busy_preview_route_returns_retryable_status(env, monkeypatch):
    client, _ = env
    async def busy(*a, **kw):
        raise gallery.RenderBusy("busy")
    monkeypatch.setattr(gallery, "thumbnail_async", busy)
    for endpoint in ("thumb", "view"):
        response = client.get(f"/api/gallery/{endpoint}", params={"path": "a.fits"})
        assert response.status_code == 503 and response.headers["Retry-After"] == "1"


def test_snapshot_preserves_get_selection_bound_and_larger_post_bound(env):
    client, root = env
    write(root, "a.fits")
    snapshot = client.get("/api/gallery/frames").json()["snapshot"]
    paths = ["a.fits"] * 1001
    for endpoint in ("summary", "download.zip"):
        assert client.get(f"/api/gallery/{endpoint}", params={"snapshot": snapshot, "path": paths}).status_code == 422
    # The POST body already has its own larger bound; it must not inherit the
    # URL limit when validating the same snapshot (UI batches allow 2,000).
    assert client.post("/api/gallery/trash", json={"snapshot": snapshot, "paths": paths}).status_code == 200
