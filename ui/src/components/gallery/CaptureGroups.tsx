import { useMemo, useState } from "react";
import type { GalleryGeometryGroup } from "../../types";

export function geometryLabel(g: GalleryGeometryGroup): string {
  const size = g.width && g.height ? `${g.width} × ${g.height} px` : "size unknown";
  const bin = g.bin_x && g.bin_y ? `bin ${g.bin_x} × ${g.bin_y}` : "binning unknown";
  const exposure = g.exposure_s != null ? `${g.exposure_s} s` : "exposure unknown";
  return `${size} · ${bin} · ${exposure}`;
}

/** Aggregates come from the server's whole selection, never the loaded tiles. */
export default function CaptureGroups({ groups = [], incomplete = false }: {
  groups?: GalleryGeometryGroup[];
  incomplete?: boolean;
}): JSX.Element | null {
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(40);
  const mixed = useMemo(() => {
    const seen = new Set<string>();
    return groups.some((g) => {
      const key = JSON.stringify([g.target.trim().toLowerCase(), g.filter.trim().toLowerCase(), g.frame_type.trim().toLowerCase()]);
      if (seen.has(key)) return true;
      seen.add(key);
      return false;
    });
  }, [groups]);
  if (!groups.length) return null;
  return (
    <section aria-label="Saved capture groups" style={{ minWidth: 0, fontSize: 12 }}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        style={{ display: "flex", gap: 8, width: "100%", minHeight: 44, alignItems: "center", textAlign: "left", flexWrap: "wrap" }}>
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        <strong>Saved capture groups</strong>
        <span>{groups.length}{incomplete ? "+" : ""} groups{mixed ? " · Mixed capture settings" : ""}</span>
      </button>
      {mixed && <p style={{ margin: "4px 0 8px", color: "var(--warn)" }}>Some frames of the same target and filter have different sizes, binning, or exposures. Review the groups before calibrating and stacking.</p>}
      {incomplete && <p>Only part of the library is represented here. These counts are incomplete.</p>}
      {open && <>
        <p style={{ margin: "4px 0 10px" }}>Frames on disk in this library selection, including later pages.</p>
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {groups.slice(0, visible).map((g, i) => <li key={i} style={{ padding: "8px 0", borderTop: "1px solid var(--line)", overflowWrap: "anywhere" }}>
            <strong>{g.target || "Unknown target"} · {g.filter || "Filter unknown"} · {g.frame_type || "Type unknown"}</strong>
            <div>{g.count.toLocaleString()} frames · {geometryLabel(g)}</div>
          </li>)}
        </ul>
        {groups.length > visible && <button type="button" onClick={() => setVisible(visible + 40)} style={{ minHeight: 44 }}>Show more groups</button>}
      </>}
    </section>
  );
}
