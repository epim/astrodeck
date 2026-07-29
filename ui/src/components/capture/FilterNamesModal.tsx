// FilterNamesModal.tsx — assign filter-wheel slot names + per-filter focuser
// offsets (UX-05). Reuses PreflightModal's overlay/focus-trap shell. Names flow
// into FITS FILTER headers, saved-image filenames (NINA-style token), and the
// filter picker; offsets feed per-filter autofocus. Persisted per profile by
// POST /api/filterwheel/names, so they survive a reconnect.

import { useEffect, useRef, useState, type JSX } from "react";
import { useFilterOffsetsLearn } from "../../store";

/** Slot to pre-select as the offset reference: an L/Lum/Clear slot when the
 *  wheel has one (case-insensitive), else the wheel's current position. Mirrors
 *  the server's `focus.filter_offsets.default_ref_slot` so the picker shows the
 *  same slot the API would choose on its own. A blackout slot can never be the
 *  reference — it passes no light, and the server rejects it — so if the wheel
 *  is sitting on one we fall through to the first slot that does pass light. */
function defaultRefSlot(names: string[], current: number,
                        opaque: boolean[] = []): number {
  const lum = ["l", "lum", "luminance", "clear", "lp", "uv/ir cut", "uvir"];
  const i = names.findIndex(
    (n, j) => !opaque[j] && lum.includes(n.trim().toLowerCase()));
  if (i >= 0) return i;
  if (!opaque[current]) return current;
  const first = names.findIndex((_, j) => !opaque[j]);
  return first >= 0 ? first : current;
}

