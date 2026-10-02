# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every first-party source file carries the project's copyright header, and
the root NOTICE file names the copyright holder.

Scope mirrors the policy a repo-wide header pass was run against: the first
-party roots (server/astrodeck, server/tests, ui/src, the ui-root .mjs/.ts
entry files, native/crates/*/src+tests, relay/relay, relay/tests, tools/,
scripts/, orangepi5/, site/assets/*.js+*.css) over the header file types
(.py .ts .tsx .js .mjs .cjs .rs .ps1 .sh .css), plus an HTML comment in
site/*.html and ui/index.html. Vendored code, build output, generated files,
third-party licence text and a short named list of files owned by a
concurrent job are excluded -- see EXCLUDE_* below, which must match the
header pass exactly or this guard reports false positives.

native/crates has a genuine two-license split, documented in
docs/native-parity/rust-header-policy.md and in each crate's Cargo.toml:
astro-guide is Apache-2.0, while astro-focus/astro-star/astro-tppa/
astrodeck-native are MPL-2.0 (the workspace default). A file in the MPL
group keeps its existing MPL notice rather than an invented Apache-2.0 SPDX
line -- asserting otherwise would misstate that file's actual licence. This
guard checks for the copyright line everywhere, and for the correct license
declaration per group.

NAMED MUTANT "header stripped from tools/privacy_scan.py": a byte backup was
taken, the two header lines (after the shebang) were deleted from the top of
that file, and this guard was run on its own:
`pytest server/tests/test_copyright_headers.py::test_every_first_party_source_file_has_a_copyright_header -q`.
The file was then restored from the backup and verified byte-identical
(sha256 afe53a2257623815d4a71ff1628bb1e18950c98038a17d5200a5b473799c6143
before, during the mutant, and after the restore -- the before and after
hashes matched). tools/privacy_scan.py rather than a server/astrodeck file:
this suite's own `_TheTreeMustNotMove` guard (conftest.py, issue #118) fails
the WHOLE run if any file under server/astrodeck changes while pytest is
running, which a live in-process mutation of this guard's own target would
trip. Observed failure:

    AssertionError: files missing a copyright/license header:
      tools/privacy_scan.py: missing 'Copyright (c) 2026 James Penick'
    assert not ["tools/privacy_scan.py: missing 'Copyright (c) 2026 James Penick'"]
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COPY = "Copyright (c) 2026 James Penick"
SPDX_APACHE = "SPDX-License-Identifier: Apache-2.0"
MPL_NOTICE = "This Source Code Form is subject to the terms of the Mozilla Public"

HASH_STYLE = {".py", ".ps1", ".sh"}
SLASH_STYLE = {".ts", ".tsx", ".js", ".mjs", ".cjs", ".rs"}
CSS_STYLE = {".css"}
CODE_EXTS = HASH_STYLE | SLASH_STYLE | CSS_STYLE

# Keep in lockstep with the header pass; a drift here makes this guard either
# blind (over-excludes) or wrong (flags an intentionally bare file).
EXCLUDE_DIR_PREFIXES = (
    "server/astrodeck/vendor/",
    "node_modules/",
    "dist/",
    "build/",
    "target/",
    "tools/licence_texts/",
    "tools/licence/",
    "packaging/",
    "deploy/reverse-proxy/",
    "server/tests/fixtures/",
)
EXCLUDE_EXACT_FILES = {
    "scripts/build_release.py",
    ".github/workflows/release.yml",
    "server/tests/test_build_binary_smoke.py",
    "server/tests/test_licence_artifact.py",
}

FIRST_PARTY_ROOTS = (
    "server/astrodeck/",
    "server/tests/",
    "ui/src/",
    "native/crates/",
    "relay/relay/",
    "relay/tests/",
    "tools/",
    "scripts/",
    "orangepi5/",
    "site/assets/",
)

# native/crates/astro-focus, astro-star, astro-tppa, astrodeck-native: MPL-2.0
# via the workspace Cargo.toml default (docs/native-parity/rust-header-policy.md).
MPL_ROOTS = (
    "native/crates/astro-focus/",
    "native/crates/astro-star/",
    "native/crates/astro-tppa/",
    "native/crates/astrodeck-native/",
)

HTML_PAGES = {
    "ui/index.html",
}


def _git_ls_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def _is_excluded(rel: str) -> bool:
    if any(rel.startswith(p) for p in EXCLUDE_DIR_PREFIXES):
        return True
    if rel in EXCLUDE_EXACT_FILES:
        return True
    if "__pycache__" in rel.split("/"):
        return True
    name = Path(rel).name
    if ".generated." in name:
        return True
    if name.startswith("LICENSE"):
        return True
    return False


def _in_first_party_root(rel: str) -> bool:
    if rel.startswith(FIRST_PARTY_ROOTS):
        return True
    if rel.startswith("ui/") and "/" not in rel[len("ui/"):] and (
            rel.endswith(".mjs") or rel.endswith(".ts")):
        return True
    return False


def _native_scope_ok(rel: str) -> bool:
    parts = rel.split("/")
    if parts[0:2] != ["native", "crates"]:
        return True
    return len(parts) >= 4 and parts[3] in ("src", "tests")


def _site_assets_scope_ok(rel: str) -> bool:
    if not rel.startswith("site/assets/"):
        return True
    return rel.endswith(".js") or rel.endswith(".css")


def eligible_code_files() -> list[str]:
    out = []
    for rel in _git_ls_files():
        if _is_excluded(rel):
            continue
        if Path(rel).suffix not in CODE_EXTS:
            continue
        if not _in_first_party_root(rel):
            continue
        if not _native_scope_ok(rel):
            continue
        if not _site_assets_scope_ok(rel):
            continue
        out.append(rel)
    return out


def eligible_html_pages() -> list[str]:
    out = []
    for rel in _git_ls_files():
        if rel in HTML_PAGES:
            out.append(rel)
        elif rel.startswith("site/") and rel.endswith(".html") and "/assets/" not in rel:
            out.append(rel)
    return out


def _head(path: Path, n: int = 1200) -> str:
    return path.read_text(encoding="utf-8", errors="strict")[:n]


def test_every_first_party_source_file_has_a_copyright_header():
    violations = []
    for rel in eligible_code_files():
        path = REPO / rel
        head = _head(path)
        if COPY not in head:
            violations.append(f"{rel}: missing '{COPY}'")
            continue
        if rel.startswith(MPL_ROOTS):
            if MPL_NOTICE not in head:
                violations.append(f"{rel}: missing the MPL-2.0 licence notice")
        else:
            if SPDX_APACHE not in head:
                violations.append(f"{rel}: missing '{SPDX_APACHE}'")
    assert not violations, "files missing a copyright/license header:\n" + "\n".join(violations)


def test_every_site_and_ui_root_html_page_has_a_copyright_comment():
    violations = []
    for rel in eligible_html_pages():
        head = _head(REPO / rel)
        if COPY not in head:
            violations.append(f"{rel}: missing the Copyright HTML comment")
        if SPDX_APACHE not in head:
            violations.append(f"{rel}: missing the SPDX-License-Identifier HTML comment")
    assert not violations, "HTML pages missing a copyright header:\n" + "\n".join(violations)


def test_notice_file_names_the_copyright_holder():
    notice = REPO / "NOTICE"
    assert notice.is_file(), "root NOTICE file is missing"
    text = notice.read_text(encoding="utf-8")
    assert "James Penick" in text, "NOTICE does not name the copyright holder"
    assert "Apache License" in text, "NOTICE does not point to the Apache licence"
