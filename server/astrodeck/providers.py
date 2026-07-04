"""Per-capability provider resolution — "who performs capability X for this rig?"

AstroDeck drives three classes of rig (NINA bridge, native Alpaca/sim, future
ASIAIR) and each should use its own best tooling. This module answers, *per
capability* (not per rig), which implementation runs a given capability for the
currently-connected rig, so a NINA-bridged mount can still use our native
autofocus if the user prefers it (and vice-versa).

Resolution policy (per the architecture spec §3):
  1. An explicit config/profile override (``auto`` | ``backend`` | ``astrodeck``)
     wins — UNLESS its prerequisites are absent, in which case we fall back to
     ``auto`` resolution rather than resolving to something that can't run.
  2. ``auto``: prefer the connected *backend's* own implementation when it
     advertises one (``supports_native_autofocus``, a live NINA TPPA client);
     otherwise the *AstroDeck native* Rust engine when its prerequisites are
     connected; otherwise a clear failure (autofocus) or the built-in simulator
     (polar align, which always has a fallback).

The native Rust engine is imported GUARDED: without the wheel the whole suite
stays green and native providers degrade to a clear DeviceError / the simulator.
``resolve_all`` never raises — it maps any resolution failure to a
``kind:"unavailable"`` row so ``poll_status`` never 500s.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import ProviderKind, config_store
from .devices.base import DeviceError
from .solve import SimSolver, get_solver

# --- guarded native import -------------------------------------------------
#
# The Rust wheel (``astrodeck_native``, built via maturin) is OPTIONAL. Import
# it once here behind a try/except so every consumer can branch on a single
# module-level bool instead of re-catching ImportError. Absent wheel => native
# providers are simply not offered (autofocus reports it clearly; polar align
# falls back to the simulator).
try:  # pragma: no cover - trivially guarded; covered both ways via monkeypatch
    import astrodeck_native  # noqa: F401
    NATIVE_AVAILABLE = True
except ImportError:  # pragma: no cover
    NATIVE_AVAILABLE = False

# Capabilities we resolve TODAY. ``guide`` / ``solve`` are reserved in the spec
# but still hard-wired (PHD2 / ASTAP), so they are not resolved here yet.
Capability = Literal["autofocus", "polar_align"]

# Hub modes for which the ``SimSolver`` REFUSES to fake a solve (solve/simsolver
# review 5d) — a real rig with no ASTAP has no trustworthy solver, so native
# TPPA (which needs plate solves) cannot run.
_REAL_MODES = ("nina", "alpaca")

# Friendly UI labels for a device's backend name. Anything unmapped is upcased
# so a new backend still renders a sane badge rather than a bare slug.
_BACKEND_LABELS: dict[str, str] = {
    "nina": "NINA",
    "alpaca": "Alpaca",
    "native": "AstroDeck native",
    "sim": "Simulator",
}


@dataclass
class ProviderChoice:
    """The resolved answer for one capability.

    ``kind`` is the resolved family (never ``auto`` — that is an input); ``label``
    is the UI badge ("NINA", "AstroDeck native", "Simulator"); ``reason`` explains
    why resolution picked it (surfaced in Settings → Capabilities).
    """
    kind: Literal["backend", "astrodeck"]
    label: str
    reason: str


def _backend_label(dev: object) -> str:
    """UI label for a connected device's backend (e.g. NINA focuser -> "NINA")."""
    b = (getattr(dev, "backend", "") or "").strip()
    return _BACKEND_LABELS.get(b, b.upper() if b else "backend")


def _override(cap: Capability, hub: object) -> ProviderKind:
    """The effective override for ``cap``: the active PROFILE's per-rig override
    wins over the global config; both default to ``auto``.

    Read defensively — an old profile lacks the ``providers`` key, a bare Hub in
    a unit test may not expose ``_active_profile``, and a malformed override value
    degrades to ``auto`` rather than raising."""
    # Per-profile override (per-rig): a NINA rig can pin native autofocus, etc.
    getter = getattr(hub, "_active_profile", None)
    if callable(getter):
        try:
            prof = getter()
        except Exception:
            prof = None
        pov = getattr(prof, "providers", None) if prof is not None else None
        if isinstance(pov, dict):
            v = pov.get(cap)
            if v in ("auto", "backend", "astrodeck"):
                return v  # type: ignore[return-value]
    # Global config default.
    try:
        cfg = config_store.cfg().providers
        v = getattr(cfg, cap, "auto")
        if v in ("auto", "backend", "astrodeck"):
            return v  # type: ignore[return-value]
    except Exception:
        pass
    return "auto"


def _connected(hub: object, role: str) -> object | None:
    """The connected device for ``role`` on the hub, or None."""
    dev = getattr(hub, "devices", {}).get(role)
    return dev if dev is not None and getattr(dev, "connected", False) else None


