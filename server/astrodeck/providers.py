"""Per-capability provider resolution — "who performs capability X for this rig?"

AstroDeck drives three classes of rig (NINA bridge, native Alpaca/sim, future
ASIAIR) and each should use its own best tooling. This module answers, *per
capability* (not per rig), which implementation runs a given capability for the
currently-connected rig, so a NINA-bridged mount can still use our native
autofocus if the user prefers it (and vice-versa).

Resolution policy (per the architecture spec §3.4):
  1. An explicit config/profile override (``auto`` | ``backend`` (legacy) |
     ``astrodeck`` | ``astap`` | ``sim`` | a configured driver id) wins —
     UNLESS its prerequisites are absent, in which case we fall back to
     ``auto`` resolution rather than resolving to something that can't run. A
     driver id maps to the FAMILY its driver type implements (only NINA
     drivers implement tasks today); anything else degrades to ``auto``.
  2. ``auto``: prefer the connected *backend's* own implementation when it
     advertises one (``supports_native_autofocus``, a live NINA TPPA client);
     otherwise the *AstroDeck native* Rust engine when its prerequisites are
     connected; otherwise a clear failure (autofocus, solve on a real rig with
     no ASTAP) or the built-in simulator (polar align, which always has a
     fallback; solve, when no connected motion device is real hardware).

The native Rust engine is imported GUARDED: without the wheel the whole suite
stays green and native providers degrade to a clear DeviceError / the simulator.
``resolve_all`` never raises — it maps any resolution failure to a
``kind:"unavailable"`` row so ``poll_status`` never 500s.

Three capabilities are resolved here: ``autofocus``, ``polar_align``, ``solve``
(``guide`` stays hard-wired PHD2). The simulator plate solver is guarded by
CONNECTED MOTION HARDWARE, not the global hub mode — a faked solve would
fake-center a real mount/focuser even on an otherwise-mixed rig (review
finding 6), so the guard cannot be beaten by any override, explicit or not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import IMPLICIT_DRIVER_IDS, config_store
from .devices.base import DeviceError
from .solve import AstapSolver, PlateSolver, SimSolver, find_astap

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

# The capabilities the resolver answers for (``guide`` stays hard-wired PHD2).
Capability = Literal["autofocus", "polar_align", "solve"]

# Device ``backend`` attribute values that mean REAL hardware. Sim devices leave
# the Device default ("" — devices/base.py:82); only nina.py / alpaca.py set one.
_REAL_BACKENDS = ("nina", "alpaca")

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

    ``kind`` answers WHO runs it: ``backend`` (the connected backend, e.g.
    NINA), ``astrodeck`` (the native Rust engine), ``astap`` (the local ASTAP
    binary), ``sim`` (the built-in simulator). Never ``auto`` — that is an
    input. ``label`` is the UI badge; ``reason`` explains the pick."""
    kind: Literal["backend", "astrodeck", "astap", "sim"]
    label: str
    reason: str


def _backend_label(dev: object) -> str:
    """UI label for a connected device's backend (e.g. NINA focuser -> "NINA")."""
    b = (getattr(dev, "backend", "") or "").strip()
    return _BACKEND_LABELS.get(b, b.upper() if b else "backend")


def _valid_override_values() -> set[str]:
    """The write-time vocabulary, read defensively (a bare config store in a
    unit test must not break resolution)."""
    try:
        return config_store.valid_override_values()
    except Exception:
        return {"auto", "backend", *IMPLICIT_DRIVER_IDS}


def _override(cap: Capability, hub: object) -> str:
    """The effective override VALUE for ``cap``: the active PROFILE's per-rig
    override wins over the global config; both default to ``auto``. A value
    outside the current vocabulary (deleted driver id, malformed junk)
    degrades to ``auto`` rather than raising (spec §3.4 resolve-time rule)."""
    valid = _valid_override_values()
    getter = getattr(hub, "_active_profile", None)
    if callable(getter):
        try:
            prof = getter()
        except Exception:
            prof = None
        pov = getattr(prof, "providers", None) if prof is not None else None
        if isinstance(pov, dict):
            v = pov.get(cap)
            if isinstance(v, str) and v in valid:
                return v
    try:
        v = getattr(config_store.cfg().providers, cap, "auto")
        if isinstance(v, str) and v in valid:
            return v
    except Exception:
        pass
    return "auto"


def _override_family(value: str) -> str:
    """Map a concrete override value to the implementation FAMILY the resolvers
    branch on. A configured driver id maps by its driver TYPE (only NINA
    drivers implement tasks — their family is the legacy ``backend``); alpaca/
    phd2 ids and anything unknown degrade to ``auto``."""
    if value in ("auto", "backend", "astrodeck", "astap", "sim"):
        return value
    try:
        for d in config_store.cfg().drivers:
            if d.id == value:
                return "backend" if d.type == "nina" else "auto"
    except Exception:
        pass
    return "auto"


def _connected(hub: object, role: str) -> object | None:
    """The connected device for ``role`` on the hub, or None."""
    dev = getattr(hub, "devices", {}).get(role)
    return dev if dev is not None and getattr(dev, "connected", False) else None


def _rig_has_real_motion(hub: object) -> bool:
    """True when any CONNECTED motion device (mount/focuser/rotator) is real
    hardware. This — not the global hub mode, meaningless on a mixed rig — is
    what makes a faked plate solve dangerous (review finding 6)."""
    for role in ("telescope", "focuser", "rotator"):
        dev = _connected(hub, role)
        if dev is not None and getattr(dev, "backend", "") in _REAL_BACKENDS:
            return True
    return False


