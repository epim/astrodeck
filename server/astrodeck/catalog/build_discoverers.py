"""Regenerate astrodeck/catalog/data/discoverers.tsv from Wikidata: who found
each object, and when.

WHY WIKIDATA, NOT WIKIPEDIA. Per docs/superpowers/backlog/2026-08-08-object-
description-sources.md (the survey this implements), Wikidata's P61
(discoverer) and P575 (point in time / discovery date) reach 58.9% of this
catalog under CC0 -- no attribution obligation at all -- for about 335 KB.
Wikipedia reaches less than half that (24.6%) and is CC BY-SA, which is why
it is a link-out elsewhere in the plan, not a vendored pack. Wikidata's own
one-line ``description`` field was ALSO measured and rejected (722 distinct
strings across 8,454 items, median 9 characters, mostly just "galaxy" --
survey section 4.4) -- P61/P575 is a completely different, much better,
result from the same site.

THE JOIN KEY. Wikidata models an NGC/IC designation as a P528 ("catalog
code") statement on the object's item, qualified by P972 ("catalog") naming
either the New General Catalogue (wd:Q14534) or the Index Catalogue
(wd:Q741672) -- confirmed by hand against NGC 6543 and IC 434 before writing
this query. The code string itself is written exactly "NGC 6543" / "IC 434"
-- the same format ngc.tsv's own ``id`` column and ``designation()`` in
build_ngc_catalog.py / build_ngc_extras.py use, so no reformatting is needed
on either side. Messier ids ("M31") do not carry their own P528 NGC/IC code
in general (M31's Wikidata item has no P61 at all -- "known since antiquity"
has no single discoverer to record, which is an honest gap, not a bug) --
this script resolves the query key for a Messier row from ngc.tsv's own
``alias`` column exactly the way build_ngc_extras.py does: ``alias or id``.

BE A CONSIDERATE CLIENT. The survey's own research got this address 429'd
across THREE Wikimedia hosts simultaneously after ~25 requests during a
13,478-title one-by-one sweep. This script never queries per-object: it pages
through ONE SPARQL query (~48,000 P528/NGC+IC statements total, measured)
in PAGE_SIZE-row chunks with a pause between requests, and it is RESUMABLE --
each page is written to a checkpoint file before being processed, so killing
this halfway through and re-running picks up where it left off instead of
re-fetching everything (or worse, being run again "just to be safe" and
tripping the same rate limit the survey hit).

AMBIGUITY: DROPPED, NOT GUESSED. Three distinct kinds turned up in the real
data and are each handled the same way -- silently excluded from the output,
counted in the summary printed at the end:

  1. CODE COLLISION. 100 of the ~48,000 P528 codes in the live data resolve
     to more than one distinct Wikidata item (e.g. "NGC 729" names two
     different items). Which item's facts belong to OUR catalog id cannot be
     known from the code alone -- dropped.
  2. MULTIPLE CREDITED DISCOVERERS. An item can carry more than one P61
     value. Some of this is genuine joint discovery (Dunlop and Herschel
     observing together at the Cape, both dated 1826-01-01); some of it is a
     Wikidata data-quality artifact (one item's P61 pointed at "United
     States" before the ``wdt:P31 wd:Q5`` human-only filter below excluded
     it). Rather than build heuristics to tell those apart, or print
     "discovered by A and B" and risk it being wrong, this script requires
     EXACTLY ONE distinct human discoverer per item -- more than one, drop.
  3. CONFLICTING DATES. Rarer, but real: one item recorded William Herschel
     discovering both NGC 1909 and IC 2118 under it, dated five decades
     apart -- a sign the item is doing double duty and neither date should be
     trusted blindly. More than one distinct discovery date for the same
     (code, item), drop the whole entry rather than keep the name and lose
     the date.

A confidently wrong discoverer is worse than a missing one; this script is
built to prefer the gap.

Run from the ``server/`` directory:

    python -m astrodeck.catalog.build_discoverers

Output is deterministic (rows sorted by id) so a rebuild diffs cleanly.
Checkpoint pages live under the OS temp dir (not under this package, so a
half-finished run never pollutes what test_credits.py walks) and are deleted
on a clean finish; pass --keep-checkpoints to keep them for inspection, or
--checkpoint-dir to point elsewhere.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).with_name("data")
NGC_TSV = DATA_DIR / "ngc.tsv"
OUT = DATA_DIR / "discoverers.tsv"

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
#: Identifies this script to WDQS with a real contact, per Wikimedia's User-
#: Agent policy -- an anonymous UA is exactly the kind of client that gets
#: banned first when a shared endpoint is under load.
USER_AGENT = "AstroDeck-catalog-build/0.1 (https://github.com/astrodeck; contact: jpenick@gmail.com)"

PAGE_SIZE = 5000
#: Between pages, not between objects -- ~10 requests total for the whole
#: catalog's worth of discovery data, not 13,000. Still paced, because the
#: whole point of this script existing is to never repeat the sweep that got
#: the survey's research 429'd.
PAGE_PAUSE_S = 1.5

NGC_CATALOG_QID = "wd:Q14534"   # "New General Catalogue" -- verified by hand
IC_CATALOG_QID = "wd:Q741672"   # "Index Catalogue" -- verified by hand

_QUERY_TEMPLATE = f"""
SELECT ?item ?code ?discoverer ?discovererLabel ?date ?datePrecision WHERE {{
  ?item p:P528 ?stmt .
  ?stmt ps:P528 ?code .
  ?stmt pq:P972 ?catalog .
  VALUES ?catalog {{ {NGC_CATALOG_QID} {IC_CATALOG_QID} }}
  OPTIONAL {{
    ?item wdt:P61 ?discoverer .
    ?discoverer wdt:P31 wd:Q5 .
  }}
  OPTIONAL {{
    ?item p:P575 ?dateStmt .
    ?dateStmt psv:P575 ?dateNode .
    ?dateNode wikibase:timeValue ?date .
    ?dateNode wikibase:timePrecision ?datePrecision .
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY ?item ?code
LIMIT %d
OFFSET %d
"""


def _fetch_page(offset: int, page_size: int) -> list[dict]:
    query = _QUERY_TEMPLATE % (page_size, offset)
    url = f"{SPARQL_ENDPOINT}?{urllib.parse.urlencode({'query': query})}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.load(resp)
    return payload["results"]["bindings"]


def _fetch_all_pages(checkpoint_dir: Path) -> list[dict]:
    """Every P528/NGC+IC binding, resuming from whatever checkpoint files
    already exist so a killed run does not re-pay the network cost."""
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    page = 0
    while True:
        page_file = checkpoint_dir / f"page_{page:04d}.json"
        if page_file.exists():
            binding = json.loads(page_file.read_text(encoding="utf-8"))
        else:
            offset = page * PAGE_SIZE
            print(f"  fetching page {page} (offset {offset})...", file=sys.stderr)
            try:
                binding = _fetch_page(offset, PAGE_SIZE)
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"  page {page} failed ({e}); re-run this script to resume "
                      f"from here -- earlier pages are already checkpointed.",
                      file=sys.stderr)
                raise
            page_file.write_text(json.dumps(binding), encoding="utf-8")
            if binding:  # only throttle when a real request was just made
                time.sleep(PAGE_PAUSE_S)
        rows.extend(binding)
        if len(binding) < PAGE_SIZE:
            break  # short page -- that was the last one
        page += 1
    return rows


def _iso_date(value: str) -> str:
    return value.split("T", 1)[0]  # "1786-02-15T00:00:00Z" -> "1786-02-15"


def _resolve(rows: list[dict]) -> tuple[dict[str, tuple[str, str, str]], dict[str, int]]:
    """(code -> (discoverer, date_iso_or_"", precision_or_""), stats)."""
    code_items: dict[str, set[str]] = defaultdict(set)
    code_item_disc: dict[tuple[str, str], set[str]] = defaultdict(set)
    code_item_dates: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)

    for r in rows:
        code = r["code"]["value"]
        item = r["item"]["value"]
        code_items[code].add(item)
        disc = r.get("discovererLabel", {}).get("value")
        if disc:
            code_item_disc[(code, item)].add(disc)
        date = r.get("date", {}).get("value")
        prec = r.get("datePrecision", {}).get("value")
        if date:
            code_item_dates[(code, item)].add((_iso_date(date), prec or ""))

    ambiguous_codes = {c for c, items in code_items.items() if len(items) > 1}
    stats = {"ambiguous_code": 0, "ambiguous_discoverer": 0, "ambiguous_date": 0,
              "no_discoverer": 0, "resolved": 0}
    final: dict[str, tuple[str, str, str]] = {}

    for code, items in code_items.items():
        if code in ambiguous_codes:
            stats["ambiguous_code"] += 1
            continue
        item = next(iter(items))
        discs = code_item_disc.get((code, item), set())
        if not discs:
            stats["no_discoverer"] += 1
            continue
        if len(discs) > 1:
            stats["ambiguous_discoverer"] += 1
            continue
        disc = next(iter(discs))
        dates = code_item_dates.get((code, item), set())
        if len(dates) > 1:
            stats["ambiguous_date"] += 1
            continue
        date_iso, prec = next(iter(dates)) if dates else ("", "")
        final[code] = (disc, date_iso, prec)
        stats["resolved"] += 1

    return final, stats


def _load_query_keys() -> dict[str, str]:
    """id -> the P528 code to look this id up by (``alias or id``), read
    straight from ngc.tsv -- see the module docstring for why this needs no
    Messier-number logic of its own."""
    out: dict[str, str] = {}
    with open(NGC_TSV, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            ident = parts[0]
            alias = parts[7] if len(parts) > 7 else ""
            out[ident] = alias or ident
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint-dir", type=Path, default=None)
    ap.add_argument("--keep-checkpoints", action="store_true")
    args = ap.parse_args(argv)

    checkpoint_dir = args.checkpoint_dir or Path(tempfile.gettempdir()) / "astrodeck_discoverer_pages"

    print("fetching P528/NGC+IC statements from Wikidata (resumable)...", file=sys.stderr)
    rows = _fetch_all_pages(checkpoint_dir)
    print(f"  {len(rows)} raw bindings", file=sys.stderr)

    final, stats = _resolve(rows)
    query_keys = _load_query_keys()

    out_rows: dict[str, tuple] = {}
    for ident, key in query_keys.items():
        hit = final.get(key)
        if hit is None:
            continue
        disc, date_iso, prec = hit
        out_rows[ident] = (ident, disc, date_iso, prec)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write("# Wikidata (https://www.wikidata.org), CC0 -- no attribution required.\n")
        fh.write("# P61 (discoverer) + P575 (point in time), joined on the P528 NGC/IC\n")
        fh.write("# catalog-code statement. Ambiguous matches are dropped, not guessed --\n")
        fh.write("# see build_discoverers.py's module docstring for the three kinds.\n")
        fh.write("# Regenerate: python -m astrodeck.catalog.build_discoverers\n")
        fh.write("# id\tdiscoverer\tdiscovery_date\tdate_precision\n")
        for key in sorted(out_rows):
            fh.write("\t".join(out_rows[key]) + "\n")

    if not args.keep_checkpoints:
        shutil.rmtree(checkpoint_dir, ignore_errors=True)

    print(f"resolved {stats['resolved']} codes total "
          f"({stats['ambiguous_code']} dropped: code named >1 item, "
          f"{stats['ambiguous_discoverer']} dropped: >1 human discoverer, "
          f"{stats['ambiguous_date']} dropped: conflicting dates, "
          f"{stats['no_discoverer']} had no discoverer at all)")
    print(f"wrote {len(out_rows)} rows to {OUT} "
          f"({sum(1 for r in out_rows.values() if r[2])} with a date)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
