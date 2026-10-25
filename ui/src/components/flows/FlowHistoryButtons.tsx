// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowHistoryButtons.tsx - the classic editor's UNDO and REDO pair for the
// places that have no header to carry it: the canvas float at tablet and on the
// phone CANVAS tab, and the phone FLOW tab (#688 part 3, #771).
//
// Both buttons read the one history in the store (`flowsUndo`, `flowsRedo`),
// the same stack the desktop header and the Ctrl+Z key drive, so an edit made on
// any surface can be taken back from any other. Honest-disabled, as the
// header's pair is: dimmed, still pressable, and a press says why there is
// nothing to step to. 44 px tall, the touch floor. Its own subscriptions, a
// string or null each, so a node drag (a history write per pointer move)
// re-renders it only when a stack empties or fills.

import type { JSX } from "react";

import { useStore } from "../../store";
import { HonestButton } from "../ui";
import { redoReason, undoReason } from "./flowsTypes";

const BUTTON_CLASS = "h-11 px-4 rounded-[10px] border border-line2 bg-raise text-ink cursor-pointer "
  + "font-display font-semibold text-[11px] tracking-[0.12em]";

export default function FlowHistoryButtons(): JSX.Element {
  const undoWhy = useStore((s) => undoReason(s.flows));
  const redoWhy = useStore((s) => redoReason(s.flows));
  const undo = useStore((s) => s.flowsUndo);
  const redo = useStore((s) => s.flowsRedo);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const explain = (reason: string): void => enqueueToast({ level: "warning", title: reason });
  return (
    <>
      <HonestButton className={BUTTON_CLASS} reason={undoWhy} onExplain={explain} onClick={undo}>
        <span data-testid="flow-undo">UNDO</span>
      </HonestButton>
      <HonestButton className={BUTTON_CLASS} reason={redoWhy} onExplain={explain} onClick={redo}>
        <span data-testid="flow-redo">REDO</span>
      </HonestButton>
    </>
  );
}
