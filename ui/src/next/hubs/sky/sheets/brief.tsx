// brief.tsx - the ⓘ card: what this object is, and what tonight does to it
// (hub-sky plan A.11).
//
// EVERYTHING HERE IS THE SERVER'S. Not one sentence about an object is written
// in this file. The catalogue ships positions, sizes and brightnesses and no
// prose at all, so the "what it is" line is the server's own composed
// `describe`, the sizes and magnitudes are its numbers, and where a value does
// not exist the ABSENCE is stated in the words the existing card already uses -
// "not published for this object" is a different claim from "not recorded", and
// both are different from a blank.
//
// TWO SHEETS IN ONE FILE, because they are one screen with two subjects:
//
//   ?id=<catalogId>   the object brief - the ⓘ on a suggested-targets row, and
//                     the lock card's info button.
//   ?info=<topic>     the hold-to-learn brief - "what does DITHER mean". The
//                     design's own card is a bottom sheet, not a toast: a toast
//                     is 2.8 s and these are paragraphs.
//
// THE ROW-SHAPE TRAP. For a solar-system row `id` is the LABEL ("Jupiter") and
// `name` is the whole describe SENTENCE, the opposite way round from a DSO. A
// title built from `name` prints a paragraph in the header, so the title comes
// from `displayName` and the sentence from `fullName` - the two functions in
// `finder/targets.ts` that are allowed to decide which field is which.

import { useEffect, useState, type JSX, type ReactNode } from "react";
import type { SheetProps } from "../../sheets";
import { Card, EmptyCard, Label, Mono, Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useSite, useStore } from "../../../../store";
import { useCapability } from "../../../../lib/caps";
import {
  fmtHoursAboveLimit, fmtMoonPhase, moonSepGlyph,
} from "../../../../lib/visibility";
import { difficultyHint, difficultyLabel } from "../../../../lib/difficulty";
import { effectiveOptics } from "../../../../lib/effective";
import { fovFromOptics } from "../../../../lib/framing";
import type { SkyRow } from "../../../../lib/skyRegion";
import type { DifficultyTier } from "../../../../types";
import { fmtClock } from "../../../lib/format";
import { displayName, fullName } from "../finder";
import { framingMatches } from "../frame/mosaic";
import { DSO_HONESTY, INFO } from "./quickCopy";
import { regionRowFor, useCatalogTarget } from "./targetsCatalog";
import { useVisibilityNight } from "./quickVisibility";

/** Arcminutes under a degree, degrees above - the Atlas's own convention. */
function fmtSize(arcmin: number): string {
  if (arcmin >= 60) return `${(arcmin / 60).toFixed(2)}° across`;
  if (arcmin >= 1) return `${arcmin.toFixed(1)}′ across`;
  return `${(arcmin * 60).toFixed(1)}″ across`;
}

/** How the object sits in THIS camera's frame. Same wording the Atlas prints
 *  under its canvas, so the two cannot describe one geometry two ways. */
function frameVerdict(sizeArcmin: number, frameFovDeg: number): string | null {
  if (!(frameFovDeg > 0) || !(sizeArcmin > 0)) return null;
  const frac = sizeArcmin / 60 / frameFovDeg;
  if (frac <= 1) return `Fills ${Math.round(frac * 100)}% of your frame`;
  return `${frac.toFixed(1)}× your frame - needs a mosaic`;
}

/**
 * The ephemeris age line for a body, with BOTH geocentric reasons kept apart.
 *
 * Only one of them is something the reader can act on. "no site is set" said to
 * a viewer on a fully configured rig sends them to fix a setting that is already
 * right - and they could not change it anyway, because it is their ROLE that
 * chose the site-free position.
 */
function ephemerisLine(row: {
  kind?: string;
  ephemeris_unix?: number;
  topocentric?: boolean;
  geocentric_reason?: "site_unset" | "not_permitted" | null;
}): string | null {
  if (row.kind !== "solar_system" || !row.ephemeris_unix) return null;
  const mins = Math.max(0, Date.now() / 1000 - row.ephemeris_unix) / 60;
  const when = mins < 1 ? "just now" : `${Math.round(mins)} min ago`;
  const where =
    row.geocentric_reason === "not_permitted"
      ? " Computed for the centre of the Earth: where a body appears from your actual "
        + "location would give away where this rig is, so your role sees the site-free "
        + "position. It is under 13″ out for a planet."
      : row.topocentric === false
        ? " Computed for the centre of the Earth, because no site is set - that moves the "
          + "Moon by up to a degree."
        : "";
  return `Position computed ${when}; it moves as the night does.${where}`;
}

function Row({ term, value, dim = false }: { term: string; value: ReactNode; dim?: boolean }): JSX.Element {
  return (
    <>
      <dt style={{ color: "var(--text-3, #7683a5)", fontSize: 11.5 }}>{term}</dt>
      <dd
        className={dim ? undefined : "nx-mono"}
        style={{ margin: 0, fontSize: 11.5, color: dim ? "var(--text-3, #7683a5)" : undefined }}
      >
        {value}
      </dd>
    </>
  );
}

