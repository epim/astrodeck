# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Things we are not licensed to redistribute (#198, #199, #200).

THE PROMISE: AstroDeck does not hand anyone a file it has no right to hand
them, and does not call a service whose terms do not cover this use — while
staying usable, which means fetching on the operator's own machine under the
operator's own grant, or asking first.

THE TRAP THIS EXISTS BECAUSE OF: `vendor/playerone/README.md` asserted that the
SDK licence "permits redistribution". Its grant paragraph contains no
distribution verb at all. That sentence was an interpretation, written once,
read as settled fact for weeks, and six binaries shipped on it. So the registry
carries the licensor's OWN WORDS separately from our reading of them, and these
tests hold that separation open — because the day the quote gets "tidied" into
the reading is the day the claim becomes unfalsifiable again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import astrodeck.licensing as lic


@pytest.fixture(autouse=True)
def isolated_consent(tmp_path, monkeypatch):
    """A developer's real acknowledgment must never decide a test, in EITHER
    direction — a granted one would hide a broken gate, and a missing one would
    fail a suite for a reason that has nothing to do with the code."""
    monkeypatch.setattr(lic, "CONSENT_FILE", tmp_path / "restricted_consent.json")


# ------------------------------------------------------------ the registry

def test_every_entry_quotes_the_licensor_rather_than_summarising_them():
    for a in lic.REGISTRY:
        assert a.quote.strip(), f"{a.id} has no quote"
        assert "—" in a.quote, (
            f"{a.id}'s quote does not attribute itself; an unattributed quote "
            f"is a summary with quotation marks around it")
        assert a.reading.strip() and a.reading != a.quote, (
            f"{a.id} does not separate our READING from their WORDS — which is "
            f"exactly how the Player One redistribution claim became a 'fact'")


def test_every_entry_says_what_stops_working():
    """A capability that goes dark with no stated consequence gets filed as a
    bug, and then 'fixed' by whoever finds the switch."""
    for a in lic.REGISTRY:
        assert a.without.strip(), f"{a.id} does not say what is lost without it"
        assert a.source.strip(), f"{a.id} does not say where to get it"


def test_the_three_findings_are_all_here():
    ids = {a.id for a in lic.REGISTRY}
    assert ids == {"dss2", "player_one_sdk", "astrospheric"}


# -------------------------------------------------------------- consent

def test_nothing_is_acknowledged_until_somebody_acknowledges_it():
    assert lic.is_acknowledged("astrospheric") is False
    assert lic.consent("astrospheric") is None


def test_an_acknowledgment_records_who_and_when():
    """WHO and WHEN, not a boolean. The point of writing this down is that a
    later reader can go and ask the person what they were agreeing to; a bare
    flag cannot be asked anything."""
    row = lic.acknowledge("astrospheric", by="bear", note="my own rig")
    assert row["by"] == "bear" and row["at"] > 0 and row["note"] == "my own rig"
    assert lic.is_acknowledged("astrospheric") is True


def test_it_can_be_taken_back():
    lic.acknowledge("astrospheric", by="bear")
    assert lic.withdraw("astrospheric") is True
    assert lic.is_acknowledged("astrospheric") is False
    assert lic.withdraw("astrospheric") is False, "a second withdraw invents one"


def test_a_fetch_asset_cannot_be_agreed_away():
    """The two remedies are not interchangeable, and this is the guard that
    keeps them apart. DSS2's problem is that we would be DISTRIBUTING the
    tiles; no amount of the operator agreeing to something changes what we
    shipped. Letting a consent stand in for not-shipping-a-file is how a
    compliance mechanism turns into a checkbox."""
    with pytest.raises(ValueError, match="must not ship"):
        lic.acknowledge("dss2", by="bear")
    with pytest.raises(ValueError, match="must not ship"):
        lic.acknowledge("player_one_sdk", by="bear")


def test_an_unknown_id_is_refused_rather_than_recorded():
    with pytest.raises(ValueError, match="unknown"):
        lic.acknowledge("not-a-thing", by="bear")


# --------------------------------------------------------------- status

def test_status_answers_the_only_question_the_operator_has():
    rows = {r["id"]: r for r in lic.status()}
    assert rows["astrospheric"]["satisfied"] is False
    lic.acknowledge("astrospheric", by="bear")
    rows = {r["id"]: r for r in lic.status()}
    assert rows["astrospheric"]["satisfied"] is True
    assert rows["astrospheric"]["consent"]["by"] == "bear"
    # A fetch asset's satisfaction is a fact about this DISK, never about a
    # consent — so it must not have moved when one was recorded.
    assert rows["dss2"]["consent"] is None


