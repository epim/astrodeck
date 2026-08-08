"""What the console shows is what the rig runs — the same layer, on both sides.

THE PROMISE: a value on screen describes THIS rig, now.

THE DEFECT CLASS (broken promises, class A — the display reads a different
source than execution). ``/api/config`` returns the GLOBAL config block, and the
ACTIVE PROFILE's block beats it at read time. Every panel bound to the global
block therefore renders a value that is right in every case anyone would check
and wrong in the one case they would not. A profile-pinned simulator drove the
polar aligner for twelve nights that way, with the console reading "AstroDeck
native" the whole time, and the same split was later found sitting unnoticed in
FIVE optics keys: Settings, the Atlas FOV rectangle and the mosaic altitudes
read global while the plate solver, the FITS ``TELESCOP`` card and native TPPA
read the profile.

``provenance.py`` + ``config.effective`` + ``ui/src/lib/effective.ts`` are the
fix. ``test_config_provenance.py`` pins that the provenance block is CORRECT.
This file pins the three ways it can quietly stop being USED — none of which
that file can see, because each of them keeps the block perfectly correct:

  1. a server execution path reading ``cfg().optics`` / ``cfg().providers``
     directly again (that is literally how all five instances were written);
  2. a UI surface binding the raw global block with no reference to the
     winning layer;
  3. the two ends drifting apart on the KEY VOCABULARY — the server derives its
     key list from the pydantic model, the client hard-codes its own, and a
     field added to ``Optics`` is then displayed from the losing layer by a
     client that never heard of it. No error, no warning: exactly the shape of
     the original bug, one field over.

DELIBERATELY MECHANICAL. These are greps with allowlists, not proofs. A grep
cannot tell a display from a write — so the rule is "a file that touches the
global block must also consult the winner", which is weaker than the truth and
strong enough to catch a new panel wired the old way.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from astrodeck.config import PROVIDER_CAPABILITIES, Optics
from astrodeck.provenance import OPTICS_KEYS

_SERVER = Path(__file__).resolve().parents[1]
_PKG = _SERVER / "astrodeck"
_UI_SRC = _SERVER.parent / "ui" / "src"
_EFFECTIVE_TS = _UI_SRC / "lib" / "effective.ts"


# ================================================== 1. the server side

#: Reads the GLOBAL block directly, and may. Everything else must go through
#: ``profiles.resolve_optics`` / ``Hub.effective_optics`` / ``providers.resolve``,
#: which apply the profile layer.
_MAY_READ_THE_GLOBAL_BLOCK = {
    "config.py":
        "owns AppConfig; the global block IS its subject",
    "provenance.py":
        "reports every layer by definition — it is what makes the losing "
        "layer visible instead of invisible",
    "providers.py":
        "override_with_layer is the ONE implementation of profile-beats-global "
        "for capability routing; a second one is the bug",
    "profiles.py":
        "resolve_optics is the ONE implementation of profile-beats-global for "
        "optics, and returns the global block when a profile carries none",
}

_GLOBAL_READ = re.compile(r"cfg\(\)\.(optics|providers)\b")


def _server_global_reads() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_PKG).as_posix()
        hits = [f"{rel}:{n}: {line.strip()}"
                for n, line in enumerate(
                    path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
                if _GLOBAL_READ.search(line)]
        if hits:
            out[path.name] = hits
    return out


def test_the_scanner_still_finds_the_reads_it_is_meant_to_find():
    """Positive control. Every assertion below is "nothing matched outside the
    allowlist", which a regex that quietly stopped matching also satisfies."""
    found = _server_global_reads()
    assert "providers.py" in found and "provenance.py" in found, (
        f"the global-block scanner matched nothing in the two modules that "
        f"certainly read it — the pattern has drifted: {sorted(found)}")


def test_no_execution_path_reads_the_layer_the_profile_beats():
    offenders = {name: hits for name, hits in _server_global_reads().items()
                 if name not in _MAY_READ_THE_GLOBAL_BLOCK}
    assert offenders == {}, (
        "these modules read the GLOBAL config block, which an active profile "
        "overrides — so on a rig with a profile they act on a value the profile "
        "has already replaced, and the console shows one of the two with no "
        "tell. Read optics through profiles.resolve_optics() (or "
        "Hub.effective_optics()) and capability routing through "
        "providers.resolve():\n  "
        + "\n  ".join(h for hits in offenders.values() for h in hits))


def test_the_allowlist_still_describes_real_files():
    for name, why in _MAY_READ_THE_GLOBAL_BLOCK.items():
        assert why.strip(), f"{name} is allowlisted with no reason"
        assert (_PKG / name).exists(), (
            f"{name} is allowlisted but no longer exists — drop it, or the "
            f"list starts excusing a file that could come back different")


# ================================================== 2. the client side

