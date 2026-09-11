"""SER v3 writer, graded against the published spec rather than against itself.

THE PARSER IN THIS FILE IS DELIBERATELY HAND-WRITTEN. Reading the file back with
``ser.read_header`` would grade the writer against its own idea of the layout:
swap two fields consistently in both and every assertion still passes while no
other program on earth can open the file. So ``_parse`` below unpacks literal
byte offsets typed out from the spec - "SER format description version 3", Heiko
Wilkens / Grischa Hahn, 2014-02-06, read out of the published PDF at
grischa-hahn.hier-im-netz.de/astro/ser/SER%20Doc%20V3b.pdf - and nothing in it
imports a constant from the module under test.
"""
from __future__ import annotations

import struct
from datetime import datetime, timezone

import numpy as np
import pytest

from astrodeck.imaging import ser
from astrodeck.imaging.ser import (SerWriter, color_id_for, read_frames,
                                   read_header)

# Offsets typed from the spec, not imported. See the module docstring.
_OFF = {
    "file_id": (0, 14), "lu_id": (14, 4), "color_id": (18, 4),
    "little_endian": (22, 4), "width": (26, 4), "height": (30, 4),
    "depth": (34, 4), "frames": (38, 4), "observer": (42, 40),
    "instrument": (82, 40), "telescope": (122, 40), "datetime": (162, 8),
    "datetime_utc": (170, 8),
}


def _parse(path) -> dict:
    """An independent SER reader: literal offsets, little-endian, 178-byte head."""
    raw = path.read_bytes()
    assert len(raw) >= 178, "a SER file is at least its 178-byte header"

    def i32(name: str) -> int:
        off, _ = _OFF[name]
        return struct.unpack_from("<i", raw, off)[0]

    def i64(name: str) -> int:
        off, _ = _OFF[name]
        return struct.unpack_from("<q", raw, off)[0]

    def text(name: str) -> bytes:
        off, size = _OFF[name]
        return raw[off:off + size]

    head = {
        "file_id": text("file_id"), "lu_id": i32("lu_id"),
        "color_id": i32("color_id"), "little_endian": i32("little_endian"),
        "width": i32("width"), "height": i32("height"), "depth": i32("depth"),
        "frames": i32("frames"), "observer": text("observer"),
        "instrument": text("instrument"), "telescope": text("telescope"),
        "datetime": i64("datetime"), "datetime_utc": i64("datetime_utc"),
    }
    bpp = 1 if head["depth"] <= 8 else 2
    stride = head["width"] * head["height"] * bpp
    body_end = 178 + head["frames"] * stride
    head["stride"] = stride
    head["pixels"] = [
        np.frombuffer(raw[178 + i * stride:178 + (i + 1) * stride],
                      dtype="<u1" if bpp == 1 else "<u2"
                      ).reshape((head["height"], head["width"]))
        for i in range(head["frames"])
    ]
    head["trailer"] = list(struct.unpack_from(
        f"<{head['frames']}q", raw, body_end)) if head["frames"] else []
    head["total_bytes"] = len(raw)
    return head


def _ticks(dt: datetime) -> int:
    """100 ns since 0001-01-01, computed here rather than imported."""
    delta = dt.replace(tzinfo=None) - datetime(1, 1, 1)
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10


def _frame(w: int, h: int, seed: int, dtype="<u2") -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 4000, size=(h, w)).astype(dtype)


# --------------------------------------------------------------- the 5 frames

W, H, N = 12, 8, 5
_START = 1_757_500_000.0          # a fixed instant, so the dates are checkable


@pytest.fixture
def five_frames(tmp_path):
    """Five 16-bit mono frames at known timestamps, written and closed."""
    frames = [_frame(W, H, seed=i) for i in range(N)]
    path = tmp_path / "rec.ser"
    with SerWriter(path, width=W, height=H, bit_depth=16, color_id=0,
                   instrument="ASI678MC", telescope="RC8",
                   start_unix=_START) as writer:
        for i, f in enumerate(frames):
            writer.add_frame(f, ts=_START + 0.25 * i)
    return path, frames


