"""Wire-codec round-trip + fail-closed decode (W3.2 framing)."""
from __future__ import annotations

import json

import pytest

from relay import protocol
from relay.protocol import (
    MAX_HEADER,
    MAX_PAYLOAD,
    Frame,
    FrameType,
    ProtocolError,
    decode,
)


def test_round_trip_all_builders():
    """Every typed builder encodes and decodes back byte-for-byte."""
    frames = [
        protocol.req_open(7, "POST", "/api/x", "a=1", [["h", "v"]],
                          has_body=True, cls=protocol.CLASS_CONTROL),
        protocol.req_data(7, b"hello", eof=False),
        protocol.req_data(7, b"world", eof=True),
        protocol.req_abort(7, "browser hung up"),
        protocol.resp_head(7, 200, [["content-type", "application/json"]]),
        protocol.resp_data(7, b"\x00\x01\x02", eof=True),
        protocol.ws_open(9, "ws1", "/ws", "", [["origin", "x"]]),
        protocol.ws_data(9, "ws1", 42, b'{"type":"status"}'),
        protocol.ws_close(9, "ws1", 1000),
        protocol.ping(123.5),
        protocol.pong(123.5),
        protocol.hello("tok", "home-1", 5),
        protocol.hello_ack(True, endpoint="/h/home-1/"),
        protocol.revoke(["j1", "j2"], ["ws1"]),
        protocol.window(7, 65536),
    ]
    for f in frames:
        raw = f.encode()
        back = decode(raw)
        assert back.type == f.type
        assert back.stream_id == f.stream_id
        assert back.header == f.header
        assert back.payload == f.payload


def test_binary_payload_survives_exact():
    payload = bytes(range(256)) * 4  # 1 KiB of every byte value
    f = protocol.resp_data(3, payload, eof=True)
    assert decode(f.encode()).payload == payload


def test_header_is_sorted_deterministic():
    """Encoded header bytes are deterministic (sorted keys) for stable wire +
    diffable captures."""
    f = Frame(FrameType.REQ_OPEN, 1, {"b": 2, "a": 1})
    raw = f.encode()
    # header starts after the 13-byte prefix.
    header_bytes = raw[13:13 + len(json.dumps({"a": 1, "b": 2}, separators=(",", ":")))]
    assert header_bytes == b'{"a":1,"b":2}'


def test_control_frames_use_reserved_stream_zero():
    assert protocol.ping(1).stream_id == protocol.CONTROL_STREAM_ID
    assert protocol.pong(1).stream_id == 0
    assert protocol.hello("t", "h", 1).stream_id == 0
    assert protocol.revoke([], []).stream_id == 0


def test_decode_rejects_short_frame():
    with pytest.raises(ProtocolError):
        decode(b"\x01\x00\x00")  # shorter than the 13-byte prefix


def test_decode_rejects_truncated_header():
    # Claim a 100-byte header but give none.
    import struct
    raw = struct.pack(">BQI", FrameType.REQ_OPEN, 1, 100)
    with pytest.raises(ProtocolError):
        decode(raw)


def test_decode_rejects_non_object_header():
    import struct
    body = b"[1,2,3]"
    raw = struct.pack(">BQI", FrameType.REQ_OPEN, 1, len(body)) + body
    with pytest.raises(ProtocolError):
        decode(raw)


def test_decode_rejects_bad_json_header():
    import struct
    body = b"{not json"
    raw = struct.pack(">BQI", FrameType.REQ_OPEN, 1, len(body)) + body
    with pytest.raises(ProtocolError):
        decode(raw)


def test_encode_rejects_oversize_payload():
    with pytest.raises(ProtocolError):
        protocol.resp_data(1, b"x" * (MAX_PAYLOAD + 1), eof=True).encode()


def test_encode_rejects_oversize_header():
    big = {"x": "y" * (MAX_HEADER + 10)}
    with pytest.raises(ProtocolError):
        Frame(FrameType.REQ_OPEN, 1, big).encode()


def test_is_control_type():
    assert protocol.is_control_type(FrameType.PING)
    assert protocol.is_control_type(FrameType.REVOKE)
    assert not protocol.is_control_type(FrameType.RESP_DATA)


def test_class_validation():
    assert protocol.valid_class(protocol.CLASS_BULK)
    assert not protocol.valid_class("nonsense")
