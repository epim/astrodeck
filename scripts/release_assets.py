# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Checksum the accepted release payloads and write the release's withheld-binaries note.

The release job publishes the signed source tarball on its own when a platform's
binaries did not pass their licence gates (#1073). An `accepted-<platform>`
artifact is uploaded only after that platform's gates pass, so whatever
executables and wheels are in the download folder are the cleared ones. This
script refuses a platform that has only one of its two payloads, writes a
.sha256 next to every cleared payload, and names the platforms that are missing
in a note for the release body.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

# platform -> executable name
EXECUTABLES = {
    "linux-x86_64": "astrodeck-linux-x86_64",
    "linux-arm64": "astrodeck-linux-arm64",
    "windows-x86_64": "astrodeck-windows-x86_64.exe",
    "macos-arm64": "astrodeck-macos-arm64",
}


def wheel_platform(name):
    """The release platform a wheel file name belongs to, or None."""
    tag = name.lower().removesuffix(".whl")
    if "win_amd64" in tag:
        return "windows-x86_64"
    if "macosx" in tag and "arm64" in tag:
        return "macos-arm64"
    if "linux" in tag and ("x86_64" in tag or "amd64" in tag):
        return "linux-x86_64"
    if "linux" in tag and ("aarch64" in tag or "arm64" in tag):
        return "linux-arm64"
    return None


def survey(dist):
    """{platform: {"exe": [names], "wheel": [names]}} for what is in `dist`."""
    found = {platform: {"exe": [], "wheel": []} for platform in EXECUTABLES}
    by_exe = {name: platform for platform, name in EXECUTABLES.items()}
    for path in sorted(Path(dist).iterdir()):
        if not path.is_file():
            continue
        if path.name in by_exe:
            found[by_exe[path.name]]["exe"].append(path.name)
        elif path.suffix == ".whl":
            platform = wheel_platform(path.name)
            if platform is None:
                raise ValueError("wheel for no known release platform: " + path.name)
            found[platform]["wheel"].append(path.name)
    return found


def plan(dist):
    """(present, withheld, payload names). Raises when a platform is half present."""
    found = survey(dist)
    present, withheld, names = [], [], []
    for platform, payloads in found.items():
        if not payloads["exe"] and not payloads["wheel"]:
            withheld.append(platform)
        elif len(payloads["exe"]) != 1 or len(payloads["wheel"]) != 1:
            raise ValueError(platform + " must have exactly one executable and one wheel, or neither: " + repr(payloads))
        else:
            present.append(platform)
            names += payloads["exe"] + payloads["wheel"]
    return present, withheld, names


def withheld_note(withheld):
    if not withheld:
        return ""
    return ("Binaries withheld for: " + ", ".join(withheld) + ". Their licence gates have not passed (#1073), "
            "so no executable or native wheel for those platforms is attached. The signed source release is "
            "complete and is what self-update installs.\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--notes", required=True, type=Path)
    args = parser.parse_args(argv)
    present, withheld, names = plan(args.dist)
    for name in names:
        path = args.dist / name
        path.with_name(name + ".sha256").write_text(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  " + name + "\n", encoding="utf-8")
    args.notes.write_text(withheld_note(withheld), encoding="utf-8")
    print("cleared platforms: " + (", ".join(present) or "none") + "; withheld: " + (", ".join(withheld) or "none"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print("release assets: " + str(exc), file=sys.stderr)
        sys.exit(1)
