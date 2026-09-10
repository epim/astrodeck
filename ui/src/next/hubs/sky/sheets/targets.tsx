// targets.tsx - SUGGESTED TARGETS, ranked by tonight's score
// (hub-sky plan A.10, design README section 2, screenshot 03).
//
// THE LIST IS AN ARGUMENT, not an index. Every column on a row is a reason to
// pick it or not: how high it is now, how long it stays up, what the sky over it
// is doing, how far the Moon is, how hard it is, and which filters it wants. A
// row that showed only a name would make the user open eleven briefs to choose.
//
// WHAT IT WILL NOT DO:
//
//  * It does not invent a status. A cloud reading that does not exist renders as
//    "UP · cloud -", never as CLEAR - `decorate()` owns that and is shared with
//    the finder, so the sheet and the marker under the reticle cannot disagree.
//  * IT NOW SHOWS SATELLITES AND COMETS (D-SKY-1), in their own section under
//    the ranked list rather than inside it, and that split is the honest one:
//    the ranked list is sorted by a REACH SCORE - minutes above the floor
//    between here and dawn - and neither kind can be scored on it. A satellite
//    is over the horizon for five minutes and is not a marker at all
//    (`finder/targets.ts SATELLITE_MARKERS` gives both reasons); a comet the
//    server could not place topocentrically has no horizon position to score.
//    Sorting either into a score they cannot be scored on would be the finder
//    ranking a guess.
//  * It does not re-word a server note. The withheld sentence, the
//    no-elements sentence and the stale sentence all reach the reader
//    verbatim - each one carries a number or a place to go that a paraphrase
//    would drop.
//  * It does not render an empty list as "nothing is up". A role without
//    `view.site_derived` cannot be ranked at all, and that is what the line
//    under the search field says.
//
// THE SEARCH FIELD is `components/atlas/CatalogSearch` mounted as it is. Its
// placeholder ("Search catalog - e.g. M 31") is load-bearing, but the dash in
// it is not: what a beginner copies out of the example is the spaced
// designation "M 31", and that is the token the server actually matches -
// squash_designation (server/astrodeck/catalog/objects.py:185-197) lowercases
// and drops every separator before comparing, and
// server/tests/test_catalog.py:24 pins "M 31" finding M31. The placeholder's
// punctuation never reaches the wire. Re-typing the widget here would lose
// the search behaviour itself, though: its zero-result copy, its outside-tap
// dismissal and its stale-response guard.

import { useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { Card, EmptyCard, Label, Mono, Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { CatalogSearch } from "../../../../components/atlas/CatalogSearch";
import { useCapability } from "../../../../lib/caps";
import { difficultyGlyph, difficultyLabel } from "../../../../lib/difficulty";
import { moonSepGlyph } from "../../../../lib/visibility";
import type { CatalogEntry, CometRow, DifficultyTier, SatelliteRow } from "../../../../types";
import { windowLabel } from "../../../lib/reach";
import {
  COMET_GEOCENTRIC_NOTE,
  KIND_ICON,
  useEphemerisRows,
  useSatellitePasses,
  type SkyKind,
  type SkyTarget,
} from "../finder";
import { LensDial } from "../cards/LensDial";
import { LENS_LEARN } from "../cards/lens";
import { PassesCard } from "../cards/PassesCard";
import { useStore } from "../../../../store";
import { TARGETS_EMPTY, TARGETS_FOOTER } from "./quickCopy";
import {
  RANK_DEFAULT_SITE, RANK_NEEDS_SITE, RANK_NOT_EMPTY, RANK_STILL_WORKING, SLOW_AFTER_S,
  useTargetsModel,
} from "./targetsModel";

/** The header over the ephemeris section. It says what the section is NOT
 *  ranked by, because everything above it is. */
const EPHEMERIS_TITLE = "SATELLITES AND COMETS";
const EPHEMERIS_SUB = "not ranked - a pass is minutes, a comet moves";

/** The link the server's own stale sentence sends people to. It is rendered
 *  only when a note actually asks for a refresh, so it never appears as a
 *  standing advertisement for a settings screen. */
const SKY_SETTINGS_LABEL = "SKY SETTINGS >";

/** Does this note ask the reader to go and refresh something? Both stale notes
 *  end "Refresh them from Sky settings when the rig is online."
 *  (`elements.py:132-143`) and the two no-elements notes ask for the same
 *  thing, so the test is on the VERB the server used, not on a whole sentence
 *  that a future edit would reword. */
function asksForRefresh(note: string): boolean {
  return /refresh/i.test(note);
}

/** The three tiers `lib/difficulty.ts` knows. The server also sends "unknown"
 *  for an object nobody published a magnitude for, and that is NOT a fourth
 *  difficulty - it is the absence of one, so no glyph is drawn rather than a
 *  confident circle standing for a number nobody measured. */
function knownTier(t: string | undefined): DifficultyTier | null {
  return t === "easy" || t === "moderate" || t === "hard" ? t : null;
}

/** Moon-separation hints, verbatim from `VisibilityPanel`'s own legend. The
 *  GLYPH is the carrier, not the tone: the night palette collapses good/warn/bad
 *  toward coral and a colour-only signal would vanish under it. */
function moonHint(sep: number): string {
  if (sep < 15) return "Very close to the moon - heavy gradient/glow.";
  if (sep < 30) return "Near the moon - expect some gradient.";
  return "Comfortable separation from the moon.";
}

export function TargetsSheet(_p: SheetProps): JSX.Element {
  const model = useTargetsModel();
  const enqueueToast = useStore((s) => s.enqueueToast);
  const [lensOpen, setLensOpen] = useState(false);

  // The ephemeris rows are fetched HERE and not by `useTargetsModel`, which is
  // another task's file: the two calls are the same two `/api/catalog` searches
  // the finder makes, on the same 2-minute cadence, and nothing in them needs
  // the ranking machinery the model exists for.
  const eph = useEphemerisRows(true);
  // The WHOLE passes route is `view.site_derived` (`ephemeris/routes.py:78`), so
  // a principal without it fires nothing rather than collecting a 403.
  const passesAllowed = useCapability("view.site_derived");
  const [pickedSat, setPickedSat] = useState<SatelliteRow | null>(null);
  const passes = useSatellitePasses(pickedSat?.norad_id ?? null, passesAllowed);

  /**
   * Aim the finder at a row and leave.
   *
   * The finder's state lives in the hub, which is a DIFFERENT component from
   * this sheet - a sheet is route state, not a child of the screen under it. So
   * the id travels in the hash and the hub picks it up, which also makes the
   * choice a deep link support can read out over the phone.
   */
  const aim = (id: string): void => {
    nav.go(`/sky?lock=${encodeURIComponent(id)}`);
  };

  const onPick = (e: CatalogEntry): void => aim(e.id);

  const sub = [
    model.clearPct == null ? "clear -" : `clear ${model.clearPct}%`,
    model.darkLine,
    model.moonLine,
    model.siteName,
  ].filter((s): s is string => !!s).join(" · ");

  const hiddenKinds = (Object.keys(model.lens) as SkyKind[]).filter((k) => model.lens[k] === false);

  // `useTargetsModel` ranks the deep-sky sources and knows nothing about the
  // ephemeris, so its per-kind counts would read 0 next to a section with rows
  // in it. The two ephemeris counts come from the rows this sheet actually
  // drew, and `LENS_COUNT_NOUN` on the dial says they are "listed" rather than
  // "in reach" - see `cards/lens.ts`.
  const kindCounts = {
    ...model.kindCounts,
    satellite: eph.satellites.length,
    comet: eph.comets.length,
  };
  const showSatellites = model.lens.satellite !== false;
  const showComets = model.lens.comet !== false;
  const ephemerisNotes = [
    ...(showSatellites ? eph.satelliteNotes : []),
    ...(showComets ? eph.cometNotes : []),
  ];
  const hasEphemeris =
    (showSatellites && eph.satellites.length > 0) ||
    (showComets && eph.comets.length > 0) ||
    ephemerisNotes.length > 0;

  return (
    <Sheet
      data-testid="sky-targets"
      title={`${model.reachCount} SUGGESTED TARGETS`}
      sub={sub}
      icon={<NxIcon name="star" size={18} />}
      backLabel="SKY"
      onBack={() => nav.back()}
      right={
        <button
          type="button"
          data-testid="targets-funnel"
          aria-label="filter which kinds of target are shown"
          onClick={() => setLensOpen(true)}
          style={{
            position: "relative", width: 40, height: 40, borderRadius: "50%",
            border: hiddenKinds.length > 0
              ? "1px solid rgba(255,180,84,.6)"
              : "1px solid rgba(140,160,220,.35)",
            background: "var(--bg, #06070B)",
            color: hiddenKinds.length > 0 ? "var(--warn, #ffb454)" : "var(--text, #e8ecf7)",
            display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
          }}
        >
          <NxIcon name="funnel" size={16} />
          {hiddenKinds.length > 0 && (
            <span
              className="nx-mono"
              style={{
                position: "absolute", right: -4, top: -4, minWidth: 18, height: 18,
                padding: "0 5px", borderRadius: 9, background: "var(--warn, #ffb454)",
                color: "#06070B", fontSize: 10, display: "flex", alignItems: "center",
                justifyContent: "center", boxSizing: "border-box",
              }}
            >
              {hiddenKinds.length}
            </span>
          )}
        </button>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {/* ---------------------------------------------------- search (GAP 6) */}
        <CatalogSearch onPick={onPick} className="w-full" />

        {!model.rankingAllowed && (
          <p data-testid="targets-needs-site" style={{ fontSize: 11.5, color: "var(--text-3, #7683a5)" }}>
            {RANK_NEEDS_SITE}
          </p>
        )}

        {model.rankingAllowed && model.siteIsDefault && (
          <p style={{ fontSize: 11.5, color: "var(--warn, #ffb454)" }}>{RANK_DEFAULT_SITE}</p>
        )}

        {/* ------------------------------------------------- the bounded wait */}
        {model.rankingAllowed && model.loading && (
          <p role="status" data-testid="targets-loading"
            style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11.5, color: "var(--text-3, #7683a5)" }}>
            <span>
              {"Ranking tonight… "}
              {model.waited >= SLOW_AFTER_S && (
                <span className="nx-mono" aria-hidden="true">{`${model.waited}s`}</span>
              )}
            </span>
            {model.waited >= SLOW_AFTER_S && <span>{RANK_STILL_WORKING}</span>}
          </p>
        )}

        {/* ----------------------------------------------------- the refusal */}
        {model.rankingAllowed && !model.loading && model.error != null && (
          <div role="alert" data-testid="targets-error"
            style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 8 }}>
            <p style={{ fontSize: 11.5, color: "var(--warn, #ffb454)" }}>
              {model.error}{" "}
              <span style={{ color: "var(--text-3, #7683a5)" }}>{RANK_NOT_EMPTY}</span>
            </p>
            <button type="button" className="nx-chip" onClick={model.retry}>Try again</button>
          </div>
        )}

        {/* -------------------------------------------------------- the list */}
        {model.rankingAllowed && !model.loading && model.error == null && (
          model.rows.length === 0 ? (
            <EmptyCard title="NOTHING MATCHES THE LENS" hint={TARGETS_EMPTY} />
          ) : (
            <Card tone="default" padding={0}>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 14px 4px" }}>
                <Label size={10}>BY TONIGHT&apos;S SCORE</Label>
                <Mono size={10} tone="dim">alt · window · sky</Mono>
              </div>
              {model.rows.map((t) => (
                <TargetRow key={t.id} t={t} onAim={() => aim(t.id)} />
              ))}
            </Card>
          )
        )}

        {/* ------------------------------------------- satellites and comets */}
        {hasEphemeris && (
          <Card tone="default" padding={0} data-testid="targets-ephemeris">
            <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 14px 4px" }}>
              <Label size={10}>{EPHEMERIS_TITLE}</Label>
              <Mono size={10} tone="dim">{EPHEMERIS_SUB}</Mono>
            </div>

            {ephemerisNotes.map((n) => (
              <p
                key={n}
                data-testid="ephemeris-note"
                style={{
                  margin: 0, padding: "6px 14px",
                  fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)",
                }}
              >
                {n}
                {asksForRefresh(n) && (
                  <>
                    {" "}
                    <button
                      type="button"
                      data-testid="ephemeris-settings-link"
                      onClick={() => nav.sheet("skyPack")}
                      style={{
                        border: 0, background: "transparent", padding: 0,
                        color: "var(--accent, #00d2ff)", fontFamily: "inherit",
                        fontSize: 11.5, cursor: "pointer",
                      }}
                    >
                      {SKY_SETTINGS_LABEL}
                    </button>
                  </>
                )}
              </p>
            ))}

            {showSatellites && eph.satellites.map((s) => (
              <SatelliteRowView
                key={s.id}
                s={s}
                picked={pickedSat?.id === s.id}
                onPick={() => setPickedSat((cur) => (cur?.id === s.id ? null : s))}
              />
            ))}

            {showComets && eph.comets.map((c) => (
              <CometRowView key={c.id} c={c} onAim={() => aim(c.id)} />
            ))}
          </Card>
        )}

        {pickedSat && (
          <PassesCard
            name={pickedSat.id}
            passes={passes.passes}
            elements={passes.elements}
            notes={passes.notes}
            loading={passes.loading}
            error={passes.error}
            lockedReason={passesAllowed ? null : (eph.satelliteNotes[0] ?? null)}
            onExplain={(reason) => enqueueToast({ level: "info", title: reason })}
            onRefresh={passes.refresh}
          />
        )}

        <p style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}>
          {TARGETS_FOOTER}
        </p>
      </div>

      {lensOpen && (
        <LensDial
          kinds={Object.keys(model.lens) as SkyKind[]}
          lens={model.lens}
          counts={kindCounts}
          icons={KIND_ICON}
          reachCount={model.reachCount}
          floorOnly={model.floorOnly}
          onToggle={model.setLens}
          onFloorOnly={model.setFloorOnly}
          onLearn={(kind) => enqueueToast({ level: "info", title: kind.toUpperCase(), detail: LENS_LEARN[kind] })}
          // The centre button already IS this screen, so it closes the dial
          // rather than pushing a second copy of the list onto the stack.
          onTonight={() => setLensOpen(false)}
          onClose={() => setLensOpen(false)}
        />
      )}
    </Sheet>
  );
}