def test_every_header_field_lands_at_its_spec_offset(five_frames):
    path, frames = five_frames
    head = _parse(path)

    assert head["file_id"] == b"LUCAM-RECORDER"
    assert head["lu_id"] == 0
    assert head["color_id"] == 0, "mono sensor -> ColorID MONO"
    assert head["little_endian"] == 1, (
        "the spec's 4_LittleEndian is 1/TRUE for little-endian 16-bit data, "
        "which is what this writer emits")
    assert head["width"] == W
    assert head["height"] == H
    assert head["depth"] == 16
    # 40-byte fixed fields, NUL filled per the spec ("fill unused characters
    # with 0 dec."), so the name is a prefix and the rest is zeros.
    assert head["observer"].startswith(b"AstroDeck")
    assert head["observer"][9:] == b"\x00" * 31
    assert head["instrument"].startswith(b"ASI678MC")
    assert head["telescope"].startswith(b"RC8")
    assert len(head["observer"]) == len(head["instrument"]) == 40

    # The two dates: the same instant, one on the local civil clock and one UTC,
    # both as 100 ns ticks since 0001-01-01.
    assert head["datetime_utc"] == _ticks(
        datetime.fromtimestamp(_START, tz=timezone.utc))
    assert head["datetime"] == _ticks(datetime.fromtimestamp(_START))

    # ...and the pixels really are the frames, at the stride the header claims.
    assert len(head["pixels"]) == N
    for got, want in zip(head["pixels"], frames):
        assert np.array_equal(got, want)


def test_the_header_frame_count_matches_the_frames_written(five_frames):
    """SABOTAGE TARGET: drop the FrameCount patch in SerWriter.close.

    FrameCount is written as 0 when the file is opened, because the count is not
    known until the recording ends. Every reader uses it to find where the image
    block stops and the timestamp trailer starts, so a file that never gets the
    patch is a file whose frames are all there and none of them reachable."""
    path, _ = five_frames
    assert _parse(path)["frames"] == N


def test_the_trailer_is_one_utc_timestamp_per_frame(five_frames):
    path, _ = five_frames
    head = _parse(path)
    assert len(head["trailer"]) == N
    assert head["trailer"] == sorted(head["trailer"]), (
        "the trailer is in capture order, so it must be non-decreasing")
    assert all(b > a for a, b in zip(head["trailer"], head["trailer"][1:])), (
        "frames 0.25 s apart must have strictly increasing timestamps")
    for i, tick in enumerate(head["trailer"]):
        assert tick == _ticks(
            datetime.fromtimestamp(_START + 0.25 * i, tz=timezone.utc)), (
            f"trailer entry {i} is not the UTC tick count for that frame")


def test_the_file_is_exactly_header_frames_trailer(five_frames):
    path, _ = five_frames
    assert _parse(path)["total_bytes"] == 178 + N * W * H * 2 + N * 8


# --------------------------------------------------------------------- colour

def test_an_osc_eight_bit_frame_is_written_rggb(tmp_path):
    """An 8-bit one-shot-colour frame: ColorID 8, one byte per pixel."""
    path = tmp_path / "osc8.ser"
    frame = _frame(8, 4, seed=9, dtype="<u1")
    with SerWriter(path, width=8, height=4, bit_depth=8,
                   color_id=color_id_for("RGGB", 1), instrument="ASI585MC",
                   start_unix=_START) as writer:
        writer.add_frame(frame, ts=_START)
    head = _parse(path)
    assert head["color_id"] == 8, "BAYER_RGGB is 8 in the spec's enumeration"
    assert head["depth"] == 8
    assert head["stride"] == 8 * 4 * 1, "1..8 bit data is one byte per pixel"
    assert np.array_equal(head["pixels"][0], frame)
    assert head["total_bytes"] == 178 + 8 * 4 + 8


