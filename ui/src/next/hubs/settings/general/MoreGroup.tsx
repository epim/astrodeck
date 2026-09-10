// MoreGroup.tsx - Settings > GENERAL > MORE (plan section C.6): the rows into
// the tuning and admin sheets, and nothing else. Every sheet body is a REUSED
// panel and belongs to another task (T-SET-4 for ten of them, T-WX-1 for
// `weatherSettings` and `cloudmap`); this file owns only the doors.
//
// REACHABLE AT EVERY BREAKPOINT. `ARCHITECTURE.md` section 0.1's "tuning
// editors at tablet/desktop" means never DROPPED, not phone-hidden - a phone
// reaches each of them through the sheet host exactly as a desktop reaches it
// through the right-hand panel.
//
// READ-ONLY RENDERING, NOT HIDING. A row whose sheet the caller cannot write is
// still a row, still pressable, and says what it would need: the panels inside
// carry their own `accessPhrase()` sentences, so this file adds ONE clause to
// the row's sub-line and never a second lock note on top of the panel's.
//
// FACTORY RESET is last, behind a divider and a DANGER ZONE label - findable,
// never brushed against. Its three interlocks (the capability, the typed word
// RESET, the hold-confirm) all live inside `FactoryResetPanel` and none of them
// is duplicated or relaxed here.

import type { JSX, ReactNode } from "react";
import { Card, Divider, Label, ListRow, Mono } from "../../../ui";
import { NxIcon, type NxIconName } from "../../../icons";
import { nav } from "../../../router";
import { accessPhrase } from "../../../../lib/caps";
import { useCaps } from "../../../../store";
import type { Capability } from "../../../../types";
import { NamingGlyph } from "./glyphs";
import { Group } from "./Group";

interface MoreRow {
  sheet: string;
  title: string;
  sub: string;
  icon: NxIconName | "naming";
  /** What the sheet's controls need. The row is never hidden; when the caller
   *  lacks it, the sub-line says so and the sheet renders read-only. */
  cap?: Capability;
}

const ROWS: MoreRow[] = [
  { sheet: "safetyTuning", title: "SAFETY", icon: "safety", cap: "config.safety",
    sub: "sun avoidance, limits, what happens when a limit trips" },
  { sheet: "standards", title: "IMAGING STANDARDS", icon: "gauge", cap: "config.safety",
    sub: "the grades a frame has to meet before it counts" },
  // `control.capture`, not `config.site_optics` (review #78): the panel's only
  // write gate is `CalibrationLibraryPanel.tsx:26 useCanControlCapture()`, so
  // the row used to tell an operator they needed admin access and then hand
  // them a live REBUILD button. A lock reason that is a guess teaches the user
  // to stop reading them.
  { sheet: "calibration", title: "CALIBRATION", icon: "layers", cap: "control.capture",
    sub: "the master library and how closely a master has to match" },
  { sheet: "naming", title: "FILE NAMING", icon: "naming", cap: "config.site_optics",
    sub: "the folder and filename every frame is written under" },
  { sheet: "wcs", title: "PLATE-SOLVE STAMP", icon: "star", cap: "config.site_optics",
    sub: "whether a solved frame carries its WCS into the file" },
  { sheet: "sync", title: "FILE SYNC", icon: "share", cap: "config.site_optics",
    sub: "where finished frames are pushed, and what is waiting" },
  { sheet: "skyPack", title: "SKY ATLAS OFFLINE PACK", icon: "sky", cap: "config.site_optics",
    sub: "survey tiles kept on the rig, for a dark site with no internet" },
  { sheet: "weatherSettings", title: "WEATHER", icon: "weather", cap: "config.site_optics",
    sub: "the forecast, the cloud threshold that holds a run, the seeing feed" },
  { sheet: "cloudmap", title: "CLOUD MAP", icon: "satellite", cap: "config.site_optics",
    sub: "the satellite cloud model - advisory only, nothing reads it" },
  { sheet: "restricted", title: "RESTRICTED ASSETS", icon: "lock", cap: "config.backend",
    sub: "imagery whose licence this rig has to acknowledge before serving it" },
  { sheet: "logExport", title: "LOG EXPORT", icon: "download",
    sub: "the night's log and the flow JSON, as one bundle for support" },
];

function glyph(icon: MoreRow["icon"]): ReactNode {
  return icon === "naming" ? <NamingGlyph /> : <NxIcon name={icon} />;
}

export function MoreGroup(): JSX.Element {
  const caps = useCaps();
  const holds = (c?: Capability): boolean => !c || caps.includes(c);

  return (
    <>
      <Group label="MORE" testId="group-more">
        {ROWS.map((r) => (
          <ListRow
            key={r.sheet}
            icon={glyph(r.icon)}
            title={r.title}
            sub={holds(r.cap) ? r.sub : `${r.sub} · needs ${accessPhrase(r.cap!)}`}
            chevron
            onPress={() => nav.sheet(r.sheet)}
            data-testid={`row-more-${r.sheet}`}
          />
        ))}
        <ListRow
          icon={<NxIcon name="back" />}
          title="OPEN THE CLASSIC UI"
          sub="the previous interface, unchanged. Everything here works there too."
          right={<Mono>OPEN</Mono>}
          onPress={() => { window.location.hash = "#/classic"; }}
          data-testid="row-classic"
        />
      </Group>

      <Divider />

      <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
        <Label>DANGER ZONE</Label>
        <Card padding={0}>
          <ListRow
            icon={<NxIcon name="refresh" />}
            title="FACTORY RESET"
            sub={
              holds("admin.users")
                ? "erases the rig's configuration and starts it over. It says what it will delete first."
                : `erases the rig's configuration · needs ${accessPhrase("admin.users")}`
            }
            chevron
            tone="bad"
            onPress={() => nav.sheet("factoryReset")}
            data-testid="row-more-factoryReset"
          />
        </Card>
      </div>
    </>
  );
}
