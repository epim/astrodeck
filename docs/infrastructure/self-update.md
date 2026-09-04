# Scope Self-Update (supervisor, releases, signing, rollback)

The scope controller checks GitHub for signed releases and, on admin consent,
downloads → verifies → stages → restarts into the new version, rolling back
automatically if the new build doesn't come up healthy. This page covers
installing the supervisor, cutting and signing a release, and recovering by hand.

Spec: `docs/superpowers/specs/2026-06-19-self-update-design.md`.

---

## 1. Install root layout

The supervisor owns an **install root** with this shape:

```
<root>/
  current                  pointer FILE: the active version string (e.g. "0.2.0")
  releases/<version>/      one dir per version: server/  ui/dist/  manifest.json
  venv/                    the Python venv that runs the server
  state/
    last-good              last version that passed the health check
    pending-update.json    written by the server, read by the supervisor
    update-result.json     written by the supervisor, read by the server on boot
    failed/<version>       marker: a version that failed health-check (never retried)
```

`current` is a plain text file (not a symlink) so it works on Windows without
elevated privileges.

### First install (manual)

1. Create the root and venv:
   ```
   mkdir -p <root>/releases <root>/state
   python -m venv <root>/venv
   ```
2. Put your built version under `releases/<version>/` (the `server/` package +
   `ui/dist` + `manifest.json` — i.e. the contents of a release tarball, see §3).
3. Install the server deps into the venv:
   ```
   <root>/venv/bin/pip install <root>/releases/<version>/server      # Windows: <root>\venv\Scripts\pip
   ```
4. Point `current` at it:
   ```
   echo <version> > <root>/current
   ```

---

## 2. Run the supervisor

The supervisor launches the server, watches it, and applies updates:

```
python -m supervisor.supervisor \
  --root <root> \
  --python <root>/venv/bin/python \
  --host 127.0.0.1 --port 8800 \
  --initial-version <version>      # only used if `current` is absent
```

- It exports `ASTRODECK_INSTALL_ROOT=<root>` and `PYTHONPATH` to the server child,
  which is how the server knows it is **supervised** (self-update apply becomes
  available) and where to write `pending-update.json`.
- Exit-code contract: server exits **0** ⇒ supervisor stops; server exits **92**
  ⇒ apply the staged update, swap `current`, relaunch, and **health-check**
  (`GET /healthz` must return `{ok:true, version:<new>}`); unhealthy ⇒ roll back
  to `last-good`, mark the version failed, and relaunch the old one.

### Keep the supervisor alive

- **Linux (recommended):** a systemd unit with `Restart=always`:
  ```ini
  # /etc/systemd/system/astrodeck.service
  [Unit]
  Description=AstroDeck supervisor
  After=network-online.target

  [Service]
  WorkingDirectory=/opt/astrodeck
  EnvironmentFile=/etc/astrodeck.env
  ExecStart=/usr/bin/python3 -m supervisor.supervisor --root /opt/astrodeck --python /opt/astrodeck/venv/bin/python --host 0.0.0.0 --port 8800
  Restart=always
  RestartSec=3

  [Install]
  WantedBy=multi-user.target
  ```
  Store a long `ASTRODECK_TOKEN` (or the environment needed by your configured
  auth provider) in `/etc/astrodeck.env`, owned by root with mode `0600`. The
  server refuses this non-loopback bind if authentication is absent.
  `systemctl daemon-reload && systemctl enable --now astrodeck`.
  (The supervisor must be importable — run from the repo/install dir, or
  `pip install` the supervisor package.)
  If `<root>/current` might be absent on first boot, add
  `--initial-version <version>` to `ExecStart` so the supervisor can seed the
  pointer instead of exiting with "no 'current' pointer".
