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
