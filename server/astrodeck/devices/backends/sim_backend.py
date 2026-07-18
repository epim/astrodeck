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

#: Sim-tuned FAST calibration engine-config (P2-T3 default flip). The sim's
#: ``pulse_guide`` AND ``expose`` both sleep for their nominal duration, so an
#: untuned calibration walk (~25px legs, ~2s exposures, ~300ms+ pulses) costs
#: ~55s of wall clock per ``start_guiding``. A short guide exposure + a smaller
#: cal distance crossed in a few longer pulses cuts a full calibrate+settle to
#: ~12s (measured, converging to ~0.3px RMS) while still producing a valid
#: calibration the engine can guide on. Tuned ONLY here (the Python config dict)
#: — the Rust engine defaults are unchanged.
_SIM_CAL_CONFIG: dict = {
    "exposure_s": 0.15,
    "calibration_distance": 10.0,
    "calibration_duration_ms": 400,
    "max_steps": 25,
}


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
        self._solver: object | None = None

    @property
    def shared_state(self) -> object | None:
        """The single ``SimRig`` state object every sim device shares (the rig's
        ``"_rig"`` entry), or None. Public, read-only accessor so the hub can keep
        setting ``self.sim_rig`` without reaching into a private attribute."""
        return self._rig.get("_rig")

    def guide_camera(self) -> object | None:
        """The sim's dedicated guide-camera device (the rig's ``"guide_camera"``
        entry), or None. Protocol accessor (W1.3): the orchestrator calls this on
        the camera-role session and surfaces it as ``ConnectResult.guide_camera``,
        so the hub keeps populating ``self.devices['guide_camera']`` without
        reaching the (now-removed) ``SimSession``-only property. SYNC by contract."""
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
        """The sim's own guider, created lazily and once.

        P2-T3 DEFAULT FLIP: sim rigs now guide with the native Rust-engine
        ``NativeGuider`` by DEFAULT — the SAME closed loop the P1 e2e gate
        exercises — so the simulator exercises the vendor-neutral guiding path
        AstroDeck ships (provider resolution badges this rig ``sim``). It is
        built with a sim-tuned FAST calibration config (``_SIM_CAL_CONFIG``:
        short cal pulses + a small cal distance) because the sim's
        ``pulse_guide`` sleeps for each pulse's duration, so an untuned
        calibration walk would cost tens of seconds of wall clock per
        ``start_guiding`` — the fast config keeps a guided sim session near the
        old ``SimGuider`` speed. The persisted per-axis algorithm selection
        (``AppConfig.guide``) rides along via ``guide_algo_config()``.

        The legacy believable-stream ``SimGuider`` (fast, deterministic; some
        sequence tests spy on its ``dither_count`` / drive its ``_guiding``) is
        still reachable behind an EXPLICIT escape hatch: set
        ``ASTRODECK_SIM_LEGACY_GUIDER`` (truthy). The native guider also falls
        back to ``SimGuider`` when the engine wheel is absent (so a wheel-less
        box stays green). SYNC by contract; the hub uses this in place of PHD2
        for the sim rig."""
        if self._guider is None:
            import os

            from ... import providers

            gcam = self._rig.get("guide_camera")
            tel = self._rig.get("telescope")
            legacy = (os.environ.get("ASTRODECK_SIM_LEGACY_GUIDER") or "").strip()
            want_legacy = legacy.lower() not in ("", "0", "false", "no", "off")
            if (not want_legacy and providers.NATIVE_AVAILABLE
                    and gcam is not None and tel is not None):
                # Deferred import: keep module load light (the native guider pulls
                # the guide stack / numpy).
                from ...guide.native import NativeGuider, guide_algo_config

                rig = self._rig.get("_rig")
                scale = float(getattr(rig, "guide_scale_arcsec_px", 1.0) or 1.0)
                self._guider = NativeGuider(
                    gcam, tel,
                    config={"image_scale_arcsec": scale,
                            **_SIM_CAL_CONFIG, **guide_algo_config()},
                    profile_id="sim")
            else:
                # Deferred import: keep module import light and avoid pulling the
                # guide stack in just to register the backend.
                from ...guide import SimGuider

                self._guider = SimGuider()
        return self._guider

    def native_solver(self) -> object | None:
        """The guarded ``SimSolver`` bound to THIS session's shared ``SimRig``.

        Because it carries the sim rig it knows the frame's provenance is sim, so
        the orchestrator's ``_pick_solver`` may safely consult it for the CAMERA
        role's session. A real-camera session (nina/native) returns None from its
        own ``native_solver``, so the hub falls back to ASTAP / a refusing
        ``SimSolver`` and the sim solver never runs against a real mount (W1.3/
        W1.5). ``mode="sim"`` so the per-role guard treats this frame as fake.
        SYNC by contract; built lazily and once."""
        if self._solver is None:
            # Deferred import: keep module import light (solve pulls the solver
            # stack) and avoid a cycle just to register the backend.
            from ...solve import SimSolver

            self._solver = SimSolver(self._rig.get("_rig"), mode="sim")
        return self._solver

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
    #: Endpoint-less: one shared SimRig per open, so the orchestrator coalesces
    #: every sim role into the single key ``("sim", None, None)`` regardless of
    #: stray host/port on an override (W1.3).
    hostless = True

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
