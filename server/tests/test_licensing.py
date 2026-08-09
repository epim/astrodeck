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
