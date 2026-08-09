"""Assets we are not licensed to redistribute, and what AstroDeck does instead.

WHAT THIS IS FOR. The 2026-08-08 licence audit found four things we ship that
nobody has granted us the right to ship (backlog
``2026-08-08-third-party-licences.md`` §1). One was closed by repointing a URL.
The other three are here. Until written permission arrives, the product must not
distribute them — and must still be usable, which means fetching them on the
operator's own machine under the operator's own grant, or asking before it uses
a service whose terms do not cover us.

TWO REMEDIES, because the problems are not the same shape:

  FETCH        the asset is fine to USE and not fine for US to REDISTRIBUTE.
               So we ship nothing and pull it, on this machine, from the party
               that publishes it. DSS2 sky tiles and the Player One SDK.

  ACKNOWLEDGE  there is no asset. The CLIENT is what the terms do not permit,
               and no amount of not-shipping-a-file fixes that. Astrospheric's
               API is scoped to "Astrospheric Professional members for use in
               personal projects" and we are a public product. So it stays inert
               until somebody with authority over this instance says, on the
               record, that this is such a use.

SCOPE OF A CONSENT: THE INSTANCE, NOT THE PERSON. One acknowledgment covers
everyone who uses this AstroDeck — viewers and operators do not each agree.
That is deliberate and was asked for explicitly: the thing being asserted is a
fact about the DEPLOYMENT ("this rig is one Pro member's personal project"),
not a promise by whoever happens to be logged in. Per-user consent would also
be a worse record, not a better one: it would collect agreements from people
who have no idea what the deployment is.

WHY THE REGISTRY QUOTES INSTEAD OF SUMMARISING. ``vendor/playerone/README.md``
said the licence "permits redistribution". It does not — its grant paragraph
contains no distribution verb at all — and that sentence was an interpretation
written down once and then read as a fact for weeks, with six binaries shipped
on it. So every entry here carries the licensor's OWN WORDS in ``quote``, and
our reading separately in ``reading``. A reader can disagree with us without
having to go and find the licence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from .persist import read_json_or, write_json_atomic
from .config import CONFIG_DIR

#: Where an acknowledgment is recorded. Its own file, not ``astrodeck.json``:
#: a factory reset of the CONFIG must not silently un-answer a legal question,
#: and a config import from another machine must not carry one in.
CONSENT_FILE = CONFIG_DIR / "restricted_consent.json"

Remedy = Literal["fetch", "acknowledge"]


@dataclass(frozen=True)
class RestrictedAsset:
    """One thing we may not redistribute, and what to do about it."""

    id: str
    title: str
    #: The licensor's own words. Never paraphrased — see the module docstring.
    quote: str
    #: Our reading of those words, kept separate so it can be disagreed with.
    reading: str
    remedy: Remedy
    #: Where the operator gets it themselves (fetch), or whose terms they are
    #: agreeing they meet (acknowledge).
    source: str
    #: What stops working until the remedy is applied. Stated plainly, because
    #: "unavailable" with no consequence attached reads as a bug.
    without: str
    #: Answers "is the fetch already done?" — None for an acknowledge remedy.
    present: Callable[[], bool] | None = field(default=None, compare=False)


#: ``requests``-free by design: this module is imported at startup and must not
#: pull a network stack in. The ``present`` probes are cheap filesystem checks.
def _dss2_present() -> bool:
    try:
        from .catalog import survey_pack
        return survey_pack.pack_present(RESTRICTED_SURVEY_ID) is not None
    except Exception:
        return False


def _player_one_present() -> bool:
    try:
        from .devices import sdk_paths
        return any(p.exists() for p in
                   sdk_paths.candidates("playerone", "PlayerOneCamera",
                                        env_var="PLAYERONE_SDK_DIR"))
    except Exception:
        return False


#: The survey whose tiles we may not redistribute — the HiPS id, matching
#: ``survey_pack.PACK_SLUGS``. Named once and read by both the probe above and
#: ``seed_bundled_pack``'s refusal, so "which survey is restricted" cannot be
#: answered differently in two places.
RESTRICTED_SURVEY_ID = "CDS/P/DSS2/color"


REGISTRY: tuple[RestrictedAsset, ...] = (
    RestrictedAsset(
        id="dss2",
        title="DSS2 sky survey imagery",
        quote=(
            "Scientists and educators conducting research, teaching (including "
            "textbooks), or other non-profit activities may use data from the "
            "copyrighted collections freely and without restriction… "
            "Commercial, for-profit use of the copyrighted collections is "
            "prohibited without written permission from the copyright "
            "holder(s). — STScI, on the Digitized Sky Surveys"),
        reading=(
            "That is a USE grant and says nothing about third-party "
            "redistribution. CDS, who host the tiles, permit mirroring only "
            "where the original copyright authorises it — the one condition "
            "that cannot be established here. So AstroDeck ships no tiles and "
            "you fetch them yourself, which is squarely inside the use grant "
            "above."),
        remedy="fetch",
        source="CDS hips2fits (the survey's own publisher)",
        without=("the Atlas draws its offline schematic sky instead of "
                 "photographic tiles"),
        present=_dss2_present,
    ),
    RestrictedAsset(
        id="player_one_sdk",
        title="Player One Camera SDK",
        quote=(
            "This SDK is only used for the secondary development of our "
            "company's cameras or other equipment. You can use our company's "
            "products and this SDK to develop any products without any "
            "restrictions. — Player One Astronomy, SDK license.txt"),
        reading=(
            "MIT-shaped — it closes with MIT's notice-retention clause and "
            "warranty disclaimer word for word — but its GRANT paragraph, "
            "quoted above, contains no distribution verb: not copy, publish, "
            "distribute, sublicense or sell. A runtime library is useless "
            "unless it ships, so redistribution is plausibly intended, and "
            "plausibly intended is not granted. Until they confirm it in "
            "writing, releases carry no Player One binary and this machine "
            "fetches the SDK from the vendor."),
        remedy="fetch",
        source="https://player-one-astronomy.com/service/software/",
        without="Player One cameras cannot be opened natively",
        present=_player_one_present,
    ),
    RestrictedAsset(
        id="astrospheric",
        title="Astrospheric seeing and transparency",
        quote=(
            "Unauthorized use, reproduction, distribution, or exploitation of "
            "Astrospheric's assets, APIs, or content outside of the Service is "
            "strictly prohibited. — Astrospheric Terms of Use. Their API "
            "documentation scopes the Data API to \"Astrospheric Professional "
            "members for use in personal projects\"."),
        reading=(
            "Nothing is being redistributed here, so there is no file to stop "
            "shipping: the CLIENT is the thing outside their scope. AstroDeck "
            "is a public product with an Astrospheric client in it. You supply "
            "your own key and your forecast is never cached beyond display — "
            "real mitigations, but facts, not a permission. So the client "
            "stays inert until somebody states that this deployment is a Pro "
            "member's personal project. One statement covers the whole "
            "instance; every user of this rig inherits it."),
        remedy="acknowledge",
        source="https://www.astrospheric.com",
        without=("the weather panel keeps its Open-Meteo forecast and shows no "
                 "seeing or transparency"),
    ),
)

_BY_ID = {a.id: a for a in REGISTRY}


def get(asset_id: str) -> RestrictedAsset | None:
    return _BY_ID.get(asset_id)


# ----------------------------------------------------------------- consent

def _load() -> dict:
    data = read_json_or(CONSENT_FILE, {})
    return data if isinstance(data, dict) else {}


def consent(asset_id: str) -> dict | None:
    """The recorded acknowledgment for ``asset_id``, or None.

    Carries WHO and WHEN. A consent with no author is not a record, it is a
    flag — and the whole point of writing this down is that a later reader can
    ask the person who agreed what they were agreeing to."""
    row = _load().get(asset_id)
    return row if isinstance(row, dict) and row.get("at") else None


def is_acknowledged(asset_id: str) -> bool:
    return consent(asset_id) is not None


def acknowledge(asset_id: str, *, by: str, note: str = "") -> dict:
    """Record an instance-wide acknowledgment. Raises for an unknown id, or for
    an asset whose remedy is to FETCH it — agreeing to terms is not a substitute
    for not shipping a file, and letting one stand in for the other is how a
    compliance mechanism becomes a checkbox."""
    asset = get(asset_id)
    if asset is None:
        raise ValueError(f"unknown restricted asset: {asset_id!r}")
    if asset.remedy != "acknowledge":
        raise ValueError(
            f"{asset_id!r} is not something to agree to — it is something we "
            f"must not ship. Fetch it from {asset.source} instead.")
    data = _load()
    row = {"at": time.time(), "by": str(by or "unknown"), "note": str(note)}
    data[asset_id] = row
    write_json_atomic(CONSENT_FILE, data)
    return row


def withdraw(asset_id: str) -> bool:
    """Remove an acknowledgment. Returns whether there was one.

    Present because a consent you cannot take back is not a consent. The
    integration goes inert again the moment this returns."""
    data = _load()
    if asset_id not in data:
        return False
    data.pop(asset_id)
    write_json_atomic(CONSENT_FILE, data)
    return True


# ------------------------------------------------------------------ status

def status() -> list[dict]:
    """Every restricted asset with its current state, for the Credits screen.

    ``satisfied`` answers the only question the operator actually has: is this
    working, and if not, what do I do? A fetch asset is satisfied once the file
    is on this machine; an acknowledge asset once somebody has said so."""
    out: list[dict] = []
    for a in REGISTRY:
        if a.remedy == "acknowledge":
            row = consent(a.id)
            satisfied = row is not None
        else:
            row = None
            satisfied = bool(a.present and a.present())
        out.append({
            "id": a.id,
            "title": a.title,
            "quote": a.quote,
            "reading": a.reading,
            "remedy": a.remedy,
            "source": a.source,
            "without": a.without,
            "satisfied": satisfied,
            "consent": row,
        })
    return out
