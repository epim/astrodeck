# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Fresh build-input evidence for release archives; never execute application code.

Run in the same private Python environment as PyInstaller. TOCs are parsed with
literal_eval, installed RECORD hashes are verified, and Python code is compared
with compiled source while ignoring only filename/line-table bookkeeping.
"""
from __future__ import annotations
import argparse
import ast
import base64
import hashlib
import importlib.metadata as metadata
import io
import json
import marshal
from pathlib import Path
import platform
import re
import shutil
import tempfile
import struct
import sys
import sysconfig
import types
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
MAX_MEMBER = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
TYPES = {"BINARY", "EXTENSION", "DATA", "PYMODULE", "PYMODULE-1", "PYMODULE-2", "PYSOURCE", "PYSOURCE-1", "PYSOURCE-2", "PYZ", "ZIPFILE", "EXECUTABLE"}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def portable(name):
    name = name.replace("\\", "/")
    parts = name.split("/")
    if not name or name.startswith("/") or ":" in name or any(p in {"", ".", ".."} for p in parts):
        raise ValueError("unsafe archive member path")
    return name

def normalized_source(data):
    return data.decode("utf-8-sig").replace("\r\n", "\n").encode("utf-8")

def notice_record(data):
    row = {"sha256": sha(data), "bytes": len(data)}
    try:
        normalized = normalized_source(data).strip()
        row["text_sha256"] = sha(normalized)
        row["text"] = normalized.decode("utf-8")
    except UnicodeError:
        pass
    return row


def code_shape(code):
    if not isinstance(code, types.CodeType):
        raise ValueError("not a Python code object")
    def const(value):
        if isinstance(value, types.CodeType):
            return ("code", code_shape(value))
        if isinstance(value, tuple):
            return ("tuple", tuple(const(x) for x in value))
        if isinstance(value, frozenset):
            return ("frozenset", tuple(sorted((const(x) for x in value), key=repr)))
        return (type(value).__name__, repr(value))
    return (code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
            code.co_nlocals, code.co_stacksize, code.co_flags, code.co_code.hex(), code.co_name, code.co_qualname,
            tuple(const(x) for x in code.co_consts), code.co_names, code.co_varnames,
            code.co_freevars, code.co_cellvars, getattr(code, "co_exceptiontable", b"").hex())

def code_matches(payload, source, pyc=False):
    try:
        actual = code_shape(marshal.loads(payload[16:] if pyc else payload))
        if source.suffix == ".pyc":
            return actual == code_shape(marshal.loads(source.read_bytes()[16:]))
        raw = source.read_bytes()
        return any(actual == code_shape(compile(raw, "<release-input>", "exec", optimize=n, dont_inherit=True)) for n in (0, 1, 2))
    except (ValueError, TypeError, EOFError, SyntaxError, OSError):
        return False

def read_zip(raw):
    files = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            name = portable(item.filename)
            mode = item.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                raise ValueError("archive contains a symbolic link")
            total += item.file_size
            if name in files or item.file_size > MAX_MEMBER or total > MAX_TOTAL:
                raise ValueError("duplicate or oversized archive member")
            files[name] = archive.read(item)
    return files

def validate_pyz(data):
    """Validate the raw list before PyInstaller converts it into a dictionary."""
    if len(data) < 17 or data[:4] != b"PYZ\0" or data[12:17] != b"\0" * 5:
        raise ValueError("invalid PYZ header")
    offset = struct.unpack_from("!i", data, 8)[0]
    if offset < 17 or offset >= len(data):
        raise ValueError("invalid PYZ table offset")
    stream = io.BytesIO(data[offset:])
    entries = marshal.load(stream)
    if stream.tell() != len(data) - offset or not isinstance(entries, list):
        raise ValueError("invalid PYZ table/trailing data")
    seen, spans = set(), []
    total = 0
    for name, details in entries:
        if not isinstance(name, str) or not all(part.isidentifier() for part in name.split(".")) or name in seen:
            raise ValueError("duplicate or invalid PYZ member name")
        seen.add(name)
        kind, start, length = details
        if kind not in {0, 1, 3} or not isinstance(start, int) or not isinstance(length, int) or start < 17 or length < 0 or start + length > offset:
            raise ValueError("invalid PYZ member range")
        if kind == 3:
            if length:
                raise ValueError("namespace contains payload")
            continue
        spans.append((start, start + length))
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(data[start:start+length], MAX_MEMBER + 1)
        total += len(raw)
        if len(raw) > MAX_MEMBER or total > MAX_TOTAL or not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
            raise ValueError("invalid or oversized PYZ compressed payload")
    cursor = 17
    for start, end in sorted(spans):
        if start != cursor or end <= start:
            raise ValueError("PYZ overlapping or unexplained payload bytes")
        cursor = end
    if cursor != offset:
        raise ValueError("PYZ unexplained payload bytes")
    return seen


def read_frozen(path):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(path))
    # PyInstaller's mapping would overwrite duplicate names. Validate its raw TOC
    # separately before using it. Format reference: upstream archive/readers.py.
    with path.open("rb") as stream:
        cookie = struct.Struct("!8sIIII64s")
        stream.seek(archive._end_offset - cookie.size)
        _, _, toc_offset, toc_length, _, _ = cookie.unpack(stream.read(cookie.size))
        stream.seek(archive._start_offset + toc_offset)
        table = stream.read(toc_length)
    pos, seen = 0, set()
    header = struct.Struct("!IIIIBc")
    while pos < len(table):
        if len(table) - pos < header.size:
            raise ValueError("truncated CArchive table")
        size, offset, packed, unpacked, compressed, kind = header.unpack_from(table, pos)
        if size <= header.size or pos + size > len(table) or unpacked > MAX_MEMBER or offset + packed > toc_offset:
            raise ValueError("invalid CArchive table entry")
        name = table[pos + header.size:pos + size].rstrip(b"\0").decode("utf-8")
        if kind != b"o":
            name = portable(name)
            if name in seen:
                raise ValueError("duplicate CArchive member")
            seen.add(name)
        pos += size
    files, kinds, total = {}, {}, 0
    for raw_name, toc in archive.toc.items():
        if toc[-1] == "o":
            continue
        name = portable(raw_name)
        data = archive.extract(raw_name)
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError("oversized CArchive")
        files[name], kinds[name] = data, toc[-1]
        if toc[-1] == "z":
            raw_names = validate_pyz(data)
            pyz = archive.open_embedded_archive(raw_name)
            if raw_names != set(pyz.toc):
                raise ValueError("PYZ reader changed the verified member set")
            for module in sorted(pyz.toc):
                child = name + "!/" + module
                value = pyz.extract(module, raw=True)
                total += len(value or b"")
                if total > MAX_TOTAL:
                    raise ValueError("oversized expanded CArchive")
                files[child], kinds[child] = value or b"", "namespace" if value is None else "python-code"
        elif name == "base_library.zip":
            for child, value in read_zip(data).items():
                total += len(value)
                if total > MAX_TOTAL:
                    raise ValueError("oversized expanded CArchive")
                files[name + "!/" + child], kinds[name + "!/" + child] = value, "pyc"
    envelope = {"prefix_bytes": archive._start_offset,
                "prefix_sha256": sha(path.read_bytes()[:archive._start_offset]),
                "suffix_bytes": path.stat().st_size - archive._end_offset}
    return files, kinds, envelope

def toc_rows(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 3 and isinstance(value[0], str) and isinstance(value[2], str) and value[2] in TYPES:
            yield value
        else:
            for child in value:
                yield from toc_rows(child)

class Sources:
    def __init__(self, root=ROOT):
        self.root = root.resolve()
        self.paths, self.packages = {}, {}
        self.base = Path(sys.base_prefix).resolve()
        runtime_manifest = self.root / "tools/licence/release-reviewed-runtime.json"
        runtime = json.loads(runtime_manifest.read_text(encoding="utf-8")) if runtime_manifest.is_file() else {}
        self.runtime_inputs = {r["path"]: r for r in runtime.get("files", []) if r.get("python") == platform.python_version() and r.get("review") and r.get("source_url")}

        for dist in metadata.distributions():
            name = dist.metadata.get("Name") or "UNKNOWN"
            version = dist.version
            key = name.lower().replace("_", "-")
            notices = []
            for item in dist.files or ():
                path = Path(dist.locate_file(item)).resolve()
                if not path.is_file():
                    continue
                record_ok = False
                if item.hash and item.hash.mode == "sha256":
                    actual = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).rstrip(b"=").decode()
                    record_ok = actual == item.hash.value
                if str(item).replace("\\", "/").endswith(".dist-info/RECORD") and item.hash is None:
                    # Wheel RECORD intentionally has no self hash. Its bytes
                    # must still match the retained build input exactly.
                    record_ok = True
                row = {"source": "dist:" + key + "/" + str(item).replace("\\", "/"),
                       "component": name, "version": version, "record_verified": record_ok}
                self.paths[str(path).casefold()] = row
                lower = str(item).lower()
                if re.fullmatch(r"(?:license|licence|copying|notice|authors|copyright)(?:[-._].*)?", Path(lower).name) and Path(lower).suffix not in {".py", ".pyc", ".pyd", ".so"}:
                    notices.append({"source": row["source"], **notice_record(path.read_bytes())})
            self.packages[key] = {"name": name, "version": version,
                                  "spdx": dist.metadata.get("License-Expression"), "notices": notices}
    def identify(self, path):
        path = path.resolve()
        # Resolve the roots too: resolve() expands a Windows 8.3 short name
        # (the hosted runner's temp dir is under RUNNER~1, which resolves to
        # runneradmin), so an unresolved root never contains the resolved
        # path and every reviewed runtime input read as unreviewed (release
        # run 37669607167, test_reviewed_runtime_input_requires_exact_hash).
        root = Path(self.root).resolve()
        base = Path(self.base).resolve()
        if path.is_relative_to(root):
            relative = path.relative_to(root).as_posix()
            if not relative.startswith(".probe/"):
                return {"source": "repo:" + relative, "component": "AstroDeck", "record_verified": True}
        if row := self.paths.get(str(path).casefold()):
            return dict(row)
        if path.is_relative_to(base):
            relative = path.relative_to(base).as_posix()
            rule = self.runtime_inputs.get(relative)
            if rule and sha(path.read_bytes()) == rule.get("sha256"):
                return {"source": "python:" + relative, "component": "CPython", "version": platform.python_version(), "record_verified": True}
            # Installation location does not authenticate CPython files. A
            # future reviewed upstream runtime manifest must identify exact
            # source/binary bytes; arbitrary added Lib/site-packages files may
            # never borrow runtime attribution or clearance.
            return {"source": "runtime-unreviewed:" + relative, "component": "Unreviewed runtime input", "version": platform.python_version(), "record_verified": False}
        return {"source": "unclassified", "component": "UNKNOWN", "record_verified": False}

def reconstruct_envelope(artifact, build_dir, index, root=ROOT):
    """Reconstruct reviewed default Windows transforms without running the EXE.

    PyInstaller 6.22.3 building/api.py EXE.assemble specifies these steps.
    Other platforms/options remain explicit unproven cases, not automatic PASS.
    """
    result = {"verified": False, "method": "unreviewed-platform-transform"}
    if sys.platform != "win32":
        result["reason"] = "Implement and review this platform's ELF/Mach-O transform reconstruction"
        return result
    try:
        tocs = list(build_dir.rglob("EXE-00.toc"))
        if len(tocs) != 1 or metadata.version("pyinstaller") != "6.22.3":
            raise ValueError("unsupported EXE layout/version")
        guts = ast.literal_eval(tocs[0].read_text(encoding="utf-8"))
        if len(guts) != 22 or not guts[1] or guts[2] or guts[3] or guts[5] is not None or guts[6] or guts[7] or not guts[9] or guts[16] or guts[17] or guts[18]:
            raise ValueError("unreviewed bootloader options")
        if len(guts[20]) != 1:
            raise ValueError("ambiguous bootloader")
        bootloader = Path(guts[20][0][1])
        icon_path = Path(guts[4])
        sources = [index.identify(bootloader), index.identify(icon_path)]
        if any(r.get("component", "").lower() != "pyinstaller" or not r.get("record_verified") for r in sources):
            raise ValueError("bootloader/icon lacks installed RECORD provenance")
        if not sources[0]["source"].endswith("/PyInstaller/bootloader/Windows-64bit-intel/run.exe") or not sources[1]["source"].endswith("/PyInstaller/bootloader/images/icon-console.ico"):
            raise ValueError("unreviewed bootloader/icon inputs")
        from PyInstaller.utils.win32 import icon, winmanifest, winresource, winutils
        import pefile
        manifest = winmanifest.create_application_manifest(None, False, False)
        if guts[8] != manifest:
            raise ValueError("unreviewed manifest content")
        pkg = Path(guts[14]).read_bytes()
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(artifact))
        actual = artifact.read_bytes()
        if actual[archive._start_offset:archive._end_offset] != pkg or archive._end_offset != len(actual):
            raise ValueError("PKG or appended bytes do not match artifact")
        scratch = root / ".probe/release"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="envelope-proof-", dir=scratch) as directory:
            target = Path(directory) / "reconstructed.exe"
            shutil.copyfile(bootloader, target)
            winresource.remove_all_resources(str(target))
            icon.CopyIcons_FromIco(str(target), [str(icon_path)])
            winmanifest.write_manifest_to_executable(str(target), manifest)
            with target.open("ab") as output:
                output.write(pkg)
            pe = pefile.PE(data=actual, fast_load=True)
            timestamp = pe.FILE_HEADER.TimeDateStamp
            pe.close()
            winutils.set_exe_build_timestamp(str(target), timestamp)
            winutils.update_exe_pe_checksum(str(target))
            rebuilt = target.read_bytes()
        if rebuilt != actual:
            raise ValueError("reconstructed bytes differ from artifact")
        result = {"verified": True, "method": "pyinstaller-6.22.3-windows-default-byte-reconstruction",
                  "artifact_sha256": sha(actual), "bootloader_sha256": sha(bootloader.read_bytes()),
                  "icon_sha256": sha(icon_path.read_bytes()), "manifest_sha256": sha(manifest),
                  "pkg_sha256": sha(pkg), "inputs": sources,
                  "transforms": ["remove resources", "installed default console icon", "standard asInvoker manifest", "append exact PKG", "build timestamp", "PE checksum"],
                  "source": "https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/PyInstaller/building/api.py"}
    except Exception as exc:
        result["reason"] = "Windows envelope reconstruction failed: " + type(exc).__name__
    return result


def collect(artifact, build_dir, root=ROOT):
    files, kinds, envelope = read_frozen(artifact)
    index = Sources(root)
    inputs, toc_hashes = {}, []
    for toc in sorted(build_dir.rglob("*-00.toc")):
        toc_hashes.append({"name": toc.relative_to(build_dir).as_posix(), "sha256": sha(toc.read_bytes())})
        value = ast.literal_eval(toc.read_text(encoding="utf-8"))
        for name, source, kind in toc_rows(value):
            if source and source != "-":
                path = Path(source)
                if path.is_file():
                    inputs.setdefault(name.replace("\\", "/"), []).append((path, kind))
    if not toc_hashes:
        raise ValueError("no retained PyInstaller build TOCs")
    records, issues = [], []
    for name, data in sorted(files.items()):
        kind = kinds[name]
        lookup = name
        if "!/" in name:
            lookup = name.split("!/", 1)[1]
            if kind == "pyc":
                lookup = lookup.removesuffix(".pyc").replace("/", ".").removesuffix(".__init__")
        options = list(inputs.get(lookup, []))
        if lookup.startswith("pyimod"):
            # The TOC can name a temporary localpycs file. Require equivalence
            # to the installed PyInstaller loader source as well.
            source = Path(metadata.distribution("pyinstaller").locate_file("PyInstaller/loader/" + lookup + ".py"))
            if source.is_file():
                options.append((source, "PYMODULE"))
        if kind == "z":
            # PyInstaller can rename PYZ-00.pyz to PYZ.pyz in the CArchive.
            # Compare actual bytes to retained PYZ inputs, never trust the name.
            options = [row for values in inputs.values() for row in values if row[1] == "PYZ"]
        match = None
        method = None
        for path, source_kind in options:
            if kind in {"python-code", "pyc", "s", "m", "M"}:
                ok = code_matches(data, path, pyc=kind == "pyc")
                how = "compiled-source-equivalent"
            else:
                ok = sha(path.read_bytes()) == sha(data)
                how = "exact-input-bytes"
            if ok:
                # Prefer original RECORD/repository source over temporary caches.
                found = index.identify(path)
                if match is None or found["record_verified"]:
                    match = {**found, "source_sha256": sha(path.read_bytes()), "input_kind": source_kind}
                    method = how
                if found["record_verified"]:
                    break
        if kind == "namespace":
            match, method = {"source": "python-namespace:" + lookup, "component": "namespace", "record_verified": True}, "empty-namespace"
        if kind == "z" or name == "base_library.zip":
            # The container itself is matched to a build input; nested payloads
            # are independently reviewed and cannot borrow this classification.
            if match is not None:
                match["component"] = "PyInstaller archive container"
                match["record_verified"] = True
        row = {"path": name, "sha256": sha(data), "bytes": len(data), "kind": kind,
               "method": method, **(match or {"source": "unclassified", "component": "UNKNOWN", "record_verified": False})}
        if match is None:
            issues.append({"code": "UNPROVEN_BUILD_INPUT", "path": name})
        records.append(row)
    python_notices = []
    for path in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "share/doc/python3.12/copyright"):
        if path.is_file():
            python_notices.append({**notice_record(path.read_bytes()),
                                   "source": "python:" + path.relative_to(sys.base_prefix).as_posix()})
    return {"schema_version": 1, "generator": "tools/licence/release_provenance.py",
            "generator_sha256": sha(Path(__file__).read_bytes()), "artifact_sha256": sha(artifact.read_bytes()),
            "python": platform.python_version(), "python_notices": python_notices,
            "pyinstaller": metadata.version("pyinstaller"), "toc_inputs": toc_hashes,
            "files": records, "packages": list(index.packages.values()), "envelope": envelope,
            "findings": issues, "envelope_proof": reconstruct_envelope(artifact, build_dir, index, root),
            "scope": "Current build input provenance; not distribution clearance"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = collect(args.artifact, args.build_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Build provenance: {len(result['files'])} payloads; {len(result['findings'])} unproven inputs")
    return 0  # Evidence is produced even with gaps. audit_release enforces them.

if __name__ == "__main__":
    raise SystemExit(main())