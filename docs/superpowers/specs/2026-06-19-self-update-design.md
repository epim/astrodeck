# AstroDeck Self-Update — Design Spec

- **Date:** 2026-06-19
- **Status:** Approved (design); pending implementation plan
- **Related:** `docs/relay-deploy.md`, `docs/architecture/2026-06-17-remote-access-architecture.md`, `docs/superpowers/specs/2026-06-16-pluggable-backends-rbac-remote-design.md`

## 1. Problem & goals

The AstroDeck **scope controller** (the main FastAPI server) runs at the telescope, behind NAT. CI can't push to it, so it must **pull** new releases from GitHub itself. We want the running server to:

1. Periodically check GitHub Releases for a newer version.
2. Surface an **Update-available dialog** (current vs new version, markdown release notes, **Upgrade / Defer**).
3. On admin consent: **download → verify → stage → restart into** the new version.
4. **Never** update mid-exposure/slew/sequence (rig-idle safety gate).
5. **Automatically roll back** if the new build does not come up healthy, so a bad release can't brick the scope — including while the operator is remote.

The **relay** is out of scope for self-update: it redeploys via Fly CI/CD (`deploy-relay.yml`). CI *pushes* to the relay; the scope *pulls*.

### Non-goals
- No silent/forced auto-apply. Auto-*check* is opt-in; **apply always requires admin consent**.
- No in-place file patching of a running process. We swap whole version directories while the server is down.
- The Python-era dependency handling (venv) is deliberately simple; the future Rust single-binary removes it.

## 2. Decisions (locked)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Restart mechanism | **Supervisor process** + exit-code + atomic dir-swap | Portable, no OS-specific magic, enables health-check + rollback, single-binary-ready |
| D2 | Release payload | **Prebuilt artifact + checksum** | UI built in CI; scope needs no node/build toolchain; clean version pinning |
| D3 | Artifact verification | **Ed25519 signature (minisign-compatible) + SHA256**, pinned public key | Auto-downloaded executable code → supply-chain integrity required |
| D4 | Remote apply | **Allowed, rollback-protected** + CI tests update+rollback **every release** | Rollback re-dials the relay on last-good → no remote lockout |
| D5 | OS target | **Windows-first now, Linux-native intent, single-binary-ready** | Current rig is Windows/NINA; roadmap is Linux/Rust |

**Architecture principle (from D5):** the supervisor + update runtime is a **thin, portable layer**. Today it wraps `python -m astrodeck` on Windows; it is structured so the future Rust/Go engine cross-compiles to a single static binary (Linux primary, Windows target). The UI is decoupled static web assets bundled into the artifact — only the engine/API needs the supervisor.

## 3. On-disk layout (scope install root)

```
<install-root>/
  supervisor/                 # portable supervisor (own component; future single binary)
  current                     # pointer FILE containing the active version string (NOT a symlink — Windows-portable)
  releases/
    0.1.0/                    # one dir per staged version (server source + ui/dist + manifest)
    0.2.0/
  venv/                       # Python venv (Python era only)
  state/
    last-good                 # last version that passed health-check
    pending-update.json       # written by server, read by supervisor (atomic write+rename)
    update-result.json        # written by supervisor after a swap; read by server on boot to surface outcome
    failed/                   # marker files for versions that failed health-check (never retried)
```

We use a **pointer file** (`current`) rather than a symlink: symlinks need privileges on Windows. The supervisor reads `current` to know which `releases/<v>/` to launch.

## 4. Components

### 4a. Version identity
- Source of truth: `server/astrodeck/__init__.py` `__version__` (kept in sync with `pyproject.toml`).
- New unauthenticated `GET /healthz` → `{"ok": true, "version": "x.y.z"}` (the supervisor health-checks this; the relay already expects a `/healthz` shape).
- New `GET /api/version` → `{current, latest_known, update_available, channel, last_check_ts}` (gated `view.status`).
- Semver compare helper; pre-release tags honored only when `channel == "prerelease"`.

