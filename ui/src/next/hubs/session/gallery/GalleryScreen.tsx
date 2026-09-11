// GalleryScreen.tsx - SESSION / GALLERY: everything the rig still holds,
// newest first (plan section C; proto `28-gallery.html`, screenshot 28).
//
// A shelf of NIGHTS, not of frames. The frame library is one tap further away
// in the `archive` sheet, because the question this screen answers is "which
// night am I after" and the library answers "which frame" - and on a phone the
// second question needs a filter bar that does not fit.
//
// THE SUMMARY LINE IS TWO DIFFERENT READS AND SAYS SO. The session count comes
// from `GET /api/sessions`; the bytes come from `GET /api/gallery/nights`,
// which is the disk. When the night index hit its ceiling the total is a
// PREFIX of the library, and the line carries "(partial)" rather than
// presenting it as the whole.

import { useEffect, useMemo, useState, type JSX } from "react";

import { listNights } from "../../../../api/gallery";
import { getSessionStack, sessionStackImageUrl } from "../../../../api/sessionStack";
import { fmtBytes } from "../../../../lib/gallery";
import { runIsLive } from "../../../../lib/lastSessionFrame";
import { useCanControlMount } from "../../../../lib/caps";
import { usePlan, useSafety, useSeq } from "../../../../store";
import type { GalleryNightsResponse } from "../../../../types";
import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { Chip, EmptyCard, Mono } from "../../../ui";
import { explainLock } from "../../../shell/explain";
import { readDownloaded, type DlEntry } from "../sheets/filesData";
import { ARCHIVE_PHONE_REASON } from "../sheets/archive";
import { SessionCard } from "./SessionCard";
import { useSessionCards } from "./useSessionCards";

/** The proto's footer, minus its last clause: the phone now has DELETE on the
 *  card, so "until you clear them from the tablet" would be false (deviation
 *  D10). */
export const GALLERY_FOOTER =
  "Everything the rig still holds, newest first. Tap a session for its "
  + "auto-stack and subs; ones already on this phone are marked.";

/** GAP-ANALYSIS section 8: "what survives a reboot" was missing entirely, and
 *  it is the question behind every hesitation over ABANDON. */
export const REBOOT_NOTE =
  "Sessions, their ledgers and their thumbnails survive a restart of the rig "
  + "computer. A run that was interrupted mid-night is offered for resume on "
  + "the Session screen.";

export function GalleryScreen(): JSX.Element {
  const seq = useSeq();
  const plan = usePlan();
  const safety = useSafety();
  const bp = useBreakpoint();
  const canControl = useCanControlMount();
  const { cards, loading, error, thumbs, refresh } = useSessionCards();

  const [nights, setNights] = useState<GalleryNightsResponse | null>(null);
  const [stackUrl, setStackUrl] = useState<string | null>(null);
  const [dl, setDl] = useState<Record<string, DlEntry>>({});

  useEffect(() => {
    let alive = true;
    listNights().then((n) => { if (alive) setNights(n); }).catch(() => { if (alive) setNights(null); });
    setDl(readDownloaded());
    return () => { alive = false; };
  }, []);

  // One request, for the one card that can show a live composite.
  useEffect(() => {
    if (!runIsLive(seq)) { setStackUrl(null); return; }
    let alive = true;
    getSessionStack()
      .then((s) => { if (alive) setStackUrl(s.has_image ? sessionStackImageUrl(s.seq, 600) : null); })
      .catch(() => { if (alive) setStackUrl(null); });
    return () => { alive = false; };
  }, [seq.state, seq.session?.id]);

  const summary = useMemo(() => {
    const bytes = (nights?.nights ?? []).reduce((a, n) => a + n.bytes, 0);
    const partial = nights?.truncated ? " (partial)" : "";
    return `${cards.length} session${cards.length === 1 ? "" : "s"} · ${fmtBytes(bytes)} on the rig${partial}`;
  }, [cards.length, nights]);

  const liveId = seq.session?.id ?? null;

  return (
    <div data-testid="session-gallery" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <span className="nx-display" style={{ fontSize: 15, letterSpacing: ".1em" }}>GALLERY</span>
        <span data-testid="gallery-summary">
          <Mono size={10} tone="dim">{summary}</Mono>
        </span>
      </div>

      {error && <Mono size={10.5} tone="bad">{error}</Mono>}

      {loading && cards.length === 0 ? (
        <Mono size={10.5} tone="dim">Reading the rig&apos;s sessions…</Mono>
      ) : cards.length === 0 ? (
        <EmptyCard
          title="NOTHING ON THE RIG YET"
          hint="A session appears here as soon as a run writes its first frame."
        />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
          {cards.map((c) => (
            <SessionCard
              key={c.key}
              card={c}
              thumb={c.id ? thumbs[c.id] : null}
              stackUrl={stackUrl}
              live={c.status === "active" && runIsLive(seq) && (liveId == null || liveId === c.id)}
              dl={c.id ? dl[c.id] : undefined}
              canControl={canControl}
              monitorConnected={!!safety?.connected}
              plan={plan}
              onChanged={() => { refresh(); setDl(readDownloaded()); }}
            />
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Chip
          data-testid="gallery-archive"
          lockedReason={bp === "phone" ? ARCHIVE_PHONE_REASON : null}
          onExplain={explainLock}
          onClick={() => nav.sheet("archive")}
        >
          FULL LIBRARY
        </Chip>
      </div>

      <p style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5, margin: 0 }}>
        {GALLERY_FOOTER}
      </p>
      <p style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5, margin: 0 }}>
        {REBOOT_NOTE}
      </p>
    </div>
  );
}

export default GalleryScreen;
