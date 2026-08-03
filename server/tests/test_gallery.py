"""Gallery: listing, night filter, thumbnails, streamed bulk download, trash.

The four tests that carry the design are named in the spec and are the reason
this file exists; everything else is coverage around them.

  1. ``test_frames_at_2350_and_0010_land_in_the_same_night`` — the whole point of
     GROUNDED 1. The two frames' FILENAMES carry different calendar dates (the
     default template renders ``$$DATE$$``), so any implementation that reads the
     night off the name fails this and splits every real session at midnight.
  2. ``test_bulk_download_is_streamed_not_materialised`` — proves the zip is
     produced lazily and in bounded chunks rather than buffered.
  3. ``test_purge_refuses_a_path_outside_the_trash_root`` — the containment
     contract; the shared attack corpus is bound to the purge route over in
     ``test_path_traversal.py`` rather than re-derived here.
  4. ``test_deleted_flat_does_not_reappear_in_a_master`` — the collision between
     a trash inside CAPTURE_DIR and the calibration scanner's rglob. A deletion
     that does not take effect is worse than one that fails.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck import gallery
from astrodeck.config import ConfigStore
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits


# --------------------------------------------------------------------- harness

@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated app with config + CAPTURE_DIR under tmp (test_calibration_api's
    fixture, verbatim in shape). Yields ``(client, capture_dir)``."""
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    cap = tmp_path / "captures"
    cap.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", cap)
    # The header cache is a module singleton keyed by absolute path; tmp_path is
    # unique per test, so a stale entry cannot collide — but a test that rewrites
    # a frame within one mtime tick could, so start every test cold.
    gallery.clear_meta_cache()
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, cap


@pytest.fixture
def cap(tmp_path, monkeypatch):
    """CAPTURE_DIR only — for the store-level tests that need no HTTP surface."""
    import astrodeck.hub as hub_mod
    root = tmp_path / "captures"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", root)
    gallery.clear_meta_cache()
    return root


def _write(root, rel, *, ts, frame_type="Light", target="M42", filt="L",
           exposure=300.0, size=16, value=100):
    """One real FITS on disk. ``ts`` becomes DATE-OBS, which is what the night is
    derived from — deliberately independent of the filename ``rel``."""
    frame = CameraFrame(data=np.full((size, size), value, np.uint16),
                        exposure_s=exposure, gain=100, offset=30, binning=1,
                        bayer_pattern=None, temperature_c=-10.0, timestamp=ts)
    return save_fits(frame, root / rel, target=target, filter_name=filt,
                     frame_type=frame_type)


def _local(y, mo, d, h, mi) -> float:
    """A local wall-clock instant as a POSIX timestamp. The night rollover is
    local by definition (an observer's midnight, not Greenwich's), so these
    tests have to speak local time or they would pass or fail by timezone."""
    return datetime(y, mo, d, h, mi).timestamp()


# ------------------------------------------------------- GROUNDED 1: the night

def test_frames_at_2350_and_0010_land_in_the_same_night(env):
    """THE test. Two frames ten minutes apart across midnight are one session.

    Their filenames carry DIFFERENT calendar dates because that is what the
    default template writes (``$$DATE$$``), so an implementation that parses the
    filename returns two nights here and fails. The night must come from the
    frame's timestamp through ``events.night_key`` — one noon-to-noon night."""
    c, root = env
    _write(root, "M42/Light_M42_L_2026-06-15_235000_0001.fits",
           ts=_local(2026, 6, 15, 23, 50))
    _write(root, "M42/Light_M42_L_2026-06-16_001000_0002.fits",
           ts=_local(2026, 6, 16, 0, 10))

    rows = c.get("/api/gallery/frames").json()["frames"]
    assert len(rows) == 2
    nights = {r["night"] for r in rows}
    assert nights == {"2026-06-15"}, (
        f"the two halves of one night were filed as {sorted(nights)} — "
        "the night was derived from the filename, not from DATE-OBS")


