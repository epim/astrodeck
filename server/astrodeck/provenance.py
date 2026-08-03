"""Effective config with PROVENANCE: which LAYER won, and what the losers hold.

The bug class this module exists to end: the ACTIVE PROFILE's values beat global
config at read time (``providers.override_with_layer`` for capability routing,
``profiles.resolve_optics`` for the optical train), but every endpoint the UI
binds to reports the GLOBAL block. So the console could display a polar-align
provider with total confidence while the rig ran the built-in simulator — and
did, for twelve days. The numbers stayed plausible the whole time; the user only
noticed when they finally went absurd.

Reporting the winning VALUE is not enough to catch that, which is the trap worth
naming: a profile pinned to ``sim`` and a global config pinned to ``sim`` render
identically, so a readout that shows only the value agrees with the rig in every
case where the bug is invisible and disagrees only where the user already knew.
The thing that makes the failure legible is the LAYER IDENTITY. Every entry here
therefore carries ``layer`` plus whatever each losing layer holds.

The response shape, one entry per overridable key::

    "<dotted.key>": {
      "value":        <the value the rig actually runs>,
      "layer":        "profile" | "config" | "camera" | "default",
      "profile":      <what the active profile holds, or null>,
      "config":       <what global AppConfig holds>,
      "default":      <the model's built-in default>,
      "profile_id":   <active profile id, or null>,
      "profile_name": <active profile name, or null>,
      "reason":       <a sentence naming the deciding branch, or null>
    }

Every key is always present; inapplicable ones are ``null`` rather than absent,
so a client can render the block generically without per-key branching. The
covered keys are exactly the ones a profile can override: the four
``providers.*`` capabilities and the seven ``optics.*`` fields. ``site_name`` is
deliberately ABSENT — a profile stores one and nothing reads it as an override
(``Hub.site`` is unconditional), and listing a dead key here would invent
provenance for a value that has none.

``reason`` follows the convention ``providers.ProviderChoice.reason`` already
set: the ``"override: "`` prefix marks an explicit-override branch. That prefix
is what let the user find the polar bug in the first place, so it is reused
rather than replaced.

One honest limitation, stated so a reader does not mistake it for a bug: once
persisted, a value a user deliberately typed is byte-identical to the field's
own default, and nothing on disk records which it was. A key whose global value
still equals the model default is therefore reported as ``default``, not
``config``. That is the safer of the two errors — it under-claims what the user
configured rather than over-claiming it — and it never affects the
``profile`` vs ``config`` distinction, which is the one the twelve-day bug
turned on.
"""
from __future__ import annotations

from .config import PROVIDER_CAPABILITIES, Optics
from .events import bus
from . import providers as providers_mod

# ``config_store`` is looked up per call (``_cfg()`` below), never bound at
# import. A module-level binding freezes whichever store object existed at
# import time, so anything that redirects ``config.config_store`` — the test
# suite's isolation idiom, and the factory-reset path — would leave this readout
# describing a config the rest of the payload is not built from. A provenance
# block that disagrees with the config it is supposed to explain is worse than
# no provenance block.

#: Layer names. ``config`` is the GLOBAL ``AppConfig`` block (the layer the UI
#: used to show unconditionally); ``camera`` is the connected camera filling a
#: zeroed optics field, which ``Hub.effective_optics`` has always done and which
#: is a real winning layer — reporting ``pixel_size_um: 0`` while the rig runs
#: 3.76 from the camera would be the same lie in a different place.
LAYER_PROFILE = "profile"
LAYER_CONFIG = "config"
LAYER_CAMERA = "camera"
LAYER_DEFAULT = "default"

#: The optics fields a profile can override. All of them, because a profile
#: optics block is swapped WHOLE (``resolve_optics``) rather than merged.
OPTICS_KEYS: tuple[str, ...] = tuple(Optics.model_fields)

