<a id="remote-access--roles"></a>

# Remote access and roles

Start with a named local administrator, then choose a network deployment. A fresh unauthenticated server binds loopback only. It is not an open LAN service.

<a id="who-may-sign-in-with-google"></a>

## Signing in

1. On the controller, stop the server if you are changing its deployment. Use the same configuration directory and account as the normal launch.
2. Run `python -m astrodeck create-admin yourname` from the installed source environment, or append `create-admin yourname` to the downloaded executable command.
3. Enter a strong password at the prompt. The command creates or resets an administrator, enables local sign-in, and exits.
4. Restart normally. Open the app and use **Sign in** where prompted.

For a realistic role test on the controller itself, check the loopback trust setting. In classic **Settings**, **Auth**, turn off **Trust loopback as admin** and press **Save methods** only after you have confirmed a working administrator account. Keep a local console available for recovery.

<a id="a-disposable-way-to-verify-this-yourself"></a>

<a id="screen-by-screen-what-each-role-actually-sees"></a>

<a id="the-three-roles-and-what-they-actually-gate"></a>

## Roles and what they actually gate

| Role | Default capabilities |
|---|---|
| viewer | Status and downsized previews |
| syncer | Status and raw science/media downloads; no rig controls |
| operator | Status, previews, weather, site-derived planning, capture, guiding, mount control and reconnecting a saved profile |
| admin | All capabilities, including precise site, configuration, power, users and updates |

An operator can reconnect the rig by activating a saved profile, but cannot edit the site, add or change backends and profiles, configure safety or download raw FITS by default. A syncer is a data-transfer role, not an operator with extra rights. Sign-in identifies the account; its role determines what it may do.

## Add or change accounts

1. In classic `#/classic/settings`, open **Users** as an administrator.
2. Create the intended local account and choose its role. Use a separate account for someone who only needs to watch.
3. Test that account in a separate browser session. Check a permitted action and a refused action; testing only as a loopback-trusted admin proves little.

For Google sign-in and account recovery, use [auth setup](../auth-setup.md). Local accounts do not require an internet sign-in provider.

<a id="the-default-open-on-the-lan"></a>

## Reach the controller from a phone

1. Confirm a named account works locally.
2. Use the [TLS reverse-proxy deployment](../../deploy/reverse-proxy/README.md) for LAN or public browser access, or deploy the [outbound relay](../relay-deploy.md).
3. Open the configured HTTPS address on the phone and sign in.
4. Confirm the account's role before operating equipment.

A shared `ASTRODECK_TOKEN` is a direct transport credential. It does not replace the named-account bootstrap in the supplied Compose deployment and is stripped at the relay boundary.

<a id="reaching-the-rig-from-outside--the-relay"></a>

## Reaching the rig from outside: the relay

The controller opens an outbound connection to an owner-operated relay. Remote browsers use the relay's HTTPS address and authenticate with a real home account. The relay terminates transport encryption and handles session cookies, so it is a trusted part of the deployment.

Some configuration and connection operations are blocked over the relay even for admins. Perform those directly at the controller. Activating an already saved profile is the exception: it is allowed over the relay for an administrator or an operator, but not with the force option, which stops a running sequence and is for an administrator at the controller. The current relay has no public viewer-share-link creation endpoint; use a viewer account instead. Closing a browser or signing out does not stop a server-side sequence.

An administrator can manage people over the relay after signing in again. Every change needs a sign-in less than five minutes old, because the relay can see and replay a session cookie but cannot make you sign in. Over the relay you can add a Google-only viewer or operator, move a viewer or operator between those two roles, disable or enable them, and delete them. Passwords, administrator accounts and changes to a username or email stay on the controller. This does not protect against a relay that is hostile while you sign in, so run the relay yourself.

## Site privacy for remote and low-role users

Viewer access is limited to status and previews. Operators have weather and site-derived information, which can reveal the observing region, while precise site fields remain admin-only. Do not describe operator access as location-free. See [site and locations](site-and-locations.md).

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
