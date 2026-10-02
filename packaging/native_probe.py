"""Deterministic native packaging probe; never import application state."""
from __future__ import annotations
import importlib.metadata as metadata
import json


def probe_native() -> dict:
    import numpy as np
    import astrodeck_native as native
    distribution = metadata.distribution("astrodeck-native")
    manifest_text = distribution.read_text("astrodeck-build.json")
    if not manifest_text:
        raise RuntimeError("native build provenance is missing")
    manifest = json.loads(manifest_text)
    if native.__version__ != distribution.version or distribution.version != manifest.get("native_version"):
        raise RuntimeError("native module and metadata versions differ")
    if not manifest.get("native_source_sha256") or not manifest.get("source_commit"):
        raise RuntimeError("native source provenance is incomplete")
    # Runs real Rust detection and the NumPy boundary, without a camera or rig.
    stars, stats = native.detect_and_measure(np.zeros((32, 32), dtype=np.uint16))
    if len(stars) != 0 or stats.get("star_count") != 0:
        raise RuntimeError("native synthetic blank-frame result is incorrect")
    return {"native_available": True, "native_version": distribution.version,
            "source_commit": manifest["source_commit"],
            "native_source_sha256": manifest["native_source_sha256"],
            "probe": "blank-32x32-uint16", "star_count": stats["star_count"]}


def main() -> int:
    try:
        result = probe_native()
    except Exception as exc:
        # No raw exception or import search path can leak build/user state.
        print(json.dumps({"native_available": False, "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
