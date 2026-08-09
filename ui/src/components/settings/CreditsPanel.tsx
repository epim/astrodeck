// CreditsPanel.tsx — everything AstroDeck is built on, and what each licence
// asks of us.
//
// WHY THE DATA ARRIVES BY DYNAMIC import(). The generated credits are ~550 KB
// of licence text — mostly the same Apache-2.0 and MIT bodies, de-duplicated
// into a pool and referenced by hash, but still far more than any other panel
// in Settings. A static import would put all of it in the settings chunk, which
// every user loads to change their site latitude. `import()` gives it its own
// chunk that is fetched the first time somebody opens this tab and never again.
//
// It is still OFFLINE-SAFE, which is the requirement that actually matters
// here: the chunk is built into ui/dist and served by AstroDeck's own web
// server from the same origin as the app. A rig with no internet loses nothing.
// The only way it fails is a broken build, which breaks the whole app anyway —
// and the failure is caught and shown rather than left as a blank tab.
//
// The `data` prop exists so the DOM test can mount this with a fixture instead
// of exercising the bundler.

import { useEffect, useMemo, useState, type JSX } from "react";
import { Panel } from "../ui";
import { Icon } from "../icons";
import {
  countEntries,
  filterGroups,
  flaggedEntries,
  hasReproducedText,
  obligationLabel,
  type CreditEntry,
  type CreditsDoc,
} from "../../lib/credits";

function LicenceText({ title, body }: { title: string; body: string }): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="tap text-xs text-dim hover:text-ink flex items-center gap-1"
        aria-expanded={open}
      >
        <Icon name={open ? "arrow-down" : "arrow-right"} size={12} />
        {open ? "Hide" : "Read"} {title}
      </button>
      {open && (
        // `whitespace-pre-wrap` keeps the licence's own line breaks, which for
        // a legal text is part of the text. `overflow-x-auto` stops a long
        // unbroken URL inside a licence from widening the whole page on a
        // phone — the body must never scroll sideways.
        <pre className="mono mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-[var(--color-line)] bg-[var(--color-bg)] p-3 text-[11px] leading-relaxed text-dim">
          {body}
        </pre>
      )}
    </div>
  );
}

function EntryRow({ entry, doc }: { entry: CreditEntry; doc: CreditsDoc }): JSX.Element {
  const flagged = !!entry.flag;
  return (
    <li
      className={`py-3 ${flagged ? "border-l-2 border-[var(--color-warn)] pl-3" : ""}`}
      data-credit-entry={entry.name}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-medium break-words">{entry.name}</span>
        {entry.version && <span className="mono text-xs text-dim">{entry.version}</span>}
        <span className="mono text-[11px] rounded bg-[var(--color-raise)] px-1.5 py-0.5 text-dim">
          {entry.spdx}
        </span>
      </div>

      {entry.requires.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {entry.requires.map((code) => (
            <span
              key={code}
              className="text-[11px] rounded bg-[var(--color-raise)] px-1.5 py-0.5 text-dim"
            >
              {obligationLabel(code)}
            </span>
          ))}
        </div>
      )}

      {entry.summaries.map((s) => (
        <p key={s} className="mt-1 text-xs text-dim">
          {s}
        </p>
      ))}

      {flagged && (
        <p className="mt-2 text-xs text-[var(--color-warn)]">
          <strong>Needs a decision:</strong> {entry.flag}
        </p>
      )}

      {entry.notes && (
        <p className="mt-2 whitespace-pre-line text-xs text-dim">{entry.notes}</p>
      )}

      {entry.url && (
        <p className="mt-1 text-xs">
          <a
            className="text-accent break-all underline"
            href={entry.url}
            target="_blank"
            rel="noreferrer"
          >
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
          <p key={t.hash} className="mt-2 text-xs text-[var(--color-bad)]">
            Licence text “{t.title}” is missing from this build. Regenerate with
            <span className="mono"> python tools/gen_credits.py</span>.
          </p>
        );
      })}

      {!hasReproducedText(doc, entry) && entry.requires.includes("notice") && (
        <p className="mt-2 text-xs text-[var(--color-bad)]">
          This licence requires its notice to be reproduced and no text shipped.
        </p>
      )}
    </li>
  );
}

