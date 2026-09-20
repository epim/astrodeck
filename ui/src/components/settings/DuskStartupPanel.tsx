import { useEffect, useState } from "react";
import { api } from "../../api";
import { listProfiles } from "../../api/backends";
import { useConfig, useStore } from "../../store";
import { useCan } from "../../lib/caps";
import type { AppConfig, DuskConfig, ProfileRow } from "../../types";
import { Field, Panel, Toggle } from "../ui";

const DEFAULTS: DuskConfig = { enabled: false, profile_id: null, sun_alt_deg: -12 };
type Status = { state: string; detail: string; temperature_c?: number | null; target_c?: number };

export default function DuskStartupPanel() {
  const config = useConfig();
  const canBackend = useCan("config.backend");
  const canSafety = useCan("config.safety");
  const canEdit = canBackend && canSafety;
  const [draft, setDraft] = useState<DuskConfig>(DEFAULTS);
  const [temperature, setTemperature] = useState("");
  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!dirty) {
      setDraft(config?.dusk ?? DEFAULTS);
      setTemperature(config?.cooling?.setpoint_c == null ? "" : String(config.cooling.setpoint_c));
    }
  }, [config?.dusk, config?.cooling?.setpoint_c, dirty]);

  useEffect(() => {
    let alive = true;
    void listProfiles().then(rows => { if (alive) setProfiles(rows); })
      .catch(() => { if (alive) setError("Could not load saved profiles. Reopen this page to try again."); });
    const poll = async () => {
      try {
        const result = await api.get<Status>("/api/dusk/state");
        if (alive) setStatus(result);
      } catch {
        if (alive) setStatus({ state: "unavailable", detail: "Preparation status is unavailable. Check the server connection." });
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 5000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  function edit(change: Partial<DuskConfig>) {
    setDraft(value => ({ ...value, ...change }));
    setDirty(true);
    setNotice("");
  }

  async function save() {
    if (!config || !canEdit || saving) return;
    const target = temperature.trim() === "" ? null : Number(temperature);
    if (draft.enabled && (!draft.profile_id || !profiles.some(p => p.id === draft.profile_id))) {
      setError("Choose a saved equipment profile."); return;
    }
    if (draft.enabled && config.site?.is_default) {
      setError("Set your observing location first so AstroDeck can calculate dusk."); return;
    }
    if ((draft.enabled && target == null) || (target != null && (!Number.isFinite(target) || target < -60 || target > 40))) {
      setError("Enter an imaging temperature between -60 and 40 °C."); return;
    }
    setSaving(true); setError(""); setNotice("");
    try {
      const updated = await api.post<AppConfig>("/api/config", {
        dusk: draft,
        cooling: { ...config.cooling, setpoint_c: target },
      });
      // Use the acknowledged response; a delayed websocket refresh must not
      // replace this form with its pre-save values.
      useStore.setState({ config: updated });
      setDirty(false);
      setNotice("Saved. If preparation already ran tonight, these settings apply tomorrow.");
    } catch {
      setError("Could not save dusk preparation. Your changes are still here; try again.");
    } finally { setSaving(false); }
  }

  return <Panel title="Prepare at dusk">
    <p className="text-sm text-dim mb-3">Connect your saved rig and cool the camera automatically each evening, without moving the telescope. Start a run yourself, or let an already armed session resume.</p>
    {!canEdit && <p className="text-sm text-dim mb-3">An administrator can change dusk preparation.</p>}
    <fieldset disabled={!canEdit || saving} className="flex flex-col gap-3 min-w-0">
      <div className="flex items-center gap-3">
        <Toggle checked={draft.enabled} disabled={!canEdit || saving} label="Prepare automatically each night"
          onChange={enabled => edit({ enabled })} />
        <span>Prepare automatically each night</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Field label="Equipment profile">
          <select className="field min-h-11" value={draft.profile_id ?? ""} onChange={e => edit({ profile_id: e.target.value || null })}>
            <option value="">Choose a saved profile</option>
            {profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
        <Field label="Start when the Sun reaches">
          <select className="field min-h-11" value={draft.sun_alt_deg} onChange={e => edit({ sun_alt_deg: Number(e.target.value) })}>
            {[-6, -12, -18].map(alt => <option key={alt} value={alt}>{alt}° below the horizon</option>)}
            {![-6, -12, -18].includes(draft.sun_alt_deg) && <option value={draft.sun_alt_deg}>{draft.sun_alt_deg}° below the horizon</option>}
          </select>
        </Field>
        <Field label="Imaging temperature (°C)">
          <input className="field min-h-11" type="number" min={-60} max={40} step={0.5} value={temperature}
            onChange={e => { setTemperature(e.target.value); setDirty(true); setNotice(""); }} />
        </Field>
      </div>
      <p className="text-sm text-dim">Use the temperature of your dark frames. This also updates the saved imaging temperature used by future runs. Choose −6° for an earlier cooldown, or −12° as a starting point.</p>
      <button type="button" className="btn btn-accent min-h-11 self-start" disabled={!dirty || saving} onClick={() => { void save(); }}>
        {saving ? "Saving…" : "Save dusk preparation"}
      </button>
    </fieldset>
    {error && <p role="alert" className="text-sm mt-3">{error}</p>}
    {notice && <p role="status" className="text-sm mt-3">{notice}</p>}
    <div role="status" className="text-sm mt-4" aria-label="Dusk preparation status">
      {status?.detail ?? "Reading preparation status…"}
      {status?.temperature_c != null && <span> {status.temperature_c.toFixed(1)} °C / {status.target_c} °C.</span>}
    </div>
    <p className="text-xs text-dim mt-2">One attempt per night. If a connection or cooldown fails, check Equipment and start manually. AstroDeck will not retry over a manual disconnect or warm-up.</p>
  </Panel>;
}
