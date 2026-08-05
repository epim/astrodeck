"""Capability taxonomy + role->capabilities map (W2.1).

This is the SINGLE SOURCE OF TRUTH for the capability strings and the role
table. There is deliberately NO monolithic ``view`` capability and NO
``config.mount_limits`` (both retired in W2.1) -- the boot-time route
assertion (auth/rbac wiring, owned elsewhere) rejects any route tagged with
either retired string.

Import-light by design: this module imports NOTHING from the package (no
``api.app``, no ``hub``, no ``config``) so it can be pulled into the boot
assertion and into tests without an import cycle.
"""
from __future__ import annotations

# --------------------------------------------------------------- capability set
# view.* = read surfaces; control.* = device motion/imaging; config.* = settings
# writes; admin.* = auth/remote administration.

CAP_VIEW_STATUS = "view.status"            # read-only status + WS subscribe
CAP_VIEW_PREVIEW = "view.preview"          # downsized preview frames (NOT raw FITS)
CAP_VIEW_MEDIA = "view.media"              # raw FITS / full-res science (bulk)
CAP_VIEW_SITE_PRECISE = "view.site_precise"  # precise lat/lon in hello/config frames
#: Values COMPUTED from the site: mount alt/az, a target's altitude verdict,
#: sun altitude, dark-window boundaries, visibility and framing answers.
#:
#: Split off ``view.site_precise`` for the same reason ``view.weather`` was
#: (2026-07-17 wave I2), and discovered the same way — by measuring what the
#: cap actually withheld. ``view.site_precise`` was implemented as a filter on
#: four KEY NAMES, which cannot withhold ``f(latitude, longitude)``: given the
#: mount's RA/Dec, an altitude pins the observer to a circle on the Earth and a
#: second sample collapses it to a point. The audit recovered the observatory to
#: 2.9 km from three requests a plain VIEWER is entitled to make.
#:
#: Operators hold this and viewers do not, which is the same line the owner
#: already drew for the radar map: an operator may learn the site REGION (they
#: plan and run sequences here, and cannot do either blind), while the precise
#: fix stays behind ``view.site_precise``. A viewer link — the thing you hand to
#: someone untrusted — discloses neither.
CAP_VIEW_SITE_DERIVED = "view.site_derived"
CAP_VIEW_WEATHER = "view.weather"          # forecast/Sky Conditions/radar (2026-07-17
                                            # decisions wave I2: split off view.site_precise
                                            # so operators get weather without precise site)

CAP_CONTROL_CAPTURE = "control.capture"    # imaging: capture/loop/AF, cooler, dew, focuser, filter
CAP_CONTROL_MOUNT = "control.mount"        # ALL mount MOTION (motion-boundary derived)
CAP_CONTROL_GUIDE = "control.guide"        # start/stop/dither guiding
CAP_CONTROL_POWER = "control.power"        # switch/set (can brown out the rig)

CAP_CONFIG_SAFETY = "config.safety"            # DESTRUCTIVE: pier/horizon floors + safety/simulate
CAP_CONFIG_SOLAR_OVERRIDE = "config.solar_override"  # daytime/sun-cone + force-bypass (seam)
CAP_CONFIG_BACKEND = "config.backend"          # backend/profile connect & apply + managed-PHD2 spawn
CAP_CONFIG_SITE_OPTICS = "config.site_optics"  # site coords + optics
CAP_CONFIG_ALERTS = "config.alerts"            # alert sinks, escalation, deadman_url (SSRF/exfil sink)
CAP_ADMIN_USERS = "admin.users"                # auth/remote config, role allowlist, jti revoke
CAP_SYSTEM_UPDATE = "system.update"            # DESTRUCTIVE: download + restart into a new release

