// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// UnreadableSessionCard.tsx - a session file the rig cannot read, on the shelf
// (#242).
//
// The server lists such a file (not valid JSON, fails validation, or states no
// status, #218) with the store's reason and the name inside it, so that it can
// be seen and removed. Before that it was invisible here and DELETE could not
// reach it, so the only way to clear one was a shell on the rig. The id is
// shown beside the name when they differ: it is what DELETE removes, and it is
// how a broken copy is told from a readable session of the same name.
//
// IT IS NOT A SESSION CARD, AND IT DOES NOT LOOK LIKE ONE. No thumbnail, no
// sub count, no dates, no MORE menu: every one of those reads the file that is
// broken, and "0 SUBS" would tell the operator the session is empty when
// nobody can count it. RESUME, UPDATE FROM PLAN and AUTO-RESUME are absent
// rather than locked, because there is no session for them to act on.
//
// DELETE IS THE SESSION CARD'S DELETE: the same capability sentence
// (`controlReason`), the same verbatim confirm and the same route
// (`runDelete`). A viewer sees it honest-disabled with that sentence, as they
// see a session's. The store's delete (`SessionStore.delete`) removes the
// file, its backup and its thumbnails directory and never a FITS frame, which
// is what the confirm body says.
//
// RESTORE IS THE WAY BACK (#280), AND ONLY WHERE THERE IS ONE. A backup
// (`<id>.json.bak`) beside the file, or all that is left of it (an "orphan"
// row: the session file is gone), puts RESTORE on the card, gated as DELETE
// is. It is rendered only when `card.backup`: with no backup the server
// would answer 404, and a control that can only fail is not offered. Whether
// it would load is the server's judgment (it reads the backup the way every
// reader does), so the card does not claim it.

import type { JSX } from "react";

import { explainLock } from "../../../shell/explain";
import { ActionButton, Mono, Pill } from "../../../ui";
import { controlReason, runDelete, runRestore } from "./cardActions";
import type { UnreadableCardData } from "./sessionsIndex";

export function UnreadableSessionCard({ card, canControl, onChanged }: {
  card: UnreadableCardData;
  canControl: boolean;
  onChanged: () => void;
}): JSX.Element {
  return (
    <div
      className="nx-card"
      data-testid={`session-unreadable-${card.id}`}
      style={{ padding: "8px 10px 10px", display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}
    >
      <span
        className="nx-display"
        style={{
          fontSize: 11.5, letterSpacing: ".1em", minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        {card.name}
      </span>
      {card.name !== card.id && (
        <Mono size={10} tone="dim">{card.id}</Mono>
      )}
      <div style={{ display: "flex" }}>
        <Pill tone="bad">UNREADABLE</Pill>
      </div>
      <Mono size={10} tone="dim">{card.reason}</Mono>
      {/* RESTORE only where a backup is there to restore (#280), and gated
          exactly as DELETE is: same sentence for a viewer, same honest lock. */}
      {card.backup && (
        <ActionButton
          kind="secondary"
          lockedReason={controlReason(canControl)}
          onExplain={explainLock}
          onPress={() => { void runRestore(card.id, card.name, onChanged); }}
          data-testid={`session-unreadable-restore-${card.id}`}
        >
          RESTORE
        </ActionButton>
      )}
      <ActionButton
        kind="danger"
        lockedReason={controlReason(canControl)}
        onExplain={explainLock}
        onPress={() => { void runDelete(card.id, card.name, onChanged); }}
        data-testid={`session-unreadable-delete-${card.id}`}
      >
        DELETE
      </ActionButton>
    </div>
  );
}