export function BriefSheet({ params }: SheetProps): JSX.Element {
  const topic = params.info ?? "";
  if (topic) return <TopicBrief topic={topic} />;
  return <ObjectBrief id={params.id ?? ""} />;
}

/** The hold-to-learn half: one paragraph, no fetch, no numbers. */
function TopicBrief({ topic }: { topic: string }): JSX.Element {
  const t = INFO[topic];
  return (
    <Sheet
      data-testid="sky-brief"
      title={t ? t.t : "NO EXPLANATION FOR THAT"}
      sub={t ? "hold any label for its own explanation" : undefined}
      icon={<NxIcon name="info" size={18} />}
      backLabel="BACK"
      onBack={() => nav.back()}
    >
      {t ? (
        <p data-testid="brief-topic" style={{ fontSize: 13, lineHeight: 1.55 }}>{t.b}</p>
      ) : (
        <EmptyCard
          title="NOTHING TO EXPLAIN HERE"
          hint={`"${topic}" has no written explanation in this build. Press BACK; nothing else is affected.`}
        />
      )}
    </Sheet>
  );
}

function ObjectBrief({ id }: { id: string }): JSX.Element {
  const site = useSite();
  const config = useStore((s) => s.config);
  const status = useStore((s) => s.status);
  const rotator = useStore((s) => s.status?.rotator ?? null);
  // THE FRAMING SLICE IS GLOBAL - one session, shared with the Atlas - so the
  // angle is only this object's when the session is this object's. Reading it
  // unconditionally printed "sends PA 30°" on the brief for every OTHER target
  // in tonight's list while a framing for one of them was kept (review #3).
  const framing = useStore((s) => s.framing);
  const rotationDeg = framingMatches(framing, id) ? (framing?.rotation_deg ?? 0) : 0;
  const canSeeSiteDerived = useCapability("view.site_derived");

  const search = useCatalogTarget(id || null);
  const row = search.row;

  // The map's row for the same object: the ONLY source of `describe`,
  // `constellation` and `alias`. The search route has none of the three, so
  // rendering "not on file for this object" from it would report a gap in the
  // catalogue that is really a gap in the request.
  const [region, setRegion] = useState<SkyRow | null>(null);
  useEffect(() => {
    if (!row || row.kind === "solar_system") { setRegion(null); return; }
    let alive = true;
    void regionRowFor(row.ra_hours, row.dec_deg, row.id)
      .then((r) => { if (alive) setRegion(r); })
      .catch(() => { if (alive) setRegion(null); });
    return () => { alive = false; };
  }, [row]);

  const night = useVisibilityNight(
    row?.ra_hours ?? null,
    row?.dec_deg ?? null,
    site?.horizon_min_deg ?? 0,
  );

  const optics = effectiveOptics(config, config?.optics ?? null, status?.optics ?? config?.optics_computed ?? null);
  const fov = fovFromOptics(optics);
  const frameFovDeg = fov?.fov_x_deg ?? 0;

  if (!id) {
    return (
      <Sheet data-testid="sky-brief" title="NO TARGET" icon={<NxIcon name="info" size={18} />}
        backLabel="BACK" onBack={() => nav.back()}>
        <EmptyCard
          title="NO OBJECT IN THIS LINK"
          hint="The address carries no target id, so there is nothing to look up. Pick a row from the suggested list."
        />
      </Sheet>
    );
  }

  const title = row ? displayName(row) : id;
  const sentence = row
    ? (row.kind === "solar_system" ? fullName(row) : (region?.describe ?? ""))
    : "";
  const tier: DifficultyTier | null =
    row?.difficulty === "easy" || row?.difficulty === "moderate" || row?.difficulty === "hard"
      ? row.difficulty
      : null;

  const eph = row ? ephemerisLine(row) : null;
  const verdict = row ? frameVerdict(row.size_arcmin, frameFovDeg) : null;
  const moon = night?.moon ?? null;

  return (
    <Sheet
      data-testid="sky-brief"
      title={title}
      sub={row ? [row.type, region?.alias ? `also ${region.alias}` : null].filter(Boolean).join(" · ") : undefined}
      icon={<NxIcon name="info" size={18} />}
      backLabel="BACK"
      onBack={() => nav.back()}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {search.loading && (
          <p style={{ fontSize: 12, color: "var(--text-3, #7683a5)" }}>
            Looking this object up in the catalogue…
          </p>
        )}

        {/* A failed FETCH is not an empty RESULT. Flattening the two is how a
            dead link to the rig reads as a confident "no such object". */}
        {search.error != null && (
          <p role="alert" style={{ fontSize: 12, color: "var(--warn, #ffb454)" }}>
            {`Could not reach the catalogue - ${search.error}. That is a link problem, not an answer about this object.`}
          </p>
        )}

        {search.notes.map((n) => (
          <p key={n} style={{ fontSize: 12, color: "var(--text-3, #7683a5)" }}>{n}</p>
        ))}

        {!search.loading && search.error == null && row == null && (
          <EmptyCard
            title="NOT IN THIS CATALOGUE"
            hint={`Nothing in the offline catalogue answers to "${id}".`}
          />
        )}

        {row && (
          <>
            {sentence !== "" && (
              <p data-testid="brief-describe" style={{ fontSize: 13, lineHeight: 1.5 }}>{sentence}</p>
            )}

            <Card tone="default">
              <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 12, rowGap: 4, margin: 0 }}>
                <Row
                  term="Size"
                  value={row.size_arcmin > 0
                    ? fmtSize(row.size_arcmin)
                    : row.kind === "star" ? "a point at any focal length" : "not recorded"}
                  dim={!(row.size_arcmin > 0)}
                />
                <Row
                  term="Magnitude"
                  value={row.mag == null ? "not published for this object" : row.mag.toFixed(1)}
                  dim={row.mag == null}
                />
                <Row
                  term="Constellation"
                  value={region?.constellation ?? row.constellation ?? "not on file for this object"}
                  dim={(region?.constellation ?? row.constellation) == null}
                />
                {tier && <Row term="Difficulty" value={`${difficultyLabel(tier)} - ${difficultyHint(tier)}`} dim />}
              </dl>
            </Card>

            {verdict && <p style={{ fontSize: 12 }}>{verdict}</p>}
            {eph && <p style={{ fontSize: 12, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}>{eph}</p>}

            {/* ------------------------------------------------------ tonight */}
            <Label size={10}>TONIGHT</Label>
            {!canSeeSiteDerived ? (
              <p style={{ fontSize: 11.5, color: "var(--text-3, #7683a5)" }}>
                Transit, the dark window and the moon are worked out from where the rig stands, so
                they need site access. The catalogue facts above do not.
              </p>
            ) : night == null ? (
              <p style={{ fontSize: 11.5, color: "var(--text-3, #7683a5)" }}>
                The ephemeris for this object has not come back yet.
              </p>
            ) : (
              <Card tone="default">
                <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 12, rowGap: 4, margin: 0 }}>
                  <Row
                    term="Transit"
                    value={`${fmtClock(night.transit_unix * 1000, night.transit_unix * 1000)} at ${Math.round(night.transit_alt)}°`
                      + (night.transit_in_daylight ? " (in daylight)" : "")}
                  />
                  <Row
                    term="Dark"
                    value={night.dark_start_unix != null && night.dark_end_unix != null
                      ? `${fmtClock(night.dark_start_unix * 1000, night.dark_start_unix * 1000)}`
                        + `-${fmtClock(night.dark_end_unix * 1000, night.dark_end_unix * 1000)}`
                      : "no astronomical darkness tonight"}
                    dim={night.dark_start_unix == null}
                  />
                  <Row
                    term={`Above ${Math.round(night.alt_limit_deg)}°`}
                    value={night.never_rises_above_limit
                      ? "never, from this site tonight"
                      : fmtHoursAboveLimit(night)}
                    dim={night.never_rises_above_limit}
                  />
                  {night.best_window && (
                    <Row
                      term="Best window"
                      value={`${fmtClock(night.best_window.start_unix * 1000, night.best_window.start_unix * 1000)}`
                        + `-${fmtClock(night.best_window.end_unix * 1000, night.best_window.end_unix * 1000)}`
                        + ` · mean ${Math.round(night.best_window.mean_alt)}°`}
                    />
                  )}
                  {moon && (
                    <>
                      <Row term="Moon" value={fmtMoonPhase(moon.illumination, moon.phase_name)} />
                      <Row
                        term="Separation"
                        value={`${moonSepGlyph(moon.separation_deg)} ${Math.round(moon.separation_deg)}°`}
                      />
                    </>
                  )}
                </dl>
              </Card>
            )}

            {/* ----------------------------------------- position-angle honesty */}
            {rotationDeg > 0.5 && (
              <p data-testid="brief-pa" style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}>
                {rotator
                  ? `Every slew the generated flow makes sends PA ${Math.round(rotationDeg)}° to `
                    + `${rotator.name ?? "the rotator"}, which rotates before it centres. `
                    + "A Go to from Rig - Mount does not: that one leaves the camera where it is."
                  : `Camera angle is manual - set your camera to PA ${Math.round(rotationDeg)}° before the run; `
                    + "there is no rotator in the rig."}
              </p>
            )}

            {row.kind !== "solar_system" && (
              <p style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}>
                {DSO_HONESTY}
              </p>
            )}

            <Mono size={10} tone="dim">
              {`RA ${row.ra_hours.toFixed(4)} h · Dec ${row.dec_deg.toFixed(3)}°`}
            </Mono>
          </>
        )}
      </div>
    </Sheet>
  );
}
