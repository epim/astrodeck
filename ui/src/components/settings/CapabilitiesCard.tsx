// CapabilitiesCard.tsx — Settings → Connect "Capabilities" card (native parity,
// implementation brief §2 / §6). Shows the two resolved capability providers
// (autofocus, polar alignment) side by side with an override <select> that pins
// the routing to auto/backend/astrodeck.
//
// Two distinct data sources, both real:
//   RESOLVED  — status.providers.{autofocus,polar_align}.{kind,label,reason},
//               read via the store's useProviders() (server/astrodeck/providers.py
//               resolve_all(), attached additively by hub.poll_status). This is
//               "what's actually running right now" — reuses <ProviderBadge/>.
//   OVERRIDE  — config.providers.{autofocus,polar_align} ("auto"|"backend"|
//               "astrodeck"), read from useConfig() and written via
//               POST /api/config/providers (server/astrodeck/config.py
//               ProvidersConfig + ConfigStore.set_providers). This is "what the
//               user asked for" — resolve() falls back to auto if the pinned
//               choice's prerequisites are absent.
//
// Gated by config.backend (useCanConfigBackend) — same cap as the rest of the
// Connect surface: choosing which implementation drives a capability is a
// backend-shape decision, not a safety one. A caller without the cap sees the
// resolved badges + reason (read-only) but the dropdowns are disabled.
import { useEffect, useState, type JSX } from "react";
import type { ProviderKind, ProvidersConfig } from "../../types";
import { setProvidersConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useProviders, useStore } from "../../store";
import { useCanConfigBackend } from "../../lib/caps";
import { Panel, InfoDot } from "../ui";
import { Icon } from "../icons";
import { ProviderBadge } from "../ProviderBadge";

type Cap = "autofocus" | "polar_align";
const CAPS: { cap: Cap; label: string }[] = [
  { cap: "autofocus", label: "Autofocus" },
  { cap: "polar_align", label: "Polar alignment" },
];

const OVERRIDE_OPTIONS: { value: ProviderKind; label: string }[] = [
  { value: "auto", label: "Auto (best available)" },
  { value: "backend", label: "Backend (e.g. NINA)" },
  { value: "astrodeck", label: "AstroDeck native" },
];

const DEFAULT_PROVIDERS: ProvidersConfig = { autofocus: "auto", polar_align: "auto" };

export default function CapabilitiesCard(): JSX.Element {
  const config = useConfig();
  const resolved = useProviders();
  const canConfig = useCanConfigBackend();

  const seed = config?.providers ?? DEFAULT_PROVIDERS;
  const [draft, setDraft] = useState<ProvidersConfig>(seed);
  const [busyCap, setBusyCap] = useState<Cap | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Re-seed whenever a fresh config lands (our own save, or another client's).
  useEffect(() => {
    setDraft(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed.autofocus, seed.polar_align]);

  const persist = async (cap: Cap, kind: ProviderKind) => {
    if (busyCap) return;
    setErr(null);
    setBusyCap(cap);
    const next: ProvidersConfig = { ...draft, [cap]: kind };
    setDraft(next); // optimistic — echoed back by loadConfig() below
    try {
      await setProvidersConfig(next);
      await useStore.getState().loadConfig();
    } catch (e) {
      setDraft(seed); // revert the optimistic edit
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "config.backend required to change provider routing"
            : e.message
          : e instanceof Error
            ? e.message
            : "couldn't save provider override";
      setErr(msg);
    } finally {
      setBusyCap(null);
    }
  };

  return (
    <Panel
      title="Capabilities"
      right={
        <InfoDot
          label="About capability providers"
          content="AstroDeck can run autofocus and polar alignment with its own native engine or hand them to the connected backend (e.g. NINA). Auto picks the best available for the current rig; pin a choice here to override it."
        />
      }
    >
      <div className="flex flex-col gap-3">
        {CAPS.map(({ cap, label }) => {
          const choice = resolved?.[cap];
          const kind = draft[cap];
          return (
            <div key={cap} className="border border-line bg-bg/60 px-3 py-2.5">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="label w-32 shrink-0">{label}</span>
                <ProviderBadge cap={cap} />
                <div className="flex-1" />
                <select
                  className="field !py-1 max-w-[210px]"
                  value={kind}
                  disabled={!canConfig || busyCap === cap}
                  onChange={(e) => persist(cap, e.target.value as ProviderKind)}
                  aria-label={`${label} provider override`}
                >
                  {OVERRIDE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              {choice?.reason && (
                <p className="text-[11px] text-dim mt-1.5 pl-[8.5rem] leading-snug">
                  {choice.reason}
                </p>
              )}
            </div>
          );
        })}
        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing provider routing needs operator or admin access.
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
