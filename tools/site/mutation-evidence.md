# Site check mutation evidence

Each source file was restored from its byte backup in a finally block.

- LINK-EXISTS: `AssertionError: 'missing local reference' not found in ''`
  Test: `SiteChecks.test_missing_asset`.
- FRAGMENT-EXISTS: `AssertionError: 'missing fragment' not found in ''`
  Test: `SiteChecks.test_missing_fragment`.
- STYLE-DASH: `AssertionError: 'em/en dash' not found in ''`
  Test: `SiteChecks.test_dash_entity`.
- HTML-PARSER: `AssertionError: 'HTML5' not found in ''`
  Test: `SiteChecks.test_invalid_html`.
- PRIVACY-SVG: `AssertionError: 'forbidden site value' not found in ''`
  Test: `SiteChecks.test_svg_privacy`.
- SIM-MARKER-PORT: `AssertionError: ValueError not raised`
  Test: `CaptureBoundary.test_refuses_wrong_probe_port`.
- CSS-RESOURCES: `AssertionError: 'external resource' not found in ''`
  Test: `SiteChecks.test_css_uppercase_url`.
- ENTITY-PRIVACY: `AssertionError: 'forbidden site value' not found in ''`
  Test: `SiteChecks.test_encoded_privacy`.
- DIAGNOSTIC-PRIVACY: `AssertionError: 'private-test-label' unexpectedly found in 'index.html: missing local reference private-test-label'`
  Test: `SiteChecks.test_diagnostics_do_not_echo_references`.
- FRESH-CONFIG: `AssertionError: ValueError not raised`
  Test: `CaptureBoundary.test_refuses_reused_config`.
- FAILED-LAUNCH-CLEANUP: `AssertionError: Lists differ: ['start', 'stop'] != ['start']`
  Test: `CaptureLifecycle.test_failed_launch_stops_owned_process`.
- PARENT-PID-REUSE: `AssertionError: False != True`
  Test: `CaptureLifecycle.test_refuses_parent_pid_reuse`.
- JS-EXIT: `AssertionError: 0 == 0`
  Test: `WorkflowChecks.test_javascript_syntax_failure_stops_step`.
- MONITOR-ONLY-PLAN: `AssertionError: Lists differ: ['monitor-desktop-light.png'] != ['flows-desktop-dark.png', 'equipment-phone[65 chars]png']`
  Test: `MonitorCapture.test_monitor_plan_excludes_other_images`.
- CAPTURE-PROVENANCE: `AssertionError: 'old-source' != 'new-source'`
  Test: `MonitorCapture.test_old_images_keep_old_provenance`.
- POINTING-READY (replaced ABOVE-HORIZON, 2026-10-01): `AssertionError: True is not false`
  Test: `MonitorCapture.test_pointing_mismatch_is_not_capture_ready`. Readiness
  no longer reads the mount's altitude: the #140 guard
  (test_rig_precheck_site_is_redacted) forbids any tool reading a mount alt/az.
  Above-horizon is pinned by HANLE-DECLINATION and the server's floor gate.
- HANLE-DECLINATION: `AssertionError: 11.035007662661501 not greater than 60`
  Test: `MonitorCapture.test_hanle_target_is_above_horizon`.

Unmutated suite: 45 tests passed. No mutant source remains.
