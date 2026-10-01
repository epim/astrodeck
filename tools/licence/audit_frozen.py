"""Inspect a PyInstaller artifact without running it.

The checked Windows baseline is evidence of contents, not an allowlist granting
rights. Changed/new members and the documented unresolved baseline findings
fail this gate. Other platforms need their own reviewed baseline and disposition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_frozen(path):
    # Install PyInstaller only in a private audit venv; no application execution.
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(path))
    files = {}
    for name, row in archive.toc.items():
        if row[-1] == "o":  # PyInstaller interpreter option, not a payload file.
            continue
        files[name.replace("\\", "/")] = archive.extract(name)
    return files


def review_frozen(files, baseline, artifact_hash):
    expected = {row["path"]: row for row in baseline["files"] if row["archive_type"] != "o"}
    findings, records = [], []
    if artifact_hash != baseline["artifact_sha256"]:
        findings.append("Executable bytes differ from the reviewed baseline; bootloader and payload need a new review")
    for name, data in sorted(files.items()):
        row = {"path": name, "bytes": len(data), "sha256": digest(data)}
        known = expected.get(name)
        if known is None:
            findings.append(f"{name}: unaccounted executable member")
        elif known["sha256"] != row["sha256"]:
            findings.append(f"{name}: executable member changed since review")
        row["reviewed_bytes"] = known is not None and known["sha256"] == row["sha256"]
        records.append(row)
    for name in sorted(set(expected) - set(files)):
        findings.append(f"{name}: reviewed executable member is missing")
    for name in files:
        lower = name.lower()
        if "/vendor/playerone/" in "/" + lower and re.search(r"\.(?:dll|dylib)$|\.so(?:\.|$)", lower):
            findings.append(f"{name}: Player One redistribution needs owner confirmation (#632)")
        if "bg_nebula." in lower:
            findings.append(f"{name}: artwork provenance needs owner confirmation (#637)")
        if lower.startswith("astropy/wcs/_wcs.") and re.search(r"\.(?:pyd|so)(?:\.|$)", lower):
            findings.append(f"{name}: WCSLIB LGPL source/relinking disposition remains open (#638)")
        if "libscipy_openblas" in lower or "libopenblas" in lower:
            findings.append(f"{name}: OpenBLAS/GCC runtime exception disposition remains open (#638)")
    # These are demonstrated limits of this exact baseline, not inferred licence
    # obligations of all future executables. A new review must explicitly close
    # them rather than inheriting a PASS from known member hashes.
    findings += [
        "Reviewed baseline credits predate the resolved Python build environment; regenerate inside each release environment",
        "Reviewed baseline omits the native engine (#630); audit its final linked wheel when packaging is corrected",
        "Microsoft runtime redistribution basis and complete native subcomponent coverage remain unverified",
        "No release clearance: baseline evidence documents open findings rather than approved distribution",
    ]
    return sorted(set(findings)), records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--baseline", type=Path, default=ROOT / "tools/licence/dependency-current-windows-archive.json")
    parser.add_argument("--report", type=Path, default=ROOT / ".probe/licence/frozen-gate-report.json")
    args = parser.parse_args()
    files = read_frozen(args.executable)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    checksum = digest(args.executable.read_bytes())
    findings, records = review_frozen(files, baseline, checksum)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({
        "artifact_sha256": checksum, "files": records, "findings": findings,
        "cleared": not findings, "scope": "Exact local Windows executable; never run.",
    }, indent=2) + "\n", encoding="utf-8")
    for finding in findings:
        print(finding)
    print(f"Frozen gate: {'FAIL' if findings else 'PASS'} ({len(records)} members, {len(findings)} findings)")
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
