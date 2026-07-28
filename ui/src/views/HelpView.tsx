// views/HelpView.tsx — the in-app troubleshooting + glossary page (NOV-9 T4).
// A thin render of the pure T2 content (lib/troubleshoot.ts TROUBLESHOOTING +
// help.ts HELP) — no logic worth a runtime test, verified by tsc -b.
//
// Reachable from the nav rail, mobile overflow (NavMoreSheet), the log drawer
// footer, and deep-linked directly from a diagnosed error's "How to fix →"
// action (store.openHelp sets view:"help" + helpTopic). On arrival with a topic
// set, the matching entry scrolls into view + highlights, then one-shot-clears
// helpTopic after a beat so a later manual visit isn't stuck highlighting.
//
// SETUP-GUIDE RE-ENTRY (QA blocker, MEASURED on a cold instance before this
// panel existed). `openWizard()` had exactly ONE caller outside the auto-open
// effect — NotConnectedInterstitial — and that interstitial only renders on an
// equipment-gated view while NOTHING is connected. Auto-open needs
// `!seenWizard && siteIsDefault && !equipConnected`. So the moment a tester
// connected a rig (Equipment is the landing view, i.e. the common path) and
// then dismissed the bar or reloaded, every re-entry was gone at once: the
// interstitial no longer rendered, and auto-open was doubly blocked by
// `seenWizard` and by `equipConnected`. Reproduced on a fresh
// ASTRODECK_CONFIG_DIR at 412x915 with real touch: connect sim -> tap the bar's
// X -> reload -> every button/link in the whole document matching
// /setup|guide|wizard|walk|first.run/ came back with the guiding nav row and
// nothing else. Steps 3-6 (pick a target, cool the camera, take your first
// frame) — the ones a beginner needs most — were unreachable without
// DISCONNECTING the rig.
//
// This panel is the durable re-entry, and Help is where it belongs: it is a
// destination, not a state, so it is reachable connected or not, first run or
// tenth. It borrows the shape AuthMethodPanel already established for the same
// problem ("Setup guide" un-dismiss affordance next to the guided card).
//
// It calls `openWizard()` and nothing else on purpose. That action sets
// `wizardStepId: null`, and `computeWizard` resolves a null override to the
// FIRST INCOMPLETE step — so reopening resumes rather than restarting, off the
// live snapshot, with no second copy of "what is done" to drift out of sync.
// The copy promises exactly that and no more; the bar itself renders the
// progress count and the step title the instant it opens.
//
// It also cannot nag: nothing here opens on its own, and `closeWizard()` still
// marks WIZARD_SEEN_KEY, so a configured user only ever sees the guide when
// they come to this page and press the button.
import { useEffect, useRef, type JSX } from "react";
import { useStore } from "../store";
import { TROUBLESHOOTING } from "../lib/troubleshoot";
import { HELP, type HelpKey } from "../help";
import { Panel } from "../components/ui";
import { Icon } from "../components/icons";

export default function HelpView(): JSX.Element {
  const helpTopic = useStore((s) => s.helpTopic);
  const clearHelpTopic = useStore((s) => s.clearHelpTopic);
  const openWizard = useStore((s) => s.openWizard);
  const focusRef = useRef<HTMLElement>(null);

  // Deep-link: scroll the arriving topic into view + highlight, then one-shot
  // clear so a later manual visit isn't stuck highlighting an old error.
  useEffect(() => {
    if (!helpTopic) return;
    focusRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    const t = window.setTimeout(() => clearHelpTopic(), 2500);
    return () => window.clearTimeout(t);
  }, [helpTopic, clearHelpTopic]);

  return (
    <div className="flex flex-col gap-4 w-full max-w-[820px]">
      <h1 className="font-display text-lg tracking-[0.2em] text-ink uppercase">Help &amp; Troubleshooting</h1>

      {/* The setup guide's permanent door. FIRST on the page: the person who
          comes here having lost the first-run bar is looking for a way back
          into it, not for a glossary. */}
      <Panel title="Setup guide">
        <p className="text-xs text-dim leading-relaxed max-w-xl">
          The step-by-step guide for a new rig — set your location, connect,
          save a profile, pick a target, cool the camera, take your first
          frame. It docks as a thin bar above the nav and points at the panel
          that finishes each step.
        </p>
        <p className="flex items-start gap-1.5 text-[11px] text-dim leading-snug max-w-xl mt-2">
          <Icon name="check" size={12} className="shrink-0 mt-0.5" aria-hidden />
          <span>
            It picks up at the first step you haven&apos;t finished — anything
            already done stays done, and you can reopen it as often as you like.
          </span>
        </p>
        <button
          type="button"
          className="btn btn-accent min-h-11 inline-flex items-center gap-2 mt-3"
          onClick={openWizard}
        >
          <Icon name="info" size={14} />
          Open the setup guide
        </button>
      </Panel>

      <Panel title="Common problems">
        <div className="flex flex-col divide-y divide-line">
          {TROUBLESHOOTING.map((e) => {
            const active = e.topic === helpTopic;
            return (
              <section
                key={e.topic}
                ref={active ? focusRef : undefined}
                aria-current={active ? "true" : undefined}
                className={`py-3 scroll-mt-20 ${active ? "-mx-2 px-2 rounded bg-accent/10" : ""}`}
              >
                <h2 className="text-sm text-ink font-medium inline-flex items-center gap-2">
                  <Icon name="alert" size={14} className="text-warn shrink-0" />
                  {e.symptom}
                </h2>
                <p className="text-xs text-dim mt-1">{e.cause}</p>
                <ol className="text-xs text-ink/90 mt-2 flex flex-col gap-1 list-decimal pl-5">
                  {e.steps.map((s, i) => <li key={i}>{s}</li>)}
                </ol>
              </section>
            );
          })}
        </div>
      </Panel>

      <Panel title="Glossary">
        <dl className="grid gap-3 sm:grid-cols-2">
          {(Object.keys(HELP) as HelpKey[]).map((k) => (
            <div key={k} className="min-w-0">
              <dt className="text-xs font-medium text-ink capitalize">{k.replace(/([A-Z])/g, " $1")}</dt>
              <dd className="text-[11px] text-dim mt-0.5 leading-snug">{HELP[k]}</dd>
            </div>
          ))}
        </dl>
      </Panel>
    </div>
  );
}
