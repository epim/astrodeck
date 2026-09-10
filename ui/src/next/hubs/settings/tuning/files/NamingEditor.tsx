// NamingEditor.tsx - the FILE NAMING sheet's body, rebuilt in the design's own
// vocabulary (wave R7, T-R7-13; replaces the mounted
// `components/settings/NamingPanel.tsx`, which is NOT edited and keeps serving
// `#/classic`).
//
// THE PREVIEW IS THE FEATURE. A template language nobody can see the output of
// is a guess about where tonight's frames will land, so the preview re-renders
// on every keystroke and every token chip, LOCALLY, with no request - and the
// path it shows comes from `lib/naming.ts`, the byte-for-byte mirror of the
// server's `render_relative_path` that the shared golden vectors keep honest.
// It is still advisory: the server's own render is what names the file, and the
// footnote says so rather than letting the preview imply authority.
//
// THREE `aria-disabled`-BUT-INERT CONTROLS ARE GONE. The legacy panel hung
// `onClick={undefined}` on the token chips, SAVE and RESET for a viewer
// (`NamingPanel.tsx` :279, :289, :295): a press did nothing at all and the only
// explanation was a `title=`, which never fires on touch. Every one is now
// `lockedReason` + `onExplain`, so a press says why.
//
// `aria-label="File-naming template"` is KEPT verbatim from the legacy input.
// It is the accessible name the field has always had, and it is what the
// settings sheet's own no-remount regression test addresses the box by.

import { useRef, useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { setNamingConfig } from "../../../../../api/backends";
import { accessPhrase } from "../../../../../lib/caps";
import { DEFAULT_TEMPLATE, NAMING_TOKENS } from "../../../../../lib/naming";
import { useConfig, useStore } from "../../../../../store";
import { useLock } from "../../../../lib/gateHook";
import { ActionButton, Card, Chip, Field, Label, LockNote, Mono, TextInput } from "../../../../ui";
import {
  filesLockSentence, NAMING_FOOTNOTE, NAMING_SUBJECT, namingPreview, tokenText,
} from "./filesModel";
import "./files.css";

export function NamingEditor(): JSX.Element {
  const config = useConfig();
  const stored = config?.naming?.template ?? DEFAULT_TEMPLATE;

  const gate = useLock({ cap: "config.site_optics" });
  const lock = filesLockSentence(
    gate.lockedReason, accessPhrase("config.site_optics"), NAMING_SUBJECT,
  );
  const explain = gate.onExplain;

  const [draft, setDraft] = useState(stored);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Re-seed from the server's value ONLY when that value itself changes -
  // during render, not in an effect. An effect would repaint the old template
  // for a frame after a save; a `key` keyed on `config.version` would throw the
  // draft away every time an UNRELATED config write bumped the version, which
  // is the hazard the sheet's own regression test guards.
  const seen = useRef(stored);
  if (seen.current !== stored) {
    seen.current = stored;
    setDraft(stored);
  }

  const dirty = draft !== stored;
  const atDefault = draft === DEFAULT_TEMPLATE;

  const save = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await setNamingConfig({ template: draft });
      await useStore.getState().loadConfig();
      useStore.getState().showToast("success", "capture naming saved");
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? `Refused - ${accessPhrase("config.site_optics")} is needed here.`
          : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  // Nothing to save is not a permission problem, so it gets its own sentence
  // rather than borrowing the lock's.
  const saveLock = lock ?? (dirty ? null : "the template already matches what the rig has");
  const resetLock = lock ?? (atDefault ? "the template is already the default" : null);

  return (
    <div className="nx-files-stack" data-testid="naming-editor">
      <Card>
        <div className="nx-files-col">
          <Field label="TEMPLATE">
            <TextInput
              value={draft}
              onChange={setDraft}
              mono
              ariaLabel="File-naming template"
              lockedReason={lock}
              data-testid="naming-template"
            />
          </Field>

          <Label size={11}>WHERE THE NEXT FRAME WOULD LAND</Label>
          <Mono size={11.5} className="nx-files-preview" data-testid="naming-preview">
            {namingPreview(draft)}
          </Mono>

          <Label size={11}>TOKENS - TAP TO APPEND</Label>
          <div className="nx-files-chips" role="group" aria-label="Naming tokens">
            {NAMING_TOKENS.map((t) => (
              <Chip
                key={t}
                tone="dim"
                lockedReason={lock}
                onExplain={explain}
                onClick={() => setDraft((d) => `${d}${tokenText(t)}`)}
                data-testid="naming-token"
              >
                {tokenText(t)}
              </Chip>
            ))}
          </div>

          <div className="nx-files-row">
            <ActionButton
              kind="primary"
              busy={busy}
              lockedReason={saveLock}
              onExplain={explain}
              onPress={() => void save()}
              data-testid="naming-save"
            >
              SAVE
            </ActionButton>
            <ActionButton
              kind="ghost"
              lockedReason={resetLock}
              onExplain={explain}
              onPress={() => setDraft(DEFAULT_TEMPLATE)}
              data-testid="naming-reset"
            >
              RESET TO DEFAULT
            </ActionButton>
          </div>

          <p className="nx-files-note">{NAMING_FOOTNOTE}</p>
        </div>
      </Card>

      {err && <p className="nx-files-err" data-testid="naming-error">{err}</p>}
      <LockNote reason={lock} data-testid="naming-lock" />
    </div>
  );
}
