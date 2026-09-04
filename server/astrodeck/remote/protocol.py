"""The W3 binary tunnel frame codec (NO gRPC, NO protobuf).

ONE outbound WSS carries multiplexed binary frames. Each WSS binary message is
exactly one frame:

    [ type:1B | stream_id:8B(uint64 BE) | header_len:4B(BE) | header:JSON | payload:bytes ]

The header is UTF-8 JSON of ``header_len`` bytes; everything after it (to the end
of the message) is the opaque payload. ``stream_id`` is allocated by the RELAY
(opaque to the scope); ``0`` is reserved for connection-level control
(PING/PONG/REVOKE) and HELLO/HELLO_ACK.

This module is PURE: it has no I/O, no websockets dependency, and no app import,
so it is trivially unit-testable off-wire (the §T7 fake in-memory frame channel
encodes/decodes through here). The relay lane (separate repo dir) implements the
SAME wire format independently; this is the home's canonical encoder/decoder.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

# Per-frame payload bound. The relay chunks request/response bodies so a 125 MB
# FITS never lands in one allocation-sized frame. The decoder enforces this as a
# hard ceiling -- an over-size frame is a protocol error, not a silent truncation.
DEFAULT_MAX_PAYLOAD = 64 * 1024  # 64 KiB

# Header JSON is attacker-controlled by the relay. Bound it independently from
# the payload so the websocket client and decoder agree on one finite message
# ceiling.
DEFAULT_MAX_HEADER = 64 * 1024  # 64 KiB

# Header prefix layout: type(1) + stream_id(8, uint64 BE) + header_len(4, uint32 BE).
_PREFIX = struct.Struct(">BQI")
_PREFIX_LEN = _PREFIX.size  # 13
DEFAULT_MAX_WIRE_SIZE = _PREFIX_LEN + DEFAULT_MAX_HEADER + DEFAULT_MAX_PAYLOAD

PROTO_VERSION = 1


class FrameType(IntEnum):
    """Wire frame type byte. Values are PINNED (the relay matches them)."""
    REQ_OPEN = 0x01   # relay->scope  hdr {method,path,query,headers,has_body,class}
    REQ_DATA = 0x02   # relay->scope  hdr {eof}                payload=req-body chunk
    REQ_ABORT = 0x03  # relay->scope
    RESP_HEAD = 0x04  # scope->relay  hdr {status,headers}
    RESP_DATA = 0x05  # scope->relay  hdr {eof}                payload=resp-body chunk
    WS_OPEN = 0x06    # relay->scope  hdr {path,query,headers,ws_id,class}
    WS_DATA = 0x07    # scope->relay  hdr {ws_id,seq}          payload=one /ws JSON event
    WS_CLOSE = 0x08   # both          hdr {ws_id,code}
    PING = 0x10       # both          stream_id=0
    PONG = 0x11       # both          stream_id=0
    HELLO = 0x12      # scope->relay  hdr {device_token,home_id,generation,proto_version}
    HELLO_ACK = 0x13  # relay->scope  hdr {ok,endpoint,reason}
    REVOKE = 0x14     # both          stream_id=0  hdr {jti,ws_id}
    WINDOW = 0x15     # both          hdr {stream_id,credit}  per-stream byte credit


# stream_id 0 is reserved for connection-level control. These frame types are the
# ONLY ones allowed to ride it; anything else on stream 0 is a protocol error, and
# these types are exempt from the per-stream registry checks (orphan-stream rules).
CONTROL_STREAM_ID = 0
_STREAM0_TYPES = frozenset({
    FrameType.PING, FrameType.PONG, FrameType.REVOKE,
    FrameType.HELLO, FrameType.HELLO_ACK,
})


class ProtocolError(Exception):
    """A malformed or out-of-bounds frame on the wire (closes the connection)."""


@dataclass(slots=True)
class Frame:
    """One decoded tunnel frame: a typed header dict + an opaque payload.

    ``header`` is the parsed JSON object (always a dict; empty for frames that
    carry none). ``payload`` is the raw trailing bytes (empty for header-only
    frames)."""
    type: FrameType
    stream_id: int = CONTROL_STREAM_ID
    header: dict[str, Any] = field(default_factory=dict)
    payload: bytes = b""

    # -- typed header accessors (convenience; never raise on a missing key) ----

    @property
    def eof(self) -> bool:
        """REQ_DATA/RESP_DATA terminal-chunk flag (default False)."""
        return bool(self.header.get("eof", False))

    @property
    def ws_id(self) -> Any:
        """Opaque relay-allocated browser id (currently a bounded string)."""
        return self.header.get("ws_id")


def encode_frame(
    type: FrameType,
    stream_id: int = CONTROL_STREAM_ID,
    header: dict[str, Any] | None = None,
    payload: bytes = b"",
    *,
    max_payload: int = DEFAULT_MAX_PAYLOAD,
) -> bytes:
    """Serialize one frame to a single WSS binary message.

    Validates the stream_id range (uint64) and the payload ceiling so a buggy
    caller can't emit an un-decodable or oversize frame. The header is compact
    JSON (no spaces) for wire economy."""
    if not 0 <= stream_id <= 0xFFFFFFFFFFFFFFFF:
        raise ProtocolError(f"stream_id out of uint64 range: {stream_id}")
    if len(payload) > max_payload:
        raise ProtocolError(
            f"payload {len(payload)}B exceeds max {max_payload}B")
    hdr = header or {}
    hdr_bytes = json.dumps(hdr, separators=(",", ":")).encode("utf-8")
    if len(hdr_bytes) > DEFAULT_MAX_HEADER:
        raise ProtocolError(
            f"header {len(hdr_bytes)}B exceeds max {DEFAULT_MAX_HEADER}B")
    return _PREFIX.pack(int(type), stream_id, len(hdr_bytes)) + hdr_bytes + payload


def decode_frame(
    raw: bytes,
    *,
    max_payload: int = DEFAULT_MAX_PAYLOAD,
) -> Frame:
    """Parse one WSS binary message into a ``Frame``.

    Raises ``ProtocolError`` on a truncated prefix, an unknown type byte, a header
    that runs past the buffer, non-object/garbage header JSON, an oversize payload,
    or a non-control frame type riding the reserved stream 0 (or a control type on
    a non-zero stream)."""
    if len(raw) < _PREFIX_LEN:
        raise ProtocolError("frame shorter than prefix")
    type_byte, stream_id, header_len = _PREFIX.unpack_from(raw, 0)
    if header_len > DEFAULT_MAX_HEADER:
        raise ProtocolError(
            f"header {header_len}B exceeds max {DEFAULT_MAX_HEADER}B")
    try:
        ftype = FrameType(type_byte)
    except ValueError as exc:
        raise ProtocolError(f"unknown frame type 0x{type_byte:02x}") from exc

    hdr_start = _PREFIX_LEN
    hdr_end = hdr_start + header_len
    if hdr_end > len(raw):
        raise ProtocolError("header_len runs past buffer")
    payload = raw[hdr_end:]
    if len(payload) > max_payload:
        raise ProtocolError(
            f"payload {len(payload)}B exceeds max {max_payload}B")

    if header_len:
        try:
            header = json.loads(raw[hdr_start:hdr_end].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError(f"bad header json: {exc}") from exc
        if not isinstance(header, dict):
            raise ProtocolError("frame header is not a JSON object")
    else:
        header = {}

    # Reserved-stream discipline: control types ONLY on stream 0; data types NEVER
    # on stream 0 (a tunneled request/ws must carry a relay-allocated id).
    if ftype in _STREAM0_TYPES:
        if stream_id != CONTROL_STREAM_ID:
            raise ProtocolError(f"control frame {ftype.name} on stream {stream_id}")
    elif stream_id == CONTROL_STREAM_ID:
        raise ProtocolError(f"data frame {ftype.name} on reserved stream 0")

    return Frame(type=ftype, stream_id=stream_id, header=header, payload=payload)


__all__ = [
    "FrameType", "Frame", "ProtocolError",
    "encode_frame", "decode_frame",
    "DEFAULT_MAX_PAYLOAD", "DEFAULT_MAX_HEADER", "DEFAULT_MAX_WIRE_SIZE",
    "CONTROL_STREAM_ID", "PROTO_VERSION",
]
