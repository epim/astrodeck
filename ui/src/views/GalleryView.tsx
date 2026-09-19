// views/GalleryView.tsx — browse, search, night-filter, bulk-download and
// delete every frame the rig has written (gallery design 2026-08-03).
//
// WHAT THIS SCREEN IS FOR. Until now the only way to see what a night produced
// was to open the capture folder on the box itself. This is the first surface
// that answers "what have I actually got, and how much disk is it eating" from
// the same tablet that took the pictures.
//
// THREE DECISIONS WORTH KNOWING BEFORE EDITING
//
// 1. THE DATE FILTER IS NOT A FILENAME FILTER. The default naming template
//    stamps the CALENDAR date into the filename, but an observing night runs
//    noon to noon — so a session that starts at 21:00 has half its frames named
//    with the next day's date. Filtering on the name would silently return half
//    a night and look like it worked. The server derives the night from each
//    frame's capture instant (events.night_key), and this screen only ever
//    sends it the night key. Frames whose two dates disagree say so on the tile.
//
// 2. BULK DOWNLOAD IS A NAVIGATION, NOT A FETCH. The archive is streamed and can
//    be tens of GB; `fetch()`-ing it into a Blob would put all of that back in
//    the browser's memory and undo the streaming entirely. The session is a
//    cookie, so a plain <a href> authenticates exactly as a fetch would. The
//    price is that the selection has to fit in a URL — see downloadPlan() in
//    lib/gallery.ts, which refuses (with a sentence) rather than truncating.
//
// 3. THE COUNT AND THE SIZE ARE SHOWN BEFORE ANYTHING STARTS. "Download 1,284
//    frames, 38.2 GB" is information the user has no other way to obtain and is
//    the difference between a deliberate action and a surprise. The numbers come
//    from the listing response's `total`/`bytes`, which the server computes for
//    the WHOLE filtered set through the same resolver `download.zip` uses — so
//    the button and the wire cannot disagree without a bug in one shared
//    function.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { ApiError } from "../api";
import {
  backfillThumbs, getTrashCount, listFrames, listNights, trashFrames,
} from "../api/gallery";
import { useStore } from "../store";
import { BASE } from "../lib/base";
import { accessPhrase, useCan, useCanControlCapture } from "../lib/caps";
import { confirmDialog } from "../components/ConfirmDialog";
import { Icon } from "../components/icons";
import {
  EmptyState, HonestButton, InfoDot, LockedNote, Panel,
} from "../components/ui";
import FrameTile from "../components/gallery/FrameTile";
import FrameViewer from "../components/gallery/FrameViewer";
import CaptureGroups from "../components/gallery/CaptureGroups";
import TrashPanel from "../components/gallery/TrashPanel";
import {
  TRASH_BATCH_CAP, downloadPlan, fmtBytes, fmtCount, fmtFrameCost,
  nightOptionLabel, nightRangeLabel, partialFailureNote, pickedTotals, scanNote,
  selectRange, togglePath, tonightHasFrames, trashBatchReason, trashConfirmCopy,
  truncatedNote, type GallerySelection,
} from "../lib/gallery";
import type { GalleryFrame, GalleryFramesPage, GalleryNightsResponse } from "../types";

/** Rows per request. The server clamps at 500; 200 keeps the JSON small enough
 *  that a phone on a hotspot gets its first screenful quickly, and "Load more"
 *  costs one warm scan (~4 ms) rather than a cold one. */
const PAGE = 200;

/** How long after the last keystroke the search actually runs. Every request is
 *  a library walk on the far end, so typing "M42" must not be four of them. */
const SEARCH_DEBOUNCE_MS = 300;

