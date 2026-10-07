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

import sys
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
def isolated_player_one(tmp_path, monkeypatch):
    """No Player One SDK reachable by any route: the vendored tree EMPTY,
    neither variable set, and the vendor installer's locations (a table the
    probe now reads too, #705) emptied, so a machine that really has the
    vendor's SDK installed cannot decide a test. The only route to a file is
    the one a test chooses to open."""
    from astrodeck.devices import sdk_paths
    from astrodeck.devices.cameras import player_one_sdk

    vendor = tmp_path / "empty-vendor"
    vendor.mkdir()
    monkeypatch.setattr(sdk_paths, "VENDOR_ROOT", vendor)
    monkeypatch.setitem(player_one_sdk._DLL_SPECS, "PlayerOneCamera.dll",
                        ([], player_one_sdk._EXPORTS))
    monkeypatch.delenv("PLAYERONE_SDK_DIR", raising=False)
    monkeypatch.delenv(_OPERATOR_FACING_ENV, raising=False)


@pytest.fixture
def fetched_player_one_sdk(isolated_player_one, tmp_path):
    """A directory holding what a fetched SDK would hold."""
    from astrodeck.devices import sdk_paths

    sdk = tmp_path / "fetched-sdk"
    sdk.mkdir()
    (sdk / sdk_paths.library_names("PlayerOneCamera")[0]).write_bytes(b"x")
    return sdk


def _player_one_satisfied() -> bool:
    return {r["id"]: r for r in lic.status()}["player_one_sdk"]["satisfied"]


