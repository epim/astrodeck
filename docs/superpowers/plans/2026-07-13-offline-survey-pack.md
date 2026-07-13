# Offline-First Survey Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Atlas renders survey imagery from a local HiPS tile pack with zero network; CDS hips2fits becomes an opt-in Settings toggle (default off) used only where it adds resolution.

**Architecture:** New `catalog/survey_pack.py` (tile fetcher + manifest + CLI + progress singleton, FastAPI-free) and `catalog/hips_local.py` (numpy/astropy-healpix TAN renderer). `catalog/survey.py`'s miss path becomes a router: upstream (when enabled and fov < 4°) → pack render → 503. Routes for config + pack live in `api/app.py` (drivers/rotator precedent). UI: Settings "Sky Atlas" card, survey-picker gating, fetching-aware Atlas banner.

**Tech Stack:** FastAPI, httpx, numpy, astropy (WCS), **astropy-healpix (new dep)**, Pillow; React+TS UI with the repo's `api.ts`/zustand idioms.

**Spec:** `docs/superpowers/specs/2026-07-13-offline-survey-pack-design.md` (read §0 Frozen contracts before touching survey.py).
**Seam files (verbatim ground truth, read before coding):** `.superpowers/sdd/seams/offline-pack-server.md`, `.superpowers/sdd/seams/offline-pack-ui.md`.

## Global Constraints

