// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// UserRow.tsx - one local account, and the five things an admin can do to it
// (wave R7, T-R7-11; plan section 3.F4).
//
// role / enable / reset password / delete, plus the identity block. TWO of the
// five go through a hold-confirm because they are self-harm rather than
// destruction: demoting or disabling YOUR OWN account silently drops your own
// access and fires no "last admin" 409 as long as another admin exists, so the
// server will happily do it and say nothing. Delete is the third confirm.
//
// OVER THE RELAY (#734, #731). The rig refuses a password reset outright, and
// it will not hand out the admin or syncer role, so those two controls are locked
// with the LAN sentence BEFORE anything is typed or pressed (`lanOnlyReason`,
// null on the LAN): a locked control sends nothing, which matters most for the
// password the relay would otherwise carry on its way to being refused. Every
// other control stays armed, because the rig does those behind a recent sign-in.
// When it asks for one (`step_up_required`), the row hands the editor a way to
// send the SAME change again (`onStepUp`) once the person has signed in.
//
// THE RESET ROW IS ITS OWN ERROR SURFACE. In the legacy panel a failed reset
// ended exactly like a successful one - field cleared, row collapsed - because
// it shared the panel-wide `run()` whose catch prints at the TOP of the list,
// above every other person's row. The one place the failure was stated was the
// one place it did not look like it was about this account, and the typed
// password was gone. Here the row stays open with what was typed still in it
// and the reason under the field.

import { useState, type JSX } from "react";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import { deleteUser, patchUser, resetUserPassword } from "../../../../../api/backends";
import { ROLE_DESCRIPTIONS } from "../../../../../lib/caps";
import { useStore } from "../../../../../store";
import type { PrincipalRole, User } from "../../../../../types";
import { ActionButton, Field, Segmented, Switch, TextInput } from "../../../../ui";
import { EyeGlyph, KeyGlyph, PersonGlyph, ShieldGlyph, TrashGlyph } from "./glyphs";
import { Note, ScrollRow, Verdict } from "./PeopleSection";
import {
  CONFIRM_SELF_DEMOTE, CONFIRM_SELF_DISABLE, DISABLED_BADGE, NO_EMAIL, PASSWORD_TOO_LONG,
  PEOPLE_RELAY_ROLES, PRINCIPAL_ROLES, USERS_DELETE_FAILED, USERS_RESET_FAILED,
  USERS_ROLE_FAILED, USERS_STATUS_FAILED, YOU_BADGE, confirmDelete, errText,
  isStepUpRequired, roleWord,
} from "./peopleModel";

const MAX_PASSWORD_BYTES = 72;

