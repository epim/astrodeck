# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Audit bytes inside a release tarball against reviewed assets and build inputs.

This is an inventory gate, not a legal opinion or proof about other platforms.
Unknown binary/data inputs, blocked assets, changed hashes and missing notices
fail closed. A source-tree registry alone never proves an archive's contents.

--image reads a container image archive (an OCI layout, a `docker save` archive
or a flat `docker export` filesystem) without running it and inventories the
merged filesystem: the native wheel, every installed Python distribution with its
licence, and the base-image facts the wheel's compatibility rests on.
"""
from __future__ import annotations

import argparse
import email.parser
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


TEXT_SUFFIXES = {".txt", ".md", ".tsv", ".csv", ".json", ".rst"}
TEXT_NOTICE_NAME = re.compile(r"(?:license|licence|notice|copying)(?:[-._].*)?", re.I)


def is_text_payload(name: str) -> bool:
    """Text payloads whose bytes depend on the checkout's newline convention."""
    base = PurePosixPath(name.replace("\\", "/")).name
    return PurePosixPath(base).suffix.lower() in TEXT_SUFFIXES or bool(TEXT_NOTICE_NAME.fullmatch(base))


def content_sha256(name: str, data: bytes) -> str:
    """Registry hash of a payload. A Windows CRLF checkout and a Linux LF checkout
    of the same text file are the same reviewed content (#1073), so text payloads
    are hashed after CRLF to LF; binary payloads stay exact."""
    return sha256(data.replace(b"\r\n", b"\n") if is_text_payload(name) else data)


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
            if content_sha256(name, data) != rule["sha256"]:
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
                if notice not in files or content_sha256(notice, files[notice]) != expected:
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


# ---------------------------------------------------------------- container image
# Measured 2026-10 on the pinned python:3.12-slim digest (#798): Debian 13 (trixie)
# with glibc 2.41, not bookworm with 2.36. The native wheel's manylinux tag is
# accepted or refused by pip against this glibc, so a moved base is a finding until
# the wheel compatibility and the dpkg/libusb allowlist are reviewed again.
IMAGE_BASE = {"codename": "trixie", "glibc": "2.41"}
FIRST_PARTY = {"astrodeck", "astrodeck-native"}
# Installer tooling that ships in every Python image; its licence is still checked.
TOOLING = {"pip"}
NATIVE = "astrodeck-native"
IMAGE_READ_LIMIT = 64 * 1024 * 1024
OS_RELEASE = ("usr/lib/os-release", "etc/os-release")
DPKG_STATUS = "var/lib/dpkg/status"
DIST_INFO = re.compile(r"^(?:.+/)?site-packages/[^/]+\.dist-info/(METADATA|WHEEL)$")
MANYLINUX = re.compile(r"^manylinux_2_(\d+)_")
LEGACY_MANYLINUX = {"manylinux1": 5, "manylinux2010": 12, "manylinux2014": 17}
PROBE = "opt/native_probe.py"


