"""Factory reset — return the controller to the state a FRESH INSTALL has.

Why this exists: the build gets handed to a QA tester, they set up the rig from
scratch on a phone, and then the box has to be handed to the *next* tester with
no trace of the first. "Delete the config file" is not a product feature; this
is.

The contract this module holds, and the reason it is a module and not four lines
inside a route:

**A factory reset clears SETUP. It never silently destroys DATA.**

Three tiers, and every one of them is stated verbatim on screen (see
``FactoryResetPanel.tsx``) BEFORE the user commits, because a reset that
surprises you about what it took is worse than no reset at all:

1. **Always cleared** — everything a tester touched while setting the rig up.
   The whole ``AppConfig`` goes back to its pydantic defaults (site back to
   ``is_default: True``, optics, safety, alerts, weather, naming, calibration,
   providers, guide algorithms, rotator, survey, drivers) and the setup files
   beside it are deleted (profiles, plans, saved locations, filter names,
   learned EGAIN, guider calibrations).

2. **Kept unless explicitly asked for** — two separate opt-ins, both default
   OFF at the API and in the UI:
   - ``delete_captures``: captured frames, session ledgers and night reports —
     **and the gallery trash**, which is deliberately NOT preserved: a frame the
     tester deleted last week is still the tester's data, and a "factory reset"
     that leaves a populated bin behind has not returned the box to a fresh
     install. Its bytes are counted by ``capture_inventory`` for the same reason
     — the on-screen number must be what actually goes.
     These are the tester's IMAGES. Losing somebody's data to a settings button
     is unforgivable, so it is a distinct, separately-labelled choice.
   - ``reset_auth``: local sign-in accounts + the auth block. Kept by default
     because wiping it reopens a rig that may be reachable from the WAN; offered
     at all because a tester who creates a local admin would otherwise lock the
     NEXT tester out, which is precisely the trap this feature exists to remove.

3. **Never cleared, at all** — install plumbing that was never part of "setup"
   and whose loss costs real money or a support call:
   - ``remote`` (relay pairing) — clearing it orphans a remotely-managed rig
     from its owner, with no way back in over the WAN.
   - ``update`` (signing pubkey / repo / channel / GH token) — clearing it
     breaks self-update on a deployed box.
   - under the capture root: ``_survey_pack`` (the offline HiPS sky pack — a
     multi-GB download), ``_survey`` / ``_weather_tiles`` (network caches) and
     ``logs``. None of these are "setup" and none are the tester's data; see
     ``PRESERVED_CAPTURE_ENTRIES``.

Directories are resolved through EXPLICIT parameters, never module-level
constants, so the route passes the live ``config.CONFIG_DIR`` / ``hub.CAPTURE_DIR``
(honouring a monkeypatch) and the tests pass a ``tmp_path``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import AppConfig, AuthConfig, ConfigStore
from .events import bus

# ---------------------------------------------------------------- config dir
#: Files/dirs BESIDE ``astrodeck.json`` that hold rig SETUP and are always
#: cleared. Each entry is (relative name, is_dir).
_SETUP_ENTRIES: tuple[tuple[str, bool], ...] = (
    ("profiles", True),        # saved equipment profiles
    ("plans", True),           # saved sequence plans
    ("guider", True),          # per-profile guider calibrations + GP-PPEC dumps
    ("locations.json", False),  # saved observing locations
    ("filter_names.json", False),  # per-profile filter slot names + offsets
    ("egain.json", False),     # learned per-gain conversion gain
)

#: Cleared ONLY when ``reset_auth`` is asked for. ``session_secret`` goes with
#: users.json so no session minted under the old install still verifies.
_AUTH_ENTRIES: tuple[tuple[str, bool], ...] = (
    ("users.json", False),
    ("session_secret", False),
)

# --------------------------------------------------------------- capture dir
#: Entries under the capture root that are NOT the tester's data and are kept
#: even when ``delete_captures`` is asked for. Verified against a real capture
#: root before this list was written — see the module docstring.
#:
#:   _survey_pack   offline HiPS sky pack (multi-GB, hours to re-fetch)
#:   _survey        hips2fits image cache
#:   _weather_tiles radar tile cache
#:   logs           diagnostics
#:
#: Everything else under the root (target folders, ``darks``, ``untargeted``,
#: ``sessions``, ``reports``, ``exports``, ``_masters``, ``_solve``, the frame
#: counters, the gallery's ``_trash`` and ``_gallery_thumbs``) IS the capture
#: library and goes when the box is ticked. Deny-list rather than allow-list on
#: purpose: target folders are named after whatever the user imaged, so no
#: allow-list could enumerate them — and that same property is why the gallery's
#: two directories need no entry here to be handled correctly. Adding ``_trash``
#: to this set would be the bug: it would hand the next tester the previous
#: tester's deleted frames.
PRESERVED_CAPTURE_ENTRIES: frozenset[str] = frozenset({
    "_survey_pack", "_survey", "_weather_tiles", "logs",
})


def _rm(path: Path) -> bool:
    """Delete a file or tree. Returns True if something was there. Never raises
    on a missing path; an OSError is swallowed and logged so one locked file
    can't abort the whole reset half-done."""
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return True
        if path.exists():
            path.unlink()
            return True
    except OSError as e:  # pragma: no cover - platform/locking dependent
        bus.log("warning", f"factory reset could not remove {path.name}: {e}",
                "config")
    return False


