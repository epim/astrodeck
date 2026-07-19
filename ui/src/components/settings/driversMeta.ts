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
};

export const DRIVER_DEFAULT_PORT: Record<string, number> = {
  nina: 1888,
  alpaca: 11111,
  phd2: 4400,
};

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
