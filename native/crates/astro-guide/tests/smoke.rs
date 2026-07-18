// SPDX-License-Identifier: Apache-2.0
//
// Provenance: smoke test for the shared Action/Axis/AxisPulse/Direction/
// FrameMeta/MeasuredStar types (native/crates/astro-guide/src/types.rs). No
// code copied from PHD2.

use astro_guide::types::{Action, Axis, AxisPulse, Direction, FrameMeta, MeasuredStar};

#[test]
fn types_construct_and_match() {
    let a = Action::PulsePair {
        ra: Some(AxisPulse {
            dir: Direction::West,
            ms: 120,
        }),
        dec: None,
    };
    match a {
        Action::PulsePair {
            ra: Some(p),
            dec: None,
        } => {
            assert_eq!(p.ms, 120);
            assert!(matches!(p.dir, Direction::West));
        }
        _ => panic!("wrong variant"),
    }
    let _ = Action::Idle;
    let _ = Action::LockLost;
    let _ = Action::Settle;
    let _ = Action::Pulse {
        axis: Axis::Ra,
        dir: Direction::East,
        ms: 5,
    };
    let m = MeasuredStar {
        x: 1.0,
        y: 2.0,
        snr: 10.0,
        mass: 500.0,
        hfd: 3.0,
        found: true,
    };
    let f = FrameMeta {
        timestamp_s: 100.0,
        exposure_s: 2.0,
    };
    assert_eq!(m.x + f.exposure_s, 3.0);
}
