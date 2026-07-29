# Equipment & profiles

This page connects real gear. AstroDeck separates **drivers** (how to reach a
backend) from **assignments** (which driver runs each device role) and lets you
save the whole arrangement as a **profile**. Connecting equipment, applying a
profile, and managing profiles all need `config.backend`, which by default
only an **admin** account holds — operator does not, and the in-app lock
notes say so (*"Read-only — connecting equipment needs admin access."*). See
[remote-access-and-roles.md](remote-access-and-roles.md).

**Jargon:** a **role** is a device slot — `Camera`, `Mount`, `Focuser`,
`Guider` (the PHD2/NINA guiding connection), `Guide camera` (a dedicated
Alpaca/native camera for the native guider — see [Guiding](guiding.md)),
`Filter wheel`, `Power / switch`, `Safety monitor`, `Rotator`. A **driver** is
a way to reach a backend: a **NINA** instance, an **Alpaca** server, or
**PHD2** (plus the built-in Simulator, AstroDeck native, and ASTAP).

---

## Step 1 — Declare your drivers

Go to **Settings → Connect → Backend Drivers**. You declare each backend once, globally,
then reference it from device slots. In the **Add driver** form:

- **Type** — **NINA**, **Alpaca server**, or **PHD2**.
- **Host** — the machine's address (e.g. `192.168.1.50`, or `astrotown.lan`).
- **Port** — defaults per type: **NINA 1888**, **Alpaca 11111**, **PHD2 4400**.
  Leave blank to use the default.
- **Label (optional)** — a friendly name.

Press **Add**. For NINA and Alpaca you can **⟳ Scan network** to auto-discover
instances instead of typing the host.

Each configured driver row has an enable **toggle**, a **Probe** button (force a
re-check of what it offers), and **Delete**. A **Built-in** section lists the
implicit drivers — **Simulator**, **AstroDeck native**, **ASTAP** — which are
detected, not configured. Each row summarises what it currently offers (e.g.
*"camera: ASI2600MM · telescope: EQ6-R · tasks: autofocus"*).

---

## Step 2 — Assign devices to roles

On the **Equipment** view, the **Devices** panel has one row per role. For each
role, pick which driver runs it from the dropdown. **The one rule:** only drivers
that are enabled, reachable, and actually offer that device appear in the list.
When a driver offers several devices for a role (common with Alpaca), a second
dropdown picks the specific device.

Each slot shows its state: `UNASSIGNED`, `ASSIGNED`, `DRIVER REMOVED`, `DRIVER
UNREACHABLE`, `DRIVER DISABLED`, or `DEVICE MISSING` — so a broken assignment is
obvious.

---

## Step 3 — Connect

In the **Rig Actions** panel:

- **Connect Rig (N)** — connect all N assigned devices. If the rig includes a
  **real** mount, focuser, or rotator, you'll get a hold-to-confirm ("Connect
  this rig?") first — a safeguard against unexpected motion.
- **▶ Simulator rig** — assigns *every* device role to the built-in simulator
  and connects it in one step, then runs through the exact same connect path
  as **Connect Rig**. That's deliberate: the **Devices** panel's assignments,
  the **Link Status** grid, and the toast all agree on one connected rig —
  there's no separate legacy shortcut that could leave them disagreeing.
- **Disconnect** — drop everything.

Either button reports the outcome as a toast, e.g. *"Rig connected — 7/7
roles up"*; if some roles failed to come up the per-row error shows inline
on that role's slot in **Devices**.

The **Link Status** panel (also on Settings → Connect and Settings →
Profiles) shows the per-role connected/error state at a glance — it is
populated from the same connect path as both buttons above, so it never lags
behind what Devices shows.

---

## Task providers (autofocus, polar align, plate solve)

The **Tasks** panel routes the three "smart" operations to whichever engine you
want:

- **Autofocus**, **Polar align**, **Plate solve** — each has a dropdown.
- **Auto (best available)** lets AstroDeck pick; or choose a specific provider —
  **AstroDeck native**, **NINA** (the connected bridge), **ASTAP** (solve), or
  **Simulator**.

Each row explains *why* it resolved the way it did (e.g. *"NINA bridge present —
using its TPPA plugin"*, *"ASTAP found at …"*, *"native V-curve engine drives the
camera + focuser"*). Provider badges elsewhere in the app (e.g. **AF · AstroDeck
native**, **TPPA · NINA**) show which engine is actually running a task.

