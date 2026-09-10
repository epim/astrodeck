// files.tsx - the FILES sheet: what tonight (or a past session, or a hand shot)
// actually produced, and how to get it off the rig.
//
// Reached from anywhere as `#/<hub>/<sub>/files?src=<current|sessionId|manual>`
// (plan section B; proto `11-files-download.html`, screenshot 11).
//
// THE THREE THINGS THIS SCREEN HAS TO GET RIGHT
//
// 1. THE NUMBERS ARE TWO ANSWERS, NOT ONE. Counts, grades and integration come
//    from the session LEDGER (`GET /api/sessions/{id}/files`, Wave S5, with the
//    ledger folded client-side when that route is not on the rig yet). Paths
//    and bytes come from the LIBRARY (`GET /api/gallery/frames`), because the
//    zip contains files on disk, not ledger rows. `filesData.foldRows` marries
//    them and never invents the half it does not have.
//
// 2. ONE ZIP, NEVER N LINKS. iOS downloads one file at a time in the
//    foreground (README "Platform"), so a ticked set is served as a single
//    `download.zip` navigation - a plain `<a href download>`, never a fetch
//    into a Blob (the archive is routinely tens of GB). A hand-picked subset
//    rides in the URL, so `lib/gallery.downloadPlan` refuses it past
//    `PICKED_URL_BUDGET` rather than truncating, and this sheet prints the
//    refusal and offers the whole-filter escape.
//
// 3. NOTHING CLAIMS A CAPABILITY IT DOES NOT HAVE. Raw FITS are `view.media`
//    (syncer or admin), so an operator - the person who ran the night - sees
//    the FITS option honest-disabled with the reason, and the JPEG path still
//    works. Regrading is refused outright by the server while the session is
//    running, so this says so BEFORE the tap instead of discovering it in a
//    409.

import { useCallback, useEffect, useMemo, useState, type CSSProperties, type JSX } from "react";

