# AstroDeck Auth Setup Guide

Operator guide to turning on authentication. Grounded in the real code:
`auth/routes.py`, `auth/local_routes.py`, `auth/capabilities.py`,
`config.py` (`AuthConfig`), `auth/passwords.py`, `auth/session.py`,
and `__main__.py` (`create-admin`).

---

## 1. Overview

**Default is OPEN.** Out of the box `AuthConfig.methods` is empty, so every
caller resolves to **admin** with no credentials. The live LAN tablet just
works — nothing to configure. Auth is strictly **opt-in**.

**Multi-method.** Auth is a *set* of methods, not a single provider:
`methods: list[str]` is a subset of `{"local", "google"}`. Empty means open.
You can enable **both** `local` and `google` at the same time (offline
username/password *and* Google web sign-in on the same server). A legacy
`provider: "google"` field is migration-only: if `methods` is empty it folds
into `["google"]`, otherwise it is ignored.

**Roles** (single source of truth: `auth/capabilities.py`):

| Role | Capability summary |
|------|--------------------|
| `viewer` | Read-only: live status + downsized preview. No raw FITS, no precise site coords. |
| `operator` | Viewer + imaging (`control.capture`) and guiding (`control.guide`). Cannot slew the mount, switch power, or change config. |
| `admin` | Everything, including raw media, precise site, and all config/user/auth administration. |

> Order = privilege rank. `viewer` is the ceiling for an untrusted Google
> `default_role` (see the ceiling rule below).

**Do not expose the server to the WAN with no method enabled** — that grants
admin to anyone who can reach it.

---

## 2. Local Users (offline, no internet)

Best for a self-contained rig with no internet. Enable the `local` method
(add `"local"` to `methods`, e.g. via the admin config API / Auth panel, or
edit `config.json` `auth.methods`).

### First-run setup (create the first admin from the LAN)

While the user store is **empty**, a one-time bootstrap screen is open:

- **`POST /auth/setup/local`** — `{username, password, email?}` creates the
  first **admin** and logs that browser straight in (sets the `ad_session`
  cookie).
- It is gated to: `local` enabled **AND** `local_enabled_first_run = true`
  **AND** the user store is empty.
- It **auto-closes** the instant any user exists (returns `409`). It can never
  be used to add a second backdoor admin later.

The UI decides what to render from the unauthenticated
**`GET /api/auth/methods`** signal: `{methods, google_configured, first_run}`.

### Managing users (admin Users panel)

All routes are gated by `require(admin.users)`:

| Method | Path | Action |
|--------|------|--------|
| `GET` | `/api/users` | List users (no password hash ever returned) |
| `POST` | `/api/users` | Create a user (`role` defaults to `viewer`) |
| `PATCH` | `/api/users/{id}` | Set role / enabled / rename / email |
| `POST` | `/api/users/{id}/password` | Reset a password |
| `DELETE` | `/api/users/{id}` | Delete a user |

**Last-admin protection:** demoting, disabling, or deleting the last enabled
admin is refused (`409`).

### Password rules (`auth/passwords.py`)

- **No blank / whitespace-only** passwords (min length after trimming = 1).
- **72-byte maximum** (the bcrypt input limit). A longer password is rejected
  (`422`), never silently truncated.
- Hashed with bcrypt cost 12.

### CLI: `create-admin`

```
python -m astrodeck create-admin <username> [--password PW]
```

- Seeds a new admin, or **resets** an existing user back to an enabled admin
  with a fresh password (recovery).
- Prompts for the password via `getpass` if `--password` is omitted.
- **Never boots the server** and never imports the app — it only touches the
  user store, so it works even if the server config is broken.

---

## 3. Google OIDC (web sign-in)

Use this for browser sign-in over the public internet. Two halves: configure
Google, then configure AstroDeck.

### A. Google Cloud Console

1. Create (or pick) a **project**.
2. Configure the **OAuth consent screen** (Internal if you have a Workspace and
   want to restrict to your domain; otherwise External).
3. Create credentials → **OAuth 2.0 Client ID** → application type
   **Web application**.
4. Set the **Authorized redirect URI** to exactly:

   ```
   <base>/auth/google/callback
   ```

   - Local example: `http://localhost:8800/auth/google/callback`
   - Production: your public **HTTPS** relay URL, e.g.
     `https://astro.example.com/auth/google/callback`

   > Google accepts **only** `localhost` or a **public HTTPS** redirect. A bare
   > `http://` LAN IP (e.g. `http://192.168.1.x`) will be **rejected** by
   > Google. For LAN access over Google, front the server with an HTTPS relay.

