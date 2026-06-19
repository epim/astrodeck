"""The supervisor and the server mirror the same contract constants -- assert they
never drift (they live in two packages that cannot import each other)."""
from astrodeck.update import protocol as SRV
from supervisor import protocol as SUP

_SHARED = [
    "EXIT_STOP", "EXIT_APPLY_UPDATE",
    "CURRENT_POINTER", "RELEASES_DIRNAME", "VENV_DIRNAME", "STATE_DIRNAME",
    "LAST_GOOD_FILE", "PENDING_FILE", "RESULT_FILE", "FAILED_DIRNAME",
]


def test_shared_constants_match():
    for name in _SHARED:
        assert getattr(SUP, name) == getattr(SRV, name), f"{name} drifted"


def test_apply_exit_code_is_92():
    assert SUP.EXIT_APPLY_UPDATE == 92 == SRV.EXIT_APPLY_UPDATE


def test_layouts_agree_on_paths(tmp_path):
    sup = SUP.Layout(tmp_path)
    srv = SRV.InstallLayout(tmp_path)
    assert sup.release("0.2.0") == srv.release("0.2.0")
    assert sup.pending == srv.pending
    assert sup.result == srv.result
    assert sup.current == srv.current
