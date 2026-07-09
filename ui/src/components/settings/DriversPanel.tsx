// DriversPanel.tsx — Settings → "Backend Drivers" (equipment-drivers spec §4.2).
// The ONE place backends are declared: add/edit/enable/probe/delete configured
// drivers (NINA / Alpaca / PHD2), see what each currently OFFERS (per-role
// devices + tasks), and see the implicit built-ins (Simulator / AstroDeck
// native / ASTAP) with their detection state. Per-device ASSIGNMENT lives on
// the Equipment tab (Phase 2) — this panel is drivers only.
//
// Data source: GET /api/drivers (probe status + offers, 15s server cache);
// probeDriver(id) forces a refresh. Writes are config.backend-gated — without
// the cap the panel renders read-only (same pattern as CapabilitiesCard).
import { useEffect, useState, type JSX } from "react";
import type { DriverInfo, DriversResponse } from "../../types";
import {
  addDriver,
  deleteDriver,
  discoverBackend,
  listDrivers,
  probeDriver,
  updateDriver,
} from "../../api/backends";
import { ApiError } from "../../api";
import { useStore } from "../../store";
import { useCanConfigBackend } from "../../lib/caps";
import { confirmDialog } from "../ConfirmDialog";
import { EmptyState, Field, InfoDot, Led, Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import type { DiscoveredAlpaca, DiscoveredNina } from "./backendMeta";
import {
  DRIVER_DEFAULT_PORT,
  DRIVER_TYPE_LABEL,
  offersSummary,
  validateDriverForm,
} from "./driversMeta";

type AddForm = { type: "nina" | "alpaca" | "phd2"; host: string; port: string; label: string };
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

  if (loadErr) {
    return (
      <Panel title="Backend Drivers">
        <EmptyState icon="alert" title="Couldn't load drivers" hint={loadErr} />
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
          content="Declare how AstroDeck reaches your equipment backends — a NINA instance, Alpaca servers, PHD2 — once, globally. Each driver is probed for what it currently offers; per-device assignment happens on the Equipment surface."
        />
      }
    >
      <div className="flex flex-col gap-2.5">
        {/* ------------------------------------------------ configured drivers */}
        {configured.length === 0 && (
          <p className="text-xs text-dim">
            No drivers configured yet — add your NINA instance, Alpaca servers or
            PHD2 below. The built-ins (Simulator, native engine, ASTAP) are always
            available.
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
              {form.type !== "phd2" && (
                <button type="button" className="btn !py-1.5" disabled={scanning} onClick={() => void scan()}>
                  {scanning ? "Scanning…" : "⟳ Scan network"}
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

        {/* --------------------------------------------------------- built-ins */}
        <div className="label mt-1">Built-in</div>
        {implicit.map((d) => (
          <div key={d.id} className="flex items-start gap-3 border border-line bg-bg/60 px-3 py-2.5">
            <Led state={d.status.reachable ? "on" : "off"} label={`${d.label} status`} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-ink">{d.label}</span>
                <span className="mono text-[10px] text-dim">{DRIVER_TYPE_LABEL[d.type] ?? d.type}</span>
                {d.status.detail && (
                  <span className="mono text-[10px] text-dim truncate">{d.status.detail}</span>
                )}
              </div>
              <p className="text-[11px] text-dim mt-0.5 leading-snug truncate">
                {d.status.reachable ? offersSummary(d) : d.status.error}
              </p>
            </div>
          </div>
        ))}

        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing drivers needs operator or admin access.
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
}: {
  d: DriverInfo;
  busy: boolean;
  canConfig: boolean;
  onToggle: (enabled: boolean) => void;
  onProbe: () => void;
  onDelete: () => void;
}): JSX.Element {
  const led = !d.enabled ? "off" : d.status.reachable ? "on" : "bad";
  return (
    <div className="border border-line bg-bg/60 px-3 py-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <Led state={led} label={`${d.label} status`} />
        <div className="min-w-0">
          <span className="text-sm text-ink">{d.label}</span>{" "}
          <span className="mono text-[10px] text-dim">
            {DRIVER_TYPE_LABEL[d.type] ?? d.type} · {d.host}:{d.port}
          </span>
        </div>
        <div className="flex-1" />
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
