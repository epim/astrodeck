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
    manifest.json                    {name, version, built_at, contents, omitted}

The UI dist and BOTH vendored asset trees are OPTIONAL by default (a warning is
printed and that asset is omitted) so the bundler can be smoke-tested without a
node build or the ~150 MB of binaries; CI populates them before calling this.

``--strict`` — what .github/workflows/release.yml passes — turns every one of
those omissions into a failed build, INCLUDING an asset the command line never
mentioned: #100 was not a flag that failed, it was a flag nobody passed. An
omission has to be named (``--allow-missing astap``) to be allowed, so it is a
decision at the call site and a line in the shipped ``manifest.json`` rather than
something a user discovers after installing (see ``_ASSETS`` and ``build``).

Producing the assets (separate, network-heavy — not this script's job):
  * ASTAP: ``python scripts/fetch_astap.py --platform <plat> --out <dir>``, then
    move ``<dir>/<plat>/astap_cli*`` UP beside the ``.290`` files before pointing
    ``--astap-dir`` at ``<dir>``. That step is not optional and not cosmetic:
    fetch_astap nests the binary per platform while the star DB lands flat, and
    ``solve/astap.py`` reads ONE flat directory — so the fetched tree as-is has
    no binary the runtime can see, and ``--strict`` says so rather than shipping
    102 MB of database next to a solver nothing will run.
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

#: What a COMPLETE release contains: asset -> (what to call it, what shipping
#: without it costs the USER). ``--strict`` fails on any of these that did not
#: land, whether or not the command line asked for it — a check that only
#: inspects the assets it was handed cannot notice the one that was forgotten,
#: and forgetting is exactly how 0.2.18 shipped with no survey pack (#100).
_ASSETS: dict[str, tuple[str, str]] = {
    "ui": ("the built UI",
           "the release would serve the API and no interface at all"),
    "astap": ("ASTAP (solver binary + star database)",
              "plate solving would need a separately-installed ASTAP on the box"),
    # The baseline survey pack USED to be here, and --strict failed without it.
    # It is gone because a complete release now DELIBERATELY ships no DSS2
    # tiles (#198): they are All Rights Reserved and the only published grant
    # is for use, not for us to pass them on. Leaving the entry would have made
    # --strict block every release on an asset we are refusing to include —
    # a check demanding the thing the fix removed.
    #
    # The user-facing cost is real and is not hidden: a fresh install draws the
    # Atlas's offline schematic sky until the operator fetches tiles, which the
    # Atlas offers and which their own use grant covers.
}


def _stage_astap(astap_dir: Path, pkg_root: Path) -> tuple[list[str], str | None]:
    """Copy the vendored ASTAP binary + star DB into the staged package at
    ``astrodeck/vendor/astap/`` and write the MPL/Gaia NOTICE.

    Returns ``(manifest labels, what the RUNTIME would not find)``. The second
    value is the strict check, and it asks ``solve/astap.py``'s questions rather
    than easier ones: ``_bundled_candidates()`` looks for the binary FLAT in this
    one directory, so the per-platform layout ``scripts/fetch_astap.py
    --all-platforms`` produces (``<dir>/linux-x86_64/astap_cli``) is invisible to
    it; and ``_bundled_db_dir()`` accepts any of ``DB_EXTENSIONS``, including
    W08's ``.001``, which this check used not to and so called a real bundle
    databaseless. A staged tree the runtime cannot use is not a shipped asset, so
    it returns NO label — the manifest must not advertise a solver that will
    never be found."""
    dest = pkg_root / "vendor" / "astap"
    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(astap_dir.iterdir()):
        if item.is_dir():
            shutil.copytree(item, dest / item.name, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest / item.name)
    (dest / "NOTICE.txt").write_text(_ASTAP_NOTICE, encoding="utf-8")
    problems: list[str] = []
    if not any((dest / n).exists()
               for n in ("astap", "astap.exe", "astap_cli", "astap_cli.exe")):
        problems.append(
            f"{astap_dir} has no astap/astap_cli at its top level, the only place "
            "solve/astap.py::_bundled_candidates looks (a per-platform subdir is "
            "not seen), so the box would fall back to a system ASTAP or no solver")
    # next(..., None): Path.glob returns a GENERATOR, which is always truthy —
    # the same trap _bundled_db_dir documents, and it would report a database in
    # an empty directory.
    if not any(next(dest.glob(f"*{ext}"), None) is not None
               for ext in (".290", ".1476", ".001")):
        problems.append(
            f"{astap_dir} has no star database (*.290/*.1476/*.001), so the "
            "bundled solver gets no -d and falls back to whatever the box has")
    if problems:
        return [], "; ".join(problems)
    return ["server/astrodeck/vendor/astap"], None


#: Slugs whose tiles we are not licensed to hand to anybody (#198). Kept here as
#: well as in ``astrodeck.licensing`` because this script must run without
#: importing the package it is packaging.
_UNSHIPPABLE_SLUGS = frozenset({"dss2color"})


def _stage_survey_pack(pack_dir: Path, pkg_root: Path,
                       slug: str = "dss2color") -> tuple[list[str], str | None]:
    """Copy a baseline HiPS pack into the staged package at
    ``astrodeck/catalog/_bundled_pack/<slug>/`` (read by
    ``survey_pack.seed_bundled_pack`` on first boot). Requires a ``pack.json`` — the
    seed gates on the manifest, so a pack without it would never be recognized.

    REFUSES A SLUG WE MAY NOT REDISTRIBUTE (#198). ``seed_bundled_pack`` already
    declines to INSTALL DSS2 tiles, but that is the second line: shipping is the
    infringement, and a tarball containing 45 MB of All-Rights-Reserved imagery
    infringes whether or not anything ever unpacks it. So the refusal has to be
    here too, where the file would actually be copied into the artifact."""
    if slug in _UNSHIPPABLE_SLUGS:
        return [], (
            f"{slug} tiles are not ours to redistribute — DSS is All Rights "
            f"Reserved and the only published grant is for USE, not for us to "
            f"pass them on. The release ships none, and the Atlas fetches them "
            f"on the operator's own machine, which that grant does cover. See "
            f"astrodeck/licensing.py.")
    if not (pack_dir / "pack.json").is_file():
        return [], (f"{pack_dir} has no pack.json, and survey_pack.pack_present() "
                    "gates on it, so a pack copied without its manifest reads as "
                    "absent on the box")
    dest = pkg_root / "catalog" / "_bundled_pack" / slug
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pack_dir, dest, ignore=_IGNORE, dirs_exist_ok=True)
    return [f"server/astrodeck/catalog/_bundled_pack/{slug}"], None


