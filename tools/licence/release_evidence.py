# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The ledger of reviewed release inputs and the command that maintains it (#1160, #1186).

    python tools/licence/release_evidence.py status [--json] [--ui-inventory P] [--frozen-provenance P]
    python tools/licence/release_evidence.py sync
    python tools/licence/release_evidence.py approve --path P [--path Q ...] --review TEXT

tools/licence/release-reviewed-inputs.json is the one place a changed file is
recorded as reviewed. A file is approved when the ledger holds a row whose digest
equals the file, or, with no row, when the file equals its copy at the ledger's
`base` commit. A row that contradicts the file approves nothing, even when the
file equals the base: the record must be able to fail. `status` reads and
reports, `sync` only removes rows that say nothing the base does not, and only
`approve` writes a digest for changed bytes, for the files named on its command
line, with a review sentence written by the person who read them.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_provenance as provenance

LEDGER = "tools/licence/release-reviewed-inputs.json"
REGISTRY = "tools/licence/artifact-registry.json"
SCHEMA_VERSION = 2
#: The digest function, by name: a different function is a different schema.
DIGEST_NAME = "normalized-utf8-sha256"
MIN_REVIEW = 40
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
#: The source gate reads these through the reviewed ledger or the base copy.
SOURCE_EXTRA_PREFIXES = ("native/", "packaging/", "tools/licence_texts/")
SOURCE_EXTRA_NAMES = {"LICENSE", "THIRD-PARTY-NOTICES.md", "server/setup.py"}
#: What scripts/build_release.py puts in the tarball besides server/ and native/.
TARBALL_EXTRAS = {
    "packaging/distribution-policy.json", "packaging/distribution_policy.py",
    "packaging/build_native.py", "packaging/native_probe.py", "LICENSE",
    "THIRD-PARTY-NOTICES.md", "tools/licence_texts/mpl-2.0.txt",
    "tools/licence_texts/apache-2.0.txt", "server/setup.py",
}


def digest(data):
    """sha256 of the text with a BOM removed and CRLF folded to LF.

    Committed blobs are LF and a CRLF checkout folds to the same text, so a
    working copy and `git show HEAD:path` give one digest.
    """
    return provenance.sha(provenance.normalized_source(data))


def is_source_extra(name):
    return name.startswith(SOURCE_EXTRA_PREFIXES) or name in SOURCE_EXTRA_NAMES


def is_registry_source(name):
    """The paths audit_artifact.review classes as application source."""
    return (name.startswith("server/astrodeck/") and name.endswith(".py")
            and "/vendor/" not in name) or name == "server/pyproject.toml"


def read_ledger(root=ROOT):
    """(ledger, problems); the ledger is None when there are problems."""
    path = Path(root) / LEDGER
    if not path.is_file():
        return None, [f"ledger {LEDGER} is missing"]
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        return None, [f"ledger {LEDGER} is not valid JSON"]
    if not isinstance(ledger, dict):
        return None, [f"ledger {LEDGER} is not an object"]
    problems = []
    if ledger.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"ledger schema_version is {ledger.get('schema_version')!r}, expected {SCHEMA_VERSION}")
    if ledger.get("digest") != DIGEST_NAME:
        problems.append(f"ledger digest is {ledger.get('digest')!r}, expected {DIGEST_NAME!r}")
    if not HEX40.fullmatch(str(ledger.get("base", ""))):
        problems.append("ledger base is not a 40 hex commit")
    rows = ledger.get("files")
    if not isinstance(rows, list):
        problems.append("ledger files is not a list")
        rows = []
    seen = set()
    for row in rows:
        name = row.get("path") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name:
            problems.append("ledger row without a path")
            continue
        if name in seen:
            problems.append(f"ledger has more than one row for {name}")
        seen.add(name)
        if not HEX64.fullmatch(str(row.get("sha256", ""))):
            problems.append(f"ledger row for {name} has no 64 hex sha256")
        if not isinstance(row.get("review"), str) or not row["review"].strip():
            problems.append(f"ledger row for {name} has no review text")
    return (None if problems else ledger), problems


def write_ledger(root, ledger):
    path = Path(root) / LEDGER
    crlf = path.is_file() and b"\r\n" in path.read_bytes()
    ledger["files"] = sorted(ledger["files"], key=lambda row: row["path"])
    text = json.dumps(ledger, indent=2, ensure_ascii=True) + "\n"
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def row_of(ledger, path):
    return next((row for row in ledger["files"] if row["path"] == path), None)


def row_verdict(ledger, path, data):
    """None when the ledger has no row for the path; else whether the row is current."""
    row = row_of(ledger, path)
    if row is None:
        return None
    return row["sha256"] == digest(data)


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True)


