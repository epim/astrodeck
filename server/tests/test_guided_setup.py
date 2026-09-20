import math
from types import SimpleNamespace
import pytest
from _simhub import sim_hub  # noqa: F401
from astrodeck.config import SafetyConfig
from astrodeck.guided import polar_field, first_targets, separation, simulated_equipment
from astrodeck.catalog.coords import altaz
from astrodeck.site_gps import parse_gga, read_usb_gps

SITE = {"latitude":35., "longitude":-115., "is_default":False}
NOW = 1758000000
SUN = (0, 0, -30)

def sentence(body):
    checksum = 0
    for c in body: checksum ^= ord(c)
    return f"${body}*{checksum:02X}"

def test_gps_accepts_valid_fix_and_rejects_checksum_nofix_and_invalid_coordinates():
    body = "GPGGA,123519,3500.000,N,11500.000,W,1,08,0.9,100.0,M,46.9,M,,"
    fix = parse_gga(sentence(body))
    assert fix["latitude"] == 35 and fix["longitude"] == -115 and fix["elevation_m"] == 100
    assert parse_gga(sentence(body)[:-2]+"00") is None
    assert parse_gga(sentence(body.replace(",1,08,", ",0,08,"))) is None
    assert parse_gga(sentence(body.replace("3500.000","3561.000"))) is None
    assert parse_gga(sentence(body.replace("100.0,M","nan,M"))) is None
    assert parse_gga("junk") is None

def test_generic_serial_adapters_are_not_opened(monkeypatch):
    from serial.tools import list_ports
    import serial
    monkeypatch.setattr(list_ports,"comports",lambda:[SimpleNamespace(vid=0x1234,description="USB serial",manufacturer="FTDI",product="bridge")])
    monkeypatch.setattr(serial,"Serial",lambda **kw: pytest.fail("generic port opened"))
    assert read_usb_gps() == {"available":False,"detected":False,"detail":"No GPS detected"}

def test_identified_gps_is_read_without_transmitting(monkeypatch):
    from serial.tools import list_ports
    import serial
    monkeypatch.setattr(list_ports,"comports",lambda:[SimpleNamespace(vid=0x1546,device="TEST",description="GNSS",manufacturer="u-blox",product="GPS")])
    class Receiver:
        def __init__(self,**kw): assert kw["port"] is None
        def open(self): assert self.dtr is False and self.rts is False
        def readline(self,size): return sentence("GNGGA,123519,3500.000,S,11500.000,E,1,08,0.9,100.0,M,46.9,M,,").encode()
        def write(self,*args): pytest.fail("GPS query transmitted data")
        def close(self): pass
    monkeypatch.setattr(serial,"Serial",Receiver)
    assert read_usb_gps()["latitude"] == -35

@pytest.mark.parametrize("sun",[(0,0,15),(0,0,-5)])
def test_no_guided_targets_or_motion_fields_in_daylight(sun):
    assert polar_field(SITE,SafetyConfig(),now=NOW,sun=sun)["field"] is None
    assert first_targets(SITE,SafetyConfig(),now=NOW,sun=sun)["picks"] == []

def test_unset_site_blocks_search():
    assert polar_field({},SafetyConfig(),now=NOW,sun=SUN)["field"] is None
    assert first_targets({},SafetyConfig(),now=NOW,sun=SUN)["picks"] == []

def test_high_horizon_and_low_ceiling_reject_fields():
    assert polar_field(SITE,SafetyConfig(horizon=[[0,85],[360,85]]),now=NOW,sun=SUN)["field"] is None
    assert polar_field(SITE,SafetyConfig(max_alt_deg=10),now=NOW,sun=SUN)["field"] is None
    assert first_targets(SITE,SafetyConfig(horizon=[[0,85],[360,85]]),now=NOW,sun=SUN)["picks"] == []

@pytest.mark.parametrize("simulation,sun", [(False, SUN), (True, (0,0,15))])
def test_both_arcs_clear_horizon_and_sun_with_drift(simulation, sun):
    result=polar_field(SITE,SafetyConfig(min_alt_deg=20),now=NOW,sun=sun,simulation=simulation)
    field=result["field"]
    assert field and result["expires_at"] == NOW+120
    sky = result["sky"]
    assert sky["at"] == NOW and len(sky["arcs"]) == 2
    assert set(sky) == {"at", "field", "arcs"}  # No private site coordinates.
    for direction, arc in zip((-1, 1), sky["arcs"]):
        assert len(arc) == 25 and arc[0] == sky["field"]
        for i in (12, 24):
            alt, az = altaz((field["ra_hours"] + direction * result["arc_deg"]/15*i/24) % 24,
                           field["dec_deg"], SITE["latitude"], SITE["longitude"], NOW)
            assert arc[i] == {"alt": alt, "az": az}
    span=result["arc_deg"]/15
    for sign in [-1,1]:
        for i in range(13):
            ra=(field["ra_hours"]+sign*span*i/12)%24
            for dt in [0,600]:
                alt,_=altaz(ra,field["dec_deg"],SITE["latitude"],SITE["longitude"],NOW+dt)
                assert 25 <= alt <= 80
            assert separation(ra,field["dec_deg"],SUN[0],SUN[1])>=45