def build(version: str, repo_root: Path, out_dir: Path,
          astap_dir: Path | None = None,
          survey_pack_dir: Path | None = None, strict: bool = False,
          allow_missing: tuple[str, ...] | list[str] = ()) -> Path:
    unknown = sorted(set(allow_missing) - set(_ASSETS))
    if unknown:
        raise SystemExit(f"--allow-missing: no such asset {', '.join(unknown)}; "
                         f"pick from {', '.join(_ASSETS)}. A waiver that names "
                         "nothing waives nothing.")
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

    contents = ["server"]

    # Missing assets are collected and decided ONCE, at the end: one build names
    # every absence, because one rebuild per discovery is how a release takes an
    # afternoon. Keyed by asset so `--allow-missing` can waive one by name.
    missing: dict[str, str] = {}

    # ui/dist: prebuilt SPA. Gate on the pair api/app.py mounts on —
    # `index.html`.is_file() AND `assets`.is_dir() — not on the directory and
    # not on index.html alone. A `npm run build` that half-ran leaves a dist
    # that passes the weaker tests; the server then boots, /healthz is green,
    # /assets/* 404s and the page is blank. The bundler has to ask the runtime's
    # question or it certifies a release the runtime will refuse to serve.
    ui_dist = repo_root / "ui" / "dist"
    if (ui_dist / "index.html").is_file() and (ui_dist / "assets").is_dir():
        shutil.copytree(ui_dist, staging / "ui" / "dist", ignore=_IGNORE)
        contents.append("ui/dist")
    else:
        # ASCII in everything that gets PRINTED: this text is the whole report
        # on a failed release, and a Windows console in an OEM code page raises
        # UnicodeEncodeError on an em dash -- turning "your release is missing
        # the UI" into a traceback about character encoding.
        missing["ui"] = (f"{ui_dist} is not a built SPA -- api/app.py mounts one "
                         "only when index.html and assets/ are BOTH there")

    # vendored ASTAP binary + D05 star DB (UX-04). Produced by
    # scripts/fetch_astap.py; not committed (the smallest DB is 102 MB).
    if astap_dir is not None and Path(astap_dir).is_dir():
        labels, problem = _stage_astap(Path(astap_dir), pkg_root)
        contents += labels
        if problem:
            missing["astap"] = problem
    else:
        missing["astap"] = (f"{astap_dir} is not a directory"
                            if astap_dir is not None else
                            "no --astap-dir was passed "
                            "(scripts/fetch_astap.py produces one)")

    # Baseline survey pack. NO LONGER A REQUIRED ASSET (#198) — if one is
    # passed anyway, the staging refusal below is what actually keeps DSS2
    # tiles out of the artifact, and it PRINTS rather than staying silent so a
    # caller still passing --survey-pack learns why nothing landed instead of
    # concluding the flag worked.
    if survey_pack_dir is not None and Path(survey_pack_dir).is_dir():
        labels, problem = _stage_survey_pack(Path(survey_pack_dir), pkg_root)
        contents += labels
        if problem:
            print(f"survey pack not staged: {problem}")

    if missing:
        blocking = sorted(a for a in missing if a not in allow_missing)
        print("release is missing assets a complete release has:" if strict
              else "WARNING: release is missing assets:")
        for asset in sorted(missing):
            label, cost = _ASSETS[asset]
            waived = " [waived: --allow-missing]" if asset not in blocking else ""
            print(f"  - {label}: {missing[asset]} -- {cost}{waived}")
        if strict and blocking:
            raise SystemExit(
                "refusing to build an incomplete release (--strict): "
                + ", ".join(blocking) + ". Produce the assets, or name the ones "
                "you mean to leave out (--allow-missing <asset>) so the omission "
                "ships in the manifest instead of being discovered on the box.")

    manifest = {
        "name": "astrodeck",
        "version": version,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "contents": contents,
        # What this release does NOT have, and what that costs. 0.2.18's only
        # record of its missing survey pack was a line in a build log nobody
        # read; a manifest travels with the bundle to the box that has the
        # black Atlas, so the artifact can answer for itself.
        "omitted": {a: _ASSETS[a][1] for a in sorted(missing)},
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
                    help="fail the build when any asset a complete release has "
                         "(%s) did not land, asked for or not, instead of "
                         "warning and shipping without it (what CI uses)"
                         % ", ".join(_ASSETS))
    ap.add_argument("--allow-missing", action="append", default=[],
                    dest="allow_missing", choices=sorted(_ASSETS),
                    metavar="ASSET",
                    help="ship without this asset ON PURPOSE, repeatable (%s). "
                         "The omission and its cost go into manifest.json."
                         % ", ".join(sorted(_ASSETS)))
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
          strict=args.strict, allow_missing=args.allow_missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
