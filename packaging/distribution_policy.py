"""One owner decision source for release payload selection.

Pending records preserve historical inclusion; inclusion is not legal approval.
The source tar excludes Player One libraries, while frozen/server wheels retain
those libraries until the owner explicitly chooses another mode.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"source-tar", "frozen", "server-wheel", "native-wheel"}
MODES = {"pending", "fetch-only", "redistribute"}
LIBRARY = re.compile(r"\.(?:dll|so(?:\..*)?|dylib|lib|a)$", re.I)


def load_policy(root: Path = ROOT) -> dict:
    path = root / "packaging/distribution-policy.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported distribution policy schema")
    decisions = data.get("decisions", {})
    if set(decisions) != {"playerone", "dss2", "artwork", "wcslib", "astrospheric", "iau"}:
        raise ValueError("distribution decision set changed; review required")
    for key, row in decisions.items():
        if row.get("mode") not in MODES or not row.get("evidence"):
            raise ValueError("invalid owner distribution decision")
    pending = decisions["playerone"].get("pending_by_artifact")
    if pending != {"source-tar": False, "frozen": True, "server-wheel": True, "native-wheel": False}:
        raise ValueError("pending Player One behavior must preserve artifact history")
    if decisions["dss2"]["mode"] != "fetch-only":
        raise ValueError("DSS2 redistribution is outside this policy change")
    return data


def includes_playerone(kind: str, policy: dict) -> bool:
    if kind not in KINDS:
        raise ValueError("unknown artifact kind")
    row = policy["decisions"]["playerone"]
    if row["mode"] == "pending":
        return row["pending_by_artifact"][kind]
    return row["mode"] == "redistribute"


def include_file(relative: str, kind: str, policy: dict) -> bool:
    """Decide a payload path relative to server/astrodeck; never edit sources."""
    parts = PurePosixPath(relative.replace("\\", "/")).parts
    if not parts or ".." in parts or PurePosixPath(relative).is_absolute():
        raise ValueError("payload path must be relative and contained")
    lowered = tuple(p.lower() for p in parts)
    if lowered[:3] == ("catalog", "_bundled_pack", "dss2color"):
        return False
    if lowered[:2] == ("vendor", "playerone") and LIBRARY.search(parts[-1]):
        return includes_playerone(kind, policy)
    if kind not in KINDS:
        raise ValueError("unknown artifact kind")
    return True


def decision_records(policy: dict, kind: str) -> list[dict]:
    if kind not in KINDS:
        raise ValueError("unknown artifact kind")
    rows = []
    for key, value in policy["decisions"].items():
        row = {"component": key, "mode": value["mode"], "artifact": kind,
               "requires_owner_decision": value["mode"] == "pending",
               "evidence": value["evidence"]}
        if key == "playerone":
            row["include_binaries"] = includes_playerone(kind, policy)
        if key == "dss2":
            row["include_tiles"] = False
        rows.append(row)
    return rows


def selected_files(package: Path, kind: str, policy: dict):
    package = package.resolve()
    for path in sorted(package.rglob("*")):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(package):
            raise ValueError("payload symlink escapes package")
        relative = path.relative_to(package).as_posix()
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if include_file(relative, kind, policy):
            yield path, relative


def package_data(policy: dict) -> list[str]:
    """Explicit pattern list; no vendor wildcard can reintroduce Player One."""
    patterns = ["vendor/manifest.json", "vendor/zwo/*.dll", "vendor/zwo/*.txt",
                "vendor/zwo/*.md", "vendor/zwo/linux-*/*", "vendor/zwo/macos/*",
                "vendor/playerone/LICENSE", "vendor/playerone/*.md", "catalog/data/*.tsv"]
    if includes_playerone("server-wheel", policy):
        patterns += ["vendor/playerone/*.dll", "vendor/playerone/linux-*/*", "vendor/playerone/macos/*"]
    return patterns


def check_package_data(root: Path = ROOT, policy: dict | None = None) -> None:
    policy = policy if policy is not None else load_policy(root)
    actual = tomllib.loads((root / "server/pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
    if actual != {"astrodeck": package_data(policy)}:
        raise ValueError("static package-data disagrees with distribution policy; run --sync-package-data")


def sync_package_data(root: Path = ROOT) -> None:
    """Update only the static package-data table; direct pip reads this table."""
    path = root / "server/pyproject.toml"
    text = path.read_text(encoding="utf-8")
    section = "[tool.setuptools.package-data]"
    start = text.index(section)
    following = re.search(r"^\[", text[start + len(section):], re.M)
    end = start + len(section) + following.start() if following else len(text)
    rendered = section + "\n# Generated by packaging/distribution_policy.py --sync-package-data.\nastrodeck = [\n"
    rendered += "".join("    " + json.dumps(value) + ",\n" for value in package_data(load_policy(root)))
    rendered += "]\n"
    path.write_text(text[:start] + rendered + text[end:], encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--kind", choices=sorted(KINDS))
    choice.add_argument("--check-package-data", action="store_true")
    choice.add_argument("--sync-package-data", action="store_true")
    args = parser.parse_args()
    if args.sync_package_data:
        sync_package_data()
    elif args.check_package_data:
        check_package_data()
        print("Static package-data agrees with central distribution policy")
    else:
        print(json.dumps(decision_records(load_policy(), args.kind), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
