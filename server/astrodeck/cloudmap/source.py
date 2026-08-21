"""Finding, fetching and evicting GOES granules (stage 3) -- the half with a socket.

Split from :mod:`astrodeck.cloudmap.granule` on exactly that boundary, so the
file reader can be exercised with no network and this module can be exercised
with no HDF5. Nothing here interprets a granule; it puts one on disk and says
where.

ANONYMOUS S3, NO BOTO3. The NOAA buckets are public, so a plain
``GET ...?list-type=2&prefix=...`` is the whole protocol and the response is
ListBucketResult XML. boto3 would add a large dependency, a
credential-resolution chain that reads ``~/.aws`` on a rig with no AWS account,
and a default retry policy this module explicitly does not want (design section
6: nothing retries in a loop in here).

THE TWO ENDPOINTS THIS MODULE TALKS TO, spelled out rather than left implicit:
https://noaa-goes18.s3.amazonaws.com/ and https://noaa-goes19.s3.amazonaws.com/
-- NOAA's GOES-R archive on the AWS Open Data registry. They are written here
in full because ``test_credits`` finds outbound services by grepping the source
for ``https://`` literals, and the request URL below is assembled from a bucket
name at runtime. Left to the concatenation, the only host that detector could
see in this file would be the ``http://s3.amazonaws.com/doc/2006-03-01/`` XML
namespace -- an identifier nothing ever fetches -- so it would flag the one
address that is not a request and miss both that are.

THREE RULES THAT ARE EASY TO BREAK AND EXPENSIVE TO NOTICE:

- **Two hours, never more.** :func:`latest_ref` reads the current UTC hour and
  at most the one before it. A product dead for two hours is a fault for stage
  4 to report, not a gap to paper over by walking back through the day -- and a
  walk-back turns one poll into 24 requests every time NOAA has a bad morning.
- **Cache writes are atomic.** Downloads land in ``<name>.part`` and reach
  their real name through :func:`os.replace`. Written straight to the
  destination, a killed 4 MB fetch leaves a file of the right name and the
  wrong length that the next run has to be clever to notice.
- **Logs are outcome-only**: the exception type, the product, the bucket.
  Never a URL, never a coordinate. ``weather.py`` follows the same rule for the
  same reason -- a lat/lon in a journal is a geolocation leak that outlives the
  process, and today's S3 URL is a query string with a site's latitude in it
  the day someone adds a point query. httpx puts the full request URL in its
  exception text, so the exception is never echoed either; only its type name.

NOTHING FIRES ON IMPORT. No poller, no module-level client, no clock read, no
config read. Every entry point takes its ``httpx.AsyncClient`` from the caller,
so a client built inside this module would be a request nobody asked for.
Stage 4 owns the cadence (design section 5.3 argues for 10 minutes, not 5).

TWO DEVIATIONS FROM THE DESIGN, both argued at the code that makes them:
:func:`latest_ref`'s fallback condition (section 4.2's wording fires on every
poll), and :func:`evict`'s sort key (section 5.2 says filename, which sorts a
mixed-platform directory by platform before time). One addition:
:func:`list_hour` refuses a naive datetime rather than listing the wrong hour.
"""
from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .granule import CloudmapUnavailable, parse_granule_name

__all__ = [
    "BUCKETS",
    "PRODUCTS",
    "GranuleRef",
    "bucket_for",
    "cache_dir",
    "ensure_cached",
    "evict",
    "latest_ref",
    "list_hour",
]

logger = logging.getLogger(__name__)

#: Platform key -> the public NOAA bucket carrying it. GOES-18 is GOES-West and
#: is the one a rig in the western United States wants; which to use is stage
#: 4's decision, not this module's.
BUCKETS = {"G18": "noaa-goes18", "G19": "noaa-goes19"}

#: The two products stage 4 reads: the 2 km clear-sky mask grid and the 10 km
#: cloud-top-height grid.
PRODUCTS = ("ABI-L2-ACMC", "ABI-L2-ACHAC")

