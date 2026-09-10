// set3.ts - T-SET-3's six sheets, handed to the SETTINGS hub as one object,
// plus the USERS and ABOUT screens the hub mounts directly for the USERS and
// ABOUT sub-nav entries (plan section E.5).
//
// This file does NOT touch `hubs/settings/sheets.ts` - that registry, and the
// GENERAL/USERS/ABOUT screen switch, belong to the Settings hub task
// (T-SET-1). `sheets3` is the block it spreads in, matching the pattern
// T-SET-2's `set2.ts` and T-SET-4's `set4.ts` already use.
import type { SheetRegistry } from "../../sheets";

export const sheets3: SheetRegistry = {
  account: { id: "settings/sheets/AccountSheet", load: () => import("./AccountSheet").then((m) => ({ default: m.AccountSheet })) },
  authMethods: { id: "settings/sheets/AuthMethodsSheet", load: () => import("./AuthMethodsSheet").then((m) => ({ default: m.AuthMethodsSheet })) },
  users: { id: "settings/sheets/UsersSheet", load: () => import("./UsersSheet").then((m) => ({ default: m.UsersSheet })) },
  update: { id: "settings/sheets/UpdateSheet", load: () => import("./UpdateSheet").then((m) => ({ default: m.UpdateSheet })) },
  credits: { id: "settings/sheets/CreditsSheet", load: () => import("./CreditsSheet").then((m) => ({ default: m.CreditsSheet })) },
  help: { id: "settings/sheets/HelpSheet", load: () => import("./HelpSheet").then((m) => ({ default: m.HelpSheet })) },
};

// THE TWO SUB-NAV SCREENS ARE NOT SHEETS and are re-exported, not registered:
// `SettingsHub` mounts them for `#/settings/users` and `#/settings/about`. They
// stay static re-exports because the hub that renders them is itself a lazy
// chunk, so they arrive with it and never reach the entry bundle. Registering
// them here would be a second, contradictory route to the same screens.
export { UsersScreen } from "./UsersScreen";
export { AboutScreen } from "./AboutScreen";
