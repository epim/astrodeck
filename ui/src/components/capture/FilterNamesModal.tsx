// FilterNamesModal.tsx — assign filter-wheel slot names + per-filter focuser
// offsets (UX-05). Reuses PreflightModal's overlay/focus-trap shell. Names flow
// into FITS FILTER headers, saved-image filenames (NINA-style token), and the
// filter picker; offsets feed per-filter autofocus. Persisted per profile by
// POST /api/filterwheel/names, so they survive a reconnect.

import { useEffect, useRef, useState, type JSX } from "react";

export function FilterNamesModal({
  open,
  onClose,
  names,
  offsets,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  names: string[];
  offsets: number[];
  onSave: (names: string[], offsets: number[]) => Promise<void>;
}): JSX.Element | null {
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const [draftNames, setDraftNames] = useState<string[]>(names);
  const [draftOffsets, setDraftOffsets] = useState<string[]>(offsets.map(String));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Re-seed the drafts from the live wheel each time the modal opens.
  useEffect(() => {
    if (!open) return;
    setDraftNames(names);
    setDraftOffsets(names.map((_, i) => String(offsets[i] ?? 0)));
    setErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Focus trap + initial focus + Escape + restore (same shell as PreflightModal).
  useEffect(() => {
    if (!open) return;
    openerRef.current = (document.activeElement as HTMLElement) ?? null;
    const panel = panelRef.current;
    panel?.querySelector<HTMLElement>("input, button:not([disabled])")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
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
  }, [open, onClose]);

  if (!open) return null;

  const setName = (i: number, v: string) =>
    setDraftNames((p) => p.map((n, j) => (j === i ? v : n)));
  const setOffset = (i: number, v: string) =>
    setDraftOffsets((p) => p.map((n, j) => (j === i ? v : n)));

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const cleanOffsets = draftOffsets.map((o) => {
        const n = Number(o);
        return Number.isFinite(n) ? Math.round(n) : 0;
      });
      await onSave(draftNames.map((n) => n.trim()), cleanOffsets);
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
          <div className="grid grid-cols-[2rem_1fr_5rem] gap-2 label !text-[9px]">
            <span>#</span>
            <span>name</span>
            <span>offset</span>
          </div>
          {draftNames.map((name, i) => (
            <div key={i} className="grid grid-cols-[2rem_1fr_5rem] gap-2 items-center">
              <span className="mono text-xs text-dim">{i + 1}</span>
              <input
                className="field"
                value={name}
                aria-label={`Slot ${i + 1} name`}
                onChange={(e) => setName(i, e.target.value)}
              />
              <input
                className="field"
                value={draftOffsets[i] ?? "0"}
                inputMode="numeric"
                aria-label={`Slot ${i + 1} focuser offset`}
                onChange={(e) => setOffset(i, e.target.value)}
              />
            </div>
          ))}
          <p className="text-[11px] text-dim leading-snug mt-1">
            Names appear in FITS headers and saved filenames. Offsets are the
            per-filter focuser step delta autofocus applies when switching filters.
          </p>
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
