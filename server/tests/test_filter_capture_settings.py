"""Per-filter exposure and gain (#215, extending #148).

THE PROMISE: a filter you have measured settings for does not have to be
re-typed. Pick Ha and the exposure and gain that Ha needs are already there.

THE SHAPE, and the thing these tests mostly exist to pin: they are DEFAULTS,
not an override. They are consumed where a value is being CHOSEN — the camera
dial when the filter changes, a plan step as it is created — and the sequence
engine never reads them. A plan is a reviewable artifact; rewriting its
exposures underneath the operator at capture time would make the plan on screen
stop describing the night, which is the same invisible-wrong-config shape as
the profile-pinned provider that ran a simulated polar aligner for twelve days.

There is exactly ONE authoritative reader, and it is the offset-learning focus
sweep, because there is no plan there to consult: a sweep is a measurement that
either works or wastes the night. The 2026-08-08 run measured L/R/G/B and could
not focus S, Ha or Oiii at the single setting it had, which is the defect this
whole feature comes from.

TRI-STATE, throughout. Unlike a focus offset — where 0 is a real value meaning
"no shift" — 0 is a real GAIN and a 0-second exposure is not "unset" either. So
"not pinned" is its own state, and it is None in the file, on the device, over
the API and in the picker.
"""
from __future__ import annotations

import astrodeck.config as configmod
from astrodeck.hub import Hub


# ------------------------------------------------------------- the store

def test_the_store_round_trips_pins_and_the_absence_of_pins(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    configmod.save_filter_config("p1", ["L", "Ha"], [0, 12],
                                 exposures=[None, 30.0], gains=[None, 100])
    saved = configmod.load_filter_config("p1")
    assert saved["exposures"] == [None, 30.0]
    assert saved["gains"] == [None, 100]


def test_an_older_caller_that_sends_neither_leaves_no_key(tmp_path, monkeypatch):
    """The two fields are optional so a client written before them keeps working
    — and, crucially, does not CLEAR pins it does not know about by omitting
    them. Absent key means "this caller has nothing to say", exactly as it
    already does for the blackout and narrowband flags."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    configmod.save_filter_config("p1", ["L"], [0])
    saved = configmod.load_filter_config("p1")
    assert "exposures" not in saved and "gains" not in saved


def test_zero_is_a_value_and_empty_is_not(tmp_path, monkeypatch):
    """The whole reason these are tri-state. A 0 gain is a real setting on every
    CMOS camera in the product; if 0 meant "unset" the operator could not pin
    it, and the dial would helpfully overwrite it every time."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    configmod.save_filter_config("p1", ["L", "R", "G"], [0, 0, 0],
                                 exposures=[0.5, None, ""], gains=[0, None, ""])
    saved = configmod.load_filter_config("p1")
    assert saved["gains"] == [0, None, None], "0 must survive; '' must not"
    assert saved["exposures"] == [0.5, None, None]


# ------------------------------------------------------- apply and persist

async def test_pins_apply_to_the_wheel_and_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        exps = [None] * n
        gains = [None] * n
        exps[4], gains[4] = 30.0, 100          # the sim wheel's Ha slot
        res = await h.set_filter_names([""] * n, None, None, None, exps, gains)
        assert res["exposures"][4] == 30.0
        assert res["gains"][4] == 100
        assert fw.slot_capture_settings(4) == (30.0, 100)
        assert fw.slot_capture_settings(0) == (None, None)
        saved = configmod.load_filter_config(
            configmod.config_store.cfg().active_profile_id)
        assert saved["exposures"][4] == 30.0
    finally:
        await h.disconnect_all()


async def test_a_pin_can_be_cleared(tmp_path, monkeypatch):
    """Sent whole, like the flag lists, and for the same reason: the caller owns
    the list, so removing the last pin has to be expressible. A merge-per-index
    write would make a pin permanent once set."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        exps = [None] * n
        exps[4] = 30.0
        await h.set_filter_names([""] * n, None, None, None, exps, [None] * n)
        assert fw.slot_capture_settings(4)[0] == 30.0
        await h.set_filter_names([""] * n, None, None, None,
                                 [None] * n, [None] * n)
        assert fw.slot_capture_settings(4) == (None, None)
    finally:
        await h.disconnect_all()


async def test_each_half_is_pinned_independently(tmp_path, monkeypatch):
    """An operator who knows Ha needs 30 s but is happy with the current gain
    pins one and not the other. If the pair had to move together, the only way
    to express that would be to re-pin a gain they did not choose."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        exps = [None] * n
        exps[4] = 30.0
        await h.set_filter_names([""] * n, None, None, None, exps, [None] * n)
        assert fw.slot_capture_settings(4) == (30.0, None)
    finally:
        await h.disconnect_all()


async def test_a_blackout_slot_cannot_keep_capture_settings(tmp_path, monkeypatch):
    """A slot with no light path has no exposure "for that filter" — there is no
    filter. Cleared on the same terms as its focus offset and its narrowband
    flag, and on EVERY save, so marking a slot blackout later also clears the
    settings pinned while it was a real filter."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        exps, gains = [None] * n, [None] * n
        exps[2], gains[2] = 30.0, 100
        await h.set_filter_names([""] * n, None, None, None, exps, gains)
        assert fw.slot_capture_settings(2) == (30.0, 100)
        opaque = [False] * n
        opaque[2] = True
        await h.set_filter_names([""] * n, None, opaque, None, None, None)
        assert fw.slot_capture_settings(2) == (None, None)
    finally:
        await h.disconnect_all()


async def test_pins_survive_a_reconnect(tmp_path, monkeypatch):
    """The reconnect is the event that erases everything the wheel does not
    report — which is why the seeding path exists at all. A pin that survived
    the file and not the reconnect would be a setting the operator had to
    re-enter every time the rig came up, i.e. the problem this replaces."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        exps, gains = [None] * n, [None] * n
        exps[4], gains[4] = 30.0, 100
        await h.set_filter_names([""] * n, None, None, None, exps, gains)
    finally:
        await h.disconnect_all()

    h2 = Hub()
    await h2.connect_sim()
    try:
        assert h2.devices["filterwheel"].slot_capture_settings(4) == (30.0, 100)
    finally:
        await h2.disconnect_all()


async def test_the_status_payload_carries_them(tmp_path, monkeypatch):
    """The dial seeds from status, which the client already polls. Behind a
    second fetch, a filter change would have to WAIT to know what to show."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        exps = [None] * n
        exps[4] = 30.0
        await h.set_filter_names([""] * n, None, None, None, exps, [None] * n)
        st = await h.poll_status()
        assert st["filterwheel"]["exposures"][4] == 30.0
        assert st["filterwheel"]["gains"][4] is None
    finally:
        await h.disconnect_all()
