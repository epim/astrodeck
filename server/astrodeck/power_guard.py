"""Which switch ports the server REFUSES to switch while a run is live (D-RIG-5).

The rule itself is old: "mount, camera and USB are locked while a session runs".
What is new is where it is decided. Until now it lived in the browser
(``ui/src/next/hubs/rig/sheets/power.tsx``, ``SESSION_CRITICAL``), which meant

- the lock was advice, not enforcement: anything that was not that one sheet -
  curl, a second tab on an older build, a script - could cut power to the mount
  mid-sequence and the server would happily do it;
- and the client and the engine could disagree, because two copies of a rule are
  two rules.

So the decision moves here, and the client is told the answer rather than
computing it (``protected_now`` on every port row).

THE NAME MATCH IS THE DEFAULT, NOT THE RULE. A port whose name matches
``_PROTECTED_NAME`` is protected unless somebody has said otherwise; that
somebody's answer is stored per port, per profile, and beats the name in BOTH
directions. Three states, and the third one is the point:

    None   nobody has said - follow the name (and keep following it when the
           port is renamed on the box, which is the behaviour that shipped)
    True   protected, whatever it is called
    False  not protected, whatever it is called

``None`` and ``False`` must not collapse into one another: "unset" adapts to a
rename, "the operator said no" is a decision that has to survive one.

STORAGE. ``CONFIG_DIR/switch_ports.json``, the ``filter_names.json`` shape
(``config.load_filter_config``) and for the reason given there: a per-port
decision is device metadata keyed by a port of a particular box on a particular
rig, and it must not live in ``AppConfig``, where a wholesale block replace would
erase by omission every flag the sender did not happen to know about.

    {"<profile_id or __default__>": {"<port_id>": {"protect_during_run": true,
                                                   "follow_dew": false}}}

``follow_dew`` lives here too, and is read by the dew controller (D-RIG-3/S7f).
Two stores for one port's settings would be two stores that drift.

EVERY READ IS TOTAL. ``port_settings`` answers for an unknown port, a missing
file, a corrupt file and a file that parses to something that is not an object -
the defaults, never an exception. A dew loop that raised because somebody
hand-edited a JSON file would stop heating the optics, which is a strictly worse
failure than forgetting one flag.
"""
from __future__ import annotations

import copy
import logging
import re
from pathlib import Path

from .locations import UNCHANGED
from .persist import read_json, write_json_atomic

log = logging.getLogger(__name__)

#: The bucket a rig with no active profile writes to, byte-identical to
#: ``config._FILTER_DEFAULT_KEY`` so the two sidecars key the same way.
DEFAULT_KEY = "__default__"

#: The name heuristic, and it is the DEFAULT rather than the rule. Kept
#: byte-identical to the pattern the UI has shipped since Wave 1, because a
#: port that was locked yesterday must not become tappable today just because
#: the decision moved server-side.
_PROTECTED_NAME = re.compile(r"mount|camera|usb", re.I)

#: The refusal, as one sentence the UI can show verbatim. It names the port, says
#: what switching it would actually do, and gives BOTH ways out - because "stop
#: the run" is not an acceptable answer to somebody whose dew port is called
#: "USB DEW" and was never session-critical in the first place.
_REFUSAL = ("{name} is protected while a run is live: switching it now would cut "
            "power to something the sequence is using. Stop the run, or clear "
            "the protection for this port in Power settings.")

#: Paths we have already complained about, so a corrupt store logs ONCE and not
#: once per port per poll. Keyed by path rather than a single global flag: the
#: config directory is rebound per test, and a fresh store is a fresh complaint.
_corrupt_warned: set[str] = set()


