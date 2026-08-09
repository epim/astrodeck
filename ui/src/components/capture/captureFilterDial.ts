// captureFilterDial.ts — Capture's own FILT ring for the speed dial (#181/#179).
//
// Every other screen builds its filter ring with `cameraDialCategories`, and
// must: that builder DROPS the blackout slots, once, for every caller, because a
// focus or solve frame through a carrier with no glass measures nothing at every
// position — which on 2026-08-08 was not a bad result but a hang (fourteen
// re-exposures at one focuser position).
//
// Capture is the exception, and it is a real one rather than an oversight:
//
//   * A pick here is a COMMAND. It moves the wheel now, through the same POST
//     the Filter Wheel panel below the preview uses. Everywhere else a filter
//     pick PINS a value for a frame that has not been taken yet.
//   * Parking on the blackout slot is a workflow. It is how you shoot darks on
//     a rig with a wheel, and Capture is the one screen in the app where that is
//     a thing an operator deliberately does. So the slots are offered — and each
//     one SAYS "blackout" on its face, so choosing it is a choice and not a
//     mistake.
//
// Kept out of the view so the two rules above are assertions rather than a
// mounted 1900-line screen: `__tests__/captureFilterDial.test.ts`.

import type { DialCategory } from "../ui/CameraDial";

export interface WheelState {
  names?: readonly (string | null | undefined)[];
  opaque?: readonly boolean[];
  position?: number | null;
}

/** The FILT category for Capture's dial, or null when there is no wheel worth
 *  offering. Options are keyed by SLOT INDEX, never by name: two slots may carry
 *  the same name and the wheel only knows positions — the 2026-08-02 offset bug
 *  shifted every frame's FILTER header by one slot, and a name-keyed control
 *  cannot even express which slot it meant. */
export function captureFilterDialCategory(
  wheel: WheelState | null | undefined,
  onPick: (slot: number) => void,
): DialCategory | null {
  const names = wheel?.names ?? [];
  const usable = names
    .map((name, i) => ({ name, i }))
    // A blank slot name is not an option, it is a gap in the wheel's
    // configuration — the same rule the shared builder applies. Blackout slots
    // are NOT dropped here; see the header.
    .filter(({ name }) => !!name && String(name).trim().length > 0);
  if (usable.length === 0) return null;
  return {
    id: "filter",
    label: "FILT",
    icon: "frame",
    options: usable.map(({ name, i }) => ({
      id: String(i),
      label: wheel?.opaque?.[i] ? `${String(name).trim()} — blackout` : String(name).trim(),
    })),
    selected: typeof wheel?.position === "number" ? String(wheel.position) : undefined,
    onPick: (id) => onPick(Number(id)),
  };
}