/** The engaged face of the Frames/Trash tab pair. ONE definition because it is
 *  one control: the two tabs disagreeing about how selection looks is its own
 *  defect.
 *
 *  THE `!` ON THE FILL IS LOAD-BEARING, and it is the whole reason this constant
 *  exists rather than an inline string somebody can trim. `.btn` is declared in
 *  index.css OUTSIDE any cascade layer and sets `background: var(--bg-raise)`;
 *  Tailwind's `bg-accent/10` lands inside `@layer utilities`. Unlayered CSS beats
 *  layered CSS no matter the specificity, so a plain `bg-accent/10` on a `.btn`
 *  RENDERS NOTHING. Verified in the built stylesheet, not reasoned about: run
 *  `vite build` and the emitted `.bg-accent\/10` rule sits inside the
 *  `@layer utilities{…}` block while `.btn` sits after its closing brace.
 *  `!bg-accent/10` compiles to `background-color: … !important`, which is the one
 *  thing that does beat an unlayered declaration.
 *
 *  MountView.tsx:623-629 records the same trap after deleting its own dead fill.
 *  NavMoreSheet.tsx:269 and SlewPad.tsx:375/470 still carry the non-important
 *  form on `.btn` elements, so those fills paint nothing either. */
const SELECTED_TAB = "!border-accent !text-accent !bg-accent/10";