def test_status_carries_the_quote_to_the_screen():
    """The Credits panel renders these verbatim. If the quote stopped reaching
    the client the page would show only our READING of each licence, which is
    the failure mode this whole module is shaped around."""
    for r in lic.status():
        assert r["quote"] and r["reading"] and r["quote"] != r["reading"]


# ------------------------------- the probe and the loader read ONE variable
#
# #632 part A. The Credits screen's "is the fetch already done?" probe asked
# for ``PLAYERONE_SDK_DIR`` while the loader reads
# ``ASTRODECK_PLAYERONE_SDK_DIR``, so an operator who did exactly what the
# loader documents got a working camera and a Credits screen that said "not
# satisfied". The unprefixed name appeared nowhere else in the repo. It is now
# one constant in ``devices/sdk_paths.py`` that both import.

#: The name an operator has in their environment. Spelled out here, not read
#: from the constant, so renaming it (even consistently in both places) is a
#: decision somebody makes on purpose rather than a refactor that silently
#: strands every existing setup.
_OPERATOR_FACING_ENV = "ASTRODECK_PLAYERONE_SDK_DIR"


@pytest.fixture
def fetched_player_one_sdk(tmp_path, monkeypatch):
    """A directory holding what a fetched SDK would hold, with the vendored
    tree EMPTY and neither variable set, so the only route to the file is the
    one a test chooses to open."""
    from astrodeck.devices import sdk_paths

    sdk = tmp_path / "fetched-sdk"
    sdk.mkdir()
    (sdk / sdk_paths.library_names("PlayerOneCamera")[0]).write_bytes(b"x")
    vendor = tmp_path / "empty-vendor"
    vendor.mkdir()
    monkeypatch.setattr(sdk_paths, "VENDOR_ROOT", vendor)
    monkeypatch.delenv("PLAYERONE_SDK_DIR", raising=False)
    monkeypatch.delenv(_OPERATOR_FACING_ENV, raising=False)
    return sdk


def _player_one_satisfied() -> bool:
    return {r["id"]: r for r in lic.status()}["player_one_sdk"]["satisfied"]


def test_player_one_present_reads_the_loaders_env_var(
        fetched_player_one_sdk, monkeypatch):
    """The loader's variable, pointed at a fetched SDK, satisfies the probe.

    MUTANT player_one_probe_old_env_name: ``_player_one_present`` passing
    ``env_var="PLAYERONE_SDK_DIR"`` again fails with ``assert False is True``
    (``where False = _player_one_satisfied()``) on the last line."""
    assert _player_one_satisfied() is False, (
        "the baseline must be unsatisfied, or the assertion below proves nothing")
    monkeypatch.setenv(_OPERATOR_FACING_ENV, str(fetched_player_one_sdk))
    assert _player_one_satisfied() is True


def test_player_one_probe_ignores_the_unprefixed_name(
        fetched_player_one_sdk, monkeypatch):
    """The two could disagree in BOTH directions: the unprefixed name was
    probed but never loaded, so the screen could say "satisfied" over a camera
    that would not open. The loader does not read it, so the probe must not
    either.

    MUTANT player_one_probe_old_env_name fails this one too, with
    ``assert True is False`` (``where True = _player_one_satisfied()``)."""
    monkeypatch.setenv("PLAYERONE_SDK_DIR", str(fetched_player_one_sdk))
    assert _player_one_satisfied() is False


def test_probe_and_loader_ask_for_the_same_variable(monkeypatch):
    """Both call sites pass ``sdk_paths.PLAYERONE_SDK_ENV`` and nothing else.

    The recorder stands in for ``candidates`` rather than the filesystem, so
    this holds on a box with no SDK and on one with three copies.

    MUTANT player_one_probe_old_env_name: ``At index 0 diff: ('playerone',
    'PLAYERONE_SDK_DIR') != ('playerone', 'ASTRODECK_PLAYERONE_SDK_DIR')``.
    MUTANT loader_reads_other_name (``env_var="ASTRODECK_PLAYERONE_DIR"`` in
    ``_find_dll``): ``At index 1 diff: ('playerone',
    'ASTRODECK_PLAYERONE_DIR') != ('playerone', 'ASTRODECK_PLAYERONE_SDK_DIR')``.
    MUTANT constant_renamed (``PLAYERONE_SDK_ENV = "PLAYERONE_SDK_DIR"``):
    ``assert 'PLAYERONE_SDK_DIR' == 'ASTRODECK_PLAYERONE_SDK_DIR'``, and the
    two probe tests above fail with it."""
    from astrodeck.devices import sdk_paths
    from astrodeck.devices.cameras import player_one_sdk

    assert sdk_paths.PLAYERONE_SDK_ENV == _OPERATOR_FACING_ENV
    seen: list[tuple[str, str | None]] = []

    def spy(vendor, stem, *, env_var=None, extra=None):
        seen.append((vendor, env_var))
        return []

    monkeypatch.setattr(sdk_paths, "candidates", spy)
    lic._player_one_present()
    player_one_sdk._find_dll("PlayerOneCamera.dll")
    assert seen == [("playerone", sdk_paths.PLAYERONE_SDK_ENV)] * 2