import { ApiError } from "../../../../api";
import { listFrames, listNights } from "../../../../api/gallery";
import { getSession, listSessions, patchFrame } from "../../../../api/sessions";
import { getBundlePreview, listReports, materializeBundle } from "../../../../api/reports";
import { getRemoteStatus } from "../../../../api/backends";
import {
  fmtIntegration, getSessionStack, sessionStackImageUrl,
  type SessionStackStatus,
} from "../../../../api/sessionStack";
import { u } from "../../../../lib/base";
import { accessPhrase, useCan } from "../../../../lib/caps";
import {
  bundleQuery, keptSummary, layoutOptions, masterChips,
  materializeDisabledReason, type BundleLayout,
} from "../../../../lib/bundleView";
import { downloadPlan, fmtBytes, fmtCount, selectionQuery } from "../../../../lib/gallery";
import { fmtDuration } from "../../../../lib/eta";
import { filterFrames, pruneSelection, toggleSel, verdictOf } from "../../../../lib/sessionReview";
import { targetProgress } from "../../../../lib/sessions";
import { sessionDates } from "../../../../components/sequence/sessionDates";
import { useSeq, useStore } from "../../../../store";
import type {
  BundlePreview, GalleryFrame, RemoteStatus, SequenceState, Session, SessionFrame,
  SessionReportSummary, SessionRow,
} from "../../../../types";
import { buildHash, currentRoute, nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import {
  ActionButton, Bar, Card, Checkbox22, Chip, EmptyCard, Label, Mono, Pill,
  Segmented, Sheet, Switch, TextInput,
} from "../../../ui";
import type { SheetProps } from "../../sheets";
import {
  buildSelection, fetchSessionFiles, filterLabel, foldRows, indexFromSession,
  noteDownloaded, noteThroughput, readDlPref, readThroughput, rowSubtitle,
  sampleFromResource, selectionCost, tickableRows, totalsOf, transferNote,
  writeDlPref, type DlPref, type FilesRow, type SessionFilesFrame,
  type SessionFilesIndex,
} from "./filesData";

/** One library page is enough to price and pick a night: 500 is the server's
 *  own clamp, and a session that produced more than that is one whose whole
 *  filter is being downloaded anyway (the FILTER selection needs no paths). */
const LIB_LIMIT = 500;

/** The blurb under the format picker, verbatim from the prototype. */
const FORMAT_BLURB =
  "Files come straight from the rig over its Wi-Fi hotspot, no cloud in between. " +
  "FITS keep the full 16-bit data and headers for stacking on a desktop later; " +
  "JPEG previews are for a quick look and sharing.";

/** The bundle blurb, verbatim (inventory-session-monitor.md section 5.3). */
const BUNDLE_BLURB =
  "One .zip with tonight's photos already sorted into folders your stacking " +
  "software understands - PixInsight, Siril or APP - together with the " +
  "matching calibration frames and a quality score for each photo.";

/** The materialize danger note, verbatim (ReportView.tsx). */
const MATERIALIZE_DANGER =
  "The photos in the exports folder are the SAME files as your originals, not " +
  "copies - deleting or editing one there deletes or edits your original " +
  "capture. Stack from this folder; don't tidy up inside it.";

const EMPTY_NOTE =
  "The list fills as subs land; each one is downloadable the moment its HFR check passes.";

/** A checkbox whose label is a picture still needs a NAME. `display: none`
 *  would take it out of the accessibility tree with the pixels; this keeps it
 *  readable to a screen reader and invisible to everyone else. */
const SR_ONLY: CSSProperties = {
  position: "absolute", width: 1, height: 1, overflow: "hidden",
  clip: "rect(0 0 0 0)", whiteSpace: "nowrap",
};

// ============================================================ small helpers

/** What the engine is doing, in one word, for the "nothing banked yet" line. */
function phaseWord(seq: SequenceState): string {
  if (seq.state !== "running" && seq.state !== "holding") return seq.state;
  const d = (seq.detail ?? "").toLowerCase();
  if (seq.state === "holding") return "cloud hold";
  if (d.startsWith("slewing") || d.startsWith("re-centring")) return "slewing";
  if (d.includes("autofocus") || d.includes("focus")) return "focus";
  if (d.includes("solving")) return "solving";
  if (d.includes("guid")) return "guiding";
  if (d.startsWith("cooling")) return "cooling";
  return "capturing";
}

/** The legacy selection copy, transcribed rather than imported: LoupePanel's
 *  `legacyCopy` is module-private, and `navigator.clipboard` is undefined over
 *  plain http, which is AstroDeck's normal deployment. */
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0;";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** An S5/ledger frame wearing the shape `lib/sessionReview` grades. `accepted`
 *  is already the EFFECTIVE verdict, and `verdictOf`/`filterFrames` read
 *  `auto_accepted` only when `override` is null - where the two agree - so the
 *  adapter is exact rather than approximate. */
function asSessionFrame(f: SessionFilesFrame): SessionFrame {
  return {
    id: f.id, ts: f.ts, night: "", target_id: "", step_id: "",
    thumb: f.thumb, metrics: {}, auto_accepted: f.accepted, override: f.override,
  };
}

const VERDICTS = [
  { value: "all", label: "ALL" },
  { value: "accepted", label: "KEPT" },
  { value: "rejected", label: "REJECTED" },
  { value: "overridden", label: "CHANGED" },
] as const;
type VerdictPick = (typeof VERDICTS)[number]["value"];

function toast(level: "info" | "success" | "warning" | "error", title: string, detail?: string): void {
  useStore.getState().enqueueToast({ level, title, detail });
}

/** SWITCH the source, never stack a second copy of this sheet on top of itself.
 *
 *  `nav.sheet("files", ...)` called from INSIDE the files sheet pushes a second
 *  "files" onto `route.sheets`, and `SheetHost` shares one `params` object
 *  across the whole stack - so the sheet underneath re-renders as the identical
 *  screen and BACK reads as a control that did nothing. Changing the source is
 *  a sub-nav change, which `nav.replace` is for. */
function goSource(src: string): void {
  const r = currentRoute();
  nav.replace(buildHash({ ...r, params: { ...r.params, src } }));
}

// ================================================================== the sheet

export function FilesSheet({ params }: SheetProps): JSX.Element {
  const seq = useSeq();
  const bp = useBreakpoint();
  const canMedia = useCan("view.media");
  // The JPEG half of the format choice. `/api/sessions/{id}/frames/{fid}/thumb`
  // is `CAP_VIEW_PREVIEW` on the server (`app.py:5000`), so the per-frame save
  // is offered to every role the route would actually serve - which is the
  // point of having a JPEG option at all.
  const canPreview = useCan("view.preview");
  const canRegrade = useCan("control.mount");
  const canCapture = useCan("control.capture");

  const src = params.src || "current";
  const manual = src === "manual";

  // ---------------------------------------------------------------- sources
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [index, setIndex] = useState<SessionFilesIndex | null>(null);
  const [gradeNote, setGradeNote] = useState<string | null>(null);
  const [lib, setLib] = useState<{ frames: GalleryFrame[]; total: number; bytes: number } | null>(null);
  const [libErr, setLibErr] = useState<string | null>(null);
  const [nightKey, setNightKey] = useState("");
  const [stack, setStack] = useState<SessionStackStatus | null>(null);
  const [remote, setRemote] = useState<RemoteStatus | null>(null);
  const [reports, setReports] = useState<SessionReportSummary[] | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    listSessions().then((r) => { if (alive) setSessions(r); }).catch(() => { if (alive) setSessions([]); });
    listNights().then((n) => { if (alive) setNightKey(n.current); }).catch(() => { /* tonight's key is a nicety */ });
    listReports().then((r) => { if (alive) setReports(r); }).catch(() => { if (alive) setReports([]); });
    getRemoteStatus().then((r) => { if (alive) setRemote(r); }).catch(() => { /* Wave S3 may be absent */ });
    return () => { alive = false; };
  }, []);

  /** Which session this sheet is about. `current` prefers the engine's own
   *  session id, because that is the run the user is watching; the newest
   *  ACTIVE row is the fallback for a page opened after a reload. */
  const sessionId = useMemo(() => {
    if (manual) return null;
    if (src !== "current") return src;
    if (seq.session?.id) return seq.session.id;
    const active = (sessions ?? []).filter((r) => r.status === "active")
      .sort((a, b) => b.updated_ts - a.updated_ts)[0];
    return active?.id ?? null;
  }, [manual, src, seq.session?.id, sessions]);

  const row = useMemo(
    () => (sessions ?? []).find((r) => r.id === sessionId) ?? null,
    [sessions, sessionId],
  );

  // ---- the ledger half ----------------------------------------------------
  useEffect(() => {
    if (!sessionId) { setSession(null); setIndex(null); setGradeNote(null); return; }
    let alive = true;
    setIndex(null);
    setGradeNote(null);
    (async () => {
      // The ledger is loaded either way: it carries the night range the library
      // listing needs and the plan the row order follows.
      let s: Session | null = null;
      try {
        s = await getSession(sessionId);
      } catch (e) {
        if (!alive) return;
        setSession(null);
        setGradeNote(`The session ledger could not be read: ${e instanceof Error ? e.message : "unknown error"}.`);
      }
      if (!alive) return;
      setSession(s);
      // Wave S5 first; a rig without it answers 404 and the ledger fold above
      // is the same answer minus the file sizes.
      const probe = await fetchSessionFiles(src === "current" && seq.session?.id === sessionId ? "current" : sessionId);
      if (!alive) return;
      if (probe.state === "ok") { setIndex(probe.index); return; }
      if (probe.state === "error") setGradeNote(`Frame grades could not be read: ${probe.message}.`);
      setIndex(s ? indexFromSession(s) : null);
    })();
    return () => { alive = false; };
  }, [sessionId, src, seq.session?.id]);

  // ---- the library half ---------------------------------------------------
  const scope = useMemo(() => {
    if (manual) return { q: "", nightFrom: "", nightTo: "" };
    if (session) {
      const nights = session.nights ?? [];
      const single = session.plan.targets.length === 1 ? session.plan.targets[0].name : "";
      return {
        q: single,
        nightFrom: nights[0] ?? "",
        nightTo: nights[nights.length - 1] ?? "",
      };
    }
    return { q: seq.target ?? "", nightFrom: nightKey, nightTo: nightKey };
  }, [manual, session, seq.target, nightKey]);

  useEffect(() => {
    let alive = true;
    setLibErr(null);
    listFrames({ q: scope.q, nightFrom: scope.nightFrom, nightTo: scope.nightTo, limit: LIB_LIMIT })
      .then((p) => {
        if (!alive) return;
        const frames = manual ? p.frames.filter((f) => f.frame_type === "Light") : p.frames;
        setLib({ frames, total: p.total, bytes: p.bytes });
      })
      .catch((e) => {
        if (!alive) return;
        setLib(null);
        setLibErr(e instanceof ApiError ? e.message : "could not read the capture library");
      });
    return () => { alive = false; };
  }, [scope.q, scope.nightFrom, scope.nightTo, manual]);

  // ---- the live stack -----------------------------------------------------
  useEffect(() => {
    if (src !== "current") return;
    let alive = true;
    getSessionStack().then((s) => { if (alive) setStack(s); }).catch(() => { /* stack may be off */ });
    return () => { alive = false; };
  }, [src]);

  // ------------------------------------------------------------------- rows
  const rows = useMemo(() => foldRows(lib?.frames ?? [], index), [lib, index]);
  const usable = useMemo(() => tickableRows(rows), [rows]);
  const totals = useMemo(() => totalsOf(rows), [rows]);

  const [ticked, setTicked] = useState<Set<string> | null>(null);
  // All rows pre-checked (proto), and re-seeded whenever the row set itself
  // changes - a tick set left over from another session is a selection about
  // frames the user is no longer looking at.
  useEffect(() => {
    setTicked(new Set(usable.map((r) => r.filter)));
  }, [usable]);
  const tick = ticked ?? new Set<string>();

  const [expanded, setExpanded] = useState<string | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [verdict, setVerdict] = useState<VerdictPick>("all");
  const [regrading, setRegrading] = useState(false);

  // Collapsing one filter and opening another must drop the selection with it.
  // A frame ticked under L and then bulk-regraded from the Ha list is a change
  // nobody watched happen - the same defect `pruneSelection` closes inside one
  // list, one level up.
  useEffect(() => { setSel([]); }, [expanded]);

  // ------------------------------------------------------------- throughput
  const [mbps, setMbps] = useState<number | null>(() => readThroughput(null)?.mbps ?? null);
  useEffect(() => {
    setMbps(readThroughput(remote?.via ?? null)?.mbps ?? null);
  }, [remote?.via]);

  const stackUrl = stack && stack.has_image ? sessionStackImageUrl(stack.seq, 1200) : null;

  /** The newest KEPT frame that has a thumbnail. A past session has no live
   *  composite (the stacker is tonight's run only), so this is the picture. */
  const pastThumb = useMemo(() => {
    if (!index) return null;
    let best: SessionFilesFrame | null = null;
    for (const g of index.by_filter) {
      for (const f of g.frames) {
        if (!f.thumb || !f.accepted) continue;
        if (!best || f.ts > best.ts) best = f;
      }
    }
    return best?.thumb ?? null;
  }, [index]);

  const heroUrl = src === "current"
    ? (stackUrl ?? (pastThumb ? u(pastThumb) : null))
    : (pastThumb ? u(pastThumb) : null);

  const onHeroLoaded = useCallback(() => {
    if (!heroUrl) return;
    // A real measurement off bytes the page already fetched - never a fixture
    // rate. A thumbnail is too small to time, and `sampleFromResource` refuses
    // anything under its floor rather than reporting an absurd speed.
    const rate = sampleFromResource(heroUrl);
    if (rate == null) return;
    noteThroughput(rate, remote?.via ?? "unknown");
    setMbps(readThroughput(remote?.via ?? null)?.mbps ?? rate);
  }, [heroUrl, remote?.via]);

  // ---------------------------------------------------------- format picker
  const [dlPref, setDlPref] = useState<DlPref>(() => readDlPref());
  const setPref = (p: DlPref) => { setDlPref(p); writeDlPref(p); };
  const fitsReason = canMedia ? null : `FITS originals need ${accessPhrase("view.media")}.`;
  // Without `view.media` there is no FITS path to choose, so the picker is
  // honest-disabled ON the JPEG option rather than left live over a choice the
  // server would refuse. `Segmented` locks as one control - it is one choice -
  // and the reason line under it names the capability.
  const pref: DlPref = canMedia ? dlPref : "jpg";

  // ------------------------------------------------------------- the header
  const target = index?.target
    || (session ? (session.plan.targets[0]?.name || session.name) : "")
    || seq.target
    || (manual ? "MANUAL SHOTS" : "SESSION");
  const live = row?.status === "active" || (src === "current" && !row);
  const dateWord = row ? sessionDates(row.created_ts, row.updated_ts).toUpperCase() : "";
  const title = manual
    ? "MANUAL SHOTS"
    : live ? `${target} · TONIGHT` : `${target} · ${dateWord || "EARLIER"}`;

  const headerSub = manual
    ? "every light frame on the rig - the library index does not record which were shot by hand"
    : totals.subs > 0
      ? `${totals.subs} subs · ${fmtIntegration(totals.integrationS)} · ${fmtBytes(totals.bytes)} on the rig`
      : live
        ? `${phaseWord(seq)} · nothing banked yet`
        : "nothing on the rig for this target";

  // ------------------------------------------------------------- the picker
  const srcValue: "current" | "session" | "manual" =
    manual ? "manual" : src === "current" ? "current" : "session";

  const pickSource = (v: "current" | "session" | "manual") => {
    if (v === "current") { goSource("current"); return; }
    if (v === "manual") { goSource("manual"); return; }
    setPickerOpen(true);
  };

  // -------------------------------------------------------------- selection
  const selection = useMemo(
    () => buildSelection(rows, tick, scope, manual),
    [rows, tick, scope, manual],
  );
  const cost = useMemo(() => selectionCost(rows, tick), [rows, tick]);
  const plan = downloadPlan(selection, cost.count);

  /** A picked selection with nothing to pick FROM. The download route reads an
   *  empty `path` list as "no path filter at all" and would stream the whole
   *  library, so this is refused rather than sent. */
  const emptyPick = selection.mode === "picked" && selection.paths.length === 0 && cost.count > 0;

  const dlLabel = (() => {
    if (totals.subs === 0) {
      if (!live) return "NO SUBS ON THE RIG";
      const etaS = firstSubEta(seq, session);
      return etaS != null
        ? `NO SUBS YET · FIRST ONE IN ~${fmtDuration(etaS)}`
        : "NO SUBS YET";
    }
    if (cost.count === 0) return "PICK AT LEAST ONE FILTER";
    return `DOWNLOAD ${fmtCount(cost.count)} SUBS · ${fmtBytes(cost.bytes)}`;
  })();

  // THE JPEG SENTENCE COMES FIRST, and it has to, because `pref` is FORCED to
  // "jpg" for anyone without `view.media`. Testing the capability ahead of the
  // format printed "Downloading raw frames needs ..." to exactly the roles that
  // can never choose FITS, and buried the one sentence that says where their
  // pictures actually are. The capability is still named - once, in its own
  // paragraph under the picker (`files-fits-locked`) and again here when it is
  // the REASON the format was chosen for them.
  const jpegReason = pref !== "jpg" ? null
    : canMedia
      ? "The rig serves JPEG previews one frame at a time - open a filter and use SAVE JPG on the frames you want, or save the stack above."
      : `Raw frames need ${accessPhrase("view.media")}, so this hands you JPEG previews instead`
        + " - open a filter and use SAVE JPG on the frames you want, or save the stack above.";
  const dlReason = jpegReason
    ?? (totals.subs === 0 ? "Nothing has been banked for this source yet."
      : cost.count === 0 ? "Tick at least one filter."
        : emptyPick
          ? "The library index has no files for these filters, so a partial pick cannot be sent. Tick every filter to send the whole night instead."
          : plan.ok ? null : plan.reason);

  const note = useMemo(() => {
    if (totals.subs === 0) return { line: EMPTY_NOTE, extra: null };
    // No zip is going to be fetched on the JPEG path, so pricing one - "12.4 GB
    // to transfer, lands in Files > AstroDeck > ..." - is a claim about
    // something that cannot happen, printed under a locked button.
    if (pref === "jpg") {
      return {
        line: "JPEG previews are served one frame at a time, so there is no zip to weigh.",
        extra: "Each one is the rig's own rendered preview (about 512 px on the long edge), "
          + "not the 16-bit original.",
      };
    }
    if (cost.count === 0) {
      return { line: "Uncheck what you already have; the rig keeps everything until you clear it.", extra: null };
    }
    return transferNote({
      bytes: cost.bytes,
      via: remote?.via ?? null,
      mbps,
      target,
      date: scope.nightTo || scope.nightFrom || nightKey,
    });
  }, [totals.subs, pref, cost.count, cost.bytes, remote?.via, mbps, target, scope.nightTo, scope.nightFrom, nightKey]);

  const href = plan.ok ? u(plan.href) : u(`/api/gallery/download.zip${selectionQuery(selection)}`);

  const onDownload = () => {
    if (sessionId) noteDownloaded(sessionId, cost.count);
  };

  // ---------------------------------------------------------------- regrade
  const regradeReason = !canRegrade
    ? `Regrading frames needs ${accessPhrase("control.mount")}.`
    : row?.status === "active"
      // The server refuses this outright ("regrade is explicitly a
      // between-nights operation"), so say why before the tap rather than
      // discovering it in a 409.
      ? "Session is running - regrades are read-only until it finishes"
      : regrading
        ? "Applying the last regrade…"
        : sel.length === 0
          ? "Select one or more frames first."
          : null;

  const bulk = async (override: "accept" | "reject") => {
    if (regradeReason || !sessionId) return;
    setRegrading(true);
    let applied = 0;
    try {
      for (const fid of sel) {
        await patchFrame(sessionId, fid, { override });
        applied++;
      }
      setIndex((cur) => applyOverride(cur, sel, override));
      toast("success", `${applied} frame${applied === 1 ? "" : "s"} marked ${override === "accept" ? "accepted" : "rejected"}`);
    } catch (e) {
      const running = e instanceof ApiError && e.status === 409;
      toast("error", running
        ? "Session is running - regrades are read-only until it finishes"
        : `Regrade failed: ${e instanceof Error ? e.message : "unknown error"}`);
      if (applied) setIndex((cur) => applyOverride(cur, sel.slice(0, applied), override));
    } finally {
      setSel([]);
      setRegrading(false);
    }
  };

  // ----------------------------------------------------------------- render
  // The badge describes the PICTURE above it. When that picture is the live
  // composite, the stacker's own counts are the truth about what went into it
  // (it refuses subs the grader kept - no stars, drifted off field); when it is
  // a past session's thumbnail there is no composite, so the ledger answers.
  const showingComposite = heroUrl != null && heroUrl === stackUrl;
  const stackFrames = showingComposite && stack ? stack.frames : (index?.totals.frames ?? totals.subs);
  const stackIntegration = showingComposite && stack
    ? stack.integrated_s
    : (index?.totals.integration_s ?? totals.integrationS);

  return (
    <Sheet
      data-testid="session-files"
      title={`FILES · ${title}`}
      sub={headerSub}
      icon={<NxIcon name="download" size={18} />}
      onBack={() => nav.back()}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {/* ------------------------------------------------------- source */}
        <Segmented
          data-testid="files-source"
          label="Which files"
          value={srcValue}
          onChange={pickSource}
          options={[
            { value: "current", label: "TONIGHT" },
            { value: "session", label: "A PAST SESSION" },
            { value: "manual", label: "MANUAL" },
          ]}
        />
        {srcValue === "session" && (
          <button
            type="button"
            className="nx-chip"
            data-testid="files-session-picker"
            onClick={() => setPickerOpen((o) => !o)}
          >
            {row ? sessionDates(row.created_ts, row.updated_ts) : "pick a session"}
          </button>
        )}
        {pickerOpen && (
          <Card tone="default" data-testid="files-session-list">
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {(sessions ?? []).map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className="nx-row"
                  onClick={() => { setPickerOpen(false); goSource(s.id); }}
                >
                  <span className="nx-row-text">
                    <span className="nx-row-title">{s.name}</span>
                    <span className="nx-row-sub">
                      {sessionDates(s.created_ts, s.updated_ts)} · {s.accepted} subs · {s.status}
                    </span>
                  </span>
                </button>
              ))}
              {(sessions ?? []).length === 0 && (
                <Mono size={10.5} tone="dim">No sessions on the rig.</Mono>
              )}
            </div>
          </Card>
        )}

        {/* -------------------------------------------------- per-target bars */}
        {session && session.plan.targets.length > 1 && (
          <Card data-testid="files-targets">
            <Label>TARGETS</Label>
            {targetProgress(session.plan, session.frames).map((t) => (
              <div key={t.target_id} style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <Mono size={10.5}>{t.name}</Mono>
                  <Mono size={10} tone="dim">{`${t.accepted} / ${t.total} kept`}</Mono>
                </div>
                <Bar value={t.total ? t.accepted / t.total : 0} height={6} tone="accent" label={`${t.name} progress`} />
              </div>
            ))}
          </Card>
        )}

        {/* -------------------------------------------------- stack preview */}
        <div
          style={{
            position: "relative", height: 200, borderRadius: 14, overflow: "hidden",
            border: "1px solid var(--line)", background: "#000", flexShrink: 0,
          }}
          data-testid="files-stack"
        >
          {heroUrl ? (
            <img
              src={heroUrl}
              alt={`Auto-stack of ${target}`}
              onLoad={onHeroLoaded}
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
            />
          ) : (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Mono size={10.5} tone="dim">
                {src === "current" ? "no composite yet" : "the auto-stack is tonight's run only"}
              </Mono>
            </div>
          )}
          <span
            style={{
              position: "absolute", left: 10, top: 10, padding: "4px 8px", borderRadius: 999,
              background: "rgba(6,7,11,.75)", border: "1px solid var(--line-bright)",
            }}
          >
            <Mono size={10}>
              AUTO-STACK · {stackFrames} subs · {fmtIntegration(stackIntegration)}
            </Mono>
          </span>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <SaveStack url={stackUrl} target={target} live={src === "current"} />
          <ShareStack url={stackUrl} target={target} live={src === "current"} />
        </div>

        {/* ----------------------------------------------------- subs card */}
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Label>SUBS</Label>
            <Segmented
              data-testid="files-format"
              label="Download format"
              value={pref}
              onChange={setPref}
              lockedReason={fitsReason}
              onExplain={explainLock}
              options={[
                { value: "fits", label: "FITS originals" },
                { value: "jpg", label: "JPEG previews" },
              ]}
            />
          </div>
          {fitsReason && (
            <p
              data-testid="files-fits-locked"
              style={{ margin: "6px 0 0", fontSize: 11, color: "var(--warn)" }}
            >
              {fitsReason}
            </p>
          )}

          {libErr && (
            <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--bad)" }}>{libErr}</p>
          )}
          {gradeNote && (
            <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--text-faint)" }}>{gradeNote}</p>
          )}
          {!gradeNote && !manual && index == null && (
            <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--text-faint)" }}>
              Reading this session&apos;s grades&hellip;
            </p>
          )}
          {manual && (
            <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--text-faint)" }}>
              These frames are not in a session ledger, so there are no grades to show.
            </p>
          )}

          <div style={{ marginTop: 8 }} data-testid="files-rows">
            {usable.length === 0 ? (
              <EmptyCard
                title={live ? dlLabel : "NOTHING ON THE RIG FOR THIS TARGET"}
                hint={live ? EMPTY_NOTE : undefined}
              />
            ) : (
              rows.map((r) => (
                <FilterRow
                  key={r.filter}
                  row={r}
                  checked={tick.has(r.filter)}
                  expanded={expanded === r.filter}
                  onToggleCheck={() => setTicked((cur) => {
                    const next = new Set(cur ?? []);
                    if (!next.delete(r.filter)) next.add(r.filter);
                    return next;
                  })}
                  onExpand={() => setExpanded((e) => (e === r.filter ? null : r.filter))}
                >
                  <FrameList
                    row={r}
                    verdict={verdict}
                    setVerdict={setVerdict}
                    sel={sel}
                    setSel={setSel}
                    regradeReason={regradeReason}
                    onBulk={bulk}
                    canMedia={canMedia}
                    galleryPaths={r.paths}
                    jpeg={pref === "jpg"}
                    canPreview={canPreview}
                    target={target}
                  />
                </FilterRow>
              ))
            )}
          </div>

          {/* --------------------------------------------------- download */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
            {dlReason ? (
              <ActionButton
                kind="primary"
                size="xl"
                full
                data-testid="files-download"
                lockedReason={dlReason}
                onExplain={explainLock}
                onPress={() => { /* unreachable while locked */ }}
              >
                {dlLabel}
              </ActionButton>
            ) : (
              <a
                data-testid="files-download"
                className="nx-btn"
                data-kind="primary"
                data-size="xl"
                data-full="true"
                href={href}
                download
                onClick={onDownload}
              >
                <span className="nx-btn-label">{dlLabel}</span>
              </a>
            )}
            <Mono size={10} tone="dim">{note.line}</Mono>
            {note.extra && <Mono size={10} tone="dim">{note.extra}</Mono>}
            {!plan.ok && canMedia && selection.mode === "picked" && cost.count > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-testid="files-refusal">
                <Mono size={10} tone="warn">{plan.reason}</Mono>
                <ActionButton
                  kind="secondary"
                  onPress={() => setTicked(new Set(usable.map((x) => x.filter)))}
                >
                  {`DOWNLOAD ALL ${fmtCount(totals.subs)} INSTEAD`}
                </ActionButton>
              </div>
            )}
          </div>
        </Card>

        {/* ------------------------------------------------ stacking bundle */}
        <BundlePanel
          session={session}
          row={row}
          reports={reports}
          canCapture={canCapture}
          desktop={bp !== "phone"}
        />

        <p style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5, margin: 0 }}>
          {FORMAT_BLURB}
        </p>
      </div>
    </Sheet>
  );
}

