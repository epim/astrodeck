"""Finding, fetching and evicting GOES granules, stage 3.

Every test here pins one specific way the source can be plausibly wrong: a key
whose scan start is read off the wrong field, a junk object under a prefix
taking the whole listing down, a truncated listing quietly losing its tail, a
fallback that walks back through the day instead of stopping at one hour, a
cache that trusts a half-written file, an eviction that keeps the wrong end,
and a failure log that carries the observing site's coordinates out to
whoever reads the journal.

NOTHING here touches the network. The client is a stub that records what it
was asked for and hands back canned S3 XML, so the whole module is exercised
with no socket, no credentials and no NOAA outage in the way.

The XML fixtures carry a real ``<?xml ... encoding="UTF-8"?>`` declaration
because S3's do: parsed as a str rather than as bytes that declaration is a
hard error, and it is the sort of thing that only shows up against the real
service.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from astrodeck.cloudmap import source as source_mod
from astrodeck.cloudmap.granule import CloudmapUnavailable, parse_granule_name
from astrodeck.cloudmap.source import (
    BUCKETS,
    PRODUCTS,
    GranuleRef,
    bucket_for,
    cache_dir,
    ensure_cached,
    evict,
    latest_ref,
    list_hour,
)

BUCKET = "noaa-goes18"
PRODUCT = "ABI-L2-ACMC"
#: The real key of design section 8, test 13.
REAL_NAME = (
    "OR_ABI-L2-ACMC-M6_G18_s20262330606176"
    "_e20262330608549_c20262330609298.nc"
)
REAL_KEY = "ABI-L2-ACMC/2026/233/06/" + REAL_NAME
NOW = datetime(2026, 8, 21, 6, 30, tzinfo=timezone.utc)


def _key(
    *,
    hour=6,
    minute=6,
    second=17,
    doy=233,
    product=PRODUCT,
    platform="G18",
    mode=6,
):
    """An S3 key shaped exactly like NOAA's, at a chosen scan start.

    ``mode`` is ABI's scan mode -- the ``-M6`` field -- and it is a parameter
    because it changes: GOES runs mode 6 and drops to mode 3 for a scheduled
    outage or a special observation. It sits BEFORE the timestamp in the
    name, which is the only reason it matters here.
    """
    stamp = f"2026{doy:03d}{hour:02d}{minute:02d}{second:02d}6"
    return (
        f"{product}/2026/{doy:03d}/{hour:02d}/"
        f"OR_{product}-M{mode}_{platform}_s{stamp}_e{stamp}_c{stamp}.nc"
    )


def _listing(entries, *, truncated=False, token=None) -> bytes:
    """S3 ListBucketResult XML, in the namespace the real one uses."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
        f"<Name>{BUCKET}</Name>",
        f"<KeyCount>{len(entries)}</KeyCount>",
        f'<IsTruncated>{"true" if truncated else "false"}</IsTruncated>',
    ]
    if token:
        parts.append(f"<NextContinuationToken>{token}</NextContinuationToken>")
    for key, size in entries:
        # size=None omits the element entirely, which is not a shape S3 emits
        # but is the shape a caller must survive: a listing entry whose size
        # is unknown cannot be told from a truncated download later.
        measurement = "" if size is None else f"<Size>{size}</Size>"
        parts.append(
            f"<Contents><Key>{key}</Key>"
            "<LastModified>2026-08-21T06:09:29.000Z</LastModified>"
            f"{measurement}"
            "<StorageClass>STANDARD</StorageClass></Contents>"
        )
    parts.append("</ListBucketResult>")
    return "".join(parts).encode("utf-8")


class _Body:
    """The half of ``httpx.Response`` this module uses, and no more.

    ``watcher`` is called after each chunk has been handed over and written,
    which is the only moment from which a test can see what the cache
    directory looks like MID-DOWNLOAD. Every other assertion about atomicity
    is made after an exception, and an implementation that writes straight to
    the destination and tidies up on its own error path satisfies all of them
    while leaving a truncated granule behind the moment the process is killed
    instead.
    """

    def __init__(self, content=b"", chunks=(), boom=None, watcher=None):
        self.content = content
        self.status_code = 200
        self._chunks = tuple(chunks)
        self._boom = boom
        self._watcher = watcher

    def raise_for_status(self):
        return self

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk
            if self._watcher is not None:
                self._watcher()
        if self._boom is not None:
            raise self._boom


