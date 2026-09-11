// reg-video.ts - T-U7b-4's one line into the Rig hub's sheet registry.
//
// The registry itself (`rig/sheets/index.ts`) is composed by the Rig devices
// task and is never opened by a sheet task: each contributes its own fragment
// and the composer spreads it. Same discipline as `reg-safety.ts`.

import type { SheetRegistry } from "../../sheets";

export const sheetsVideo: SheetRegistry = {
  videoLibrary: { id: "rig/sheets/videoLibrary", load: () => import("./videoLibrary").then((m) => ({ default: m.VideoLibrarySheet })) },
};