5. Copy the generated **Client ID** and **Client secret**.

### B. AstroDeck fields (`AuthConfig`)

| Field | Meaning |
|-------|---------|
| `google_client_id` | From Google. |
| `google_client_secret` | From Google (secret; redacted from the config API). |
| `google_redirect_uri` | Must match the Google redirect URI **exactly**. |
| `role_allowlist` | `email -> role` map, re-evaluated on **every** login (remove an entry and the next login is denied/downgraded). |
| `default_role` | Role for any authenticated-but-unlisted user. `null` = **deny** unlisted (recommended). |
| `google_hd` | Optional Workspace hosted-domain pin (restricts to one domain). |

Then add `"google"` to `methods` to enable it.

**Login flow** (`auth/routes.py`): `GET /auth/login` starts Authorization-Code
+ PKCE and 302s to Google; `GET /auth/google/callback` verifies state/PKCE/ID
token, maps email→role, and mints the `ad_session` cookie. If Google is not
enabled, both routes are inert (`404`).

**Authorization order:** `role_allowlist[email]` wins; otherwise
`default_role`; otherwise the login is **denied** (`403`, authenticated but not
authorized).

> **Ceiling rule (enforced on save, `validate_auth_config`):** a `default_role`
> **above `viewer`** (i.e. `operator` or `admin`) requires a non-empty
> `google_hd`. Otherwise the save is rejected (`400`) — this refuses to
> auto-elevate the entire Google population over the WAN. Keep `default_role`
> at `null` or `viewer` unless you pin a Workspace domain.

---

## 4. Anti-Lockout / Recovery

Three independent ways back in:

1. **Break-glass token (`ASTRODECK_TOKEN`).** Set this env var before launch.
   A caller presenting it (as `X-Auth-Token` / bearer) is **always admin**,
   **independent of any method**. Works even with `local`/`google` on. This is
   the generalized shared-token gate (`admin_token` in config mirrors it).
2. **`create-admin` CLI** (Section 2) — seeds or resets a local admin offline,
   without booting the server.
3. **First-run setup** (`POST /auth/setup/local`) — only while the user store
   is empty.

**If you are locked out:**
- Set `ASTRODECK_TOKEN`, restart, and use it to fix config / users; **or**
- Run `python -m astrodeck create-admin <you>` to reset your admin password
  and log in via the local form.

---

## 5. Security Notes

- **Session secret auto-persists.** When you first enable a real method and
  `ASTRODECK_SECRET` is unset, the boot path generates a random secret and
  writes it to `config/session_secret`. Sessions are never signed with the
  public dev sentinel. **In production, set `ASTRODECK_SECRET` explicitly** so
  you manage the key. A fail-closed interlock refuses to mint/verify sessions
  if a method is enabled but only the dev default is available.
- **Cookies.** The session cookie (`ad_session`) is `HttpOnly`,
  `SameSite=Strict`, and **`Secure` on HTTPS** (auto-detected, honoring
  `X-Forwarded-Proto` behind a TLS-terminating proxy). The short-lived pre-auth
  cookie (`ad_oauth`) is `SameSite=Lax` so it survives the redirect back from
  Google.
- **Never expose to the WAN without a method enabled** (or at minimum
  `ASTRODECK_TOKEN`) — open = admin-for-all. For a local-only rig, bind
  loopback: `--host 127.0.0.1`.
- **Secrets are redacted** from the config API/WS broadcast:
  `google_client_secret`, `admin_token`, and `session_private_key` are blanked
  and replaced with `*_configured` booleans.
- **Revocation is append-only.** Logout appends the session `jti` to
  `revoked_jti` (killing that cookie on its next use); the registry can never be
  shrunk on save.

---

## 6. Quick Start

- **Local-only offline rig:** add `"local"` to `methods` → open the tablet →
  complete the first-run setup screen → manage the rest in the Users panel.
  Set `ASTRODECK_SECRET` and keep `ASTRODECK_TOKEN` handy for recovery.
- **Google web:** configure the Console (HTTPS redirect), fill the
  `google_*` fields, set `role_allowlist`, leave `default_role = null`, add
  `"google"` to `methods`.
- **Both:** enable both methods — the login UI shows the local form and the
  Google button.
