// archive.tsx - THE FULL FRAME LIBRARY, at tablet and desktop.
//
// The Session hub's Gallery is a shelf of sessions; this is the thing under it:
// every frame the rig has written, the bin they go to when deleted, and the
// live stack's own per-channel breakdown. Plan section C.6.
//
// WHY IT IS A SHEET AND NOT A SCREEN. `views/GalleryView` is layout-bound (a
// three-panel desktop surface with a `minmax(140px, 1fr)` grid), so the design
// gives the phone the session cards instead and puts the library one tap
// further away. Below 768 px this renders the reason and nothing else: a
// 200-column filter bar squeezed onto a phone is worse than an honest absence.
//
// WHAT IS TRANSCRIBED AND WHAT IS MOUNTED. The BROWSE surface is re-laid-out
// here in `nx-` chrome, control for control - search, the two inclusive
// noon-to-noon night bounds, Tonight (with its own reason when tonight has no
// frames), Clear filter, Refresh, Rebuild previews, select-all-in-filter,
// Load more, the priced download and the named delete. The three pieces that
// are NOT layout - `FrameTile`, `FrameViewer` and `TrashPanel` - are mounted
// unchanged, and so is the legacy `SessionStack`, which is the only place the
// per-channel list, the labelled backfill checkbox and Reset live.
//
// THE SELECTION SEMANTICS ARE CARRIED, NOT REINVENTED. A filter change clears
// the selection (a set that no longer means what it meant is how "delete what I
// ticked" deletes something invisible); un-ticking one tile out of "everything
// in the filter" collapses to an explicit list, because the URL form cannot
// express "all except this"; and shift+click is ADDITIVE, unlike a file
// manager, because the flow is "sweep the bad run, then untick the two that
// came out fine".

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import { ApiError } from "../../../../api";
import {
  backfillThumbs, getTrashCount, listFrames, listNights, trashFrames,
} from "../../../../api/gallery";
import { u } from "../../../../lib/base";
import { accessPhrase, useCan, useCanControlCapture } from "../../../../lib/caps";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import FrameTile from "../../../../components/gallery/FrameTile";
import FrameViewer from "../../../../components/gallery/FrameViewer";
import TrashPanel from "../../../../components/gallery/TrashPanel";
import SessionStack from "../../../../components/preview/SessionStack";
import {
  TRASH_BATCH_CAP, downloadPlan, fmtBytes, fmtCount, fmtFrameCost,
  nightOptionLabel, nightRangeLabel, partialFailureNote, pickedTotals, scanNote,
  selectRange, togglePath, tonightHasFrames, trashBatchReason, trashConfirmCopy,
  truncatedNote, type GallerySelection,
} from "../../../../lib/gallery";
import { useStatus, useStore } from "../../../../store";
import type {
  GalleryFrame, GalleryFramesPage, GalleryNightsResponse,
} from "../../../../types";
import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import {
  ActionButton, Card, EmptyCard, Label, Mono, Sheet, SubNav, TextInput,
} from "../../../ui";
import type { SheetProps } from "../../sheets";

/** Rows per request. The server clamps at 500; 200 keeps the JSON small enough
 *  that a phone on a hotspot gets its first screenful quickly. */
const PAGE = 200;
/** How long after the last keystroke the search actually runs. Every request is
 *  a library walk on the far end. */
const SEARCH_DEBOUNCE_MS = 300;

export const ARCHIVE_PHONE_REASON = "The full frame library opens on a tablet or desktop.";

type Tab = "frames" | "trash" | "stack";

