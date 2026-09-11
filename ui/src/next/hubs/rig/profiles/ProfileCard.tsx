// ProfileCard.tsx - one saved profile, and the five things that can be done to
// it (wave R7, T-R7-17; plan section 3.F20).
//
// Presentation plus the rename draft, nothing else: every action is a callback
// the editor owns, because four of the five need the list refreshed afterwards
// and a card that re-fetched for itself would show a different library from the
// one beside it.
//
// THREE THINGS THE LEGACY CARD DID THAT THIS KEEPS, DELIBERATELY.
//
//   DELETE IS THE QUIETEST CONTROL ON THE ROW, AND THE FURTHEST RIGHT. It was
//   measured at x=96 on a 390 px phone - directly under a right thumb, 6 px
//   from EXPORT - before the legacy panel pushed it to the end of the row
//   behind a divider and dropped its danger tone. Danger colour belongs in the
//   confirm dialog, where it means something; on the row it only pulls a thumb
//   toward the one action that cannot be undone. The tone here is `ghost`, the
//   glyph is a bin and the word is DELETE (glyph AND word, never colour alone).
//
//   A SINGLE TAP OPENS THE CONFIRM - it does not delete. The friction lives
//   where it can also explain itself.
//
//   RENAME IS AN IN-CARD FIELD, not `window.prompt`, which breaks night mode
//   and the dimmer, and which no phone user can style out of the way.
//
// AND ONE THING IT CHANGES: no native `disabled`. The legacy card carried five
// (`ProfileList.tsx` :678 :695 :716 :765 plus the capture button at :566), and
// a `disabled` element leaves the accessibility tree taking its reason with it.
// Every one is `lockedReason` + `onExplain` here, so a press during a connect
// says which profile is holding the controller instead of doing nothing.

import { useEffect, useRef, useState, type JSX } from "react";
import { profileOverrideSummary } from "../../../../lib/effective";
import type { ProfileRow } from "../../../../types";
import { NxIcon } from "../../../icons";
import { ActionButton, Card, Mono, Pill, TextInput } from "../../../ui";
import { PencilGlyph, TrashGlyph } from "./glyphs";
import {
  ACTIVATE_LABEL, ACTIVE_BADGE, CONNECTING_LABEL, DELETE_LABEL, EXPORT_LABEL,
  NAME_LABEL, OTHER_CONNECTING, OVERRIDES_WORD, RECONNECT_LABEL, RENAME_CANCEL_LABEL,
  RENAME_LABEL, RENAME_SAVE_LABEL, ROW_BUSY, UPDATE_LABEL, overridesTail,
  profileSubline,
} from "./profilesModel";

