#!/usr/bin/env python3
"""Assemble a release bundle: server source + ui/dist + optional vendored assets
+ manifest -> ``.tar.gz``.

    python scripts/build_release.py --version 0.2.0 --out dist \
        --astap-dir release-assets/astap \
        --survey-pack release-assets/dss2color

Layout inside the tarball (single top dir ``astrodeck-<version>/``):

    server/astrodeck/...                            the package
    server/astrodeck/vendor/astap/                  bundled ASTAP binary + D05 DB
    server/astrodeck/catalog/_bundled_pack/dss2color/  baseline survey pack
    server/pyproject.toml
    ui/dist/...                                     the prebuilt SPA
    manifest.json                                   {name, version, built_at, contents}

The UI dist and BOTH vendored asset trees are OPTIONAL (a warning is printed and
that asset is omitted) so the bundler can be smoke-tested without a node build or
the ~150 MB of binaries; CI populates them before calling this.

Producing the assets (separate, network-heavy — not this script's job):
  * ASTAP: download the per-OS ``astap_cli`` from github.com/han-k59/astap plus the
    D05 star DB (``*.290``) into one dir; rename ``astap_cli``->``astap`` if desired.
  * Survey pack (order-3 baseline, ~45 MB):
      python -m astrodeck.catalog.survey_pack fetch --order 3 --dest release-assets/dss2color

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

# MPL-2.0 lets us redistribute the astap_cli binary inside the release as long as
# we ship this notice (license text + source link) and the Gaia DB credit.
_ASTAP_NOTICE = """\
ASTAP command-line plate solver (astap_cli) — bundled with AstroDeck.

ASTAP is licensed under the Mozilla Public License 2.0 (MPL-2.0).
Full license: https://www.mozilla.org/MPL/2.0/
Source:       https://github.com/han-k59/astap

The bundled star database is derived from ESA/Gaia data:
    "This work has made use of data from the European Space Agency (ESA)
     mission Gaia, processed by the Gaia Data Processing and Analysis
     Consortium (DPAC)."  Credit: ESA/Gaia/DPAC.

MPL-2.0 is file-scoped copyleft covering ASTAP's own source only; it does not
affect AstroDeck's license.
"""


def _stage_astap(astap_dir: Path, pkg_root: Path) -> list[str]:
    """Copy the vendored ASTAP binary + star DB into the staged package at
    ``astrodeck/vendor/astap/`` (where ``solve/astap.py`` discovers both the binary
    and, via ``-d``, the ``.290`` DB). Writes the MPL/Gaia NOTICE. Warns loudly (no
    silent gap) when the binary or DB is missing. Returns manifest content labels."""
    dest = pkg_root / "vendor" / "astap"
    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(astap_dir.iterdir()):
        if item.is_dir():
            shutil.copytree(item, dest / item.name, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest / item.name)
    (dest / "NOTICE.txt").write_text(_ASTAP_NOTICE, encoding="utf-8")
    has_bin = any((dest / n).exists()
                  for n in ("astap", "astap.exe", "astap_cli", "astap_cli.exe"))
    has_db = any(dest.glob("*.290")) or any(dest.glob("*.1476"))
    if not has_bin:
        print(f"WARNING: {astap_dir} has no astap binary -- the box will fall back "
              "to a system ASTAP install (or have no solver)")
    if not has_db:
        print(f"WARNING: {astap_dir} has no star DB (*.290/*.1476) -- ASTAP will "
              "rely on its own DB location")
    return ["server/astrodeck/vendor/astap"]


def _stage_survey_pack(pack_dir: Path, pkg_root: Path, slug: str = "dss2color") -> list[str]:
    """Copy a baseline HiPS pack into the staged package at
    ``astrodeck/catalog/_bundled_pack/<slug>/`` (read by
    ``survey_pack.seed_bundled_pack`` on first boot). Requires a ``pack.json`` — the
    seed gates on the manifest, so a pack without it would never be recognized."""
    if not (pack_dir / "pack.json").is_file():
        print(f"WARNING: {pack_dir} has no pack.json -- NOT bundling a baseline survey "
              "pack (a fresh box boots to the 'no survey source' Atlas CTA)")
        return []
    dest = pkg_root / "catalog" / "_bundled_pack" / slug
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pack_dir, dest, ignore=_IGNORE, dirs_exist_ok=True)
    return [f"server/astrodeck/catalog/_bundled_pack/{slug}"]


def build(version: str, repo_root: Path, out_dir: Path,
          astap_dir: Path | None = None,
          survey_pack_dir: Path | None = None, strict: bool = False) -> Path:
    repo_root = Path(repo_root).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    staging = out_dir / f"astrodeck-{version}"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "server").mkdir(parents=True)

    # server: the package + pyproject (enough for `pip install ./server`).
    srv = repo_root / "server"
    pkg_root = staging / "server" / "astrodeck"
    shutil.copytree(srv / "astrodeck", pkg_root, ignore=_IGNORE)
    shutil.copy2(srv / "pyproject.toml", staging / "server" / "pyproject.toml")

    contents = ["server", "ui/dist"]

    # Missing assets are collected and decided ONCE, at the end. A warning
    # printed during an unattended build is not a control: 0.2.18 shipped with
    # no survey pack and no online fetch, so the Atlas on the deployed box was
    # simply black, and the only evidence was a line in a build log nobody read
    # (#100). `--strict` turns every omission into a failed build.
    missing: list[str] = []

    # ui/dist: prebuilt SPA.
    ui_dist = repo_root / "ui" / "dist"
    if ui_dist.is_dir():
        shutil.copytree(ui_dist, staging / "ui" / "dist", ignore=_IGNORE)
    else:
        missing.append(f"the built UI ({ui_dist}) — the release would serve no "
                       "interface at all")

    # vendored ASTAP binary + D05 star DB (UX-04).
    if astap_dir is not None and Path(astap_dir).is_dir():
        contents += _stage_astap(Path(astap_dir), pkg_root)
    elif astap_dir is not None:
        missing.append(f"ASTAP (--astap-dir {astap_dir}) — plate solving would "
                       "need a separately-installed ASTAP")

    # baseline survey pack for first-boot seeding (UX-07).
    if survey_pack_dir is not None and Path(survey_pack_dir).is_dir():
        contents += _stage_survey_pack(Path(survey_pack_dir), pkg_root)
    elif survey_pack_dir is not None:
        missing.append(f"the baseline survey pack (--survey-pack "
                       f"{survey_pack_dir}) — the Atlas would have no image "
                       "source offline, which is the default")

    if missing:
        head = ("release is missing assets that were asked for:"
                if strict else "WARNING: release is missing assets:")
        print(head)
        for m in missing:
            print(f"  - {m}")
        if strict:
            raise SystemExit(
                "refusing to build an incomplete release (--strict). Provide the "
                "assets, or drop the flag if you meant to omit them.")

    manifest = {
        "name": "astrodeck",
        "version": version,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "contents": contents,
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--strict", action="store_true",
                    help="fail the build when a requested asset is "
                         "missing, instead of warning and shipping "
                         "without it (what CI should use)")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--repo-root",
                    default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--astap-dir", default=None,
                    help="dir with the astap_cli binary + D05 star DB to vendor")
    ap.add_argument("--survey-pack", default=None, dest="survey_pack",
                    help="dir with a baseline HiPS pack (must contain pack.json)")
    args = ap.parse_args()
    build(args.version, Path(args.repo_root), Path(args.out),
          astap_dir=Path(args.astap_dir) if args.astap_dir else None,
          survey_pack_dir=Path(args.survey_pack) if args.survey_pack else None,
          strict=args.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
