// set3.ts - T-SET-3's six sheets, handed to the SETTINGS hub as one object,
// plus the USERS and ABOUT screens the hub mounts directly for the USERS and
// ABOUT sub-nav entries (plan section E.5).
//
// This file does NOT touch `hubs/settings/sheets.ts` - that registry, and the
// GENERAL/USERS/ABOUT screen switch, belong to the Settings hub task
// (T-SET-1). `sheets3` is the block it spreads in, matching the pattern
// T-SET-2's `set2.ts` and T-SET-4's `set4.ts` already use.
import type { SheetComponent } from "../../sheets";
import { AccountSheet } from "./AccountSheet";
import { AuthMethodsSheet } from "./AuthMethodsSheet";
import { UsersSheet } from "./UsersSheet";
import { UpdateSheet } from "./UpdateSheet";
import { CreditsSheet } from "./CreditsSheet";
import { HelpSheet } from "./HelpSheet";

export const sheets3: Record<string, SheetComponent> = {
  account: AccountSheet,
  authMethods: AuthMethodsSheet,
  users: UsersSheet,
  update: UpdateSheet,
  credits: CreditsSheet,
  help: HelpSheet,
};

export { UsersScreen } from "./UsersScreen";
export { AboutScreen } from "./AboutScreen";
