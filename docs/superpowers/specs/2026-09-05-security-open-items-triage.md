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
| OPEN-002 | High | FIXED (rotation/revocation) + ACCEPTED (mTLS) | TODO | Add device-token rotation + revocation path with tests: an operator can rotate the home/relay bearer and immediately invalidate the old one. Asymmetric/mTLS identity recorded as future. Tokens never logged (assert via redaction tests). | server, relay |
| OPEN-003 | High | VERIFIED (sw) + ACCEPTED (enclosure QR) | TODO | Confirm per-device AP credential, one-shot provisioning expiry, and physical-reset-to-reopen are implemented and tested on the Orange Pi provisioner. Printed/QR per-device bootstrap secret is retail-hardware; document. | orangepi-security |
| OPEN-004 | High | FIXED | TODO | Factory reset clears remote pairing/credentials by default, OR a separate, clearly-named transfer reset does so. Test: after the reset, no relay pairing/credential material remains. Document ownership-transfer semantics. | orangepi-security, server |
| OPEN-005 | High | VERIFIED | TODO | Confirm `runtime_security.py` (server + relay), UID 10001 images, and `deploy/systemd/` drop privileges; tests assert non-root and least-capability. Never runs as root/Administrator. | container-security, server, relay |
| OPEN-006 | High | FIXED | TODO | Add `pip-audit`, `npm audit --omit=dev`, `cargo audit` as CI gates that fail on vulns (preserving the one tracked maintenance warning). Pin container base images by digest. Generate hash-locked constraints where feasible; emit an SBOM. | ci (new deps-audit), release |
| OPEN-007 | High | FIXED | TODO | Verify bundled native SDK binaries against the signed release manifest (hash check) before the server uses them; a tampered/unknown binary fails closed with a clear error. Record vendor provenance + hashes. | server, native |
| OPEN-008 | Med | FIXED (audit log) + VERIFIED (proxy/limits) | TODO | Add a redacted security audit log for auth events (login success/failure, token use, rotation). Confirm per-account login rate limiting and loopback-default bind. Document the TLS reverse proxy as the required front for non-loopback. | server |
| OPEN-009 | Med | FIXED (policy test) + VERIFIED (interlock) | TODO | Add a deployment-policy test asserting shipped service definitions invoke the guarded `python -m astrodeck run` entrypoint (not a raw `uvicorn --host 0.0.0.0`). Document that custom ASGI hosting is unsupported. Non-loopback interlock already exits 2. | server |
| OPEN-010 | Med | VERIFIED | TODO | Confirm `windows_acl.py` creates + verifies a private DACL (no broad Users/Everyone read), rejects reparse/junctions; the windows-private-state CI job gates it. | windows-private-state |
| OPEN-011 | Med | FIXED | TODO | Replace the WS shared-token query-string fallback with a short-lived, single-use ticket obtained over an authenticated POST (or first-message auth). Redact token query strings in all logs. Prefer session cookies. | server |
| OPEN-012 | Med | FIXED | TODO | Serve a CSP nonce/hash for scripts and remove `unsafe-inline` for `script-src`. Inline bootstrap moved to static or nonce-tagged. Style-src may retain a documented exception if React requires it. | server, ui |
| OPEN-013 | Low | FIXED | TODO | `cargo audit` runs release-visible (folded into OPEN-006). `paste` unmaintained crate tracked to its nalgebra origin with a removal-when-feasible note. | native, ci |
| OPEN-014 | Low | ACCEPTED | TODO | Single-home-per-origin is the intended safe design after cookie isolation. Document it; record explicit flow-control/fair-scheduling as a future step before any high-density multi-tenant hosting. | n/a (docs) |
| OPEN-015 | Low | FIXED | TODO | Migrate the Uvicorn/WebSockets integration off the deprecated API so the relay + server emit no legacy deprecation warnings; keep on a scanned supported release. | relay, server |

## Per-iteration playbook (for the loop)

1. Pick the highest row whose Status is not DONE (High severity first, then Med, then Low; within a severity, FIXED before VERIFIED before ACCEPTED so code lands before doc-only closes).
2. Apply systematic-debugging / TDD: write the failing test first, implement the minimal change, verify it passes, then run the mapped CI job(s) locally against `server/.venv`.
3. Commit locally with explicit pathspecs (`git commit -- <paths>`); no push, no deploy.
4. Update this table's Status to DONE and mirror the resolution into `SECURITY_REVIEW_FINDINGS.md`.
5. When all rows are DONE, run the full local CI dry-run; if green, the loop is complete.

## Out of scope for this loop (follow-on)

- `AstroDeck-review3-codex.md` UX field-review ("Top 10 by user pain").
- The 8 unverified NEW audit findings at the bottom of the broken-promises audit backlog.
