#!/usr/bin/env python3
"""Generate the credits data the UI ships, FROM THE REAL MANIFESTS.

    python tools/gen_credits.py            # write ui/src/credits.generated.json
    python tools/gen_credits.py --check    # exit 1 if that file is out of date

Why generated and not written by hand: a hand-maintained credits list is wrong
the day after it ships, and being wrong here is a licence breach rather than a
cosmetic defect. Every entry below is read out of something that already had to
be correct for the build to work — the installed Python environment,
``ui/package-lock.json``, ``native/Cargo.lock``, the vendor directory, the
catalogue data directory. Add a dependency and its credit appears; the only way
to ship an uncredited dependency is to delete this generator.

Four things are NOT in any manifest, and each gets a different treatment rather
than a shrug:

* **Vendored binaries** — discovered by walking ``server/astrodeck/vendor/*``,
  so a new vendor directory cannot be added silently. Their licence text is the
  ``LICENSE`` file sitting next to the binary.
* **Shipped data** — discovered by walking ``server/astrodeck/catalog/data``.
  Provenance comes from the registry, keyed by filename, and a file with no
  registry entry fails the build.
* **Network services and external programs** — these genuinely have no
  manifest, so they live in ``tools/credits_registry.py``. What keeps THAT
  honest is not discipline: ``server/tests/test_credits.py`` greps the server
  source for outbound hosts and fails when one is not credited.
* **Clean-room ports** (PHD2, NINA/Hocus Focus) — obligations that arise from
  reading source, not from installing a package. Registry, and the existing
  ``THIRD-PARTY-NOTICES.md`` remains their long-form home.

Output shape: one JSON file with a de-duplicated licence-text pool. Apache-2.0's
text alone appears against ~40 crates; storing it once and referencing it by
content hash takes the payload from ~1.4 MB to something the UI can simply
import. Nothing in the output is timestamped, so regenerating without changing a
dependency produces a byte-identical file and ``--check`` means something.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import credits_registry as registry  # noqa: E402
import licence_policy as policy  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "ui" / "src" / "credits.generated.json"

#: Filenames that carry a licence, in the order we prefer to show them. Matched
#: case-insensitively against the whole name.
_LICENCE_FILE = re.compile(
    r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS|COPYRIGHT|OFL)([-._].*)?$", re.I)


#: Below this, a "licence file" is a pointer to one, not a licence.
_MIN_LICENCE_CHARS = 200


class Fatal(Exception):
    """Something the generator will not paper over."""


@dataclass
class Entry:
    name: str
    version: str
    spdx: str
    tier: str
    group: str
    url: str = ""
    notes: str = ""
    requires: tuple[str, ...] = ()
    summaries: list[str] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)
    flag: str | None = None

    def as_json(self) -> dict:
        d = {
            "name": self.name, "version": self.version, "spdx": self.spdx,
            "tier": self.tier, "requires": list(self.requires),
            "summaries": self.summaries, "texts": self.texts,
        }
        if self.url:
            d["url"] = self.url
        if self.notes:
            d["notes"] = self.notes
        if self.flag:
            d["flag"] = self.flag
        return d


class TextPool:
    """Content-addressed store of licence bodies.

    Deduplication is not just a size trick. Two packages that ship byte-identical
    Apache-2.0 text genuinely have the same obligation discharged by the same
    words, and showing the reader one copy referenced 40 times is more honest
    about that than 40 scrolling copies.
    """

    def __init__(self) -> None:
        self._by_hash: dict[str, str] = {}

    def add(self, body: str) -> str:
        body = body.replace("\r\n", "\n").strip("\n")
        key = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        self._by_hash.setdefault(key, body)
        return key

    def as_json(self) -> dict[str, str]:
        return dict(sorted(self._by_hash.items()))


def _read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise Fatal(f"cannot decode {path}")


def _gather_texts(pool: TextPool, root: Path, rel_to: Path | None = None) -> list[dict]:
    """Collect every licence-ish file under ``root`` (recursively).

    Recursion matters: ``httptools`` ships its vendored llhttp and http-parser
    notices at ``licenses/vendor/<project>/LICENSE``, and a flat listing would
    drop two third-party notices that we redistribute inside a wheel.
    """
    if not root.is_dir():
        return []
    out: list[dict] = []
    base = rel_to or root
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _LICENCE_FILE.match(path.name):
            continue
        body = _read_text(path).strip()
        # Crates routinely ship a LICENSE file whose entire content is a pointer
        # -- nalgebra-macros' is the five bytes "../LICENSE", typenum's is the
        # string "MIT OR Apache-2.0". Reproducing that discharges nothing, and
        # letting it through would make the "every entry has text" guard pass on
        # a file that is not a licence. Anything this short is a pointer.
        if len(body) < _MIN_LICENCE_CHARS:
            continue
        try:
            title = str(path.relative_to(base)).replace("\\", "/")
        except ValueError:
            title = path.name
        out.append({"title": title, "hash": pool.add(body)})
    return out


def _canonical(pool: TextPool, spdx: str | None) -> list[dict]:
    """Fall back to the canonical text of a licence that has no per-package text.

    Only safe for licences whose wording does not embed a copyright holder --
    Apache-2.0 and MPL-2.0 are identical for every user, so reproducing the
    canonical copy genuinely discharges the obligation. MIT deliberately has no
    fallback: its text carries the holder's name, and a generic MIT body would
    credit nobody while looking like it had.
    """
    for part in (spdx or "").replace("/", " OR ").split(" OR "):
        body = registry.text_for_spdx(part.strip())
        if body:
            return [{"title": f"{part.strip()} (canonical text)", "hash": pool.add(body)}]
    return []


def _resolve(entry_name: str, spdx: str | None) -> tuple[policy.Resolution | None, str | None]:
    """Map a licence string to obligations, converting a Flag into an entry field.

    A flagged licence does not stop the generator dead — it becomes a visible
    entry in the "needs an owner decision" group. Stopping would tempt someone
    to delete the check; surfacing puts the decision on a screen the owner reads.
    """
    try:
        return policy.resolve(spdx), None
    except policy.Flag as exc:
        return None, f"{entry_name}: {exc}"


def _entry(pool: TextPool, *, name: str, version: str, spdx: str | None,
           tier: str, group: str, url: str = "", notes: str = "",
           texts: list[dict] | None = None,
           requires: tuple[str, ...] | None = None,
           summaries: list[str] | None = None,
           flag: str | None = None) -> Entry:
    res, auto_flag = _resolve(name, spdx)
    # An explicit registry flag says something specific ("the source URL 404s")
    # where the automatic one can only say "unrecognised". Prefer the specific.
    flag = flag or auto_flag
    ent = Entry(
        name=name, version=version, spdx=(spdx or "UNKNOWN"), tier=tier,
        group=group, url=url, notes=notes, texts=texts or [], flag=flag,
    )
    if res is not None:
        ent.requires = requires if requires is not None else res.requires
        ent.summaries = summaries if summaries is not None else res.summaries
    else:
        ent.requires = requires or ()
        ent.summaries = summaries or []
    return ent


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

def _walk(seeds: list[tuple[str, frozenset[str]]]) -> tuple[dict, list[str]]:
    import importlib.metadata as md
    from packaging.requirements import Requirement

    found: dict[str, object] = {}
    missing: list[str] = []
    stack = list(seeds)
    seen: set[tuple[str, frozenset[str]]] = set()
    while stack:
        name, extras = stack.pop()
        key = (name.lower().replace("_", "-"), extras)
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            missing.append(name)
            continue
        found[dist.metadata["Name"]] = dist
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker and not any(req.marker.evaluate({"extra": e})
                                      for e in (extras or frozenset({""}))):
                continue
            stack.append((req.name, frozenset(req.extras)))
    return found, missing


def _seeds(specs: list[str]) -> list[tuple[str, frozenset[str]]]:
    from packaging.requirements import Requirement
    out = []
    for spec in specs:
        req = Requirement(spec)
        out.append((req.name, frozenset(req.extras)))
    return out


def _python_closure() -> dict[str, object]:
    """Runtime dependency closure of ``server/pyproject.toml``, from the live env.

    Deliberately the INSTALLED environment rather than the top-level list: the
    task is to credit what ships, and ``uvicorn[standard]`` alone drags in six
    packages that a reader of pyproject.toml never sees. The PyInstaller
    single-file binary bundles every one of them, so every one is redistributed.

    Core and optional dependencies are walked separately because a missing one
    means different things. A core dependency that is not installed makes the
    credits unverifiable and stops the generator. An optional extra
    (``comhost``'s comtypes, ``asiair``'s libasi) is routinely absent — libasi
    is not even on PyPI — yet it still ships to anyone who installs the extra,
    so it is credited from the registry with its absence recorded rather than
    dropped.
    """
    pyproject = tomllib.loads(_read_text(REPO / "server" / "pyproject.toml"))
    proj = pyproject["project"]
    core = _seeds(proj["dependencies"])
    optional: list[tuple[str, frozenset[str]]] = []
    for extra, specs in proj.get("optional-dependencies", {}).items():
        if extra == "dev":
            continue  # test-only; never redistributed
        optional += _seeds(specs)

    found, missing_core = _walk(core)
    if missing_core:
        raise Fatal(
            "these declared runtime dependencies are not installed, so their "
            f"licence text cannot be read: {sorted(missing_core)}. Install them "
            "and re-run.")
    opt_found, missing_opt = _walk(optional)
    for name, dist in opt_found.items():
        found.setdefault(name, dist)
    unknown = [n for n in missing_opt if n.lower() not in registry.OPTIONAL_UNINSTALLED]
    if unknown:
        raise Fatal(
            f"optional dependencies {sorted(unknown)} are neither installed nor "
            "described in credits_registry.OPTIONAL_UNINSTALLED. An extra that "
            "ships to users still needs a credit entry.")
    return {"dists": found, "missing": sorted(set(missing_opt))}


def _python_licence(dist) -> str | None:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    raw = (meta.get("License") or "").strip()
    if raw and "\n" not in raw and len(raw) < 60:
        return raw
    classifiers = [c for c in (meta.get_all("Classifier") or [])
                   if c.startswith("License ::")]
    names = [c.rsplit("::", 1)[-1].strip() for c in classifiers]
    return " OR ".join(names) if names else None


def _python_url(dist) -> str:
    meta = dist.metadata
    for key in (meta.get_all("Project-URL") or []):
        label, _, url = key.partition(",")
        if label.strip().lower() in ("homepage", "source", "repository", "home"):
            return url.strip()
    return (meta.get("Home-page") or "").strip()


def collect_python(pool: TextPool) -> list[Entry]:
    closure = _python_closure()
    out: list[Entry] = []
    for name in closure["missing"]:
        spec = registry.OPTIONAL_UNINSTALLED[name.lower()]
        out.append(_entry(
            pool, name=spec["name"], version=spec.get("version", ""),
            spdx=spec["spdx"], tier="redistributed-optional", group="python",
            url=spec.get("url", ""), notes=spec["notes"], flag=spec.get("flag"),
            texts=[{"title": t["title"], "hash": pool.add(t["body"])}
                   for t in spec.get("texts", [])]))
    for name, dist in sorted(closure["dists"].items(), key=lambda kv: kv[0].lower()):
        info = Path(dist._path) if hasattr(dist, "_path") else None
        texts: list[dict] = []
        if info and info.is_dir():
            texts = _gather_texts(pool, info / "licenses", rel_to=info / "licenses")
            texts += [t for t in _gather_texts(pool, info, rel_to=info)
                      if not t["title"].startswith("licenses/")]
        override = registry.TEXT_OVERRIDES.get(name.lower())
        if not texts and override:
            texts = [{"title": override["title"], "hash": pool.add(override["body"])}]
        out.append(_entry(
            pool, name=name, version=dist.version,
            spdx=(override or {}).get("spdx") or _python_licence(dist),
            tier="redistributed", group="python", url=_python_url(dist),
            texts=texts,
            notes=(override or {}).get("note", ""),
        ))
    return out


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------

def collect_npm(pool: TextPool) -> list[Entry]:
    lock = json.loads(_read_text(REPO / "ui" / "package-lock.json"))
    node_modules = REPO / "ui"
    out: list[Entry] = []
    for path, meta in sorted(lock["packages"].items()):
        if not path:
            continue  # the root project itself
        if meta.get("dev"):
            # Build-time only: vite/tailwind/tsc never place their own licensed
            # bytes in the shipped bundle. Scope is stated on the screen.
            continue
        name = path.split("node_modules/")[-1]
        pkg_dir = node_modules / path
        texts = _gather_texts(pool, pkg_dir, rel_to=pkg_dir) if pkg_dir.is_dir() else []
        # Only the package's own top-level licence files — never a dependency's.
        texts = [t for t in texts if "/" not in t["title"]]
        spdx = meta.get("license")
        if not spdx and (pkg_dir / "package.json").is_file():
            spdx = json.loads(_read_text(pkg_dir / "package.json")).get("license")
        is_font = name.startswith("@fontsource/")
        out.append(_entry(
            pool, name=name, version=meta.get("version", ""), spdx=spdx,
            tier="redistributed",
            group="fonts" if is_font else "npm",
            url=meta.get("resolved", "").split("/-/")[0] or "",
            texts=texts,
            notes="" if texts else
                  "Upstream ships no licence file in the package; the SPDX id "
                  "above is from its package.json and no text could be reproduced.",
        ))
    return out


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

def _cargo_src_dirs() -> list[Path]:
    home = Path.home() / ".cargo" / "registry" / "src"
    return sorted(p for p in home.glob("*") if p.is_dir()) if home.is_dir() else []


def collect_cargo(pool: TextPool) -> list[Entry]:
    lock = tomllib.loads(_read_text(REPO / "native" / "Cargo.lock"))
    workspace = tomllib.loads(_read_text(REPO / "native" / "Cargo.toml"))
    ours = {Path(m).name for m in workspace["workspace"]["members"]}
    src_roots = _cargo_src_dirs()
    out: list[Entry] = []
    unfetched: list[str] = []
    for pkg in sorted(lock["package"], key=lambda p: p["name"].lower()):
        name, version = pkg["name"], pkg["version"]
        if name in ours:
            continue
        src = next((r / f"{name}-{version}" for r in src_roots
                    if (r / f"{name}-{version}").is_dir()), None)
        if src is None:
            unfetched.append(f"{name}-{version}")
            continue
        manifest = tomllib.loads(_read_text(src / "Cargo.toml")).get("package", {})
        texts = [t for t in _gather_texts(pool, src, rel_to=src) if "/" not in t["title"]]
        texts = texts or _canonical(pool, manifest.get("license"))
        out.append(_entry(
            pool, name=name, version=version, spdx=manifest.get("license"),
            tier="redistributed", group="cargo",
            url=manifest.get("repository") or manifest.get("homepage") or "",
            texts=texts))
    if unfetched:
        raise Fatal(
            "these crates are in Cargo.lock but not in the local registry cache, "
            f"so their licence text cannot be read: {unfetched}. "
            "Run `cargo fetch` in native/ and re-run.")
    return out


# ---------------------------------------------------------------------------
# Vendored binaries + shipped data
# ---------------------------------------------------------------------------

_BINARY_SUFFIX = re.compile(r"\.(dll|dylib|exe|lib|a)$|\.so(\.|$)", re.I)


def collect_vendor(pool: TextPool) -> list[Entry]:
    root = REPO / "server" / "astrodeck" / "vendor"
    out: list[Entry] = []
    if not root.is_dir():
        return out
    for vendor_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        binaries = sorted(
            str(p.relative_to(vendor_dir)).replace("\\", "/")
            for p in vendor_dir.rglob("*")
            if p.is_file() and _BINARY_SUFFIX.search(p.name))
        if not binaries:
            continue
        spec = registry.VENDORED.get(vendor_dir.name)
        if spec is None:
            raise Fatal(
                f"{vendor_dir} ships {len(binaries)} binaries and has no entry in "
                "tools/credits_registry.py VENDORED. Redistributing a binary is a "
                "stronger act than depending on a package; it does not get to be "
                "implicit.")
        texts = [t for t in _gather_texts(pool, vendor_dir, rel_to=vendor_dir)
                 if "/" not in t["title"]]
        if not texts:
            raise Fatal(f"{vendor_dir} ships binaries but no licence file sits beside them")
        out.append(_entry(
            pool, name=spec["name"], version=spec.get("version", ""),
            spdx=spec["spdx"], tier="redistributed-binary", group="vendored",
            url=spec.get("url", ""), texts=texts, flag=spec.get("flag"),
            requires=tuple(spec["requires"]) if "requires" in spec else None,
            summaries=spec.get("summaries"),
            notes=spec["notes"] + "\n\nBinaries shipped: " + ", ".join(binaries)))
    return out


def collect_data(pool: TextPool) -> list[Entry]:
    root = REPO / "server" / "astrodeck" / "catalog" / "data"
    out: list[Entry] = []
    for path in sorted(root.glob("*")) if root.is_dir() else []:
        if not path.is_file():
            continue
        spec = registry.DATA.get(path.name)
        if spec is None:
            raise Fatal(
                f"{path} is shipped data with no entry in credits_registry.DATA. "
                "Data carries attribution and sometimes share-alike obligations; "
                "add its provenance before shipping it.")
        texts = [{"title": t["title"], "hash": pool.add(t["body"])}
                 for t in spec.get("texts", [])]
        out.append(_entry(
            pool, name=spec["name"], version=spec.get("version", ""),
            spdx=spec["spdx"], tier="redistributed-data", group="data",
            url=spec.get("url", ""), texts=texts, notes=spec["notes"],
            flag=spec.get("flag")))
    return out


# ---------------------------------------------------------------------------
# Registry-only groups
# ---------------------------------------------------------------------------

def collect_registry(pool: TextPool, key: str, group: str, tier: str) -> list[Entry]:
    out: list[Entry] = []
    for spec in getattr(registry, key):
        texts = [{"title": t["title"], "hash": pool.add(t["body"])}
                 for t in spec.get("texts", [])]
        out.append(_entry(
            pool, name=spec["name"], version=spec.get("version", ""),
            spdx=spec["spdx"], tier=tier, group=group, url=spec.get("url", ""),
            texts=texts, notes=spec["notes"], flag=spec.get("flag"),
            requires=tuple(spec["requires"]) if "requires" in spec else None,
            summaries=spec.get("summaries")))
    return out


def collect_own_crates(pool: TextPool) -> list[Entry]:
    workspace = tomllib.loads(_read_text(REPO / "native" / "Cargo.toml"))
    out: list[Entry] = []
    for member in sorted(workspace["workspace"]["members"]):
        crate = REPO / "native" / member
        manifest = tomllib.loads(_read_text(crate / "Cargo.toml")).get("package", {})
        lic = manifest.get("license")
        if isinstance(lic, dict) or lic is None:  # inherited from [workspace.package]
            lic = workspace["workspace"]["package"]["license"]
        # Our own crates still owe their own licence text — Apache-2.0 and
        # MPL-2.0 both require it, and "we wrote it" is not an exemption.
        body = (_read_text(REPO / "LICENSE") if lic == "Apache-2.0"
                else registry.text_for_spdx(lic))
        out.append(_entry(
            pool, name=Path(member).name, version=manifest.get("version", ""),
            spdx=lic, tier="first-party", group="astrodeck",
            texts=[{"title": lic, "hash": pool.add(body)}] if body else [],
            notes="AstroDeck's own Rust crate."))
    return out


GROUPS = [
    ("flagged", "Needs an owner decision",
     "Licences this build could not clear on its own. Each is a decision for the "
     "project owner — accept the obligation knowingly, or remove the dependency."),
    ("vendored", "Vendored binaries",
     "Compiled libraries redistributed inside AstroDeck. Shipping someone's "
     "binary is a stronger act than depending on their package, so each vendor's "
     "own terms are reproduced in full."),
    ("data", "Catalogues and data",
     "Astronomical data shipped with AstroDeck. Where we changed the data, this "
     "says so."),
    ("services", "Network services",
     "Services AstroDeck calls over the internet. Nothing here ships with the "
     "app; each is credited because its terms ask to be."),
    ("programs", "External programs",
     "Programs AstroDeck runs as separate processes or ships alongside itself."),
    ("derived", "Derived algorithms",
     "Code written by reading someone else's source. The obligation follows the "
     "logic, not the file, so these notices travel even though no upstream line "
     "was copied."),
    ("python", "Python packages",
     "The server and everything it imports. The single-file binary bundles all "
     "of these, so all of them are redistributed."),
    ("npm", "JavaScript packages",
     "Bundled into the interface JavaScript."),
    ("fonts", "Fonts",
     "Typefaces embedded in the interface. Fonts carry their own licence, "
     "separate from the code that renders them."),
    ("cargo", "Rust crates",
     "Compiled into the native engine that ships as a Python extension module."),
    ("astrodeck", "AstroDeck's own components", "Written for this project."),
]


def build() -> dict:
    pool = TextPool()
    entries: list[Entry] = []
    entries += collect_python(pool)
    entries += collect_npm(pool)
    entries += collect_cargo(pool)
    entries += collect_vendor(pool)
    entries += collect_data(pool)
    entries += collect_registry(pool, "DATA_EXTRA", "data", "redistributed-data")
    entries += collect_registry(pool, "SERVICES", "services", "service")
    entries += collect_registry(pool, "PROGRAMS", "programs", "external-program")
    entries += collect_registry(pool, "DERIVED", "derived", "derived-source")
    entries += collect_own_crates(pool)

    # Anything the policy module refused to clear is lifted out of its ecosystem
    # group and shown first. Burying a flag inside 34 Python packages is the same
    # as not raising it.
    for ent in entries:
        if ent.flag:
            ent.group = "flagged"

    by_group: dict[str, list[Entry]] = {}
    for ent in entries:
        by_group.setdefault(ent.group, []).append(ent)

    pyproject = tomllib.loads(_read_text(REPO / "server" / "pyproject.toml"))
    return {
        "generator": "tools/gen_credits.py",
        "project": {
            "name": "AstroDeck",
            "version": pyproject["project"]["version"],
            "spdx": "Apache-2.0",
        },
        "scope": (
            "Every component whose bytes ship inside an AstroDeck release or are "
            "compiled into one, plus every external program and network service "
            "AstroDeck depends on at runtime. Build-time-only tooling (vite, "
            "tailwindcss, typescript, pytest) is not listed: none of it places "
            "its own licensed code in a shipped artifact."),
        "licenses": pool.as_json(),
        "groups": [
            {"id": gid, "title": title, "blurb": blurb,
             "entries": [e.as_json() for e in
                         sorted(by_group.get(gid, []), key=lambda e: e.name.lower())]}
            for gid, title, blurb in GROUPS
            if by_group.get(gid)
        ],
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed output is out of date")
    args = ap.parse_args()
    try:
        text = render(build())
    except Fatal as exc:
        print(f"gen_credits: {exc}", file=sys.stderr)
        return 2
    if args.check:
        current = _read_text(OUT) if OUT.is_file() else ""
        if current != text:
            print(f"gen_credits: {OUT} is out of date — run `python tools/gen_credits.py`",
                  file=sys.stderr)
            return 1
        print("gen_credits: up to date")
        return 0
    OUT.write_text(text, encoding="utf-8")
    doc = json.loads(text)
    total = sum(len(g["entries"]) for g in doc["groups"])
    print(f"wrote {OUT.relative_to(REPO)}  "
          f"{total} entries, {len(doc['licenses'])} distinct licence texts, "
          f"{len(text) / 1024:.0f} KiB")
    for group in doc["groups"]:
        print(f"  {group['id']:<10} {len(group['entries']):>3}")
    flagged = next((g for g in doc["groups"] if g["id"] == "flagged"), None)
    if flagged:
        print("\nNEEDS AN OWNER DECISION:")
        for ent in flagged["entries"]:
            print(f"  {ent['name']}: {ent['flag']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
