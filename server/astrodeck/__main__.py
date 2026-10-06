# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""AstroDeck CLI.

Subcommands (argparse):

  run (DEFAULT)   python -m astrodeck [run] [--host H] [--port P]
                  Start the FastAPI server. A bare ``python -m astrodeck`` (no
                  subcommand) is identical to ``run`` with the same --host/--port
                  flags, so the historical launch line is byte-for-byte unchanged.

  create-admin    python -m astrodeck create-admin <username> [--password PW]
                  Seed OR reset a LOCAL admin user in the user store and EXIT
                  WITHOUT starting the server (anti-lockout #2). The password is
                  prompted via ``getpass`` when ``--password`` is omitted. This
                  path never imports ``api.app`` / uvicorn -- it only touches the
                  ``UserStore``, so it works even if the server config is broken.

Security: the default bind is loopback. A non-loopback bind requires a shared
token or a configured local/Google method; the explicit
``--allow-insecure-open`` escape hatch is for isolated development only.
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys


ALLOW_INSECURE_OPEN_ENV = "ASTRODECK_ALLOW_INSECURE_OPEN"
REQUIRE_AUTH_ENV = "ASTRODECK_REQUIRE_AUTH"

# #496 / backlog ruling D-12: the Windows proactor's overlapped-cache fault
# (OSError [WinError 6] chained onto a KeyError out of run_forever) exits
# with this code instead of tearing the process down as an unhandled
# exception, so the rig's supervisor restarts the server and the durable log
# says why it died.
EXIT_EVENT_LOOP_FAULT = 1


def _strict_env_bool(name: str, *, default: bool = False) -> bool:
    """Read a deployment security toggle without permissive fallbacks."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value")


def _security_banner(host: str, port: int) -> bool:
    """Log a one-line auth posture, and a LOUD warning when the server is bound
    to a non-loopback interface (reachable off-box) with no auth configured.
    Returns whether real authentication is configured. ASCII only -- the live
    console is cp1252."""
    # Lazy import: keep the create-admin path free of api.app / uvicorn.
    from .api.app import AUTH_ENV_VAR, auth_enabled
    from .auth import session as _session
    from .config import (
        config_store,
        deployment_auth_required,
        network_auth_ready,
    )

    log = logging.getLogger("astrodeck")
    exposed = host not in ("127.0.0.1", "localhost", "::1")

    # Critical fix: warn if a real auth method (local/google) is enabled while
    # ASTRODECK_SECRET is unset AND no secret has been persisted yet. The boot
    # lifespan auto-generates+persists one (or arms the fail-closed interlock),
    # but surface the posture here too so the operator sees it on the console.
    try:
        methods = config_store.cfg().auth.methods_effective()
    except Exception:  # noqa: BLE001 - never let a banner break launch
        methods = []
    if methods and _session.secret_is_default():
        log.warning("=" * 70)
        log.warning("SECURITY: auth method(s) %s enabled but %s is not set.",
                    "+".join(methods), _session.SECRET_ENV_VAR)
        log.warning("A random session secret will be generated and persisted to")
        log.warning("config/%s. Set %s to manage the key yourself; without a",
                    _session.SECRET_FILE_NAME, _session.SECRET_ENV_VAR)
        log.warning("usable secret, sessions FAIL CLOSED and logins will not work.")
        log.warning("=" * 70)

    # An exposed or deployment-managed listener needs a real boundary, not a
    # merely ticked login method.  Strict readiness rejects weak bearer tokens,
    # the unauthenticated first-run window, and legacy password records whose
    # strength has never been established.
    configured = network_auth_ready(
        config_store.cfg().auth,
        strict=exposed or deployment_auth_required(),
    )
    if configured:
        log.info("auth: ENABLED on %s:%d", host, port)
        return True
    if exposed:
        log.warning("=" * 70)
        log.warning("SECURITY WARNING: AstroDeck is bound to %s:%d with NO auth.",
                    host, port)
        log.warning("Anyone on this network can slew the mount and command the")
        log.warning("rig. Startup will refuse this bind unless auth is configured.")
        log.warning("Set %s, enable local/Google auth, or use the explicit",
                    AUTH_ENV_VAR)
        log.warning("--allow-insecure-open development override.")
        log.warning("For a local-only rig, bind loopback: --host 127.0.0.1")
        log.warning("See docs/SECURITY.md before any remote deployment.")
        log.warning("=" * 70)
    else:
        log.info("auth: disabled; bound to loopback %s:%d (local-only).",
                 host, port)
    return False


def create_admin(username: str, password: str | None = None) -> dict:
    """Seed OR reset a local ADMIN user, returning its ``to_public()`` shape.

    This is the underlying function the ``create-admin`` subcommand calls (and
    that tests call directly). It NEVER imports ``api.app`` / uvicorn -- it only
    touches the ``UserStore``. If a user with ``username`` already exists it is
    FORCED back to an enabled admin and its password is reset (so a locked-out
    operator can always recover their own admin). ``password`` is required here
    (the CLI front-end prompts for it); a blank/too-long password raises
    ``ValueError`` (surfaced to the CLI as a clean error)."""
    if password is None or password == "":
        raise ValueError("password required")

    # Import the store lazily so importing this module stays light.
    from .auth.users import user_store

    existing = user_store.get_by_username(username)
    if existing is None:
        # require_email=False: this is the break-glass path. It has to work on
        # a rig with no Google configured and nothing but a console, so it
        # accepts a bare username where the API insists on an email.
        user = user_store.create(username=username, password=password,
                                 role="admin", enabled=True,
                                 require_email=False)
    else:
        # Reset the existing account to a known-good enabled admin.
        user_store.set_password(existing.id, password)
        if existing.role != "admin":
            user_store.set_role(existing.id, "admin")
        if not existing.enabled:
            user_store.set_enabled(existing.id, True)
        user = user_store.get(existing.id)

    # …AND MAKE THE ACCOUNT REACHABLE. Creating a local admin while ``local``
    # is not an enabled method produces a perfectly good user that the login
    # page will not offer a form for — the break-glass hands you a key to a
    # door it does not unlock. That is exactly the state the rig was found in
    # on 2026-08-09 (#205): one local admin in the store, ``methods:
    # ["google"]``, Google unconfigured, and a login page saying no sign-in
    # method was available.
    #
    # Enabling ``local`` is the smallest change that makes this account usable
    # and it takes nothing else away: other methods stay exactly as they were.
    from .config import config_store
    auth = config_store.cfg().auth
    methods = list(auth.methods_effective())
    if "local" not in set(methods) or auth.local_enabled_first_run:
        config_store.set_auth(
            auth.model_copy(update={
                "methods": methods if "local" in methods else [*methods, "local"],
                "local_enabled_first_run": False,
            }))
        if "local" not in methods:
            print("enabled the 'local' sign-in method so this account can be used.")
    return user.to_public()


def _cmd_create_admin(args: argparse.Namespace) -> int:
    """``create-admin`` front-end: resolve the password (prompt if omitted),
    write the admin, print a confirmation, and EXIT (never starts the server)."""
    from .config import CONFIG_DIR
    from .persist import secure_private_tree

    try:
        secure_private_tree(CONFIG_DIR)
    except RuntimeError as exc:
        print(
            f"error: cannot secure private configuration at {CONFIG_DIR}: {exc}",
            file=sys.stderr,
        )
        return 2
    password = args.password
    if not password:
        password = getpass.getpass("New admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("error: passwords do not match", file=sys.stderr)
            return 2
    try:
        pub = create_admin(args.username, password)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"admin user '{pub['username']}' ready (id={pub['id']}).")
    return 0


def uvicorn_config_kwargs(*, host: str, port: int, access_log_enabled: bool,
                          trusted_proxy_ips: str, log_config: dict) -> dict:
    """Every keyword ``_cmd_run`` hands ``uvicorn.Config``, in one place so a
    test can build the exact production config (#550) without copying it out
    of ``_cmd_run`` by hand and drifting from it. ``log_config`` is taken as a
    parameter, not built here, so this stays a plain dict shuffle with no
    import of its own -- ``_cmd_run`` imports ``logfmt`` (and ``uvicorn``)
    lazily, after the unprivileged-runtime interlock below, and a bare
    ``python -m astrodeck create-admin`` must still never import either."""
    return dict(
        host=host, port=port, log_level="info",
        proxy_headers=bool(trusted_proxy_ips),
        forwarded_allow_ips=trusted_proxy_ips or "",
        access_log=access_log_enabled,
        log_config=log_config,
        limit_concurrency=128, backlog=128, timeout_keep_alive=5,
        ws_max_size=1024 * 1024, ws_max_queue=16,
        h11_max_incomplete_event_size=65536,
    )


def _is_windows_proactor_overlapped_fault(exc: BaseException) -> bool:
    """True only for the EXACT Windows proactor fault #496 recorded: an
    ``OSError`` ``[WinError 6]`` ("the handle is invalid") raised out of
    CPython's ``IocpProactor._poll`` while it is unwinding a ``KeyError`` from
    its overlapped cache (a stale completion-port address popped a miss, and
    the handler's own ``_winapi.CloseHandle(key)`` then found the handle
    already gone). That ``OSError`` is never routed through the loop's
    ``call_exception_handler`` -- it is a bare exception escaping
    ``run_forever()`` -- so this is a plain predicate on the exception object,
    not an installed asyncio handler. Anything else (a different winerror, no
    ``KeyError`` behind it, or not an ``OSError`` at all) returns False and
    must propagate unchanged per D-12's adjudication: a key-name match alone
    (just ``winerror == 6``) would also swallow a genuine, unrelated I/O
    failure that happens to share the same Windows error code."""
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "winerror", None) != 6:
        return False
    return isinstance(exc.__context__, KeyError)


def _run_server_with_fault_handling(server: "uvicorn.Server") -> int:
    """Run ``server`` to completion, turning the narrow Windows proactor
    fault (#496, backlog ruling D-12) into a LOGGED, deliberate non-zero exit
    instead of an unhandled traceback silently tearing the process down. D-12
    keeps the proactor loop (the server runs ``asyncio.create_subprocess_exec``
    in ``solve/astap.py``, which the selector loop cannot do on Windows), so
    the fix is this narrow catch-log-exit, not a different event loop.

    The rig's supervisor only restarts a process that has actually exited, so
    this still lets the process die -- it just makes sure the durable log
    says why before it does, instead of whatever ad hoc traceback uvicorn's
    own shutdown path would otherwise produce.

    Any OTHER exception out of ``server.run()`` -- including an OSError that
    only superficially resembles #496 -- is reraised unchanged. That keeps
    today's behaviour: an unhandled exception already prints a traceback to
    stderr and the interpreter exits non-zero on its own, and this function
    must not mask a different bug as the Windows proactor fault."""
    try:
        server.run()
    except OSError as exc:
        if not _is_windows_proactor_overlapped_fault(exc):
            raise
        logging.getLogger("astrodeck").error(
            "Windows event-loop fault: the proactor's overlapped cache lost "
            "a completion-port entry (KeyError in _poll) while closing a "
            "stale handle, raising OSError [WinError 6] out of run_forever "
            "(see #496). Exiting non-zero so the supervisor restarts the "
            "server.",
            exc_info=exc,
        )
        return EXIT_EVENT_LOOP_FAULT
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """``run`` front-end (and the bare-invocation default): start uvicorn.

    Returns the process exit code: ``0`` on a normal shutdown,
    ``EXIT_APPLY_UPDATE`` (92) when a self-update was applied -- the supervisor
    reads that code to swap ``current`` and relaunch the new version -- or
    ``EXIT_EVENT_LOOP_FAULT`` (1) when the Windows proactor fault (#496, D-12)
    was caught and logged; either way the supervisor sees a dead process and
    restarts it. We build the
    ``uvicorn.Server`` explicitly (instead of ``uvicorn.run``) so the update
    service can request a graceful shutdown and arm the exit code."""
    from .runtime_security import (
        parse_forwarded_allow_ips,
        require_unprivileged_runtime,
    )

    # This network process is never a privileged helper.  Run the interlock
    # before importing configuration, reading secrets, or building the ASGI
    # application so a bad service/container identity fails closed.
    require_unprivileged_runtime("AstroDeck server")

    trusted_proxy_ips = parse_forwarded_allow_ips(
        os.environ.get("ASTRODECK_FORWARDED_ALLOW_IPS")
    )
    access_log_enabled = _strict_env_bool(
        "ASTRODECK_UVICORN_ACCESS_LOG", default=True
    )
    require_auth = _strict_env_bool(REQUIRE_AUTH_ENV)

    from .config import CONFIG_DIR
    from .persist import secure_private_tree

    try:
        secure_private_tree(CONFIG_DIR)
    except RuntimeError as exc:
        logging.getLogger("astrodeck").error(
            "cannot secure private configuration at %s: %s", CONFIG_DIR, exc
        )
        return 2
    try:
        authenticated = _security_banner(args.host, args.port)
    except (RuntimeError, ValueError) as exc:
        logging.getLogger("astrodeck").error(
            "authentication settings are unsafe: %s; refusing to start", exc
        )
        return 2
    exposed = args.host not in ("127.0.0.1", "localhost", "::1")
    env_override = (os.environ.get(ALLOW_INSECURE_OPEN_ENV) or "").strip().lower()
    allow_insecure = bool(getattr(args, "allow_insecure_open", False)) or (
        env_override in {"1", "true", "yes", "on"})
    if require_auth and not authenticated:
        logging.getLogger("astrodeck").error(
            "%s is enabled but no usable authentication is configured; "
            "refusing to start", REQUIRE_AUTH_ENV)
        return 2
    if exposed and not authenticated and not allow_insecure:
        logging.getLogger("astrodeck").error(
            "refusing unauthenticated non-loopback bind; configure auth or use "
            "--allow-insecure-open only on an isolated development network")
        return 2
    # Lazy imports after the security interlock so a refused launch never builds
    # the app or starts loading server machinery.
    import uvicorn

    from . import logfmt
    from .api import create_app
    from .api.app import ALLOWED_HOSTS_ENV
    from .update import service as update_service

    try:
        app = create_app(
            bind_host=args.host,
            allowed_hosts=os.environ.get(ALLOWED_HOSTS_ENV),
        )
    except ValueError as exc:
        logging.getLogger("astrodeck").error(
            "invalid %s: %s; refusing to start", ALLOWED_HOSTS_ENV, exc
        )
        return 2
    config = uvicorn.Config(
        app, **uvicorn_config_kwargs(
            host=args.host, port=args.port,
            access_log_enabled=access_log_enabled,
            trusted_proxy_ips=trusted_proxy_ips,
            log_config=logfmt.uvicorn_log_config(),
        ),
    )
    server = uvicorn.Server(config)
    update_service.bind_server(server)  # enables graceful exit-92 on self-update
    fault_exit = _run_server_with_fault_handling(server)
    if fault_exit != 0:
        return fault_exit
    return update_service.consume_exit_code()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrodeck")
    sub = parser.add_subparsers(dest="command")

    # run (explicit) -- same flags as the historical bare invocation.
    p_run = sub.add_parser("run", help="start the AstroDeck server (default)")
    p_run.add_argument("--host", default="127.0.0.1",
                       help="bind interface (default: loopback only)")
    p_run.add_argument("--port", type=int, default=8800)
    p_run.add_argument(
        "--allow-insecure-open", action="store_true",
        help="allow an unauthenticated non-loopback bind (unsafe; development only)")
    p_run.set_defaults(func=_cmd_run)

    # create-admin -- seed/reset a local admin, then exit.
    p_admin = sub.add_parser(
        "create-admin",
        help="seed or reset a local admin user, then exit (no server)")
    p_admin.add_argument("username", help="admin username (case-insensitive)")
    p_admin.add_argument("--password", default=None,
                         help="admin password; prompted via getpass if omitted")
    p_admin.set_defaults(func=_cmd_create_admin)

    return parser


_SUBCOMMANDS = ("run", "create-admin")


def _run_only_parser() -> argparse.ArgumentParser:
    """A flat parser carrying ONLY the run flags, used for the bare invocation so
    ``python -m astrodeck --host H --port P`` behaves byte-for-byte like the
    historical (subcommand-less) launch line."""
    rp = argparse.ArgumentParser(prog="astrodeck")
    rp.add_argument("--host", default="127.0.0.1",
                    help="bind interface (default: loopback only)")
    rp.add_argument("--port", type=int, default=8800)
    rp.add_argument(
        "--allow-insecure-open", action="store_true",
        help="allow an unauthenticated non-loopback bind (unsafe; development only)")
    return rp


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Bare ``python -m astrodeck [--host H --port P]`` (no subcommand) == ``run``.
    # Detect this by the FIRST non-flag token: if it is not a known subcommand we
    # treat the whole argv as run flags (so --host/--port keep working bare). A
    # leading -h/--help still falls through to the full parser's help.
    first = next((a for a in raw if not a.startswith("-")), None)
    if first is None and not any(a in ("-h", "--help") for a in raw):
        # no positional at all (and not asking for help) -> run with these flags.
        return _cmd_run(_run_only_parser().parse_args(raw))
    if first is not None and first not in _SUBCOMMANDS:
        # an unknown leading positional -> assume the legacy bare run form.
        return _cmd_run(_run_only_parser().parse_args(raw))

    args = _build_parser().parse_args(raw)
    if getattr(args, "command", None) is None:
        return _cmd_run(_run_only_parser().parse_args(raw))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