Guiding has its own provider override with the same "who runs it" shape
(Auto / AstroDeck native / PHD2 / NINA bridge / Simulator) but lives on the
**Guide** view instead of this panel, since it depends on a guide camera + mount
connection rather than a driver's task offer — see
[Guiding](guiding.md#provider--phd2-fallback).

---

## The imaging train (focal length, and the scope's name)

**Settings → Connect → Imaging train.** Get this right before your first plate
solve, not after: focal length sets the image scale that plate solving, the
framing overlay and the guiding readout all depend on, and a wrong value makes
solves fail with nothing on screen explaining why.

- **Focal length (mm)** — the *effective* focal length of the whole train,
  including any reducer or extender. A 0.8× reducer on a 530 mm scope is 424,
  not 530. This is the single most common cause of "plate solving just doesn't
  work".
- **Telescope name** — written to the FITS `TELESCOP` card, which stackers group
  on. Name the **optical tube**, not the mount: a wrong string here silently
  splits one target across two groups at stacking time.
- **Take sensor details from the camera** — on by default, and right for almost
  every rig. Turn it off to pin pixel size and sensor dimensions by hand, which
  is worth doing when a driver reports the wrong pixel size, or to keep a working
  image scale while the camera is unplugged.
- **Guide scope focal length** — the *guide* train, not the imaging one. Without
  it, guiding RMS is reported in pixels at an assumed 1″/px rather than in real
  arcseconds. Leave it blank if you do not guide.

The Sky Atlas sidebar has an inline focal-length field too, and both write the
same setting.

---

## Rotator

When a rotator is connected, the Equipment view shows a **Rotator** card:

- **Move to** a position angle (with **Go**, `−1°` / `+1°` nudges, and **Halt**),
  and **Rotate to PA (plate solve)** to converge on a sky angle by solving.
- **Range of motion** — pick **full**, **half**, or **quarter** (to model
  cable-wrap limits). For half/quarter, set the mechanical **Start** (or **Set to
  current position**) and a **Tolerance** (in mod-180 degrees; default 1°).

Defaults are full range, start 0°, tolerance 1°. If you ask for a PA outside the
range, the card warns it will image at the nearest reachable angle instead.

---

## Profiles

A **profile** saves your whole rig — the per-role driver assignments, plus
optional per-rig optics, site name, and task routing — so you can restore a setup
in one tap. Two places manage them:

- **Equipment → Profiles**: **Save current assignments as** (name it, **Save**),
  then per row **Load** (repopulate the assignments to review before connecting)
  and **Activate**.
- **Settings → Profiles**: the full card list, plus a **Save Current Rig**
  panel that captures the *connected* rig as a new profile. Each card notes
  its backend mode (**Native / Alpaca**, **NINA bridge**, **Mixed backends**,
  or **Empty**) and device count, and offers:
  - **Activate** (or **Reconnect** if it's already the active profile) — sets
    it as the boot profile and connects it now.
  - **Rename** — an inline field (Enter or the check button to save, Escape
    or the × to cancel).
  - **Update** — a hold-to-confirm control that overwrites this profile's
    stored devices/backend/site name with whatever rig is *currently
    connected*, while keeping the profile's own optics, task-provider
    overrides, and PHD2/NINA-port settings untouched. It gets the same
    hold friction as Delete because it destroys the profile's previous
    device intent.
  - **↓ Export** — downloads the full profile as JSON.
  - **Delete** — a danger-styled, hold-to-confirm button (icon + label, not a
    bare ×).
  - Above the card list, the panel header has **Import** (upload a
    previously exported profile JSON) and **Refresh**.

The **active** profile **auto-connects on boot**. Activating a profile with real
motion devices asks you to confirm first, and offers **Force activate** if the
rig is busy.

---

## Routes

Drivers: `GET /api/drivers`, `POST /api/config/drivers`,
`PATCH`/`DELETE /api/config/drivers/{id}`, `POST /api/drivers/{id}/probe`.
Connect: `POST /api/connect/rig` (and `/sim`, `/alpaca`, `/nina`, `/phd2`),
`POST /api/disconnect`. Task routing: `POST /api/config/providers`. Rotator:
`POST /api/config/rotator`, `/api/rotator/move`, `/halt`, `/reverse`,
`/rotate-to-pa`. Profiles: `GET/POST /api/profiles`,
`POST /api/profiles/{id}/activate`, `/capture`, etc. Connect/profile writes
require `config.backend`; rotator moves require `control.mount`.

---

## Related

- [Getting started](getting-started.md) — the simulator rig on first launch.
- [Focus](focus.md) · [Sky Atlas](sky-atlas.md) — task providers in action.