export function ArchiveSheet({ params }: SheetProps): JSX.Element {
  const bp = useBreakpoint();
  const canBrowse = useCan("view.preview");
  const canMedia = useCan("view.media");
  const canDelete = useCanControlCapture();
  const status = useStatus();

  const initial: Tab = params.tab === "trash" || params.tab === "stack"
    ? (params.tab as Tab) : "frames";
  const [tab, setTab] = useState<Tab>(initial);

  const [nights, setNights] = useState<GalleryNightsResponse | null>(null);
  const [trashCount, setTrashCount] = useState<number | null>(null);
  const [gen, setGen] = useState(0);
  const refresh = useCallback(() => setGen((g) => g + 1), []);

  useEffect(() => {
    if (!canBrowse) return;
    let alive = true;
    listNights().then((n) => { if (alive) setNights(n); }).catch(() => { if (alive) setNights(null); });
    return () => { alive = false; };
  }, [gen, canBrowse]);

  useEffect(() => {
    if (!canDelete) return;
    let alive = true;
    getTrashCount()
      .then((t) => { if (alive) setTrashCount(t.count); })
      .catch(() => { if (alive) setTrashCount(null); });
    return () => { alive = false; };
  }, [gen, canDelete]);

  const bytes = (nights?.nights ?? []).reduce((a, n) => a + n.bytes, 0);
  const frames = (nights?.nights ?? []).reduce((a, n) => a + n.frames, 0);
  const diskLine = `${fmtBytes(bytes)} in ${fmtCount(frames)} frames`
    + (nights?.truncated ? " (partial)" : "")
    + (status?.disk?.free_gb != null ? ` · ${status.disk.free_gb.toFixed(1)} GB free` : "");

  const items = [
    { id: "frames", label: "FRAMES" },
    ...(canDelete ? [{ id: "trash", label: "TRASH", count: trashCount ?? undefined }] : []),
    { id: "stack", label: "STACK" },
  ];

  return (
    <Sheet
      data-testid="session-archive"
      title="FRAME LIBRARY"
      sub={diskLine}
      icon={<NxIcon name="layers" size={18} />}
      onBack={() => nav.back()}
    >
      {bp === "phone" ? (
        <EmptyCard
          title="FULL LIBRARY"
          hint={ARCHIVE_PHONE_REASON}
        />
      ) : !canBrowse ? (
        <EmptyCard
          title="LIBRARY NOT AVAILABLE TO THIS ACCOUNT"
          hint={`Listing frames and thumbnails needs ${accessPhrase("view.preview")}.`}
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <SubNav items={items} value={tab} onChange={(id) => setTab(id as Tab)} ariaLabel="Library section" />
          {tab === "frames" && (
            <FramesTab
              nights={nights}
              canMedia={canMedia}
              canDelete={canDelete}
              gen={gen}
              refresh={refresh}
            />
          )}
          {tab === "trash" && canDelete && <TrashPanel onChanged={refresh} />}
          {tab === "stack" && <SessionStack />}
        </div>
      )}
    </Sheet>
  );
}

// ================================================================ browse tab