#: S3's ListBucketResult namespace, carried by every element of the response.
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

#: One follow-up page and no more (design section 5.1). At max-keys=400 against
#: 12 granules an hour truncation cannot happen; handling it anyway is what
#: stops a cadence change from silently halving a listing, and refusing a THIRD
#: page is what stops a malformed continuation token from looping forever.
_MAX_PAGES = 2
_MAX_KEYS = "400"

#: How old a ``.part`` must be before :func:`evict` reads it as abandoned
#: rather than as a download in flight. A granule takes about a second.
_PART_GRACE_S = 3600.0


def _s3_root(bucket: str) -> str:
    return "https://" + bucket + ".s3.amazonaws.com/"


def bucket_for(platform: str) -> str:
    """The bucket carrying a platform's products.

    ``ValueError``, not the raw ``KeyError`` a dict would raise: design section
    7 lists an unknown platform key among the domain errors, and a caller
    catching ValueError for every other bad argument should not have to catch a
    second type for this one.
    """
    try:
        return BUCKETS[platform]
    except KeyError:
        raise ValueError(
            "no bucket for platform "
            + repr(platform)
            + "; this stage knows "
            + ", ".join(sorted(BUCKETS))
        ) from None


@dataclass(frozen=True)
class GranuleRef:
    """One object in a bucket, identified without having fetched it.

    ``size_bytes`` is the listing's own ``Size`` and it is load-bearing twice:
    it is how :func:`ensure_cached` decides a cached file is complete without a
    request, and how it decides a body that ended early was short. A ref whose
    size came from nowhere would make both of those checks agree with anything.
    """

    bucket: str
    key: str
    product: str
    platform: str
    scan_start: datetime
    size_bytes: int


def _refs_from(body: bytes, bucket: str) -> tuple[list[GranuleRef], str | None]:
    """Every granule in one ListBucketResult page, plus its continuation token.

    A listing entry whose key is not a granule name is skipped rather than
    fatal: NOAA leaves other objects under a prefix, and one ``index.html``
    must not cost the caller the whole hour.

    An entry with no readable size is skipped too, which is the less obvious
    half. Defaulting a missing size to zero would make every later completeness
    check pass vacuously, and a granule whose size nobody knows is a granule
    nobody can tell from a truncated one.
    """
    root = ElementTree.fromstring(body)
    refs: list[GranuleRef] = []
    for contents in root.findall(_NS + "Contents"):
        key_element = contents.find(_NS + "Key")
        if key_element is None or not key_element.text:
            continue
        key = key_element.text
        identity = parse_granule_name(key)
        if identity is None:
            continue
        size_element = contents.find(_NS + "Size")
        try:
            size_bytes = int((size_element.text or "").strip())
        except (AttributeError, ValueError):
            continue
        if size_bytes <= 0:
            continue
        product, platform, scan_start = identity
        refs.append(
            GranuleRef(
                bucket=bucket,
                key=key,
                # From the KEY, not from the prefix that found it: the cache
                # path is built from this field, so a file is filed under the
                # product it says it is.
                product=product,
                platform=platform,
                scan_start=scan_start,
                size_bytes=size_bytes,
            )
        )
    truncated = root.find(_NS + "IsTruncated")
    if truncated is None or (truncated.text or "").strip().lower() != "true":
        return refs, None
    token = root.find(_NS + "NextContinuationToken")
    return refs, (token.text if token is not None else None)