### 4b. Release pipeline (CI/CD) — `.github/workflows/`
- **`release.yml`** (trigger: tag `v*`):
  1. Build UI: `npm ci && npm run build` → `ui/dist`.
  2. Bundle a version dir: `server/` source + `ui/dist` + `requirements.lock` + `manifest.json` (version, build time, sha256 of contents).
  3. Archive (`astrodeck-<version>.tar.gz`), compute **SHA256**, **sign** with the Ed25519 release key (`RELEASE_SIGNING_KEY` Actions secret, minisign-compatible).
  4. Publish a GitHub Release: artifact + `.sha256` + `.minisig` + the markdown release notes (the Release body, shown verbatim in the UI dialog).
- **`deploy-relay.yml`** (trigger: push to `main`, **path-filtered `relay/**`**): `superfly/flyctl-actions/setup-flyctl` → `flyctl deploy --remote-only` using `FLY_API_TOKEN`. One-time bootstrap (`fly launch`, volume, mounted secrets) stays manual; CI owns subsequent deploys.
- **`update-selftest`** (see §8): runs the full apply + rollback on a **Windows + Linux matrix** every release; release fails if rollback doesn't work.

### 4c. Supervisor — `supervisor/` (repo-root, portable)
A small standalone launcher. Responsibilities:
- Read `current` → launch `releases/<v>/` server as a child (Python era: `venv/python -m astrodeck …`).
- Watch the child:
  - **exit 0** → stop (operator-initiated shutdown); supervisor exits.
  - **exit 92 (APPLY_UPDATE)** → read `state/pending-update.json` → record the outgoing version as rollback target → set `current` to the new version → launch → **health-check** `GET /healthz` + `/api/version` for up to `HEALTH_TIMEOUT_S` (default 60). Healthy & version changed → write `state/last-good`, clear pending, write `update-result.json{ok:true}`. Unhealthy / child exits non-zero within stabilization window → **rollback**: set `current` = last-good, relaunch, write `failed/<v>` and `update-result.json{ok:false,reason}`.
  - **other non-zero / crash** → crash-recovery relaunch of the same version with capped backoff; if the crash occurs within the post-swap stabilization window, treat as failed update → rollback.
- Linux: wrapped by a systemd unit (`Restart=always`) later. Windows: runs as the launched process (optional NSSM wrapper documented).
- Pure exit-code + file protocol → **no OS-specific calls**; ports directly to the single-binary future.

### 4d. Update service — `server/astrodeck/update/`
- `github.py` — GitHub Releases API client (`GET /repos/<owner>/<repo>/releases/latest`, list for channel filtering). No auth needed for public repo; optional token for rate limits.
- `version.py` — semver parse/compare; current-vs-latest decision.
- `download.py` — streamed download of artifact + `.sha256` + `.minisig` to a temp dir, with progress callbacks (→ WS).
- `verify.py` — compute SHA256 and compare; verify the Ed25519 signature against the **pinned public key** using `cryptography` (no external binary at runtime). Reject on any mismatch.
- `stage.py` — unpack the verified archive into `releases/<version>/`; reconcile venv deps from `requirements.lock` (Python era).
- `protocol.py` — exit codes (`APPLY_UPDATE = 92`), `pending-update.json` / `update-result.json` schema, atomic write helpers.
- `service.py` — orchestrator + background poller task (started from the app lifespan, opt-in via config): poll cadence, holds "available" state, runs the apply pipeline, publishes WS events, requests graceful shutdown with exit 92. On boot, reads `update-result.json` to surface the outcome of the last attempt.

