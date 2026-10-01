"""server_ctl.py -- start/stop an ISOLATED AstroDeck server for the UI probe.

Owned entirely by tools/ui_probe/. Run with the SYSTEM python (it only shells
out to the server's own venv python and speaks HTTP with the stdlib -- no
Playwright, no third-party deps here on purpose, so this half of the harness
never needs the server venv to have Playwright installed, and the probe half
never needs the server venv at all).

Isolation:
  - ASTRODECK_CONFIG_DIR   -> --config-dir (default <repo>/.probe/cfg)
  - ASTRODECK_CAPTURE_DIR  -> --capture-dir (default <repo>/.probe/captures)
  Both are grepped straight from the server source, not guessed:
    server/astrodeck/config.py:40   _CONFIG_ENV = os.environ.get("ASTRODECK_CONFIG_DIR")
    server/astrodeck/hub.py:216     _CAPTURE_ENV = os.environ.get("ASTRODECK_CAPTURE_DIR")
  A probe run must never touch the developer's real config/ or captures/.

Auth posture:
  - Default (no --auth): AuthConfig.methods defaults to [] ("open/admin" --
    server/astrodeck/config.py:291). A loopback caller auto-resolves to admin
    (trust_loopback default True), so /api/connect/sim and /api/status need no
    credentials at all. This is the reliable path for the default run: no
    first-run race, no login dance.
  - --auth: seeds a LOCAL admin via the CLI break-glass path BEFORE the server
    ever starts (`python -m astrodeck create-admin`, server/astrodeck/__main__.py).
    That path writes methods=["local"], local_enabled_first_run=False directly
    into the config store -- it never touches the unauthenticated
    POST /auth/setup/local race window over the LAN, and it works headlessly
    (no browser needed to bootstrap admin). We then log in as that admin over
    HTTP (POST /auth/local, server/astrodeck/auth/local_routes.py) and use the
    admin-gated POST /api/users to create probe_operator / probe_viewer.
    Credentials are written to <config-dir>/probe_users.json for probe.py to
    read when driving the login FORM in the browser.

    POST /api/users requires require_email=True (local_routes.py create_user),
    so operator/viewer usernames are email-shaped (probe_operator@probe.local);
    the CLI-created admin is a bare username (create-admin uses
    require_email=False, matching the break-glass contract).

Health: GET /healthz is unauthenticated always (app.py _AUTH_OPEN_EXACT).

Simulator: POST /api/connect/sim (CAP_CONFIG_BACKEND), then poll GET /api/status
until data["mode"] == "sim" and data["connected"]["camera"]["connected"] and
data["connected"]["telescope"]["connected"] (role names from
server/astrodeck/devices/backend.py ROLES).

Stop: taskkill /PID <pid> /T /F -- kills the whole process tree, not just the
launcher, matching the Windows quirk this harness has to live with.

Never the rig's port (#189 S7): `start` refuses port 8800 (RIG_PORT), the port
the rig's own server listens on (CLAUDE.md, "The rig"), before it wipes or
spawns anything. A probe server is private by its config dir, its capture dir
and its port; one on 8800 is where a probe run, a browser tab or a seed meant
for the rig's server would find a simulator, or a rig script meant for the
rig find this one. probe.py refuses a base on that port too.

The probe marker (#539): `start` writes `.astrodeck-probe` (PROBE_MARKER)
into the config and the capture directory it creates, holding the port and
pid of the server it started and the directory itself, once the server is
spawned. seed_session.py, which rewrites a session and can arm it for an
unattended start, refuses any directory without one, so the marker is the
claim "a probe start made this directory", and `start` makes it only where
that is true: a directory that did not exist before this start (or that
--fresh wiped), or one holding the marker an earlier start wrote for that
directory. A directory that was there without one, which may be a real
install's, is used as asked but never marked, and the start says so; so is
one holding a marker copied from another directory, which marking again
would pass off as this directory's.

`--fresh` requires that same marker before it deletes anything (#541): a
mistyped or pasted --config-dir/--capture-dir used to be `shutil.rmtree`'d
with no check at all. It now refuses to wipe an existing directory that
carries no marker naming it, unless that directory is the checkout's own
`<repo>/.probe/` or lives under it, which is accepted unmarked once (this
default predates the marker) and marked like any other directory `--fresh`
just cleared. Both directories are checked before either is deleted, so a
refusal on one leaves both untouched. Backlog ruling D-02 (owner-approved
2026-09-30).

A staged solve failure (#189 S7 item 1): `--sim-solve-fault RA_H,DEC,RADIUS`
sets ASTRODECK_SIM_SOLVE_FAULT for the server, which the SimSolver reads
(server/astrodeck/solve/simsolver.py) to fail every solve within RADIUS deg of
that sky position, so one panel of a mosaic never centres. Without the flag the
variable is removed from the server's environment, so a value left in the
shell that runs this never stages a failure nobody asked for.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / ".probe" / "cfg"
DEFAULT_CAPTURE_DIR = REPO_ROOT / ".probe" / "captures"
DEFAULT_VENV_PYTHON = REPO_ROOT / "server" / ".venv" / "Scripts" / "python.exe"
DEFAULT_PORT = 8801
#: The port the rig's own AstroDeck server listens on (CLAUDE.md, "The rig").
#: Never a probe server's: see the module docstring.
RIG_PORT = 8800
#: The simulator's staged solve failure (server/astrodeck/solve/simsolver.py
#: SOLVE_FAULT_ENV), which `--sim-solve-fault` sets.
SOLVE_FAULT_ENV = "ASTRODECK_SIM_SOLVE_FAULT"
#: The marker `start` leaves in each directory it creates (module docstring,
#: #539). seed_session.py reads it under its own PROBE_MARKER: the two run
#: under different pythons and import nothing of each other, so the server
#: suite's test_h4_seed_session_allow_list.py holds them to one another.
PROBE_MARKER = ".astrodeck-probe"

PROBE_ADMIN_USER = "probe_admin"
PROBE_ADMIN_PASSWORD = "Probe-Admin-Pass-1!"
PROBE_OPERATOR_USER = "probe_operator@probe.local"
PROBE_OPERATOR_PASSWORD = "Probe-Operator-Pass-1!"
PROBE_VIEWER_USER = "probe_viewer@probe.local"
PROBE_VIEWER_PASSWORD = "Probe-Viewer-Pass-1!"


def _log(msg: str) -> None:
    print(f"[server_ctl] {msg}", flush=True)


# --------------------------------------------------------------- HTTP client

class ServerClient:
    """Tiny cookie-carrying HTTP client -- stdlib only (no `requests`)."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self._cj = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj))

    def _do(self, method: str, path: str, body: dict | None, timeout: float):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
                return resp.status, parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                parsed = {"detail": raw.decode("utf-8", "replace")}
            return exc.code, parsed
        except urllib.error.URLError as exc:
            raise ConnectionError(f"{method} {path} unreachable: {exc}") from exc

    def get(self, path: str, timeout: float = 10.0):
        return self._do("GET", path, None, timeout)

    def post(self, path: str, body: dict | None = None, timeout: float = 15.0):
        return self._do("POST", path, body or {}, timeout)


