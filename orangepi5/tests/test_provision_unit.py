"""Privilege-boundary, service-sandbox, and AP lifecycle tests."""

from __future__ import annotations

import ast
import json
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
PROVISION_DIR = ROOT / "orangepi5/provision"
FRONTEND = PROVISION_DIR / "astrodeck-provision.py"
BROKER = PROVISION_DIR / "astrodeck-provision-broker.py"
FRONTEND_SERVICE = PROVISION_DIR / "astrodeck-provision.service"
BROKER_SERVICE = PROVISION_DIR / "astrodeck-provision-broker.service"
BROKER_SOCKET = PROVISION_DIR / "astrodeck-provision-broker.socket"
SYSUSERS = PROVISION_DIR / "astrodeck-provision.sysusers.conf"
RECORD_SERVICE = PROVISION_DIR / "astrodeck-recovery-record.service"
CLEAR_SERVICE = PROVISION_DIR / "astrodeck-recovery-clear.service"
CLEAR_TIMER = PROVISION_DIR / "astrodeck-recovery-clear.timer"
INSTALLER = PROVISION_DIR / "install-to-rootfs.sh"


def _unit_values(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result.setdefault(key, []).append(value)
    return result


def _ok(args):
    return subprocess.CompletedProcess(args, 0, "", "")


def test_network_frontend_is_non_root_and_cannot_reach_manager_bus():
    values = _unit_values(FRONTEND_SERVICE)
    assert values["User"] == ["astrodeck-setup"]
    assert values["Group"] == ["astrodeck-setup"]
    assert values["NoNewPrivileges"] == ["yes"]
    assert values["PrivateDevices"] == ["yes"]
    assert values["ProtectSystem"] == ["strict"]
    assert values["ProtectProc"] == ["invisible"]
    assert values["ProcSubset"] == ["pid"]
    assert values["CapabilityBoundingSet"] == ["CAP_NET_BIND_SERVICE"]
    assert values["AmbientCapabilities"] == ["CAP_NET_BIND_SERVICE"]
    assert "ReadWritePaths" not in values
    assert set(values["RestrictAddressFamilies"][0].split()) == {
        "AF_UNIX", "AF_INET", "AF_INET6"
    }
    inaccessible = set(values["InaccessiblePaths"][0].split())
    assert "-/run/systemd/private" in inaccessible
    assert "-/run/dbus/system_bus_socket" in inaccessible
    assert "-/run/systemd/system" in inaccessible
    assert "-/etc/systemd/system" in inaccessible
    assert "@privileged" in values["SystemCallFilter"][0]
    assert values["SocketBindDeny"] == ["any"]
    # systemd takes ONE rule per SocketBindAllow= line. A space-separated
    # list is rejected at parse time and, with SocketBindDeny=any in force,
    # the portal then binds nothing. Seen live on the board, 2026-09-03.
    bind_allow = values["SocketBindAllow"]
    assert all(" " not in rule for rule in bind_allow), bind_allow
    assert set(bind_allow) == {"ipv4:tcp:80", "ipv4:udp:53"}
    assert values["IPAddressDeny"] == ["any"]
    assert values["IPAddressAllow"] == ["10.42.0.0/24"]


def test_frontend_source_has_no_privileged_execution_surface():
    source = FRONTEND.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "subprocess" not in imported
    for forbidden in (
        "systemctl", "networkctl", "netplan", "wpa_supplicant", "pkill",
        "/etc/netplan", "/run/systemd/network", "setup-identity.json",
    ):
        assert forbidden not in source
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"system", "popen", "spawn", "execv", "execve"}
        for node in ast.walk(tree)
    )


def test_root_broker_is_socket_activated_and_keeps_only_network_caps():
    values = _unit_values(BROKER_SERVICE)
    assert values["User"] == ["root"]
    assert values["Group"] == ["root"]
    assert values["ExecStart"] == [
        "/usr/bin/python3 /usr/local/lib/astrodeck/astrodeck-provision-broker.py broker"
    ]
    assert values["RequiresMountsFor"] == ["/data"]
    assert values["ConditionPathIsMountPoint"] == ["/data"]
    assert values["NoNewPrivileges"] == ["yes"]
    assert set(values["CapabilityBoundingSet"][0].split()) == {
        "CAP_NET_ADMIN", "CAP_NET_RAW"
    }
    assert set(values["ReadWritePaths"][0].split()) == {
        "/run/astrodeck", "/run/systemd/network", "/etc/netplan", "/data/astrodeck"
    }
    assert values["KillMode"] == ["control-group"]
    assert values["Requires"] == ["astrodeck-provision-broker.socket"]