### 4e. Wiring to existing patterns
- **RBAC:** new `CAP_SYSTEM_UPDATE = "system.update"` in `auth/capabilities.py`, added to `ALL_CAPS` and the **admin** role only. Boot-time `@declare` assertion validates wiring (existing rbac.py mechanism).
- **Config:** `UpdateConfig` block + `ConfigStore.set_update_config()` (mirrors `set_remote`/`set_auth`), persisted via the existing atomic `_save()` + version-bump pattern.
- **API:** routes on `app.py`, each `Depends(require(CAP_SYSTEM_UPDATE))` + `@declare`:
  - `POST /api/update/check` → force a poll; returns latest status.
  - `GET /api/update/status` → `{current, latest, update_available, notes_md, state, progress, last_result}`.
  - `POST /api/update/apply` → **safety gate** (below) then `_spawn("system.update", …)`; returns `{"started": "system.update"}`. Progress over WS.
- **Safety gate (apply):** refuse with a structured reason if `hub.looping`, `hub.busy_label is not None`, mount slewing, or camera exposing. Response distinguishes "busy now" from "unsafe" so the UI can offer **Defer / apply when idle**. The gate is enforced at the home regardless of auth/relay (same posture as `hub._check_solar`).
- **WS:** `update` events — `{phase: "available"|"downloading"|"verifying"|"staging"|"applying"|"result", version, progress?, ok?, reason?}` published on `bus` and handled in the store.

### 4f. UI — `ui/src/`
- `components/UpdateDialog.tsx` — current vs new version, **rendered markdown** release notes, **Upgrade / Defer**, live progress bar, and a **"rig is busy — sequence running"** warning + disabled/hold-confirm Upgrade when the safety gate reports not-idle. Reuses `confirmDialog` hold-to-confirm for the destructive action.
- `components/settings/UpdatePanel.tsx` — toggle auto-check, interval, channel (stable/prerelease), shows current version + last check + last result. Follows the existing settings-panel load/draft/persist/rehydrate convention; gated by `useCan("system.update")`.
- `store.ts` / `ws.ts` — handle the `update` WS event; an available update raises a banner + opens the dialog.

### 4g. Runbook docs — `docs/infrastructure/`
By-hand setup for the whole stack (§9).

## 5. Update lifecycle (data flow)

```
poll (every check_interval_hours) ─► newer release? ─► WS "available" ─► UI dialog
                                                              │ admin: Upgrade
                                                              ▼
                                              safety gate (idle?) ──not idle──► refuse + Defer-when-idle
                                                              │ idle
                                                              ▼
            download(artifact,.sha256,.minisig) ─► verify(SHA256 then Ed25519 vs pinned pubkey)
                                                              │ ok
                                                              ▼
                       unpack ─► releases/<vN>/ ─► write state/pending-update.json (atomic)
                                                              │
                                                              ▼
                          graceful shutdown (lifespan teardown) ─► exit 92
                                                              │
                                                  ┌───────────┴───────────┐
                                                  ▼  supervisor           │
                            set current=vN ─► launch vN ─► health-check (/healthz + /api/version)
                                                  │                       │
                                  ┌───────────────┴────────┐              │
                                  ▼ healthy                 ▼ unhealthy/crash
                       last-good=vN, clear pending    rollback: current=last-good, relaunch,
                       update-result{ok:true}         failed/<vN>, update-result{ok:false,reason}
```

Remote apply uses the identical path; rollback to last-good (which re-dials the relay on boot) is what makes it safe to trigger from off-site.

## 6. Security & safety invariants
1. **Admin-only** (`system.update`) on every update route; **tunnel posture unchanged** — remote requests still re-auth `remote=True`, so the open `none` provider is hard-denied remotely.
2. **Rig-idle gate** enforced at the home below the API; an update can never interrupt an exposure/slew/sequence.
3. **Signature + SHA256 verified against a pinned public key** before any staged code can run; unsigned/tampered → hard reject. The **private signing key exists only in GitHub Actions**; the **public key is pinned in the scope** (`UpdateConfig.signing_pubkey` default, committed).
4. **Rollback is the remote-safety net** — a failed update restores last-good automatically; the failed version is marked and never retried.
5. **No new relay trust** — the relay still holds no signing secret and forwards only; it cannot forge an update or a principal.

