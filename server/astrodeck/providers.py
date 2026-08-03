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

Four capabilities are resolved here: ``autofocus``, ``polar_align``, ``solve``,
``guide``. The native autoguider (``guide``) targets Alpaca/native/sim rigs
where AstroDeck owns the devices; a NINA rig keeps NINA/PHD2 guiding (D5). The
simulator plate solver is guarded by CONNECTED MOTION HARDWARE, not the global
hub mode — a faked solve would fake-center a real mount/focuser even on an
otherwise-mixed rig (review finding 6), so the guard cannot be beaten by any
override, explicit or not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import IMPLICIT_DRIVER_IDS, PROVIDER_CAPABILITIES, config_store
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

# The capabilities the resolver answers for.
Capability = Literal["autofocus", "polar_align", "solve", "guide"]

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


def override_with_layer(cap: Capability, hub: object) -> tuple[str, str, object]:
    """``(value, layer, profile_raw)`` — the effective override for ``cap`` AND
    the identity of the layer that supplied it.

    This is the ONE implementation of the provider precedence rule; ``_override``
    below is a thin projection of it. Keeping them fused matters: the /api/config
    provenance readout is only trustworthy if it reports the layer that the
    resolver ACTUALLY consulted, and a second hand-written copy of "profile beats
    config" would be free to drift into agreeing about the value while lying
    about where it came from — which is the exact failure this whole change
    exists to end.

    ``layer`` is one of:
      ``"profile"``  the active profile pinned a value inside the vocabulary;
      ``"config"``   global ``AppConfig.providers`` pinned a non-``auto`` value;
      ``"default"``  nobody pinned anything, so ``resolve()`` picks freely.
    Note that a profile pinning the literal ``"auto"`` reports ``"profile"``, not
    ``"default"`` — it still BEATS a global config pinned to (say) ``astap``, so
    it is a real override and the user has to be able to see it. Conversely a
    GLOBAL ``"auto"`` is indistinguishable from the field's own default once
    persisted, so it is reported honestly as ``"default"``.

    ``profile_raw`` is whatever the active profile holds for ``cap`` BEFORE
    validation (``None`` when it holds nothing). A pin naming a driver id that no
    longer exists degrades to the global layer with no signal anywhere in the
    product today; surfacing the discarded string is the only way a user can tell
    a silently-dropped override from an override that was never written.
    """
    valid = _valid_override_values()
    prof_raw: object = None
    getter = getattr(hub, "_active_profile", None)
    if callable(getter):
        try:
            prof = getter()
        except Exception:
            prof = None
        pov = getattr(prof, "providers", None) if prof is not None else None
        if isinstance(pov, dict):
            prof_raw = pov.get(cap)
            if isinstance(prof_raw, str) and prof_raw in valid:
                return prof_raw, "profile", prof_raw
    try:
        v = getattr(config_store.cfg().providers, cap, "auto")
        if isinstance(v, str) and v in valid:
            return v, ("default" if v == "auto" else "config"), prof_raw
    except Exception:
        pass
    return "auto", "default", prof_raw


def _override(cap: Capability, hub: object) -> str:
    """The effective override VALUE for ``cap``: the active PROFILE's per-rig
    override wins over the global config; both default to ``auto``. A value
    outside the current vocabulary (deleted driver id, malformed junk)
    degrades to ``auto`` rather than raising (spec §3.4 resolve-time rule)."""
    return override_with_layer(cap, hub)[0]


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
        if dev is not None and getattr(dev, "hardware", False):
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


# ---- guide-provider SELECTION + honesty (P5-T1 fix round C1) --------------
#
# The per-profile guide override is a real SELECTION input applied at guiding
# START by ``hub.select_guide_provider``: it swaps ``hub.guider`` to a guider of
# the requested FAMILY when one is constructible on the connected rig, and
# DEGRADES to whatever is already wired otherwise (never a crash). The badge
# (``_resolve_guide`` below) then reports the ACTUAL serving guider — never the
# requested override — so the UI's same-night RMS ticks, tagged by this badge,
# can never be mislabeled (the C1 root fix).

def actual_guide_family(guider: object) -> str | None:
    """The provider FAMILY of a LIVE guider object (``"native"`` | ``"backend"``),
    or None when there is no guider / its family is unknown. Reads the guider's
    ``Guider.provider_family`` marker — NOT its class — so this stays free of a
    ``guide.native`` → ``providers`` import cycle and works on test fakes."""
    if guider is None:
        return None
    fam = getattr(guider, "provider_family", None)
    return fam if fam in ("native", "backend") else None


def guide_override_family(hub: object) -> str:
    """The effective guide-provider override family for this rig
    (``"auto"`` | ``"backend"`` | ``"astrodeck"`` | ``"sim"``), profile-over-
    config, degraded to ``"auto"`` for junk/deleted values (spec §3.4). One
    source of truth the hub's guide-start selection shares with the resolver."""
    return _override_family(_override("guide", hub))


