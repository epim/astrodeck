"""Target-state contract for Orange Pi first-boot Wi-Fi provisioning.

The tests are host-safe: they never touch networking, netplan, systemd, or the
real appliance state paths.  Hardware-only checks remain in the execution doc.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROVISION_DIR = ROOT / "orangepi5" / "provision"
PROVISIONER = PROVISION_DIR / "astrodeck-provision.py"
BROKER = PROVISION_DIR / "astrodeck-provision-broker.py"
SERVICE = PROVISION_DIR / "astrodeck-provision.service"
BROKER_SERVICE = PROVISION_DIR / "astrodeck-provision-broker.service"
BROKER_SOCKET = PROVISION_DIR / "astrodeck-provision-broker.socket"
SYSUSERS = PROVISION_DIR / "astrodeck-provision.sysusers.conf"
ROOTFS_INSTALLER = ROOT / "orangepi5" / "provision" / "install-to-rootfs.sh"
RECOVERY_RECORD_SERVICE = (
    ROOT / "orangepi5" / "provision" / "astrodeck-recovery-record.service"
)
RECOVERY_CLEAR_SERVICE = (
    ROOT / "orangepi5" / "provision" / "astrodeck-recovery-clear.service"
)
RECOVERY_CLEAR_TIMER = (
    ROOT / "orangepi5" / "provision" / "astrodeck-recovery-clear.timer"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # exec_module() does not perform importlib's normal sys.modules insertion.
    # Register the module so inspect.getsource() can resolve class definitions.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_provisioner():
    return _load(PROVISIONER, "astrodeck_security_acceptance_provision")


def _load_broker():
    return _load(BROKER, "astrodeck_security_acceptance_broker")


def _require(module: Any, name: str):
    value = getattr(module, name, None)
    if value is None:
        pytest.fail(
            f"Orange Pi security contract is missing {name}(); "
            "implement the execution design before changing this test"
        )
    return value


def _field(value: Any, name: str):
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def test_no_shared_factory_password_and_no_secret_value_is_logged():
    weak_secret_assignments: list[str] = []
    leaking_calls: list[str] = []
    for path in (PROVISIONER, BROKER):
        source = path.read_text(encoding="utf-8")
        assert not re.search(
            r"(?m)^\s*AP_PSK\s*=\s*(['\"])astrodeck\1", source, re.IGNORECASE
        ), "a public password must not unlock every shipped appliance"
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                names = [
                    target.id.lower()
                    for target in targets
                    if isinstance(target, ast.Name)
                ]
                if (
                    any(
                        part in name
                        for name in names
                        for part in ("psk", "password", "secret")
                    )
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and value.value.lower()
                    in {"astrodeck", "password", "changeme", "default"}
                ):
                    weak_secret_assignments.append(f"{path.name}: {ast.unparse(node)}")
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "log":
                continue
            for formatted in ast.walk(node.args[0]):
                if isinstance(formatted, ast.FormattedValue):
                    expression = ast.unparse(formatted.value).lower()
                    if "psk" in expression or "password" in expression:
                        leaking_calls.append(f"{path.name}: {ast.unparse(node)}")
    assert not weak_secret_assignments, (
        f"shared/default setup secret remains: {weak_secret_assignments}"
    )
    assert not leaking_calls, f"setup secret is interpolated into logs: {leaking_calls}"

    frontend = _load_provisioner()
    broker = _load_broker()
    ap_up = _require(broker, "ap_up")
    assert list(inspect.signature(ap_up).parameters)[:2] == ["ssid", "psk"]
    ap_tree = ast.parse(inspect.getsource(ap_up))
    for call in (node for node in ast.walk(ap_tree) if isinstance(node, ast.Call)):
        called = ast.unparse(call.func).lower()
        arguments = " ".join(ast.unparse(arg).lower() for arg in call.args)
        if called == "run":
            assert "psk" not in arguments and "password" not in arguments, (
                "setup credential was placed in a subprocess argv that is logged/visible"
            )
    assert "AP_PSK" not in inspect.getsource(frontend.main_run)
    assert "setup-identity.json" not in PROVISIONER.read_text(encoding="utf-8")


def test_setup_password_format_and_generation_are_per_device():
    provisioner = _load_broker()
    generate = _require(provisioner, "generate_setup_password")
    generator_source = inspect.getsource(generate)
    assert "secrets.choice" in generator_source
    assert not re.search(r"(?i)(serial|mac|uuid|getrandbits|random\.)", generator_source)

    generated = {generate() for _ in range(128)}
    assert len(generated) == 128
    assert all(re.fullmatch(r"[a-z0-9]{4}(?:-[a-z0-9]{4}){2}", p) for p in generated)
    assert "astrodeck" not in generated


def test_commissioned_identity_is_exclusive_private_and_stable(tmp_path: Path):
    provisioner = _load_broker()
    commission = _require(provisioner, "commission_setup_identity")
    load = _require(provisioner, "load_setup_identity")

    first_path = tmp_path / "unit-a" / "setup-identity.json"
    second_path = tmp_path / "unit-b" / "setup-identity.json"
    first = commission(first_path, ssid="AstroDeck-1001", now=1000.0)
    second = commission(second_path, ssid="AstroDeck-1002", now=1000.0)

    assert _field(first, "schema") == 1
    assert _field(first, "setup_ssid") == "AstroDeck-1001"
    assert _field(first, "generation") == 1
    assert _field(first, "factory_psk") == _field(first, "active_psk")
    assert _field(first, "factory_psk") != _field(second, "factory_psk")
    authorization = _field(first, "ap_authorization")
    assert _field(authorization, "armed") is True
    assert _field(authorization, "reason") == "factory"
    assert _field(load(first_path), "factory_psk") == _field(first, "factory_psk")

    with pytest.raises(FileExistsError):
        commission(first_path, ssid="AstroDeck-1001", now=1001.0)

    if os.name == "posix":
        assert stat.S_IMODE(first_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(first_path.stat().st_mode) == 0o600

        first_path.chmod(0o644)
        with pytest.raises((PermissionError, ValueError, RuntimeError, OSError)):
            load(first_path)


def test_identity_loader_fails_closed_for_missing_malformed_or_symlinked_state(
    tmp_path: Path,
):
    provisioner = _load_broker()
    load = _require(provisioner, "load_setup_identity")

    with pytest.raises((FileNotFoundError, ValueError, RuntimeError, OSError)):
        load(tmp_path / "missing.json")

    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"schema": 1, "factory_psk": "astrodeck"}', encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        load(malformed)

    non_regular = tmp_path / "directory.json"
    non_regular.mkdir()
    with pytest.raises((ValueError, RuntimeError, OSError)):
        load(non_regular)

    victim = tmp_path / "victim.json"
    victim.write_text("do not read as an identity", encoding="utf-8")
    link = tmp_path / "identity-link.json"
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a file symlink")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        load(link)


def test_ap_authorization_is_one_shot_and_portal_is_time_bounded(tmp_path: Path):
    broker = _load_broker()
    frontend = _load_provisioner()
    commission = _require(broker, "commission_setup_identity")
    consume = _require(broker, "consume_ap_authorization")

    identity_path = tmp_path / "setup-identity.json"
    commission(identity_path, ssid="AstroDeck-BEEF", now=1000.0)

    assert consume(identity_path, now=1001.0) is True
    assert consume(identity_path, now=1002.0) is False
    assert int(_require(broker, "PORTAL_LIFETIME_S")) == 15 * 60
    assert int(_require(frontend, "PORTAL_LIFETIME_S")) == 15 * 60

    broker_source = inspect.getsource(broker.BrokerState.open_window)
    assert "consume_ap_authorization" in broker_source
    assert "PORTAL_LIFETIME_S" in broker_source
    frontend_source = inspect.getsource(frontend.main_run)
    assert 'opened["remaining_s"]' in frontend_source
    assert "consume_ap_authorization" not in frontend_source


def test_three_distinct_short_boots_rearm_the_printed_factory_credential(
    tmp_path: Path,
):
    provisioner = _load_broker()
    commission = _require(provisioner, "commission_setup_identity")
    consume = _require(provisioner, "consume_ap_authorization")
    rotate = _require(provisioner, "rotate_setup_password")
    record = _require(provisioner, "record_short_boot")
    load = _require(provisioner, "load_setup_identity")

    identity_path = tmp_path / "setup-identity.json"
    original = commission(identity_path, ssid="AstroDeck-BEEF", now=1000.0)
    factory_psk = _field(original, "factory_psk")
    original_generation = int(_field(original, "generation"))
    assert consume(identity_path, now=1001.0) is True

    rotated = rotate(identity_path, now=1010.0)
    assert rotated != factory_psk
    assert consume(identity_path, now=1011.0) is False
    after_rotation = load(identity_path)
    assert _field(after_rotation, "factory_psk") == factory_psk
    assert _field(after_rotation, "active_psk") == rotated
    assert int(_field(after_rotation, "generation")) == original_generation + 1

    assert record(identity_path, boot_id="boot-a", now=1100.0) is False
    assert record(identity_path, boot_id="boot-a", now=1101.0) is False
    assert record(identity_path, boot_id="boot-b", now=1110.0) is False
    assert record(identity_path, boot_id="boot-c", now=1120.0) is True

    recovered = load(identity_path)
    assert _field(recovered, "active_psk") == factory_psk
    assert int(_field(recovered, "generation")) == original_generation + 2
    assert consume(identity_path, now=1121.0) is True
    assert consume(identity_path, now=1122.0) is False


def test_recovery_sequence_expires_and_a_healthy_boot_clears_it(tmp_path: Path):
    provisioner = _load_broker()
    commission = _require(provisioner, "commission_setup_identity")
    consume = _require(provisioner, "consume_ap_authorization")
    record = _require(provisioner, "record_short_boot")
    clear = _require(provisioner, "clear_short_boot_counter")

    assert int(_require(provisioner, "SHORT_BOOT_MAX_UPTIME_S")) == 60
    assert int(_require(provisioner, "RECOVERY_SEQUENCE_WINDOW_S")) == 180

    identity_path = tmp_path / "setup-identity.json"
    commission(identity_path, ssid="AstroDeck-BEEF", now=1000.0)
    assert consume(identity_path, now=1001.0) is True

    assert record(identity_path, boot_id="old-a", now=1100.0) is False
    assert record(identity_path, boot_id="new-a", now=1281.0) is False
    assert record(identity_path, boot_id="new-b", now=1282.0) is False
    assert record(identity_path, boot_id="new-c", now=1283.0) is True
    assert consume(identity_path, now=1284.0) is True

    assert record(identity_path, boot_id="clear-a", now=1300.0) is False
    clear(identity_path)
    assert record(identity_path, boot_id="clear-b", now=1301.0) is False
    assert record(identity_path, boot_id="clear-c", now=1302.0) is False
    assert record(identity_path, boot_id="clear-d", now=1303.0) is True


def test_portal_exposes_no_network_recovery_or_credential_route():
    provisioner = _load_provisioner()
    portal_source = inspect.getsource(provisioner.Portal).lower()
    for forbidden in (
        "/recover",
        "/recovery",
        "/factory-reset",
        "factory_psk",
        "active_psk",
        "setup_password",
    ):
        assert forbidden not in portal_source


def _unit_values(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key, []).append(value)
    return values


def test_network_frontend_is_non_root_and_has_no_system_manager_bus_access():
    values = _unit_values(SERVICE.read_text(encoding="utf-8"))

    assert values.get("User") == ["astrodeck-setup"]
    assert values.get("Group") == ["astrodeck-setup"]
    assert values.get("RequiresMountsFor") == ["/data"]
    assert values.get("ConditionPathIsMountPoint") == ["/data"]
    assert values.get("Requires") == [BROKER_SOCKET.name]
    assert values.get("UMask") == ["0077"]
    assert values.get("NoNewPrivileges") in (["yes"], ["true"])
    assert values.get("ProtectSystem") == ["strict"]
    assert values.get("ProtectHome") in (["yes"], ["true"])
    assert values.get("PrivateTmp") in (["yes"], ["true"])
    assert values.get("PrivateDevices") in (["yes"], ["true"])
    assert values.get("ProtectProc") == ["invisible"]
    assert values.get("ProcSubset") == ["pid"]
    for key in (
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "ProtectClock",
        "ProtectHostname",
        "RestrictSUIDSGID",
        "RestrictRealtime",
        "LockPersonality",
        "MemoryDenyWriteExecute",
    ):
        assert values.get(key) in (["yes"], ["true"]), f"missing {key}"
    assert values.get("RestrictNamespaces") in (["yes"], ["true"])
    assert values.get("SystemCallArchitectures") == ["native"]
    assert values.get("LimitCORE") == ["0"]
    assert values.get("TasksMax") == ["64"]
    assert values.get("LimitNOFILE") == ["128"]

    assert values.get("CapabilityBoundingSet") == ["CAP_NET_BIND_SERVICE"]
    assert values.get("AmbientCapabilities") == ["CAP_NET_BIND_SERVICE"]

    families = set(" ".join(values.get("RestrictAddressFamilies", [])).split())
    assert families == {"AF_UNIX", "AF_INET", "AF_INET6"}

    assert "ReadWritePaths" not in values
    inaccessible = set(" ".join(values.get("InaccessiblePaths", [])).split())
    assert "-/run/systemd/private" in inaccessible
    assert "-/run/dbus/system_bus_socket" in inaccessible
    assert values.get("SocketBindDeny") == ["any"]
    # One rule per line: systemd rejects a space-separated SocketBindAllow=
    # and the deny-all then leaves the portal unable to bind 80 or 53.
    bind_allow = values.get("SocketBindAllow", [])
    assert all(" " not in rule for rule in bind_allow), bind_allow
    assert set(bind_allow) == {"ipv4:tcp:80", "ipv4:udp:53"}
    assert values.get("IPAddressDeny") == ["any"]
    assert values.get("IPAddressAllow") == ["10.42.0.0/24"]

    filters = " ".join(values.get("SystemCallFilter", []))
    for denied_group in (
        "@clock",
        "@cpu-emulation",
        "@debug",
        "@module",
        "@mount",
        "@obsolete",
        "@privileged",
        "@raw-io",
        "@reboot",
        "@swap",
    ):
        assert denied_group in filters


def test_frontend_cannot_invoke_privileged_commands_or_read_private_identity():
    source = PROVISIONER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "subprocess" not in imported
    for forbidden in (
        "systemctl",
        "networkctl",
        "netplan",
        "wpa_supplicant",
        "/etc/netplan",
        "/run/systemd/network",
        "setup-identity.json",
    ):
        assert forbidden not in source


def test_root_broker_is_socket_activated_and_has_a_fixed_protocol():
    service = _unit_values(BROKER_SERVICE.read_text(encoding="utf-8"))
    socket_values = _unit_values(BROKER_SOCKET.read_text(encoding="utf-8"))
    assert service.get("User") == ["root"]
    assert service.get("Group") == ["root"]
    assert service.get("ExecStart") == [
        "/usr/bin/python3 /usr/local/lib/astrodeck/"
        "astrodeck-provision-broker.py broker"
    ]
    assert service.get("CapabilityBoundingSet") == ["CAP_NET_ADMIN CAP_NET_RAW"]
    writable = set(" ".join(service.get("ReadWritePaths", [])).split())
    assert writable == {
        "/run/astrodeck",
        "/run/systemd/network",
        "/etc/netplan",
        "/data/astrodeck",
    }
    assert socket_values.get("ListenStream") == [
        "/run/astrodeck-provision-broker.sock"
    ]
    assert socket_values.get("Accept") == ["no"]
    assert socket_values.get("SocketUser") == ["root"]
    assert socket_values.get("SocketGroup") == ["astrodeck-setup"]
    assert socket_values.get("SocketMode") == ["0660"]
    assert "astrodeck-setup" in SYSUSERS.read_text(encoding="utf-8")

    broker = _load_broker()
    assert broker.BROKER_OPERATIONS == frozenset({"open", "scan", "join", "close"})
    valid_join = {
        "version": 1,
        "operation": "join",
        "ssid": "home",
        "psk": "12345678",
        "sae": False,
    }
    for request in (
        {"version": 1, "operation": "systemctl"},
        {"version": 1, "operation": "run", "command": "id"},
        {"version": 1, "operation": "open", "path": "/etc/shadow"},
        {"version": 1, "operation": "close", "unit": "evil.service"},
        {**valid_join, "argv": ["sh", "-c", "id"]},
        {**valid_join, "path": "/tmp/attacker.yaml"},
    ):
        with pytest.raises(broker.BrokerRequestError):
            broker.validate_broker_request(request)


def test_root_broker_keeps_the_measured_root_sandbox():
    """The A4 sandbox followed the root identity from the portal unit into the
    broker; the contract has to follow it too, because the root process is the
    one whose escape matters. The portal test above covers the non-root side."""
    values = _unit_values(BROKER_SERVICE.read_text(encoding="utf-8"))

    assert values.get("User") == ["root"]
    assert values.get("RequiresMountsFor") == ["/data"]
    assert values.get("ConditionPathIsMountPoint") == ["/data"]
    assert values.get("UMask") == ["0077"]
    assert values.get("NoNewPrivileges") in (["yes"], ["true"])
    assert values.get("ProtectSystem") == ["strict"]
    assert values.get("ProtectHome") in (["yes"], ["true"])
    assert values.get("PrivateTmp") in (["yes"], ["true"])
    assert values.get("ProtectProc") == ["invisible"]
    assert values.get("RuntimeDirectory") == ["astrodeck"]
    assert values.get("RuntimeDirectoryMode") == ["0700"]
    for key in (
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "ProtectClock",
        "ProtectHostname",
        "RestrictSUIDSGID",
        "RestrictRealtime",
        "RestrictNamespaces",
        "LockPersonality",
        "MemoryDenyWriteExecute",
    ):
        assert values.get(key) in (["yes"], ["true"]), key
    assert values.get("SystemCallArchitectures") == ["native"]
    assert values.get("LimitCORE") == ["0"]
    assert values.get("TasksMax") == ["64"]
    assert values.get("LimitNOFILE") == ["128"]
    assert values.get("KillMode") == ["control-group"]
    # A4: rfkill may need device access, so the root side must not gain
    # PrivateDevices by a copy-paste from the portal unit.
    assert "PrivateDevices" not in values

    families = set(" ".join(values.get("RestrictAddressFamilies", [])).split())
    assert families == {"AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK", "AF_PACKET"}

    filters = " ".join(values.get("SystemCallFilter", []))
    for denied_group in (
        "@clock",
        "@cpu-emulation",
        "@debug",
        "@module",
        "@mount",
        "@obsolete",
        "@raw-io",
        "@reboot",
        "@swap",
    ):
        assert denied_group in filters

    writable = " ".join(values.get("ReadWritePaths", [])).split()
    assert "/run" not in writable, "do not grant the root broker all of /run"


def test_hotspot_network_file_is_readable_by_networkd_and_the_portal_waits_for_its_address():
    """Two defects only the board could show (2026-09-03): the networkd
    drop-in was 0600, so networkd never assigned the hotspot address; and the
    portal bound that address the instant the beacon started."""
    broker = _load_broker()
    frontend = _load_provisioner()
    ap_up = inspect.getsource(broker.ap_up)
    assert re.search(r"_write_private\(\s*NETWORKD_PATH,.*?mode=0o644\)", ap_up, re.S)
    assert BROKER.read_text(encoding="utf-8").count("mode=0o644") == 1
    assert "_bind_when_addressed" in inspect.getsource(frontend.main_run)
    assert "EADDRNOTAVAIL" in inspect.getsource(frontend._bind_when_addressed)


def test_recovery_units_make_service_restart_insufficient_for_recovery():
    for path in (
        RECOVERY_RECORD_SERVICE,
        RECOVERY_CLEAR_SERVICE,
        RECOVERY_CLEAR_TIMER,
    ):
        assert path.is_file(), f"missing recovery unit {path.name}"

    record = _unit_values(RECOVERY_RECORD_SERVICE.read_text(encoding="utf-8"))
    clear_service = _unit_values(
        RECOVERY_CLEAR_SERVICE.read_text(encoding="utf-8")
    )
    clear_timer = _unit_values(RECOVERY_CLEAR_TIMER.read_text(encoding="utf-8"))

    assert record.get("Type") == ["oneshot"]
    assert record.get("User") == ["root"]
    assert record.get("ConditionPathIsMountPoint") == ["/data"]
    assert "astrodeck-provision.service" in " ".join(record.get("Before", []))
    assert "network-pre.target" in " ".join(record.get("Before", []))
    assert record.get("WantedBy") == ["network-pre.target"]
    assert any("record" in value and "boot" in value for value in record.get("ExecStart", []))
    assert clear_service.get("Type") == ["oneshot"]
    assert clear_service.get("ConditionPathIsMountPoint") == ["/data"]
    assert any("clear" in value and "boot" in value for value in clear_service.get("ExecStart", []))
    assert clear_timer.get("OnBootSec") == ["60s"]
    assert clear_timer.get("Unit") == [RECOVERY_CLEAR_SERVICE.name]
    assert clear_timer.get("Persistent") not in (["yes"], ["true"]), (
        "a persistent timer could clear state immediately after a short powered-off boot"
    )
    assert "timers.target" in " ".join(clear_timer.get("WantedBy", []))

    installer = ROOTFS_INSTALLER.read_text(encoding="utf-8")
    for unit in (RECOVERY_RECORD_SERVICE.name, RECOVERY_CLEAR_TIMER.name):
        assert unit in installer, f"rootfs installer never enables {unit}"
    assert (
        f"network-pre.target.wants/{RECOVERY_RECORD_SERVICE.name}" in installer
    ), "the short-boot recorder is enabled too late to precede network setup"
