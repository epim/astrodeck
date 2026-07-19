"""python -m astrodeck.comhost — the bundled COM->Alpaca host entry (COM-T2).

Binds loopback-only, publishes its chosen ephemeral port to --portfile, prints
a READY line, and serves until terminated. Windows-only in practice (COM), but
starts anywhere for the portable contract tests.
"""
from __future__ import annotations

import argparse
import sys
import time

from .server import serve


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="astrodeck-comhost")
    ap.add_argument("--port", type=int, default=0,
                    help="loopback port (0 = ephemeral, recommended)")
    ap.add_argument("--portfile", default=None,
                    help="path to write {'pid','port'} JSON for the server")
    a = ap.parse_args(argv)
    httpd = serve(port=a.port, portfile=a.portfile)
    print(f"comhost READY 127.0.0.1:{httpd.server_address[1]}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    finally:
        httpd.com_host.close()
        httpd.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