class _Stream:
    def __init__(self, body):
        self._body = body

    async def __aenter__(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aexit__(self, *exc):
        return False


class _StubClient:
    """An ``httpx.AsyncClient`` that answers from a script and records asks."""

    def __init__(self, pages=(), download=None):
        self._pages = list(pages)
        self._download = download
        self.gets: list[tuple[str, dict]] = []
        self.downloads: list[str] = []

    async def get(self, url, params=None, **kwargs):
        self.gets.append((url, dict(params or {})))
        if not self._pages:
            raise AssertionError("a listing request the test did not script")
        page = self._pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return _Body(content=page)

    def stream(self, method, url, **kwargs):
        self.downloads.append(url)
        if self._download is None:
            raise AssertionError("a download the test did not script")
        return _Stream(self._download)


class _Boom:
    """``survey.py``'s: an ``AsyncClient`` that cannot be built at all.

    Every entry point here takes its client from the caller, so a client
    constructed anywhere inside this module is by definition one nobody asked
    for.
    """

    def __init__(self, *args, **kwargs):
        raise AssertionError("stage 3 built an httpx client of its own")


def _assert_log_is_outcome_only(caplog, raised, must_say):
    """Design section 6, applied to every failure path rather than to one.

    The rule is the same wherever it is checked: the outcome reaches the
    journal and nothing else does. No URL, no decimal number (a decimal degree
    is the one this is really about), and no ``exc_info`` -- because attaching
    the exception hands the formatter httpx's own message, which carries the
    full request URL that the log line itself was careful not to print.

    ``must_say`` is what the raised ``CloudmapUnavailable`` must still tell a
    caller: usually the exception type name, and on the short-body path the
    two lengths that did not match. A rule that only says what may NOT appear
    is satisfied by a log line that says nothing at all.
    """
    assert caplog.records
    for record in caplog.records:
        message = record.getMessage()
        assert "amazonaws" not in message
        assert re.search(r"-?\d+\.\d+", message) is None
        assert record.exc_info is None
        assert record.exc_text is None
    assert "amazonaws" not in str(raised)
    assert must_say in str(raised)


# ----------------------------------------------------------------- the keys


def test_a_key_is_parsed_into_its_scan_start():
    """``_s2026233 0606176`` is day 233 of 2026 at 06:06:17.6 UTC.

    The parser lives in ``granule`` because both modules need it and only one
    of them is allowed a socket. Two copies of a rule this fiddly -- a
    zero-padded day of year, a trailing tenth of a second that is NOT part of
    the seconds field -- would eventually disagree.
    """
    parsed = parse_granule_name(REAL_KEY)

    assert parsed is not None
    product, platform, scan_start = parsed
    assert product == "ABI-L2-ACMC"
    assert platform == "G18"
    assert scan_start == datetime(2026, 8, 21, 6, 6, 17, tzinfo=timezone.utc)
    # The bare basename parses identically: a cached file has no prefix left.
    assert parse_granule_name(REAL_NAME) == parsed


async def test_an_unrecognised_key_is_skipped_not_fatal():
    """NOAA leaves other objects under a prefix; one must not cost the hour.

    An entry with no usable ``Size`` is skipped for a different reason and it
    is the less obvious one. ``size_bytes`` is what tells a complete cached
    granule from a truncated one and a finished download from a short body;
    defaulting a missing size to zero would make both of those checks agree
    with anything, so an entry that cannot state its size is not a granule
    this stage is willing to fetch.
    """
    client = _StubClient(
        pages=[
            _listing(
                [
                    (_key(minute=6), 4_060_000),
                    ("ABI-L2-ACMC/2026/233/06/index.html", 512),
                    (_key(minute=21), 0),          # a zero-length object
                    (_key(minute=26), None),       # no <Size> at all
                    (_key(minute=31), "roughly 4 MB"),
                    (_key(minute=11), 4_061_000),
                ]
            )
        ]
    )

    refs = await list_hour(client, BUCKET, PRODUCT, NOW)

    assert len(refs) == 2
    assert [r.scan_start.minute for r in refs] == [6, 11]
    assert refs[0].bucket == BUCKET
    assert refs[0].product == PRODUCT
    assert refs[0].platform == "G18"
    assert refs[0].size_bytes == 4_060_000


async def test_the_newest_key_is_the_last_in_sort_order():
    """Out of order in the XML, in order once parsed.

    S3 returns keys sorted and the fixed-width timestamp makes that time
    order -- but "the last <Contents> element wins" is an assumption about
    someone else's service, so the newest is chosen by scan start.

    Which leaves ``list_hour`` owing the caller the order its name claims.
    Stage 4 gets a list, and a list that arrives in whatever order the XML
    happened to carry is one an unlucky caller reads the oldest end of.
    """
    out_of_order = [
        (_key(minute=16), 4_060_000),
        (_key(minute=6), 4_061_000),
        (_key(minute=11), 4_062_000),
    ]
    client = _StubClient(pages=[_listing(out_of_order)])

    ref = await latest_ref(client, BUCKET, PRODUCT, NOW)

    assert ref is not None
    assert ref.scan_start == datetime(
        2026, 8, 21, 6, 16, 17, tzinfo=timezone.utc
    )
    assert len(client.gets) == 1        # the current hour had an answer

    listed = await list_hour(
        _StubClient(pages=[_listing(out_of_order)]), BUCKET, PRODUCT, NOW
    )
    assert [r.scan_start.minute for r in listed] == [6, 11, 16]


async def test_the_listing_asks_for_a_v2_page_of_four_hundred_keys():
    """The whole request, pinned as a set -- host, page size and listing type.

    ``list-type=2`` is what makes the answer a V2 ListBucketResult, and V2 is
    the only version that carries ``NextContinuationToken``. Drop it and S3
    replies with a V1 listing that still says ``IsTruncated`` and has no token
    element at all, so the truncation handling below goes quietly dead against
    the real service while every stubbed test in this file keeps passing.

    ``max-keys=400`` is the other half of the same argument: 400 against the
    twelve granules an hour a product actually ships is what makes truncation
    impossible in the first place. A page size small enough to truncate turns
    "the newest granule" into "the newest granule in the first two pages",
    which is the oldest one that stage 4 will ever see.
    """
    client = _StubClient(pages=[_listing([(_key(minute=6), 4_060_000)])])

    await list_hour(client, BUCKET, PRODUCT, NOW)

    url, params = client.gets[0]
    assert params == {
        "list-type": "2",
        "max-keys": "400",
        "prefix": "ABI-L2-ACMC/2026/233/06/",
    }
    assert url == "https://noaa-goes18.s3.amazonaws.com/"


async def test_every_request_goes_to_the_bucket_it_was_given(tmp_path):
    """GOES-19's granules come out of GOES-19's bucket.

    The host is assembled from the bucket name at request time, so a hostname
    hardcoded to ``noaa-goes18`` reads correctly, passes every test that only
    inspects query parameters, and quietly serves GOES-West data to a rig that
    asked for GOES-East. Nobody finds out from an error; they find out because
    the clouds are three thousand kilometres from the sky overhead.

    The download is checked as well as the listing. They build their URL
    through the same helper today, which is exactly why one assertion would
    stop covering the other the moment someone inlines it.
    """
    assert {platform: bucket_for(platform) for platform in BUCKETS} == BUCKETS

    listing = _StubClient(
        pages=[_listing([(_key(minute=6, platform="G19"), 7)])]
    )
    refs = await list_hour(listing, bucket_for("G19"), PRODUCT, NOW)

    assert listing.gets[0][0] == "https://noaa-goes19.s3.amazonaws.com/"
    assert refs[0].bucket == "noaa-goes19"

    download = _StubClient(download=_Body(chunks=[b"g" * 7]))
    await ensure_cached(download, refs[0], tmp_path)

    assert download.downloads == [
        "https://noaa-goes19.s3.amazonaws.com/" + refs[0].key
    ]


async def test_the_order_survives_a_scan_mode_change_mid_hour():
    """Time order, not filename order -- the mode field comes first.

    ABI does not stay in one scan mode: it runs mode 6 and drops to mode 3,
    and the switch can land mid-hour. The mode sits BEFORE the timestamp in
    the key, so an hour that straddles one sorts every ``-M3`` key ahead of
    every ``-M6`` key however late the M6 frame was taken. It is the same trap
    ``evict`` documents for the platform field, one field along, and it is why
    the order comes from the parsed scan start rather than from the name.
    """
    client = _StubClient(
        pages=[
            _listing(
                [
                    (_key(minute=6, mode=6), 4_060_000),
                    (_key(minute=11, mode=3), 4_061_000),   # newer, sorts first
                ]
            )
        ]
    )

    refs = await list_hour(client, BUCKET, PRODUCT, NOW)

    assert [r.scan_start.minute for r in refs] == [6, 11]
    # ...and the filename order this is NOT, so the fixture cannot go stale.
    assert sorted(r.key for r in refs) != [r.key for r in refs]


async def test_a_truncated_listing_follows_its_continuation_token():
    """At 400 keys against 12 an hour this never fires -- until a cadence
    changes, and then a silent half-listing looks exactly like a quiet sky."""
    client = _StubClient(
        pages=[
            _listing([(_key(minute=6), 1)], truncated=True, token="TOK-2"),
            _listing([(_key(minute=11), 2)]),
        ]
    )

    refs = await list_hour(client, BUCKET, PRODUCT, NOW)

    assert [r.scan_start.minute for r in refs] == [6, 11]
    assert len(client.gets) == 2
    assert "continuation-token" not in client.gets[0][1]
    assert client.gets[1][1]["continuation-token"] == "TOK-2"


async def test_a_third_page_is_refused_rather_than_followed(caplog):
    """The follow-up is bounded, and the bound is the point.

    "Follow the continuation token" written as a while-loop is a request loop
    driven by a value the other end supplies: a token that never terminates,
    or one that keeps returning itself, polls S3 until something else breaks.
    Two pages is 800 keys against a product that ships twelve an hour, so the
    third page is a fault to say out loud, not a page to fetch.
    """
    caplog.set_level(logging.DEBUG, logger=source_mod.__name__)
    client = _StubClient(
        pages=[
            _listing([(_key(minute=6), 1)], truncated=True, token="TOK-2"),
            _listing([(_key(minute=11), 2)], truncated=True, token="TOK-3"),
        ]
    )

    refs = await list_hour(client, BUCKET, PRODUCT, NOW)

    assert [r.scan_start.minute for r in refs] == [6, 11]
    assert len(client.gets) == 2         # a third would be unscripted and raise
    assert caplog.records
    for record in caplog.records:
        assert "TOK-3" not in record.getMessage()
        assert "amazonaws" not in record.getMessage()


async def test_an_empty_current_hour_falls_back_one_hour_and_no_further():
    """One hour back, never two.

    A product dead for two hours is a fault to report, not a gap to paper
    over -- and a walk back through the day turns one poll into 24 requests
    every time NOAA has a bad morning.
    """
    now = datetime(2026, 8, 21, 6, 3, tzinfo=timezone.utc)
    client = _StubClient(
        pages=[_listing([]), _listing([(_key(hour=5, minute=56), 4_060_000)])]
    )

    ref = await latest_ref(client, BUCKET, PRODUCT, now)

    assert ref is not None
    assert ref.scan_start == datetime(
        2026, 8, 21, 5, 56, 17, tzinfo=timezone.utc
    )
    assert len(client.gets) == 2
    assert client.gets[0][1]["prefix"] == "ABI-L2-ACMC/2026/233/06/"
    assert client.gets[1][1]["prefix"] == "ABI-L2-ACMC/2026/233/05/"

    empty = _StubClient(pages=[_listing([]), _listing([])])
    assert await latest_ref(empty, BUCKET, PRODUCT, now) is None
    assert len(empty.gets) == 2


async def test_a_day_of_year_below_one_hundred_is_zero_padded():
    """Every prefix from the first of January to the ninth of April.

    The key layout is ``<product>/<YYYY>/<DDD>/<HH>/`` with the day of year
    padded to three digits. Day 33 written as ``33`` is a prefix nothing lives
    under: the current hour comes back empty, the fallback hour comes back
    empty, and stage 4 is told the product is dead. Every night. All winter.
    Under a clear sky. Nothing raises, nothing retries, and the log line says
    only that there were no granules -- which is true of that prefix.

    Every other fixture in this file is day 233, which is three digits wide
    already and cannot tell a padded field from an unpadded one.
    """
    client = _StubClient(pages=[_listing([]), _listing([])])
    february = datetime(2026, 2, 2, 6, 30, tzinfo=timezone.utc)     # day 33

    assert await latest_ref(client, BUCKET, PRODUCT, february) is None

    assert [get[1]["prefix"] for get in client.gets] == [
        "ABI-L2-ACMC/2026/033/06/",
        "ABI-L2-ACMC/2026/033/05/",
    ]


async def test_a_current_hour_of_only_future_keys_falls_back_like_an_empty_one():
    """The half of design section 4.2's fallback clause that is implementable.

    Section 4.2 falls back when the hour "is empty or every key is older than
    ``now``". Every key is always older than now -- that is what a scan start
    is -- so read literally the clause fires on every poll and the one-request
    case above could not exist. What it was reaching for is this: an hour
    holding only keys stamped AHEAD of ``now``, which is a clock disagreeing
    with NOAA rather than data, and which must not be handed to stage 4 as the
    time a frame was observed.
    """
    now = datetime(2026, 8, 21, 6, 3, tzinfo=timezone.utc)
    client = _StubClient(
        pages=[
            _listing([(_key(hour=6, minute=41), 4_060_000)]),   # ahead of now
            _listing([(_key(hour=5, minute=56), 4_061_000)]),
        ]
    )

    ref = await latest_ref(client, BUCKET, PRODUCT, now)

    assert ref is not None
    assert ref.scan_start == datetime(
        2026, 8, 21, 5, 56, 17, tzinfo=timezone.utc
    )
    assert len(client.gets) == 2


async def test_an_aware_datetime_is_converted_to_utc_before_the_prefix():
    """The door the naive-datetime refusal below does not cover.

    ``datetime.now().astimezone()`` is AWARE -- it carries the machine's own
    zone -- so it walks straight past the tzinfo guard and then means the
    wrong hour to every line that follows. The prefix is a UTC hour, and this
    is the conversion that makes it one.

    A rig on Pacific time at 22:30 on 1 February is at 06:30 UTC on the 2nd,
    which is a different HOUR and a different DAY: converted, the prefix is
    ``2026/033/06/``; unconverted it is ``2026/032/22/``, a prefix nothing
    lives under. The current hour comes back empty, the fallback hour comes
    back empty, and stage 4 is told the product is dead -- the same silent
    night the refusal below exists to prevent, reached through the one input
    that refusal accepts.

    Every other fixture in this file is already UTC, where the conversion and
    its absence are the same line.
    """
    now = datetime(2026, 2, 1, 22, 30, tzinfo=timezone(timedelta(hours=-8)))
    client = _StubClient(
        pages=[_listing([(_key(doy=33, hour=6, minute=6), 4_060_000)])]
    )

    ref = await latest_ref(client, BUCKET, PRODUCT, now)

    assert [get[1]["prefix"] for get in client.gets] == [
        "ABI-L2-ACMC/2026/033/06/"
    ]
    # And the "at or before now" filter compared the two across the offset
    # rather than dropping a granule it read as being in the future.
    assert ref is not None
    assert ref.scan_start == datetime(2026, 2, 2, 6, 6, 17, tzinfo=timezone.utc)


async def test_a_naive_datetime_is_refused_rather_than_listing_the_wrong_hour():
    """ADDITION TO DESIGN SECTION 7, and the cheapest silent night there is.

    The prefix is a UTC hour. A caller who passed ``datetime.now()`` off the
    observatory's own clock -- or ``datetime.utcnow()``, which is naive and
    which Python now deprecates for exactly this -- would list a prefix seven
    or eight hours out, get an empty listing, fall back to another empty one
    and be told the product is dead. All night. Under a clear sky. So it is a
    refusal before the first request, not an empty answer after two.
    """
    client = _StubClient(pages=[_listing([(_key(), 4_060_000)])])
    naive = NOW.replace(tzinfo=None)

    with pytest.raises(ValueError, match="timezone-aware"):
        await list_hour(client, BUCKET, PRODUCT, naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        await latest_ref(client, BUCKET, PRODUCT, naive)
    assert client.gets == []


# ---------------------------------------------------------------- the cache


async def test_a_cached_file_of_the_right_size_is_not_refetched(tmp_path):
    """These objects are immutable once written, so the right size is proof.

    53 MB an hour is what this saves on a rig also carrying the relay tunnel.
    """
    body = b"x" * 4096
    ref = GranuleRef(
        bucket=BUCKET,
        key=REAL_KEY,
        product=PRODUCT,
        platform="G18",
        scan_start=NOW,
        size_bytes=len(body),
    )
    dest = tmp_path / PRODUCT / REAL_NAME
    dest.parent.mkdir(parents=True)
    dest.write_bytes(body)
    client = _StubClient()

    got = await ensure_cached(client, ref, tmp_path)

    assert got == dest
    assert client.downloads == []
    assert client.gets == []


async def test_a_cached_file_of_the_wrong_size_is_refetched(tmp_path):
    """A size mismatch means re-download, not repair.

    The only ways to get one are a killed process and a truncated body, and
    neither leaves a file that a byte-range top-up would make whole.
    """
    body = b"y" * 4096
    ref = GranuleRef(
        bucket=BUCKET,
        key=REAL_KEY,
        product=PRODUCT,
        platform="G18",
        scan_start=NOW,
        size_bytes=len(body),
    )
    dest = tmp_path / PRODUCT / REAL_NAME
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"half a granule")
    client = _StubClient(download=_Body(chunks=[body[:100], body[100:]]))

    got = await ensure_cached(client, ref, tmp_path)

    assert len(client.downloads) == 1
    assert got.read_bytes() == body
    assert got.stat().st_size == ref.size_bytes


async def test_a_download_is_atomic(tmp_path, caplog):
    """A body that dies half way leaves nothing behind that reads as whole.

    Written straight to its final name, a killed 4 MB download leaves a file
    of the right name and the wrong length, and the next run has to be clever
    to notice. Written to ``.part`` and renamed, there is nothing to notice.

    The log rule is asserted HERE as well as on the listing, because this is
    the path that holds a full object URL -- the longest and most quotable
    string in the module. A ``logger.warning("download failed: %s", exc)``
    would satisfy every other assertion in this test while putting
    ``https://noaa-goes18.s3.amazonaws.com/ABI-L2-ACMC/...`` in the journal.
    """
    caplog.set_level(logging.DEBUG, logger=source_mod.__name__)
    ref = GranuleRef(
        bucket=BUCKET,
        key=REAL_KEY,
        product=PRODUCT,
        platform="G18",
        scan_start=NOW,
        size_bytes=4096,
    )
    # The exception carries what httpx's really carries. Measured against a
    # real AsyncClient: raise_for_status() on a 403 raises with "Client error
    # '403 Forbidden' for url 'https://noaa-goes18.s3.amazonaws.com/?list-
    # type=2&...'" in the message. Given a bare ReadError("dropped") this test
    # cannot tell `%s` on the exception from `%s` on its type name, because
    # both print something harmless -- and `%s` on the exception is precisely
    # the line the docstring above says must never be written.
    client = _StubClient(
        download=_Body(
            chunks=[b"z" * 100],
            boom=httpx.ReadError(
                "dropped while reading "
                "https://noaa-goes18.s3.amazonaws.com/" + REAL_KEY
                + "?lat=40.0274&lon=-105.2519"
            ),
        )
    )

    with pytest.raises(CloudmapUnavailable) as caught:
        await ensure_cached(client, ref, tmp_path)

    dest = tmp_path / PRODUCT / REAL_NAME
    assert not dest.exists()
    assert list((tmp_path / PRODUCT).glob("*.part")) == []
    # glob rather than iterdir: an implementation that staged its .part
    # somewhere else and only made this directory on success would be correct
    # and would make iterdir raise FileNotFoundError, turning "nothing was
    # left behind" into an error instead of a pass.
    assert sorted(p.name for p in (tmp_path / PRODUCT).glob("*")) == []
    _assert_log_is_outcome_only(caplog, caught.value, "ReadError")


async def test_the_destination_name_never_holds_an_incomplete_granule(tmp_path):
    """Watched from inside the download, not inferred from the wreckage.

    Every other atomicity assertion here fires after an exception, and an
    implementation that writes straight to the destination and unlinks it on
    its own error path passes all of them -- measured. The failure ``.part``
    exists for is the one with NO error path: the process is killed, nothing
    tidies up, and a file of the right name and the wrong length is left where
    the next run will find it and call it cached. Nothing raised here; the
    question is only what is on disk while the bytes are still arriving.
    """
    product_dir = tmp_path / PRODUCT
    seen: list[list[str]] = []

    def _look():
        seen.append(sorted(p.name for p in product_dir.glob("*")))

    body = b"w" * 200
    ref = GranuleRef(
        bucket=BUCKET,
        key=REAL_KEY,
        product=PRODUCT,
        platform="G18",
        scan_start=NOW,
        size_bytes=len(body),
    )
    client = _StubClient(
        download=_Body(chunks=[body[:100], body[100:]], watcher=_look)
    )

    got = await ensure_cached(client, ref, tmp_path)

    assert got.read_bytes() == body
    assert seen, "the download never handed over a chunk"
    for names in seen:
        assert REAL_NAME not in names          # never under its real name
        assert REAL_NAME + ".part" in names    # always under a staged one
    assert sorted(p.name for p in product_dir.iterdir()) == [REAL_NAME]


async def test_a_destination_that_appears_mid_download_is_replaced(tmp_path):
    """``os.replace``, not ``os.rename``: on Windows the second one raises.

    Two things can be fetching the same granule -- stage 4's poll and a
    caller's own refresh -- and the size check that would have short-circuited
    the second one ran before the first one had finished writing. The bytes
    are identical either way, since these objects are immutable, so the right
    answer is to overwrite and move on.

    ``os.rename`` instead raises ``FileExistsError`` on Windows, and it raises
    it OUTSIDE the try block, so it reaches the caller raw rather than as the
    ``CloudmapUnavailable`` this stage promises for everything that can go
    wrong -- a poller that catches the documented type keeps running; one
    that meets an undocumented one does not.
    """
    product_dir = tmp_path / PRODUCT
    body = b"v" * 200
    ref = GranuleRef(
        bucket=BUCKET,
        key=REAL_KEY,
        product=PRODUCT,
        platform="G18",
        scan_start=NOW,
        size_bytes=len(body),
    )

    def _the_other_fetch_lands():
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / REAL_NAME).write_bytes(body)

    client = _StubClient(
        download=_Body(
            chunks=[body[:100], body[100:]], watcher=_the_other_fetch_lands
        )
    )

    got = await ensure_cached(client, ref, tmp_path)

    assert got.read_bytes() == body
    assert sorted(p.name for p in product_dir.iterdir()) == [REAL_NAME]


