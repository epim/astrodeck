"""Tests for the offline survey pack fetcher (offline-pack spec §2)."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

import astrodeck.catalog.survey_pack as sp

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 60


def _transport(fail_npix: set[int] = frozenset(), counter: dict | None = None):
    """MockTransport serving a properties file and fake JPEG tiles."""
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter[request.url.path] = counter.get(request.url.path, 0) + 1
        if request.url.path.endswith("/properties"):
            return httpx.Response(200, text=(
                "hips_tile_width      = 64\nhips_tile_format     = jpeg\n"))
        npix = int(request.url.path.rsplit("Npix", 1)[1].split(".")[0])
        if npix in fail_npix:
            return httpx.Response(200, content=b"<html>not a jpeg</html>")
        return httpx.Response(200, content=_JPEG)
    return httpx.MockTransport(handler)


@pytest.fixture
def pack_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PACK_ROOT", tmp_path / "_survey_pack")
    sp.fetch_state.finish()  # never leak running state between tests
    return tmp_path / "_survey_pack"


def test_full_fetch_writes_tree_and_manifest(pack_root):
    res = asyncio.run(sp.fetch_pack(order=0, transport=_transport()))
    assert res["failed"] == 0
    pack = sp.pack_dir()
    assert (pack / "properties").exists()
    for npix in range(12):
        assert sp.tile_path(pack, 0, npix).read_bytes() == _JPEG
    man = sp.read_manifest(pack)
    assert man["order"] == 0 and man["tile_count"] == 12
    assert man["tile_width"] == 64 and man["slug"] == "dss2color"
    assert sp.pack_present("CDS/P/DSS2/color") == pack


def test_resume_skips_existing_tiles(pack_root):
    pack = sp.pack_dir()
    pre = sp.tile_path(pack, 0, 3)
    pre.parent.mkdir(parents=True, exist_ok=True)
    pre.write_bytes(_JPEG)
    counter: dict = {}
    asyncio.run(sp.fetch_pack(order=0, transport=_transport(counter=counter)))
    assert not any(p.endswith("Npix3.jpg") for p in counter)  # skipped
    assert sum(1 for p in counter if "Npix" in p) == 11


def test_non_jpeg_counts_failed_and_blocks_manifest(pack_root):
    res = asyncio.run(sp.fetch_pack(order=0, transport=_transport(fail_npix={5})))
    assert res["failed"] == 1
    assert not sp.tile_path(sp.pack_dir(), 0, 5).exists()
    assert sp.read_manifest(sp.pack_dir()) is None
    assert sp.pack_present("CDS/P/DSS2/color") is None


def test_preflight_blocks_fresh_but_allows_resume(pack_root, monkeypatch):
    pack = sp.pack_dir()
    free = 60 * 1024 * 1024  # 60 MB free
    monkeypatch.setattr(sp.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": free})())
    # fresh order-4 fetch: 4092 tiles * 70 KB + 50 MB ≈ 337 MB required -> blocked
    with pytest.raises(sp.InsufficientSpace) as exc:
        sp.preflight_disk(pack, remaining_tiles=4092)
    assert exc.value.required > exc.value.free
    # nearly-complete resume: 3 tiles remaining -> allowed on the same card
    got_free, required = sp.preflight_disk(pack, remaining_tiles=3)
    assert got_free == free and required < free


def test_fetch_state_single_flight_and_remove_guard(pack_root):
    assert sp.fetch_state.try_start(total=10) is True
    assert sp.fetch_state.try_start(total=10) is False
    with pytest.raises(sp.FetchAlreadyRunning):
        sp.remove_pack()
    sp.fetch_state.finish()
    assert sp.fetch_state.snapshot() is None
    assert sp.remove_pack() is False  # nothing on disk yet -> False, no raise


def test_pack_status_shapes(pack_root):
    s = sp.pack_status()
    assert s == {"present": False, "slug": "dss2color",
                 "survey": "CDS/P/DSS2/color", "order": None, "bytes": None,
                 "tile_count": None, "fetched_at": None, "fetching": None}
    asyncio.run(sp.fetch_pack(order=0, transport=_transport()))
    s = sp.pack_status()
    assert s["present"] is True and s["order"] == 0 and s["bytes"] > 0


def test_remove_pack_deletes_tree(pack_root):
    asyncio.run(sp.fetch_pack(order=0, transport=_transport()))
    assert sp.remove_pack() is True
    assert not sp.pack_dir().exists()
    assert sp.pack_present("CDS/P/DSS2/color") is None