def test_night_filter_returns_the_whole_night_not_half_of_it(env):
    """The consequence of the above, at the filter. Asking for 2026-06-15 must
    return BOTH frames; a filename-derived night returns one and looks fine."""
    c, root = env
    _write(root, "M42/Light_M42_L_2026-06-15_235000_0001.fits",
           ts=_local(2026, 6, 15, 23, 50))
    _write(root, "M42/Light_M42_L_2026-06-16_001000_0002.fits",
           ts=_local(2026, 6, 16, 0, 10))
    # A frame from the NEXT night, to prove the filter still excludes something.
    _write(root, "M42/Light_M42_L_2026-06-16_220000_0003.fits",
           ts=_local(2026, 6, 16, 22, 0))

    r = c.get("/api/gallery/frames",
              params={"night_from": "2026-06-15", "night_to": "2026-06-15"})
    body = r.json()
    assert body["total"] == 2
    assert {f["name"][-9:] for f in body["frames"]} == {"0001.fits", "0002.fits"}


def test_night_falls_back_to_mtime_when_the_header_cannot_be_read(cap):
    """A frame whose header is unreadable still appears, filed by mtime. Losing
    a row from the user's library because one file is corrupt is not acceptable;
    an approximate night is."""
    bad = cap / "M42" / "garbage.fits"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a fits file at all")
    rows, _ = gallery.scan()
    assert len(rows) == 1
    assert rows[0]["night"] == gallery.night_key(bad.stat().st_mtime)
    assert rows[0]["target"] == "M42"      # falls back to the folder name


def test_malformed_night_bound_is_refused_not_ignored(env):
    """A silently-ignored bad bound returns the whole library and looks like a
    filter that worked."""
    c, _ = env
    assert c.get("/api/gallery/frames",
                 params={"night_from": "last week"}).status_code == 422
    assert c.get("/api/gallery/frames",
                 params={"night_to": "2026-6-15"}).status_code == 422


# ------------------------------------------------------------- listing / search

def test_listing_carries_what_a_grid_needs(env):
    c, root = env
    _write(root, "M42/Light_M42_Ha_2026-06-15_220000_0001.fits",
           ts=_local(2026, 6, 15, 22, 0), filt="Ha", exposure=180.0)
    row = c.get("/api/gallery/frames").json()["frames"][0]
    assert row["path"] == "M42/Light_M42_Ha_2026-06-15_220000_0001.fits"
    assert row["folder"] == "M42"
    assert row["target"] == "M42"
    assert row["filter"] == "Ha"
    assert row["frame_type"] == "Light"
    assert row["exposure_s"] == 180.0
    assert row["bytes"] > 0 and row["mtime"] > 0
    assert row["night"] == "2026-06-15"


def test_search_matches_the_whole_relative_path(env):
    """A flat-per-target tree makes people type the target folder first."""
    c, root = env
    _write(root, "M42/Light_M42_L_2026-06-15_220000_0001.fits",
           ts=_local(2026, 6, 15, 22, 0))
    _write(root, "NGC 7000/Light_NGC7000_L_2026-06-15_223000_0001.fits",
           ts=_local(2026, 6, 15, 22, 30), target="NGC 7000")
    assert c.get("/api/gallery/frames", params={"q": "ngc"}).json()["total"] == 1
    assert c.get("/api/gallery/frames", params={"q": "0001"}).json()["total"] == 2


def test_pagination_pages_the_filtered_set_and_totals_describe_all_of_it(env):
    c, root = env
    for i in range(5):
        _write(root, f"M42/Light_M42_L_2026-06-15_2200{i:02d}_{i:04d}.fits",
               ts=_local(2026, 6, 15, 22, i))
    body = c.get("/api/gallery/frames", params={"limit": 2, "offset": 2}).json()
    assert len(body["frames"]) == 2
    assert body["total"] == 5              # the whole set, not the page
    assert body["bytes"] == sum(f["bytes"] for f in
                                c.get("/api/gallery/frames").json()["frames"])


