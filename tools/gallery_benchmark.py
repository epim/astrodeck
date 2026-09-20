"""Synthetic-only gallery benchmark. Run with server on PYTHONPATH.

python tools/gallery_benchmark.py --frames 5000
Never reads a configured capture library or connects equipment.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def scan(root):
    from astrodeck import gallery
    original = gallery._read_header_meta
    reads = 0
    def counted(path):
        nonlocal reads
        reads += 1
        return original(path)
    gallery._read_header_meta = counted
    started = time.perf_counter()
    rows, _ = gallery.scan(root)
    elapsed = time.perf_counter() - started
    gallery._read_header_meta = original
    return {"frames": len(rows), "ms": round(elapsed * 1000, 1), "headers_read": reads}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=5000)
    parser.add_argument("--scan", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.scan:
        print(json.dumps(scan(args.scan)))
        return
    if not 1 <= args.frames <= 200000:
        parser.error("frames must be between 1 and 200000")
    import numpy as np
    from astropy.io import fits
    from astrodeck import gallery, gallery_listing, hub
    from astrodeck.imaging import processing
    with tempfile.TemporaryDirectory(prefix="astrodeck-gallery-benchmark-") as temporary:
        root = Path(temporary)
        image = fits.PrimaryHDU(np.zeros((16, 16), dtype=np.uint16))
        for key, value in dict(OBJECT="Synthetic target", FILTER="L", IMAGETYP="Light", XBINNING=1, YBINNING=1, EXPTIME=60).items():
            image.header[key] = value
        buffer = io.BytesIO()
        image.writeto(buffer)
        raw = buffer.getvalue()
        for i in range(args.frames):
            (root / f"frame-{i:06d}.fits").write_bytes(raw)
        hub.CAPTURE_DIR = root
        result = {"initial_index": scan(root)}
        restarted = subprocess.run([sys.executable, __file__, "--scan", str(root)], check=True, capture_output=True, text=True)
        result["after_process_restart"] = json.loads(restarted.stdout)
        started = time.perf_counter()
        first = gallery_listing.page(limit=200)
        result["first_page_ms"] = round((time.perf_counter() - started) * 1000, 1)
        if first["next_cursor"]:
            started = time.perf_counter()
            second = gallery_listing.page(limit=200, cursor=first["next_cursor"])
            result["next_page_ms"] = round((time.perf_counter() - started) * 1000, 1)
            assert not {r["path"] for r in first["frames"]} & {r["path"] for r in second["frames"]}
        started = time.perf_counter()
        assert gallery_listing.page(q="frame-000000")["total"] == 1
        result["one_file_search_ms"] = round((time.perf_counter() - started) * 1000, 1)
        calls = []
        original = processing.to_thumb
        def render(*a, **kw):
            calls.append(1)
            time.sleep(.05)
            return original(*a, **kw)
        processing.to_thumb = render
        with ThreadPoolExecutor(max_workers=8) as workers:
            outputs = list(workers.map(lambda _: gallery.thumbnail("frame-000000.fits"), range(8)))
        assert all(data == outputs[0] for data in outputs)
        result["eight_identical_requests_render_count"] = len(calls)
        processing.to_thumb = original
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
