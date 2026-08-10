// The AstroDeck design system — the library boundary.
//
// WHY THIS FILE EXISTS. `ui/` is a Vite APPLICATION: private, no `main`, no
// `exports`, and its build emits an app bundle (index.html + assets), not a
// library. That is fine for shipping the rig's UI and useless to anything that
// wants to BUILD with these components — including claude.ai/design, whose
// agent codes against the `.d.ts` this build now emits. Without a real entry
// the only option was synthesizing one from `src/`, which produces thin prop
// contracts; a thin contract is worse than a missing one, because the agent
// trusts it and misuses the API in every design it makes.
//
// WHAT BELONGS HERE, and what does not. This is the ~24 pieces that are
// genuinely a LIBRARY: primitives with no knowledge of the rig. The other ~100
// components in `src/components/` are screen-specific compositions —
// GuideQuickBar, PolarReticle, FilterNamesModal — which read the hub's status
// shape and would be nonsense to compose a new screen from. They are app code
// and deliberately absent.
//
// This file is exports ONLY. Anything with a side effect at import time (the
// zustand store, the API client, `index.css`) stays out: a consumer importing
// Panel must not thereby start polling a telescope.

// ---------------------------------------------------------------- primitives
export {
  Panel,
  Led,
  Field,
  Stat,
  Toggle,
  IconButton,
  Stepper,
  HoldButton,
  type HoldBind,
  Tooltip,
  InfoDot,
  Disclosure,
  EmptyState,
} from "./components/ui";

// ------------------------------------------------- the honest-disabled family
//
// One read-only presentation for the whole app (house rule §11.8). The native
// `disabled` attribute is never used for a control someone could plausibly want
// to press: it strips the element from the accessibility tree, taking the
// reason with it. These three carry the reason instead — `lockedProps` for a
// container, `LockedNote` for visible text, `LockedChip` for an inline
// stand-in, `HonestButton` for a control that must stay pressable and say why.
export {
  LOCKED_CLASS,
  lockedProps,
  LockedNote,
  LockedChip,
  HonestButton,
} from "./components/ui";

// ------------------------------------------------------------------ controls
export { SegmentedControl, segmentedNextIndex,
         type SegmentedControlProps } from "./components/ui/SegmentedControl";
export { default as PickerButton,
         type PickerOption } from "./components/ui/PickerButton";
export { default as StepDial } from "./components/ui/StepDial";
export { default as ActivityRing } from "./components/ui/ActivityRing";

// -------------------------------------------------------- the radial camera UI
//
// One control for every camera setting, used one-handed in the dark at the
// scope. Tap the disc, the categories bloom on an arc; tap a category, its
// values replace them. A category with more than four options opens RingPicker
// instead — the choice is derived from seat count, not judged.
export { default as CameraDial, dialPolar,
         type DialOption, type DialCategory } from "./components/ui/CameraDial";
export { default as RingPicker,
         type RingItem, type RingHub } from "./components/ui/RingPicker";
export {
  EXPOSURE_PRESETS_S,
  GUIDE_EXPOSURE_PRESETS_S,
  GAIN_PRESETS,
  BIN_PRESETS,
  fmtExposure,
  ExposurePicker,
} from "./components/ui/CameraPickers";

// -------------------------------------------------------------------- layout
export { Overlay, useMediaQuery, useIsLg,
         type OverlayProps, type OverlayVariant } from "./components/Overlay";

// --------------------------------------------------------------------- icons
//
// The v2 "instrument glyphs" set. 24x24, currentColor, and — the one behaviour
// worth knowing — stroke width auto-compensates by size when `strokeWidth` is
// omitted (1.7 at <=16px down to 1.4 above 26px), because a 1.5 stroke goes
// wispy at the 16px the desktop client renders.
export { Icon, type IconName, type IconProps } from "./components/icons";
