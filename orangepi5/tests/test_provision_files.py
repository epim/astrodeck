"""Private-file and configuration-emission regression tests."""

from __future__ import annotations

import os
import stat

import pytest


@pytest.fixture
def prov(broker):
    """System configuration and private state belong to the root broker."""
    return broker


def test_atomic_private_write_replaces_content_and_sets_posix_modes(prov, tmp_path):
    path = tmp_path / "private" / "value.json"
    prov._atomic_write_private(path, "first")
    prov._atomic_write_private(path, "second")
    assert path.read_text(encoding="utf-8") == "second"
    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_exclusive_write_cannot_replace_an_existing_identity(prov, tmp_path):
    path = tmp_path / "setup-identity.json"
    prov._atomic_write_private(path, "original", exclusive=True)
    with pytest.raises(FileExistsError):
        prov._atomic_write_private(path, "replacement", exclusive=True)
    assert path.read_text(encoding="utf-8") == "original"


def test_private_writer_rejects_symlink_targets(prov, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("host cannot create file symlinks")
    with pytest.raises((OSError, RuntimeError, PermissionError)):
        prov._atomic_write_private(link, "overwrite")
    assert victim.read_text(encoding="utf-8") == "keep"


def test_replace_failure_propagates_and_preserves_old_destination(
    prov, tmp_path, monkeypatch
):
    path = tmp_path / "value"
    prov._atomic_write_private(path, "old")

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(prov.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        prov._atomic_write_private(path, "new")
    assert path.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".astrodeck-*"))


def test_fsync_failure_propagates_before_publication(prov, tmp_path, monkeypatch):
    path = tmp_path / "value"

    def fail_fsync(_fd):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(prov.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="injected fsync failure"):
        prov._atomic_write_private(path, "secret")
    assert not path.exists()
    assert not list(tmp_path.glob(".astrodeck-*"))


def test_wpa_config_escapes_quoted_setup_ssid(prov):
    config = prov.emit_wpa_ap_conf('AstroDeck-"\\BEEF', "abcd-1234-efgh")
    assert 'ssid="AstroDeck-\\"\\\\BEEF"' in config
    assert "\n    key_mgmt=WPA-PSK\n" in config
    assert config.count("network={") == 1


def test_netplan_and_wpa_emitters_reject_controls(prov):
    with pytest.raises(ValueError):
        prov.emit_netplan("bad\nssid", "12345678")
    with pytest.raises(ValueError):
        prov.emit_wpa_ap_conf("bad\nssid", "abcd-1234-efgh")
    with pytest.raises(ValueError):
        prov.emit_wpa_ap_conf("AstroDeck-BEEF", "bad\npassword")


def test_hotspot_networkd_file_is_readable_by_networkd_and_nothing_else_is(prov, tmp_path):
    """networkd reads /run/systemd/network as its own user, so the one file the
    broker writes there is published 0644 on request; the default stays 0600
    and the mode is verified either way (seen on the appliance 2026-09-03)."""
    private = tmp_path / "p" / "private.json"
    shared = tmp_path / "05-astrodeck-ap.network"
    prov._write_private(private, "{}")
    prov._write_private(shared, prov.emit_networkd_ap(), private_parent=False, mode=0o644)
    if os.name == "posix":
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        assert stat.S_IMODE(shared.stat().st_mode) == 0o644
    assert "10.42.0.1/24" in shared.read_text(encoding="utf-8")


def _record_run(prov, monkeypatch, rc_for=None):
    """Replace prov.run with a recorder. rc_for: callable(args)->int or None."""
    calls = []

    def fake_run(args, timeout=30):
        calls.append(list(args))
        import subprocess
        rc = 0 if rc_for is None else rc_for(args)
        return subprocess.CompletedProcess(args, rc, "", "")

    monkeypatch.setattr(prov, "run", fake_run)
    monkeypatch.setattr(prov.time, "sleep", lambda _s: None)
    return calls


def test_reset_radio_reloads_the_driver_and_waits_for_wlan0(prov, monkeypatch):
    calls = _record_run(prov, monkeypatch)
    monkeypatch.setattr(prov, "_wlan_present", lambda: True)
    assert prov.reset_radio() is True
    assert ["modprobe", "-r", "brcmfmac"] in calls
    assert ["modprobe", "brcmfmac"] in calls
    # the remove must precede the load
    assert calls.index(["modprobe", "-r", "brcmfmac"]) < calls.index(["modprobe", "brcmfmac"])


def test_reset_radio_fails_closed_if_wlan0_never_returns(prov, monkeypatch):
    _record_run(prov, monkeypatch)
    monkeypatch.setattr(prov, "_wlan_present", lambda: False)
    assert prov.reset_radio() is False


def test_reset_radio_fails_when_the_module_will_not_load(prov, monkeypatch):
    def rc_for(args):
        return 1 if args[:2] == ["modprobe", "brcmfmac"] else 0
    _record_run(prov, monkeypatch, rc_for)
    monkeypatch.setattr(prov, "_wlan_present", lambda: True)
    assert prov.reset_radio() is False


def test_request_radio_reset_starts_the_isolated_unit(prov, monkeypatch):
    calls = _record_run(prov, monkeypatch)
    assert prov.request_radio_reset() is True
    assert ["systemctl", "start", "--wait", prov.RADIO_RESET_UNIT] in calls


def test_request_radio_reset_tolerates_a_missing_unit(prov, monkeypatch):
    _record_run(prov, monkeypatch, rc_for=lambda args: 1)
    # a factory-fresh radio needs no reset, so a failing/absent unit must not
    # block onboarding: it is logged and reported False, never raised.
    assert prov.request_radio_reset() is False


def test_radio_watchdog_resets_only_when_wlan0_is_truly_absent(prov, monkeypatch):
    calls = _record_run(prov, monkeypatch)
    # present -> nothing
    monkeypatch.setattr(prov, "_wlan_present", lambda: True)
    assert prov.radio_watchdog() == 0
    assert not any(a[:2] == ["systemctl", "start"] for a in calls)
    # a transient absence that recovers on the recheck -> still nothing
    seq = iter([False, True])
    monkeypatch.setattr(prov, "_wlan_present", lambda: next(seq))
    assert prov.radio_watchdog() == 0
    assert not any(a[:2] == ["systemctl", "start"] for a in calls)
    # absent on both checks -> ask the reset unit to run
    monkeypatch.setattr(prov, "_wlan_present", lambda: False)
    assert prov.radio_watchdog() == 0
    assert ["systemctl", "start", prov.RADIO_RESET_UNIT] in calls


def test_ap_up_resets_the_radio_before_starting_the_supplicant(prov):
    import inspect
    src = inspect.getsource(prov.ap_up)
    assert "request_radio_reset()" in src
    assert src.index("request_radio_reset()") < src.index("wpa_supplicant")
