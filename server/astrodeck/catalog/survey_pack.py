"""Offline survey pack — fetch-once HiPS tile store (offline-pack spec §1–2, §5).

Owns pack paths/slugs, the manifest, the ENOSPC pre-flight, the resumable tile
fetch engine, the in-memory fetch-progress singleton, pack removal, and the
CLI (`python -m astrodeck.catalog.survey_pack fetch`). NO FastAPI imports —
api/app.py hosts the routes and maps this module's exceptions to HTTP statuses.
Importing ..hub for CAPTURE_DIR is seam-verified side-effect-free (spec §2).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import threading
import time
from pathlib import Path

import httpx

from ..hub import CAPTURE_DIR

PACK_ROOT = CAPTURE_DIR / "_survey_pack"
PACK_SLUGS: dict[str, str] = {"CDS/P/DSS2/color": "dss2color"}
DEFAULT_SLUG = "dss2color"
DEFAULT_ORDER = 4

_MIRRORS: dict[str, list[str]] = {
    "dss2color": [
        "https://skies.esac.esa.int/DSSColor",
        "https://alasky.cds.unistra.fr/DSS/DSSColor",
        "https://alaskybis.cds.unistra.fr/DSS/DSSColor",
    ],
}
_USER_AGENT = "AstroDeck/0.1"
_TILE_TIMEOUT_S = 30.0
_CONCURRENCY = 8
_TILE_BYTES_EST = 70_000                 # measured order-4 DSS2-color tile (spec §2)
_HEADROOM_BYTES = 50 * 1024 * 1024


class InsufficientSpace(RuntimeError):
    """Disk pre-flight failed; carries the numbers for the API's 507 body."""
    def __init__(self, free: int, required: int) -> None:
        super().__init__(f"insufficient disk space: {free} free, {required} required")
        self.free = free
        self.required = required


class FetchAlreadyRunning(RuntimeError):
    """A pack fetch is already in flight (single-flight, spec §2)."""


class PackFetchState:
    """Thread-locked progress snapshot polled by GET /api/survey/pack
    (same shape of idea as update/state.py's UpdateState)."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.done = 0
        self.total = 0
        self.failed = 0

    def try_start(self, total: int) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running, self.done, self.total, self.failed = True, 0, total, 0
            return True

    def tick(self, ok: bool) -> None:
        with self._lock:
            self.done += 1
            if not ok:
                self.failed += 1

    def finish(self) -> None:
        with self._lock:
            self.running = False

    def snapshot(self) -> dict | None:
        with self._lock:
            if not self.running:
                return None
            return {"done": self.done, "total": self.total, "failed": self.failed}


fetch_state = PackFetchState()


def pack_dir(slug: str = DEFAULT_SLUG) -> Path:
    return PACK_ROOT / slug


def tile_path(pack: Path, k: int, npix: int) -> Path:
    return pack / f"Norder{k}" / f"Dir{(npix // 10000) * 10000}" / f"Npix{npix}.jpg"


def tiles_for(order: int) -> list[tuple[int, int]]:
    return [(k, npix) for k in range(order + 1) for npix in range(12 * 4 ** k)]


def _have_tile(p: Path) -> bool:
    try:
        return p.stat().st_size > 0
    except OSError:
        return False


def read_manifest(pack: Path) -> dict | None:
    try:
        man = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return man if isinstance(man, dict) else None


def pack_present(survey_id: str) -> Path | None:
    """Routing gate (spec §1): manifest exists and parses -> pack dir, else None."""
    slug = PACK_SLUGS.get(survey_id)
    if slug is None:
        return None
    p = pack_dir(slug)
    return p if read_manifest(p) is not None else None


def preflight_disk(pack: Path, remaining_tiles: int) -> tuple[int, int]:
    """ENOSPC guard (spec §2): scaled by REMAINING tiles so a nearly-complete
    resume isn't refused on a nearly-full card. Returns (free, required)."""
    required = remaining_tiles * _TILE_BYTES_EST + _HEADROOM_BYTES
    probe = pack
    while not probe.exists():        # disk_usage needs an existing path
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < required:
        raise InsufficientSpace(free, required)
    return free, required


