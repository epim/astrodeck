"""Reconciliation core tests.

These drive the REAL :func:`diff` against REAL manifests. There is deliberately
no transport double here: a fake transport that reimplemented the comparison
would agree with itself no matter what the shipping code did, which is exactly
how a sabotage came back MISSED on this project once before. The transports get
their own tests; the rules live here and are tested directly.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from astrodeck.sync import manifest as M


def _facts(relpath: str, size: int, mtime_ns: int, **kw) -> M.FileFacts:
    return M.FileFacts(relpath=relpath, size=size, mtime_ns=mtime_ns, **kw)


def _write(root: Path, rel: str, data: bytes, *, age_s: float = 3600.0) -> M.FileFacts:
    """Write a file and backdate it so it counts as settled by default."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    when = time.time() - age_s
    import os
    os.utime(p, (when, when))
    st = p.stat()
    return _facts(rel, st.st_size, st.st_mtime_ns)


# --------------------------------------------------------------- finality

def test_a_file_still_being_written_is_not_offered(tmp_path):
    """Hazard 1: save_fits streams into the FINAL filename, so a fresh file may
    still be growing. It must be reported as unsettled, never hashed, and never
    offered for transfer."""
    fresh = _write(tmp_path, "now.fits", b"partial", age_s=0.0)
    man = M.build([fresh], root=tmp_path, now=time.time())
    assert man.entries == {}
    assert man.unsettled == ["now.fits"]


def test_a_settled_file_is_offered(tmp_path):
    old = _write(tmp_path, "old.fits", b"complete", age_s=600.0)
    man = M.build([old], root=tmp_path, now=time.time())
    assert list(man.entries) == ["old.fits"]
    assert man.unsettled == []


def test_a_file_from_the_future_is_not_final():
    """Clock skew must make sync WAIT, not charge ahead: an mtime in the future
    would read as infinitely old under a naive subtraction."""
    future = int((time.time() + 300) * 1e9)
    assert M.is_final(future, time.time()) is False


# ------------------------------------------------------------------ diff

def test_missing_file_is_a_transfer():
    src = M.Manifest(entries={"a": M.Entry("a", 10, "hash-a")})
    dst = M.Manifest()
    d = M.diff(src, dst)
    assert [e.relpath for e in d.missing] == ["a"]
    assert d.identical == 0
    assert len(d.transfers) == 1


def test_identical_hash_is_not_retransferred():
    e = M.Entry("a", 10, "hash-a")
    d = M.diff(M.Manifest(entries={"a": e}), M.Manifest(entries={"a": e}))
    assert d.transfers == []
    assert d.identical == 1


def test_same_size_different_bytes_is_stale():
    """THE torn-copy case, and the reason comparison is on hash alone. A
    transfer that died at exactly the right moment leaves a destination file of
    the CORRECT LENGTH holding the wrong bytes. Size- or existence-based
    comparison calls that done forever."""
    src = M.Manifest(entries={"a": M.Entry("a", 50_000_000, "real")})
    dst = M.Manifest(entries={"a": M.Entry("a", 50_000_000, "torn")})
    d = M.diff(src, dst)
    assert [e.relpath for e in d.stale] == ["a"]
    assert d.transfers[0].sha256 == "real"


def test_mtime_never_decides():
    """Every transport rewrites mtime and the two clocks are unrelated, so a
    wildly different mtime with a matching hash must NOT trigger a re-send."""
    src = M.Manifest(entries={"a": M.Entry("a", 10, "same", mtime_ns=1)})
    dst = M.Manifest(entries={"a": M.Entry("a", 10, "same", mtime_ns=999_999)})
    assert M.diff(src, dst).transfers == []


def test_extra_destination_files_are_reported_and_never_deleted():
    """The destination is where processed masters and crops live beside the raw
    data. Sync reports what it does not recognise; it must never propose
    removing it."""
    src = M.Manifest(entries={"a": M.Entry("a", 1, "h")})
    dst = M.Manifest(entries={"a": M.Entry("a", 1, "h"),
                              "my_master.xisf": M.Entry("my_master.xisf", 9, "z")})
    d = M.diff(src, dst)
    assert d.extra == ["my_master.xisf"]
    assert d.transfers == []
    assert not hasattr(d, "delete")