async def test_a_short_body_is_refused_rather_than_cached(tmp_path, caplog):
    """A body that ends early WITHOUT raising is the case ``.part`` cannot see.

    A connection closed between chunks does not always surface as an
    exception; ``aiter_bytes`` simply stops. Rename that into place and the
    cache holds a truncated granule of the right name that the size check on
    the next run cannot distinguish from a killed download -- except that
    nothing ever re-reads it, because it is already "there". So the bytes are
    counted as they arrive and measured against the listing's own Size.
    """
    caplog.set_level(logging.DEBUG, logger=source_mod.__name__)
    ref = GranuleRef(
        bucket=BUCKET,
        key=REAL_KEY,
        product=PRODUCT,
        platform="G18",
        scan_start=NOW,
        size_bytes=4096,
    )
    client = _StubClient(download=_Body(chunks=[b"z" * 100]))   # ends cleanly

    with pytest.raises(CloudmapUnavailable) as caught:
        await ensure_cached(client, ref, tmp_path)

    assert not (tmp_path / PRODUCT / REAL_NAME).exists()
    assert sorted(p.name for p in (tmp_path / PRODUCT).glob("*")) == []
    # The same log rule as every other failure path. This one is the easiest
    # to write badly, because the natural way to say what went wrong is to
    # quote the object that came up short -- and the only name this code has
    # for that object is its URL.
    _assert_log_is_outcome_only(caplog, caught.value, "the listing declared")


