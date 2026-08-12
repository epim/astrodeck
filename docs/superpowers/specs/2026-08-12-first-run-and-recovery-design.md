# First run, claiming, and recovery

**Date:** 2026-08-12. **Status:** design (owner-approved flow).
**Covers:** requirement 3 of the product charter — sub-project 3, *"Secure-by-default
first run and the shipped credential"*. Depends on sub-project 1 (image bake).
**Charter:** `2026-08-10-appliance-product-program.md` (D2, D3, D6, D8).

Evidence convention: **[v]** verified by reading the file or running it during
this work; **[r]** reported, not independently checked.

## The idea

**An unclaimed appliance does not run AstroDeck at all.** A dirty bit gates
`astrodeck.service`; while it is set, a small setup server owns the port instead.

This is stronger than a first-run screen inside the app. There is no window in
which the API is reachable unauthenticated, because the API is not running. It
also collapses the WiFi and Ethernet paths into one: whichever way the customer
reaches the box, `astrodeck.local` is the setup portal until it is claimed, and
AstroDeck afterwards.

It replaces the open-admin default, which is otherwise the shipped posture:
`_active_provider` defaults to `NoneAuthProvider` (`auth/deps.py:41`), whose own
docstring reads *"The open default. Resolves EVERY caller to admin/ALL_CAPS"*
(`auth/providers.py:40-49`), with `--host 0.0.0.0` (`__main__.py:163`). **[v]**

## The claim credential

A **per-device password generated at first boot** — never baked into a shared
image, where one constant would open every unit ever sold. It is:

- printed on the enclosure label, next to a WiFi-join QR code;
- shown in the hotspot portal after a successful WiFi join;
- **required by the setup portal to claim the box**;
- immediately replaced by one the customer chooses.

That last point is what makes it safe on a shared network. Pure first-claim-wins
is exploitable: an Ethernet-first box in an apartment or at a star party is
claimable by whoever loads the page first, and the real owner gets a 409 with no
recovery. Requiring the label password turns *"first to find it"* into *"proves
they physically have it"*, and costs nothing because the password exists anyway.

**Format:** three lowercase-alphanumeric groups of four (`rrxk-sl3a-qatn`) —
readable aloud, typable on a phone in the dark, ~62 bits.

## One service, two phases

`orangepi5/provision/` grows from a WiFi portal into the setup service. **Same
program, same styling, two phases** — not two codebases:

| Phase | When | Binds | Serves |
|---|---|---|---|
| **A — network** | no default route within 90 s | `10.42.0.1:80` on the hotspot | WiFi picker; on success, the address and the generated password |
| **B — claim** | online and dirty bit set | `:8800` and `:80` on the LAN | claim form: label password → new password → timezone |

Phase A already exists and works. Phase B is new.

`:80` needs `CAP_NET_BIND_SERVICE`, and must sequence behind phase A on offline
boots — the captive portal owns `:80` on exactly the boots where onboarding
matters, so this needs arbitration, not a port number.

## What the customer sees

1. Power on. No network → hotspot `AstroDeck-XXXX`. LED signals *needs attention*.
2. Join the hotspot — by scanning the label QR (`WIFI:S:AstroDeck-1A2B;T:WPA;P:…;;`)
   or by hand. Captive portal opens itself.
3. Pick WiFi, enter the passphrase.
4. **Before the hotspot drops**: "Connected to *<SSID>*", the address
   `astrodeck.local`, the raw IP as a fallback, and the generated password.
5. **The hotspot stays up for a grace period** (5 min, or until an authenticated
   session appears from the LAN side). A customer who mistyped, or whose router
   did something odd, still has a way back in. This is the single biggest UX
   difference from the current design, where the moment of success is also the
   moment the connection drops and there is no second screen to recover on.
6. Open `astrodeck.local`. Setup portal: enter the label password, choose a new
   one, confirm timezone.
7. Reboot. AstroDeck answers on the same URL.

An Ethernet-first customer skips 1-5 entirely and starts at 6, which is the
whole point of gating on the dirty bit rather than on the portal.

## Timezone, and why not location

**Timezone is taken automatically** from
`Intl.DateTimeFormat().resolvedOptions().timeZone` — a plain JS API needing no
permission and no secure context — and offered for confirmation.

**Browser geolocation is not available and must not be designed around.**
`navigator.geolocation` requires a secure context; browsers block it on plain
HTTP except on `localhost`, and `.local` mDNS names do not qualify. The box
serves plain HTTP on a LAN name, so the prompt never appears. **[r — verify
against the shipping browsers before implementing]**

Instead, derive a *suggested* location from the IANA timezone and present it for
correction. This matters beyond convenience: dawn-park, the sun watchdog, solar
exclusion and resume windows all derive from local time, and a wrong clock is
silently self-consistent across all of them.