def test_transfers_are_oldest_first():
    """A stacker wants a contiguous run, so early frames must complete first."""
    src = M.Manifest(entries={
        "c": M.Entry("c", 1, "hc", mtime_ns=300),
        "a": M.Entry("a", 1, "ha", mtime_ns=100),
        "b": M.Entry("b", 1, "hb", mtime_ns=200)})
    d = M.diff(src, M.Manifest())
    assert [e.relpath for e in d.transfers] == ["a", "b", "c"]


# ------------------------------------------------------- state is derived

def test_reconciliation_holds_no_memory(tmp_path):
    """The design claim, tested directly: throw away EVERYTHING the sync knew
    and rebuild from the two sides. If a destination already holds the bytes,
    the rebuilt diff must be empty — nothing re-sends merely because the
    bookkeeping was lost. This is what makes a crash mid-night free."""
    src_root = tmp_path / "rig"
    dst_root = tmp_path / "pix"
    f = _write(src_root, "n/one.fits", b"x" * 4096)
    dst_root.mkdir()
    (dst_root / "n").mkdir()
    (dst_root / "n" / "one.fits").write_bytes(b"x" * 4096)

    now = time.time()
    src = M.build([f], root=src_root, now=now)
    dst = M.build(M.walk_facts(dst_root), root=dst_root, now=now, settle_s=0.0)
    assert M.diff(src, dst).transfers == []


def test_a_corrupted_destination_file_comes_back_forever(tmp_path):
    """Self-healing: no flag can mark this done, because there is no flag."""
    src_root = tmp_path / "rig"
    dst_root = tmp_path / "pix"
    f = _write(src_root, "one.fits", b"good" * 100)
    dst_root.mkdir()
    (dst_root / "one.fits").write_bytes(b"bad!" * 100)   # same length, wrong bytes

    now = time.time()
    src = M.build([f], root=src_root, now=now)
    dst = M.build(M.walk_facts(dst_root), root=dst_root, now=now, settle_s=0.0)
    d = M.diff(src, dst)
    assert [e.relpath for e in d.stale] == ["one.fits"]


# ------------------------------------------------------------ hash cache

def test_hash_cache_avoids_rereading(tmp_path):
    p = tmp_path / "f.fits"
    p.write_bytes(b"abc")
    cache: dict = {}
    first = M.hash_file(p, cache=cache)
    p.unlink()                      # gone: only the cache can answer now
    st_size, st_mtime = 3, list(cache)[0][2]
    assert M.hash_file(p, cache=cache, size=st_size, mtime_ns=st_mtime) == first


def test_header_rewrite_invalidates_the_cache(tmp_path):
    """Hazard 2: a WCS stamp rewrites the header IN PLACE. On a 2880-byte block
    boundary the size can be unchanged, so a size-only cache key would serve the
    pre-stamp hash forever and the destination would keep a frame with no
    astrometry while sync insisted everything matched."""
    import os
    p = tmp_path / "f.fits"
    p.write_bytes(b"A" * 2880)
    cache: dict = {}
    before = M.hash_file(p, cache=cache)
    p.write_bytes(b"B" * 2880)                       # same size, new content
    os.utime(p, (time.time() + 5, time.time() + 5))  # and a new mtime
    after = M.hash_file(p, cache=cache)
    assert after != before


# ---------------------------------------------------------- serialisation

def test_manifest_round_trips():
    man = M.Manifest(entries={"a": M.Entry("a", 5, "h", night="2026-08-09")},
                     unsettled=["b"])
    back = M.Manifest.from_json(man.to_json())
    assert back.entries["a"].sha256 == "h"
    assert back.entries["a"].night == "2026-08-09"
    assert back.unsettled == ["b"]


def test_a_manifest_in_another_algorithm_is_refused():
    """Comparing sha256 against something else would mark every file stale and
    re-send the whole night. Refuse loudly instead."""
    with pytest.raises(ValueError, match="speaks"):
        M.Manifest.from_json({"algo": "md5", "entries": []})


def test_vanished_file_is_dropped_not_fatal(tmp_path):
    """A night changes while it is being described (trash, purge, rotation). One
    missing frame must not fail the whole poll."""
    good = _write(tmp_path, "good.fits", b"ok")
    ghost = _facts("ghost.fits", 10, int((time.time() - 600) * 1e9))
    man = M.build([good, ghost], root=tmp_path, now=time.time())
    assert list(man.entries) == ["good.fits"]
