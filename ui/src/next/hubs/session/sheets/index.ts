// session/sheets/index.ts - the SESSION hub's sheet registry
// (ARCHITECTURE.md section 5).
//
// Composed from two halves because two tasks built them: `set2.ts` is files /
// report / archive, written by the task that owns those files, and `planEditor`
// is this one's. A single registry file edited by both is how a merge silently
// drops a sheet - the file resolves, the app builds, and one name is simply not
// in the map any more. Spreading a named export instead means a lost half is a
// compile error, not a missing screen.
//
// THIS FILE REPLACED `session/sheets.ts`, WHICH HAD TO GO. `hubs/index.ts` does
// `import { sheets } from "./session/sheets"`, and with both a `sheets.ts` and a
// `sheets/` directory present, the FILE wins in every resolver - so the empty
// placeholder shadowed this directory and the hub registered no sheets at all
// while looking perfectly correct. Deleting it is the fix; `sessionSheets.test`
// asserts it stays deleted, because re-creating it is a one-line mistake with no
// visible symptom until someone taps FILES.
//
// Names are GLOBAL across the app (`hubs/index.ts` throws on a collision), which
// is why they are plain words here rather than hub-prefixed.

import type { SheetRegistry } from "../../sheets";
import { sheets2 } from "./set2";

export const sheets: SheetRegistry = {
  ...sheets2,
  // The plan editor drags `views/SequenceView` in with it - the single largest
  // sheet in the app - which is exactly why it is a dynamic import (D-FU-2)
  // rather than a line in front of first paint.
  planEditor: {
    id: "session/sheets/planEditor",
    load: () => import("./planEditor").then((m) => ({ default: m.PlanEditorSheet })),
  },
};