def test_broker_socket_and_dedicated_identity_are_fixed():
    values = _unit_values(BROKER_SOCKET)
    assert values["ListenStream"] == ["/run/astrodeck-provision-broker.sock"]
    assert values["FileDescriptorName"] == ["provision-broker"]
    assert values["Accept"] == ["no"]
    assert values["SocketUser"] == ["root"]
    assert values["SocketGroup"] == ["astrodeck-setup"]
    assert values["SocketMode"] == ["0660"]
    assert values["Backlog"] == ["8"]
    sysusers = SYSUSERS.read_text(encoding="utf-8")
    assert sysusers.strip() == (
        'u astrodeck-setup - "AstroDeck WiFi setup frontend" '
        "/nonexistent /usr/sbin/nologin"
    )


def test_broker_protocol_rejects_commands_paths_units_and_extra_fields(broker):
    valid_join = {
        "version": 1,
        "operation": "join",
        "ssid": "home",
        "psk": "12345678",
        "sae": False,
    }
    assert broker.validate_broker_request(valid_join) == valid_join
    for request in (
        {"version": 1, "operation": "systemctl"},
        {"version": 1, "operation": "run", "command": "id"},
        {"version": 1, "operation": "open", "path": "/etc/shadow"},
        {"version": 1, "operation": "close", "unit": "evil.service"},
        {**valid_join, "argv": ["sh", "-c", "id"]},
        {**valid_join, "path": "/tmp/netplan.yaml"},
        {**valid_join, "sae": "false"},
        {**valid_join, "psk": "short"},
    ):
        with pytest.raises(broker.BrokerRequestError):
            broker.validate_broker_request(request)


def test_broker_framing_is_single_canonical_bounded_json_line(broker):
    valid = b'{"version":1,"operation":"open"}\n'
    assert broker.decode_broker_request(valid)["operation"] == "open"
    for frame in (
        valid[:-1],
        b" " + valid,
        valid + b"\n",
        b'{"version":1,"version":1,"operation":"open"}\n',
        b"\xff\n",
        b"x" * (broker.MAX_BROKER_REQUEST_BYTES + 1),
    ):
        with pytest.raises(broker.BrokerRequestError):
            broker.decode_broker_request(frame)


def test_broker_peer_check_allows_only_exact_dedicated_uid(broker):
    assert broker.broker_peer_is_authorized(1200, 1200) is True
    assert broker.broker_peer_is_authorized(0, 1200) is False
    assert broker.broker_peer_is_authorized(1201, 1200) is False
    assert broker.broker_peer_is_authorized(True, 1) is False


def test_broker_dispatch_has_only_four_fixed_operations(broker):
    assert broker.BROKER_OPERATIONS == frozenset({"open", "scan", "join", "close"})
    source = ast.parse(BROKER.read_text(encoding="utf-8"))
    dispatch = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_broker_request"
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"eval", "exec", "getattr"}
        for node in ast.walk(dispatch)
    )


def test_broker_deadline_clears_secret_and_tears_down_ap(broker, monkeypatch):
    state = broker.BrokerState()
    calls = []
    monkeypatch.setattr(broker, "ap_down", lambda: calls.append("down"))
    with state._state_lock:
        state._active = True
        state._generation = 7
        state._deadline = time.monotonic() - 1
        state._setup_ssid = "AstroDeck-BEEF"
        state._setup_psk = "abcd-1234-efgh"
    state._watch_window(7)
    assert calls == ["down"]
    assert state._active is False
    assert state._setup_ssid == ""
    assert state._setup_psk == ""


def test_successful_join_is_not_torn_down_twice(broker, monkeypatch):
    calls = []
    monkeypatch.setattr(
        broker, "run", lambda args, timeout=30: calls.append(args) or _ok(args)
    )
    monkeypatch.setattr(broker.os, "remove", lambda _path: None)
    broker.AP_MAY_BE_ACTIVE.set()
    broker.ap_down()
    first_count = len(calls)
    assert not broker.AP_MAY_BE_ACTIVE.is_set()
    broker.ap_down()
    assert len(calls) == first_count
    assert sum(args[:4] == ["ip", "addr", "flush", "dev"] for args in calls) == 1


