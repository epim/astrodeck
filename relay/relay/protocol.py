"""AstroDeck relay tunnel protocol -- the binary wire format (W3.2).

ONE outbound WSS carries EVERYTHING between the home "scope" and this relay:
many concurrent browser HTTP request/response exchanges, many nested browser
``/ws`` streams, bulk media, keepalive, and home<->relay control pushes
(revocation). Concurrency is keyed by a per-message ``stream_id``; the ``type``
byte says what each frame is.

This module is a *self-contained* copy of the codec so ``relay/`` keeps deps
separate from the server venv. It MUST stay byte-for-byte compatible with
``server/astrodeck/remote/protocol.py`` -- the scope client (home side) and the
relay both encode/decode the exact same frame:

    [ type:1B | stream_id:8B (uint64 BE) | header_len:4B (BE) | header:JSON | payload:bytes ]

- ``type``       one byte, one of the ``FrameType`` constants below.
- ``stream_id``  uint64 big-endian. The RELAY allocates a fresh id for every
                 inbound browser HTTP request and every browser ``/ws``. The id
                 is OPAQUE to the home and only echoed back. ``stream_id == 0``
                 is the reserved CONTROL channel (PING/PONG/REVOKE/HELLO).
- ``header``     small JSON metadata (method/path/status/headers/ws_id/...),
                 ``header_len`` bytes. JSON, not protobuf, so it is debuggable
                 and needs no codegen. Bounded (a few KiB); never the body.
- ``payload``    raw bytes (an HTTP body chunk, one ``/ws`` JSON event, ...).
                 Bounded per frame (``MAX_PAYLOAD``, default 64 KiB) so one big
                 body never monopolizes the socket.

Decode is fail-closed: any short/over-long/garbled frame raises ``ProtocolError``
rather than returning a partial frame.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Final

# ------------------------------------------------------------------ constants

PROTO_VERSION: Final[int] = 1

# Reserved control-channel stream id (PING/PONG/REVOKE/HELLO/HELLO_ACK ride it).
CONTROL_STREAM_ID: Final[int] = 0

# Default per-frame payload cap. One 125 MB FITS is sliced into <=64 KiB chunks
# so it never head-of-line-blocks the 2 s status poll on the one socket.
MAX_PAYLOAD: Final[int] = 64 * 1024

# Header JSON is bounded so a malicious/buggy peer can't make us allocate a huge
# buffer from a 4-byte length field.
MAX_HEADER: Final[int] = 64 * 1024

# Fixed prefix size: type(1) + stream_id(8) + header_len(4).
_PREFIX = struct.Struct(">BQI")
_PREFIX_LEN: Final[int] = _PREFIX.size  # 13


class FrameType:
    """The ``type`` byte. Values are pinned by the W3.2 spec table."""

    REQ_OPEN = 0x01   # relay->scope  hdr {method,path,query,headers[],has_body,class}
    REQ_DATA = 0x02   # relay->scope  hdr {eof}                 payload=req-body chunk
    REQ_ABORT = 0x03  # relay->scope  hdr {reason}              (browser hung up)
    RESP_HEAD = 0x04  # scope->relay  hdr {status,headers[]}
    RESP_DATA = 0x05  # scope->relay  hdr {eof}                 payload=resp-body chunk
    WS_OPEN = 0x06    # relay->scope  hdr {path,query,headers[],ws_id,class}
    WS_DATA = 0x07    # scope->relay  hdr {ws_id,seq}           payload=one /ws JSON event
    WS_CLOSE = 0x08   # both          hdr {ws_id,code}
    PING = 0x10       # both          hdr {ts}                  (stream_id=0)
    PONG = 0x11       # both          hdr {ts}                  (stream_id=0)
    HELLO = 0x12      # scope->relay  hdr {device_token,home_id,generation,proto_version}
    HELLO_ACK = 0x13  # relay->scope  hdr {ok,endpoint,reason}
    REVOKE = 0x14     # both          hdr {jti[],ws_id[]}       (stream_id=0)
    WINDOW = 0x15     # both          hdr {stream_id,credit}    per-stream byte credit


# Frame-stream classes (the scheduling hint carried in REQ_OPEN/WS_OPEN headers).
# The scope's writer round-robins ready streams and caps each ``bulk`` stream's
# per-turn quota so a 125 MB FITS never head-of-line-blocks ``event`` frames.
CLASS_EVENT: Final[str] = "event"      # /ws status/preview/sequence frames
CLASS_CONTROL: Final[str] = "control"  # small request/response (deliver-or-error)
CLASS_BULK: Final[str] = "bulk"        # FITS/PNG/large GET (chunked, drop-never)

_VALID_CLASSES: Final[frozenset[str]] = frozenset(
    {CLASS_EVENT, CLASS_CONTROL, CLASS_BULK}
)

# Control frames exempt from the orphan-stream rule -- they are routed by type
# (and by jti for REVOKE), never by a per-request stream id.
_CONTROL_TYPES: Final[frozenset[int]] = frozenset(
    {
        FrameType.PING,
        FrameType.PONG,
        FrameType.HELLO,
        FrameType.HELLO_ACK,
        FrameType.REVOKE,
    }
)

_TYPE_NAMES: Final[dict[int, str]] = {
    v: k for k, v in vars(FrameType).items() if isinstance(v, int)
}


class ProtocolError(Exception):
    """A malformed frame on the wire (bad length, garbled header, over-cap).

    Decode is fail-closed: the caller should tear the tunnel down rather than
    proceed on a partially-parsed frame."""


def type_name(t: int) -> str:
    """Human-readable name for a frame ``type`` byte (for logs/errors)."""
    return _TYPE_NAMES.get(t, f"0x{t:02x}")


def is_control_type(t: int) -> bool:
    """True for frame types that ride the reserved ``stream_id == 0`` channel
    and are exempt from the orphan-stream-id rule."""
    return t in _CONTROL_TYPES


def valid_class(cls: str) -> bool:
    """True iff ``cls`` is one of the three scheduling classes."""
    return cls in _VALID_CLASSES


# ------------------------------------------------------------------ the frame

@dataclass(frozen=True)
class Frame:
    """One decoded tunnel frame: a ``type`` byte, a ``stream_id``, a JSON
    ``header`` dict, and a ``payload`` byte string.

    ``encode()`` is the inverse of ``decode()``; ``Frame`` is frozen so a
    decoded frame can't be mutated in flight."""

    type: int
    stream_id: int
    header: dict
    payload: bytes = b""

    def encode(self) -> bytes:
        """Serialize to the wire format. Raises ``ProtocolError`` if the payload
        or the encoded header exceeds its cap, or the stream id is out of the
        uint64 range."""
        if not (0 <= self.stream_id <= 0xFFFFFFFFFFFFFFFF):
            raise ProtocolError(f"stream_id out of uint64 range: {self.stream_id}")
        if len(self.payload) > MAX_PAYLOAD:
            raise ProtocolError(
                f"payload {len(self.payload)} > MAX_PAYLOAD {MAX_PAYLOAD}"
            )
        # Compact, deterministic header bytes (sorted keys => stable on the wire,
        # which keeps tests byte-exact and makes captures diffable).
        header_bytes = json.dumps(
            self.header, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if len(header_bytes) > MAX_HEADER:
            raise ProtocolError(
                f"header {len(header_bytes)} > MAX_HEADER {MAX_HEADER}"
            )
        prefix = _PREFIX.pack(self.type & 0xFF, self.stream_id, len(header_bytes))
        return prefix + header_bytes + self.payload

    # -- header convenience (read-only; never mutate a frozen frame) ----------

    def eof(self) -> bool:
        """The ``eof`` flag on REQ_DATA / RESP_DATA (default False)."""
        return bool(self.header.get("eof", False))

    def ws_id(self):
        """The ``ws_id`` on WS_OPEN / WS_DATA / WS_CLOSE (or None)."""
        return self.header.get("ws_id")

    @property
    def name(self) -> str:
        return type_name(self.type)


def decode(buf: bytes) -> Frame:
    """Decode exactly one frame from ``buf`` (one WSS binary message).

    Fail-closed: a frame shorter than its declared length, an over-cap header,
    a non-UTF-8 / non-object header, or trailing garbage all raise
    ``ProtocolError``."""
    if len(buf) < _PREFIX_LEN:
        raise ProtocolError(
            f"frame too short: {len(buf)} < prefix {_PREFIX_LEN}"
        )
    t, stream_id, header_len = _PREFIX.unpack_from(buf, 0)
    if header_len > MAX_HEADER:
        raise ProtocolError(f"header_len {header_len} > MAX_HEADER {MAX_HEADER}")
    header_end = _PREFIX_LEN + header_len
    if len(buf) < header_end:
        raise ProtocolError(
            f"truncated header: need {header_end}, have {len(buf)}"
        )
    header_bytes = buf[_PREFIX_LEN:header_end]
    payload = buf[header_end:]
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(
            f"payload {len(payload)} > MAX_PAYLOAD {MAX_PAYLOAD}"
        )
    if header_len == 0:
        header: dict = {}
    else:
        try:
            header = json.loads(header_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError(f"bad header JSON: {exc}") from exc
        if not isinstance(header, dict):
            raise ProtocolError("header JSON is not an object")
    return Frame(type=t, stream_id=stream_id, header=header, payload=payload)


# ------------------------------------------------------------ frame builders
# Small typed constructors so callers never hand-roll a header dict (and so the
# header key names are pinned in ONE place that both sides import).

def req_open(stream_id: int, method: str, path: str, query: str,
             headers: list[list[str]], *, has_body: bool = False,
             cls: str = CLASS_CONTROL) -> Frame:
    """relay->scope: open a browser HTTP exchange on ``stream_id``."""
    return Frame(
        FrameType.REQ_OPEN, stream_id,
        {
            "method": method, "path": path, "query": query,
            "headers": headers, "has_body": bool(has_body), "class": cls,
        },
    )


def req_data(stream_id: int, payload: bytes, *, eof: bool) -> Frame:
    """relay->scope: a request-body chunk (uploads stream in)."""
    return Frame(FrameType.REQ_DATA, stream_id, {"eof": bool(eof)}, payload)


def req_abort(stream_id: int, reason: str = "") -> Frame:
    """relay->scope: the browser hung up before EOF."""
    return Frame(FrameType.REQ_ABORT, stream_id, {"reason": reason})


def resp_head(stream_id: int, status: int,
              headers: list[list[str]]) -> Frame:
    """scope->relay: the HTTP response status line + headers."""
    return Frame(
        FrameType.RESP_HEAD, stream_id, {"status": int(status), "headers": headers}
    )


def resp_data(stream_id: int, payload: bytes, *, eof: bool) -> Frame:
    """scope->relay: a response-body chunk (streamed off disk for FITS/SPA)."""
    return Frame(FrameType.RESP_DATA, stream_id, {"eof": bool(eof)}, payload)


def ws_open(stream_id: int, ws_id: str, path: str, query: str,
            headers: list[list[str]], *, cls: str = CLASS_EVENT) -> Frame:
    """relay->scope: a browser opened ``/ws`` (one stream per browser socket)."""
    return Frame(
        FrameType.WS_OPEN, stream_id,
        {
            "ws_id": ws_id, "path": path, "query": query,
            "headers": headers, "class": cls,
        },
    )


def ws_data(stream_id: int, ws_id: str, seq: int, payload: bytes) -> Frame:
    """scope->relay: one ``/ws`` JSON event (server->client ONLY)."""
    return Frame(
        FrameType.WS_DATA, stream_id, {"ws_id": ws_id, "seq": int(seq)}, payload
    )


def ws_close(stream_id: int, ws_id: str, code: int = 1000) -> Frame:
    """both: close a tunnelled ``/ws`` stream."""
    return Frame(
        FrameType.WS_CLOSE, stream_id, {"ws_id": ws_id, "code": int(code)}
    )


def ping(ts: float) -> Frame:
    """both: keepalive on the reserved control stream."""
    return Frame(FrameType.PING, CONTROL_STREAM_ID, {"ts": ts})


def pong(ts: float) -> Frame:
    """both: keepalive reply on the reserved control stream."""
    return Frame(FrameType.PONG, CONTROL_STREAM_ID, {"ts": ts})


def hello(device_token: str, home_id: str, generation: int,
          proto_version: int = PROTO_VERSION) -> Frame:
    """scope->relay: the FIRST frame -- device registration."""
    return Frame(
        FrameType.HELLO, CONTROL_STREAM_ID,
        {
            "device_token": device_token, "home_id": home_id,
            "generation": int(generation), "proto_version": int(proto_version),
        },
    )


def hello_ack(ok: bool, *, endpoint: str = "", reason: str = "") -> Frame:
    """relay->scope: confirm (or reject) the registration."""
    return Frame(
        FrameType.HELLO_ACK, CONTROL_STREAM_ID,
        {"ok": bool(ok), "endpoint": endpoint, "reason": reason},
    )


def revoke(jti: list[str], ws_id: list[str]) -> Frame:
    """both: a revocation push on the reserved control stream."""
    return Frame(
        FrameType.REVOKE, CONTROL_STREAM_ID, {"jti": list(jti), "ws_id": list(ws_id)}
    )


def window(stream_id: int, credit: int) -> Frame:
    """both: grant ``credit`` more body bytes on ``stream_id`` (flow-control).

    The ``stream_id`` the credit applies to is BOTH the frame's routing id AND
    echoed in the header, so a WINDOW grant is self-describing in a capture."""
    return Frame(
        FrameType.WINDOW, stream_id, {"stream_id": stream_id, "credit": int(credit)}
    )
