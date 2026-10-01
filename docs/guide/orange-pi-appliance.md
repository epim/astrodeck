# Orange Pi appliance

The repository contains an Orange Pi 5 Wi-Fi provisioning component and an AstroDeck application. The provisioning installer copies portal and recovery services into an existing root filesystem; it does not flash an OS or install the complete astronomy application.

The recorded board check used an Orange Pi 5 Pro with Armbian Trixie. It is evidence for that board and software combination, not a claim that every Orange Pi or Wi-Fi adapter has passed the same checks. The test substituted software reboots for physical power cuts.

## Use an already commissioned appliance

1. Find the appliance's printed setup network name and per-device password. There is no universal setup password.
2. During its authorized setup window, join that Wi-Fi network from your phone or computer.
3. Open the captive portal, or browse to `http://10.42.0.1`, and select your home Wi-Fi network. Enter its password locally.
4. After joining, reconnect your phone to the home network. Reach AstroDeck through the application address configured for that appliance and sign in with an application account.

The Wi-Fi credential and the AstroDeck administrator account are different. Provisioning the network does not create an application account. A setup window expires after 15 minutes and does not reopen simply because home Wi-Fi is absent.

## Prepare your own board

1. Install and boot a suitable 64-bit OS. Confirm storage, networking and the board's power supply before installing AstroDeck.
2. Install the application through the [Linux ARM64 binary](install-binary.md), [source setup](getting-started.md), or [Docker path](install-docker.md). These are alternatives; none is silently installed by the portal script.
3. Create an administrator and configure authenticated access. Test the simulator locally before attaching hardware.
4. If you are building a provisioning appliance, follow the [provisioner installation and commissioning instructions](../../orangepi5/README.md). Its services require a durable `/data` mount and local commissioning. Keep the one-time credential handoff private and put the credential on the enclosure.
5. Validate the target board's USB/serial discovery, hotplug, storage and restart behavior with the intended equipment. Host tests do not replace that hardware check.

Native guiding and native autofocus still need a separately built `astrodeck_native` extension; current release packaging does not supply it (#630). ASTAP and its star database are separate. An ARM64 OS or executable alone does not establish that all vendor SDKs are usable on that board.

## Recover Wi-Fi access

Three distinct short boots within the recovery window can authorize a new setup window and restore the printed factory Wi-Fi password. The implemented thresholds are boots shorter than 60 seconds within 180 seconds. This is Wi-Fi recovery; it does not reset AstroDeck accounts, captures or plans. Follow the appliance's commissioning instructions before relying on it.

If a commissioned appliance fails to rejoin, use its local console to inspect the provisioning services. Do not factory-reset the application to repair a Wi-Fi problem. The [recorded hardware gate](../hardware/orange-pi-5-hardware-gate-2026-09-03.md) lists what was tested and what remained unverified.
