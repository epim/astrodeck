// reg-mount-polar.ts - the two sheets T-RIG-3 owns, offered to the Rig hub's
// registry as ONE object.
//
// Why a fragment and not an entry in `sheets.tsx`: that file is composed by the
// Rig devices task, and two tasks editing one registry is how a merge silently
// drops a sheet. The composer spreads this in:
//
//     export const sheets = { ...sheetsMountPolar, ...sheetsCameraPower, ... };
//
// `polar` is registered at the same flat level as `mount` because sheet names
// are GLOBAL (ARCHITECTURE.md section 5); its two-deep-ness is a property of
// the route (`#/rig/devices/mount/polar`), not of the registry. Opening it
// directly by URL is legal - the host mounts `mount` under it, inert, so BACK
// lands on the mount sheet.

import type { SheetRegistry } from "../../sheets";

export const sheetsMountPolar: SheetRegistry = {
  mount: { id: "rig/sheets/mount", load: () => import("./mount").then((m) => ({ default: m.MountSheet })) },
  polar: { id: "rig/sheets/polar", load: () => import("./polar").then((m) => ({ default: m.PolarSheet })) },
};

export default sheetsMountPolar;
