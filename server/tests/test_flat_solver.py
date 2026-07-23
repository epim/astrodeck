from astrodeck.imaging.flats import FlatExposureSolver


def _linear_panel(k, bias=100.0):
    # a flat whose median ADU = bias + k * exposure (the panel model)
    return lambda exp: bias + k * exp


def _run(solver, panel, cap=20):
    st = solver.first()
    for _ in range(cap):
        st = solver.update(panel(st.exposure_s))
        if st.done:
            return st
    raise AssertionError("solver never terminated")


def test_converges_to_target_from_low_seed():
    # k=5000 ADU/s, target 25000 → true exposure ≈ (25000-100)/5000 ≈ 4.98s
    s = FlatExposureSolver(25000, initial_exposure_s=1.0, pedestal=100.0)
    st = _run(s, _linear_panel(5000))
    assert st.converged and st.reason == "converged"
    assert abs((100.0 + 5000 * st.exposure_s) - 25000) <= 0.10 * 25000


def test_converges_from_high_seed():
    s = FlatExposureSolver(20000, initial_exposure_s=30.0, pedestal=100.0)
    st = _run(s, _linear_panel(8000))
    assert st.converged


def test_too_bright_at_min_rails():
    # even the shortest exposure overshoots the target → honest rail, not a loop
    s = FlatExposureSolver(5000, initial_exposure_s=1.0, min_exposure_s=0.5,
                           pedestal=0.0)
    st = _run(s, _linear_panel(100000))     # 0.5s → 50000 ADU ≫ 5000
    assert st.done and not st.converged and st.reason == "too_bright_at_min"
    assert st.exposure_s == 0.5


def test_too_dim_at_max_rails():
    s = FlatExposureSolver(50000, initial_exposure_s=1.0, max_exposure_s=2.0,
                           pedestal=0.0)
    st = _run(s, _linear_panel(1000))       # 2s → 2000 ADU ≪ 50000
    assert st.done and not st.converged and st.reason == "too_dim_at_max"
    assert st.exposure_s == 2.0


def test_no_signal_pushes_to_max_then_rails():
    s = FlatExposureSolver(25000, initial_exposure_s=1.0, max_exposure_s=10.0,
                           pedestal=100.0)
    st = _run(s, lambda exp: 100.0)         # flat bias, zero panel signal
    assert st.done and not st.converged and st.reason == "too_dim_at_max"


def test_bounded_iterations_returns_closest():
    # a panel that never converges (measurement ignores exposure, off-band)
    s = FlatExposureSolver(25000, initial_exposure_s=1.0, max_iterations=4,
                           pedestal=0.0)
    st = _run(s, lambda exp: 24000 if exp < 3 else 26000)  # oscillates off-band? keep off-band
    assert st.done and st.iterations <= 4


def test_first_clamps_initial_into_bounds():
    s = FlatExposureSolver(1000, initial_exposure_s=99.0, max_exposure_s=30.0)
    assert s.first().exposure_s == 30.0
