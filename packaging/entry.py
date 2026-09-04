"""Entry point for the single-file build.

Not a copy of ``astrodeck.__main__`` — it defers to it, so the CLI can never
drift between the binary and ``python -m astrodeck``. What it adds is the two
things a downloaded binary needs and a source checkout does not.

**A default state directory.** Run from a checkout, AstroDeck writes config and
captures beside the source. A binary has no such place: its own directory is
wherever the file happened to be saved (a Downloads folder, a read-only volume,
a USB stick that gets pulled). So unless the user has said otherwise, both land
under the OS's normal per-user data location.

**Saying where things are.** A user who double-clicks a binary has no terminal
history to scroll back through and no obvious answer to "where did my pictures
go?". The binary prints the address to open and the paths it is using before the
server starts.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def default_state_dir() -> Path:
    """Per-user application data, by the convention of the running OS."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "AstroDeck"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AstroDeck"
    # Linux and the rest: XDG, falling back to its documented default.
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "astrodeck"


def main() -> None:
    # PyInstaller + multiprocessing: without this, a child process re-runs the
    # bootloader and starts a SECOND server instead of the worker it meant to.
    multiprocessing.freeze_support()

    state = default_state_dir()
    # Only fill these in when the user has not. An explicit env var, a service
    # unit, or a docker-compose file always wins.
    config_dir = os.environ.get("ASTRODECK_CONFIG_DIR") or str(state / "config")
    capture_dir = os.environ.get("ASTRODECK_CAPTURE_DIR") or str(state / "captures")
    os.environ.setdefault("ASTRODECK_CONFIG_DIR", config_dir)
    os.environ.setdefault("ASTRODECK_CAPTURE_DIR", capture_dir)
    # Import only after the environment is final: config paths are module-level
    # constants in the application package.  Secure config before the generic
    # directory loop and before printing paths/status to a double-click console.
    from astrodeck.persist import secure_private_tree
    try:
        secure_private_tree(Path(config_dir))
    except RuntimeError as e:
        print(
            f"cannot secure private configuration at {config_dir}: {e}",
            file=sys.stderr,
        )
        raise SystemExit(2) from e
    for d in (config_dir, capture_dir):
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # A read-only or unwritable location is worth failing on loudly and
            # immediately, rather than at the first frame of the night.
            print(f"cannot create {d}: {e}", file=sys.stderr)
            raise SystemExit(2) from e

    # Only for the plain `run` path — a subcommand like create-admin is not a
    # server start and printing an address for it would be a lie.
    argv = sys.argv[1:]
    if not argv or argv[0] == "run" or argv[0].startswith("-"):
        port = "8800"
        for i, a in enumerate(argv):
            if a == "--port" and i + 1 < len(argv):
                port = argv[i + 1]
            elif a.startswith("--port="):
                port = a.split("=", 1)[1]
        from astrodeck import __version__
        print(f"AstroDeck {__version__}")
        print(f"  open        http://localhost:{port}")
        print(f"  settings    {config_dir}")
        print(f"  captures    {capture_dir}")
        print("  stop        Ctrl-C")
        print()

    # main() RETURNS the exit code; astrodeck.__main__ raises SystemExit around
    # it. Swallowing that would make every failure look like success to a
    # service manager or a shell script.
    from astrodeck.__main__ import main as cli
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
