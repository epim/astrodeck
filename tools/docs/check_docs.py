# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Validate the operator documentation without fetching URLs or running examples.

Checks the scoped Markdown, local links/anchors, UI-label provenance, source
claim references, style (including a ban on money metaphors in prose) and
the repository's external privacy needles. A quoted UI label is matched by
its text anywhere in the cited source file; the recorded line is a hint for
resolving more than one hit and for diagnostics, never the label's identity,
so an unrelated edit above the label does not fail this check (#660).
Diagnostics name files and error categories, never document text or link values.
"""
from __future__ import annotations
import argparse
import bisect
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import runpy
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
EXTRA_PAGES = ["README.md", "docs/quickstart.md", "docs/overview.md",
               "docs/auth-setup.md", "docs/relay-deploy.md"]
REQUIRED_GUIDES = {
    "README", "getting-started", "install-binary", "install-docker",
    "equipment-and-profiles", "site-and-locations", "sky-atlas",
    "plan-and-sequences", "capture", "focus", "guiding", "monitor",
    "weather", "safety-and-automation", "sessions-multi-night",
    "remote-access-and-roles", "troubleshooting", "flows-and-mosaics",
    "unattended-nights", "orange-pi-appliance", "windows-rig", "next-ui",
}
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
PUFFERY = re.compile(r"\b(seamless|robust|leverage|unlock|delve|foster)\b", re.I)
# Money metaphors for things that do not involve money (earned hours, a
# "budget" of exposure time, frames a target "owes"). A quoted, ledgered UI
# label is exempt: see `money_label_spans`. "pay"/"paid" alone are common
# words for an actual purchase (a paid API account) and are not flagged;
# only the "pay off" idiom is a metaphor.
MONEY = re.compile(
    r"\b(earn(?:ed|s|ing)?|bank(?:ed|s|ing)?|worth|budget(?:s|ed|ing)?|"
    r"spend(?:s|ing)?|spent|cost(?:s|ing|ly)?|invest(?:s|ed|ing|ment)?|"
    r"fund(?:s|ed|ing)?|owe[sd]?|pay(?:s|ing)?\s+off|paid\s+off)\b",
    re.I,
)
BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|[^\s]+)", re.M)


def documents(repo):
    return sorted((repo / "docs/guide").glob("*.md")) + [repo / p for p in EXTRA_PAGES]


def prose(text):
    """Keep line positions while hiding fenced code from Markdown structure."""
    out, fence = [], None
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(\x60{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            out.append("")
        else:
            out.append("" if fence else line)
    return "\n".join(out)


def anchors(text):
    result, seen = set(), {}
    for line in prose(text).splitlines():
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", line)
        if not match:
            continue
        title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", match.group(1))
        title = re.sub(r"<[^>]*>", "", title)
        slug = re.sub(r"[^\w\- ]", "", html.unescape(title).lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if not count else f"{slug}-{count}")
    parser = HTMLRefs()
    parser.feed(prose(text))
    result.update(parser.ids)
    return result


class HTMLRefs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and (key == "id" or (tag == "a" and key == "name")):
                self.ids.add(value)
            if key in {"href", "src"} and value:
                self.refs.append(value)


def references(text):
    body = prose(text)
    refs = [m.group(1).strip("<>") for pattern in (LINK, REFERENCE) for m in pattern.finditer(body)]
    parser = HTMLRefs()
    parser.feed(body)
    return refs + parser.refs


def local_ref(repo, page, ref):
    parsed = urlsplit(html.unescape(ref))
    if parsed.scheme or parsed.netloc:
        return None, None
    raw = unquote(parsed.path)
    target = (repo / raw.lstrip("/") if raw.startswith("/") else page.parent / raw) if raw else page
    target = target.resolve()
    if not target.is_relative_to(repo.resolve()):
        raise ValueError("link escapes repository")
    return target, unquote(parsed.fragment)


def load_rows(path, key, privacy_scan=None):
    text = path.read_text(encoding="utf-8")
    if privacy_scan and any(privacy_scan(value) for value in (text, html.unescape(text), unquote(html.unescape(text)))):
        raise ValueError("ledger contains a forbidden observing-site value")
    value = json.loads(text)
    decoded = json.dumps(value, ensure_ascii=False)
    if privacy_scan and privacy_scan(html.unescape(decoded)):
        raise ValueError("ledger contains a forbidden observing-site value")
    rows = value if isinstance(value, list) else value.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("ledger must contain a list")
    return rows


def money_label_spans(text, page, labels_by_page):
    """Character spans of **label** text registered for `page` in the ledger.

    A money word inside one of these spans is a UI label quoted verbatim
    from the app, not a metaphor the docs introduced, so it is exempt.
    """
    allowed = labels_by_page.get(page)
    if not allowed:
        return []
    return [match.span() for match in BOLD.finditer(text) if match.group(1) in allowed]


def locate_label(lines, label):
    """Map each exact occurrence of `label`'s text to its 1-based start line.

    The file is read as one normalized stream, the same join already used
    to confirm a bold label's presence in the Markdown, so a label split
    across a line break (JSX can do this) is still found as one occurrence.
    This locates the label by its content, not a recorded coordinate, so an
    unrelated edit elsewhere in the file does not break the citation (#660).
    """
    unescaped = [html.unescape(entry) for entry in lines]
    starts, offset = [], 0
    for entry in unescaped:
        starts.append(offset)
        offset += len(entry) + 1  # +1 for the separating space below.
    joined = " ".join(unescaped)
    occurrences, search_from = [], 0
    while True:
        index = joined.find(label, search_from)
        if index == -1:
            return occurrences
        occurrences.append(bisect.bisect_right(starts, index))
        search_from = index + max(len(label), 1)


def resolve_label(lines, label, hint):
    """Decide whether `label` is still quoted in `lines`, using `hint` only
    to pick among more than one occurrence, never as the match's identity.

    An edit above the label shifts it to a later line, which is what #660
    is about, so an occurrence at or after the hint is preferred outright;
    there is always at most one closest such line, so this side never
    ties. Only when every occurrence is before the hint, and there is more
    than one, is the citation genuinely ambiguous: the ledger's line number
    cannot say which one is meant.
    """
    occurrences = locate_label(lines, label)
    forward = [found for found in occurrences if found >= hint]
    if forward:
        return "found"
    behind = [found for found in occurrences if found < hint]
    if len(behind) == 1:
        return "found"
    if behind:
        return "ambiguous"
    return "missing"


def check(repo, pages, labels=(), claims=(), privacy_scan=None, enforce_coverage=True):
    repo = repo.resolve()
    errors, bodies = [], {}
    labels_by_page = {}
    for row in labels:
        page, label = row.get("page", ""), row.get("label", "")
        if page and label:
            labels_by_page.setdefault(page, set()).add(label)
    for page in pages:
        relative = page.relative_to(repo).as_posix()
        if not page.is_file():
            errors.append(f"{relative}: missing documentation page")
            continue
        data = page.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            errors.append(f"{relative}: UTF-8 BOM is not permitted")
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            errors.append(f"{relative}: invalid UTF-8")
            continue
        bodies[relative] = text
        decoded = html.unescape(text)
        if privacy_scan and (privacy_scan(text) or privacy_scan(decoded) or privacy_scan(unquote(decoded))):
            errors.append(f"{relative}: forbidden observing-site value")
            # No label/link/source diagnostics are allowed to echo private prose.
            continue
        if "\u2013" in decoded or "\u2014" in decoded:
            errors.append(f"{relative}: em/en dash is not permitted")
        if EMOJI.search(decoded):
            errors.append(f"{relative}: emoji is not permitted")
        if PUFFERY.search(decoded):
            errors.append(f"{relative}: house-style filler word")
        exempt = money_label_spans(decoded, relative, labels_by_page)
        if any(not any(start <= match.start() < end for start, end in exempt) for match in MONEY.finditer(decoded)):
            errors.append(f"{relative}: money metaphor is not permitted")
        for ref in references(text):
            try:
                target, fragment = local_ref(repo, page, ref)
            except ValueError:
                errors.append(f"{relative}: local link escapes repository")
                continue
            if target is None:
                continue
            if not target.exists():
                errors.append(f"{relative}: missing local link target")
                continue
            if fragment and target.is_file() and target.suffix.lower() in {".md", ".html"}:
                if fragment not in anchors(target.read_text(encoding="utf-8")):
                    errors.append(f"{relative}: missing local link anchor")

    label_pairs = set()
    for row in labels:
        page, label, source = row.get("page", ""), row.get("label", ""), row.get("source", "")
        if page not in bodies or not label:
            errors.append("UI ledger: missing page or label")
            continue
        label_pairs.add((page, label))
        normalized_doc = " ".join(bodies[page].split())
        if "**" + label + "**" not in normalized_doc:
            errors.append(f"{page}: registered UI label is absent from bold text")
        path = (repo / source).resolve()
        if not source.startswith("ui/src/") or not path.is_relative_to(repo / "ui/src") or not path.is_file():
            errors.append(f"{page}: UI label lacks a UI source file")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        line = row.get("line")
        if not isinstance(line, int) or not 1 <= line <= len(lines):
            errors.append(f"{page}: UI label has an invalid source line")
            continue
        # Locate the label by its content anywhere in the file (#660). The
        # recorded line is a hint used only to resolve more than one hit,
        # never the label's identity, so edits above it do not make this
        # citation stale.
        status = resolve_label(lines, label, line)
        if status == "missing":
            errors.append(f"{page}: quoted UI label is absent at its source")
        elif status == "ambiguous":
            errors.append(f"{page}: quoted UI label is ambiguous at its source")
    if enforce_coverage:
        for page, text in bodies.items():
            # README keeps its owner's pitch emphasis. Task pages use bold only
            # for labels, while bold Markdown links remain navigation.
            if page == "README.md":
                continue
            for match in re.finditer(r"\*\*([^*\n]+)\*\*", prose(text)):
                value = match.group(1)
                if value.startswith("[") or value in {"Note", "Warning"}:
                    continue
                if (page, value) not in label_pairs:
                    errors.append(f"{page}: bold UI label has no provenance record")

    claim_pages = set()
    for row in claims:
        page = row.get("page", "")
        if page not in bodies or not row.get("claim"):
            errors.append("Claim ledger: missing page or claim")
            continue
        claim_pages.add(page)
        sources = row.get("sources") or [{"path": row.get("source", ""), "line": row.get("line")}]
        if not sources:
            errors.append(f"{page}: claim has no source evidence")
        for source in sources:
            path = (repo / source.get("path", "")).resolve()
            line = source.get("line")
            if not path.is_relative_to(repo) or not path.is_file():
                errors.append(f"{page}: claim source file is missing")
            elif not isinstance(line, int) or not 1 <= line <= len(path.read_text(encoding="utf-8").splitlines()):
                errors.append(f"{page}: claim source line is invalid")
        if row.get("verified_by") not in {"source-trace", "source trace", "simulator-run", "simulator run"}:
            errors.append(f"{page}: claim verification method is missing")
    if enforce_coverage:
        for page in set(bodies) - claim_pages:
            errors.append(f"{page}: no claim/procedure evidence ledger")
    return sorted(set(errors))


def check_stable_anchors(repo, baseline):
    errors = []
    for relative, expected in baseline.items():
        path = repo / relative
        if not path.is_file():
            errors.append(f"{relative}: stable guide URL is missing")
        elif not set(expected).issubset(anchors(path.read_text(encoding="utf-8"))):
            errors.append(f"{relative}: stable guide anchor is missing")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-privacy", action="store_true")
    args = parser.parse_args()
    scanner = runpy.run_path(str(ROOT / "tools/privacy_scan.py"))
    needles = scanner["load_needles"]()
    if args.require_privacy and not needles:
        print("Docs checks: missing external privacy configuration")
        return 2
    patterns = scanner["patterns_for"](needles) if needles else []
    private = lambda text: bool(scanner["scan_text"](text, patterns))
    labels, claims, errors = [], [], []
    for path in sorted((ROOT / "tools/docs").glob("*-ui-labels.json")):
        try:
            labels += load_rows(path, "labels", private)
        except (ValueError, UnicodeError):
            errors.append("UI ledger: invalid JSON/schema or forbidden observing-site value")
    for path in sorted((ROOT / "tools/docs").glob("*-claims.json")):
        try:
            claims += load_rows(path, "claims", private)
        except (ValueError, UnicodeError):
            errors.append("Claim ledger: invalid JSON/schema or forbidden observing-site value")
    for slug in REQUIRED_GUIDES:
        if not (ROOT / "docs/guide" / (slug + ".md")).is_file():
            errors.append("docs/guide: required stable page is missing")
    pages = documents(ROOT)
    errors += check(ROOT, pages, labels, claims, private)
    baseline = json.loads((ROOT / "tools/docs/stable-anchors.json").read_text(encoding="utf-8"))
    errors += check_stable_anchors(ROOT, baseline["anchors"])
    for error in sorted(set(errors)):
        print(error)
    print(f"Docs checks: {'FAIL' if errors else 'PASS'} ({len(pages)} pages, {len(claims)} claims, {len(labels)} UI labels)")
    if not needles:
        print("Privacy needles unavailable; local text checks ran, privacy scan skipped.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
