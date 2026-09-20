import { api, ApiError } from "../api";
import { useStore } from "../store";
import { useExperience } from "./experience";
import { observeSetupChecks, useGuidedSetup, type SetupFact, type SkyPosition } from "./setup";

export interface Checkpoint {
  boot_id: string; context: string; revision: number;
  facts: Record<SetupFact, boolean>; reason: string;
  focus?: ReturnType<typeof useStore.getState>["focus"];
  polar?: ReturnType<typeof useStore.getState>["polar"];
  filter_offsets?: ReturnType<typeof useStore.getState>["filterOffsetsLearn"];
  busy?: string[];
}
const FIELD_KEY = "astrodeck.guided.field.v1";
function configSignature() {
  const c = useStore.getState().config;
  return JSON.stringify([c?.site,c?.safety?.horizon,c?.providers,c?.active_profile_id,c?.optics]);
}
export function readField(storage: Pick<Storage, "getItem">, context: string, now = Date.now()): SkyPosition | null {
  try {
    const saved = JSON.parse(storage.getItem(FIELD_KEY) ?? "null");
    const f = saved?.field;
    if (saved?.context !== context || !Number.isFinite(saved?.at) || now < saved.at || now - saved.at > 12*3600000 ||
        !Number.isFinite(f?.ra_hours) || f.ra_hours < 0 || f.ra_hours >= 24 || !Number.isFinite(f?.dec_deg) || Math.abs(f.dec_deg) > 90) return null;
    return f;
  } catch { return null; }
}

/** Only this adapter transports checks. Reopening a view never dispatches a
 * hardware command; operation snapshots come from the running controller. */
