// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// HorizonSuggestionOffer.tsx - the learned-horizon offer on the Site page (WP
// 3.5, #599, #1093). The rig files every obstructed solve frame under the
// direction it failed in; when a night's frames agree, the server offers the
// line that would have refused them. This card shows the offer and one accept
// control. Nothing is stored until the operator presses it (ruling D-07).
//
// It asks only a principal holding view.site_derived (admin, operator): the
// regions are computed from the site, so a viewer link neither fetches nor sees
// them. With no offer and no horizon drawn it says what the operator can do
// about that (#1093); with a horizon drawn and no offer it renders nothing.
import { useCallback, useEffect, useState, type JSX } from "react";
import { ApiError } from "../../../../api";
import {
  acceptHorizonSuggestion, getHorizonSuggestion,
  type HorizonSuggestionState,
} from "../../../../api/horizonSuggestion";
import { useCapability } from "../../../../lib/caps";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import { ACCEPT_NOTE, NO_HORIZON_GUIDE, acceptFailure, offerSummary, regionLine } from "./horizonSuggestionModel";

/** `onAccepted` runs after the server stored the line, so the editor beside the
 *  card can reload the line it shows. */
export function HorizonSuggestionOffer({ onAccepted }: { onAccepted?: () => void }): JSX.Element | null {
  const cleared = useCapability("view.site_derived");
  const [state, setState] = useState<HorizonSuggestionState | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await getHorizonSuggestion());
    } catch (e) {
      setState(null);
      setProblem(e instanceof ApiError ? `The horizon offer could not be loaded: ${e.message}` : "The horizon offer could not be loaded.");
    }
  }, []);

  useEffect(() => {
    if (cleared) void load();
  }, [cleared, load]);

  if (!cleared) return null;
  const offer = state?.suggestion ?? null;

  const accept = async () => {
    if (!offer) return;
    setBusy(true);
    setProblem(null);
    try {
      await acceptHorizonSuggestion(offer.id);
      await load();
      onAccepted?.();
    } catch (e) {
      const err = e as { code?: string; message?: string };
      setProblem(acceptFailure(err));
      if (err.code === "suggestion_changed" || err.code === "no_suggestion") await load();
    } finally {
      setBusy(false);
    }
  };

  if (!offer) {
    if (problem) return <Mono size={10.5} tone="warn" data-testid="horizon-offer-problem">{problem}</Mono>;
    if (state && !state.drawn) {
      return <Mono size={10.5} data-testid="horizon-offer-guide">{NO_HORIZON_GUIDE}</Mono>;
    }
    return null;
  }

  return (
    <Card tone="accent" data-testid="horizon-offer">
      <Label>LEARNED HORIZON</Label>
      <Mono size={11} data-testid="horizon-offer-summary">{offerSummary(offer)}</Mono>
      <ul data-testid="horizon-offer-regions">
        {offer.regions.map((r) => (
          <li key={`${r.az_from}-${r.az_to}`}><Mono size={10.5}>{regionLine(r)}</Mono></li>
        ))}
      </ul>
      <Mono size={10} tone="dim">{ACCEPT_NOTE}</Mono>
      <ActionButton kind="primary" size="md" busy={busy} onPress={() => void accept()} data-testid="horizon-offer-accept">
        Accept as the site horizon
      </ActionButton>
      {problem && <Mono size={10.5} tone="warn" data-testid="horizon-offer-problem">{problem}</Mono>}
    </Card>
  );
}
