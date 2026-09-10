// HelpArea.tsx - Help and troubleshooting, rebuilt in the design's vocabulary
// (wave R7, T-R7-16; replaces the mount of `views/HelpView.tsx`).
//
// This is the ROOT of the area and the only file that imports `help.css`, per
// the wave rule that `next.css` has exactly one owner (T-R7-0) and every
// rebuilt area carries its own sheet.
//
// Three blocks, in this order, and the order is the parity table's
// (`wave-r7.md` 3.D):
//
//   1. SETUP GUIDE   - the permanent re-entry into the first-run guide. First,
//                      because the person who arrives here having lost the
//                      docked bar is looking for the way back in, not for a
//                      glossary.
//   2. COMMON PROBLEMS - the ten `TROUBLESHOOTING` entries, deep-linkable.
//   3. GLOSSARY      - the sixteen `HELP` definitions.
//
// It reads no capability and renders no lock: nothing on this screen commands
// the rig, and a viewer who cannot press a single button elsewhere in the app
// still gets the whole of the help.
//
// The legacy view is untouched and `#/classic/help` still mounts it.

import type { JSX } from "react";
import { CommonProblems } from "./CommonProblems";
import { Glossary } from "./Glossary";
import { SetupGuideCard } from "./SetupGuideCard";
import "./help.css";

export function HelpArea(): JSX.Element {
  return (
    <div className="nx-help">
      <SetupGuideCard />
      <CommonProblems />
      <Glossary />
    </div>
  );
}