/** Seconds until the first sub lands, or null when the phase is not knowable.
 *  Floored at 60 s, exactly as the prototype's own formula does - an estimate
 *  under a minute is a promise the slew alone will break. */
function firstSubEta(seq: SequenceState, session: Session | null): number | null {
  const exposure = seq.progress?.current_exposure_s
    ?? session?.plan.targets[0]?.steps[0]?.exposure_s
    ?? null;
  if (exposure == null) return null;
  return Math.max(60, exposure);
}

/** Fold a completed override back into the index so the badges move without a
 *  refetch. Returns the SAME object when nothing matched, so React can skip. */
function applyOverride(
  index: SessionFilesIndex | null,
  ids: readonly string[],
  override: "accept" | "reject",
): SessionFilesIndex | null {
  if (!index) return index;
  const set = new Set(ids);
  let touched = false;
  const by_filter = index.by_filter.map((g) => {
    if (!g.frames.some((f) => set.has(f.id))) return g;
    touched = true;
    const frames = g.frames.map((f) =>
      set.has(f.id) ? { ...f, override, accepted: override === "accept" } : f);
    const accepted = frames.filter((f) => f.accepted).length;
    // Integration follows the verdict: rejecting a sub has to take its minutes
    // off the header too, or the sheet keeps advertising time it no longer
    // counts. Same arithmetic the server's fold does (accepted x the step's
    // exposure), so a refetch cannot disagree with this.
    return { ...g, frames, accepted, integration_s: accepted * g.exposure_s };
  });
  if (!touched) return index;
  return {
    ...index,
    by_filter,
    totals: {
      ...index.totals,
      accepted: by_filter.reduce((a, g) => a + g.accepted, 0),
    },
  };
}