# ------------------------------------------------------------------ helpers

def _rmtree_if_exists(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def write_probe_marker(directory: Path, port: int, pid: int) -> Path:
    """Leave the probe marker in `directory`: one line of JSON naming the
    server's port and pid and the directory itself, resolved, which
    seed_session.py compares with the directory it is given, so a probe
    directory copied elsewhere is not taken for a probe server's."""
    path = directory / PROBE_MARKER
    path.write_text(json.dumps({"port": port, "pid": pid,
                                "dir": str(directory.resolve())}) + "\n",
                    encoding="utf-8")
    return path


def _marked_here(directory: Path) -> bool:
    """Whether `directory` holds the marker an earlier start wrote for it:
    `write_probe_marker`'s JSON, naming this directory. A marker copied in
    from another directory, which seed_session.py refuses, is not one, and
    marking it again would write this directory's name into it and let the
    copy through; nor is a file of that name holding anything else."""
    try:
        data = json.loads((directory / PROBE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    where = data.get("dir") if isinstance(data, dict) else None
    if not (isinstance(where, str) and where):
        return False
    try:
        other = Path(where).resolve()
    except (OSError, ValueError):
        return False
    return os.path.normcase(str(other)) == os.path.normcase(str(directory.resolve()))


def _dirs_to_mark(dirs: tuple[Path, ...]) -> list[Path]:
    """Which of `dirs` this start may mark, asked BEFORE it creates any: one
    that is not there yet (this start creates it), or one holding the marker
    an earlier start wrote for it (`_marked_here`). Never one that was there
    without that marker: it may be a real install's (module docstring,
    #539)."""
    return [d for d in dict.fromkeys(dirs)
            if not d.exists() or _marked_here(d)]


def _under_probe_default(path: Path) -> bool:
    """Whether `path` is the checkout's own `<repo>/.probe/`, or lives under
    it: the one place D-02 (backlog ruling D-02, owner-approved 2026-09-30)
    lets `--fresh` wipe an unmarked directory, because a checkout's default
    probe directories can predate the marker and would otherwise need a
    manual delete on every stale checkout. Resolved against the CURRENT
    REPO_ROOT (a module global, not a value closed over earlier), so a test
    can point it at a scratch tree without touching the real checkout."""
    try:
        probe_root = (REPO_ROOT / ".probe").resolve()
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == probe_root or probe_root in resolved.parents


def _refuse_fresh_wipe(directory: Path, flag: str) -> str | None:
    """Why `--fresh` may not wipe `directory`, or None. Nothing to refuse if
    it does not exist yet. One holding the marker an earlier start wrote for
    it (`_marked_here`) is a probe directory by its own claim. Anything else
    that already exists outside the checkout's own `<repo>/.probe/` may be a
    real install's (#541): D-02 requires the marker there, so it is refused
    rather than wiped."""
    if not directory.exists():
        return None
    if _marked_here(directory):
        return None
    if _under_probe_default(directory):
        return None
    return (f"{flag} {directory} exists and carries no {PROBE_MARKER} marker "
            f"naming it, so it may be a real install's directory and not one "
            f"a probe start made: refusing to wipe it (#541). Delete it by "
            f"hand if it is safe to lose, or pass a directory under "
            f"{REPO_ROOT / '.probe'} (accepted unmarked once) or one this "
            f"tool already marked (backlog ruling D-02, owner-approved "
            f"2026-09-30).")


def _wait_healthz(client: ServerClient, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, body = client.get("/healthz", timeout=5.0)
            if status == 200 and body.get("ok"):
                _log(f"healthz OK (version={body.get('version')})")
                return
            last_err = RuntimeError(f"healthz returned {status}: {body}")
        except ConnectionError as exc:
            last_err = exc
        time.sleep(1.0)
    raise TimeoutError(
        f"server did not become healthy within {timeout_s}s: {last_err}")


def _wait_sim_connected(client: ServerClient, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last_status: dict = {}
    while time.monotonic() < deadline:
        status, body = client.get("/api/status", timeout=5.0)
        if status == 200:
            last_status = body
            connected = body.get("connected", {})
            cam = connected.get("camera", {}) or {}
            scope = connected.get("telescope", {}) or {}
            if (body.get("mode") == "sim" and cam.get("connected")
                    and scope.get("connected")):
                return body
        time.sleep(0.5)
    raise TimeoutError(
        f"simulator did not report connected within {timeout_s}s; "
        f"last /api/status: {json.dumps(last_status)[:500]}")


def _spawn_server(venv_python: Path, port: int, env: dict, log_path: Path) -> int:
    log_f = open(log_path, "ab")
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [str(venv_python), "-m", "astrodeck", "run", "--port", str(port)],
        cwd=str(REPO_ROOT), env=env, stdout=log_f, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, close_fds=True, **kwargs)
    return proc.pid


def _create_admin_cli(venv_python: Path, env: dict, username: str,
                       password: str, log_path: Path) -> None:
    """Seed the first local admin via the CLI break-glass path, BEFORE the
    server starts. This never races the unauthenticated LAN first-run window
    and works with no browser (server/astrodeck/__main__.py create_admin())."""
    with open(log_path, "ab") as log_f:
        log_f.write(f"\n--- create-admin {username} ---\n".encode())
        result = subprocess.run(
            [str(venv_python), "-m", "astrodeck", "create-admin", username,
             "--password", password],
            cwd=str(REPO_ROOT), env=env, stdout=log_f, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(
            f"create-admin exited {result.returncode}; see {log_path}")


def _bootstrap_auth_users(client: ServerClient, config_dir: Path) -> dict:
    """Log in as the CLI-seeded admin, create operator + viewer over the
    admin-gated POST /api/users, and write credentials for probe.py."""
    status, body = client.post(
        "/auth/local",
        {"username": PROBE_ADMIN_USER, "password": PROBE_ADMIN_PASSWORD})
    if status != 200:
        raise RuntimeError(f"admin local login failed ({status}): {body}")
    _log("logged in as probe_admin (session cookie acquired)")

    for username, password, role in (
        (PROBE_OPERATOR_USER, PROBE_OPERATOR_PASSWORD, "operator"),
        (PROBE_VIEWER_USER, PROBE_VIEWER_PASSWORD, "viewer"),
    ):
        status, body = client.post("/api/users", {
            "username": username, "password": password, "role": role,
            "email": username, "enabled": True,
        })
        if status not in (200, 201):
            # 409 = already exists (re-run against a non-fresh dir) -- fine.
            if status == 409:
                _log(f"user {username} already exists, leaving as-is")
                continue
            raise RuntimeError(f"create user {username} failed ({status}): {body}")
        _log(f"created {role} user {username}")

    creds = {
        "admin": {"username": PROBE_ADMIN_USER, "password": PROBE_ADMIN_PASSWORD},
        "operator": {"username": PROBE_OPERATOR_USER,
                     "password": PROBE_OPERATOR_PASSWORD},
        "viewer": {"username": PROBE_VIEWER_USER, "password": PROBE_VIEWER_PASSWORD},
    }
    creds_path = config_dir / "probe_users.json"
    creds_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    _log(f"wrote probe credentials to {creds_path}")
    return creds


# ------------------------------------------------------------------- start

def refuse_rig_port(port: int) -> str | None:
    """Why a probe server may not listen on `port`, or None. Only RIG_PORT
    is refused (module docstring): every other port is a private one."""
    if port == RIG_PORT:
        return (f"port {RIG_PORT} is the rig's server port, and a probe server "
                f"is private: pass --port with any other port")
    return None


def parse_solve_fault(raw: str) -> str:
    """`--sim-solve-fault`'s value, checked the way the SimSolver reads it:
    three finite numbers, RA hours, Dec degrees and a positive radius in
    degrees. Returned normalised; ValueError names what is wrong, so a typo
    refuses the start rather than staging no failure (the SimSolver reads an
    unreadable value as none)."""
    parts = [p.strip() for p in (raw or "").split(",")]
    try:
        ra_h, dec, radius = (float(p) for p in parts)
    except ValueError:
        raise ValueError(f"--sim-solve-fault {raw!r} is not RA_HOURS,DEC_DEG,RADIUS_DEG")
    if not all(math.isfinite(v) for v in (ra_h, dec, radius)):
        raise ValueError(f"--sim-solve-fault {raw!r} is not three finite numbers")
    if not (0.0 <= ra_h < 24.0 and -90.0 <= dec <= 90.0 and radius > 0.0):
        raise ValueError(f"--sim-solve-fault {raw!r}: RA must be 0 to 24 h, Dec -90 "
                         f"to 90 deg and the radius above 0 deg")
    return f"{ra_h!r},{dec!r},{radius!r}"


def cmd_start(args: argparse.Namespace) -> int:
    config_dir = Path(args.config_dir).resolve()
    capture_dir = Path(args.capture_dir).resolve()
    venv_python = Path(args.venv_python).resolve()
    port = args.port

    # Before anything is wiped or spawned: a refused start touches nothing.
    refused = refuse_rig_port(port)
    if refused:
        _log(f"ERROR: {refused}")
        return 2
    fault = None
    if args.sim_solve_fault is not None:
        try:
            fault = parse_solve_fault(args.sim_solve_fault)
        except ValueError as exc:
            _log(f"ERROR: {exc}")
            return 2

    if not venv_python.is_file():
        _log(f"ERROR: venv python not found at {venv_python}")
        return 2

    if args.fresh:
        # Both are checked before either is deleted (#541): a refusal on one
        # must not leave the other already wiped.
        refusals = [r for r in (
            _refuse_fresh_wipe(config_dir, "--config-dir"),
            _refuse_fresh_wipe(capture_dir, "--capture-dir"),
        ) if r]
        if refusals:
            for why in refusals:
                _log(f"ERROR: {why}")
            return 2
        _log(f"--fresh: wiping {config_dir} and {capture_dir}")
        _rmtree_if_exists(config_dir)
        _rmtree_if_exists(capture_dir)

    # Asked before the two are created, and written once the server's pid is
    # known (below): the marker is the claim that this start made them.
    to_mark = _dirs_to_mark((config_dir, capture_dir))
    config_dir.mkdir(parents=True, exist_ok=True)
    capture_dir.mkdir(parents=True, exist_ok=True)
    log_path = config_dir / "server.log"
    pid_path = config_dir / "server.pid"

    if pid_path.exists():
        _log(f"WARNING: stale pid file {pid_path} -- a previous run may not "
             f"have been stopped cleanly. Overwriting.")

    env = dict(os.environ)
    env["ASTRODECK_CONFIG_DIR"] = str(config_dir)
    env["ASTRODECK_CAPTURE_DIR"] = str(capture_dir)
    # UI bundle: server/astrodeck/api/app.py's _resolve_ui_dist() falls back to
    # a REPO-RELATIVE path (parents[3] of app.py, i.e. "<repo>/ui/dist") only
    # when it finds no "<package>/webui" bundled copy -- an assumption that
    # holds for an editable (`pip install -e .`) checkout but NOT for a
    # normal site-packages install, where parents[3] lands inside the venv
    # itself (measured: "<venv>/Lib/ui/dist", which of course has no
    # index.html) and the SPA catch-all route never gets registered, so EVERY
    # path 404s -- not just "/". Verified against this repo's own venv
    # (server/.venv is a non-editable install). Setting ASTRODECK_UI_DIR
    # explicitly is the documented override for exactly this case, and it
    # also pins the probe to the bundle THIS run just built, regardless of
    # what pip install mode produced the venv.
    ui_dir = args.ui_dir or str(REPO_ROOT / "ui" / "dist")
    env["ASTRODECK_UI_DIR"] = ui_dir
    if not (Path(ui_dir) / "index.html").is_file():
        _log(f"WARNING: no index.html under ASTRODECK_UI_DIR={ui_dir} -- "
             f"the server will come up API-only with no SPA. Run "
             f"`cd ui && npm run build` first.")
    # Loopback bind, default posture -- never force insecure-open; the server
    # refuses that unless the bind is non-loopback anyway (see __main__.py).
    env.pop("ASTRODECK_ALLOW_INSECURE_OPEN", None)
    env.pop("ASTRODECK_REQUIRE_AUTH", None)
    # The staged solve failure is this start's to ask for, never inherited.
    env.pop(SOLVE_FAULT_ENV, None)
    if fault is not None:
        env[SOLVE_FAULT_ENV] = fault

    if args.auth:
        _log("bootstrapping local admin via CLI create-admin (pre-start)...")
        _create_admin_cli(venv_python, env, PROBE_ADMIN_USER,
                          PROBE_ADMIN_PASSWORD, log_path)

    base = f"http://127.0.0.1:{port}"
    _log(f"starting server: {venv_python} -m astrodeck run --port {port}")
    _log(f"  ASTRODECK_CONFIG_DIR={config_dir}")
    _log(f"  ASTRODECK_CAPTURE_DIR={capture_dir}")
    _log(f"  ASTRODECK_UI_DIR={ui_dir}")
    if fault is not None:
        _log(f"  {SOLVE_FAULT_ENV}={fault}")
    _log(f"  log -> {log_path}")
    pid = _spawn_server(venv_python, port, env, log_path)
    pid_path.write_text(str(pid), encoding="utf-8")
    _log(f"server pid={pid}")
    for directory in dict.fromkeys((config_dir, capture_dir)):
        if directory in to_mark:
            write_probe_marker(directory, port, pid)
            _log(f"  marked {directory} ({PROBE_MARKER})")
        else:
            _log(f"WARNING: {directory} was there before this start without a "
                 f"{PROBE_MARKER} marker written for it, so it is not marked, and "
                 f"seed_session.py "
                 f"will refuse it: start with --fresh, or a new directory, for a "
                 f"probe that seeds sessions")

    client = ServerClient(base)
    try:
        _wait_healthz(client, args.timeout)
    except TimeoutError as exc:
        _log(f"ERROR: {exc}")
        _log(f"--- tail of {log_path} ---")
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")
            print("\n".join(tail.splitlines()[-60:]))
        except OSError:
            pass
        return 3

    if args.auth:
        creds = _bootstrap_auth_users(client, config_dir)
    else:
        creds = None

    if args.sim:
        _log("connecting simulator rig...")
        status, body = client.post("/api/connect/sim")
        if status != 200:
            _log(f"ERROR: /api/connect/sim -> {status}: {body}")
            return 4
        try:
            final = _wait_sim_connected(client, args.sim_timeout)
        except TimeoutError as exc:
            _log(f"ERROR: {exc}")
            return 4
        _log(f"simulator connected: mode={final.get('mode')} "
             f"camera={final['connected']['camera']['connected']} "
             f"telescope={final['connected']['telescope']['connected']}")

    _log(f"server ready at {base}")
    print(json.dumps({
        "base": base, "pid": pid, "config_dir": str(config_dir),
        "capture_dir": str(capture_dir), "log": str(log_path),
        "auth": bool(args.auth),
        "creds_file": str(config_dir / "probe_users.json") if creds else None,
        "sim_solve_fault": fault,
    }))
    return 0


# -------------------------------------------------------------------- stop

def cmd_stop(args: argparse.Namespace) -> int:
    config_dir = Path(args.config_dir).resolve()
    pid_path = config_dir / "server.pid"
    if not pid_path.exists():
        _log(f"no pid file at {pid_path} -- nothing to stop")
        return 0
    pid = pid_path.read_text(encoding="utf-8").strip()
    _log(f"stopping server pid={pid} (taskkill /T /F)")
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", pid, "/T", "/F"],
            capture_output=True, text=True)
        print(result.stdout.strip())
        if result.returncode not in (0, 128):  # 128: process already gone
            print(result.stderr.strip(), file=sys.stderr)
    else:
        try:
            os.kill(int(pid), 15)
        except ProcessLookupError:
            pass
    pid_path.unlink(missing_ok=True)
    _log("stopped")
    return 0


# --------------------------------------------------------------------- cli

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR),
                   help=f"ASTRODECK_CONFIG_DIR (default {DEFAULT_CONFIG_DIR})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="server_ctl")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="start an isolated server")
    _add_common(p_start)
    p_start.add_argument("--capture-dir", default=str(DEFAULT_CAPTURE_DIR),
                         help=f"ASTRODECK_CAPTURE_DIR (default {DEFAULT_CAPTURE_DIR})")
    p_start.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_start.add_argument("--venv-python", default=str(DEFAULT_VENV_PYTHON),
                         help="server venv python (launches the server ONLY)")
    p_start.add_argument("--ui-dir", default=None,
                         help="ASTRODECK_UI_DIR override (default <repo>/ui/dist "
                             "-- see the comment at its use site for why this "
                             "is set explicitly rather than left to the "
                             "server's own repo-relative fallback)")
    p_start.add_argument("--fresh", action="store_true",
                         help="wipe config-dir and capture-dir before starting")
    p_start.add_argument("--auth", action="store_true",
                         help="bootstrap local auth + probe_admin/operator/viewer")
    p_start.add_argument("--sim", dest="sim", action="store_true", default=True,
                         help="connect the simulator rig and wait (default on)")
    p_start.add_argument("--no-sim", dest="sim", action="store_false",
                         help="skip connecting the simulator rig")
    p_start.add_argument("--timeout", type=float, default=60.0,
                         help="seconds to wait for /healthz (default 60)")
    p_start.add_argument("--sim-timeout", type=float, default=30.0,
                         help="seconds to wait for sim camera+telescope connected")
    p_start.add_argument("--sim-solve-fault", default=None, metavar="RA_H,DEC,RADIUS",
                         help=f"stage a solve failure: every simulator solve within "
                             f"RADIUS deg of (RA_H hours, DEC deg) fails "
                             f"({SOLVE_FAULT_ENV}); off unless given")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="stop a server started by this tool")
    _add_common(p_stop)
    p_stop.set_defaults(func=cmd_stop)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