#: The three optics fields ``Hub.effective_optics`` will fill from the connected
#: camera when the winning layer leaves them at 0. The rest have no camera
#: source: a camera does not know the focal length, the scope's name, the guide
#: scope, or whether the user wants auto-seeding.
_CAMERA_FILLED: frozenset[str] = frozenset(
    {"pixel_size_um", "sensor_width_px", "sensor_height_px"})

#: Built-in defaults, read off the pydantic model so they cannot drift from it.
_OPTICS_DEFAULTS: dict[str, object] = {
    name: field.default for name, field in Optics.model_fields.items()}

#: Consequence notes for keys whose "never set" state costs the user something
#: real. A layer of ``default`` on most keys is unremarkable; on these it is the
#: explanation for a number the user is staring at and does not believe.
_UNSET_NOTES: dict[str, str] = {
    "guide_focal_length_mm":
        "never set — until a guide-scope focal length is saved the native guider "
        "has no arcsec/px for the guide camera and reports RMS in pixels",
}


def _cfg():
    """The live ``AppConfig``, resolved through the module attribute so a
    redirected store is honoured (see the import note above)."""
    from .config import config_store
    return config_store.cfg()


def _show(value: object) -> str:
    """Render a layer's value for a reason sentence.

    ``0`` / ``""`` / ``None`` all mean "nobody filled this in" for optics, and
    printing them raw reads as a real measurement of zero. Booleans are checked
    FIRST because ``False == 0`` and ``auto_from_camera: false`` is a deliberate
    setting, not an empty one."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None or value == "" or value == 0:
        return "unset"
    return str(value)


def _active_profile(hub: object) -> object | None:
    """The hub's cached active profile, or None. Defensive: this runs inside the
    /api/config payload, which is the UI's boot read — a hub in a half-built
    state must degrade to "no profile", never 500 the whole console."""
    getter = getattr(hub, "_active_profile", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def _entry(*, value: object, layer: str, config: object, default: object = None,
           profile: object = None, profile_id: str | None = None,
           profile_name: str | None = None, reason: str | None = None) -> dict:
    """One provenance record. Keyword-only so a caller cannot silently transpose
    the winning value and a losing layer's value — they are frequently equal,
    which is exactly when a transposition would go unnoticed."""
    return {
        "value": value,
        "layer": layer,
        "profile": profile,
        "config": config,
        "default": default,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "reason": reason,
    }


def _provider_entries(hub: object) -> dict[str, dict]:
    """Provenance for the four capability-routing keys.

    The winning value and the layer both come from
    ``providers.override_with_layer`` — the same call ``resolve()`` makes — so
    this readout cannot drift from the routing that actually happens."""
    prof = _active_profile(hub)
    pid = getattr(prof, "id", None) if prof is not None else None
    pname = getattr(prof, "name", None) if prof is not None else None
    try:
        cfg_providers = _cfg().providers
    except Exception:
        cfg_providers = None

    out: dict[str, dict] = {}
    for cap in PROVIDER_CAPABILITIES:
        value, layer, prof_raw = providers_mod.override_with_layer(cap, hub)
        cfg_val = getattr(cfg_providers, cap, "auto") if cfg_providers else "auto"

        if layer == LAYER_PROFILE:
            reason = (f"override: profile {pname!r} pins {value!r} for this "
                      f"capability; global config holds {cfg_val!r}")
        elif isinstance(prof_raw, str) and prof_raw:
            # The profile DID pin something and it was thrown away — a driver id
            # that has since been deleted, or junk from an imported profile file.
            # Nothing in the product says so today; a user reads the resolved
            # provider, sees their pin ignored, and has no way to learn why.
            reason = (f"profile {pname!r} holds {prof_raw!r}, which is not a "
                      f"valid provider any more — it was discarded, so "
                      f"{'global config' if layer == LAYER_CONFIG else 'automatic selection'} "
                      f"({value!r}) runs")
        elif prof is not None:
            reason = f"profile {pname!r} pins no provider for this capability"
        elif layer == LAYER_CONFIG:
            reason = f"global config pins {value!r}; no profile is active"
        else:
            reason = None

        out[f"providers.{cap}"] = _entry(
            value=value, layer=layer, config=cfg_val,
            default="auto", profile=prof_raw, profile_id=pid,
            profile_name=pname, reason=reason)
    return out


def _optics_entries(hub: object) -> dict[str, dict]:
    """Provenance for the seven optics fields.

    The winning values come from ``Hub.effective_optics`` (the one profile-aware
    readout in the config payload, and the same numbers that reach plate-solving,
    the FITS TELESCOP card, HFR arcsec and native TPPA), so the ``value`` here is
    by construction what the rig uses. This function only has to establish WHERE
    each one came from."""
    try:
        cfg_optics = _cfg().optics
    except Exception:
        cfg_optics = Optics()
    prof = _active_profile(hub)
    prof_optics = getattr(prof, "optics", None) if prof is not None else None
    pid = getattr(prof, "id", None) if prof is not None else None
    pname = getattr(prof, "name", None) if prof is not None else None
    try:
        effective = hub.effective_optics()          # type: ignore[attr-defined]
    except Exception:
        effective = {}

    out: dict[str, dict] = {}
    for key in OPTICS_KEYS:
        cfg_val = getattr(cfg_optics, key, None)
        default = _OPTICS_DEFAULTS.get(key)
        prof_val = getattr(prof_optics, key, None) if prof_optics is not None else None

        # A profile optics block wins WHOLE, so every field is "profile" the
        # moment the block exists — including fields the profile left at their
        # own defaults. That is not a technicality: it is why a profile that only
        # meant to change the focal length also silently reverts a pixel size.
        if prof_optics is not None:
            layer_value, layer = prof_val, LAYER_PROFILE
        elif cfg_val != default:
            layer_value, layer = cfg_val, LAYER_CONFIG
        else:
            layer_value, layer = cfg_val, LAYER_DEFAULT

        # effective_optics has the last word: it is what actually runs, and for
        # the three camera-filled fields it may override the layer above.
        value = effective.get(key, layer_value)
        if key in _CAMERA_FILLED and not layer_value and value:
            layer = LAYER_CAMERA

        if layer == LAYER_CAMERA:
            holder = (f"profile {pname!r}" if prof_optics is not None
                      else "global config")
            reason = (f"override: the connected camera supplies this "
                      f"({holder} holds {_show(layer_value)})")
        elif layer == LAYER_PROFILE:
            reason = (f"override: profile {pname!r} carries an optics block, so "
                      f"every optics field comes from it; global config holds "
                      f"{_show(cfg_val)}")
        elif prof is not None:
            reason = f"profile {pname!r} carries no optics override"
        elif layer == LAYER_DEFAULT:
            reason = _UNSET_NOTES.get(key)
        else:
            reason = None

        out[f"optics.{key}"] = _entry(
            value=value, layer=layer, config=cfg_val, default=default,
            profile=prof_val if prof_optics is not None else None,
            profile_id=pid, profile_name=pname, reason=reason)
    return out


def effective_config(hub: object) -> dict[str, dict]:
    """Every profile-overridable key with its winning value AND winning layer.

    Built per-family and per-family-guarded: this hangs off ``/api/config``,
    which is the payload the console boots on, so a failure to explain the
    config must never take the config itself down with it. A family that cannot
    be resolved is omitted and logged — omission is honest (the UI shows no
    provenance and falls back to raw values), whereas a fabricated entry would
    reintroduce a confidently-wrong readout."""
    out: dict[str, dict] = {}
    for name, build in (("providers", _provider_entries),
                        ("optics", _optics_entries)):
        try:
            out.update(build(hub))
        except Exception as e:  # pragma: no cover - defensive
            bus.log("warning",
                    f"could not resolve {name} provenance: {type(e).__name__}: {e}",
                    "config")
    return out