def test_newest_capture_first(env):
    c, root = env
    _write(root, "M42/old.fits", ts=_local(2026, 6, 15, 22, 0))
    _write(root, "M42/new.fits", ts=_local(2026, 6, 15, 23, 0))
    names = [f["name"] for f in c.get("/api/gallery/frames").json()["frames"]]
    assert names == ["new.fits", "old.fits"]


def test_infrastructure_directories_are_not_frames(env):
    """``exports/`` holds HARDLINKS to frames that are also listed at their real
    path, so counting it would double every byte of the "38.2 GB" summary; the
    rest are caches, ledgers and the trash."""
    c, root = env
    _write(root, "M42/real.fits", ts=_local(2026, 6, 15, 22, 0))
    for skip in ("exports", "_masters", "_solve", "_trash", "sessions"):
        _write(root, f"{skip}/x.fits", ts=_local(2026, 6, 15, 22, 0))
    body = c.get("/api/gallery/frames").json()
    assert body["total"] == 1
    assert body["frames"][0]["path"] == "M42/real.fits"


def test_a_target_folder_named_like_infrastructure_is_still_listed(cap):
    """Pruning is by FIRST path component only. Someone imaging an object into a
    folder called ``logs`` deep in the tree must not have their frames vanish."""
    _write(cap, "M42/logs/Light_0001.fits", ts=_local(2026, 6, 15, 22, 0))
    rows, _ = gallery.scan()
    assert [r["path"] for r in rows] == ["M42/logs/Light_0001.fits"]


def test_nights_index_offers_only_nights_that_exist(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 23, 50))
    _write(root, "M42/b.fits", ts=_local(2026, 6, 16, 0, 10))
    _write(root, "M42/c.fits", ts=_local(2026, 6, 17, 21, 0))
    nights = c.get("/api/gallery/nights").json()["nights"]
    # Two nights, not three: the 23:50 and 00:10 frames are one observing night.
    assert [n["night"] for n in nights] == ["2026-06-17", "2026-06-15"]
    assert nights[1]["frames"] == 2        # the split night counted as one


# -------------------------------------------------------------------- summary

def test_summary_matches_what_the_download_will_contain(env):
    """The number on the button and the bytes on the wire come from one code
    path with identical parameters, so they cannot disagree."""
    c, root = env
    for i in range(3):
        _write(root, f"M42/f{i}.fits", ts=_local(2026, 6, 15, 22, i))
    summary = c.get("/api/gallery/summary").json()
    listing = c.get("/api/gallery/frames").json()
    assert summary["count"] == listing["total"] == 3
    assert summary["bytes"] == listing["bytes"]

    with c.stream("GET", "/api/gallery/download.zip") as r:
        assert r.headers["x-gallery-frames"] == "3"
        assert r.headers["x-gallery-bytes"] == str(summary["bytes"])
        r.read()


def test_summary_of_an_explicit_selection_reports_refusals(env):
    c, root = env
    _write(root, "M42/keep.fits", ts=_local(2026, 6, 15, 22, 0))
    body = c.get("/api/gallery/summary",
                 params={"path": ["M42/keep.fits", "../escape.fits",
                                  "M42/never-existed.fits"]}).json()
    assert body["count"] == 1
    reasons = {f["path"]: f["reason"] for f in body["failed"]}
    assert set(reasons) == {"../escape.fits", "M42/never-existed.fits"}


# --------------------------------------------- GROUNDED 2: the streamed download