## 7. Config schema (`UpdateConfig`)

| field | default | meaning |
|-------|---------|---------|
| `enabled` | `true` | master switch for the update subsystem |
| `auto_check` | `false` | poll GitHub on a timer (opt-in); apply is always manual |
| `check_interval_hours` | `24` | poll cadence when `auto_check` |
| `channel` | `"stable"` | `stable` ignores pre-release tags; `prerelease` includes them |
| `repo` | `"epim/astrodeck"` | release source |
| `signing_pubkey` | pinned default | Ed25519 public key (minisign-compatible) the scope verifies against |
| `health_timeout_s` | `60` | supervisor health-check window |
| `last_check_ts` | `null` | bookkeeping |

`redacted()` exposes nothing secret here (the public key is public). Written through `set_update_config()`, never the `extra="forbid"` `POST /api/config` merge.

## 8. Testing

**Unit/integration:**
- `verify`: valid sig accepts; bad signature, wrong key, bad SHA256, truncated artifact all reject.
- `version`: semver compare incl. pre-release + channel filtering.
- safety gate: refusal when looping/busy/slewing/exposing; reason codes correct.
- supervisor state machine: swap → healthy commit; swap → unhealthy rollback; crash-loop backoff; failed-version never retried (file-driven, no real network).
- API: RBAC denial for non-admin; apply spawns; status shape.

**`update-selftest` CI job (every release, Windows + Linux matrix):**
1. Boot an "old" version under the supervisor.
2. Point the update service at the just-built **signed** release (fixture/local server).
3. Trigger apply → assert: downloaded, **signature verified**, dir swapped, new `/api/version` matches, `/healthz` green.
4. **Negative tests:** (a) tampered artifact (bad signature) → apply refuses, no swap; (b) crash-on-boot build → supervisor **rolls back** to last-good, service recovers, version marked failed.
5. Release **fails** if the rollback path doesn't recover.

## 9. Runbook docs scope (`docs/infrastructure/`)
- **Relay deploy by hand** (cross-link `docs/relay-deploy.md`): `fly launch --no-deploy`, volume + mounted secret files, `fly secrets set`, `fly deploy`; then CI owns redeploys.
- **Running the supervisor** on Windows now (and the systemd unit for Linux later); manual update + manual rollback procedure.
- **Signing keys:** generate the Ed25519 release keypair, store the **private** key as the `RELEASE_SIGNING_KEY` Actions secret, commit/pin the **public** key; how to verify an artifact by hand with the `minisign` CLI; rotation.
- **All secrets inventory:** `FLY_API_TOKEN`, `RELEASE_SIGNING_KEY`, relay device tokens, OIDC/viewer seeds — where each lives and how to rotate.
- **GitHub Actions secrets are write-only:** you cannot read a secret's value back out. The source-of-truth value lives in a password manager (and/or Fly); recovery means **rotate** (`fly tokens create deploy`, new signing key) and overwrite the secret — never "retrieve."

## 10. Build sequence (phases)
1. **Release pipeline + signing + `/healthz` + `/api/version`** (no self-update is possible without signed releases to consume) — includes `deploy-relay.yml`.
2. **Supervisor + restart/rollback protocol** (+ its selftest harness).
3. **Update service + RBAC cap + `UpdateConfig` + API + safety gate + WS events.**
4. **UI dialog + settings panel.**
5. **Runbook docs.**

## 11. Future (single-binary)
When the engine is ported to Rust/Go, the venv/`requirements.lock` reconciliation in `stage.py` disappears: a release artifact becomes one cross-compiled binary per platform (Linux primary, Windows target) plus `ui/dist`. The supervisor — already pure exit-code + file protocol — is the natural first component to port, and its contract is unchanged.
