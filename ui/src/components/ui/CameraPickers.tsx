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
import type { JSX } from "react";
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

/**
 * THE FILTER IN THE LIGHT PATH — always named, never an abstraction.
 *
 * The face used to read "as-is" whenever nothing was pinned, which is a
 * statement about our own bookkeeping, not about the rig: the wheel is a
 * physical object and it always has SOME filter in the beam (operator,
 * 2026-08-07 21:27 — "filt should never show as-is, it should show the
 * current filter"). So the face answers the only question worth asking —
 * what will the next frame shoot through? — which is the pinned filter when
 * one is pinned, and the wheel's actual current slot when none is.
 *
 * `value` is the PIN (null = not pinned). The distinction still exists and
 * still matters (a pinned filter is re-asserted before every frame, an
 * unpinned one follows whatever the wheel is doing) — it is carried by the
 * menu's selection dot and the "follow the wheel" row, not by hiding the
 * filter's name.
 */
export function FilterPicker({
  value, onPick, className = "", align = "left",
  excludeOpaque = true, disabled = false, disabledReason, onBlocked,
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
}): JSX.Element | null {
  const wheel = useStatus()?.filterwheel;
  const names = wheel?.names ?? [];
  if (names.length === 0) return null;

  const usable = names
    .map((n, i) => ({ name: n, i }))
    .filter(({ name, i }) => name && !(excludeOpaque && (wheel?.opaque?.[i] ?? false)));

  const currentName = typeof wheel?.position === "number"
    ? names[wheel.position] ?? null : null;
  // What the NEXT frame shoots through: the pin if pinned, else what is in
  // the beam right now. Always a real filter name when the wheel can say.
  const face = value ?? currentName ?? "—";

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
   wheel (the guide camera) must not be handed one, and the ring must never
   offer an opaque slot — a solve or focus frame through a carrier with no glass
   is a dark frame. */
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
  filters?: readonly string[];
  /** The filter actually in the beam, so FILT names a real filter rather than
   *  an abstraction (2026-08-07: "as-is" described our bookkeeping, not the
   *  rig). */
  currentFilter?: string | null;
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
      id: "gain", label: "GAIN",
      options: gains.map((g) => ({ id: String(g), label: String(g) })),
      selected: String(p.values.gain),
      onPick: (id) => p.onGain(Number(id)),
    },
  ];
  if (p.onBinning) {
    out.push({
      id: "binning", label: "BIN",
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
  if (p.onFilter && (p.filters?.length ?? 0) > 0) {
    out.push({
      id: "filter", label: "FILT",
      options: (p.filters ?? []).map((f) => ({ id: f, label: f })),
      selected: p.values.filter ?? p.currentFilter ?? undefined,
      onPick: (id) => p.onFilter!(id),
    });
  }
  return out;
}
