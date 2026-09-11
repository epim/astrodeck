// ConnectOnceCard.tsx - FIRST NIGHT / CONNECT ONCE (plan A.2).
//
// This card is also where `components/NotConnectedInterstitial.tsx` goes to
// live. That component is NOT mounted anywhere in the new UI, and its four
// jobs each land on a row here rather than being dropped:
//
//   headline + one-liner   -> the card label and the body below it
//   "Go to Rig"            -> unnecessary; this IS the Rig screen. The OTHER
//                             hubs get the design's browse banner instead.
//   "Open the setup guide" -> OPEN THE SETUP GUIDE
//   "See what's up tonight" -> SEE WHAT IS UP TONIGHT, with its own note
//
// The viewer copy is the interstitial's, verbatim: a viewer cannot connect, so
// dangling a CTA at them leads to a read-only picker. They get the sentence
// that names who CAN, and the buttons stay on screen, dimmed, with the reason -
// nothing is hidden (ARCHITECTURE section 8).
//
// WHY THE SETUP-GUIDE ROW IS ALWAYS SHOWN HERE. The plan says show it "only
// while that checklist is incomplete". This card renders only when the rig is
// NOT connected, and "connect the devices" is one of the checklist's steps - so
// while this card exists, the checklist is incomplete by construction. That is
// the condition, satisfied without plumbing a second copy of the wizard's state
// through the screen.

import type { JSX } from "react";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import { nav } from "../../../router";
import { accessPhrase } from "../../../../lib/caps";
import type { ProfileRow } from "../../../../types";

export interface ConnectOnceCardProps {
  /** The active profile, or null when none is active (or its id dangles). */
  profile: ProfileRow | null;
  canConfig: boolean;
  /** `gate.ts`'s `LOCAL_ONLY_REASON` while this tab is on the relay, else null.
   *  All three verbs below reach a route on the rig's LAN fence (`/api/profiles`
   *  for the activate, `/api/connect` + `/api/drivers` for the simulator,
   *  `/api/discover` for the scan DETECT MY HARDWARE lands on), so the refusal
   *  is about the ORIGIN and outranks the capability sentence - an admin on the
   *  relay is refused too. */
  lanReason?: string | null;
  busy: boolean;
  onConnectProfile: (row: ProfileRow) => void;
  /** No profile yet: scan this computer for what is plugged into it. */
  onDetect: () => void;
  onSimulator: () => void;
  explain: (reason: string) => void;
}

const VIEWER_BODY = (
  <>
    Ask someone with {accessPhrase("config.backend")} to connect the rig, then
    this view comes alive.
  </>
);

function body(profileName: string | null): string {
  return profileName
    ? `Power the rig, then connect. The ${profileName} profile remembers every device, `
      + "so next time this step is a receipt, not a task. No hardware yet? The "
      + "simulator runs the whole app."
    : "Power the rig, then connect. Saving this rig as a profile afterwards makes "
      + "next time one tap. No hardware yet? The simulator runs the whole app.";
}

export function ConnectOnceCard(p: ConnectOnceCardProps): JSX.Element {
  // LAN first, capability second, busy last - the same order `gate.ts` uses.
  const lock = p.lanReason
    ?? (p.canConfig
      ? (p.busy ? "A rig action is already running - wait for it to finish." : null)
      : `needs ${accessPhrase("config.backend")}`);

  return (
    <Card tone="accent" padding={14} data-testid="first-night">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <Label size={11}>FIRST NIGHT · CONNECT ONCE</Label>

        <div style={{ fontSize: 12, color: "var(--text-2, #9aa6c2)", lineHeight: 1.5 }}>
          {p.canConfig ? body(p.profile?.name ?? null) : VIEWER_BODY}
        </div>

        {p.profile ? (
          <ActionButton
            kind="primary"
            size="lg"
            full
            data-testid="first-night-connect"
            lockedReason={lock}
            onExplain={p.explain}
            onPress={() => p.onConnectProfile(p.profile as ProfileRow)}
          >
            {`CONNECT ${p.profile.name.toUpperCase()} PROFILE`}
          </ActionButton>
        ) : (
          <ActionButton
            kind="primary"
            size="lg"
            full
            data-testid="first-night-detect"
            lockedReason={lock}
            onExplain={p.explain}
            onPress={p.onDetect}
          >
            DETECT MY HARDWARE
          </ActionButton>
        )}

        <ActionButton
          kind="ghost"
          full
          data-testid="first-night-sim"
          lockedReason={lock}
          onExplain={p.explain}
          onPress={p.onSimulator}
        >
          RUN THE SIMULATOR
        </ActionButton>

        <ActionButton
          kind="ghost"
          full
          data-testid="first-night-setup"
          onPress={() => nav.go("/settings/general/setup")}
        >
          OPEN THE SETUP GUIDE
        </ActionButton>

        <ActionButton
          kind="ghost"
          full
          data-testid="first-night-tonight"
          onPress={() => nav.hub("sky")}
        >
          SEE WHAT IS UP TONIGHT
        </ActionButton>
        <Mono size={11} tone="dim">Planning works with the rig switched off.</Mono>
      </div>
    </Card>
  );
}
