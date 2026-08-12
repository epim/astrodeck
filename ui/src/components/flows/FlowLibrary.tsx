// FlowLibrary.tsx — the library screen: heading, sub-paragraph, toolbar
// (search + three folder chips), and one section per folder.
//
// Two things here are worth knowing before changing anything.
//
// FOLDERS COME FROM THE SERVER, IN THE SERVER'S ORDER. `FlowStore.folders()`
// already seeds "My flows" and "Examples" at count 0 and ranks them My-flows /
// user-made / Examples, so this file does not sort and does not hardcode a
// two-folder world — folder CRUD exists on the API and a third folder must land
// somewhere sensible without an edit here.
//
// THE CARDS ARE A PROJECTION, NOT GRAPHS. `GET /api/flows` returns FlowCard,
// which is why `stages`/`wires` are read off the card rather than counted.
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { FlowCard } from "../../lib/flowsApi";
import { useStore } from "../../store";
import { EmptyState } from "../ui";
import { Icon } from "../icons";
import { FlowLibraryCard, NewFlowCard } from "./FlowLibraryCard";

/** Mirrors `MY_FLOWS_FOLDER` in server/astrodeck/flows/models.py:35. The dashed
 *  card leads THIS grid because it is where a wizard/blank creation is saved
 *  (wizard.py:358) — offering "new flow" at the head of a read-only folder
 *  would promise something the server refuses. */
const MY_FLOWS_FOLDER = "My flows";

const CHIPS = [
  { key: "all", label: "All" },
  { key: "mine", label: "My flows" },
  { key: "examples", label: "Examples" },
] as const;

/* §G-9: neither glyph exists in icons.tsx (42 names, verified). Both are
   transcribed from the prototype rather than added to the shared icon set,
   which is not this file's to edit. `currentColor` so they inherit the token
   colour of the wrapper instead of carrying a hex. */
function FolderGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
         className="translate-y-px" aria-hidden="true" focusable="false">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
    </svg>
  );
}

function SearchGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={2} strokeLinecap="round" aria-hidden="true" focusable="false">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.8-3.8" />
    </svg>
  );
}

interface Section {
  name: string;
  cards: FlowCard[];
  hasNew: boolean;
}

