"""Where the guide scope points, relative to where the OTA points.

The guide scope is bolted to the OTA and does not look at the same piece of sky:
on this rig the operator can see the misalignment by eye. Plate-solve a frame
from each camera at the same moment and the difference between the two solved
centres IS the offset, in arcseconds, sign and all. No star-hopping, no eyepiece
reticle, no measuring tape.

WHY IT IS NOT SIMPLY A RA/DEC DIFFERENCE. The two scopes are rigidly joined, so
the offset is fixed in the INSTRUMENT frame -- but the instrument rotates
against the sky. A German mount flips through 180 degrees at the meridian and a
rotator turns the imaging train independently, so the same physical offset
appears at a different position angle every time either changes. Stored as a
raw RA/Dec pair it would be right until the first flip and quietly wrong after.

So it is stored the way it physically is: a SEPARATION and a POSITION ANGLE
measured against the imaging frame's own solved orientation. Rotating that back
out at apply time is exact for any rotator angle and any pier side, and needs
nothing remembered about the conditions except what the solve already reports.

WHAT THIS MODULE DOES NOT DO. It never touches a camera, a mount or a solver --
it is arithmetic on two solutions, so it can be tested without a sky. The
capture-and-solve half lives on the hub, where the devices are.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "GuideOffset",
    "STALE_AFTER_DAYS",
    "offset_from_solves",
    "aim_for",
    "staleness",
]

#: How old a measurement may be before it is called stale.
#:
#: THE HARDWARE ONLY MOVES IF SOMEBODY MOVES IT, so an age limit is a proxy for
#: "has this rig been handled", not for drift -- nothing about a bolted guide
#: scope decays on a schedule. Thirty days is long enough that a permanent pier
#: setup is never nagged and short enough that a rig which travels gets asked
#: again before a season's data is taken through a wrong offset.
#:
#: It WARNS, it does not block, and that is the operator's call recorded here:
#: a stale offset is still enormously better than no offset, and the person who
#: knows whether the guide scope was unbolted is the person at the telescope.
STALE_AFTER_DAYS = 30.0

_ARCSEC = 3600.0


@dataclass(frozen=True)
class GuideOffset:
    """Where the guide scope looks, relative to the OTA, in the instrument frame.

    ``pa_deg`` is measured FROM the imaging frame's own position angle, so it
    survives a meridian flip and any rotator move. ``measured_pa_deg`` is kept
    only so a human can see the conditions of the measurement; nothing reads it
    to correct anything.
    """

    sep_arcsec: float
    pa_deg: float
    measured_ts: float
    #: The imaging frame's sky position angle when this was measured.
    measured_pa_deg: float
    #: Identity of the two cameras. A different camera is a different geometry,
    #: whatever the age says.
    camera: str = ""
    guide_camera: str = ""
    #: Free text from the measurement -- how many stars solved, residuals.
    note: str = ""


def _sep_pa(ra1_h: float, dec1_d: float, ra2_h: float, dec2_d: float
            ) -> tuple[float, float]:
    """Angular separation (arcsec) and position angle (deg E of N) from 1 to 2.

    Spherical, not the small-angle approximation. The offset here is arcminutes
    and the flat form would be fine, but a guide scope on a wide finder can be
    degrees off and near the pole the cos(dec) term stops being a detail.
    """
    ra1 = math.radians(ra1_h * 15.0)
    ra2 = math.radians(ra2_h * 15.0)
    d1 = math.radians(dec1_d)
    d2 = math.radians(dec2_d)
    dra = ra2 - ra1

    sep = math.acos(max(-1.0, min(1.0,
        math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra))))
    pa = math.atan2(
        math.sin(dra) * math.cos(d2),
        math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(dra),
    )
    return math.degrees(sep) * _ARCSEC, math.degrees(pa) % 360.0


def offset_from_solves(*, main_ra_hours: float, main_dec_deg: float,
                       main_pa_deg: float, guide_ra_hours: float,
                       guide_dec_deg: float, measured_ts: float,
                       camera: str = "", guide_camera: str = "",
                       note: str = "") -> GuideOffset:
    """Turn two plate solves into a reusable offset.

    ``main_pa_deg`` is the imaging frame's own sky position angle, from its WCS.
    Subtracting it is what makes the result an INSTRUMENT-frame quantity: the
    same bolted geometry measured before and after a meridian flip produces the
    same ``pa_deg``, where the raw sky angle would differ by 180.
    """
    sep, pa = _sep_pa(main_ra_hours, main_dec_deg, guide_ra_hours, guide_dec_deg)
    return GuideOffset(
        sep_arcsec=sep,
        pa_deg=(pa - main_pa_deg) % 360.0,
        measured_ts=measured_ts,
        measured_pa_deg=main_pa_deg % 360.0,
        camera=camera,
        guide_camera=guide_camera,
        note=note,
    )


def aim_for(*, ra_hours: float, dec_deg: float, offset: GuideOffset,
            current_pa_deg: float) -> tuple[float, float]:
    """Where to POINT so that the thing you measured against lands on target.

    Forward only, and that is a correctness decision rather than a convenience.

    The first version of this was ``guide_to_main`` -- "given the guide centre,
    where is the OTA" -- implemented by walking ``sep`` back along ``pa + 180``.
    That is the flat-sky reversal: on a sphere the back azimuth is not the
    forward azimuth plus 180, because the meridians converge. Measured, the
    round trip missed by 0.5 arcsec at declination 20 and 13 arcsec at
    declination 84. Exactly the shape of error this project just spent a night
    removing from the cloud model, arriving from the opposite direction.

    It is also unnecessary. Every use is "I want the OTA here, where do I send
    the mount", which is a FORWARD displacement from the target -- the same
    function used to build the measurement, so the two cannot drift apart and
    there is no inverse to get subtly wrong.

    ``current_pa_deg`` is the imaging frame's position angle NOW. Passing the
    measurement's angle instead would be right until the first meridian flip.
    """
    return _offset_by(ra_hours, dec_deg, offset.sep_arcsec,
                      offset.pa_deg + current_pa_deg)


def _offset_by(ra_hours: float, dec_deg: float, sep_arcsec: float,
               pa_deg: float) -> tuple[float, float]:
    """Move a coordinate ``sep`` along position angle ``pa``, on the sphere."""
    d = math.radians(sep_arcsec / _ARCSEC)
    pa = math.radians(pa_deg)
    dec = math.radians(dec_deg)
    ra = math.radians(ra_hours * 15.0)

    sin_dec2 = math.sin(dec) * math.cos(d) + math.cos(dec) * math.sin(d) * math.cos(pa)
    dec2 = math.asin(max(-1.0, min(1.0, sin_dec2)))
    dra = math.atan2(math.sin(pa) * math.sin(d) * math.cos(dec),
                     math.cos(d) - math.sin(dec) * math.sin(dec2))
    return ((math.degrees(ra + dra) / 15.0) % 24.0, math.degrees(dec2))


def staleness(offset: GuideOffset | None, *, now: float, camera: str = "",
              guide_camera: str = "") -> str | None:
    """Why this offset should not be trusted blindly, or None.

    NEVER A REFUSAL. Every sentence here is a warning, by the operator's own
    instruction: "a stale one should warn not block". A stale offset is still
    far better than none, and the person who knows whether the guide scope was
    unbolted is standing next to it.

    Equipment identity outranks age. Thirty days on the same hardware is very
    likely still true; five minutes on a different guide camera is not true at
    all, because the sensor that defines the frame has changed.
    """
    if offset is None:
        return "no guide-scope offset has been measured, so a goto centres the "\
               "OTA only as well as the mount's own pointing does"

    if camera and offset.camera and camera != offset.camera:
        return (f"measured with a different imaging camera ({offset.camera!r}, "
                f"now {camera!r}) -- the offset describes hardware that is no "
                f"longer on the telescope")
    if guide_camera and offset.guide_camera and guide_camera != offset.guide_camera:
        return (f"measured with a different guide camera ({offset.guide_camera!r}, "
                f"now {guide_camera!r}) -- re-measure before trusting it")

    age_days = (now - offset.measured_ts) / 86400.0
    if age_days > STALE_AFTER_DAYS:
        return (f"measured {age_days:.0f} days ago; nothing about a bolted guide "
                f"scope drifts on its own, but if the rig has been moved or the "
                f"guide scope re-seated since then this is wrong")
    return None
