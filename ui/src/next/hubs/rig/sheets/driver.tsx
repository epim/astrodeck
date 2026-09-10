// driver.tsx - ADD A DRIVER, one level under ADD A DEVICE (plan A.6, GAP-2
// "Missing - driver declaration").
//
// A scan finds what is plugged into this computer and what shouts on this
// network. Everything else has to be DECLARED - a NINA instance on the
// observatory PC, an Alpaca server behind a router, PHD2 on a fixed port - and
// the design had no screen for it, which made the whole "works alongside NINA"
// promise unreachable from the phone.
//
// The form is the shipped one from `components/settings/DriversPanel.tsx`,
// field for field, with its validator imported rather than re-derived: the
// three messages ("unknown driver type" / "host is required" / "port must be
// 1-65535") are the server's own 422s said early, and a second copy would
// drift away from them.
//
// TWO MODES. Normally this adds a driver. With `?edit=<id>` it edits ONE field -
// the serial port path of a native USB/serial driver - which is the shipped
// "Edit port" affordance: a COM port that moved between sessions is fixed in
// place instead of delete-and-recreate, which would lose every profile
// reference to that driver id.

import { useEffect, useMemo, useState, type JSX } from "react";
import {
  ActionButton, Card, Field, Label, ListRow, Mono, Segmented, Sheet, TextInput,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import type { SheetProps } from "../../sheets";
import {
  addDriver, listBackends, listDrivers, updateDriver,
} from "../../../../api/backends";
import { backendMeta } from "../../../../components/settings/backendMeta";
import {
  DRIVER_DEFAULT_PORT, DRIVER_TYPE_LABEL, validateDriverForm,
} from "../../../../components/settings/driversMeta";
import { useStore } from "../../../../store";
import type { DriverInfo } from "../../../../types";
import { scanNetwork, type NetFound } from "./addDevice";

/** The driver types this form can declare: exactly the ones the shipped
 *  validator accepts, which is exactly the ones with a default port. A type the
 *  server has not registered is dropped below. */
const DECLARABLE = Object.keys(DRIVER_DEFAULT_PORT);

/** `alpaca` is the driver type; `native` is what the registry calls that
 *  backend. The blurb table is keyed by registry name. */
const BACKEND_FOR_TYPE: Record<string, string> = {
  nina: "nina", alpaca: "native", phd2: "phd2", asiair: "asiair",
};

export function DriverSheet({ params }: SheetProps): JSX.Element {
  const editId = params.edit ?? null;

  const [type, setType] = useState<string>(params.type ?? "nina");
  const [host, setHost] = useState(params.host ?? "");
  const [port, setPort] = useState(params.port ?? "");
  const [label, setLabel] = useState("");
  const [portPath, setPortPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [net, setNet] = useState<NetFound[] | null>(null);
  const [registered, setRegistered] = useState<string[] | null>(null);
  const [editing, setEditing] = useState<DriverInfo | null>(null);

  const toast = (level: string, message: string) =>
    useStore.getState().showToast(level, message);
  const explain = (reason: string) => toast("warning", reason);
  const { lockedReason } = useLock({ cap: "config.backend" });
  const lock = lockedReason ?? (busy ? "The last change is still saving." : null);

  // Which backends this server actually has. ASIAIR only exists when the
  // optional dependency is installed, and offering a type the server would 422
  // is a promise nothing keeps.
  useEffect(() => {
    listBackends()
      .then((bs) => setRegistered(bs.map((b) => b.name)))
      .catch(() => setRegistered(null));
  }, []);

  useEffect(() => {
    if (!editId) return;
    listDrivers()
      .then((d) => {
        const found = d.drivers.find((x) => x.id === editId) ?? null;
        setEditing(found);
        setPortPath(found?.port_path ?? "");
      })
      .catch(() => setEditing(null));
  }, [editId]);

  const options = useMemo(() => DECLARABLE
    .filter((t) => registered == null || registered.includes(BACKEND_FOR_TYPE[t] ?? t))
    .map((t) => ({ value: t, label: DRIVER_TYPE_LABEL[t] ?? t })), [registered]);

  // The port field is SEEDED from the type, never silently rewritten over
  // something typed: a blank port means "let the server default it".
  const pickType = (t: string) => {
    setType(t);
    if (port.trim() === "") setPort(String(DRIVER_DEFAULT_PORT[t] ?? ""));
  };

  const err = validateDriverForm(type, host, port);
  const canScan = type === "nina" || type === "alpaca";

  const doScan = () => void (async () => {
    setBusy(true);
    try { setNet(await scanNetwork()); }
    finally { setBusy(false); }
  })();

  const doAdd = () => void (async () => {
    if (err) { toast("warning", err); return; }
    setBusy(true);
    try {
      const res = await addDriver({
        type,
        host: host.trim(),
        port: port.trim() === "" ? undefined : Number(port),
        label: label.trim() || undefined,
      });
      const name = res.driver.label || DRIVER_TYPE_LABEL[type] || type;
      toast("success", `${name} added - assign it to a role below.`);
      nav.back();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "could not add that driver");
    } finally {
      setBusy(false);
    }
  })();

  const doSavePort = () => void (async () => {
    if (!editId) return;
    setBusy(true);
    try {
      await updateDriver(editId, { port_path: portPath.trim() });
      toast("success", "Port updated - probe it to see what answers.");
      nav.back();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "could not change that port");
    } finally {
      setBusy(false);
    }
  })();

  if (editId) {
    return (
      <Sheet
        title="EDIT PORT"
        backLabel="ADD A DEVICE"
        icon={<NxIcon name="settings" size={18} />}
        live={editing ? `${editing.label} · ${editing.port_path || "no port set"}` : "reading the driver..."}
        onBack={() => nav.back()}
        data-testid="sheet-driver"
      >
        <Mono size={11} tone="dim">
          A serial port moves between sessions. Changing it here keeps the driver id,
          so every profile that names this driver still points at it.
        </Mono>
        <Field label="PORT PATH" hint="COM3 on Windows, /dev/ttyUSB0 on Linux">
          <TextInput
            value={portPath}
            onChange={setPortPath}
            mono
            ariaLabel="Serial port path"
            lockedReason={lock}
            data-testid="driver-port-path"
          />
        </Field>
        <ActionButton
          kind="primary"
          size="lg"
          full
          data-testid="driver-save-port"
          lockedReason={lock}
          onExplain={explain}
          onPress={doSavePort}
        >
          SAVE PORT
        </ActionButton>
      </Sheet>
    );
  }

  return (
    <Sheet
      title="ADD A DRIVER"
      backLabel="ADD A DEVICE"
      icon={<NxIcon name="plus" size={18} />}
      live={`${options.length} kinds this server can talk to`}
      onBack={() => nav.back()}
      data-testid="sheet-driver"
    >
      <Field label="TYPE">
        <Segmented
          options={options}
          value={type}
          onChange={pickType}
          label="Driver type"
          lockedReason={lock}
          onExplain={explain}
          data-testid="driver-type"
        />
      </Field>
      <Mono size={11} tone="dim">{backendMeta(BACKEND_FOR_TYPE[type] ?? type).blurb}</Mono>

      <Field label="HOST" hint="the machine this driver runs on">
        <TextInput
          value={host}
          onChange={setHost}
          mono
          placeholder="192.168.1.40"
          ariaLabel="Driver host"
          lockedReason={lock}
          data-testid="driver-host"
        />
      </Field>
      <Field label="PORT" hint={`blank uses the default (${DRIVER_DEFAULT_PORT[type] ?? "server-chosen"})`}>
        <TextInput
          value={port}
          onChange={setPort}
          mono
          type="number"
          ariaLabel="Driver port"
          lockedReason={lock}
          data-testid="driver-port"
        />
      </Field>
      <Field label="LABEL" hint="what to call it in the device list; optional">
        <TextInput
          value={label}
          onChange={setLabel}
          ariaLabel="Driver label"
          lockedReason={lock}
          data-testid="driver-label"
        />
      </Field>

      {canScan && (
        <>
          <ActionButton
            kind="secondary"
            full
            data-testid="driver-scan"
            busy={busy}
            lockedReason={lock}
            onExplain={explain}
            onPress={doScan}
          >
            SCAN NETWORK
          </ActionButton>
          {net != null && net.length === 0 && (
            <Mono size={11} tone="dim">nothing found on this network</Mono>
          )}
          {net != null && net.length > 0 && (
            <Card padding={0} data-testid="driver-net-found">
              {net.map((n, i) => (
                <ListRow
                  key={`${n.type}:${n.host}:${n.port}`}
                  data-testid={`driver-net-${i}`}
                  title={n.label}
                  sub={`${n.host}:${n.port}`}
                  right={<Mono size={10} tone="accent">USE</Mono>}
                  onPress={() => {
                    setType(n.type);
                    setHost(n.host);
                    setPort(String(n.port));
                  }}
                />
              ))}
            </Card>
          )}
        </>
      )}

      {err && <Mono size={11} tone="warn">{err}</Mono>}

      <ActionButton
        kind="primary"
        size="lg"
        full
        data-testid="driver-add"
        busy={busy}
        lockedReason={lock ?? err}
        onExplain={explain}
        onPress={doAdd}
      >
        ADD DRIVER
      </ActionButton>

      <Label>WHAT A DRIVER IS</Label>
      <Mono size={11} tone="dim">
        A driver is an address, not a device. Declaring one lets AstroDeck ask it what
        it can run; the roles it offers then appear on the assignment rows behind BACK.
      </Mono>
      <div style={{ height: 8 }} />
    </Sheet>
  );
}
