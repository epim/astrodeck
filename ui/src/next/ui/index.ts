// next/ui/index.ts - the primitives library (ARCHITECTURE.md section 6).
//
// Every component here is store-free and fetch-free: props in, DOM out. That is
// what lets a hub be tested without a server and a viewer see the same screen
// an operator does. Interactive primitives take `lockedReason` + `onExplain`
// and render honest-disabled (see `honest.ts`); none of them owns the toast.
//
// The classes they emit live in `next/next.css`, which only `NextApp` imports.

export { Wordmark } from "./Wordmark";
export { Card } from "./Card";
export { Pill } from "./Pill";
export { Chip } from "./Chip";
export { Label } from "./Label";
export { Mono } from "./Mono";
export { Divider } from "./Divider";

export { ActionButton, type ActionButtonProps } from "./ActionButton";
export { IconButton48 } from "./IconButton48";
export { Switch } from "./Switch";
export { Segmented, type SegmentedOption } from "./Segmented";
export { Checkbox22 } from "./Checkbox22";
export { ReadoutGrid, ReadoutTile } from "./Readout";
export { Dial, type DialOption } from "./Dial";
export { RingGauge } from "./RingGauge";
export { Bar, type BarSegment } from "./Bar";
export { Stepper2 } from "./Stepper2";
export { Field } from "./Field";
export { TextInput } from "./TextInput";

export { StatusPill } from "./StatusPill";
export { IncidentCard, sinceLine } from "./IncidentCard";
export { BannerCard } from "./BannerCard";
export { EmptyCard } from "./EmptyCard";
export { ListRow } from "./ListRow";
export { DeviceGlyphTile } from "./DeviceGlyphTile";
export { Sheet } from "./Sheet";
export { Popover } from "./Popover";
export { SubNav, type SubNavItem } from "./SubNav";

export { lockedAttrs, lockedClass, honestPress } from "./honest";
export type { Tone, NxTone, Incident, IncidentAction } from "./types";
