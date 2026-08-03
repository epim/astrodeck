// OverrideNote.tsx — the permanent, information-carrying disclosure that a
// control's value is NOT the one the rig is running (#129).
//
// The failure this answers: the active profile's values beat global config, but
// every panel bound itself to global. A polar-align provider pinned to the
// simulator inside a profile rendered as "AstroDeck native" for twelve days,
// because the two layers are byte-identical on screen and nothing named which
// one won. So a badge that only says "overridden" repeats the mistake at one
// remove — it tells the user something is wrong without telling them what is
// running, what would run instead, or how to get back. Every string here names
// the profile AND both values.
//
// Three constraints shape the rendering:
//
//   * PERMANENT TEXT, not a hover title. The resolver's reason strings already
//     existed as `title=` attributes, and this product is used on a tablet in
//     the dark where hover does not exist. Nobody ever read them.
//   * NON-HUE channel. Night mode collapses the palette toward coral, so the
//     override chip is distinguished by BORDER STYLE (dashed = a profile pin,
//     dotted = filled by the camera) and a leading word, never by color.
//   * A WAY OUT. `onClear` removes the pin server-side; without it the
//     disclosure is a dead end, since profiles have no editor.

import { useState, type JSX } from "react";
import type { EffectiveEntry } from "../types";
import {
  describeCameraFill,
  describeOverride,
  isProfileOverride,
  overrideProfileName,
  showValue,
} from "../lib/effective";
import { Icon } from "./icons";

/** The compact marker that sits beside a control. Carries the layer NAME and
 *  the profile it came from — the two facts that were missing — in a chip whose
 *  border style, not its color, is what distinguishes it. */
export function LayerChip({
  entry,
  who: showWho = true,
  className = "",
}: {
  entry: EffectiveEntry | null;
  /** Include the profile's name. Off for the per-field chips in a panel that
   *  has already named it once at the top — seven repetitions of the same name
   *  is how a marker stops being read. */
  who?: boolean;
  className?: string;
}): JSX.Element | null {
  if (!entry) return null;
  if (entry.layer === "profile") {
    const who = overrideProfileName(entry);
    return (
      <span
        className={`layer-chip layer-chip-profile ${className}`}
        // The chip is the summary; the sentence under the control is the
        // payload. aria-label always carries the profile even when the visible
        // chip does not, so a screen reader gets the "which profile" answer
        // from the chip alone.
        aria-label={`Pinned by profile ${who ?? "unknown"}`}
      >
        <span aria-hidden>PROFILE</span>
        {showWho && who && (
          <span className="layer-chip-who" aria-hidden>
            {who}
          </span>
        )}
      </span>
    );
  }
  if (entry.layer === "camera") {
    return (
      <span
        className={`layer-chip layer-chip-camera ${className}`}
        aria-label="Supplied by the connected camera"
      >
        <span aria-hidden>CAMERA</span>
      </span>
    );
  }
  return null;
}

/**
 * The full disclosure: chip + sentence + (optionally) the way out.
 *
 * Renders NOTHING when the key is not overridden, so callers can drop it into
 * every row unconditionally. `format` renders a raw layer value for display
 * (units, rounding); without it, values print bare.
 *
 * `clearLabel`/`onClear` are the escape hatch. They are deliberately per-CALLER
 * rather than baked in, because the granularity differs: a provider pin can be
 * cleared one capability at a time, while an optics block wins WHOLE and can
 * only be cleared whole.
 */
