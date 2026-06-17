"""Concrete backend adapters (Stage A).

Each module here WRAPS an existing device factory behind the ``Backend`` /
``BackendSession`` Protocols from ``devices.backend`` and self-registers via
``register(...)`` at import. Importing this package imports every concrete
backend (so the registry is populated), but ``devices.backend`` itself imports
none of them -- the contract module stays import-light and cycle-free.
"""
from __future__ import annotations

from . import sim_backend  # noqa: F401  (import for self-registration side effect)
from . import nina_backend  # noqa: F401  (import for self-registration side effect)
from . import native_backend  # noqa: F401  (import for self-registration side effect)
from . import phd2_backend  # noqa: F401  (import for self-registration side effect)

__all__ = ["sim_backend", "nina_backend", "native_backend", "phd2_backend"]
