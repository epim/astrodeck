// TasksPanel.tsx — the Equipment tab's TASKS section (spec §4.1): who runs
// autofocus / polar align / plate solve. Same row grammar as the device slots:
// a dropdown of concrete eligible providers (THE ONE RULE, task edition —
// enabled + reachable + actually offering the task) around an "Auto" default,
// the resolved ProviderBadge, and the resolver's reason line permanently
// visible (spec §5: "why is this on NINA right now" always has an answer).
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
// The select is DISABLED under a profile pin rather than left editable, and
// that is a deliberate product decision, not an omission: writes here go to
// `POST /api/config/providers`, which is the GLOBAL block, and there is no
// route that writes a profile's providers dict. Leaving the control live would
// let a user pick a provider, watch the save succeed, and change nothing that
// runs — a worse lie than the one being fixed. Clear the pin, then choose.
import { useEffect, useState, type JSX } from "react";
import type { DriverInfo, ProvidersConfig } from "../../types";
import { clearProfileOverrides, setProvidersConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useProviders, useStore } from "../../store";
import { accessPhrase, useCanConfigBackend } from "../../lib/caps";
import { eligibleTaskDrivers, TASK_CAPS, type TaskCap } from "../../lib/equipment";
import {
  effectiveProviders,
  entryOf,
  isProfileOverride,
  overriddenCaps,
  providerKey,
} from "../../lib/effective";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";
import { ProviderBadge } from "../ProviderBadge";
import { OverrideNote, useClearOverride } from "../OverrideNote";

export const DEFAULT_PROVIDERS: ProvidersConfig = {
  autofocus: "auto",
  polar_align: "auto",
  solve: "auto",
  // Not one of this panel's TASK_CAPS rows (guide eligibility comes from
  // connected devices, not driver task offers — see GuideView's own
  // provider-switch row, P5-T1); included here only so this literal keeps
  // satisfying ProvidersConfig and the profile save/load round-trip below
  // (EquipmentView doSaveProfile/doLoadProfile spread the WHOLE providers
  // object) carries the guide override for free.
  guide: "auto",
};

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

  // Only the caps this panel actually renders. `guide` is a fourth pinnable
  // capability but lives in GuideView, and counting it here would produce a
  // banner announcing a pin with no row to point at.
  const pinnedCaps = overriddenCaps(config).filter((c) =>
    TASK_CAPS.some((t) => t.cap === c),
  );

  const persist = async (cap: TaskCap, value: string) => {
    if (busyCap) return;
    setErr(null);
    setBusyCap(cap);
    const next: ProvidersConfig = { ...draft, [cap]: value };
    setDraft(next); // optimistic — echoed back by loadConfig() below
    try {
      await setProvidersConfig(next);
      await useStore.getState().loadConfig();
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
          content="Pick who runs each task. Auto chooses the best available for the connected rig; the options are the drivers that actually offer the task right now. The line under each row explains the current resolution. A row marked PROFILE is pinned by the equipment profile you activated — that pin beats this dropdown until you clear it."
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
            routed by the active equipment profile, not by the settings below.
            The pinned rows show what is actually running.
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
          return (
            <div key={cap} className="border border-line bg-bg/60 px-3 py-2.5">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="label w-28 shrink-0">{label}</span>
                <select
                  className="field !py-1 max-w-[220px]"
                  value={value}
                  // Under a profile pin this select cannot change anything that
                  // runs (it writes global; the profile wins), so it reports
                  // rather than pretends. See the header comment.
                  disabled={!canConfig || busy || busyCap === cap || pinned}
                  onChange={(e) => void persist(cap, e.target.value)}
                  aria-label={`${label} provider override`}
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
              <OverrideNote
                entry={entry}
                format={(v) => providerLabel(v, drivers)}
                clearHint="Clearing it hands this row back to the setting above."
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
