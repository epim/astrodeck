/* CameraPickers — ONE set of camera-setting pickers, shared by every screen
   that shoots a frame (2026-08-07 21:27).

   Before this, three screens grew three vocabularies for the same four
   controls: Align cycled badges, Focus cycled badges with a different preset
   list, Guide had none at all. A user who learns "tap EXP, pick 5s" on one
   screen should not meet a different idiom on the next, and a preset list
   that is right for a solve frame is right for a focus frame — the camera is
   the same camera.

   So: the PRESETS live here, the CONTROLS live here, and a screen supplies
   only what is genuinely its own — which camera, what it currently uses, and
   what to do when the value changes. Per-device overrides exist (a guide
   camera has no business offering a 5-minute frame) and are passed in, not
   forked.

   Built on PickerButton, so they inherit its viewport-clamping and its grid
   layout for free. */
import { useRef, type JSX } from "react";
import PickerButton from "./PickerButton";
import type { DialCategory } from "./CameraDial";
import { useStatus } from "../../store";

/** Every exposure a frame-shooting screen offers, in seconds.
 *  The long tail is not decoration: an OSC camera behind a narrowband filter
 *  legitimately needs minutes per plate-solve frame (operator, 2026-08-07).
 *  The sub-second pair is where the solve defaults live. */
export const EXPOSURE_PRESETS_S = [
  0.3, 0.5, 1, 2, 5, 10, 15, 30, 60, 90, 120, 180, 300,
];
/** A guide camera's own ceiling: past ~10 s the loop is no longer guiding,
 *  it is drifting between corrections. Same control, honest range. */
export const GUIDE_EXPOSURE_PRESETS_S = [0.2, 0.5, 1, 1.5, 2, 3, 5, 10];
export const GAIN_PRESETS = [0, 50, 100, 120, 150, 200, 250, 300, 400, 500];
export const BIN_PRESETS = [1, 2, 3, 4];

/** Seconds → the face a human reads. Minutes once it stops being a count. */
export const fmtExposure = (s: number): string =>
  s < 60 ? `${s}s` : Number.isInteger(s / 60) ? `${s / 60}m` : `${(s / 60).toFixed(1)}m`;

export function ExposurePicker({
  value, onPick, presets = EXPOSURE_PRESETS_S, label = "EXP",
  className = "", disabled = false, disabledReason, onBlocked, children,
}: {
  value: number;
  onPick: (seconds: number) => void;
  presets?: readonly number[];
  label?: string;
  className?: string;
  disabled?: boolean;
  disabledReason?: string | null;
  onBlocked?: (reason: string) => void;
  /** Extra panel content — the custom-value box, where a screen wants one. */
  children?: React.ReactNode;
}): JSX.Element {
  return (
    <PickerButton
      label={label}
      summary={fmtExposure(value)}
      className={className}
      columns={3}
      disabled={disabled}
      disabledReason={disabledReason}
      onBlocked={onBlocked}
      options={presets.map((s) => ({ id: String(s), label: fmtExposure(s) }))}
      selected={[String(value)]}
      onPick={(id) => onPick(Number(id))}
    >
      {children}
    </PickerButton>
  );
}

export function GainPicker({
  value, onPick, presets = GAIN_PRESETS, className = "",
  disabled = false, disabledReason, onBlocked,
}: {
  value: number;
  onPick: (gain: number) => void;
  presets?: readonly number[];
  className?: string;
  disabled?: boolean;
  disabledReason?: string | null;
  onBlocked?: (reason: string) => void;
}): JSX.Element {
  return (
    <PickerButton
      label="GAIN"
      summary={String(value)}
      className={className}
      columns={3}
      disabled={disabled}
      disabledReason={disabledReason}
      onBlocked={onBlocked}
      options={presets.map((g) => ({ id: String(g), label: String(g) }))}
      selected={[String(value)]}
      onPick={(id) => onPick(Number(id))}
    />
  );
}

