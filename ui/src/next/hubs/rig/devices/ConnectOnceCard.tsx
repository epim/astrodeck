// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
import { RECONNECT_CAP } from "../profiles/profilesModel";

export interface ConnectOnceCardProps {
  /** The active profile, or null when none is active (or its id dangles). */
  profile: ProfileRow | null;
  /** `config.backend` (admin): what DETECT MY HARDWARE and RUN THE SIMULATOR
   *  need. NOT what CONNECT <profile> needs - see `canReconnect`. */
  canConfig: boolean;
  /** `control.reconnect` (operator and admin, #759): what CONNECT <profile>
   *  needs. An operator may bring a saved profile back and may not scan, run the
   *  simulator or write a profile, so the two are separate props and the card
   *  gates each verb on its own. */
  canReconnect: boolean;
  /** `gate.ts`'s `LOCAL_ONLY_REASON` while this tab is on the relay, else null.
   *  It locks DETECT MY HARDWARE and RUN THE SIMULATOR, which reach routes on the
   *  rig's LAN fence (`/api/connect` + `/api/drivers` for the simulator,
   *  `/api/discover` for the scan DETECT lands on): the refusal is about the
   *  ORIGIN and outranks the capability sentence - an admin on the relay is
   *  refused too.
   *
   *  It does NOT lock CONNECT <profile>. Activating a saved profile is the one
   *  `/api/profiles` call the rig lets through the fence (`POST
   *  /api/profiles/<id>/activate` without `force`, #685), so that button follows
   *  its capability (`canReconnect`) and the busy flag alone, as the popover's
   *  ACTIVATE does. */
  lanReason?: string | null;
  busy: boolean;
  onConnectProfile: (row: ProfileRow) => void;
  /** No profile yet: scan this computer for what is plugged into it. */
  onDetect: () => void;
  onSimulator: () => void;
  explain: (reason: string) => void;
}

/** Who to ask. With a profile to bring back the answer is whoever may reconnect
 *  (operator or admin); with none, nothing short of `config.backend` can scan or
 *  start the simulator, so it is admin. Derived from the role table, not
 *  hand-written, so the sentence names the policy the buttons enforce. */
function askBody(hasProfile: boolean): JSX.Element {
  return (
    <>
      Ask someone with {accessPhrase(hasProfile ? RECONNECT_CAP : "config.backend")} to
      connect the rig, then this view comes alive.
    </>
  );
}

function body(profileName: string | null, canConfig: boolean): string {
  return profileName
    ? `Power the rig, then connect. The ${profileName} profile remembers every device, `
      + "so you can connect them again with one tap. "
      + (canConfig
        ? "No hardware yet? The simulator runs the whole app."
        // An operator's CONNECT works and the simulator row beneath it does not:
        // the card must not invite a press it will refuse.
        : `Detecting hardware and the simulator need ${accessPhrase("config.backend")}.`)
    : "Power the rig, then connect. Saving this rig as a profile afterwards makes "
      + "next time one tap. No hardware yet? The simulator runs the whole app.";
}

export function ConnectOnceCard(p: ConnectOnceCardProps): JSX.Element {
  const busyNote = "A rig action is already running - wait for it to finish.";
  // Capability, then busy: what CONNECT <profile> answers to on any origin. Its
  // capability is `control.reconnect`, which an operator holds (#759).
  const connectLock = p.canReconnect
    ? (p.busy ? busyNote : null)
    : `needs ${accessPhrase(RECONNECT_CAP)}`;
  // What the scan and the simulator answer to: `config.backend`, admin only.
  const backendLock = p.canConfig
    ? (p.busy ? busyNote : null)
    : `needs ${accessPhrase("config.backend")}`;
  // LAN first, capability second, busy last - the same order `gate.ts` uses -
  // for the two verbs the rig's relay fence refuses.
  const lock = p.lanReason ?? backendLock;

  return (
    <Card tone="accent" padding={14} data-testid="first-night">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <Label size={11}>FIRST NIGHT · CONNECT ONCE</Label>

        <div style={{ fontSize: 12, color: "var(--text-2, #9aa6c2)", lineHeight: 1.5 }}>
          {p.profile
            ? (p.canReconnect ? body(p.profile.name, p.canConfig) : askBody(true))
            : (p.canConfig ? body(null, true) : askBody(false))}
        </div>

        {p.profile ? (
          <ActionButton
            kind="primary"
            size="lg"
            full
            data-testid="first-night-connect"
            lockedReason={connectLock}
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
