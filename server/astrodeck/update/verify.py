"""Policy wrapper over ``signing.verify_artifact``: a pinned public key is
REQUIRED, SHA256 then Ed25519 must both pass, fail-closed. Kept separate so the
service reads as download -> verify -> stage."""
from __future__ import annotations

from pathlib import Path

from . import signing


def verify_download(artifact: Path, pubkey: str, *,
                    sha256_text: "str | None" = None,
                    signature_text: "str | None" = None) -> tuple[bool, str]:
    return signing.verify_artifact(
        artifact, pubkey,
        sha256_text=sha256_text,
        signature_b64=signature_text)
