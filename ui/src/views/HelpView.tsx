// views/HelpView.tsx — the in-app troubleshooting + glossary page (NOV-9 T4).
// A thin render of the pure T2 content (lib/troubleshoot.ts TROUBLESHOOTING +
// help.ts HELP) — no logic worth a runtime test, verified by tsc -b.
//
// Reachable from mobile overflow (NavMoreSheet), the log drawer footer, and
// deep-linked directly from a diagnosed error's "How to fix →" action
// (store.openHelp sets view:"help" + helpTopic). On arrival with a topic set,
// the matching entry scrolls into view + highlights, then one-shot-clears
// helpTopic after a beat so a later manual visit isn't stuck highlighting.
import { useEffect, useRef, type JSX } from "react";
import { useStore } from "../store";
import { TROUBLESHOOTING } from "../lib/troubleshoot";
import { HELP, type HelpKey } from "../help";
import { Panel } from "../components/ui";
import { Icon } from "../components/icons";

export default function HelpView(): JSX.Element {
  const helpTopic = useStore((s) => s.helpTopic);
  const clearHelpTopic = useStore((s) => s.clearHelpTopic);
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
