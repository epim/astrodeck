// CreditsBrowser.tsx - Settings > CREDITS AND LICENCES, rebuilt in the design
// vocabulary (wave R7, T-R7-12; parity table 3.F9).
//
// Everything AstroDeck is built on, and what each licence asks of us.
//
// WHY THE DATA ARRIVES BY DYNAMIC import(). The generated credits are ~550 KB
// of licence text - mostly the same Apache-2.0 and MIT bodies, de-duplicated
// into a pool and referenced by hash, but still far more than any other screen
// in Settings. A static import would put all of it in the settings chunk, which
// every user loads to change their site latitude. `import()` gives it its own
// chunk, fetched the first time somebody opens this sheet and never again.
//
// It is still OFFLINE-SAFE, which is the requirement that actually matters: the
// chunk is built into ui/dist and served by AstroDeck's own web server from the
// same origin as the app. A rig with no internet loses nothing. The only way it
// fails is a broken build - and the failure is caught and shown rather than
// left as a blank screen.
//
// THE GROUPS ARE `Disclosure`s (wave plan 3.F9). 123 entries expanded is not a
// page anybody reads, so they start collapsed - with two exceptions: the group
// holding owner decisions, and any state where a search has already narrowed
// the list, at which point hiding the matches behind a second click is just a
// second click.
//
// The `data` prop exists so a DOM test can mount this with a fixture instead of
// exercising the bundler.

import { useEffect, useMemo, useState, type JSX } from "react";
import {
  countEntries, filterGroups, flaggedEntries, hasReproducedText, obligationLabel,
  type CreditEntry, type CreditGroup, type CreditsDoc,
} from "../../../../../lib/credits";
import { Card, Disclosure, Field, Label, Mono, Pill, TextInput } from "../../../../ui";
import {
  CREDITS_OFFLINE_NOTE, CREDITS_OPEN_NOTE, CREDITS_SEARCH_PLACEHOLDER,
} from "./systemModel";
import "./system.css";

/** One licence body, behind its own disclosure. `pre-wrap` keeps the licence's
 *  own line breaks, which for a legal text is part of the text; the container
 *  scrolls sideways so a long unbroken URL inside a licence cannot widen the
 *  phone. */
function LicenceText({ title, body }: { title: string; body: string }): JSX.Element {
  return (
    <Disclosure summary={title} sub="licence text">
      <pre className="nx-sys-pre" data-testid="credits-licence-text">{body}</pre>
    </Disclosure>
  );
}

function EntryRow({ entry, doc }: { entry: CreditEntry; doc: CreditsDoc }): JSX.Element {
  return (
    <li
      className="nx-sys-entry"
      data-flagged={entry.flag ? "true" : undefined}
      data-credit-entry={entry.name}
      data-testid="credits-entry"
    >
      <div className="nx-sys-entryhead">
        <span className="nx-sys-entryname">{entry.name}</span>
        {entry.version && <Mono size={10}>{entry.version}</Mono>}
        <Pill tone="dim">{entry.spdx}</Pill>
      </div>

      {entry.requires.length > 0 && (
        <div className="nx-sys-chips">
          {entry.requires.map((code) => (
            <Pill key={code} tone="dim">{obligationLabel(code)}</Pill>
          ))}
        </div>
      )}

      {entry.summaries.map((s) => <p key={s} className="nx-sys-note">{s}</p>)}

      {entry.flag && (
        <p className="nx-sys-warn" data-testid="credits-flag">
          {`Needs a decision: ${entry.flag}`}
        </p>
      )}

      {entry.notes && <p className="nx-sys-note">{entry.notes}</p>}

      {entry.url && (
        <p className="nx-sys-note">
          <a className="nx-sys-link" href={entry.url} target="_blank" rel="noreferrer">
            {entry.url}
          </a>
        </p>
      )}

      {entry.texts.map((t) => {
        const body = doc.licenses[t.hash];
        // A dangling hash means the pool and the entries disagree, which the
        // test suite forbids. Say so on screen rather than render nothing:
        // silently dropping a licence body is the exact failure this page
        // exists to prevent.
        return body ? (
          <LicenceText key={t.hash} title={t.title} body={body} />
        ) : (
          <p key={t.hash} className="nx-sys-bad">
            {`Licence text "${t.title}" is missing from this build. Regenerate with `}
            <span className="nx-sys-code">python tools/gen_credits.py</span>
          </p>
        );
      })}

      {!hasReproducedText(doc, entry) && entry.requires.includes("notice") && (
        <p className="nx-sys-bad">
          This licence requires its notice to be reproduced and no text shipped.
        </p>
      )}
    </li>
  );
}

