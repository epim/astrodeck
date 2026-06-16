// Checklist.tsx — pure presentational readiness list (onboarding spec §1b).
// Takes already-computed CheckItem[] (from lib/preflight.buildPreflight); reused
// by the inline strip, the modal, and (later) the safety monitor.
//
// Hard rule from the spec + master plan: state is NEVER colour-only. Every row
// carries a SHAPE glyph + a redundant uppercase WORD token; the existing
// --good/--warn/--bad tints are tertiary. The build test asserts every
// CheckItem.word is non-empty (that invariant is produced by lib/preflight).

import { useEffect, useRef, useState } from "react";
import type { JSX, ReactNode } from "react";
import type { CheckItem, CheckStatus } from "../types";
import { useStore } from "../store";
import { InfoDot } from "./ui";
import { helpText } from "../help";

// Shape + weight differentiated glyphs (18px via inline font-size, never the
// missing `.check-glyph` class — this component is self-sufficient). Each status
// is a DISTINCT silhouette so it survives grayscale + reduced-motion.
const GLYPH: Record<CheckStatus, string> = {
  ok: "✓",
  warn: "△",
  blocked: "✕",
  checking: "◌", // STATIC — never relies on .blink (resolves critique3 #11)
  skipped: "–",
  disabled: "⊘",
};

// Tertiary tint only (shape + word are the primary cue). Maps to AA tokens.
const TINT: Record<CheckStatus, string> = {
  ok: "text-good",
  warn: "text-warn",
  blocked: "text-bad",
  checking: "text-dim",
  skipped: "text-dim",
  disabled: "text-warn",
};

function StatusGlyph({ status }: { status: CheckStatus }) {
  return (
    <span
      aria-hidden
      className={`shrink-0 leading-none ${TINT[status]} ${
        status === "blocked" ? "inline-flex items-center justify-center rounded-full ring-1 ring-current w-[18px] h-[18px] text-[12px]" : ""
      }`}
      style={status === "blocked" ? undefined : { fontSize: 18, lineHeight: 1 }}
    >
      {GLYPH[status]}
    </span>
  );
}

/** Stat-style detail: primary value in --ink, unit/target in --dim; never truncated. */
function Detail({ detail }: { detail: NonNullable<CheckItem["detail"]> }) {
  return (
    <span className="mono text-[12px] text-ink leading-snug break-words">
      {detail.value}
      {detail.unit && <span className="text-dim ml-1">{detail.unit}</span>}
    </span>
  );
}

/** Fix affordance — in-place button (no navigation) or a deep-link that sets the view. */
function FixControl({ fix }: { fix: NonNullable<CheckItem["fix"]> }): JSX.Element {
  const setView = useStore((s) => s.setView);
  const onClick = () => {
    if (fix.inPlace && fix.onClick) {
      void fix.onClick();
    } else if (fix.view) {
      setView(fix.view);
    } else if (fix.onClick) {
      void fix.onClick();
    }
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className="btn !py-1 !px-2.5 !text-[12px] min-h-11 sm:min-h-9 shrink-0"
    >
      {fix.label}
    </button>
  );
}

export function ChecklistItem({ item, dense = false }: { item: CheckItem; dense?: boolean }): JSX.Element {
  const help = helpText(item.help);
  const showFix =
    !!item.fix && (item.status === "warn" || item.status === "blocked" || item.status === "disabled");
  return (
    <div
      className={`flex flex-wrap items-center gap-x-2.5 gap-y-1 ${dense ? "min-h-10 py-1.5" : "min-h-11 py-2"} px-1`}
      role="listitem"
      aria-label={`${item.label}: ${item.word}`}
    >
      <StatusGlyph status={item.status} />
      {/* redundant text token — the sufficient non-colour cue, always present */}
      <span className={`mono text-[11px] tracking-wider ${TINT[item.status]} shrink-0`}>
        {item.word}
      </span>
      <span className="text-sm text-ink inline-flex items-center gap-1 min-w-0">
        {item.label}
        {help && <InfoDot content={help} label={`About ${item.label}`} />}
      </span>
      <div className="flex-1 min-w-[8px]" />
      {item.detail && <Detail detail={item.detail} />}
      {showFix && item.fix && <FixControl fix={item.fix} />}
    </div>
  );
}

// Severity rank — a row "regressed" when its new status is worse than the old one.
// Only ok/warn/blocked carry severity; transient/neutral states (checking/skipped/
// disabled) never trigger an alert delta.
const SEVERITY: Partial<Record<CheckStatus, number>> = { ok: 0, warn: 1, blocked: 2 };

/**
 * Visually-hidden delta-announcer. The list itself is re-sorted every status tick,
 * so a region-level `aria-live` on the list would replay the entire list to a
 * screen reader on every poll (F-D1a). Instead we diff status-by-id across renders
 * and announce ONLY the rows that newly regressed (ok→warn/blocked, warn→blocked).
 * Assertive when anything reached `blocked`; polite for warn-only deltas.
 */
function useDeltaAnnounce(items: CheckItem[]): { message: string; assertive: boolean } {
  const prev = useRef<Map<string, CheckStatus>>(new Map());
  const [state, setState] = useState<{ message: string; assertive: boolean }>({
    message: "",
    assertive: false,
  });

  useEffect(() => {
    const regressions: { label: string; status: CheckStatus }[] = [];
    for (const it of items) {
      const before = prev.current.get(it.id);
      const oldRank = before != null ? SEVERITY[before] : undefined;
      const newRank = SEVERITY[it.status];
      if (newRank != null && oldRank != null && newRank > oldRank) {
        regressions.push({ label: it.label, status: it.status });
      }
    }
    // Rebuild the snapshot for the next diff (every id, not just regressions).
    const next = new Map<string, CheckStatus>();
    for (const it of items) next.set(it.id, it.status);
    prev.current = next;

    if (regressions.length === 0) return;
    const blocked = regressions.some((r) => r.status === "blocked");
    const msg = regressions
      .map((r) => `${r.label} ${r.status === "blocked" ? "blocked" : "warning"}`)
      .join("; ");
    // New object each time so an identical repeated message still re-announces.
    setState({ message: msg, assertive: blocked });
  }, [items]);

  return state;
}

/**
 * The readiness checklist. Empty (no items) renders 3 `checking` skeleton rows so
 * the layout never collapses while the first status tick / preflight fetch lands.
 */
export function Checklist({ items, dense = false }: { items: CheckItem[]; dense?: boolean }): JSX.Element {
  const announce = useDeltaAnnounce(items);
  const rows: ReactNode = items.length
    ? items.map((it) => <ChecklistItem key={it.id} item={it} dense={dense} />)
    : ([0, 1, 2] as const).map((i) => (
        <ChecklistItem
          key={`skeleton-${i}`}
          dense={dense}
          item={{ id: `skeleton-${i}`, label: "Checking…", status: "checking", word: "CHECKING" }}
        />
      ));
  return (
    <>
      {/* Visually-hidden delta-announcer (F-D1a): announces only newly-regressed
          rows, not the whole re-sorted list. assertive when a row hit `blocked`. */}
      <span className="sr-only" role="status" aria-live={announce.assertive ? "assertive" : "polite"}>
        {announce.message}
      </span>
      <div role="list" className="flex flex-col divide-y divide-line">
        {rows}
      </div>
    </>
  );
}
