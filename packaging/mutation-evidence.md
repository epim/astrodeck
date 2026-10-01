# Packaging mutation evidence

Run with the test interpreter: `python -B packaging/mutation_check.py --run`. No artifact build may run concurrently.

All 45 named mutants failed the selected assertion, without test errors. The full new packaging suite passed before and after. Every source was restored byte for byte in `finally`; SHA-256 values are in mutation-evidence.json.

All process and HTTP behavior is mocked. Wheel builds use synthetic projects without dependency installation. This is regression evidence, not cross-platform runtime certification.

| Mutant | Intended fixture |
| --- | --- |
| pending-source-default | `test_release_packaging.ReleasePolicy.test_pending_preserves_artifact_history` |
| fetch-only-decision | `test_release_packaging.ReleasePolicy.test_fetch_only_synchronizes_every_artifact` |
| dss-payload-refusal | `test_release_packaging.ReleasePolicy.test_dss2_is_refused_in_all_payloads` |
| pending-owner-visible | `test_release_packaging.ReleasePolicy.test_pending_preserves_artifact_history` |
| static-package-table-drift | `test_release_packaging.ReleasePolicy.test_static_table_drift_is_rejected` |
| tar-policy-consumer | `test_release_packaging.ReleasePolicy.test_real_tar_follows_selected_policy` |
| installer-url-metadata-excluded | `test_release_packaging.MetadataSelection.test_only_installer_local_url_is_omitted` |
| spec-native-import | `test_release_packaging.ReleasePolicy.test_spec_collects_native_module_and_metadata` |
| spec-native-metadata | `test_release_packaging.ReleasePolicy.test_spec_collects_native_module_and_metadata` |
| spec-policy-selection | `test_release_packaging.ReleasePolicy.test_spec_filters_actual_sdk_and_tile_files` |
| server-clean-stage | `test_release_packaging.ReleasePolicy.test_server_install_never_reuses_stale_build` |
| server-same-version-reinstalled | `test_release_packaging.ReleasePolicy.test_validated_server_wheel_replaces_same_version_without_dependency_changes` |
| server-dependencies-preserved | `test_release_packaging.ReleasePolicy.test_validated_server_wheel_replaces_same_version_without_dependency_changes` |
| server-wheel-policy-check | `test_release_packaging.ReleasePolicy.test_server_install_refuses_forbidden_actual_wheel_member` |
| native-rebuild-source-data | `test_release_packaging.NativeWheel.test_rebuild_recipe_prepares_required_source_data` |
| native-abi3-required | `test_release_packaging.NativeWheel.test_non_abi3_wheel_refused` |
| native-notice-files-required | `test_release_packaging.NativeWheel.test_missing_native_notices_refused` |
| pep639-utf8-text | `test_release_packaging.NativeWheel.test_non_utf8_license_file_is_rejected` |
| pep639-nul-text | `test_release_packaging.NativeWheel.test_nul_license_file_is_rejected` |
| native-source-archive-hash | `test_release_packaging.NativeWheel.test_source_archive_hash_is_verified` |
| native-source-tree-match | `test_release_packaging.NativeWheel.test_changed_source_refused` |
| native-app-version-match | `test_release_packaging.NativeWheel.test_changed_release_version_refused` |
| native-notice-hash | `test_release_packaging.NativeWheel.test_tampered_notice_refused` |
| native-license-declarations | `test_release_packaging.NativeWheel.test_license_declarations_must_cover_notices` |
| native-sbom-hash | `test_release_packaging.NativeWheel.test_tampered_sbom_refused` |
| native-record-coverage | `test_release_packaging.NativeWheel.test_record_must_cover_payload` |
| native-record-digest | `test_release_packaging.NativeWheel.test_record_digest_is_verified` |
| native-extension-hash | `test_release_packaging.NativeWheel.test_tampered_extension_refused` |
| sbom-private-reference | `test_release_packaging.NativeWheel.test_sbom_paths_normalize_without_breaking_graph` |
| native-install-mandatory | `test_release_packaging.BinaryOrchestration.test_skip_server_install_still_installs_native_before_freeze` |
| native-probe-mandatory | `test_release_packaging.BinaryOrchestration.test_skip_server_install_still_installs_native_before_freeze` |
| native-detector-result | `test_release_packaging.NativeProbe.test_incorrect_detector_result_refused` |
| native-probe-version | `test_release_packaging.NativeProbe.test_module_metadata_mismatch_refused` |
| native-probe-private-errors | `test_release_packaging.NativeProbe.test_probe_errors_do_not_echo_exception_text` |
| native-probe-before-state | `test_release_packaging.NativeProbe.test_entry_probe_never_initializes_application_state` |
| smoke-creation-time | `test_smoke_ownership.SmokeOwnership.test_child_pid_reuse_is_refused` |
| smoke-command-identity | `test_smoke_ownership.SmokeOwnership.test_wrong_command_is_not_owned` |
| smoke-parent-ancestry | `test_smoke_ownership.SmokeOwnership.test_unproven_ancestry_is_not_adopted` |
| smoke-loopback-only | `test_smoke_ownership.SmokeOwnership.test_wildcard_listener_is_refused` |
| smoke-preexisting-launch | `test_smoke_ownership.SmokeOwnership.test_preexisting_launch_pid_is_refused` |
| smoke-unknown-setup-cleanup | `test_smoke_ownership.SmokeOwnership.test_constructor_failure_cleans_already_proven_identity` |
| smoke-stop-retry | `test_smoke_ownership.SmokeOwnership.test_failed_stop_can_be_retried` |
| smoke-preserve-unverified-state | `test_smoke_ownership.SmokeOwnership.test_unverified_launch_preserves_private_test_state` |
| smoke-early-state-cleanup | `test_smoke_ownership.SmokeOwnership.test_launch_failure_leaves_no_test_state` |
| smoke-no-raw-log-read | `test_smoke_ownership.SmokeOwnership.test_no_raw_log_read` |
