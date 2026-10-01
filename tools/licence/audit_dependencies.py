"""Policy gate for a live locked Cargo graph and all npm lock entries.

Build-only npm/Cargo entries are retained as such; inclusion in this report is
not a claim that their code occurs in a release. The artifact gate separately
checks actual retained UI inputs, native payloads and accompanying notices.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools"))
import licence_policy as policy


def review(packages):
    findings = []
    for row in packages:
        name = row["name"]
        try:
            result = policy.resolve(row.get("spdx"))
            if result.flag:
                findings.append(f"{name}: owner review remains required")
        except policy.Flag:
            findings.append(f"{name}: licence is outside the allowed policy")
    return sorted(set(findings))


def cargo_rows(metadata):
    # --locked/--offline metadata is live evidence, never a hand-written SPDX list.
    return [{"ecosystem": "cargo", "name": p["name"], "version": p["version"],
             "spdx": p.get("license"), "source": p.get("source") or "workspace",
             "scope": "resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths"}
            for p in metadata["packages"]]


def npm_rows(lock):
    return [{"ecosystem": "npm", "name": path.split("node_modules/")[-1],
             "version": meta["version"], "spdx": meta.get("license"),
             "source": meta.get("resolved", "workspace"),
             "scope": "development/build" if meta.get("dev") else "production-resolution"}
            for path, meta in lock["packages"].items() if path]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / ".probe/licence/dependency-gate-report.json")
    args = parser.parse_args()
    command = ["cargo", "metadata", "--locked", "--offline", "--format-version", "1",
               "--manifest-path", str(ROOT / "native/Cargo.toml")]
    metadata = json.loads(subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8").stdout)
    lock = ROOT / "ui/package-lock.json"
    packages = cargo_rows(metadata) + npm_rows(json.loads(lock.read_text(encoding="utf-8")))
    findings = review(packages)
    fingerprints = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (lock, ROOT / "native/Cargo.lock")}
    report = {"scope": "SPDX policy on resolved graphs; not binary, notice or service clearance",
              "inputs": fingerprints, "packages": packages, "findings": findings, "policy_pass": not findings}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for finding in findings:
        print(finding)
    print(f"Dependency policy: {'FAIL' if findings else 'PASS'} ({len(packages)} packages)")
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