def test_player_one_present_reads_the_loaders_env_var(
        fetched_player_one_sdk, monkeypatch):
    """The loader's variable, pointed at a fetched SDK, satisfies the probe.

    MUTANT shared_list_old_env_name (``env_var="PLAYERONE_SDK_DIR"`` in
    ``player_one_sdk._spec_candidates``, the one place the probe and the
    loader now both take the variable from) fails with ``assert False is
    True`` (``where False = _player_one_satisfied()``) on the last line."""
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

    MUTANT shared_list_old_env_name fails this one too, with ``assert True
    is False`` (``where True = _player_one_satisfied()``)."""
    monkeypatch.setenv("PLAYERONE_SDK_DIR", str(fetched_player_one_sdk))
    assert _player_one_satisfied() is False


def test_probe_and_loader_ask_for_the_same_variable(monkeypatch):
    """Both call sites pass ``sdk_paths.PLAYERONE_SDK_ENV`` and nothing else.

    The recorder stands in for ``candidates`` rather than the filesystem, so
    this holds on a box with no SDK and on one with three copies.

    Since #705 both call sites reach ``candidates`` through
    ``player_one_sdk.sdk_candidates()``, so a probe-only misspelling (the
    #632 defect) can no longer be written without editing the loader too; the
    test still holds what is left.

    MUTANT shared_list_old_env_name (``env_var="PLAYERONE_SDK_DIR"`` in
    ``player_one_sdk._spec_candidates``): ``At index 0 diff: ('playerone',
    'PLAYERONE_SDK_DIR') != ('playerone', 'ASTRODECK_PLAYERONE_SDK_DIR')``.
    MUTANT constant_renamed (``PLAYERONE_SDK_ENV = "PLAYERONE_SDK_DIR"``):
    ``assert 'PLAYERONE_SDK_DIR' == 'ASTRODECK_PLAYERONE_SDK_DIR'``, and the
    two probe tests above fail with it."""
    from astrodeck.devices import sdk_paths
    from astrodeck.devices.cameras import player_one_sdk

    assert sdk_paths.PLAYERONE_SDK_ENV == _OPERATOR_FACING_ENV
    seen: list[tuple[str, str | None]] = []
    extras: list[list[str]] = []

    def spy(vendor, stem, *, env_var=None, extra=None):
        seen.append((vendor, env_var))
        extras.append(list(extra or []))
        return []

    monkeypatch.setattr(sdk_paths, "candidates", spy)
    lic._player_one_present()
    player_one_sdk._find_dll("PlayerOneCamera.dll")
    assert seen == [("playerone", sdk_paths.PLAYERONE_SDK_ENV)] * 2
    # and the same known-install alternatives, which the probe once left off
    # (#705): none at all would be the probe's old call.
    assert extras[0] and extras[0] == extras[1], extras


# ------------- the probe and the loader walk ONE list of places (#705)
#
# #705. The Credits probe called ``sdk_paths.candidates`` with no ``extra``,
# while the loader also tries the vendor installer's own locations
# (``_DLL_SPECS``), so an SDK the vendor's installer put down opened a working
# camera under a row that said "not satisfied": the class of the env-var
# disagreement above, two readers answering one question from two lists. Both
# now read ``player_one_sdk.sdk_candidates()``, which reads ``_DLL_SPECS`` at
# call time, so the table is the one place the alternatives are written. These
# tests move the table into ``tmp_path``, which is why they hold on a machine
# that really has the vendor's SDK installed.

_INSTALLER_DETAIL = ("installed by the vendor's installer "
                     "(redistribution ruling pending, #632)")


@pytest.fixture
def installer_path(isolated_player_one, tmp_path, monkeypatch):
    """The ONE place the vendor installer's locations are written, pointed at
    a file in ``tmp_path`` that does not exist yet."""
    from astrodeck.devices.cameras import player_one_sdk

    installed = tmp_path / "Program Files" / "PlayerOne" / "PlayerOneCamera.dll"
    monkeypatch.setitem(player_one_sdk._DLL_SPECS, "PlayerOneCamera.dll",
                        ([str(installed)], player_one_sdk._EXPORTS))
    return installed


def _put_down(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def _player_one_row() -> dict:
    return {r["id"]: r for r in lic.status()}["player_one_sdk"]


def test_an_sdk_the_vendors_installer_put_down_satisfies_the_probe(
        installer_path):
    """An installer location, and nothing else, is enough.

    MUTANT probe_ignores_the_installer_locations (``for path in
    player_one_sdk.sdk_candidates()`` in ``_player_one_source`` filtered to
    exclude ``installs``, which is what the probe did before): RED
    (observed): ``assert False is True`` (``where False =
    _player_one_satisfied()``) on the last assertion."""
    assert _player_one_satisfied() is False, (
        "the baseline must be unsatisfied, or the assertion below proves nothing")
    _put_down(installer_path)
    assert _player_one_satisfied() is True


def test_a_directory_by_the_sdks_name_is_not_an_sdk(
        installer_path, monkeypatch):
    """The probe's test is the loader's own, ``is_file``, not ``exists``: a
    folder that happens to carry the library's name satisfies the second and
    can never be loaded, so a probe that asked ``exists`` would answer "in use"
    for a camera that cannot open (the disagreement #705 is about, in the other
    direction). The loader is asked too, with the load itself stubbed to
    succeed, so the only thing that can refuse the folder is the file test.

    MUTANT probe_asks_exists (``path.is_file()`` replaced by ``path.exists()``
    in ``_player_one_source``): RED (observed): ``assert True is False``
    (``where True = _player_one_satisfied()``)."""
    from astrodeck.devices.cameras import player_one_sdk

    installer_path.mkdir(parents=True)
    monkeypatch.setattr(player_one_sdk, "_loads_with_exports",
                        lambda path, exports: object())
    assert player_one_sdk._find_dll("PlayerOneCamera.dll") is None
    assert _player_one_satisfied() is False
    assert _player_one_row()["detail"] is None


def test_the_row_says_where_an_installed_sdk_came_from(installer_path):
    """It counts as in use, because the camera opens from it, and the row
    says it was the vendor's installer that put it there and that the
    redistribution ruling is still open: whether that counts as "fetched" is
    the owner's (#632), so the row states the fact and not the verdict.

    MUTANT detail_never_says (``if _player_one_source() ==
    _BY_THE_VENDORS_INSTALLER`` replaced by ``if False`` in
    ``_player_one_detail``): RED (observed): ``assert None == "installed by
    the vendor's installer (redistribution ruling pending, #632)"``."""
    _put_down(installer_path)
    row = _player_one_row()
    assert row["satisfied"] is True
    assert row["detail"] == _INSTALLER_DETAIL


def test_a_fetched_sdk_is_not_described_as_the_installers(
        installer_path, fetched_player_one_sdk, monkeypatch):
    """With both an installed copy and the operator's directory present, the
    loader takes the operator's directory first, so the row must not say the
    installer's copy is what is in use.

    MUTANT everything_is_the_installers (``else "other"`` replaced by ``else
    "installer"`` in ``_player_one_source``): RED (observed): ``assert
    "installed by the vendor's installer (redistribution ruling pending,
    #632)" is None``."""
    _put_down(installer_path)
    monkeypatch.setenv(_OPERATOR_FACING_ENV, str(fetched_player_one_sdk))
    row = _player_one_row()
    assert row["satisfied"] is True
    assert row["detail"] is None


def test_every_row_carries_a_detail_and_only_the_installed_one_says_anything(
        isolated_player_one):
    rows = lic.status()
    assert all("detail" in r for r in rows), rows
    assert [r["detail"] for r in rows] == [None] * len(rows)


def test_the_loader_loads_what_the_probe_found(installer_path, monkeypatch):
    """The loader, handed only the installer's location, loads from it.

    MUTANT loader_ignores_the_installer_locations (``for c in
    _spec_candidates(basename)`` in ``_find_dll`` filtered to exclude
    ``system_install_paths()``): RED (observed): ``assert None is <object
    object at ...>``."""
    from astrodeck.devices.cameras import player_one_sdk

    _put_down(installer_path)
    sentinel = object()
    tried: list = []

    def load(path, exports):
        tried.append(path)
        return sentinel
    monkeypatch.setattr(player_one_sdk, "_loads_with_exports", load)
    assert lic._player_one_present() is True
    assert player_one_sdk._find_dll("PlayerOneCamera.dll") is sentinel
    assert tried == [installer_path]


@pytest.mark.skipif(sys.platform != "win32", reason=(
    "the vendor installer's alternatives (_DLL_SPECS) and the .dll name exist "
    "only on Windows; on Linux the library is libPlayerOneCamera.so, so the "
    "operator directory's .dll is not a candidate (CI run 37665144132)"))
def test_the_probe_and_the_loader_try_the_same_files_in_the_same_order(
        isolated_player_one, tmp_path, monkeypatch):
    """Two alternatives and an operator directory, all present: the loader
    tries every one, in ``sdk_candidates()``'s order, because the files it is
    handed fail to load; and ``sdk_candidates()`` is the list the probe walks.
    The alternatives are the table's, written once."""
    from astrodeck.devices.cameras import player_one_sdk

    first, second = (tmp_path / "A" / "PlayerOneCamera.dll",
                     tmp_path / "B" / "PlayerOneCamera.dll")
    monkeypatch.setitem(player_one_sdk._DLL_SPECS, "PlayerOneCamera.dll",
                        ([str(first), str(second)], player_one_sdk._EXPORTS))
    operator = tmp_path / "operator"
    _put_down(operator / "PlayerOneCamera.dll")
    _put_down(first)
    _put_down(second)
    monkeypatch.setenv(_OPERATOR_FACING_ENV, str(operator))
    tried: list = []
    monkeypatch.setattr(player_one_sdk, "_loads_with_exports",
                        lambda path, exports: tried.append(path))
    assert player_one_sdk._find_dll("PlayerOneCamera.dll") is None
    present = [p for p in player_one_sdk.sdk_candidates() if p.is_file()]
    assert tried == present, (tried, present)
    assert tried[0] == operator / "PlayerOneCamera.dll"
    assert tried[-2:] == [first, second]
    assert player_one_sdk.system_install_paths() == [first, second]


@pytest.mark.parametrize("module", [
    "astrodeck.devices.cameras.player_one_sdk",
    "astrodeck.devices.cameras.zwo_asi_sdk",
    "astrodeck.devices.zwo_sdk",
])
def test_no_loader_keeps_an_uncalled_second_finder(module):
    """Each of the three loaders kept a ``_find_dll_legacy`` that nothing
    called and that spelled the search order out a second time, which is how
    a stale literal hides (#705, and the third spelling of the env var in
    #632). One finder each.

    MUTANT legacy_finder_back (``def _find_dll_legacy`` added to ``zwo_sdk``):
    RED (observed): ``AssertionError: assert ['_find_dll_legacy'] == []``."""
    import importlib

    mod = importlib.import_module(module)
    assert [n for n in vars(mod)
            if n.startswith("_find_dll") and n != "_find_dll"] == []


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
