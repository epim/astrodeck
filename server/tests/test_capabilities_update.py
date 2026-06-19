"""system.update is admin-only and destructive."""
from astrodeck.auth.capabilities import (ALL_CAPS, CAP_SYSTEM_UPDATE,
                                          DESTRUCTIVE_CAPS, has_capability)


def test_in_all_caps_and_destructive():
    assert CAP_SYSTEM_UPDATE in ALL_CAPS
    assert CAP_SYSTEM_UPDATE in DESTRUCTIVE_CAPS


def test_only_admin_holds_it():
    assert has_capability("admin", CAP_SYSTEM_UPDATE)
    assert not has_capability("operator", CAP_SYSTEM_UPDATE)
    assert not has_capability("viewer", CAP_SYSTEM_UPDATE)