def distribution_name(name: str) -> str:
    """PEP 503 normalisation, so pydantic_core and pydantic-core are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def wanted_in_image(name: str) -> bool:
    return name in OS_RELEASE or name == DPKG_STATUS or bool(DIST_INFO.match(name))


def _json_member(archive: tarfile.TarFile, name: str) -> dict:
    handle = archive.extractfile(name)
    if handle is None:
        raise ValueError(f"image archive lacks {name}")
    return json.loads(handle.read().decode("utf-8"))


def _blob(digest: str) -> str:
    algorithm, _, value = digest.partition(":")
    return f"blobs/{algorithm}/{value}"


def _oci_manifest(archive: tarfile.TarFile) -> dict:
    descriptors = _json_member(archive, "index.json").get("manifests", [])
    while True:
        if not descriptors:
            raise ValueError("image archive holds no image manifest")
        if len(descriptors) > 1:
            raise ValueError("image archive holds several images; export one platform")
        document = _json_member(archive, _blob(descriptors[0]["digest"]))
        if "layers" in document:
            return document
        descriptors = document.get("manifests", [])


def image_layers(archive: tarfile.TarFile) -> tuple[list[str] | None, str | None]:
    """Layer member names in application order, and the config member, or (None, None)
    for a flat filesystem export that has no layers."""
    names = set(archive.getnames())
    if "manifest.json" in names:
        images = _json_member(archive, "manifest.json")
        if len(images) != 1:
            raise ValueError("image archive holds several images; export one platform")
        return list(images[0]["Layers"]), images[0].get("Config")
    if "index.json" in names:
        manifest = _oci_manifest(archive)
        return [_blob(layer["digest"]) for layer in manifest["layers"]], _blob(manifest["config"]["digest"])
    return None, None


def _safe_image_path(name: str) -> str | None:
    """The member's path relative to the image root, "" for the root itself, None when unsafe."""
    while name.startswith("./"):
        name = name[2:]
    parts = PurePosixPath(name).parts
    if name.startswith("/") or ".." in parts or "\\" in name:
        return None
    return "/".join(parts)


def _remove_below(directory: str, names: set[str], wanted: dict[str, bytes]) -> None:
    prefix = directory + "/" if directory else ""
    for name in [n for n in names if n.startswith(prefix)]:
        names.discard(name)
        wanted.pop(name, None)


def apply_layer(layer: tarfile.TarFile, names: set[str], wanted: dict[str, bytes], errors: list[str]) -> None:
    """Merge one layer over the filesystem so far, the way a container runtime does:
    whiteouts delete what earlier layers made, an opaque marker hides a directory's
    earlier contents, and anything else adds or replaces."""
    additions = []
    for member in layer.getmembers():
        path = _safe_image_path(member.name)
        if path is None:
            errors.append("archive contains an unsafe member path")
            continue
        if not path:
            continue
        parent, _, leaf = path.rpartition("/")
        if leaf == ".wh..wh..opq":
            _remove_below(parent, names, wanted)
        elif leaf.startswith(".wh."):
            target = (parent + "/" if parent else "") + leaf[len(".wh."):]
            names.discard(target)
            wanted.pop(target, None)
            _remove_below(target, names, wanted)
        else:
            additions.append((path, member))
    for path, member in additions:
        names.add(path)
        wanted.pop(path, None)
        if member.isfile() and wanted_in_image(path):
            if member.size > IMAGE_READ_LIMIT:
                errors.append(f"{path}: file exceeds the review limit")
                continue
            wanted[path] = layer.extractfile(member).read()


def read_image(path: Path):
    """(file names, the few files the gate reads, architecture, errors) of the merged image."""
    names, wanted, errors, architecture = set(), {}, [], None
    with tarfile.open(path, "r:*") as archive:
        layer_names, config = image_layers(archive)
        if config is not None and config in archive.getnames():
            architecture = _json_member(archive, config).get("architecture")
        if layer_names is None:
            apply_layer(archive, names, wanted, errors)
        for layer_name in layer_names or []:
            handle = archive.extractfile(layer_name)
            if handle is None:
                raise ValueError(f"image archive lacks layer {layer_name}")
            with tarfile.open(fileobj=handle, mode="r:*") as layer:
                apply_layer(layer, names, wanted, errors)
    return names, wanted, architecture, errors


def parse_metadata(data: bytes):
    return email.parser.BytesParser().parsebytes(data, headersonly=True)


def declared_licence(message) -> str:
    """What the installed metadata says: an SPDX expression, else the licence
    classifiers, else a one-line License field."""
    expression = (message.get("License-Expression") or "").strip()
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in message.get_all("Classifier") or []
                   if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    field = (message.get("License") or "").strip()
    return " ".join(field.split())[:120]