@pytest.mark.parametrize("simulation,sun", [(False, SUN), (True, (0,0,15))])
def test_first_targets_are_bounded_easy_and_clear_later(simulation, sun):
    picks=first_targets(SITE,SafetyConfig(),now=NOW,sun=sun,simulation=simulation)["picks"]
    assert 0<len(picks)<=5
    for p in picks:
        assert p["difficulty"]=="easy"
        assert separation(p["ra_hours"],p["dec_deg"],sun[0],sun[1])>=45
        for dt in [0,1800]:
            alt,_=altaz(p["ra_hours"],p["dec_deg"],SITE["latitude"],SITE["longitude"],NOW+dt)
            assert alt>=28


def test_daylight_simulation_still_requires_site_and_clear_horizon():
    for site, safety in [({}, SafetyConfig()), (SITE, SafetyConfig(horizon=[[0,85],[360,85]]))]:
        assert polar_field(site,safety,now=NOW,sun=(0,0,15),simulation=True)["field"] is None
        assert first_targets(site,safety,now=NOW,sun=(0,0,15),simulation=True)["picks"] == []


async def test_daylight_practice_requires_known_connected_sim_equipment(sim_hub):
    assert simulated_equipment(sim_hub)
    original = dict(sim_hub.devices)
    try:
        for role in ("camera", "telescope", "focuser", "rotator", "guide_camera"):
            for hardware in (True, False):
                sim_hub.devices = {**original, role:SimpleNamespace(connected=True,hardware=hardware)}
                assert not simulated_equipment(sim_hub), role
        for role in ("camera", "telescope"):
            sim_hub.devices = {key:dev for key,dev in original.items() if key != role}
            assert not simulated_equipment(sim_hub)
            sim_hub.devices = dict(original)
            dev = sim_hub.devices[role]
            dev.connected = False
            try:
                assert not simulated_equipment(sim_hub)
            finally:
                dev.connected = True
    finally:
        sim_hub.devices = original

def test_polar_reading_time_changes_only_with_new_measurement(monkeypatch):
    from astrodeck.polar.session import PolarAlignSession
    from astrodeck.polar import session
    instance=object.__new__(PolarAlignSession)
    instance._native_paused=False
    instance.state=PolarAlignSession._idle()
    monkeypatch.setattr(session.bus,"publish",lambda *a,**kw:None)
    monkeypatch.setattr(session.time,"time",lambda:100.)
    instance._publish(az_error=1,alt_error=1)
    assert instance.state["reading_ts"]==100
    monkeypatch.setattr(session.time,"time",lambda:200.)
    instance._publish(activity="solving")
    assert instance.state["reading_ts"]==100


async def test_first_capture_confirmation_is_correlated_and_does_not_expose_path(sim_hub):
    from astrodeck.hub import external_preview
    saved = await sim_hub.capture(.1,100,30,save=True,target="Test star field",request_id="guided_test_1")
    assert saved["capture_request_id"] == "guided_test_1"
    assert saved["capture_saved"] is True
    public = external_preview(saved)
    assert "saved_path" not in public
    assert public["capture_request_id"] == "guided_test_1"
    unsaved = await sim_hub.capture(.1,100,30,save=False,request_id="guided_test_2")
    assert unsaved["capture_saved"] is False
    assert unsaved["capture_request_id"] == "guided_test_2"


def test_guided_gps_and_sky_search_permissions(tmp_path, monkeypatch):
    from test_rbac_enforcement import _make_client, _install, _principal_with, _FakeTel
    from astrodeck.auth import principal_for_role, CAP_CONFIG_SITE_OPTICS
    from fastapi.testclient import TestClient
    import astrodeck.hub as hub_mod
    import astrodeck.site_gps as gps
    _, app = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(hub_mod.hub,"devices",{"telescope":_FakeTel(35,-115,100)})
    monkeypatch.setattr(gps,"read_usb_gps",lambda:{"available":False,"detected":False})
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        assert c.get("/api/site/mount-gps?detected_only=true").status_code==403
        assert c.get("/api/guided/polar-field").status_code==403
        assert c.get("/api/guided/first-targets").status_code==403
    _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
    monkeypatch.setattr(hub_mod.hub,"devices",{"telescope":_FakeTel(35,-115,100)})
    with TestClient(app) as c:
        assert c.get("/api/site/mount-gps").json()["available"] is True
        assert c.get("/api/site/mount-gps?detected_only=true").json()["available"] is False
    from astrodeck.auth import CAP_VIEW_SITE_DERIVED
    import astrodeck.providers as providers
    _install(_principal_with(CAP_VIEW_SITE_DERIVED))
    monkeypatch.setattr(providers,"resolve",lambda *args:SimpleNamespace(kind="backend"))
    with TestClient(app) as c:
        result=c.get("/api/guided/polar-field").json()
        assert result["field"] is None and "provider" in result["reason"]
    monkeypatch.setattr(providers,"resolve",lambda *args:SimpleNamespace(kind="sim"))
    monkeypatch.setattr(providers,"_rig_has_real_motion",lambda *args:True)
    with TestClient(app) as c:
        result=c.get("/api/guided/polar-field").json()
        assert result["field"] is None and "simulated" in result["reason"]
