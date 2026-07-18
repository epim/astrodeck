import pytest

from astrodeck.devices.sim import build_sim_rig


@pytest.mark.asyncio
async def test_sim_mount_reports_pulse_guide_and_rates():
    rig = build_sim_rig()
    tel = rig["telescope"]
    await tel.connect()
    assert tel.can_pulse_guide is True
    rates = await tel.guide_rates()
    assert rates is not None
    ra, dec = rates
    assert ra > 0 and dec > 0


def test_base_telescope_defaults():
    from astrodeck.devices.base import Telescope
    assert Telescope.can_pulse_guide is False
