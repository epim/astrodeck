"""Inventory an actual release artifact before upload.

No observed baseline is an approval. Unknown payloads, missing provenance/notices
and pending central owner decisions are independent blocking findings.
"""
from __future__ import annotations
import argparse
import base64
import csv
from email.parser import BytesParser
import tomllib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import struct
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))
import audit_artifact
import licence_policy
import release_provenance as provenance

PLATFORMS = {"linux-x86_64", "windows-x86_64", "linux-arm64", "macos-arm64", "source"}
KINDS = {"source-tar", "frozen", "native-wheel", "server-wheel"}
BASE = "6e8dad9d66f66b78d5df7cc12872f49e1f626d6b"
UNACCOUNTED = {"UNACCOUNTED", "PROVENANCE", "SOURCE_CHANGED", "NATIVE_UNREVIEWED", "ARCHIVE", "ENVELOPE_UNREVIEWED", "UI_SOURCE", "UI_PROVENANCE", "NATIVE_SBOM", "NATIVE_SEAL"}
BASE_CACHE = {}

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def finding(code, message, path=None, component=None, decision=None):
    return {"code": code, "severity": "blocker", "path": path, "component": component,
            "decision": decision, "message": message}

def canonical(name):
    for start in ("server/astrodeck/webui/", "astrodeck/webui/"):
        if name.startswith(start):
            return "ui/dist/" + name[len(start):]
    if name.startswith("astrodeck/"):
        return "server/" + name
    return name

def rows(files):
    return [{"path": n, "bytes": len(b), "sha256": provenance.sha(b)} for n, b in sorted(files.items())]

def credits_entries(credits, group=None):
    entries = {}
    for container in credits.get("groups", []):
        for entry in container.get("entries", []):
            moved = container.get("id") == "flagged" and (
                group == "python" and entry.get("tier", "").startswith("installed-") or
                group == "cargo" and entry.get("tier") == "resolved-native-build-graph")
            if group is None or container.get("id") == group or moved:
                entries[entry["name"].lower().replace("_", "-")] = entry
    return entries

def credit_errors(entry, credits, name):
    if entry is None:
        return [finding("CREDIT_MISSING", "No current generated credit for retained component", component=name)]
    errors = audit_artifact.credit_findings(name, entry.get("spdx", "UNKNOWN"), entry, credits.get("licenses", {}))
    return [finding("CREDIT", e, component=name) for e in errors]

def checked_ui(files, ui, credits_bytes):
    errors = []
    current = {canonical(k): v for k, v in files.items()}
    if not ui or ui.get("credits_sha256") != provenance.sha(credits_bytes):
        return [finding("UI_PROVENANCE", "UI inventory is absent or used different generated credits")], False
    relevant = [x for x in ui.get("files", []) if "repo:ui/src/credits.generated.json" in x.get("inputs", [])]
    good = bool(relevant) and all(x["path"] in current and provenance.sha(current[x["path"]]) == x["sha256"] for x in relevant)
    if not good:
        errors.append(finding("NOTICE_MISSING", "Artifact does not contain the exact compiled credits recorded by this UI build"))
    return errors, good

def owner_findings(files, kind, decisions):
    names = "\n".join(files).lower()
    errors = []
    for row in decisions:
        component = row.get("component", "UNKNOWN").lower()
        applies = {
            "playerone": any("vendor/playerone/" in "/" + n.lower() and re.search(r"\.(dll|dylib|lib|a)$|\.so(?:\.|$)", n.lower()) for n in files),
            "artwork": "bg_nebula." in names,
            "wcslib": kind == "frozen" and ("astropy/wcs/_wcs" in names or "openblas" in names),
            "astrospheric": kind in {"source-tar", "server-wheel", "frozen"},
            "iau": kind in {"source-tar", "server-wheel", "frozen"} and ("brightstars.py" in names or "astrodeck.catalog.brightstars" in names),
        }.get(component, False)
        if applies and row.get("requires_owner_decision", row.get("mode") == "pending"):
            errors.append(finding("OWNER_PENDING", "Central distribution decision remains unresolved", component=component, decision=row.get("evidence")))
        if applies and component not in {"playerone", "dss2"} and row.get("mode") == "fetch-only":
            errors.append(finding("POLICY_EXCLUDED", "Fetch-only decision does not permit this retained component; the packager/service still needs a separately reviewed change", component=component, decision=row.get("evidence")))
        if component == "dss2" and not row.get("include_tiles", True) and any("/catalog/_bundled_pack/dss2color/" in "/" + n.lower() for n in files):
            errors.append(finding("POLICY_EXCLUDED", "Central policy forbids bundled DSS2 tiles", component=component, decision=row.get("evidence")))
        if component == "playerone" and applies and not row.get("include_binaries", True):
            errors.append(finding("POLICY_EXCLUDED", "Central policy excludes this vendor binary from this artifact", component=component, decision=row.get("evidence")))
    return errors

