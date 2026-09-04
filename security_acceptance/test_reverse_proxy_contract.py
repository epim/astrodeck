"""Target-state contract for the supported nginx HTTPS/WSS edge."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
NGINX_ROOT = ROOT / "deploy" / "reverse-proxy" / "nginx"
PROXY_COMPOSE = ROOT / "deploy" / "reverse-proxy" / "docker-compose.yml"
RELAY_PROXY_COMPOSE = (
    ROOT / "deploy" / "reverse-proxy" / "docker-compose.relay.yml"
)
PROXY_README = ROOT / "deploy" / "reverse-proxy" / "README.md"


def _nginx_sources() -> dict[Path, str]:
    if not NGINX_ROOT.is_dir():
        pytest.fail(
            "missing deploy/reverse-proxy/nginx reference deployment; implement "
            "the reverse-proxy design"
        )
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in NGINX_ROOT.rglob("*")
        if path.is_file() and (".conf" in path.name or path.suffix == ".template")
    }
    assert sources, "deploy/reverse-proxy/nginx contains no nginx configuration"
    names = {path.name for path in sources}
    assert "astrodeck.conf.template" in names
    assert "relay.conf.template" in names
    return sources


def _all_nginx() -> str:
    return "\n".join(_nginx_sources().values())


def _named_nginx(name: str) -> str:
    matches = [text for path, text in _nginx_sources().items() if path.name == name]
    assert len(matches) == 1, f"expected exactly one nginx source named {name}"
    return matches[0]


def _directive_values(source: str, directive: str) -> list[str]:
    return re.findall(rf"(?m)^\s*{re.escape(directive)}\s+([^;]+);", source)


def _duration_seconds(raw: str) -> float:
    value = raw.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)?", value)
    assert match, f"unsupported duration in acceptance test: {raw!r}"
    number = float(match.group(1))
    return number * {None: 1.0, "ms": 0.001, "s": 1.0, "m": 60.0}[match.group(2)]


def test_reference_runbook_covers_bootstrap_firewall_certificates_and_rootless_limits():
    assert PROXY_README.is_file()
    text = PROXY_README.read_text(encoding="utf-8").lower()
    for required in (
        "create-admin",
        "firewall",
        "8800",
        "certificate",
        "private key",
        "nginx -t",
        "rootless",
        "source ip",
        "docker-compose.relay.yml",
        "public_https_port",
        "1 to 65535",
    ):
        assert required in text, f"reverse-proxy runbook omits {required!r}"
    assert "privileged: true" not in text


def test_edge_bounds_bodies_headers_connections_and_request_rates():
    source = _all_nginx()

    assert re.search(r"include\s+/tmp/site\.conf\s*;", source)
    assert any(v.strip().lower() == "8m" for v in _directive_values(source, "client_max_body_size"))
    assert any(_duration_seconds(v) <= 10 for v in _directive_values(source, "client_header_timeout"))
    assert any(_duration_seconds(v) <= 30 for v in _directive_values(source, "client_body_timeout"))
    assert any(_duration_seconds(v) <= 30 for v in _directive_values(source, "send_timeout"))
    assert "proxy_request_buffering on;" in source
    assert "proxy_buffering off;" in source
    assert re.search(r"client_header_buffer_size\s+(?:[1-8]k|8192)\s*;", source)
    assert re.search(r"large_client_header_buffers\s+4\s+8k\s*;", source)
    assert any(_duration_seconds(v) <= 15 for v in _directive_values(source, "keepalive_timeout"))
    assert any(_duration_seconds(v) <= 5 for v in _directive_values(source, "proxy_connect_timeout"))
    assert any(_duration_seconds(v) <= 60 for v in _directive_values(source, "proxy_read_timeout"))
    assert any(_duration_seconds(v) <= 60 for v in _directive_values(source, "proxy_send_timeout"))
    assert "limit_req_zone" in source and "zone=auth" in source and "zone=general" in source
    assert "limit_conn_zone" in source
    assert re.search(r"limit_req_zone\s+\$binary_remote_addr\s+zone=auth:[^;]+rate=5r/m\s*;", source)
    assert re.search(r"limit_req_zone\s+\$binary_remote_addr\s+zone=critical:[^;]+rate=2r/m\s*;", source)
    for zone, rate in (
        ("static", "30r/s"),
        ("general", "20r/s"),
        ("ws_open", "10r/m"),
        ("relay_http", "20r/s"),
        ("relay_scope", "2r/s"),
        ("relay_ws", "1r/s"),
    ):
        assert re.search(
            rf"limit_req_zone\s+\$binary_remote_addr\s+zone={zone}:[^;]+"
            rf"rate={re.escape(rate)}\s*;",
            source,
        )
    assert re.search(r"limit_conn_zone\s+\$binary_remote_addr\s+zone=", source)
    for key, zone in (
        (r"\$binary_remote_addr", "per_ip"),
        (r"\$server_name", "per_host"),
        (r"\$binary_remote_addr", "relay_per_ip"),
        (r"\$server_name", "relay_total"),
    ):
        assert re.search(rf"limit_conn_zone\s+{key}\s+zone={zone}:", source)
    assert re.search(r"limit_req_status\s+429\s*;", source)
    assert re.search(r"limit_conn_status\s+429\s*;", source)
    for auth_path in ("/auth/local", "/auth/token", "/auth/setup/local"):
        assert re.search(
            rf"location\s*=\s*{re.escape(auth_path)}\s*\{{[^}}]*"
            r"limit_req\s+zone=auth\s+burst=5\s+nodelay",
            source,
            re.S,
        )
        assert re.search(
            rf"location\s*=\s*{re.escape(auth_path)}\s*\{{[^}}]*"
            r"client_max_body_size\s+4k\s*;",
            source,
            re.S,
        )
    assert re.search(r"location\s+\^~\s+/api/\s*\{[^}]*limit_req\s+zone=general", source, re.S)
    assert re.search(r"location\s+/\s*\{[^}]*limit_req\s+zone=static\s+burst=120\s+nodelay", source, re.S)
    assert re.search(r"location\s+\^~\s+/api/\s*\{[^}]*limit_req\s+zone=general\s+burst=60\s+nodelay", source, re.S)
    assert re.search(r"location\s*=\s*/ws\s*\{[^}]*(limit_req|limit_conn)", source, re.S)
    assert re.search(r"location\s*=\s*/ws\s*\{[^}]*limit_req\s+zone=ws_open\s+burst=5\s+nodelay", source, re.S)
    assert re.search(r"location\s*=\s*/ws\s*\{[^}]*limit_conn\s+per_ip\s+8\s*;", source, re.S)
    assert re.search(r"location\s*=\s*/ws\s*\{[^}]*limit_conn\s+per_host\s+128\s*;", source, re.S)
    assert re.search(
        r"location\s*=\s*/ws\s*\{[^}]*if\s*\(\s*\$args\s*!=\s*['\"]?['\"]?\s*\)"
        r"\s*\{\s*return\s+400\s*;",
        source,
        re.S,
    )


def test_edge_terminates_tls_rejects_unknown_hosts_and_supports_websockets():
    source = _all_nginx()

    assert re.search(r"listen\s+(?:443|8443)\s+ssl(?:\s+[^;]*)?;", source)
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in source
    assert "ssl_session_tickets off;" in source
    assert re.search(r"Strict-Transport-Security", source, re.I)
    for hsts in re.findall(r"add_header\s+Strict-Transport-Security\s+([^;]+);", source, re.I):
        assert "preload" not in hsts.lower()
        assert "includesubdomains" not in hsts.lower()
    assert re.search(r"listen\s+(?:443|8443)\s+ssl\s+default_server", source)
    assert "ssl_reject_handshake on;" in source or re.search(
        r"server_name\s+_\s*;[^}]*return\s+444\s*;", source, re.S
    )
    assert re.search(r"listen\s+(?:80|8080)\s+default_server", source)
    assert re.search(r"server_name\s+_\s*;[^}]*return\s+444\s*;", source, re.S)

    assert "proxy_set_header Upgrade $http_upgrade;" in source
    assert "proxy_set_header Connection $connection_upgrade;" in source
    assert re.search(r"map\s+\$http_upgrade\s+\$connection_upgrade", source)
    ws_timeouts = _directive_values(source, "proxy_read_timeout")
    assert any(_duration_seconds(value) >= 75 for value in ws_timeouts)
    assert "proxy_http_version 1.1;" in source

    home = _named_nginx("astrodeck.conf.template")
    assert re.search(
        r"location\s+\^~\s+/api/connect/\s*\{[^}]*"
        r"proxy_read_timeout\s+140s\s*;",
        home,
        re.S,
    )


def test_proxy_overwrites_forwarding_headers_and_logs_no_secrets_or_queries():
    sources = _nginx_sources()
    source = "\n".join(sources.values())

    assert re.search(r"(?m)^\s*access_log\s+off\s*;", source)
    assert " combined;" not in source
    assert "proxy_set_header Host $http_host;" in source
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in source
    assert "proxy_set_header X-Forwarded-Proto https;" in source
    assert "proxy_set_header X-Request-ID $request_id;" in source
    assert re.search(r"proxy_set_header\s+Forwarded\s+['\"]?['\"]?\s*;", source)
    assert re.search(r"proxy_set_header\s+X-Real-IP\s+['\"]?['\"]?\s*;", source)
    assert re.search(r"proxy_set_header\s+X-Forwarded-Host\s+['\"]?['\"]?\s*;", source)
    assert "$proxy_add_x_forwarded_for" not in source
    for template in ("astrodeck.conf.template", "relay.conf.template"):
        template_source = _named_nginx(template)
        assert "proxy_next_upstream off;" in template_source
        assert not re.search(r"proxy_next_upstream\s+(?!off\b)", template_source)
        assert "proxy_set_header X-Forwarded-For $remote_addr;" in template_source
        assert "proxy_set_header X-Forwarded-Proto https;" in template_source
        assert "proxy_set_header X-Request-ID $request_id;" in template_source
        assert re.search(
            r"if\s*\(\s*\$arg_token\s*!=\s*['\"]?['\"]?\s*\)\s*\{\s*return\s+400\s*;",
            template_source,
            re.S,
        )
    assert re.search(
        r"return\s+30[178]\s+https://\$host:\$\{(?:ASTRODECK|RELAY)_PUBLIC_HTTPS_PORT\}\$uri\s*;",
        source,
    )
    assert re.search(
        r"if\s*\(\s*\$arg_token\s*!=\s*['\"]?['\"]?\s*\)\s*\{\s*return\s+400\s*;",
        source,
        re.S,
    ), "production edge must reject legacy query tokens with HTTP 400"

    declarations: list[tuple[str, str]] = []
    for text in sources.values():
        declarations.extend(
            re.findall(r"log_format\s+(\w+)\s+(.+?);", text, re.S)
        )
    assert declarations, "define an explicit redacted access log format"
    forbidden = {
        "$request",
        "$request_uri",
        "$args",
        "$query_string",
        "$http_authorization",
        "$http_cookie",
        "$http_x_auth_token",
        "$http_referer",
        "$http_user_agent",
    }
    formats = {name: declaration for name, declaration in declarations}
    assert set(formats) == {"astrodeck_audit", "relay_audit"}
    for name, declaration in declarations:
        assert "escape=json" in declaration
        assert "$request_id" in declaration
        variables = set(re.findall(r"\$[A-Za-z0-9_]+", declaration))
        assert not (forbidden & variables)
        if name == "astrodeck_audit":
            assert "$uri" in declaration
        else:
            assert "$relay_audit_uri" in declaration
            assert "$uri" not in declaration
    relay = _named_nginx("relay.conf.template")
    assert re.search(
        r"map\s+\$uri\s+\$relay_audit_uri\s*\{[^}]*\^/h/\[\^/\]\+/",
        relay,
        re.S,
    )
    assert "/h/:home/" in relay
    assert re.search(r"~\^/h/\[\^/\]\+\$\s+/h/:home\s*;", relay), (
        "the bare /h/<home-id> path must not leak the identifier into logs"
    )
    assert re.search(
        r"access_log\s+/dev/stdout\s+astrodeck_audit\s*;",
        _named_nginx("astrodeck.conf.template"),
    )
    assert re.search(r"access_log\s+/dev/stdout\s+relay_audit\s*;", relay)
    assert re.search(r"error_log\s+[^;]+\s+crit\s*;", source)


def test_home_and_relay_templates_keep_route_specific_limits_separate():
    home = _named_nginx("astrodeck.conf.template")
    relay = _named_nginx("relay.conf.template")
    assert set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", home)) == {
        "ASTRODECK_PUBLIC_HOST",
        "ASTRODECK_PUBLIC_HTTPS_PORT",
    }
    assert set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", relay)) == {
        "RELAY_PUBLIC_HOST",
        "RELAY_PUBLIC_HTTPS_PORT",
    }

    for path in ("/auth/local", "/auth/token", "/auth/setup/local"):
        assert re.search(
            rf"location\s*=\s*{re.escape(path)}\s*\{{[^}}]*limit_req\s+zone=auth",
            home,
            re.S,
        )
    assert "/scope" not in home
    assert re.search(r"location\s+(?:=|\^~)\s*/scope[^\{]*\{[^}]*limit_req", relay, re.S)
    assert re.search(
        r"location\s*=\s*/scope\s*\{[^}]*limit_req\s+zone=relay_scope\s+burst=10\s+nodelay",
        relay,
        re.S,
    )
    assert re.search(
        r"location\s*=\s*/scope\s*\{[^}]*if\s*\(\s*\$args\s*!=\s*['\"]?['\"]?\s*\)"
        r"\s*\{\s*return\s+400\s*;",
        relay,
        re.S,
    )
    assert re.search(
        r"location\s+~\s+\^/h/\[\^/\]\+/ws\$\s*\{[^}]*"
        r"if\s*\(\s*\$args\s*!=\s*['\"]?['\"]?\s*\)\s*\{\s*return\s+400\s*;",
        relay,
        re.S,
    )
    assert re.search(
        r"location\s+~\s+\^/h/\[\^/\]\+/ws\$\s*\{[^}]*"
        r"limit_req\s+zone=relay_ws\s+burst=5\s+nodelay",
        relay,
        re.S,
    )
    assert re.search(
        r"location\s+/h/\s*\{[^}]*limit_req\s+zone=relay_http\s+burst=40\s+nodelay",
        relay,
        re.S,
    )
    assert "/api/system/factory-reset" not in relay


def test_forwarded_allow_list_parser_rejects_wildcard_and_all_address_trust():
    try:
        from astrodeck.runtime_security import parse_forwarded_allow_ips as app_parse
        from relay.config import parse_forwarded_allow_ips as relay_parse
    except ImportError as exc:
        pytest.fail(f"missing trusted-proxy parser ({exc})")

    for parse in (app_parse, relay_parse):
        assert parse(None) == ""
        assert parse("") == ""
        assert parse(" 127.0.0.1,10.20.0.0/24,2001:db8::1 ") == (
            "127.0.0.1,10.20.0.0/24,2001:db8::1"
        )
        for invalid in (
            "*",
            "0.0.0.0/0",
            "::/0",
            "proxy.internal",
            "127.0.0.1,,10.0.0.1",
            "127.0.0.1,127.0.0.1",
            "not-an-ip",
        ):
            with pytest.raises((ValueError, RuntimeError)):
                parse(invalid)


def test_uvicorn_trust_is_explicit_and_application_never_parses_xfp_itself():
    from astrodeck import __main__ as app_main
    from astrodeck.auth import local_routes, routes
    from relay import server as relay_server

    app_run = inspect.getsource(app_main._cmd_run)
    relay_run = inspect.getsource(relay_server.main)
    for name, source in (("server", app_run), ("relay", relay_run)):
        assert "proxy_headers" in source, f"{name} must set proxy header behavior explicitly"
        assert "forwarded_allow_ips" in source, f"{name} must pin trusted proxy IPs/networks"
        assert not re.search(r"forwarded_allow_ips\s*=\s*['\"]\*['\"]", source)

    for helper in (local_routes._is_secure, routes._is_secure):
        source = inspect.getsource(helper).lower()
        assert "x-forwarded-proto" not in source
        assert "request.url.scheme" in source

        from starlette.requests import Request

        common = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "client": ("203.0.113.7", 40000),
            "server": ("astrodeck.test", 443),
        }
        spoofed = Request(
            {
                **common,
                "scheme": "http",
                "headers": [(b"x-forwarded-proto", b"https")],
            }
        )
        normalized = Request({**common, "scheme": "https", "headers": []})
        assert helper(spoofed) is False
        assert helper(normalized) is True


def test_update_and_factory_reset_have_tighter_edge_limits():
    source = _named_nginx("astrodeck.conf.template")
    for path in (
        "/api/update/check",
        "/api/update/apply",
        "/api/update/config",
        "/api/system/factory-reset",
    ):
        pattern = (
            rf"location\s*=\s*{re.escape(path)}\s*\{{[^}}]*"
            r"limit_req\s+zone=critical\s+burst=2\s+nodelay"
        )
        assert re.search(pattern, source, re.S), f"missing critical rate limit for {path}"


def test_local_login_account_limiter_is_sliding_bounded_and_resettable():
    try:
        from astrodeck.auth.login_rate_limit import LoginAttemptLimiter
    except ImportError as exc:
        pytest.fail(f"missing local-login account limiter ({exc})")

    limiter = LoginAttemptLimiter(
        b"a" * 32,
        max_failures=10,
        window_s=600,
        max_keys=10_000,
        idle_ttl_s=3600,
    )
    account = "target-account"
    for attempt in range(10):
        assert limiter.begin_attempt(account, now=float(attempt)) is None
    retry = limiter.begin_attempt(account, now=10.0)
    assert isinstance(retry, int) and 1 <= retry <= 60
    assert account not in repr(vars(limiter)), "limiter state retained a raw username"
    limiter.record_success(account)
    assert limiter.begin_attempt(account, now=11.0) is None

    concurrent = LoginAttemptLimiter(
        b"c" * 32,
        max_failures=10,
        window_s=600,
        max_keys=64,
        idle_ttl_s=3600,
    )
    with ThreadPoolExecutor(max_workers=32) as pool:
        outcomes = list(
            pool.map(
                lambda _: concurrent.begin_attempt("parallel-target", now=20.0),
                range(32),
            )
        )
    assert sum(outcome is None for outcome in outcomes) == 10
    assert all(
        outcome is None or (isinstance(outcome, int) and 1 <= outcome <= 60)
        for outcome in outcomes
    )

    bounded = LoginAttemptLimiter(
        b"b" * 32,
        max_failures=10,
        window_s=600,
        max_keys=64,
        idle_ttl_s=3600,
    )
    protected_account = "must-not-be-evicted"
    for attempt in range(10):
        assert bounded.begin_attempt(protected_account, now=float(attempt)) is None
    assert bounded.begin_attempt(protected_account, now=10.0) is not None
    for index in range(100):
        bounded.begin_attempt(f"unknown-{index}", now=100.0 + index / 1000)
    assert len(bounded) <= 64
    assert bounded.begin_attempt(protected_account, now=101.0) is not None, (
        "attacker-chosen username churn evicted an active account throttle"
    )
    assert bounded.begin_attempt("overflow-probe", now=101.0) is not None, (
        "a full limiter admitted unbounded fresh-name guesses instead of using "
        "its fail-closed overflow bucket"
    )
    state = repr(vars(bounded))
    assert protected_account not in state and "unknown-" not in state

    from astrodeck.auth import local_routes

    route_source = inspect.getsource(local_routes.local_login)
    for operation in ("begin_attempt", "record_success"):
        assert operation in route_source, f"local login never calls limiter.{operation}()"
    assert route_source.index("begin_attempt") < route_source.index(".verify(")
    assert route_source.index(".verify(") < route_source.index("record_success")


def _live_settings() -> tuple[str, str, str]:
    base = (os.environ.get("ASTRODECK_SECURITY_BASE_URL") or "").rstrip("/")
    username = os.environ.get("ASTRODECK_SECURITY_USERNAME") or ""
    password = os.environ.get("ASTRODECK_SECURITY_PASSWORD") or ""
    if not base or not username or not password:
        pytest.fail(
            "live proxy gate needs ASTRODECK_SECURITY_BASE_URL=https://..., "
            "ASTRODECK_SECURITY_USERNAME, and ASTRODECK_SECURITY_PASSWORD for "
            "a disposable local-admin lab account"
        )
    parsed = urlsplit(base)
    assert parsed.scheme == "https" and parsed.hostname and not parsed.path.rstrip("/")
    return base, username, password


def _relay_live_settings() -> tuple[str, str, str]:
    base = (os.environ.get("RELAY_SECURITY_BASE_URL") or "").rstrip("/")
    token = os.environ.get("RELAY_SECURITY_DEVICE_TOKEN") or ""
    home_id = os.environ.get("RELAY_SECURITY_HOME_ID") or ""
    if not base or not token or not home_id:
        pytest.fail(
            "live relay gate needs RELAY_SECURITY_BASE_URL=https://..., "
            "RELAY_SECURITY_DEVICE_TOKEN, and RELAY_SECURITY_HOME_ID for a "
            "disposable one-home lab stack"
        )
    parsed = urlsplit(base)
    assert parsed.scheme == "https" and parsed.hostname and not parsed.path.rstrip("/")
    assert 32 <= len(token) <= 256
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", home_id)
    return base, token, home_id


@pytest.mark.security_live
def test_live_https_origin_body_websocket_and_rate_limit_contract():
    import httpx

    base, username, password = _live_settings()
    with httpx.Client(base_url=base, follow_redirects=False, timeout=20.0) as client:
        health = client.get("/healthz")
        assert health.status_code == 200

        try:
            unknown = client.get("/healthz", headers={"Host": "attacker.invalid"})
        except httpx.RemoteProtocolError:
            unknown = None
        if unknown is not None:
            assert unknown.status_code in {400, 421, 444}

        oversized = client.post(
            "/auth/local",
            content=b"x" * 4097,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert oversized.status_code == 413

        evil = client.post(
            "/auth/local",
            json={"username": "nobody", "password": "wrong"},
            headers={"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site"},
        )
        assert evil.status_code == 403

        methods = client.get("/api/auth/methods")
        assert methods.status_code == 200
        assert "local" in methods.json().get("methods", [])

        login = client.post(
            "/auth/local",
            json={"username": username, "password": password},
            headers={"Origin": base, "Sec-Fetch-Site": "same-origin"},
        )
        assert login.status_code == 200, login.text
        set_cookie = login.headers.get("set-cookie", "").lower()
        assert "secure" in set_cookie and "httponly" in set_cookie and "samesite=strict" in set_cookie

        status = client.get("/api/status")
        assert status.status_code == 200

        import websockets.sync.client

        ws_url = "wss" + base[len("https") :] + "/ws"
        cookie_header = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        connect_parameters = inspect.signature(websockets.sync.client.connect).parameters
        header_name = "additional_headers" if "additional_headers" in connect_parameters else "extra_headers"
        with websockets.sync.client.connect(
            ws_url,
            origin=base,
            **{header_name: {"Cookie": cookie_header}},
        ) as websocket:
            hello = json.loads(websocket.recv(timeout=10))
            assert hello.get("type") == "hello"

        statuses = []
        for index in range(12):
            response = client.post(
                "/auth/local",
                json={
                    "username": f"rate-probe-{index}",
                    "password": "definitely-wrong",
                },
                headers={
                    "Origin": base,
                    "Sec-Fetch-Site": "same-origin",
                    "X-Forwarded-For": f"198.51.100.{index + 1}",
                },
            )
            statuses.append(response.status_code)
        assert 429 in statuses


@pytest.mark.security_live
def test_live_relay_wss_scope_https_round_trip_and_redacted_logs():
    import httpx
    import websockets

    from relay import protocol

    base, device_token, home_id = _relay_live_settings()
    canary = "RELAY_QUERY_SECRET_CANARY_68a31d"

    async def exercise() -> None:
        async with httpx.AsyncClient(base_url=base, timeout=20.0) as client:
            for path in (f"/scope?token={canary}", f"/scope?%74oken={canary}"):
                response = await client.get(path)
                assert response.status_code == 400

        scope_url = "wss" + base[len("https") :] + "/scope"
        async with websockets.connect(scope_url, max_size=2 * 1024 * 1024) as ws:
            await ws.send(
                protocol.hello(
                    device_token,
                    home_id,
                    generation=int(time.time()),
                ).encode()
            )
            ack = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=10))
            assert ack.header.get("ok") is True, ack.header.get("reason", "rejected")

            async def answer_one_request() -> None:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    frame = protocol.decode(raw)
                    if frame.type == protocol.FrameType.PING:
                        await ws.send(protocol.pong(0.0).encode())
                        continue
                    if frame.type == protocol.FrameType.REQ_OPEN:
                        body = json.dumps(
                            {
                                "ok": True,
                                "method": frame.header["method"],
                                "path": frame.header["path"],
                            }
                        ).encode()
                        await ws.send(
                            protocol.resp_head(
                                frame.stream_id,
                                200,
                                [["content-type", "application/json"]],
                            ).encode()
                        )
                        await ws.send(
                            protocol.resp_data(
                                frame.stream_id, body, eof=True
                            ).encode()
                        )
                        return

            responder = asyncio.create_task(answer_one_request())
            try:
                async with httpx.AsyncClient(base_url=base, timeout=20.0) as client:
                    response = await client.get(f"/h/{home_id}/api/status")
                assert response.status_code == 200, response.text
                assert response.json() == {
                    "ok": True,
                    "method": "GET",
                    "path": "/api/status",
                }
                await asyncio.wait_for(responder, timeout=10)
            finally:
                responder.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await responder

    asyncio.run(exercise())

    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required to inspect relay stack logs")
    compose_file = os.environ.get(
        "RELAY_SECURITY_COMPOSE_FILE", str(RELAY_PROXY_COMPOSE)
    )
    logs = subprocess.run(
        [docker, "compose", "-f", compose_file, "logs", "--no-color"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert logs.returncode == 0, logs.stdout + logs.stderr
    combined = logs.stdout + logs.stderr
    if canary in combined:
        pytest.fail("relay query canary appeared in Compose logs")
    if device_token in combined:
        pytest.fail("relay device token appeared in Compose logs")
    if home_id in combined:
        pytest.fail("relay home identifier appeared in Compose logs")


@pytest.mark.security_live
def test_live_slow_header_is_closed_by_the_edge_deadline():
    base, _, _ = _live_settings()
    parsed = urlsplit(base)
    host = parsed.hostname
    assert host is not None
    port = parsed.port or 443

    context = ssl.create_default_context()
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.settimeout(15)
            tls.sendall(f"GET /healthz HTTP/1.1\r\nHost: {host}\r\nX-Slow:".encode("ascii"))
            try:
                data = tls.recv(1)
            except (socket.timeout, ssl.SSLError) as exc:
                pytest.fail(f"edge did not close a slow header within its deadline: {exc}")
            assert data in (b"", b"H")  # close or an explicit HTTP timeout/error response
    assert time.monotonic() - started <= 15


@pytest.mark.security_live
def test_live_slow_partial_body_is_closed_by_the_edge_deadline():
    base, _, _ = _live_settings()
    parsed = urlsplit(base)
    host = parsed.hostname
    assert host is not None
    port = parsed.port or 443

    context = ssl.create_default_context()
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.settimeout(35)
            tls.sendall(
                (
                    "POST /auth/local HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: 10\r\n\r\n"
                    "{"
                ).encode("ascii")
            )
            try:
                data = tls.recv(1)
            except (socket.timeout, ssl.SSLError) as exc:
                pytest.fail(f"edge did not close a slow body within its deadline: {exc}")
            assert data in (b"", b"H")
    assert time.monotonic() - started <= 35


@pytest.mark.security_live
def test_live_proxy_logs_redact_websocket_query_tokens():
    import httpx

    base, _, _ = _live_settings()
    canary = "ASTRODECK_QUERY_SECRET_CANARY_4f8529"
    with httpx.Client(base_url=base, timeout=20.0) as client:
        for query in (f"token={canary}", f"%74oken={canary}"):
            response = client.get(f"/ws?{query}")
            assert response.status_code == 400

    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required to inspect the supported proxy deployment logs")
    compose_file = os.environ.get(
        "ASTRODECK_SECURITY_COMPOSE_FILE", str(PROXY_COMPOSE)
    )
    logs = subprocess.run(
        [docker, "compose", "-f", compose_file, "logs", "--no-color"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert logs.returncode == 0, logs.stdout + logs.stderr
    assert canary not in logs.stdout and canary not in logs.stderr


@pytest.mark.security_live
def test_live_tls_versions_oversized_header_and_hidden_upstream():
    base, _, _ = _live_settings()
    parsed = urlsplit(base)
    host = parsed.hostname
    assert host is not None
    port = parsed.port or 443

    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.fail("OpenSSL CLI is required for the live TLS policy gate")
    for option, should_succeed in (
        ("-tls1", False),
        ("-tls1_1", False),
        ("-tls1_2", True),
        ("-tls1_3", True),
    ):
        probe = subprocess.run(
            [
                openssl,
                "s_client",
                "-connect",
                f"{host}:{port}",
                "-servername",
                host,
                option,
                "-brief",
            ],
            input="",
            text=True,
            capture_output=True,
            timeout=15,
        )
        negotiated = "Protocol version:" in (probe.stdout + probe.stderr)
        assert negotiated is should_succeed, (
            f"unexpected TLS result for {option}: {probe.stdout}{probe.stderr}"
        )

    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.settimeout(10)
            tls.sendall(
                (
                    f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                    f"X-Oversized: {'a' * 9000}\r\n\r\n"
                ).encode("ascii")
            )
            response = tls.recv(4096)
    assert re.match(rb"HTTP/1\.[01] (400|431)\b", response)

    hidden = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    hidden.settimeout(2)
    try:
        result = hidden.connect_ex((host, 8800))
    finally:
        hidden.close()
    assert result != 0, "upstream port 8800 is reachable outside the proxy boundary"


@pytest.mark.security_live
def test_live_nginx_effective_configuration_is_valid():
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required to inspect the supported proxy deployment")
    compose_file = os.environ.get(
        "ASTRODECK_SECURITY_COMPOSE_FILE", str(PROXY_COMPOSE)
    )
    check = subprocess.run(
        [
            docker,
            "compose",
            "-f",
            compose_file,
            "exec",
            "-T",
            "proxy",
            "nginx",
            "-T",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    effective = check.stdout + check.stderr
    assert "proxy_next_upstream off;" in effective
    assert "$proxy_add_x_forwarded_for" not in effective

    for service, expected_uid in (("astrodeck", "10001"), ("proxy", "101")):
        runtime = subprocess.run(
            [
                docker,
                "compose",
                "-f",
                compose_file,
                "exec",
                "-T",
                service,
                "sh",
                "-c",
                "id -u; grep '^CapEff:' /proc/self/status; "
                "grep '^NoNewPrivs:' /proc/self/status",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert runtime.returncode == 0, runtime.stdout + runtime.stderr
        lines = runtime.stdout.splitlines()
        assert lines[0].strip() == expected_uid
        assert re.fullmatch(r"CapEff:\s+0+", lines[1].strip())
        assert re.fullmatch(r"NoNewPrivs:\s+1", lines[2].strip())
