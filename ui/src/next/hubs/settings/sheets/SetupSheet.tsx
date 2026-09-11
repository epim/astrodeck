// SetupSheet.tsx - FIRST-TIME SETUP (`screenshots/23-first-time-setup.png`,
// plan section C.2.2).
//
// Five rows off the SAME live snapshot the card reads, so the sheet and the
// card can never disagree about what is done. Each row's right-hand word is the
// design's own: `GO ›` while a step is unfinished, `REVIEW` once it is - a
// finished step stays pressable, because "go and look at what you set" is a
// thing a user does the night after they set it.
//
// THE FOOTER PARAGRAPH IS TRUE BECAUSE THE DERIVATION IS LIVE. "Steps re-open
// if a device disappears or you image from a new site" is a promise a cached
// done-map would break the first time a USB cable came loose; every predicate
// here is recomputed from the current snapshot, so a step that stops being
// finished stops looking finished.
//
// DISMISS marks `first-run-wizard` in the `astrodeck-coach-seen` map - the same
// key the legacy docked wizard writes on Finish, so dismissing here and
// finishing there are one fact, not two. It hides the CARD, never this sheet:
// Settings > ABOUT > Help > SETUP GUIDE reopens it, and so does the row that
// sent you here.

import type { JSX } from "react";
import { ActionButton, Bar, Card, ListRow, Mono, Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useStore, useHasSeen } from "../../../../store";
import { WIZARD_SEEN_KEY } from "../../../../lib/coach";
import { SetupGlyph } from "../general/glyphs";
import type { SetupGo, SetupStep } from "../general/setupSteps";
import { useSetupFacts } from "../general/useSetup";
import type { SheetProps } from "../../sheets";

const FOOT = {
  fontSize: "11.5px", lineHeight: 1.5, color: "var(--text-faint)",
  padding: "0 2px", margin: "10px 0 0",
} as const;

function ringStyle(done: boolean): Record<string, string> {
  return {
    width: "28px", height: "28px", borderRadius: "50%",
    border: `1.5px solid ${done ? "var(--good)" : "var(--accent)"}`,
    background: done ? "color-mix(in srgb, var(--good) 12%, transparent)" : "transparent",
    color: done ? "var(--good)" : "var(--accent)",
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    fontFamily: '"IBM Plex Mono", monospace', fontSize: "11px",
  };
}

function open(go: SetupGo): void {
  if (go.kind === "sheet") nav.sheet(go.name, go.params);
  else nav.go(go.path);
}

function Row({ step }: { step: SetupStep }): JSX.Element {
  return (
    <ListRow
      icon={
        <span style={ringStyle(step.done)} aria-hidden="true">
          {step.done ? <NxIcon name="check" size={14} strokeWidth={2.4} /> : String(step.n)}
        </span>
      }
      title={step.title}
      sub={step.sub}
      right={<Mono tone={step.done ? "dim" : "accent"}>{step.act}</Mono>}
      onPress={() => open(step.go)}
      data-testid={`setup-step-${step.id}`}
    />
  );
}

export function SetupSheet(_p: SheetProps): JSX.Element {
  const { view } = useSetupFacts();
  const dismissed = useHasSeen(WIZARD_SEEN_KEY);
  const markSeen = useStore((s) => s.markSeen);

  return (
    <Sheet
      data-testid="settings-setup"
      title="FIRST-TIME SETUP"
      sub={`${view.doneCount} of 5 done`}
      icon={<SetupGlyph size={18} />}
      onBack={nav.back}
      backLabel="SETTINGS"
      footer={
        dismissed ? undefined : (
          <ActionButton
            kind="secondary"
            full
            onPress={() => { markSeen(WIZARD_SEEN_KEY); nav.back(); }}
            data-testid="setup-dismiss"
          >
            DONE - STOP SHOWING THE CARD
          </ActionButton>
        )
      }
    >
      <Bar
        value={view.doneCount / view.total}
        tone="accent"
        height={3}
        label={`${view.doneCount} of 5 steps done`}
        data-testid="setup-progress"
      />

      <Card padding={0}>
        {view.steps.map((s, i) => (
          <div
            key={s.id}
            style={i < view.steps.length - 1 ? { borderBottom: "1px solid var(--line)" } : undefined}
          >
            <Row step={s} />
          </div>
        ))}
      </Card>

      <p style={FOOT}>
        Five things, once. After that the app opens on Sky and every night is:
        point, image, watch. Steps re-open if a device disappears or you image
        from a new site.
      </p>
    </Sheet>
  );
}
