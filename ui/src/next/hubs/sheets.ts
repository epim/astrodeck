// hubs/sheets.ts - the sheet contract (ARCHITECTURE.md section 5).
//
// A sheet is a plain component that takes the route's query params and its
// depth in the stack. It does NOT take an `onBack`: the `Sheet` primitive's
// BACK is wired by `SheetHost` to `nav.back()`, so every sheet in the app
// closes the same way and none of them can decide not to.

import type { JSX } from "react";

export interface SheetProps {
  params: Record<string, string>;
  /** 0 is the sheet the user opened; 1 is one opened on top of it (Mount -> Polar). */
  depth: 0 | 1;
}

export type SheetComponent = (p: SheetProps) => JSX.Element;

// --------------------------------------------------------------- the registry
//
// A registry entry is not the component: it is the component's MODULE IDENTITY
// plus the one line that fetches it. Sheets are code-split (D-FU-2), so a hub's
// registry can no longer hand `hubs/index.ts` a function object to compare -
// two hubs that register the same sheet (`sites` and `horizon` are registered
// by Sky and by Settings on purpose) each build their own `lazy()` wrapper, and
// comparing those by identity would report the contract working as a collision.
//
// `id` is what the duplicate check compares instead: the module path from
// `next/hubs`, spelled once per sheet. Same id, same screen - two hubs pointing
// at one module. Different ids under one name is the real fault, where the hash
// names a screen that depends on which module loaded last.
//
// `load` returns `{ default }` because that is what `React.lazy` takes; the
// sheets themselves keep their named exports, so nothing about a sheet file
// changes to be registered here.

export interface SheetEntry {
  /** Module path from `next/hubs`, e.g. `"sky/sheets/sites"`. Written by hand
   *  rather than derived, because there is nothing at runtime to derive it
   *  from - and it is the one string the duplicate check trusts. */
  id: string;
  load: () => Promise<{ default: SheetComponent }>;
}

/** What every hub's `sheets` export is: sheet name -> how to get the sheet. */
export type SheetRegistry = Record<string, SheetEntry>;