- **Windows (current rig):** run the supervisor command in a startup task, or
  wrap it as a service with [NSSM](https://nssm.cc/). The supervisor itself does
  the swap/rollback; NSSM just keeps the supervisor process alive.

---

## 3. Cut and sign a release

A release is a Git tag `vX.Y.Z`. The `release` GitHub Actions workflow does the
whole thing automatically (see [ci-cd.md](ci-cd.md)); to do it by hand:

1. **Bump the version** in BOTH `server/astrodeck/__init__.py` (`__version__`) and
   `server/pyproject.toml`. They must match the tag — the release workflow fails
   if `tag != __version__`, and the supervisor's health check compares the
   running version to the release dir name.
2. **Build the bundle:**
   ```
   cd ui && npm ci && npm run build && cd ..
   python scripts/build_release.py --version X.Y.Z --out dist
   ```
   This produces `dist/astrodeck-X.Y.Z.tar.gz` (server source + `ui/dist` +
   `manifest.json`, single top dir `astrodeck-X.Y.Z/`).
3. **Sign it:**
   ```
   RELEASE_SIGNING_KEY=<base64-private-seed> python scripts/sign_release.py dist/astrodeck-X.Y.Z.tar.gz
   ```
   This writes `astrodeck-X.Y.Z.tar.gz.sha256` and `.tar.gz.sig` (base64 Ed25519
   over the raw artifact bytes).
4. **Publish a GitHub Release** for tag `vX.Y.Z` and attach all three files
   (`.tar.gz`, `.tar.gz.sha256`, `.tar.gz.sig`). The Release **body** is shown
   verbatim as the release notes in the UI dialog.

### Signing keys

```
python scripts/gen_signing_key.py
```
prints a **private seed** (base64) and a **public key** (base64).

- Private seed → CI secret `RELEASE_SIGNING_KEY` (and a password manager). Never
  committed.
- Public key → pin in the scope: **Settings → Updates → Release signing public
  key**, or `POST /api/update/config {"signing_pubkey": "<base64>", ...}`.

**Verify an artifact by hand** (sanity check before trusting a download):
```
sha256sum -c astrodeck-X.Y.Z.tar.gz.sha256
python -c "import sys; sys.path.insert(0,'server'); from astrodeck.update import signing; \
print(signing.verify_bytes(open('astrodeck-X.Y.Z.tar.gz','rb').read(), \
open('astrodeck-X.Y.Z.tar.gz.sig').read().strip(), '<public-key-base64>'))"
```

**Rotate** the signing key: generate a new pair, update `RELEASE_SIGNING_KEY` in
CI, and update the pinned public key on every scope **before** the next release
(a release signed with the new key won't verify against an old pinned key).

---

## 4. Manual update and rollback

You don't need the UI. To install a version by hand:

```
# stage it
tar -xzf astrodeck-X.Y.Z.tar.gz -C <root>/releases     # yields releases/astrodeck-X.Y.Z/
mv <root>/releases/astrodeck-X.Y.Z <root>/releases/X.Y.Z
<root>/venv/bin/pip install <root>/releases/X.Y.Z/server
# point current at it and restart the supervisor
echo X.Y.Z > <root>/current
```

**Roll back by hand:** set `current` to a known-good version (it's still under
`releases/`) and restart the supervisor:
```
echo <last-good-version> > <root>/current
cat <root>/state/last-good        # the supervisor's own record of last-good
```
A version that failed an automated update is marked under `state/failed/` and is
never retried automatically; delete that marker to allow a retry.

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| UI shows "Not running under the supervisor" | The server wasn't launched by the supervisor (no `ASTRODECK_INSTALL_ROOT`). Start it via `python -m supervisor.supervisor …`. |
| "No signing public key pinned" / can't Upgrade | Pin the release public key in Settings → Updates. |
| "Can't install now: rig is …" | The safety gate — a sequence/slew/exposure is active. Wait for idle, then retry. |
| Update applied, then reverted | The new build failed its health check; the supervisor rolled back. Check `state/update-result.json` and `state/failed/<version>`, and the server logs for why the new version didn't serve `/healthz`. |
| Health check never passes | The new version's `__version__` must equal its `releases/<version>/` dir name, and the server must bind the same `--port` the supervisor probes. |
