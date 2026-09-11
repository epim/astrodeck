// help/index.ts - what the rebuilt Help area exports.
//
// The mount file (`settings/sheets/HelpSheet.tsx`) composes exactly one of
// these: `HelpArea`. The rest are exported for the area's own tests and for
// anything that later needs one of these sentences to match word for word.

export { HelpArea } from "./HelpArea";
export { CommonProblems } from "./CommonProblems";
export { Glossary } from "./Glossary";
export { SetupGuideCard, SETUP_WHAT, SETUP_RESUME } from "./SetupGuideCard";
export {
  HIGHLIGHT_MS, HELP_KEYS, hyphenate, problemSub, seeAlsoLine, termLabel,
} from "./helpModel";
