# AstroDeck security review findings

Review dates: 2026-08-27 to 2026-08-28

This file records every security-relevant defect and material residual risk
identified during this review of the AstroDeck server, browser API, outbound
WebSocket relay, native Rust extension, update path, containers, and Orange Pi
first-boot provisioner. It also records the clear non-security defects noticed
along the way.

`Fixed` below means fixed in this working tree. It does not mean the change has
been committed, released, independently penetration-tested, or deployed.

## Executive result

The original code was not safe to distribute as an internet-facing controller.
The most serious paths were an unauthenticated non-loopback listener, mutable
self-update trust roots, fail-open authentication initialization, relay access to
host/LAN-sensitive administration routes, and several unbounded relay/resource
paths. Those issues are fixed in this working tree.

Current dependency scans report no known vulnerabilities in the installed
Python or JavaScript dependency sets or the Rust lockfile. One unmaintained Rust
transitive macro crate remains and is listed under residual risks.

The remaining high-impact items are architectural or deployment decisions, not
small patches: the relay is still a trusted bearer-token intermediary; the setup
access point uses a public factory password; factory reset preserves remote
pairing; and bare-metal processes still need OS-level least privilege. Do not
advertise the relay as zero-trust or end-to-end encrypted until that architecture
changes.

## Fixed security defects

