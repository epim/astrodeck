// FilterNamesModal.tsx — assign filter-wheel slot names + per-filter focuser
// offsets (UX-05). Reuses PreflightModal's overlay/focus-trap shell. Names flow
// into FITS FILTER headers, saved-image filenames (NINA-style token), and the
// filter picker; offsets feed per-filter autofocus. Persisted per profile by
// POST /api/filterwheel/names, so they survive a reconnect.

import { useEffect, useRef, useState, type JSX } from "react";
import { useFilterOffsetsLearn, usePreviews, useStatus } from "../../store";
import { nameForOpaqueToggle } from "../../lib/filterSlots";
import { NARROWBAND_EXPOSURE_MULTIPLE, deriveAutofocusParams,
         narrowbandSweepSettings } from "../../lib/autofocus";
import { api } from "../../api";
import { HonestButton } from "../ui";
import { Icon } from "../icons";

/** What a learn run is told to do, beyond which slot is the reference.
 *
 *  It used to be told nothing at all: both call sites posted `{ ref_slot }`
 *  and the server's own defaults (2 s, gain 120) ran the whole wheel. The
 *  2026-08-08 run on the rig needed 10 s at gain 300 to measure L/R/G/B, and
 *  needed something different again for the narrowband slots — which is the
 *  fix below, and it is worth nothing if the exposure it multiplies is a
 *  number nobody chose. */
