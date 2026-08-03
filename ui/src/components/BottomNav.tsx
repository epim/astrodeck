// BottomNav.tsx — 5-item + More mobile bottom nav (touch spec §5, R14/R15/R21/R27).
//
// Replaces the old 9-item, 8px-label, unreadable bottom strip. Structure:
//   PRIMARY (one tap)  = Rig, Align, Mount, Focus, Capture   (setup-critical — R14)
//   OVERFLOW (in More) = Guide, Plan, Power, Monitor, Settings (occasional/monitoring)
//   + a 6th "More" button with a FIXED grid icon that never morphs (R15); when ANY
//     non-primary view is active it shows an accent dot + the view name as a sub-label.
//
// Each tab: 56px min-height, 24px icon, 11px label (was 8px tracking-widest — the
// cause of unreadability). Active state encodes by SHAPE, not color alone (R21):
// `bg-accent/15` chip + a 2px top border + a heavier icon stroke (filled-feel).
//
// The More sheet open state is LOCAL useState (transient UI, NOT global — touch
// spec §2.2) so it never widens subscriptions. Narrow store selectors only (R27).

import { useState } from "react";
import type { ViewName } from "../types";
import { useStore } from "../store";
import { Icon, type IconName } from "./icons";
import NavMoreSheet, { OVERFLOW_VIEWS } from "./NavMoreSheet";
import { haptics } from "../lib/haptics";

const PRIMARY: { id: ViewName; label: string; icon: IconName }[] = [
  // Stays "Equipment" even though this file's header calls the set "Rig, Align,
  // Mount, Focus, Capture" and "Rig" would fit without an ellipsis. The
  // first-run wizard sends a novice here BY NAME, and its copy contract
  // (lib/__tests__/firstRunWizard.test.ts — "the view name a novice reads on
  // the nav") asserts the step body says "Equipment". Renaming the tab alone
  // would leave the wizard pointing at a word that is no longer on screen, and
  // no test would catch it because that one reads the wizard, not the nav.
  // Measured at 320px this truncates to "EQUIPME…"; the icon disambiguates, and
  // an ellipsis is the cheaper cost than a broken instruction.
  { id: "connect", label: "Equipment", icon: "rig" },
  { id: "polar", label: "Align", icon: "align" },
  { id: "mount", label: "Mount", icon: "mount" },
  { id: "focus", label: "Focus", icon: "focus" },
  { id: "capture", label: "Capture", icon: "capture" },
];

const PRIMARY_IDS = new Set<ViewName>(PRIMARY.map((v) => v.id));

function Tab({
  active,
  label,
  icon,
  subLabel,
  showDot,
  badge,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: IconName;
  subLabel?: string;
  showDot?: boolean;
  badge?: React.ReactNode;
  onClick: () => void;
}) {
  // The label's `w-full min-w-0 truncate` is load-bearing, not tidiness. A flex
  // item's default `min-width:auto` refuses to shrink below its content, so at
  // 320px the six tabs each got ~53px while "EQUIPMENT" measured ~72px at 11px
  // uppercase. The label is centred, so the excess spilled EQUALLY to both
  // sides — and on the leftmost tab that half went past x=0, off the viewport.
  // Measured on the running app at 320px: the "E" was cut off. The document
  // itself never overflowed (scrollWidth == clientWidth == 320), which is why
  // no page-level overflow guard caught it, and why it reads as a rendering
  // glitch rather than the layout bug it is.
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`relative flex-1 min-w-[44px] flex flex-col items-center justify-center gap-0.5 min-h-[56px]
        ${active ? "text-accent bg-accent/15" : "text-dim"}`}
    >
      {active && <span className="absolute top-0 inset-x-2 h-[2px] bg-accent" aria-hidden />}
      {showDot && (
        <span className="absolute top-1.5 right-1/2 translate-x-3 w-1.5 h-1.5 rounded-full bg-accent" aria-hidden />
      )}
      {/* filled-vs-outline feel via stroke weight (R21 — shape, not color alone) */}
      <Icon name={icon} size={24} strokeWidth={active ? 2.25 : 1.5} />
      <span className="w-full min-w-0 px-0.5 text-center truncate text-[11px] tracking-normal font-display uppercase leading-none">
        {label}
      </span>
      {subLabel && (
        <span className="text-[8px] text-accent/80 leading-none truncate max-w-[56px]">{subLabel}</span>
      )}
      {badge}
    </button>
  );
}

export default function BottomNav() {
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const seqState = useStore((s) => s.sequence.state);
  const camConnected = useStore((s) => !!s.status?.connected?.camera?.connected);
  const mountConnected = useStore((s) => !!s.status?.connected?.telescope?.connected);
  const unseenError = useStore((s) => s.unseenError);
  const [moreOpen, setMoreOpen] = useState(false);

  // Any view that is NOT a primary tab lives in the More sheet → light More with a
  // "you are here" dot + sub-label (F-B3). This now covers Monitor/Settings (newly
  // reachable on phone) and any future non-primary view, not just the old 3.
  const overflowActive = !PRIMARY_IDS.has(view);
  const activeOverflow = OVERFLOW_VIEWS.find((v) => v.id === view);

  const pick = (v: ViewName) => {
    setView(v);
    setMoreOpen(false);
  };

  const ledFor = (id: ViewName): React.ReactNode => {
    if (id === "capture" && camConnected)
      return <span className="absolute top-1 right-1/2 translate-x-3 led led-on" aria-hidden />;
    if (id === "mount" && mountConnected)
      return <span className="absolute top-1 right-1/2 translate-x-3 led led-on" aria-hidden />;
    return null;
  };

  return (
    <>
      <nav
        className="sm:hidden flex border-t border-line bg-raise shrink-0 fixed bottom-0 inset-x-0 z-20"
        aria-label="Primary"
      >
        {PRIMARY.map((n) => (
          <Tab
            key={n.id}
            active={view === n.id}
            label={n.label}
            icon={n.icon}
            badge={ledFor(n.id)}
            onClick={() => pick(n.id)}
          />
        ))}
        {/* More — fixed grid icon ALWAYS (R15); dot + sub-label when overflow active */}
        <Tab
          active={overflowActive || moreOpen}
          label="More"
          icon="plan"
          subLabel={overflowActive ? activeOverflow?.label : undefined}
          showDot={overflowActive}
          badge={
            unseenError > 0 ? (
              <span
                className="absolute top-1 right-1/2 translate-x-4 inline-flex items-center justify-center
                  min-w-[16px] h-4 px-1 rounded-full border border-bad text-bad text-[9px] font-bold leading-none tabular-nums"
                aria-hidden
              >
                {unseenError > 99 ? "99+" : unseenError}
              </span>
            ) : seqState === "error" ? (
              <span className="absolute top-1 right-1/2 translate-x-3 led led-bad blink-alert" aria-hidden />
            ) : null
          }
          onClick={() => {
            haptics.tap();
            setMoreOpen((v) => !v);
          }}
        />
      </nav>

      <NavMoreSheet open={moreOpen} onClose={() => setMoreOpen(false)} />
    </>
  );
}
