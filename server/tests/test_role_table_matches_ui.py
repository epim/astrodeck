"""The UI's copy of the role table must equal the server's.

``ui/src/lib/caps.ts`` hand-mirrors ``ROLES_CAP`` and says "keep in sync" in a
comment. A comment is not a mechanism, and it had already failed: the TS table
omitted ``view.site_derived`` from BOTH operator and admin, and the TS
``Capability`` union did not contain the string at all — so every lock note
derived from that table was answering "who can do this?" from a table that had
drifted from the one the server enforces.

That is the failure this project keeps re-finding under different names: two
correct-looking definitions of one rule, only one of which gets the next edit.
Parsing the TS here is not elegant; it is the cheapest thing that FAILS when
they diverge, which is the only property that matters.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from astrodeck.auth.capabilities import ALL_CAPS, ROLES, ROLES_CAP

CAPS_TS = Path(__file__).resolve().parents[2] / "ui" / "src" / "lib" / "caps.ts"
TYPES_TS = Path(__file__).resolve().parents[2] / "ui" / "src" / "types.ts"

pytestmark = pytest.mark.skipif(
    not CAPS_TS.exists(), reason="ui/ not present (server-only checkout)")


def _ts_role_caps() -> dict[str, set[str]]:
    """Parse ``const ROLE_CAPS: Record<PrincipalRole, readonly Capability[]>``.

    Strips // comments FIRST: the block above each entry quotes capability
    names in prose ("NOT power/config.*/media"), and a parser that reads those
    as members would report a table far richer than the one that ships — a
    test that passes for the wrong reason.
    """
    src = CAPS_TS.read_text(encoding="utf-8")
    body = src.split("const ROLE_CAPS", 1)[1].split("\n};", 1)[0]
    body = re.sub(r"//[^\n]*", "", body)
    out: dict[str, set[str]] = {}
    for role, arr in re.findall(r"(\w+)\s*:\s*\[(.*?)\]", body, re.S):
        out[role] = set(re.findall(r'"([\w.]+)"', arr))
    return out


def _ts_union(name: str) -> set[str]:
    src = TYPES_TS.read_text(encoding="utf-8")
    body = src.split(f"export type {name} =", 1)[1].split(";", 1)[0]
    return set(re.findall(r'"([\w.]+)"', body))


def test_the_parser_is_not_fooled_by_prose():
    """Guard the guard. If this parser silently returned {} or swept up the
    capability names quoted in the comments, every assertion below would pass
    no matter how far the tables drifted."""
    table = _ts_role_caps()
    assert set(table) == set(ROLES), f"parsed roles {sorted(table)}"
    assert table["viewer"] == {"view.status", "view.preview"}, table["viewer"]
    # The comment above `operator` contains the words power/config/media. If
    # those leaked in, this is how we find out.
    assert not any(c.startswith(("config.", "control.power"))
                   for c in table["operator"]), table["operator"]


@pytest.mark.parametrize("role", ROLES)
def test_ui_role_caps_match_the_server(role):
    ts = _ts_role_caps()
    assert role in ts, f"ui/src/lib/caps.ts has no entry for role {role!r}"
    server = set(ROLES_CAP[role])
    missing = server - ts[role]
    extra = ts[role] - server
    assert not missing, (
        f"the UI table under-states {role}: missing {sorted(missing)} — every "
        f"lock note derived from it will name the wrong roles")
    assert not extra, (
        f"the UI table over-states {role}: claims {sorted(extra)} the server "
        f"does not grant — the UI would offer a control the server refuses")


def test_the_ui_capability_union_covers_every_server_capability():
    missing = ALL_CAPS - _ts_union("Capability")
    assert not missing, (
        f"ui/src/types.ts Capability is missing {sorted(missing)} — a cap the "
        f"TS type cannot even name cannot be checked by the UI")


def test_the_ui_role_union_matches_the_server_roles():
    assert _ts_union("PrincipalRole") == set(ROLES)
