// index.ts - the PEOPLE area's public surface (wave R7, T-R7-11).
//
// The three sheets import from here, never from a file inside the area, so the
// area's internal shape (rows, glyphs, the shared section shell, the model) can
// move without touching a mount file another task may also be editing.

export { UsersEditor } from "./UsersEditor";
export { AuthMethodsEditor } from "./AuthMethodsEditor";
export { AccountIdentity } from "./AccountIdentity";
export { AddUserForm } from "./AddUserForm";
export { PersonGlyph, ShieldGlyph } from "./glyphs";
export {
  DEFAULT_ROLE_VALUES, PRINCIPAL_ROLES, PEOPLE_CAP, breakGlassBlocker, roleWord,
  type DefaultRole,
} from "./peopleModel";