function Group({
  group,
  doc,
  startOpen,
}: {
  group: { id: string; title: string; blurb: string; entries: CreditEntry[] };
  doc: CreditsDoc;
  startOpen: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(startOpen);
  // A search that narrows to three results should not leave them behind a
  // closed disclosure — reopen whenever the caller says the set changed.
  useEffect(() => setOpen(startOpen), [startOpen]);
  return (
    <section className="border-t border-[var(--color-line)] pt-3" data-credit-group={group.id}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="tap flex w-full items-center gap-2 text-left"
        aria-expanded={open}
      >
        <Icon name={open ? "arrow-down" : "arrow-right"} size={14} />
        <span className="panel-title">{group.title}</span>
        <span className="mono text-xs text-dim">{group.entries.length}</span>
      </button>
      <p className="mt-1 pl-6 text-xs text-dim">{group.blurb}</p>
      {open && (
        <ul className="mt-1 divide-y divide-[var(--color-line)] pl-6">
          {group.entries.map((e) => (
            <EntryRow key={`${group.id}:${e.name}`} entry={e} doc={doc} />
          ))}
        </ul>
      )}
    </section>
  );
}

export default function CreditsPanel({ data }: { data?: CreditsDoc }): JSX.Element {
  const [doc, setDoc] = useState<CreditsDoc | null>(data ?? null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (data) return;
    let alive = true;
    import("../../credits.generated.json")
      .then((m) => alive && setDoc((m.default ?? m) as unknown as CreditsDoc))
      .catch((e: unknown) =>
        alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [data]);

  const groups = useMemo(() => (doc ? filterGroups(doc, query) : []), [doc, query]);
  const flagged = useMemo(() => (doc ? flaggedEntries(doc) : []), [doc]);

  if (error) {
    return (
      <Panel title="Credits">
        <p className="text-sm text-[var(--color-bad)]">
          The credits data did not load: {error}
        </p>
        <p className="mt-2 text-xs text-dim">
          It is built into this app and needs no internet, so this means the
          build is incomplete rather than that the rig is offline.
        </p>
      </Panel>
    );
  }
  if (!doc) {
    return (
      <Panel title="Credits">
        <p className="text-sm text-dim">Loading the licence texts…</p>
      </Panel>
    );
  }

  const total = countEntries(doc);
  const shown = groups.reduce((n, g) => n + g.entries.length, 0);

  return (
    <Panel title="Credits">
      <p className="text-sm text-dim">
        {doc.project.name} {doc.project.version} is released under{" "}
        {doc.project.spdx} and is built on the work below —{" "}
        <span className="mono">{total}</span> components, with each licence
        reproduced in full rather than linked, so this page works with no
        internet.
      </p>
      <p className="mt-2 text-xs text-dim">{doc.scope}</p>

      {flagged.length > 0 && (
        <p className="mt-3 rounded border border-[var(--color-warn)] p-2 text-xs text-[var(--color-warn)]">
          <strong>
            {flagged.length} licence{flagged.length === 1 ? "" : "s"} need a
            decision from the project owner.
          </strong>{" "}
          They are listed first below. Nothing here is settled by this page
          being published.
        </p>
      )}

      <label className="field mt-3 block">
        <span className="label">Search</span>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="package, licence, or what it requires"
          className="w-full"
          aria-label="Search the credits"
        />
      </label>

      <p className="mt-2 text-xs text-dim" data-credit-count>
        {shown === total
          ? `Showing all ${total}`
          : `Showing ${shown} of ${total}`}
      </p>

      <div className="mt-3 flex flex-col gap-3">
        {groups.map((g) => (
          <Group
            key={g.id}
            group={g}
            doc={doc}
            // Collapsed by default, because 123 entries expanded is not a page
            // anybody reads. Two exceptions: the group holding owner decisions,
            // and any state where a search has already narrowed the list — at
            // which point hiding the matches behind a second click is just a
            // second click.
            startOpen={g.id === "flagged" || query.trim().length > 0}
          />
        ))}
        {groups.length === 0 && (
          <p className="text-sm text-dim">Nothing matches “{query}”.</p>
        )}
      </div>
    </Panel>
  );
}