| ID | Severity | Finding and impact | Remediation in this tree |
|---|---:|---|---|
| SEC-001 | Critical | The normal server command could listen beyond loopback with no authentication, exposing mount motion, power, captures, and administrative APIs to the LAN. | Loopback is now the default. The CLI and supervised entry paths refuse an unauthenticated non-loopback bind unless the operator uses an explicitly named insecure-development override. Docker deployment requires a token. |
| SEC-002 | Critical | Authentication-provider initialization and malformed persisted auth settings could fail open to the all-powerful `none` provider. A typo or startup exception could silently make a protected rig public. | Provider initialization and invalid persisted methods/provider/TTL now fail closed to a provider that grants no principal. Regression tests cover malformed persisted configuration and initialization failure. |
| SEC-003 | Critical | An authenticated web request could replace both the update repository and Ed25519 public key. That made admin-panel compromise equivalent to arbitrary signed-code execution as the AstroDeck OS account. | Update repository and signing public key are offline-only trust roots. The API may no longer set or rotate them, update mutation requires configured authentication, and update routes are direct-only rather than relay-accessible. |
| SEC-004 | High | Creating an administrator did not enable local authentication, leaving the open-admin provider active and giving a false impression that the instance had become protected. | `create-admin` now persists `local` as an enabled auth method; tests cover new and reset accounts. |
| SEC-005 | High | Multiple homes behind one relay HTTP origin shared the browser cookie namespace. A cookie established for one home could be sent to another home endpoint. | The relay now enforces one configured home per process/origin and rejects mismatched home routing. Multi-home deployments must use separate hostnames/processes. |
| SEC-006 | High | The remote tunnel could reach identity management, relay configuration, factory reset, update, discovery, sync, driver/profile/config mutation, arbitrary alert destinations, connection setup, and other host/LAN-sensitive surfaces. A compromised relay or remote admin session could use the home as an SSRF or local-resource foothold. | Security-sensitive exact routes and route families are now direct-only. Remote mutation of config, alerts, drivers, profiles, connections, and survey packs is blocked before route dispatch. |
| SEC-007 | High | The direct shared token was accepted as a tunneled credential and could cross the relay trust boundary. | Authorization, `X-Auth-Token`, and token query values are stripped from tunneled requests. A remote scope must use a home-verified user session; the unauthenticated provider denies remote scopes. |
| SEC-008 | High | Plain `ws://` relay transport could expose device tokens, sessions, commands, and images in transit. The home also treated a sent HELLO as success without a positive authentication acknowledgement. | Non-loopback relays require `wss://`; plaintext is limited to loopback development. The protocol now has an authenticated, timeout-bounded `HELLO_ACK`, and the home remains local-only until it receives the acknowledgement. |
| SEC-009 | High | Weak, short device bearer tokens were accepted, and a latent runtime development-HMAC signer made accidental insecure deployment possible. | Device tokens must contain at least 32 bytes of secret material and are compared in constant time. Runtime startup no longer creates an implicit development signer; Ed25519 signing is available only with an explicit seed. Test-only HMAC helpers remain test-only. |
| SEC-010 | High | Relay request bodies, direct server bodies, frame metadata, per-home tasks, streams, response buffering, and socket queues had missing or ineffective bounds. Attackers could exhaust memory, tasks, sockets, or the event loop. | Direct and tunneled HTTP bodies are capped at 8 MiB, including chunked/lying bodies. Frame headers, WebSocket messages/queues, concurrent streams/tasks, per-stream response queues, total buffered response bytes, concurrency, backlog, keep-alive, and incomplete HTTP events are bounded. Over-limit transfers fail and close rather than drain indefinitely. |
| SEC-011 | High | Body forwarding buffered large payloads before enforcing limits and had incomplete timeout/cancellation handling. A stalled sender could hold resources; an over-limit body could use the configured maximum as an allocation target. | Request and response bodies now stream through bounded queues with read, write, idle, header, and overall deadlines. Limits are enforced while reading, and disconnect/abort paths cancel and reap work. |
| SEC-012 | High | An aborted tunneled request could continue running in the home ASGI app and emit late data into a reused stream. | Abort now fences the captured tunnel generation, cancels the app task, drains/reaps it, and suppresses writes after closure. |
| SEC-013 | High | Reconnect races allowed stale generations or old sockets to unregister/overwrite newer live tunnels, and equality was accepted in places where only a strictly newer generation was safe. | Generation fencing is strict and restart-surviving. Higher generations evict and close the old WebSocket; equal/lower generations are rejected; unregister and send operations are conditional on the exact registration/socket that created them. |
| SEC-014 | High | Cross-origin browser requests and WebSocket handshakes were not consistently rejected. A malicious page could attempt authenticated state changes or cross-site WebSocket hijacking against a local/LAN AstroDeck. | Unsafe browser requests require an exact effective-origin match when `Origin` is present and reject cross-site/same-site Fetch Metadata. WebSocket handshakes apply the same exact-origin policy. Session cookies use hardened attributes. |
| SEC-015 | High | The root Orange Pi provisioning portal accepted state-changing GET/POST actions without CSRF protection, accepted unbounded form bodies, allowed concurrent Wi-Fi reconfiguration, and wrote attacker-controlled SSID/PSK values into network configuration without adequate validation/escaping. It runs as root. | Connect and rescan are CSRF-protected POSTs; request bodies are capped at 4 KiB; SSID/PSK lengths and control characters are validated; YAML/WPA values are safely encoded; join/scan operations are serialized; HTTP/DNS concurrency is bounded; and the portal binds only the setup interface. |
| SEC-016 | High | Persisted sessions, auth secrets, device tokens, and configuration used predictable temporary names and did not consistently repair broad POSIX modes. Local users could potentially read secrets or interfere with replacement files. | Atomic writes use unpredictable `mkstemp` files, `fsync`, and replacement. Secret/config files and backups are forced to mode `0600` and private directories to `0700` on POSIX; existing files are repaired on read. Windows remains dependent on directory ACLs, noted below. |
| SEC-017 | High | Current dependency resolution included known advisories in `cryptography`, Pillow, Starlette, PyO3, and `crossbeam-epoch`; old minimums allowed those vulnerable versions to be installed. | Python minimums now require FastAPI 0.141.1, Starlette 1.6.0, Pillow 12.3.0, and cryptography 50.0.0. Relay minimums match. PyO3/NumPy bindings moved to 0.29 and `crossbeam-epoch` to 0.9.20; the binding was migrated and rebuilt. Python, npm, and Rust vulnerability scans are now clear. |
| SEC-018 | Medium | Proxy mappings were published before registration completed and were not rolled back transactionally, leaking streams/mappings on partial failure. | Mapping publication and cleanup are transactional and conditional on ownership. Partial setup failures roll back without deleting a replacement stream. |
| SEC-019 | Medium | Response headers/status metadata could contain invalid values, hop-by-hop headers, duplicate server defaults, or CR/LF injection opportunities. Host/cookie rewriting was too permissive. | Header names/values and status are validated; hop-by-hop and proxy-auth headers are removed; malformed metadata fails closed; response raw headers are assigned exactly once; Host, Origin, cookie, and redirect rewriting use strict rules. |
| SEC-020 | Medium | Relay health output disclosed configured home identifiers. | Health output now reports aggregate state without home IDs. |
| SEC-021 | Medium | Configured limits accepted zero, negative, NaN, or otherwise inert values, turning documented controls off or producing inconsistent behavior. | Configuration parsing validates finite positive values and coherent min/max relationships at startup. |
| SEC-022 | Medium | Disabling remote access in persisted configuration did not reliably stop an already running client until restart. | The home watches the remote configuration and promptly stops/restarts the tunnel when the enablement or trusted parameters change. |
| SEC-023 | Medium | Browser responses lacked a consistent security-header baseline and Uvicorn defaults left generous resource exposure. | Responses now set CSP, frame denial, MIME sniffing protection, referrer and permissions policies, and HSTS on HTTPS. Uvicorn concurrency, backlog, keep-alive, HTTP parser, WebSocket message, and WebSocket queue limits are explicit. |
| SEC-024 | Medium | The privileged first-boot provisioner wrote logs/state/network material with broad defaults and had a large root service sandbox. | Files are private, the state directory is `0700`, and the systemd unit now uses a restrictive umask, no-new-privileges, filesystem/home/tmp isolation, capability and syscall restrictions, and a narrow writable-path set. |
| SEC-025 | Medium | A slow response on one tunneled stream could head-of-line block other streams or grow aggregate buffering. | Each stream has an independently bounded response queue and aggregate byte accounting; the socket writer multiplexes bounded frames and times out slow transfers. |
| SEC-026 | Low | Relay proxy/config documentation contained unsafe short-token examples and implied signing secrets were required even though the exposed relay routes do not use those signers. | Examples now generate strong random tokens; deployment docs describe the actual minimal secret set and trusted-relay boundary. |

