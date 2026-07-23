// sequenceTemplates.ts — starter exposure recipes for first-timers (NOV-5). Pure
// data + a pure template->ExposureStep[] mapping; SequenceView renders a thin
// gallery over SEQUENCE_TEMPLATES and applies templateSteps() through
// setPlanWithUndo. No new PLAN shape — the output is plain ExposureStep[]
// (types.ts:386), mirroring the plan-library "logic in lib, thin render"
// split (lib/planLibrary.ts).
import type { ExposureStep } from "../types";

// Non-exposure step baseline. Mirrors SequenceView.DEFAULT_STEP's gain/offset/
// bin/frame_type (SequenceView.tsx:26-28) so a template-applied step is
// indistinguishable from a hand-added one except in the values a template sets.
const STARTER_BASE = { gain: 100, offset: 30, binning: 1, frame_type: "Light" } as const;

// Filter-intent aliases (lowercased). A mono wheel usually exposes "L"; the
// Luminance starter should still land on it. Broadband starters use null intent.
const FILTER_ALIASES: Record<string, string[]> = {
  luminance: ["luminance", "lum", "l"],
};

export interface TemplateStepSpec {
  filter: string | null;
  exposure_s: number;
  count: number;
}

export interface SequenceTemplate {
  id: string;
  label: string;
  blurb: string;
  steps: TemplateStepSpec[];
}

export const SEQUENCE_TEMPLATES: SequenceTemplate[] = [
  {
    id: "lum-60x120",
    label: "60 × 120s Luminance",
    blurb: "Deep mono luminance — the backbone of an LRGB image.",
    steps: [{ filter: "Luminance", exposure_s: 120, count: 60 }],
  },
  {
    id: "osc-30x180",
    label: "OSC broadband 30 × 180s",
    blurb: "One-shot-colour broadband — no filter wheel needed.",
    steps: [{ filter: null, exposure_s: 180, count: 30 }],
  },
  {
    id: "quick-20x60",
    label: "Quick 20 × 60s test",
    blurb: "A fast test run to check framing, focus and tracking.",
    steps: [{ filter: null, exposure_s: 60, count: 20 }],
  },
];

export function resolveFilter(intent: string | null, available: string[]): string | null {
  if (intent == null) return null;
  const key = intent.trim().toLowerCase();
  const wanted = FILTER_ALIASES[key] ?? [key];
  const hit = available.find((n) => wanted.includes(n.trim().toLowerCase()));
  return hit ?? null;
}

export function templateSteps(t: SequenceTemplate, available: string[] = []): ExposureStep[] {
  return t.steps.map((s) => ({
    ...STARTER_BASE,
    filter: resolveFilter(s.filter, available),
    exposure_s: s.exposure_s,
    count: s.count,
  }));
}