export interface LearnOffsetsRequest {
  exposure_s: number;
  gain: number;
  /** per-slot narrowband marking, persisted by the server before the run */
  narrowband: boolean[];
  nb_exposure_s: number;
  nb_gain: number;
}

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
  narrowband = [],
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
  /** per-slot narrowband flags, parallel to `names` */
  narrowband?: boolean[];
  /** current wheel slot — the reference-picker fallback */
  position?: number;
  /** show the auto-learn disclosure at all (a focuser is present) */
  canLearn?: boolean;
  /** non-null => Start is honest-disabled with this reason in its title */
  learnDisabledReason?: string | null;
  onLearn?: (refSlot: number, req: LearnOffsetsRequest) => Promise<void>;
  onSave: (names: string[], offsets: number[], opaque: boolean[],
           narrowband: boolean[]) => Promise<void>;
}): JSX.Element | null {
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const [draftNames, setDraftNames] = useState<string[]>(names);
  const [draftOffsets, setDraftOffsets] = useState<string[]>(offsets.map(String));
  const [draftOpaque, setDraftOpaque] = useState<boolean[]>(names.map((_, i) => !!opaque[i]));
  const [draftNarrow, setDraftNarrow] = useState<boolean[]>(names.map((_, i) => !!narrowband[i]));
  // What each slot was called before blackout renamed it, so unticking can
  // put it back rather than leaving DARK on a slot that now passes light.
  const priorNames = useRef<(string | undefined)[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [learnOpen, setLearnOpen] = useState(false);
  const [refSlot, setRefSlot] = useState(0);
  // Set by a press on the blocked Start. The reason is on screen either way
  // (see the line under the button); this only raises its voice for the person
  // who pressed, because a press that produced nothing at all is what got this
  // control cited in the first place.
  const [askedWhy, setAskedWhy] = useState(false);
  // The sweep's own settings. Strings, like every other numeric field in this
  // file, so a half-typed "1" is not read as a one-second exposure.
  const [learnExposure, setLearnExposure] = useState("");
  const [learnGain, setLearnGain] = useState("");
  // null = "follow the sweep settings". The narrowband pair is DERIVED from
  // the broadband one, so typing 10 into the sweep exposure has to move it —
  // a panel showing 8 s under a 10 s sweep would be stating a multiple that is
  // not the one it applies. Once hand-edited it stops following, because at
  // that point the operator knows something the derivation does not.
  const [nbExposure, setNbExposure] = useState<string | null>(null);
  const [nbGain, setNbGain] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const learn = useFilterOffsetsLearn();
  const status = useStatus();
  const previews = usePreviews();

  // What the sweep WOULD do, derived exactly as the Focus screen derives it —
  // same builder, so the two screens cannot disagree about what a sweep is.
  const lastFrame = previews.length ? previews[previews.length - 1] : null;
  const derived = deriveAutofocusParams({
    focuserMax: status?.focuser?.max ?? null,
    maxBin: status?.camera?.max_bin ?? null,
    maxGain: status?.camera?.max_gain ?? null,
    liveExposureS: lastFrame?.exposure_s ?? null,
    liveGain: lastFrame?.gain ?? null,
    liveBinning: lastFrame?.binning ?? null,
    liveStars: lastFrame?.stars ?? null,
    liveHfr: lastFrame?.hfr ?? null,
    hasLiveFrame: !!lastFrame,
  });
  const hcg = (status?.camera as { hcg_threshold_gain?: number | null } | undefined)
    ?.hcg_threshold_gain ?? null;

  // Re-seed the drafts from the live wheel each time the modal opens.
  useEffect(() => {
    if (!open) return;
    setDraftNames(names);
    setDraftOffsets(names.map((_, i) => String(offsets[i] ?? 0)));
    setDraftOpaque(names.map((_, i) => !!opaque[i]));
    setDraftNarrow(names.map((_, i) => !!narrowband[i]));
    setRefSlot(defaultRefSlot(names, position, opaque));
    setLearnExposure(String(derived.exposure_s));
    setLearnGain(String(derived.gain));
    setNbExposure(null);
    setNbGain(null);
    setErr(null);
    setAskedWhy(false);
    setStopping(false);
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
  // Ticking blackout NAMES the slot DARK (user nit 2026-07-30). The name is not
  // decoration: it lands in the FITS FILTER header and the saved-filename
  // token, so a dark slot still called "L" files darks under the wrong filter.
  // Only a placeholder name is replaced — silently overwriting one somebody
  // typed is the variant to avoid — and unticking puts the old name back.
  const toggleOpaque = (i: number) => {
    const nowOpaque = !draftOpaque[i];
    setDraftNames((prev) => prev.map((n, j) => {
      if (j !== i) return n;
      const next = nameForOpaqueToggle(n, i, nowOpaque, priorNames.current[i]);
      if (nowOpaque && next !== n) priorNames.current[i] = n;
      return next;
    }));
    setDraftOpaque((p) => p.map((b, j) => (j === i ? !b : b)));
    // A slot with no light path is not a narrowband slot: the two together
    // would only buy a longer exposure of nothing. The server enforces this on
    // save too; doing it here keeps the tick the user can SEE honest.
    if (nowOpaque) setDraftNarrow((p) => p.map((b, j) => (j === i ? false : b)));
  };
  const toggleNarrow = (i: number) =>
    setDraftNarrow((p) => p.map((b, j) => (j === i ? !b : b)));

  const anyOpaque = draftOpaque.some(Boolean);
  const nbSlots = draftNarrow
    .map((n, i) => (n && !draftOpaque[i] ? i : -1))
    .filter((i) => i >= 0);
  const numOr = (raw: string | null, fallback: number, min = 0): number => {
    const n = Number(raw);
    return (raw ?? "").trim() !== "" && Number.isFinite(n) && n >= min
      ? n : fallback;
  };
  // ONE derivation, feeding both the fields on screen and the request that
  // goes out. The sweep values are read from the fields the user is editing,
  // so what the narrowband line says is always a multiple of what is beside it.
  const sweepExposureS = numOr(learnExposure, derived.exposure_s, 0.001);
  const sweepGain = numOr(learnGain, derived.gain);
  const [derivedNbExposure, derivedNbGain] =
    narrowbandSweepSettings(sweepExposureS, sweepGain, hcg);
  const nbExposureS = numOr(nbExposure, derivedNbExposure, 0.001);
  const nbGainValue = numOr(nbGain, derivedNbGain);
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
                   [...draftOpaque],
                   draftNarrow.map((n, i) => n && !draftOpaque[i]));
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
          <div className="grid grid-cols-[1.5rem_1fr_4rem_2.75rem_2.75rem] gap-2 label !text-[9px]">
            <span>#</span>
            <span>name</span>
            <span>offset</span>
            <span className="text-center">dark</span>
            <span className="text-center">nb</span>
          </div>
          {draftNames.map((name, i) => (
            <div key={i} className="grid grid-cols-[1.5rem_1fr_4rem_2.75rem_2.75rem] gap-2 items-center">
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
              {/* THE MULTI-SELECT the operator asked for, in the place filters
                  are already configured rather than in the run that uses it:
                  ticking it here persists with the names and offsets, so a
                  wheel that does not change does not have to be re-described
                  every time offsets are learned. */}
              {draftOpaque[i] ? (
                <span
                  className="mono text-xs text-dim opacity-60 text-center"
                  aria-label={`Slot ${i + 1} narrowband — not applicable, blackout slot`}
                >
                  —
                </span>
              ) : (
                <label className="flex min-h-11 items-center justify-center cursor-pointer">
                  <input
                    type="checkbox"
                    className="h-5 w-5 accent-accent"
                    checked={draftNarrow[i] ?? false}
                    aria-label={`Slot ${i + 1} is a narrowband filter`}
                    onChange={() => toggleNarrow(i)}
                  />
                </label>
              )}
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
          <p className="text-[11px] text-dim leading-snug">
            Tick <span className="text-ink">nb</span> for a narrowband filter. A
            3–7 nm passband delivers a star tens of times fainter than
            luminance, so those slots are focused at their own exposure and gain
            (below). Saved with the names, so the wheel is only described once.
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
                  {/* THE SWEEP'S OWN SETTINGS. Both call sites used to post
                      `{ ref_slot }` alone, so every learn run in this app's
                      history ran at the server's 2 s / gain 120 defaults while
                      the operator's own working sweep was 10 s / gain 300.
                      Seeded from the same derivation the Focus screen prints,
                      and SENT — a number shown but not sent is the control
                      this file already has one scar from. */}
                  <div className="flex items-center gap-2 text-[11px] text-dim flex-wrap">
                    <span>Sweep</span>
                    <input
                      className="field !w-16"
                      value={learnExposure}
                      inputMode="decimal"
                      aria-label="Sweep exposure seconds"
                      onChange={(e) => setLearnExposure(e.target.value)}
                    />
                    <span>s at gain</span>
                    <input
                      className="field !w-16"
                      value={learnGain}
                      inputMode="numeric"
                      aria-label="Sweep gain"
                      onChange={(e) => setLearnGain(e.target.value)}
                    />
                  </div>
                  {nbSlots.length > 0 && (
                    <>
                      <div className="flex items-center gap-2 text-[11px] text-dim flex-wrap">
                        <span>Narrowband</span>
                        <input
                          className="field !w-16"
                          value={nbExposure ?? String(derivedNbExposure)}
                          inputMode="decimal"
                          aria-label="Narrowband sweep exposure seconds"
                          onChange={(e) => setNbExposure(e.target.value)}
                        />
                        <span>s at gain</span>
                        <input
                          className="field !w-16"
                          value={nbGain ?? String(derivedNbGain)}
                          inputMode="numeric"
                          aria-label="Narrowband sweep gain"
                          onChange={(e) => setNbGain(e.target.value)}
                        />
                      </div>
                      <p className="text-[10px] text-dim">
                        Applies to{" "}
                        {nbSlots.map((i) => draftNames[i] || `slot ${i + 1}`).join(", ")}.
                        Starting point is ×{NARROWBAND_EXPOSURE_MULTIPLE} the
                        sweep exposure — worth about 1.5 magnitudes on a frame
                        with almost no sky in it, not the ×40 a 7 nm passband
                        really costs. Raise it if a slot still cannot be
                        focused.
                      </p>
                    </>
                  )}
                  {/* Start used to dim itself and keep the whole reason in a
                      `title=`, which a fingertip never fires — so on the tablet
                      it was a pale button that depressed under a thumb and did
                      nothing, forever, with no way to ask why. HonestButton
                      (house rule §11.8) keeps it focusable and pressable and
                      routes the press to a reason; the line below states the
                      same reason to somebody who never presses it, exactly as
                      the egain twin on the Capture screen does. */}
                  <HonestButton
                    className="btn self-start"
                    reason={onLearn ? (learnDisabledReason ?? null)
                                    : "this build has no learn handler wired"}
                    onExplain={() => setAskedWhy(true)}
                    onClick={() => {
                      setErr(null);
                      setAskedWhy(false);
                      onLearn?.(effectiveRef, {
                        exposure_s: sweepExposureS,
                        gain: sweepGain,
                        narrowband: draftNarrow.map((n, i) => n && !draftOpaque[i]),
                        nb_exposure_s: nbExposureS,
                        nb_gain: nbGainValue,
                      }).catch((e) => setErr((e as Error).message));
                    }}>
                    Start
                  </HonestButton>
                  {learnDisabledReason && (
                    <p role={askedWhy ? "alert" : undefined}
                      className={`text-[11px] inline-flex items-center gap-1.5 ${askedWhy ? "text-warn" : "text-dim"}`}>
                      <Icon name="lock" size={11} aria-hidden /> Unavailable — {learnDisabledReason}.
                    </p>
                  )}
                  {learn?.state === "running" && (
                    <>
                      <p className="text-[11px] text-accent" role="status">
                        Focusing {learn.name ?? `slot ${(learn.slot ?? 0) + 1}`}
                        {learn.of ? ` (${(learn.slot ?? 0) + 1} of ${learn.of})` : ""}…
                      </p>
                      {/* THE WAY OUT. Until this existed the only way to clear
                          a hung filter-offsets run was to restart the server —
                          found on 2026-08-08, when a sweep re-exposed one
                          unmeasurable position fourteen times and the run could
                          neither finish nor fail. The route waits for the sweep
                          to put the focuser back before it halts it. */}
                      <button
                        type="button"
                        className="btn self-start min-h-11"
                        disabled={stopping}
                        onClick={async () => {
                          setStopping(true);
                          setErr(null);
                          try {
                            await api.post("/api/filterwheel/learn-offsets/cancel", {});
                          } catch (e) {
                            setErr((e as Error).message);
                          } finally {
                            setStopping(false);
                          }
                        }}>
                        {stopping ? "Stopping…" : "Stop"}
                      </button>
                    </>
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