- Frozen contracts (spec §0): `ra` query param is HOURS (`ra_deg = ra*15`); TAN, square, North-up, no rot; snapped `X-Survey-Ra-Deg`/`X-Survey-Dec-Deg`/`X-Survey-Fov-Deg` headers on every 200; failure shape exactly `503 {"detail": "survey unavailable", "fallback": "schematic"}`; `_snap_geometry` bucketing unchanged.
- Upstream cache keys byte-identical to today: `sha1("v2|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|{stretch}")`. Pack keys: `sha1("v2pk|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|-")`.
- `survey.online_fetch` defaults to **False**. With it False, no code path may construct an httpx client for cutouts.
- Threshold constant `_ONLINE_FOV_MAX_DEG = 4.0`, compared against **snapped_fov**.
- Pack location `CAPTURE_DIR / "_survey_pack" / "dss2color"`; `_evict_cache` must never touch it.
- Capabilities: `CAP_CONFIG_SITE_OPTICS` for all writes (config/survey, pack fetch, pack delete); `CAP_VIEW_STATUS` for GET pack. Every new app.py route needs a matching `@declare(...)` (boot audit).
- Renderer runs off the event loop (`asyncio.to_thread`); disk writes use the `.tmp` + `.replace()` atomic idiom.
- Mirror list order: `https://skies.esac.esa.int/DSSColor`, `https://alasky.cds.unistra.fr/DSS/DSSColor`, `https://alaskybis.cds.unistra.fr/DSS/DSSColor`.
- Server tests run from `server/`: `./.venv/Scripts/python.exe -m pytest -q <file>` (bash: `/c/Users/bear/astro/server/.venv/Scripts/python.exe`). UI: `npm run build` (tsc -b && vite) + self-executing `npx tsx src/lib/__tests__/<name>.test.ts` — there is NO vitest.
- Every commit message ends with the two trailers:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL`.

---

### Task 1: Pack store + fetch engine + CLI (`survey_pack.py`)

**Files:**
- Create: `server/astrodeck/catalog/survey_pack.py`
- Test: `server/tests/test_survey_pack_fetch.py`

**Interfaces:**
- Consumes: `CAPTURE_DIR` from `..hub` (import verified side-effect-free, seam §4); httpx; `update/state.py`'s singleton idiom (seam §6) as a shape reference only.
- Produces (used by Tasks 2/3/4): `PACK_ROOT: Path`, `PACK_SLUGS: dict[str,str]`, `DEFAULT_ORDER = 4`, `pack_dir(slug="dss2color") -> Path`, `tile_path(pack, k, npix) -> Path`, `tiles_for(order) -> list[tuple[int,int]]`, `read_manifest(pack) -> dict | None`, `pack_present(survey_id) -> Path | None`, `preflight_disk(pack, remaining_tiles) -> tuple[int,int]` (raises `InsufficientSpace(free, required)`), `fetch_state: PackFetchState` (`try_start(total)->bool`, `tick(ok)`, `finish()`, `snapshot()->dict|None`), `async fetch_pack(order=4, slug="dss2color", dest=None, log=None, transport=None) -> dict`, `start_fetch(order=4, slug="dss2color") -> None` (raises `FetchAlreadyRunning` / `InsufficientSpace`; schedules an asyncio task), `pack_status(slug="dss2color") -> dict`, `remove_pack(slug="dss2color") -> bool` (raises `FetchAlreadyRunning`), `main(argv) -> int`.

- [ ] **Step 1: Write the failing tests** — `server/tests/test_survey_pack_fetch.py`. Use `httpx.MockTransport`; patch `survey_pack_mod.PACK_ROOT` to tmp. Core of the file (write all of it):

```python
"""Tests for the offline survey pack fetcher (offline-pack spec §2)."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

import astrodeck.catalog.survey_pack as sp

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 60


def _transport(fail_npix: set[int] = frozenset(), counter: dict | None = None):
    """MockTransport serving a properties file and fake JPEG tiles."""
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter[request.url.path] = counter.get(request.url.path, 0) + 1
        if request.url.path.endswith("/properties"):
            return httpx.Response(200, text=(
                "hips_tile_width      = 64\nhips_tile_format     = jpeg\n"))
        npix = int(request.url.path.rsplit("Npix", 1)[1].split(".")[0])
        if npix in fail_npix:
            return httpx.Response(200, content=b"<html>not a jpeg</html>")
        return httpx.Response(200, content=_JPEG)
    return httpx.MockTransport(handler)


@pytest.fixture
def pack_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PACK_ROOT", tmp_path / "_survey_pack")
    sp.fetch_state.finish()  # never leak running state between tests
    return tmp_path / "_survey_pack"


def test_full_fetch_writes_tree_and_manifest(pack_root):
    res = asyncio.run(sp.fetch_pack(order=0, transport=_transport()))
    assert res["failed"] == 0
    pack = sp.pack_dir()
    assert (pack / "properties").exists()
    for npix in range(12):
        assert sp.tile_path(pack, 0, npix).read_bytes() == _JPEG
    man = sp.read_manifest(pack)
    assert man["order"] == 0 and man["tile_count"] == 12
    assert man["tile_width"] == 64 and man["slug"] == "dss2color"
    assert sp.pack_present("CDS/P/DSS2/color") == pack


def test_resume_skips_existing_tiles(pack_root):
    pack = sp.pack_dir()
    pre = sp.tile_path(pack, 0, 3)
    pre.parent.mkdir(parents=True, exist_ok=True)
    pre.write_bytes(_JPEG)
    counter: dict = {}
    asyncio.run(sp.fetch_pack(order=0, transport=_transport(counter=counter)))
    assert not any(p.endswith("Npix3.jpg") for p in counter)  # skipped
    assert sum(1 for p in counter if "Npix" in p) == 11


def test_non_jpeg_counts_failed_and_blocks_manifest(pack_root):
    res = asyncio.run(sp.fetch_pack(order=0, transport=_transport(fail_npix={5})))
    assert res["failed"] == 1
    assert not sp.tile_path(sp.pack_dir(), 0, 5).exists()
    assert sp.read_manifest(sp.pack_dir()) is None
    assert sp.pack_present("CDS/P/DSS2/color") is None


def test_preflight_blocks_fresh_but_allows_resume(pack_root, monkeypatch):
    pack = sp.pack_dir()
    free = 60 * 1024 * 1024  # 60 MB free
    monkeypatch.setattr(sp.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": free})())
    # fresh order-4 fetch: 4092 tiles * 70 KB + 50 MB ≈ 337 MB required -> blocked
    with pytest.raises(sp.InsufficientSpace) as exc:
        sp.preflight_disk(pack, remaining_tiles=4092)
    assert exc.value.required > exc.value.free
    # nearly-complete resume: 3 tiles remaining -> allowed on the same card
    got_free, required = sp.preflight_disk(pack, remaining_tiles=3)
    assert got_free == free and required < free


def test_fetch_state_single_flight_and_remove_guard(pack_root):
    assert sp.fetch_state.try_start(total=10) is True
    assert sp.fetch_state.try_start(total=10) is False
    with pytest.raises(sp.FetchAlreadyRunning):
        sp.remove_pack()
    sp.fetch_state.finish()
    assert sp.fetch_state.snapshot() is None
    assert sp.remove_pack() is False  # nothing on disk yet -> False, no raise


def test_pack_status_shapes(pack_root):
    s = sp.pack_status()
    assert s == {"present": False, "slug": "dss2color",
                 "survey": "CDS/P/DSS2/color", "order": None, "bytes": None,
                 "tile_count": None, "fetched_at": None, "fetching": None}
    asyncio.run(sp.fetch_pack(order=0, transport=_transport()))
    s = sp.pack_status()
    assert s["present"] is True and s["order"] == 0 and s["bytes"] > 0


def test_remove_pack_deletes_tree(pack_root):
    asyncio.run(sp.fetch_pack(order=0, transport=_transport()))
    assert sp.remove_pack() is True
    assert not sp.pack_dir().exists()
    assert sp.pack_present("CDS/P/DSS2/color") is None
```

- [ ] **Step 2: Run to verify failure** — from `server/`: `./.venv/Scripts/python.exe -m pytest -q tests/test_survey_pack_fetch.py`. Expected: collection error (`No module named 'astrodeck.catalog.survey_pack'`).

- [ ] **Step 3: Implement `server/astrodeck/catalog/survey_pack.py`** (complete module):

```python
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
```

- [ ] **Step 4: Run tests to verify pass** — `./.venv/Scripts/python.exe -m pytest -q tests/test_survey_pack_fetch.py`. Expected: 7 passed.
- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/catalog/survey_pack.py server/tests/test_survey_pack_fetch.py
git commit -m "feat(survey): offline pack fetch engine — resumable HiPS tile downloader, manifest, ENOSPC pre-flight, CLI (spec §1-2)"
```

---

### Task 2: Local HiPS renderer (`hips_local.py`) + astropy-healpix dep

**Files:**
- Modify: `server/pyproject.toml` (dependencies list, seam §7)
- Create: `server/astrodeck/catalog/hips_local.py`
- Test: `server/tests/test_hips_local.py`

**Interfaces:**
- Consumes: `read_manifest`, `tile_path` from `.survey_pack` (Task 1).
- Produces (used by Task 3): `render_cutout(pack: Path, ra_deg: float, dec_deg: float, fov_deg: float, width: int) -> bytes` (JPEG), `class PackUnavailable(RuntimeError)`, `_order_for(fov_deg, width, tile_width, max_order) -> int`, `_deinterleave(sub: np.ndarray, s: int) -> tuple[np.ndarray, np.ndarray]`.

**Correctness anchoring (spec §3):** the two bit conventions are module constants `_X_FROM_EVEN_BITS = True` and `_JPG_FLIP_Y = True` (HiPS 1.0 §4.2: within-tile nested sub-index de-interleaves x from even bits / y from odd bits; JPEG tiles are the FITS raster flipped vertically). Tile-LEVEL orientation (N/S, E/W) is pinned by tests against astropy-healpix truth. The WITHIN-tile conventions additionally require the dev-time ground-truth check in Step 6 — if the ground truth disagrees, flip the constants (and/or the WCS RA sign) until it matches, then re-run the tests.

- [ ] **Step 1: Add the dependency** — in `server/pyproject.toml` dependencies, after `"astropy>=6.0",` add `"astropy-healpix>=1.0",`; then from `server/`: `./.venv/Scripts/python.exe -m pip install -e ".[dev]"`. Expected: astropy-healpix installs from a wheel.

- [ ] **Step 2: Write the failing tests** — `server/tests/test_hips_local.py`:

```python
"""Tests for the local HiPS TAN renderer (offline-pack spec §3, §7).
Synthetic packs only — no licensed imagery in the repo."""
import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import astrodeck.catalog.hips_local as hl
from astrodeck.catalog.survey_pack import tile_path

TILE_W = 64  # power of two; renderer must honor manifest tile_width, not 512


def make_pack(root: Path, order: int, color_for) -> Path:
    """Synthetic pack: every tile a solid RGB from color_for(k, npix)."""
    pack = root / "dss2color"
    for k in range(order + 1):
        for npix in range(12 * 4 ** k):
            p = tile_path(pack, k, npix)
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (TILE_W, TILE_W), color_for(k, npix)).save(
                p, "JPEG", quality=95)
    (pack / "pack.json").write_text(json.dumps(
        {"survey": "CDS/P/DSS2/color", "slug": "dss2color", "order": order,
         "tile_width": TILE_W, "tile_count": sum(12 * 4 ** k for k in range(order + 1)),
         "fetched_at": 0, "bytes": 1}), encoding="utf-8")
    return pack


def _mean_rgb(img: bytes, box: tuple[int, int, int, int]) -> np.ndarray:
    arr = np.asarray(Image.open(io.BytesIO(img)).convert("RGB").crop(box), float)
    return arr.reshape(-1, 3).mean(axis=0)


def _npix_at(order: int, ra_deg: float, dec_deg: float) -> int:
    from astropy_healpix import HEALPix
    import astropy.units as u
    return int(HEALPix(nside=2 ** order, order="nested").lonlat_to_healpix(
        ra_deg * u.deg, dec_deg * u.deg))


def test_order_selection_math():
    # 64px tiles: order-k scale = 58.6324*3600/(64*2^k) arcsec/px
    # fov=30 deg, width=256 -> s_out=421.9"/px; order0 tile=3297"/px -> climbs
    assert hl._order_for(30.0, 256, 64, max_order=5) == 3   # 412"/px <= 421.9
    assert hl._order_for(1.0, 768, 64, max_order=5) == 5    # clamped to pack depth
    assert hl._order_for(250.0, 256, 64, max_order=5) == 0  # s_out 3516 >= 3298 -> base
    assert hl._order_for(30.0, 256, 512, max_order=5) == 0  # 512px tiles: 412"/px at k=0


def test_deinterleave_hand_cases():
    x, y = hl._deinterleave(np.array([0b1101]), s=2)
    assert (int(x[0]), int(y[0])) == (0b11, 0b10)  # even bits -> x, odd -> y
    x, y = hl._deinterleave(np.array([0]), s=3)
    assert (int(x[0]), int(y[0])) == (0, 0)
    x, y = hl._deinterleave(np.array([(1 << 6) - 1]), s=3)  # all ones
    assert (int(x[0]), int(y[0])) == (7, 7)


def test_tile_targeting_center_color(tmp_path):
    # Cutout centered inside a known order-1 healpix pixel is dominated by
    # that tile's color (astropy-healpix is the truth for which tile that is).
    target = _npix_at(1, 45.0, 30.0)
    pack = make_pack(tmp_path, 1, lambda k, n: (250, 30, 30) if (k, n) == (1, target)
                     else (10, 10, 10))
    img = hl.render_cutout(pack, 45.0, 30.0, 5.0, 128)
    mid = _mean_rgb(img, (48, 48, 80, 80))
    assert mid[0] > 150 and mid[1] < 80  # strongly red at center


def test_orientation_north_up_east_left(tmp_path):
    # North tile red, south tile blue, east tile green, west tile yellow;
    # centered between them the rendered halves must land accordingly.
    n = _npix_at(1, 45.0, 40.0)
    s = _npix_at(1, 45.0, 10.0)
    e = _npix_at(1, 65.0, 25.0)
    w = _npix_at(1, 25.0, 25.0)
    colors = {n: (250, 20, 20), s: (20, 20, 250), e: (20, 250, 20), w: (250, 250, 20)}
    pack = make_pack(tmp_path, 1, lambda k, np_: colors.get(np_, (0, 0, 0)))
    img = hl.render_cutout(pack, 45.0, 25.0, 40.0, 200)
    top = _mean_rgb(img, (80, 0, 120, 40))
    bottom = _mean_rgb(img, (80, 160, 120, 200))
    left = _mean_rgb(img, (0, 80, 40, 120))
    right = _mean_rgb(img, (160, 80, 200, 120))
    assert top[0] > bottom[0]      # red (north, +dec) at the TOP
    assert bottom[2] > top[2]      # blue (south) at the bottom
    assert left[1] > right[1]      # green (east, +RA) on the LEFT (E-left)
    assert right[0] > left[0] and right[1] > left[1] * 0.5  # yellow (west) right
    # NOTE (implementer): healpix pixels are diamonds — if an assert fails
    # because a sample box straddles a pixel edge, tune the sample points/boxes.
    # The DIRECTION relations (N top / S bottom / E left / W right) are the
    # requirement; do not weaken those to make a box placement pass.


def test_missing_tile_fills_black_no_raise(tmp_path):
    target = _npix_at(0, 45.0, 30.0)
    pack = make_pack(tmp_path, 0, lambda k, n: (200, 200, 200))
    tile_path(pack, 0, target).unlink()
    img = hl.render_cutout(pack, 45.0, 30.0, 5.0, 64)
    assert _mean_rgb(img, (24, 24, 40, 40)).max() < 40  # black fill at center


def test_absent_manifest_raises(tmp_path):
    with pytest.raises(hl.PackUnavailable):
        hl.render_cutout(tmp_path / "nope", 45.0, 30.0, 5.0, 64)


def test_render_perf_768(tmp_path):
    pack = make_pack(tmp_path, 0, lambda k, n: (60, 60, 60))
    hl.render_cutout(pack, 45.0, 30.0, 5.0, 128)  # warm the tile LRU
    t0 = time.perf_counter()
    hl.render_cutout(pack, 44.0, 29.0, 5.0, 768)
    assert time.perf_counter() - t0 < 0.15  # spec §3 budget (generous)
```

- [ ] **Step 3: Run to verify failure** — `./.venv/Scripts/python.exe -m pytest -q tests/test_hips_local.py`. Expected: `No module named 'astrodeck.catalog.hips_local'`.

- [ ] **Step 4: Implement `server/astrodeck/catalog/hips_local.py`** (complete module):

```python
"""Local HiPS -> TAN cutout renderer (offline-pack spec §3).

