// TonightPlan.tsx — the literal SequencePlan, with nothing between it and the
// operator.
//
// This tab does NOT read /tonight (§E.5). Its source is the compile the editor
// already ran — `flows.compiled` — and specifically `payload.plan`, not the
// four-key compile payload: the caption promises "the literal plan this graph
// compiles to", and putting the doctor's `issues` and `unmapped` lists inside
// the JSON block would make the run's instructions and the advice about them
// indistinguishable. §G-19 is open only on that scoping question; the block and
// COPY JSON show the same subset either way.

import { useStore } from "../../store";
import type { JSX } from "react";

/** Pre-async-clipboard copy path, mirroring `preview/LoupePanel.tsx`.
 *  `navigator.clipboard` is UNDEFINED in a non-secure context, which is how
 *  AstroDeck is normally reached (plain http to the box at the scope), so an
 *  optional-chained `navigator.clipboard?.writeText(...)` evaluates to nothing
 *  on every field install that is not localhost — no copy, no error, no
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

export function TonightPlan(): JSX.Element {
  // Narrow selectors: the plan changes when the compile lands, and a pan or a
  // node-status tick must not re-render this block.
  const plan = useStore((s) => s.flows.compiled?.plan ?? null);
  const compiling = useStore((s) => s.flows.compiling);
  const enqueueToast = useStore((s) => s.enqueueToast);

  const json = plan === null ? "" : JSON.stringify(plan, null, 2);

  const copy = () => {
    if (!json) return;
    const settle = (ok: boolean) =>
      enqueueToast(ok
        ? { level: "success", title: "Plan JSON copied" }
        : { level: "warning", title: "Copy blocked by browser" });
    const nav = navigator.clipboard;
    if (nav?.writeText) {
      void nav.writeText(json).then(
        () => settle(true),
        () => settle(legacyCopy(json)),
      );
      return;
    }
    settle(legacyCopy(json));
  };

  return (
    <>
      <div className="flex items-center justify-between gap-2.5 mb-2">
        <span className="text-[11px] text-dim leading-[1.45] [text-wrap:pretty]">
          The literal plan this graph compiles to — SequencePlan targets + steps,
          automation, and when/then instructions. Nothing here the engine can't run.
        </span>
        {/* Never a `disabled` button with no plan: an inert grey rectangle
            takes its own reason out of the accessibility tree (house rule
            §11.8). With nothing to copy there is nothing to press, and the
            block below says why in words. */}
        {json && (
          <button
            type="button"
            onClick={copy}
            className="flex-none font-display font-semibold text-[9.5px] tracking-[0.14em]
              px-2.5 py-[7px] rounded-lg border border-accent2 text-accent
              bg-[color-mix(in_srgb,var(--accent)_8%,transparent)]"
          >
            COPY JSON
          </button>
        )}
      </div>

      {json ? (
        <pre className="whitespace-pre overflow-auto font-mono text-[10px] leading-[1.55]
          text-dim bg-bg border border-line rounded-lg p-3 max-h-[56dvh]">
          {json}
        </pre>
      ) : (
        <p className="text-[12px] leading-[1.5] text-dim [text-wrap:pretty]">
          {compiling
            ? "Compiling this graph — the plan appears when the server answers."
            : "This graph has not compiled to a plan, so there is nothing here the "
              + "engine could run. The editor's validation chip says what is wrong with it."}
        </p>
      )}
    </>
  );
}

export default TonightPlan;