def test_eviction_keeps_the_newest_of_each_product_and_counts_what_it_removed(
    tmp_path,
):
    """Per product, by scan start, and a leftover ``.part`` only once it is
    too old to be a download still in flight.

    TWO product directories, because with one the ``per_product`` in
    ``keep_per_product`` is unconstrained: a global keep-newest-6 across the
    whole cache and a per-directory one produce the same survivors and the
    same count, and the global version would delete seven files here instead
    of three.

    TWO platforms in one directory, because design section 5.2's "newest by
    filename" is only time order within a platform -- the platform field comes
    first in the name, so filename order puts every G18 before every G19 and a
    keep-newest-6 would hold four ancient G19 granules while deleting the
    three most recent G18 ones bar two.
    """
    acmc = tmp_path / PRODUCT
    achac = tmp_path / "ABI-L2-ACHAC"
    acmc.mkdir(parents=True)
    achac.mkdir(parents=True)

    older_g19 = [Path(_key(minute=m, platform="G19")).name for m in (1, 6, 11, 16)]
    newer_g18 = [
        Path(_key(minute=m, platform="G18")).name for m in (21, 26, 31, 36, 41)
    ]
    for name in older_g19 + newer_g18:
        (acmc / name).write_bytes(b"granule")
    small = [
        Path(_key(minute=m, product="ABI-L2-ACHAC")).name for m in (1, 6, 11, 16)
    ]
    for name in small:
        (achac / name).write_bytes(b"granule")

    fresh_part = acmc / (newer_g18[-1] + ".part")
    fresh_part.write_bytes(b"in flight")
    stale_part = acmc / (older_g19[0] + ".part")
    stale_part.write_bytes(b"abandoned")
    old = time.time() - 2 * 3600
    os.utime(stale_part, (old, old))
    # Not a granule name, so not this cleaner's business to delete.
    junk = acmc / "index.html"
    junk.write_bytes(b"<html>")

    removed = evict(tmp_path, keep_per_product=6)

    assert removed == 4                      # three granules and one dead part
    assert sorted(p.name for p in acmc.iterdir()) == sorted(
        older_g19[3:] + newer_g18 + [fresh_part.name, junk.name]
    )
    assert sorted(p.name for p in achac.iterdir()) == sorted(small)
    assert acmc.is_dir() and achac.is_dir()  # never removes a directory