def capture_inventory(capture_dir: Path) -> dict:
    """What ``delete_captures`` WOULD remove, counted for real.

    Feeds the panel's on-screen scope statement so the copy quotes measured
    numbers instead of a guess. Cheap enough to call per panel render (one
    ``rglob`` over the capture root); returns zeros for a missing root."""
    frames = 0
    entries = 0
    bytes_ = 0
    try:
        children = list(capture_dir.iterdir())
    except OSError:
        return {"frames": 0, "entries": 0, "bytes": 0}
    for child in children:
        if child.name in PRESERVED_CAPTURE_ENTRIES:
            continue
        entries += 1
        try:
            if child.is_dir():
                for f in child.rglob("*"):
                    if f.is_file():
                        bytes_ += f.stat().st_size
                        if f.suffix.lower() in (".fits", ".fit", ".fts", ".xisf"):
                            frames += 1
            else:
                bytes_ += child.stat().st_size
        except OSError:
            continue
    return {"frames": frames, "entries": entries, "bytes": bytes_}


def preview(store: ConfigStore, config_dir: Path, capture_dir: Path) -> dict:
    """Measured scope, for the confirm copy. Pure read — changes nothing."""
    cfg = store.cfg()
    profiles_dir = config_dir / "profiles"
    plans_dir = config_dir / "plans"

    def _count(d: Path) -> int:
        try:
            return sum(1 for p in d.glob("*.json"))
        except OSError:
            return 0

    try:
        from .auth.users import user_store
        users = len(user_store.list())
    except Exception:
        users = 0

    return {
        "site_is_default": bool(cfg.site.is_default),
        "profiles": _count(profiles_dir),
        "plans": _count(plans_dir),
        "drivers": len(cfg.drivers),
        "alert_sinks": len(cfg.alerts),
        "users": users,
        "captures": capture_inventory(capture_dir),
        "preserved_capture_entries": sorted(PRESERVED_CAPTURE_ENTRIES),
    }


def factory_reset(store: ConfigStore, config_dir: Path, capture_dir: Path, *,
                  delete_captures: bool = False,
                  reset_auth: bool = False) -> dict:
    """Do the reset. Returns a report of what was actually cleared.

    Both destructive extras default to False here as well as at the route and in
    the UI — the safe value is the default at every layer, so no single missing
    argument anywhere in the stack can turn a settings reset into data loss.
    """
    cfg = store.cfg()

    # 1. Config back to pydantic defaults, carrying forward ONLY the tier-3
    #    install plumbing. `version` is carried and bumped (never reset to 1):
    #    an open client holding an older token must still lose its optimistic-
    #    concurrency race rather than accidentally match a rewound counter.
    fresh = AppConfig()
    fresh.version = cfg.version
    fresh.remote = cfg.remote
    fresh.update = cfg.update

    if reset_auth:
        # Defaults, EXCEPT the session epoch, which is monotonic by contract
        # (config.set_auth clamps to max) — bump it so every session minted
        # against the old install stops verifying immediately.
        fresh.auth = AuthConfig(session_epoch=int(cfg.auth.session_epoch or 0) + 1)
    else:
        fresh.auth = cfg.auth

    cleared_config = store.replace(fresh)

    # 2. Setup files beside the config.
    removed: list[str] = []
    for name, _is_dir in _SETUP_ENTRIES:
        if _rm(config_dir / name):
            removed.append(name)
    # The recovery backup is a snapshot of the PREVIOUS install's config; leaving
    # it would let a corrupt-file recovery silently resurrect the old site.
    _rm(config_dir / "astrodeck.json.bak")

    if reset_auth:
        for name, _is_dir in _AUTH_ENTRIES:
            if _rm(config_dir / name):
                removed.append(name)
        # ``user_store`` is a process singleton with its OWN in-memory cache, so
        # deleting users.json on disk is not enough — without this the accounts
        # stay live in memory (and keep authenticating) until a restart. Drop the
        # cache so the next read lazily reloads from the now-absent file.
        try:
            from .auth.users import user_store
            user_store._users = None
        except Exception:  # pragma: no cover - import-time only
            pass

    # 3. Captures — ONLY on the explicit opt-in.
    captures_removed = 0
    if delete_captures:
        try:
            children = list(capture_dir.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.name in PRESERVED_CAPTURE_ENTRIES:
                continue
            if _rm(child):
                captures_removed += 1

    bus.log("warning",
            f"factory reset: config restored to defaults"
            f"{', captures deleted' if delete_captures else ''}"
            f"{', sign-in accounts cleared' if reset_auth else ''}",
            "config")

    return {
        "ok": True,
        "config_reset": True,
        "removed": removed,
        "captures_deleted": delete_captures,
        "capture_entries_removed": captures_removed,
        "auth_reset": reset_auth,
        "version": cleared_config.version,
    }
