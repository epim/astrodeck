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


def test_netplan_sae():
    y = prov.emit_netplan("W3Only", "12345678", sae=True)
    assert "key-management: sae" in y
    assert 'password: "12345678"' in y
    # sae flag must be ignored for open networks
    assert "sae" not in prov.emit_netplan("open-net", "", sae=True)


IW_FIXTURE = (
    "BSS 84:23:88:16:09:7c(on wlan0)\n"
    "\tsignal: -45.0 dBm\n"
    "\tSSID: W3Net\n"
    "\tRSN:\t * Version: 1\n"
    "\t\t * Authentication suites: SAE\n"
    "BSS c8:84:8c:52:14:90(on wlan0)\n"
    "\tsignal: -38.0 dBm\n"
    "\tSSID: Mixed\n"
    "\tRSN:\t * Version: 1\n"
    "\t\t * Authentication suites: PSK SAE\n"
    "BSS c8:84:8c:52:14:91(on wlan0)\n"
    "\tsignal: -60.0 dBm\n"
    "\tSSID: Legacy\n"
    "\tRSN:\t * Version: 1\n"
    "\t\t * Authentication suites: PSK\n"
)


def test_parse_scan_akm_and_order():
    nets = prov.parse_scan(IW_FIXTURE)
    assert [n["ssid"] for n in nets] == ["Mixed", "W3Net", "Legacy"]
    assert prov.needs_sae("W3Net", nets)
    assert not prov.needs_sae("Mixed", nets)
    assert not prov.needs_sae("Legacy", nets)
    assert not prov.needs_sae("NotSeen", nets)


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
