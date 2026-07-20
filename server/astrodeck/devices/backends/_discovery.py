"""Entry-point discovery for third-party device backends.

External packages declare ``[project.entry-points."astrodeck.backends"]`` whose
values resolve to a zero-argument callable that calls ``register(MyBackend())``
(mirroring the built-ins' self-registration idiom). Discovery is guarded per
entry point -- a broken or incompatible plugin is recorded, never fatal -- and
refuses to overwrite a built-in backend name.
"""
from __future__ import annotations

import importlib.metadata as md

from ..backend import BACKENDS

#: Load outcomes for the UI/API: {name, dist, version, status, detail}.
#: status is one of "loaded" | "failed" | "incompatible".
_REPORT: list[dict] = []


def plugin_load_report() -> list[dict]:
    """A JSON-able copy of the last discovery run's outcomes."""
    return [dict(r) for r in _REPORT]


def _too_new(min_app: str, app_version: str) -> bool:
    """True when the app is OLDER than a plugin's ``min_app_version``."""
    try:
        from packaging.version import Version
        return Version(str(min_app)) > Version(str(app_version))
    except Exception:  # noqa: BLE001 - unparseable version -> do not gate
        return False


def discover_plugin_backends(*, group: str = "astrodeck.backends",
                             app_version: str | None = None) -> None:
    """Discover and register external backends from ``group``. Guarded per entry
    point; rebuilds the report each run (safe to call again in tests)."""
    if app_version is None:
        from ... import __version__ as app_version  # astrodeck.__version__

    _REPORT.clear()
    builtin_names = set(BACKENDS)
    try:
        eps = md.entry_points(group=group)
    except Exception as exc:  # noqa: BLE001 - a bad environment must not break import
        _REPORT.append({"name": None, "dist": None, "version": None,
                        "status": "failed",
                        "detail": f"entry-point scan failed: {exc}"[:300]})
        return

    for ep in eps:
        dist = getattr(getattr(ep, "dist", None), "name", None)
        before = dict(BACKENDS)
        try:
            fn = ep.load()
            fn()
        except Exception as exc:  # noqa: BLE001 - never let one plugin break startup
            BACKENDS.clear()
            BACKENDS.update(before)                    # undo any partial mutation
            _REPORT.append({"name": ep.name, "dist": dist, "version": None,
                            "status": "failed", "detail": str(exc)[:300]})
            continue

        overwritten = [n for n in builtin_names
                       if BACKENDS.get(n) is not before.get(n)]
        added = [n for n in BACKENDS if n not in before]
        if overwritten:
            for n in overwritten:                      # restore protected built-ins
                BACKENDS[n] = before[n]
            for n in added:                            # drop the misbehaving plugin
                BACKENDS.pop(n, None)
            _REPORT.append({"name": ep.name, "dist": dist, "version": None,
                            "status": "failed",
                            "detail": f"refused: overwrites built-in backend(s) "
                                      f"{sorted(overwritten)}"})
            continue

        if not added:
            _REPORT.append({"name": ep.name, "dist": dist, "version": None,
                            "status": "loaded",
                            "detail": "registered no new backend"})
            continue

        for n in added:
            b = BACKENDS[n]
            ver = str(getattr(b, "version", "0"))
            minv = str(getattr(b, "min_app_version", "0"))
            if _too_new(minv, app_version):
                BACKENDS.pop(n, None)
                _REPORT.append({"name": n, "dist": dist, "version": ver,
                                "status": "incompatible",
                                "detail": f"requires app >= {minv} "
                                          f"(have {app_version})"})
            else:
                _REPORT.append({"name": n, "dist": dist, "version": ver,
                                "status": "loaded", "detail": None})
