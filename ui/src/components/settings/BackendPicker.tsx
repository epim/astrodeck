// BackendPicker.tsx — the W1.C per-role backend picker. Choose a PRIMARY backend
// (Simulator / NINA / Native cards), optionally OVERRIDE individual roles onto a
// different backend (e.g. Native devices + PHD2 guiding), enter connection params
// with discovery buttons, then connect the whole rig in ONE call:
//   builds a RigSpec → POST /api/connect/rig (connectRig) → maps each RoleResult
//   back onto its row by `role` for inline errors.
//
// Non-breaking: this lives in Settings; ConnectView + the legacy connect_* paths
// are untouched. Connecting here goes through the new RigSpec path, which populates
// backend_links (the tri-state grid).

import { useEffect, useMemo, useState, type JSX } from "react";
import type {
  BackendInfo,
  ConnectRigResult,
  ConnSpec,
  RigSpec,
  RoleResult,
} from "../../types";
import { listBackends, discoverBackend, connectRig } from "../../api/backends";
import { ApiError } from "../../api";
import { useStore } from "../../store";
import { confirmDialog } from "../ConfirmDialog";
import { Panel, Field, Toggle, Led, EmptyState, InfoDot } from "../ui";
import { Icon } from "../icons";
import {
  ALL_ROLES,
  ROLE_LABEL,
  addrKind,
  backendBlurb,
  defaultPortFor,
  buildConnSpec,
  type DiscoveredAlpaca,
  type DiscoveredNina,
  type Role,
} from "./backendMeta";

// Per-role addressing the user has entered (kept as strings for controlled inputs).
interface RoleFields {
  host: string;
  port: string;
  dev_type: string;
  dev_num: string;
}
const emptyFields = (): RoleFields => ({ host: "", port: "", dev_type: "", dev_num: "" });

// A role is "overridden" when the user pins it to a specific backend; otherwise it
// INHERITS the primary. We store the override backend name (or null = inherit).
type Overrides = Partial<Record<Role, string | null>>;

// The roles that, when they resolve to a real device backend (not sim), make a
// connect destructive enough to warrant the hold-confirm: a mount that could slew
// or a focuser that could drive. Mirrors the prompt's "real mount/focuser" rule.
const MOTION_ROLES: Role[] = ["telescope", "focuser"];

