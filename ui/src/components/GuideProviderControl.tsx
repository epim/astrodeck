// GuideProviderControl.tsx — the ONE guide-provider control, rendered on two
// screens (UX-02 + #132).
//
// WHY IT IS SHARED. Choosing who autoguides lived only on the Guide view, inside
// `GuideProviderPanel`. Equipment's Tasks section — the screen whose entire job
// is "who runs each task" — could not show it, because its rows come from
// `TASK_CAPS` and each row's options come from `driver.offers.tasks`, and NO
// driver offers a "guide" task. So the fourth pinnable capability was invisible
// exactly where a user goes looking for it, and a real user concluded AstroDeck
// could not guide at all. Surfacing it on Equipment by writing a second copy of
// the control would have been worse than leaving it hidden: two copies of a
// layer-aware write path is precisely the duplication that produced #132 (one
// copy learned about the profile layer, the other kept writing global). Both
// screens therefore render THIS component; only the flex direction differs.
//
// WHY IT IS NOT A <select>. Every other provider row is a dropdown, and this one
// deliberately is not. The guide vocabulary is per-RIG, not per-driver: on a rig
// with no guide camera assigned, "AstroDeck native" cannot run, and the honest
// rendering of that is the house honest-disabled pattern — dim + lock glyph +
// the reason in words, still focusable and still tappable so the reason is
// reachable by touch. A <select> cannot express any of that. Its only tool is
// the native `disabled` attribute, which drops the option out of the
// accessibility tree along with the reason, and the alternative — omitting the
// option — is what taught a user that the product could not guide. The
// vocabulary is at most four values, so a radiogroup of 44px chips is also the
// better tablet control.
//
// WHERE THE SAVE GOES: `lib/providerWrite.ts` decides, `lib/providerSave.ts`
// performs. Neither lives here, so the Tasks rows take the same branch.

import { useEffect, useState, type JSX } from "react";
import { ApiError } from "../api";
import { clearProfileOverrides } from "../api/backends";
import { useConfig, useProviders, useStore } from "../store";
import { accessPhrase, useCanConfigBackend } from "../lib/caps";
import { entryOf, providerKey } from "../lib/effective";
import {
  blockedSelectionNote,
  guideProviderLabel,
  guideProviderRows,
  providerWriteNote,
  providerWriteTarget,
  type GuideOptionRow,
} from "../lib/providerWrite";
import { writeProviderOverride } from "../lib/providerSave";
import { Icon } from "./icons";
import { LockedChip, LOCKED_CLASS } from "./ui";
import { OverrideNote, useClearOverride } from "./OverrideNote";

/** One choice chip. Eligible → a real button. Blocked → dim + lock glyph +
 *  `aria-disabled`, but STILL focusable and still tappable
 *  (`!pointer-events-auto`, the escape LockedChip uses), and pressing it states
 *  the reason instead of doing nothing. The selected chip is marked by a CHECK
 *  GLYPH as well as the accent fill: night mode collapses the palette toward
 *  coral, so "which one is selected" must survive with no hue channel at all. */
function ProviderChip({
  row,
  selected,
  busy,
  onPick,
  onExplain,
}: {
  row: GuideOptionRow;
  selected: boolean;
  busy: boolean;
  onPick: (value: string) => void;
  onExplain: (reason: string) => void;
}): JSX.Element {
  const blocked = !row.eligible;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-disabled={blocked || undefined}
      // The accessible name carries the blocker, so a screen-reader user learns
      // it from the chip alone and does not have to hunt for the note below.
      aria-label={
        blocked && row.reason ? `${row.label} — unavailable: ${row.reason}` : undefined
      }
      className={
        `btn tap !px-2.5 !py-1 text-[11px] inline-flex items-center gap-1.5` +
        (selected ? " btn-accent" : "") +
        (blocked ? ` ${LOCKED_CLASS} !pointer-events-auto` : "")
      }
      onClick={() => {
        if (blocked) return onExplain(row.reason ?? "not available on this rig");
        if (!busy && !selected) onPick(row.value);
      }}
    >
      {/* Both glyphs when both apply: a stored choice that cannot run here is
          selected AND blocked, and dropping the check would leave "which one is
          stored" with no channel but the accent fill — which night mode flattens
          toward the same coral as everything else. */}
      {blocked && <Icon name="lock" size={11} aria-hidden />}
      {selected && <Icon name="check" size={11} aria-hidden />}
      {row.label}
    </button>
  );
}

/**
 * The control. `layout` is a rendering knob and nothing else — the eligibility,
 * the seed, the write-back branch and every sentence are identical on both
 * screens.
 *
 *   "row"     Equipment's Tasks section: label left, chips inline, matching the
 *             three sibling rows' grammar.
 *   "stacked" the Guide view's 300px right column, where an inline label plus a
 *             chip group would wrap into nonsense.
 */
