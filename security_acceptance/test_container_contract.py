"""Target-state contract for non-root processes and hardened containers."""

from __future__ import annotations

import ast
import inspect
import os
import platform
import re
import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any

import pytest

try:
    import yaml
except ImportError:  # keep an ordinary (skipped) root pytest collection harmless
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
PROXY_COMPOSE = ROOT / "deploy" / "reverse-proxy" / "docker-compose.yml"
RELAY_PROXY_COMPOSE = (
    ROOT / "deploy" / "reverse-proxy" / "docker-compose.relay.yml"
)
DOCKERFILE = ROOT / "Dockerfile"
RELAY_DOCKERFILE = ROOT / "relay" / "Dockerfile"
ASTRODECK_SYSTEMD = ROOT / "deploy" / "systemd" / "astrodeck.service"
RELAY_SYSTEMD = ROOT / "deploy" / "systemd" / "astrodeck-relay.service"
HARDWARE_SYSTEMD = (
    ROOT / "deploy" / "systemd" / "astrodeck-hardware.conf.example"
)
USB_COMPOSE = ROOT / "docker-compose.usb.yml"


def _compose(path: Path = COMPOSE) -> dict[str, Any]:
    if yaml is None:
        pytest.fail("PyYAML is required to run the deployment contract tests")
    if not path.is_file():
        pytest.fail(f"missing required Compose profile: {path.relative_to(ROOT)}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _sequence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _mount_targets(service: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for mount in service.get("volumes", []) or []:
        if isinstance(mount, str):
            parts = mount.split(":")
            if len(parts) >= 2:
                targets.append(parts[1])
        elif isinstance(mount, dict):
            targets.append(str(mount.get("target", "")))
    return targets


def _environment(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment", {}) or {}
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    result: dict[str, str] = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        result[key] = value
    return result


def _network_ip(service: dict[str, Any], network: str) -> str:
    attached = service.get("networks", {}) or {}
    if not isinstance(attached, dict) or not isinstance(attached.get(network), dict):
        return ""
    return str(attached[network].get("ipv4_address", ""))


def _unit_values(path: Path) -> dict[str, list[str]]:
    assert path.is_file(), f"missing bare-metal service policy: {path.relative_to(ROOT)}"
    values: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key, []).append(value)
    return values


def _call_line(source: str, name: str) -> int:
    tree = ast.parse(textwrap.dedent(source))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = ""
        if isinstance(node.func, ast.Name):
            called = node.func.id
        elif isinstance(node.func, ast.Attribute):
            called = node.func.attr
        if called == name:
            lines.append(node.lineno)
    assert lines, f"missing executable {name}() call"
    return min(lines)


def _import_line(source: str, module: str) -> int:
    tree = ast.parse(textwrap.dedent(source))
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            (isinstance(node, ast.Import) and any(alias.name == module for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == module)
        )
    ]
    assert lines, f"missing executable import of {module}"
    return min(lines)


def _assert_hardened_service(
    name: str,
    service: dict[str, Any],
    *,
    expected_user: str,
    expected_pids: int,
    expected_grace: str,
) -> None:
    assert str(service.get("user")) == expected_user, f"{name} runtime UID/GID"
    assert service.get("read_only") is True, f"{name} root filesystem"
    assert {v.upper() for v in _sequence(service.get("cap_drop"))} == {"ALL"}
    security_opt = {v.replace("=", ":").lower() for v in _sequence(service.get("security_opt"))}
    assert security_opt == {"no-new-privileges:true"}
    assert service.get("init") is True
    assert service.get("privileged") not in (True, "true", "True")
    assert service.get("network_mode") != "host"
    assert service.get("pid") != "host"
    assert service.get("ipc") != "host"
    assert service.get("uts") != "host"
    assert service.get("cgroup") != "host"
    assert service.get("userns") != "host"
    assert not service.get("cap_add")
    assert not service.get("devices")
    assert not service.get("device_cgroup_rules")
    assert not service.get("volumes_from")
    assert int(service.get("pids_limit", 0)) == expected_pids
    assert str(service.get("stop_signal", "")).upper() == "SIGTERM"
    assert str(service.get("stop_grace_period", "")).lower() == expected_grace
    assert service.get("restart") == "unless-stopped"
    assert service.get("oom_kill_disable") not in (True, "true", "True")

    tmpfs = "\n".join(_sequence(service.get("tmpfs"))).lower()
    assert "/tmp" in tmpfs and "noexec" in tmpfs and "nosuid" in tmpfs and "nodev" in tmpfs
    assert "size=128m" in tmpfs and "mode=1777" in tmpfs

    mounts = _mount_targets(service)
    assert "/var/run/docker.sock" not in mounts
    assert "/run/docker.sock" not in mounts