Same output contract as the hips2fits proxy: square TAN JPEG, North-up,
East-left, no rotation. Pure function core, no FastAPI imports; the survey
route calls render_cutout via asyncio.to_thread (never on the event loop).

Bit conventions (HiPS 1.0 §4.2), pinned by tests + the dev ground-truth check:
within-tile nested sub-index de-interleaves x from EVEN bits / y from ODD
bits; JPEG/PNG tiles are the FITS raster flipped vertically.
"""
from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from .survey_pack import read_manifest, tile_path

_X_FROM_EVEN_BITS = True
_JPG_FLIP_Y = True
_ORDER0_TILE_ARCSEC = 58.6324 * 3600.0   # healpix order-0 pixel side, sqrt(pi/3) rad
_JPEG_QUALITY = 85
_TILE_LRU = 48                           # ~36 MB worst case at 512px RGB


class PackUnavailable(RuntimeError):
    """No usable pack manifest — the route falls through (spec §4 step 5)."""


def _order_for(fov_deg: float, width: int, tile_width: int, max_order: int) -> int:
    """Smallest order whose tile scale <= output scale, clamped (spec §3)."""
    s_out = fov_deg * 3600.0 / width
    k = 0
    while k < max_order and (_ORDER0_TILE_ARCSEC / (tile_width * 2 ** k)) > s_out:
        k += 1
    return k


def _deinterleave(sub: np.ndarray, s: int) -> tuple[np.ndarray, np.ndarray]:
    """Split a 2s-bit nested sub-index into (x, y) tile coordinates."""
    x = np.zeros_like(sub)
    y = np.zeros_like(sub)
    for b in range(s):
        x |= ((sub >> (2 * b)) & 1) << b
        y |= ((sub >> (2 * b + 1)) & 1) << b
    return (x, y) if _X_FROM_EVEN_BITS else (y, x)


@lru_cache(maxsize=_TILE_LRU)
def _load_tile(pack_str: str, k: int, npix: int, tile_width: int):
    """Tile file -> RGB ndarray, or None (missing/corrupt -> black fill)."""
    p = tile_path(Path(pack_str), k, npix)
    try:
        arr = np.asarray(Image.open(p).convert("RGB"))
    except Exception:  # noqa: BLE001 — any unreadable tile renders black
        return None
    if arr.shape != (tile_width, tile_width, 3):
        return None
    return arr


def render_cutout(pack: Path, ra_deg: float, dec_deg: float,
                  fov_deg: float, width: int) -> bytes:
    man = read_manifest(pack)
    if man is None:
        raise PackUnavailable(f"no pack manifest under {pack}")
    tile_width = int(man.get("tile_width", 512))
    s = tile_width.bit_length() - 1
    if 2 ** s != tile_width:
        raise PackUnavailable(f"tile_width {tile_width} is not a power of two")
    max_order = int(man["order"])
    k = _order_for(fov_deg, width, tile_width, max_order)

    from astropy.wcs import WCS  # deferred: keeps module import light
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.crpix = [(width + 1) / 2.0, (width + 1) / 2.0]
    scale = fov_deg / width
    w.wcs.cdelt = [-scale, scale]        # RA grows LEFT (East-left), dec UP

    cols = np.arange(width)
    rows = np.arange(width)
    px, py = np.meshgrid(cols, (width - 1) - rows)  # row 0 (top) = +dec
    lon, lat = w.all_pix2world(px, py, 0)

    from astropy_healpix import HEALPix
    import astropy.units as u
    hp = HEALPix(nside=2 ** (k + s), order="nested")
    h = hp.lonlat_to_healpix(lon * u.deg, lat * u.deg)
    npix = h >> (2 * s)
    sub = h & ((1 << (2 * s)) - 1)
    tx, ty = _deinterleave(sub, s)
    tile_row = (tile_width - 1 - ty) if _JPG_FLIP_Y else ty

    out = np.zeros((width, width, 3), np.uint8)
    for t in np.unique(npix):
        arr = _load_tile(str(pack), k, int(t), tile_width)
        if arr is None:
            continue                      # black fill (spec §3)
        m = npix == t
        out[m] = arr[tile_row[m], tx[m]]

    buf = io.BytesIO()
    Image.fromarray(out).save(buf, "JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()
```

Note: `_load_tile`'s LRU key includes the pack path string; tests use distinct tmp dirs so cross-test cache hits cannot occur. If a test mutates tiles in place (the missing-tile test unlinks AFTER no render has happened yet), no `cache_clear()` is needed; if you reorder tests, call `hl._load_tile.cache_clear()` first.

- [ ] **Step 5: Run tests to verify pass** — `./.venv/Scripts/python.exe -m pytest -q tests/test_hips_local.py`. Expected: 8 passed. If the orientation test fails: flip `w.wcs.cdelt[0]`'s sign or the meshgrid row flip until N/S/E/W land correctly — those two choices are what the test exists to pin. If only the within-tile look is wrong later (Step 6), flip `_X_FROM_EVEN_BITS`/`_JPG_FLIP_Y`.

- [ ] **Step 6: Dev ground-truth check (REQUIRED, report the result):** fetch a real order-3 pack (~35 MB, ESA mirror is up): from `server/`: `./.venv/Scripts/python.exe -m astrodeck.catalog.survey_pack fetch --order 3`. Then render the bucket that already has a genuine CDS JPEG in the live cache and compare:

```python
# server/ python - <<'EOF' style one-off; adjust nothing else
from pathlib import Path
from astrodeck.catalog.hips_local import render_cutout
from astrodeck.catalog.survey import _snap_geometry, _cache_key, _SURVEY_CACHE_DIR
from astrodeck.catalog.survey_pack import pack_dir
ra_h, dec, fov = 0.7123, 41.269, 1.5   # the user's cached M31 view
sra, sdec, sfov, idx = _snap_geometry(ra_h, dec, fov)
ref = _SURVEY_CACHE_DIR / f"{_cache_key(idx, 768, 'CDS/P/DSS2/color', 'linear')}.jpg"
out = Path("../.superpowers/sdd/m31_local_render.jpg")
out.write_bytes(render_cutout(pack_dir(), sra, sdec, sfov, 768))
print("local render:", out.resolve())
print("CDS reference:", ref, ref.exists())
EOF
```

View BOTH images (Read tool renders JPEGs). M31 must appear in the same position, same orientation (NGC 205 on the same side), same E/W mirroring — soft/blurry is expected (order 3 = 52″/px). If mirrored or rotated, flip the responsible constant and re-run Step 5 + this step. State the outcome explicitly in your report.

- [ ] **Step 7: Commit**

```bash
git add server/pyproject.toml server/astrodeck/catalog/hips_local.py server/tests/test_hips_local.py
git commit -m "feat(survey): local HiPS TAN renderer (astropy-healpix), orientation pinned by synthetic-pack tests (spec §3)"
```

---

### Task 3: Config block + offline-first routing in `survey.py`

**Files:**
- Modify: `server/astrodeck/config.py` (SurveyConfig model + AppConfig field + `set_survey`)
- Modify: `server/astrodeck/catalog/survey.py` (imports, `_pack_cache_key`, route body)
- Modify: `server/tests/test_survey.py` (fixture + new tests; legacy upstream tests keep passing via `online_fetch=True`)

**Interfaces:**
- Consumes: `pack_present` (Task 1), `render_cutout`/`PackUnavailable` (Task 2), `config_store`/`ConfigStore` (seam §1).
- Produces: `SurveyConfig` (config.py, `online_fetch: bool = False`), `ConfigStore.set_survey(survey) -> AppConfig`, survey.py constants `_ONLINE_FOV_MAX_DEG = 4.0` and `_pack_cache_key(idx, width, survey) -> str`, response header `X-Survey-Source: upstream | pack`.

- [ ] **Step 1: config.py additions.** Model (place near RotatorConfig, config.py ~:271):

```python
class SurveyConfig(BaseModel):
    """Sky-Atlas survey source (offline-pack spec §4). online_fetch gates ALL
    hips2fits upstream calls: False (the default) = offline-first, the local
    pack is the only source; True = upstream for fov < 4°, pack as fallback."""
    online_fetch: bool = False
```

AppConfig field (after the rotator line, same additive-comment convention):

```python
    # --- Sky-Atlas survey source (offline-pack spec §4; appended — old configs load fine) ---
    survey: SurveyConfig = Field(default_factory=SurveyConfig)
```

Setter on ConfigStore (below `set_rotator`; a bool needs no validation):

```python
    def set_survey(self, survey: "SurveyConfig") -> AppConfig:
        """Persist the survey-source config (offline-pack spec §4)."""
        cfg = self.cfg()
        cfg.survey = survey
        return self.bump_and_save()
```

- [ ] **Step 2: Write the new failing route tests** (append to `server/tests/test_survey.py`; also UPDATE the existing `client` fixture and `_asgi_client` helper first — shown here in full):

Updated fixture — legacy tests exercise the upstream path, so they run with `online_fetch=True` and no pack (behavior identical to today):

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    yield from _make_client(tmp_path, monkeypatch, online=True, pack=False)


def _make_client(tmp_path, monkeypatch, *, online: bool, pack: bool):
    import astrodeck.catalog.survey_pack as pack_mod
    from astrodeck.config import ConfigStore

    monkeypatch.setattr(survey_mod, "_SURVEY_CACHE_DIR", tmp_path / "_survey")
    monkeypatch.setattr(survey_mod.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_params = None
    _FakeClient.fail = False

    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    temp_store.cfg().survey.online_fetch = online
    monkeypatch.setattr(survey_mod, "config_store", temp_store)
    monkeypatch.setattr(pack_mod, "PACK_ROOT", tmp_path / "_survey_pack")
    if pack:
        pdir = pack_mod.pack_dir()
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "pack.json").write_text(
            '{"survey": "CDS/P/DSS2/color", "slug": "dss2color", "order": 4,'
            ' "tile_width": 512, "tile_count": 4092, "fetched_at": 0, "bytes": 1}')

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(survey_mod.router)
    with TestClient(app) as c:
        yield c
```

(`_asgi_client` gets the same three additions: temp_store with `online_fetch=True`, `config_store` patch, `PACK_ROOT` patch — keep the `_RealAsyncClient` outer-client trick EXACTLY as-is, seam §5.)

New fixtures + tests:

```python
_FAKE_RENDER = b"\xff\xd8\xff\xe0fake-pack-render"


@pytest.fixture
def offline_client(tmp_path, monkeypatch):
    yield from _make_client(tmp_path, monkeypatch, online=False, pack=True)


@pytest.fixture
def offline_nopack_client(tmp_path, monkeypatch):
    yield from _make_client(tmp_path, monkeypatch, online=False, pack=False)


@pytest.fixture
def online_pack_client(tmp_path, monkeypatch):
    yield from _make_client(tmp_path, monkeypatch, online=True, pack=True)


@pytest.fixture
def fake_render(monkeypatch):
    calls = {"n": 0}
    def _render(pack, ra, dec, fov, width):
        calls["n"] += 1
        return _FAKE_RENDER
    import astrodeck.catalog.hips_local as hl
    monkeypatch.setattr(hl, "render_cutout", _render)
    return calls


class _Boom:
    """httpx.AsyncClient stand-in whose CONSTRUCTION fails the test."""
    def __init__(self, *a, **kw):
        raise AssertionError("httpx client constructed with online_fetch=False")


def test_offline_serves_pack_and_never_builds_client(offline_client, fake_render, monkeypatch):
    monkeypatch.setattr(survey_mod.httpx, "AsyncClient", _Boom)
    r = offline_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=2.3")
    assert r.status_code == 200
    assert r.headers["X-Survey-Source"] == "pack"
    assert r.headers["X-Survey-Fov-Deg"]          # snapped headers still present
    assert r.content == _FAKE_RENDER
    r2 = offline_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=2.3")
    assert r2.status_code == 200 and fake_render["n"] == 1  # cached, one render


def test_offline_no_pack_is_frozen_503(offline_nopack_client, monkeypatch):
    monkeypatch.setattr(survey_mod.httpx, "AsyncClient", _Boom)
    r = offline_nopack_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=2.3")
    assert r.status_code == 503
    assert r.json()["detail"] == {"detail": "survey unavailable", "fallback": "schematic"}


def test_online_smallfov_upstream_then_pack_fallback(online_pack_client, fake_render):
    r = online_pack_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=1.5")
    assert r.status_code == 200 and r.headers["X-Survey-Source"] == "upstream"
    _FakeClient.fail = True
    r = online_pack_client.get("/api/survey/cutout.jpg?ra=9.0&dec=10.0&fov=1.5")
    assert r.status_code == 200 and r.headers["X-Survey-Source"] == "pack"
    assert r.content == _FAKE_RENDER


def test_online_widefov_uses_pack_without_upstream(online_pack_client, fake_render, monkeypatch):
    monkeypatch.setattr(survey_mod.httpx, "AsyncClient", _Boom)
    r = online_pack_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=6.0")
    assert r.status_code == 200 and r.headers["X-Survey-Source"] == "pack"


def test_pack_cached_file_does_not_satisfy_upstream_want(online_pack_client, fake_render):
    # Seed the pack-cache file by failing upstream once...
    _FakeClient.fail = True
    r = online_pack_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=1.5")
    assert r.headers["X-Survey-Source"] == "pack"
    # ...then upstream recovers: the same bucket must UPGRADE to upstream.
    _FakeClient.fail = False
    r = online_pack_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=1.5")
    assert r.headers["X-Survey-Source"] == "upstream"


def test_cache_key_formats_pinned():
    import hashlib
    idx = (10, 20, 30)
    up = survey_mod._cache_key(idx, 768, "CDS/P/DSS2/color", "linear")
    assert up == hashlib.sha1(b"v2|CDS/P/DSS2/color|10|20|30|768|linear").hexdigest()
    pk = survey_mod._pack_cache_key(idx, 768, "CDS/P/DSS2/color")
    assert pk == hashlib.sha1(b"v2pk|CDS/P/DSS2/color|10|20|30|768|-").hexdigest()


def test_evict_never_touches_pack_dir(tmp_path):
    cache = tmp_path / "_survey"
    cache.mkdir()
    packf = tmp_path / "_survey_pack" / "dss2color" / "Norder0" / "Dir0" / "Npix0.jpg"
    packf.parent.mkdir(parents=True)
    packf.write_bytes(b"\xff\xd8keep")
    survey_mod._evict_cache(cache, ttl_s=0, max_bytes=0, max_files=0)
    assert packf.exists()


def test_render_failure_offline_falls_to_503(offline_client, monkeypatch):
    import astrodeck.catalog.hips_local as hl
    def _boom(*a, **kw):
        raise hl.PackUnavailable("bad pack")
    monkeypatch.setattr(hl, "render_cutout", _boom)
    r = offline_client.get("/api/survey/cutout.jpg?ra=5.591&dec=-5.39&fov=2.3")
    assert r.status_code == 503
```

- [ ] **Step 3: Run to verify failures** — `./.venv/Scripts/python.exe -m pytest -q tests/test_survey.py`. Expected: new tests fail (`_pack_cache_key` missing, `X-Survey-Source` missing); legacy tests may also fail until the fixture edit lands — that's fine at this step.

- [ ] **Step 4: Rework `survey.py`.** Imports (top of file): add

```python
from ..config import config_store
from . import hips_local
from . import survey_pack as pack_mod
```

Constants (near `_KEY_SALT`):

```python
_ONLINE_FOV_MAX_DEG = 4.0   # below this the order-4 pack is soft on 768px (spec §4)
_PACK_KEY_SALT = "v2pk"     # pack renders; stretch normalized to "-" (baked-in)
```

New key function (below `_cache_key`):

```python
def _pack_cache_key(idx: tuple[int, int, int], width: int, survey: str) -> str:
    """Disk key for LOCAL pack renders — distinct salt so an upstream upgrade
    is never masked; stretch is normalized ('-') because pack JPEGs bake it in."""
    ra_idx, dec_idx, fov_idx = idx
    raw = f"{_PACK_KEY_SALT}|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|-"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
```

Replace the route body from `key = _cache_key(...)` down (keep signature, clamp, snap; the docstring's flow comment should cite spec §4):

```python
    up_key = _cache_key(idx, width, survey, stretch)
    pk_key = _pack_cache_key(idx, width, survey)
    up_path = _SURVEY_CACHE_DIR / f"{up_key}.jpg"
    pk_path = _SURVEY_CACHE_DIR / f"{pk_key}.jpg"
    cache_headers = {
        "Cache-Control": f"max-age={_CACHE_MAX_AGE}",
        "X-Survey-Ra-Deg": f"{snapped_ra:.6f}",
        "X-Survey-Dec-Deg": f"{snapped_dec:.6f}",
        "X-Survey-Fov-Deg": f"{snapped_fov:.6f}",
    }

    online = bool(config_store.cfg().survey.online_fetch)
    want_upstream = online and snapped_fov < _ONLINE_FOV_MAX_DEG

    def _file(path: Path, source: str) -> FileResponse:
        return FileResponse(path, media_type="image/jpeg",
                            headers={**cache_headers, "X-Survey-Source": source})

    # Fast path: an upstream-quality file always wins; a pack file only when
    # upstream isn't wanted (spec §4 step 2 — upstream gets its upgrade chance).
    if up_path.exists():
        return _file(up_path, "upstream")
    if pk_path.exists() and not want_upstream:
        return _file(pk_path, "pack")

    async with _single_flight(up_key if want_upstream else pk_key):
        if up_path.exists():
            return _file(up_path, "upstream")
        if pk_path.exists() and not want_upstream:
            return _file(pk_path, "pack")

        if want_upstream:
            params = _hips2fits_params(snapped_ra, snapped_dec, snapped_fov, width, survey, stretch)
            try:
                body = await _fetch_cutout(params)
                await asyncio.to_thread(_write_cache, up_path, body)
                return Response(body, media_type="image/jpeg",
                                headers={**cache_headers, "X-Survey-Source": "upstream"})
            except RuntimeError:
                pass  # never 503 while the pack can still render (spec §4 step 4)

        pack = pack_mod.pack_present(survey)
        if pack is not None:
            if pk_path.exists():
                return _file(pk_path, "pack")
            try:
                body = await asyncio.to_thread(
                    hips_local.render_cutout, pack, snapped_ra, snapped_dec, snapped_fov, width)
            except Exception:  # noqa: BLE001 — any render failure falls through
                body = None
            if body:
                await asyncio.to_thread(_write_cache, pk_path, body)
                return Response(body, media_type="image/jpeg",
                                headers={**cache_headers, "X-Survey-Source": "pack"})

        if online and not want_upstream:
            # fov >= 4° and the pack failed — upstream as last resort (spec §4 step 6)
            params = _hips2fits_params(snapped_ra, snapped_dec, snapped_fov, width, survey, stretch)
            try:
                body = await _fetch_cutout(params)
                await asyncio.to_thread(_write_cache, up_path, body)
                return Response(body, media_type="image/jpeg",
                                headers={**cache_headers, "X-Survey-Source": "upstream"})
            except RuntimeError:
                pass

    raise HTTPException(
        status_code=503,
        detail={"detail": "survey unavailable", "fallback": "schematic"},
    )
```

- [ ] **Step 5: Apply the fixture/helper edits from Step 2, run the whole file** — `./.venv/Scripts/python.exe -m pytest -q tests/test_survey.py`. Expected: ALL pass (legacy 16 + new 8). If a legacy test fails, the cause is almost always the fixture not setting `online_fetch=True` or `PACK_ROOT` unpatched.
- [ ] **Step 6: Quick regression sweep of neighbors** — `./.venv/Scripts/python.exe -m pytest -q tests/test_survey.py tests/test_survey_pack_fetch.py tests/test_hips_local.py`. Expected: all pass.
- [ ] **Step 7: Commit**

```bash
git add server/astrodeck/config.py server/astrodeck/catalog/survey.py server/tests/test_survey.py
git commit -m "feat(survey): offline-first routing — online_fetch config (default off), 4deg threshold, v2pk pack cache, X-Survey-Source (spec §4)"
```

---

### Task 4: API routes — `POST /api/config/survey` + pack GET/POST/DELETE

**Files:**
- Modify: `server/astrodeck/api/app.py` (imports, body model, 4 routes near the drivers/rotator routes at app.py:717-845)
- Test: `server/tests/test_survey_pack_api.py`

**Interfaces:**
- Consumes: `SurveyConfig`/`config_store.set_survey` (Task 3); `survey_pack` module functions (Task 1); `CAP_CONFIG_SITE_OPTICS`, `CAP_VIEW_STATUS`, `require`, `declare` (seam §2).
- Produces: routes exactly as spec §5; UI (Tasks 5-6) consumes their JSON shapes.

- [ ] **Step 1: Read the rotator config route** (`app.py:731` area) and note what it returns — the new `POST /api/config/survey` must mirror its return payload convention exactly (config payload vs the block). Read the drivers routes (app.py:717-845) for the `@declare`/`require` placement.

- [ ] **Step 2: Write the failing tests** — `server/tests/test_survey_pack_api.py`. Fixture per the repo's `create_app()` convention (seam §8, `test_drivers_api.py` shape) — full file:

```python
"""API tests for /api/config/survey + /api/survey/pack* (offline-pack spec §5)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.catalog.survey_pack as sp
    monkeypatch.setattr(sp, "PACK_ROOT", tmp_path / "_survey_pack")
    sp.fetch_state.finish()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_get_pack_status_shape_absent(client):
    r = client.get("/api/survey/pack")
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is False and body["fetching"] is None
    assert body["survey"] == "CDS/P/DSS2/color" and body["slug"] == "dss2color"


