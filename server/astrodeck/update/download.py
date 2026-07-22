"""Streamed artifact download + small sidecar-text fetch (httpx).

The artifact is streamed to disk with a byte ceiling and progress callbacks so the
UI sees a live bar; sidecars (``.sha256`` / ``.sig``) are tiny text fetches.
"""
from __future__ import annotations

from pathlib import Path

import httpx

DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB ceiling (a release bundle is small)


def _headers(token: "str | None", accept: "str | None" = None) -> dict:
    # A GitHub asset API url (private repos) needs ``Accept: application/octet-stream``
    # to redirect to the download; the token authorizes the GitHub hop. httpx
    # follow_redirects strips the Authorization header on the cross-host redirect
    # to the signed storage URL, so the token never leaks to storage.
    h: dict = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if accept:
        h["Accept"] = accept
    return h


async def fetch_text(url: str, *, token: "str | None" = None,
                     accept: "str | None" = None, timeout: float = 15.0) -> str:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url, headers=_headers(token, accept))
        r.raise_for_status()
        return r.text


async def download(url: str, dest: Path, *, on_progress=None,
                   token: "str | None" = None, accept: "str | None" = None,
                   max_bytes: int = DEFAULT_MAX_BYTES,
                   timeout: float = 120.0) -> Path:
    """Stream ``url`` to ``dest``. Raises ``ValueError`` past ``max_bytes``.
    ``on_progress(fraction)`` is called as bytes arrive when a length is known."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=_headers(token, accept)) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                written = 0
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError(
                                f"artifact exceeds {max_bytes} byte ceiling")
                        f.write(chunk)
                        if on_progress and total:
                            on_progress(min(1.0, written / total))
    except BaseException:
        # never leave a partial/oversized artifact on a storage-limited scope.
        dest.unlink(missing_ok=True)
        raise
    return dest
