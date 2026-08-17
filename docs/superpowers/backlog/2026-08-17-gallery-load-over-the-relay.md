# The gallery over the relay: measured, and mostly not what we thought

Reported 2026-08-17: over the relay the gallery "seems to crash or something
else happens preventing the images from loading", with thumbnails suspected at
~17 KB each and a request for pagination with a LOAD MORE button.

Measured. **Three of the four hypotheses were already true in the product, and
my own first measurement of the fourth was wrong.** Recorded in full because
the wrong number was nearly acted on.

## One page load, through the relay

    wall clock to settle : 8.7 s
    API requests         : 53        total 231 KB
    thumbnails           : 50        121 KB
    thumb bytes          : min 996  median 1,506  max 7,491
    failures             : 0

    200     965 B  /api/gallery/nights
    200  71,082 B  /api/gallery/frames?limit=200
    200  40,166 B  /api/gallery/trash

**The failure did not reproduce.** One clean load, nothing over 400, under nine
seconds.

## The thumbnails are fine, and my first number was garbage

I first reported gallery thumbnails at "median 5.0 KB, max 187.9 KB". The max
was wrong: the glob fell through to `rglob("*.jpg")` and swept in the offline
survey pack, whose HiPS tiles average 64 KB. Broken down properly, by the width
step baked into each cache filename:

    width      n    median       p90       max
      256   1548      1.6K      5.6K      8.6K
      512   1548      5.1K     24.5K     33.6K
      768     84     16.2K     35.3K     68.7K

The grid loads the **256 px tier at 1.6 KB median**. Fifty of them is 80 KB.
There is no thumbnail problem to fix, and re-encoding them would have been days
of work against a measurement error.

The remembered "17 KB" is the SESSION thumbnails (14.7 KB median) - the ~512 px
review renders, a different cache for a different screen.

## Pagination, LOAD MORE and lazy loading all already exist

* `GET /api/gallery/frames` already takes `offset` and `limit`, and returns
  `total` so the caller knows what it has not got.
* `GalleryView` already has `onLoadMore()` paging by `rows.length`, and already
  renders a **"Load N more"** button (GalleryView.tsx:681).
* `FrameTile` already holds `src` back behind an IntersectionObserver until a
  tile is nearly on screen, which is why one page of 200 rows produced 50
  thumbnail requests and not 200.

That machinery is why the measured load was 231 KB rather than several MB.

## What was actually worth fixing

`/api/gallery/trash` returned the **whole bin - 40 KB of rows - on every gallery
open**, purely to put a number on the Trash tab. Second-largest payload of a
page load, to render one integer. `?count_only=1` now drops the rows; the panel
still fetches them when it is opened. FIXED.

## What is left, and how to catch it

The failure is real and unreproduced, so nothing here explains it yet. What has
NOT been ruled out:

* **The relay itself.** A single Fly machine proxying a WebSocket, with ~50
  concurrent image requests riding it. The night log already shows the relay
  dropping under load on 2026-08-16: `relay connection lost
  (ConnectionClosedError: sent 1011 keepalive ping timeout); retrying
  local-only`. That is the most promising lead by some distance, and nothing
  about the gallery's own code would show it.
* **A cold cache.** Every measurement above hit warm thumbnails. The first view
  of a night decodes FITS per tile; on a Pi-class box that is a different
  animal, and the 8.7 s could be minutes.
* **`limit=200`** is the first-paint page size. Bounded in bytes by lazy
  loading, but it is still 71 KB of JSON before a single tile appears.

Worth capturing when it next fails: whether `remote` logs a relay drop at the
same moment, whether the failures are HTTP statuses or images that never start,
and whether it is the first view of a night (cold) or a revisit (warm).