def test_config_survey_roundtrip(client):
    assert client.get("/api/config").json()["survey"] == {"online_fetch": False}
    r = client.post("/api/config/survey", json={"online_fetch": True})
    assert r.status_code == 200
    assert client.get("/api/config").json()["survey"] == {"online_fetch": True}


def test_fetch_starts_and_reports_already(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    started = {}
    def fake_start(order=4, slug="dss2color"):
        if started:
            raise sp.FetchAlreadyRunning()
        started["order"] = order
    monkeypatch.setattr(sp, "start_fetch", fake_start)
    r = client.post("/api/survey/pack/fetch", json={"order": 3})
    assert r.status_code == 202 and r.json() == {"started": True}
    assert started["order"] == 3
    r = client.post("/api/survey/pack/fetch", json={"order": 3})
    assert r.status_code == 200 and r.json() == {"started": False, "already": True}


def test_fetch_order_clamped(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    seen = {}
    monkeypatch.setattr(sp, "start_fetch",
                        lambda order=4, slug="dss2color": seen.setdefault("o", order))
    client.post("/api/survey/pack/fetch", json={"order": 99})
    assert seen["o"] == 6


def test_fetch_507_on_insufficient_space(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    def no_space(order=4, slug="dss2color"):
        raise sp.InsufficientSpace(free=1000, required=9999)
    monkeypatch.setattr(sp, "start_fetch", no_space)
    r = client.post("/api/survey/pack/fetch", json={})
    assert r.status_code == 507
    assert r.json()["detail"] == {"detail": "insufficient disk space",
                                  "free_bytes": 1000, "required_bytes": 9999}


def test_delete_pack_and_409_while_running(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    r = client.delete("/api/survey/pack")
    assert r.status_code == 200 and r.json() == {"deleted": False}
    assert sp.fetch_state.try_start(5)
    try:
        r = client.delete("/api/survey/pack")
        assert r.status_code == 409
    finally:
        sp.fetch_state.finish()
```

- [ ] **Step 3: Run to verify failure** — `./.venv/Scripts/python.exe -m pytest -q tests/test_survey_pack_api.py`. Expected: 404s / missing-route failures (fixture boots the real app fine).

- [ ] **Step 4: Implement the routes in `app.py`.** Imports: extend the existing `from ..config import ...` line with `SurveyConfig`; add `from ..catalog import survey_pack as survey_pack_mod`. Body model near the other request models:

```python
class PackFetchBody(BaseModel):
    order: int = 4
```

Routes (place beside the rotator config route; keep the file's decorator style; if the rotator route returns something other than shown here, mirror THE ROTATOR ROUTE — that convention wins over this sketch):

```python
    @app.post("/api/config/survey",
              summary="Set the Sky-Atlas survey source (offline-pack spec §4)")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_survey_config(
        body: SurveyConfig,
        principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS)),
    ):
        config_store.set_survey(body)
        return _config_payload()

    @app.get("/api/survey/pack",
             summary="Offline sky-pack status + fetch progress (spec §5)")
    @declare(CAP_VIEW_STATUS)
    async def get_survey_pack(
        principal: Principal = Depends(require(CAP_VIEW_STATUS)),
    ):
        return survey_pack_mod.pack_status()

    @app.post("/api/survey/pack/fetch",
              summary="Start/resume the offline sky-pack download (spec §5)")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def start_survey_pack_fetch(
        body: PackFetchBody | None = None,
        principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS)),
    ):
        order = max(1, min(6, body.order if body is not None else 4))
        try:
            survey_pack_mod.start_fetch(order=order)
        except survey_pack_mod.FetchAlreadyRunning:
            return JSONResponse({"started": False, "already": True}, status_code=200)
        except survey_pack_mod.InsufficientSpace as exc:
            raise HTTPException(status_code=507, detail={
                "detail": "insufficient disk space",
                "free_bytes": exc.free, "required_bytes": exc.required})
        return JSONResponse({"started": True}, status_code=202)

    @app.delete("/api/survey/pack",
                summary="Delete the offline sky pack (spec §5)")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def delete_survey_pack(
        principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS)),
    ):
        try:
            removed = survey_pack_mod.remove_pack()
        except survey_pack_mod.FetchAlreadyRunning:
            raise HTTPException(status_code=409,
                                detail={"detail": "pack fetch in progress"})
        return {"deleted": removed}
