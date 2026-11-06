// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// api/horizonSuggestion.ts - the horizon the rig learned from obstructed solve
// frames (WP 3.5, #599). Both routes need view.site_derived, so a viewer link is
// refused and never asks. Reading changes nothing on the rig; only accept writes
// (ruling D-07).
import { api } from "../api";

/** A run of neighbouring azimuth bins whose frames agree. `az_from` is greater
 *  than `az_to` when the run crosses north. */
export interface HorizonRegion {
  az_from: number;
  az_to: number;
  alt_min: number;
  alt_max: number;
  frames: number;
}

/** The offer: its regions and the whole line accept would store. `id` follows
 *  that line, so an accept names the offer that was shown. */
export interface HorizonSuggestion {
  id: string;
  frames: number;
  bins: number;
  nights: number;
  points: [number, number][];
  regions: HorizonRegion[];
}

/** `drawn` says whether the active site has a horizon line at all. */
export interface HorizonSuggestionState {
  drawn: boolean;
  suggestion: HorizonSuggestion | null;
}

/** GET /api/locations/horizon-suggestion. view.site_derived. */
export const getHorizonSuggestion = (): Promise<HorizonSuggestionState> =>
  api.get<HorizonSuggestionState>("/api/locations/horizon-suggestion");

/** POST /api/locations/horizon-suggestion/accept?suggestion_id=. 409
 *  `no_suggestion` or `suggestion_changed` when the offer moved; nothing is
 *  written then. Not reachable over the relay (the /api/locations fence). */
export const acceptHorizonSuggestion = (
  id: string,
): Promise<{ accepted: string; version: number }> =>
  api.post(`/api/locations/horizon-suggestion/accept?suggestion_id=${encodeURIComponent(id)}`);