function TargetRow({ t, onAim }: { t: SkyTarget; onAim: () => void }): JSX.Element {
  const altPct = Math.round((Math.max(0, t.altNow) / 90) * 100);
  const tier = knownTier(t.difficulty);
  const sep = t.moonSepDeg;
  return (
    <div
      data-target-row={t.id}
      style={{ display: "flex", alignItems: "center", borderTop: "1px solid rgba(120,140,200,.1)" }}
    >
      <button
        type="button"
        data-testid="target-pick"
        onClick={onAim}
        style={{
          flex: 1, minWidth: 0, display: "grid",
          gridTemplateColumns: "minmax(0,1fr) auto", gap: "4px 10px", alignItems: "center",
          padding: "10px 0 10px 14px", minHeight: 56, background: "transparent", border: 0,
          color: "var(--text, #e8ecf7)", textAlign: "left", cursor: "pointer",
          opacity: t.obstructed ? 0.55 : 1,
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ color: t.color, flexShrink: 0, display: "flex" }}>
            <NxIcon name={KIND_ICON[t.kind]} size={14} />
          </span>
          <span className="nx-display" style={{ fontSize: 12, letterSpacing: ".1em", flexShrink: 0 }}>
            {t.name}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-2, #9aa6c2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {t.full}
          </span>
        </span>
        <span className="nx-mono" style={{ fontSize: 10, color: t.color, letterSpacing: ".06em", whiteSpace: "nowrap" }}>
          {t.statusTxt}
        </span>

        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ flex: 1, height: 5, borderRadius: 999, background: "rgba(120,140,200,.15)" }}>
            <span style={{ display: "block", height: "100%", width: `${altPct}%`, borderRadius: 999, background: t.color }} />
          </span>
          <Mono size={10} tone="dim">
            {`${Math.round(t.altNow)}° · ${windowLabel(t.windowMinutes)}`}
          </Mono>
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}>
          {sep != null && (
            <span
              className="nx-mono"
              data-moon-glyph={moonSepGlyph(sep)}
              title={moonHint(sep)}
              style={{ fontSize: 10, color: sep < 15 ? "var(--bad, #ff5470)" : sep < 30 ? "var(--warn, #ffb454)" : "var(--text-3, #7683a5)" }}
            >
              {`${moonSepGlyph(sep)} ${Math.round(sep)}°`}
            </span>
          )}
          {tier && (
            <span
              className="nx-mono"
              data-difficulty={tier}
              title={difficultyLabel(tier)}
              style={{ fontSize: 10, color: "var(--text-3, #7683a5)" }}
            >
              {difficultyGlyph(tier)}
            </span>
          )}
          <Mono size={10} tone="dim">{t.palette}</Mono>
        </span>
      </button>
      <button
        type="button"
        data-testid="target-info"
        aria-label={`About ${t.name}`}
        onClick={() => nav.sheet("brief", { id: t.id })}
        style={{
          width: 44, height: 56, border: 0, background: "transparent",
          color: "var(--text-3, #7683a5)", fontSize: 12, fontStyle: "italic",
          cursor: "pointer", flexShrink: 0,
        }}
      >
        i
      </button>
    </div>
  );
}