def test_the_legacy_flat_loader_reads_the_same_variable(
        tmp_path, monkeypatch):
    """``_find_dll_legacy`` carried a third spelling of the name as a string
    literal. Nothing calls it today, which is how a literal gets to be wrong
    unnoticed; it reads the constant like the others.

    MUTANT legacy_loader_old_literal (``os.environ.get("PLAYERONE_SDK_DIR")``
    in ``_find_dll_legacy``): ``assert None is <object object at ...>``."""
    from astrodeck.devices.cameras import player_one_sdk

    fetched = tmp_path / "fetched-sdk"
    fetched.mkdir()
    (fetched / "PlayerOneCamera.dll").write_bytes(b"x")
    # The vendored tree and the known-install alternatives are emptied: this
    # checkout really has a vendored PlayerOneCamera.dll, and with it reachable
    # the sentinel would come back through THAT candidate whatever the
    # variable's name, so the test could not fail.
    empty_vendor = tmp_path / "empty-vendor"
    empty_vendor.mkdir()
    monkeypatch.setattr(player_one_sdk, "_VENDOR_DIR", empty_vendor)
    monkeypatch.setitem(player_one_sdk._DLL_SPECS, "PlayerOneCamera.dll",
                        ([], []))
    sentinel = object()
    monkeypatch.setattr(player_one_sdk, "_loads_with_exports",
                        lambda path, exports: sentinel)
    monkeypatch.delenv(_OPERATOR_FACING_ENV, raising=False)
    assert player_one_sdk._find_dll_legacy("PlayerOneCamera.dll") is None, (
        "the baseline must find nothing, or the assertion below proves nothing")
    monkeypatch.setenv(_OPERATOR_FACING_ENV, str(fetched))
    assert player_one_sdk._find_dll_legacy("PlayerOneCamera.dll") is sentinel


# ---------------------- the prose must say what the licence and the build say
#
# #632 part A. Three sentences told a reader that Player One's SDK may be
# redistributed, while the grant has no distribution verb and the build ships
# the files anyway. It is the same class the August audit named: a registry
# sentence contradicted by the build.

_REPO = Path(__file__).resolve().parents[2]
_PLAYER_ONE_PROSE = (
    "server/astrodeck/vendor/playerone/README.md",
    "docs/hardware/player-one-sdk-licensing.md",
    "server/astrodeck/devices/cameras/player_one_sdk.py",
)
#: Case-sensitive on purpose: the README quotes the OLD claim, in lower case
#: and inside a correction, to say why it was withdrawn. That is history, not
#: the claim.
_RETIRED_CLAIMS = (
    "PERMITS redistribution",
    "releases carry no Player One binary",
    "squarely inside the grant",
    "MIT-style license",
)


@pytest.mark.parametrize("rel", _PLAYER_ONE_PROSE)
def test_no_doc_claims_player_one_is_permitted(rel):
    """None of the three files repeats a claim the build contradicts, and each
    points a reader at the open ruling.

    MUTANT retired_permits_claim: putting "PERMITS redistribution" back into
    the doc fails with ``AssertionError: docs/hardware/player-one-sdk-
    licensing.md still says 'PERMITS redistribution'``.
    MUTANT retired_no_binary_claim_readme: putting "releases carry no Player
    One binary" back into the README fails with ``AssertionError:
    server/astrodeck/vendor/playerone/README.md still says 'releases carry no
    Player One binary'``.
    MUTANT retired_mit_style_docstring: putting "MIT-style license" back into
    ``player_one_sdk.py`` fails with ``AssertionError:
    server/astrodeck/devices/cameras/player_one_sdk.py still says 'MIT-style
    license'``."""
    text = (_REPO / rel).read_text(encoding="utf-8")
    for phrase in _RETIRED_CLAIMS:
        assert phrase not in text, f"{rel} still says {phrase!r}"
    assert "#632" in text, (
        f"{rel} does not point at the open ruling, so a reader has no way to "
        f"learn the question is still open")


def test_the_licensing_doc_states_the_build_and_the_grant():
    """The doc a reader opens to settle the question says the two facts that
    decide it: the grant has no distribution verb, and which builds carry the
    files in the meantime."""
    text = (_REPO / "docs/hardware/player-one-sdk-licensing.md").read_text(
        encoding="utf-8")
    assert "no distribution verb" in text
    assert "pending" in text
    for artifact in ("frozen", "wheel", "Docker"):
        assert artifact in text, f"the doc does not say the {artifact} build carries them"
