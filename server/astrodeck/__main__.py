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

Security (P0-4): the shared-token gate (``ASTRODECK_TOKEN``) and the multi-method
RBAC providers are OFF by default, so LAN access keeps working unchanged. Set a
token and/or enable a method (local/google) to require auth. For a local-only rig
bind loopback with ``--host 127.0.0.1``. See docs/SECURITY.md before any remote
deployment.
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys


def _security_banner(host: str, port: int) -> None:
    """Log a one-line auth posture, and a LOUD warning when the server is bound
    to a non-loopback interface (reachable off-box) with no token configured.
    ASCII only -- the live console is cp1252."""
    # Lazy import: keep the create-admin path free of api.app / uvicorn.
    from .api.app import AUTH_ENV_VAR, auth_enabled
    from .auth import session as _session
    from .config import config_store

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

    if auth_enabled():
        log.info("auth: ENABLED (shared token from %s) on %s:%d",
                 AUTH_ENV_VAR, host, port)
        return
    if exposed:
        log.warning("=" * 70)
        log.warning("SECURITY WARNING: AstroDeck is bound to %s:%d with NO auth.",
                    host, port)
        log.warning("Anyone on this network can slew the mount and command the")
        log.warning("rig. To require a shared token, set the %s env var.",
                    AUTH_ENV_VAR)
        log.warning("For a local-only rig, bind loopback: --host 127.0.0.1")
        log.warning("See docs/SECURITY.md before any remote deployment.")
        log.warning("=" * 70)
    else:
        log.info("auth: disabled; bound to loopback %s:%d (local-only).",
                 host, port)


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
    return user.to_public()


def _cmd_create_admin(args: argparse.Namespace) -> int:
    """``create-admin`` front-end: resolve the password (prompt if omitted),
    write the admin, print a confirmation, and EXIT (never starts the server)."""
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


def _cmd_run(args: argparse.Namespace) -> int:
    """``run`` front-end (and the bare-invocation default): start uvicorn.

    Returns the process exit code: ``0`` on a normal shutdown, or
    ``EXIT_APPLY_UPDATE`` (92) when a self-update was applied -- the supervisor
    reads that code to swap ``current`` and relaunch the new version. We build the
    ``uvicorn.Server`` explicitly (instead of ``uvicorn.run``) so the update
    service can request a graceful shutdown and arm the exit code."""
    # Lazy import so create-admin never pulls in the full app / uvicorn.
    import uvicorn

    from .api import create_app
    from .update import service as update_service

    _security_banner(args.host, args.port)
    app = create_app()
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    update_service.bind_server(server)  # enables graceful exit-92 on self-update
    server.run()
    return update_service.consume_exit_code()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrodeck")
    sub = parser.add_subparsers(dest="command")

    # run (explicit) -- same flags as the historical bare invocation.
    p_run = sub.add_parser("run", help="start the AstroDeck server (default)")
    p_run.add_argument("--host", default="0.0.0.0",
                       help="bind interface; use 127.0.0.1 for local-only")
    p_run.add_argument("--port", type=int, default=8800)
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
    rp.add_argument("--host", default="0.0.0.0",
                    help="bind interface; use 127.0.0.1 for local-only")
    rp.add_argument("--port", type=int, default=8800)
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
