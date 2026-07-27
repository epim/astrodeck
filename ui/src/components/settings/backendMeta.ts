// backendMeta.ts — UI-side metadata for the backend picker. The SERVER is the
// source of truth for which backends exist and which roles each can fill (GET
// /api/backends → BackendInfo[]); this module only adds the human explainer copy
// and the per-backend addressing affordances the picker renders (host/port fields,
// discovery support, default ports). Keyed by the registry `name` so a backend the
// server adds later still lists (it just falls back to generic copy).

import type { ConnSpec } from "../../types";

export const ALL_ROLES = [
  "camera",
  "guide_camera",
  "telescope",
  "focuser",
  "guider",
  "filterwheel",
  "switch",
  "safety",
  "rotator",
  "covercalibrator",
] as const;
export type Role = (typeof ALL_ROLES)[number];

export const ROLE_LABEL: Record<string, string> = {
  camera: "Camera",
  guide_camera: "Guide camera",
  telescope: "Mount",
  focuser: "Focuser",
  // "Guiding" (not "Guider") — distinct from "Guide camera" in the Devices
  // list; the roles are genuinely different (guiding *algorithm*, e.g. PHD2/
  // native, vs the physical *camera* it reads from). Label-only change, no
  // structural merge (native-hardware-onramp spec 2026-07-21 task 4).
  guider: "Guiding",
  filterwheel: "Filter wheel",
  switch: "Power / switch",
  safety: "Safety monitor",
  rotator: "Rotator",
  covercalibrator: "Flat panel",
  // UX review #41: the dome row rendered its raw role id, so it was the one
  // Equipment slot labelled in lower case ("dome link") among ten Title-Case
  // siblings.
  dome: "Roof / dome",
};

// What addressing a backend needs in the picker:
//   "none"   → sim: nothing to configure (hostless).
//   "host"   → phd2: a host (+ implicit default port), no Alpaca device selection.
//   "nina"   → host + port, discoverable to a NINA instance.
//   "alpaca" → host + port + dev_type/dev_num, discoverable to Alpaca devices.
export type AddrKind = "none" | "host" | "nina" | "alpaca";

interface BackendMeta {
  addr: AddrKind;
  defaultPort?: number;
  blurb: string; // one-line explainer under the primary picker
}

// Fallbacks by registry name. Unknown backends get generic Alpaca-ish defaults so a
// future server backend still renders sensibly.
const META: Record<string, BackendMeta> = {
  sim: {
    addr: "none",
    blurb: "Demo rig — fully simulated camera, mount, focuser and guider. No hardware needed.",
  },
  native: {
    addr: "alpaca",
    defaultPort: 11111,
    blurb: "Direct ASCOM Alpaca devices on your network (ZWO, Pegasus, QHY…). The no-NINA path.",
  },
  nina: {
    addr: "nina",
    defaultPort: 1888,
    blurb: "Bridge to a running NINA instance via its Advanced API. A transition path off NINA.",
  },
  phd2: {
    addr: "host",
    defaultPort: 4400,
    blurb: "PHD2 guiding only — pair it with Native or NINA devices for a mixed rig.",
  },
};

const GENERIC: BackendMeta = {
  addr: "alpaca",
  defaultPort: 11111,
  blurb: "Network backend.",
};

export const backendMeta = (name: string): BackendMeta => META[name] ?? GENERIC;
export const addrKind = (name: string): AddrKind => backendMeta(name).addr;
export const defaultPortFor = (name: string): number | undefined =>
  backendMeta(name).defaultPort;
export const backendBlurb = (name: string): string => backendMeta(name).blurb;

// Does this backend need any per-role addressing the picker must collect? sim needs
// nothing; phd2 needs just a host; nina/alpaca need host+port (+device for alpaca).
export const needsAddressing = (name: string): boolean => addrKind(name) !== "none";

// Build a clean ConnSpec for one role/override, dropping empty addressing so the
// server's RigSpec.resolve() and per-backend defaults apply. `extra` is included
// only when non-empty (keeps the posted body tidy + matches profile JSON).
export function buildConnSpec(
  role: string,
  backend: string,
  fields: { host?: string; port?: string | number; dev_type?: string; dev_num?: string | number },
  extra?: Record<string, unknown>,
): ConnSpec {
  const kind = addrKind(backend);
  const spec: ConnSpec = { backend, role };
  if (kind === "host" || kind === "nina" || kind === "alpaca") {
    const host = String(fields.host ?? "").trim();
    if (host) spec.host = host;
    const port = fields.port === "" || fields.port == null ? NaN : Number(fields.port);
    if (Number.isFinite(port) && port > 0) spec.port = port;
  }
  if (kind === "alpaca") {
    const dt = String(fields.dev_type ?? "").trim();
    if (dt) spec.dev_type = dt;
    const dn = fields.dev_num === "" || fields.dev_num == null ? NaN : Number(fields.dev_num);
    if (Number.isFinite(dn)) spec.dev_num = dn;
  }
  if (extra && Object.keys(extra).length > 0) spec.extra = extra;
  return spec;
}

// The discovered-server shape the Native (Alpaca) backend returns from
// GET /api/discover/native — mirrors AlpacaServer.
export interface DiscoveredAlpaca {
  address: string;
  port: number;
  devices: { DeviceName: string; DeviceType: string; DeviceNumber: number }[];
}

// The discovered-instance shape the NINA backend returns from
// GET /api/discover/nina — mirrors NinaInstance.
export interface DiscoveredNina {
  host: string;
  hostname: string | null;
  port: number;
  url: string;
  nina_version: string | null;
  devices: Record<string, string>;
}

// The discovered-device shape every native USB/serial hardware backend
// returns from GET /api/discover/{name} (zwo-am5, wanderer-snowflake,
// zwo-usb, zwo-asi, player-one) — mirrors each backend's own discover() list
// entries (e.g. ZwoAm5Backend.discover(), ZwoUsbBackend.discover()). Serial
// backends carry `port_path` (e.g. "COM3"); local/SDK-enumerated (USB)
// backends carry none — presence alone is the addressing. Camera backends
// (zwo-asi, player-one) instead carry a 0-based `index` per enumerated unit
// (follow-up 2026-07-21), letting two identical USB cameras be told apart.
export interface DiscoveredHardware {
  role: string;
  name: string;
  port_path?: string;
  index?: number;
  verified?: boolean;
}