/**
 * One satellite. It is a DISCLOSURE, not an aim: tapping it opens the passes
 * card under the section rather than pointing the finder at it.
 *
 * A satellite's position is only true for the instant it was computed, and this
 * sheet reads it on a two-minute cache. Aiming the finder at a two-minute-old
 * ISS would point at a part of the sky it left several hundred kilometres ago,
 * and it would look exactly like a correct answer. `finder/targets.ts
 * SATELLITE_MARKERS` carries the full argument; when a per-second position
 * source exists, this row becomes an aim like every other and the passes card
 * moves to the lock card.
 */
function SatelliteRowView(
  { s, picked, onPick }: { s: SatelliteRow; picked: boolean; onPick: () => void },
): JSX.Element {
  return (
    <button
      type="button"
      data-testid="target-row-satellite"
      data-norad={s.norad_id}
      aria-expanded={picked}
      onClick={onPick}
      style={{
        width: "100%", display: "grid",
        gridTemplateColumns: "minmax(0,1fr) auto", gap: "4px 10px", alignItems: "center",
        padding: "10px 14px", minHeight: 56, border: 0,
        borderTop: "1px solid rgba(120,140,200,.1)",
        background: picked ? "color-mix(in srgb, var(--accent, #00d2ff) 8%, transparent)" : "transparent",
        color: "var(--text, #e8ecf7)", textAlign: "left", cursor: "pointer",
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span style={{ color: "var(--accent, #00d2ff)", flexShrink: 0, display: "flex" }}>
          <NxIcon name={KIND_ICON.satellite} size={14} />
        </span>
        <span className="nx-display" style={{ fontSize: 12, letterSpacing: ".1em", flexShrink: 0 }}>
          {s.id}
        </span>
        <span style={{ fontSize: 11.5, color: "var(--text-2, #9aa6c2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {s.name}
        </span>
      </span>
      <Mono size={10} tone="dim">{picked ? "passes below" : "passes ›"}</Mono>
    </button>
  );
}

/**
 * One comet. A comet IS a deep-sky-shaped target, so this row aims the finder
 * exactly as a galaxy row does - unless the server could not place it, in which
 * case there is no horizon position to aim at and the row says so instead of
 * moving the view somewhere arbitrary.
 */
function CometRowView({ c, onAim }: { c: CometRow; onAim: () => void }): JSX.Element {
  const placed = c.topocentric === true;
  const mag = typeof c.mag === "number" ? `mag ${c.mag.toFixed(1)}` : "mag unknown";
  return (
    <div
      data-testid="target-row-comet"
      style={{ display: "flex", flexDirection: "column", borderTop: "1px solid rgba(120,140,200,.1)" }}
    >
      <button
        type="button"
        data-testid="comet-pick"
        onClick={placed ? onAim : undefined}
        aria-disabled={placed ? undefined : "true"}
        style={{
          width: "100%", display: "grid",
          gridTemplateColumns: "minmax(0,1fr) auto", gap: "4px 10px", alignItems: "center",
          padding: "10px 14px", minHeight: 56, border: 0, background: "transparent",
          color: "var(--text, #e8ecf7)", textAlign: "left",
          cursor: placed ? "pointer" : "default", opacity: placed ? 1 : 0.75,
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ color: "var(--accent, #00d2ff)", flexShrink: 0, display: "flex" }}>
            <NxIcon name={KIND_ICON.comet} size={14} />
          </span>
          <span className="nx-display" style={{ fontSize: 12, letterSpacing: ".1em", flexShrink: 0 }}>
            {c.id}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-2, #9aa6c2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {c.name}
          </span>
        </span>
        <Mono size={10} tone="dim">
          {`${mag} · ${c.delta_au.toFixed(2)} au away`}
        </Mono>
      </button>
      {!placed && (
        <p
          data-testid="comet-geocentric"
          style={{ margin: 0, padding: "0 14px 10px", fontSize: 11, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}
        >
          {c.geocentric_reason ?? COMET_GEOCENTRIC_NOTE}
        </p>
      )}
    </div>
  );
}