def test_bulk_download_is_streamed_not_materialised(cap):
    """The generator must produce bounded chunks lazily.

    Asserted at the generator, where "not fully materialised" is observable: the
    first chunk arrives after reading a fraction of the payload, and no chunk
    exceeds the relay's frame ceiling. A buffered implementation would have to
    read every source file before yielding anything."""
    payload = 4 * 1024 * 1024
    for i in range(4):
        (cap / f"big{i}.fits").write_bytes(b"\0" * payload)
    members = [(cap / f"big{i}.fits", f"big{i}.fits") for i in range(4)]

    gen = gallery.iter_zip(members)
    got = [next(gen) for _ in range(9)]
    assert all(0 < len(ch) <= gallery.ZIP_CHUNK_BYTES for ch in got)
    # Nine chunks in, we must still be far from the total — i.e. the generator
    # has not quietly run to completion behind our back.
    produced = sum(len(ch) for ch in got)
    assert produced < payload, (
        f"{produced} bytes materialised before the 10th chunk — "
        "the archive is being built up front, not streamed")

    got.extend(gen)
    blob = b"".join(got)
    assert len(blob) > 4 * payload * 0.99
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert sorted(z.namelist()) == [f"big{i}.fits" for i in range(4)]
        assert z.testzip() is None


def test_every_streamed_chunk_fits_one_relay_frame(cap):
    """The relay tunnels bodies in 64 KiB frames with an awaited send; a chunk
    larger than that either splits (losing the one-chunk-one-frame property that
    lets backpressure reach the disk read) or trips the decoder's hard ceiling."""
    (cap / "a.fits").write_bytes(b"\0" * (700 * 1024))
    chunks = list(gallery.iter_zip([(cap / "a.fits", "a.fits")]))
    assert len(chunks) > 1
    assert max(len(c) for c in chunks) <= gallery.ZIP_CHUNK_BYTES
    from astrodeck.remote.protocol import DEFAULT_MAX_PAYLOAD
    assert gallery.ZIP_CHUNK_BYTES == DEFAULT_MAX_PAYLOAD


