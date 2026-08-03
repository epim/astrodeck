// TasksPanel.tsx — the Equipment tab's TASKS section (spec §4.1): who runs
// autofocus / polar align / plate solve / guiding. Same row grammar as the
// device slots: a dropdown of concrete eligible providers (THE ONE RULE, task
// edition — enabled + reachable + actually offering the task) around an "Auto"
// default, the resolved ProviderBadge, and the resolver's reason line
// permanently visible (spec §5: "why is this on NINA right now" always has an
// answer).
//
// Values are driver ids (implicit "astrodeck"/"astap"/"sim" or configured
// "nina-xxxx"); the server validates writes against the registry (422) and
// keeps accepting the LEGACY "backend" alias — shown here as a sticky option
// when it is the stored value, never offered fresh.
//
// #129 — THE BUG THIS PANEL CAUSED. The dropdown used to seed from
// `config.providers`, the GLOBAL AppConfig block. But the ACTIVE PROFILE's
// `providers` dict beats it inside `providers.override_with_layer`, so with a
// profile pinned to the built-in simulator this control displayed "AstroDeck
// native" — confidently, plausibly, and wrongly — for twelve days, while the
// polar aligner returned numbers nothing had measured. Nothing on screen
// distinguished the two layers, because the value is the only thing that was
// ever shown and both layers hold values of the same shape.
//
// The select now shows the value the rig IS RUNNING (`config.effective`), and
// when a profile is what supplies it the row says so in permanent text, names
// the profile, prints the global value being shadowed, and offers the way out.
//
// #132 — THE SECOND HALF, and why the pinned select is no longer inert. Under a
// profile pin this control used to be DISABLED, because the only write route
// (`POST /api/config/providers`) targets the GLOBAL block that the profile then
// beats: leaving it live would have let a user pick a provider, watch the save
// succeed, and change nothing that runs. Disabling it was the honest option
// available at the time, and it was still a dead end — "clear the pin, then
// choose" makes the user destroy the per-rig setting in order to edit it. There
// is now a route that writes the active profile's providers entry, so the rule
// is: WRITE BACK TO THE LAYER YOU ARE READING FROM. The row stays editable, and
// says in permanent text which layer the save will land on before the user
// commits (lib/providerWrite.ts owns that decision; lib/providerSave.ts performs
// the write, so this panel and the Guide view cannot take different branches).
//
// UX-02 — the fourth row. `guide` is a real pinnable capability whose control
// existed only on the Guide view, i.e. nowhere on the screen whose whole subject
// is task routing. It cannot come from this file's TASK_CAPS/`offers.tasks`
// mechanism because NO driver offers a "guide" task; its vocabulary is per-RIG
// and comes from the server (`status.providers.guide.options`). It is therefore
// rendered by the shared GuideProviderControl, the SAME component the Guide view
// renders — not a second copy.
import { useEffect, useState, type JSX } from "react";
import type { DriverInfo, ProvidersConfig } from "../../types";
import { clearProfileOverrides } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useProviders } from "../../store";
import { accessPhrase, useCanConfigBackend } from "../../lib/caps";
import { eligibleTaskDrivers, TASK_CAPS, type TaskCap } from "../../lib/equipment";
import {
  effectiveProviders,
  entryOf,
  isProfileOverride,
  overriddenCaps,
  providerKey,
} from "../../lib/effective";
import {
  DEFAULT_PROVIDERS,
  providerWriteNote,
  providerWriteTarget,
} from "../../lib/providerWrite";
import { writeProviderOverride } from "../../lib/providerSave";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";
import { ProviderBadge } from "../ProviderBadge";
import { OverrideNote, useClearOverride } from "../OverrideNote";
import GuideProviderControl from "../GuideProviderControl";

// Re-exported from its real home in lib/providerWrite.ts, which is where the
// three surfaces that spread it can reach it without importing a panel.
export { DEFAULT_PROVIDERS };