export function FilterNamesModal({
  open,
  onClose,
  names,
  offsets,
  opaque = [],
  position = 0,
  canLearn = false,
  learnDisabledReason = null,
  onLearn,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  names: string[];
  offsets: number[];
  /** per-slot blackout flags, parallel to `names` */
  opaque?: boolean[];
  /** current wheel slot — the reference-picker fallback */
  position?: number;
  /** show the auto-learn disclosure at all (a focuser is present) */
  canLearn?: boolean;
  /** non-null => Start is honest-disabled with this reason in its title */
  learnDisabledReason?: string | null;
  onLearn?: (refSlot: number) => Promise<void>;
  onSave: (names: string[], offsets: number[], opaque: boolean[]) => Promise<void>;
}): JSX.Element | null {
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const [draftNames, setDraftNames] = useState<string[]>(names);
  const [draftOffsets, setDraftOffsets] = useState<string[]>(offsets.map(String));
  const [draftOpaque, setDraftOpaque] = useState<boolean[]>(names.map((_, i) => !!opaque[i]));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [learnOpen, setLearnOpen] = useState(false);
  const [refSlot, setRefSlot] = useState(0);
  const learn = useFilterOffsetsLearn();

  // Re-seed the drafts from the live wheel each time the modal opens.
  useEffect(() => {
    if (!open) return;
    setDraftNames(names);
    setDraftOffsets(names.map((_, i) => String(offsets[i] ?? 0)));
    setDraftOpaque(names.map((_, i) => !!opaque[i]));
    setRefSlot(defaultRefSlot(names, position, opaque));
    setErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // A finished learn run fills the offset inputs as EDITABLE DRAFTS — the
  // expert can hand-tweak any slot before Save. Slots whose autofocus failed
  // are reported in `kept` and keep their prior value (never a bogus 0).
  useEffect(() => {
    if (!open || learn?.state !== "done" || !learn.offsets) return;
    setDraftOffsets(learn.offsets.map(String));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, learn?.state]);

  // The parent's `onClose` reaches the focus trap through a ref, NOT through
  // the effect's dependency array. This is the whole of UX review #4, and it
  // corrupted the FITS `FILTER` header.
  //
  // MEASURED before the fix, on a live sim rig, s25ultra, real touch: tap Slot
  // 3's name field, type "Ha 3nm" one character at a time at human cadence, and
  // every keystroke lands in SLOT 1 — final state `Slot 1 = "LHa 3nm"`, Slot 3
  // still "G". Focus was already gone 200 ms after the tap, before the first
  // character.
  //
  // Root cause: the effect's deps were `[open, onClose]`, and both call sites
  // pass an arrow (`onClose={() => setOpen(false)}`) that is a NEW identity on
  // every parent render. The Equipment and Capture views re-render on every
  // device-status frame, so the effect re-ran a few times a second; its cleanup
  // yanked focus to the opener and its body then focused the panel's first
  // input — Slot 1's name. Typing a whole name between two frames misses it
  // entirely, which is why one reviewer hit it on every keystroke and another
  // never saw it at all. A race, not a contradiction.
  //
  // Fixing it in the parents alone (useCallback) would leave the landmine armed
  // for the next caller, so the component is made correct on its own terms:
  // the trap mounts ONCE per open, and the live `onClose` is read at call time.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // Focus trap + initial focus + Escape + restore (same shell as PreflightModal).
  useEffect(() => {
    if (!open) return;
    openerRef.current = (document.activeElement as HTMLElement) ?? null;
    const panel = panelRef.current;
    // Belt and braces for the same class of bug: never steal focus from a
    // field the user is already inside. Even if some future effect re-arms
    // this, it cannot move a caret mid-word.
    if (!panel?.contains(document.activeElement)) {
      panel?.querySelector<HTMLElement>("input, button:not([disabled])")?.focus();
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab" || !panel) return;
      const f = panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input, [tabindex]:not([tabindex="-1"])',
      );
      if (f.length === 0) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      openerRef.current?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  const setName = (i: number, v: string) =>
    setDraftNames((p) => p.map((n, j) => (j === i ? v : n)));
  const setOffset = (i: number, v: string) =>
    setDraftOffsets((p) => p.map((n, j) => (j === i ? v : n)));
  const toggleOpaque = (i: number) =>
    setDraftOpaque((p) => p.map((b, j) => (j === i ? !b : b)));

  const anyOpaque = draftOpaque.some(Boolean);
  // The reference must stay on a slot that passes light; marking the current
  // reference as blackout moves it rather than letting Start 400.
  const effectiveRef = draftOpaque[refSlot]
    ? draftOpaque.findIndex((b) => !b)
    : refSlot;

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const cleanOffsets = draftOffsets.map((o, i) => {
        if (draftOpaque[i]) return 0;   // no light path, so no offset to apply
        const n = Number(o);
        return Number.isFinite(n) ? Math.round(n) : 0;
      });
      await onSave(draftNames.map((n) => n.trim()), cleanOffsets,
                   [...draftOpaque]);
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center"
      role="presentation"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="fixed inset-0 bg-black/70" aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Filter slot names"
        className="panel relative z-[61] w-full sm:max-w-[420px] max-h-[88vh] sm:rounded-none rounded-t flex flex-col sheet-enter"
      >
        <header className="flex items-center justify-between gap-3 p-4 border-b border-line shrink-0">
          <h2 className="panel-title !text-ink truncate">Filter slot names</h2>
        </header>

        <div className="overflow-y-auto p-4 grow flex flex-col gap-2">
          <div className="grid grid-cols-[1.5rem_1fr_4rem_2.75rem] gap-2 label !text-[9px]">
            <span>#</span>
            <span>name</span>
            <span>offset</span>
            <span className="text-center">dark</span>
          </div>
          {draftNames.map((name, i) => (
            <div key={i} className="grid grid-cols-[1.5rem_1fr_4rem_2.75rem] gap-2 items-center">
              <span className="mono text-xs text-dim">{i + 1}</span>
              <input
                className="field"
                value={name}
                aria-label={`Slot ${i + 1} name`}
                onChange={(e) => setName(i, e.target.value)}
              />
              {draftOpaque[i] ? (
                /* Honest-disabled: an offset through a slot with no light path
                   is not a measurement. Shown as an em dash rather than a greyed
                   input so it reads as "not applicable", not "type here". The
                   reason is stated once below the grid — never title-only. */
                <span
                  className="mono text-xs text-dim opacity-60 text-center"
                  aria-label={`Slot ${i + 1} focuser offset — not applicable, blackout slot`}
                >
                  —
                </span>
              ) : (
                <input
                  className="field"
                  value={draftOffsets[i] ?? "0"}
                  inputMode="numeric"
                  aria-label={`Slot ${i + 1} focuser offset`}
                  onChange={(e) => setOffset(i, e.target.value)}
                />
              )}
              <label className="flex min-h-11 items-center justify-center cursor-pointer">
                <input
                  type="checkbox"
                  className="h-5 w-5 accent-accent"
                  checked={draftOpaque[i] ?? false}
                  aria-label={`Slot ${i + 1} is a blackout slot`}
                  onChange={() => toggleOpaque(i)}
                />
              </label>
            </div>
          ))}
          <p className="text-[11px] text-dim leading-snug mt-1">
            Names appear in FITS headers and saved filenames. Offsets are the
            per-filter focuser step delta autofocus applies when switching filters.
          </p>
          <p className="text-[11px] text-dim leading-snug">
            Tick <span className="text-ink">dark</span> for a blackout slot — a
            carrier with no glass that blocks the light path.
            {anyOpaque
              ? " Darks and bias will be shot through it, it takes no focus offset, and it is not offered as a filter for lights or flats."
              : " Most wheels do not have one."}
          </p>

          {/* --- Advanced: learn the offsets automatically. Collapsed by
               default; the manual grid above is untouched and still the novice
               path (offsets of 0 image perfectly well). --- */}
          {canLearn && (
            <div className="mt-2 border-t border-line pt-2">
              <button
                type="button"
                className="btn !px-2 !py-1 text-[11px]"
                aria-expanded={learnOpen}
                onClick={() => setLearnOpen((v) => !v)}>
                {learnOpen ? "▾ Learn offsets automatically" : "▸ Learn offsets automatically"}
              </button>
              {learnOpen && (
                <div className="mt-2 flex flex-col gap-2">
                  <p className="text-[11px] text-dim leading-snug">
                    Focuses each filter for you and fills in the offsets. Point at
                    a star field first. Takes a few minutes.
                    {anyOpaque && " Blackout slots are skipped."}
                  </p>
                  <label className="flex items-center gap-2 text-[11px] text-dim">
                    <span>Reference</span>
                    <select
                      className="field !w-32"
                      value={effectiveRef}
                      aria-label="Reference filter"
                      onChange={(e) => setRefSlot(Number(e.target.value))}>
                      {/* blackout slots are absent, not disabled: the server
                          rejects them outright, so offering one would only
                          produce a 400 the user cannot act on. */}
                      {draftNames.map((n, i) =>
                        draftOpaque[i] ? null : (
                          <option key={i} value={i}>{n || `Slot ${i + 1}`}</option>
                        ),
                      )}
                    </select>
                  </label>
                  <p className="text-[10px] text-dim">
                    Offsets are measured relative to this filter (it stays at 0).
                  </p>
                  <button
                    type="button"
                    className={`btn self-start ${learnDisabledReason ? "opacity-50 cursor-default" : ""}`}
                    aria-disabled={learnDisabledReason ? true : undefined}
                    title={learnDisabledReason ? `Unavailable — ${learnDisabledReason}` : undefined}
                    onClick={learnDisabledReason || !onLearn ? undefined : () => {
                      setErr(null);
                      onLearn(effectiveRef).catch((e) => setErr((e as Error).message));
                    }}>
                    Start
                  </button>
                  {learn?.state === "running" && (
                    <p className="text-[11px] text-accent" role="status">
                      Focusing {learn.name ?? `slot ${(learn.slot ?? 0) + 1}`}
                      {learn.of ? ` (${(learn.slot ?? 0) + 1} of ${learn.of})` : ""}…
                    </p>
                  )}
                  {learn?.state === "failed" && (
                    <p className="text-[11px] text-bad">{learn.error ?? "learn failed"}</p>
                  )}
                  {learn?.state === "done" && (learn.kept?.length ?? 0) > 0 && (
                    <p className="text-[11px] text-dim">
                      kept prior offset (no focus found) for:{" "}
                      {learn.kept!.map((i) => draftNames[i] || `slot ${i + 1}`).join(", ")}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {err && <p className="text-[11px] text-bad">{err}</p>}
        </div>

        <footer className="flex justify-end gap-2 p-4 border-t border-line shrink-0">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-accent min-h-12" onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}