def _assert_bounded_logging(name: str, service: dict[str, Any]) -> None:
    logging = service.get("logging", {}) or {}
    assert logging.get("driver") == "local", f"{name} log driver must rotate"
    options = logging.get("options", {}) or {}
    assert str(options.get("max-size", "")).lower() == "10m"
    assert str(options.get("max-file", "")) == "5"


def test_compose_hardens_app_and_proxy_and_only_publishes_the_edge():
    assert PROXY_COMPOSE.is_file(), "missing supported reverse-proxy Compose stack"
    compose = _compose(PROXY_COMPOSE)
    assert compose.get("name") == "astrodeck-edge"
    services = compose.get("services", {})
    assert {"astrodeck", "proxy"} <= set(services)

    app = services["astrodeck"]
    proxy = services["proxy"]
    _assert_hardened_service(
        "astrodeck",
        app,
        expected_user="10001:10001",
        expected_pids=256,
        expected_grace="90s",
    )
    _assert_hardened_service(
        "proxy", proxy, expected_user="101:101", expected_pids=64, expected_grace="30s"
    )
    _assert_bounded_logging("astrodeck", app)
    _assert_bounded_logging("proxy", proxy)

    assert not app.get("ports"), "publishing 8800 bypasses TLS and edge limits"
    assert any(str(port).split("/")[0] == "8800" for port in _sequence(app.get("expose")))
    assert proxy.get("ports"), "the edge is the sole host-published listener"

    networks = compose.get("networks", {})
    internal = [
        name
        for name, cfg in networks.items()
        if isinstance(cfg, dict) and bool(cfg.get("internal"))
    ]
    assert len(internal) == 1
    network = internal[0]
    app_networks = app.get("networks", {}) or {}
    # Outbound integrations need one project-private egress bridge, while all
    # inbound proxy traffic remains on the exact internal backend.
    egress = set(app_networks) - {network}
    assert len(egress) == 1, "app needs exactly one dedicated outbound bridge"
    egress_network = next(iter(egress))
    egress_cfg = networks.get(egress_network, {}) or {}
    assert not bool(egress_cfg.get("internal"))
    assert not bool(egress_cfg.get("external")), (
        "the controller egress bridge must remain project-private"
    )
    for service_name, service in services.items():
        if service_name != "astrodeck":
            assert egress_network not in (service.get("networks", {}) or {}), (
                "the outbound bridge must be private to the controller"
            )
    assert network in (proxy.get("networks", {}) or {})
    proxy_ip = _network_ip(proxy, network)
    app_ip = _network_ip(app, network)
    assert proxy_ip and app_ip and proxy_ip != app_ip
    app_environment = _environment(app)
    assert app_environment.get("ASTRODECK_FORWARDED_ALLOW_IPS") == proxy_ip
    assert app_environment.get("ASTRODECK_REQUIRE_AUTH", "").lower() in {
        "1",
        "true",
        "yes",
    }
    assert app_environment.get("ASTRODECK_UVICORN_ACCESS_LOG", "").lower() in {
        "0",
        "false",
        "no",
    }

    keys = set(app_environment)
    assert "ASTRODECK_TOKEN" not in keys, (
        "the legacy transport token is not a usable SPA bootstrap and is exposed "
        "by container metadata; bootstrap a named local admin instead"
    )