def test_the_eviction_defaults_are_six_granules_and_an_hour_of_grace(tmp_path):
    """Both numbers in the signature, reached through the default path.

    The eviction test above passes ``keep_per_product=6`` explicitly, so the
    documented default is a number no test reads; and it ages its ``.part``
    by two hours, from where a grace of an hour and a grace of a minute look
    identical. They are not. A download on a rig sharing its uplink with the
    relay tunnel can sit part-written for minutes, and a grace that expires
    first turns eviction into the thing that deletes it -- costing the fetch
    again and, on Windows, unlinking a file its writer still holds open.
    """
    product_dir = tmp_path / PRODUCT
    product_dir.mkdir(parents=True)
    for minute in (1, 6, 11, 16, 21, 26, 31, 36):
        (product_dir / Path(_key(minute=minute)).name).write_bytes(b"granule")
    in_flight = product_dir / (Path(_key(minute=41)).name + ".part")
    in_flight.write_bytes(b"still arriving")
    ten_minutes_ago = time.time() - 600
    os.utime(in_flight, (ten_minutes_ago, ten_minutes_ago))

    removed = evict(tmp_path)

    assert removed == 2                                  # eight kept down to six
    assert len(list(product_dir.glob("*.nc"))) == 6
    assert in_flight.exists()