// ============================================================== filter row

function FilterRow({ row, checked, expanded, onToggleCheck, onExpand, children }: {
  row: FilesRow;
  checked: boolean;
  expanded: boolean;
  onToggleCheck: () => void;
  onExpand: () => void;
  children: JSX.Element;
}): JSX.Element {
  const dead = row.subs === 0;
  const swatch = `var(--nx-filter-${row.filter || "L"}, var(--text-faint))`;
  return (
    <div
      data-testid={`files-row-${row.filter || "none"}`}
      data-checked={checked ? "true" : "false"}
      style={{ borderBottom: "1px solid var(--line)", opacity: dead ? 0.55 : 1 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 4, minHeight: 46 }}>
        <Checkbox22
          checked={checked && !dead}
          onChange={onToggleCheck}
          lockedReason={dead ? "No subs of this filter have been banked yet." : null}
          onExplain={explainLock}
          data-testid={`files-tick-${row.filter || "none"}`}
          label={<span style={SR_ONLY}>{`include ${filterLabel(row.filter)}`}</span>}
        />
        <button
          type="button"
          onClick={onExpand}
          aria-expanded={expanded}
          style={{
            flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 1,
            background: "transparent", border: 0, textAlign: "left", cursor: "pointer",
            color: "inherit", minHeight: 46, justifyContent: "center", padding: 0,
          }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: 999, background: swatch }} aria-hidden="true" />
            <span className="nx-display" style={{ fontSize: 12, letterSpacing: ".1em" }}>
              {filterLabel(row.filter)}
            </span>
          </span>
          <Mono size={10} tone="dim">{rowSubtitle(row)}</Mono>
        </button>
        <Mono size={11}>{fmtBytes(row.bytes)}</Mono>
      </div>
      {expanded && children}
    </div>
  );
}