def _rig_has_bridge_session(hub: object) -> bool:
    """True when the connected rig wired a NINA/PHD2 backend session, so a bridge
    (``backend``) guider is genuinely available to switch to. Reads the retained
    ``ConnectResult`` endpoint keys — cheap, constructs no guider."""
    res = getattr(hub, "last_connect_result", None)
    sessions = getattr(res, "sessions", None) if res is not None else None
    if not isinstance(sessions, dict):
        return False
    return any(isinstance(k, tuple) and k and k[0] in ("phd2", "nina")
               for k in sessions)


#: The guide-provider override values the UI renders a ROW for, in offer order.
#: ``"sim"`` is deliberately absent: ``_resolve_guide`` has no ``sim`` branch, so
#: pinning it behaves exactly like ``auto``. A control that does nothing is worse
#: than no control, so it is never offered — an old profile that already holds it
#: still round-trips (the UI keeps a stored value as a sticky row).
GUIDE_PROVIDER_VALUES: tuple[str, ...] = ("auto", "astrodeck", "backend")


def _guide_native_blocker(hub: object) -> str | None:
    """Why the native guider cannot be OFFERED on this rig, or ``None`` when it
    can. The sentence is user-facing and names the fix, not the symptom.

    THE BUG THIS FUNCTION EXISTS TO END. The offer predicate used to be written
    out a second time, one term looser than the resolver's: the dropdown accepted
    ``guide_camera OR camera`` while ``_resolve_guide`` computes ``native_ok``
    from ``guide_camera`` ALONE. So on a rig with only the imaging camera
    connected, "AstroDeck native" was OFFERED, the write succeeded, and the
    resolver fell straight through to ``ProviderChoice("backend", "PHD2", "no
    guide camera connected — using the PHD2 bridge")``. The user picked native
    and the badge said PHD2, with nothing on screen admitting the pick had been
    discarded. One predicate now answers both questions, so the offer cannot
    drift looser than the resolver again.

    Requiring the ROLE rather than any connected camera is the physically correct
    rule, not a stylistic one: the native guide loop needs its own exposure
    stream, and the imaging camera is busy taking the light frame the guiding
    exists to protect. A rig with a genuine off-axis guider assigns that camera
    to the ``guide_camera`` role, so the requirement costs a correctly-configured
    OAG rig nothing.

    Order matters — the FIRST blocker is the one the user is shown, so it has to
    be the one they would act on first. A NINA rig is checked before anything
    else because no amount of guide-camera wiring changes that answer (D5).

    Every sentence NAMES its own provider. The client renders these without
    prefixing the option's label (prefixing produced the stutter "AstroDeck
    native — AstroDeck native needs a guide camera…"), so a sentence that did not
    identify itself would arrive on screen orphaned under a row of chips.
    ``test_every_blocked_reason_names_its_own_provider`` pins that.
    """
    if getattr(hub, "nina_client", None) is not None:
        return ("NINA owns guiding on a NINA rig — AstroDeck's own guider isn't "
                "offered while NINA is driving this rig")
    if not NATIVE_AVAILABLE:
        return ("the AstroDeck native guiding engine isn't installed on this "
                "host, so only the PHD2/NINA bridge can guide")
    if _connected(hub, "guide_camera") is None:
        return ("AstroDeck native needs a guide camera assigned and connected — "
                "assign one to the guide camera role on Equipment")
    if _connected(hub, "telescope") is None:
        return ("AstroDeck native needs the mount connected — it guides by "
                "pulsing the mount")
    return None


def _guide_backend_blocker(hub: object) -> str | None:
    """Why the PHD2/NINA bridge cannot be OFFERED. Always ``None`` — it always
    can be.

    This used to refuse the bridge unless one was already reachable, which broke
    the very rule the native blocker exists to keep: **the offer must be the same
    predicate as the resolver**. ``_resolve_guide`` honours an explicit
    ``backend`` override unconditionally, and says why in its own comment — the
    legacy PHD2 socket is one the host can always attempt, so there is nothing to
    be "connected" in advance.

    Refusing it produced a screen that argued with itself on the DEFAULT rig
    (imaging camera + mount, no guide camera, no bridge driver): one line read
    "no PHD2 or NINA bridge is connected", the next read "no guide camera
    connected — using the PHD2 bridge", and the badge read PHD2. Three
    individually-true sentences that cannot all be about the same rig. It only
    became visible when this change started RENDERING blocked reasons; before
    that an ineligible value was simply absent from the select, so the sentence
    did not exist to contradict anything.

    Kept as a function rather than inlined so the symmetry with
    ``_guide_native_blocker`` is legible at the call site, and so a future
    genuine precondition has an obvious home."""
    return None


def guide_provider_options(hub: object) -> list[dict]:
    """Every guide-provider value the UI should RENDER, each with whether it is
    selectable here and — when it is not — the sentence saying why.

    Returning the blocked values WITH their reason (rather than silently
    dropping them, which is what ``eligible`` alone forced) is what lets the
    client use the house honest-disabled pattern: a dim, lock-marked, still
    tap-reachable row carrying "AstroDeck native needs a guide camera assigned
    and connected". A missing row tells the user nothing to act on; worse, on a
    rig that simply has not connected yet it reads as "this product cannot do
    that at all", which is how a user concluded AstroDeck could not guide."""
    out: list[dict] = []
    blockers = {
        "auto": None,                              # always selectable
        "astrodeck": _guide_native_blocker(hub),
        "backend": _guide_backend_blocker(hub),
    }
    for value in GUIDE_PROVIDER_VALUES:
        blocker = blockers.get(value)
        out.append({"value": value, "eligible": blocker is None,
                    "reason": blocker})
    return out


