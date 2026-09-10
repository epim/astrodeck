// session/sheets/index.ts - the SESSION hub's sheet registry
// (ARCHITECTURE.md section 5).
//
// Composed from several halves because several tasks built them: `set2.ts` is
// files / report / archive, written by the task that owns those files;
// `planEditor` is this one's; and the four Flows areas each publish their own
// `flow*Sheets` export. A single registry file edited by all of them is how a
// merge silently drops a sheet - the file resolves, the app builds, and one name
// is simply not in the map any more. Spreading a named export instead means a
// lost half is a compile error, not a missing screen.
//
// THIS FILE REPLACED `session/sheets.ts`, WHICH HAD TO GO. `hubs/index.ts` does
// `import { sheets } from "./session/sheets"`, and with both a `sheets.ts` and a
// `sheets/` directory present, the FILE wins in every resolver - so the empty
// placeholder shadowed this directory and the hub registered no sheets at all
// while looking perfectly correct. Deleting it is the fix; `sessionSheets.test`
// asserts it stays deleted, because re-creating it is a one-line mistake with no
// visible symptom until someone taps FILES.
//
// THE FLOWS IMPORTS GO TO THE REGISTRY MODULE, NOT THE AREA BARREL. This file is
// reached synchronously from `hubs/index.ts`, which is in the entry chunk, so
// every static import it makes is paid for before first paint (D-FU-2). Each
// `flow*Sheets` entry is `{ id, load }` with a dynamic `import()`, and every one
// of the four paths below is deep and component-free on purpose: an area barrel
// re-exports that area's whole component tree, so importing it for one registry
// object drags the tree in with it. Three of the four used to do exactly that
// (T-R7-20 measured +42.94 kB raw / +13.46 kB gzip on this entry chunk for the
// area CSS alone); `reg.ts` beside each area is the entries and nothing else.
//
// Names are GLOBAL across the app (`hubs/index.ts` throws on a collision), which
// is why they are plain words here rather than hub-prefixed.

import type { SheetRegistry } from "../../sheets";
import { sheets2 } from "./set2";
import { flowCanvasSheets } from "../flows/canvas/sheets";
import { flowInspectorSheets } from "../flows/inspector/reg";
import { flowTonightSheets } from "../flows/tonight/reg";
import { flowCreateSheets } from "../flows/create/reg";

export const sheets: SheetRegistry = {
  ...sheets2,
  // flowStages (phone stage list), flowNode + flowPalette (stage editor and the
  // add-stage list), flowTonight, flowNew + flowQuick. Six names, four owners,
  // zero shared files.
  ...flowCanvasSheets,
  ...flowInspectorSheets,
  ...flowTonightSheets,
  ...flowCreateSheets,
  // The plan editor drags `views/SequenceView` in with it - the single largest
  // sheet in the app - which is exactly why it is a dynamic import (D-FU-2)
  // rather than a line in front of first paint.
  planEditor: {
    id: "session/sheets/planEditor",
    load: () => import("./planEditor").then((m) => ({ default: m.PlanEditorSheet })),
  },
};
