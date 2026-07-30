"""The user store — ONE account list, whatever a person signs in with.

A tiny JSON-backed user database living at ``server/config/users.json``, written
atomically through the same ``write_json_atomic`` path the config store uses.

**Identity is the email address.** Case-folded, unique, and the same key
whether somebody signs in with a password or with Google. The *requirement* that
it be an email is enforced at the API route, not here: this store must still be
able to hold a bare username, because the CLI break-glass admin creates one and
records predating unification already carry one.
That is the whole point of this module's shape: user management used to be
split in two, a local username+password store here and an email->role allowlist
in ``AuthConfig``, with no relationship between them. Creating a Google-only
user meant editing an allowlist rather than creating a user, and the same person
could exist twice with two different roles. There is now one record per person.

**A password is optional.** An empty ``password_hash`` is a valid, deliberate
state meaning "this account signs in with Google only" — not a broken record and
not an empty password. ``verify`` refuses it outright, so a password-less
account can never be logged into locally, whatever is posted.

Hard secrecy invariant -- the bcrypt ``password_hash``:
  - is READ only inside ``verify``;
  - is WRITTEN only inside ``create`` / ``set_password``;
  - is NEVER present in any outward shape. ``User.to_public()`` structurally
    OMITS it (it is not blanked -- the key is absent), and ``UserStore`` only
    ever returns ``to_public()`` dicts to callers above the store.

Last-admin protection: the store refuses to delete, disable, or demote the last
remaining ENABLED admin (``ValueError("last admin")`` -> the route maps to 409),
so a misconfiguration can never lock every admin out of their own rig.

Import-light: depends only on ``capabilities`` (role validity), ``passwords``
(bcrypt), ``persist`` (atomic JSON), and pydantic. No ``api.app`` / ``hub`` /
``config`` import -- the ``users.json`` PATH is injected (defaulting to
``config.CONFIG_DIR / 'users.json'`` resolved lazily) so there is no import cycle.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from ..persist import read_json_or, write_json_atomic
from .capabilities import ROLES
from .passwords import dummy_verify, hash_password, verify_password


def _default_store_path() -> Path:
    """``server/config/users.json``. Resolved lazily so importing this module
    never imports ``config`` (kept import-light / cycle-free)."""
    from ..config import CONFIG_DIR  # lazy: avoid an import cycle at module load
    return CONFIG_DIR / "users.json"


def _norm_username(username: str) -> str:
    """Canonical (case-folded, trimmed) form used for storage + uniqueness."""
    return (username or "").strip().casefold()


class InvalidEmailError(ValueError):
    """The identity given is not a usable email address."""


def normalize_email(email: str) -> str:
    """Canonical (trimmed, case-folded) email, or raise ``InvalidEmailError``.

    Deliberately permissive — one ``@``, something either side, a dot in the
    domain, no whitespace. This is not RFC 5322 validation and does not try to
    be: the only thing that ultimately proves an address is a successful Google
    sign-in. What it exists to catch is a bare username typed where an email
    belongs, because that address is what Google will match against, and an
    entry that can never match is an account somebody believes they created.
    """
    e = (email or "").strip().casefold()
    if not e:
        raise InvalidEmailError("an email address is required")
    if any(c.isspace() for c in e):
        raise InvalidEmailError("an email address cannot contain spaces")
    # Exactly one "@". `partition` splits on the FIRST one, so without this
    # "two@@at.com" reads as local="two", domain="@at.com" — which has a dot and
    # passes every other check.
    if e.count("@") != 1:
        raise InvalidEmailError(
            f"{email!r} is not an email address — it needs exactly one '@'")
    local, sep, domain = e.partition("@")
    if not sep or not local or not domain or "." not in domain:
        raise InvalidEmailError(
            f"{email!r} is not an email address — sign-in matches on the "
            "address Google reports, so it has to be a real one")
    return e


class User(BaseModel):
    """One account. ``password_hash`` is SECRET and never leaves the store.

    ``username`` is the canonical, case-folded, unique key and for every account
    created since the stores were unified it IS the email address. It keeps its
    name so existing records, sessions and the JSON on disk stay readable
    without a migration that could lock somebody out of their own rig.

    ``password_hash`` empty means **Google sign-in only** — a deliberate state,
    not a broken record. ``verify`` refuses it, so no password can ever match.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    username: str                         # stored case-folded (unique key)
    email: str | None = None
    role: str = "viewer"
    password_hash: str = ""               # bcrypt $2b$12$...  -- SECRET; "" = OIDC-only
    enabled: bool = True
    created: float = Field(default_factory=lambda: time.time())

    @property
    def login_email(self) -> str:
        """The address sign-in matches on. ``email`` when a legacy record
        carries one, else the username (which is the email for anything created
        since unification)."""
        return (self.email or self.username or "").strip().casefold()

    @property
    def can_sign_in_locally(self) -> bool:
        """False for a Google-only account. The store enforces this in
        ``verify``; this is for surfaces that need to SAY which it is."""
        return bool(self.password_hash)

    def to_public(self) -> dict:
        """Non-secret, JSON-safe view. ``password_hash`` is structurally ABSENT
        (the key is not present at all, not blanked)."""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "login_email": self.login_email,
            "role": self.role,
            "enabled": self.enabled,
            "created": self.created,
            # WHICH sign-in this account can use. Derived from whether a hash
            # exists, never from the hash itself, so nothing about the secret
            # leaves the store — only the yes/no the UI needs to say "password"
            # or "Google only" instead of leaving the user to guess.
            "has_password": self.can_sign_in_locally,
        }