def test_a_binned_osc_frame_is_written_mono(tmp_path):
    """SABOTAGE TARGET: use normalise_bayer instead of effective_bayer.

    Binning sums a 2x2 block on the sensor, so the mosaic is already gone from
    the pixels that arrive - but the camera still reports RGGB, because the
    pattern describes the sensor and not the readout. Stamp BAYER_RGGB onto a
    bin-2 recording and every stacker downstream debayers a frame with no mosaic
    in it, turning real surface detail into a colour lattice."""
    assert color_id_for("RGGB", 1) == 8, "precondition: unbinned OSC is RGGB"
    assert color_id_for("RGGB", 2) == 0
    assert color_id_for("RG", 2) == 0, (
        "the native bindings report the top-left PAIR, and a binned frame off "
        "one of those is just as mono")

    path = tmp_path / "osc_bin2.ser"
    with SerWriter(path, width=6, height=4, bit_depth=16,
                   color_id=color_id_for("RGGB", 2), start_unix=_START) as w:
        w.add_frame(_frame(6, 4, seed=3), ts=_START)
    assert _parse(path)["color_id"] == 0


# -------------------------------------------------------------------- aborted

def test_an_aborted_recording_is_a_valid_file_at_the_frames_it_got(tmp_path):
    """The cancel case, which is the COMMON case for a planetary recording.

    An operator stops when the seeing collapses, a cloud hold stops it, the
    camera errors. What must not happen is that the frames already on disk
    become unreadable because the header still describes the recording we
    planned."""
    path = tmp_path / "aborted.ser"
    frames = [_frame(W, H, seed=100 + i) for i in range(3)]
    with pytest.raises(RuntimeError, match="cloud"):
        with SerWriter(path, width=W, height=H, bit_depth=16,
                       start_unix=_START) as writer:
            for i, f in enumerate(frames):
                writer.add_frame(f, ts=_START + i)
            raise RuntimeError("cloud hold at frame 3 of 5")

    head = _parse(path)
    assert head["frames"] == 3, "the header counts what landed, not what was planned"
    assert head["total_bytes"] == 178 + 3 * W * H * 2 + 3 * 8
    assert len(head["trailer"]) == 3
    for got, want in zip(head["pixels"], frames):
        assert np.array_equal(got, want)


def test_a_wrong_sized_frame_is_refused_rather_than_written(tmp_path):
    """One short frame does not lose one frame: it shifts every frame after it
    and the trailer with them, and nothing downstream can tell."""
    path = tmp_path / "short.ser"
    with SerWriter(path, width=W, height=H, bit_depth=16,
                   start_unix=_START) as writer:
        writer.add_frame(_frame(W, H, seed=1), ts=_START)
        with pytest.raises(ValueError, match="shift every later frame"):
            writer.add_frame(_frame(W - 2, H, seed=2), ts=_START)
    assert _parse(path)["frames"] == 1


# ================================ the count survives a process that never closed
#
# FrameCount is written as 0 by ``open`` and patched by ``close``, which covers
# a cancel, a cloud hold and a camera error - all three unwind through
# ``__exit__``. It does not cover the process not getting to run any of that: a
# power cut, a kill, the rig PC's thermal reset on 2026-09-08. The header then
# still says 0, and every reader in the world - SER Player, PIPP, AutoStakkert,
# Siril, and this module's own ``read_frames`` - reads that as an empty file. So
# the frames that DID land are unreadable at the exact moment they are the only
# ones there are.
#
# Two answers, and both are needed. The writer CHECKPOINTS the count every
# ``FRAMECOUNT_PATCH_EVERY`` frames, so a killed file is already playable back
# to the last checkpoint; and ``repair_frame_count`` recovers the rest from the
# file's own length, which is the second witness a header cannot corrupt.

def _kill(writer) -> None:
    """Drop a live writer the way a power cut does: the bytes that were flushed
    are on disk, ``close`` never runs, and no trailer is written."""
    writer._fh.flush()
    writer._fh.close()
    writer._fh = None
    writer._closed = True


