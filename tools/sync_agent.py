#!/usr/bin/env python3
"""Pull a night off an AstroDeck rig, incrementally, while it is still running.

Run this on the machine that has PixInsight. It asks the rig what it holds,
compares that against what is already in the destination directory, and fetches
the difference — oldest frame first, so a filter's early subs form a usable
contiguous set long before the night ends.

    python sync_agent.py --base http://astrotown:8800 \\
                         --token "$AD_TOKEN" \\
                         --dest D:/astro/incoming \\
                         --night 2026-08-09 --watch

STDLIB ONLY, on purpose. This runs on someone else's workstation, and a sync
tool that needs a pip install before it can rescue a night is a sync tool that
does not get used. Python 3.9+ and nothing else.

THE DESTINATION WRITE IS ATOMIC EVEN THOUGH THE SOURCE'S IS NOT. Bytes land in
``<name>.part``, are hashed, and only then ``os.replace``d onto the real name —
so a file at its final path in the destination is always complete, whatever
happened to the network or this process. That is precisely the property the rig
itself lacks (``save_fits`` streams into the final filename, which is why the
manifest makes you wait out a settle window), and it is worth fixing here
rather than inheriting: PixInsight, WBPP, or a directory watcher pointed at the
destination must never see a half file.

NOTHING IS EVER DELETED. Not from the rig, not from the destination. Files the
rig does not have are reported as ``extra`` and left alone, because that is
where your masters, crops and experiments live.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024
HASH_ALGO = "sha256"


def _get(base: str, path: str, token: str, params: dict | None = None,
         *, range_from: int = 0, timeout: float = 120.0):
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    # Both carriers: a session cookie is what the browser flow mints, and the
    # bearer header is what a scripted caller usually has. Sending both costs
    # nothing and saves the operator guessing which one this rig wants.
    req.add_header("Cookie", f"ad_session={token}")
    req.add_header("Authorization", f"Bearer {token}")
    if range_from > 0:
        req.add_header("Range", f"bytes={range_from}-")
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_manifest(base: str, token: str, night: str) -> dict:
    params = {"night": night} if night else {}
    with _get(base, "/api/sync/manifest", token, params) as r:
        return json.loads(r.read().decode("utf-8"))


def hash_file(path: Path) -> str:
    h = hashlib.new(HASH_ALGO)
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def local_index(dest: Path, cache: dict) -> dict[str, str]:
    """relpath -> sha256 for everything already in the destination.

    Cached on (size, mtime_ns) so a six-hour watch does not re-read 9 GB every
    poll. The cache is a pure accelerator: delete it and the next pass simply
    reads more, and reaches the identical answer. Nothing here is authoritative
    state — that is the whole design, and it is what makes killing this process
    at any moment free.
    """
    out: dict[str, str] = {}
    for dirpath, _dirs, names in os.walk(dest):
        for name in names:
            if name.endswith(".part"):
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(dest).as_posix()
            key = (rel, st.st_size, st.st_mtime_ns)
            digest = cache.get(key)
            if digest is None:
                try:
                    digest = hash_file(p)
                except OSError:
                    continue
                cache[key] = digest
            out[rel] = digest
    return out


def download(base: str, token: str, entry: dict, dest: Path) -> tuple[bool, str]:
    """Fetch one file. Returns (ok, detail). Verifies before it commits."""
    rel = entry["relpath"]
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")

    have = part.stat().st_size if part.exists() else 0
    mode = "ab" if have else "wb"
    try:
        resp = _get(base, "/api/gallery/file", token, {"path": rel},
                    range_from=have)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:                      # noqa: BLE001 - report, continue
        return False, str(e)

    with resp:
        # A server that ignored the Range header answers 200 with the WHOLE
        # file. Appending that to a partial would silently produce a file of the
        # right name and the wrong length, so start clean instead.
        if have and resp.status != 206:
            mode, have = "wb", 0
        with open(part, mode) as fh:
            while True:
                block = resp.read(CHUNK)
                if not block:
                    break
                fh.write(block)
            fh.flush()
            os.fsync(fh.fileno())

    got = hash_file(part)
    if got != entry["sha256"]:
        # Leave nothing plausible-looking behind: a wrong-hash .part that
        # survived would be resumed from next pass and never converge.
        part.unlink(missing_ok=True)
        return False, f"hash mismatch (want {entry['sha256'][:12]}, got {got[:12]})"
    os.replace(part, target)
    return True, "ok"


def one_pass(base: str, token: str, night: str, dest: Path, cache: dict,
             *, dry_run: bool = False) -> dict:
    man = fetch_manifest(base, token, night)
    remote = {e["relpath"]: e for e in man.get("entries", [])}
    have = local_index(dest, cache)

    missing = [e for r, e in remote.items() if r not in have]
    stale = [e for r, e in remote.items()
             if r in have and have[r] != e["sha256"]]
    extra = sorted(set(have) - set(remote))
    todo = sorted(missing + stale, key=lambda e: (e.get("mtime_ns", 0), e["relpath"]))

    print(f"  rig has {len(remote)} files, {man.get('bytes', 0)/1e9:.2f} GB"
          f" | here {len(have)} | to fetch {len(todo)}"
          f" | not yet settled {len(man.get('unsettled', []))}"
          f" | only here {len(extra)}")
    if dry_run or not todo:
        return {"fetched": 0, "failed": 0, "todo": len(todo)}

    fetched = failed = 0
    for e in todo:
        t0 = time.time()
        ok, detail = download(base, token, e, dest)
        if ok:
            fetched += 1
            mb = e["size"] / 1e6
            dt = max(time.time() - t0, 1e-6)
            print(f"    + {e['relpath']}  {mb:.0f} MB in {dt:.1f}s ({mb/dt:.0f} MB/s)")
        else:
            failed += 1
            # Not fatal and not remembered: a file that failed simply stays in
            # the next diff. There is no retry counter to get wrong.
            print(f"    ! {e['relpath']}: {detail}", file=sys.stderr)
    return {"fetched": fetched, "failed": failed, "todo": len(todo)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="e.g. http://astrotown:8800")
    ap.add_argument("--token", default=os.environ.get("AD_TOKEN", ""),
                    help="session token (or set AD_TOKEN)")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--night", default="", help="YYYY-MM-DD; omit for everything")
    ap.add_argument("--watch", action="store_true", help="keep polling")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.token:
        print("no token: pass --token or set AD_TOKEN", file=sys.stderr)
        return 2
    args.dest.mkdir(parents=True, exist_ok=True)
    cache: dict = {}

    while True:
        stamp = time.strftime("%H:%M:%S")
        try:
            print(f"[{stamp}] sync")
            one_pass(args.base, args.token, args.night, args.dest, cache,
                     dry_run=args.dry_run)
        except urllib.error.HTTPError as e:
            print(f"[{stamp}] rig said HTTP {e.code} "
                  f"({'token expired?' if e.code == 401 else e.reason})",
                  file=sys.stderr)
        except Exception as e:                  # noqa: BLE001 - a watch must not die
            print(f"[{stamp}] {e}", file=sys.stderr)
        if not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