## Open architectural and deployment risks

These are not silently accepted. They need an explicit product or deployment
decision before a broad release.

| ID | Risk | Rating | Required action |
|---|---|---:|---|
| OPEN-001 | The relay terminates TLS and sees the home-signed session cookie. Relay compromise can observe and replay that bearer session for remotely allowed actions. The tunnel is not end-to-end encrypted. | High | Operate the relay as trusted infrastructure with a dedicated hostname, minimal logs, patching, network isolation, and secret rotation. For a zero-trust relay, redesign around end-to-end request encryption plus device-bound/mutually authenticated keys. |
| OPEN-002 | Each home uses a long-lived bearer device token. Theft lets an attacker impersonate that home to its relay until rotation. | High | Add rotation/revocation UX and preferably replace static bearer pairing with per-device asymmetric identity or mTLS. Never log or bake tokens into images. |
| OPEN-003 | The first-boot Wi-Fi AP password is the public factory value `astrodeck`. Anyone within radio range during provisioning can join the AP and submit their own network. CSRF hardening does not prove physical possession. | High | Before retail distribution, issue a unique per-device bootstrap password/QR code printed on the enclosure or displayed locally. Automatically expire provisioning mode and require a physical reset gesture to reopen it. |
| OPEN-004 | Factory reset intentionally preserves relay configuration/pairing. On resale or transfer, the previous relay operator may retain a path to future traffic. | High | Define ownership-transfer semantics. Prefer clearing remote pairing and credentials on factory reset, or provide a separate unmistakable transfer reset that does so and document it prominently. |
| OPEN-005 | Bare-metal AstroDeck has no application sandbox. Any future server/native-driver compromise inherits the OS account's filesystem, device, and network privileges; running as Administrator/root turns that into base-OS compromise. | High | Run the main server and relay as dedicated unprivileged accounts or rootless containers. Grant only required serial/USB device ACLs/groups and capture/config directories. Never run the general server as root/Administrator. The provisioning helper is the narrowly sandboxed exception. |
| OPEN-006 | The Python service/container dependency set has secure minimums but is not fully hash-locked across Windows, Linux, amd64, and arm64; base image tags are mutable. A future resolver or compromised registry could alter a release. | High | Generate platform-specific, hash-checked lock/constraints files in CI; pin container base images by digest; produce an SBOM; sign artifacts/images; and run dependency scans as release gates. |
| OPEN-007 | Bundled third-party native SDK DLLs/shared objects execute inside the server process and are not re-verified against a signed manifest at runtime. | High | Record vendor provenance and hashes, scan every shipped binary, verify the signed release manifest before launch/update, and isolate device drivers out-of-process where practical. |
| OPEN-008 | The built-in server does not provide TLS, comprehensive rate limiting, slow-header defense, or a security audit log. Local password and token endpoints can be brute-forced or flooded if directly exposed. | Medium | Put a maintained TLS reverse proxy/firewall in front of any non-loopback listener; enforce connection/header/body deadlines, per-IP/account limits, and redacted audit logs there. Bind AstroDeck itself to loopback where possible. |
| OPEN-009 | Launching the ASGI app directly with a third-party `uvicorn ... --host 0.0.0.0` command bypasses the AstroDeck CLI's non-loopback interlock. | Medium | Ship and document only the guarded AstroDeck entrypoint. Service definitions must invoke `python -m astrodeck run`; add deployment tests/policy checks if custom ASGI hosting is supported. |
| OPEN-010 | Windows secret protection relies on the ACL inherited from `%LOCALAPPDATA%`; the code does not create or verify a private DACL. | Medium | Installer/service setup must create the config directory for the dedicated service user and verify no broad Users/Everyone read access. Add Windows ACL enforcement and tests if multi-user Windows hosts are supported. |
| OPEN-011 | The browser WebSocket shared-token fallback puts the token in a query string because the browser API cannot add an Authorization header. URLs can enter history, telemetry, or proxy logs. | Medium | Prefer user-session cookies. If the bootstrap token must remain, exchange it over an authenticated HTTPS POST for a short-lived, single-use WebSocket ticket or authenticate in the first WebSocket message. Redact query strings everywhere. |
| OPEN-012 | CSP still contains `unsafe-inline` for scripts/styles because the current SPA shell and React styling need it. This weakens CSP against an HTML/script injection bug. | Medium | Move inline bootstrap/style code to static files or deploy nonces/hashes, then remove `unsafe-inline`. |
| OPEN-013 | `paste` 1.0.15 is an unmaintained transitive Rust macro dependency (RUSTSEC-2024-0436), although no vulnerability is reported. | Low | Track the upstream nalgebra dependency or replacement and remove the unmaintained crate when feasible. Keep `cargo audit` warnings release-visible. |
| OPEN-014 | The relay intentionally supports only one home per public origin/process after the cookie-isolation fix. This is safe but limits scale, and per-stream fairness remains an availability concern under abusive authenticated clients. | Low | Use one hostname/process or a trusted edge partition per home. Add explicit flow-control windows/fair scheduling before offering high-density multi-tenant hosting. |
| OPEN-015 | Current Uvicorn/WebSockets integration emits legacy API deprecation warnings. This is not an active vulnerability but is a future upgrade risk. | Low | Migrate the integration before the legacy implementation is removed and keep WebSocket libraries on a scanned supported release. |

