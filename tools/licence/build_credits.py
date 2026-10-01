"""Generate build-only credits from this release environment, without installing.

Missing core dependencies remain fatal. Absent optional roots/closures are named
in release_environment.omitted_optional and receive no invented licence entry.
Installed build tools are labelled as environment inventory, not shipped proof.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gen_credits as generator


def environment_closure(gen=generator):
    project = tomllib.loads((gen.REPO / "server/pyproject.toml").read_text(encoding="utf-8"))["project"]
    found, missing = gen._walk(gen._seeds(project.get("dependencies", [])))
    if missing:
        raise gen.Fatal("Missing required runtime dependencies: " + ", ".join(sorted(missing)))
    optional = []
    for name, requirements in project.get("optional-dependencies", {}).items():
        if name != "dev":
            optional += gen._seeds(requirements)
    optional_found, omitted = gen._walk(optional)
    for name, dist in optional_found.items():
        found.setdefault(name, dist)
    declared = set(found)
    # PyInstaller can retain imports from packaging/build tools (notably
    # setuptools). Their actual inclusion is decided by artifact inspection.
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            found.setdefault(name, dist)
    return found, sorted(set(omitted)), declared


def collect_environment_python(pool, state):
    found, omitted, declared = environment_closure()
    state["omitted_optional"] = omitted
    state["installed_python"] = [{"name": n, "version": d.version,
                                  "declared_runtime_closure": n in declared} for n, d in sorted(found.items())]
    entries = []
    for name, dist in sorted(found.items(), key=lambda row: row[0].lower()):
        info = Path(dist._path) if hasattr(dist, "_path") else None
        texts = []
        if info and info.is_dir():
            texts = generator._gather_texts(pool, info / "licenses", rel_to=info / "licenses")
            texts += [t for t in generator._gather_texts(pool, info, rel_to=info) if not t["title"].startswith("licenses/")]
            # PEP 639 permits meaningful names such as MPL-2.0.txt. Honor the
            # declared files, while recording binary source archives as data.
            for relative in dist.metadata.get_all("License-File", []) or []:
                candidates = [(info / "licenses" / relative).resolve(), (info / relative).resolve()]
                candidate = next((p for p in candidates if p.is_relative_to(info.resolve()) and p.is_file()), None)
                if candidate is None:
                    raise generator.Fatal("Declared licence file is missing for " + name)
                raw = candidate.read_bytes()
                try:
                    body = raw.decode("utf-8")
                    if "\0" in body:
                        raise UnicodeError
                except UnicodeError:
                    state.setdefault("nontext_license_files", []).append({"component": name, "path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
                    continue
                if len(body.strip()) >= 200:
                    item = {"title": relative, "hash": pool.add(body)}
                    if item not in texts:
                        texts.append(item)
        # Include complete current notices from the installed distribution,
        # including retained vendored directories and short selector notices.
        # The artifact gate decides which distributions/modules actually ship.
        notice_bodies = []
        for item in dist.files or ():
            basename = Path(str(item)).name
            if not re.fullmatch(r"(?:LICENSE|LICENCE|COPYING|NOTICE|AUTHORS|COPYRIGHT)(?:[-._].*)?", basename, re.I) or Path(basename).suffix in {".py", ".pyc", ".pyd", ".so"}:
                continue
            source = Path(dist.locate_file(item))
            if not source.is_file():
                continue
            try:
                body = source.read_text(encoding="utf-8").strip()
                if "\0" in body:
                    continue
            except UnicodeError:
                continue
            if body:
                notice_bodies.append("## " + str(item).replace("\\", "/") + "\n\n" + body)
        if notice_bodies:
            combined = "Current installed distribution notices for " + name + " " + dist.version + "\n\n" + "\n\n".join(notice_bodies)
            if len(combined) >= 200:
                texts.append({"title": "Complete current installed distribution notices", "hash": pool.add(combined)})
        override = generator.registry.TEXT_OVERRIDES.get(name.lower())
        if not texts and override:
            texts = [{"title": override["title"], "hash": pool.add(override["body"])}]
        notes = (override or {}).get("note", "")
        if name not in declared:
            notes += " Installed in the release build environment; this entry is not proof that the package ships. Actual retained modules are checked by the artifact inventory."
        entries.append(generator._entry(pool, name=name, version=dist.version,
            spdx=(override or {}).get("spdx") or generator._python_licence(dist),
            tier="installed-runtime" if name in declared else "installed-build-environment", group="python",
            url=generator._python_url(dist), notes=notes.strip(), texts=texts))
    return entries


def collect_resolved_cargo(pool, state):
    command = ["cargo", "metadata", "--locked", "--offline", "--format-version", "1",
               "--manifest-path", str(ROOT / "native/Cargo.toml")]
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    graph = json.loads(result.stdout)
    entries = []
    state["cargo_packages"] = []
    for package in sorted(graph["packages"], key=lambda p: (p["name"], p["version"])):
        state["cargo_packages"].append({"name": package["name"], "version": package["version"],
                                         "spdx": package.get("license"), "source": package.get("source") or "workspace"})
        if package.get("source") is None:
            continue
        source = Path(package["manifest_path"]).parent
        texts = [t for t in generator._gather_texts(pool, source, rel_to=source) if "/" not in t["title"]]
        texts = texts or generator._canonical(pool, package.get("license"))
        entries.append(generator._entry(pool, name=package["name"], version=package["version"],
            spdx=package.get("license"), tier="resolved-native-build-graph", group="cargo",
            url=package.get("repository") or "", texts=texts,
            notes="Current locked Cargo metadata closure with default features; includes build and target-conditional dependencies. Artifact inventory determines retention."))
    return entries


def build():
    state = {"python": platform.python_version(), "platform": sys.platform,
             "scope": "Current release environment. Uninstalled optional dependencies omitted; not permission or proof of artifact inclusion."}
    old_python, old_cargo = generator.collect_python, generator.collect_cargo
    try:
        generator.collect_python = lambda pool: collect_environment_python(pool, state)
        generator.collect_cargo = lambda pool: collect_resolved_cargo(pool, state)
        document = generator.build()
    finally:
        generator.collect_python, generator.collect_cargo = old_python, old_cargo
    document["generator"] = "tools/licence/build_credits.py (using tools/gen_credits.py collectors)"
    document["release_environment"] = state
    document["input_fingerprints"]["tools/licence/build_credits.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    document["scope"] = "Release build environment and declared external services/data. Absent optional dependencies are listed without fabricated licences. Installed build tools and historical observed subcomponents are not claims of current artifact inclusion. The release artifact gate separately checks retained files, versions, notices and unresolved owner decisions."
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    destination = args.out.resolve()
    if not destination.is_relative_to((ROOT / ".probe/release").resolve()):
        parser.error("--out must be inside this checkout's .probe/release directory")
    try:
        doc = build()
    except generator.Fatal as exc:
        print("Release credits: FAIL: " + str(exc))
        return 1
    except subprocess.CalledProcessError:
        print("Release credits: FAIL: locked offline Cargo metadata failed; prepare the private Cargo cache")
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generator.render(doc), encoding="utf-8", newline="\n")
    entries = sum(len(g["entries"]) for g in doc["groups"])
    omitted = doc["release_environment"]["omitted_optional"]
    print(f"Release credits: {entries} entries; {len(omitted)} absent optional dependencies omitted")
    print("Omitted optional: " + ", ".join(omitted))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())