def test_the_count_is_checkpointed_while_the_recording_runs(tmp_path):
    """SABOTAGE (run red, restored): drop the ``_patch_frame_count`` call from
    ``add_frame``. A killed recording reads as an empty file."""
    path = tmp_path / "killed.ser"
    w = SerWriter(path, width=8, height=4, bit_depth=16).open()
    frame = np.arange(32, dtype="<u2").reshape((4, 8))
    for _ in range(ser.FRAMECOUNT_PATCH_EVERY + 5):
        w.add_frame(frame)
    _kill(w)

    head = read_header(path)
    assert head["frames"] == ser.FRAMECOUNT_PATCH_EVERY, (
        "the header was never checkpointed, so a killed recording is empty")
    # ...and those frames really read back, which is the whole point.
    assert len(list(read_frames(path))) == ser.FRAMECOUNT_PATCH_EVERY


def test_the_checkpoint_does_not_corrupt_the_frames_it_interrupts(tmp_path):
    """The patch seeks BACKWARDS into a file that is still being appended to.
    A seek that did not return to the end would overwrite the header with
    pixels, or interleave frames, and the file would still look plausible."""
    path = tmp_path / "checkpointed.ser"
    n = ser.FRAMECOUNT_PATCH_EVERY * 2 + 3
    with SerWriter(path, width=8, height=4, bit_depth=16) as w:
        for i in range(n):
            w.add_frame(np.full((4, 8), i, dtype="<u2"))
    parsed = _parse(path)
    assert parsed["frames"] == n
    assert len(parsed["pixels"]) == n
    for i, frame in enumerate(parsed["pixels"]):
        assert int(frame[0][0]) == i, f"frame {i} is not the one written"


def test_the_count_is_recovered_from_the_file_size(tmp_path):
    """SABOTAGE (run red, restored): make ``repair_frame_count`` a no-op. The
    tail of a killed recording - everything since the last checkpoint - stays
    unreadable, and on a short file that is the whole recording."""
    path = tmp_path / "torn.ser"
    w = SerWriter(path, width=8, height=4, bit_depth=16).open()
    for i in range(7):                  # fewer than one checkpoint interval
        w.add_frame(np.full((4, 8), i, dtype="<u2"))
    _kill(w)
    assert read_header(path)["frames"] == 0, "precondition: the header says 0"

    assert ser.repair_frame_count(path) == 7
    assert read_header(path)["frames"] == 7
    assert [int(f[0][0]) for f in read_frames(path)] == list(range(7))


def test_the_repair_is_a_no_op_on_a_file_that_closed_properly(tmp_path):
    """It runs on every file the library lists, so it has to be safe on the
    ordinary case - including not counting the TRAILER as extra frames, which
    on a small planetary ROI is a dozen frames' worth of bytes."""
    path = tmp_path / "clean.ser"
    with SerWriter(path, width=8, height=4, bit_depth=16) as w:
        for i in range(5):
            w.add_frame(np.full((4, 8), i, dtype="<u2"))
    before = path.read_bytes()
    assert ser.repair_frame_count(path) == 5
    assert path.read_bytes() == before, "the repair rewrote a healthy file"


def test_a_frame_torn_in_half_is_not_counted(tmp_path):
    """A partial frame is not a frame: counting it would shift every reader's
    idea of where the next one starts."""
    path = tmp_path / "half.ser"
    w = SerWriter(path, width=8, height=4, bit_depth=16).open()
    for i in range(3):
        w.add_frame(np.full((4, 8), i, dtype="<u2"))
    _kill(w)
    with open(path, "r+b") as fh:       # lose half of the last frame
        fh.truncate(path.stat().st_size - 32)
    assert ser.repair_frame_count(path) == 2


def test_the_library_lists_a_killed_recording_with_its_real_count(tmp_path,
                                                                  monkeypatch):
    """The repair where a user meets it: a recording whose process died shows
    the frames it got instead of 0, and is stackable."""
    from astrodeck.imaging import video as video_mod

    monkeypatch.setattr(video_mod, "video_dir", lambda: tmp_path)
    path = tmp_path / "night-01.ser"
    w = SerWriter(path, width=8, height=4, bit_depth=16).open()
    for i in range(9):
        w.add_frame(np.full((4, 8), i, dtype="<u2"))
    _kill(w)

    rows = video_mod.list_recordings()
    assert len(rows) == 1
    assert rows[0]["frames"] == 9, (
        "the library called a killed recording empty")
