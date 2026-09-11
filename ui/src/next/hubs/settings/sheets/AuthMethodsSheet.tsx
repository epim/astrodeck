// AuthMethodsSheet.tsx - Settings > USERS > "Sign-in methods" (plan section
// C.7.2), rebuilt for wave R7 (T-R7-11, cutover table section 7).
//
// It now mounts `tuning/people`'s `AuthMethodsEditor` instead of
// `components/settings/AuthMethodPanel.tsx`. The legacy panel is untouched and
// still serves `#/classic`.
//
// The capability check that used to live HERE (because `AuthMethodPanel` calls
// `listUsers()` for its guided card with no check of its own, assuming
// SettingsView had already gated it) now lives inside the editor, which needs
// it anyway to decide what to render. A deep link to this sheet without
// `admin.users` still issues no request; it lands on the editor's own
// read-only rendering, which states the capability rather than hiding the
// screen.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { Sheet } from "../../../ui";
import { AuthMethodsEditor, ShieldGlyph } from "../tuning/people";

export function AuthMethodsSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-authMethods"
      title="SIGN-IN METHODS"
      icon={<ShieldGlyph size={18} />}
      onBack={nav.back}
    >
      <AuthMethodsEditor />
    </Sheet>
  );
}
