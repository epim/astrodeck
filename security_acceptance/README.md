# Platform security acceptance suite

This directory is an executable specification for the four unfinished security
workstreams documented in
`docs/superpowers/specs/2026-09-01-platform-security-hardening-execution-design.md`.
It is deliberately **opt-in** and deliberately **red before implementation**.
Ordinary server, relay, UI, and Orange Pi test runs do not enable it.

Recorded pre-implementation baseline (Windows, 2026-09-02): 53 tests collect;
an ordinary run skips all 53; the explicit portable gate reports 39 failed,
2 passed, and 12 live tests deselected. Those failures are the missing target
controls, not regressions introduced by the acceptance files.

Do not make a test green by weakening, deleting, skipping, or replacing its
assertion with an implementation-detail mock. If the implementation needs a
different public seam, update the design and this suite together in a reviewed
commit, retaining the same externally observable security property.

Portable contract gate (run during implementation):

```powershell
server\.venv\Scripts\python.exe -m pytest -q -p no:xdist security_acceptance `
  --run-security-acceptance -m "not security_live"
```

```bash
server/.venv/bin/python -m pytest -q -p no:xdist security_acceptance \
  --run-security-acceptance -m 'not security_live'
```

Live gates are intentionally separate. Run the Windows file on Windows
NTFS/ReFS, the container file on native Linux amd64 and arm64, and the proxy
file from a host that can reach the disposable HTTPS lab stack. A wrong target
fails rather than silently counting as a pass:

```powershell
server\.venv\Scripts\python.exe -m pytest -q -p no:xdist `
  security_acceptance\test_windows_acl_contract.py `
  --run-security-acceptance --run-security-live -m security_live
```

```bash
server/.venv/bin/python -m pytest -q -p no:xdist \
  security_acceptance/test_container_contract.py \
  --run-security-acceptance --run-security-live -m security_live

server/.venv/bin/python -m pytest -q -p no:xdist \
  security_acceptance/test_reverse_proxy_contract.py \
  --run-security-acceptance --run-security-live -m security_live
```

A green portable run is necessary but not sufficient. Release also requires:

- the Windows live DACL tests on NTFS as a standard user and as the dedicated
  service account;
- the Docker and bare-metal systemd gates on Linux amd64 and arm64;
- the HTTPS/WSS tests against both deployed nginx lab stacks; and
- the destructive Orange Pi 5 power-cycle/AP/join checklist in the design doc.

Once all target work is implemented and the baseline has gone green, add the
portable lane to normal CI. Keep target-specific lanes explicit so a missing
Docker daemon, Windows runner, or Orange Pi cannot silently turn a required
release check into a skip.