export function UserRow({
  user, isSelf, onChanged, onError, lockedReason, onExplain, lanOnlyReason = null, onStepUp,
}: {
  user: User;
  /** Whether this row is the signed-in principal. Drives the YOU badge and the
   *  two self-harm confirms. */
  isSelf: boolean;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  /** `gate.ts`'s `LOCAL_ONLY_REASON` while this tab is on the relay, else null.
   *  It locks RESET and the role options the relay may not hand out, and nothing
   *  else on the row: the rig does the rest over the relay. It outranks
   *  `lockedReason`, which is the order `gate.ts` ranks the two in. */
  lanOnlyReason?: string | null;
  /** The rig refused this change for want of a recent sign-in. `retry` sends the
   *  same change again; the editor calls it once the person has signed in. */
  onStepUp?: (retry: () => void) => void;
}): JSX.Element {
  const showToast = useStore((s) => s.showToast);
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [pw, setPw] = useState("");
  const [pwErr, setPwErr] = useState<string | null>(null);
  const pwTooLong = new TextEncoder().encode(pw).length > MAX_PASSWORD_BYTES;

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      await onChanged();
    } catch (e) {
      onError(errText(e, fallback));
      // A refusal for want of a recent sign-in is not a verdict on the change:
      // the editor is handed a way to send it again once the person has signed
      // in, with whatever they had already confirmed (a hold-confirm is not
      // asked twice).
      if (isStepUpRequired(e)) onStepUp?.(() => { void run(fn, fallback); });
    } finally {
      setBusy(false);
    }
  };

  const onRole = async (role: PrincipalRole) => {
    if (role === user.role) return;
    if (isSelf && user.role === "admin" && role !== "admin") {
      const ok = await confirmDialog({ ...CONFIRM_SELF_DEMOTE, tone: "danger", mode: "hold" });
      if (!ok) return;
    }
    await run(() => patchUser(user.id, { role }), USERS_ROLE_FAILED);
  };

  const onToggle = async (enabled: boolean) => {
    if (isSelf && !enabled) {
      const ok = await confirmDialog({ ...CONFIRM_SELF_DISABLE, tone: "danger", mode: "hold" });
      if (!ok) return;
    }
    await run(() => patchUser(user.id, { enabled }), USERS_STATUS_FAILED);
  };

  const onDelete = async () => {
    const ok = await confirmDialog({ ...confirmDelete(user.username), tone: "danger", mode: "hold" });
    if (!ok) return;
    await run(() => deleteUser(user.id), USERS_DELETE_FAILED);
  };

  const submitReset = async () => {
    if (busy || pw === "" || pwTooLong) return;
    setPwErr(null);
    setBusy(true);
    try {
      await resetUserPassword(user.id, pw);
      // No list refresh: a password is not part of the account's public shape,
      // so nothing on this row can have changed.
      setPw("");
      setResetting(false);
      showToast("success", `New password set for ${user.username}.`);
    } catch (e) {
      setPwErr(errText(e, USERS_RESET_FAILED));
    } finally {
      setBusy(false);
    }
  };

  const Glyph = user.role === "admin" ? ShieldGlyph : user.enabled ? PersonGlyph : EyeGlyph;
  // The reset is refused outright over the relay, so its lock is the LAN
  // sentence there, and the group's own lock (no admin.users, a dead link) on
  // the LAN.
  const resetLock = lanOnlyReason ?? lockedReason;
  // A blocked reset would leave the typed password on screen with nothing to do
  // about it, so the reason names the field rather than the button.
  const resetBlocker = resetLock
    ?? (pw === "" ? "Type the new password first." : pwTooLong ? PASSWORD_TOO_LONG : null);

  return (
    <div className="nx-people-row" data-testid="users-row" data-enabled={user.enabled ? "true" : "false"}>
      <span className="nx-people-rowtile" aria-hidden="true"><Glyph size={18} /></span>

      <div className="nx-people-rowtext">
        <div className="nx-people-rowname">
          <span className="nx-people-rowuser">{user.username}</span>
          {isSelf && <span className="nx-people-badge" data-tone="accent">{YOU_BADGE}</span>}
          {!user.enabled && <span className="nx-people-badge" data-tone="warn">{DISABLED_BADGE}</span>}
        </div>
        <div className="nx-people-rowmail">{user.email || NO_EMAIL}</div>
        <Note>{ROLE_DESCRIPTIONS[user.role]}</Note>
      </div>

      <div className="nx-people-rowctl">
        <ScrollRow>
          <Segmented<PrincipalRole>
            label={`Role for ${user.username}`}
            value={user.role}
            onChange={(r) => { void onRole(r); }}
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid="users-row-role"
            options={PRINCIPAL_ROLES.map((r) => ({
              value: r,
              label: roleWord(r),
              // The rig will not hand a relayed admin's reach out over the
              // relay: only viewer and operator. The row's own role is left
              // alone, since pressing it changes nothing.
              lockedReason: lanOnlyReason && r !== user.role && !PEOPLE_RELAY_ROLES.includes(r)
                ? lanOnlyReason : null,
            }))}
          />
        </ScrollRow>

        <div className="nx-people-rowbtns">
          <Switch
            checked={user.enabled}
            onChange={(v) => { void onToggle(v); }}
            label={`${user.enabled ? "Disable" : "Enable"} ${user.username}`}
            hideLabel
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid="users-row-enabled"
          />
          <ActionButton
            kind="secondary"
            glyph={<KeyGlyph />}
            onPress={() => { setResetting((v) => !v); setPwErr(null); }}
            lockedReason={resetLock}
            onExplain={onExplain}
            ariaLabel={`Reset password for ${user.username}`}
            data-testid="users-row-reset"
          >
            RESET
          </ActionButton>
          <ActionButton
            kind="danger"
            glyph={<TrashGlyph />}
            onPress={() => { void onDelete(); }}
            lockedReason={lockedReason}
            onExplain={onExplain}
            ariaLabel={`Delete ${user.username}`}
            data-testid="users-row-delete"
          >
            DELETE
          </ActionButton>
        </div>
      </div>

      {resetting && (
        <div className="nx-people-reset" data-testid="users-reset-form">
          <Field
            label={`New password for ${user.username}`}
            hint={pwTooLong ? PASSWORD_TOO_LONG : "Bcrypt truncates past 72 bytes, so that is the limit."}
          >
            <TextInput
              type="password"
              value={pw}
              onChange={(v) => { setPw(v); setPwErr(null); }}
              ariaLabel={`New password for ${user.username}`}
              lockedReason={resetLock}
              data-testid="users-reset-input"
            />
          </Field>
          {pwErr && !pwTooLong && (
            <Verdict tone="bad" data-testid="users-reset-error">
              {`${pwErr} ${user.username}'s password is unchanged.`}
            </Verdict>
          )}
          <div className="nx-people-actions">
            <ActionButton
              kind="primary"
              onPress={() => { void submitReset(); }}
              busy={busy}
              lockedReason={resetBlocker}
              onExplain={onExplain}
              data-testid="users-reset-submit"
            >
              SET PASSWORD
            </ActionButton>
            <ActionButton
              kind="ghost"
              onPress={() => { setResetting(false); setPw(""); setPwErr(null); }}
              data-testid="users-reset-cancel"
            >
              CANCEL
            </ActionButton>
          </div>
        </div>
      )}
    </div>
  );
}
