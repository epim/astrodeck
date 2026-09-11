// AuthMethodsEditor.tsx - SIGN-IN METHODS, rebuilt in the design's vocabulary
// (wave R7, T-R7-11; plan section 3.F5). Replaces
// `components/settings/AuthMethodPanel.tsx` at its one mount inside the new UI,
// `settings/sheets/AuthMethodsSheet.tsx`. The legacy file is untouched and
// still serves `#/classic`.
//
// THIS PANEL IS A DRAFT AND HAS TO SAY SO. Every switch here looks and moves
// like the immediate-write switches elsewhere in Settings, and nothing reaches
// the rig until SAVE METHODS. The pair the house uses is kept verbatim:
// mutually exclusive DIRTY and SAVED notes, so a switch showing a state the
// server does not hold is visibly a draft, and "Saved" can never outlive the
// save it names. The scenario that matters is an admin hardening the box:
// turning "Trust loopback as admin" OFF, seeing OFF beside a green "Saved", and
// walking away with loopback still trusted.
//
// SECRETS ARE WRITE-ONLY. The `auth` block the UI reads is REDACTED - the
// server blanks `admin_token`, `google_client_secret` and `session_private_key`
// before it ever leaves the rig, and reads a blank one back as "unchanged"
// (`server/astrodeck/api/app.py:_preserve_auth_secrets`). So the break-glass
// input starts empty on every mount, is never seeded from anything, and is only
// added to the save body when somebody has actually typed a new one. The only
// thing the server will say about the stored token is whether one exists
// (`admin_token_configured`), and that is what the disclosure's sub-line shows.
//
// NEW HERE, AND DELIBERATE: the break-glass token is now SETTABLE. The legacy
// panel could only report the state of a token an admin had to reach the
// server's filesystem or environment to set, which made the documented recovery
// path unreachable from the product. `POST /api/auth/config` has always
// accepted `admin_token`; this is the first control that uses it. Its two hard
// edges are stated where it is typed: the server refuses anything under 32
// bytes, and with no sign-in method enabled a saved token becomes the ONLY way
// in (`auth/deps.py` swaps `NoneAuthProvider` for `TokenAdminProvider`).

import { useEffect, useMemo, useState, type JSX } from "react";
import { listUsers, setAuthConfig } from "../../../../../api/backends";
import { SETUP_CARD_DISMISSED_KEY, setupCardState } from "../../../../../lib/setupCard";
import { useCanAdminUsers } from "../../../../../lib/caps";
import { useConfig, useStore } from "../../../../../store";
import type { AuthState, PrincipalRole, User } from "../../../../../types";
import { reconnectWs } from "../../../../../ws";
import { useLock, useOnRelay } from "../../../../lib/gateHook";
import {
  ActionButton, Card, Disclosure, EmptyCard, Field, LockNote, Mono, NumberField,
  Segmented, Switch, TextInput,
} from "../../../../ui";
import { AddUserForm } from "./AddUserForm";
import { ControlRow, Note, ScrollRow, Section, Verdict } from "./PeopleSection";
import {
  ALLOWLIST_ADD, ALLOWLIST_BLURB, ALLOWLIST_EMPTY, ALLOWLIST_MALFORMED, ALLOWLIST_TITLE,
  AUTH_ENABLED_TOAST, AUTH_EYEBROW, AUTH_INTRO, AUTH_LOADING, BREAKGLASS_ARIA,
  BREAKGLASS_BLURB, BREAKGLASS_HINT_SET, BREAKGLASS_HINT_UNSET, BREAKGLASS_LABEL,
  BREAKGLASS_OPEN_WARNING, BREAKGLASS_PLACEHOLDER, BREAKGLASS_SET, BREAKGLASS_SUMMARY,
  BREAKGLASS_UNSET, DEFAULT_ROLE_HINT, DEFAULT_ROLE_LABEL, DEFAULT_ROLE_VALUES, DENY,
  DIRTY_NOTE, FIRST_RUN_BLURB, FIRST_RUN_LABEL, FIRST_RUN_TITLE, GOOGLE_BLURB_READY,
  GOOGLE_BLURB_UNSET, GOOGLE_LABEL, GOOGLE_LOCK_REASON, GOOGLE_NOT_CONFIGURED, GOOGLE_TITLE,
  LOCAL_BLURB, LOCAL_LABEL, LOCAL_TITLE, LOCKOUT_WARNING, LOOPBACK_BLURB, LOOPBACK_LABEL,
  LOOPBACK_TITLE, METHODS_HIDDEN_HINT, METHODS_HIDDEN_TITLE, OPEN_WARNING, OPEN_WARNING_TOKEN,
  PEOPLE_CAP, PRINCIPAL_ROLES, SAVED_NOTE, SAVE_FAILED, SAVE_LABEL, SAVE_OPEN_LABEL,
  SETUP_DISMISS, SETUP_INTRO, SETUP_REOPEN, SETUP_STEP1, SETUP_STEP1_DONE, SETUP_STEP1_OPEN,
  SETUP_STEP2, SETUP_STEP2_ARMED, SETUP_STEP2_BTN, SETUP_STEP2_BTN_DONE, SETUP_STEP2_LOCKED,
  SETUP_STEP2_LOCK_REASON, SETUP_STEP2_ON_REASON, SETUP_STEP2_OPEN, SETUP_STEP3,
  SETUP_STEP3_BLURB, SETUP_TITLE, TTL_ARIA, TTL_HINT, TTL_LABEL, TTL_MAX, TTL_MIN,
  allowlistBody, authDirty, breakGlassBlocker, defaultRoleBlurb, draftOf, errText,
  hasMalformedAddress, methodsOf, roleWord, tokenBytes,
  type AuthDraft, type DefaultRole,
} from "./peopleModel";

