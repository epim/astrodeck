"""Simulator backend adapter (Stage A).

WRAPS ``devices.sim.build_sim_rig`` behind the ``Backend`` / ``BackendSession``
Protocols. Purely additive and behavior-preserving: it does NOT change what the
sim devices do -- it just hands out the very same device objects the hub builds
today, keyed by role, plus the ``SimGuider`` as the native guider.

The sim has no network presence (``discoverable = False``) and no native plate
solver of its own (``native_solver()`` returns None -- the hub picks a solver via
``solve.get_solver``). Closing the session is a no-op: the sim owns no external
equipment to tear down.

Imports are deferred into ``open()`` (``build_sim_rig``) and ``SimSession``
(``SimGuider``) so importing this module is cheap and never drags in numpy / the
guide stack just to populate the registry.
"""
from __future__ import annotations

from ..backend import ROLES, BackendSession, ConnSpec, register


class SimSession:
    """A live simulated rig.

    Built ONCE per ``open()``: ``build_sim_rig()`` returns one coherent set of
    devices sharing a single ``SimRig`` state object, and we keep that exact dict
    so every ``get_device`` call hands back the same instance (mutating one device
    is visible to the others, matching the hub's behavior). ``guide_camera`` is
    exposed as an extra (non-ROLES) key, mirroring the sim rig dict.
    """

    name = "sim"

    def __init__(self, rig: dict[str, object]) -> None:
        # ``rig`` is exactly what build_sim_rig() returns: role -> device, plus
        # the "guide_camera" device and the "_rig" shared-state object.
        self._rig = rig
        self._guider: object | None = None

    @property
    def shared_state(self) -> object | None:
        """The single ``SimRig`` state object every sim device shares (the rig's
        ``"_rig"`` entry), or None. Public, read-only accessor so the hub can keep
        setting ``self.sim_rig`` without reaching into a private attribute."""
        return self._rig.get("_rig")

    @property
    def guide_camera(self) -> object | None:
        """The sim's dedicated guide-camera device (the rig's ``"guide_camera"``
        entry), or None. It is NOT a canonical ROLE, so the hub reads it here to
        keep populating ``self.devices['guide_camera']`` exactly as before."""
        return self._rig.get("guide_camera")

    async def get_device(self, role: str, conn: ConnSpec) -> object:
        """Return the sim device filling ``role``.

        ``conn`` is accepted for interface parity but unused -- the sim needs no
        address. ``guide_camera`` is reachable here too (it is not a ROLE but is
        the sim's dedicated guide camera, matching the hub's rig dict). Raises
        ``KeyError`` for a role the sim does not provide."""
        try:
            return self._rig[role]
        except KeyError:
            raise KeyError(
                f"sim backend has no device for role {role!r}; "
                f"available: {sorted(k for k in self._rig if not k.startswith('_'))}"
            ) from None

    def native_guider(self) -> object | None:
        """The sim's own guider (``SimGuider``), created lazily and once.

        SYNC by contract. The hub uses this in place of PHD2 for the sim rig."""
        if self._guider is None:
            # Deferred import: keep module import light and avoid pulling the
            # guide stack in just to register the backend.
            from ...guide import SimGuider

            self._guider = SimGuider()
        return self._guider

    def native_solver(self) -> object | None:
        """The sim has no native plate solver -- the hub chooses one via
        ``solve.get_solver``. SYNC by contract."""
        return None

    async def health(self) -> dict | None:
        """The sim has no out-of-band link to report on."""
        return None

    async def close(self) -> None:
        """No-op: the sim owns no external equipment to release."""
        return None


class SimBackend:
    """The Simulator vendor adapter.

    ``open()`` builds the sim rig once and wraps it in a ``SimSession``. Not
    discoverable (no network presence), so ``discover()`` returns ``[]``.
    """

    name = "sim"
    label = "Simulator"
    roles = ROLES
    discoverable = False

    async def open(self, conn: ConnSpec) -> BackendSession:
        """Build one coherent sim rig and return a session over it.

        Deferred import of ``build_sim_rig`` (it pulls in numpy) so merely
        registering this backend at import time stays cheap."""
        from ..sim import build_sim_rig

        return SimSession(build_sim_rig())

    async def discover(self) -> list[dict]:
        """The sim has no network presence."""
        return []


# Self-register at import (last-registration-wins). ``register`` returns the
# instance; we keep a module-level handle for convenience/introspection.
SIM_BACKEND = register(SimBackend())