def _has_real_solver(hub: object) -> bool:
    """Whether a TRUSTWORTHY plate solver resolves for this rig (native TPPA
    needs one). Delegates to the ``solve`` capability — ONE guard, motion-
    device-keyed, shared with solve_and_sync."""
    try:
        resolve("solve", hub)
        return True
    except Exception:
        return False


# -------------------------------------------------------------- per-capability

def _resolve_autofocus(hub: object, override: str) -> ProviderChoice:
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


def _resolve_polar(hub: object, override: str) -> ProviderChoice:
    nina = getattr(hub, "nina_client", None) is not None
    cam = _connected(hub, "camera")
    tel = _connected(hub, "telescope")
    native_polar = bool(NATIVE_AVAILABLE and cam is not None and tel is not None
                        and _has_real_solver(hub))

    # (1) explicit override — honored only when its prerequisites are present.
    # ``sim`` has no prerequisites: the built-in simulator always runs.
    if override == "sim":
        return ProviderChoice("sim", "Simulator", "override: built-in simulator")
    if override == "backend" and nina:
        return ProviderChoice("backend", "NINA", "override: NINA TPPA plugin")
    if override == "astrodeck" and native_polar:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "override: native TPPA on camera + mount + solver")

    # (2) auto (or an override whose prerequisites were absent). The first-party
    # native engine is PREFERRED over the NINA bridge when it can run: NINA is a
    # transition bridge, so a mere-present bridge no longer auto-outranks native.
    # The driver-selection model routes polar align to NINA only when the user
    # EXPLICITLY selects it (override == "backend"/a NINA driver id, handled above).
    if native_polar:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "native TPPA (camera + mount + real solver)")
    if nina:
        return ProviderChoice("backend", "NINA",
                              "native prerequisites absent — using the NINA TPPA plugin")

    # Polar ALWAYS has a fallback: the built-in simulator (AstroDeck-side, no
    # external backend).
    if not NATIVE_AVAILABLE:
        reason = "no NINA and native engine not installed — using the simulator"
    elif cam is None or tel is None:
        reason = "no NINA and no camera + mount connected — using the simulator"
    elif not _has_real_solver(hub):
        reason = "no NINA and no trusted plate solver (install ASTAP) — using the simulator"
    else:
        reason = "using the built-in simulator"
    return ProviderChoice("sim", "Simulator", reason)


def _resolve_solve(hub: object, override: str) -> ProviderChoice:
    """Who plate-solves. ASTAP is the only solver trusted near real hardware;
    the simulator solver is offered ONLY when no connected motion device is
    real — even an explicit ``sim`` override cannot beat that guard (review
    finding 6: a faked solve would fake-center a real mount)."""
    astap = find_astap()
    sim_ok = not _rig_has_real_motion(hub)

    # (1) explicit override — honored only when its prerequisites are present.
    if override == "astap" and astap:
        return ProviderChoice("astap", "ASTAP", f"override: ASTAP at {astap}")
    if override == "sim" and sim_ok:
        return ProviderChoice("sim", "Simulator",
                              "override: simulator solver (no real motion connected)")

    # (2) auto (or an override whose prerequisites were absent).
    if astap:
        return ProviderChoice("astap", "ASTAP", f"ASTAP found at {astap}")
    if sim_ok:
        return ProviderChoice("sim", "Simulator",
                              "no ASTAP — simulator solver (no real motion connected)")
    raise DeviceError(
        "plate solving unavailable: ASTAP not found and a real mount/focuser "
        "is connected — refusing the simulator solver (install ASTAP or set "
        "ASTAP_PATH)")


_RESOLVERS = {
    "autofocus": _resolve_autofocus,
    "polar_align": _resolve_polar,
    "solve": _resolve_solve,
}


def resolve(cap: Capability, hub: object) -> ProviderChoice:
    """Resolve who performs ``cap`` for the hub's current rig.

    Raises ``DeviceError`` (user-presentable) when a capability with no fallback
    (autofocus, solve on a real rig with no ASTAP) cannot run. Polar align never
    raises — it degrades to the simulator."""
    resolver = _RESOLVERS.get(cap)
    if resolver is None:
        raise DeviceError(f"unknown capability: {cap!r}")
    return resolver(hub, _override_family(_override(cap, hub)))


def resolve_all(hub: object) -> dict[str, dict[str, str]]:
    """Resolve every capability for ``poll_status``. NEVER raises: a resolution
    failure becomes a ``kind:"unavailable"`` row carrying the reason, so status
    never 500s and the UI can badge every panel."""
    out: dict[str, dict[str, str]] = {}
    for cap in ("autofocus", "polar_align", "solve"):
        try:
            c = resolve(cap, hub)  # type: ignore[arg-type]
            out[cap] = {"kind": c.kind, "label": c.label, "reason": c.reason}
        except DeviceError as e:
            out[cap] = {"kind": "unavailable", "label": "Unavailable", "reason": str(e)}
        except Exception as e:  # defensive: a bad hub state must not 500 status
            out[cap] = {"kind": "unavailable", "label": "Unavailable",
                        "reason": f"resolution error: {e}"}
    return out


def pick_solver(hub: object) -> PlateSolver:
    """The actual solver instance for this rig, per ``resolve("solve")``.
    Raises ``DeviceError`` (user-presentable) when nothing trustworthy can
    solve — BEFORE any exposure is wasted. The SimSolver is built with
    ``mode=None``: the resolver's motion-device guard is the safety authority
    now (finding 6); SimSolver's own mode check remains only for legacy direct
    ``get_solver`` callers."""
    choice = resolve("solve", hub)
    if choice.kind == "astap":
        astap = find_astap()
        if astap:
            return AstapSolver(astap)
        raise DeviceError("ASTAP disappeared between resolution and use")
    return SimSolver(getattr(hub, "sim_rig", None), mode=None)
