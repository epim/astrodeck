"""Cloud occlusion geometry and, in later stages, satellite cloud products.

Stage 1 is :mod:`astrodeck.cloudmap.geometry` alone: pure coordinate geometry
with no I/O, no config, and no dependency beyond stdlib ``math``.
"""
from .geometry import (
    EARTH_RADIUS_KM,
    GEOSTATIONARY_ALT_KM,
    GEOSTATIONARY_RADIUS_KM,
    GeoPoint,
    LookVector,
    Site,
    agl_to_msl_km,
    beam_footprint_km,
    deparallax,
    look_from,
    pierce_point,
    satellite_look,
    slant_to_layer_km,
)

__all__ = [
    "EARTH_RADIUS_KM",
    "GEOSTATIONARY_RADIUS_KM",
    "GEOSTATIONARY_ALT_KM",
    "Site",
    "GeoPoint",
    "LookVector",
    "look_from",
    "pierce_point",
    "slant_to_layer_km",
    "satellite_look",
    "deparallax",
    "beam_footprint_km",
    "agl_to_msl_km",
]