def switch_ports_path() -> Path:
    """The store's path, resolved at CALL time.

    A FUNCTION, NOT A MODULE CONSTANT, for the reason ``config.
    focus_calibration_path`` spells out: ``CONFIG_DIR`` is rebound per test
    (``tests/conftest.py::_isolate_config_store``), so a path computed at import
    time captures the developer's real ``server/config/`` and writes there for
    the whole session.
    """
    from .config import CONFIG_DIR
    return CONFIG_DIR / "switch_ports.json"


def _active_profile_id() -> str | None:
    """The profile the rig is running, or None. Never raises."""
    try:
        from .config import config_store
        return config_store.cfg().active_profile_id
    except Exception:          # pragma: no cover - a store that cannot load
        return None


def _key(profile_id: str | None) -> str:
    """Which bucket to read/write.

    ``profile_id=None`` means "the profile this rig is running", resolved here
    rather than at every call site - ``hub.py`` passes
    ``config_store.cfg().active_profile_id`` by hand for the filter-name sidecar,
    and a caller that forgets writes into the wrong rig's bucket. A rig with no
    active profile lands on ``DEFAULT_KEY``, exactly as ``load_filter_config``
    does with the same value.
    """
    if profile_id is None:
        profile_id = _active_profile_id()
    return profile_id or DEFAULT_KEY


def _port_key(port_id) -> str:
    """JSON object keys are strings; port ids are ints. One conversion, here."""
    try:
        return str(int(port_id))
    except (TypeError, ValueError):
        return str(port_id)


def _load_store() -> dict:
    """The whole envelope, or ``{}``. TOTAL: missing, unreadable, unparseable and
    parsed-to-a-non-object all answer ``{}``; only the last two say so, once."""
    path = switch_ports_path()
    try:
        data = read_json(path)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        _warn_once(path, f"unreadable ({exc})")
        return {}
    if not isinstance(data, dict):
        _warn_once(path, "not a JSON object")
        return {}
    return data


def _warn_once(path: Path, why: str) -> None:
    marker = str(path)
    if marker in _corrupt_warned:
        return
    _corrupt_warned.add(marker)
    log.warning(
        "switch port settings at %s are %s - falling back to the name heuristic "
        "for every port until it is fixed or rewritten", path, why)


def _entry(store: dict, key: str, port_id) -> dict:
    """One port's raw stored object, or ``{}``. Tolerates junk at every level."""
    bucket = store.get(key)
    if not isinstance(bucket, dict):
        return {}
    row = bucket.get(_port_key(port_id))
    return row if isinstance(row, dict) else {}


def _settings_from(entry: dict) -> dict:
    """Coerce one raw stored object into the answer shape.

    Strict on purpose: only a real ``True``/``False`` is a decision. A string
    ``"false"`` or a ``0`` left by a hand-edit is not "not protected" - it is
    somebody having said something we cannot read, and the safe reading of that
    is "nobody has said", which falls back to the name.
    """
    protect = entry.get("protect_during_run")
    if protect is not True and protect is not False:
        protect = None
    follow = entry.get("follow_dew")
    return {"protect_during_run": protect, "follow_dew": follow is True}


def port_settings(port_id: int, *, profile_id: str | None = None) -> dict:
    """This port's stored settings: ``{"protect_during_run": bool | None,
    "follow_dew": bool}``.

    TOTAL. An unknown port, an absent file, a corrupt file and a store full of
    junk all answer the defaults (``{"protect_during_run": None,
    "follow_dew": False}``) rather than raising. S7f's dew loop depends on that:
    it runs unattended for a whole night and must never stop heating because of
    a JSON file.
    """
    return _settings_from(_entry(_load_store(), _key(profile_id), port_id))


