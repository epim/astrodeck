# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Check host COPY inputs for every checked-in edge Compose build and relay Fly.

This checks context paths, stage references and local COPY existence/ignores.
It does not build an image, contact a Docker daemon or certify remote base images.
Unsupported COPY syntax is rejected instead of silently skipped.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class Build:
    name: str
    context: Path
    dockerfile: Path

def within(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("build path escapes repository/context")
    return resolved

def build_paths(root):
    builds = []
    for name in ("docker-compose.yml", "docker-compose.relay.yml"):
        config = root / "deploy/reverse-proxy" / name
        document = yaml.safe_load(config.read_text(encoding="utf-8"))
        for service, value in document["services"].items():
            build = value.get("build")
            if build is None:
                continue
            if isinstance(build, str):
                build = {"context": build}
            if not isinstance(build, dict) or build.get("dockerfile_inline"):
                raise ValueError("unsupported Compose build definition")
            raw = build.get("context", ".")
            if not isinstance(raw, str) or "$" in raw or "://" in raw:
                raise ValueError("build context must be a local literal")
            context = within(config.parent / raw, root)
            dockerfile = within(context / build.get("dockerfile", "Dockerfile"), context)
            builds.append(Build(f"{name}:{service}", context, dockerfile))
    fly = root / "relay/fly.toml"
    configuration = tomllib.loads(fly.read_text(encoding="utf-8"))
    build = configuration.get("build", {})
    if set(build) - {"dockerfile", "args"}:
        raise ValueError("unsupported Fly build configuration")
    builds.append(Build("relay/fly.toml", fly.parent, within(fly.parent / build.get("dockerfile", "Dockerfile"), fly.parent)))
    return builds

def path_pattern(pattern, recursive=False):
    """Segment-aware subset of Go filepath.Match and Dockerignore **."""
    if any(c in pattern for c in "\\[]") or any(p in {".", "..", ""} for p in pattern.split("/")):
        raise ValueError("unsupported path pattern")
    parts, expression = pattern.split("/"), ""
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if "**" in part:
            if not recursive or part != "**":
                raise ValueError("unsupported recursive path pattern")
            expression += ".*" if last else "(?:[^/]+/)*"
        else:
            expression += "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in part)
            if not last:
                expression += "/"
    return re.compile(expression)


def ignored(relative, patterns):
    """Match this repository's literal, glob and descendant ignore rules."""
    parts = PurePosixPath(relative).parts
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    result = False
    for raw in patterns:
        rule = raw.strip()
        if not rule or rule.startswith("#") or rule == ".":
            continue
        negate = rule.startswith("!")
        rule = rule[1:] if negate else rule
        rule = rule.strip("/")
        matcher = path_pattern(rule, recursive=True)
        if any(matcher.fullmatch(candidate) for candidate in candidates):
            result = not negate
    return result

def instructions(text):
    pending = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending += stripped[:-1] + " " if stripped.endswith("\\") else stripped
        if stripped.endswith("\\"):
            continue
        yield pending
        pending = ""
    if pending:
        raise ValueError("unterminated Dockerfile continuation")

def check_build(build):
    errors, copies, stages = [], [], []
    if not build.context.is_dir() or not build.dockerfile.is_file():
        return ["missing context or Dockerfile"], copies
    specific = build.dockerfile.with_name(build.dockerfile.name + ".dockerignore")
    ignore = specific if specific.is_file() else build.context / ".dockerignore"
    patterns = ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []
    try:
        for instruction in instructions(build.dockerfile.read_text(encoding="utf-8")):
            parts = instruction.split(None, 1)
            operation, value = parts[0], parts[1] if len(parts) == 2 else ""
            if operation.upper() == "FROM":
                match = re.search(r"\sAS\s+(\S+)\s*$", value, re.I)
                stages.append(match.group(1) if match else str(len(stages)))
                continue
            if operation.upper() == "ADD":
                errors.append("ADD needs explicit review; use checked COPY inputs")
                continue
            if operation.upper() != "COPY":
                continue
            source_stage = None
            while value.startswith("--"):
                flag, _, value = value.partition(" ")
                value = value.lstrip()
                if flag.startswith("--from="):
                    source_stage = flag.split("=", 1)[1]
                elif not (flag.startswith(("--chown=", "--chmod=")) or flag == "--link"):
                    raise ValueError("unsupported COPY flag")
            args = json.loads(value) if value.startswith("[") else shlex.split(value)
            if not isinstance(args, list) or len(args) < 2 or not all(isinstance(v, str) for v in args):
                raise ValueError("invalid COPY operands")
            if source_stage is not None:
                previous = stages[:-1]
                if source_stage not in previous and not (source_stage.isdecimal() and int(source_stage) < len(previous)):
                    errors.append("COPY references an unknown or current stage")
                continue
            for source in args[:-1]:
                if "$" in source or source.startswith(("/", "\\")) or ".." in PurePosixPath(source).parts or "://" in source:
                    raise ValueError("COPY source must stay inside local context")
                # COPY follows filepath.Match, not pathlib's recursive **.
                # Reject unsupported syntax and filter case-insensitive host globbing.
                normalized = PurePosixPath(source).as_posix()
                if normalized == ".":
                    matches = [build.context]
                else:
                    matcher = path_pattern(normalized)
                    matches = [p for p in build.context.glob(source)
                               if matcher.fullmatch(p.relative_to(build.context).as_posix())]
                if not matches:
                    errors.append("COPY source is missing: " + source)
                    continue
                present = []
                for path in matches:
                    within(path, build.context)
                    relative = path.relative_to(build.context).as_posix()
                    if not ignored(relative, patterns):
                        present.append(relative)
                if not present:
                    errors.append("COPY source is excluded by dockerignore: " + source)
                copies.extend(present)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        errors.append(str(error))
    return errors, sorted(set(copies))

def check(root):
    reports = []
    for build in build_paths(root):
        errors, copies = check_build(build)
        reports.append({"build": build.name, "context": build.context.relative_to(root).as_posix(),
                        "dockerfile": build.dockerfile.relative_to(root).as_posix(),
                        "copy_sources": copies, "findings": errors})
    return reports

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    reports = check(ROOT)
    result = {"scope": "Local host COPY inputs and stage references; no image build or deployment", "builds": reports}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for row in reports:
        print(row["build"] + ": " + ("FAIL: " + "; ".join(row["findings"]) if row["findings"] else "PASS"))
    return int(any(row["findings"] for row in reports))

if __name__ == "__main__":
    raise SystemExit(main())
