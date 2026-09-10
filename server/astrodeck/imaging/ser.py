"""SER v3 writer: the raw-frame container a planetary/lunar recording lands in.

WHY SER AND NOT FITS-PER-FRAME. A 60 s run at 30 fps is 1800 frames. Written as
1800 FITS files that is 1800 directory entries, 1800 headers and 1800 opens for
anything that later wants to stack them; written as SER it is one file whose
frames are back to back at a known stride, which is what every planetary stacker
(AutoStakkert, PIPP, Registax, SER Player, Siril) already reads.

THE SPEC THIS IMPLEMENTS, AND WHERE IT WAS CHECKED. "SER format description
version 3", Heiko Wilkens (v2) + Grischa Hahn (v3 extensions, 2014-02-06), read
directly out of the published PDF at
grischa-hahn.hier-im-netz.de/astro/ser/SER%20Doc%20V3b.pdf - not from memory of
a reader implementation. Its field table is reproduced offset by offset below
and every number in it is asserted in tests/test_ser_writer.py against a parser
written independently of this module.

  off  len  field                 what we write
    0   14  FileID                b"LUCAM-RECORDER"
   14    4  LuID                  0 (Lumenera camera series id, "currently
                                  unused; default = 0" per the spec)
   18    4  ColorID               MONO/BAYER_* - see COLOR_* below
   22    4  LittleEndian          1 - see the endianness note
   26    4  ImageWidth            BINNED pixels per row
   30    4  ImageHeight           BINNED rows
   34    4  PixelDepthPerPlane    8 or 16
   38    4  FrameCount            patched on close
   42   40  Observer              "AstroDeck"
   82   40  Instrument            the camera's name, truncated
  122   40  Telescope             effective_optics()["telescope_name"] or blank
  162    8  DateTime              start, LOCAL civil, 100 ns ticks from 0001-01-01
  170    8  DateTime_UTC          the same instant in UTC
  178   ..  image data            FrameCount x H x W x BytesPerPixel
   ..    8  trailer, per frame    UTC ticks, one int64 each

Everything past FileID is little-endian, including the two int64 dates.

THE ENDIANNESS FLAG, AND THE CONFUSION THE SPEC ITSELF CAUSED. The spec is not
ambiguous: 4_LittleEndian is "0 FALSE for big-endian byte order in 16 bit image
data, 1 TRUE for little-endian byte order in 16 bit image data". What happened
is that several of the first programs to support SER wrote it INVERTED, and
enough files exist that later readers (Siril among them) adopted the reversed
reading for compatibility - documented on free-astro's SER page. So a 1 here is
spec-correct and is what SharpCap/FireCapture-era readers expect from a modern
writer; a reader that guesses from the flag alone can still get it backwards,
which is why the pixel data is ALSO plain little-endian uint16, the only layout
this rig's adapters ever download (devices/cameras/engine.py:_shape).

One more oddity worth carrying: the spec notes Raoul Behrend's finding that the
date record is really a 62-bit unsigned integer rather than the 64-bit one it
claims, with no information about the two most significant bits. We write dates
that fit in 62 bits by construction (any year <= 9999 does), so this costs us
nothing - it is recorded so the next person does not rediscover it.

PADDING: the spec says the three 40-byte strings are "40 ASCII characters
{32..126 dec.}, fill unused characters with 0 dec." - NUL fill, not spaces. That
is what this writer does. (The S7d brief said space-padded; the published spec
won, and the difference is called out in the task report. Readers trim both.)
"""
from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

#: Fixed header size (spec: "Header with fixed size of 178 Byte").
SER_HEADER_BYTES = 178
#: 14 ASCII characters, fixed.
SER_FILE_ID = b"LUCAM-RECORDER"

# --- 3_ColorID, verbatim from the spec's enumeration ------------------------
COLOR_MONO = 0
COLOR_BAYER_RGGB = 8
COLOR_BAYER_GRBG = 9
COLOR_BAYER_GBRG = 10
COLOR_BAYER_BGGR = 11

#: The four patterns this rig can produce (imaging/sessionstack._BAYER_4), in
#: the spec's numbering. CYYM/YCMY/YMCY/MYYC (16-19) and RGB/BGR (100/101) exist
#: in the spec and are deliberately absent: no adapter here downloads them, and
#: a number we cannot produce is a number we cannot test.
BAYER_COLOR_IDS: dict[str, int] = {
    "RGGB": COLOR_BAYER_RGGB,
    "GRBG": COLOR_BAYER_GRBG,
    "GBRG": COLOR_BAYER_GBRG,
    "BGGR": COLOR_BAYER_BGGR,
}

#: The .NET/Microsoft tick epoch the spec cites: 100 ns increments since
#: 0001-01-01 00:00:00.
_TICK_EPOCH = datetime(1, 1, 1)
_TICKS_PER_SECOND = 10_000_000


