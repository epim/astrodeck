// SessionReviewDrawer.tsx — frame-grid review for one session (sessions spec
// §7). LogDrawer pattern: lg+ docked right column, bottom sheet below lg;
// role="dialog" aria-modal="false", Escape, focus trap, focus return — all of
// which now come from the shared <Overlay/> primitive.
//
// REVIEW #38: the drawer was `bg-raise/95 backdrop-blur` (lg+: `bg-raise/60`),
// so the AUTOMATION labels and toggles of the panel behind it read straight
// through the frame metadata, and its right column of cards was clipped
// mid-line ("HFR 2.51 · ★20 ·"). Both are primitive-level: `.overlay-surface`
// is opaque, and the surface is a clamped flex column whose BODY is the only
// scroll region, so the card grid can no longer run past the surface edge.
// The lg+ variant was additionally `fixed right-0 top-0 bottom-0` authored
// INSIDE SessionsPanel — i.e. inside a `.panel`, whose `backdrop-filter` is a
// containing block — so "fixed to the right of the screen" was never true.
import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { Overlay } from "../Overlay";
import { BASE } from "../../lib/base";
import { ApiError } from "../../api";
import { useStore } from "../../store";
import { accessPhrase, useCanControlMount } from "../../lib/caps";
import { getSession, patchFrame } from "../../api/sessions";
import {
  filterFrames, pruneSelection, toggleSel, verdictOf, withOverride,
} from "../../lib/sessionReview";
import type { FrameFilters } from "../../lib/sessionReview";
import type { Session } from "../../types";

/** Tokened placeholder shared by thumb=null and broken-thumb states. */
function ThumbFallback() {
  return (
    <div className="w-full aspect-video bg-line2/40 flex items-center justify-center text-dim text-[10px]">
      no thumb
    </div>
  );
}

/** Frame thumbnail with a broken-image fallback (state lives per frame via the
 *  grid's key={f.id}, so a failed load doesn't blank its neighbours). */
function Thumb({ src }: { src: string | null }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken) return <ThumbFallback />;
  return (
    <img src={src} alt="" className="w-full aspect-video object-cover"
      loading="lazy" onError={() => setBroken(true)} />
  );
}

