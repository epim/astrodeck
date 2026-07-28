// DriversPanel.tsx — Settings → "Backend Drivers" (equipment-drivers spec §4.2).
// The ONE place backends are declared: add/edit/enable/probe/delete configured
// drivers (NINA / Alpaca / PHD2), see what each currently OFFERS (per-role
// devices + tasks), and see the implicit built-ins (Simulator / AstroDeck
// native / ASTAP) with their detection state. Per-device ASSIGNMENT lives on
// the Equipment tab (Phase 2) — this panel is drivers only.
//
// Data source: GET /api/drivers (probe status + offers, 15s server cache);
// probeDriver(id) forces a refresh. Writes are config.backend-gated — without
// the cap the panel renders read-only (same Gated pattern as the rest of Settings).
import { useEffect, useState, type JSX } from "react";
import type { DriverInfo, DriversResponse } from "../../types";
import {
  addDriver,
  addDriverForHardware,
  deleteDriver,
  discoverBackend,
  discoverHardware,
  hwAlreadyConfigured,
  listBackends,
  listDrivers,
  probeDriver,
  updateDriver,
  type HwFound,
} from "../../api/backends";
import { ApiError } from "../../api";
import { useStore } from "../../store";
import { accessPhrase, useCanConfigBackend } from "../../lib/caps";
import { confirmDialog } from "../ConfirmDialog";
import { EmptyState, Field, InfoDot, Led, Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import type { DiscoveredAlpaca, DiscoveredNina } from "./backendMeta";
import {
  DRIVER_DEFAULT_PORT,
  DRIVER_TYPE_LABEL,
  driverTypeChip,
  offersSummary,
  validateDriverForm,
} from "./driversMeta";

type AddForm = { type: "nina" | "alpaca" | "phd2" | "asiair"; host: string; port: string; label: string };
const emptyForm = (): AddForm => ({ type: "nina", host: "", port: "", label: "" });

export default function DriversPanel(): JSX.Element {
  const showToast = useStore((s) => s.showToast);
  const canConfig = useCanConfigBackend();

  const [data, setData] = useState<DriversResponse | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<AddForm>(emptyForm());
  const [formErr, setFormErr] = useState<string | null>(null);
  // discovery substate for the add form (nina/alpaca only)
  const [scanning, setScanning] = useState(false);
  const [found, setFound] = useState<DiscoveredNina[] | DiscoveredAlpaca[] | null>(null);
  // discovery substate for "Scan for USB / serial hardware" (native backends)
  const [hwScanning, setHwScanning] = useState(false);
  const [hwFound, setHwFound] = useState<HwFound[] | null>(null);
  // Registry names the SERVER actually registered. Optional backends (asiair,
  // which needs libasi installed) are only OFFERED in the add form when the
  // server has them — otherwise "Add" would 422 on an unknown driver type.
  // Fails soft to [] so a fetch problem just hides the optional entries.
  const [backendNames, setBackendNames] = useState<string[]>([]);

  const reload = async () => {
    try {
      setData(await listDrivers());
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "couldn't load drivers");
    }
  };
  useEffect(() => {
    void reload();
    void (async () => {
      try {
        setBackendNames((await listBackends()).map((b) => b.name));
      } catch {
        setBackendNames([]);
      }
    })();
  }, []);

  const run = async (fn: () => Promise<unknown>, okMsg?: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      await reload();
      if (okMsg) showToast("success", okMsg);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "config.backend required to change drivers"
            : e.message
          : e instanceof Error
            ? e.message
            : "driver operation failed";
      showToast("error", msg);
    } finally {
      setBusy(false);
    }
  };

  const submitAdd = () => {
    const err = validateDriverForm(form.type, form.host, form.port);
    setFormErr(err);
    if (err) return;
    void run(async () => {
      await addDriver({
        type: form.type,
        host: form.host.trim(),
        port: form.port.trim() === "" ? undefined : Number(form.port),
        label: form.label.trim() || undefined,
      });
      setForm(emptyForm());
      setFound(null);
    }, "driver added");
  };

  const scan = async () => {
    // Registry names: the Alpaca lane is backend "native"; NINA is "nina".
    const backend = form.type === "alpaca" ? "native" : "nina";
    setScanning(true);
    setFound(null);
    try {
      setFound((await discoverBackend(backend)) as DiscoveredNina[] | DiscoveredAlpaca[]);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "discovery failed");
    } finally {
      setScanning(false);
    }
  };

  // "Scan for USB / serial hardware" — a generic pass over EVERY registered
  // hardware backend (zwo-am5, wanderer-snowflake, zwo-usb, zwo-asi,
  // player-one today; a future plugin backend needs zero client changes as
  // long as it sets `hardware`+`discoverable`+`driver_type` in the registry).
  // discoverHardware() (api/backends.ts) is the ONE shared implementation of
  // this scan+group logic — EquipmentView's "Detect hardware rig" uses it too.
  const scanHardware = async () => {
    setHwScanning(true);
    setHwFound(null);
    try {
      setHwFound(await discoverHardware());
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "hardware scan failed");
      setHwFound([]);
    } finally {
      setHwScanning(false);
    }
  };

  const addHw = (f: HwFound) =>
    void run(() => addDriverForHardware(f), `${f.name} added`);

  const addAllHw = () => {
    const toAdd = (hwFound ?? []).filter((f) => !hwAlreadyConfigured(f, data?.drivers ?? []));
    if (toAdd.length === 0) return;
    void run(async () => {
      // Sequential, not Promise.all: each add mints a server-side id off the
      // current config file — concurrent POSTs racing that read-modify-write
      // is the kind of thing worth just not risking.
      for (const f of toAdd) {
        await addDriverForHardware(f);
      }
    }, `${toAdd.length} driver(s) added`);
  };

  const pick = (host: string, port: number) => {
    setForm((f) => ({ ...f, host, port: String(port) }));
    setFound(null);
  };

  const remove = (d: DriverInfo) =>
    void (async () => {
      const ok = await confirmDialog({
        title: `Delete ${d.label}?`,
        body: "Profiles referencing this driver will show 'driver removed' until reassigned.",
        tone: "danger",
        confirmLabel: "Delete driver",
      });
      if (ok) void run(() => deleteDriver(d.id), "driver deleted");
    })();

  // A first-load failure (no data yet) has nothing to fall back to, so it's
  // the one case that blanks the panel (with a retry). `reload()` also runs
  // after every toggle/probe/add/delete (the shared `run()` helper) — a
  // transient failure THERE must not discard the already-loaded configured/
  // built-in driver lists and add-driver form; that surfaces as an inline
  // banner below instead (mirrors ProfileList's in-place error).
  if (loadErr && !data) {
    return (
      <Panel title="Backend Drivers">
        <EmptyState
          icon="alert"
          title="Couldn't load drivers"
          hint={loadErr}
          action={
            <button type="button" className="btn btn-accent !py-1.5" onClick={() => void reload()}>
              Retry
            </button>
          }
        />
      </Panel>
    );
  }

  const configured = data?.drivers.filter((d) => !d.implicit) ?? [];
  const implicit = data?.drivers.filter((d) => d.implicit) ?? [];

  return (
    <Panel
      title="Backend Drivers"
      right={
        <InfoDot
          label="About backend drivers"
          content="Declare how AstroDeck reaches your equipment backends — a NINA instance, Alpaca servers, PHD2, or hardware plugged directly into this machine (ZWO, Player One, Wanderer…) — once, globally. Each driver is probed for what it currently offers; per-device assignment happens on the Equipment surface."
        />
      }
    >
      <div className="flex flex-col gap-2.5">
        {/* Background-refresh failure (data already loaded): a non-destructive
            inline banner + retry — never the full-panel blank the first-load
            EmptyState above uses. */}
        {loadErr && data && (
          <div className="flex items-center gap-2 border border-bad/50 bg-bad/5 px-3 py-2">
            <Icon name="alert" size={14} className="text-bad shrink-0" />
            <span className="text-sm text-ink flex-1">Couldn't refresh drivers: {loadErr}</span>
            <button type="button" className="btn !py-1 !px-2 text-[11px]" onClick={() => void reload()}>
              Retry
            </button>
          </div>
        )}
        {/* ------------------------------------------------ configured drivers */}
        {configured.length === 0 && (
          <p className="text-xs text-dim">
            No drivers configured yet — add your NINA instance, Alpaca servers or
            PHD2 below, or scan for USB/serial hardware (ZWO, Player One, Wanderer…)
            plugged into this machine. The built-ins (Simulator, native engine,
            ASTAP) are always available.
          </p>
        )}
        {configured.map((d) => (
          <DriverRow
            key={d.id}
            d={d}
            busy={busy}
            canConfig={canConfig}
            onToggle={(en) => void run(() => updateDriver(d.id, { enabled: en }))}
            onProbe={() => void run(() => probeDriver(d.id))}
            onDelete={() => remove(d)}
            onEditPort={(portPath) => void run(() => updateDriver(d.id, { port_path: portPath }), "port updated")}
          />
        ))}

        {/* ------------------------------------------------------- add driver */}
        {canConfig && (
          <div className="border border-line bg-bg/60 px-3 py-2.5">
            <div className="label mb-2">Add driver</div>
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Type">
                <select
                  className="field !py-1"
                  value={form.type}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, type: e.target.value as AddForm["type"] }))
                  }
                >
                  <option value="nina">NINA</option>
                  <option value="alpaca">Alpaca server</option>
                  <option value="phd2">PHD2</option>
                  {backendNames.includes("asiair") && (
                    <option value="asiair">ZWO ASIAIR</option>
                  )}
                </select>
              </Field>
              <Field label="Host">
                <input
                  className="field !py-1 w-[150px]"
                  placeholder={form.type === "nina" ? "astrotown.lan" : "192.168.1.50"}
                  value={form.host}
                  onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
                />
              </Field>
              <Field label="Port">
                <input
                  className="field !py-1 w-[78px]"
                  placeholder={String(DRIVER_DEFAULT_PORT[form.type])}
                  value={form.port}
                  onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
                />
              </Field>
              <Field label="Label (optional)">
                <input
                  className="field !py-1 w-[160px]"
                  value={form.label}
                  onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                />
              </Field>
              <button type="button" className="btn btn-accent !py-1.5" disabled={busy} onClick={submitAdd}>
                <Icon name="plus" size={12} className="inline -mt-0.5 mr-1" />
                Add
              </button>
              {/* asiair has no discovery protocol libasi implements — the user
                  types the box's IP (ASIAIR app connection screen / router). */}
              {form.type !== "phd2" && form.type !== "asiair" && (
                <button type="button" className="btn !py-1.5" disabled={scanning} onClick={() => void scan()}>
                  {scanning ? "Scanning…" : <><Icon name="refresh" size={12} className="inline -mt-0.5 mr-1" />Scan network</>}
                </button>
              )}
            </div>
            {formErr && <p className="text-[11px] text-bad mt-1.5">{formErr}</p>}
            {found !== null && (
              <div className="mt-2 flex flex-col gap-1">
                {found.length === 0 && (
                  <p className="text-[10px] text-dim">nothing found on this network</p>
                )}
                {form.type === "nina"
                  ? (found as DiscoveredNina[]).map((i) => (
                      <button
                        key={i.url}
                        type="button"
                        className="btn !normal-case !tracking-normal !font-sans !py-1.5 text-left flex justify-between items-center"
                        onClick={() => pick(i.host, i.port)}
                      >
                        <span className="mono text-[11px] truncate">
                          {i.hostname ?? i.host}:{i.port}
                        </span>
                        <span className="label">
                          {i.nina_version ? `NINA ${i.nina_version}` : "use"}
                        </span>
                      </button>
                    ))
                  : (found as DiscoveredAlpaca[]).map((s) => (
                      <button
                        key={`${s.address}:${s.port}`}
                        type="button"
                        className="btn !normal-case !tracking-normal !font-sans !py-1.5 text-left flex justify-between items-center"
                        onClick={() => pick(s.address, s.port)}
                      >
                        <span className="mono text-[11px] truncate">
                          {s.address}:{s.port}
                        </span>
                        <span className="label">{s.devices.length} device(s)</span>
                      </button>
                    ))}
              </div>
            )}
          </div>
        )}

        {/* ------------------------------------------ USB / serial hardware scan */}
        {canConfig && (
          <div className="border border-line bg-bg/60 px-3 py-2.5">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div>
                <div className="label">Scan for USB / serial hardware</div>
                <p className="text-[11px] text-dim mt-0.5">
                  Finds ZWO (AM5, ASI, EAF/CAA), Player One and Wanderer Snowflake
                  hardware plugged into this machine — no host/port to type in.
                </p>
              </div>
              <button
                type="button"
                className="btn !py-1.5 shrink-0"
                disabled={hwScanning}
                onClick={() => void scanHardware()}
              >
                {hwScanning ? "Scanning…" : <><Icon name="refresh" size={12} className="inline -mt-0.5 mr-1" />Scan for USB/serial hardware</>}
              </button>
            </div>
            {hwFound !== null && (
              <div className="mt-2 flex flex-col gap-1">
                {hwFound.length === 0 && (
                  <p className="text-[10px] text-dim inline-flex items-center gap-1.5">
                    <Icon name="alert" size={11} />
                    No USB/serial hardware detected — check cables/power.
                  </p>
                )}
                {hwFound.map((f) => {
                  const already = hwAlreadyConfigured(f, data?.drivers ?? []);
                  return (
                    <div
                      key={`${f.driver_type}::${f.port_path ?? ""}::${f.index ?? ""}`}
                      className="flex items-center gap-2 border border-line bg-bg px-2.5 py-1.5"
                    >
                      <div className="min-w-0 flex-1">
                        <span className="text-sm text-ink">{f.name}</span>{" "}
                        <span className="mono text-[10px] text-dim">
                          {DRIVER_TYPE_LABEL[f.driver_type] ?? f.driver_type}
                          {f.port_path ? ` · ${f.port_path}` : f.index !== undefined ? ` · #${f.index}` : ""}
                        </span>
                        <span className="text-[10px] text-dim ml-1">
                          ({f.roles.join(", ")})
                        </span>
                      </div>
                      {already ? (
                        <span className="text-[10px] text-dim shrink-0 inline-flex items-center gap-1">
                          <Icon name="check" size={11} />
                          already configured
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="btn !py-1 !px-2 text-[10px] shrink-0"
                          disabled={busy}
                          onClick={() => addHw(f)}
                        >
                          <Icon name="plus" size={11} className="inline -mt-0.5 mr-1" />
                          Add
                        </button>
                      )}
                    </div>
                  );
                })}
                {hwFound.some((f) => !hwAlreadyConfigured(f, data?.drivers ?? [])) && (
                  <button
                    type="button"
                    className="btn btn-accent !py-1.5 self-start mt-1"
                    disabled={busy}
                    onClick={addAllHw}
                  >
                    Add all
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* --------------------------------------------------------- built-ins */}
        <div className="label mt-1">Built-in</div>
        {implicit.map((d) => (
          <div key={d.id} className="flex items-start gap-3 border border-line bg-bg/60 px-3 py-2.5">
            <Led state={d.status.reachable ? "on" : "off"} label={`${d.label} status`} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-ink">{d.label}</span>
                {/* #42: only when it adds information — see driverTypeChip */}
                {driverTypeChip(d.label, d.type) && (
                  <span className="mono text-[10px] text-dim">{driverTypeChip(d.label, d.type)}</span>
                )}
                {d.status.detail && (
                  <span className="mono text-[10px] text-dim truncate">{d.status.detail}</span>
                )}
              </div>
              {/* `truncate` is one hard-cut line, and offersSummary() joins
                  EVERY device a driver offers. On the simulator that is 11
                  entries / ~1805px of text in a 325px column, so a phone user
                  saw "camera: Simulated " and nothing else — 82% of the line
                  gone with no affordance to reach it, and no title fallback
                  (which would not fire on touch anyway).

                  line-clamp-2 gives two real lines on a phone and still clamps
                  on a narrow desktop column; an error string stays on one line
                  because it is short and should not push the row around. */}
              <p className={`text-[11px] text-dim mt-0.5 leading-snug ${
                d.status.reachable ? "line-clamp-2" : "truncate"}`}>
                {d.status.reachable ? offersSummary(d) : d.status.error}
              </p>
            </div>
          </div>
        ))}

        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing drivers needs {accessPhrase("config.backend")}.
          </p>
        )}
      </div>
    </Panel>
  );
}

// One configured-driver row: LED + label + addressing + offers line + controls.
function DriverRow({
  d,
  busy,
  canConfig,
  onToggle,
  onProbe,
  onDelete,
  onEditPort,
}: {
  d: DriverInfo;
  busy: boolean;
  canConfig: boolean;
  onToggle: (enabled: boolean) => void;
  onProbe: () => void;
  onDelete: () => void;
  onEditPort: (portPath: string) => void;
}): JSX.Element {
  const led = !d.enabled ? "off" : d.status.reachable ? "on" : "bad";
  // A native serial driver (zwo-am5, wanderer-snowflake): host==="" (no
  // network endpoint) + transport==="serial" — the only kind whose addressing
  // (COM port) can move and is worth an inline edit control.
  const isSerialNative = d.host === "" && d.transport === "serial";
  const [editingPort, setEditingPort] = useState(false);
  const [portDraft, setPortDraft] = useState(d.port_path ?? "");
  return (
    <div className="border border-line bg-bg/60 px-3 py-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <Led state={led} label={`${d.label} status`} />
        <div className="min-w-0">
          <span className="text-sm text-ink">{d.label}</span>{" "}
          {/* host truthy => real network endpoint; host===null/undefined =>
              RBAC-redacted (viewer without config.backend, see redact.py
              _redact_drivers_for) => "endpoint hidden"; host==="" => a native
              serial/local hardware driver, which has no network endpoint —
              show its COM port (serial) or SDK unit index (USB camera)
              instead of leaving it blank. Joined rather than concatenated so
              a suppressed type chip (#42) doesn't leave a dangling " · ". */}
          <span className="mono text-[10px] text-dim">
            {[
              driverTypeChip(d.label, d.type),
              d.host
                ? `${d.host}:${d.port}`
                : d.host == null
                  ? "endpoint hidden"
                  : d.port_path
                    ? d.port_path
                    : d.index != null
                      ? `#${d.index}`
                      : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
        <div className="flex-1" />
        {canConfig && isSerialNative && !editingPort && (
          <button
            type="button"
            className="btn !py-1 !px-2 text-[10px]"
            disabled={busy}
            onClick={() => {
              setPortDraft(d.port_path ?? "");
              setEditingPort(true);
            }}
          >
            Edit port
          </button>
        )}
        <Toggle checked={d.enabled} disabled={!canConfig || busy} onChange={onToggle} label={`${d.label} enabled`} showState />
        <button type="button" className="btn !py-1 !px-2 text-[10px]" disabled={busy} onClick={onProbe}>
          <Icon name="refresh" size={12} className="inline -mt-0.5 mr-1" />
          Probe
        </button>
        {canConfig && (
          <button type="button" className="btn btn-danger !py-1 !px-2 text-[10px]" disabled={busy} onClick={onDelete}>
            Delete
          </button>
        )}
      </div>
      {canConfig && isSerialNative && editingPort && (
        <div className="mt-1.5 pl-[1.6rem] flex items-center gap-1.5">
          <input
            className="field !py-1 w-[100px] text-[11px]"
            value={portDraft}
            placeholder="COM3"
            onChange={(e) => setPortDraft(e.target.value)}
            aria-label={`${d.label} port`}
          />
          <button
            type="button"
            className="btn !py-1 !px-2 text-[10px]"
            disabled={busy || !portDraft.trim()}
            onClick={() => {
              onEditPort(portDraft.trim());
              setEditingPort(false);
            }}
          >
            Save
          </button>
          <button
            type="button"
            className="btn !py-1 !px-2 text-[10px]"
            disabled={busy}
            onClick={() => setEditingPort(false)}
          >
            Cancel
          </button>
        </div>
      )}
      <p className="mt-1.5 pl-[1.6rem] text-[11px] leading-snug truncate">
        {!d.enabled ? (
          <span className="text-faint">disabled</span>
        ) : d.status.reachable ? (
          <span className="text-dim">{offersSummary(d)}</span>
        ) : (
          <span className="text-warn">{d.status.error}</span>
        )}
      </p>
    </div>
  );
}