def ticks_from_datetime(dt: datetime) -> int:
    """A naive-or-aware datetime as SER ticks (100 ns since 0001-01-01).

    An aware datetime is used AS ITS OWN WALL CLOCK, not converted: the header
    carries local civil time in one field and UTC in the other, and both are
    stored as bare tick counts with no zone. Converting here would silently make
    the two fields the same instant twice.
    """
    naive = dt.replace(tzinfo=None)
    delta = naive - _TICK_EPOCH
    return ((delta.days * 86400 + delta.seconds) * _TICKS_PER_SECOND
            + delta.microseconds * 10)


def utc_ticks(unix_ts: float) -> int:
    """A unix timestamp as SER ticks in UTC."""
    return ticks_from_datetime(datetime.fromtimestamp(unix_ts, tz=timezone.utc))


def local_ticks(unix_ts: float) -> int:
    """A unix timestamp as SER ticks on the rig's LOCAL civil clock."""
    return ticks_from_datetime(datetime.fromtimestamp(unix_ts))


def color_id_for(pattern: str | None, binning: int | None = 1) -> int:
    """The ColorID for a frame off a sensor with ``pattern``, binned ``binning``.

    Goes through BOTH ``normalise_bayer`` (so the two-letter form the native ZWO
    and Player One bindings report is accepted alongside the four-letter FITS
    card) AND ``effective_bayer`` (so a BINNED frame is written MONO).

    The binning half is the one that matters and the one that is easy to skip.
    Binning sums a 2x2 block on the sensor, so the mosaic is gone from the
    pixels that arrive - but the camera still reports RGGB, because the pattern
    describes the sensor and not the readout. Stamp RGGB onto a bin-2 recording
    and every stacker downstream debayers a frame that has no mosaic left,
    turning real detail into a colour lattice. Same reasoning, same helper, as
    the live frame loop: see imaging/sessionstack.effective_bayer.
    """
    from .sessionstack import effective_bayer
    return BAYER_COLOR_IDS.get(effective_bayer(pattern, binning) or "", COLOR_MONO)


def bytes_per_pixel(bit_depth: int) -> int:
    """Spec table 7_PixelDepthPerPlane: 1..8 bits -> 1 byte, 9..16 -> 2 bytes.
    (Mono only here; a 3-plane RGB SER would multiply this by 3.)"""
    return 1 if int(bit_depth) <= 8 else 2


def _fixed(text: str, length: int) -> bytes:
    """``text`` as exactly ``length`` bytes of printable ASCII, NUL filled.

    The spec restricts these fields to 32..126 decimal, so anything outside that
    (an accented telescope name, a stray newline) is replaced rather than
    written - a control byte in a fixed field is how a reader loses the rest of
    the header."""
    raw = (text or "").encode("ascii", errors="replace")
    clean = bytes(b if 32 <= b <= 126 else 0x3F for b in raw)[:length]
    return clean + b"\x00" * (length - len(clean))


def build_header(*, width: int, height: int, bit_depth: int, color_id: int,
                 frame_count: int, observer: str, instrument: str,
                 telescope: str, start_unix: float) -> bytes:
    """The 178 bytes, laid out at the offsets in this module's docstring."""
    return b"".join((
        SER_FILE_ID,                                   # 0   14
        struct.pack("<i", 0),                          # 14   4  LuID
        struct.pack("<i", int(color_id)),              # 18   4  ColorID
        struct.pack("<i", 1),                          # 22   4  LittleEndian
        struct.pack("<i", int(width)),                 # 26   4  ImageWidth
        struct.pack("<i", int(height)),                # 30   4  ImageHeight
        struct.pack("<i", int(bit_depth)),             # 34   4  PixelDepth
        struct.pack("<i", int(frame_count)),           # 38   4  FrameCount
        _fixed(observer, 40),                          # 42  40
        _fixed(instrument, 40),                        # 82  40
        _fixed(telescope, 40),                         # 122 40
        struct.pack("<q", local_ticks(start_unix)),    # 162  8  DateTime
        struct.pack("<q", utc_ticks(start_unix)),      # 170  8  DateTime_UTC
    ))