export function OverrideNote({
  entry,
  format,
  onClear,
  clearLabel = "Clear this pin",
  clearing = false,
  clearHint,
  error,
  className = "",
}: {
  entry: EffectiveEntry | null;
  format?: (v: unknown) => string;
  onClear?: () => void;
  clearLabel?: string;
  clearing?: boolean;
  /** One extra sentence saying what clearing will make happen. Optional because
   *  on some rows the consequence is already obvious from the sentence above. */
  clearHint?: string;
  error?: string | null;
  className?: string;
}): JSX.Element | null {
  const profileText = describeOverride(entry, format);
  const cameraText = describeCameraFill(entry, format);
  const text = profileText ?? cameraText;
  if (!text) return null;

  return (
    <div className={`ovr-note ${className}`}>
      <div className="flex items-start gap-2 flex-wrap">
        <LayerChip entry={entry} className="mt-px shrink-0" />
        <p className="text-[11px] text-dim leading-snug min-w-0 flex-1">
          {text}
          {clearHint && onClear ? ` ${clearHint}` : ""}
        </p>
      </div>
      {onClear && isProfileOverride(entry) && (
        <div className="flex items-center gap-2 flex-wrap mt-1.5">
          <button
            type="button"
            className="btn !py-1 !px-2 text-[11px] inline-flex items-center gap-1.5"
            onClick={onClear}
            disabled={clearing}
          >
            <Icon name="x" size={11} />
            {clearing ? "Clearing…" : clearLabel}
          </button>
        </div>
      )}
      {error && (
        <p className="text-[11px] text-bad inline-flex items-center gap-1.5 mt-1">
          <Icon name="alert" size={11} className="shrink-0" />
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * The COMPACT form, for a panel that already carries the full explanation once
 * at the top (the Imaging train, whose seven fields all come from one profile
 * block — seven copies of the same paragraph would be noise, and noise is what
 * gets skipped).
 *
 * Renders only when it has something the user cannot already see:
 *   - a profile pin whose value DIFFERS from the field it sits under, i.e. the
 *     input is showing one number and the rig is running another;
 *   - a camera-supplied value, where the input reads 0 and the rig runs 3.76.
 * A profile pin that matches the field is deliberately silent here — "Running
 * 530 mm" beside an input reading 530 is copy that describes what is on screen.
 *
 * Deliberately terse: three words and a number. The WHY (which profile, what a
 * whole-block swap means, how to undo it) belongs in the one banner above, and
 * restating it under all seven optics fields turned the panel into a wall the
 * eye slides off — which is the same failure as saying nothing.
 */
export function RunningNote({
  entry,
  format,
  className = "",
}: {
  entry: EffectiveEntry | null;
  format?: (v: unknown) => string;
  className?: string;
}): JSX.Element | null {
  if (!entry) return null;
  const running = showValue(entry.value, format);
  const differs = !Object.is(entry.value, entry.config);
  const text =
    entry.layer === "profile" && differs
      ? `Rig runs ${running}`
      : entry.layer === "camera"
        ? `Rig runs ${running}, from the camera`
        : null;
  if (!text) return null;
  return (
    <div className={`ovr-note flex items-start gap-2 flex-wrap ${className}`}>
      <LayerChip entry={entry} who={false} className="mt-px shrink-0" />
      <span className="text-[11px] text-dim leading-snug min-w-0 flex-1">
        {text}
      </span>
    </div>
  );
}

/** Shared clear-the-pin plumbing: one in-flight guard, one error string, and a
 *  config reload so the badges re-derive from the WINNING layer rather than
 *  from an optimistic local guess. The reload is not optional — the whole point
 *  of this feature is that the client must never assert a layer the server has
 *  not confirmed. */
export function useClearOverride(): {
  clear: (fn: () => Promise<unknown>) => Promise<void>;
  clearing: boolean;
  error: string | null;
  reset: () => void;
} {
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clear = async (fn: () => Promise<unknown>) => {
    if (clearing) return;
    setError(null);
    setClearing(true);
    try {
      await fn();
      // Imported lazily so this module stays usable from a plain node test
      // process (the store pulls in the WS client and a browser-only stack).
      const { useStore } = await import("../store");
      await useStore.getState().loadConfig();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "could not clear the profile override",
      );
    } finally {
      setClearing(false);
    }
  };
  return { clear, clearing, error, reset: () => setError(null) };
}

export default OverrideNote;
