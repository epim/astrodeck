# CI/CD

Three GitHub Actions workflows live in `.github/workflows/`. All can be replaced
by the by-hand procedures elsewhere in this folder if you don't use GitHub
Actions.

| Workflow | Trigger | Does |
|----------|---------|------|
| `ci.yml` | push to `main`, every PR | server pytest, relay pytest, UI build |
| `release.yml` | push a tag `v*`, or a manual dispatch (a dry run unless `dry_run` is turned off) | selftest gate → build + sign → publish GitHub Release |
| `deploy-relay.yml` | push to `main` touching `relay/**` | `flyctl deploy` the relay |

---

## `ci.yml` — checks

Runs three independent jobs so a contributor's PR is validated before merge:

- **server** — `pip install -e ".[dev]"` + `pytest -q` (≈680 tests).
- **relay** — installs `relay/requirements.txt` + `pytest -q`.
- **ui** — `npm ci && npm run build` (the `tsc` typecheck is the UI gate).

No secrets required.

---

## `release.yml` — build, sign, publish

Cut a release:

1. Bump the version in `server/astrodeck/__init__.py` **and**
   `server/pyproject.toml` (they must match the tag).
2. Commit, then:
   ```
   git tag v0.2.0
   git push origin v0.2.0
   ```

The workflow then:

1. **`selftest` job (Windows + Linux matrix)** — runs the real-I/O self-update
   checks (`pytest -m selftest`: real download + Ed25519 verify + tamper-reject +
   launcher/health probe) **and** the supervisor swap/rollback state machine.
   A failure here **blocks the release**.
2. **`release` job** — builds the UI, asserts `tag == __version__`, bundles the
   artifact (`scripts/build_release.py`), signs it (`scripts/sign_release.py`
   using `RELEASE_SIGNING_KEY`), and publishes a GitHub Release with the
   `.tar.gz` + `.sha256` + `.sig` and auto-generated notes.

**Publishing is source first (#1073).** The signed source tarball, its `.sha256` and
`.sig`, and the SBOM are published as soon as the source job's licence gates pass,
whatever the binary jobs did. An executable and its native wheel are attached only for
a platform whose own gates passed (its `accepted-<platform>` artifact exists only then).
When a platform is missing, the release body names the platforms whose binaries were
withheld and says their licence gates have not passed; no uncleared artifact is ever
uploaded. The container image is pushed only when every binary job passed, because it
installs the Linux wheels.

**Dry runs.** A manual dispatch defaults to `dry_run: true`: every gate and build runs,
the package version only has to be plain `X.Y.Z` (no tag is needed), artifact names come
from the package version, and the publish and image jobs are skipped. Turning `dry_run`
off is meant for re-running a version tag and then behaves like a tag push.

**Required secret:** `RELEASE_SIGNING_KEY` (the Ed25519 private seed — see
[secrets.md](secrets.md)). Set it once:
```
python scripts/gen_signing_key.py            # prints private seed + public key
gh secret set RELEASE_SIGNING_KEY --repo <owner>/<repo>   # paste the private seed
```
Then pin the printed **public** key on every scope (Settings → Updates).

---

## `deploy-relay.yml` — relay auto-deploy

On any push to `main` under `relay/**`, deploys the relay to Fly.io. A
contributor's merged relay change ships automatically.

**One-time bootstrap (manual, not in CI):**
```
cd relay
fly launch --no-deploy            # create the app, accept fly.toml (region sjc)
# provision device tokens + Ed25519 seeds as MOUNTED FILES (Fly volume / secrets),
# point RELAY_*_FILE env at them; set RELAY_ORIGIN to the public host
fly deploy                        # first deploy by hand
```
After that, CI owns redeploys. Full walkthrough + security model:
[../relay-deploy.md](../relay-deploy.md).

**Required secret:** `FLY_API_TOKEN` (a Fly deploy token):
```
fly tokens create deploy
gh secret set FLY_API_TOKEN --repo <owner>/<repo>
```

> Remember the write-only rule: once set, a GitHub secret can't be read back.
> Keep the source-of-truth value in a password manager; to recover, rotate.