def guide_eligible_providers(hub: object) -> list[str]:
    """The guide-provider override VALUES actually selectable on the connected
    rig (mirrors the UI's ``eligibleTaskDrivers``: only offer what applies,
    review I1). DERIVED from :func:`guide_provider_options` rather than computed
    again, so the list and the per-option reasons can never disagree — that
    duplication is precisely what produced the offer-a-provider-the-resolver-
    rejects bug documented on ``_guide_native_blocker``."""
    return [o["value"] for o in guide_provider_options(hub) if o["eligible"]]


def _resolve_guide(hub: object, override: str) -> ProviderChoice:
    """Who autoguides. NINA rigs keep NINA/PHD2 guiding (Global Constraint D5);
    an Alpaca/native rig with a connected guide camera + mount and the wheel runs
    the native Rust engine; a sim rig runs the native engine too but is badged
    ``sim`` (the guider OBJECT is a ``NativeGuider`` over the sim devices after
    the P2-T3 default flip). Anything else degrades to the PHD2 bridge
    (``backend``) — guiding NEVER raises (spec §3.3/§4). Mirrors
    ``_resolve_autofocus`` / the native-first ``_resolve_polar`` model."""
    nina = getattr(hub, "nina_client", None) is not None
    gcam = _connected(hub, "guide_camera")
    tel = _connected(hub, "telescope")
    native_ok = bool(NATIVE_AVAILABLE and gcam is not None and tel is not None)
    # A sim rig owns non-real ('' / sim) devices; a native/Alpaca rig owns real
    # hardware. Both run the NativeGuider engine — ``kind`` is only the UI badge.
    real_rig = gcam is not None and getattr(gcam, "hardware", False)

    # (0) HONESTY (fix round C1): when a guider is actually WIRED, the badge
    # reports what is REALLY serving this rig — never the requested override —
    # so the UI's per-provider RMS ticks (tagged by this badge) can't be
    # mislabeled. The override became a real selection input at guiding START
    # (hub.select_guide_provider), which either honored it or degraded to what
    # exists; this reflects that outcome. The pure matrix below still answers
    # the "what WOULD guide" preview for a rig with no guider wired yet (a
    # disconnected rig / the unit fixtures).
    actual = actual_guide_family(getattr(hub, "guider", None))
    if actual == "backend":
        return ProviderChoice("backend", "NINA" if nina else "PHD2",
                              "NINA is guiding this rig" if nina
                              else "the PHD2/NINA bridge is guiding this rig")
    if actual == "native":
        # sim-vs-real badge: the native engine runs over EITHER; the badge keys
        # off whether the guide (or, for an OAG, the imaging) camera is real.
        cam2 = gcam if gcam is not None else _connected(hub, "camera")
        is_real = cam2 is not None and getattr(cam2, "hardware", False)
        if is_real:
            return ProviderChoice("astrodeck", "AstroDeck native",
                                  "the native guider is running (guide camera + mount)")
        return ProviderChoice("sim", "Simulator",
                              "the native guider is running over the simulated rig")

    # (1) explicit override — honored only when runnable.
    if override == "backend":
        # The NINA/PHD2 bridge is always selectable (a NINA rig owns guiding; the
        # legacy PHD2 socket is one the host can always attempt), so honor it.
        return ProviderChoice("backend", "NINA" if nina else "PHD2",
                              "override: NINA/PHD2 bridge guiding")
    if override == "astrodeck" and native_ok:
        return ProviderChoice("astrodeck", "AstroDeck native",
                              "override: native guider on guide camera + mount")

    # (2) auto (or an override whose prerequisites were absent). NINA owns
    # guiding on a NINA rig (D5); native guiding targets rigs AstroDeck owns.
    if nina:
        return ProviderChoice("backend", "NINA",
                              "NINA owns guiding on a NINA rig")
    if native_ok:
        if real_rig:
            return ProviderChoice("astrodeck", "AstroDeck native",
                                  "native guider (guide camera + mount connected)")
        return ProviderChoice("sim", "Simulator",
                              "native guider over the simulated rig")

    # No guide camera / wheel absent: the PHD2 bridge is the vendor-neutral
    # fallback (spec §3.3/§4 — never a crash).
    if not NATIVE_AVAILABLE:
        reason = "native engine not installed — using the PHD2 bridge"
    elif gcam is None:
        reason = "no guide camera connected — using the PHD2 bridge"
    else:
        reason = "no mount connected — using the PHD2 bridge"
    return ProviderChoice("backend", "PHD2", reason)


_RESOLVERS = {
    "autofocus": _resolve_autofocus,
    "polar_align": _resolve_polar,
    "solve": _resolve_solve,
    "guide": _resolve_guide,
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
    for cap in PROVIDER_CAPABILITIES:
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