# ------------------------------------------------------------- the two rules


def test_nothing_reaches_the_network_unless_asked(monkeypatch, tmp_path):
    """No poller, no singleton, nothing that fires on import.

    Everything constructible is constructed here with ``AsyncClient`` replaced
    by one that explodes on contact, so any client this module builds for
    itself is a failure rather than a request nobody sees. Stage 4 owns the
    cadence; stage 3 owns nothing that ticks.

    THE IMPORT IS THE CASE THIS TEST NAMES, and it is the one a patch applied
    inside the test body cannot see: line 32 of this file imported the module
    long before ``_Boom`` existed, so a module-level ``httpx.AsyncClient()``
    would already have been built and the assertion would never fire. So the
    module is loaded a second time from its own file, under a throwaway name,
    with ``AsyncClient`` already replaced -- the same idiom the granule suite
    uses to mask h5py. The in-call assertions below cover a different moment
    and are both wanted.
    """
    from astrodeck import hub

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    monkeypatch.setattr(hub, "CAPTURE_DIR", tmp_path / "captures")

    name = "astrodeck.cloudmap._source_under_boom"
    spec = importlib.util.spec_from_file_location(name, source_mod.__file__)
    fresh = importlib.util.module_from_spec(spec)
    # Registered before exec so the relative import of `granule` resolves and
    # @dataclass can look this module up; monkeypatch drops it afterwards.
    monkeypatch.setitem(sys.modules, name, fresh)
    spec.loader.exec_module(fresh)
    assert fresh.PRODUCTS == PRODUCTS

    assert BUCKETS["G18"] == "noaa-goes18"
    assert BUCKETS["G19"] == "noaa-goes19"
    assert PRODUCTS == ("ABI-L2-ACMC", "ABI-L2-ACHAC")
    assert bucket_for("G18") == "noaa-goes18"
    with pytest.raises(ValueError):
        bucket_for("G17")               # not an unhandled KeyError

    ref = GranuleRef(
        bucket=BUCKET,
        key=REAL_KEY,
        product=PRODUCT,
        platform="G18",
        scan_start=NOW,
        size_bytes=1,
    )
    assert ref.key == REAL_KEY
    assert parse_granule_name(ref.key) is not None

    # Resolved live off the hub, and resolving a path creates nothing.
    assert cache_dir() == tmp_path / "captures" / "_cloudmap"
    assert not cache_dir().exists()
    assert evict(tmp_path / "never-created") == 0