```

Note: the test monkeypatches `sp.start_fetch` — so app.py must call it as `survey_pack_mod.start_fetch(...)` (attribute lookup at call time), NOT `from ...survey_pack import start_fetch` (which would bind the original). Same for `remove_pack`/`pack_status`. Confirm `JSONResponse` is already imported in app.py (it is used by existing routes; if not, add it to the fastapi.responses import).

- [ ] **Step 5: Run to verify pass** — `./.venv/Scripts/python.exe -m pytest -q tests/test_survey_pack_api.py`. Expected: 6 passed (boot audit passing proves the `@declare`s are right).
- [ ] **Step 6: Commit**

```bash
git add server/astrodeck/api/app.py server/tests/test_survey_pack_api.py
git commit -m "feat(api): survey config route + pack status/fetch/delete with RBAC, 507 ENOSPC, 409 while fetching (spec §5)"
```

---

### Task 5: UI — types, API client, Settings "Sky Atlas" card

**Files:**
- Modify: `ui/src/types.ts` (new banner section + AppConfig field)
- Modify: `ui/src/api/backends.ts` (4 typed calls)
- Create: `ui/src/components/settings/skyAtlasMeta.ts` (pure helpers)
- Create: `ui/src/components/settings/SkyAtlasPanel.tsx`
- Modify: `ui/src/components/settings/SettingsView.tsx` (mount in the connect tab under `<DriversPanel />`, seam §1)
- Test: `ui/src/lib/__tests__/skyAtlasMeta.test.ts`

**Interfaces:**
- Consumes: `api`/`ApiError` (ui/src/api.ts), `useConfig`/`useStore` (store.ts), `Panel`/`Toggle` (components/ui), `confirmDialog` (components/ConfirmDialog), server shapes from Task 4.
- Produces: `SurveyConfig`/`PackStatus`/`PackFetchProgress` types; `setSurveyConfig`, `getPackStatus`, `startPackFetch`, `deletePack` in api/backends.ts; `packStatusLabel(s: PackStatus | null): string`, `packProgressPct(f: PackFetchProgress): number` in skyAtlasMeta.ts (Task 6 reuses nothing from here except types).

- [ ] **Step 1: types.ts** — new banner section (near the drivers section) + AppConfig field after `rotator?`:

```ts
// ------------------------------------------------ offline survey pack (spec 2026-07-13)
export interface SurveyConfig {
  /** True => hips2fits upstream is used for FOV < 4°; false (default) => pack only. */
  online_fetch: boolean;
}
export interface PackFetchProgress { done: number; total: number; failed: number; }
export interface PackStatus {
  present: boolean;
  slug: string;
  survey: string;
  order: number | null;
  bytes: number | null;
  tile_count: number | null;
  fetched_at: number | null;
  fetching: PackFetchProgress | null;
}
```

and in `AppConfig`: `survey?: SurveyConfig;`

- [ ] **Step 2: api/backends.ts** — four wrappers in the file's JSDoc convention:

```ts
/** POST /api/config/survey → config payload. config.site_optics. */
export const setSurveyConfig = (survey: SurveyConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/survey", survey);

/** GET /api/survey/pack → offline pack status + fetch progress. view.status. */
export const getPackStatus = (): Promise<PackStatus> =>
  api.get<PackStatus>("/api/survey/pack");

/** POST /api/survey/pack/fetch → 202 {started} | 200 {already} | 507 no space. config.site_optics. */
export const startPackFetch = (order = 4): Promise<{ started: boolean; already?: boolean }> =>
  api.post<{ started: boolean; already?: boolean }>("/api/survey/pack/fetch", { order });

/** DELETE /api/survey/pack → {deleted}. 409 while a fetch runs. config.site_optics. */
export const deletePack = (): Promise<{ deleted: boolean }> =>
  api.del<{ deleted: boolean }>("/api/survey/pack");
```

(import `SurveyConfig`, `PackStatus` types at the top with the existing type imports.)

- [ ] **Step 3: skyAtlasMeta.ts + its failing test.** Helpers:

```ts
// skyAtlasMeta.ts — pure helpers for the Settings "Sky Atlas" card (offline-pack spec §6).
import type { PackFetchProgress, PackStatus } from "../../types";

export function packProgressPct(f: PackFetchProgress): number {
  return f.total > 0 ? Math.min(100, Math.round((100 * f.done) / f.total)) : 0;
}

export function packStatusLabel(s: PackStatus | null): string {
  if (!s) return "Offline sky pack: checking…";
  if (s.fetching) return `Offline sky pack: downloading ${s.fetching.done}/${s.fetching.total}…`;
  if (!s.present) return "Offline sky pack: not downloaded";
  const mb = s.bytes != null ? `${Math.round(s.bytes / 1048576)} MB` : "size unknown";
  const when = s.fetched_at != null
    ? new Date(s.fetched_at * 1000).toISOString().slice(0, 10) : "unknown date";
  return `Offline sky pack: ${mb}, order ${s.order ?? "?"}, fetched ${when}`;
}
```

Test `ui/src/lib/__tests__/skyAtlasMeta.test.ts` in the repo's self-executing harness (copy the `test()`/tally scaffold from `drivers.test.ts`, seam §7): assert `packProgressPct({done:0,total:0,failed:0}) === 0`, `packProgressPct({done:2046,total:4092,failed:0}) === 50`, label for null → "checking…", label while fetching contains "downloading 3/12", label absent → "not downloaded", label present with `bytes: 262144000, order: 4, fetched_at: 1786000000` contains "250 MB" and "order 4". Run `npx tsx src/lib/__tests__/skyAtlasMeta.test.ts` from `ui/` — must fail before the helper exists, pass after.

- [ ] **Step 4: SkyAtlasPanel.tsx** — full component (mirror SafetyPanel's toggle-persist + DriversPanel's busy/`run` idioms, seams §1-2):

```tsx
// SkyAtlasPanel.tsx — Settings → "Sky Atlas" (offline-pack spec §6).
// Online-fetch toggle (POST /api/config/survey) + offline pack
// status/download/delete (GET/POST/DELETE /api/survey/pack*). Progress polls
// every 2s only while the card is mounted AND a fetch is running.
import { useEffect, useState, type JSX } from "react";
import type { PackStatus } from "../../types";
import { deletePack, getPackStatus, setSurveyConfig, startPackFetch } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { confirmDialog } from "../ConfirmDialog";
import { Panel, Toggle } from "../ui";
import { packProgressPct, packStatusLabel } from "./skyAtlasMeta";

const POLL_MS = 2000;

export default function SkyAtlasPanel(): JSX.Element {
  const config = useConfig();
  const showToast = useStore((s) => s.showToast);
  const onlineFetch = config?.survey?.online_fetch ?? false;
  const [status, setStatus] = useState<PackStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastFailed, setLastFailed] = useState(0);

  const reload = async () => {
    try {
      const s = await getPackStatus();
      setStatus(s);
      if (s.fetching) setLastFailed(s.fetching.failed);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "couldn't load pack status");
    }
  };
  useEffect(() => { void reload(); }, []);

  // 2s progress poll, only while a fetch runs (GuideFramePreview idiom).
  const fetching = !!status?.fetching;
  useEffect(() => {
    if (!fetching) return;
    const id = window.setInterval(() => { void reload(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [fetching]);

  const toggleOnline = async (v: boolean) => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await setSurveyConfig({ online_fetch: v });
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? "config.site_optics required to change survey settings" : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  const download = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await startPackFetch();
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 507
        ? "Not enough space on the capture volume for the pack."
        : e instanceof Error ? e.message : "download failed");
    } finally { setBusy(false); }
  };

  const remove = () => void (async () => {
    const ok = await confirmDialog({
      title: "Delete offline sky pack?",
      body: "The Atlas will have no survey imagery until it's downloaded again (or online fetch is enabled).",
      tone: "danger",
      confirmLabel: "Delete pack",
    });
    if (!ok) return;
    setBusy(true);
    setErr(null);
    try {
      await deletePack();
      await reload();
      showToast("success", "offline sky pack deleted");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "delete failed");
    } finally { setBusy(false); }
  })();

  const f = status?.fetching ?? null;
  const showRetryHint = (f && f.failed > 0) || (!f && !status?.present && lastFailed > 0);
  return (
    <Panel title="Sky Atlas">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className="label">Online survey fetch (CDS)</span>
          <Toggle checked={onlineFetch} onChange={(v) => void toggleOnline(v)}
                  disabled={busy} label="Online survey fetch" showState />
        </div>
        <p className="text-[12px] text-dim">
          When on, small fields load full-resolution imagery from CDS; the offline pack remains the fallback.
        </p>
        <div className="text-[12px] mono text-ink">{packStatusLabel(status)}</div>
        {f && (
          <div className="h-2 bg-black/30 border border-line2" role="progressbar"
               aria-valuenow={packProgressPct(f)} aria-valuemin={0} aria-valuemax={100}
               aria-label="Pack download progress">
            <div className="h-full bg-[var(--accent)]" style={{ width: `${packProgressPct(f)}%` }} />
          </div>
        )}
        <div className="flex gap-2 flex-wrap">
          <button type="button" className="btn btn-touch" onClick={() => void download()}
                  disabled={busy || !!f}>
            {status?.present ? "Update offline sky pack" : "Download offline sky pack (~250 MB)"}
          </button>
          {status?.present && (
            <button type="button" className="btn btn-touch text-warn" onClick={remove}
                    disabled={busy || !!f}>
              Delete pack
            </button>
          )}
        </div>
        {showRetryHint && (
          <p className="text-[12px] text-warn">
            Some tiles failed — running the download again resumes and retries them.
          </p>
        )}
        {err && <p className="text-[12px] text-warn">{err}</p>}
        <p className="text-[11px] text-dim">DSS2 imagery © AAO/STScI, served from CDS/ESA HiPS mirrors.</p>
      </div>
    </Panel>
  );
}
```

- [ ] **Step 5: Mount it** — in `SettingsView.tsx`'s connect tab, directly below `<DriversPanel />` (same column): `<SkyAtlasPanel />` (+ import).
- [ ] **Step 6: Build + tests** — from `ui/`: `npm run build` (expected: clean) and `npx tsx src/lib/__tests__/skyAtlasMeta.test.ts` (expected: all pass, non-zero exit if any fail).
- [ ] **Step 7: Commit**

```bash
git add ui/src/types.ts ui/src/api/backends.ts ui/src/components/settings/skyAtlasMeta.ts ui/src/components/settings/SkyAtlasPanel.tsx ui/src/components/settings/SettingsView.tsx ui/src/lib/__tests__/skyAtlasMeta.test.ts
git commit -m "feat(ui/settings): Sky Atlas card — online-fetch toggle, pack download with progress + 507 copy, delete (spec §6)"
```

---

### Task 6: UI — survey picker gating, fetching-aware Atlas banner, README

**Files:**
- Modify: `ui/src/components/atlas/SurveyControls.tsx` (new prop + gating)
- Modify: `ui/src/components/atlas/SkyCanvas.tsx` (optional `degradedText` prop)
- Modify: `ui/src/views/AtlasView.tsx` (thread `onlineFetch`, pack-status poll, banner copy)
- Modify: `README.md` (short "Offline sky pack" section)

**Interfaces:**
- Consumes: `getPackStatus` + `PackStatus` (Task 5), `useConfig` (already imported in AtlasView, seam §5), existing `surveyDegraded` machinery (seam §4).
- Produces: `SurveyControlsProps.onlineFetch: boolean`; `SkyCanvas` prop `degradedText?: string`.

- [ ] **Step 1: SurveyControls.tsx.** Add to `SurveyControlsProps`: `/** config.survey.online_fetch — gates online-only surveys + stretch. */ onlineFetch: boolean;` (destructure it). Replace the options map and gate the stretch buttons:

```tsx
{SURVEYS.map((s) => {
  const needsOnline = s.id === "CDS/P/DSS2/red" || s.id === "CDS/P/2MASS/color";
  const off = needsOnline && !onlineFetch;
  return (
    <option key={s.id} value={s.id} disabled={off}>
      {s.label}{off ? " (online only)" : ""}
    </option>
  );
})}
```

Both stretch buttons get `disabled={!onlineFetch}` and `title="Stretch applies to online imagery — pack tiles are pre-stretched"` (keep the existing className/aria-pressed logic).

- [ ] **Step 2: SkyCanvas.tsx.** Add prop after `surveyDegraded`:

```ts
/** Replacement copy for the degraded banner (offline-pack spec §6). */
degradedText?: string;
```

In the banner block (seam §4, SkyCanvas.tsx:533-543) render `{degradedText ?? <>⚠ Survey unreachable — {shownUrl ? "showing the last image" : "schematic framing"}; retrying automatically.</>}`, and mirror the same `degradedText ??` fallback in the visually-hidden `role="status"` region (SkyCanvas.tsx:435-443) so screen readers hear the same copy.

- [ ] **Step 3: AtlasView.tsx.** Near the existing `surveyDegraded` state (AtlasView.tsx:148-163) add:

```tsx
const onlineFetch = config?.survey?.online_fetch ?? false;
const [packStatus, setPackStatus] = useState<PackStatus | null>(null);
// Poll pack status every 2s only while degraded with online fetch off — the
// only state in which the banner copy depends on it (offline-pack spec §6).
useEffect(() => {
  if (!surveyDegraded || onlineFetch) return;
  let live = true;
  const tick = () => {
    getPackStatus().then((p) => { if (live) setPackStatus(p); }).catch(() => {});
  };
  tick();
  const id = window.setInterval(tick, 2000);
  return () => { live = false; window.clearInterval(id); };
}, [surveyDegraded, onlineFetch]);

