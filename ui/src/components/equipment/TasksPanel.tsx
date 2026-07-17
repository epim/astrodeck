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
// when it is the stored value, never offered fresh. Writes go to the GLOBAL
// config (POST /api/config/providers), exactly as the retired CapabilitiesCard
// did; per-profile overrides ride profile save/load in EquipmentView.
import { useEffect, useState, type JSX } from "react";
import type { DriverInfo, ProvidersConfig } from "../../types";
import { setProvidersConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useProviders, useStore } from "../../store";
import { accessPhrase, useCanConfigBackend } from "../../lib/caps";
import { eligibleTaskDrivers, TASK_CAPS, type TaskCap } from "../../lib/equipment";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";
import { ProviderBadge } from "../ProviderBadge";

export const DEFAULT_PROVIDERS: ProvidersConfig = {
  autofocus: "auto",
  polar_align: "auto",
  solve: "auto",
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

  const seed: ProvidersConfig = { ...DEFAULT_PROVIDERS, ...(config?.providers ?? {}) };
  const [draft, setDraft] = useState<ProvidersConfig>(seed);
  const [busyCap, setBusyCap] = useState<TaskCap | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Re-seed whenever a fresh config lands (our own save, or another client's).
  useEffect(() => {
    setDraft(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed.autofocus, seed.polar_align, seed.solve]);

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

  return (
    <Panel
      title="Tasks"
      right={
        <InfoDot
          label="About task routing"
          content="Pick who runs each task. Auto chooses the best available for the connected rig; the options are the drivers that actually offer the task right now. The line under each row explains the current resolution."
        />
      }
    >
      <div className="flex flex-col gap-2.5">
        {TASK_CAPS.map(({ cap, label }) => {
          const eligible = eligibleTaskDrivers(cap, drivers);
          const value = draft[cap];
          const choice = resolved?.[cap];
          const inList = value === "auto" || eligible.some((d) => d.id === value);
          const stale = drivers.find((d) => d.id === value);
          return (
            <div key={cap} className="border border-line bg-bg/60 px-3 py-2.5">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="label w-28 shrink-0">{label}</span>
                <select
                  className="field !py-1 max-w-[220px]"
                  value={value}
                  disabled={!canConfig || busy || busyCap === cap}
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