def copy_at(root, rev, path):
    shown = git(root, "show", f"{rev}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def matches_base(root, ledger, path, data):
    original = copy_at(root, ledger["base"], path)
    if original is None:
        return False
    try:
        return provenance.normalized_source(original) == provenance.normalized_source(data)
    except UnicodeError:
        return original == data


def approved(root, ledger, path, data):
    """The whole rule: a row decides when there is one; otherwise the base copy does."""
    verdict = row_verdict(ledger, path, data)
    if verdict is not None:
        return verdict
    return matches_base(root, ledger, path, data)


def base_is_ancestor(root, base):
    return git(root, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0


def head_revision(root):
    return git(root, "rev-parse", "HEAD").stdout.decode().strip()


def tracked_files(root):
    listed = git(root, "ls-files", "-z").stdout.decode("utf-8", "replace")
    return [name for name in listed.split("\0") if name]


def is_tracked(root, path):
    return git(root, "ls-files", "--error-unmatch", "--", path).returncode == 0


def hint(path, data, root=ROOT):
    """What to run for a path the gate refuses."""
    ledger, problems = read_ledger(root)
    if ledger is None:
        return "the ledger is unusable: " + problems[0]
    row = row_of(ledger, path)
    command = f"release_evidence.py approve --path {path} --review TEXT"
    if row is None:
        return f"no ledger row; approve with {command}"
    return (f"ledger row is stale (recorded {row['sha256'][:8]}, the file is {digest(data)[:8]}); "
            f"run release_evidence.py sync if the file equals the base, otherwise {command}")


def static_scope(root):
    """The tracked paths the source gate reads without a build."""
    scope = []
    for name in tracked_files(root):
        native = name.startswith("native/") and not {"target", "licenses"} & set(name.split("/")[1:])
        if is_registry_source(name) or native or name in TARBALL_EXTRAS:
            scope.append(name)
    return scope


def inventory_paths(ui_inventory=None, frozen_provenance=None):
    """Paths named by a UI build inventory and by a frozen build's provenance."""
    paths = set()
    if ui_inventory:
        for row in json.loads(Path(ui_inventory).read_text(encoding="utf-8")).get("source_inputs", []):
            if row["path"].startswith("ui/") and row["path"] != "ui/src/credits.generated.json":
                paths.add(row["path"])
    if frozen_provenance:
        for row in json.loads(Path(frozen_provenance).read_text(encoding="utf-8")).get("files", []):
            source = row.get("source", "")
            if source.startswith("repo:") and not source[5:].startswith((".probe/", "ui/dist/")):
                paths.add(source[5:])
    return paths


def row_state(root, ledger, row):
    """current, droppable (gone, or equal to the base) or changed (a false claim)."""
    file = Path(root) / row["path"]
    if not file.is_file():
        return "droppable"
    data = file.read_bytes()
    try:
        if row["sha256"] == digest(data):
            return "current"
        return "droppable" if matches_base(root, ledger, row["path"], data) else "changed"
    except UnicodeError:
        return "changed"


def eol_hazards(root, paths):
    listed = git(root, "ls-files", "--eol", "-z").stdout.decode("utf-8", "replace")
    wanted = set(paths)
    found = []
    for entry in listed.split("\0"):
        info, _, name = entry.partition("\t")
        if name in wanted and info.split()[0] not in {"i/lf", "i/-text", "i/none"}:
            found.append(f"{name}: committed with line endings {info.split()[0]}, which a digest cannot tell from LF")
    return sorted(found)


def survey(root=ROOT, extra_paths=()):
    ledger, problems = read_ledger(root)
    result = {"base": None, "problems": problems, "unapproved": [], "stale_droppable": [],
              "stale_changed": [], "hazards": [], "notes": []}
    if ledger is None:
        return result
    result["base"] = ledger["base"]
    if not base_is_ancestor(root, ledger["base"]):
        result["problems"].append("ledger base is not an ancestor of HEAD")
    registry = Path(root) / REGISTRY
    if registry.is_file():
        baseline = json.loads(registry.read_text(encoding="utf-8")).get("reviewed_baseline")
        if baseline != ledger["base"]:
            result["notes"].append(f"{REGISTRY} reviewed_baseline ({str(baseline)[:12]}) differs from the ledger base; the gate reads the ledger")
    rows = {row["path"] for row in ledger["files"]}
    for row in ledger["files"]:
        state = row_state(root, ledger, row)
        if state == "droppable":
            result["stale_droppable"].append(row["path"])
        elif state == "changed":
            result["stale_changed"].append(row["path"])
    scope = sorted(set(static_scope(root)) | set(extra_paths))
    for path in scope:
        file = Path(root) / path
        if path not in rows and file.is_file() and not matches_base(root, ledger, path, file.read_bytes()):
            result["unapproved"].append(path)
    result["hazards"] = eol_hazards(root, scope + sorted(rows))
    if not extra_paths:
        result["notes"].append("UI build inputs and frozen build inputs were not checked; pass --ui-inventory and --frozen-provenance")
    return result


def work_remains(result):
    return bool(result["problems"] or result["unapproved"] or result["stale_droppable"] or result["stale_changed"])


def sync(root=ROOT):
    """Remove the rows that say nothing the base does not. Never writes a digest."""
    ledger, problems = read_ledger(root)
    if ledger is None:
        raise SystemExit("; ".join(problems))
    kept, dropped = [], []
    for row in ledger["files"]:
        if row_state(root, ledger, row) == "droppable":
            dropped.append(row["path"])
        else:
            kept.append(row)
    ledger["files"] = kept
    write_ledger(root, ledger)
    return dropped


def approve(root, paths, review):
    """Record that a person read the changed bytes of exactly these paths.

    Returns (path, old digest or None, new digest) for each path written.
    Every refusal happens before anything is written.
    """
    paths = sorted({p.replace("\\", "/") for p in paths})
    if not paths:
        raise SystemExit("approve needs at least one --path")
    if len(review.strip()) < MIN_REVIEW:
        raise SystemExit(f"--review must say what was read and found, at least {MIN_REVIEW} characters")
    ledger, problems = read_ledger(root)
    if ledger is None:
        raise SystemExit("; ".join(problems))
    plan = []
    for path in paths:
        if set(path) & set("*?["):
            raise SystemExit(f"{path}: name each file exactly; patterns are refused")
        if not is_tracked(root, path):
            raise SystemExit(f"{path}: not a file tracked by git")
        data = (Path(root) / path).read_bytes()
        try:
            new = digest(data)
        except UnicodeError:
            raise SystemExit(f"{path}: not UTF-8 text; a binary is not approved here")
        old = row_of(ledger, path)
        if old is not None and old["sha256"] == new:
            continue
        if matches_base(root, ledger, path, data):
            raise SystemExit(f"{path}: equals the reviewed base copy, there is nothing to approve; run sync")
        if old is not None and old["review"].strip() == review.strip():
            raise SystemExit(f"{path}: the bytes changed since the recorded review; write a new review sentence")
        plan.append((path, old, new))
    revision = head_revision(root)
    for path, old, new in plan:
        row = {"path": path, "sha256": new, "review": review.strip(), "reviewed_revision": revision}
        ledger["files"] = [r for r in ledger["files"] if r["path"] != path] + [row]
    if plan:
        write_ledger(root, ledger)
    return [(path, old["sha256"] if old else None, new) for path, old, new in plan]


def change_size(root, base, path):
    return git(root, "diff", "--shortstat", base, "--", path).stdout.decode().strip() or "no change"


def render(result):
    lines = [f"ledger base {str(result['base'])[:12]}"]
    for problem in result["problems"]:
        lines.append("PROBLEM " + problem)
    for path in result["unapproved"]:
        lines.append(f"UNAPPROVED {path}: changed since the base, no ledger row; release_evidence.py approve --path {path} --review TEXT")
    for path in result["stale_droppable"]:
        lines.append(f"STALE-DROPPABLE {path}: the row says nothing the base does not; release_evidence.py sync")
    for path in result["stale_changed"]:
        lines.append(f"STALE-CHANGED {path}: the file changed since the review; release_evidence.py approve --path {path} --review TEXT")
    for hazard in result["hazards"]:
        lines.append("HAZARD " + hazard)
    for note in result["notes"]:
        lines.append("NOTE " + note)
    lines.append("clean" if not work_remains(result) else "work remains")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="read only; exit 1 while work remains")
    status.add_argument("--json", action="store_true")
    status.add_argument("--ui-inventory")
    status.add_argument("--frozen-provenance")
    commands.add_parser("sync", help="remove rows that equal the base; never writes a digest")
    approving = commands.add_parser("approve", help="record that you read the changed bytes of these files")
    approving.add_argument("--path", action="append", default=[])
    approving.add_argument("--review", required=True)
    args = parser.parse_args(argv)
    if args.command == "status":
        result = survey(ROOT, inventory_paths(args.ui_inventory, args.frozen_provenance))
        print(json.dumps(result, indent=2) if args.json else render(result))
        return 1 if work_remains(result) else 0
    if args.command == "sync":
        dropped = sync(ROOT)
        print("\n".join(f"dropped {path}" for path in dropped) or "nothing to drop")
        return 0
    ledger, _ = read_ledger(ROOT)
    written = approve(ROOT, args.path, args.review)
    for path, old, new in written:
        print(f"{path}: {(old or 'no row')[:8]} -> {new[:8]}; {change_size(ROOT, ledger['base'], path)}")
    if not written:
        print("nothing to approve: every named file is already approved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