class UserStore:
    """JSON-backed CRUD over local ``User`` records (atomic, in-memory cache).

    Loaded lazily on first access; every mutation rewrites the whole file via
    ``write_json_atomic`` (keeping a ``.bak``). The store is the SOLE owner of
    ``password_hash``: it reads it only in ``verify`` and writes it only in
    ``create`` / ``set_password``.
    """

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else _default_store_path()
        self._users: dict[str, User] | None = None   # id -> User (lazy)

    # -- loading / persistence -------------------------------------------------

    def _load(self) -> dict[str, User]:
        raw = read_json_or(self._path, default=None)
        users: dict[str, User] = {}
        if isinstance(raw, dict):
            records = raw.get("users", [])
        elif isinstance(raw, list):
            records = raw
        else:
            records = []
        for rec in records or []:
            try:
                u = User(**rec)
            except Exception:
                continue  # skip a malformed record rather than brick the store
            users[u.id] = u
        return users

    def _cache(self) -> dict[str, User]:
        if self._users is None:
            self._users = self._load()
        return self._users

    def _save(self) -> None:
        users = self._cache()
        # Persist FULL records (incl. password_hash) -- this is the at-rest store,
        # not an outward shape. Sorted by created for a stable on-disk order.
        data = {"version": 1,
                "users": [u.model_dump() for u in
                          sorted(users.values(), key=lambda x: x.created)]}
        write_json_atomic(self._path, data)

    def reload(self) -> None:
        """Drop the in-memory cache and re-read from disk (tests)."""
        self._users = None
        self._cache()

    # -- queries ---------------------------------------------------------------

    def is_empty(self) -> bool:
        """True iff NO users exist (gates the first-run create-admin path)."""
        return len(self._cache()) == 0

    def list(self) -> list[User]:
        """All users, ordered by creation time (oldest first)."""
        return sorted(self._cache().values(), key=lambda u: u.created)

    def get(self, user_id: str) -> User | None:
        return self._cache().get(user_id)

    def get_by_username(self, username: str) -> User | None:
        key = _norm_username(username)
        for u in self._cache().values():
            if u.username == key:
                return u
        return None

    def _enabled_admins(self, *, exclude_id: str | None = None) -> list[User]:
        return [u for u in self._cache().values()
                if u.role == "admin" and u.enabled and u.id != exclude_id]

    # -- mutation --------------------------------------------------------------

    def create(self, *, username: str, password: str = "", role: str,
               email: str | None = None, enabled: bool = True,
               require_email: bool = False) -> User:
        """Create a user. The identity is an EMAIL address.

        ``password`` is OPTIONAL. Omitting it creates a Google-only account:
        a real, usable record that simply cannot be logged into with a
        password. That is the case this signature exists to serve — before
        unification, a Google user was not a user at all but a row in an
        allowlist, so there was nowhere to set their role, disable them, or
        see them beside everyone else.

        ``require_email`` is the PRODUCT policy — every account created through
        the API is keyed on an email — and it lives at the route, not here,
        deliberately. The store has to tolerate a bare username regardless: the
        CLI break-glass admin (``python -m astrodeck create-admin``) must work
        on a rig with no Google configured and nothing but a console, and
        records predating unification already carry one. A store that refused
        what it must still be able to hold would be lying about its own data.

        Raises ``ValueError`` on a duplicate or unknown role,
        ``InvalidEmailError`` on an identity that is not an email, and
        ``PasswordTooLongError`` from ``hash_password``.
        """
        key = normalize_email(username) if require_email else _norm_username(username)
        if not key:
            raise ValueError("username required")
        if role not in ROLES:
            raise ValueError(f"unknown role: {role!r}")
        if self.get_by_username(key) is not None:
            raise ValueError("username already exists")
        # An empty password stores an EMPTY hash, never a hash of "". Hashing
        # the empty string would produce a real bcrypt digest that a posted
        # empty password would then match — turning "Google only" into "no
        # password required".
        pw_hash = hash_password(password) if password else ""
        user = User(username=key, email=email or key, role=role,
                    password_hash=pw_hash, enabled=enabled)
        self._cache()[user.id] = user
        self._save()
        return user

    def get_by_email(self, email: str) -> User | None:
        """The account whose sign-in address matches, or None.

        The single lookup Google sign-in resolves a role through, so one person
        cannot hold one role locally and a different one over Google."""
        try:
            key = normalize_email(email)
        except InvalidEmailError:
            return None
        for u in self._cache().values():
            if u.login_email == key:
                return u
        return None

    def clear_password(self, user_id: str) -> User:
        """Turn an account into Google-only sign-in.

        Refuses on the last enabled admin when Google is not the only way back
        in — see the route layer, which knows whether Google is configured.
        Here it is unconditional: the store's job is the record, not policy."""
        user = self._require(user_id)
        user.password_hash = ""
        self._save()
        return user

    def set_password(self, user_id: str, password: str) -> User:
        """Reset a user's password. Raises ``ValueError`` if unknown or too long."""
        user = self._require(user_id)
        new_hash = hash_password(password)   # may raise PasswordTooLongError
        user.password_hash = new_hash
        self._save()
        return user

    def set_role(self, user_id: str, role: str) -> User:
        """Change a user's role. Refuses to demote the LAST enabled admin
        (``ValueError("last admin")``). Unknown role -> ``ValueError``."""
        if role not in ROLES:
            raise ValueError(f"unknown role: {role!r}")
        user = self._require(user_id)
        if user.role == "admin" and role != "admin" and user.enabled:
            if not self._enabled_admins(exclude_id=user_id):
                raise ValueError("last admin")
        user.role = role
        self._save()
        return user

    def set_enabled(self, user_id: str, enabled: bool) -> User:
        """Enable/disable a user. Refuses to disable the LAST enabled admin."""
        user = self._require(user_id)
        if not enabled and user.role == "admin" and user.enabled:
            if not self._enabled_admins(exclude_id=user_id):
                raise ValueError("last admin")
        user.enabled = enabled
        self._save()
        return user

    def rename(self, user_id: str, username: str) -> User:
        """Rename a user (case-folded). Raises on blank/duplicate username."""
        key = _norm_username(username)
        if not key:
            raise ValueError("username required")
        existing = self.get_by_username(key)
        if existing is not None and existing.id != user_id:
            raise ValueError("username already exists")
        user = self._require(user_id)
        user.username = key
        self._save()
        return user

    def delete(self, user_id: str) -> None:
        """Delete a user. Refuses to delete the LAST enabled admin."""
        user = self._require(user_id)
        if user.role == "admin" and user.enabled:
            if not self._enabled_admins(exclude_id=user_id):
                raise ValueError("last admin")
        del self._cache()[user_id]
        self._save()

    # -- authentication --------------------------------------------------------

    def verify(self, username: str, password: str) -> User | None:
        """Return the matching ENABLED user, or ``None``.

        The ONLY method that reads ``password_hash``. Returns ``None`` (no reason
        leaked) for an unknown username, a wrong password, OR a disabled account.
        Fail-closed and timing-aware: the bcrypt compare runs even on an unknown
        user (against the stored hash if present) so presence isn't trivially
        distinguishable by response time.
        """
        user = self.get_by_username(username)
        if user is None:
            # Burn a REAL bcrypt compare against a decoy hash so an unknown
            # username takes the same ~cost-12 time as a known one (no timing
            # enumeration oracle). ``verify_password(password, None)`` would NOT
            # do this -- it short-circuits before bcrypt runs.
            dummy_verify(password)
            return None
        if not user.enabled:
            # Same treatment for a disabled account: burn a real compare so a
            # disabled user is timing-indistinguishable from an enabled one.
            dummy_verify(password)
            return None
        if not user.password_hash:
            # A Google-only account. Refused HERE, explicitly, rather than left
            # to verify_password: whether an empty hash can be matched is the
            # single most dangerous question in this module, and the answer must
            # not depend on a bcrypt library's behaviour on an empty string.
            # Same decoy compare, so "has no password" is not distinguishable
            # from "wrong password" by timing either.
            dummy_verify(password)
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    # -- internals -------------------------------------------------------------

    def _require(self, user_id: str) -> User:
        user = self._cache().get(user_id)
        if user is None:
            raise KeyError(user_id)
        return user


# Module singleton (mirrors ``config.config_store`` / ``hub``). Lazily resolves
# its path on first use so importing this module never imports ``config``.
user_store = UserStore()


__all__ = ["User", "UserStore", "user_store"]