export function BinningPicker({
  value, onPick, max = 4, className = "",
  disabled = false, disabledReason, onBlocked,
}: {
  value: number;
  onPick: (bin: number) => void;
  /** The camera's own max_bin, when the caller knows it. */
  max?: number;
  className?: string;
  disabled?: boolean;
  disabledReason?: string | null;
  onBlocked?: (reason: string) => void;
}): JSX.Element {
  const bins = BIN_PRESETS.filter((b) => b <= max);
  return (
    <PickerButton
      label="BIN"
      summary={`${value}×${value}`}
      className={className}
      columns={2}
      disabled={disabled}
      disabledReason={disabledReason}
      onBlocked={onBlocked}
      options={bins.map((b) => ({ id: String(b), label: `${b}×${b}` }))}
      selected={[String(value)]}
      onPick={(id) => onPick(Number(id))}
    />
  );
}

/** The sensor's ADU pedestal.
 *
 *  A CONTINUUM, not a list — its useful values depend on the sensor and it is
 *  set once and forgotten — so this is one field and a Set button rather than a
 *  preset grid, matching the radial dial's `kind: "entry"` ring.
 *
 *  It exists because offset was unreachable on three of the five surfaces that
 *  claimed to offer it: the Align bar rendered four pickers and this was not
 *  one of them, so the ONLY way to change a solve frame's offset was the radial
 *  dial over the reticle — which hides itself entirely below 124 px of stage.
 */
