# AstroDeck Infrastructure Runbook

Operator-facing guide to standing up and maintaining the whole AstroDeck stack
**by hand** — no magic, no hidden state. If a script or CI job is unavailable,
everything here can be done manually.

The stack has three deployables:

| Piece | Where it runs | How it updates |
|-------|---------------|----------------|
| **Scope controller** (the FastAPI server + React UI) | a computer at the telescope, behind NAT | **pulls** signed GitHub Releases and self-updates ([self-update.md](self-update.md)) |
| **Relay** (optional) | a small cloud VM (Fly.io) | CI **pushes** via `flyctl deploy` ([ci-cd.md](ci-cd.md)), or deploy by hand ([../relay-deploy.md](../relay-deploy.md)) |
| **UI** | served by the scope controller itself (static `ui/dist`) | ships inside the controller's release bundle |

Design rationale lives in
`docs/superpowers/specs/2026-06-19-self-update-design.md` and the remote-access
ADR `docs/architecture/2026-06-17-remote-access-architecture.md`.

---

## Bring-up order (from nothing)

1. **Generate the release signing keypair** — one time, ever.
   `python scripts/gen_signing_key.py`. Keep the **private seed** in a password
   manager; you'll paste it into CI. See [secrets.md](secrets.md).
2. **Set up CI secrets** (if you use GitHub Actions): `RELEASE_SIGNING_KEY` (the
   private seed) and, for relay auto-deploy, `FLY_API_TOKEN`. See [ci-cd.md](ci-cd.md).
3. **Cut the first release** — bump the version, tag `vX.Y.Z`, push the tag; the
   `release` workflow builds + signs + publishes. Or build by hand ([self-update.md](self-update.md)).
4. **Install the scope controller under the supervisor** and pin the signing
   **public key** in its update config. See [self-update.md](self-update.md).
5. **(Optional) Deploy the relay** for remote access. See [../relay-deploy.md](../relay-deploy.md)
   and [ci-cd.md](ci-cd.md).

---

## The golden rules

- **GitHub Actions secrets are write-only.** You cannot read a secret's value
  back out of GitHub — ever. The source-of-truth copy lives in your password
  manager (and/or the upstream system, e.g. Fly). Recovery means **rotate**, not
  retrieve. This is spelled out per-secret in [secrets.md](secrets.md).
- **A release must be signed.** The scope refuses any artifact that doesn't
  verify (Ed25519 + SHA256) against the pinned public key. No key pinned ⇒ no
  self-update (fail-closed).
- **Updates never interrupt imaging.** Apply is blocked while a sequence runs or
  the mount is slewing/exposing (`hub.restart_blocker`), and a failed update
  **rolls back automatically** to the last-good version.
- **The relay is untrusted.** It forwards bytes only; it holds no signing secret
  and cannot forge a principal or push an update. See the relay deploy doc's
  "Security model" section.

---

## Documents in this folder

- **[self-update.md](self-update.md)** — the supervisor, cutting/signing a
  release, manual update + rollback, troubleshooting.
- **[secrets.md](secrets.md)** — every secret in the system: where it lives, how
  to rotate it, and why you can't read GitHub secrets back.
- **[ci-cd.md](ci-cd.md)** — the three GitHub Actions workflows and their
  one-time bootstrap.
- **[../relay-deploy.md](../relay-deploy.md)** — full relay deploy walkthrough +
  security model.