/** The guided card's step marker: shape and a numeral, never colour alone. */
function StepBadge({ n, state }: { n: number; state: "done" | "locked" | "active" }): JSX.Element {
  return (
    <span className="nx-people-stepbadge" data-state={state} aria-hidden="true">
      {state === "done" ? "OK" : state === "locked" ? "-" : n}
    </span>
  );
}

export function AuthMethodsEditor(): JSX.Element {
  const config = useConfig();
  const auth = config?.auth ?? null;
  const canAdmin = useCanAdminUsers();
  // `GET /api/users` is on the fence by prefix for every method, so the guided
  // card's "does an enabled admin exist" read is refused over the relay too.
  // It stays honest without it (no confirmed admin), and SAVE METHODS is locked
  // by the same fence, so the card cannot mislead anyone into a half-setup.
  const onRelay = useOnRelay();
  // `needsLan`: SAVE METHODS is `POST /api/auth/config`, an EXACT entry on the
  // fence (every method), and the guided card reads `GET /api/users`, a fenced
  // prefix. A tunnelled session is a replayable bearer credential, so the rig
  // refuses both for an admin too.
  const { lockedReason, onExplain } = useLock({ cap: PEOPLE_CAP, needsLan: true });

  const [draft, setDraft] = useState<AuthDraft>({
    localOn: false, googleOn: false, ttlH: 8, firstRun: true, trustLoopback: true,
    defaultRole: DENY, allowlist: [],
  });
  // The write-only break-glass draft. Never seeded, never echoed: see the file
  // header. Cleared after every save.
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // The guided card reads the SAME list PEOPLE does, so "an enabled admin
  // exists" is a fact rather than a guess. Only fetched while auth is still
  // open, which is the only state the card can appear in.
  const [setupUsers, setSetupUsers] = useState<User[] | null>(null);
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try { return localStorage.getItem(SETUP_CARD_DISMISSED_KEY) === "1"; } catch { return false; }
  });
  const setDismissFlag = (next: boolean) => {
    setDismissed(next);
    // A per-device UI preference, not rig data, and deliberately the SAME key
    // the legacy panel uses: dismissing the guide in one root must not make it
    // reappear in the other.
    try { localStorage.setItem(SETUP_CARD_DISMISSED_KEY, next ? "1" : "0"); } catch { /* quota */ }
  };

  const refreshSetupUsers = async () => {
    if (!canAdmin || onRelay) return;
    try { setSetupUsers(await listUsers()); } catch { /* stays honest: no confirmed admin */ }
  };

  const seedKey = useMemo(
    () => (auth ? JSON.stringify([
      auth.methods, auth.session_ttl_s, auth.local_enabled_first_run,
      auth.trust_loopback, auth.default_role, auth.role_allowlist,
    ]) : ""),
    [auth],
  );

  useEffect(() => {
    if (!auth) return;
    setDraft(draftOf(auth));
    setToken("");
    if ((auth.methods ?? []).length === 0) void refreshSetupUsers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey, canAdmin]);

  if (!canAdmin) {
    return (
      <Section eyebrow={AUTH_EYEBROW} data-testid="auth-editor">
        <LockNote reason={lockedReason} data-testid="auth-locknote" />
        <EmptyCard title={METHODS_HIDDEN_TITLE} hint={METHODS_HIDDEN_HINT} data-testid="auth-hidden" />
      </Section>
    );
  }

  if (!auth) {
    return (
      <Section eyebrow={AUTH_EYEBROW} data-testid="auth-editor">
        <Card data-testid="auth-loading"><Mono>{AUTH_LOADING}</Mono></Card>
      </Section>
    );
  }

  const googleConfigured = !!auth.google_configured;
  const tokenConfigured = !!auth.admin_token_configured;
  const methods = methodsOf(draft);
  const openWarning = methods.length === 0;
  const dirty = authDirty(draft, auth, token);
  const tokenBlocker = breakGlassBlocker(token);

  // Keyed off the SERVER-persisted methods, not the draft toggles, so the card
  // does not vanish the instant a switch is flipped that has not been saved.
  const methodsPersisted = (auth.methods ?? []).length > 0;
  const hasEnabledAdmin = (setupUsers ?? []).some((u) => u.role === "admin" && u.enabled);
  const setup = setupCardState(methodsPersisted, hasEnabledAdmin, dismissed);

  const patch = (over: Partial<AuthDraft>) => setDraft((d) => ({ ...d, ...over }));

  const save = async () => {
    if (busy) return;
    setErr(null);
    setBusy(true);
    const wasEnabled = (auth.methods ?? []).length > 0;
    try {
      const body: Partial<AuthState> & Record<string, unknown> = {
        ...auth,
        methods,
        session_ttl_s: Math.max(TTL_MIN, draft.ttlH) * 3600,
        local_enabled_first_run: draft.firstRun,
        trust_loopback: draft.trustLoopback,
        default_role: draft.defaultRole === DENY ? null : draft.defaultRole,
        role_allowlist: allowlistBody(draft.allowlist),
        // Only when typed. An absent field is a blank secret, and a blank
        // secret means UNCHANGED - which is exactly what we want when nobody
        // touched the box.
        ...(tokenBytes(token) > 0 ? { admin_token: token.trim() } : {}),
      };
      await setAuthConfig(body);
      if (!wasEnabled && methods.length > 0) {
        // Sticky (ttl 0): the tab lands on Login next and a 2.8 s toast would
        // be swallowed by that transition.
        useStore.getState().enqueueToast({ level: "info", title: AUTH_ENABLED_TOAST, ttl: 0 });
      }
      await Promise.all([
        useStore.getState().loadConfig(),
        useStore.getState().loadAuthMethods(),
        // Enabling a method live-flips the provider, so THIS browser (which has
        // no session) now gets 401 from /api/me. Without re-resolving the
        // principal the tab keeps a stale admin identity over a dead link.
        useStore.getState().loadPrincipal(),
      ]);
      reconnectWs();
      setToken("");
      setSavedAt(Date.now());
    } catch (e) {
      setErr(errText(e, SAVE_FAILED));
    } finally {
      setBusy(false);
    }
  };

  const saveLock = lockedReason ?? tokenBlocker ?? (dirty ? null : "Nothing has changed since the last save.");

  return (
    <Section eyebrow={AUTH_EYEBROW} data-testid="auth-editor">
      <LockNote reason={lockedReason} data-testid="auth-locknote" />

      {/* ---------------------------------------------- guided setup card */}
      {setup.visible && (
        <Card tone="accent" data-testid="auth-setup-card">
          <div className="nx-people-head">
            <Mono size={11}>{SETUP_TITLE}</Mono>
            <ActionButton
              kind="ghost"
              onPress={() => setDismissFlag(true)}
              ariaLabel={SETUP_DISMISS}
              data-testid="auth-setup-dismiss"
            >
              DISMISS
            </ActionButton>
          </div>
          <Note>{SETUP_INTRO}</Note>

          <div className="nx-people-step">
            <StepBadge n={1} state={setup.step1Done ? "done" : "active"} />
            <div className="nx-people-steptext">
              <div className="nx-people-ctltitle">{SETUP_STEP1}</div>
              <Note>{setup.step1Done ? SETUP_STEP1_DONE : SETUP_STEP1_OPEN}</Note>
              {!setup.step1Done && (
                <AddUserForm
                  defaultRole="admin"
                  lockedReason={lockedReason}
                  onExplain={onExplain}
                  onCreated={refreshSetupUsers}
                />
              )}
            </div>
          </div>

          <div className="nx-people-step">
            <StepBadge n={2} state={setup.step2Enabled ? "active" : "locked"} />
            <div className="nx-people-steptext">
              <div className="nx-people-ctltitle">{SETUP_STEP2}</div>
              <Note>
                {!setup.step2Enabled ? SETUP_STEP2_LOCKED
                  : draft.localOn ? SETUP_STEP2_ARMED : SETUP_STEP2_OPEN}
              </Note>
              <ActionButton
                kind="primary"
                onPress={() => patch({ localOn: true })}
                lockedReason={
                  lockedReason
                  ?? (!setup.step2Enabled ? SETUP_STEP2_LOCK_REASON
                    : draft.localOn ? SETUP_STEP2_ON_REASON : null)
                }
                onExplain={onExplain}
                data-testid="auth-setup-enable-local"
              >
                {draft.localOn ? SETUP_STEP2_BTN_DONE : SETUP_STEP2_BTN}
              </ActionButton>
            </div>
          </div>

          <div className="nx-people-step">
            <StepBadge n={3} state="active" />
            <div className="nx-people-steptext">
              <div className="nx-people-ctltitle">{SETUP_STEP3}</div>
              <Note>{SETUP_STEP3_BLURB}</Note>
            </div>
          </div>
        </Card>
      )}

      {/* The un-dismiss affordance: only while auth is still open. */}
      {!methodsPersisted && dismissed && (
        <ActionButton
          kind="ghost"
          onPress={() => setDismissFlag(false)}
          data-testid="auth-setup-reopen"
        >
          {SETUP_REOPEN}
        </ActionButton>
      )}

      {/* ------------------------------------------------------- methods */}
      <Card data-testid="auth-methods-card">
        <Note>{AUTH_INTRO}</Note>

        <ControlRow
          title={LOCAL_TITLE}
          blurb={LOCAL_BLURB}
          right={
            <Switch
              checked={draft.localOn}
              onChange={(v) => patch({ localOn: v })}
              label={LOCAL_LABEL}
              hideLabel
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid="auth-local"
            />
          }
        />

        <ControlRow
          title={GOOGLE_TITLE}
          badge={googleConfigured ? undefined : GOOGLE_NOT_CONFIGURED}
          blurb={googleConfigured ? GOOGLE_BLURB_READY : GOOGLE_BLURB_UNSET}
          right={
            <Switch
              checked={draft.googleOn}
              onChange={(v) => patch({ googleOn: v })}
              label={GOOGLE_LABEL}
              hideLabel
              lockedReason={lockedReason ?? (googleConfigured ? null : GOOGLE_LOCK_REASON)}
              onExplain={onExplain}
              data-testid="auth-google"
            />
          }
        />

        <div className="nx-people-grid">
          <NumberField
            label={TTL_LABEL}
            hint={TTL_HINT}
            ariaLabel={TTL_ARIA}
            unit="h"
            value={draft.ttlH}
            min={TTL_MIN}
            max={TTL_MAX}
            integer
            onCommit={(v) => patch({ ttlH: v })}
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid="auth-ttl"
          />

          <Field label={DEFAULT_ROLE_LABEL} hint={`${DEFAULT_ROLE_HINT} ${defaultRoleBlurb(draft.defaultRole)}`}>
            <ScrollRow>
              <Segmented<DefaultRole>
                label={DEFAULT_ROLE_LABEL}
                value={draft.defaultRole}
                onChange={(v) => patch({ defaultRole: v })}
                lockedReason={lockedReason}
                onExplain={onExplain}
                data-testid="auth-default-role"
                options={DEFAULT_ROLE_VALUES.map((r) => ({ value: r, label: roleWord(r) }))}
              />
            </ScrollRow>
          </Field>
        </div>

        {/* ------------------------------------------------- allowlist */}
        {draft.googleOn && (
          <div data-testid="auth-allowlist">
            <ControlRow
              title={ALLOWLIST_TITLE}
              blurb={ALLOWLIST_BLURB}
              right={
                <ActionButton
                  kind="secondary"
                  onPress={() => patch({ allowlist: [...draft.allowlist, ["", "viewer"]] })}
                  lockedReason={lockedReason}
                  onExplain={onExplain}
                  data-testid="auth-allow-add"
                >
                  {ALLOWLIST_ADD}
                </ActionButton>
              }
            />
            {draft.allowlist.length === 0 ? (
              <Note>{ALLOWLIST_EMPTY}</Note>
            ) : (
              draft.allowlist.map(([email, role], i) => (
                <div className="nx-people-allow" key={i} data-testid="auth-allow-row">
                  <TextInput
                    type="email"
                    value={email}
                    onChange={(v) => patch({
                      allowlist: draft.allowlist.map((row, j) => (j === i ? [v, row[1]] : row)),
                    })}
                    placeholder="name@example.com"
                    ariaLabel={`Allowed address ${i + 1}`}
                    lockedReason={lockedReason}
                  />
                  <ScrollRow>
                    <Segmented<string>
                      label={`Role for ${email || `address ${i + 1}`}`}
                      value={role}
                      onChange={(v) => patch({
                        allowlist: draft.allowlist.map((row, j) => (j === i ? [row[0], v] : row)),
                      })}
                      lockedReason={lockedReason}
                      onExplain={onExplain}
                      options={PRINCIPAL_ROLES.map((r: PrincipalRole) => ({ value: r as string, label: roleWord(r) }))}
                    />
                  </ScrollRow>
                  <ActionButton
                    kind="ghost"
                    onPress={() => patch({ allowlist: draft.allowlist.filter((_, j) => j !== i) })}
                    ariaLabel={`Remove ${email || `address ${i + 1}`}`}
                    lockedReason={lockedReason}
                    onExplain={onExplain}
                  >
                    REMOVE
                  </ActionButton>
                </div>
              ))
            )}
            {hasMalformedAddress(draft.allowlist) && (
              <Note tone="warn" data-testid="auth-allow-malformed">{ALLOWLIST_MALFORMED}</Note>
            )}
          </div>
        )}

        <ControlRow
          title={FIRST_RUN_TITLE}
          blurb={FIRST_RUN_BLURB}
          right={
            <Switch
              checked={draft.firstRun}
              onChange={(v) => patch({ firstRun: v })}
              label={FIRST_RUN_LABEL}
              hideLabel
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid="auth-firstrun"
            />
          }
        />

        <ControlRow
          title={LOOPBACK_TITLE}
          blurb={LOOPBACK_BLURB}
          right={
            <Switch
              checked={draft.trustLoopback}
              onChange={(v) => patch({ trustLoopback: v })}
              label={LOOPBACK_LABEL}
              hideLabel
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid="auth-loopback"
            />
          }
        />

        {openWarning && (
          <Note tone="warn" data-testid="auth-open-warning">
            {tokenConfigured ? `${OPEN_WARNING} ${OPEN_WARNING_TOKEN}` : OPEN_WARNING}
          </Note>
        )}
        {openWarning && !draft.trustLoopback && (
          <Note tone="bad" data-testid="auth-lockout-warning">{LOCKOUT_WARNING}</Note>
        )}

        {err && <Verdict tone="bad" data-testid="auth-error">{err}</Verdict>}

        <div className="nx-people-actions">
          <ActionButton
            kind={openWarning ? "danger" : "primary"}
            onPress={() => { void save(); }}
            busy={busy}
            lockedReason={saveLock}
            onExplain={onExplain}
            data-testid="auth-save"
          >
            {openWarning ? SAVE_OPEN_LABEL : SAVE_LABEL}
          </ActionButton>
          {/* Mutually exclusive, so "Saved" can never outlive its save. */}
          {dirty && !busy && <Mono tone="warn" data-testid="auth-dirty">{DIRTY_NOTE}</Mono>}
          {savedAt != null && !dirty && !busy && !err && (
            <Mono tone="good" data-testid="auth-saved">{SAVED_NOTE}</Mono>
          )}
        </div>
      </Card>

      {/* ----------------------------------------------- break-glass token */}
      <Disclosure
        summary={BREAKGLASS_SUMMARY}
        sub={tokenConfigured ? BREAKGLASS_SET : BREAKGLASS_UNSET}
        data-testid="auth-breakglass"
      >
        <Note>{BREAKGLASS_BLURB}</Note>
        {openWarning && <Note tone="warn" data-testid="auth-breakglass-open">{BREAKGLASS_OPEN_WARNING}</Note>}
        <Field
          label={BREAKGLASS_LABEL}
          hint={tokenConfigured ? BREAKGLASS_HINT_SET : BREAKGLASS_HINT_UNSET}
        >
          <TextInput
            type="password"
            value={token}
            onChange={setToken}
            placeholder={BREAKGLASS_PLACEHOLDER}
            ariaLabel={BREAKGLASS_ARIA}
            lockedReason={lockedReason}
            data-testid="auth-breakglass-input"
          />
        </Field>
        {tokenBlocker && <Verdict tone="bad" data-testid="auth-breakglass-error">{tokenBlocker}</Verdict>}
        <Note>{`It is saved with everything else: press ${SAVE_LABEL} above.`}</Note>
      </Disclosure>
    </Section>
  );
}
