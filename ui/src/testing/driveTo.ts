// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// driveTo.ts -- drive a step wizard to a step BY NAME, in a test.
//
// A wizard test that says "press NEXT five times" is pinned to how many steps
// the wizard happens to have. #720: backlog WP-100 added the NIGHT and RESUME
// steps and six cases went red in files that had nothing to do with either,
// because each hard-coded how many presses reach REVIEW. The walk a test needs
// is "get to REVIEW", and that is what `driveTo("review", driver)` says.
//
// The helper knows nothing about React, jsdom or a harness: a test hands it a
// WizardDriver (what step is showing, how to press NEXT, why NEXT is locked)
// and `domWizardDriver` builds one over any Document with the wizard's own
// test ids. Every press must LEAVE the step it was on, and the walk is bounded,
// so a lock that holds the wizard or a step that is never reached fails here by
// name, with the steps it did pass, and never loops.

/** What the walker needs from a mounted wizard. */
export interface WizardDriver {
  /** The step showing now (`data-step` of the wizard's step marker), or null. */
  step(): string | null;
  /** Press NEXT. Throws, saying why, when NEXT is locked or absent. */
  press(): void;
  /** The wizard's own words for what is missing, for the failure message. */
  missing(): string;
}

/** The most presses a walk may take: far more than the wizard has steps. */
export const MAX_PRESSES = 12;

/** Press NEXT until the wizard is on `step`; return the steps it passed, the
 *  one it arrived on last. Already on `step` presses nothing. */
export function driveTo(step: string, driver: WizardDriver): string[] {
  const visited: string[] = [];
  for (let i = 0; i < MAX_PRESSES && driver.step() !== step; i++) {
    const was = driver.step();
    driver.press();
    const now = driver.step();
    if (now === was) {
      throw new Error(`NEXT did not leave ${was}: ${driver.missing()}`);
    }
    visited.push(String(now));
  }
  if (driver.step() !== step) {
    throw new Error(
      `NEXT never reached ${step}: stopped on ${driver.step()} (passed ${visited.join(", ")})`,
    );
  }
  return visited;
}

/** A driver over a document that carries the wizard's test ids
 *  (`wizard-step`, `wizard-next`, `wizard-missing`). `click` is the harness's
 *  own click (it wraps the dispatch in `act`). */
export function domWizardDriver(
  doc: Document,
  click: (el: Element) => void,
): WizardDriver {
  const byId = (id: string): Element | null => doc.querySelector(`[data-testid="${id}"]`);
  const missing = (): string => byId("wizard-missing")?.textContent ?? "";
  return {
    step: () => byId("wizard-step")?.getAttribute("data-step") ?? null,
    missing,
    press: () => {
      const next = byId("wizard-next")?.closest("button") ?? null;
      if (next === null) throw new Error("the wizard has no NEXT button");
      if (next.getAttribute("aria-disabled") === "true") {
        throw new Error(`NEXT is locked: ${missing() || (next.getAttribute("title") ?? "")}`);
      }
      click(next);
    },
  };
}