ALL_CAPS = frozenset({
    CAP_VIEW_STATUS, CAP_VIEW_PREVIEW, CAP_VIEW_MEDIA, CAP_VIEW_SITE_PRECISE,
    CAP_VIEW_SITE_DERIVED, CAP_VIEW_WEATHER,
    CAP_CONTROL_CAPTURE, CAP_CONTROL_MOUNT, CAP_CONTROL_GUIDE, CAP_CONTROL_POWER,
    CAP_CONFIG_SAFETY, CAP_CONFIG_SOLAR_OVERRIDE, CAP_CONFIG_BACKEND,
    CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_ALERTS, CAP_ADMIN_USERS, CAP_SYSTEM_UPDATE,
})

# DESTRUCTIVE group (UI double-confirms even for admin; W2.4/W2.5). Pinned here
# so the future viewer/operator UI reads it from one place.
DESTRUCTIVE_CAPS = frozenset({
    CAP_CONFIG_SAFETY, CAP_CONFIG_SOLAR_OVERRIDE,
    CAP_CONTROL_MOUNT, CAP_CONTROL_POWER, CAP_ADMIN_USERS, CAP_SYSTEM_UPDATE,
})

# Retired capability strings. The boot-time route assertion fails create_app()
# if any route is tagged with one of these (they guarded empty/merged surfaces).
RETIRED_CAPS = frozenset({"view", "config.mount_limits"})

# ---------------------------------------------------------------- role -> caps
# The DEFAULT viewer set: live-watch only. EXCLUDES view.media +
# view.site_precise + view.weather (2026-07-17 decisions wave I2: weather
# visibility stops at operator; viewers stay fully stripped).
# A future relay viewer-LINK carries this same default set (W2.5/W3.3), so the
# local viewer role and the remote viewer link never drift apart.
VIEWER_LINK_CAPS = frozenset({CAP_VIEW_STATUS, CAP_VIEW_PREVIEW})

ROLES_CAP: dict[str, frozenset[str]] = {
    # NO raw FITS, NO precise site.
    "viewer": VIEWER_LINK_CAPS,
    # imaging + guiding + mount motion; NOT power/config/media. Product-owner
    # decision (2026-07-17 decisions wave, I1): operators are intended to run
    # sequences (future: telescope-rental interface), so operator holds
    # control.mount -- sequence run/pause/resume/abort, session regrade, and
    # mount slewing are all gated on control.mount and are now operator-usable.
    # Product-owner decision (2026-07-17 decisions wave, I2): full weather
    # (forecast, Sky Conditions, radar map) is also visible to operators --
    # the owner accepts that the radar map's tile coordinates disclose the
    # site region to an operator (consistent with the rental model), so
    # operator holds view.weather even though it still lacks
    # view.site_precise. Still NOT control.power/config.*/view.media/
    # view.site_precise (the exact GPS fix stays admin-only everywhere else).
    "operator": frozenset({
        CAP_VIEW_STATUS, CAP_VIEW_PREVIEW, CAP_VIEW_WEATHER,
        CAP_VIEW_SITE_DERIVED,
        CAP_CONTROL_CAPTURE, CAP_CONTROL_GUIDE, CAP_CONTROL_MOUNT,
    }),
    # everything incl. view.media, view.site_precise.
    "admin": ALL_CAPS,
}

# Ordering = privilege rank (viewer is the ceiling for an untrusted default_role).
ROLES = ("viewer", "operator", "admin")


def caps_for_role(role: str) -> frozenset[str]:
    """Resolve a role name -> its capability set. Unknown role -> empty set
    (fail-closed: an unrecognized role holds NOTHING, never admin)."""
    return ROLES_CAP.get(role, frozenset())


def has_capability(role: str, cap: str) -> bool:
    """True iff ``role`` (by the role->caps map) holds ``cap``.

    Fail-closed for unknown roles and unknown capabilities."""
    return cap in caps_for_role(role)


def role_rank(role: str) -> int:
    """Privilege rank of a role (index in ROLES); -1 for an unknown role.

    Used to validate a default_role ceiling (e.g. a WAN default must be at most
    ``viewer`` unless a Workspace hosted-domain is pinned)."""
    try:
        return ROLES.index(role)
    except ValueError:
        return -1
