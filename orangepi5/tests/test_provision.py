"""Pure-function tests for the WiFi provisioner. No hardware, no network."""

import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "astrodeck_provision",
    pathlib.Path(__file__).parent.parent / "provision" / "astrodeck-provision.py",
)
prov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prov)


def test_derive_ssid():
    assert prov.derive_ssid("aa:bb:cc:dd:ee:ff") == "AstroDeck-EEFF"
    assert prov.derive_ssid("AA-BB-CC-DD-EE-01") == "AstroDeck-EE01"


def test_psk_validation():
    assert prov.valid_psk("astrodeck")
    assert prov.valid_psk("")            # open network
    assert not prov.valid_psk("short")
    assert not prov.valid_psk("x" * 64)


def test_netplan_psk_quoting():
    y = prov.emit_netplan('Cafe "42"\\home', "pass word 8")
    assert '"Cafe \\"42\\"\\\\home"' in y
    assert 'password: "pass word 8"' in y
    assert "renderer: networkd" in y


def test_netplan_open_network():
    y = prov.emit_netplan("open-net", "")
    assert y.strip().endswith('"open-net": {}')
    assert "password:" not in y


def test_netplan_rejects_newlines():
    import pytest
    with pytest.raises(ValueError):
        prov.emit_netplan("evil\nnet", "12345678")


def test_wpa_ap_conf():
    c = prov.emit_wpa_ap_conf("AstroDeck-BEEF", "astrodeck")
    assert "mode=2" in c
    assert 'ssid="AstroDeck-BEEF"' in c
    assert "frequency=2437" in c


def test_networkd_ap_unit():
    n = prov.emit_networkd_ap()
    assert "DHCPServer=yes" in n
    assert "Address=10.42.0.1/24" in n


def test_pages_escape_html():
    page = prov.render_portal([{"ssid": "x<script>y", "signal": -40}], 'e"rr', 'a"b')
    assert "<script>" not in page
    joining = prov.render_joining("net<script>")
    assert "<script>" not in joining
