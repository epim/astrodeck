<a id="6-quick-start"></a>

<a id="astrodeck-auth-setup-guide"></a>

# Authentication setup

Use a named local administrator before exposing AstroDeck beyond the controller. The initial server is loopback-only. An empty list of sign-in methods is not a safe LAN deployment, and a non-loopback launch without valid authentication is refused.

<a id="cli-create-admin"></a>

<a id="first-run-setup-create-the-first-admin-from-the-lan"></a>

## 1. Create the administrator

1. Use the account and configuration directory that normally run AstroDeck. Stop the server while preparing a deployment.
2. From the installed server environment, run:

```text
python -m astrodeck create-admin yourname
```

3. Enter and confirm the password at the prompts. For a downloaded binary, use that executable followed by `create-admin yourname` instead.
4. Restart the server and verify that the account can sign in. The command enables local sign-in automatically and exits without launching the server.

The same command resets an existing account's password, enables it and restores its admin role. It is the local recovery path, so protect console access and the configuration directory. Do not pass a password on the command line.

<a id="2-local-users-offline-no-internet"></a>

<a id="4-anti-lockout--recovery"></a>

<a id="managing-users-admin-users-panel"></a>

## 2. Manage local users

1. Open the classic interface at `#/classic/settings`, then **Users**.
2. Add the intended account and role. Use viewer for status/previews, operator for imaging and mount operation, or syncer for a data-transfer account. Admin includes configuration and user management.
3. Sign in as the new account in a separate browser session to verify the access you intended.

Passwords are stored as bcrypt hashes. The current password rules reject fewer than 12 characters after trimming surrounding whitespace and more than 72 UTF-8 bytes. Choose a unique password that satisfies both limits.

<a id="b-astrodeck-fields-authconfig"></a>

<a id="password-rules-authpasswordspy"></a>

## 3. Check sign-in methods

1. In classic **Settings**, open **Auth** and **Sign-in methods**.
2. Review **Enable local accounts** and **Trust loopback as admin**. Loopback trust can make a browser on the controller act as admin without testing an account's real permissions.
3. Keep a working admin and local recovery console before disabling that trust. Press **Save methods** to apply the draft.
4. Expect an authentication change to invalidate existing sessions. Sign in again and confirm the new settings.

Turning off all methods is not a phone-access setup. Use the [network deployment guide](guide/remote-access-and-roles.md).

<a id="3-google-oidc-web-sign-in"></a>

<a id="a-google-cloud-console"></a>

## 4. Google sign-in

Google sign-in is optional and needs an OAuth client configured for the address users actually open. Retain a local administrator as a recovery route.

1. Prepare a Google OAuth web client and its callback address ending in `/auth/google/callback`.
2. On the controller, stop the server and back up the private configuration directory. In its `astrodeck.json` auth block, configure `google_client_id`, `google_client_secret`, and `google_redirect_uri`; keep these secrets out of logs and source control. Preserve unrelated fields.
3. Set `role_allowlist` for the intended email addresses. Keep `default_role` null to deny unlisted accounts, or viewer if that broader access is intended. Higher default roles require a pinned `google_hd` Workspace domain.
4. Restart, open classic **Settings**, **Auth**, and enable **Enable Google sign-in**. Press **Save methods**.
5. Test **Sign in with Google** in a separate browser. A successful Google identity does not grant access unless the allowlist or default role authorizes it.

Changing the address, proxy or relay can change the callback URL. Configure the OAuth client and server consistently. For a relay deployment, retain the `/h/<home_id>/` path prefix in the public callback URL.

<a id="1-overview"></a>

<a id="5-security-notes"></a>

## 5. Recovery and remote access

Use `create-admin` against the normal configuration directory when locked out. Do not delete account files or the whole configuration tree. For remote use, follow [relay deployment](relay-deploy.md) or the [TLS reverse-proxy instructions](../deploy/reverse-proxy/README.md). The current relay forwards home authentication; it does not provide public share-link creation or a separate relay login provider.
