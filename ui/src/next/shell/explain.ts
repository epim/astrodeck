// explain.ts - the channel a locked control uses to say why it is locked
// (ARCHITECTURE.md section 6).
//
// The primitives take `onExplain` as a PROP because they own no store. This is
// the shell's implementation of that prop: one warning toast carrying the
// reason. Every hub passes `explainLock` (or `useLock`'s own `onExplain`, which
// does the same thing) so a locked press is never silent.

import { useStore } from "../../store";

export function explainLock(reason: string): void {
  if (!reason) return;
  useStore.getState().enqueueToast({ level: "warning", title: reason });
}
