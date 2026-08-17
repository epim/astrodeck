# The gallery over the relay: measured, not guessed

Reported 2026-08-17: over the relay the gallery "seems to crash or something
else happens preventing the images from loading", with a suspicion that
thumbnails are too big (~17 KB each) and that it needs pagination with a LOAD
MORE button.

Measured before proposing anything, because the numbers change the fix.

## What one page load actually does

Driven in a browser through `https://astrodeck-relay.fly.dev/h/home-1/`,
clicking Gallery from a warm session and timing to settle:

    wall clock to settle : 8.7 s
    API requests         : 53        total 231 KB
    thumbnails           : 50        121 KB
    thumb bytes          : min 996  median 1,506  max 7,491
    last thumb at        : t+2.2 s
    failures             : 0

    200   965 B    t+0.5s  /api/gallery/nights
    200  71,082 B  t+0.6s  /api/gallery/frames?limit=200
    200  40,166 B  t+0.7s  /api/gallery/trash

**I did not reproduce the failure.** One clean load, nothing over 400, under
nine seconds. So what follows is about the loaded gun, not a smoking one.

## What is on disk

    FITS frames on disk : 1,522

    gallery thumbs : 3,142 files   22.0 MB   mean 7.2 KB
                     median 5.0 KB   p90 34.3 KB   max 187.9 KB
    session thumbs : 1,019 files   13.9 MB   mean 14.0 KB
                     median 14.7 KB  p90 26.8 KB   max 32.0 KB

Three things fall out of that.

**1. The 17 KB figure is the SESSION thumbs, not the gallery ones.** Gallery
thumbnails run a 5 KB median. Session thumbnails - the ~512 px review renders
the sessions spec added - run 14.7 KB median, which is where the remembered
number comes from. Shrinking the wrong set would cost work and change nothing
about the gallery.

**2. The median is not the problem; the tail is.** Gallery thumbs are 5 KB in
the middle and **188 KB at the top**, a 37x spread. A mean of 7.2 KB hides
that completely. Whatever renders them is not enforcing a size or a quality
ceiling, so a frame with a lot of high-frequency detail (a rich star field, or
a noisy sub) produces a "thumbnail" a third the size of a JPEG preview. Fifty
of those is 9 MB, not 121 KB - and fifty is what one screenful asked for.

**3. There are twice as many thumbs as frames.** 3,142 gallery thumbs against
1,522 FITS. Some of that is legitimate (a frame can have more than one render),
but `/api/gallery/trash` returning 40 KB on every gallery open suggests deleted
frames are keeping their thumbs. Worth a sweep.

## The two fixes, in the order they matter

**Cap the thumbnail encode.** A 188 KB thumbnail is a bug regardless of
pagination - it is 37x the median for the same on-screen size. Fix the encoder
(max dimension + a quality/target-bytes ceiling, re-encoding above it) and the
worst case per screenful drops by an order of magnitude on its own. Existing
thumbs need a one-shot re-encode pass, which can run in the background.

**Then paginate.** `?limit=200` is a hard cap with no UI: with 1,522 frames on
disk, the gallery silently shows the newest 200 and there is no way to reach
the rest. That is a correctness problem as much as a performance one - the
frames are there and the product does not admit they exist. A LOAD MORE button
appending the next page is the right shape, and it is also the only honest one:
infinite scroll over a relay that may be the thing failing would hide the
failure inside a spinner.

## What to look at when it next fails

The load that fails is the interesting one and I did not catch it. Worth
capturing when it happens: whether the relay socket drops (the `remote` source
logs "relay connection lost ... retrying local-only"), whether the failures are
HTTP or the images themselves, and how many thumbs were in flight. The relay is
a single Fly machine proxying a WebSocket; fifty concurrent image requests
through it is a very different load from fifty over the LAN, and none of the
LAN testing would have shown it.