// =============================================================== frame list

function FrameList({
  row, verdict, setVerdict, sel, setSel, regradeReason, onBulk, canMedia, galleryPaths,
  jpeg, canPreview, target,
}: {
  row: FilesRow;
  verdict: VerdictPick;
  setVerdict: (v: VerdictPick) => void;
  sel: string[];
  setSel: (f: (cur: string[]) => string[]) => void;
  regradeReason: string | null;
  onBulk: (o: "accept" | "reject") => void;
  canMedia: boolean;
  galleryPaths: readonly string[];
  /** JPEG is the chosen (or the only available) format, so each row carries its
   *  own save link - the "one frame at a time" the reason line promises. */
  jpeg: boolean;
  canPreview: boolean;
  target: string;
}): JSX.Element {
  const adapted = useMemo(() => row.frames.map(asSessionFrame), [row.frames]);
  const visible = useMemo(() => {
    const keep = filterFrames(adapted, verdict === "all" ? {} : { verdict });
    const ids = new Set(keep.map((f) => f.id));
    return row.frames.filter((f) => ids.has(f.id));
  }, [adapted, row.frames, verdict]);

  // A filter change must prune the selection: a hidden-but-selected frame
  // regraded by a bulk action is a change nobody watched happen.
  useEffect(() => {
    setSel((cur) => pruneSelection(cur, visible.map(asSessionFrame)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [verdict]);

  if (!row.frames.length) {
    return (
      <div style={{ padding: "8px 0 10px" }}>
        <Mono size={10} tone="dim">
          {row.subs > 0
            ? "No ledger rows for this filter, so there is nothing to grade here."
            : "Nothing banked yet."}
        </Mono>
      </div>
    );
  }

  return (
    <div style={{ padding: "6px 0 10px", display: "flex", flexDirection: "column", gap: 8 }}
      data-testid={`files-frames-${row.filter || "none"}`}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {VERDICTS.map((v) => (
          <Chip key={v.value} active={verdict === v.value} onClick={() => setVerdict(v.value)}>
            {v.label}
          </Chip>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column" }}>
        {visible.map((f) => {
          const v = verdictOf(asSessionFrame(f));
          const picked = sel.includes(f.id);
          return (
            <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 44 }}>
              <Checkbox22
                checked={picked}
                onChange={() => setSel((cur) => toggleSel(cur, f.id))}
                label={<span style={SR_ONLY}>{`select frame ${f.id}`}</span>}
              />
              {f.thumb ? (
                <img src={u(f.thumb)} alt="" width={40} height={30}
                  style={{ objectFit: "cover", borderRadius: 4, background: "#000" }} />
              ) : (
                <span style={{ width: 40, height: 30, borderRadius: 4, border: "1px dashed var(--line-bright)" }} aria-hidden="true" />
              )}
              <Mono size={10} tone="dim">
                {`HFR ${f.hfr != null ? f.hfr.toFixed(2) : "-"} · ${f.stars != null ? `${f.stars}*` : "- stars"} · RMS ${f.guide_rms != null ? f.guide_rms.toFixed(2) : "-"}`}
              </Mono>
              <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
                {jpeg && <SaveJpg frame={f} canPreview={canPreview} target={target} filter={row.filter} />}
                <Pill tone={v === "rejected" ? "bad" : v === "overridden" ? "warn" : "good"}>
                  {v === "accepted" ? "KEPT" : v === "rejected" ? "REJECTED" : "CHANGED"}
                </Pill>
              </span>
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <ActionButton
          kind="secondary"
          data-testid="files-mark-accepted"
          lockedReason={regradeReason}
          onExplain={explainLock}
          onPress={() => onBulk("accept")}
        >
          {`MARK ACCEPTED (${sel.length})`}
        </ActionButton>
        <ActionButton
          kind="secondary"
          data-testid="files-mark-rejected"
          lockedReason={regradeReason}
          onExplain={explainLock}
          onPress={() => onBulk("reject")}
        >
          {`MARK REJECTED (${sel.length})`}
        </ActionButton>
      </div>
      {regradeReason && (
        <Mono size={10} tone="warn">{regradeReason}</Mono>
      )}
      {!jpeg && canMedia && galleryPaths.length > 0 && (
        <Mono size={10} tone="dim">
          {`${galleryPaths.length} of these are in the library index and ride in the zip above.`}
        </Mono>
      )}
    </div>
  );
}

/** One frame's JPEG, as a plain `<a download>` - never a fetch into a Blob, for
 *  the same reason the zip is not one.
 *
 *  This is the OTHER half of the FITS deviation. Withholding raw frames from a
 *  role without `view.media` is right; leaving that role with a format picker
 *  stuck on JPEG and nothing anywhere that serves a JPEG is not. The route
 *  behind `frame.thumb` is `CAP_VIEW_PREVIEW`, so this works for exactly the
 *  roles the picker forces onto it, and it says so when a frame has no rendered
 *  preview rather than offering a link to a 404. */
function SaveJpg({ frame, canPreview, target, filter }: {
  frame: SessionFilesFrame;
  canPreview: boolean;
  target: string;
  filter: string;
}): JSX.Element {
  const reason = !canPreview
    ? `Saving a preview needs ${accessPhrase("view.preview")}.`
    : !frame.thumb
      ? "The rig rendered no preview for this frame, so there is no JPEG to save."
      : null;
  if (reason) {
    return (
      <ActionButton
        kind="ghost"
        data-testid={`files-save-jpg-${frame.id}`}
        lockedReason={reason}
        onExplain={explainLock}
        onPress={() => { /* unreachable while locked */ }}
      >
        SAVE JPG
      </ActionButton>
    );
  }
  const stem = `${target || "frame"}${filter ? `-${filter}` : ""}-${frame.id}`
    .replace(/\s+/g, "-");
  return (
    <a
      className="nx-btn"
      data-kind="ghost"
      data-testid={`files-save-jpg-${frame.id}`}
      href={u(frame.thumb as string)}
      download={`${stem}.jpg`}
    >
      <span className="nx-btn-label">SAVE JPG</span>
    </a>
  );
}

// ============================================================== stack saves

const NO_STACK_LIVE = "There is no composite yet - the stack builds from accepted subs.";
const NO_STACK_PAST = "The auto-stack is tonight's run only; a past session has its frames, not a composite.";

function SaveStack({ url, target, live }: { url: string | null; target: string; live: boolean }): JSX.Element {
  if (!url) {
    return (
      <ActionButton
        kind="secondary"
        full
        lockedReason={live ? NO_STACK_LIVE : NO_STACK_PAST}
        onExplain={explainLock}
        onPress={() => { /* locked */ }}
      >
        SAVE STACK
      </ActionButton>
    );
  }
  return (
    <a className="nx-btn" data-kind="secondary" data-full="true" href={url} download={`${target}-stack.jpg`}
      data-testid="files-save-stack">
      <span className="nx-btn-label">SAVE STACK · JPG</span>
    </a>
  );
}

function ShareStack({ url, target, live }: { url: string | null; target: string; live: boolean }): JSX.Element {
  const share = async () => {
    if (!url) return;
    const abs = new URL(url, window.location.href).toString();
    const nav2 = navigator as Navigator & { share?: (d: { title?: string; url?: string }) => Promise<void> };
    if (nav2.share) {
      try {
        await nav2.share({ title: `${target} auto-stack`, url: abs });
        return;
      } catch {
        /* cancelled or unsupported payload - fall through to the clipboard */
      }
    }
    const clip = navigator.clipboard;
    if (clip?.writeText) {
      try {
        await clip.writeText(abs);
        toast("success", "Link copied");
        return;
      } catch {
        /* http origins reject it; the legacy path below still works */
      }
    }
    if (legacyCopy(abs)) { toast("success", "Link copied"); return; }
    // Never silent: both paths refused and the user must know.
    toast("warning", "Copy blocked", "This browser refused both the share sheet and the clipboard over a plain-http connection.");
  };

  return (
    <ActionButton
      kind="ghost"
      data-testid="files-share"
      lockedReason={url ? null : live ? NO_STACK_LIVE : NO_STACK_PAST}
      onExplain={explainLock}
      onPress={() => void share()}
    >
      SHARE
    </ActionButton>
  );
}

// =========================================================== bundle panel

function BundlePanel({ session, row, reports, canCapture, desktop }: {
  session: Session | null;
  row: SessionRow | null;
  reports: SessionReportSummary[] | null;
  canCapture: boolean;
  desktop: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<BundlePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [layout, setLayout] = useState<BundleLayout>("grouped");
  const [weightAlt, setWeightAlt] = useState(false);
  const [keepOn, setKeepOn] = useState(false);
  const [keepText, setKeepText] = useState("0.5");
  const [materializing, setMaterializing] = useState(false);

  const keepThreshold = keepOn ? clamp01(Number(keepText)) : null;
  // Typing "0.55" is four keystrokes and each one re-counts the whole night
  // server-side; the INPUT stays live, only the refetch waits.
  const [keepDebounced, setKeepDebounced] = useState<number | null>(keepThreshold);
  useEffect(() => {
    const t = setTimeout(() => setKeepDebounced(keepThreshold), 300);
    return () => clearTimeout(t);
  }, [keepThreshold]);

  /** The report this session wrote, if it wrote one. Matched on the plan name
   *  plus the session's own window - `SessionReportSummary` carries no session
   *  id, and picking the newest report of any plan would attach the wrong
   *  night's bundle to this sheet. */
  const reportId = useMemo(() => {
    if (!session || !reports?.length) return null;
    const from = session.created_ts - 3600;
    const to = session.updated_ts + 12 * 3600;
    const hit = reports
      .filter((r) => r.plan_name === session.plan.name && r.started_at >= from && r.started_at <= to)
      .sort((a, b) => b.started_at - a.started_at)[0];
    return hit?.id ?? null;
  }, [session, reports]);

  const opts = useMemo(
    () => ({ layout, weightAlt, keepThreshold: keepDebounced }),
    [layout, weightAlt, keepDebounced],
  );

  useEffect(() => {
    if (!open || !reportId) return;
    let alive = true;
    setPreviewLoading(true);
    setPreviewErr(null);
    getBundlePreview(reportId, opts)
      .then((p) => { if (alive) setPreview(p); })
      .catch((e) => {
        if (!alive) return;
        setPreview(null);
        setPreviewErr(e instanceof Error ? e.message : "unknown error");
      })
      .finally(() => { if (alive) setPreviewLoading(false); });
    return () => { alive = false; };
  }, [open, reportId, opts]);

  const live = row?.status === "active";
  const gateReason = live
    ? "The bundle is built from the night's report - it is ready when the run ends."
    : !session
      ? "Pick a session; a bundle is built from one night's report."
      : !reportId
        ? "No report was written for this session, so there is nothing to bundle."
        : null;

  const matReason = !canCapture
    ? `Writing to the capture box needs ${accessPhrase("control.capture")}.`
    : previewErr
      ? `Couldn't read this session's bundle preview: ${previewErr}`
      : materializeDisabledReason(preview ? sumLights(preview) : 0, preview);

  const kept = keptSummary(preview);

  const onMaterialize = async () => {
    if (!reportId || matReason) return;
    setMaterializing(true);
    try {
      const r = await materializeBundle(reportId, opts);
      toast("success", "Folder written", r.export_dir);
    } catch (e) {
      toast("error", "Couldn't materialize the bundle", e instanceof Error ? e.message : undefined);
    } finally {
      setMaterializing(false);
    }
  };

  return (
    <Card data-testid="files-bundle">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex", alignItems: "center", gap: 8, width: "100%", minHeight: 44,
          background: "transparent", border: 0, color: "inherit", cursor: "pointer", padding: 0,
        }}
      >
        <NxIcon name={open ? "chevron-down" : "chevron-right"} size={14} />
        <Label>STACKING BUNDLE</Label>
      </button>

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
          <p style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5, margin: 0 }}>
            {BUNDLE_BLURB}
          </p>

          {gateReason ? (
            <Mono size={10} tone="warn">{gateReason}</Mono>
          ) : (
            <>
              {previewErr && (
                <Mono size={10} tone="warn">
                  {`Couldn't work out what this session would bundle: ${previewErr}. The .zip below still streams whatever is on disk; only the per-group breakdown is missing.`}
                </Mono>
              )}
              {preview && preview.groups.length > 0 && (
                <div style={{ opacity: previewLoading ? 0.5 : 1 }}>
                  {preview.groups.map((g, i) => (
                    <div key={`${g.dir}-${i}`}
                      style={{ display: "flex", flexWrap: "wrap", gap: 8, padding: "4px 0", borderBottom: "1px solid var(--line)" }}>
                      <Mono size={10.5}>
                        {[g.target, g.filter ?? "NoFilter", `${g.exposure_s}s`,
                          g.gain != null ? `g${g.gain}` : "", g.binning != null ? `bin${g.binning}` : ""]
                          .filter(Boolean).join(" · ")}
                      </Mono>
                      <Mono size={10} tone="dim">
                        {`${g.light_count} lights`}
                        {g.accepted_count !== g.light_count ? ` (${g.accepted_count} accepted)` : ""}
                        {g.kept_count != null && g.kept_count !== g.light_count ? ` · ${g.kept_count} kept` : ""}
                      </Mono>
                      <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                        {masterChips(g.masters).map((c) => (
                          <Pill key={c.kind} tone={c.ok ? "good" : "dim"}>
                            {`${c.ok ? "+" : "-"} ${c.kind}`}
                          </Pill>
                        ))}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {preview?.warnings.map((w, i) => (
                <Mono key={i} size={10} tone="warn">{w}</Mono>
              ))}
              {kept && (
                <Mono size={10} tone="dim">
                  {kept}{previewLoading ? " (recounting for the new cutoff…)" : ""}
                </Mono>
              )}

              {/* ------------------------------------------------ advanced */}
              <Segmented
                label="Bundle folder layout"
                data-testid="bundle-layout"
                value={layout}
                onChange={setLayout}
                options={layoutOptions().map((o) => ({ value: o.value, label: o.label, sub: o.hint }))}
              />
              <Switch
                checked={weightAlt}
                onChange={setWeightAlt}
                label="weight subs by altitude"
                note="Folds a sin(altitude) transparency term into each sub's weight - higher subs shoot through less air."
              />
              <Switch
                checked={keepOn}
                onChange={setKeepOn}
                label="flag the weakest subs"
                note="Subs below the cutoff are marked keep=false in the manifest. Nothing is deleted; every sub is still exported."
              />
              {keepOn && (
                <TextInput
                  ariaLabel="Keep threshold (normalized weight)"
                  value={keepText}
                  onChange={setKeepText}
                  mono
                  data-testid="bundle-keep"
                />
              )}

              <a
                className="nx-btn"
                data-kind="primary"
                data-testid="bundle-zip"
                href={u(`/api/reports/${encodeURIComponent(reportId ?? "")}/bundle.zip${bundleQuery(opts)}`)}
                download
              >
                <span className="nx-btn-label">DOWNLOAD BUNDLE</span>
              </a>
              <Mono size={10} tone="dim">
                the manifest and the build script - the photos stay on the rig
              </Mono>

              {desktop && (
                <>
                  <Mono size={10} tone="warn">{MATERIALIZE_DANGER}</Mono>
                  <ActionButton
                    kind="secondary"
                    data-testid="bundle-materialize"
                    busy={materializing}
                    lockedReason={matReason}
                    onExplain={explainLock}
                    onPress={() => void onMaterialize()}
                  >
                    MATERIALIZE ON THE RIG
                  </ActionButton>
                </>
              )}

              <a
                className="nx-btn"
                data-kind="ghost"
                data-testid="bundle-csv"
                href={u(`/api/reports/${encodeURIComponent(reportId ?? "")}/frames.csv`)}
                download
              >
                <span className="nx-btn-label">DOWNLOAD FRAMES.CSV</span>
              </a>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

function sumLights(p: BundlePreview): number {
  return p.groups.reduce((a, g) => a + g.light_count, 0);
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0.5;
  return Math.min(1, Math.max(0, v));
}

export default FilesSheet;