export function startGuidedRecovery() {
  let disposed = false, applying = false, busy = false, queued = 0;
  let checkpoint: Checkpoint | null = null;
  let connected = false, first = true;
  let epoch = 0;
  let queue: Promise<unknown> = Promise.resolve();
  const localOnly = new Set<SetupFact>();
  const available = () => { const s = useStore.getState(); return s.wsPhase === "up" && !s.telemetryStale && !!s.status && !!s.config; };
  const apply = (value: Checkpoint, reconnect = false, started = useStore.getState()) => {
    if (disposed || !available()) return;
    const changed = checkpoint !== null && checkpoint.context !== value.context;
    const updated = checkpoint?.revision !== value.revision;
    checkpoint = value;
    applying = true;
    const s = useStore.getState(), setup = useGuidedSetup.getState();
    const focusUnchanged = started.focus === s.focus;
    const polarUnchanged = started.polar === s.polar;
    if (changed || reconnect || first || updated || !value.facts.location || !value.facts.horizon) localOnly.clear();
    const facts = {...value.facts};
    const canKeepLocal = !changed && !updated && !first && !reconnect;
    if (!focusUnchanged) {facts.focus=canKeepLocal&&setup.focus;facts.alignment=canKeepLocal&&setup.alignment;}
    if (!polarUnchanged) facts.alignment=canKeepLocal&&setup.alignment;
    for (const fact of localOnly) facts[fact] = setup[fact];
    let field = changed || !value.facts.horizon ? null : setup.field;
    if (first && value.facts.horizon) {
      try { field = readField(window.localStorage, value.context); } catch { /* browser storage is optional */ }
    }
    useGuidedSetup.setState({ ...facts, field,
      locationKey: JSON.stringify(s.config?.site), horizonKey: JSON.stringify(s.config?.safety?.horizon),
      recovering: false,
      ...(first || reconnect || changed || updated ? {recoveryMessage: value.reason} : {}),
    });
    // These snapshots contain the latest terminal event as well as live work.
    // Never synthesize a successful result when an operation simply disappeared.
    if (focusUnchanged && value.focus && JSON.stringify(value.focus) !== JSON.stringify(s.focus)) s.handleEvent({type:"focus",data:value.focus,ts:Date.now()/1000} as never);
    if (polarUnchanged && value.polar && JSON.stringify(value.polar) !== JSON.stringify(s.polar)) s.handleEvent({type:"polar",data:value.polar,ts:Date.now()/1000} as never);
    if (started.filterOffsetsLearn === s.filterOffsetsLearn && value.filter_offsets && JSON.stringify(value.filter_offsets) !== JSON.stringify(s.filterOffsetsLearn)) s.handleEvent({type:"filter_offsets",data:value.filter_offsets,ts:Date.now()/1000} as never);
    if ((first || reconnect) && useExperience.getState().mode === "guided") {
      const step = value.busy?.includes("polar") ? "alignment" : value.busy?.some(l => l === "autofocus" || l === "filter_offsets") ? "focus" : null;
      if (step) {
        s.setView(step === "focus" ? "focus" : "polar");
        useExperience.getState().openWizard(step);
      }
    }
    applying = false;
    first = false;
  };
  const refresh = async (reconnect = false) => {
    if (disposed || busy || queued || !available()) return;
    busy = true;
    const started = useStore.getState(), requestedAt = epoch;
    if (first || reconnect) useGuidedSetup.setState({recovering:true});
    try {
      const value = await api.get<Checkpoint>("/api/guided/checkpoint");
      if (requestedAt === epoch) apply(value, reconnect, started);
    }
    catch {
      if (!disposed) useGuidedSetup.setState({recovering:false, recoveryMessage:"We couldn't recover the setup checks from the controller. Your saved site and horizon are still available; review the steps before continuing."});
    } finally { busy = false; if (!disposed) useGuidedSetup.setState({recovering:false}); }
  };
  observeSetupChecks((action, fact, origin) => {
    if (applying || disposed) return;
    epoch++;
    if (action === "invalidate") {
      const order: SetupFact[] = ["location","horizon","focus","alignment"];
      order.slice(order.indexOf(fact)).forEach(key => localOnly.delete(key));
    }
    if (action === "complete" && fact === "focus" && origin === "manual") {
      localOnly.add("focus");
      useGuidedSetup.setState({recoveryMessage:"Manual focus checked on this device. After reconnecting or switching devices, check the stars again."});
      return Promise.resolve(true);
    }
    queued++;
    const actionEpoch = epoch;
    useGuidedSetup.setState({savingChecks:true});
    // User confirmations are serialized, never retried after a conflict. A new
    // server context is allowed only while the reviewed local config is intact.
    const reviewedConfig = configSignature();
    const outcome = queue.then(async () => {
      if (disposed) return false;
      try {
        if (!available()) throw new Error("connection lost");
        const current = await api.get<Checkpoint>("/api/guided/checkpoint");
        if (disposed) return false;
        if (!available()) throw new Error("connection lost");
        if (action === "complete" && reviewedConfig !== configSignature()) throw new Error("configuration changed");
        const result = await api.post<Checkpoint>("/api/guided/checkpoint", {context:current.context,revision:current.revision,fact,action});
        // Do not hydrate an older response over a newer local confirmation in
        // the queue. The final poll reconciles the full shared checkpoint.
        checkpoint = result;
        localOnly.delete(fact);
        if (!disposed && queued === 1 && epoch === actionEpoch) {
          useGuidedSetup.setState({recoveryMessage:result.reason});
        }
        return true;
      } catch (error) {
        if (!disposed && action === "invalidate" && error instanceof ApiError && error.status === 409) {
          // Several browsers can observe the same run starting. A competing
          // invalidation is already the requested result; confirm it with a
          // read instead of sending another write or warning about failure.
          try {
            const confirmed = await api.get<Checkpoint>("/api/guided/checkpoint");
            const order: SetupFact[] = ["location","horizon","focus","alignment"];
            if (!disposed && available() && order.slice(order.indexOf(fact)).every(key=>confirmed.facts[key]===false)) {
              if (queued === 1 && epoch === actionEpoch) useGuidedSetup.setState({recoveryMessage:confirmed.reason});
              return true;
            }
          } catch { /* an unconfirmed read remains a conflict */ }
        }
        if (!disposed) {
          const order: SetupFact[] = ["location","horizon","focus","alignment"];
          useGuidedSetup.setState({...Object.fromEntries(order.slice(order.indexOf(fact)).map(key=>[key,false])),
            recoveryMessage:"The controller couldn't confirm this setup check. Review the step again before continuing."});
        }
        return false;
      }
    }).finally(async () => { queued--; if(!disposed){useGuidedSetup.setState({savingChecks:queued>0});if(!queued)await refresh();} });
    queue = outcome;
    return outcome;
  });
  const unsubscribeField = useGuidedSetup.subscribe((state, previous) => {
    if (applying || state.field === previous.field || !checkpoint) return;
    try {
      if (!state.field) window.localStorage.removeItem(FIELD_KEY);
      else window.localStorage.setItem(FIELD_KEY, JSON.stringify({context:checkpoint.context,at:Date.now(),field:state.field}));
    } catch { /* storage is not required for safe operation */ }
  });
  const unsubscribe = useStore.subscribe(() => {
    const up = available();
    if (!up && connected) {
      useGuidedSetup.setState({focus:false,alignment:false,recoveryMessage:"Connection lost. We’ll check the controller before restoring completed steps."});
    }
    if (up && !connected) void refresh(!first);
    connected = up;
  });
  connected = available();
  if (connected) void refresh();
  const timer = window.setInterval(() => void refresh(), 10000);
  return () => {disposed=true;observeSetupChecks(null);unsubscribe();unsubscribeField();window.clearInterval(timer);useGuidedSetup.setState({recovering:false,savingChecks:false});};
}
