# Security OPEN items: triage and closure backlog (2026-09-05)

Source of truth for the ralph-loop closing out `SECURITY_REVIEW_FINDINGS.md`
OPEN-001..015. Every item resolves to exactly one terminal disposition:

- FIXED: code change landed, tests added, mapped CI job green locally.
- VERIFIED: Round 2 already implemented it; confirm tests exist and pass, then
  update the findings file to reflect the true state.
- ACCEPTED: genuinely out of scope for an autonomous software change (retail
  hardware, protocol redesign, or operational policy). Document the rationale
  and the mitigations already in place; not a code change.

The loop commits locally with explicit pathspecs only. It does NOT push and
does NOT deploy; both remain pending user decisions. When every row is terminal
and a full local CI dry-run is green, the loop completes.

## Status legend

`TODO` not started, `WIP` in progress, `DONE` terminal (disposition reached).

## Backlog

| ID | Sev | Disposition | Status | Acceptance criteria | CI job(s) |
|----|-----|-------------|--------|---------------------|-----------|
| OPEN-001 | High | ACCEPTED | TODO | Relay is trusted infra, not zero-trust. Document the threat boundary and existing mitigations (single-origin, cookie isolation, TLS termination at edge). e2e request encryption + device-bound keys recorded as an explicit future redesign, not attempted here. | n/a (docs) |
| OPEN-002 | High | FIXED (rotation/revocation) + ACCEPTED (mTLS) | DONE | Relay `revoke` (evicts live tunnel), `rotate` (seamless), `replace_tokens` + `reload_device_tokens` (durable file reload), SIGHUP handler; tokens never echoed in errors/logs (redaction tests). Server side already scrubs `device_token` in `redacted()` (test_remote_relay). mTLS/asymmetric identity recorded as future. | server, relay |
| OPEN-003 | High | VERIFIED (sw) + ACCEPTED (enclosure QR) | TODO | Confirm per-device AP credential, one-shot provisioning expiry, and physical-reset-to-reopen are implemented and tested on the Orange Pi provisioner. Printed/QR per-device bootstrap secret is retail-hardware; document. | orangepi-security |
| OPEN-004 | High | FIXED | DONE | New `reset_remote` opt-in (default OFF, third separately-labelled choice) clears `cfg.remote` (relay pairing incl. device_token) and scrubs `update.github_token`; ordinary resets still preserve pairing (QA handoff). Server `factory_reset`/`preview`/route + `FactoryResetPanel` transfer switch shown only when paired. Tests: test_factory_reset (20). Actual CI: server, ui. | server, ui |
| OPEN-005 | High | VERIFIED | TODO | Confirm `runtime_security.py` (server + relay), UID 10001 images, and `deploy/systemd/` drop privileges; tests assert non-root and least-capability. Never runs as root/Administrator. | container-security, server, relay |
| OPEN-006 | High | FIXED (audits+digest+SBOM) + ACCEPTED-future (hash-lock) | DONE | pip-audit (server/relay/deploy-relay), `npm audit --omit=dev` (ui), `cargo audit` (native) folded into existing CI jobs; all fail on a vuln, verified green locally. Base images pinned by digest (Dockerfile + relay/Dockerfile). CycloneDX SBOM published on release. Cross-platform hash-locked constraints deferred (documented). | ci, release |
| OPEN-007 | High | FIXED | DONE | `vendor_verify` hash-pins every bundled SDK binary in `vendor/manifest.json`; the ctypes loaders call `verify_if_vendored` before `CDLL`, so a tampered/planted binary under `vendor/` fails closed (a user's own installed SDK is not pinned). Manifest ships in package-data; a CI test fails on manifest drift. Tests: test_vendor_verify (7) + 748 camera/focuser/rotator green. | server |
| OPEN-008 | Med | FIXED (audit log) + VERIFIED (proxy/limits) | DONE | New `auth/audit.py` emits redacted `astrodeck.audit` lines (who/outcome/ip/reason, never a secret) wired into local login success/failure/rate-limit, break-glass token login, and first-run admin. Per-account rate limiting (LoginAttemptLimiter) and loopback-default bind already existed; TLS reverse proxy shipped in deploy/reverse-proxy/. Tests: test_auth_audit (5) + 87 auth-route green. | server |
| OPEN-009 | Med | FIXED (policy test) + VERIFIED (interlock) | DONE | test_deployment_entrypoint_policy (5) pins that every shipped unit/Docker CMD launches `python -m astrodeck run` / `python -m relay` and no shipped definition binds uvicorn directly. deploy/systemd/README documents custom ASGI hosting as unsupported. The non-loopback interlock (exit 2) already existed. | server |
| OPEN-010 | Med | VERIFIED | TODO | Confirm `windows_acl.py` creates + verifies a private DACL (no broad Users/Everyone read), rejects reparse/junctions; the windows-private-state CI job gates it. | windows-private-state |
| OPEN-011 | Med | FIXED | DONE | New `auth/ws_ticket` single-use short-TTL store + `POST /api/auth/ws-ticket` (auth via cookie/header, never a query); the /ws gate accepts `?ticket=` (one-time) so a long-lived token need not ride the URL. UI already connects by session cookie. Query-token redaction already handled: nginx logs `$uri` (path only), uvicorn access-log off behind the proxy, relay strips `?token=`. Tests: test_ws_ticket (8) + 22 auth/loopback green. | server |
| OPEN-012 | Med | FIXED | DONE | The one pre-paint inline script moved to ui/public/bootstrap.js (external, blocking, served 'self'); CSP `script-src` drops `'unsafe-inline'`. style-src keeps it as a documented exception (React/Vite runtime styles). Tests: test_csp_headers (2) + ui cspBootstrap (3); build confirms dist/index.html has no inline script. | server, ui |
| OPEN-013 | Low | FIXED | DONE | `cargo audit` runs on every native CI build (folded into OPEN-006); plain audit keeps the `paste` unmaintained warning release-visible (exit 0) while failing on any real vuln. Tracked to its nalgebra origin, removed when upstream drops it. | native, ci |
| OPEN-014 | Low | ACCEPTED | TODO | Single-home-per-origin is the intended safe design after cookie isolation. Document it; record explicit flow-control/fair-scheduling as a future step before any high-density multi-tenant hosting. | n/a (docs) |
| OPEN-015 | Low | FIXED | DONE | The code already uses top-level `websockets.connect`, which is the modern asyncio impl on websockets>=14; raised the floor to `websockets>=14.0` (server pyproject now declares it directly; relay pyproject + requirements) so no legacy default can resolve, and added test_websockets_modern (connect is asyncio, floors >=14). Dep-audit gates (OPEN-006) keep it on a scanned release. | relay, server |

## Per-iteration playbook (for the loop)

1. Pick the highest row whose Status is not DONE (High severity first, then Med, then Low; within a severity, FIXED before VERIFIED before ACCEPTED so code lands before doc-only closes).
2. Apply systematic-debugging / TDD: write the failing test first, implement the minimal change, verify it passes, then run the mapped CI job(s) locally against `server/.venv`.
3. Commit locally with explicit pathspecs (`git commit -- <paths>`); no push, no deploy.
4. Update this table's Status to DONE and mirror the resolution into `SECURITY_REVIEW_FINDINGS.md`.
5. When all rows are DONE, run the full local CI dry-run; if green, the loop is complete.

## Out of scope for this loop (follow-on)

- `AstroDeck-review3-codex.md` UX field-review ("Top 10 by user pain").
- The 8 unverified NEW audit findings at the bottom of the broken-promises audit backlog.