def _parse_properties(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip()
    return out


async def fetch_pack(order: int = DEFAULT_ORDER, slug: str = DEFAULT_SLUG,
                     dest: Path | None = None, log=None,
                     transport: httpx.BaseTransport | None = None) -> dict:
    """One resumable sweep (spec §2): pick the first mirror whose `properties`
    answers, fetch missing tiles (8-way, one retry each, JPEG-magic checked,
    atomic writes), write pack.json iff failed == 0. Ticks `fetch_state` for
    live progress but keeps its own authoritative counters for the result."""
    pack = dest if dest is not None else pack_dir(slug)
    todo = [(k, n) for k, n in tiles_for(order) if not _have_tile(tile_path(pack, k, n))]
    preflight_disk(pack, len(todo))
    headers = {"User-Agent": _USER_AGENT}
    async with httpx.AsyncClient(timeout=_TILE_TIMEOUT_S, headers=headers,
                                 transport=transport) as client:
        base = props_text = None
        for mirror in _MIRRORS[slug]:
            try:
                resp = await client.get(f"{mirror}/properties")
                resp.raise_for_status()
                props_text = resp.text
                props = _parse_properties(props_text)
                if "jpeg" not in props.get("hips_tile_format", "jpeg"):
                    continue
                base = mirror
                break
            except Exception:  # noqa: BLE001 — try the next mirror
                continue
        if base is None:
            raise RuntimeError("no HiPS mirror reachable")
        tile_width = int(props.get("hips_tile_width", "512"))
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "properties").write_text(props_text, encoding="utf-8")

        sem = asyncio.Semaphore(_CONCURRENCY)

        async def one(k: int, npix: int) -> bool:
            path = tile_path(pack, k, npix)
            url = f"{base}/Norder{k}/Dir{(npix // 10000) * 10000}/Npix{npix}.jpg"
            async with sem:
                for attempt in range(2):     # initial + one retry (spec §2)
                    try:
                        r = await client.get(url)
                        r.raise_for_status()
                        body = r.content
                        if not body.startswith(b"\xff\xd8"):
                            raise RuntimeError("not a JPEG")
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_suffix(".tmp")
                        tmp.write_bytes(body)
                        tmp.replace(path)    # atomic publish (survey.py idiom)
                        fetch_state.tick(ok=True)
                        return True
                    except Exception:  # noqa: BLE001 — uniform per-tile failure
                        if attempt == 0:
                            await asyncio.sleep(0.4)
                fetch_state.tick(ok=False)
                if log:
                    log(f"tile Norder{k}/Npix{npix} failed")
                return False

        results = await asyncio.gather(*(one(k, n) for k, n in todo))

    failed = sum(1 for ok in results if not ok)
    manifest = None
    if failed == 0:
        all_tiles = tiles_for(order)
        total_bytes = sum(tile_path(pack, k, n).stat().st_size for k, n in all_tiles)
        survey = next(s for s, sl in PACK_SLUGS.items() if sl == slug)
        manifest = {"survey": survey, "slug": slug, "order": order,
                    "tile_width": tile_width, "tile_count": len(all_tiles),
                    "fetched_at": time.time(), "bytes": total_bytes}
        tmp = pack / "pack.json.tmp"
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tmp.replace(pack / "pack.json")
    return {"done": len(todo), "total": len(todo), "failed": failed,
            "manifest": manifest}


def start_fetch(order: int = DEFAULT_ORDER, slug: str = DEFAULT_SLUG) -> None:
    """API entry (spec §5): synchronous disk pre-flight (507 upstream), then a
    background task on the running loop. Raises FetchAlreadyRunning if one is
    in flight. `fetch_pack` re-derives `todo`; nothing else writes tiles, so
    the total set in try_start matches its tick count in practice."""
    pack = pack_dir(slug)
    todo = sum(1 for k, n in tiles_for(order) if not _have_tile(tile_path(pack, k, n)))
    preflight_disk(pack, todo)
    if not fetch_state.try_start(todo):
        raise FetchAlreadyRunning()

    async def _run() -> None:
        try:
            await fetch_pack(order=order, slug=slug)
        except Exception:  # noqa: BLE001 — progress ends; GET shows fetching=null
            pass
        finally:
            fetch_state.finish()

    asyncio.get_running_loop().create_task(_run())


def pack_status(slug: str = DEFAULT_SLUG) -> dict:
    """GET /api/survey/pack payload (spec §5): manifest + live progress; never
    a 4k-file directory scan on the request path."""
    survey = next(s for s, sl in PACK_SLUGS.items() if sl == slug)
    man = read_manifest(pack_dir(slug)) or {}
    return {"present": bool(man), "slug": slug, "survey": survey,
            "order": man.get("order"), "bytes": man.get("bytes"),
            "tile_count": man.get("tile_count"),
            "fetched_at": man.get("fetched_at"),
            "fetching": fetch_state.snapshot()}


def remove_pack(slug: str = DEFAULT_SLUG) -> bool:
    """Delete the pack directory (spec §5). Refuses while a fetch runs."""
    if fetch_state.snapshot() is not None:
        raise FetchAlreadyRunning()
    p = pack_dir(slug)
    if not p.exists():
        return False
    shutil.rmtree(p, ignore_errors=True)
    return True


async def _cli_fetch(order: int, dest: Path | None, log) -> dict:
    pack = dest if dest is not None else pack_dir()
    todo = sum(1 for k, n in tiles_for(order) if not _have_tile(tile_path(pack, k, n)))
    fetch_state.try_start(todo)

    async def report() -> None:
        while True:
            await asyncio.sleep(2)
            snap = fetch_state.snapshot()
            if snap:
                log(f"{snap['done']}/{snap['total']} ({snap['failed']} failed)")

    rep = asyncio.create_task(report())
    try:
        return await fetch_pack(order=order, dest=dest, log=log)
    finally:
        rep.cancel()
        fetch_state.finish()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m astrodeck.catalog.survey_pack")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download the offline sky pack (resumable)")
    f.add_argument("--order", type=int, default=DEFAULT_ORDER,
                   help="HiPS depth 1-6 (default 4, ~250 MB)")
    f.add_argument("--dest", type=Path, default=None,
                   help="pack directory (default: <captures>/_survey_pack/dss2color)")
    args = ap.parse_args(argv)
    order = max(1, min(6, args.order))

    def log(msg: str) -> None:
        print(msg, flush=True)

    try:
        result = asyncio.run(_cli_fetch(order, args.dest, log))
    except InsufficientSpace as exc:
        print(f"not enough disk space: {exc.free // 2**20} MB free, "
              f"~{exc.required // 2**20} MB required", flush=True)
        return 2
    except RuntimeError as exc:
        print(f"fetch failed: {exc}", flush=True)
        return 1
    ok = result["done"] - result["failed"]
    print(f"done: {ok}/{result['total']} tiles, {result['failed']} failed"
          + ("" if result["manifest"] else " — re-run to resume"), flush=True)
    return 0 if result["manifest"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
