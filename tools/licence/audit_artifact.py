"""Audit bytes inside a release tarball against reviewed assets and build inputs.

This is an inventory gate, not a legal opinion or proof about other platforms.
Unknown binary/data inputs, blocked assets, changed hashes and missing notices
fail closed. A source-tree registry alone never proves an archive's contents.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.append(str(ROOT / "tools"))
import licence_policy as policy

CODE = {".py", ".h", ".js", ".css", ".html", ".toml"}
METADATA = {
    "manifest.json", "server/astrodeck/vendor/manifest.json",
    "server/astrodeck/vendor/playerone/LICENSE",
    "server/astrodeck/vendor/playerone/README.md",
    "server/astrodeck/vendor/zwo/LICENSE.txt",
    "server/astrodeck/vendor/zwo/README.md",
    "server/astrodeck/vendor/zwo/EAF_focuser.h",
    "ui/dist/manifest.json",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_archive(path: Path):
    files, errors = {}, []
    roots = set()
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not parts or member.name.startswith("/") or ".." in parts or "\\" in member.name:
                errors.append("archive contains an unsafe member path")
                continue
            roots.add(parts[0])
            if member.isdir():
                continue
            if not member.isfile() or len(parts) < 2:
                errors.append("archive contains a link or non-file member")
                continue
            name = "/".join(parts[1:])
            if name in files:
                errors.append("archive contains a duplicate member")
                continue
            if member.size > 512 * 1024 * 1024:
                errors.append("archive member exceeds the review limit")
                continue
            files[name] = archive.extractfile(member).read()
    if len(roots) != 1:
        errors.append("archive must have exactly one top-level directory")
    return files, errors


def package_name(module: str) -> str:
    parts = module.removeprefix("npm:").split("/")
    return "/".join(parts[:2]) if parts[0].startswith("@") else parts[0]


def credit_findings(label: str, expression: str, entry: dict | None, pool: dict) -> list[str]:
    errors = []
    if entry is None:
        return [f"{label}: component has no generated credit"]
    if entry.get("spdx") != expression:
        errors.append(f"{label}: credit licence differs from reviewed provenance")
    try:
        resolution = policy.resolve(expression)
        if resolution.flag or entry.get("flag"):
            errors.append(f"{label}: reviewed licence still needs owner clearance")
        needs_text = resolution.needs_text
    except policy.Flag:
        errors.append(f"{label}: licence is outside the allowed policy")
        needs_text = bool(set(entry.get("requires", [])) & policy.NEEDS_TEXT)
    refs = entry.get("texts", [])
    if needs_text and not refs:
        errors.append(f"{label}: required licence text is absent")
    for ref in refs:
        key = ref.get("hash", "")
        body = pool.get(key, "")
        if (len(body.strip()) < 200
                or sha256(body.encode("utf-8"))[:16] != key):
            errors.append(f"{label}: licence text reference is missing, truncated or corrupt")
    return errors


def source_sha256(data: bytes) -> str:
    # Git's Windows checkout newline conversion is not a source change.
    return sha256(data.decode("utf-8").replace("\r\n", "\n").encode("utf-8"))


def review(files: dict[str, bytes], rules: dict, credits: dict, ui_build: dict,
           credits_bytes: bytes, npm_lock: dict):
    errors, records = [], []
    registered = {item["path"]: item for item in rules["assets"]}
    entries = {entry["name"]: entry for group in credits["groups"] for entry in group["entries"]}
    outputs = {item["path"]: item for item in ui_build.get("files", [])}
    sources = {item["path"]: item for item in rules.get("reviewed_sources", [])}
    pool = credits.get("licenses", {})
    if ui_build.get("credits_sha256") != sha256(credits_bytes):
        errors.append("UI build evidence uses stale generated credits")
    credit_chunks = [item for item in outputs.values()
                     if "repo:ui/src/credits.generated.json" in item.get("inputs", [])]
    if not credit_chunks or not all(
            item["path"] in files and sha256(files[item["path"]]) == item["sha256"]
            for item in credit_chunks):
        errors.append("archive is missing the exact compiled credits from its UI build")
    components = set(rules.get("compiler_runtime_components", []))
    for name, data in sorted(files.items()):
        record = {"path": name, "bytes": len(data), "sha256": sha256(data)}
        canonical = name.replace("server/astrodeck/webui/", "ui/dist/", 1) if name.startswith("server/astrodeck/webui/") else name
        rule = registered.get(canonical)
        if rule is not None:
            record["component"] = rule["component"]
            if rule.get("blocked"):
                errors.append(f"{name}: registered asset requires an owner decision")
            if sha256(data) != rule["sha256"]:
                errors.append(f"{name}: asset bytes differ from the reviewed source")
            try:
                resolution = policy.resolve(rule["spdx"])
                if resolution.flag:
                    errors.append(f"{name}: reviewed licence still needs owner clearance")
            except policy.Flag:
                errors.append(f"{name}: asset licence is outside the allowed policy")
            if credit := rule.get("credit"):
                errors += credit_findings(name, rule["spdx"], entries.get(credit), pool)
            for notice, expected in rule.get("notices", {}).items():
                if notice not in files or sha256(files[notice]) != expected:
                    errors.append(f"{name}: required companion notice is absent or changed")
        elif canonical in METADATA:
            record["component"] = "AstroDeck metadata or accompanying licence text"
        elif canonical.startswith("ui/dist/") and Path(name).suffix in {".js", ".css", ".html"}:
            output = outputs.get(canonical)
            if output is None or output["sha256"] != sha256(data):
                errors.append(f"{name}: code output has no matching fresh build evidence")
            else:
                for module in output.get("inputs", []):
                    if module.startswith("npm:"):
                        components.add(package_name(module))
                    elif module == "build-helper:commonjsHelpers.js":
                        components.add("vite")  # Vite's bundled CommonJS helper notice.
                    elif not module.startswith("repo:ui/"):
                        errors.append(f"{name}: unclassified build input")
            record["component"] = "AstroDeck UI plus recorded compiler/runtime inputs"
        elif (name.startswith("server/astrodeck/") and name.endswith(".py")
              and "/vendor/" not in name) or name == "server/pyproject.toml":
            source = sources.get(name)
            try:
                same = source is not None and source_sha256(data) == source["sha256"]
            except UnicodeError:
                same = False
            if not same:
                errors.append(f"{name}: source or embedded data has not been reviewed")
            elif source.get("credit"):
                errors += credit_findings(name, source["spdx"], entries.get(source["credit"]), pool)
            record["component"] = source.get("component", "UNREVIEWED source") if source else "UNREVIEWED source"
        else:
            errors.append(f"{name}: unaccounted binary/data member")
            record["component"] = "UNACCOUNTED"
        # Renaming binary bytes to .py/.js is not a way to call them source.
        if Path(name).suffix in CODE or canonical in METADATA:
            try:
                decoded = data.decode("utf-8")
                if "\0" in decoded:
                    raise UnicodeError
            except UnicodeError:
                errors.append(f"{name}: binary bytes masquerade as text")
        records.append(record)
    for name in sorted(components):
        meta = npm_lock.get("packages", {}).get("node_modules/" + name)
        entry = entries.get(name)
        if meta is None or entry is None:
            errors.append(f"UI component {name}: missing lockfile or generated credit")
            continue
        if meta["version"] != entry["version"]:
            errors.append(f"UI component {name}: credit version differs from build lock")
        try:
            resolution = policy.resolve(meta.get("license") or entry["spdx"])
            if resolution.flag:
                errors.append(f"UI component {name}: reviewed licence still needs owner clearance")
        except policy.Flag:
            errors.append(f"UI component {name}: licence is outside the allowed policy")
            continue
        errors += credit_findings(f"UI component {name}", meta.get("license") or entry["spdx"], entry, pool)
    return sorted(set(errors)), records


def build_tarball(repo: Path, out: Path) -> Path:
    repo, out = repo.resolve(), out.resolve()
    if not out.is_relative_to(repo / ".probe"):
        raise ValueError("Build output must be inside this checkout's private .probe directory")
    spec = importlib.util.spec_from_file_location("licence_release_builder", repo / "scripts/build_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    # The real packager runs. ASTAP omission is explicit, never implied clearance.
    return builder.build("licence-audit", repo, out, strict=True, allow_missing=("astap",))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / ".probe/licence/gate")
    parser.add_argument("--ui-inventory", type=Path, default=ROOT / ".probe/licence/ui-build-inventory.json")
    parser.add_argument("--report", type=Path, default=ROOT / ".probe/licence/artifact-gate-report.json")
    args = parser.parse_args()
    if args.build == bool(args.archive):
        parser.error("select exactly one of --build or --archive")
    archive = build_tarball(ROOT, args.out) if args.build else args.archive
    files, errors = read_archive(archive)
    read_json = lambda path: json.loads(path.read_text(encoding="utf-8"))
    credits_bytes = (ROOT / "ui/src/credits.generated.json").read_bytes()
    findings, records = review(
        files, read_json(ROOT / "tools/licence/artifact-registry.json"),
        json.loads(credits_bytes), read_json(args.ui_inventory), credits_bytes,
        read_json(ROOT / "ui/package-lock.json"))
    errors += findings
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({
        "artifact_sha256": sha256(archive.read_bytes()), "files": records,
        "findings": errors, "cleared": not errors,
        "scope": "Only this local tarball. ASTAP, dependency wheels, native wheels, other executables and containers require separate evidence.",
    }, indent=2) + "\n", encoding="utf-8")
    for error in errors:
        print(error)
    print(f"Artifact gate: {'FAIL' if errors else 'PASS'} ({len(records)} files, {len(errors)} findings)")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
