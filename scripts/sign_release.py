#!/usr/bin/env python3
"""Sign one or more release artifacts, writing ``.sha256`` + ``.sig`` sidecars.

    RELEASE_SIGNING_KEY=<base64-seed> python scripts/sign_release.py dist/astrodeck-0.2.0.tar.gz

Reads the Ed25519 private seed from the ``RELEASE_SIGNING_KEY`` environment
variable (the GitHub Actions secret). Accepts explicit paths and/or globs. Exits
non-zero if the key is missing or no artifact matched -- so a release build fails
loudly rather than publishing an unsigned artifact.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from astrodeck.update import signing  # noqa: E402


def main(argv: list[str]) -> int:
    seed = (os.environ.get("RELEASE_SIGNING_KEY") or "").strip()
    if not seed:
        print("error: RELEASE_SIGNING_KEY is not set", file=sys.stderr)
        return 2

    paths: list[Path] = []
    for arg in argv:
        matches = glob.glob(arg)
        paths.extend(Path(m) for m in matches) if matches else paths.append(Path(arg))

    real = [p for p in paths if p.is_file()]
    if not real:
        print(f"error: no artifact matched {argv}", file=sys.stderr)
        return 2

    for p in real:
        sha_path, sig_path = signing.write_sidecars(p, seed)
        print(f"signed {p.name} -> {sha_path.name}, {sig_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
