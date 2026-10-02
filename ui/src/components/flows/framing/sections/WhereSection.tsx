// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WhereSection.tsx - the Target modal's WHERE section (#189 S4 item 1; spec
// 2026-09-23 flows mosaic, 2.4 WHERE).
//
// CatalogSearch, the same widget QuickFlow uses, fills the name and both
// coordinates at once: `to_plan` reads ra/dec and never the name (#190), so a
// pick that set only the name would frame the object and shoot wherever the
// old coordinates were. The coordinates are written in the TARGET node's own
// sexagesimal form (`raHms`, `decDms`), which the server's parser reads and
// an operator can check at a glance.
//
// FIT OBJECT zooms the sky to the object; SUGGEST GRID proposes columns and
// rows from its catalogued size (framingModel `suggestGrid`, ATL-05). The
// suggestion is only a hint, and the row says why once: the catalogue holds
// one size and no axes or position angle, so an elongated galaxy gets a grid
// sized for its long axis both ways.

import type { JSX } from "react";
import { HonestButton } from "../../../ui";
import { CatalogSearch } from "../../../atlas/CatalogSearch";
import type { CatalogEntry } from "../../../../types";

export const SUGGEST_IS_A_HINT =
  "a hint: the catalogue holds one size and no axes or angle, so the grid is sized for the long axis both ways";
export const NO_SIZE = "the catalogue gives no size for this object";
export const NO_OBJECT = "pick an object from the catalogue first";

export interface WhereSectionProps {
  name: string;
  ra: string;
  dec: string;
  /** SUGGEST GRID's proposal for the picked object, or null. */
  suggestion: { cols: number; rows: number } | null;
  /** Why SUGGEST GRID cannot act, or null. */
  suggestLock: string | null;
  /** Why FIT OBJECT cannot act, or null. */
  fitLock: string | null;
  onName: (text: string) => void;
  onRa: (text: string) => void;
  onDec: (text: string) => void;
  onPick: (e: CatalogEntry) => void;
  onFit: () => void;
  onSuggest: () => void;
  explain: (reason: string) => void;
}

export function WhereSection(p: WhereSectionProps): JSX.Element {
  return (
    <section className="tfs-section" aria-labelledby="tfs-where-h" data-testid="framing-where">
      <h3 id="tfs-where-h" className="tfs-section-h">WHERE</h3>
      <div className="tfs-row">
        <CatalogSearch className="w-full" onPick={p.onPick} />
      </div>
      <div className="tfs-row">
        <label className="tfs-label" htmlFor="tfs-name">NAME</label>
        <input id="tfs-name" className="field tfs-input" type="text" value={p.name}
          onChange={(e) => p.onName(e.target.value)} />
      </div>
      <div className="tfs-row">
        <label className="tfs-label" htmlFor="tfs-ra">RA</label>
        <input id="tfs-ra" className="field tfs-input tfs-mono" type="text" value={p.ra}
          placeholder="00h 42m 44s" spellCheck={false}
          onChange={(e) => p.onRa(e.target.value)} />
      </div>
      <div className="tfs-row">
        <label className="tfs-label" htmlFor="tfs-dec">DEC</label>
        <input id="tfs-dec" className="field tfs-input tfs-mono" type="text" value={p.dec}
          placeholder="+41 16 09" spellCheck={false}
          onChange={(e) => p.onDec(e.target.value)} />
      </div>
      <div className="tfs-row tfs-chips">
        <HonestButton className="tfs-btn" reason={p.fitLock} onClick={p.onFit} onExplain={p.explain}>
          FIT OBJECT
        </HonestButton>
        <HonestButton className="tfs-btn" reason={p.suggestLock} onClick={p.onSuggest} onExplain={p.explain}>
          {p.suggestion ? `SUGGEST GRID ${p.suggestion.cols}x${p.suggestion.rows}` : "SUGGEST GRID"}
        </HonestButton>
      </div>
      {p.suggestion && <div className="tfs-row tfs-note">{SUGGEST_IS_A_HINT}</div>}
    </section>
  );
}

export default WhereSection;