async def list_hour(
    client: Any, bucket: str, product: str, when: datetime
) -> list[GranuleRef]:
    """Every granule of one product under one UTC hour, oldest first.

    ``when`` must be timezone-aware. ADDITION TO DESIGN SECTION 7: a naive
    datetime is refused rather than read as UTC. The prefix is a UTC hour, so a
    caller who passed ``datetime.now()`` off the observatory's own clock would
    list a prefix seven or eight hours out, get an empty listing, fall back to
    another empty one and be told the product is dead -- all night, silently,
    under a perfectly clear sky.

    Raises :class:`CloudmapUnavailable` for anything the network or S3 does.
    """
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError(
            "when must be timezone-aware; a naive datetime would silently "
            "list the wrong UTC hour and read as a dead product"
        )
    moment = when.astimezone(timezone.utc)
    prefix = "%s/%04d/%03d/%02d/" % (
        product,
        moment.year,
        moment.timetuple().tm_yday,
        moment.hour,
    )

    refs: list[GranuleRef] = []
    token: str | None = None
    for _page in range(_MAX_PAGES):
        params = {"list-type": "2", "max-keys": _MAX_KEYS, "prefix": prefix}
        if token:
            params["continuation-token"] = token
        try:
            response = await client.get(_s3_root(bucket), params=params)
            response.raise_for_status()
            page, token = _refs_from(response.content, bucket)
        except Exception as exc:  # noqa: BLE001 - one type for every failure
            # The type name and nothing else. httpx's own message carries the
            # full request URL; see the module docstring.
            logger.warning(
                "listing %s in %s failed: %s", product, bucket, type(exc).__name__
            )
            raise CloudmapUnavailable(
                "listing " + product + " in " + bucket + " failed: "
                + type(exc).__name__
            ) from exc
        refs.extend(page)
        if not token:
            break
    if token:
        # A third page means either a cadence nobody told us about or a token
        # that never terminates. Either way the answer in hand is usable and
        # the loop is not worth entering again.
        logger.warning(
            "listing %s in %s offered more pages than this stage reads",
            product,
            bucket,
        )
    refs.sort(key=lambda ref: (ref.scan_start, ref.key))
    return refs


async def latest_ref(
    client: Any, bucket: str, product: str, now: datetime
) -> GranuleRef | None:
    """The newest granule at or before ``now``, from this hour or the last one.

    ``None`` means both hours came back with nothing usable, in exactly two
    requests. That is a fault for stage 4 to surface, not a reason to keep
    walking back through the day.

    DEVIATION FROM DESIGN SECTION 4.2, which falls back when the current hour
    "is empty or every key is older than ``now``". Every key is always older
    than now -- that is what a scan start is -- so read literally the clause
    fires on every poll, and section 8's test 15, which asserts exactly ONE
    request when the current hour has granules, could never pass. The condition
    implemented is the one that makes both sentences true: fall back when the
    current hour yields no granule AT OR BEFORE ``now``. That still covers the
    case the clause was reaching for, a listing holding only keys stamped in
    the future, which is a clock disagreeing with NOAA rather than data.

    The newest is chosen BY SCAN START rather than taken as the last element.
    S3 returns keys sorted and the fixed-width timestamp makes that time order,
    but "the last entry wins" is an assumption about someone else's service and
    this module has to parse the timestamp anyway.
    """
    for hour in (now, now - timedelta(hours=1)):
        usable = [
            ref
            for ref in await list_hour(client, bucket, product, hour)
            if ref.scan_start <= now
        ]
        if usable:
            return max(usable, key=lambda ref: ref.scan_start)
    return None