export default function GalleryView(): JSX.Element {
  const enqueueToast = useStore((s) => s.enqueueToast);
  // view.preview lists and thumbnails; view.media hands over raw FITS (which
  // embed SITELAT/SITELONG); control.capture deletes. All three are separate
  // answers, and each blocked control says which one it wanted.
  const canBrowse = useCan("view.preview");
  const canMedia = useCan("view.media");
  const canDelete = useCanControlCapture();

  const [tab, setTab] = useState<"library" | "trash">("library");
  const [qDraft, setQDraft] = useState("");
  const [q, setQ] = useState("");
  const [nightFrom, setNightFrom] = useState("");
  const [nightTo, setNightTo] = useState("");
  const [nights, setNights] = useState<GalleryNightsResponse | null>(null);
  const [page, setPage] = useState<GalleryFramesPage | null>(null);
  const [rows, setRows] = useState<GalleryFrame[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // The frame the operator opened, or null. One at a time: this is a
  // look-at-it overlay, not a filmstrip.
  const [viewing, setViewing] = useState<GalleryFrame | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [allInFilter, setAllInFilter] = useState(false);
  /** The last tile ticked by a PLAIN click. A shift+click selects from here to
   *  the tile clicked, so a bad run is two clicks rather than forty. It stays
   *  put across range selects, so the span can be re-extended. */
  const [anchor, setAnchor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [trashCount, setTrashCount] = useState<number | null>(null);
  // The bin's real TTL, read from the server rather than assumed: the
  // confirmation promises "recoverable for N days", and a hardcoded N that
  // drifts from the server's is a promise the UI cannot keep.
  const [trashTtl, setTrashTtl] = useState(30);
  /** Bumped to force a re-read after anything mutates the library. */
  const [gen, setGen] = useState(0);
  const refresh = useCallback(() => setGen((g) => g + 1), []);

  // Rebuilding previews is minutes of CPU on a big library, so it carries an
  // in-flight state rather than a click that appears to do nothing. It reports
  // TRUNCATION explicitly: a partial pass that said "done" would leave frames
  // permanently cold while claiming otherwise.
  const [warming, setWarming] = useState(false);
  const rebuildPreviews = useCallback(async () => {
    if (warming) return;
    setWarming(true);
    try {
      const r = await backfillThumbs();
      enqueueToast({
        level: r.truncated ? "warning" : "info",
        title: r.rendered
          ? `Rebuilt ${r.rendered} preview${r.rendered === 1 ? "" : "s"}`
          : "Every preview was already built",
        detail: [
          `${r.frames} frame${r.frames === 1 ? "" : "s"} checked`,
          r.unrenderable ? `${r.unrenderable} could not be rendered` : "",
          r.truncated ? "the library was longer than one pass — run it again" : "",
        ].filter(Boolean).join(" · "),
      });
      refresh();
    } catch (e) {
      enqueueToast({
        level: "error",
        title: "Could not rebuild previews",
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setWarming(false);
    }
  }, [warming, refresh]);
  /** Bumped on every listing REQUEST. The effect below has its own `alive`
   *  flag, but "Load more" is fired from a click and outlives no effect, so it
   *  needs this: it APPENDS, so a page fetched under the previous filter does
   *  not merely paint stale tiles, it concatenates them onto the new grid AND
   *  replaces `page` — the object `total`/`bytes` come from, which the select-
   *  all count and the download button's stated price are computed from and
   *  which the .zip href does not share. */
  const listReq = useRef(0);

  // ---- search debounce -----------------------------------------------------
  useEffect(() => {
    const t = window.setTimeout(() => setQ(qDraft.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [qDraft]);

  // ---- the listing ---------------------------------------------------------
  useEffect(() => {
    if (!canBrowse) return;
    let alive = true;
    listReq.current += 1;
    setLoading(true);
    setErr(null);
    // A changed filter is a changed set, so both selections stop meaning what
    // they meant. Carrying them over is how "delete everything I ticked" ends
    // up deleting something the user can no longer see.
    setPicked(new Set());
    setAllInFilter(false);
    listFrames({ q, nightFrom, nightTo, offset: 0, limit: PAGE })
      .then((p) => {
        if (!alive) return;
        setPage(p);
        setRows(p.frames);
      })
      .catch((e) => {
        if (!alive) return;
        setPage(null);
        setRows([]);
        setErr(e instanceof ApiError ? e.message : "could not read the capture library");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [q, nightFrom, nightTo, gen, canBrowse]);

  // ---- which nights exist --------------------------------------------------
  useEffect(() => {
    if (!canBrowse) return;
    let alive = true;
    listNights()
      .then((n) => { if (alive) setNights(n); })
      .catch(() => { if (alive) setNights(null); });
    return () => { alive = false; };
  }, [gen, canBrowse]);

  // ---- trash badge ---------------------------------------------------------
  // Fetched here as well as inside TrashPanel so the tab can carry a count
  // BEFORE the panel is ever opened — "Trash 41" is what tells someone their
  // disk is still full of things they thought they deleted.
  useEffect(() => {
    if (!canDelete) return;
    let alive = true;
    // COUNT ONLY. This asked for the whole bin to render one number - 40 KB
    // over the relay on every gallery open. TrashPanel still fetches the rows.
    getTrashCount()
      .then((t) => {
        if (!alive) return;
        setTrashCount(t.count);
        setTrashTtl(t.ttl_days);
      })
      .catch(() => { if (alive) setTrashCount(null); });
    return () => { alive = false; };
  }, [gen, canDelete]);

  // ---- selection -----------------------------------------------------------
  const selection: GallerySelection = useMemo(
    () => (allInFilter
      ? { mode: "filter", q, nightFrom, nightTo }
      : { mode: "picked", paths: [...picked] }),
    [allInFilter, q, nightFrom, nightTo, picked],
  );
  const selCount = allInFilter ? (page?.total ?? 0) : picked.size;
  const selBytes = allInFilter
    ? (page?.bytes ?? 0)
    : pickedTotals(picked, rows).bytes;

  const plan = downloadPlan(selection, selCount);
  const downloadReason = !canMedia
    ? `Downloading raw FITS needs ${accessPhrase("view.media")} — the files embed the observatory's coordinates.`
    : plan.ok ? null : plan.reason;
  const deleteReason = !canDelete
    ? `Deleting needs ${accessPhrase("control.capture")}.`
    : busy ? "Another gallery action is still running."
      : selCount <= 0 ? "Nothing is selected."
        : trashBatchReason(selCount);

  const explain = (reason: string): void =>
    enqueueToast({ level: "info", title: "Not available", detail: reason });

  /** The paths a delete will actually submit.
   *
   *  A ticked selection is already a list. "Everything in the filter" is not —
   *  the grid holds one page — so it is enumerated by paging the listing, and
   *  the enumeration is what gets sent. Deleting "the filter" by handing the
   *  server a filter would be a different contract than the one the confirmation
   *  dialog just described, and the count in that dialog has to be the count
   *  that goes. */
  async function collectPaths(): Promise<string[]> {
    if (!allInFilter) return [...picked];
    const out: string[] = [];
    const total = page?.total ?? 0;
    for (let off = 0; off < total && out.length < TRASH_BATCH_CAP; off += 500) {
      const p = await listFrames({ q, nightFrom, nightTo, offset: off, limit: 500 });
      if (!p.frames.length) break;
      for (const f of p.frames) out.push(f.path);
    }
    return out;
  }

  async function onDelete(): Promise<void> {
    setBusy(true);
    try {
      const paths = await collectPaths();
      if (!paths.length) return;
      const copy = trashConfirmCopy(paths.length, selBytes, trashTtl);
      const go = await confirmDialog({
        title: copy.title,
        body: copy.body,
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Move to trash",
      });
      if (!go) return;
      const r = await trashFrames(paths);
      enqueueToast({
        level: r.failed.length ? "warning" : "success",
        title: `Moved ${fmtCount(r.trashed.length)} ${r.trashed.length === 1 ? "frame" : "frames"} to the trash`,
        detail: partialFailureNote(r.trashed.length, r.failed)
          ?? `${fmtBytes(r.bytes)} recoverable for ${trashTtl} days — the space is not freed until it purges.`,
      });
      refresh();
    } catch (e) {
      enqueueToast({
        level: "error",
        title: "Delete failed",
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function onLoadMore(): Promise<void> {
    // The page this click is asking for belongs to the filter that is on screen
    // NOW; if that moves while the request is in flight the answer is about a
    // set nobody is looking at any more (see `listReq`).
    const req = listReq.current;
    setLoadingMore(true);
    try {
      const p = await listFrames({ q, nightFrom, nightTo, offset: rows.length, limit: PAGE });
      if (req !== listReq.current) return;
      setRows((r) => [...r, ...p.frames]);
      setPage(p);
    } catch (e) {
      if (req !== listReq.current) return;
      enqueueToast({
        level: "error",
        title: "Couldn't load more frames",
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setLoadingMore(false);
    }
  }

  // ---- render --------------------------------------------------------------
  if (!canBrowse) {
    return (
      <Panel title="Gallery">
        <EmptyState
          icon="gallery"
          title="Browsing the capture library isn't available to this account"
          hint={`Listing frames and thumbnails needs ${accessPhrase("view.preview")}.`}
        />
      </Panel>
    );
  }

  const nightList = nights?.nights ?? [];
  const tonight = nights?.current ?? "";
  const tonightLive = tonightHasFrames(tonight, nightList);
  const cold = page ? scanNote(page.scan_ms, page.total) : null;
  //: one shape for every action button on this screen; >=44px on a phone.
  const actionBtn = "!py-1.5 !px-3 text-[11px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5";

  return (
    <>
    <div className="flex flex-col gap-3">
      {/* =============================================================== filter */}
      <Panel
        title="Capture library"
        right={
          <div className="flex items-center gap-1.5">
            {/* SELECTION IS A FILL, NOT A HUE. These two used to mark the active
                tab with `!border-accent !text-accent` alone. Under the red night
                palette that is worse than nothing: --accent (#ff3a3a) is DIMMER
                than --text (#ff7a7a), so the selected label read fainter than
                the unselected one — like the disabled tab — while the 1px border
                got brighter, i.e. the two channels pointed opposite ways and the
                word won by area. index.css:154-157 states the rule ("Any status
                that must survive night mode needs a non-hue channel — ... fill
                for selection").

                Do NOT copy the fill from NavMoreSheet:269 or SlewPad:375/470.
                Those append a NON-important `bg-accent/10` to a `.btn`, which
                the unlayered `.btn` background overrides — they are dead
                declarations, not working precedent. See SELECTED_TAB above. */}
            <button
              className={`btn ${actionBtn} ${tab === "library" ? SELECTED_TAB : ""}`}
              onClick={() => setTab("library")}
              aria-pressed={tab === "library"}
            >
              <Icon name="gallery" size={13} /> Frames
            </button>
            {canDelete && (
              <button
                className={`btn ${actionBtn} ${tab === "trash" ? SELECTED_TAB : ""}`}
                onClick={() => setTab("trash")}
                aria-pressed={tab === "trash"}
              >
                <Icon name="trash" size={13} /> Trash
                {trashCount != null && trashCount > 0 && (
                  <span className="tabular-nums">{fmtCount(trashCount)}</span>
                )}
              </button>
            )}
          </div>
        }
      >
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 min-w-0 flex-1 basis-[220px]">
            <span className="label inline-flex items-center gap-1">
              Search
              <InfoDot
                label="About gallery search"
                content="Matches anywhere in the frame's path, folder included — so typing a target name finds that target's frames, and typing a filter letter finds every frame shot through it."
              />
            </span>
            <input
              className="field"
              type="search"
              value={qDraft}
              placeholder="target, filter, filename…"
              onChange={(e) => setQDraft(e.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 min-w-0 basis-[210px]">
            <span className="label inline-flex items-center gap-1">
              From night
              <InfoDot
                label="About observing nights"
                content="A night runs noon to noon, so everything from one session lands under one key even across midnight. Filenames carry the calendar date instead, which is why a 00:10 frame's name and its night disagree."
              />
            </span>
            <select
              className="field"
              value={nightFrom}
              onChange={(e) => setNightFrom(e.target.value)}
            >
              <option value="">Any night</option>
              {nightList.map((n) => (
                <option key={n.night} value={n.night}>{nightOptionLabel(n)}</option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 min-w-0 basis-[210px]">
            <span className="label">To night</span>
            <select
              className="field"
              value={nightTo}
              onChange={(e) => setNightTo(e.target.value)}
            >
              <option value="">Any night</option>
              {nightList.map((n) => (
                <option key={n.night} value={n.night}>{nightOptionLabel(n)}</option>
              ))}
            </select>
          </label>

          <div className="flex flex-wrap items-center gap-2">
            <HonestButton
              className={`btn ${actionBtn}`}
              // Offering "Tonight" when tonight has produced nothing yet would
              // filter to an empty grid and read as a broken filter.
              reason={tonightLive ? null : `Nothing has been captured yet on tonight's night (${tonight || "unknown"}).`}
              onClick={() => { setNightFrom(tonight); setNightTo(tonight); }}
              onExplain={explain}
            >
              <Icon name="moon" size={13} /> Tonight
            </HonestButton>
            <HonestButton
              className={`btn ${actionBtn}`}
              reason={!q && !nightFrom && !nightTo ? "No filter is set." : null}
              onClick={() => { setQDraft(""); setQ(""); setNightFrom(""); setNightTo(""); }}
              onExplain={explain}
            >
              <Icon name="x" size={13} /> Clear filter
            </HonestButton>
            <button className={`btn ${actionBtn}`} onClick={refresh} title="Re-read the library from disk">
              <Icon name="refresh" size={13} /> Refresh
            </button>
            {/* Frames captured from 0.2.72 on warm their own preview as they
                land. This is for everything shot before that, and for any gap a
                restart left mid-night — without it those frames are only ever
                rendered the first time somebody scrolls past them, which on a
                desktop grid is forty at once. */}
            <HonestButton
              className={`btn ${actionBtn}`}
              reason={warming ? "Already rebuilding previews." : null}
              onClick={rebuildPreviews}
              onExplain={explain}
            >
              <Icon name="gallery" size={13} />
              {warming ? "Rebuilding previews…" : "Rebuild previews"}
            </HonestButton>
          </div>
        </div>

        {/* What the filter currently means, and what it found. Both halves are
            needed: the range in words ("Night of 2026-06-15") is what makes an
            inclusive two-ended range legible, and the totals are the number the
            download button is about to act on. */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3 text-xs text-dim">
          <span className="inline-flex items-center gap-1.5 text-ink">
            <Icon name="clock" size={12} aria-hidden />
            {nightRangeLabel(nightFrom, nightTo)}
          </span>
          <span>
            {loading ? "reading…" : page ? fmtFrameCost(page.total, page.bytes) : "—"}
            {page && rows.length < page.total && !loading && (
              <span className="text-faint"> · showing {fmtCount(rows.length)}</span>
            )}
          </span>
          {nights?.truncated && (
            <span className="inline-flex items-center gap-1 text-warn">
              <Icon name="alert" size={12} aria-hidden /> night index is partial
            </span>
          )}
        </div>

        {page?.truncated && (
          <p className="flex items-start gap-1.5 text-[11px] text-warn mt-2">
            <Icon name="alert" size={12} aria-hidden className="mt-0.5" />
            <span>{truncatedNote(page.total)}</span>
          </p>
        )}
        {cold && (
          <p className="flex items-start gap-1.5 text-[11px] text-dim mt-2">
            <Icon name="info" size={12} aria-hidden className="mt-0.5" />
            <span>{cold}</span>
          </p>
        )}
        {err && (
          <p className="flex items-start gap-1.5 text-xs text-bad mt-2">
            <Icon name="alert" size={12} aria-hidden className="mt-0.5" />
            <span>{err}</span>
          </p>
        )}
      </Panel>

      {tab === "trash" && canDelete ? (
        <TrashPanel onChanged={refresh} />
      ) : (
        <>
          {/* ========================================================== actions */}
          <Panel className="!py-3">
            <div className="flex flex-wrap items-center gap-2">
              <HonestButton
                className={`btn ${actionBtn}`}
                reason={(page?.total ?? 0) === 0 ? "The filter matched nothing." : null}
                onClick={() => { setAllInFilter(true); setPicked(new Set()); }}
                onExplain={explain}
              >
                <Icon name="check" size={13} />
                {page ? `Select all ${fmtCount(page.total)} in filter` : "Select all in filter"}
              </HonestButton>
              <HonestButton
                className={`btn ${actionBtn}`}
                reason={selCount === 0 ? "Nothing is selected." : null}
                onClick={() => { setAllInFilter(false); setPicked(new Set()); }}
                onExplain={explain}
              >
                Clear selection
              </HonestButton>

              <div className="flex-1" />

              {/* The size lives ON the button. A confirmation that appears only
                  after the click is a confirmation of a decision already made. */}
              {downloadReason ? (
                <HonestButton
                  className={`btn ${actionBtn}`}
                  reason={downloadReason}
                  onClick={() => { /* unreachable while `reason` is set */ }}
                  onExplain={explain}
                >
                  <Icon name="download" size={13} /> Download
                </HonestButton>
              ) : (
                <a
                  className={`btn btn-accent ${actionBtn}`}
                  // A navigation, not a fetch — see this file's header (3).
                  href={`${BASE}${plan.ok ? plan.href : ""}`}
                  download
                  title="Streamed as one .zip; the folder structure is preserved"
                >
                  <Icon name="download" size={13} />
                  Download {fmtFrameCost(selCount, selBytes)}
                </a>
              )}

              <HonestButton
                className={`btn btn-danger ${actionBtn}`}
                reason={deleteReason}
                onClick={() => void onDelete()}
                onExplain={explain}
              >
                <Icon name="trash" size={13} /> Move to trash
              </HonestButton>
            </div>

            {/* Selection state in words. "Select all in filter" covers frames
                this page has not loaded, which is the whole point of it and also
                the thing that would otherwise surprise someone. */}
            <div className="mt-2 text-[11px] text-dim">
              {allInFilter ? (
                <span className="inline-flex items-start gap-1.5">
                  <Icon name="check" size={12} aria-hidden className="mt-0.5 text-accent" />
                  <span>
                    Everything the filter matches is selected — {fmtFrameCost(selCount, selBytes)},
                    including the {fmtCount(Math.max(0, (page?.total ?? 0) - rows.length))} not yet
                    shown below.
                  </span>
                </span>
              ) : picked.size > 0 ? (
                <span className="inline-flex items-start gap-1.5">
                  <Icon name="check" size={12} aria-hidden className="mt-0.5 text-accent" />
                  <span>{fmtFrameCost(picked.size, selBytes)} ticked.</span>
                </span>
              ) : (
                <span>Tick frames to act on some, or use Select all to take the whole filter.</span>
              )}
            </div>

            {!canMedia && (
              <LockedNote
                className="mt-2"
                reason={`Raw FITS downloads need ${accessPhrase("view.media")} — every frame embeds the observatory's coordinates. Thumbnails are not restricted.`}
              />
            )}
          </Panel>

          {/* ============================================================= grid */}
          {!loading && !!page?.geometry_groups?.length && (
            <Panel><CaptureGroups groups={page.geometry_groups} incomplete={page.truncated || page.geometry_truncated} /></Panel>
          )}
          {loading && rows.length === 0 ? (
            <Panel><p className="text-xs text-dim">Reading the capture library…</p></Panel>
          ) : rows.length === 0 ? (
            <Panel>
              <EmptyState
                icon="gallery"
                title={q || nightFrom || nightTo ? "Nothing matches this filter" : "No frames on disk yet"}
                hint={
                  q || nightFrom || nightTo
                    ? "Search matches the whole path, and night bounds are inclusive noon-to-noon keys — a session that ran past midnight is filed under the night it started."
                    : "Frames appear here as soon as a capture or a sequence writes one."
                }
              />
            </Panel>
          ) : (
            <Panel className="!p-3">
              <div
                className="grid gap-2"
                style={{ gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}
              >
                {rows.map((f) => (
                  <FrameTile
                    key={f.path}
                    frame={f}
                    // "All in filter" ticks every visible tile too: a selection
                    // the grid contradicts is a selection nobody trusts.
                    selected={allInFilter || picked.has(f.path)}
                    selectable={canDelete || canMedia}
                    canDownload={canMedia}
                    onOpen={canMedia ? () => setViewing(f) : undefined}
                    onToggle={(shift) => {
                      if (allInFilter) {
                        // Un-ticking one tile out of "everything" means "all of
                        // them except this" — which the URL form cannot express,
                        // so it collapses to an explicit list of what is loaded.
                        setAllInFilter(false);
                        setPicked(new Set(rows.map((r) => r.path).filter((p) => p !== f.path)));
                        setAnchor(f.path);
                        return;
                      }
                      // A shift+click with somewhere to reach from sweeps the
                      // span. Everything else is a plain toggle AND moves the
                      // anchor — including un-ticking, so the next sweep starts
                      // from the tile last touched rather than a stale one.
                      if (shift && anchor && anchor !== f.path) {
                        setPicked((p) => selectRange(p, rows.map((r) => r.path), anchor, f.path));
                        return;
                      }
                      setPicked((p) => togglePath(p, f.path));
                      setAnchor(f.path);
                    }}
                  />
                ))}
              </div>

              {page && rows.length < page.total && (
                <div className="flex items-center justify-center gap-3 mt-3">
                  <button
                    className={`btn ${actionBtn}`}
                    onClick={() => void onLoadMore()}
                    // `loading` too: while a changed filter is being re-read the
                    // grid still shows the OLD rows, so this button would be
                    // asking for the next page of a set that is on its way out.
                    disabled={loadingMore || loading}
                  >
                    {loadingMore || loading
                      ? "Loading…"
                      : `Load ${fmtCount(Math.min(PAGE, page.total - rows.length))} more`}
                  </button>
                  <span className="text-[11px] text-dim">
                    {fmtCount(rows.length)} of {fmtCount(page.total)} shown
                  </span>
                </div>
              )}
            </Panel>
          )}
        </>
      )}
      </div>
      {viewing && (
        <FrameViewer frame={viewing} onClose={() => setViewing(null)} />
      )}
    </>
  );
}