export default function BackendPicker(): JSX.Element {
  const showToast = useStore((s) => s.showToast);

  const [backends, setBackends] = useState<BackendInfo[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [primary, setPrimary] = useState<string>("sim");
  const [overrides, setOverrides] = useState<Overrides>({});
  const [fields, setFields] = useState<Record<string, RoleFields>>({});
  const [managedPhd2, setManagedPhd2] = useState(false);
  const [busy, setBusy] = useState(false);

  // Per-role inline result after a connect (RoleResult mapped by role).
  const [results, setResults] = useState<Record<string, RoleResult>>({});

  // Per-row discovery state: scanning flag, the discovered payload, and an error.
  const [discoverOpen, setDiscoverOpen] = useState<string | null>(null);
  const [discScan, setDiscScan] = useState<Record<string, boolean>>({});
  const [discData, setDiscData] = useState<Record<string, DiscoveredAlpaca[] | DiscoveredNina[] | null>>({});
  const [discErr, setDiscErr] = useState<Record<string, string | null>>({});

  useEffect(() => {
    let live = true;
    listBackends()
      .then((b) => {
        if (!live) return;
        setBackends(b);
        // Seed primary to sim if present, else the first backend.
        const has = (n: string) => b.some((x) => x.name === n);
        setPrimary(has("sim") ? "sim" : b[0]?.name ?? "sim");
      })
      .catch((e) => live && setLoadErr(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, []);

  const byName = useMemo(() => {
    const m = new Map<string, BackendInfo>();
    (backends ?? []).forEach((b) => m.set(b.name, b));
    return m;
  }, [backends]);

  // The backends offered as PRIMARY: those that can fill 2+ roles read as a
  // "whole rig" primary (sim/native/nina); single-role ones (phd2) are
  // override-only and not shown as primary cards.
  const primaryChoices = useMemo(
    () => (backends ?? []).filter((b) => b.roles.length >= 2),
    [backends],
  );

  // Resolve which backend fills a given role: the override if set + valid, else the
  // primary (when the primary can fill it). Returns null = role not filled at all.
  const resolvedBackend = (role: Role): string | null => {
    const ov = overrides[role];
    if (ov) return ov;
    const p = byName.get(primary);
    return p && p.roles.includes(role) ? primary : null;
  };

  const setField = (role: string, patch: Partial<RoleFields>) =>
    setFields((f) => ({ ...f, [role]: { ...(f[role] ?? emptyFields()), ...patch } }));

  // A NINA primary fills the guider via NINA itself, so the "managed PHD2" toggle
  // (which spawns a PHD2 child) is meaningless — hard-disable it when any role
  // resolves to NINA (per the pinned BackendPicker contract).
  const anyNina = useMemo(
    () => ALL_ROLES.some((r) => resolvedBackend(r) === "nina"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [primary, overrides, byName],
  );
  useEffect(() => {
    if (anyNina && managedPhd2) setManagedPhd2(false);
  }, [anyNina, managedPhd2]);

  // ----------------------------------------------------------------- discovery
  // Reset a row's discovery substate (results + error + scanning flag) so a row
  // never shows results from a backend it no longer resolves to.
  const clearDiscovery = (role: string) => {
    setDiscData((s) => ({ ...s, [role]: null }));
    setDiscErr((s) => ({ ...s, [role]: null }));
    setDiscScan((s) => ({ ...s, [role]: false }));
  };

  const runDiscover = async (role: string, backend: string) => {
    setDiscScan((s) => ({ ...s, [role]: true }));
    setDiscErr((s) => ({ ...s, [role]: null }));
    try {
      const data = await discoverBackend(backend);
      setDiscData((s) => ({ ...s, [role]: data as DiscoveredAlpaca[] | DiscoveredNina[] }));
    } catch (e) {
      setDiscErr((s) => ({
        ...s,
        [role]: e instanceof Error ? e.message : "discovery failed",
      }));
      setDiscData((s) => ({ ...s, [role]: null }));
    } finally {
      setDiscScan((s) => ({ ...s, [role]: false }));
    }
  };

  // Apply a discovered Alpaca device to the role's fields.
  const pickAlpaca = (
    role: string,
    srv: DiscoveredAlpaca,
    d: DiscoveredAlpaca["devices"][number],
  ) => {
    setField(role, {
      host: srv.address,
      port: String(srv.port),
      dev_type: d.DeviceType.toLowerCase(),
      dev_num: String(d.DeviceNumber),
    });
    setDiscoverOpen(null);
  };

  const pickNina = (role: string, inst: DiscoveredNina) => {
    setField(role, { host: inst.host, port: String(inst.port) });
    setDiscoverOpen(null);
  };

  // -------------------------------------------------------------- build + connect
  // Compose the RigSpec: primary + per-role overrides (only roles the user pinned,
  // OR roles that need addressing the user typed). Roles that simply inherit a
  // hostless/auto primary need no entry — the server resolves them from primary.
  const buildSpec = (): RigSpec => {
    const roles: Record<string, ConnSpec> = {};
    for (const role of ALL_ROLES) {
      const ov = overrides[role];
      const f = fields[role] ?? emptyFields();
      if (ov) {
        // Explicit override → always include (the server validates role∈backend.roles).
        const extra =
          ov === "phd2" && managedPhd2 ? { managed: true } : undefined;
        roles[role] = buildConnSpec(role, ov, f, extra);
      } else {
        // Inheriting the primary: only emit a ConnSpec when the user supplied
        // addressing the primary needs (host/dev) so we don't override needlessly.
        const kind = addrKind(primary);
        const hasAddr = f.host.trim() || f.dev_type.trim() || f.dev_num.trim();
        if (kind !== "none" && hasAddr && byName.get(primary)?.roles.includes(role)) {
          roles[role] = buildConnSpec(role, primary, f);
        }
      }
    }
    return { primary, roles };
  };

  // A summary of what will be connected (for the summary line + danger gating).
  const summary = useMemo(() => {
    const out: { role: Role; backend: string | null }[] = [];
    for (const role of ALL_ROLES) out.push({ role, backend: resolvedBackend(role) });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [primary, overrides, byName]);

  const realMotion = summary.some(
    (s) => MOTION_ROLES.includes(s.role) && s.backend != null && s.backend !== "sim",
  );

  const doConnect = async () => {
    // Danger hold-confirm when the resolved rig drives a real mount/focuser — a bad
    // connect could command motion. Routed through the store's hold-confirm path.
    if (realMotion) {
      const ok = await confirmDialog({
        title: "Connect this rig?",
        body: "This rig includes a real mount or focuser. Connecting will command the hardware to attach and may move it. Hold to confirm.",
        mode: "hold",
        tone: "danger",
        confirmLabel: "Connect rig",
      });
      if (!ok) return;
    }
    setBusy(true);
    setResults({});
    try {
      const spec = buildSpec();
      const res: ConnectRigResult = await connectRig(spec);
      const map: Record<string, RoleResult> = {};
      for (const r of res.results) map[r.role] = r;
      setResults(map);
      const connected = res.results.filter((r) => r.ok).length;
      const attempted = res.results.filter((r) => r.attempted).length;
      if (connected === 0 && attempted > 0) {
        showToast("error", "Rig connect attempted but no roles came up — see per-role errors");
      } else {
        showToast("success", `Rig connected — ${connected}/${attempted} roles up`);
      }
    } catch (e) {
      // 422 = a bad override/primary the server refused; 502 = connection failure.
      // Whole-request failure → one toast (per the contract: don't toast per-role).
      const msg =
        e instanceof ApiError
          ? e.status === 422
            ? `Invalid rig: ${e.message}`
            : e.message
          : e instanceof Error
            ? e.message
            : "connect failed";
      showToast("error", msg);
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------------- render
  if (loadErr) {
    return (
      <Panel title="Backend Picker">
        <EmptyState
          icon="alert"
          title="Couldn't load backends"
          hint={loadErr}
        />
      </Panel>
    );
  }
  if (!backends) {
    return (
      <Panel title="Backend Picker">
        <p className="text-dim text-xs">Loading backends…</p>
      </Panel>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* ---------------------------------------------------- PRIMARY backend */}
      <Panel
        title="Primary Backend"
        right={
          <InfoDot
            label="About the primary backend"
            content="The default backend for every role you don't override. Pick the one that drives most of your rig; override individual roles below for a mixed setup."
          />
        }
      >
        <div className="grid gap-2 sm:grid-cols-3">
          {primaryChoices.map((b) => {
            const active = primary === b.name;
            return (
              <button
                key={b.name}
                type="button"
                onClick={() => setPrimary(b.name)}
                aria-pressed={active}
                className={`text-left border px-3 py-3 transition-colors min-h-[44px]
                  ${active ? "border-accent bg-accent2/10" : "border-line bg-bg/60 hover:border-line2"}`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Led state={active ? "on" : "off"} />
                  <span className={`font-display text-sm tracking-wide ${active ? "text-accent" : "text-ink"}`}>
                    {b.label}
                  </span>
                </div>
                <p className="text-[10px] text-dim leading-snug">{backendBlurb(b.name)}</p>
              </button>
            );
          })}
        </div>
        {addrKind(primary) !== "none" && (
          <p className="text-[11px] text-dim mt-3 leading-relaxed">
            <Icon name="info" size={12} className="inline -mt-0.5 mr-1 text-accent" />
            Each role below inherits <span className="text-ink">{byName.get(primary)?.label}</span>.
            Use a role's <span className="text-ink">discover</span> button to fill its address, or
            override the role onto a different backend.
          </p>
        )}
      </Panel>

      {/* ------------------------------------------------------ PER-ROLE rows */}
      <Panel
        title="Per-Role Backends"
        right={<span className="label text-dim">override or inherit</span>}
      >
        <div className="flex flex-col gap-2.5">
          {ALL_ROLES.map((role) => (
            <RoleRow
              key={role}
              role={role}
              primary={primary}
              backends={backends}
              override={overrides[role] ?? null}
              fields={fields[role] ?? emptyFields()}
              result={results[role]}
              resolved={resolvedBackend(role)}
              managedPhd2={managedPhd2}
              anyNina={anyNina}
              discoverOpen={discoverOpen === role}
              discScan={!!discScan[role]}
              discData={discData[role] ?? null}
              discErr={discErr[role] ?? null}
              onSetOverride={(b) => {
                setOverrides((o) => ({ ...o, [role]: b }));
                // The row's resolved backend changed → any discovery results from
                // the PREVIOUS backend are now stale and cross-backend (e.g. Alpaca
                // servers under a now-NINA row). Clear them and close the panel so a
                // fresh open re-scans against the new backend.
                clearDiscovery(role);
                setDiscoverOpen((cur) => (cur === role ? null : cur));
              }}
              onSetField={(patch) => setField(role, patch)}
              onToggleDiscover={() => {
                // Open AND immediately kick the first scan (one tap, not two). The
                // inline button then acts as "Rescan". Re-resolve the backend here
                // since `resolved` for this role is computed at render.
                setDiscoverOpen((cur) => {
                  const next = cur === role ? null : role;
                  if (next === role) {
                    const rb = resolvedBackend(role);
                    // Only auto-scan when nothing has been fetched for this row yet.
                    if (rb && discData[role] == null && !discScan[role]) {
                      void runDiscover(role, rb);
                    }
                  }
                  return next;
                });
              }}
              onRunDiscover={(backend) => runDiscover(role, backend)}
              onPickAlpaca={(srv, d) => pickAlpaca(role, srv, d)}
              onPickNina={(inst) => pickNina(role, inst)}
              onSetManagedPhd2={setManagedPhd2}
            />
          ))}
        </div>
      </Panel>

      {/* ----------------------------------------------------------- SUMMARY */}
      <Panel title="Rig Summary">
        <div className="flex flex-wrap gap-1.5 mb-3">
          {summary.map((s) => (
            <span
              key={s.role}
              className={`inline-flex items-center gap-1.5 border px-2 py-1 text-[10px] tracking-wide
                ${s.backend ? "border-line2 text-ink" : "border-line text-faint"}`}
              title={s.backend ? `${ROLE_LABEL[s.role]} → ${byName.get(s.backend)?.label ?? s.backend}` : `${ROLE_LABEL[s.role]} not filled`}
            >
              <span className="label !text-dim">{ROLE_LABEL[s.role]}</span>
              <span className="mono">
                {s.backend ? (byName.get(s.backend)?.label ?? s.backend) : "—"}
              </span>
            </span>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn btn-accent min-h-11"
            disabled={busy}
            onClick={doConnect}
          >
            <Icon name="link" size={14} className="inline -mt-0.5 mr-1.5" />
            {busy ? "Connecting…" : "Connect Rig"}
          </button>
          {realMotion && (
            <span className="text-[11px] text-warn inline-flex items-center gap-1">
              <Icon name="alert" size={12} /> Drives a real mount/focuser — hold to confirm.
            </span>
          )}
        </div>
      </Panel>
    </div>
  );
}

// ============================================================================
// One per-role row: shows the RESOLVED backend (inherits vs override), a backend
// override <select>, the addressing fields the resolved backend needs, a
// discover affordance, the managed-PHD2 toggle (guider+phd2 only), and the inline
// RoleResult after a connect.
// ============================================================================
function RoleRow({
  role,
  primary,
  backends,
  override,
  fields,
  result,
  resolved,
  managedPhd2,
  anyNina,
  discoverOpen,
  discScan,
  discData,
  discErr,
  onSetOverride,
  onSetField,
  onToggleDiscover,
  onRunDiscover,
  onPickAlpaca,
  onPickNina,
  onSetManagedPhd2,
}: {
  role: Role;
  primary: string;
  backends: BackendInfo[];
  override: string | null;
  fields: RoleFields;
  result?: RoleResult;
  resolved: string | null;
  managedPhd2: boolean;
  anyNina: boolean;
  discoverOpen: boolean;
  discScan: boolean;
  discData: DiscoveredAlpaca[] | DiscoveredNina[] | null;
  discErr: string | null;
  onSetOverride: (b: string | null) => void;
  onSetField: (patch: Partial<RoleFields>) => void;
  onToggleDiscover: () => void;
  onRunDiscover: (backend: string) => void;
  onPickAlpaca: (srv: DiscoveredAlpaca, d: DiscoveredAlpaca["devices"][number]) => void;
  onPickNina: (inst: DiscoveredNina) => void;
  onSetManagedPhd2: (v: boolean) => void;
}): JSX.Element {
  // The backends that can fill THIS role (client-side reject of an override onto a
  // backend whose roles[] doesn't include the role — e.g. safety→nina).
  const eligible = backends.filter((b) => b.roles.includes(role));
  const byName = new Map(backends.map((b) => [b.name, b] as const));
  const resolvedInfo = resolved ? byName.get(resolved) : null;
  const kind = resolved ? addrKind(resolved) : "none";
  const inherits = !override;
  const showPhd2Toggle = role === "guider" && resolved === "phd2";

  // The status pill: inherits-primary vs override, and whether the role is filled.
  const pill = inherits
    ? resolved
      ? { word: "INHERITS", tone: "text-dim" }
      : { word: "NOT FILLED", tone: "text-faint" }
    : { word: "OVERRIDE", tone: "text-accent" };

  // Result LED (after connect): attempted/ok mapped to the tri-state shape.
  const resLed = result
    ? !result.attempted
      ? "off"
      : result.ok
        ? "on"
        : "bad"
    : null;

  return (
    <div className={`border bg-bg/60 px-3 py-2.5 ${override ? "border-line2" : "border-line"}`}>
      <div className="flex items-center gap-3 flex-wrap">
        {resLed ? (
          <Led state={resLed} label={`${ROLE_LABEL[role]} connect result`} />
        ) : (
          <Led state="off" />
        )}
        <span className="label w-28 shrink-0">{ROLE_LABEL[role]}</span>

        {/* override <select>: "Inherit (primary)" + every eligible backend. */}
        <select
          className="field !py-1 max-w-[170px]"
          value={override ?? ""}
          onChange={(e) => onSetOverride(e.target.value || null)}
          aria-label={`${ROLE_LABEL[role]} backend`}
        >
          <option value="">
            Inherit{resolvedInfo && inherits ? ` — ${byName.get(primary)?.label ?? primary}` : ""}
          </option>
          {eligible.map((b) => (
            <option key={b.name} value={b.name}>
              {b.label}
            </option>
          ))}
        </select>

        <span className={`mono text-[10px] tracking-wider ${pill.tone}`}>{pill.word}</span>

        <div className="flex-1" />

        {/* discover toggle — only when the resolved backend is discoverable. */}
        {resolved && resolvedInfo?.discoverable && (kind === "nina" || kind === "alpaca") && (
          <button
            type="button"
            className="btn !py-1 !px-2 text-[10px]"
            onClick={onToggleDiscover}
          >
            <Icon name="refresh" size={12} className="inline -mt-0.5 mr-1" />
            Discover
          </button>
        )}
      </div>

      {/* addressing fields for the resolved backend (host/port [+device]). */}
      {resolved && kind !== "none" && (
        <div className="mt-2 flex flex-wrap items-end gap-2 pl-[2.6rem]">
          {(kind === "host" || kind === "nina" || kind === "alpaca") && (
            <Field label="Host">
              <input
                className="field !py-1 w-[150px]"
                placeholder={resolved === "nina" ? "127.0.0.1" : "192.168.1.50"}
                value={fields.host}
                onChange={(e) => onSetField({ host: e.target.value })}
              />
            </Field>
          )}
          {(kind === "nina" || kind === "alpaca") && (
            <Field label="Port">
              <input
                className="field !py-1 w-[78px]"
                placeholder={String(defaultPortFor(resolved) ?? "")}
                value={fields.port}
                onChange={(e) => onSetField({ port: e.target.value })}
              />
            </Field>
          )}
          {kind === "alpaca" && (
            <>
              <Field label="Device type">
                <input
                  className="field !py-1 w-[110px]"
                  placeholder={role}
                  value={fields.dev_type}
                  onChange={(e) => onSetField({ dev_type: e.target.value })}
                />
              </Field>
              <Field label="Dev #">
                <input
                  className="field !py-1 w-[58px]"
                  placeholder="0"
                  value={fields.dev_num}
                  onChange={(e) => onSetField({ dev_num: e.target.value })}
                />
              </Field>
            </>
          )}
        </div>
      )}

      {/* managed-PHD2 toggle: only for guider→phd2; hard-disabled if any role is
          NINA (NINA owns guiding then, so a managed PHD2 child is meaningless). */}
      {showPhd2Toggle && (
        <div className="mt-2 pl-[2.6rem] flex items-center gap-2">
          <Toggle
            checked={managedPhd2 && !anyNina}
            disabled={anyNina}
            onChange={onSetManagedPhd2}
            label="Let AstroDeck launch and manage PHD2"
            showState
          />
          <span className="label inline-flex items-center gap-1">
            Managed PHD2
            <InfoDot
              label="About managed PHD2"
              content={
                anyNina
                  ? "Unavailable while a role uses NINA — NINA drives guiding itself."
                  : "AstroDeck launches PHD2 and manages its lifecycle instead of connecting to an already-running instance."
              }
            />
          </span>
        </div>
      )}

      {/* discovery substate: scanning / error / empty / list of devices. */}
      {discoverOpen && resolved && (
        <div className="mt-2 pl-[2.6rem] border-l border-line ml-[1.1rem]">
          <div className="flex items-center gap-2 mb-1.5">
            <button
              type="button"
              className="btn !py-1 !px-2 text-[10px]"
              disabled={discScan}
              onClick={() => onRunDiscover(resolved)}
            >
              {discScan ? "Scanning…" : discData != null || discErr ? "⟳ Rescan" : "⟳ Scan network"}
            </button>
            <span className="text-[10px] text-dim">
              {resolved === "nina" ? "NINA instances" : "Alpaca devices"}
            </span>
          </div>
          {discScan && <p className="text-[10px] text-dim mb-1">Scanning the network…</p>}
          {discErr && <p className="text-warn text-[10px] mb-1">{discErr}</p>}
          {!discScan && !discErr && discData != null && (
            <DiscoverList
              backend={resolved}
              data={discData}
              role={role}
              onPickAlpaca={onPickAlpaca}
              onPickNina={onPickNina}
            />
          )}
        </div>
      )}

      {/* inline RoleResult error after a connect attempt. */}
      {result && !result.ok && result.error && (
        <p className="mt-2 pl-[2.6rem] text-[10px] text-bad">{result.error}</p>
      )}
    </div>
  );
}

function DiscoverList({
  backend,
  data,
  role,
  onPickAlpaca,
  onPickNina,
}: {
  backend: string;
  data: DiscoveredAlpaca[] | DiscoveredNina[];
  role: Role;
  onPickAlpaca: (srv: DiscoveredAlpaca, d: DiscoveredAlpaca["devices"][number]) => void;
  onPickNina: (inst: DiscoveredNina) => void;
}): JSX.Element {
  if (data.length === 0) {
    return (
      <p className="text-[10px] text-dim">
        {backend === "nina" ? "No NINA instances found." : "No Alpaca devices found."}
      </p>
    );
  }
  if (backend === "nina") {
    const insts = data as DiscoveredNina[];
    return (
      <div className="flex flex-col gap-1">
        {insts.map((inst) => (
          <button
            key={inst.url}
            type="button"
            className="btn !normal-case !tracking-normal !font-sans !py-1.5 text-left flex justify-between items-center"
            onClick={() => onPickNina(inst)}
          >
            <span className="mono text-[11px] truncate">
              {inst.hostname ?? inst.host}:{inst.port}
            </span>
            <span className="label">{inst.nina_version ? `NINA ${inst.nina_version}` : "use"}</span>
          </button>
        ))}
      </div>
    );
  }
  // Alpaca: list devices that match this role's type, others greyed but pickable.
  const servers = data as DiscoveredAlpaca[];
  return (
    <div className="flex flex-col gap-1.5">
      {servers.map((srv) => (
        <div key={`${srv.address}:${srv.port}`}>
          <div className="mono text-[10px] text-accent mb-1">
            {srv.address}:{srv.port}
          </div>
          <div className="flex flex-col gap-1">
            {srv.devices.map((d, i) => {
              const match = d.DeviceType.toLowerCase() === role;
              return (
                <button
                  key={i}
                  type="button"
                  className={`btn !normal-case !tracking-normal !font-sans !py-1.5 text-left flex justify-between items-center
                    ${match ? "" : "opacity-60"}`}
                  onClick={() => onPickAlpaca(srv, d)}
                >
                  <span className="truncate">{d.DeviceName}</span>
                  <span className="label">
                    {d.DeviceType} #{d.DeviceNumber}
                  </span>
                </button>
              );
            })}
            {srv.devices.length === 0 && (
              <p className="text-[10px] text-dim">no devices on this server</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