Site coordinates are sensitive (`view.site_derived` exists because a viewer
geolocated the observatory to 2.9 km from *derived* values). The setup portal
must store them through the same path the app uses, not a side channel.

## The "no password" escape hatch

The owner may run the box with no authentication. It is their hardware and PSTI
bans *shipped defaults*, not informed choices. It must be:

- a deliberate confirmation, not a Next-Next default;
- recorded with a timestamp and the fact that it was chosen;
- surfaced as a permanent banner in the UI;
- **and it must clear the dirty bit** — otherwise the box loops back into setup
  on every boot.

## Recovery: three power cycles

No SD access, no console, and the OPi's only recessed button is MaskROM —
repurposing that invites a bricked board. So: **three consecutive short power
cycles**. It proves physical presence (you must reach the plug) and needs no
hardware. A reset button on the DC/USB HAT supersedes it when that board exists.

Detection: a oneshot early in boot increments a counter on `/data`; a timer
clears it once uptime passes 60 s; the counter also resets if the previous boot
was more than a few minutes ago, so the cycles must be deliberate and
consecutive.

**Credential recovery and factory reset are different actions**, and the
accidentally-triggerable one is the harmless one — a brownout at a dark site
should cost a re-login, never data:

| | Trigger | Effect | Keeps |
|---|---|---|---|
| **Credential recovery** | 3 power cycles | new generated password, dirty bit set, all sessions killed via `session_epoch` | WiFi, plans, captures, config |
| **Factory reset** | UI, authenticated | wipes `/data` | nothing |

Recovery must be **loud**: the LED signals it, and the next successful login
shows *"admin credential was reset at <time> by physical reset."* Someone with
physical access can always do this; the owner must see that it happened.

**Recovery is never a network request.** A reachable reset endpoint is the
escalation path requirement 6 exists to prevent.

## Existing code this changes

| What | Where | Why |
|---|---|---|
| `create_admin` does not enable the local method | `__main__.py:80-110` **[v]** | Seeding an admin leaves `methods == []` → `NoneAuthProvider` → still open. Setting the credential must set the method, or the two must be done together by the claim flow. |
| Factory reset leaves the customer's WiFi | `factory_reset.py` touches only `CONFIG_DIR`/`CAPTURE_DIR` **[v]** | A customer who sells the box hands over their home SSID and passphrase. Network config moves to `/data/network.json` under factory reset's control. |
| `reset_auth` restores `methods` to `[]` | `factory_reset.py:203,222` **[r]** | "Factory reset" currently *un-authenticates* the box. It must set the dirty bit and mint a new credential instead. |
| `write_json_atomic` sets no file mode | `persist.py:158` **[v]** | Proven live on the board: chmod 0600, restart, files return 0644/0664. `session_secret`, `users.json` and the relay token need explicit modes. |
| `POST /api/auth/config` never calls `_preserve_auth_secrets` | `api/app.py:5711,5736,5774` **[v]** | One Save wipes `admin_token` and can flip a configured box back to open admin. The claim flow must not route through it. |

`POST /auth/setup/local` already exists, is LAN-only and auto-closes at 409
forever (`local_routes.py:222-278`) **[r]** — the claim portal should reuse that
contract rather than invent a second one.

## Durable guards

Written as the invariants, not the implementations:

1. **An unclaimed box serves no API.** Boot with the dirty bit set and assert
   every `/api/*` route is unreachable — not 401, *absent* — while the setup
   portal answers.
2. **A claimed box is never `NoneAuthProvider`.** Assert that clearing the dirty
   bit implies `methods != []`, so the `create_admin` gap cannot reappear.
3. **Factory reset leaves no network credential.** Assert the WiFi passphrase is
   absent from every file under `/data` afterwards — by value, not by key name.
   A key-name filter cannot withhold a secret that moved.
4. **The claim endpoint closes.** Second claim attempt is 409 forever, including
   across a restart.
5. **Recovery is not reachable over the network.** Enumerate routes and assert
   none triggers credential reset.

Each needs a sabotage test that makes it fail on demand; a guard that cannot
fail is worse than none, because the green check stops anyone looking.

## Open

- Verify the geolocation-on-HTTP claim against shipping browsers before building
  around it.
- Decide whether the reboot after claiming is required or whether the setup
  service can hand off to `astrodeck.service` in place. Reboot is simpler and
  more predictable; the setup page must then poll `/healthz` and redirect, or
  the customer sees a dead browser tab at the moment of success.
- The grace-period hotspot needs AP+STA concurrency on the AIC8800, or a timed
  hand-back. Verify on hardware before promising it.
