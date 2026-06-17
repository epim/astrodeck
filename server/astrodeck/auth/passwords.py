"""bcrypt password hashing helpers (local-auth core).

A thin, import-light wrapper over the ``bcrypt`` library so the rest of the
package never touches the crypto primitive directly. Two functions only:

  - ``hash_password(pw) -> str``  : mint a ``$2b$12$...`` bcrypt hash.
  - ``verify_password(pw, hash)`` : constant-time check, ALWAYS bool, never raises.

Pinned decisions:
  - bcrypt directly (NOT passlib), ``gensalt(12)`` cost.
  - Reject passwords longer than 72 UTF-8 bytes with ``PasswordTooLongError``
    (a ``ValueError``) rather than letting bcrypt silently truncate / raise its
    own opaque error. The route layer maps this to a 422.
  - ``verify_password`` is TOTAL: a malformed/tampered stored hash, an empty
    hash, a too-long candidate, or any internal bcrypt error all resolve to
    ``False`` (fail-closed) -- never an exception that could be mistaken for a
    pass, and never a stack trace leaking the hash.

Import-light: depends ONLY on the ``bcrypt`` wheel. No package imports, so it can
be pulled into the user store, the CLI, and tests without an import cycle.
"""
from __future__ import annotations

import bcrypt

# bcrypt silently considers only the first 72 bytes of a password. Rather than
# let two distinct passwords collide on their first 72 bytes, we reject longer
# inputs outright at hash time (and treat them as a non-match at verify time).
MAX_PASSWORD_BYTES = 72

# Minimum acceptable password length (characters after stripping surrounding
# whitespace). The load-bearing rule is "no blank / whitespace-only password"
# (an empty-password ADMIN created over the open first-run/LAN surface is a real
# bypass); the minimum is kept small to mirror the CLI's "password required"
# guard without dictating a heavy policy here.
MIN_PASSWORD_LEN = 1

# Work factor (log2 rounds). 12 is the pinned cost.
BCRYPT_ROUNDS = 12


class PasswordTooLongError(ValueError):
    """Raised by ``hash_password`` when the password exceeds 72 UTF-8 bytes.

    Subclasses ``ValueError`` so a route's ``except ValueError`` can map it to a
    422 without importing this symbol."""


class PasswordTooShortError(ValueError):
    """Raised by ``hash_password`` for a blank/whitespace-only or too-short
    password. Subclasses ``ValueError`` so a route's ``except ValueError`` maps
    it to a 4xx, and the route layer maps it explicitly to 422 (mirroring the
    too-long case and the CLI's 'password required' guard)."""


def _too_long(pw: str) -> bool:
    return len(pw.encode("utf-8")) > MAX_PASSWORD_BYTES


def _too_short(pw: str) -> bool:
    """True for a blank / whitespace-only password, or one shorter than
    ``MIN_PASSWORD_LEN`` once surrounding whitespace is stripped."""
    return len((pw or "").strip()) < MIN_PASSWORD_LEN


def hash_password(password: str) -> str:
    """Hash ``password`` with bcrypt (cost 12), returning the ``$2b$12$...`` str.

    Raises ``PasswordTooShortError`` for a blank/whitespace-only (or too-short)
    password -- closing the empty-password-admin hole on EVERY write path
    (first-run setup, user create, password reset, CLI). Raises
    ``PasswordTooLongError`` if the password exceeds 72 UTF-8 bytes (the bcrypt
    input limit) -- we refuse rather than truncate, so two long passwords sharing
    a 72-byte prefix can never be treated as equal.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a str")
    if _too_short(password):
        raise PasswordTooShortError(
            "password must not be blank "
            f"(minimum {MIN_PASSWORD_LEN} non-whitespace character(s))")
    if _too_long(password):
        raise PasswordTooLongError(
            f"password exceeds {MAX_PASSWORD_BYTES} bytes "
            "(bcrypt input limit; choose a shorter password)")
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS))
    return digest.decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """True iff ``password`` matches the stored bcrypt ``password_hash``.

    TOTAL and fail-closed: returns ``False`` (never raises) for an empty/None or
    structurally invalid hash, an over-length candidate, or any internal bcrypt
    error (e.g. a tampered hash with a bad salt/cost). bcrypt's own compare is
    constant-time over the digest."""
    if not password_hash or not isinstance(password_hash, str):
        return False
    if not isinstance(password, str):
        return False
    if _too_long(password):
        return False  # could never have produced a stored hash (hashing rejects it)
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed/tampered hash (bad prefix, truncated salt, non-ascii) -> deny.
        return False


# A real, valid cost-12 bcrypt hash of a random throwaway password, computed ONCE
# at import. ``dummy_verify`` runs a genuine ``checkpw`` against it so the
# unknown-username path burns the SAME ~bcrypt time as a real compare (anti-
# enumeration). It is a decoy: no real password can match it (the plaintext is
# unknown and never stored), and it is never used as a credential.
_DUMMY_HASH = hash_password(__import__("secrets").token_urlsafe(16))


def dummy_verify(password: str) -> bool:
    """Run a real bcrypt compare against a fixed decoy hash, then return False.

    Used on the unknown-user branch of ``UserStore.verify`` so the response time
    is indistinguishable from a known user with a wrong password -- without this,
    an unknown username short-circuits in microseconds while a known one spends
    ~cost-12 bcrypt time, trivially leaking account existence. ALWAYS returns
    False (it is a timing-equalizer, never an auth decision)."""
    verify_password(password if isinstance(password, str) else "", _DUMMY_HASH)
    return False


__all__ = [
    "hash_password",
    "verify_password",
    "dummy_verify",
    "PasswordTooLongError",
    "PasswordTooShortError",
    "MAX_PASSWORD_BYTES",
    "MIN_PASSWORD_LEN",
    "BCRYPT_ROUNDS",
]
