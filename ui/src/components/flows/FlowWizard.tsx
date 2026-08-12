// FlowWizard.tsx — the guided "NEW FLOW" sheet. Contract §C.14, ref 09.
//
// THE GENERATOR IS NOT CONNECTED, AND THIS FILE DOES NOT PRETEND OTHERWISE.
// `server/astrodeck/flows/wizard.py` has `generate()` / `generate_record()` /
// `flow_name()` and **no route reaches any of them** (contract §E.7, §G-2 item
// 4). The two ways to close that are (a) add `POST /api/flows/wizard`, or (b)
// re-implement `genWizard()` in TypeScript as a third transcription of the same
// rules — and §G-2 is explicit that the choice is the user's, not this file's.
// So the sheet collects its three answers exactly as designed and GENERATE FLOW
// is an honest-disabled control that SAYS the generator is unreachable. A button
// that quietly built a graph from a TS copy of the rules would be the drift the
// open question exists to prevent; one that silently did nothing would be worse.
//
// START BLANK is real: `POST /api/flows` exists and is the same route the
// generated record would have been saved through.
import { useState, type CSSProperties } from "react";
import { Overlay } from "../Overlay";
import { HonestButton, LockedNote } from "../ui";
import { accessPhrase, useCanControlCapture } from "../../lib/caps";
import { handleRadioKeyDown, rovingTabIndex } from "../../lib/radiogroup";
import { flowsApi } from "../../lib/flowsApi";
import { useStore } from "../../store";
import { NODE_DEFS } from "./nodeDefs";
import type { FlowNodeRec } from "./flowsTypes";

/** Question 1. Order and labels from §C.14; `Deep-sky target` is the default. */
const KINDS = ["Deep-sky target", "Best of several", "EAA quick look"] as const;
type WizardKind = (typeof KINDS)[number];

/** Question 2, in the contract's order. `Guiding` and `HFR watchdog` start on. */
const AUTOMATIONS = [
  "Guiding", "Dusk flats", "Dome",
  "Cloud-dodge calibration", "HFR watchdog", "Notify my phone",
] as const;
const AUTOMATION_DEFAULTS: readonly string[] = ["Guiding", "HFR watchdog"];

/** The literal run of spaces is part of the placeholder — it is what separates
 *  the single-target example from the comma-list one without a second field. */
const TARGET_PLACEHOLDER = "M16    ·    or: M16, M17, M8, NGC 6946";

/** Why GENERATE FLOW cannot act. One short claim + one paragraph of why, both
 *  shown in BOTH places — as the visible LockedNote under the blurb and as the
 *  toast a press produces. Two wordings for one refusal is how an operator ends
 *  up unsure whether they hit two different problems. */
export const GENERATE_BLOCKED =
  "Generating is not connected yet — nothing on the rig exposes the generator.";
export const GENERATE_BLOCKED_WHY =
  "The rig can build a graph from these answers, but no endpoint reaches that "
  + "code. START BLANK opens an empty canvas; the library's Examples are "
  + "editable copies.";

// The blank flow the prototype's `loadPipe("new")` produces (line 891): a TARGET
// and a SLEW, unwired, at these coordinates. Two nodes rather than none because
// an empty canvas gives the operator nothing to drag a wire from.
const BLANK_NAME = "Untitled flow";
const BLANK_TAGLINE = "Started blank";
const BLANK_NODES: ReadonlyArray<{ type: "target" | "slew"; x: number; y: number }> = [
  { type: "target", x: 60, y: 120 },
  { type: "slew", x: 320, y: 120 },
];

// Node ids are minted client-side and are only local handles. Prefixed for the
// reason flowsSlice's `nextNodeId` documents: a collision with a server-minted
// id would silently rewire a graph, so it is made impossible rather than
// unlikely. Not shared with the slice's counter — these ids never coexist with
// slice-minted ones in the same graph, they ARE the graph.
let blankSeq = 0;
const blankNodes = (): FlowNodeRec[] =>
  BLANK_NODES.map((n) => ({
    id: `w${++blankSeq}_${Date.now().toString(36)}`,
    type: n.type,
    x: n.x,
    y: n.y,
    params: { ...NODE_DEFS[n.type].params },
  }));

