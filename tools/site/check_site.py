# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Check static pages, local references, text style, and optional privacy.

HTML5 parsing is supplied by html5lib; this script adds project conventions.
It never fetches external URLs and never prints private values or page text.
"""
from __future__ import annotations

import argparse
import html
import xml.etree.ElementTree as ET
from pathlib import Path
import re
import runpy
import sys
from urllib.parse import unquote, urlsplit

import html5lib
import tinycss2

REPO = Path(__file__).resolve().parents[2]
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
PUFFERY = re.compile(r"\b(seamless|robust|leverage|unlock|delve)\b", re.I)
TEXT = {".html", ".css", ".js", ".svg", ".json", ".txt", ".md", ".xml", ".py", ".yml"}


def local_target(site: Path, source: Path, ref: str):
    url = urlsplit(ref)
    if url.scheme or url.netloc:
        return None
    # Project Pages are mounted under /astrodeck/, so root-relative links break.
    if url.path.startswith("/"):
        raise ValueError("root-relative reference is not portable to project Pages")
    target = (source.parent / unquote(url.path)).resolve() if url.path else source
    if not target.is_relative_to(site.resolve()):
        raise ValueError("reference leaves the published site")
    if target.is_dir():
        target = target / "index.html"
    return target, unquote(url.fragment)


def css_references(source: str):
    """Read CSS tokens, including escapes, comments, and nested functions."""
    errors, refs = [], []

    def walk(tokens):
        importing = False
        for token in tokens:
            kind = token.type
            if kind == "error":
                errors.append("invalid CSS token")
            elif kind == "at-keyword":
                importing = token.value.lower() == "import"
            elif kind in {"whitespace", "comment"}:
                continue
            elif kind == "url":
                refs.append(token.value)
                importing = False
            elif kind == "string" and importing:
                refs.append(token.value)
                importing = False
            elif kind == "function":
                if token.lower_name == "url":
                    values = [t.value for t in token.arguments if t.type == "string"]
                    refs.extend(values)
                    if not values:
                        errors.append("invalid CSS URL")
                elif token.lower_name in {"image-set", "-webkit-image-set"}:
                    refs.extend(t.value for t in token.arguments if t.type == "string")
                walk(token.arguments)
                importing = False
            else:
                if hasattr(token, "content"):
                    walk(token.content)
                if kind not in {"comment", "whitespace"}:
                    importing = False
    walk(tinycss2.parse_component_value_list(source))
    return refs, errors


def document_references(doc):
    refs, css = [], []
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.rsplit("}", 1)[-1]
        for key, value in el.attrib.items():
            key = key.rsplit("}", 1)[-1]
            if key in {"href", "src", "poster"}:
                metadata = tag == "link" and el.attrib.get("rel") == "canonical"
                refs.append((value, tag == "a" or metadata))
            elif key == "srcset":
                refs.extend((part.strip().split()[0], False) for part in value.split(",") if part.strip())
            elif key == "style":
                css.append(value)
        if tag == "style":
            css.append("".join(el.itertext()))
    return refs, css


def check(site: Path) -> list[str]:
    errors = []
    pages = sorted(site.rglob("*.html"))
    documents = {}
    ids_by_page = {}
    if not pages:
        return ["site: no HTML pages"]
    for page in pages:
        rel = page.relative_to(site).as_posix()
        raw = page.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            errors.append(f"{rel}: UTF-8 BOM")
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{rel}: invalid UTF-8")
            continue
        parser = html5lib.HTMLParser(namespaceHTMLElements=False)
        doc = parser.parse(source)
        documents[page.resolve()] = doc
        for (line, column), code, _ in parser.errors:
            errors.append(f"{rel}:{line}:{column}: HTML5 {code}")
        ids = [el.attrib["id"] for el in doc.iter() if "id" in el.attrib]
        ids_by_page[page.resolve()] = set(ids)
        if len(ids) != len(set(ids)):
            errors.append(f"{rel}: duplicate id")
        if not doc.attrib.get("lang"):
            errors.append(f"{rel}: html needs lang")
        if len(doc.findall(".//h1")) != 1 or len(doc.findall(".//main")) != 1:
            errors.append(f"{rel}: expected one h1 and one main")
        if not (doc.findtext(".//title") or "").strip():
            errors.append(f"{rel}: missing title")
        if not any(el.attrib.get("name") == "viewport" for el in doc.iter("meta")):
            errors.append(f"{rel}: missing viewport")
        if not any(el.attrib.get("rel") == "stylesheet" for el in doc.iter("link")):
            errors.append(f"{rel}: missing stylesheet")
        visible = " ".join(doc.itertext()) + " " + " ".join(
            value for el in doc.iter() for key, value in el.attrib.items()
            if key in {"alt", "title", "aria-label"} or (el.tag == "meta" and key == "content")
        )
        if "\u2013" in visible or "\u2014" in visible:
            errors.append(f"{rel}: em/en dash in site text")
        if EMOJI.search(visible):
            errors.append(f"{rel}: emoji in site text")
        if PUFFERY.search(visible):
            errors.append(f"{rel}: prohibited promotional vocabulary")
        for el in doc.iter():
            if el.tag == "img" and "alt" not in el.attrib:
                errors.append(f"{rel}: image needs alt text")
            if el.tag in {"button", "a"}:
                name = el.attrib.get("aria-label") or "".join(el.itertext()).strip()
                if not name and not any(x.attrib.get("alt") for x in el.iter("img")):
                    errors.append(f"{rel}: unnamed interactive element")
            if el.tag in {"a", "button"} and any(
                child is not el and child.tag in {"a", "button", "input", "select", "textarea"}
                for child in el.iter()
            ):
                errors.append(f"{rel}: nested interactive element")
    for svg in site.rglob("*.svg"):
        try:
            doc = ET.fromstring(svg.read_bytes())
        except ET.ParseError:
            errors.append(f"{svg.name}: invalid SVG XML")
            continue
        documents[svg.resolve()] = doc
        ids_by_page[svg.resolve()] = {el.attrib["id"] for el in doc.iter() if "id" in el.attrib}

    resources = []
    for page, doc in documents.items():
        refs, css_sources = document_references(doc)
        resources.extend((page, ref, external_ok) for ref, external_ok in refs)
        for css_source in css_sources:
            refs, problems = css_references(css_source)
            errors.extend(f"{page.name}: {problem}" for problem in problems)
            resources.extend((page, ref, False) for ref in refs)
    for css in site.rglob("*.css"):
        refs, problems = css_references(css.read_text(encoding="utf-8"))
        errors.extend(f"{css.name}: {problem}" for problem in problems)
        resources.extend((css.resolve(), ref, False) for ref in refs)

    for page, ref, external_ok in resources:
        rel = page.relative_to(site.resolve()).as_posix()
        try:
            url = urlsplit(ref)
            if url.scheme or url.netloc:
                if not external_ok:
                    errors.append(f"{rel}: external resource is forbidden")
                elif url.scheme not in {"https", "mailto"}:
                    errors.append(f"{rel}: unsafe external link scheme")
                continue
            target, fragment = local_target(site, page, ref)
        except ValueError:
            errors.append(f"{rel}: invalid, root-relative, or escaping reference")
            continue
        if not target.is_file():
            errors.append(f"{rel}: missing local reference")
        elif fragment and target.suffix in {".html", ".svg"} and fragment not in ids_by_page.get(target, set()):
            errors.append(f"{rel}: missing fragment")
    return sorted(set(errors))


def privacy(site: Path, require: bool) -> list[str]:
    scanner = runpy.run_path(str(REPO / "tools/privacy_scan.py"))
    needles = scanner["load_needles"]()
    if not needles:
        return ["privacy: no needles configured"] if require else []
    patterns = scanner["patterns_for"](needles)
    errors = []
    for path in sorted(site.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT:
            text = path.read_text(encoding="utf-8", errors="replace")
            if scanner["scan_text"](html.unescape(text), patterns) or scanner["scan_text"](text, patterns):
                errors.append(f"{path.relative_to(site)}: forbidden site value")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=REPO / "site")
    parser.add_argument("--privacy", action="store_true")
    parser.add_argument("--require-privacy", action="store_true")
    args = parser.parse_args()
    site = args.site.resolve()
    errors = privacy(site, args.require_privacy) if args.privacy or args.require_privacy else []
    if not errors:
        errors = check(site)
    for error in errors:
        print(error)
    if errors:
        print(f"Site checks: FAIL ({len(errors)} findings)")
        return 1
    print(f"Site checks: PASS ({len(list(site.rglob('*.html')))} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