def test_archive_is_stored_not_deflated(cap):
    """FITS is dense integer data deflate barely touches, and the CPU it costs is
    a Pi core the rig needs for guiding."""
    _write(cap, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    blob = b"".join(gallery.iter_zip([(cap / "M42" / "a.fits", "M42/a.fits")]))
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        info = z.getinfo("M42/a.fits")
        assert info.compress_type == zipfile.ZIP_STORED
        assert info.compress_size == info.file_size


def test_download_route_streams_and_preserves_the_folder_layout(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    _write(root, "NGC 7000/b.fits", ts=_local(2026, 6, 15, 22, 5),
           target="NGC 7000")
    with c.stream("GET", "/api/gallery/download.zip") as r:
        assert r.status_code == 200
        # A materialised Response sets Content-Length; a streamed one cannot.
        assert "content-length" not in {k.lower() for k in r.headers}
        assert r.headers["content-disposition"].endswith('frames-2.zip"')
        blob = b"".join(r.iter_bytes())
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert sorted(z.namelist()) == ["M42/a.fits", "NGC 7000/b.fits"]


def test_download_of_one_night_names_the_file_after_it(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 23, 50))
    with c.stream("GET", "/api/gallery/download.zip",
                  params={"night_from": "2026-06-15",
                          "night_to": "2026-06-15"}) as r:
        assert 'filename="astrodeck-2026-06-15.zip"' in r.headers["content-disposition"]
        r.read()


def test_download_of_nothing_is_a_404_not_an_empty_zip(env):
    """An archive with nothing in it is a download that looks like it worked."""
    c, _ = env
    assert c.get("/api/gallery/download.zip",
                 params={"q": "nothing-matches-this"}).status_code == 404


def test_download_skips_a_frame_that_vanished_mid_stream(cap):
    """Headers are already on the wire when the body starts, so a missing source
    must not raise: the user gets a valid archive their unzip tool reports as
    short, not a corrupt one it cannot open."""
    (cap / "here.fits").write_bytes(b"\0" * 1024)
    members = [(cap / "here.fits", "here.fits"),
               (cap / "gone.fits", "gone.fits")]
    blob = b"".join(gallery.iter_zip(members))
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert z.namelist() == ["here.fits"]


# ------------------------------------------------------------------ thumbnails

def test_thumbnail_renders_once_and_is_served_from_cache(env):
    """Neither existing store is reusable — the preview ring is memory-only and
    capped at 50, and session thumbs exist only for SEQUENCE frames. This is the
    third store; it must at least not re-render on every scroll."""
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0), size=64)
    r = c.get("/api/gallery/thumb", params={"path": "M42/a.fits"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"          # JPEG SOI
    cached = list((root / gallery.THUMBS_DIRNAME).glob("*.jpg"))
    assert len(cached) == 1
    assert cached[0].read_bytes() == r.content

    # Second request must be byte-identical and must not add a cache entry.
    again = c.get("/api/gallery/thumb", params={"path": "M42/a.fits"})
    assert again.content == r.content
    assert len(list((root / gallery.THUMBS_DIRNAME).glob("*.jpg"))) == 1


def test_thumbnail_cache_key_includes_mtime(cap):
    """A re-capture at the same path must not serve last week's picture."""
    _write(cap, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0), size=64, value=100)
    gallery.thumbnail("M42/a.fits")
    first = {p.name for p in (cap / gallery.THUMBS_DIRNAME).glob("*.jpg")}
    time.sleep(1.05)                     # mtime granularity is a second on some FS
    _write(cap, "M42/a.fits", ts=_local(2026, 6, 16, 22, 0), size=64, value=900)
    gallery.clear_meta_cache()
    gallery.thumbnail("M42/a.fits")
    both = {p.name for p in (cap / gallery.THUMBS_DIRNAME).glob("*.jpg")}
    assert first < both, "the re-captured frame reused the old cache entry"


def test_thumbnail_refuses_a_path_outside_the_library(env):
    c, _ = env
    assert c.get("/api/gallery/thumb",
                 params={"path": "../../secret.fits"}).status_code == 404
    assert c.get("/api/gallery/thumb",
                 params={"path": "_trash/x.fits"}).status_code == 404
    # ``safe_subpath`` treats a backslash as a separator on BOTH platforms, so
    # the infrastructure-prefix guard has to normalize before it splits or this
    # one slips past it and renders a deleted frame.
    assert c.get("/api/gallery/thumb",
                 params={"path": "_trash\\x.fits"}).status_code == 404
    assert c.get("/api/gallery/thumb",
                 params={"path": "notes.txt"}).status_code == 404


def test_unrenderable_frame_is_422_so_the_grid_can_still_offer_the_download(env):
    """A frame we cannot render is not a frame that is missing."""
    c, root = env
    (root / "M42").mkdir(parents=True)
    (root / "M42" / "broken.fits").write_bytes(b"junk")
    assert c.get("/api/gallery/thumb",
                 params={"path": "M42/broken.fits"}).status_code == 422


# ------------------------------------------------------------- single download

def test_single_frame_download_returns_the_fits(env):
    c, root = env
    p = _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    r = c.get("/api/gallery/file", params={"path": "M42/a.fits"})
    assert r.status_code == 200
    assert r.content == p.read_bytes()
    assert 'filename="a.fits"' in r.headers["content-disposition"]


def test_single_frame_download_refuses_escapes_and_non_frames(env):
    c, root = env
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    for bad in ("../../etc/passwd", "notes.txt", "_masters/x.fits"):
        assert c.get("/api/gallery/file",
                     params={"path": bad}).status_code == 404, bad


# ------------------------------------------------------------------------ trash

def test_delete_is_a_rename_that_preserves_the_relative_path(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    r = c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    assert r.status_code == 200, r.text
    assert r.json()["trashed"][0]["original"] == "M42/a.fits"
    assert not (root / "M42" / "a.fits").exists()
    assert (root / gallery.TRASH_DIRNAME / "M42" / "a.fits").is_file()
    assert c.get("/api/gallery/frames").json()["total"] == 0


def test_trash_sidecar_records_where_it_came_from_and_when(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    before = time.time()
    c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    sidecar = (root / gallery.TRASH_DIRNAME / "M42"
               / ("a.fits" + gallery.TRASH_INFO_SUFFIX))
    info = json.loads(sidecar.read_text(encoding="utf-8"))
    assert info["original"] == "M42/a.fits"
    assert info["deleted_at"] >= before
    assert info["bytes"] > 0


def test_trash_listing_says_when_each_item_goes_and_whether_it_can_return(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    body = c.get("/api/gallery/trash").json()
    assert body["count"] == 1 and body["ttl_days"] == 30
    item = body["items"][0]
    assert item["path"] == "M42/a.fits"
    assert item["original"] == "M42/a.fits"
    assert item["restorable"] is True
    assert item["expires_at"] == pytest.approx(item["deleted_at"] + 30 * 86400)

    # Something else now occupies the original path -> honestly not restorable.
    _write(root, "M42/a.fits", ts=_local(2026, 6, 16, 22, 0))
    assert c.get("/api/gallery/trash").json()["items"][0]["restorable"] is False


def test_restore_puts_the_frame_back(env):
    """A bin without restore is a delayed delete."""
    c, root = env
    original = _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    payload = original.read_bytes()
    c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    r = c.post("/api/gallery/trash/restore", json={"paths": ["M42/a.fits"]})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == [{"path": "M42/a.fits",
                                     "restored_to": "M42/a.fits"}]
    assert (root / "M42" / "a.fits").read_bytes() == payload
    assert c.get("/api/gallery/trash").json()["count"] == 0
    assert not (root / gallery.TRASH_DIRNAME / "M42"
                / ("a.fits" + gallery.TRASH_INFO_SUFFIX)).exists()


def test_restore_refuses_to_overwrite_a_live_frame(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0), value=100)
    c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    _write(root, "M42/a.fits", ts=_local(2026, 6, 16, 22, 0), value=900)
    r = c.post("/api/gallery/trash/restore", json={"paths": ["M42/a.fits"]})
    assert r.json()["restored"] == []
    assert "already exists" in r.json()["failed"][0]["reason"]


def test_two_deletions_of_the_same_path_both_survive(env):
    """``os.replace`` overwrites silently, so without a unique destination the
    first deletion would vanish with no error."""
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    _write(root, "M42/a.fits", ts=_local(2026, 6, 16, 22, 0))
    c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    body = c.get("/api/gallery/trash").json()
    assert body["count"] == 2
    assert {i["original"] for i in body["items"]} == {"M42/a.fits"}
    assert {i["path"] for i in body["items"]} == {"M42/a.fits", "M42/a-1.fits"}


def test_delete_reports_each_refusal_instead_of_silently_dropping_it(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    r = c.post("/api/gallery/trash", json={
        "paths": ["M42/a.fits", "../escape.fits", "_masters/m.fits",
                  "M42/never.fits"]})
    body = r.json()
    assert [t["original"] for t in body["trashed"]] == ["M42/a.fits"]
    assert {f["path"] for f in body["failed"]} == {
        "../escape.fits", "_masters/m.fits", "M42/never.fits"}


def test_purge_now_deletes_for_real(env):
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]})
    r = c.post("/api/gallery/trash/purge", json={"paths": ["M42/a.fits"]})
    assert r.json()["purged"] == 1 and r.json()["bytes"] > 0
    assert c.get("/api/gallery/trash").json()["count"] == 0
    assert not (root / gallery.TRASH_DIRNAME / "M42" / "a.fits").exists()
    assert not (root / gallery.TRASH_DIRNAME / "M42"
                / ("a.fits" + gallery.TRASH_INFO_SUFFIX)).exists()


def test_purge_all_empties_the_bin_and_needs_the_explicit_flag(env):
    c, root = env
    for i in range(3):
        _write(root, f"M42/f{i}.fits", ts=_local(2026, 6, 15, 22, i))
        c.post("/api/gallery/trash", json={"paths": [f"M42/f{i}.fits"]})
    # An empty paths list is NOT "empty the trash" — that has to be asked for.
    assert c.post("/api/gallery/trash/purge", json={"paths": []}).status_code == 422
    assert c.get("/api/gallery/trash").json()["count"] == 3
    assert c.post("/api/gallery/trash/purge", json={"all": True}).json()["purged"] == 3
    assert c.get("/api/gallery/trash").json()["count"] == 0


def test_purge_refuses_a_path_outside_the_trash_root(cap):
    """Containment at the store level. The full shared attack corpus is bound to
    the purge ROUTE in tests/test_path_traversal.py."""
    victim = cap / "M42" / "keepme.fits"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"live frame")
    gallery.trash_root().mkdir(parents=True, exist_ok=True)
    out = gallery.purge_paths(["../M42/keepme.fits", "../../etc/passwd"])
    assert out["purged"] == 0
    assert len(out["failed"]) == 2
    assert victim.exists()