async def ensure_cached(
    client: Any, ref: GranuleRef, cache_dir: "str | Path"
) -> Path:
    """The granule on local disk, fetched only if it is not already there.

    These objects are immutable once written, so a destination of the declared
    size is proof and costs no request -- 53 MB an hour on a rig that is also
    carrying the relay tunnel.

    A size mismatch means re-download, not repair: the only ways to get one are
    a killed process and a truncated body, and neither leaves a file a
    byte-range top-up would make whole.

    THE BODY IS COUNTED AS IT ARRIVES and refused if it does not match the
    listing. That is not belt-and-braces on top of the ``.part`` rename: a
    connection that closes early does not always raise, so without the count a
    short body renames itself into place looking exactly like a complete
    granule -- which is the one failure ``.part`` exists to prevent.

    ``cache_dir`` here is the caller's directory and shadows the module
    function of the same name. The parameter name is design section 4.2's and
    the two never meet inside this function.
    """
    dest = Path(cache_dir) / ref.product / Path(ref.key).name
    if dest.exists():
        if dest.stat().st_size == ref.size_bytes:
            return dest
        dest.unlink()

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        with part.open("wb") as sink:
            async with client.stream("GET", _s3_root(ref.bucket) + ref.key) as body:
                body.raise_for_status()
                async for chunk in body.aiter_bytes():
                    written += len(chunk)
                    sink.write(chunk)
    except Exception as exc:  # noqa: BLE001 - one type for every failure
        part.unlink(missing_ok=True)
        logger.warning(
            "fetching %s from %s failed: %s",
            ref.product,
            ref.bucket,
            type(exc).__name__,
        )
        raise CloudmapUnavailable(
            "fetching " + ref.product + " from " + ref.bucket + " failed: "
            + type(exc).__name__
        ) from exc

    if written != ref.size_bytes:
        part.unlink(missing_ok=True)
        logger.warning(
            "fetching %s from %s returned a short body", ref.product, ref.bucket
        )
        raise CloudmapUnavailable(
            "fetching " + ref.product + " from " + ref.bucket + " returned "
            + repr(written) + " bytes, not the " + repr(ref.size_bytes)
            + " the listing declared"
        )

    os.replace(part, dest)
    return dest


def evict(cache_dir: "str | Path", keep_per_product: int = 6) -> int:
    """Trim each product directory to its newest granules. Returns the count.

    A missing cache directory is zero, not an error: eviction runs on a
    schedule and the first run of a fresh install has nothing to trim.

    Never removes a directory, and never touches a file it cannot identify as
    a granule -- an unlink loop over unrecognised names is how a cache cleaner
    ends up deleting the thing that put it there.

    A ``.part`` is swept only once it is older than an hour. Younger, it is a
    download in flight, and a granule takes about a second; unlinking one under
    a live writer costs a fetch and, on Windows, raises.

    DEVIATION FROM DESIGN SECTION 5.2, which says to sort by filename. Filename
    order IS time order within one platform, because the timestamp is fixed
    width -- but the platform field comes BEFORE it in the name, so a directory
    holding both G18 and G19 sorts by platform first, and keep-newest-6 would
    keep six G19 granules and delete every G18 one however recent. Sorting by
    the parsed scan start costs nothing (the name is parsed anyway, to decide
    whether the file is a granule at all) and means the same thing everywhere.
    """
    root = Path(cache_dir)
    if not root.is_dir():
        return 0

    stale_before = time.time() - _PART_GRACE_S
    removed = 0
    for product_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        granules: list[tuple[datetime, str, Path]] = []
        for entry in sorted(product_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name.endswith(".part"):
                if entry.stat().st_mtime < stale_before:
                    entry.unlink(missing_ok=True)
                    removed += 1
                continue
            identity = parse_granule_name(entry.name)
            if identity is None:
                continue
            granules.append((identity[2], entry.name, entry))

        granules.sort(key=lambda item: (item[0], item[1]))
        surplus = max(0, len(granules) - max(0, keep_per_product))
        for _scan_start, _name, entry in granules[:surplus]:
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


def cache_dir() -> Path:
    """Where granules live: ``CAPTURE_DIR / "_cloudmap"``. Creates nothing.

    ``hub`` is imported HERE rather than at module scope for two reasons. It
    keeps ``CAPTURE_DIR`` resolved live, so a config change that moves the
    capture directory moves the cache with it instead of being outvoted by
    whatever the value was when this module was first imported. And it keeps
    stage 3 importable without dragging the whole application in, which is what
    lets "nothing at module scope reads config" be true of this module rather
    than only of its own lines.
    """
    from astrodeck import hub

    return hub.CAPTURE_DIR / "_cloudmap"