export function OffsetPicker({
  value, onPick, className = "", align = "left",
  disabled = false, disabledReason, onBlocked,
}: {
  value: number;
  onPick: (offset: number) => void;
  className?: string;
  align?: "left" | "right";
  disabled?: boolean;
  disabledReason?: string | null;
  onBlocked?: (reason: string) => void;
}): JSX.Element {
  const box = useRef<HTMLInputElement>(null);
  const apply = () => {
    const v = Number(box.current?.value ?? "");
    if (!Number.isFinite(v) || v < 0 || v > 255) return;
    onPick(Math.round(v));
    if (box.current) box.current.value = "";
    // PickerButton owns its open state and listens for Escape at the window —
    // the one way a child can ask the panel to close.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  };
  return (
    <PickerButton
      label="OFFS"
      summary={String(value)}
      className={className}
      align={align}
      disabled={disabled}
      disabledReason={disabledReason}
      onBlocked={onBlocked}
      options={[]}
      selected={[]}
      onPick={() => {}}
    >
      <div className="flex items-center gap-1.5 min-w-[190px]">
        <input
          ref={box}
          className="field flex-1 min-w-0 mono text-xs"
          inputMode="numeric"
          placeholder={String(value)}
          aria-label="Offset in ADU"
          defaultValue=""
          onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
        />
        <button type="button" className="btn min-h-[40px] text-[11px] !px-3"
          onClick={apply}>
          Set
        </button>
      </div>
      <p className="text-[10px] text-faint mt-1 max-w-[190px] leading-snug">
        ADU pedestal — set once per camera.
      </p>
    </PickerButton>
  );
}

/** The FILT face, given the wheel's actual slot and this scope's pin.
 *
 *  ONE PHYSICAL RESOURCE, so a pin is not a setting — it is an INTENT to move
 *  the wheel, and until something acts on it the wheel is somewhere else. The
 *  face renders that as a transition (`Oiii → R`) and never as `R` alone.
 *
 *  Exported so the radial dial's summary and the test can read the same rule.
 */
export function filterFace(
  current: string | null, pin: string | null,
): { face: string; pending: boolean } {
  if (!pin) return { face: current ?? "—", pending: false };
  if (pin === current) return { face: current, pending: false };
  return { face: `${current ?? "—"} → ${pin}`, pending: true };
}

/**
 * THE FILTER IN THE LIGHT PATH — always named, never an abstraction, and never
 * a claim about where the wheel is that the wheel does not make.
 *
 * The face used to read "as-is" whenever nothing was pinned, which is a
 * statement about our own bookkeeping, not about the rig (operator,
 * 2026-08-07 21:27). It then read the PIN, unconditionally and unlabelled —
 * `value ?? currentName` — which is worse: on 2026-08-08 the operator set FILT
 * to R on the Align screen, where a pin is applied only inside a solve frame,
 * and this face said "R" over a wheel parked on Oiii. Capture, reading the
 * wheel, said Oiii and was right. Two surfaces, one wheel, two answers.
 *
 * So the face carries BOTH, always in the same order: what is in the beam now,
 * then what this scope will move it to. A pin that has not yet moved the wheel
 * must never look like the wheel.
 */
export function FilterPicker({
  value, onPick, className = "", align = "left",
  excludeOpaque = true, disabled = false, disabledReason, onBlocked,
  pendingNote,
}: {
  value: string | null;
  onPick: (name: string | null) => void;
  className?: string;
  align?: "left" | "right";
  /** Opaque slots are carriers with no glass — a solve or focus frame through
   *  one is a dark frame. Excluded everywhere except a deliberate dark. */
  excludeOpaque?: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
  onBlocked?: (reason: string) => void;
  /** WHEN this scope's pin reaches the wheel, in the operator's words — e.g.
   *  "applied when the alignment runs". Shown whenever the pin and the wheel
   *  disagree, because "the wheel has not moved yet" is inferable from the
   *  arrow but "and here is what would move it" is not. */
  pendingNote?: string;
}): JSX.Element | null {
  const wheel = useStatus()?.filterwheel;
  const names = wheel?.names ?? [];
  if (names.length === 0) return null;

  const usable = names
    .map((n, i) => ({ name: n, i }))
    .filter(({ name, i }) => name && !(excludeOpaque && (wheel?.opaque?.[i] ?? false)));

  const currentName = typeof wheel?.position === "number"
    ? names[wheel.position] ?? null : null;
  const { face, pending } = filterFace(currentName, value);

  return (
    <PickerButton
      label="FILT"
      summary={face}
      className={className}
      align={align}
      columns={2}
      disabled={disabled}
      disabledReason={disabledReason}
      onBlocked={onBlocked}
      options={usable.map(({ name }) => ({
        id: name,
        label: name,
        hint: name === currentName ? "in the light path now" : undefined,
      }))}
      selected={value ? [value] : []}
      onPick={(id) => onPick(id)}
    >
      {pending && pendingNote && (
        <p className="col-span-2 text-[10px] text-warn leading-snug px-1 pt-1">
          The wheel is on {currentName ?? "an unknown slot"}. {pendingNote}
        </p>
      )}
      {/* Unpinning is a real intent — "stop re-asserting a filter, just use
          whatever the wheel is on" — so it keeps a row. It is NOT the face. */}
      <div className="col-span-2 border-t border-line mt-1 pt-1.5">
        <button
          type="button"
          className={`w-full min-h-[40px] rounded border text-[11px] px-2 ${
            value == null
              ? "border-accent text-accent bg-accent/10"
              : "border-line text-dim"}`}
          onClick={() => {
            onPick(null);
            // Closes like any other choice: picking a filter dismisses the
            // panel, and this IS a choice about the filter. PickerButton
            // owns its open state and listens for Escape at the window —
            // the one way a child can ask it to close.
            window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
          }}
        >
          follow the wheel{currentName ? ` (${currentName})` : ""}
        </button>
      </div>
    </PickerButton>
  );
}

/* ======================================================= the radial dial's menu

   The SAME settings, as the fan-out dial's two rings (components/ui/CameraDial).
   One builder, so a surface that shows the dial and a surface that shows the
   flat pickers cannot drift into offering different presets or different words
   for the same thing — which is exactly what happened before CameraPickers
   existed, when three screens grew three vocabularies for four controls.

   `filters` is passed in rather than read from the store here: a caller with no
   wheel (the guide camera) must not be handed one. What the builder does NOT
   leave to the caller any more is DROPPING THE OPAQUE SLOTS — a focus or solve
   frame through a carrier with no glass measures nothing at every point, which
   on 2026-08-08 was not a slow failure but a hang (fourteen re-exposures at one
   focuser position). `FilterPicker` had always dropped them internally;
   `PolarView` dropped them at the call site; `FocusView` did not, and offered a
   blackout slot as a sweep filter. One of three callers getting it right is
   what a caller-side rule looks like from the inside. */
export interface CameraDialValues {
  exposure_s: number;
  gain: number;
  binning?: number;
  offset?: number;
  filter?: string | null;
}

export function cameraDialCategories(p: {
  values: CameraDialValues;
  exposures?: readonly number[];
  gains?: readonly number[];
  maxBin?: number;
  /** The wheel's FULL slot list, opaque slots included — the builder drops
   *  them. Pass `status.filterwheel.names` straight through. */
  filters?: readonly (string | null | undefined)[];
  /** Per-slot blackout flags, index-aligned with `filters`
   *  (`status.filterwheel.opaque`). */
  opaqueSlots?: readonly boolean[];
  /** REQUIRED: the filter actually in the beam right now. Not optional, because
   *  `values.filter` is a PIN — an intent to move the wheel — and a dial that
   *  renders a pin without the wheel is the 2026-08-08 R-vs-Oiii defect. A
   *  caller with no wheel passes null and says so. */
  currentFilter: string | null;
  onExposure: (s: number) => void;
  onGain: (g: number) => void;
  onBinning?: (b: number) => void;
  onOffset?: (o: number) => void;
  onFilter?: (name: string | null) => void;
}): DialCategory[] {
  const exposures = p.exposures ?? EXPOSURE_PRESETS_S;
  const gains = p.gains ?? GAIN_PRESETS;
  const bins = BIN_PRESETS.filter((b) => b <= (p.maxBin ?? 4));
  const out: DialCategory[] = [
    {
      id: "exposure", label: "EXP", icon: "capture",
      options: exposures.map((s) => ({ id: String(s), label: fmtExposure(s) })),
      selected: String(p.values.exposure_s),
      onPick: (id) => p.onExposure(Number(id)),
    },
    {
      // Every hub needs its own glyph: the ring renders the icon at the centre
      // and falls back to label text without one, so three of the four hubs
      // read as unfinished beside EXP's.
      id: "gain", label: "GAIN", icon: "brightness",
      options: gains.map((g) => ({ id: String(g), label: String(g) })),
      selected: String(p.values.gain),
      onPick: (id) => p.onGain(Number(id)),
    },
  ];
  if (p.onBinning) {
    out.push({
      id: "binning", label: "BIN", icon: "grid",
      options: bins.map((b) => ({ id: String(b), label: `${b}×${b}` })),
      selected: String(p.values.binning ?? 1),
      onPick: (id) => p.onBinning!(Number(id)),
    });
  }
  if (p.onOffset) {
    // A CONTINUUM, not a list: offset's useful values depend on the sensor and
    // it is set once and then forgotten, so a preset ring would be both wrong
    // and in the way. `kind: "entry"` renders one field and a Set button.
    out.push({
      id: "offset", label: "OFFS", kind: "entry",
      value: String(p.values.offset ?? 30),
      placeholder: "offset",
      hint: "ADU pedestal — set once per camera.",
      onSubmit: (text) => {
        const v = Number(text);
        if (Number.isFinite(v) && v >= 0) p.onOffset!(Math.round(v));
      },
    });
  }
  // OPAQUE SLOTS ARE DROPPED HERE, once, for every caller. A blackout slot is
  // a carrier with no glass: a focus sweep through one measures nothing at
  // every position, and the engine only advances when a measurement lands — so
  // it is a hang, not a bad result (fourteen re-exposures at one position on
  // the rig, 2026-08-08). Blank slot names go too: an unnamed slot is not an
  // option, it is a gap in the wheel's configuration.
  const usable = (p.filters ?? [])
    .map((name, i) => ({ name, i }))
    .filter(({ name, i }) => !!name && !(p.opaqueSlots?.[i] ?? false))
    .map(({ name }) => name as string);
  if (p.onFilter && usable.length > 0) {
    out.push({
      id: "filter", label: "FILT", icon: "frame",
      options: usable.map((f) => ({ id: f, label: f })),
      // The pin when pinned, else the wheel's real slot — so the ring's mark
      // is never on a filter that is neither where the wheel is nor where it
      // is going.
      selected: p.values.filter ?? p.currentFilter ?? undefined,
      onPick: (id) => p.onFilter!(id),
    });
  }
  return out;
}