def test_relay_has_a_separate_hardened_edge_stack_with_exact_proxy_trust():
    assert RELAY_PROXY_COMPOSE.is_file(), "missing supported relay proxy Compose stack"
    compose = _compose(RELAY_PROXY_COMPOSE)
    assert compose.get("name") == "astrodeck-relay-edge"
    services = compose.get("services", {})
    assert {"relay", "proxy"} <= set(services)

    relay = services["relay"]
    proxy = services["proxy"]
    _assert_hardened_service(
        "relay",
        relay,
        expected_user="10001:10001",
        expected_pids=128,
        expected_grace="30s",
    )
    _assert_hardened_service(
        "proxy", proxy, expected_user="101:101", expected_pids=64, expected_grace="30s"
    )
    _assert_bounded_logging("relay", relay)
    _assert_bounded_logging("proxy", proxy)
    assert not relay.get("ports")
    assert any(str(port).split("/")[0] == "8080" for port in _sequence(relay.get("expose")))
    assert proxy.get("ports")

    internal = [
        name
        for name, cfg in (compose.get("networks", {}) or {}).items()
        if isinstance(cfg, dict) and bool(cfg.get("internal"))
    ]
    assert len(internal) == 1
    network = internal[0]
    relay_networks = relay.get("networks", {}) or {}
    assert set(relay_networks) == {network}, (
        "relay must attach only to the internal proxy backend"
    )
    assert network in (proxy.get("networks", {}) or {})
    proxy_ip = _network_ip(proxy, network)
    relay_ip = _network_ip(relay, network)
    assert proxy_ip and relay_ip and proxy_ip != relay_ip
    relay_environment = _environment(relay)
    assert relay_environment.get("RELAY_FORWARDED_ALLOW_IPS") == proxy_ip
    assert relay_environment.get("RELAY_UVICORN_ACCESS_LOG", "").lower() in {
        "0",
        "false",
        "no",
    }
    assert relay_environment.get("RELAY_DEVICE_TOKENS_FILE") == (
        "/run/secrets/device_tokens"
    )
    assert "RELAY_DEVICE_TOKENS" not in relay_environment
    attached_secrets = relay.get("secrets", []) or []
    assert len(attached_secrets) == 1
    mounted_secret = attached_secrets[0]
    assert isinstance(mounted_secret, dict), (
        "the relay secret needs explicit target ownership and mode"
    )
    assert mounted_secret.get("source") == "device_tokens"
    assert mounted_secret.get("target") == "device_tokens"
    assert str(mounted_secret.get("uid")) == "10001"
    assert str(mounted_secret.get("gid")) == "10001"
    mode = mounted_secret.get("mode")
    assert mode == 0o400 or str(mode) == "0400"

    top_secret = (compose.get("secrets", {}) or {}).get("device_tokens")
    assert top_secret == {"environment": "RELAY_DEVICE_TOKENS_JSON"}, (
        "file-backed Compose secrets ignore uid/gid/mode and strand a 0400 "
        "secret as root; use the environment-backed secret provider without "
        "adding the value to service metadata"
    )


def test_compose_persistent_mounts_are_explicit_and_only_expected_paths_are_writable():
    app = _compose(PROXY_COMPOSE)["services"]["astrodeck"]
    mounts = app.get("volumes", []) or []
    by_target: dict[str, Any] = {}
    for mount in mounts:
        if isinstance(mount, dict):
            by_target[str(mount.get("target"))] = mount
        else:
            pytest.fail("use long mount syntax so read/write intent is reviewable")

    assert set(by_target) == {"/data/config", "/data/captures"}
    for target, mount in by_target.items():
        assert mount.get("read_only") is not True, f"{target} must remain writable"
        assert mount.get("type") == "volume", (
            "the supported rootless profile uses named volumes; put rootful SSD "
            "bind mounts in the separate documented override"
        )

    for compose_path, expected_template, forbidden_template, public_variable in (
        (
            PROXY_COMPOSE,
            "astrodeck.conf.template",
            "relay.conf.template",
            "ASTRODECK_PUBLIC_HOST",
        ),
        (
            RELAY_PROXY_COMPOSE,
            "relay.conf.template",
            "astrodeck.conf.template",
            "RELAY_PUBLIC_HOST",
        ),
    ):
        proxy = _compose(compose_path)["services"]["proxy"]
        sources: list[str] = []
        targets: list[str] = []
        for mount in proxy.get("volumes", []) or []:
            assert isinstance(mount, dict), "proxy mounts use reviewable long syntax"
            assert mount.get("read_only") is True, (
                f"proxy config/certificate mount {mount.get('target')} must be read-only"
            )
            assert mount.get("type") == "bind"
            assert (mount.get("bind") or {}).get("create_host_path") is False
            sources.append(str(mount.get("source", "")))
            targets.append(str(mount.get("target", "")))
        joined_sources = "\n".join(sources)
        assert expected_template in joined_sources
        assert forbidden_template not in joined_sources
        assert "/etc/nginx/nginx.conf" in targets
        assert "/etc/nginx/site.conf.template" in targets
        assert "/certs/fullchain.pem" in targets
        assert "/certs/privkey.pem" in targets
        assert "/etc/nginx/conf.d" not in targets
        entrypoint = " ".join(_sequence(proxy.get("entrypoint")))
        assert "/bin/sh" in entrypoint and "-e" in entrypoint
        command = " ".join(_sequence(proxy.get("command")))
        assert "envsubst" in command
        assert f"${{{public_variable}}}" in command
        assert not re.search(r"envsubst\s*(?:<|$)", command)
        assert "/tmp/site.conf" in command
        assert "nginx -t" in command
        assert "exec nginx" in command


