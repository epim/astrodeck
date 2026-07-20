"""LX200 codec golden tests — vectors from the captured AM5N session
(docs/hardware/zwo-am5-lx200-protocol.md). All geographic coordinates here are
FICTIONAL (site privacy)."""
from datetime import datetime, timezone

from astrodeck.devices import lx200


def test_build_and_ack_constants():
    assert lx200.build("GR") == b":GR#"
    assert lx200.build("Spu") == b":Spu#"
    assert lx200.ACK_OK == "1" and lx200.REFUSED == "e14"


def test_parse_ra_dec_capture_vectors():
    assert abs(lx200.parse_ra("10:13:56#") - (10 + 13 / 60 + 56 / 3600)) < 1e-9
    assert lx200.parse_dec("+90*00:00#") == 90.0
    assert abs(lx200.parse_dec("-05*30:15") - -(5 + 30 / 60 + 15 / 3600)) < 1e-9


def test_format_roundtrip():
    assert lx200.format_ra(10 + 13 / 60 + 56 / 3600) == "10:13:56"
    assert lx200.format_dec(-(5 + 30 / 60 + 15 / 3600)) == "-05*30:15"
    assert abs(lx200.parse_dec(lx200.format_dec(37.1308)) - 37.1308) < 1 / 3600


def test_smge_uses_w_positive_longitude():
    # East-positive -100.5083... (= 100°30'30" W) -> W-positive +100*30:30
    s = lx200.smge(40.0, -(100 + 30 / 60 + 30 / 3600))
    assert s == "SMGE+40*00:00&+100*30:30"


def test_utc_init_cmds_shape():
    t = datetime(2026, 7, 19, 22, 14, 58, tzinfo=timezone.utc)
    assert lx200.utc_init_cmds(t) == ["SG+00:00", "SH0", "SC07/19/26", "SL22:14:58"]


def test_parse_status_capture_words():
    st = lx200.parse_status("nGM000000005#")
    assert st.tracking is False and st.raw == "nGM000000005"
    assert lx200.parse_status("NGM000000000#").tracking is True