export interface ProfileCardProps {
  row: ProfileRow;
  /** An action on THIS row is in flight. */
  busy: boolean;
  /** THIS row's rig connect is in flight - server truth: its active pointer has
   *  not moved yet. */
  connecting: boolean;
  /** SOME OTHER profile is connecting. */
  otherConnecting: boolean;
  renaming: boolean;
  onActivate: () => void;
  onStartRename: () => void;
  onCancelRename: () => void;
  onCommitRename: (next: string) => void;
  onUpdateFromRig: () => void;
  onExport: () => void;
  onDelete: () => void;
  /** The one sentence for a principal who cannot write backend config, or a
   *  dead link. Null when the only thing in the way is this row's own state. */
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

export function ProfileCard({
  row, busy, connecting, otherConnecting, renaming,
  onActivate, onStartRename, onCancelRename, onCommitRename,
  onUpdateFromRig, onExport, onDelete, lockedReason, onExplain,
}: ProfileCardProps): JSX.Element {
  // The permission always wins: it is the reason that will still be true after
  // the in-flight request finishes. Row state is the second answer, not the
  // first, so a viewer never reads "an action is still running" for something
  // they could not have started.
  const rowLock = lockedReason ?? (busy ? ROW_BUSY : null);
  const activateLock = rowLock ?? (otherConnecting ? OTHER_CONNECTING : null);
  const overrides = profileOverrideSummary(row);

  return (
    <Card
      tone={row.active ? "accent" : "default"}
      className="nx-prof-card"
      data-testid="profile-card"
    >
      <div className="nx-prof-cardhead" data-profile-id={row.id}>
        <span className="nx-prof-led" data-on={row.active ? "true" : "false"} aria-hidden="true" />
        <div className="nx-prof-ident">
          {renaming ? (
            <RenameField
              initial={row.name}
              onCommit={onCommitRename}
              onCancel={onCancelRename}
              lockedReason={lockedReason}
              onExplain={onExplain}
            />
          ) : (
            <>
              <div className="nx-prof-nameline">
                <span className="nx-prof-name" data-testid="profile-name">{row.name}</span>
                {row.active && <Pill tone="accent" data-testid="profile-active">{ACTIVE_BADGE}</Pill>}
              </div>
              <Mono size={10.5} tone="dim" className="nx-prof-sub">{profileSubline(row)}</Mono>
            </>
          )}
        </div>
      </div>

      {/* #129 - a profile can carry values that BEAT global config the moment it
          is activated, and until the row started returning them no screen in
          the product displayed either block. That is how a `polar_align: "sim"`
          pin written during one session kept the aligner simulated for twelve
          days. Spelled out rather than badged: "overrides" with no values is
          exactly the label that would have been ignored. */}
      {overrides && (
        <p className="nx-prof-overrides" data-active={row.active ? "true" : "false"}
          data-testid="profile-overrides">
          <Pill tone={row.active ? "warn" : "dim"}>{OVERRIDES_WORD}</Pill>
          <span className="nx-prof-overtext">{overrides}{overridesTail(row.active)}</span>
        </p>
      )}

      <div className="nx-prof-actions">
        <ActionButton
          kind={row.active ? "secondary" : "primary"}
          glyph={<NxIcon name="play" size={14} />}
          busy={connecting}
          lockedReason={activateLock}
          onExplain={onExplain}
          onPress={onActivate}
          ariaLabel={`${row.active ? RECONNECT_LABEL : ACTIVATE_LABEL} ${row.name}`}
          data-testid="profile-activate"
        >
          {connecting ? CONNECTING_LABEL : row.active ? RECONNECT_LABEL : ACTIVATE_LABEL}
        </ActionButton>

        <ActionButton
          kind="ghost"
          glyph={<PencilGlyph size={14} />}
          lockedReason={rowLock}
          onExplain={onExplain}
          onPress={onStartRename}
          ariaLabel={`${RENAME_LABEL} ${row.name}`}
          data-testid="profile-rename"
        >
          {RENAME_LABEL}
        </ActionButton>

        <ActionButton
          kind="ghost"
          glyph={<NxIcon name="refresh" size={14} />}
          lockedReason={rowLock}
          onExplain={onExplain}
          onPress={onUpdateFromRig}
          ariaLabel={`Update ${row.name} from the current rig`}
          data-testid="profile-update"
        >
          {UPDATE_LABEL}
        </ActionButton>

        {/* EXPORT carries the SAME gate as the writes, and that is deliberate
            rather than tidy. `GET /api/profiles/{id}` is wire-redacted below
            `config.backend` (no host, no port, no NINA or PHD2 fields, no
            site), so
            an operator's export would be a profile FILE missing the fields that
            make it connect - a broken rig, saved to disk, indistinguishable
            from a whole one until someone imports it on another box. The legacy
            panel gated this on `busy` alone. */}
        <ActionButton
          kind="ghost"
          glyph={<NxIcon name="download" size={14} />}
          lockedReason={rowLock}
          onExplain={onExplain}
          onPress={onExport}
          ariaLabel={`${EXPORT_LABEL} ${row.name}`}
          data-testid="profile-export"
        >
          {EXPORT_LABEL}
        </ActionButton>

        <span className="nx-prof-danger">
          <ActionButton
            kind="ghost"
            className="nx-prof-delete"
            glyph={<TrashGlyph size={14} />}
            lockedReason={rowLock}
            onExplain={onExplain}
            onPress={onDelete}
            ariaLabel={`${DELETE_LABEL} ${row.name}`}
            data-testid="profile-delete"
          >
            {DELETE_LABEL}
          </ActionButton>
        </span>
      </div>
    </Card>
  );
}

/** The in-card rename affordance. Commits on Enter, on SAVE NAME, and on blur -
 *  the last one because on touch the ordinary way to leave a field is to tap
 *  somewhere else, and a field that silently dropped the edit there would look
 *  like it had saved.
 *
 *  CANCEL therefore has a race to win: a tap on it blurs the input first, and
 *  the blur would commit before the click ever arrived. `pointerdown` fires
 *  before `blur`, so the capture-phase listener on the wrapper marks the
 *  intention and the blur honours it. Escape does the same through the same
 *  flag, and `settled` makes sure only one of the three paths ever fires. */
function RenameField({ initial, onCommit, onCancel, lockedReason, onExplain }: {
  initial: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}): JSX.Element {
  const [value, setValue] = useState(initial);
  const settled = useRef(false);
  const cancelling = useRef(false);
  const inputRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const input = inputRef.current?.querySelector("input");
    input?.focus();
    input?.select();
  }, []);

  const commit = (next: string) => {
    if (settled.current) return;
    if (cancelling.current) { cancel(); return; }
    settled.current = true;
    onCommit(next);
  };
  const cancel = () => {
    if (settled.current) return;
    settled.current = true;
    onCancel();
  };

  return (
    <div
      ref={inputRef}
      className="nx-prof-rename"
      onPointerDownCapture={(e) => {
        const el = e.target as Element | null;
        if (el && el.closest("[data-rename-cancel]")) cancelling.current = true;
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); cancel(); }
      }}
    >
      <TextInput
        value={value}
        onChange={setValue}
        onEnter={commit}
        onBlur={commit}
        ariaLabel={NAME_LABEL}
        lockedReason={lockedReason}
        data-testid="profile-rename-input"
      />
      <ActionButton
        kind="primary"
        lockedReason={lockedReason}
        onExplain={onExplain}
        onPress={() => commit(value)}
        data-testid="profile-rename-save"
      >
        {RENAME_SAVE_LABEL}
      </ActionButton>
      <span data-rename-cancel="true">
        <ActionButton
          kind="ghost"
          onPress={cancel}
          data-testid="profile-rename-cancel"
        >
          {RENAME_CANCEL_LABEL}
        </ActionButton>
      </span>
    </div>
  );
}
