# Design brief: AstroDeck DC power + USB hub HAT

**Date:** 2026-08-10. **Status:** brief ready to hand to a hardware design agent.
**Purpose:** this document *is* the deliverable for requirement 8 — a prompt
carrying the constraints a receiving agent needs so it does not design the
wrong board. Sub-project 10 in `2026-08-10-appliance-product-program.md`.

---

## Prompt for the hardware design agent

You are designing a HAT for the **Orange Pi 5 Pro (RK3588S)** that turns the
AstroDeck appliance into a single-cable astrophotography power and data hub.
The appliance is sold to amateur astronomers; it sits on a moving telescope
mount, outdoors, overnight, often on battery, in condensing humidity and
sub-zero temperatures.

### What it must do

- **4 switched DC outputs on 5521 barrel jacks** (5.5 mm OD × 2.1 mm ID),
  individually controllable, with per-port current sensing.
- **2 dedicated PWM dew-heater channels** (resistive loads; PWM duty control,
  not on/off).
- **A downstream USB hub: 4× USB-A + 2× USB-C**, all with data, at usable
  per-port current.
- **Power the Orange Pi itself** from the same 12 V input, so one cable
  supplies the whole rig.

### Hard constraints you must respect

**Connector choice is not cosmetic.** 5.5×2.1 and 5.5×2.5 both exist in astro
gear. A 2.5 mm plug will not enter a 2.1 mm jack, and a 2.1 mm plug in a
2.5 mm jack makes intermittent contact that presents as a random device
dropout at 2 a.m. State which you chose and why, and say what the customer
does about the other kind.

**The USB topology is already two tiers deep before you add one.** The
verified peripheral train on the reference rig is six USB devices plus a flat
panel and a WandererBox, and the ZWO AM5N mount carries its own downstream hub.
Your hub becomes a third tier for anything behind the mount. Account for it:
USB 2.0 permits five tiers, but every added tier costs latency and reliability,
and this codebase already carries reconnect logic for intermittent-disconnect
failures. Say explicitly which upstream port on the Pi you take, what that
costs the host, and whether you are USB 2.0 or USB 3.x per port.

**The 11 V floor is a hard voltage-drop budget.** Wanderer rotators refuse to
move below 11 V. On a 12 V battery already sagging during a slew, another
0.3-0.5 V lost across a high-side switch and a sense resistor puts the rotator
into refusal. Budget total drop from input terminal to jack pin and state it.
Prefer low-R<sub>DS(on)</sub> switching and a sense method that does not burn
headroom.

**Input range.** Design for 12 V nominal but tolerate a real field range —
a sagging LiFePO4 pack through a 13.8 V regulated supply. State your survival
and operating ranges. Reverse-polarity protection, inrush limiting, per-channel
fusing or e-fuse behaviour, and defined behaviour on brown-out are all
required, not optional.

**Do not fabricate electrical figures.** There is not one current or power
measurement for any peripheral anywhere in the AstroDeck repository. The only
electrical numbers that exist are simulator telemetry (invented) and the 11 V
rotator threshold. **Every current, power or thermal number in your design must
cite a vendor datasheet by name and revision, or be labelled explicitly as an
assumption to be measured.** A plausible-looking "measured peak TEC draw" that
you invented is worse than an honest unknown, because it will size a component.

Cooled cameras are the dominant and least predictable load — TEC draw varies
with delta-T and duty. Treat the imaging camera channel as the sizing case and
say what you assumed.

### Astro-specific hazards that generic hub designs get wrong

- **Ground loops put banding in images.** USB shield ground and DC return
  sharing a path through the camera is a real, well-known source of pattern
  noise in astrophotography. Address it deliberately — say what your grounding
  topology is and why it does not create a loop through the imaging train.
- **EMI.** Switching regulators and PWM dew heaters sit centimetres from a
  cooled sensor reading out at high gain. State switching frequencies and what
  keeps them out of the image.
- **Condensation and cold.** The board is outdoors all night. Conformal
  coating, connector selection and component temperature ratings matter.
- **Thermal.** A header-mounted board sits directly over the RK3588S, which
  needs its own heatsinking. Say how you avoid smothering it, and where your
  own dissipation goes.
- **The Orange Pi 5 Pro has no RTC.** A wrong clock in this product is a safety
  issue, not an inconvenience — solar-avoidance, dawn-park and resume windows
  all derive from it, and a wrong clock is silently self-consistent across all
  of them. **Adding a battery-backed RTC to this HAT is high value.** Propose
  one.
- A **hardware watchdog** is likewise worth proposing for an unattended box.

### The control-interface fork — decide it explicitly

**Option A: an MCU on the HAT presenting USB CDC-ACM serial.** The appliance
already has `pyserial` and a `SerialLink` abstraction with reconnect logic, so
the software cost is near zero and the driver is portable to any host. Costs a
USB port and an MCU.

**Option B: direct GPIO/I²C/PWM off the 40-pin header.** No MCU, but it adds a
GPIO runtime dependency, generally needs elevated privilege (the appliance runs
its server as a **non-root** user behind a typed privileged broker — see the
program charter), and pins the driver to this SoC and this header, which
conflicts with the product's vendor-neutral direction.

**Recommend A unless you can show B is clearly better here.** If you choose B,
say exactly which header pins you claim and confirm they do not collide with
anything the appliance needs.

### Software work this HAT implies — state it, do not assume it exists

- **No first-party switch driver exists on any platform today.** The only
  `Switch` implementation in the tree is inside the ASIAIR bridge backend
  (`server/astrodeck/devices/backends/asiair_backend.py`). Your HAT needs a new
  native backend written against `server/astrodeck/devices/base.py`.
- **`SwitchPort` cannot represent what you are building.** Verified at
  `devices/base.py:536-544`, its fields are `id, name, can_write, is_boolean,
  value, min, max, unit` — **no current, no voltage, no fault, no
  temperature.** Per-port current sensing would have nowhere to land and would
  render as unpaired telemetry. Specify the device-model extension your board
  requires as part of your deliverable.
- Dew-heater PWM, per-port current, per-port fault and input-voltage telemetry
  each need a home in that model. Say what you need; do not assume a field.

### Open questions the repository cannot answer

- Whether the Pi may be back-fed from the HAT's 5 V rail while also having its
  own USB-C power input connected, and what happens if both are present. This
  is not documented anywhere in the project and you must resolve it from Orange
  Pi's own schematic, not by inference.
- The 40-pin header's real 5 V and ground current budget on this specific
  board, and its actual power-input rating.

### Deliverables

1. A block diagram and a stated architecture decision (A or B above).
2. A parts list with named vendors, part numbers and **datasheet citations for
   every electrical claim**.
3. Power budget: per-channel and total, with the sizing assumptions labelled.
4. Voltage-drop budget from input terminal to each jack, checked against the
   11 V floor.
5. Grounding and EMI strategy, addressed to the banding problem specifically.
6. Thermal strategy for both the HAT and the SoC beneath it.
7. The device-model extension and native backend the software side must add.
8. An explicit list of everything you assumed and everything you could not
   verify. This is a required section, not a caveat.

### What not to do

Do not present invented numbers as measurements. Do not design past the brief
into sequencing, imaging or mount control — this board is power and data only.
Do not assume any software abstraction exists that this document has not
confirmed.