export function CreditsBrowser({ data }: { data?: CreditsDoc }): JSX.Element {
  const [doc, setDoc] = useState<CreditsDoc | null>(data ?? null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (data) return;
    let alive = true;
    import("../../../../../credits.generated.json")
      .then((m) => alive && setDoc((m.default ?? m) as unknown as CreditsDoc))
      .catch((e: unknown) =>
        alive && setError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [data]);

  const searching = query.trim().length > 0;
  // A search that narrows to three results should not leave them behind a
  // closed disclosure, so entering and leaving the searching state drops every
  // manual open/closed choice and the defaults below apply again.
  useEffect(() => { setOpenMap({}); }, [searching]);

  const groups = useMemo(() => (doc ? filterGroups(doc, query) : []), [doc, query]);
  const flagged = useMemo(() => (doc ? flaggedEntries(doc) : []), [doc]);

  if (error) {
    return (
      <Card padding={12} data-testid="credits-error">
        <div className="nx-sys-stack">
          <Label size={11}>Credits did not load</Label>
          <p className="nx-sys-bad">{error}</p>
          <p className="nx-sys-note">{CREDITS_OFFLINE_NOTE}</p>
        </div>
      </Card>
    );
  }
  if (!doc) {
    return (
      <Card padding={12} data-testid="credits-loading">
        <p className="nx-sys-note">Loading the licence texts...</p>
      </Card>
    );
  }

  const total = countEntries(doc);
  const shown = groups.reduce((n, g) => n + g.entries.length, 0);
  const isOpen = (g: CreditGroup) => openMap[g.id] ?? (g.id === "flagged" || searching);

  return (
    <div className="nx-sys-stack">
      <Card padding={12} data-testid="credits-summary">
        <div className="nx-sys-stack">
          <div className="nx-sys-head">
            <Label size={11}>{`${doc.project.name} ${doc.project.version}`}</Label>
            <Mono size={10.5}>{doc.project.spdx}</Mono>
          </div>
          <p className="nx-sys-note">
            {`Built on ${total} components, with each licence reproduced in full rather than `
              + "linked, so this page works with no internet."}
          </p>
          <p className="nx-sys-note">{doc.scope}</p>
          <p className="nx-sys-note">{CREDITS_OPEN_NOTE}</p>

          {flagged.length > 0 && (
            <p className="nx-sys-warn" data-testid="credits-flagged-count">
              {`${flagged.length} licence${flagged.length === 1 ? "" : "s"} need a decision from `
                + "the project owner. They are listed first below. Nothing here is settled by "
                + "this page being published."}
            </p>
          )}

          <Field label="Search" hint="Name, version, licence id and obligations - not the licence bodies, or every MIT package would match on the word warranty.">
            <TextInput
              value={query}
              onChange={setQuery}
              type="search"
              placeholder={CREDITS_SEARCH_PLACEHOLDER}
              ariaLabel="Search the credits"
              data-testid="credits-search"
            />
          </Field>

          <Mono size={10.5} data-testid="credits-count">
            {shown === total ? `Showing all ${total}` : `Showing ${shown} of ${total}`}
          </Mono>
        </div>
      </Card>

      {groups.map((g) => (
        <Card key={g.id} padding={12} data-testid="credits-group">
          <Disclosure
            summary={g.title}
            sub={`${g.entries.length} - ${g.blurb}`}
            open={isOpen(g)}
            onToggle={(next) => setOpenMap((m) => ({ ...m, [g.id]: next }))}
            data-testid={`credits-group-${g.id}`}
          >
            <ul className="nx-sys-entries">
              {g.entries.map((e) => (
                <EntryRow key={`${g.id}:${e.name}`} entry={e} doc={doc} />
              ))}
            </ul>
          </Disclosure>
        </Card>
      ))}

      {groups.length === 0 && (
        <Card padding={12} data-testid="credits-nomatch">
          <p className="nx-sys-note">{`Nothing matches "${query}".`}</p>
        </Card>
      )}
    </div>
  );
}

export default CreditsBrowser;