export default function SessionReviewDrawer({ id, onClose }: {
  id: string | null;
  onClose: () => void;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [flt, setFlt] = useState<FrameFilters>({});
  const [sel, setSel] = useState<string[]>([]);
  const [remaining, setRemaining] = useState<Record<string, number> | null>(null);
  const [busy, setBusy] = useState(false);
  const showToast = useStore((s) => s.showToast);
  // VIEWER-READ-ONLY (W2.5): the drawer itself stays viewable for every role
  // (browsing frames is a read, matching SessionsPanel's ungated "review"
  // affordance that opens it) — only the regrade controls, which PATCH
  // /api/sessions/{id}/frames/{fid}, gate. That route requires control.mount
  // (same cap as SessionsPanel's resume/delete/update-from-plan controls),
  // NOT control.capture — an operator can run captures but not regrade.
  const canRegrade = useCanControlMount();

  useEffect(() => {
    if (!id) {
      setSession(null);
      setSel([]);
      setRemaining(null);
      setFlt({});
      return;
    }
    void getSession(id).then(setSession).catch(() => setSession(null));
  }, [id]);

  if (!id || !session) return null;

  const nights = [...new Set(session.frames.map((f) => f.night))];
  const targets = session.plan.targets;
  const filterOf = (stepId: string): string => {
    for (const t of targets) {
      for (const s of t.steps) if (s.id === stepId) return s.filter ?? "—";
    }
    return "—";
  };
  // UX-2026-07-26 #37: every card already prints its filter band, but there was
  // no way to SEE one band at a time — a three-night SHO project was 180
  // thumbnails scrolled by eye. The band list comes from the frames actually in
  // this session (not the whole plan), so it never offers an empty filter.
  const bands = [...new Set(session.frames.map((f) => filterOf(f.step_id)))]
    .filter((b) => b !== "—")
    .sort();
  const frames = filterFrames(session.frames, flt, filterOf);
  const targetName = (tid: string): string =>
    targets.find((t) => t.id === tid)?.name ?? tid.slice(0, 8);
  const remainingTotal = remaining
    ? Object.values(remaining).reduce((a, b) => a + b, 0)
    : null;

  // Filter changes prune the selection against the NEW visible set so a
  // hidden-but-selected frame can never be regraded invisibly by a bulk action.
  const applyFlt = (next: FrameFilters) => {
    setFlt(next);
    setSel((s) => pruneSelection(s, filterFrames(session.frames, next, filterOf)));
  };

  const bulk = async (override: "accept" | "reject") => {
    // Belt-and-braces: only ever regrade the intersection of the selection and
    // the currently VISIBLE frames (applyFlt already keeps them in sync).
    const ids = pruneSelection(sel, frames);
    if (!canRegrade || busy || ids.length === 0) return;
    setBusy(true);
    let frames2 = session.frames;
    let rem: Record<string, number> | null = null;
    try {
      for (const fid of ids) {
        const res = await patchFrame(session.id, fid, { override });
        frames2 = withOverride(frames2, fid, override);
        rem = res.remaining;
      }
    } catch (e) {
      /* partial bulk: keep what applied — header stays truthful below. A 409
         means the session is running; the drawer stays usable read-only. */
      const running = e instanceof ApiError && e.status === 409;
      showToast("error", running
        ? "Session is running — regrades are read-only until it finishes"
        : `Regrade failed: ${(e as Error).message}`);
    }
    setSession({ ...session, frames: frames2 });
    if (rem) setRemaining(rem);   // live per-step remaining from the PATCH
    setSel([]);
    setBusy(false);
  };

  // #24: a bulk-regrade button the user might well want to press must state why
  // it won't fire. Native `disabled` says nothing on a touch device.
  const regradeReason = !canRegrade
    ? `Regrading frames needs ${accessPhrase("control.mount")}.`
    : busy
      ? "Applying the last regrade…"
      : sel.length === 0
        ? "Select one or more frames first."
        : null;

  const header = (
    <div className="flex items-center gap-2 p-3 flex-wrap">
      <span className="font-display font-semibold text-accent tracking-wider">
        Review · {session.name}
      </span>
      {remainingTotal != null && (
        <span className="mono text-[11px] text-dim">{remainingTotal} remaining</span>
      )}
      <div className="flex-1" />
      <button
        className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-dim hover:text-accent"
        aria-label="Close review" onClick={onClose}>
        <Icon name="x" size={16} />
      </button>
    </div>
  );

  const body = (
    <>
      <div className="flex items-center gap-2 pb-2 flex-wrap text-[11px]">
        <select className="field tap min-h-[44px] !py-1 !w-28" value={flt.target_id ?? ""}
          aria-label="Filter by target"
          onChange={(e) => applyFlt({ ...flt, target_id: e.target.value || undefined })}>
          <option value="">all targets</option>
          {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select className="field tap min-h-[44px] !py-1 !w-32" value={flt.night ?? ""}
          aria-label="Filter by night"
          onChange={(e) => applyFlt({ ...flt, night: e.target.value || undefined })}>
          <option value="">all nights</option>
          {nights.map((nx) => <option key={nx} value={nx}>{nx}</option>)}
        </select>
        {bands.length > 0 && (
          <select className="field tap min-h-[44px] !py-1 !w-24" value={flt.filter ?? ""}
            aria-label="Filter by filter band"
            onChange={(e) => applyFlt({ ...flt, filter: e.target.value || undefined })}>
            <option value="">all filters</option>
            {bands.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        )}
        <select className="field tap min-h-[44px] !py-1 !w-28" value={flt.verdict ?? ""}
          aria-label="Filter by verdict"
          onChange={(e) => applyFlt({ ...flt, verdict: (e.target.value || undefined) as FrameFilters["verdict"] })}>
          <option value="">all verdicts</option>
          <option value="accepted">accepted</option>
          <option value="rejected">rejected</option>
          <option value="overridden">overridden</option>
        </select>
        <div className="flex-1" />
        <button className={`btn tap min-h-[44px] !px-3 !text-[11px] ${regradeReason ? "opacity-50 cursor-not-allowed" : ""}`}
          aria-disabled={!!regradeReason || undefined}
          aria-label={regradeReason ? `Mark accepted — ${regradeReason}` : undefined}
          aria-describedby={regradeReason ? "regrade-reason" : undefined}
          onClick={() => { if (!regradeReason) void bulk("accept"); }}>
          <Icon name="check" size={12} /> mark accepted ({sel.length})
        </button>
        <button
          className={`tap min-h-[44px] !px-3 !text-[11px] border border-bad/60 text-bad hover:bg-bad/10 ${regradeReason ? "opacity-50 cursor-not-allowed" : ""}`}
          aria-disabled={!!regradeReason || undefined}
          aria-label={regradeReason ? `Mark rejected — ${regradeReason}` : undefined}
          aria-describedby={regradeReason ? "regrade-reason" : undefined}
          onClick={() => { if (!regradeReason) void bulk("reject"); }}>
          <Icon name="x" size={12} /> mark rejected
        </button>
      </div>
      {regradeReason && (
        <p id="regrade-reason" className="text-[11px] text-dim pb-2 inline-flex items-center gap-1.5">
          <Icon name="lock" size={11} aria-hidden />
          {regradeReason}
        </p>
      )}
      {/* what the filters are actually showing — with a band filter on, an
          empty grid must say so rather than look like a load failure (#37). */}
      <p className="mono text-[11px] text-dim pb-2">
        {frames.length === session.frames.length
          ? `${frames.length} frames`
          : `${frames.length} of ${session.frames.length} frames`}
        {frames.length === 0 && session.frames.length > 0 && " — no frame matches these filters"}
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {frames.map((f) => {
          const v = verdictOf(f);
          const selected = sel.includes(f.id);
          return (
            <button key={f.id}
              className={`text-left border p-1 flex flex-col gap-1 ${selected ? "border-accent" : "border-line"}`}
              aria-pressed={selected}
              aria-label={`Select frame ${f.id}`}
              onClick={() => setSel(toggleSel(sel, f.id))}>
              <Thumb src={f.thumb
                ? `${BASE}/api/sessions/${session.id}/frames/${f.id}/thumb` : null} />
              <span className="mono text-[10px] text-dim">
                {targetName(f.target_id)} · {filterOf(f.step_id)} · {f.night.slice(-6)}
              </span>
              <span className="mono text-[10px] text-dim">
                HFR {f.metrics.hfr != null ? f.metrics.hfr.toFixed(2) : "—"} ·
                ★{f.metrics.stars != null ? Math.round(f.metrics.stars) : "—"} ·
                RMS {f.metrics.guide_rms != null ? f.metrics.guide_rms.toFixed(2) : "—"}
              </span>
              <span className={`text-[10px] uppercase tracking-widest ${
                v === "rejected" ? "text-bad" : v === "overridden" ? "text-warn" : "text-good"}`}>
                {v}{f.override ? ` (${f.override})` : ""}
              </span>
            </button>
          );
        })}
      </div>
    </>
  );

  return (
    <Overlay
      open
      label="Session review"
      variant="dock"
      modal={false}
      scrim={false}
      dismissOnOutside
      trapFocus
      autoFocus
      onClose={onClose}
      head={header}
      bodyClassName="px-3 pb-3"
    >
      {body}
    </Overlay>
  );
}