def test_auto_purge_deletes_at_thirty_days_and_keeps_the_rest(cap):
    _write(cap, "M42/old.fits", ts=_local(2026, 6, 15, 22, 0))
    _write(cap, "M42/new.fits", ts=_local(2026, 6, 15, 22, 1))
    gallery.trash_frames(["M42/old.fits", "M42/new.fits"])
    # Age the sidecar of exactly one of them past the horizon.
    old_info = (gallery.trash_root() / "M42"
                / ("old.fits" + gallery.TRASH_INFO_SUFFIX))
    info = json.loads(old_info.read_text(encoding="utf-8"))
    info["deleted_at"] = time.time() - 31 * 86400
    old_info.write_text(json.dumps(info), encoding="utf-8")

    out = gallery.purge_expired()
    assert out["purged"] == 1
    remaining = [i["path"] for i in gallery.list_trash()["items"]]
    assert remaining == ["M42/new.fits"]


def test_auto_purge_ages_by_deletion_not_by_capture_time(cap):
    """mtime survives the rename into the trash, so it is the CAPTURE time.
    Ageing by it would purge a two-month-old frame the instant it was deleted —
    destroying exactly the data the 30-day grace period protects."""
    path = _write(cap, "M42/ancient.fits", ts=_local(2026, 6, 15, 22, 0))
    import os
    old = time.time() - 120 * 86400
    os.utime(path, (old, old))
    gallery.trash_frames(["M42/ancient.fits"])
    assert gallery.purge_expired()["purged"] == 0
    assert gallery.list_trash()["count"] == 1