function FramesTab({ nights, canMedia, canDelete, gen, refresh }: {
  nights: GalleryNightsResponse | null;
  canMedia: boolean;
  canDelete: boolean;
  gen: number;
  refresh: () => void;
}): JSX.Element {
  const enqueueToast = useStore((s) => s.enqueueToast);

  const [qDraft, setQDraft] = useState("");
  const [q, setQ] = useState("");
  const [nightFrom, setNightFrom] = useState("");
  const [nightTo, setNightTo] = useState("");
  const [page, setPage] = useState<GalleryFramesPage | null>(null);
  const [rows, setRows] = useState<GalleryFrame[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [viewing, setViewing] = useState<GalleryFrame | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [allInFilter, setAllInFilter] = useState(false);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [warming, setWarming] = useState(false);
  const [trashTtl, setTrashTtl] = useState(30);
  const listReq = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setQ(qDraft.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qDraft]);

  useEffect(() => {
    if (!canDelete) return;
    let alive = true;
    getTrashCount().then((t) => { if (alive) setTrashTtl(t.ttl_days); }).catch(() => { /* default 30 */ });
    return () => { alive = false; };
  }, [canDelete]);

  useEffect(() => {
    let alive = true;
    listReq.current += 1;
    setLoading(true);
    setErr(null);
    // A changed filter is a changed set, so both selections stop meaning what
    // they meant.
    setPicked(new Set());
    setAllInFilter(false);
    listFrames({ q, nightFrom, nightTo, offset: 0, limit: PAGE })
      .then((p) => { if (!alive) return; setPage(p); setRows(p.frames); })
      .catch((e) => {
        if (!alive) return;
        setPage(null);
        setRows([]);
        setErr(e instanceof ApiError ? e.message : "could not read the capture library");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [q, nightFrom, nightTo, gen]);

  const nightList = nights?.nights ?? [];
  const tonight = nights?.current ?? "";
  const tonightLive = tonightHasFrames(tonight, nightList);
  const cold = page ? scanNote(page.scan_ms, page.total) : null;

  const selection: GallerySelection = useMemo(
    () => (allInFilter
      ? { mode: "filter", q, nightFrom, nightTo }
      : { mode: "picked", paths: [...picked] }),
    [allInFilter, q, nightFrom, nightTo, picked],
  );
  const selCount = allInFilter ? (page?.total ?? 0) : picked.size;
  const selBytes = allInFilter ? (page?.bytes ?? 0) : pickedTotals(picked, rows).bytes;
  const plan = downloadPlan(selection, selCount);

  const downloadReason = !canMedia
    ? `Downloading raw FITS needs ${accessPhrase("view.media")} - the files embed the observatory's coordinates.`
    : plan.ok ? null : plan.reason;
  const deleteReason = !canDelete
    ? `Deleting needs ${accessPhrase("control.capture")}.`
    : busy ? "Another gallery action is still running."
      : selCount <= 0 ? "Nothing is selected."
        : trashBatchReason(selCount);

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
          r.truncated ? "the library was longer than one pass - run it again" : "",
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
  }, [warming, refresh, enqueueToast]);

  /** The paths a delete will actually submit. "Everything in the filter" is
   *  enumerated by paging the listing: deleting "the filter" by handing the
   *  server a filter would be a different contract than the one the
   *  confirmation just described. */
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
        title: copy.title, body: copy.body, tone: "warn",
        mode: "confirm", confirmLabel: "Move to trash",
      });
      if (!go) return;
      const r = await trashFrames(paths);
      enqueueToast({
        level: r.failed.length ? "warning" : "success",
        title: `Moved ${fmtCount(r.trashed.length)} ${r.trashed.length === 1 ? "frame" : "frames"} to the trash`,
        detail: partialFailureNote(r.trashed.length, r.failed)
          ?? `${fmtBytes(r.bytes)} recoverable for ${trashTtl} days - the space is not freed until it purges.`,
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

  return (
    <>
      <Card data-testid="archive-frames">
        <Label>CAPTURE LIBRARY</Label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "flex-end", marginTop: 8 }}>
          <div style={{ flex: "1 1 220px", minWidth: 0 }}>
            <TextInput
              ariaLabel="Search the capture library"
              value={qDraft}
              onChange={setQDraft}
              placeholder="target, filter, filename"
              data-testid="archive-search"
            />
          </div>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <Label>FROM NIGHT</Label>
            <select
              className="nx-input"
              aria-label="From night"
              value={nightFrom}
              onChange={(e) => setNightFrom(e.target.value)}
            >
              <option value="">Any night</option>
              {nightList.map((n) => (
                <option key={n.night} value={n.night}>{nightOptionLabel(n)}</option>
              ))}
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <Label>TO NIGHT</Label>
            <select
              className="nx-input"
              aria-label="To night"
              value={nightTo}
              onChange={(e) => setNightTo(e.target.value)}
            >
              <option value="">Any night</option>
              {nightList.map((n) => (
                <option key={n.night} value={n.night}>{nightOptionLabel(n)}</option>
              ))}
            </select>
          </label>
        </div>

        {/* The two facts the filter bar cannot show. GalleryView carried them in
            InfoDots; the new language has no such primitive, and the night/
            filename disagreement is the single most-reported "the filter is
            broken" - so both are printed rather than dropped. */}
        <Mono size={10} tone="dim">
          Search matches anywhere in the frame&apos;s path, folder included - so a target
          name finds that target&apos;s frames, and a filter letter finds every frame shot
          through it.
        </Mono>
        <Mono size={10} tone="dim">
          A night runs noon to noon, so everything from one session lands under one key
          even across midnight. Filenames carry the calendar date instead, which is why a
          00:10 frame&apos;s name and its night disagree.
        </Mono>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
          <ActionButton
            kind="ghost"
            lockedReason={tonightLive ? null : `Nothing has been captured yet on tonight's night (${tonight || "unknown"}).`}
            onExplain={explainLock}
            onPress={() => { setNightFrom(tonight); setNightTo(tonight); }}
          >
            TONIGHT
          </ActionButton>
          <ActionButton
            kind="ghost"
            lockedReason={!q && !nightFrom && !nightTo ? "No filter is set." : null}
            onExplain={explainLock}
            onPress={() => { setQDraft(""); setQ(""); setNightFrom(""); setNightTo(""); }}
          >
            CLEAR FILTER
          </ActionButton>
          <ActionButton kind="ghost" onPress={refresh}>REFRESH</ActionButton>
          <ActionButton
            kind="ghost"
            busy={warming}
            lockedReason={warming ? "Already rebuilding previews." : null}
            onExplain={explainLock}
            onPress={() => void rebuildPreviews()}
          >
            {warming ? "REBUILDING PREVIEWS" : "REBUILD PREVIEWS"}
          </ActionButton>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 8 }}>
          <Mono size={10.5}>{nightRangeLabel(nightFrom, nightTo)}</Mono>
          <Mono size={10.5} tone="dim">
            {loading ? "reading…" : page ? fmtFrameCost(page.total, page.bytes) : "-"}
            {page && rows.length < page.total && !loading ? ` · showing ${fmtCount(rows.length)}` : ""}
          </Mono>
          {nights?.truncated && <Mono size={10.5} tone="warn">night index is partial</Mono>}
        </div>
        {page?.truncated && <Mono size={10} tone="warn">{truncatedNote(page.total)}</Mono>}
        {cold && <Mono size={10} tone="dim">{cold}</Mono>}
        {err && <Mono size={10} tone="bad">{err}</Mono>}
      </Card>

      {/* ------------------------------------------------------------ actions */}
      <Card>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <ActionButton
            kind="ghost"
            lockedReason={(page?.total ?? 0) === 0 ? "The filter matched nothing." : null}
            onExplain={explainLock}
            onPress={() => { setAllInFilter(true); setPicked(new Set()); }}
          >
            {page ? `SELECT ALL ${fmtCount(page.total)} IN FILTER` : "SELECT ALL IN FILTER"}
          </ActionButton>
          <ActionButton
            kind="ghost"
            lockedReason={selCount === 0 ? "Nothing is selected." : null}
            onExplain={explainLock}
            onPress={() => { setAllInFilter(false); setPicked(new Set()); }}
          >
            CLEAR SELECTION
          </ActionButton>
          <span style={{ flex: 1 }} />
          {downloadReason ? (
            <ActionButton
              kind="primary"
              data-testid="archive-download"
              lockedReason={downloadReason}
              onExplain={explainLock}
              onPress={() => { /* locked */ }}
            >
              DOWNLOAD
            </ActionButton>
          ) : (
            <a className="nx-btn" data-kind="primary" data-testid="archive-download"
              href={u(plan.ok ? plan.href : "")} download>
              <span className="nx-btn-label">{`DOWNLOAD ${fmtFrameCost(selCount, selBytes)}`}</span>
            </a>
          )}
          <ActionButton
            kind="danger"
            data-testid="archive-trash"
            lockedReason={deleteReason}
            onExplain={explainLock}
            onPress={() => void onDelete()}
          >
            MOVE TO TRASH
          </ActionButton>
        </div>
        <Mono size={10} tone="dim">
          {allInFilter
            ? `Everything the filter matches is selected - ${fmtFrameCost(selCount, selBytes)}, including the ${fmtCount(Math.max(0, (page?.total ?? 0) - rows.length))} not yet shown below.`
            : picked.size > 0
              ? `${fmtFrameCost(picked.size, selBytes)} ticked.`
              : "Tick frames to act on some, or use Select all to take the whole filter."}
        </Mono>
        {!canMedia && (
          <Mono size={10} tone="warn">
            {`Raw FITS downloads need ${accessPhrase("view.media")} - every frame embeds the observatory's coordinates. Thumbnails are not restricted.`}
          </Mono>
        )}
      </Card>

      {/* --------------------------------------------------------------- grid */}
      {loading && rows.length === 0 ? (
        <Card><Mono size={10.5} tone="dim">Reading the capture library…</Mono></Card>
      ) : rows.length === 0 ? (
        <EmptyCard
          title={q || nightFrom || nightTo ? "NOTHING MATCHES THIS FILTER" : "NO FRAMES ON DISK YET"}
          hint={q || nightFrom || nightTo
            ? "Search matches the whole path, and night bounds are inclusive noon-to-noon keys - a session that ran past midnight is filed under the night it started."
            : "Frames appear here as soon as a capture or a sequence writes one."}
        />
      ) : (
        <Card padding={12}>
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>
            {rows.map((f) => (
              <FrameTile
                key={f.path}
                frame={f}
                selected={allInFilter || picked.has(f.path)}
                selectable={canDelete || canMedia}
                canDownload={canMedia}
                onOpen={canMedia ? () => setViewing(f) : undefined}
                onToggle={(shift) => {
                  if (allInFilter) {
                    // "all except this" is not expressible in the URL form, so
                    // it collapses to the explicit list of what is loaded.
                    setAllInFilter(false);
                    setPicked(new Set(rows.map((r) => r.path).filter((p) => p !== f.path)));
                    setAnchor(f.path);
                    return;
                  }
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
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12, marginTop: 12 }}>
              <ActionButton
                kind="ghost"
                lockedReason={loadingMore || loading ? "Still reading the last page." : null}
                onExplain={explainLock}
                onPress={() => void onLoadMore()}
              >
                {loadingMore || loading
                  ? "LOADING"
                  : `LOAD ${fmtCount(Math.min(PAGE, page.total - rows.length))} MORE`}
              </ActionButton>
              <Mono size={10} tone="dim">{`${fmtCount(rows.length)} of ${fmtCount(page.total)} shown`}</Mono>
            </div>
          )}
        </Card>
      )}

      {viewing && <FrameViewer frame={viewing} onClose={() => setViewing(null)} />}
    </>
  );
}

export default ArchiveSheet;
