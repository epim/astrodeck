"""GitHub Releases client for self-update.

Lists releases, picks the newest for the configured channel that is strictly
newer than the running version, and extracts the artifact + ``.sha256`` + ``.sig``
asset URLs. Pure ``pick_release`` is the logic (unit-tested); ``fetch_releases``
is the thin network call (httpx, already a server dep). No auth needed for a
public repo; an optional token raises the API rate limit.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from . import version as V

GITHUB_API = "https://api.github.com"


@dataclass
class ReleaseInfo:
    version: str           # normalized, e.g. "0.2.0"
    tag: str               # the raw tag, e.g. "v0.2.0"
    notes_md: str          # the release body (markdown), shown in the UI dialog
    artifact_url: str      # browser_download_url (public repos)
    sha256_url: "str | None"
    sig_url: "str | None"
    prerelease: bool
    # asset API urls (``/repos/.../releases/assets/{id}``). For a PRIVATE repo the
    # browser_download_url can't be fetched with a token; the asset API url + a
    # ``Accept: application/octet-stream`` header + the token can. None when the
    # release JSON omitted them. The service downloads from these when a
    # github_token is configured, else from the browser urls above.
    artifact_api_url: "str | None" = None
    sha256_api_url: "str | None" = None
    sig_api_url: "str | None" = None


def _headers(token: "str | None") -> dict:
    h = {"Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def fetch_releases(repo: str, *, token: "str | None" = None,
                         timeout: float = 15.0) -> list[dict]:
    url = f"{GITHUB_API}/repos/{repo}/releases?per_page=30"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url, headers=_headers(token))
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


def _find_asset(assets: list, *, suffix: str) -> "dict | None":
    for a in assets:
        if isinstance(a, dict) and str(a.get("name", "")).endswith(suffix):
            return a
    return None


def _asset_url(assets: list, *, suffix: str) -> "str | None":
    a = _find_asset(assets, suffix=suffix)
    return a.get("browser_download_url") if a else None


def pick_release(releases: list[dict], *, channel: str = "stable",
                 current: "str | None" = None) -> "ReleaseInfo | None":
    """Choose the newest non-draft release for ``channel`` strictly newer than
    ``current`` that carries a ``.tar.gz`` artifact. ``None`` if none qualifies."""
    rels = [r for r in releases if isinstance(r, dict) and not r.get("draft")]
    if channel != "prerelease":
        rels = [r for r in rels if not r.get("prerelease")]
    tags = [str(r.get("tag_name", "")) for r in rels]
    latest = V.select_latest(tags, channel=channel, current=current)
    if latest is None:
        return None

    chosen = None
    for r in rels:
        v = V.parse(str(r.get("tag_name", "")))
        if v is not None and str(v) == str(latest):
            chosen = r
            break
    if chosen is None:
        return None

    assets = chosen.get("assets") or []
    art = _find_asset(assets, suffix=".tar.gz")
    if not art or not art.get("browser_download_url"):
        return None  # a release with no artifact is not installable
    sha = _find_asset(assets, suffix=".sha256")
    sig = _find_asset(assets, suffix=".sig")
    return ReleaseInfo(
        version=str(latest),
        tag=str(chosen.get("tag_name", "")),
        notes_md=chosen.get("body") or "",
        artifact_url=art.get("browser_download_url"),
        sha256_url=sha.get("browser_download_url") if sha else None,
        sig_url=sig.get("browser_download_url") if sig else None,
        prerelease=bool(chosen.get("prerelease")),
        artifact_api_url=art.get("url"),
        sha256_api_url=sha.get("url") if sha else None,
        sig_api_url=sig.get("url") if sig else None,
    )


async def latest_release(repo: str, *, channel: str = "stable",
                         current: "str | None" = None,
                         token: "str | None" = None,
                         timeout: float = 15.0) -> "ReleaseInfo | None":
    rels = await fetch_releases(repo, token=token, timeout=timeout)
    return pick_release(rels, channel=channel, current=current)