def test_auto_purge_tick_is_a_no_op_on_an_empty_rig(cap):
    """The keeper's first tick runs at boot on every install, including one that
    has never deleted anything."""
    import asyncio
    keeper = gallery.TrashKeeper()
    assert asyncio.run(keeper.tick()) == {"purged": 0, "bytes": 0, "failed": []}


def test_orphan_sidecar_is_swept(cap):
    _write(cap, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    gallery.trash_frames(["M42/a.fits"])
    (gallery.trash_root() / "M42" / "a.fits").unlink()   # purged by hand
    assert gallery.sweep_orphan_sidecars() == 1
    assert gallery.list_trash()["count"] == 0


# ------------------------------- GROUNDED 3: the trash vs. the other tree walks

def test_deleted_flat_does_not_reappear_in_a_master(env):
    """THE other test. The calibration scanner rglobs every ``*.fits`` under the
    capture dir, and the trash lives there because a rename is only atomic on one
    volume. Without ``_trash`` in ``EXCLUDE_DIRS`` a deleted flat is stacked
    straight back into a master: gone from the gallery, still in the data.

    Six flats, three of them deleted. The master must be built from three."""
    c, root = env
    for i in range(6):
        _write(root, f"flats/flat_{i}.fits", ts=_local(2026, 6, 15, 20, i),
               frame_type="Flat", target="", filt="L", exposure=2.0,
               value=1000 + i)
    assert c.post("/api/calibration/build").json()["frames_indexed"] == 6

    doomed = [f"flats/flat_{i}.fits" for i in (3, 4, 5)]
    assert c.post("/api/gallery/trash", json={"paths": doomed}).status_code == 200
    # The frames really are still on the same volume, one directory away.
    assert (root / gallery.TRASH_DIRNAME / "flats" / "flat_5.fits").is_file()

    rebuilt = c.post("/api/calibration/build").json()
    assert rebuilt["frames_indexed"] == 3, (
        "a deleted flat was stacked back into a master — _trash is missing from "
        "calibration.library.EXCLUDE_DIRS")
    masters = c.get("/api/calibration/masters").json()
    assert masters[0]["frame_count"] == 3


def test_calibration_exclude_dirs_names_the_trash(cap):
    """Pinned as a constant, not a behaviour, so the reason survives a refactor
    that moves the scanner."""
    from astrodeck.calibration.library import EXCLUDE_DIRS
    assert gallery.TRASH_DIRNAME in EXCLUDE_DIRS


def test_factory_reset_takes_the_trash_with_the_captures(cap, tmp_path):
    """The other sweep over this tree. Deleted frames are still the tester's
    data: a reset that leaves a populated bin has not returned the box to a
    fresh install, and one that empties it without the opt-in has destroyed
    data a settings button had no business touching."""
    from astrodeck import factory_reset
    from astrodeck.config import ConfigStore
    _write(cap, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    gallery.trash_frames(["M42/a.fits"])
    trash = gallery.trash_root()
    assert trash.is_dir()

    store = ConfigStore(path=tmp_path / "cfg.json")
    factory_reset.factory_reset(store, tmp_path / "cfgdir", cap,
                                delete_captures=False)
    assert trash.is_dir(), "a settings reset destroyed the user's deleted frames"

    factory_reset.factory_reset(store, tmp_path / "cfgdir", cap,
                                delete_captures=True)
    assert not trash.exists()


def test_factory_reset_inventory_counts_trashed_frames(cap):
    """The on-screen number must be what actually goes."""
    from astrodeck import factory_reset
    _write(cap, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    gallery.trash_frames(["M42/a.fits"])
    assert factory_reset.capture_inventory(cap)["frames"] == 1


# --------------------------------------------------------------- capabilities

class _FakeAuthProvider:
    """Returns a FIXED principal for every request (test_api_mount.py's idiom)."""

    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def as_role():
    """Run the gallery as a given role, restoring the open default afterwards."""
    from astrodeck.auth import (principal_for_role, reset_active_provider,
                                set_active_provider)

    def _install(role: str):
        set_active_provider(_FakeAuthProvider(principal_for_role(role)))
    yield _install
    reset_active_provider()


def test_a_viewer_browses_thumbnails_but_cannot_download_a_frame(env, as_role):
    """Every frame embeds SITELAT/SITELONG/SITEELEV, so a download hands over the
    observatory's location. That is why the FITS routes are ``view.media`` while
    the grid and its downsized previews are ``view.preview`` — the split the
    capability comments already describe."""
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0), size=64)
    as_role("viewer")
    assert c.get("/api/gallery/frames").status_code == 200
    assert c.get("/api/gallery/nights").status_code == 200
    assert c.get("/api/gallery/thumb", params={"path": "M42/a.fits"}).status_code == 200
    assert c.get("/api/gallery/file", params={"path": "M42/a.fits"}).status_code == 403
    assert c.get("/api/gallery/download.zip").status_code == 403
    assert c.post("/api/gallery/trash", json={"paths": ["M42/a.fits"]}).status_code == 403
    assert c.get("/api/gallery/trash").status_code == 403


def test_an_operator_can_delete_but_still_cannot_download_the_fits(env, as_role):
    """``control.capture`` is already the authority to FILL this volume, so it is
    the authority to free it; ``view.media`` is a separate, location-disclosing
    thing an operator does not hold."""
    c, root = env
    _write(root, "M42/a.fits", ts=_local(2026, 6, 15, 22, 0))
    as_role("operator")
    assert c.post("/api/gallery/trash",
                  json={"paths": ["M42/a.fits"]}).status_code == 200
    assert c.get("/api/gallery/trash").status_code == 200
    assert c.get("/api/gallery/download.zip").status_code == 403
