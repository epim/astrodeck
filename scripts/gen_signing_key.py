#!/usr/bin/env python3
"""Generate an Ed25519 release-signing keypair for AstroDeck self-update.

    python scripts/gen_signing_key.py

Prints the PRIVATE seed (base64) and the PUBLIC key (base64). Store the private
seed as the GitHub Actions secret ``RELEASE_SIGNING_KEY`` and pin the public key
in ``UpdateConfig.signing_pubkey`` (Settings -> Updates, or the config API). The
private seed must NEVER be committed; keep a copy in a password manager so you can
recover/rotate. See docs/infrastructure/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from astrodeck.update import signing  # noqa: E402


def main() -> int:
    seed_b64, pub_b64 = signing.generate_keypair()
    print("=== AstroDeck release signing keypair (Ed25519) ===")
    print()
    print("PRIVATE seed (CI secret RELEASE_SIGNING_KEY -- keep secret, never commit):")
    print(f"  {seed_b64}")
    print()
    print("PUBLIC key (pin in UpdateConfig.signing_pubkey -- safe to share/commit):")
    print(f"  {pub_b64}")
    print()
    print("Next: `gh secret set RELEASE_SIGNING_KEY` (paste the private seed via stdin)")
    print("and set the public key in the server's update config.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
