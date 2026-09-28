// index.ts - the Target modal's door (#189 S4; spec 2026-09-23 flows mosaic,
// 2.1).
//
// THE SHEET IS LOADED LAZILY, following the entry-chunk rule D-FU-2
// (next/ARCHITECTURE.md section 5): it brings the survey canvas, the catalogue
// search and the night card, none of which the flow editor needs until the
// operator opens a block's framing. A caller mounts `TargetFramingSheetLazy`
// inside a Suspense boundary; a #/next sheet registry entry uses
// `loadTargetFramingSheet` as its `load`. Nothing here imports the sheet's
// module statically, so importing this file costs the entry chunk nothing.

import { lazy } from "react";

export type { TargetFramingSheetProps } from "./TargetFramingSheet";

/** The sheet's module, for a registry entry's `load`. */
export const loadTargetFramingSheet = () => import("./TargetFramingSheet");

/** The sheet as a lazy component: `<TargetFramingSheetLazy nodeId onClose />`. */
export const TargetFramingSheetLazy = lazy(loadTargetFramingSheet);