def load_decisions(root, kind):
    path = root / "packaging/distribution_policy.py"
    if not path.is_file():
        return [], [finding("POLICY_MISSING", "Central distribution-policy loader is missing")]
    spec = importlib.util.spec_from_file_location("release_distribution_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = module.load_policy(root)
    return module.decision_records(policy, kind), []

def warm_base(root, paths):
    names = sorted(set(paths))
    if any("\n" in n or "\r" in n or ":" in n or ".." in Path(n).parts for n in names):
        raise ValueError("invalid source path")
    names = [n for n in names if (str(root.resolve()), n) not in BASE_CACHE]
    if not names:
        return
    query = "".join(BASE + ":" + n + "\n" for n in names).encode()
    result = subprocess.run(["git", "cat-file", "--batch"], cwd=root, input=query, capture_output=True)
    if result.returncode:
        return
    stream = io.BytesIO(result.stdout)
    for name in names:
        header = stream.readline()
        if header.rstrip().endswith(b" missing"):
            data = None
        else:
            fields = header.split()
            if len(fields) != 3 or fields[1] != b"blob":
                raise ValueError("unexpected Git source object")
            size = int(fields[2])
            data = stream.read(size)
            if len(data) != size or stream.read(1) != b"\n":
                raise ValueError("truncated Git source object")
        BASE_CACHE[(str(root.resolve()), name)] = data

def review_ui_inputs(ui, credits_bytes, root=ROOT):
    if ui.get("schema_version") != 2 or not ui.get("source_inputs"):
        return [finding("UI_SOURCE", "UI build lacks current source-input evidence")]
    errors = []
    if ui.get("npm_lock_sha256") != provenance.sha((root / "ui/package-lock.json").read_bytes()):
        errors.append(finding("UI_SOURCE", "UI build lockfile differs from this release"))
    source_rows = {r["path"]: r for r in ui["source_inputs"]}
    required = {n[5:] for row in ui.get("files", []) for n in row.get("inputs", []) if n.startswith("repo:ui/")}
    if required != set(source_rows):
        errors.append(finding("UI_SOURCE", "UI source inventory does not exactly cover its declared inputs"))
    warm_base(root, required - {"ui/src/credits.generated.json"})
    for path, row in source_rows.items():
        provenance.portable(path)
        if not path.startswith("ui/"):
            errors.append(finding("UI_SOURCE", "Unexpected UI source root", path))
            continue
        local = root / path
        data = credits_bytes if path == "ui/src/credits.generated.json" else local.read_bytes()
        comparable = provenance.normalized_source(data) if row.get("encoding") == "normalized-utf8" else data
        if provenance.sha(comparable) != row.get("sha256"):
            errors.append(finding("UI_SOURCE", "UI source differs from recorded build input", path))
        if path != "ui/src/credits.generated.json" and not approved_source(path, data, root):
            errors.append(finding("SOURCE_CHANGED", "UI source/data addition needs review", path))
    return errors

def approved_source(path, data, root=ROOT):
    """Retain the reviewed base; new packaging inputs need explicit review hashes."""
    extra_path = root / "tools/licence/release-reviewed-inputs.json"
    if extra_path.is_file():
        extra = read_json(extra_path)
        for row in extra.get("files", []):
            if row["path"] == path and row.get("review") and row["sha256"] == provenance.sha(provenance.normalized_source(data)):
                return True
    key = (str(root.resolve()), path)
    if key not in BASE_CACHE:
        result = subprocess.run(["git", "show", BASE + ":" + path], cwd=root, capture_output=True)
        BASE_CACHE[key] = result.stdout if result.returncode == 0 else None
    original = BASE_CACHE[key]
    if original is None:
        return False
    try:
        return provenance.normalized_source(original) == provenance.normalized_source(data)
    except UnicodeError:
        return original == data

def resolved_owner_messages(messages, files, rules, decisions):
    """Resolve only exact known owner questions, never bytes/notices/provenance.

    An explicit redistribute record is the owner's disposition. It does not
    rewrite SPDX data, manufacture a grant or waive other component obligations.
    """
    accepted = {r["component"] for r in decisions if r.get("mode") == "redistribute" and r.get("evidence")}
    permitted = set()
    lookup = {rule["path"]: rule for rule in rules.get("assets", []) + rules.get("reviewed_sources", [])}
    for actual_name, data in files.items():
        name = canonical(actual_name)
        rule = lookup.get(name)
        if not rule:
            continue
        component = "artwork" if name == "ui/dist/bg_nebula.png" else "iau" if name == "server/astrodeck/catalog/brightstars.py" else None
        if component not in accepted:
            continue
        digest = provenance.sha(provenance.normalized_source(data)) if name.endswith(".py") else provenance.sha(data)
        if digest != rule["sha256"]:
            continue
        for suffix in ("registered asset requires an owner decision", "asset licence is outside the allowed policy", "reviewed licence still needs owner clearance", "licence is outside the allowed policy"):
            permitted.add(actual_name + ": " + suffix)
    return [message for message in messages if message not in permitted]


def source_review(files, credits, credits_bytes, ui, root=ROOT, decisions=()):
    rules = read_json(root / "tools/licence/artifact-registry.json")
    normal, added, errors = {}, [], review_ui_inputs(ui, credits_bytes, root)
    warm_base(root, [n for n in files if n.startswith(("native/", "packaging/", "tools/licence_texts/")) or n in {"LICENSE", "THIRD-PARTY-NOTICES.md", "server/setup.py"}])
    for name, data in files.items():
        # Base tar review accounts the application/assets. Extra source packaging
        # cannot be auto-whitelisted by its file suffix or the current checkout.
        if name.startswith(("native/", "packaging/", "tools/licence_texts/")) or name in {"LICENSE", "THIRD-PARTY-NOTICES.md", "server/setup.py"}:
            okay = approved_source(name, data, root)
            added.append({"path": name, "bytes": len(data), "sha256": provenance.sha(data),
                          "component": "reviewed release source/notice" if okay else "UNREVIEWED source"})
            if not okay:
                errors.append(finding("SOURCE_CHANGED", "Release source addition differs from reviewed base/delta", name))
        else:
            normal[name] = data
    # Update a known source rule only for a separately reviewed exact delta.
    # This preserves the existing component/data notice obligations.
    warm_base(root, [rule["path"] for rule in rules.get("reviewed_sources", []) if rule["path"] in normal])
    for rule in rules.get("reviewed_sources", []):
        name = rule["path"]
        if name in normal and approved_source(name, normal[name], root):
            rule["sha256"] = provenance.sha(provenance.normalized_source(normal[name]))
    old_errors, records = audit_artifact.review(normal, rules, credits, ui, credits_bytes, read_json(root / "ui/package-lock.json"))
    old_errors = resolved_owner_messages(old_errors, normal, rules, decisions)
    for message in old_errors:
        code = "UNACCOUNTED" if any(x in message for x in ("unaccounted", "unclassified", "not been reviewed", "no matching")) else "ARTIFACT"
        errors.append(finding(code, message))
    return errors, records + added

def verify_record(files):
    paths = [n for n in files if n.endswith(".dist-info/RECORD")]
    errors = []
    if len(paths) != 1:
        return [finding("WHEEL_RECORD", "Wheel must contain exactly one RECORD")]
    record = paths[0]
    seen = set()
    try:
        for name, encoded, length in csv.reader(io.StringIO(files[record].decode("utf-8"))):
            provenance.portable(name)
            if name in seen:
                raise ValueError("duplicate RECORD row")
            seen.add(name)
            if name not in files:
                raise ValueError("RECORD names absent file")
            if name == record:
                if encoded or length:
                    raise ValueError("RECORD self row must have blank hash/size")
                continue
            expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode()
            if encoded != expected or length != str(len(files[name])):
                errors.append(finding("WHEEL_RECORD", "Wheel member fails RECORD hash/size", name))
        if seen != set(files):
            errors.append(finding("WHEEL_RECORD", "Wheel has unrecorded payloads"))
    except (ValueError, UnicodeError, csv.Error):
        errors.append(finding("WHEEL_RECORD", "Malformed wheel RECORD"))
    return errors

def native_source_review(data, root):
    errors, found = [], set()
    expected = {p.relative_to(root).as_posix() for p in (root / "native").rglob("*")
                if p.is_file() and not any(part in {"target", ".git", "__pycache__", "licenses"} for part in p.relative_to(root / "native").parts)}
    warm_base(root, expected)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for item in archive:
                if item.isdir():
                    continue
                name = provenance.portable(item.name)
                if not item.isfile() or item.size > provenance.MAX_MEMBER or name in found:
                    raise ValueError("unsafe native source archive")
                found.add(name)
                path = name if name.startswith("native/") else "native/" + name
                if not approved_source(path, archive.extractfile(item).read(), root):
                    errors.append(finding("SOURCE_CHANGED", "Native source differs from reviewed base/delta", path))
        expected = {p.relative_to(root).as_posix() for p in (root / "native").rglob("*")
                    if p.is_file() and not any(part in {"target", ".git", "__pycache__", "licenses"} for part in p.relative_to(root / "native").parts)}
        normalized = {n if n.startswith("native/") else "native/" + n for n in found}
        if normalized != expected or not any(n.endswith("Cargo.lock") for n in found) or not any(n.endswith("lib.rs") for n in found):
            errors.append(finding("SOURCE_MISSING", "Native source archive does not contain the complete reviewed native workspace"))
    except (ValueError, tarfile.TarError):
        errors.append(finding("ARCHIVE", "Invalid native source archive"))
    return errors

def license_file_errors(files, stem, metadata):
    errors = []
    for relative in metadata.get_all("License-File", []):
        try:
            relative = provenance.portable(relative)
            path = stem + "/licenses/" + relative
            text = files[path].decode("utf-8")
            if "\0" in text:
                raise ValueError("binary NUL in declared license")
        except (ValueError, KeyError, UnicodeError):
            errors.append(finding("LICENSE_FILE", "PEP 639 License-File must name an included UTF-8 text file", component=relative))
    return errors


def native_machine_matches(data, platform_id):
    try:
        if platform_id == "windows-x86_64":
            if not data.startswith(b"MZ") or len(data) < 64:
                return False
            offset = struct.unpack_from("<I", data, 60)[0]
            return data[offset:offset + 4] == b"PE\0\0" and struct.unpack_from("<H", data, offset + 4)[0] == 0x8664
        if platform_id in {"linux-x86_64", "linux-arm64"}:
            return data[:7] == b"\x7fELF\x02\x01\x01" and struct.unpack_from("<H", data, 18)[0] == {"linux-x86_64":62,"linux-arm64":183}[platform_id]
        if platform_id == "macos-arm64":
            return data[:4] == b"\xcf\xfa\xed\xfe" and struct.unpack_from("<I", data, 4)[0] == 0x0100000c
    except struct.error:
        pass
    return False


def native_review(files, platform_id, credits, root=ROOT, wheel=True):
    errors = verify_record(files) if wheel else []
    metadata_paths = [p for p in files if p.endswith(".dist-info/astrodeck-build.json")]
    if len(metadata_paths) != 1:
        return errors + [finding("NATIVE_SEAL", "Missing or ambiguous native build seal")], rows(files)
    stem = metadata_paths[0].rsplit("/", 1)[0]
    needed = ["MPL-2.0.txt", "THIRD-PARTY-NOTICES.md", "CARGO-NOTICES.txt", "NATIVE-SOURCE.txt", "native-source.tar.gz"]
    notice_paths = {}
    for basename in needed:
        hits = [p for p in files if p.startswith(stem + ("/source/" if basename == "native-source.tar.gz" else "/licenses/")) and p.rsplit("/", 1)[-1] == basename]
        if len(hits) != 1:
            errors.append(finding("NOTICE_MISSING", "Missing or ambiguous native notice", component=basename))
        else:
            notice_paths[basename] = hits[0]
    notice = {n: files.get(notice_paths.get(n, ""), b"") for n in needed}
    for name, body in notice.items():
        if len(body) < (100 if name != "native-source.tar.gz" else 200):
            errors.append(finding("NOTICE_MISSING", "Native artifact lacks required complete notice/source material", stem + "/licenses/" + name))
    package_meta = BytesParser().parsebytes(files.get(stem + "/METADATA", b""))
    errors += license_file_errors(files, stem, package_meta)
    expected_license = tomllib.loads((root / "native/crates/astrodeck-native/pyproject.toml").read_text(encoding="utf-8"))["project"].get("license")
    if expected_license != "MPL-2.0" or package_meta.get("License-Expression") != expected_license:
        errors.append(finding("NATIVE_LICENSE", "Native metadata license expression differs from the reviewed MPL-2.0 project"))
    if provenance.normalized_source(notice["THIRD-PARTY-NOTICES.md"]).strip() != provenance.normalized_source((root / "THIRD-PARTY-NOTICES.md").read_bytes()).strip():
        errors.append(finding("NOTICE_MISSING", "Native artifact does not carry the exact reviewed root third-party notices"))
    extensions = [p for p in files if re.search(r"(?:^|/)astrodeck_native(?:\.[^/]*)?\.(?:pyd|so)$", p)]
    try:
        seal = json.loads(files[metadata_paths[0]])
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        if seal.get("source_commit") != head or not re.fullmatch(r"[0-9a-f]{40}", seal.get("source_commit", "")):
            errors.append(finding("NATIVE_SEAL", "Native source commit is not this build checkout"))
        actual_extensions = {p: provenance.sha(files[p]) for p in extensions}
        if len(extensions) != 1 or seal.get("extension_sha256") != actual_extensions:
            errors.append(finding("NATIVE_SEAL", "Native extension is absent/ambiguous or differs from sealed bytes"))
        actual_sources = {p: provenance.sha(b) for p, b in files.items() if p.startswith(stem + "/source/")}
        if seal.get("source_archive_sha256") != actual_sources or len(actual_sources) != 1:
            errors.append(finding("NATIVE_SEAL", "Native source archive differs from seal or is not ordinary source data"))
        actual_notices = {p: provenance.sha(b) for p, b in files.items() if p.startswith(stem + "/licenses/")}
        if seal.get("notice_sha256") != actual_notices:
            errors.append(finding("NATIVE_SEAL", "Native notices/source archive differ from seal"))
        source_digest = hashlib.sha256()
        with tarfile.open(fileobj=io.BytesIO(notice["native-source.tar.gz"]), mode="r:gz") as archive:
            members = [m for m in archive if m.isfile()]
            for member in members:
                source_digest.update(member.name.encode() + b"\0" + hashlib.sha256(archive.extractfile(member).read()).digest())
        if seal.get("native_source_sha256") != source_digest.hexdigest():
            errors.append(finding("NATIVE_SEAL", "Native source tree digest differs from seal"))
        native_version = tomllib.loads((root / "native/crates/astrodeck-native/pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        app_version = tomllib.loads((root / "server/pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        if package_meta.get("Name") != "astrodeck-native" or package_meta.get("Version") != native_version or seal.get("native_version") != native_version or seal.get("app_version") != app_version:
            errors.append(finding("NATIVE_VERSION", "Native/app version differs from this release source"))
        listed = {stem + "/licenses/" + p for p in package_meta.get_all("License-File", [])}
        if not set(actual_notices).issubset(listed):
            errors.append(finding("NOTICE_MISSING", "Native notices are not declared by wheel METADATA"))
        tags = files.get(stem + "/WHEEL", b"").decode("utf-8")
        platform_tag = {"windows-x86_64": "win_amd64", "linux-x86_64": "x86_64", "linux-arm64": "aarch64", "macos-arm64": "arm64"}.get(platform_id, "unsupported-platform")
        if not any(line.startswith("Tag: cp311-abi3-") and platform_tag in line for line in tags.splitlines()):
            errors.append(finding("PLATFORM", "Native ABI3 tag contradicts requested platform"))
        if seal.get("source_commit", "not-a-commit").encode() not in notice["NATIVE-SOURCE.txt"]:
            errors.append(finding("SOURCE_MISSING", "Native source information omits exact build commit"))
    except (ValueError, TypeError, IndexError, tarfile.TarError, subprocess.CalledProcessError):
        errors.append(finding("NATIVE_SEAL", "Malformed native build seal"))
    if notice["native-source.tar.gz"]:
        errors += native_source_review(notice["native-source.tar.gz"], root)
    for name in extensions:
        binary = files[name]
        if not native_machine_matches(binary, platform_id):
            errors.append(finding("PLATFORM", "Native machine header contradicts the requested architecture/format", name))
    body = notice["CARGO-NOTICES.txt"].decode("utf-8", errors="replace")
    pool = credits.get("licenses", {})
    sbom_paths = [p for p in files if p.startswith(stem + "/sboms/")]
    cargo = []
    if len(sbom_paths) != 1 or not sbom_paths[0].endswith("/astrodeck-native.cyclonedx.json"):
        errors.append(finding("NATIVE_SBOM", "Missing or unknown native dependency SBOM"))
    else:
        try:
            sbom_path = sbom_paths[0]
            raw_sbom = files[sbom_path]
            sbom = json.loads(raw_sbom)
            if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") not in {"1.5", "1.6"}:
                raise ValueError("unknown SBOM format")
            if seal.get("sbom_sha256") != {sbom_path: provenance.sha(raw_sbom)}:
                errors.append(finding("NATIVE_SEAL", "Native dependency SBOM is not bound by the build seal"))
            if re.search(r"file:///[A-Za-z]:/|/Users/|/home/|[A-Za-z]:\\\\Users\\", raw_sbom.decode("utf-8")):
                errors.append(finding("BUILD_PATH", "Native SBOM contains an absolute private build path"))
            lock = tomllib.loads((root / "native/Cargo.lock").read_text(encoding="utf-8"))
            locked = {(x["name"], x["version"]): x for x in lock.get("package", [])}
            current = credits_entries(credits, "cargo")
            refs = {x.get("bom-ref") for x in sbom.get("components", [])}
            root_component = sbom.get("metadata", {}).get("component", {})
            refs.add(root_component.get("bom-ref"))
            refs.update(x.get("bom-ref") for x in root_component.get("components", []))
            refs.discard(None)
            for edge in sbom.get("dependencies", []):
                if edge.get("ref") not in refs or not set(edge.get("dependsOn", [])).issubset(refs):
                    errors.append(finding("NATIVE_SBOM", "Native SBOM contains an unaccounted dependency reference"))
            seen = set()
            for component in sbom.get("components", []):
                key = (component.get("name"), component.get("version"))
                if key in seen or key not in locked:
                    errors.append(finding("NATIVE_SBOM", "Native SBOM component is duplicate or absent from Cargo.lock", component=str(key[0])))
                    continue
                seen.add(key)
                if not locked[key].get("source"):
                    continue
                expressions = [v.get("expression") or (v.get("license") or {}).get("id") for v in component.get("licenses", [])]
                try:
                    if not expressions or any(licence_policy.resolve(e).flag for e in expressions):
                        raise licence_policy.Flag("native dependency licence unresolved")
                except licence_policy.Flag:
                    errors.append(finding("CARGO_POLICY", "Native dependency licence is unresolved", component=key[0]))
                entry = current.get(key[0].lower().replace("_", "-"))
                if entry is None or entry.get("version") != key[1]:
                    errors.append(finding("CREDIT_VERSION", "Native resolved dependency does not match current generated credit", component=key[0]))
                else:
                    cargo.append(entry)
            if not sbom.get("components") or not sbom.get("dependencies"):
                errors.append(finding("NATIVE_SBOM", "Native dependency graph is empty"))
        except (ValueError, TypeError, KeyError):
            errors.append(finding("NATIVE_SBOM", "Malformed native dependency SBOM"))
    for entry in cargo:
        errors += credit_errors(entry, credits, entry["name"])
        if entry["name"] not in body or entry["version"] not in body:
            errors.append(finding("NOTICE_MISSING", "Cargo notices omit resolved component/version", component=entry["name"]))
        for ref in entry.get("texts", []):
            text = pool.get(ref.get("hash"), "")
            if text and " ".join(text.split()) not in " ".join(body.split()):
                errors.append(finding("NOTICE_MISSING", "Cargo full text absent from native notices", component=entry["name"]))
    if provenance.normalized_source(notice["MPL-2.0.txt"]).strip() != provenance.normalized_source((root / "tools/licence_texts/mpl-2.0.txt").read_bytes()).strip():
        errors.append(finding("NOTICE_MISSING", "Native MPL-2.0 full text is missing or truncated"))
    allowed_meta = {"METADATA", "WHEEL", "RECORD", "astrodeck-build.json", "entry_points.txt"}
    init_expected = 'from .astrodeck_native import *\n\n__doc__ = astrodeck_native.__doc__\nif hasattr(astrodeck_native, "__all__"):\n    __all__ = astrodeck_native.__all__\n'
    for path, raw in files.items():
        accepted = path in extensions or path in sbom_paths or path in notice_paths.values() or path.startswith(stem + "/") and path[len(stem)+1:] in allowed_meta
        if not wheel and path.startswith(stem + "/") and path[len(stem)+1:] in {"INSTALLER", "REQUESTED"}:
            # Frozen RECORD/TOC proof authenticates benign installer markers.
            # PEP 610 direct_url.json remains forbidden even with a wheel hash.
            tail = path[len(stem)+1:]
            accepted = (tail == "INSTALLER" and raw.strip() == b"pip") or (tail == "REQUESTED" and not raw)
        if path == "astrodeck_native/__init__.py":
            try:
                accepted = provenance.code_shape(compile(raw, "<native-init>", "exec")) == provenance.code_shape(compile(init_expected, "<native-init>", "exec"))
            except (SyntaxError, ValueError):
                accepted = False
        if not accepted:
            errors.append(finding("UNACCOUNTED", "Unaccounted native wheel payload", path))
    return errors, [dict(r, component="sealed native engine/source/notices") for r in rows(files)]

def server_metadata_review(files):
    errors, records = [], []
    stems = {n.split("/", 1)[0] for n in files if ".dist-info/" in n}
    if len(stems) != 1 or not next(iter(stems), "").startswith("astrodeck-"):
        errors.append(finding("UNACCOUNTED", "Unexpected server distribution metadata root"))
    known = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt"}
    for name, raw in files.items():
        if ".dist-info/" not in name:
            continue
        tail = name.split("/", 1)[1]
        accepted = tail in known
        if tail in {"licenses/LICENSE", "LICENSE"}:
            accepted = provenance.normalized_source(raw).strip() == provenance.normalized_source((ROOT / "LICENSE").read_bytes()).strip()
        try:
            text = raw.decode("utf-8")
            accepted = accepted and "\0" not in text
        except UnicodeError:
            accepted = False
        if not accepted:
            errors.append(finding("UNACCOUNTED", "Unaccounted server-wheel metadata payload", name))
        records.append({"path": name, "bytes": len(raw), "sha256": provenance.sha(raw), "component": "server wheel metadata"})
    return errors, records


def runtime_tool_resolution(component, retained, package):
    """Exact upstream terms apply only to their named embedded runtime paths."""
    known = {
        "pyinstaller": ("11ec72d544b0ecf0831c8615adcb74ac17fbd3688513f55af8367d3d98b0022c", ("dist:pyinstaller/PyInstaller/loader/", "dist:pyinstaller/PyInstaller/hooks/rthooks/", "dist:pyinstaller/PyInstaller/fake-modules/")),
        "pyinstaller-hooks-contrib": ("e0f26791e2ae4726a8b7e8cde0e1fb4ccb0eabfe1be921781f6cf2db918612b3", ("dist:pyinstaller-hooks-contrib/_pyinstaller_hooks_contrib/rthooks/",)),
    }
    rule = known.get(component)
    return bool(rule and retained and all(r.get("record_verified") and r.get("source", "").startswith(rule[1]) for r in retained)
                and any(n.get("text_sha256") == rule[0] for n in (package or {}).get("notices", [])))


def is_native_payload(name, data):
    if "!/" in name:
        return False
    return bool(re.search(r"\.(dll|pyd|dylib|so|a|lib)(?:\.[0-9.]+)?$", name, re.I)) or data.startswith((b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"))


def notice_present(row, raw_hashes, text_hashes, full_texts=()):
    if row.get("sha256") in raw_hashes or row.get("text_sha256") and row["text_sha256"] in text_hashes:
        return True
    text = row.get("text", "")
    return bool(text and provenance.sha(text.encode()) == row.get("text_sha256") and any(text in body for body in full_texts))


def frozen_review(files, kinds, proof, artifact_sha, credits, credits_bytes, ui, root=ROOT, platform_id="source", decisions=()):

    errors, records = [], []
    if proof.get("artifact_sha256") != artifact_sha or proof.get("generator_sha256") != provenance.sha((root / "tools/licence/release_provenance.py").read_bytes()):
        errors.append(finding("PROVENANCE", "Fresh build provenance is absent, stale, or from another collector/artifact"))
    expected = {r["path"]: r for r in proof.get("files", [])}
    if set(expected) != set(files):
        errors.append(finding("PROVENANCE", "Build provenance does not enumerate exactly this payload"))
    ui_errors, credits_present = checked_ui(files, ui, credits_bytes)
    errors += ui_errors + review_ui_inputs(ui, credits_bytes, root)
    entries = credits_entries(credits, "python")
    packages = {p["name"].lower().replace("_", "-"): p for p in proof.get("packages", [])}
    observed = {r["path"]: r for r in read_json(root / "tools/licence/dependency-current-windows-archive.json")["files"]}
    assets = {r["path"]: r for r in read_json(root / "tools/licence/artifact-registry.json")["assets"]}
    notice_hashes = {provenance.sha(b) for b in files.values()}
    full_texts = [provenance.normalized_source(t.encode()).strip().decode() for t in credits.get("licenses", {}).values()] if credits_present else []
    text_hashes = {provenance.sha(provenance.normalized_source(t.encode()).strip()) for t in credits.get("licenses", {}).values()} if credits_present else set()
    for raw in files.values():
        try:
            text_hashes.add(provenance.sha(provenance.normalized_source(raw).strip()))
        except UnicodeError:
            pass
    # Apply the same exact asset, compiler input and full-notice checks to the
    # UI/data retained in a frozen executable as to a source release.
    rules = read_json(root / "tools/licence/artifact-registry.json")
    companion_paths = {p for rule in rules["assets"] for p in rule.get("notices", {})}
    ui_payload = {canonical(n): b for n, b in files.items() if canonical(n).startswith("ui/dist/") or canonical(n) in assets or canonical(n) in companion_paths}
    ui_findings, _ = audit_artifact.review(ui_payload, rules, credits, ui, credits_bytes, read_json(root / "ui/package-lock.json"))
    ui_findings = resolved_owner_messages(ui_findings, ui_payload, rules, decisions)
    errors += [finding("UNACCOUNTED" if "unaccounted" in e or "no matching" in e or "unclassified" in e else "ARTIFACT", e) for e in ui_findings]
    outputs = {r["path"]: r for r in ui.get("files", [])}
    checked_components = set()
    native_files = {n: b for n, b in files.items() if n.startswith("astrodeck_native/") or re.match(r"astrodeck_native-[^/]+\.dist-info/", n)}
    if not native_files:
        errors.append(finding("NATIVE_MISSING", "Frozen artifact omits native engine/source/notices (#630)"))
    else:
        native_errors, _ = native_review(native_files, platform_id, credits, root, wheel=False)
        errors += native_errors
    warm_base(root, [r["source"][5:] for r in expected.values() if r.get("source", "").startswith("repo:") and not canonical(r["path"]).startswith("ui/dist/")])
    for name, data in sorted(files.items()):
        # Analysis can add metadata after the spec's explicit data selection.
        # Never parse or report the installer URL; its exact path is sufficient.
        if re.fullmatch(r"(?:astrodeck|astrodeck[_-]native)-[^/]+\.dist-info/direct_url\.json", name):
            errors.append(finding("PRIVATE_METADATA", "Installer-local application/native URL metadata must not be distributed", name))
        row = expected.get(name, {})
        record = {"path": name, "bytes": len(data), "sha256": provenance.sha(data),
                  "component": row.get("component", "UNKNOWN"), "provenance": row.get("source", "unclassified")}
        records.append(record)
        ui_output = outputs.get(canonical(name))
        ui_exact = ui_output is not None and ui_output.get("sha256") == record["sha256"]
        asset = assets.get(canonical(name))
        asset_exact = asset is not None and asset.get("sha256") == record["sha256"]
        if not (ui_exact or asset_exact) and (row.get("sha256") != record["sha256"] or not row.get("record_verified") or not row.get("method")):
            errors.append(finding("PROVENANCE", "Payload has no verified current build input", name, record["component"]))
        component = row.get("component", "UNKNOWN")
        key = component.lower().replace("_", "-")
        repo_approved = False
        if row.get("source", "").startswith("repo:") and not (ui_exact or asset_exact):
            path = row["source"][5:]
            local = root / path
            repo_approved = local.is_file() and provenance.sha(local.read_bytes()) == row.get("source_sha256") and approved_source(path, local.read_bytes(), root)
            if not repo_approved:
                errors.append(finding("SOURCE_CHANGED", "First-party build input is not the reviewed source", name))
        elif component.lower() not in {"namespace", "pyinstaller archive container", "cpython", "unknown", "astrodeck", "astrodeck-native"} and key not in checked_components:
            checked_components.add(key)
            entry = entries.get(key)
            component_errors = credit_errors(entry, credits, component)
            package = packages.get(key)
            retained = [r for r in expected.values() if r.get("component", "").lower().replace("_", "-") == key]
            if runtime_tool_resolution(key, retained, package):
                component_errors = [e for e in component_errors if e["message"] != component + ": licence is outside the allowed policy"]
            errors += component_errors
            if package is None or entry is None or entry.get("version") != package.get("version"):
                errors.append(finding("CREDIT_VERSION", "Retained package does not match current credit version", component=component))
            for notice in (package or {}).get("notices", []):
                if not notice_present(notice, notice_hashes, text_hashes, full_texts):
                    errors.append(finding("NOTICE_MISSING", "Current retained package notice is absent or differs from compiled credits", component=component))
        if asset and asset["sha256"] != record["sha256"]:
            errors.append(finding("UNACCOUNTED", "Shipped asset differs from reviewed bytes", name))
        if is_native_payload(name, data) and name not in native_files and not asset:
            old = observed.get(name)
            if not old or old["sha256"] != record["sha256"]:
                errors.append(finding("NATIVE_UNREVIEWED", "New native payload needs component/platform rights review; RECORD ownership is not subcomponent clearance", name, component))
            else:
                errors.append(finding("NATIVE_RIGHTS_OPEN", "Previously observed native bytes still require artifact-specific rights disposition", name, component))
        if kinds.get(name) in {"x", "b"} and not is_native_payload(name, data) and name != "base_library.zip" and not (ui_exact or asset_exact or name in native_files):
            old = observed.get(name)
            notice = record["sha256"] in {n["sha256"] for p in packages.values() for n in p.get("notices", [])}
            metadata_tail = name.split(".dist-info/", 1)[1] if ".dist-info/" in name else None
            metadata_ok = metadata_tail in {"METADATA", "WHEEL", "RECORD", "INSTALLER", "REQUESTED", "entry_points.txt", "top_level.txt"}
            if metadata_ok:
                try:
                    metadata_ok = "\0" not in data.decode("utf-8")
                except UnicodeError:
                    metadata_ok = False
            if not (old and old["sha256"] == record["sha256"] or notice or metadata_ok or repo_approved or canonical(name) in companion_paths):
                errors.append(finding("UNACCOUNTED", "New data payload needs artifact-specific source and rights review", name, component))
    # Bootloader output is transformed differently on each OS. Do not pretend
    # a fresh observed prefix hash proves its code/resources came from an input.
    envelope_proof = proof.get("envelope_proof", {})
    if not envelope_proof.get("verified") or envelope_proof.get("artifact_sha256") != artifact_sha or envelope_proof.get("method") != "pyinstaller-6.22.3-windows-default-byte-reconstruction":
        errors.append(finding("ENVELOPE_UNREVIEWED", envelope_proof.get("reason") or "Missing reviewed platform-specific executable reconstruction; retain TOCs and installed bootloader inputs"))
    if not proof.get("python_notices"):
        errors.append(finding("NOTICE_MISSING", "CPython runtime notice provenance was not found"))
    else:
        for row in proof["python_notices"]:
            if not notice_present(row, notice_hashes, text_hashes, full_texts):
                errors.append(finding("NOTICE_MISSING", "Actual CPython build licence text is not in artifact notices"))
    return errors, records

def run(args):
    credits_path = args.credits or ROOT / "ui/src/credits.generated.json"
    credits_bytes = credits_path.read_bytes()
    credits = json.loads(credits_bytes)
    ui = read_json(args.ui_inventory) if args.ui_inventory else {}
    checksum = provenance.sha(args.artifact.read_bytes())
    decisions, errors = load_decisions(ROOT, args.kind)
    envelope = None
    if args.kind == "source-tar":
        files, bad = audit_artifact.read_archive(args.artifact)
        errors += [finding("ARCHIVE", e) for e in bad]
        more, records = source_review(files, credits, credits_bytes, ui, decisions=decisions)
    elif args.kind in {"native-wheel", "server-wheel"}:
        files = provenance.read_zip(args.artifact.read_bytes())
        if args.kind == "native-wheel":
            more, records = native_review(files, args.platform, credits)
        else:
            more = verify_record(files)
            payload = {canonical(n): b for n, b in files.items() if not re.match(r"[^/]+\.dist-info/", n)}
            extra, records = source_review(payload, credits, credits_bytes, ui, decisions=decisions)
            more += extra
            metadata_errors, metadata_records = server_metadata_review(files)
            more += metadata_errors
            records += metadata_records
    else:
        files, kinds, envelope = provenance.read_frozen(args.artifact)
        proof = read_json(args.build_provenance) if args.build_provenance else {}
        more, records = frozen_review(files, kinds, proof, checksum, credits, credits_bytes, ui, platform_id=args.platform, decisions=decisions)
    errors += more + owner_findings(files, args.kind, decisions)
    # Deterministic structured output. Findings never become PASS merely because
    # the build job expects a known owner question.
    unique = {json.dumps(e, sort_keys=True): e for e in errors}
    errors = [unique[k] for k in sorted(unique)]
    return {"schema_version": 1, "artifact": {"name": args.artifact.name, "kind": args.kind,
            "platform": args.platform, "sha256": checksum, "bytes": args.artifact.stat().st_size},
            "members": records, "findings": errors, "owner_decisions": decisions,
            "envelope": envelope, "accounted": not any(e["code"] in UNACCOUNTED for e in errors),
            "cleared": not errors, "scope": "Only these artifact bytes and supplied current build evidence; no uninspected platform clearance"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=sorted(KINDS), required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--credits", type=Path)
    parser.add_argument("--ui-inventory", type=Path)
    parser.add_argument("--build-provenance", type=Path)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:
        # Paths, package contents and build logs stay private. Never emit an
        # exception's possibly sensitive raw message in a release workflow.
        result = {"schema_version": 1, "artifact": {"name": args.artifact.name, "kind": args.kind, "platform": args.platform},
                  "members": [], "findings": [finding("ARCHIVE", "Audit could not complete: " + type(exc).__name__)],
                  "accounted": False, "cleared": False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    counts = {}
    for row in result["findings"]:
        counts[row["code"]] = counts.get(row["code"], 0) + 1
    print(f"Release gate: {'PASS' if result['cleared'] else 'FAIL'} ({len(result['members'])} members; {len(result['findings'])} blockers)")
    print(json.dumps(counts, sort_keys=True))
    return 0 if result["cleared"] else 1

if __name__ == "__main__":
    raise SystemExit(main())