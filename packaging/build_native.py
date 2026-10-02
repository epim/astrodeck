# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Build a licensed ABI3 native wheel from this tree without changing an environment.

python packaging/build_native.py --out .probe/release/native-wheels

Requires maturin>=1.9,<2 and Rust in the current build environment. Uses a private
Cargo home/target and staged native workspace under .probe/release. Install the
result explicitly, or pass it to build_binary.py --native-wheel. The staged
metadata includes real license texts; the wheel also contains exact native
source as ordinary wheel data and a hash-bound build record. No application/device/config is imported.
"""
from __future__ import annotations
import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
NOTICE_NAME = re.compile(r"^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)([-._].*)?$", re.I)


def source_revision(root: Path) -> str:
    if (root / ".git").exists():
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    else:
        try:
            commit = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["source_commit"]
        except (OSError, KeyError, ValueError):
            raise ValueError("native build needs Git revision or source-release manifest") from None
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("source revision is not a full Git object id")
    return commit


def source_files(root: Path):
    native = root / "native"
    for path in sorted(native.rglob("*")):
        if not path.is_file() or any(p in {"target", ".git", "__pycache__", "licenses"} for p in path.relative_to(native).parts):
            continue
        if not path.resolve().is_relative_to(native.resolve()):
            raise ValueError("native source symlink escapes workspace")
        yield path, path.relative_to(root).as_posix()


def native_source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path, relative in source_files(root):
        digest.update(relative.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def source_archive(root: Path, destination: Path) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        for path, relative in source_files(root):
            data = path.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            archive.addfile(info, io.BytesIO(data))


def cargo_notices(metadata: dict, root: Path) -> str:
    """Locked Cargo metadata closure, including build/dev-only notice surplus."""
    chunks = ["Rust dependency notices", "", "This is the locked Cargo metadata closure, including build-only and development dependencies; it is a notice superset, not a claim that every crate is linked into this target.", ""]
    for package in sorted(metadata["packages"], key=lambda p: (p["name"], p["version"])):
        if package.get("source") is None:
            continue
        source = Path(package["manifest_path"]).parent
        bodies = []
        for path in sorted(source.iterdir()):
            if path.is_file() and NOTICE_NAME.match(path.name):
                body = path.read_text(encoding="utf-8", errors="replace").strip()
                if len(body) >= 200:
                    bodies.append((path.name, body))
        # Canonical fallback only for terms without per-holder copyright slots.
        if not bodies:
            choices = (package.get("license") or "").replace("/", " OR ").split(" OR ")
            for spdx, filename in (("Apache-2.0", "apache-2.0.txt"), ("MPL-2.0", "mpl-2.0.txt")):
                if spdx in choices:
                    bodies = [(spdx + " canonical text", (root / "tools/licence_texts" / filename).read_text(encoding="utf-8"))]
                    break
        if not bodies:
            raise ValueError("Cargo dependency has no distributable notice text: " + package["name"])
        chunks += ["## " + package["name"] + " " + package["version"], "License expression: " + (package.get("license") or "UNKNOWN"), "Source: " + (package.get("repository") or package.get("source") or ""), ""]
        for name, body in bodies:
            chunks += ["### " + name, "", body, ""]
    return "\n".join(chunks)


def wheel_entries(path: Path) -> tuple[dict[str, bytes], str]:
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        if len(names) != len(set(names)) or any(n.startswith("/") or ".." in Path(n).parts for n in names):
            raise ValueError("unsafe or duplicate wheel member")
        entries = {name: wheel.read(name) for name in names if not name.endswith("/")}
    metadata = [n for n in entries if n.endswith(".dist-info/METADATA")]
    if len(metadata) != 1:
        raise ValueError("wheel must have one metadata directory")
    return entries, metadata[0].rsplit("/", 1)[0]


def normalize_sbom(value, source_digest: str):
    """Replace only local native paths with identifiers for embedded source.

    CycloneDX bom-ref values are opaque IDs. The same mapping applies to every
    dependency reference, and local PURL download_url qualifiers become source
    content URNs. Registry URLs and all graph/version/license data stay intact.
    """
    def source_urn(text):
        text = unquote(text).replace("\\", "/")
        if "/native/" not in text:
            raise ValueError("unexpected local SBOM source path")
        relative = "native/" + text.rsplit("/native/", 1)[1]
        if ".." in relative.split("/"):
            raise ValueError("unsafe local SBOM source path")
        return "urn:astrodeck:source:sha256:" + source_digest + ":" + relative
    if isinstance(value, dict):
        return {key: normalize_sbom(item, source_digest) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_sbom(item, source_digest) for item in value]
    if not isinstance(value, str):
        return value
    if value.startswith("path+file://"):
        return source_urn(value)
    if value.startswith("pkg:"):
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        def qualifier(item):
            if not item.startswith("file://"):
                return item
            # Maturin uses these crate-relative download hints as well as
            # absolute paths. Relative hints disclose no private build path.
            if re.fullmatch(r"file://(?:\.|\.\./[A-Za-z0-9_-]+)", item.replace("\\", "/")):
                return item
            return source_urn(item)
        changed = [(key, qualifier(item)) for key, item in pairs]
        if changed != pairs:
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(changed, quote_via=quote), parsed.fragment))
        return value
    if "file://" in unquote(value):
        raise ValueError("unrecognized local SBOM reference")
    return value


def license_texts(entries: dict[str, bytes], info: str) -> set[str]:
    """PEP 639 License-File members must exist and contain UTF-8 text."""
    meta = BytesParser().parsebytes(entries[info + "/METADATA"])
    listed = {info + "/licenses/" + name for name in meta.get_all("License-File", [])}
    notices = {n for n in entries if n.startswith(info + "/licenses/")}
    if not notices.issubset(listed):
        raise ValueError("native notice files are missing from METADATA")
    for name in listed:
        if name not in entries:
            raise ValueError("declared native License-File is absent")
        if b"\0" in entries[name]:
            raise ValueError("native License-File must be text without NUL bytes")
        try:
            entries[name].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueError("native License-File must be UTF-8 text") from None
    return listed


def seal_wheel(path: Path, root: Path, commit: str) -> dict:
    entries, info = wheel_entries(path)
    for name, body in list(entries.items()):
        if name.startswith(info + "/sboms/") and name.endswith(".json"):
            entries[name] = (json.dumps(normalize_sbom(json.loads(body), native_source_digest(root)), indent=2, sort_keys=True) + "\n").encode()
    wheel_meta = entries[info + "/WHEEL"].decode()
    if not re.search(r"^Tag: cp311-abi3-", wheel_meta, re.M):
        raise ValueError("native wheel must be cp311 ABI3")
    required = {"MPL-2.0.txt", "THIRD-PARTY-NOTICES.md", "CARGO-NOTICES.txt", "NATIVE-SOURCE.txt"}
    names = {Path(n).name for n in entries if n.startswith(info + "/licenses/")}
    if not required.issubset(names):
        raise ValueError("native wheel is missing actual license/source files")
    license_texts(entries, info)
    source_names = [n for n in entries if Path(n).name == "native-source.tar.gz"]
    if len(source_names) != 1 or source_names[0].startswith(info + "/licenses/"):
        raise ValueError("native source archive must be separate ordinary wheel data")
    source_name = info + "/source/native-source.tar.gz"
    entries[source_name] = entries.pop(source_names[0])
    extensions = {n: hashlib.sha256(body).hexdigest() for n, body in entries.items()
                  if n.endswith((".pyd", ".so")) and "astrodeck_native" in n}
    if len(extensions) != 1:
        raise ValueError("native wheel must contain exactly one extension")
    native = tomllib.loads((root / "native/crates/astrodeck-native/pyproject.toml").read_text(encoding="utf-8"))["project"]
    app = tomllib.loads((root / "server/pyproject.toml").read_text(encoding="utf-8"))["project"]
    record = {"schema_version": 1, "source_commit": commit,
              "source_archive_sha256": {source_name: hashlib.sha256(entries[source_name]).hexdigest()},
              "sbom_sha256": {n: hashlib.sha256(body).hexdigest() for n, body in entries.items() if n.startswith(info + "/sboms/")},
              "notice_sha256": {n: hashlib.sha256(body).hexdigest() for n, body in entries.items() if n.startswith(info + "/licenses/")},
              "native_source_sha256": native_source_digest(root),
              "native_version": native["version"], "app_version": app["version"],
              "extension_sha256": extensions}
    entries[info + "/astrodeck-build.json"] = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    rows = []
    for name, body in sorted(entries.items()):
        if name != info + "/RECORD":
            digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
            rows.append((name, "sha256=" + digest, str(len(body))))
    rows.append((info + "/RECORD", "", ""))
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    entries[info + "/RECORD"] = buffer.getvalue().encode()
    temporary = path.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as wheel:
            for name, body in sorted(entries.items()):
                wheel.writestr(name, body)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def validate_wheel(path: Path, root: Path = ROOT) -> dict:
    entries, info = wheel_entries(path)
    try:
        record = json.loads(entries[info + "/astrodeck-build.json"])
    except (KeyError, ValueError):
        raise ValueError("native wheel has no build provenance") from None
    meta = BytesParser().parsebytes(entries[info + "/METADATA"])
    project = tomllib.loads((root / "native/crates/astrodeck-native/pyproject.toml").read_text(encoding="utf-8"))["project"]
    if meta["Name"] != "astrodeck-native" or meta["Version"] != project["version"] or record.get("native_version") != project["version"]:
        raise ValueError("native wheel metadata version does not match source")
    if not re.search(r"^Tag: cp311-abi3-", entries[info + "/WHEEL"].decode(), re.M):
        raise ValueError("native wheel must be cp311 ABI3")
    required = {"MPL-2.0.txt", "THIRD-PARTY-NOTICES.md", "CARGO-NOTICES.txt", "NATIVE-SOURCE.txt"}
    notices = {n: hashlib.sha256(body).hexdigest() for n, body in entries.items() if n.startswith(info + "/licenses/")}
    if not required.issubset({Path(n).name for n in notices}) or notices != record.get("notice_sha256"):
        raise ValueError("native license/source files differ from build provenance")
    license_texts(entries, info)
    source_name = info + "/source/native-source.tar.gz"
    sources = {n: hashlib.sha256(body).hexdigest() for n, body in entries.items()
               if n.startswith(info + "/source/")}
    if set(sources) != {source_name} or sources != record.get("source_archive_sha256"):
        raise ValueError("native source archive differs from build provenance")
    sboms = {n: hashlib.sha256(body).hexdigest() for n, body in entries.items() if n.startswith(info + "/sboms/")}
    if sboms != record.get("sbom_sha256"):
        raise ValueError("native SBOM differs from build provenance")
    rows = list(csv.reader(io.StringIO(entries[info + "/RECORD"].decode())))
    if {row[0] for row in rows} != set(entries) or len(rows) != len(entries):
        raise ValueError("wheel RECORD does not cover the payload exactly")
    for name, digest, size in rows:
        if name == info + "/RECORD":
            continue
        body = entries[name]
        expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
        if digest != expected or size != str(len(body)):
            raise ValueError("wheel RECORD digest mismatch")
    app = tomllib.loads((root / "server/pyproject.toml").read_text(encoding="utf-8"))["project"]
    if record.get("app_version") != app["version"]:
        raise ValueError("native wheel app version does not match this release")
    if record.get("native_source_sha256") != native_source_digest(root):
        raise ValueError("native wheel does not match the current native source")
    extensions = {n: hashlib.sha256(body).hexdigest() for n, body in entries.items()
                  if n.endswith((".pyd", ".so")) and "astrodeck_native" in n}
    if len(extensions) != 1 or extensions != record.get("extension_sha256"):
        raise ValueError("native extension differs from build provenance")
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
    if not parse_wheel_filename(path.name)[3].intersection(set(sys_tags())):
        raise ValueError("native wheel is incompatible with this build interpreter")
    return record


def native_source_notice(root: Path, commit: str) -> str:
    return (
            "AstroDeck native source is licensed per file: MPL-2.0 workspace components and Apache-2.0 astro-guide, with the third-party notices supplied here.\n"
            "The exact native workspace used by this build is included in native-source.tar.gz; its files and Cargo.lock are the corresponding project source.\n"
            "Public base source: https://github.com/epim/astrodeck/tree/" + commit + "/native\n"
            "Native source tree SHA-256: " + native_source_digest(root) + "\n"
            "The embedded archive includes local changes, if any; the public base URL alone is not a claim that uncommitted changes are published.\n"
            "To rebuild: extract native-source.tar.gz; create native/crates/astrodeck-native/licenses and native/crates/astrodeck-native/sources; copy the four accompanying license/notice texts into the licenses directory; copy the original accompanying native-source.tar.gz into native/crates/astrodeck-native/sources/native-source.tar.gz (do not extract this copy); install Rust and maturin>=1.9,<2 in a build environment, then run python -m maturin build --release --locked --manifest-path native/crates/astrodeck-native/Cargo.toml. Cargo fetches the locked upstream sources.\n"
    )


def build(out: Path, root: Path = ROOT) -> Path:
    scratch = root / ".probe/release"
    scratch.mkdir(parents=True, exist_ok=True)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CARGO_HOME"] = str(scratch / "cargo-home")
    env["CARGO_TARGET_DIR"] = str(scratch / "cargo-target")
    commit = source_revision(root)
    with tempfile.TemporaryDirectory(prefix="native-build-", dir=scratch) as work:
        work = Path(work)
        shutil.copytree(root / "native", work / "native", ignore=shutil.ignore_patterns("target", ".git", "__pycache__", "licenses"))
        crate = work / "native/crates/astrodeck-native"
        licenses = crate / "licenses"
        licenses.mkdir()
        shutil.copy2(root / "tools/licence_texts/mpl-2.0.txt", licenses / "MPL-2.0.txt")
        shutil.copy2(root / "THIRD-PARTY-NOTICES.md", licenses / "THIRD-PARTY-NOTICES.md")
        sources = crate / "sources"
        sources.mkdir()
        source_archive(root, sources / "native-source.tar.gz")
        metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--locked", "--format-version", "1", "--manifest-path", str(work / "native/Cargo.toml")], env=env, text=True))
        (licenses / "CARGO-NOTICES.txt").write_text(cargo_notices(metadata, root), encoding="utf-8")
        (licenses / "NATIVE-SOURCE.txt").write_text(native_source_notice(root, commit), encoding="utf-8")
        wheel_out = work / "wheels"
        subprocess.run([sys.executable, "-m", "maturin", "build", "--release", "--locked", "--manifest-path", str(crate / "Cargo.toml"), "--interpreter", sys.executable, "--out", str(wheel_out)], env=env, check=True)
        wheels = list(wheel_out.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("native build did not produce exactly one wheel")
        seal_wheel(wheels[0], root, commit)
        validate_wheel(wheels[0], root)
        destination = out / wheels[0].name
        shutil.copy2(wheels[0], destination)
        return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / ".probe/release/native-wheels")
    parser.add_argument("--check", type=Path, help="validate an existing wheel against this tree and interpreter")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(validate_wheel(args.check), indent=2))
    else:
        print(build(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
