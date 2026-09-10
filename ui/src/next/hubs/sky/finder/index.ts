// finder/index.ts - the boundary the rest of the Sky hub imports.
//
// T-SKY-2 (the hub shell, the cards, the lens dial, FRAME) codes against exactly
// what is re-exported here and never reaches into the modules behind it. Two
// names carry the whole contract: `useSkyModel(boxPx)` derives everything the hub
// renders, and `SkyView` draws the box. Everything else on this list is either a
// type those two hand back or a constant a card has to agree with (the lock
// radius, the 25 degree floor, the five kinds).

export { SkyView, type SkyViewProps } from "./SkyView";
export {
  useSkyModel,
  windowLabel,
  MAX_MARKERS,
  LAYERS_NOTE_DEFAULT,
  LAYERS_NOTE_NO_WEATHER,
  LAYERS_NOTE_NO_HORIZON,
  WIND_NOTE_NONE,
  RANK_NEEDS_SITE,
  NO_COORDS_NOTE,
  type SkyModel,
  type ReticleModel,
  type PatchModel,
} from "./model";

export {
  KIND_ICON,
  KIND_LABEL,
  SKY_KINDS,
  CLOUDED_PCT,
  LOCK_RADIUS_PX,
  pickLock,
  decorate,
  displayName,
  fullName,
  kindOf,
  mergeRows,
  narrowbandSlots,
  paletteFor,
  type CatalogRowLike,
  type Marker,
  type MergedRow,
  type SkyKind,
  type SkyTarget,
  type WheelLike,
} from "./targets";

export {
  FLOOR_DEG,
  TRACK_COLORS,
  buildTrack,
  classify,
  isObstructedAt,
  minutesAboveFloor,
  walkTrack,
  type TrackContext,
  type TrackRender,
  type TrackSample,
  type TrackSegment,
  type TrackState,
} from "./track";

export {
  PPD,
  PPF,
  altLines,
  altStrOf,
  azStrOf,
  boxHeightFor,
  compassTicks,
  makeProjector,
  ppdFor,
  type AltLine,
  type CompassTick,
  type Projector,
} from "./projection";

export {
  D2R,
  R2D,
  SIDEREAL_DEG_PER_HOUR,
  decDmsStr,
  eq,
  hz,
  raDecFromAltAz,
  raHmsStr,
  wrapRaHours,
} from "./equatorial";

export {
  TILE_DEG,
  TILE_MIN_PCT,
  cloudBlobLabels,
  cloudPctAt,
  cloudRects,
  domeToSamples,
  tilesFromDome,
  type CloudLabel,
  type CloudRect,
} from "./clouds";

export {
  compassPoint,
  cloudBaseKm,
  minutesToDeckAtZenith,
  windArrows,
  windFrom,
  windLine,
  type WindArrow,
  type WindModel,
} from "./wind";

export {
  panView,
  useSkyGestures,
  type SkyGestureHandlers,
  type SkyGestureOptions,
} from "./gestures";

export {
  cameraErrorMessage,
  cameraSupport,
  startCamera,
  stopCamera,
} from "./camera";

export { gyroSupport, headingOf, startGyro, type GyroHandle } from "./gyro";

// `SkyModel.horizonPoints` is typed from the shared pure library; re-exported
// here so a card does not have to know which module under `next/lib` it lives in.
export type { HorizonPoint } from "../../../lib/horizonModel";

export * as skyPrefs from "./prefs";
export type { LayerPrefs, LensPrefs, QuickPrefs, SkyMode, FrameMode } from "./prefs";