## Clear non-security defects observed

1. Several tests are marked async while implemented synchronously, producing
   pytest warnings and making intent unclear.
2. Busy-lane tests produce `coroutine was never awaited` warnings for a test
   helper. That can conceal a real missed-await regression in similar code.
3. The old Orange Pi join-lock release path was split across success/failure
   branches and was fragile against double-release or leaks during future edits;
   it is now one `finally` path.
4. Response raw headers were extended onto framework defaults, producing
   duplicate headers in some relay responses; assignment now replaces defaults.

## Non-security distribution blockers

The dependency-credit generator also surfaced three licensing/provenance issues.
They are not security vulnerabilities, but they are reasons not to publish the
current bundle until the owner makes and records a decision:

1. Astrospheric's published terms appear to limit API access to Professional
   members' personal projects and do not clearly grant redistribution rights for
   a public product. Obtain written permission or remove the integration.
2. The bundled DSS/DSS2 color survey imagery is copyrighted, and the published
   permission located by the project covers non-profit research/teaching use,
   not third-party redistribution of the offline pack. Obtain written STScI
   permission or replace it with openly licensed imagery.
3. The Player One Camera SDK text does not clearly grant redistribution of the
   six bundled compiled binaries. Obtain written vendor confirmation or require
   users to install the vendor SDK separately.