def test_direct_development_compose_is_hardened_and_binds_only_loopback():
    app = _compose()["services"]["astrodeck"]
    _assert_hardened_service(
        "astrodeck",
        app,
        expected_user="10001:10001",
        expected_pids=256,
        expected_grace="90s",
    )
    _assert_bounded_logging("astrodeck", app)
    for published in app.get("ports", []) or []:
        if isinstance(published, str):
            assert published.startswith("127.0.0.1:") or published.startswith("[::1]:")
        elif isinstance(published, dict):
            assert str(published.get("host_ip")) in {"127.0.0.1", "::1"}
        else:
            pytest.fail("direct-development port must visibly name a loopback host IP")


def _last_user(dockerfile: Path) -> str:
    users = re.findall(
        r"(?im)^\s*USER\s+([^\s#]+(?:\s*:\s*[^\s#]+)?)", dockerfile.read_text(encoding="utf-8")
    )
    assert users, f"{dockerfile} has no USER"
    return users[-1].replace(" ", "")


def test_runtime_images_pin_numeric_non_root_uid_and_gid():
    assert _last_user(DOCKERFILE) == "10001:10001"
    assert _last_user(RELAY_DOCKERFILE) == "10001:10001"

    for dockerfile in (DOCKERFILE, RELAY_DOCKERFILE):
        source = dockerfile.read_text(encoding="utf-8")
        assert re.search(r"groupadd\b[^\n]*--gid\s+10001\b", source)
        assert re.search(r"useradd\b[^\n]*--uid\s+10001\b[^\n]*--gid\s+10001\b", source)


def test_bare_metal_systemd_units_use_dedicated_unprivileged_identities():
    for path, user, module, state_root, port in (
        (ASTRODECK_SYSTEMD, "astrodeck", "astrodeck", "/var/lib/astrodeck", "8800"),
        (
            RELAY_SYSTEMD,
            "astrodeck-relay",
            "relay",
            "/var/lib/astrodeck-relay",
            "8080",
        ),
    ):
        values = _unit_values(path)
        assert values.get("User") == [user]
        assert values.get("Group") == [user]
        assert user not in {"root", "0"}
        assert values.get("UMask") == ["0077"]
        assert values.get("NoNewPrivileges") in (["yes"], ["true"])
        assert values.get("ProtectSystem") == ["strict"]
        assert values.get("ProtectHome") in (["yes"], ["true"])
        assert values.get("PrivateTmp") in (["yes"], ["true"])
        assert values.get("PrivateDevices") in (["yes"], ["true"])
        assert values.get("CapabilityBoundingSet") == [""]
        assert values.get("AmbientCapabilities") == [""]
        assert values.get("RestrictNamespaces") in (["yes"], ["true"])
        assert values.get("RestrictSUIDSGID") in (["yes"], ["true"])
        assert values.get("LockPersonality") in (["yes"], ["true"])
        assert values.get("MemoryDenyWriteExecute") in (["yes"], ["true"])
        assert values.get("SystemCallArchitectures") == ["native"]
        assert int(values.get("TasksMax", ["0"])[0]) > 0
        assert int(values.get("LimitNOFILE", ["0"])[0]) > 0

        writable = " ".join(values.get("ReadWritePaths", []))
        assert state_root in writable
        assert "/etc" not in writable.split()
        assert "/opt" not in writable.split()

        start = " ".join(values.get("ExecStart", []))
        assert f"-m {module}" in start
        if module == "astrodeck":
            assert "--host 127.0.0.1" in start
            assert f"--port {port}" in start
            environment = " ".join(values.get("Environment", []))
            assert "ASTRODECK_REQUIRE_AUTH=true" in environment
        else:
            environment = " ".join(values.get("Environment", []))
            assert "RELAY_BIND_HOST=127.0.0.1" in environment
            assert f"RELAY_BIND_PORT={port}" in environment