export default function GuideProviderControl({
  label = "Guide",
  layout = "row",
}: {
  label?: string;
  layout?: "row" | "stacked";
}): JSX.Element {
  const config = useConfig();
  const providers = useProviders();
  const canConfig = useCanConfigBackend();
  const showToast = useStore((s) => s.showToast);
  const { clear, clearing, error: clearErr } = useClearOverride();

  // #129: read the WINNING layer. `config.providers.guide` (global) stays
  // underneath purely as the WS `hello` bootstrap fallback — that payload
  // carries no `effective` block, and showing the old, sometimes-wrong value
  // beats showing nothing, but it IS a degradation and is written as one.
  const entry = entryOf<string>(config, providerKey("guide"));
  const seed =
    (typeof entry?.value === "string" && entry.value) ||
    config?.providers?.guide ||
    "auto";
  const [draft, setDraft] = useState(seed);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Re-seed whenever a fresh config lands: our own save, another client's, or a
  // cleared/activated profile — any of which can change the WINNER without
  // touching the value this component last wrote.
  useEffect(() => {
    setDraft(seed);
  }, [seed]);

  const choice = providers?.guide;
  const rows = guideProviderRows(choice?.options, choice?.eligible, draft);

  // The layer this save will land on, and the sentence that says so BEFORE the
  // user commits. Without it, picking a value under a profile pin is the exact
  // action that used to succeed and change nothing.
  const target = providerWriteTarget(entry);
  const writeNote = providerWriteNote(target);

  const persist = async (value: string) => {
    if (busy) return;
    setErr(null);
    setBusy(true);
    setDraft(value); // optimistic — echoed back by the config reload
    try {
      await writeProviderOverride({
        cap: "guide",
        value,
        target,
        // The RAW global block, never the draft: see globalProvidersBody.
        globals: config?.providers,
      });
      showToast(
        "success",
        target.layer === "profile"
          ? `Guide provider saved to profile “${target.profileName ?? "active"}”`
          : "Guide provider saved",
      );
    } catch (e) {
      setDraft(seed); // revert the optimistic edit
      setErr(
        e instanceof ApiError
          ? e.status === 403
            ? `${accessPhrase("config.backend")} required to change the guide provider`
            : e.message
          : e instanceof Error
            ? e.message
            : "couldn't save the guide provider override",
      );
    } finally {
      setBusy(false);
    }
  };

  const blocked = rows.filter((r) => !r.eligible && r.reason);
  const stickyRow = rows.find((r) => r.sticky);
  // The stored choice cannot run here: says so, so the three true-but-apparently
  // contradictory lines around it (badge, resolver reason, override disclosure)
  // read as one situation rather than as a console defect.
  const blockedNote = blockedSelectionNote(rows, draft, choice?.kind);

  const chips = (
    <div
      role="radiogroup"
      aria-label="Guide provider override"
      className="flex flex-wrap gap-1.5 min-w-0"
    >
      {rows.map((r) => (
        <ProviderChip
          key={r.value}
          row={r}
          selected={r.value === draft}
          busy={busy}
          onPick={(v) => void persist(v)}
          onExplain={(reason) => showToast("info", reason)}
        />
      ))}
    </div>
  );

  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <div
        className={
          layout === "row"
            ? "flex items-start gap-3 flex-wrap"
            : "flex flex-col gap-1"
        }
      >
        <span className={layout === "row" ? "label w-28 shrink-0 pt-1.5" : "label"}>
          {label}
        </span>
        {canConfig ? (
          chips
        ) : (
          // No capability: show the VALUE through the house locked stand-in
          // rather than a dead group of chips (UX #24).
          <LockedChip
            reason={`Guide provider override — ${accessPhrase("config.backend")} required`}
            className="btn"
          >
            {guideProviderLabel(draft)}
          </LockedChip>
        )}
      </div>

      <div className={layout === "row" ? "flex flex-col gap-1 sm:pl-[8rem]" : "flex flex-col gap-1"}>
        {/* Every blocked option's reason, in words, for the sighted user who
            never presses the chip. NOT prefixed with the chip label: each server
            reason already NAMES its provider ("AstroDeck native needs a guide
            camera assigned and connected"), and prefixing produced the stutter
            "AstroDeck native — AstroDeck native needs…". That requirement is
            pinned on the server side, where the sentences are written. */}
        {blocked.map((r) => (
          <p
            key={r.value}
            className="text-[11px] text-dim leading-snug flex items-start gap-1.5"
          >
            <Icon name="lock" size={11} className="mt-0.5 shrink-0" aria-hidden />
            <span>{r.reason}</span>
          </p>
        ))}
        {stickyRow?.reason && (
          <p className="text-[11px] text-dim leading-snug">{stickyRow.reason}</p>
        )}
        {blockedNote && (
          <p className="text-[11px] text-dim leading-snug">{blockedNote}</p>
        )}
        {/* The resolver's own sentence: what is ACTUALLY serving this rig right
            now, which is not always what is pinned (a degraded override reports
            the guider that really ran). */}
        {choice?.reason && (
          <p className="text-[11px] text-dim leading-snug">{choice.reason}</p>
        )}
        {writeNote && (
          <p className="text-[11px] text-warn leading-snug flex items-start gap-1.5">
            <Icon name="alert" size={11} className="mt-0.5 shrink-0" aria-hidden />
            <span>{writeNote}</span>
          </p>
        )}
        <OverrideNote
          entry={entry}
          format={(v) => guideProviderLabel(String(v))}
          clearLabel="Clear the profile pin"
          clearHint="Clearing it hands this row back to the global setting."
          clearing={clearing}
          error={clearErr}
          onClear={
            canConfig && entry?.profile_id
              ? () =>
                  void clear(() =>
                    clearProfileOverrides(entry.profile_id as string, {
                      providers: ["guide"],
                    }),
                  )
              : undefined
          }
          className="ml-0"
        />
        {/* A fact about WHEN, which nothing on screen can show: the switch is
            applied by hub.select_guide_provider at guiding START and never
            swaps a running guider. */}
        <p className="text-[11px] text-faint leading-snug">
          A switch takes effect at the next guiding start — it never swaps a
          guider that is already running.
        </p>
        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} aria-hidden />
            Read-only - changing the guide provider needs{" "}
            {accessPhrase("config.backend")}.
          </p>
        )}
        {err && (
          <p className="text-[11px] text-bad inline-flex items-start gap-1.5">
            <Icon name="alert" size={11} className="mt-0.5 shrink-0" aria-hidden />
            {err}
          </p>
        )}
      </div>
    </div>
  );
}