def base_facts(wanted: dict[str, bytes]) -> dict:
    release = {}
    source = next((wanted[n] for n in OS_RELEASE if n in wanted), b"")
    for line in source.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition("=")
        release[key.strip()] = value.strip().strip('"')
    libc6 = None
    for paragraph in wanted.get(DPKG_STATUS, b"").decode("utf-8", "replace").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line)
        if fields.get("Package") == "libc6":
            libc6 = fields.get("Version")
    return {"id": release.get("ID"), "version_id": release.get("VERSION_ID"),
            "codename": release.get("VERSION_CODENAME"), "libc6": libc6}


def base_findings(base: dict) -> list[str]:
    errors = []
    if base["codename"] is None:
        errors.append("the image does not record its operating system release")
    if base["libc6"] is None:
        errors.append("the image does not record its libc6 version")
    if base["codename"] is not None and base["libc6"] is not None:
        glibc = base["libc6"].split("-")[0]
        if (base["codename"], glibc) != (IMAGE_BASE["codename"], IMAGE_BASE["glibc"]):
            errors.append(
                f"base image is {base['codename']} with libc6 {base['libc6']}, not the reviewed "
                f"{IMAGE_BASE['codename']} with glibc {IMAGE_BASE['glibc']}: review which manylinux "
                "wheels pip accepts and the dpkg and libusb allowlist before releasing on it")
    return errors


def glibc_needed(platform_tag: str) -> int | None:
    """The glibc 2.N a manylinux platform tag needs, or None for a plain linux tag."""
    match = MANYLINUX.match(platform_tag)
    if match:
        return int(match.group(1))
    return LEGACY_MANYLINUX.get(platform_tag.split("_")[0])


def native_findings(tags: list[str], libc6: str | None) -> list[str]:
    """Findings for the installed native wheel's platform tags against the base glibc."""
    platforms = sorted({p for tag in tags for p in tag.split("-")[-1].split(".")})
    linux = [p for p in platforms if p.startswith(("manylinux", "linux_"))]
    if not linux:
        return [f"native wheel has no linux platform tag (WHEEL says {', '.join(platforms) or 'nothing'})"]
    if libc6 is None:
        return []
    available = int(libc6.split("-")[0].split(".")[1])
    needed = [glibc_needed(p) for p in linux]
    if None in needed or min(needed) <= available:
        return []
    worst = min(linux, key=lambda p: glibc_needed(p))
    return [f"native wheel tag {worst} needs glibc 2.{min(needed)}, the base has 2.{available}"]


def python_distributions(wanted: dict[str, bytes]):
    """(name, version, licence, dist-info directory, parse error) of every installed
    distribution, in path order."""
    found, errors = [], []
    for path in sorted(wanted):
        match = DIST_INFO.match(path)
        if not match or match.group(1) != "METADATA":
            continue
        message = parse_metadata(wanted[path])
        directory = path.rsplit("/", 1)[0]
        if not message.get("Name"):
            errors.append(f"{directory}: a distribution record has no name")
            continue
        found.append((distribution_name(message["Name"]), message["Name"], message.get("Version", ""),
                      declared_licence(message), directory))
    return found, errors


def package_findings(distributions, credits: dict) -> list[str]:
    errors = []
    entries = {distribution_name(entry["name"]): entry
               for group in credits["groups"] for entry in group["entries"]}
    pool = credits.get("licenses", {})
    for normal, display, _, licence, _ in distributions:
        if not licence:
            errors.append(f"{display}: the installed metadata states no licence")
        if normal in FIRST_PARTY:
            continue  # Its licence is checked against the source-tar and native-wheel gates; listed in the report.
        if normal in TOOLING:
            if licence:
                try:
                    if policy.resolve(licence).flag:
                        errors.append(f"{display}: reviewed licence still needs owner clearance")
                except policy.Flag:
                    errors.append(f"{display}: licence is outside the allowed policy")
            continue
        entry = entries.get(normal)
        errors += credit_findings(display, entry["spdx"] if entry else "", entry, pool)
    return errors