def test_bare_metal_hardware_dropin_opens_only_enumerated_devices():
    values = _unit_values(HARDWARE_SYSTEMD)
    assert values.get("PrivateDevices") in (["no"], ["false"])
    assert values.get("DevicePolicy") == ["closed"]
    allowed = values.get("DeviceAllow", [])
    assert allowed
    assert all(value.endswith(" rw") for value in allowed)
    forbidden = {"/dev rw", "/dev/ rw", "char-* rw", "block-* rw"}
    assert not (set(allowed) & forbidden)
    assert values.get("CapabilityBoundingSet") in (None, [""])
    source = HARDWARE_SYSTEMD.read_text(encoding="utf-8")
    assert "CAP_SYS_ADMIN" not in source
    assert not re.search(r"(?im)^\s*User\s*=\s*(?:root|0)\s*$", source)


def test_container_usb_override_is_narrow_and_keeps_all_capabilities_dropped():
    compose = _compose(USB_COMPOSE)
    app = (compose.get("services", {}) or {}).get("astrodeck", {})
    assert app
    assert app.get("privileged") not in (True, "true", "True")
    assert not app.get("cap_add")
    if app.get("cap_drop") is not None:
        assert {value.upper() for value in _sequence(app.get("cap_drop"))} == {"ALL"}
    assert set(_sequence(app.get("device_cgroup_rules"))) == {"c 189:* rmw"}
    assert any("ASTRODECK_USB_GID:?" in value for value in _sequence(app.get("group_add")))

    usb_mounts = [
        mount
        for mount in app.get("volumes", []) or []
        if isinstance(mount, dict) and mount.get("target") == "/dev/bus/usb"
    ]
    assert len(usb_mounts) == 1
    mount = usb_mounts[0]
    assert mount.get("type") == "bind"
    assert mount.get("source") == "/dev/bus/usb"
    assert mount.get("read_only") is not True
    assert (mount.get("bind") or {}).get("create_host_path") is False