#: UI files that touch the raw global block WITHOUT consulting the winning
#: layer, with the reason each is legitimate. Every other file must reference
#: `effective` (directly or via lib/effective.ts's helpers) if it names
#: `config.optics` / `config.providers` at all.
_UI_MAY_READ_THE_GLOBAL_BLOCK = {
    "EquipmentView.tsx":
        "snapshots the CURRENT GLOBAL routing into a new profile so Activate "
        "can restore it — the global block is the subject, not a display",
    "providerWrite.ts":
        "computes what to WRITE to the global block; its docstring already "
        "requires the raw block and refuses a panel draft",
    "backends.ts":
        "types the write payload; names the keys, reads no value",
    "types.ts":
        "the type declaration + the comment stating this very rule",
}

_UI_GLOBAL_READ = re.compile(r"config\??\.(optics|providers)\b")


def _ui_files():
    return [p for ext in ("*.ts", "*.tsx") for p in _UI_SRC.rglob(ext)
            if "__tests__" not in p.parts]


def test_the_ui_scanner_is_looking_at_a_real_tree():
    files = _ui_files()
    assert len(files) > 50, f"only {len(files)} UI sources under {_UI_SRC}"
    assert _EFFECTIVE_TS.exists(), _EFFECTIVE_TS


def test_no_ui_surface_binds_the_global_block_without_the_winner():
    offenders = []
    for path in sorted(_ui_files()):
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Strip comments: this file, and several of the panels, DISCUSS the
        # global block at length in prose. A scanner that grades comments
        # produces noise, and noise is how an allowlist becomes a habit.
        code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
        if not _UI_GLOBAL_READ.search(code):
            continue
        if "effective" in code.lower():
            continue
        if path.name in _UI_MAY_READ_THE_GLOBAL_BLOCK:
            continue
        offenders.append(path.relative_to(_UI_SRC).as_posix())
    assert offenders == [], (
        "these UI files render a value from the GLOBAL config block and never "
        "consult config.effective, so on a rig with an active profile they show "
        "a setting the rig is not using — the twelve-day polar bug. Read the "
        "winner with valueOf()/entryOf() from lib/effective.ts, and keep the "
        "raw block only as the bootstrap fallback:\n  " + "\n  ".join(offenders))


def test_the_ui_allowlist_still_describes_real_files():
    names = {p.name for p in _ui_files()}
    for name, why in _UI_MAY_READ_THE_GLOBAL_BLOCK.items():
        assert why.strip(), f"{name} excused with no reason"
        assert name in names, f"{name} is allowlisted but no longer exists"


# ================================================== 3. the two ends agree

def _ts_string_list(name: str) -> list[str]:
    """The entries of a ``export const NAME = [...] as const`` list."""
    src = _EFFECTIVE_TS.read_text(encoding="utf-8")
    m = re.search(rf"export const {name} = \[(.*?)\] as const", src, flags=re.S)
    assert m, f"{name} is no longer declared in lib/effective.ts"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_the_client_knows_every_provider_capability_the_server_publishes():
    """A capability the client has never heard of is displayed from the losing
    layer, silently: ``entryOf`` returns null for a key nobody asks about, and
    null degrades to the raw global value."""
    assert _ts_string_list("PROVIDER_CAPS") == list(PROVIDER_CAPABILITIES), (
        "ui/src/lib/effective.ts PROVIDER_CAPS and server PROVIDER_CAPABILITIES "
        "disagree. A key only the server knows is rendered from global config "
        "with no override tell.")


def test_the_client_knows_every_optics_key_the_server_publishes():
    """The server derives its list from the pydantic model, so adding a field to
    ``Optics`` publishes a new provenance key automatically. The client's list
    is hand-written and does not move — which makes the new field the one place
    the console still shows the layer the profile beat."""
    assert _ts_string_list("OPTICS_KEYS") == list(OPTICS_KEYS), (
        "a field was added to config.Optics (or reordered) and "
        "ui/src/lib/effective.ts OPTICS_KEYS was not updated, so that field is "
        "displayed from the GLOBAL block while the active profile's value is "
        "what the rig uses.")


def test_the_optics_key_list_is_derived_from_the_model_not_typed_out():
    """The server half must stay automatic. If OPTICS_KEYS ever becomes a
    hand-written tuple, both ends are hand-written and the test above degrades
    into two lists agreeing about being wrong."""
    assert tuple(OPTICS_KEYS) == tuple(Optics.model_fields), (
        "provenance.OPTICS_KEYS no longer equals Optics.model_fields — a "
        "profile-overridable field now has no provenance entry at all")


@pytest.mark.parametrize("key", list(OPTICS_KEYS) + list(PROVIDER_CAPABILITIES))
def test_every_published_key_is_reachable_by_the_client_helpers(key):
    """The key STRINGS have to match too, not just the field names: the client
    builds ``providers.<cap>`` / ``optics.<field>`` itself, so a change to the
    server's naming breaks the lookup with no type error on either side."""
    src = _EFFECTIVE_TS.read_text(encoding="utf-8")
    assert 'providerKey = (cap: ProviderCap): string => `providers.${cap}`' in src
    assert 'opticsKey = (key: OpticsKey): string => `optics.${key}`' in src