def set_port_settings(port_id: int, *,
                      protect_during_run: object = UNCHANGED,
                      follow_dew: object = UNCHANGED,
                      profile_id: str | None = None) -> dict:
    """Write one or both of this port's settings; return the result.

    ``UNCHANGED`` (imported from ``locations``, not re-invented) is what makes
    the partial write correct. ``protect_during_run`` is TRI-state, so a caller
    that only wants to flip ``follow_dew`` cannot signal "leave it alone" with
    ``None`` - ``None`` is one of its three real values. Defaulting the parameter
    to ``None`` here would erase the protection decision of every port whose dew
    setting was ever touched, which is the exact bug ``locations.py`` documents
    at its own ``UNCHANGED``.

    Raises ``ValueError`` on a value that is not of the field's type.
    """
    if protect_during_run is not UNCHANGED:
        if protect_during_run is not None and not isinstance(protect_during_run, bool):
            raise ValueError("protect_during_run must be true, false or null")
    if follow_dew is not UNCHANGED:
        if not isinstance(follow_dew, bool):
            raise ValueError("follow_dew must be true or false")

    store = _load_store()
    key = _key(profile_id)
    bucket = store.get(key)
    if not isinstance(bucket, dict):
        bucket = {}
    entry = dict(_entry(store, key, port_id))

    if protect_during_run is not UNCHANGED:
        entry["protect_during_run"] = protect_during_run
    if follow_dew is not UNCHANGED:
        entry["follow_dew"] = bool(follow_dew)

    bucket[_port_key(port_id)] = entry
    store[key] = bucket
    path = switch_ports_path()
    write_json_atomic(path, store)
    # The file we may have complained about is now ours again.
    _corrupt_warned.discard(str(path))
    return _settings_from(entry)


def _is_protected(port, settings: dict) -> bool:
    """The effective answer for a port whose settings are already loaded."""
    stored = settings.get("protect_during_run")
    if stored is not None:
        return bool(stored)
    return bool(_PROTECTED_NAME.search(getattr(port, "name", "") or ""))


def is_protected(port, *, profile_id: str | None = None) -> bool:
    """Is this port protected during a run? Stored flag when set, else the name.

    Says nothing about whether a run is HAPPENING - that is ``refusal``'s and
    ``annotate``'s job. This is the standing policy for the port.
    """
    return _is_protected(port, port_settings(getattr(port, "id", None),
                                             profile_id=profile_id))


def refusal(port, *, run_active: bool, profile_id: str | None = None) -> str | None:
    """The sentence to refuse a switch of this port with, or None to allow it.

    ``run_active`` is ``bool(getattr(hub.engine, "running", False))`` - the same
    predicate ``hub.restart_blocker`` uses, and it is True through a PAUSE on
    purpose. A paused run still owns the camera and still intends to continue;
    cutting the mount's power under it loses the alignment the resume needs.
    """
    if not run_active:
        return None
    if not is_protected(port, profile_id=profile_id):
        return None
    return _REFUSAL.format(name=getattr(port, "name", "this port"))


def annotate(ports: list, *, run_active: bool,
             profile_id: str | None = None) -> list:
    """COPIES of ``ports`` with ``protect_during_run``, ``protected_now`` and
    ``follow_dew`` filled in from the store.

    COPIES, never the originals, for the reason ``api/redact.py`` gives about
    driver rows: the list a driver hands back can alias its own cached objects,
    so writing a derived field in place is a way to poison the driver's state
    from a read-only route. Nothing here belongs to the device - the device
    reports a port's value, not who is allowed to change it.

    ``protect_during_run`` on the copy is the STORED tri-state (``None`` when
    nobody has decided), not the effective answer: the client needs to be able to
    tell "unset, following the name" from "someone pinned this on", or its
    settings toggle cannot show what it is about to change. ``protected_now`` is
    the effective answer, already ANDed with the run.
    """
    store = _load_store()
    key = _key(profile_id)
    out = []
    for port in ports:
        settings = _settings_from(_entry(store, key, getattr(port, "id", None)))
        clone = copy.copy(port)
        clone.protect_during_run = settings["protect_during_run"]
        clone.follow_dew = settings["follow_dew"]
        clone.protected_now = bool(run_active and _is_protected(port, settings))
        out.append(clone)
    return out
