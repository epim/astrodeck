"""Pure-function tests for the WiFi provisioner. No hardware, no network."""

import importlib.util
import pathlib

provision_dir = pathlib.Path(__file__).parent.parent / "provision"
frontend_spec = importlib.util.spec_from_file_location(
    "astrodeck_provision_frontend", provision_dir / "astrodeck-provision.py"
)
frontend = importlib.util.module_from_spec(frontend_spec)
frontend_spec.loader.exec_module(frontend)
broker_spec = importlib.util.spec_from_file_location(
    "astrodeck_provision_broker", provision_dir / "astrodeck-provision-broker.py"
)
prov = importlib.util.module_from_spec(broker_spec)
broker_spec.loader.exec_module(prov)


def test_derive_ssid():
    assert prov.derive_ssid("aa:bb:cc:dd:ee:ff") == "AstroDeck-EEFF"
    assert prov.derive_ssid("AA-BB-CC-DD-EE-01") == "AstroDeck-EE01"


def test_psk_validation():
    assert prov.valid_psk("astrodeck")
    assert prov.valid_psk("")            # open network
    assert not prov.valid_psk("short")
    assert not prov.valid_psk("x" * 64)
    assert not prov.valid_psk("1234567\r")
    assert frontend.valid_psk("example88")


def test_ssid_validation_is_byte_bounded_and_control_free():
    assert prov.valid_ssid("home")
    assert prov.valid_ssid("é" * 16)       # 32 UTF-8 bytes
    assert not prov.valid_ssid("é" * 17)
    assert not prov.valid_ssid("evil\rname")
    assert frontend.valid_ssid("home")


def test_netplan_psk_quoting():
    y = prov.emit_netplan('Cafe "42"\\home', "pass word 8")
    assert '"Cafe \\"42\\"\\\\home"' in y
    assert 'password: "pass word 8"' in y
    assert "renderer: networkd" in y


def test_netplan_open_network():
    y = prov.emit_netplan("open-net", "")
    assert y.strip().endswith('"open-net": {}')
    assert "password:" not in y


def test_netplan_rejects_control_characters():
    import pytest
    with pytest.raises(ValueError):
        prov.emit_netplan("evil\nnet", "12345678")
    with pytest.raises(ValueError):
        prov.emit_netplan("evil\rnet", "12345678")


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
    page = frontend.render_portal(
        [{"ssid": "x<script>y", "signal": -40, "akm": []}], 'e"rr', 'a"b'
    )
    assert "<script>" not in page
    joining = frontend.render_joining("net<script>")
    assert "<script>" not in joining
    assert f'name="csrf" value="{frontend.CSRF_TOKEN}"' in page
    assert 'method="post" action="/rescan"' in page
