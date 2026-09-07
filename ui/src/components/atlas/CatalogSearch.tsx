// CatalogSearch — the Atlas's inline target search (wave-2 §2). Same idiom as
// Plan's search (SequenceView): 250 ms debounce, GET /api/catalog?q=, top 6.
// Self-contained: owns its query/results state and clears itself after a pick;
// the parent decides what "pick" means (setFraming vs openFraming).
//
// Zero-state (R2-ATL-01 minimal, ATLAS-01, r1 ATL-02): a non-empty query ALWAYS
// renders a dropdown now — "Searching…" while the debounced fetch is in
// flight, otherwise either the result list or an explicit "no matches" panel
// with a scope hint (planet-specific when the query looks like Sun/Moon/a
// planet — those aren't in the catalog, see catalogHint.ts — general
// otherwise). Previously an empty result set rendered nothing at all.
//
// The examples in the placeholder and in the zero-state hint are load-bearing:
// whatever they show, a beginner will type. Both "M 31" and "M31" now find the
// object — server-side, `search_catalog` strips separators from the query and
// the id before comparing (server/astrodeck/catalog/objects.py,
// `squash_designation`), so spacing and case are irrelevant for designations.
// Until that landed the placeholder's own example, "M 31", returned
// "No matches", which is why the default placeholder below carries it too.

import { useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";
import { api } from "../../api";
import type { CatalogEntry } from "../../types";
import { catalogScopeHint } from "../../lib/catalogHint";
import { altTone, fmtAlt } from "../../lib/catalogFormat";
import { Icon } from "../icons";

/** Movement (CSS px) allowed between pointerdown and pointerup before the
 *  gesture stops being a tap. A finger never lands perfectly still; a scroll
 *  travels far more than this within the first frames. */
export const TAP_SLOP_PX = 10;

/** Was an outside pointer gesture a genuine TAP (a dismissal) or a
 *  scroll/drag that merely started outside the widget (not a dismissal)?
 *
 *  This predicate is the whole fix for the phone regression below, so it is
 *  pure and pinned by a test. `down` is null when the gesture did not start
 *  outside the widget, or when the browser claimed it (`pointercancel`, which
 *  is what fires when a touch turns into a scroll). */
export function outsideTapDismisses(
  down: { x: number; y: number } | null,
  up: { x: number; y: number },
  upIsOutside: boolean,
  slop: number = TAP_SLOP_PX,
): boolean {
  if (!down || !upIsOutside) return false;
  return Math.hypot(up.x - down.x, up.y - down.y) <= slop;
}

/** Gap kept between the suggestion list and the edge of the screen. */
export const DROPDOWN_EDGE_MARGIN_PX = 8;

/** How far LEFT the suggestion list has to move to stay on screen.
 *
 *  UX-2026-07-28 S5, measured on an 820px tablet in the Plan: the field sits at
 *  x=563, the list is anchored to the field's left edge and is 288px wide, so it
 *  ran to 851 — 31px past the screen, taking the altitude badge (the number you
 *  choose the target BY) with it. The list is not scrollable sideways and the
 *  page clips, so those pixels were unreachable, not merely ugly.
 *
 *  Pure, and pinned by a test, because the interesting cases are the ones that
 *  must NOT move: shifting a list that already fits would drag it off the other
 *  edge instead. Never shifts further than the anchor's own distance from the
 *  left edge. */
export function dropdownShiftPx(
  rootLeft: number,
  width: number,
  viewportWidth: number,
  margin: number = DROPDOWN_EDGE_MARGIN_PX,
): number {
  const overhang = rootLeft + width + margin - viewportWidth;
  if (!(overhang > 0)) return 0;
  return Math.min(overhang, Math.max(0, rootLeft - margin));
}

export function CatalogSearch({
  onPick,
  placeholder = "Search catalog — e.g. M 31",
  className = "w-56",
}: {
  onPick: (e: CatalogEntry) => void;
  placeholder?: string;
  /** Width of the whole widget. `.field` is 100% wide in unlayered CSS (which
   *  beats a Tailwind utility on the input itself), so the width has to be set
   *  on the wrapper. The 224px default is what every in-a-toolbar caller had
   *  hard-coded; the Atlas empty-state card passes `w-full` so the field is as
   *  wide as the card on a phone instead of a 224px island in the middle. */
  className?: string;
}): JSX.Element {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  //: What the SERVER said it could not answer and why. Empty on an older
  //: server, which is the only case the local hint below still covers.
  const [notes, setNotes] = useState<string[]>([]);
  const [searching, setSearching] = useState(false);
  // Dismissed by outside-click/Escape without clearing the query, so the
  // dropdown stays gone until the user edits the query again or picks a
  // result (which clears search directly). Mirrors ui.tsx's Tooltip.
  const [dismissed, setDismissed] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const dropRef = useRef<HTMLDivElement | null>(null);
  // px the suggestion list is nudged left so it cannot run off the screen. 0 in
  // every layout that already fits (see dropdownShiftPx).
  const [dropShift, setDropShift] = useState(0);
  // Monotonic query id. `clearTimeout` only cancels a debounce that has not
  // fired yet — a request already ON THE WIRE still lands and still writes.
  // On a phone talking to a Pi over patchy WiFi that is routine, and the
  // observed failure is not merely "stale results": the older request FAILING
  // wrote its catch-branch `[]` over a good newer answer, so a correct
  // "M31 Andromeda Galaxy" was replaced, seconds later, by
  // `No matches for "m31" — try a name or ID (e.g. M31)`. Only the newest
  // query may write results.
  const queryId = useRef(0);
  // A failed FETCH is not an empty RESULT. Flattening the catch branch into []
  // made a dead catalog endpoint read as `No matches for "m31"` — a confident,
  // plausible, wrong answer that sends the user off rechecking their spelling
  // while the real problem is the link to the box. Track it and say so.
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setDismissed(false);
  }, [search]);

  useEffect(() => {
    const id = ++queryId.current; // supersedes anything still in flight
    if (!search.trim()) { setResults([]); setSearching(false); return; }
    setSearching(true);
    const t = setTimeout(async () => {
      let next: CatalogEntry[];
      let why: string[] = [];
      let broke = false;
      try {
        // explain=1: the server answers with rows AND notes. The notes are the
        // answers that are not rows — "Pluto is not carried", "the ephemeris is
        // unavailable right now" — composed by the code that actually knows.
        // Without this the browser guessed a reason instead, and went on saying
        // "Planets aren't supported yet" for a year after they were.
        const r = await api.get<{ results: CatalogEntry[]; notes?: string[] }>(
          `/api/catalog?q=${encodeURIComponent(search)}&explain=1`);
        next = (r.results ?? []).slice(0, 6);
        why = r.notes ?? [];
      } catch {
        next = [];
        broke = true;
      }
      if (queryId.current !== id) return; // a newer query owns the field now
      setResults(next);
      setNotes(why);
      setFailed(broke);
      setSearching(false);
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const pick = (e: CatalogEntry) => {
    onPick(e);
    setSearch("");
    setResults([]);
  };

  const showDropdown = search.trim().length > 0 && !dismissed;

  // Keep the suggestion list on the screen (see dropdownShiftPx). Measured from
  // the ANCHOR's left edge and the list's own width — neither depends on the
  // shift we then apply, so this settles in one pass and cannot oscillate.
  // Layout effect, not effect: the list must be in place before it is painted.
  useLayoutEffect(() => {
    if (!showDropdown) { setDropShift(0); return; }
    const measure = () => {
      const root = rootRef.current, drop = dropRef.current;
      if (!root || !drop) return;
      setDropShift(dropdownShiftPx(
        root.getBoundingClientRect().left, drop.offsetWidth, window.innerWidth));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [showDropdown]);

  // Outside-TAP + Escape dismissal (ui.tsx Tooltip precedent) so the dropdown
  // doesn't float over the page forever once the user has looked away without
  // picking a result.
  //
  // This used to dismiss on outside `pointerdown`, and that is a desktop
  // assumption: a mouse only presses where it means to, but a touch screen
  // dispatches `pointerdown` at the start of EVERY finger gesture — including
  // the swipe that scrolls the page. So one scroll (the natural move when the
  // suggestion list sits under the docked wizard bar or the soft keyboard)
  // latched the dropdown shut while the caret was still in the field and the
  // query still typed, and nothing but EDITING the query brought it back.
  // Measured on s25ultra: type "m31" -> M31 listed; one swipe outside ->
  // dropdown gone; re-tapping the input does NOT restore it; backspace to
  // "m3" restores it. That is the field report verbatim ("typed m31, nothing
  // happened … deleted the 1 and it showed m31"), and it is the SECOND time
  // this latch has shut on a phone user — UX-06 was the same defect through
  // onBlur. So dismissal now needs a real tap: down and up both outside, with
  // no travel in between. A scroll moves; a gesture the browser claims for
  // scrolling fires `pointercancel`. Neither is a dismissal.
  useEffect(() => {
    if (!showDropdown) return;
    const outside = (t: EventTarget | null) =>
      !!rootRef.current && !rootRef.current.contains(t as Node);
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => {
      down = outside(e.target) ? { x: e.clientX, y: e.clientY } : null;
    };
    const onUp = (e: PointerEvent) => {
      const from = down;
      down = null;
      if (outsideTapDismisses(from, { x: e.clientX, y: e.clientY }, outside(e.target))) {
        setDismissed(true);
      }
    };
    const onCancel = () => { down = null; };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDismissed(true); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onCancel);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onCancel);
      document.removeEventListener("keydown", onKey);
    };
  }, [showDropdown]);

  return (
    <div className={`relative ${className}`} ref={rootRef}
      onBlur={(e) => {
        // Only dismiss when focus genuinely moved to an element OUTSIDE this
        // widget. A null relatedTarget is focus going nowhere focusable — an
        // Android soft-keyboard hide, or a tap on the non-focusable canvas —
        // which is NOT a real focus-out, so we keep the dropdown (otherwise the
        // first query's results land into a latched-dismissed state; UX-06).
        // Genuine dismissal still comes from outside pointerdown / Escape (the
        // effect above) and picking a result (which clears the query).
        const to = e.relatedTarget as Node | null;
        if (to && !e.currentTarget.contains(to)) setDismissed(true);
      }}>
      <input
        className="field btn-touch"
        placeholder={placeholder}
        aria-label="Search the target catalog"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {/* Dropdown width: as wide as the field, never narrower than 288px (the
          toolbar callers' field is 224px and every row carries a name AND an
          altitude badge), and never wider than the screen it is anchored in. */}
      {showDropdown && (
        <div
          ref={dropRef}
          style={dropShift ? { left: -dropShift } : undefined}
          className="absolute left-0 top-full mt-1 w-full min-w-72 max-w-[calc(100vw-2rem)] panel z-20 max-h-60 overflow-y-auto"
        >
          {searching ? (
            <p className="px-3 py-2 text-xs text-dim">Searching…</p>
          ) : results.length > 0 ? (
            results.map((r) => (
              <button key={r.id} type="button" onClick={() => pick(r)}
                className="w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex justify-between cursor-pointer">
                <span><span className="mono text-accent">{r.id}</span> {r.name}</span>
                <span className={`mono ${altTone(r.alt, "text-dim")}`}>
                  {fmtAlt(r.alt)}
                </span>
              </button>
            ))
          ) : (
            <div className="px-3 py-2 text-xs flex flex-col gap-1">
              {failed ? (
                <>
                  <p className="text-warn inline-flex items-center gap-1.5">
                    <Icon name="alert" size={12} aria-hidden />
                    Couldn&apos;t reach the catalog.
                  </p>
                  <p className="text-dim">
                    That is the connection to the telescope, not your spelling. Keep typing to
                    retry.
                  </p>
                </>
              ) : (
                <>
                  <p className="text-ink">No matches for &quot;{search.trim()}&quot;.</p>
                  {/* The SERVER's reason when it has one — it knows what it
                      carries and whether the ephemeris answered. The local hint
                      is only the fallback for an older server. */}
                  {notes.length > 0
                    ? notes.map((n, i) => <p key={i} className="text-dim">{n}</p>)
                    : <p className="text-dim">{catalogScopeHint(search)}</p>}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default CatalogSearch;