/** Human label for a stored provider value, used in the override sentence where
 *  the raw id ("nina-1a2b") would mean nothing. Falls back to the id, which is
 *  still an answer — an unresolvable id is exactly the case where the user most
 *  needs to see the literal string the profile is carrying. */
const providerLabel = (value: unknown, drivers: DriverInfo[]): string => {
  if (typeof value !== "string" || !value) return "not set";
  if (value === "auto") return "Auto (best available)";
  if (value === "backend") return "Backend (legacy)";
  return drivers.find((d) => d.id === value)?.label ?? value;
};

export default function TasksPanel({
  drivers,
  busy = false,
}: {
  drivers: DriverInfo[];
  busy?: boolean;
}): JSX.Element {
  const config = useConfig();
  const resolved = useProviders();
  const canConfig = useCanConfigBackend();
  const { clear, clearing, error: clearErr } = useClearOverride();

  // The seed is the WINNING layer. `config.providers` (global) stays underneath
  // as the degradation path for the WS `hello` bootstrap, which carries no
  // `effective` block — showing the old, sometimes-wrong value is better than
  // showing an empty select, but it is a degradation and it is written as one.
  const seed: ProvidersConfig = {
    ...DEFAULT_PROVIDERS,
    ...(config?.providers ?? {}),
    ...effectiveProviders(config),
  };
  const [draft, setDraft] = useState<ProvidersConfig>(seed);
  const [busyCap, setBusyCap] = useState<TaskCap | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Re-seed whenever a fresh config lands (our own save, another client's, or a
  // cleared profile pin — which changes the winner without touching global).
  useEffect(() => {
    setDraft(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed.autofocus, seed.polar_align, seed.solve]);

  // Every capability the active profile pins, INCLUDING `guide` — it now has a
  // row of its own below, so counting it here no longer produces a banner
  // announcing a pin with nothing on this screen to point at.
  const pinnedCaps = overriddenCaps(config);

  const persist = async (cap: TaskCap, value: string) => {
    if (busyCap) return;
    setErr(null);
    setBusyCap(cap);
    setDraft({ ...draft, [cap]: value }); // optimistic — echoed back by the reload
    try {
      await writeProviderOverride({
        cap,
        value,
        // #132: the layer that is actually in force. A profile pin is written
        // back INTO the profile; anything else writes global, as before.
        target: providerWriteTarget(entryOf(config, providerKey(cap))),
        // The RAW global block, deliberately NOT `draft`. The draft is seeded
        // from the EFFECTIVE values, so POSTing it would copy every
        // profile-won value down into global config as a side effect of
        // editing one unrelated row — invisible until the pin was later
        // cleared, at which point the rig fell back to a value nobody chose.
        globals: config?.providers,
      });
    } catch (e) {
      setDraft(seed); // revert the optimistic edit
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "config.backend required to change task routing"
            : e.message
          : e instanceof Error
            ? e.message
            : "couldn't save task override";
      setErr(msg);
    } finally {
      setBusyCap(null);
    }
  };

  const unpin = (cap: TaskCap, profileId: string) =>
    void clear(() => clearProfileOverrides(profileId, { providers: [cap] }));

  return (
    <Panel
      title="Tasks"
      right={
        <InfoDot
          label="About task routing"
          content="Pick who runs each task. Auto chooses the best available for the connected rig; the options are the drivers that actually offer the task right now. The line under each row explains the current resolution. A row marked PROFILE is pinned by the equipment profile you activated — editing that row rewrites the pin itself, so the change reaches the rig; clearing the pin hands the row back to the global setting."
        />
      }
    >
      <div className="flex flex-col gap-2.5">
        {pinnedCaps.length > 0 && (
          // One line at the top so the state is legible before any row is read:
          // "this rig is running on profile pins" is a fact about the whole
          // panel, and finding it out row by row is how it stayed unnoticed.
          <p className="text-[11px] text-warn leading-snug border border-line2 bg-raise px-2.5 py-2">
            <Icon name="alert" size={11} className="inline-block mr-1.5 -mt-px" />
            {pinnedCaps.length === 1 ? "One task is" : `${pinnedCaps.length} tasks are`}{" "}
            routed by the active equipment profile, not by the global settings.
            The pinned rows show what is actually running, and editing one
            rewrites the profile&rsquo;s pin rather than the global setting.
          </p>
        )}
        {TASK_CAPS.map(({ cap, label }) => {
          const eligible = eligibleTaskDrivers(cap, drivers);
          const value = draft[cap];
          const choice = resolved?.[cap];
          const inList = value === "auto" || eligible.some((d) => d.id === value);
          const stale = drivers.find((d) => d.id === value);
          const entry = entryOf(config, providerKey(cap));
          const pinned = isProfileOverride(entry);
          const writeNote = providerWriteNote(providerWriteTarget(entry));
          return (
            <div key={cap} className="border border-line bg-bg/60 px-3 py-2.5">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="label w-28 shrink-0">{label}</span>
                <select
                  className="field !py-1 max-w-[220px]"
                  value={value}
                  // #132: NO LONGER disabled under a profile pin. The save is
                  // routed to the winning layer, so a pinned row is editable and
                  // the edit reaches the rig; `writeNote` below says where it
                  // lands before the user commits.
                  disabled={!canConfig || busy || busyCap === cap}
                  onChange={(e) => void persist(cap, e.target.value)}
                  aria-label={`${label} provider override${pinned ? " (pinned by the active profile — saving rewrites the pin)" : ""}`}
                >
                  <option value="auto">Auto (best available)</option>
                  {eligible.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.label}
                    </option>
                  ))}
                  {/* sticky (spec §5): a stored value no longer offered stays
                      listed (legacy "backend" alias, or a driver that went
                      unreachable/was deleted) instead of silently vanishing */}
                  {!inList && (
                    <option value={value}>
                      {value === "backend"
                        ? "Backend (legacy)"
                        : (stale?.label ?? value)}
                    </option>
                  )}
                </select>
                <div className="flex-1" />
                <ProviderBadge cap={cap} />
              </div>
              {choice?.reason && (
                <p className="text-[11px] text-dim mt-1.5 pl-[8rem] leading-snug">
                  {choice.reason}
                </p>
              )}
              {writeNote && (
                // The one fact a user cannot get any other way, and the one whose
                // absence made the original bug feel like being ignored: where
                // this save is about to go. The alert glyph carries the emphasis
                // on a non-hue channel, for night mode.
                <p className="text-[11px] text-warn mt-1 pl-0 sm:pl-[8rem] leading-snug flex items-start gap-1.5">
                  <Icon name="alert" size={11} className="mt-0.5 shrink-0" aria-hidden />
                  <span>{writeNote}</span>
                </p>
              )}
              <OverrideNote
                entry={entry}
                format={(v) => providerLabel(v, drivers)}
                clearHint="Clearing it hands this row back to the global setting."
                clearLabel="Clear the profile pin"
                clearing={clearing}
                error={clearErr}
                onClear={
                  canConfig && entry?.profile_id
                    ? () => unpin(cap, entry.profile_id as string)
                    : undefined
                }
                className="ml-0 sm:ml-[8rem]"
              />
            </div>
          );
        })}
        {/* The fourth capability. Not a TASK_CAPS row: its options are per-RIG
            (a guide camera must be assigned AND connected), not per-driver, so
            it renders through the shared control the Guide view also uses. */}
        <div className="border border-line bg-bg/60 px-3 py-2.5">
          <div className="flex items-start gap-3 flex-wrap">
            <div className="flex-1 min-w-0">
              <GuideProviderControl label="Guiding" layout="row" />
            </div>
            <ProviderBadge cap="guide" />
          </div>
        </div>
        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing task routing needs {accessPhrase("config.backend")}.
          </p>
        )}
        {err && (
          <p className="text-[11px] text-bad inline-flex items-center gap-1.5">
            <Icon name="alert" size={11} /> {err}
          </p>
        )}
      </div>
    </Panel>
  );
}