## Verification performed

- Complete server/appliance run: 6,234 passed and 28 skipped. Its sole failure
  was expected generated credits drift after the patched Rust lockfile changed;
  the credits were regenerated, and the credits/provisioning verification lane
  then passed 29/29.
- Focused authentication, persistence, origin, request-bound, remote-tunnel,
  update, and provisioning security tests pass.
- Relay suite: 111 passed, 5 dependency deprecation warnings.
- Native Rust workspace: all unit, integration, property, and doc tests pass
  after the PyO3/NumPy migration.
- The release-optimized native Python wheel was rebuilt and its native
  guider/autofocus tests pass in the complete server gate. A debug-only run hit
  one performance timeout; the identical test passed in 11 seconds after the
  release build replaced it.
- UI tests: 2,542 passed across 207 files; production TypeScript/Vite build
  succeeds (with non-fatal chunk-size/dynamic-import warnings).
- `pip-audit --local`: no known vulnerabilities after the dependency upgrade.
- `npm audit --omit=dev`: 0 vulnerabilities.
- `cargo audit`: 0 vulnerabilities; one unmaintained transitive crate warning
  (OPEN-013).
- `cargo fmt --check` and `git diff --check` pass.
- Independent CI on every release target is still required immediately before
  release. Test success is evidence, not proof of security.

## Release-blocking deployment checklist

1. Commit/review these changes and rerun every CI/release job on Windows, Linux
   amd64, Linux arm64, Raspberry Pi 5, and Orange Pi 5 images.
2. Do not publish the relay until it is behind HTTPS/WSS with exact Host/Origin
   routing, a single home origin, strong unique tokens, bounded proxy timeouts,
   rate limits, and redacted logs.
3. Run the server/relay as dedicated non-admin users. Give hardware access with
   narrow device ACLs rather than account-wide elevation.
4. Resolve OPEN-003 and OPEN-004 before shipping a consumer appliance.
5. Add `pip-audit`, `npm audit`, and `cargo audit` to release CI and fail on
   vulnerabilities. Preserve the one maintenance warning as a tracked issue.
6. Commission an independent penetration test of the public relay, update
   mechanism, first-boot portal, and ownership-transfer flow before claiming
   internet-safe or zero-trust operation.

Reference design guidance used during remediation:

- [OWASP WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html)
- [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [RustSec RUSTSEC-2026-0177 (PyO3)](https://rustsec.org/advisories/RUSTSEC-2026-0177.html)
- [RustSec RUSTSEC-2026-0204 (crossbeam-epoch)](https://rustsec.org/advisories/RUSTSEC-2026-0204.html)

## OPEN-item closure log (2026-09-05)

Tracked in docs/superpowers/specs/2026-09-05-security-open-items-triage.md.
Each entry: disposition then what shipped. Committed locally; not yet pushed.

- OPEN-002 FIXED (rotation/revocation) + ACCEPTED (mTLS as future). The relay
  gained `HomeRegistry.revoke` (drops the mapping and evicts the live tunnel, so
  a leaked token dies at once), `rotate` (atomic swap that keeps the live tunnel
  for a planned rotation), and `replace_tokens` + `config.reload_device_tokens`
  for a durable file reload, wired to SIGHUP in the server lifespan. A shared
  `config.valid_device_token` governs the format everywhere and
  `config.new_device_token` mints one. No error, log, or registration repr
  echoes token material (redaction tests in relay/tests/test_token_lifecycle.py).
  The home already scrubs `device_token` in `redacted()`
  (server test_remote_relay::test_device_token_redacted). Operator flow:
  relay/README. Asymmetric/mTLS device identity remains a documented future
  redesign; the accepted blast radius of a leaked static token is unchanged.

- OPEN-004 FIXED (ownership transfer). Factory reset gained a third opt-in,
  `reset_remote` (default OFF at module, route, and UI). When chosen it clears
  `cfg.remote` (relay pairing including the device_token) and scrubs
  `update.github_token`, keeping the PUBLIC signing key/repo/channel so the new
  owner's self-update still verifies. Ordinary resets still preserve pairing so a
  QA handoff within one org does not orphan a remotely-managed box. The
  FactoryResetPanel shows the transfer switch only when the box is actually
  paired (`preview.remote_paired`) and the confirm dialog states the loss.
  Tests: server/tests/test_factory_reset.py (transfer clears, non-transfer
  preserves, preview reports pairing, route passes it through).

- OPEN-006 FIXED (audit gates + digest pins + SBOM) with hash-locked constraints
  ACCEPTED as future. Dependency vulnerability gates now run on every CI build
  and fail on a known-vulnerable dependency: pip-audit (server, relay, and the
  relay deploy gate), `npm audit --omit=dev` (ui), `cargo audit` (native). All
  four verified green locally. Container base images are pinned by digest in
  Dockerfile and relay/Dockerfile (python:3.12-slim, node:20-slim). A CycloneDX
  SBOM is generated and published alongside each GitHub Release. Cross-platform
  hash-locked constraints (Windows/Linux x amd64/arm64) and cosign image signing
  are recorded as future hardening; the artifact tarball is already Ed25519-signed.
- OPEN-013 FIXED. `cargo audit` runs on every native CI build. Plain audit keeps
  the `paste` 1.0.15 unmaintained advisory (RUSTSEC-2024-0436) visible in the log
  and exit-0, while any real vulnerability fails the build. Removed when the
  nalgebra dependency chain drops the crate.

- OPEN-007 FIXED. The bundled ZWO / Player One SDK binaries run in-process via
  ctypes; a new `astrodeck.devices.vendor_verify` module pins each one's SHA-256
  (and size + vendor) in `astrodeck/vendor/manifest.json`, and the three loaders
  (player_one_sdk, zwo_asi_sdk, zwo_sdk) call `verify_if_vendored(path)` before
  `ctypes.CDLL`. A tampered or planted binary UNDER `vendor/` fails closed with a
  clear error before it can execute; a user's own separately-installed SDK
  (ASTRODECK_*_SDK_DIR or a system path) is out of our provenance and is not
  hash-pinned. The manifest ships in package-data (covered by the Ed25519
  release signature), and a CI test (test_vendor_verify) fails if a bundled
  binary is updated without regenerating the manifest
  (`python -m astrodeck.devices.vendor_verify --write`). Out-of-process driver
  isolation remains a future option; the in-process check is the reachable
  control today.

- OPEN-008 FIXED (audit log) + VERIFIED (rate limit / loopback / proxy). New
  astrodeck/auth/audit.py emits a redacted line to the dedicated `astrodeck.audit`
  logger (and the event bus) for each security-relevant auth event -- local login
  success/failure, rate-limited login, break-glass token login, and first-run
  admin creation -- recording who/outcome/ip/reason and NEVER a password or
  token (proven by test_auth_audit). Per-account login rate limiting
  (LoginAttemptLimiter) and the loopback-default bind (python -m astrodeck, exits
  2 on an unauthenticated non-loopback bind) already existed; a maintained TLS
  reverse proxy ships in deploy/reverse-proxy/ and is the required front for any
  non-loopback listener.