def test_cleanup_attempts_every_step_and_remains_retryable_on_failure(
    broker, monkeypatch
):
    calls = []

    def fake_run(args, timeout=30):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 1 if args[0] == "networkctl" else 0, "", ""
        )

    monkeypatch.setattr(broker, "run", fake_run)
    monkeypatch.setattr(broker.os, "remove", lambda _path: None)
    broker.AP_MAY_BE_ACTIVE.set()
    with pytest.raises(RuntimeError, match="reloading networkd"):
        broker.ap_down()
    assert broker.AP_MAY_BE_ACTIVE.is_set()
    assert any(args[:3] == ["ip", "addr", "flush"] for args in calls)
    assert any(args[:2] == ["networkctl", "reload"] for args in calls)
    # No mask/unmask any more: on a netplan host the client supplicant unit is
    # generated into /run/systemd/system, so a runtime mask cannot be placed.
    assert not any(args[:2] == ["systemctl", "mask"] or args[:2] == ["systemctl", "unmask"] for args in calls)


def test_expired_frontend_join_worker_never_calls_broker(prov, monkeypatch):
    calls = []
    monkeypatch.setattr(prov.BROKER, "join", lambda *_args: calls.append("join"))
    fake_handler = object()
    prov.DONE.set()
    assert prov.JOIN_LOCK.acquire(blocking=False)
    prov.Portal._join(fake_handler, "home", "12345678", False)
    assert calls == []
    assert prov.JOIN_LOCK.acquire(blocking=False)
    prov.JOIN_LOCK.release()


def test_wifi_route_check_is_scoped_to_wlan_interface(broker, monkeypatch):
    commands = []
    monkeypatch.setattr(
        broker,
        "run",
        lambda args, timeout=30: commands.append(args)
        or subprocess.CompletedProcess(args, 0, "default via 192.0.2.1", ""),
    )
    assert broker.have_default_route() is True
    assert commands == [["ip", "route", "show", "default", "dev", broker.IFACE]]


def test_default_appliance_state_refuses_a_missing_data_mount(broker, monkeypatch):
    monkeypatch.setattr(broker.os, "name", "posix")
    monkeypatch.setattr(broker.os.path, "ismount", lambda _path: False)
    with pytest.raises(RuntimeError, match="durable /data mount"):
        broker._require_durable_data_mount()


def test_installer_and_recovery_units_use_the_broker_boundary():
    installer = INSTALLER.read_text(encoding="utf-8")
    for artifact in (
        "astrodeck-provision.py",
        "astrodeck-provision-broker.py",
        "astrodeck-provision-broker.service",
        "astrodeck-provision-broker.socket",
        "astrodeck-provision.sysusers.conf",
    ):
        assert artifact in installer
    record = _unit_values(RECORD_SERVICE)
    clear = _unit_values(CLEAR_SERVICE)
    assert "astrodeck-provision-broker.py record-boot" in record["ExecStart"][0]
    assert "astrodeck-provision-broker.py clear-boots" in clear["ExecStart"][0]
    assert record["Before"] == ["astrodeck-provision.service network-pre.target"]
    assert record["WantedBy"] == ["network-pre.target"]
    assert "network-pre.target.wants/astrodeck-recovery-record.service" in installer
    timer = _unit_values(CLEAR_TIMER)
    assert timer["OnBootSec"] == ["60s"]
    assert timer["Persistent"] == ["no"]


def test_frontend_waits_for_the_hotspot_address_before_binding(prov, monkeypatch):
    import errno

    monkeypatch.setattr(prov.time, "sleep", lambda _s: None)
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError(errno.EADDRNOTAVAIL, "address not yet assigned")
        return "server"

    assert prov._bind_when_addressed(factory, "HTTP") == "server"
    assert len(attempts) == 3


def test_frontend_bind_gives_up_at_the_deadline_and_on_other_errors(prov, monkeypatch):
    import errno

    monkeypatch.setattr(prov.time, "sleep", lambda _s: None)
    monkeypatch.setattr(prov, "ADDRESS_WAIT_S", 0.0)

    def never():
        raise OSError(errno.EADDRNOTAVAIL, "never")

    with pytest.raises(OSError):
        prov._bind_when_addressed(never, "HTTP")
    monkeypatch.setattr(prov, "ADDRESS_WAIT_S", 20.0)
    calls = []

    def denied():
        calls.append(1)
        raise OSError(errno.EACCES, "denied")

    with pytest.raises(OSError):
        prov._bind_when_addressed(denied, "HTTP")
    assert len(calls) == 1
