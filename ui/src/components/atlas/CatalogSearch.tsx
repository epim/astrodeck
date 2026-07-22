// CatalogSearch — the Atlas's inline target search (wave-2 §2). Same idiom as
// Plan's search (SequenceView): 250 ms debounce, GET /api/catalog?q=, top 6.
// Self-contained: owns its query/results state and clears itself after a pick;
// the parent decides what "pick" means (setFraming vs openFraming).
//
// Zero-state (R2-ATL-01 minimal, ATLAS-01, r1 ATL-02): a non-empty query ALWAYS
// renders a dropdown now — "Searching…" while the debounced fetch is in
// flight, otherwise either the result list or an explicit "no matches" panel
// with a scope hint (planet-specific when the query looks like Sun/Moon/a
// planet — those aren't in the catalog, see catalogHint.ts — general
// otherwise). Previously an empty result set rendered nothing at all.

import { useEffect, useRef, useState, type JSX } from "react";
import { api } from "../../api";
import type { CatalogEntry } from "../../types";
import { catalogScopeHint } from "../../lib/catalogHint";

export function CatalogSearch({
  onPick,
  placeholder = "Search catalog — frame a target",
}: {
  onPick: (e: CatalogEntry) => void;
  placeholder?: string;
}): JSX.Element {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  const [searching, setSearching] = useState(false);
  // Dismissed by outside-click/Escape without clearing the query, so the
  // dropdown stays gone until the user edits the query again or picks a
  // result (which clears search directly). Mirrors ui.tsx's Tooltip.
  const [dismissed, setDismissed] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setDismissed(false);
  }, [search]);

  useEffect(() => {
    if (!search.trim()) { setResults([]); setSearching(false); return; }
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        setResults((await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(search)}`)).slice(0, 6));
      } catch {
        // transient search errors read the same as "no matches" below — the
        // zero-state still gives the user a next step instead of dead air.
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const pick = (e: CatalogEntry) => {
    onPick(e);
    setSearch("");
    setResults([]);
  };

  const showDropdown = search.trim().length > 0 && !dismissed;

  // Outside-pointerdown + Escape dismissal (ui.tsx Tooltip precedent) so the
  // dropdown doesn't float over the page forever once the user has looked
  // away without picking a result.
  useEffect(() => {
    if (!showDropdown) return;
    const onDoc = (e: Event) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setDismissed(true);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDismissed(true); };
    document.addEventListener("pointerdown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [showDropdown]);

  return (
    <div className="relative" ref={rootRef}
      onBlur={(e) => {
        // Only dismiss when focus genuinely moved to an element OUTSIDE this
        // widget. A null relatedTarget is focus going nowhere focusable — an
        // Android soft-keyboard hide, or a tap on the non-focusable canvas —
        // which is NOT a real focus-out, so we keep the dropdown (otherwise the
        // first query's results land into a latched-dismissed state; UX-06).
        // Genuine dismissal still comes from outside pointerdown / Escape (the
        // effect above) and picking a result (which clears the query).
        const to = e.relatedTarget as Node | null;
        if (to && !e.currentTarget.contains(to)) setDismissed(true);
      }}>
      <input
        className="field btn-touch !w-56"
        placeholder={placeholder}
        aria-label="Search the target catalog"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {showDropdown && (
        <div className="absolute left-0 top-full mt-1 w-72 panel z-20 max-h-60 overflow-y-auto">
          {searching ? (
            <p className="px-3 py-2 text-xs text-dim">Searching…</p>
          ) : results.length > 0 ? (
            results.map((r) => (
              <button key={r.id} type="button" onClick={() => pick(r)}
                className="w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex justify-between cursor-pointer">
                <span><span className="mono text-accent">{r.id}</span> {r.name}</span>
                <span className={`mono ${r.alt > 40 ? "text-good" : r.alt < 20 ? "text-warn" : "text-dim"}`}>
                  {r.alt.toFixed(0)}°
                </span>
              </button>
            ))
          ) : (
            <div className="px-3 py-2 text-xs flex flex-col gap-1">
              <p className="text-ink">No matches for &quot;{search.trim()}&quot;.</p>
              <p className="text-dim">{catalogScopeHint(search)}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default CatalogSearch;
