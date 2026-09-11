// slowClock.ts - the Weather hub's shared minute clock.
//
// WHY A CLOCK AT ALL, when `Date.now()` is right there. Because `Date.now()`
// read inside a `useMemo` is not a clock: it is the instant that memo last ran,
// frozen until one of its dependencies changes. `DomeScreen`'s target marker
// and its path to dawn were both computed that way, keyed on `[target, lat,
// lon]` - three values that do not move during a session - so the marker sat
// where the object was when the panel mounted while the dome under it re-tiled
// every couple of minutes. An hour in, the ring was an hour wrong and nothing
// on the screen said so.
//
// WHY ONE MINUTE, and not the status bus. The status frame arrives every two
// seconds and the conditions screen draws a 96-sample chart; re-deriving that
// thirty times a minute is work nobody asked for. A minute is also the finest
// grain any of the copy claims ("updated 4 min ago", a path sampled in quarter
// hours), so a faster tick would change pixels without changing facts.
//
// WHY IT IS SHARED rather than one per screen: the dome's ring and the
// conditions band's "now" are two readings of the same instant, and two
// independent intervals started at different mount times drift apart by up to a
// minute. One hook means one rule; the interval itself is still per-consumer,
// which is the cheap part.

import { useEffect, useState } from "react";

/** Seconds since the epoch, re-published every `periodMs` (default 60 s). */
export function useSlowClock(periodMs = 60_000): number {
  const [t, setT] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setT(Date.now() / 1000), periodMs);
    return () => window.clearInterval(id);
  }, [periodMs]);
  return t;
}
