// TonightPlanBlock.tsx - the literal SequencePlan, with nothing between it and
// the operator (parity row A21).
//
// This tab does NOT read `/tonight`. Its source is the compile the editor
// already ran - `flows.compiled` - and specifically `payload.plan`, not the
// four-key compile payload: the caption promises "the literal plan this graph
// compiles to", and folding the doctor's `issues` and `unmapped` lists into the
// JSON block would make the run's instructions and the advice about them
// indistinguishable.
//
// Because it needs no ephemeris, PLAN is the one tab that still answers while
// the site is unset and `/tonight` is refusing - which is why the sheet checks
// it before the error and empty branches.

import type { JSX } from "react";

import { useStore } from "../../../../../store";
import { ActionButton, Card, Label, Mono } from "../../../../ui";
import { CalibrationMatrixCard } from "./CalibrationMatrixCard";

/** Pre-async-clipboard copy path, mirroring `preview/LoupePanel.tsx`.
 *
 *  `navigator.clipboard` is UNDEFINED in a non-secure context, which is how
 *  AstroDeck is normally reached (plain http to the box at the scope), so an
 *  optional-chained `navigator.clipboard?.writeText(...)` evaluates to nothing
 *  on every field install that is not localhost - no copy, no error, no
 *  feedback. `execCommand` is deprecated and is the only route that works
 *  there. Returns whether the copy actually happened, so the button can be
 *  honest either way. */
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    // Off-screen but still selectable; `display:none` would break execCommand.
    ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0;";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export function TonightPlanBlock(): JSX.Element {
  // Narrow selectors: the plan changes when the compile lands, and a pan or a
  // node-status tick must not re-render this block.
  const plan = useStore((s) => s.flows.compiled?.plan ?? null);
  const compiling = useStore((s) => s.flows.compiling);
  const enqueueToast = useStore((s) => s.enqueueToast);

  const json = plan === null ? "" : JSON.stringify(plan, null, 2);
  const lines = json === "" ? 0 : json.split("\n").length;

  const copy = () => {
    if (!json) return;
    const settle = (ok: boolean) =>
      enqueueToast(ok
        ? { level: "success", title: "Plan JSON copied" }
        : { level: "warning", title: "Copy blocked by browser" });
    const clip = navigator.clipboard;
    if (clip?.writeText) {
      void clip.writeText(json).then(
        () => settle(true),
        () => settle(legacyCopy(json)),
      );
      return;
    }
    settle(legacyCopy(json));
  };

  return (
    <div className="nx-tn-stack" data-testid="tonight-plan">
      <CalibrationMatrixCard />

      <Card className="nx-tn-plan">
        <div className="nx-tn-plan-head">
          <Label size={10}>COMPILED PLAN</Label>
          {/* Never a native-`disabled` button with no plan: an inert rectangle
              takes its own reason out of the accessibility tree. With nothing
              to copy there is nothing to press, and the block below says why in
              words. */}
          {json !== "" && (
            <ActionButton
              kind="secondary"
              onPress={copy}
              data-testid="tonight-plan-copy"
              ariaLabel={`Copy the compiled plan, ${lines} lines of JSON`}
            >
              COPY JSON
            </ActionButton>
          )}
        </div>

        <Mono size={10} tone="dim">
          SequencePlan targets and steps, automation, and when/then instructions
          - nothing here the engine cannot run.
        </Mono>

        {json !== "" ? (
          <pre className="nx-tn-plan-json" data-testid="tonight-plan-json">{json}</pre>
        ) : (
          <p className="nx-tn-note">
            {compiling
              ? "Compiling this graph - the plan appears when the server answers."
              : "This graph has not compiled to a plan, so there is nothing here the "
                + "engine could run. The editor's validation chip says what is wrong with it."}
          </p>
        )}
      </Card>
    </div>
  );
}

export default TonightPlanBlock;
