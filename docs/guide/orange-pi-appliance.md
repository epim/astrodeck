# Orange Pi appliance

An Orange Pi 5 Pro is a small, low-power single-board computer. Some rigs
run AstroDeck on one instead of a laptop or a Windows PC, so the controller
can sit at the telescope permanently. Because a board like this usually has
no keyboard, mouse or screen attached, this repository includes a small
onboarding tool: the board broadcasts a temporary Wi-Fi network of its own,
and you join your home Wi-Fi to it from a phone, the same way you would set
up a smart-home device.

This repository does not provide a ready-to-flash appliance image you can
download and write to a card. It provides the onboarding tool
(`orangepi5/`) that someone installs onto a board after they have put an OS
on it and installed AstroDeck. If you received a board that someone else
already prepared, start at [Use an appliance someone set up for
you](#use-an-appliance-someone-set-up-for-you). If you are starting from a
bare board, start at [Prepare your own board](#prepare-your-own-board).

Only one board and OS combination has actually been checked end to end: an
Orange Pi 5 Pro running Armbian Trixie (community build
`26.8.0-trunk.7`), checked on 2026-09-03 with software restarts standing in
for real power cuts. The onboarding tool should work the same way on other
boards in the Orange Pi 5 family, but treat that as unverified, not as
broken. See [Known limits](#known-limits) below.

## What you need

- An Orange Pi 5 Pro board (or a close relative; see above).
- A microSD card for the operating system. Armbian boots from the card; if
  the board also has eMMC storage, that is kept separate for write-heavy
  data such as captures, not for the OS.
- A power supply that matches what the board's own documentation calls
  for. An underpowered supply is a common cause of a board that will not
  boot reliably, and is easy to mistake for a software problem.
- A 2.4 or 5 GHz Wi-Fi network to join the board to. Onboarding only works
  over Wi-Fi; this tooling has no wired-network setup path.
- A phone or computer to do the onboarding from.

## Use an appliance someone set up for you

A "commissioned" appliance already has AstroDeck installed and the
onboarding tool enabled, and has a setup password printed for it (on a
label on the case, or handed to you separately).

1. Power the appliance on and give it a minute or two to finish booting.
2. On your phone, look for a Wi-Fi network named `AstroDeck-XXXX` (the last
   four characters are specific to that board) and join it using the
   printed password. The password is 12 characters in three groups of
   four, for example `ab12-cd34-ef56`. There is no shared or fallback
   password; each board's is different.
3. A setup page should open on its own, the way a hotel or airport Wi-Fi
   page does. If it does not, open a browser and go to `http://10.42.0.1/`.
4. On that page, choose your home Wi-Fi network (or type its name) and
   enter its password, then choose Connect. The page will tell you it is
   joining your network and that this setup network is about to switch
   off. That message confirms the appliance received your request, not
   that the join itself succeeded; the next step confirms that.
5. Reconnect your phone or computer to your home network. Find the
   appliance there, either in your router's list of connected devices or
   at the address you were given for it, and open that address in a
   browser.
6. Sign in with your AstroDeck account. The Wi-Fi password from step 2 and
   your AstroDeck sign-in are different things: joining Wi-Fi does not
   create or change an AstroDeck account. If you do not have an account on
   this appliance yet, whoever prepared it needs to create one; see
   [Remote access and roles](remote-access-and-roles.md).

### If the setup network never appears

The setup network only opens for one 15-minute window per authorization.
If this is not the board's first boot since it was commissioned, or
someone already used that window, it will not reopen by itself. Use
[Recover a missed or expired setup window](#recover-a-missed-or-expired-setup-window)
below.

### If joining your home network fails

If you mistyped your home Wi-Fi password, reconnect to the same
`AstroDeck-XXXX` network before the 15-minute window ends; the setup page
shows the error and lets you try again. Once the window has closed,
retrying needs the recovery steps below.

### Recover a missed or expired setup window

This restarts Wi-Fi onboarding only. It does not touch AstroDeck accounts,
captures, plans or any other configuration.

1. Turn the appliance's power off.
2. Turn it back on, then off again within about a minute, before it has
   finished a normal boot.
3. Repeat that two more times, so the power has been cycled three times in
   a row, with all three cycles finished within about three minutes of the
   first.
4. On the next boot, the `AstroDeck-XXXX` network opens again for a new
   15-minute window, using the original password printed when the board
   was commissioned (not a later one, if it was ever changed). Continue
   from step 2 under [Use an appliance someone set up for
   you](#use-an-appliance-someone-set-up-for-you).

If three short power cycles do not bring the network back, the appliance
was probably never commissioned, or its onboarding services are not
running. Recovering from that needs local or console access to the board;
see [orangepi5/README.md](../../orangepi5/README.md).

### If the appliance still does not show up on your network

The appliance also runs a Wi-Fi watchdog that reloads a crashed radio
driver on its own, usually within about 30 seconds, so a brief drop is not
necessarily a failure. If it is still missing after that and recovery does
not help, someone with physical or console access to the board can install
the temporary telemetry collector
([orangepi5/diag/install-diag.sh](../../orangepi5/diag/install-diag.sh))
onto its SD card. It logs Wi-Fi state, signal and relevant system messages
to a file every minute so they can be read later without a live
connection to the board. This is a console-level tool for whoever has the
board in hand, not something you can run from a phone.

## Prepare your own board

Building an appliance means installing an OS and AstroDeck yourself, and
optionally adding the same onboarding tool described above so the board
(or whoever you hand it to) can join Wi-Fi networks without a keyboard or
screen attached.

1. Install and boot a 64-bit OS on the board, such as Armbian. Confirm
   storage, networking and the power supply match the board's own
   documentation before going further.
2. Install AstroDeck through the [Linux ARM64
   binary](install-binary.md), [source setup](getting-started.md) or
   [Docker path](install-docker.md). These are three separate
   alternatives; none of them is installed by the onboarding tool below.
3. Create an administrator account and sign in once over a direct
   connection before putting the board on a network unattended; see
   [Remote access and roles](remote-access-and-roles.md).
4. If you also want the phone-based Wi-Fi onboarding this guide describes,
   install the tooling in `orangepi5/` onto the board's root filesystem,
   make sure `/data` is mounted as its own durable filesystem rather than
   a plain directory on the OS disk (its services refuse to run
   otherwise), then commission the board once from its local console.
   Commissioning prints the setup password described above: write it on
   the appliance's case, and do not keep it anywhere that turns into a
   shared list across boards. Full commands are in
   [orangepi5/README.md](../../orangepi5/README.md).
5. Before relying on the board unattended, connect your actual imaging
   equipment to it and confirm USB or serial discovery, hot-plug and
   restart behavior yourself. The checks that ship with this tooling run
   on a host computer and do not exercise real hardware.

## Known limits

Native guiding and native autofocus require `astrodeck_native`, which
published releases do not include yet ([#630](https://github.com/epim/astrodeck/issues/630)).
ASTAP and its star database are a separate installation. An Orange Pi
running AstroDeck is not, by itself, evidence that either is present.

Only an Orange Pi 5 Pro running Armbian Trixie has had its onboarding
tooling checked against real hardware, and that check used software
restarts rather than physical power cuts. The results and what was left
unverified are recorded in the [hardware
gate](../hardware/orange-pi-5-hardware-gate-2026-09-03.md).

## Related

[Install the binary](install-binary.md) · [Install with
Docker](install-docker.md) · [Remote access and
roles](remote-access-and-roles.md) · [orangepi5/README.md](../../orangepi5/README.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
