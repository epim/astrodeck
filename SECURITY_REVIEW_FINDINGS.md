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

- OPEN-009 FIXED (policy test) + VERIFIED (interlock). Every shipped launch
  definition already used the guarded entrypoint -- deploy/systemd/astrodeck.service
  (`python -m astrodeck run --host 127.0.0.1`), astrodeck-relay.service
  (`python -m relay`), the server Dockerfile CMD (`python -m astrodeck run`) and
  the relay Dockerfile CMD (`python -m relay`) -- and none bind uvicorn directly.
  test_deployment_entrypoint_policy now pins that invariant so a future edit that
  reintroduces a raw `uvicorn --host` bind fails CI, and deploy/systemd/README
  states custom ASGI hosting is unsupported. The CLI's non-loopback interlock
  (exit 2 on an unauthenticated off-box bind) is the enforcement it protects.

- OPEN-011 FIXED. A browser cannot set a WS Authorization header, so a shared
  token had to ride ?token=. New astrodeck/auth/ws_ticket provides a single-use,
  short-TTL (30s) ticket store, and POST /api/auth/ws-ticket lets an already
  authenticated caller (session cookie or X-Auth-Token header -- never a query)
  mint one; the /ws gate accepts ?ticket= and redeems it once, so no long-lived
  credential need appear in a URL, and a leaked ticket is inert. The UI already
  connects by same-origin session cookie (the finding's preferred path). Query
  redaction was already in place: the astrodeck nginx logs $uri (path only, no
  query), the reverse proxy sets the uvicorn access-log off, and the relay strips
  ?token= from tunneled queries. First-message auth was the alternative; the
  ticket keeps the send-only /ws contract unchanged. Tests: test_ws_ticket
  (single-use, expiry, bound, endpoint, accept/reuse/unknown over the socket).

- OPEN-012 FIXED (script-src) + documented exception (style-src). The SPA shell
  had one inline pre-paint script (anti-flash night/brightness), which forced
  script-src 'unsafe-inline'. It moved to ui/public/bootstrap.js, loaded as an
  external blocking <script src="./bootstrap.js"> (still runs before first
  paint), so the CSP now serves script-src 'self' with NO 'unsafe-inline' -- an
  injected inline <script> is refused by the browser. style-src retains
  'unsafe-inline' because React/Vite set element style attributes and inject
  <style> at runtime, which nonces/hashes cannot cover without a styling-layer
  rewrite; that is the documented residual, and script injection (the
  higher-value class) is closed. Tests: server test_csp_headers (script-src has
  no unsafe-inline; style-src keeps it) and ui cspBootstrap (index.html carries
  no inline script and loads the external bootstrap); the production build
  confirms dist/index.html has only external scripts.

- OPEN-015 FIXED. The deprecation the review saw came from old websockets (12.x),
  where top-level websockets.connect was the legacy asyncio client. websockets
  14.0 made websockets.connect the modern asyncio implementation, which is what
  the server already imports (remote relay client, NINA bridge, polar). Raised
  the declared floor to websockets>=14.0 -- server/pyproject now declares it
  directly (it was only a transitive of uvicorn[standard] despite a direct
  import), and relay/pyproject + relay/requirements -- so no legacy default can
  resolve. test_websockets_modern pins that websockets.connect is the asyncio
  impl and every floor is >=14. The dependency-audit gates (OPEN-006) keep the
  library on a scanned supported release.

- OPEN-003 VERIFIED (software) + ACCEPTED (enclosure QR). The Orange Pi
  provisioner generates a per-device AP credential (not the public factory
  string), commissioning is one-shot (rotation does not re-arm the AP), and three
  distinct short boots re-arm the factory secret for physical recovery -- all
  covered by orangepi5/tests (75 passed), incl.
  test_generated_credentials_are_independent_and_label_safe,
  test_commission_is_exclusive_and_rotation_does_not_arm_ap, and
  test_only_three_distinct_current_boots_rearm_factory_secret. A printed/QR
  per-device bootstrap secret on the enclosure is retail-hardware and remains a
  manufacturing step, not a software change.
- OPEN-005 VERIFIED. runtime_security.py (server + relay) refuses an
  over-privileged identity at startup (require_unprivileged_runtime); the images
  run as uid 10001 (Dockerfile + relay/Dockerfile) and the systemd units set
  User=astrodeck / astrodeck-relay, never root. Tests: server
  test_runtime_security (5), relay test_runtime_security (3), and the
  container-security live gate exercises the isolated container.
- OPEN-010 VERIFIED. windows_acl.py creates and verifies a private DACL
  (handle-based, no broad Users/Everyone read) and rejects reparse points /
  junctions; test_windows_private_acl (18) and the live NTFS acceptance gate
  test_windows_acl_contract (14 passed, 1 skipped where the runner lacks the
  create-symlink privilege) gate it on the windows-private-state CI job.
- OPEN-014 ACCEPTED. One home per public origin/process is the intended safe
  design after the cookie-isolation fix -- config.load_device_tokens refuses a
  token map with more than one home id, so cookies/origins can never be shared
  across homes on /h/<home_id>. Per-stream fairness / flow-control windows are a
  future availability step before any high-density multi-tenant hosting; deploy
  one hostname or trusted edge partition per home until then.
- OPEN-001 ACCEPTED (documented threat boundary; not a code change). The relay
  terminates TLS and forwards the home-signed session cookie: it is a trusted
  bearer-token intermediary, NEVER zero-trust. Existing mitigations: one home per
  origin, browser-cookie isolation, TLS at the edge, minimal/ redacted logs, and
  -- belt and suspenders -- admin/config.* routes are tunnel-blocked at the home
  regardless of the forwarded principal, and the relay holds NO signing secret
  that mints a home-trusted admin (see relay/relay/principal.py). A zero-trust
  relay (end-to-end request encryption + device-bound / mutually authenticated
  keys) is a deliberate future redesign, not attempted here. See
  [[broken-promises-bug-class]], [[astrodeck-security-hardening]] in memory.

## Re-review of the closure loop (2026-09-05, Fable 5.1)

The OPEN-item loop above ran on Opus 4.8. A fresh adversarial pass over every
production diff found two real defects and several honesty gaps, all fixed the
same day. Corrections to the entries above:

- OPEN-002 (CORRECTED). The registry's revoke/replace_tokens only fenced the
  registry layer: they popped the home from the routing table and set two flags
  on the tunnel, but the relay resolves browser traffic through
  RelayState.connections and never closed the WebSocket. A revoked token
  therefore kept the session it already held; only a NEW HELLO was refused. The
  claim "a leaked token cannot keep or resume a session" was false. The unit
  tests passed because FakeScopeTunnel exposes only those flags (a test double
  hiding the code under test). Fix: RelayState.evict_home drops the affinity
  entry and physically closes the socket (1008), and the SIGHUP reload schedules
  it for every evicted home; test_reload_tears_down_the_live_socket_and_routing
  asserts the close code and the cleared affinity against a tunnel that records
  a real close.
- OPEN-007 (CORRECTED). (1) Containment and the manifest key used
  Path.resolve(), which follows symlinks: a symlink planted at a bundled name
  resolved OUTSIDE vendor/ (check skipped, the target loaded) or onto a
  different manifested binary (a name that still hashed clean). Fix: lexical
  containment with no symlink resolution, plus an outright refusal of any
  symlink under vendor/. test_verify_refuses_a_symlink_planted_under_vendor
  runs on Linux CI (skips where the runner cannot create symlinks); a WSL probe
  confirmed the old logic bypassed and the new one refuses. A missing or corrupt
  manifest now raises VendorIntegrityError rather than a bare OSError. (2) The
  closure said binaries are verified "against the signed release manifest". The
  manifest is covered by the release tarball's Ed25519 signature at download
  time; it is NOT authenticated at runtime, and an attacker who can write
  site-packages/astrodeck/vendor/ can rewrite it alongside a binary. The runtime
  check defends against a non-privileged swap or plant and against accident;
  write-protection of the install (the Windows private DACL, the non-root
  service identity) is what keeps the manifest itself trustworthy.
- OPEN-004 (TIGHTENED). "Prepare for sale or transfer" left the seller's admin
  sign-in on the box unless "Also remove sign-in accounts" was ticked too; for a
  consumer that is a trap. The switch copy now says so. It was also hidden when
  only an update credential (no relay pairing) existed, though it scrubs that
  too; preview now reports update_credential and the panel offers the switch for
  either.
- OPEN-006 (TIGHTENED). Pinning base images by digest freezes them: without a
  bump process the pin accumulates every base-image CVE fixed after it was
  taken, which is worse over time than a floating tag. Added
  .github/dependabot.yml (docker for / and /relay, github-actions) so digest and
  action bumps are proposed weekly and graded by the audit gates. The SBOM
  scanned the repo checkout (node_modules dev tooling, the references/ reference
  SDKs) rather than the artifact; it now unpacks the release tarball and
  inventories exactly what ships.

Noted, not changed (judged acceptable or out of scope): the audit log records
the attempted username on a failed login, so a password typed into the username
field is logged (a known audit-log trade-off); behind the reverse proxy the
audit ip is the proxy's (correlate with the nginx request_id); a brute-forcer
can churn the 200-entry in-app log ring with deny lines; the ws-ticket mint
endpoint, like every route, still accepts the shared token in a query string if
a caller chooses to send it that way; the SIGHUP reload re-reads the FILE, so it
is a no-op for env-sourced tokens (Fly secrets), where a redeploy is the
rotation path; npm audit --omit=dev fails on any severity including low, which
may block a release on an unfixable advisory (--audit-level=moderate is the
lever); third-party actions are pinned by tag per repo convention, not by SHA.
- OPEN-007 (CORRECTED AGAIN, from the first Linux CI run). The manifest
  builder matched shared libraries on Path.suffix alone, and the Linux vendor
  libs ship as fully versioned sonames (libPlayerOneCamera.so.3.10.0, suffix
  ".0"), so all four Player One Linux builds were absent from the manifest and
  the fail-closed check refused them as "not in the manifest" -- Player One
  would not have loaded on the Orange Pi. The Windows-only local runs could not
  see it; CI could. The iterator now judges on every suffix ("so" anywhere),
  the manifest covers 13 binaries, and test_manifest_covers_versioned_linux_sonames
  pins the arm64 and x86_64 entries.

## 0.3.23 in production: the Host allowlist broke the relay path (2026-09-05)

Deployed to the rig the same evening, 0.3.23 answered 421 "unrecognized Host
authority" to every request that arrived through the relay: the round-2 Host
allowlist ran before the remote-scope check, and a tunneled request carries the
relay's public hostname, which is never a listener name. The LAN kept working,
the remote display "kept disconnecting", and the relay itself was healthy.

Fix (0.3.24): the allowlist guards the LISTENER only. A relay-tunneled scope
is marked in ASGI state by the scope-side client (not forgeable over the
wire) and skips the Host check; on the listener the relay's name stays refused
as rebinding-shaped. Rebinding and Host injection need a listener; a hostile
Host on the tunnel requires a hostile relay, which can already forge every
header and which the home never trusts for auth. Regression test:
test_relay_tunneled_requests_bypass_the_listener_host_allowlist, verified to
fail against the 0.3.23 middleware. Lesson recorded: a listener allowlist must
be exercised on the RELAY path, not only loopback, before a release is called
deployable.

