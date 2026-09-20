"""Small, bounded sky searches for Guided setup. These functions never move hardware."""
from __future__ import annotations
import math
import time
from .catalog.coords import altaz, lst_hours
from .sequence.schedule import effective_floor


def separation(ra: float, dec: float, other_ra: float, other_dec: float) -> float:
    a, b = math.radians(dec), math.radians(other_dec)
    c = math.sin(a)*math.sin(b) + math.cos(a)*math.cos(b)*math.cos(math.radians((ra-other_ra)*15))
    return math.degrees(math.acos(max(-1, min(1, c))))


def sky_context(site, now):
    from astropy.coordinates import get_sun
    from astropy.time import Time
    sun = get_sun(Time(now, format="unix"))
    ra, dec = float(sun.ra.hour), float(sun.dec.deg)
    return ra, dec, altaz(ra, dec, site["latitude"], site["longitude"], now)[0]


def simulated_equipment(hub) -> bool:
    """Allow daylight practice only for positively identified simulator devices.

    The hub's mode can still say sim on a mixed rig. Missing, disconnected or
    unknown camera/mount devices, and any connected non-sim device, fail closed.
    """
    from .devices import sim
    devices = getattr(hub, "devices", {})
    connected = {role: dev for role, dev in devices.items() if getattr(dev, "connected", False)}
    if type(connected.get("camera")) not in (sim.SimCamera, sim.SimColorCamera):
        return False
    if type(connected.get("telescope")) is not sim.SimTelescope:
        return False
    known = (sim.SimCamera, sim.SimColorCamera, sim.SimGuideCamera,
             sim.SimTelescope, sim.SimFocuser, sim.SimRotator, sim.SimFilterWheel,
             sim.SimSwitch, sim.SimSafetyMonitor, sim.SimCoverCalibrator,
             sim.SimDome, sim.SimRotatingDome)
    return all(type(dev) in known and not getattr(dev, "hardware", False)
               for dev in connected.values())


def polar_field(site, safety, *, now=None, sun=None, simulation=False):
    """Sample the entire 24-degree native RA arc, plus ten minutes of drift.

    Both directions are checked because the native driver may choose by pier
    side after its first plate solve. A recommendation is sky clearance only;
    the user must confirm physical clearance and every move retains API guards.
    """
    now = time.time() if now is None else now
    if site.get("is_default", True):
        return {"field": None, "reason": "Save your observing location first."}
    sun_ra, sun_dec, sun_alt = sun or sky_context(site, now)
    if sun_alt > -6 and not simulation:
        return {"field": None, "reason": "Wait until dusk to choose a field with visible stars."}
    from .polar.native import _RA_STEP_HOURS
    span = _RA_STEP_HOURS * 2
    lat, lon = site["latitude"], site["longitude"]
    floor = max(20, safety.min_alt_deg, site.get("horizon_min_deg", 0))
    candidates = []
    for ha in (-4, -3, -2, 2, 3, 4):
        ra = (lst_hours(lon, now) - ha) % 24
        for dec in range(-60, 61, 10):
            clearance = 90.0
            valid = True
            for direction in (-1, 1):
                for i in range(13):
                    sample_ra = (ra + direction * span * i / 12) % 24
                    if separation(sample_ra, dec, sun_ra, sun_dec) < max(45, safety.solar_exclusion_deg + 5):
                        valid = False; break
                    for dt in (0, 600):
                        alt, az = altaz(sample_ra, dec, lat, lon, now + dt)
                        margin = alt - effective_floor(floor, safety.horizon, az, safety.nogo_box)
                        if margin < 5 or alt > min(80, safety.max_alt_deg - 2):
                            valid = False; break
                        clearance = min(clearance, margin)
                    if not valid: break
                if not valid: break
            if valid:
                alt, az = altaz(ra, dec, lat, lon, now)
                candidates.append({"ra_hours":ra,"dec_deg":dec,"alt":alt,"az":az,"clearance_deg":clearance})
    if not candidates:
        return {"field":None,"reason":"No clear alignment arc was found. Review the horizon or try later; use Pro to choose a field yourself."}
    best = max(candidates, key=lambda row: row["clearance_deg"] - abs(row["alt"]-50)*.2)
    # Operators may see sky-derived coordinates without access to the private
    # site position. Include the display arc so their dome stays useful too.
    def sky_position(ra):
        alt, az = altaz(ra % 24, best["dec_deg"], lat, lon, now)
        return {"alt": alt, "az": az}
    sky = {"at": now, "field": sky_position(best["ra_hours"]),
           "arcs": [[sky_position(best["ra_hours"] + direction * span * i / 24)
                     for i in range(25)] for direction in (-1, 1)]}
    return {"field":best,"expires_at":now+120,"arc_deg":span*15,"sky":sky,"reason":None}


def first_targets(site, safety, *, now=None, sun=None, simulation=False):
    """Rank the small curated catalog at the current instant, without nightly ephemerides."""
    from .catalog.objects import CATALOG, _TYPE_NAMES
    from .catalog.difficulty import difficulty_for
    now = time.time() if now is None else now
    if site.get("is_default", True):
        return {"picks":[],"reason":"Save your observing location first."}
    sun_ra, sun_dec, sun_alt = sun or sky_context(site, now)
    if sun_alt > -6 and not simulation:
        return {"picks":[],"reason":"The sky is still too bright. Try again after dusk."}
    picks = []
    for obj in CATALOG:
        if obj.mag is None: continue
        difficulty = difficulty_for(obj.id, obj.mag, obj.size_arcmin)
        if difficulty["tier"] != "easy": continue
        alt, az = altaz(obj.ra_hours, obj.dec_deg, site["latitude"], site["longitude"], now)
        floor = effective_floor(max(25, safety.min_alt_deg, site.get("horizon_min_deg", 0)), safety.horizon, az, safety.nogo_box)
        later_alt, later_az = altaz(obj.ra_hours, obj.dec_deg, site["latitude"], site["longitude"], now+1800)
        later_floor = effective_floor(max(25, safety.min_alt_deg, site.get("horizon_min_deg", 0)), safety.horizon, later_az, safety.nogo_box)
        if alt < floor+3 or later_alt < later_floor+3 or max(alt,later_alt) > safety.max_alt_deg-2 or separation(obj.ra_hours,obj.dec_deg,sun_ra,sun_dec)<max(45,safety.solar_exclusion_deg+5): continue
        picks.append({"id":obj.id,"name":obj.name,"type":_TYPE_NAMES[obj.type],"ra_hours":obj.ra_hours,"dec_deg":obj.dec_deg,
                      "mag":obj.mag,"size_arcmin":obj.size_arcmin,"alt":round(alt,1),"az":round(az,1),"difficulty":"easy"})
    picks.sort(key=lambda obj: obj["alt"] - obj["mag"]*2, reverse=True)
    return {"picks":picks[:5],"reason":None if picks else "No easy targets clear your horizon right now. Try again later or explore the atlas in Pro."}