def test_documentation_never_recommends_privileged_containers():
    docs = [
        COMPOSE,
        PROXY_COMPOSE,
        ROOT / "docs" / "guide" / "install-docker.md",
        ROOT / "docs" / "SECURITY.md",
        ROOT / "relay" / "README.md",
    ]
    offenders: list[str] = []
    for path in docs:
        if not path.is_file():
            pytest.fail(f"missing required deployment file: {path.relative_to(ROOT)}")
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?im)^\s*privileged\s*:\s*true\s*(?:#.*)?$", source):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"remove privileged:true deployment examples: {offenders}"


def test_server_and_relay_entrypoints_refuse_an_elevated_runtime_before_listening():
    try:
        from astrodeck import runtime_security as server_runtime_security
        from relay import runtime_security as relay_runtime_security
    except ImportError as exc:
        pytest.fail(f"missing component runtime-security module ({exc})")

    for module in (server_runtime_security, relay_runtime_security):
        assert callable(getattr(module, "is_elevated_runtime", None))
        assert callable(getattr(module, "require_unprivileged_runtime", None))

    from astrodeck import __main__ as server_main
    from relay import server as relay_server

    server_source = inspect.getsource(server_main._cmd_run)
    relay_source = inspect.getsource(relay_server.main)
    server_guard = _call_line(server_source, "require_unprivileged_runtime")
    assert server_guard < _call_line(server_source, "_security_banner")
    assert server_guard < _import_line(server_source, "uvicorn")
    relay_guard = _call_line(relay_source, "require_unprivileged_runtime")
    assert relay_guard < _call_line(relay_source, "from_env")
    assert relay_guard < _import_line(relay_source, "uvicorn")


def test_runtime_guard_refuses_elevation_and_detection_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    try:
        from astrodeck import runtime_security as server_runtime_security
        from relay import runtime_security as relay_runtime_security
    except ImportError as exc:
        pytest.fail(f"missing component runtime-security module ({exc})")

    for module in (server_runtime_security, relay_runtime_security):
        guard = module.require_unprivileged_runtime
        monkeypatch.setattr(module, "is_elevated_runtime", lambda: False)
        assert guard("acceptance-component") is None

        monkeypatch.setattr(module, "is_elevated_runtime", lambda: True)
        with pytest.raises(RuntimeError, match=r"(?i)(refus|elevat|root|admin)"):
            guard("acceptance-component")

        error_type = getattr(module, "PrivilegeDetectionError", RuntimeError)

        def detection_failed():
            raise error_type("token query failed")

        monkeypatch.setattr(module, "is_elevated_runtime", detection_failed)
        with pytest.raises(error_type):
            guard("acceptance-component")


def _build_image(docker: str, *, tag: str, dockerfile: str, context: str) -> None:
    build = subprocess.run(
        [docker, "build", "-f", dockerfile, "-t", tag, context],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=1800,
    )
    assert build.returncode == 0, build.stdout + build.stderr


def _require_linux_container_live() -> None:
    if platform.system() != "Linux":
        pytest.fail(
            "this mandatory container release gate must run on a Linux "
            "amd64 or arm64 host, not Docker Desktop emulation"
        )
    if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        pytest.fail(f"unsupported release-gate architecture: {platform.machine()}")


def _assert_image_identity(docker: str, *, tag: str) -> None:
    configured_user = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Config.User}}", tag],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert configured_user.returncode == 0, configured_user.stdout + configured_user.stderr
    assert configured_user.stdout.strip() == "10001:10001"

    identity = subprocess.run(
        [docker, "run", "--rm", "--entrypoint", "sh", tag, "-c", "id -u; id -g"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    assert identity.stdout.splitlines() == ["10001", "10001"]


def _assert_root_override_fails(
    docker: str, *, tag: str, module: str, extra_args: list[str] | None = None
) -> None:
    command = [
        docker,
        "run",
        "--rm",
        "--user",
        "0:0",
        "--entrypoint",
        "python",
        tag,
        "-m",
        module,
        *(extra_args or []),
    ]
    try:
        root_run = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"root override reached a listening server instead of failing closed: {exc}")
    output = (root_run.stdout + root_run.stderr).lower()
    assert root_run.returncode != 0
    assert "refus" in output and ("root" in output or "privileg" in output or "elevat" in output)


def _assert_image_sandbox_primitives(docker: str, *, tag: str) -> None:
    probe = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
            "--entrypoint",
            "sh",
            tag,
            "-c",
            "grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status && "
            "grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status && "
            "! touch /etc/should-not-write && ! touch /opt/should-not-write && "
            "touch /tmp/allowed",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr


@pytest.mark.security_live
def test_built_image_defaults_to_uid_10001_and_root_override_fails_closed():
    _require_linux_container_live()
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required for the live container release gate")

    tag = os.environ.get("ASTRODECK_SECURITY_IMAGE", "astrodeck:security-acceptance")
    if not os.environ.get("ASTRODECK_SECURITY_IMAGE"):
        _build_image(docker, tag=tag, dockerfile="Dockerfile", context=".")

    relay_tag = os.environ.get(
        "RELAY_SECURITY_IMAGE", "astrodeck-relay:security-acceptance"
    )
    if not os.environ.get("RELAY_SECURITY_IMAGE"):
        _build_image(
            docker,
            tag=relay_tag,
            dockerfile="relay/Dockerfile",
            context="relay",
        )

    _assert_image_identity(docker, tag=tag)
    _assert_image_identity(docker, tag=relay_tag)
    _assert_image_sandbox_primitives(docker, tag=tag)
    _assert_image_sandbox_primitives(docker, tag=relay_tag)
    _assert_root_override_fails(
        docker,
        tag=tag,
        module="astrodeck",
        extra_args=["run", "--host", "127.0.0.1", "--port", "8800"],
    )
    _assert_root_override_fails(docker, tag=relay_tag, module="relay")


@pytest.mark.security_live
def test_hardened_container_has_no_caps_nnp_read_only_code_and_persistent_data():
    _require_linux_container_live()
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required for the live container release gate")

    tag = os.environ.get("ASTRODECK_SECURITY_IMAGE", "astrodeck:security-acceptance")
    suffix = uuid.uuid4().hex
    config_volume = f"astrodeck-security-config-{suffix}"
    capture_volume = f"astrodeck-security-captures-{suffix}"
    common = [
        docker,
        "run",
        "--rm",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
        "--mount",
        f"type=volume,source={config_volume},target=/data/config",
        "--mount",
        f"type=volume,source={capture_volume},target=/data/captures",
        "--entrypoint",
        "sh",
        tag,
        "-c",
    ]
    try:
        probe = subprocess.run(
            common
            + [
                "test \"$(id -u)\" = 10001 && test \"$(id -g)\" = 10001 && "
                "grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status && "
                "grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status && "
                "! touch /etc/astrodeck-should-fail && "
                "! touch /opt/astrodeck-should-fail && "
                "touch /tmp/ok /data/config/persisted /data/captures/ok"
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr

        replacement = subprocess.run(
            common + ["test -f /data/config/persisted"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert replacement.returncode == 0, replacement.stdout + replacement.stderr
    finally:
        subprocess.run(
            [docker, "volume", "rm", "-f", config_volume, capture_volume],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
