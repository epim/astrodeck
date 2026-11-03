# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The OpenNGC input behind ngc.tsv and ngc_extras.tsv is one pinned commit (#804).

Both files were built from a hand-downloaded NGC.csv that nobody recorded.
Rebuilt later from a newer download, 6 of 12,013 sidecar rows differed and two of
them disagreed with ngc.tsv about the object's size. The build now reads
the OpenNGC commit named in tools/build_ngc_catalog.py, checks both CSVs against
recorded sha256 values, and refuses any ref that is not a full commit id
(master, HEAD, a branch, a short id). The shipped files carry the pin in their
header and the row counts of that snapshot.

The network is never used here: fetching is exercised through an injected
opener that serves bytes from memory.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import pytest

from tools import build_ngc_catalog as build

DATA = Path(__file__).resolve().parents[1] / "astrodeck" / "catalog" / "data"

#: Row counts of the snapshot at build.OPENNGC_COMMIT, written here as literals
#: so that moving the pin without regenerating the files is a test failure
#: rather than a silent change.
NGC_ROWS = 13369
EXTRAS_ROWS = 12001


def _data_rows(path: Path) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh if ln.strip() and not ln.startswith("#")]


def _header(path: Path) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.startswith("#")]


def test_the_pin_is_a_full_commit_id():
    assert re.fullmatch(r"[0-9a-f]{40}", build.OPENNGC_COMMIT), (
        f"OPENNGC_COMMIT must be a 40-hex commit id, not {build.OPENNGC_COMMIT!r}")


@pytest.mark.parametrize("ref", ["master", "HEAD", "main", "be3cd6c", "", "refs/heads/master"])
def test_a_ref_that_is_not_a_full_commit_id_is_refused(ref):
    with pytest.raises(ValueError, match="full commit"):
        build.require_pinned(ref)


def test_every_fetch_url_names_the_pinned_commit():
    for name in build.SNAPSHOT_SHA256:
        url = build.snapshot_url(name)
        assert build.OPENNGC_COMMIT in url
        assert "/master/" not in url and "/HEAD/" not in url


def _opener(payloads: dict[str, bytes], asked: list[str]):
    def opener(url: str):
        asked.append(url)
        for name, body in payloads.items():
            if url.endswith("/" + name):
                return io.BytesIO(body)
        raise AssertionError(f"unexpected url {url}")
    return opener


def test_fetch_accepts_only_bytes_matching_the_recorded_checksums(tmp_path, monkeypatch):
    good = {"NGC.csv": b"Name;Type\n", "addendum.csv": b"Name;Type\nX;G\n"}
    monkeypatch.setattr(build, "SNAPSHOT_SHA256",
                        {n: hashlib.sha256(b).hexdigest() for n, b in good.items()})
    asked: list[str] = []
    paths = build.fetch_snapshot(tmp_path, opener=_opener(good, asked))
    assert [p.name for p in paths] == ["NGC.csv", "addendum.csv"]
    assert paths[0].read_bytes() == good["NGC.csv"]
    assert all(build.OPENNGC_COMMIT in u for u in asked)

    drifted = dict(good, **{"NGC.csv": b"Name;Type\nchanged upstream\n"})
    with pytest.raises(ValueError, match="NGC.csv"):
        build.fetch_snapshot(tmp_path / "again", opener=_opener(drifted, []))
    assert not (tmp_path / "again" / "NGC.csv").exists()


def test_ngc_tsv_header_records_the_pin():
    header = _header(DATA / "ngc.tsv")
    assert f"# OpenNGC commit: {build.OPENNGC_COMMIT}" in header
    for name, digest in build.SNAPSHOT_SHA256.items():
        assert f"# {name} sha256: {digest}" in header


def test_ngc_tsv_and_its_sidecar_have_the_snapshot_row_counts():
    assert len(_data_rows(DATA / "ngc.tsv")) == NGC_ROWS
    assert len(_data_rows(DATA / "ngc_extras.tsv")) == EXTRAS_ROWS


def _by_id(path: Path) -> dict[str, list[str]]:
    rows = [ln.splitlines()[0].split(chr(9)) for ln in _data_rows(path)]
    return {row[0]: row for row in rows}


def test_the_two_rows_that_disagreed_on_size_now_agree():
    """IC 2105 and IC 3231 had a MajAx in the sidecar that differed from the
    size in ngc.tsv, so the sidecar withheld their shape and the Atlas drew
    circles. From one snapshot both files carry the same MajAx and the shape is
    there: the ratio is MinAx over the size ngc.tsv shows."""
    ngc = _by_id(DATA / "ngc.tsv")
    extras = _by_id(DATA / "ngc_extras.tsv")
    for ident, size, minax, ratio in (("IC 2105", 3.0, 1.5, "0.500"),
                                      ("IC 3231", 0.69, 0.63, "0.913")):
        assert float(ngc[ident][6]) == size
        row = extras[ident]
        assert float(row[2]) == minax
        assert row[5] == ratio and row[6] != "", (ident, row)