const degradedText =
  surveyDegraded && !onlineFetch && packStatus && !packStatus.present
    ? packStatus.fetching
      ? `Downloading offline sky pack… ${packStatus.fetching.done}/${packStatus.fetching.total}`
      : "No survey source — download the offline sky pack in Settings, or enable online fetch."
    : undefined;
```

(imports: `getPackStatus` from `../api/backends`, `PackStatus` type from `../types`.) Pass `degradedText={degradedText}` to `<SkyCanvas …/>` and `onlineFetch={onlineFetch}` to `<SurveyControls …/>` (mount sites at AtlasView.tsx:716-750, seam §5). No completion wiring: when the pack lands, SkyCanvas's backoff retry succeeds and `onSurveyLoad` clears the degraded flag.

- [ ] **Step 4: README** — add a short section after the existing setup content:

```markdown
## Offline sky pack

The Atlas renders survey imagery from a local DSS2 tile pack — no internet
needed at the scope. Download it once (~250 MB) from **Settings → Sky Atlas →
Download offline sky pack**, or via CLI:

    cd server && python -m astrodeck.catalog.survey_pack fetch

Small fields render soft from the pack (26″/px). For full-resolution deep
zooms, enable **Settings → Sky Atlas → Online survey fetch (CDS)** — the pack
remains the automatic fallback whenever the CDS service is unreachable.
DSS2 imagery © AAO/STScI, fetched from public CDS/ESA HiPS mirrors.
```

- [ ] **Step 5: Build + full tsx tests** — from `ui/`: `npm run build`, then run all 6 test files: `for f in src/lib/__tests__/*.test.ts; do npx tsx "$f" || exit 1; done` (bash). Expected: clean build, all asserts pass.
- [ ] **Step 6: Commit**

```bash
git add ui/src/components/atlas/SurveyControls.tsx ui/src/components/atlas/SkyCanvas.tsx ui/src/views/AtlasView.tsx README.md
git commit -m "feat(ui/atlas): online-only survey gating, fetching-aware empty-state banner, offline-pack README (spec §6)"
```

---

## Post-plan verification (controller, not a task)

1. Full server suite from `server/`: `./.venv/Scripts/python.exe -m pytest -q` (~12 min; expect prior 942 + ~29 new, 0 failures).
2. Push + `gh run watch` per grounded-CI rule.
3. Restart :8801, complete the pack to order 4 (`fetch --order 4`, resumes over Task 2's order-3 seed), verify the Atlas renders offline with the toggle off, and hand the user the manual smoke list (Settings download UX, banner states, online toggle upgrade).

## Known-minor (accepted, do not fix in this plan)

- If the selected survey is DSS2 red / 2MASS when `online_fetch` is turned off, the picker shows a disabled selected option and the Atlas degrades until the user picks DSS2 color — acceptable; the banner explains the state.
- `start_fetch`'s `try_start` total and `fetch_pack`'s recomputed todo could differ if tiles appear between the two scans — nothing else writes tiles, comment documents it.
