// set2.ts - the sheets THIS task owns, handed to whichever task composes the
// SESSION hub's registry.
//
// Not `sheets/index.ts`: that file is the whole hub's registry (files, report,
// archive AND planEditor) and belongs to the task that owns `planEditor.tsx`.
// Two tasks writing one registry file is how a merge silently drops a sheet, so
// this half is exported on its own and spread in there:
//
//     import { sheets2 } from "./set2";
//     export const sheets = { ...sheets2, planEditor: PlanEditorSheet };
//
// Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
// collision), which is why they are plain words here rather than hub-prefixed.

import type { SheetComponent } from "../../sheets";
import { FilesSheet } from "./files";
import { ReportSheet } from "./report";
import { ArchiveSheet } from "./archive";

export const sheets2: Record<string, SheetComponent> = {
  files: FilesSheet,
  report: ReportSheet,
  archive: ArchiveSheet,
};