// Exported BOTH ways (see the `export default` at the foot). `components/` is
// split down the middle on this — BottomNav.tsx is a default export, Gated.tsx
// is named — and FlowsView.tsx is another author's file, so the import it
// happens to write should not be a build break.
export function FlowLibrary() {
  // Narrow selectors, one field each: a keystroke in the filter box must not
  // re-render anything that does not read `query`.
  const cards = useStore((s) => s.flows.cards);
  const folders = useStore((s) => s.flows.folders);
  const libraryError = useStore((s) => s.flows.libraryError);
  const query = useStore((s) => s.flows.ui.query);
  const folderChip = useStore((s) => s.flows.ui.folderChip);
  const flowsLoadLibrary = useStore((s) => s.flowsLoadLibrary);
  const flowsSetUi = useStore((s) => s.flowsSetUi);
  const flowsOpen = useStore((s) => s.flowsOpen);

  // Fetch once per mount. Guarded by a ref, not by `libraryLoaded`: the slice
  // leaves that flag FALSE on a failed load (so an unreachable library never
  // renders as an empty one), and a flag-guarded effect would then retry on
  // every render. Recovery is the explicit RETRY below.
  const asked = useRef(false);
  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    void flowsLoadLibrary();
  }, [flowsLoadLibrary]);

  // Stable identity, or FlowLibraryCard's React.memo buys nothing: a fresh
  // arrow per render is a changed prop, and every card re-renders on every
  // keystroke in the filter box.
  const openFlow = useCallback((id: string) => { void flowsOpen(id); }, [flowsOpen]);
  const openWizard = useCallback(() => flowsSetUi({ wizardOpen: true }), [flowsSetUi]);

  const sections = useMemo<Section[]>(() => {
    // Verbatim from the prototype (dc.html:1513): substring over name + tagline,
    // case-insensitive, trimmed.
    const q = query.trim().toLowerCase();
    const hit = (c: FlowCard) => !q || `${c.name} ${c.tagline}`.toLowerCase().includes(q);
    const visible = folders.filter((f) =>
      folderChip === "all" ? true : folderChip === "examples" ? f.readonly : !f.readonly);
    return visible.map((f) => ({
      name: f.name,
      cards: cards.filter((c) => c.folder === f.name && hit(c)),
      hasNew: f.name === MY_FLOWS_FOLDER && !q,
    }));
  }, [cards, folders, folderChip, query]);

  const noMatch = query.trim().length > 0 && sections.every((s) => s.cards.length === 0);

  return (
    <div
      data-flows-tab="library"
      data-screen-label="Flow library"
      className="fill-grow overflow-y-auto"
      style={{
        backgroundImage:
          "linear-gradient(rgba(6,7,11,.82),rgba(6,7,11,.94)),url('/bg_nebula.png')",
        backgroundSize: "cover",
        backgroundPosition: "center",
      }}
    >
      <div className="max-w-[1020px] mx-auto px-5 pt-[38px] pb-[60px]">
        <h1 className="font-display font-semibold text-[22px] tracking-[0.14em]">FLOWS</h1>
        <p className="mt-1.5 text-[13px] text-dim max-w-[560px] [text-wrap:pretty]">
          Visual automation for the rig. Wire targets, windows and sensors into capture
          stages and rules — a flow compiles to a sequence plan plus when/then
          instructions and runs on the engine, fail-closed.
        </p>

        <div className="mt-[22px] flex flex-wrap items-center gap-2.5">
          <div className="flex-1 min-w-[200px] max-w-[340px] relative">
            <span className="absolute left-[10px] top-1/2 -translate-y-1/2 pointer-events-none text-faint">
              <SearchGlyph />
            </span>
            <input
              type="text"
              value={query}
              onChange={(e) => flowsSetUi({ query: e.target.value })}
              placeholder="Filter flows…"
              aria-label="Filter flows"
              // `!text-[12px]`: `.field` sets font-size 13px from an UNLAYERED
              // rule, and an unlayered declaration beats any Tailwind utility
              // regardless of order — the same cascade trap that once ate a
              // tooltip's z-index. The `!` is what makes the design's 12px stick.
              className="field pl-[30px] min-h-[40px] rounded-[10px] !text-[12px]"
            />
          </div>
          <div className="flex flex-wrap gap-[7px]">
            {CHIPS.map((c) => {
              const on = folderChip === c.key;
              return (
                <button
                  key={c.key}
                  type="button"
                  aria-pressed={on}
                  onClick={() => flowsSetUi({ folderChip: c.key })}
                  className={`min-h-[40px] px-3.5 py-1.5 rounded-full border font-mono
                              text-[11px] cursor-pointer transition-colors ${
                    on ? "border-accent text-accent bg-accent-fill"
                       : "border-line2 text-dim bg-transparent"}`}
                >
                  {c.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* A library the client could not reach must never render as an empty
            one — that reads as data loss. Glyph + word, not colour alone. */}
        {libraryError && (
          <div role="status"
               className="mt-6 flex flex-wrap items-center gap-2 font-mono text-[11px] text-bad">
            <Icon name="alert" size={14} />
            <span>Could not read the flow library: {libraryError}</span>
            <button type="button" className="btn !text-[10.5px] !px-2.5 !py-[7px]"
                    onClick={() => void flowsLoadLibrary()}>
              RETRY
            </button>
          </div>
        )}

        {noMatch && (
          <div className="mt-10 flex flex-col items-center gap-2 text-center">
            {/* Static, no pulse — a pulse on an empty state reads as loading.
                `.empty-state-inline` sets a size and a colour but no family, so
                README §2's "quiet centered MONO line" needs the family supplied
                by the wrapper, where it inherits in. */}
            <div className="font-mono">
              <EmptyState size="inline" icon="grid" title={`No flows match “${query}”`} />
            </div>
            <p className="text-[11px] text-faint">
              Try a target name, filter, or technique — or clear the search.
            </p>
          </div>
        )}

        {sections.map((sec) => (
          <section key={sec.name} className="mt-[26px]">
            <div className="flex items-baseline gap-2 mb-3">
              <span className="text-faint"><FolderGlyph /></span>
              <h2 className="font-display font-semibold text-[10px] tracking-[0.22em] text-dim uppercase">
                {sec.name}
              </h2>
              {/* The count of what is UNDER this heading, not the folder's total:
                  with a filter active a server count would disagree with the
                  cards the operator can see. */}
              <span className="font-mono text-[10px] text-faint">{sec.cards.length}</span>
            </div>
            <div
              className="grid gap-[14px]"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(232px, 1fr))" }}
            >
              {sec.hasNew && <NewFlowCard onClick={openWizard} />}
              {sec.cards.map((c) => (
                <FlowLibraryCard key={c.id} card={c} onOpen={openFlow} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

export default FlowLibrary;