export default function FlowWizard() {
  const open = useStore((s) => s.flows.ui.wizardOpen);
  const setUi = useStore((s) => s.flowsSetUi);
  const flowsOpen = useStore((s) => s.flowsOpen);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const canCreate = useCanControlCapture();

  // The three answers live here, not in the store: nothing outside this sheet
  // reads them, and FlowsUiState deliberately carries only `wizardOpen`.
  const [kind, setKind] = useState<WizardKind>(KINDS[0]);
  const [autos, setAutos] = useState<readonly string[]>(AUTOMATION_DEFAULTS);
  const [target, setTarget] = useState("");
  const [creating, setCreating] = useState(false);

  const close = () => setUi({ wizardOpen: false });

  /** HonestButton's required channel: a blocked press SAYS the reason instead
   *  of doing nothing. `why` carries the paragraph the short reason cannot. */
  const explain = (reason: string, why?: string) =>
    enqueueToast({ level: "warning", title: reason, detail: why });

  const blankReason = !canCreate
    ? `Creating a flow needs ${accessPhrase("control.capture")}.`
    : creating ? "Already creating a flow — one moment." : null;

  const startBlank = async () => {
    setCreating(true);
    try {
      const rec = (await flowsApi.create({
        name: BLANK_NAME,
        // No `folder`: the server's FlowRecord already defaults to "My flows",
        // and restating a default here is a second place for it to change.
        tagline: BLANK_TAGLINE,
        graph: { nodes: blankNodes(), edges: [] },
      })) as { id?: string };
      if (!rec?.id) throw new Error("the server returned a flow with no id");
      close();
      await flowsOpen(rec.id);
    } catch (e) {
      enqueueToast({
        level: "error",
        title: "Could not start a blank flow",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setCreating(false);
    }
  };

  const kindIndex = KINDS.indexOf(kind);

  return (
    <Overlay
      open={open}
      variant="center"
      label="New flow — guided"
      onClose={close}
      // `center`'s sm geometry already supplies --ov-max-w: 560px; only the
      // height is the design's own number (§C.14).
      surfaceStyle={{ "--ov-max-h": "90dvh" } as CSSProperties}
      head={(
        <header
          data-flows-wizard
          className="flex items-center justify-between gap-3 px-[18px] py-[13px]"
        >
          <span className="font-display font-semibold text-[11px] tracking-[0.22em]">
            NEW FLOW — GUIDED
          </span>
          <button
            type="button"
            aria-label="Close the guided wizard"
            onClick={close}
            className="flex-none min-w-[40px] min-h-[34px] rounded-lg border border-line2
              bg-transparent text-dim hover:text-accent hover:border-accent
              transition-colors cursor-pointer"
          >
            <span aria-hidden>✕</span>
          </button>
        </header>
      )}
      foot={(
        // `.overlay-foot` already draws the top rule and pays the safe-area
        // inset; repeating `border-t` here would render a 2px double line.
        <footer className="flex gap-2.5 px-[18px] py-[13px]">
          <HonestButton
            reason={GENERATE_BLOCKED}
            // Unreachable while `reason` is set, which is always: the day a
            // route exists, this is where it gets called and the reason goes.
            onClick={() => { /* no generator to call — §G-2 item 4. */ }}
            onExplain={(r) => explain(r, GENERATE_BLOCKED_WHY)}
            className="flex-1 min-h-[46px] rounded-[10px] border border-accent2
              bg-accent-fill text-accent font-display font-semibold text-[12px]
              tracking-[0.14em] cursor-pointer"
          >
            GENERATE FLOW
          </HonestButton>
          <HonestButton
            reason={blankReason}
            onClick={() => { void startBlank(); }}
            onExplain={(r) => explain(r)}
            className="flex-none min-h-[46px] px-3.5 rounded-[10px] border border-transparent
              bg-transparent text-dim hover:border-line2 font-display font-semibold
              text-[11px] tracking-[0.12em] transition-colors cursor-pointer"
          >
            START BLANK
          </HonestButton>
        </footer>
      )}
      bodyClassName="px-[18px] py-4 flex flex-col gap-4"
    >
      {/* ── 1. what kind of night */}
      <div className="flex flex-col gap-1.5">
        <span
          id="flows-wiz-kind"
          className="font-display font-semibold text-[9.5px] tracking-[0.18em] text-dim"
        >
          WHAT ARE WE DOING TONIGHT?
        </span>
        <div role="radiogroup" aria-labelledby="flows-wiz-kind" className="flex flex-wrap gap-2">
          {KINDS.map((k, i) => {
            const on = k === kind;
            return (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={on}
                // Roving tabindex + the shared arrow-key model, so the group is
                // one tab stop inside Overlay's focus trap — and so this sheet
                // behaves like every other radiogroup in the app (UX-20).
                tabIndex={rovingTabIndex(i, kindIndex)}
                onClick={() => setKind(k)}
                onKeyDown={(e) =>
                  handleRadioKeyDown(e, i, KINDS.length, (idx) => setKind(KINDS[idx]))}
                className={`flex-1 min-w-[140px] min-h-[44px] px-2.5 py-2 rounded-[10px]
                  border font-mono text-[11px] transition-colors cursor-pointer ${
                    on
                      ? "border-accent text-accent bg-accent-fill"
                      : "border-line2 text-dim bg-transparent hover:border-accent"
                  }`}
              >
                {k}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── 2. automation pills */}
      <div className="flex flex-col gap-1.5">
        <span
          id="flows-wiz-auto"
          className="font-display font-semibold text-[9.5px] tracking-[0.18em] text-dim"
        >
          ADD AUTOMATION
        </span>
        <div aria-labelledby="flows-wiz-auto" role="group" className="flex flex-wrap gap-2">
          {AUTOMATIONS.map((a) => {
            const on = autos.includes(a);
            return (
              <button
                key={a}
                type="button"
                aria-pressed={on}
                onClick={() => setAutos((prev) =>
                  prev.includes(a) ? prev.filter((p) => p !== a) : [...prev, a])}
                className={`min-h-[38px] px-3 py-[7px] rounded-full border font-mono
                  text-[10.5px] transition-colors cursor-pointer ${
                    on
                      ? "border-accent text-accent bg-accent-fill"
                      : "border-line2 text-dim bg-transparent hover:border-accent"
                  }`}
              >
                {a}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── 3. the target, or the pool candidates */}
      <label className="flex flex-col gap-[5px]">
        <span className="font-display font-semibold text-[9.5px] tracking-[0.18em] text-dim">
          TARGET (OR CANDIDATES, COMMA-SEPARATED)
        </span>
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder={TARGET_PLACEHOLDER}
          // `.field` is unlayered authored CSS, so its 13px font and 6px/9px
          // padding beat any plain Tailwind utility here — the design's values
          // have to arrive as `!` or they never apply (§C.2's note, same trap).
          className="field !text-[13px] !p-2.5 min-h-[44px]"
        />
      </label>

      <p className="text-[11px] text-faint leading-[1.5] [text-wrap:pretty]">
        Generates a complete, valid graph — then everything is just stages and wires
        you can rearrange. The doctor will flag anything risky.
      </p>

      {/* The blurb above is the design's copy and it describes a generator that
          is not reachable. The refusal goes directly under it so the promise and
          its qualification are read together, and so the reason reaches someone
          who never presses the button. */}
      <LockedNote
        reason={`${GENERATE_BLOCKED} ${GENERATE_BLOCKED_WHY}`}
        // The note wraps to several lines; `!` because LockedNote's own
        // `items-center` would otherwise float the lock glyph mid-paragraph.
        className="!items-start leading-[1.5] [text-wrap:pretty]"
      />
    </Overlay>
  );
}
