#!/usr/bin/env python3
"""Assemble a release bundle: server source + ui/dist + manifest -> ``.tar.gz``.

    python scripts/build_release.py --version 0.2.0 --out dist

Layout inside the tarball (single top dir ``astrodeck-<version>/``):

    server/astrodeck/...     the package (enough to `pip install ./server`)
    server/pyproject.toml
    ui/dist/...              the prebuilt SPA (CI builds it first)
    manifest.json           {name, version, built_at, contents}

The UI dist is OPTIONAL locally (a warning is printed) so the bundler can be
smoke-tested without a node build; CI always builds the UI before calling this.
Prints the tarball path on success.
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import tarfile
from pathlib import Path

_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "config",
)


def build(version: str, repo_root: Path, out_dir: Path) -> Path:
    repo_root = Path(repo_root).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    staging = out_dir / f"astrodeck-{version}"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "server").mkdir(parents=True)

    # server: the package + pyproject (enough for `pip install ./server`).
    srv = repo_root / "server"
    shutil.copytree(srv / "astrodeck", staging / "server" / "astrodeck", ignore=_IGNORE)
    shutil.copy2(srv / "pyproject.toml", staging / "server" / "pyproject.toml")

    # ui/dist: prebuilt SPA (optional locally).
    ui_dist = repo_root / "ui" / "dist"
    if ui_dist.is_dir():
        shutil.copytree(ui_dist, staging / "ui" / "dist", ignore=_IGNORE)
    else:
        print(f"WARNING: {ui_dist} missing -- bundling WITHOUT the UI "
              "(CI must build it first)")

    manifest = {
        "name": "astrodeck",
        "version": version,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "contents": ["server", "ui/dist"],
    }
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    tarball = out_dir / f"astrodeck-{version}.tar.gz"
    if tarball.exists():
        tarball.unlink()
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(staging, arcname=f"astrodeck-{version}")

    print(str(tarball))
    return tarball


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default="dist")
    ap.add_argument("--repo-root",
                    default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args()
    build(args.version, Path(args.repo_root), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