def _has_real_solver(hub: object) -> bool:
    """Whether a TRUSTWORTHY plate solver exists for this rig (native TPPA needs
    one). ASTAP counts everywhere; the ``SimSolver`` counts only when it will not
    refuse — i.e. NOT on a real (nina/alpaca) rig where a faked solve would
    fake-center a real mount (solve/simsolver review 5d)."""
    mode = getattr(hub, "mode", None)
    try:
        solver = get_solver(getattr(hub, "sim_rig", None), mode)
    except Exception:
        return False
    if isinstance(solver, SimSolver) and mode in _REAL_MODES:
        return False
    return True


# -------------------------------------------------------------- per-capability

def _resolve_autofocus(hub: object, override: ProviderKind) -> ProviderChoice:
    foc = _connected(hub, "focuser")
    cam = _connected(hub, "camera")
    # The backend advertises its own autofocus (NINA delegates to native_autofocus).
    backend_af = bool(foc is not None and getattr(foc, "supports_native_autofocus", False))
    # The Rust engine can run a V-curve given a camera + focuser.
    native_af = bool(NATIVE_AVAILABLE and cam is not None and foc is not None)

    # (1) explicit override — honored only when its prerequisites are present.
    if override == "backend" and backend_af:
        return ProviderChoice("backend", _backend_label(foc),
                              f"override: {_backend_label(foc)} runs its own autofocus")
    if override == "astrodeck" and native_af:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "override: native V-curve on camera + focuser")

    # (2) auto (or an override whose prerequisites were absent).
    if backend_af:
        return ProviderChoice("backend", _backend_label(foc),
                              f"{_backend_label(foc)} provides its own autofocus")
    if native_af:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "native V-curve engine drives the camera + focuser")

    # Nothing can run it — name exactly what is missing.
    if not NATIVE_AVAILABLE:
        raise DeviceError(
            "autofocus unavailable: no backend autofocus and the native engine "
            "is not installed")
    missing = [r for r in ("camera", "focuser") if _connected(hub, r) is None]
    raise DeviceError(
        "autofocus unavailable: native engine present but "
        + (f"no {' + '.join(missing)} connected" if missing
           else "no camera + focuser connected"))


def _resolve_polar(hub: object, override: ProviderKind) -> ProviderChoice:
    nina = getattr(hub, "nina_client", None) is not None
    cam = _connected(hub, "camera")
    tel = _connected(hub, "telescope")
    native_polar = bool(NATIVE_AVAILABLE and cam is not None and tel is not None
                        and _has_real_solver(hub))

    # (1) explicit override — honored only when its prerequisites are present.
    if override == "backend" and nina:
        return ProviderChoice("backend", "NINA", "override: NINA TPPA plugin")
    if override == "astrodeck" and native_polar:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "override: native TPPA on camera + mount + solver")

    # (2) auto (or an override whose prerequisites were absent).
    if nina:
        return ProviderChoice("backend", "NINA",
                              "NINA bridge present — using its TPPA plugin")
    if native_polar:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "native TPPA (camera + mount + real solver)")

    # Polar ALWAYS has a fallback: the built-in simulator (AstroDeck-side, no
    # external backend). Reported as ``astrodeck`` kind, labelled "Simulator" so
    # the UI never confuses it with the real native engine.
    if not NATIVE_AVAILABLE:
        reason = "no NINA and native engine not installed — using the simulator"
    elif cam is None or tel is None:
        reason = "no NINA and no camera + mount connected — using the simulator"
    elif not _has_real_solver(hub):
        reason = "no NINA and no trusted plate solver (install ASTAP) — using the simulator"
    else:
        reason = "using the built-in simulator"
    return ProviderChoice("astrodeck", "Simulator", reason)


_RESOLVERS = {
    "autofocus": _resolve_autofocus,
    "polar_align": _resolve_polar,
}


def resolve(cap: Capability, hub: object) -> ProviderChoice:
    """Resolve who performs ``cap`` for the hub's current rig.

    Raises ``DeviceError`` (user-presentable) when a capability with no fallback
    (autofocus) cannot run. Polar align never raises — it degrades to the
    simulator."""
    resolver = _RESOLVERS.get(cap)
    if resolver is None:
        raise DeviceError(f"unknown capability: {cap!r}")
    return resolver(hub, _override(cap, hub))


def resolve_all(hub: object) -> dict[str, dict[str, str]]:
    """Resolve every capability for ``poll_status``. NEVER raises: a resolution
    failure becomes a ``kind:"unavailable"`` row carrying the reason, so status
    never 500s and the UI can badge every panel."""
    out: dict[str, dict[str, str]] = {}
    for cap in ("autofocus", "polar_align"):
        try:
            c = resolve(cap, hub)  # type: ignore[arg-type]
            out[cap] = {"kind": c.kind, "label": c.label, "reason": c.reason}
        except DeviceError as e:
            out[cap] = {"kind": "unavailable", "label": "Unavailable", "reason": str(e)}
        except Exception as e:  # defensive: a bad hub state must not 500 status
            out[cap] = {"kind": "unavailable", "label": "Unavailable",
                        "reason": f"resolution error: {e}"}
    return out
