// sky/sheets.ts - the SKY hub's sheet registry (ARCHITECTURE.md section 5).
//
// Empty until the SKY task fills it in. The export must exist now because
// `hubs/index.ts` composes the global registry from all six at module load.

import type { SheetComponent } from "../sheets";

export const sheets: Record<string, SheetComponent> = {};