class SerWriter:
    """Streams frames into one .ser and leaves a PLAYABLE file however it ends.

    Used as a context manager. Frames go to disk as they arrive - a 1800-frame
    recording is never held in RAM - and ``close`` writes the timestamp trailer
    and patches FrameCount at offset 38 to the number of frames that ACTUALLY
    landed.

    That patch is the whole reason this is a class and not a function. A
    recording is cancelled by an operator, aborted by a cloud hold, or cut by a
    camera error far more often than it runs to its full duration, and a header
    still claiming the frame count we HOPED for describes a file whose image
    block ends early and whose trailer starts somewhere the reader will not look
    - so the frames we did get become unreadable at the exact moment they are
    the only ones we have. ``__exit__`` finalises on the exception path too.
    """

    def __init__(self, path: str | Path, *, width: int, height: int,
                 bit_depth: int = 16, color_id: int = COLOR_MONO,
                 observer: str = "AstroDeck", instrument: str = "",
                 telescope: str = "", start_unix: float | None = None) -> None:
        import time as _time
        self.path = Path(path)
        self.width = int(width)
        self.height = int(height)
        self.bit_depth = int(bit_depth)
        self.color_id = int(color_id)
        self.observer = observer
        self.instrument = instrument
        self.telescope = telescope
        self.start_unix = float(start_unix if start_unix is not None else _time.time())
        self.bytes_per_pixel = bytes_per_pixel(self.bit_depth)
        self.frame_bytes = self.width * self.height * self.bytes_per_pixel
        self.frames = 0
        self._timestamps: list[int] = []
        self._fh = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> "SerWriter":
        if self._fh is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "wb")
        self._fh.write(build_header(
            width=self.width, height=self.height, bit_depth=self.bit_depth,
            color_id=self.color_id, frame_count=0, observer=self.observer,
            instrument=self.instrument, telescope=self.telescope,
            start_unix=self.start_unix))
        return self

    def __enter__(self) -> "SerWriter":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Finalise on EVERY path, including a cancel: see the class docstring.
        self.close()
        return False

    # -- writing -----------------------------------------------------------
    def add_frame(self, frame, ts: float | None = None) -> int:
        """Append one frame. ``frame`` is a 2-D ndarray or already-packed bytes.

        Returns the new frame count. Refuses a frame of the wrong size rather
        than writing it: every frame in a SER is at a fixed stride, so one short
        frame does not lose one frame, it shifts every frame after it and the
        trailer with them."""
        import time as _time
        if self._fh is None:
            self.open()
        if self._closed:
            raise ValueError("SER file already closed")
        payload = self._pack(frame)
        self._fh.write(payload)
        self._timestamps.append(utc_ticks(ts if ts is not None else _time.time()))
        self.frames += 1
        return self.frames

    def _pack(self, frame) -> bytes:
        if isinstance(frame, (bytes, bytearray, memoryview)):
            raw = bytes(frame)
            if len(raw) != self.frame_bytes:
                raise ValueError(
                    f"SER frame is {len(raw)} bytes but this file's frames are "
                    f"{self.frame_bytes} ({self.width}x{self.height} at "
                    f"{self.bytes_per_pixel} byte(s)/px); writing it would shift "
                    "every later frame and the timestamp trailer")
            return raw
        arr = np.asarray(frame)
        if arr.shape != (self.height, self.width):
            raise ValueError(
                f"SER frame is {arr.shape} but this file's frames are "
                f"({self.height}, {self.width}); writing it would shift every "
                "later frame and the timestamp trailer")
        dtype = "<u1" if self.bytes_per_pixel == 1 else "<u2"
        return np.ascontiguousarray(arr.astype(dtype)).tobytes()

    def close(self) -> None:
        """Trailer, then the FrameCount patch, then close. Idempotent."""
        if self._closed or self._fh is None:
            self._closed = True
            return
        try:
            for tick in self._timestamps:
                self._fh.write(struct.pack("<q", tick))
            # THE PATCH. Offset 38, one int32, the count that really landed.
            self._fh.flush()
            self._fh.seek(38)
            self._fh.write(struct.pack("<i", self.frames))
            self._fh.flush()
        finally:
            self._fh.close()
            self._fh = None
            self._closed = True

    @property
    def size_bytes(self) -> int:
        """What the finished file will be: header + frames + trailer."""
        return (SER_HEADER_BYTES + self.frames * self.frame_bytes
                + self.frames * 8)


# --------------------------------------------------------------------- reading

def read_header(path: str | Path) -> dict:
    """Parse a .ser header. Raises ValueError when it is not one."""
    with open(path, "rb") as fh:
        raw = fh.read(SER_HEADER_BYTES)
    if len(raw) < SER_HEADER_BYTES or raw[:14] != SER_FILE_ID:
        raise ValueError(f"{path} is not a SER file")
    (lu_id, color_id, little_endian, width, height, depth,
     frames) = struct.unpack_from("<7i", raw, 14)
    dt_local, dt_utc = struct.unpack_from("<2q", raw, 162)

    def _txt(off: int) -> str:
        return raw[off:off + 40].split(b"\x00")[0].decode("ascii", "replace").strip()

    return {
        "lu_id": lu_id, "color_id": color_id, "little_endian": little_endian,
        "width": width, "height": height, "bit_depth": depth,
        "frames": frames, "observer": _txt(42), "instrument": _txt(82),
        "telescope": _txt(122), "datetime_ticks": dt_local,
        "datetime_utc_ticks": dt_utc,
        "bytes_per_pixel": bytes_per_pixel(depth),
    }


def read_frames(path: str | Path, *, limit: int | None = None) -> Iterator[np.ndarray]:
    """Yield frames as 2-D arrays. Streams: never more than one frame in RAM."""
    head = read_header(path)
    w, h, bpp = head["width"], head["height"], head["bytes_per_pixel"]
    dtype = "<u1" if bpp == 1 else "<u2"
    n = head["frames"] if limit is None else min(head["frames"], limit)
    stride = w * h * bpp
    with open(path, "rb") as fh:
        fh.seek(SER_HEADER_BYTES)
        for _ in range(max(0, n)):
            buf = fh.read(stride)
            if len(buf) < stride:
                return
            yield np.frombuffer(buf, dtype=dtype).reshape((h, w))
