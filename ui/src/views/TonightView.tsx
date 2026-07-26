// TonightView — the dedicated "Tonight" destination (polish grab-bag (c)).
//
// A first-timer should not have to already know that "what can I image right
// now?" is a panel inside Atlas — this view gives the question a NAMED
// destination. (It is a discoverability fix, not a tap-count one: on a phone it
// still lives in the More sheet. See the NAV comment in App.tsx for why the
// append-only nav rule wins over a primary-bar promotion.)
//
// A thin shell around the SAME `TonightPicker` AtlasView mounts (server-ranked,
// difficulty-tagged, beginner filter already ON by default) — no new backend, no
// new picker, no duplicated logic. Picking hands straight to `openFraming`,
// exactly like Atlas does, so the flow is: Tonight -> tap a target -> it's framed.
//
// (c2) While a sequence is RUNNING or PAUSED it also re-surfaces what you are
// already imaging, with its difficulty tier when the client can honestly resolve
// it (lib/difficulty.tierForTargetName over the framing session's origin entry).
// Unknown tier => the chip is simply absent; we never guess a badge.
import type { JSX } from "react";
import { useStore } from "../store";
import { Panel, Tooltip } from "../components/ui";
import { Icon } from "../components/icons";
import { TonightPicker } from "../components/atlas/TonightPicker";
import {
  difficultyGlyph, difficultyHint, difficultyLabel, difficultyTone, tierForTargetName,
} from "../lib/difficulty";

export default function TonightView(): JSX.Element {
  const openFraming = useStore((s) => s.openFraming);
  const seqState = useStore((s) => s.sequence.state);
  const seqTarget = useStore((s) => s.sequence.target);
  const framingTarget = useStore((s) => s.framing?.target);

  const running = seqState === "running" || seqState === "paused";
  const tier = tierForTargetName(seqTarget, framingTarget ? [framingTarget] : []);

  return (
    <div className="flex flex-col gap-4">
      {/* active-session line: what the rig is on right now, so opening Tonight
          mid-run never reads as "nothing is happening". */}
      {running && seqTarget && (
        <div
          className="flex items-center gap-2 px-3 py-2 border border-line2 bg-raise/40 text-xs"
          aria-live="polite"
        >
          <Icon name="capture" size={14} className="text-accent shrink-0" />
          <span className="min-w-0 truncate">
            <span className="text-dim">
              {seqState === "paused" ? "Paused on" : "Imaging now"} ·{" "}
            </span>
            <span className="text-ink">{seqTarget}</span>
          </span>
          {/* The tier hint used to live only in `title=`, which never fires on
              touch — this app's primary field device is a tablet. Tooltip has a
              tap path, a keyboard path and an Escape. */}
          {tier && (
            <Tooltip content={difficultyHint(tier)}>
              <span
                className={`inline-flex items-center gap-1 shrink-0 ${
                  difficultyTone(tier) === "good" ? "text-good"
                  : difficultyTone(tier) === "warn" ? "text-warn" : "text-bad"
                }`}
              >
                <span aria-hidden>{difficultyGlyph(tier)}</span>
                {difficultyLabel(tier)}
              </span>
            </Tooltip>
          )}
        </div>
      )}

      <Panel title="Tonight">
        <p className="text-xs text-dim mb-3">
          Point-and-shoot targets that are up right now, ranked by how high they
          climb tonight. Tap one to frame it.
        </p>
        <TonightPicker onPick={openFraming} />
      </Panel>
    </div>
  );
}
