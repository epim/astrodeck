// capturePresets.ts — NOV-4: one-tap beginner capture presets for the Capture view.
// Pure data; CaptureView maps a chip row over CAPTURE_PRESETS and applies a preset
// via the existing setExposure/setGain/setOffset/setBinning setters (the same path
// as "Match last lights", CaptureView.tsx:368). No new state shape.
export interface CapturePreset {
  id: string;
  label: string;
  blurb: string;
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
}

// Gain 100 / offset 30 mirror SequenceView.DEFAULT_STEP (SequenceView.tsx:28) so a
// preset-applied capture matches a hand-built plan step except where the intent
// differs. First-light uses a short, higher-gain frame for fast framing/focus.
export const CAPTURE_PRESETS: CapturePreset[] = [
  { id: "nebula-broadband", label: "Nebula (broadband)",
    blurb: "Faint broadband nebulosity — long subs, moderate gain.",
    exposure_s: 180, gain: 100, offset: 30, binning: 1 },
  { id: "galaxy", label: "Galaxy",
    blurb: "Small bright cores with faint arms — medium subs.",
    exposure_s: 120, gain: 100, offset: 30, binning: 1 },
  { id: "cluster", label: "Cluster",
    blurb: "Bright stars — short subs keep cores from clipping.",
    exposure_s: 60, gain: 100, offset: 30, binning: 1 },
  { id: "first-light", label: "First light",
    blurb: "Quick, high-gain frames to check framing and focus.",
    exposure_s: 5, gain: 200, offset: 30, binning: 1 },
];
