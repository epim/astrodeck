// driversMeta.ts — pure helpers for the Backend Drivers settings panel
// (equipment-drivers spec §4.2). Kept out of the component so the inline-assert
// test harness (npx tsx, no DOM) can exercise them directly.
import type { DriverInfo } from "../../types";

export const DRIVER_TYPE_LABEL: Record<string, string> = {
  nina: "NINA",
  alpaca: "Alpaca server",
  phd2: "PHD2",
  sim: "Simulator",
  astrodeck: "AstroDeck native",
  astap: "ASTAP",
  "ascom-local": "ASCOM (local)",
  // Native USB/serial hardware driver_types (native-hardware-onramp spec
  // 2026-07-21) — mirrors each backend's own `.label` (drivers.py registry).
  "zwo-am5": "ZWO AM5 (native serial)",
  "wanderer-snowflake": "Wanderer Snowflake FW",
  "zwo-usb": "ZWO USB accessories",
  "zwo-asi": "ZWO ASI camera",
  "player-one": "Player One camera",
  // Network bridge to a ZWO ASIAIR box (server: devices/backends/asiair_backend.py).
  // Only listed when the server registered the backend, i.e. libasi is installed.
  asiair: "ZWO ASIAIR",
};

export const DRIVER_DEFAULT_PORT: Record<string, number> = {
  nina: 1888,
  alpaca: 11111,
  phd2: 4400,
  // The ASIAIR's MAIN JSON-RPC port; the server opens 4400/4801 itself.
  asiair: 4700,
};

/** The type chip rendered beside a driver's label — or "" when it would just
 *  repeat the label.
 *
 *  UX review #42: built-in rows printed "Simulator  Simulator", "ASTAP  ASTAP",
 *  "ASCOM (local)  ASCOM (local)" because `DriverInfo.label` and the type label
 *  are the same string for every implicit driver, in two typefaces. Compared
 *  case- and space-insensitively so "AstroDeck native" vs "AstroDeck  native"
 *  still collapses. */
export function driverTypeChip(label: string, type: string): string {
  const chip = DRIVER_TYPE_LABEL[type] ?? type;
  const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ");
  return norm(chip) === norm(label) ? "" : chip;
}

/** One-line human summary of what a driver offers right now — the Settings
 *  "offers" readout ("camera: ASI2600MM · telescope: EQ6-R · tasks: autofocus"). */
export function offersSummary(d: DriverInfo): string {
  const parts = d.offers.devices.map((o) => `${o.role}: ${o.name}`);
  if (d.offers.tasks.length) parts.push(`tasks: ${d.offers.tasks.join(", ")}`);
  return parts.length ? parts.join(" · ") : "nothing offered";
}

/** Validate the add/edit driver form. Returns an error string, or null when
 *  valid. Port may be blank (server defaults it per type). */
export function validateDriverForm(
  type: string,
  host: string,
  port: string,
): string | null {
  if (!(type in DRIVER_DEFAULT_PORT)) return "unknown driver type";
  if (!host.trim()) return "host is required";
  if (port.trim() !== "") {
    const p = Number(port);
    if (!Number.isInteger(p) || p < 1 || p > 65535) return "port must be 1-65535";
  }
  return null;
}
