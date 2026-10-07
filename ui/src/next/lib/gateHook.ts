// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// gateHook.ts - the THIN React wrapper over gate.ts's pure `lockReason`
// (ARCHITECTURE.md #8's `useLock`). Split into its own file, deliberately
// NOT in gate.ts, so `gate.ts` stays store-free/React-free like the rest of
// `next/lib` (the T0.3 brief's explicit deviation from ARCHITECTURE.md's
// single-file sketch, where `useLock` is shown alongside `lockReason`).
//
// This is the one file in `next/lib` allowed to import React and the store.
// `useLock` is NOT unit-tested here (a thin hook has nothing to assert beyond
// "it calls lockReason with the store's four fields and wires onExplain to
// enqueueToast" - both already covered by gate.test.ts and the store's own
// tests); a DOM test belongs with whichever primitive/hub first consumes it.
// `useStepUp` (the recent-sign-in state at the bottom, #685) is not that thin and
// is exercised through both of its consumers: `settings/__tests__/
// relayFenceDom.test.tsx` (the new UI's PEOPLE editor) and `components/settings/
// __tests__/w15UsersPanelStepUp.test.tsx` (the classic panel).
//
// The fifth field is `onRelay`, from `next/lib/relay.ts` - the SAME derivation
// Settings > Connection draws its cards from, so a control's "you are on the
// relay" and that screen's "you are here" can never disagree. It is a
// subscription rather than a read so that a control rendered before `GET
// /api/remote/status` answered re-renders when the rig's own `via` arrives.

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { ApiError } from "../../api";
import { localLogin } from "../../api/backends";
import { u } from "../../lib/base";
import {
  usePrincipal, useStatus, useEquipConnected, useWsPhase, useStore, useAuthMethods,
} from "../../store";
import { lockReason, type GateInput } from "./gate";
import { onRelay, subscribeRelay } from "./relay";

export interface UseLockResult {
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

/** True while this tab is tunnelled through the relay. Exported for the few
 *  surfaces that need the fact itself rather than a lock reason (a note on a
 *  screen, a branch in a model); every CONTROL should take `needsLan: true`
 *  through `useLock` instead, so the sentence is written once. */
export function useOnRelay(): boolean {
  return useSyncExternalStore(subscribeRelay, onRelay, onRelay);
}

export function useLock(inp: GateInput): UseLockResult {
  const principal = usePrincipal();
  const status = useStatus();
  const equipConnected = useEquipConnected();
  const wsPhase = useWsPhase();
  const relay = useOnRelay();

  const lockedReason = lockReason(inp, {
    principal, status, equipConnected, wsPhase, onRelay: relay,
  });

  const onExplain = (reason: string) => {
    useStore.getState().enqueueToast({ level: "warning", title: reason });
  };

  return { lockedReason, onExplain };
}

// ----------------------------------------------------------- the recent sign-in
// Over the relay the rig asks for a sign-in under five minutes old before it
// changes who may use it (`server/astrodeck/auth/deps.py`, #685). A screen that
// makes such a change needs the same four things: to know whether the rig has
// asked, to offer the sign-in methods this rig actually has, to run the local
// one without leaving the page, and to send the Google one through its full-page
// redirect. This hook holds that, and no copy: the words belong to the screen.

/** How long a re-sign-in is shown as open before the hook goes back to `idle`.
 *  The rig's own window is `STEP_UP_MAX_AGE_S` (300 s) and is the authority; the
 *  margin makes the note retire BEFORE the rig stops honouring the sign-in, so
 *  the screen never claims a minute it no longer has. */
export const STEP_UP_NOTE_MS = 285_000;

/** `idle`: nothing asked. `required`: the rig refused a change for want of a
 *  recent sign-in (or the person opened the form first). `fresh`: a local
 *  sign-in just succeeded here. A Google sign-in leaves the page, so it has no
 *  `fresh` of its own: the next load starts at `idle`. */
export type StepUpPhase = "idle" | "required" | "fresh";

export type StepUpFailure = "failed" | "rate_limited";

export interface UseStepUpResult {
  phase: StepUpPhase;
  /** Which sign-ins this rig offers, from `GET /api/auth/methods`. Both false
   *  until that has answered - the screen then offers neither rather than a
   *  button that may lead nowhere. */
  local: boolean;
  google: boolean;
  /** The address the principal is signed in as, to prefill the username. */
  username: string;
  busy: boolean;
  failure: StepUpFailure | null;
  /** The rig refused for want of a recent sign-in, or the person asked to sign
   *  in before trying. */
  markRequired: () => void;
  /** POST /auth/local. True on success; the principal is re-read so the screen
   *  sees the new session. */
  signInLocal: (username: string, password: string) => Promise<boolean>;
  /** Full-page redirect to the Google sign-in (`GET /auth/login`). With
   *  `returnTo` (a bare in-app hash route, `#/...`) the rig brings the browser
   *  back to that route after the callback instead of the home screen (#733);
   *  the rig refuses anything that is not such a route, and falls back to the
   *  home screen. */
  signInGoogle: (returnTo?: string) => void;
}

/** The address the Google sign-in starts at: the rig's login route under the
 *  relay's mount prefix when there is one (`u`), with `returnTo` as `return`
 *  when given. Percent-encoded, because a `#` in a query string is not part of
 *  the query. The one builder both UIs use (#733). */
export function googleLoginHref(returnTo?: string): string {
  const base = u("/auth/login");
  return returnTo ? `${base}?return=${encodeURIComponent(returnTo)}` : base;
}

export function useStepUp(): UseStepUpResult {
  const methods = useAuthMethods();
  const principal = usePrincipal();
  const [phase, setPhase] = useState<StepUpPhase>("idle");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<StepUpFailure | null>(null);

  // `fresh` retires itself. The timer is cleared with the effect, so a screen
  // that unmounts mid-window leaves nothing running.
  useEffect(() => {
    if (phase !== "fresh") return undefined;
    const t = setTimeout(() => setPhase("idle"), STEP_UP_NOTE_MS);
    return () => clearTimeout(t);
  }, [phase]);

  const markRequired = useCallback(() => {
    setFailure(null);
    setPhase((p) => (p === "fresh" ? p : "required"));
  }, []);

  const signInLocal = useCallback(async (username: string, password: string) => {
    setBusy(true);
    setFailure(null);
    try {
      await localLogin(username.trim(), password);
      await useStore.getState().loadPrincipal();
      setPhase("fresh");
      return true;
    } catch (e) {
      setFailure(e instanceof ApiError && e.status === 429 ? "rate_limited" : "failed");
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const signInGoogle = useCallback((returnTo?: string) => {
    window.location.href = googleLoginHref(returnTo);
  }, []);

  const enabled = methods?.methods ?? [];
  return {
    phase,
    local: enabled.includes("local"),
    google: enabled.includes("google") && !!methods?.google_configured,
    username: principal?.email ?? "",
    busy,
    failure,
    markRequired,
    signInLocal,
    signInGoogle,
  };
}