def native_wheel_record(wanted: dict[str, bytes], names: set[str], distributions):
    """(record or None, findings) for the one installed native wheel."""
    native = [d for d in distributions if d[0] == NATIVE]
    if not native:
        return None, ["native wheel is absent: the image has no astrodeck_native distribution"]
    if len(native) > 1:
        return None, ["more than one native wheel is installed: " + ", ".join(sorted(d[4] for d in native))]
    _, _, version, _, directory = native[0]
    tags = [line.split(":", 1)[1].strip() for line in
            wanted.get(directory + "/WHEEL", b"").decode("utf-8", "replace").splitlines()
            if line.startswith("Tag:")]
    package = directory.rsplit("/", 1)[0] + "/astrodeck_native/"
    modules = sorted(n for n in names if n.startswith(package) and n.endswith(".so"))
    errors = [] if modules else ["native wheel has no compiled extension module in the image"]
    return {"name": NATIVE, "version": version, "tags": tags, "extension_modules": modules}, errors


def audit_image(path: Path, credits: dict):
    """(findings, report) for one image archive. Nothing in the image is executed."""
    names, wanted, architecture, errors = read_image(path)
    base = base_facts(wanted)
    distributions, record_errors = python_distributions(wanted)
    native, native_errors = native_wheel_record(wanted, names, distributions)
    findings = errors + record_errors + base_findings(base) + native_errors + package_findings(distributions, credits)
    if native is not None:
        findings += native_findings(native["tags"], base["libc6"])
    if PROBE not in names:
        findings.append("the native probe is not in the image: nothing can report the engine at run time")
    report = {
        "architecture": architecture,
        "base": base,
        "native_wheel": native,
        "python_packages": [{"name": normal, "version": version, "licence": licence, "path": directory}
                            for normal, _, version, licence, directory in distributions],
    }
    return sorted(set(findings)), report


def build_tarball(repo: Path, out: Path) -> Path:
    repo, out = repo.resolve(), out.resolve()
    if not out.is_relative_to(repo / ".probe"):
        raise ValueError("Build output must be inside this checkout's private .probe directory")
    spec = importlib.util.spec_from_file_location("licence_release_builder", repo / "scripts/build_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    # The real packager runs. ASTAP omission is explicit, never implied clearance.
    return builder.build("licence-audit", repo, out, strict=True, allow_missing=("astap",))


def image_main(args) -> int:
    image = args.image
    credits_bytes = args.credits.read_bytes()
    findings, report = audit_image(image, json.loads(credits_bytes))
    report.update({
        "artifact_sha256": sha256(image.read_bytes()), "findings": findings, "cleared": not findings,
        "scope": "Only this image filesystem as exported: installed Python distributions, the native wheel and the "
                 "base-image release. Other apt packages, the base image's own licence texts and anything outside "
                 "a Python distribution require separate evidence.",
    })
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for finding in findings:
        print(finding)
    print(f"Image gate: {'FAIL' if findings else 'PASS'} "
          f"({len(report['python_packages'])} packages, {len(findings)} findings)")
    return int(bool(findings))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--image", type=Path,
                        help="a container image archive: an OCI layout, a docker save archive or a docker export tar")
    parser.add_argument("--credits", type=Path, default=ROOT / "ui/src/credits.generated.json",
                        help="generated credits that must account for every installed package (--image)")
    parser.add_argument("--out", type=Path, default=ROOT / ".probe/licence/gate")
    parser.add_argument("--ui-inventory", type=Path, default=ROOT / ".probe/licence/ui-build-inventory.json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if [args.build, args.archive is not None, args.image is not None].count(True) != 1:
        parser.error("select exactly one of --build, --archive or --image")
    if args.image is not None:
        args.report = args.report or ROOT / ".probe/licence/image-gate-report.json"
        return image_main(args)
    args.report = args.report or ROOT / ".probe/licence/artifact-gate-report.json"
    archive = build_tarball(ROOT, args.out) if args.build else args.archive
    files, errors = read_archive(archive)
    read_json = lambda path: json.loads(path.read_text(encoding="utf-8"))
    credits_bytes = args.credits.read_bytes()
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