def test_nothing_at_module_scope_reads_the_clock(monkeypatch, tmp_path):
    """Design section 9, the half the network test above does not cover.

    A ``_STALE_BEFORE = time.time() - _PART_GRACE_S`` hoisted to module scope
    is a plausible tidy-up and it survives every other test in this file: the
    eviction tests age their ``.part`` by two hours, so an import-time reading
    of the clock and a call-time one give the same answer in a suite that
    finishes in a second. In a process that stays up all night they diverge by
    the length of the night -- ``evict`` first stops sweeping abandoned parts
    at all, then starts sweeping ones that are still being written.

    So the module is imported with the clock taken away, the same way the test
    above imports it with ``AsyncClient`` taken away.
    """
    def _no_clock(*args, **kwargs):
        raise AssertionError("the clock was read")

    stub_time = types.ModuleType("time")
    for attribute in dir(time):
        setattr(stub_time, attribute, getattr(time, attribute))
    for reader in ("time", "time_ns", "monotonic", "perf_counter", "gmtime"):
        setattr(stub_time, reader, _no_clock)

    class _NoNow(datetime):
        now = classmethod(_no_clock)
        utcnow = classmethod(_no_clock)
        today = classmethod(_no_clock)
        fromtimestamp = classmethod(_no_clock)

    import datetime as datetime_module

    stub_datetime = types.ModuleType("datetime")
    for attribute in dir(datetime_module):
        setattr(stub_datetime, attribute, getattr(datetime_module, attribute))
    stub_datetime.datetime = _NoNow

    name = "astrodeck.cloudmap._source_without_a_clock"
    spec = importlib.util.spec_from_file_location(name, source_mod.__file__)
    fresh = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, fresh)
    real = {"time": sys.modules["time"], "datetime": sys.modules["datetime"]}
    sys.modules["time"] = stub_time
    sys.modules["datetime"] = stub_datetime
    try:
        spec.loader.exec_module(fresh)          # the assertion is this line
    finally:
        sys.modules.update(real)

    assert fresh.PRODUCTS == PRODUCTS

    # The mask is live rather than decorative: the one clock read this module
    # does make is inside evict, per call, and the same stub catches it there.
    # Without this the test would pass just as well against a module that
    # imports no clock at all -- including one that never sweeps a .part.
    (tmp_path / PRODUCT).mkdir(parents=True)
    with pytest.raises(AssertionError, match="the clock was read"):
        fresh.evict(tmp_path)


async def test_a_failure_logs_no_url_and_no_coordinates(caplog):
    """Outcome only: the exception type, the product, the bucket.

    The exception's own text is never echoed -- httpx puts the full request
    URL in it, and an S3 URL today is a query string with a site's latitude
    in it tomorrow. A lat/lon in a log file is a geolocation leak that
    outlives the process, which is why ``weather.py`` follows the same rule.
    """
    caplog.set_level(logging.DEBUG, logger=source_mod.__name__)
    client = _StubClient(
        pages=[
            httpx.ConnectError(
                "connection refused for url "
                "https://noaa-goes18.s3.amazonaws.com/?prefix=40.02,-105.27"
            )
        ]
    )

    with pytest.raises(CloudmapUnavailable) as caught:
        await list_hour(client, BUCKET, PRODUCT, NOW)

    _assert_log_is_outcome_only(caplog, caught.value, "ConnectError")
