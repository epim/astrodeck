"""Semantic-version parsing + comparison for the self-update feature.

GitHub Release tags look like ``v0.2.0`` or ``v0.2.0-rc1``. We parse a restricted
SemVer (major.minor.patch + optional pre-release + ignored build metadata),
compare per the SemVer precedence rules, and pick the newest release honoring the
configured channel:

  - ``stable``     -> ignores pre-releases (``-rc1`` etc.)
  - ``prerelease`` -> includes pre-releases

A final release outranks a pre-release of the same core (``0.2.0`` > ``0.2.0-rc1``).

Import-light: stdlib only. No package imports (safe for the boot path + tests).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# major.minor.patch with an OPTIONAL -prerelease and an OPTIONAL +build (ignored).
_SEMVER_RE = re.compile(
    r"^\s*v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z][0-9A-Za-z.-]*))?"
    r"(?:\+[0-9A-Za-z.-]+)?\s*$"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[str, ...] = field(default_factory=tuple)  # () => final release
    raw: str = ""

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    def __str__(self) -> str:  # pragma: no cover - trivial
        core = f"{self.major}.{self.minor}.{self.patch}"
        return f"{core}-{'.'.join(self.pre)}" if self.pre else core


def parse(tag: str) -> "Version | None":
    """Parse a tag/version string into a ``Version`` (``None`` if unparseable)."""
    if not isinstance(tag, str):
        return None
    m = _SEMVER_RE.match(tag)
    if m is None:
        return None
    pre = tuple(m.group("pre").split(".")) if m.group("pre") else ()
    return Version(
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        patch=int(m.group("patch")),
        pre=pre,
        raw=tag.strip(),
    )


def _ident_key(ident: str) -> tuple[int, object]:
    """SemVer pre-release identifier precedence: numeric identifiers compare as
    ints and rank BELOW alphanumeric ones (the (0,..) vs (1,..) prefix keeps the
    comparison from ever pitting an int against a str)."""
    return (0, int(ident)) if ident.isdigit() else (1, ident)


def sort_key(v: Version) -> tuple:
    """Total order key. A final release (empty ``pre``) ranks above an otherwise
    equal pre-release via the ``pre_rank`` field."""
    pre_rank = 1 if not v.pre else 0
    return (v.major, v.minor, v.patch, pre_rank, tuple(_ident_key(p) for p in v.pre))


def is_newer(a: str | Version, b: str | Version) -> bool:
    """True iff version ``a`` is strictly newer than ``b``. Unparseable => False
    (fail-safe: an undecodable tag is never treated as an upgrade)."""
    va = a if isinstance(a, Version) else parse(a)
    vb = b if isinstance(b, Version) else parse(b)
    if va is None or vb is None:
        return False
    return sort_key(va) > sort_key(vb)


def select_latest(tags, *, channel: str = "stable",
                  current: str | None = None) -> "Version | None":
    """Newest parseable tag honoring ``channel``, strictly newer than ``current``.

    Returns ``None`` if nothing qualifies (no parseable tag, all filtered out by
    channel, or none newer than ``current``)."""
    versions = [v for v in (parse(t) for t in tags) if v is not None]
    if channel != "prerelease":
        versions = [v for v in versions if not v.is_prerelease]
    if not versions:
        return None
    latest = max(versions, key=sort_key)
    if current is not None:
        cur = parse(current)
        if cur is not None and not (sort_key(latest) > sort_key(cur)):
            return None
    return latest
