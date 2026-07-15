// SessionReviewDrawer.tsx — frame-grid review for one session (sessions spec
// §7). LogDrawer pattern: lg+ docked right column, bottom sheet below lg;
// role="dialog" aria-modal="false", Escape, focus trap, focus return.
// Thumbs + metrics only (no full-size preview — out of scope v1).
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { BASE } from "../../lib/base";
import { ApiError } from "../../api";
import { useStore } from "../../store";
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
  const deskRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

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

  useEffect(() => {
    if (!id) return;
    returnFocusRef.current = (document.activeElement as HTMLElement) ?? null;
    const isVisible = (el: HTMLElement | null) => !!el && el.getClientRects().length > 0;
    const node = isVisible(deskRef.current) ? deskRef.current : sheetRef.current;
    const focusables = () =>
      node
        ? Array.from(
            node.querySelectorAll<HTMLElement>(
              'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((el) => !el.hasAttribute("disabled"))
        : [];
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      returnFocusRef.current?.focus?.();
    };
  }, [id, onClose]);

  if (!id || !session) return null;

  const frames = filterFrames(session.frames, flt);
  const nights = [...new Set(session.frames.map((f) => f.night))];
  const targets = session.plan.targets;
  const filterOf = (stepId: string): string => {
    for (const t of targets) {
      for (const s of t.steps) if (s.id === stepId) return s.filter ?? "—";
    }
    return "—";
  };
  const targetName = (tid: string): string =>
    targets.find((t) => t.id === tid)?.name ?? tid.slice(0, 8);
  const remainingTotal = remaining
    ? Object.values(remaining).reduce((a, b) => a + b, 0)
    : null;

  // Filter changes prune the selection against the NEW visible set so a
  // hidden-but-selected frame can never be regraded invisibly by a bulk action.
  const applyFlt = (next: FrameFilters) => {
    setFlt(next);
    setSel((s) => pruneSelection(s, filterFrames(session.frames, next)));
  };

  const bulk = async (override: "accept" | "reject") => {
    // Belt-and-braces: only ever regrade the intersection of the selection and
    // the currently VISIBLE frames (applyFlt already keeps them in sync).
    const ids = pruneSelection(sel, frames);
    if (busy || ids.length === 0) return;
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

  const header = (
    <div className="flex items-center gap-2 pb-2 flex-wrap">
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
        <select className="field !py-1 !w-28" value={flt.target_id ?? ""}
          aria-label="Filter by target"
          onChange={(e) => applyFlt({ ...flt, target_id: e.target.value || undefined })}>
          <option value="">all targets</option>
          {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select className="field !py-1 !w-32" value={flt.night ?? ""}
          aria-label="Filter by night"
          onChange={(e) => applyFlt({ ...flt, night: e.target.value || undefined })}>
          <option value="">all nights</option>
          {nights.map((nx) => <option key={nx} value={nx}>{nx}</option>)}
        </select>
        <select className="field !py-1 !w-28" value={flt.verdict ?? ""}
          aria-label="Filter by verdict"
          onChange={(e) => applyFlt({ ...flt, verdict: (e.target.value || undefined) as FrameFilters["verdict"] })}>
          <option value="">all verdicts</option>
          <option value="accepted">accepted</option>
          <option value="rejected">rejected</option>
          <option value="overridden">overridden</option>
        </select>
        <div className="flex-1" />
        <button className="btn tap min-h-[44px] !px-3 !text-[11px]"
          disabled={busy || sel.length === 0} onClick={() => void bulk("accept")}>
          <Icon name="check" size={12} /> mark accepted ({sel.length})
        </button>
        <button
          className="tap min-h-[44px] !px-3 !text-[11px] border border-bad/60 text-bad hover:bg-bad/10 disabled:opacity-40"
          disabled={busy || sel.length === 0} onClick={() => void bulk("reject")}>
          <Icon name="x" size={12} /> mark rejected
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 overflow-y-auto flex-1">
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
    <>
      {/* lg+ docked right column (LogDrawer precedent) */}
      <aside ref={deskRef} role="dialog" aria-modal="false" aria-label="Session review"
        className="hidden lg:flex flex-col w-[420px] border-l border-line bg-raise/60
          backdrop-blur p-3 overflow-y-auto shrink-0 fixed right-0 top-0 bottom-0 z-40">
        {header}
        {body}
      </aside>
      {/* below lg: bottom sheet; tap-catcher above only, no scrim */}
      <div className="lg:hidden">
        <div className="fixed inset-x-0 top-0 bottom-[60vh] z-30" onClick={onClose}
          aria-hidden="true" />
        <div ref={sheetRef} role="dialog" aria-modal="false" aria-label="Session review"
          className="fixed inset-x-0 bottom-0 z-40 h-[60vh] flex flex-col
            border-t border-line2 bg-raise/95 backdrop-blur p-3 sheet-enter">
          {header}
          {body}
        </div>
      </div>
    </>
  );
}
