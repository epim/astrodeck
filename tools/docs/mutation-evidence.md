# Documentation gate mutation evidence

Run 2026-10-02T15:56:01.928588+00:00.

Behavioral fixtures use synthetic text and temporary files; procedure lifecycle tests mock processes, sockets and privacy inputs. No application server or device is contacted. Every changed source is restored from exact bytes in a finally block and SHA-256 checked.

- LOCAL-TARGET: `DocsChecks.test_missing_link`
  `AssertionError: 'missing local link target' not found in ''`

- LINK-ANCHOR: `DocsChecks.test_missing_anchor`
  `AssertionError: 'missing local link anchor' not found in ''`

- REPOSITORY-CONTAINMENT: `DocsChecks.test_encoded_escape`
  `AssertionError: 'local link escapes repository' not found in 'docs/guide/demo.md: missing local link target'`

- ENCODED-STYLE: `DocsChecks.test_encoded_dash`
  `AssertionError: 'em/en dash is not permitted' not found in ''`

- ENCODED-PRIVACY: `DocsChecks.test_encoded_private_value`
  `AssertionError: 'forbidden observing-site value' not found in ''`

- URL-PRIVACY: `DocsChecks.test_percent_encoded_private_link`
  `AssertionError: 'forbidden observing-site value' not found in 'docs/guide/demo.md: missing local link target'`

- DIAGNOSTIC-PRIVACY: `DocsChecks.test_diagnostics_do_not_echo_reference`
  `AssertionError: 'private-canary' unexpectedly found in 'docs/guide/demo.md: missing local link target private-canary'`

- LABEL-QUOTED: `DocsChecks.test_label_must_be_quoted`
  `AssertionError: 'registered UI label is absent from bold text' not found in ''`

- LABEL-SOURCE: `DocsChecks.test_label_requires_ui_source`
  `AssertionError: 'UI label lacks a UI source file' not found in ''`

- LABEL-LINE: `DocsChecks.test_label_line_valid`
  `AssertionError: 'UI label has an invalid source line' not found in ''`

- LABEL-DRIFT: `DocsChecks.test_label_source_drift`
  `AssertionError: 'quoted UI label is absent at its source' not found in ''`

- LABEL-COVERAGE: `DocsChecks.test_label_coverage`
  `AssertionError: 'bold UI label has no provenance record' not found in ''`

- LABEL-EXACT-LINE: `DocsChecks.test_label_survives_insertion_above`
  `AssertionError: '' != 'docs/guide/demo.md: quoted UI label is absent at its source'`

- MONEY-METAPHOR: `DocsChecks.test_money_metaphor`
  `AssertionError: 'money metaphor is not permitted' not found in ''`

- CLAIM-SOURCE: `DocsChecks.test_claim_source_exists`
  `AssertionError: 'claim source file is missing' not found in ''`

- CLAIM-LINE: `DocsChecks.test_claim_line_valid`
  `AssertionError: 'claim source line is invalid' not found in ''`

- CLAIM-METHOD: `DocsChecks.test_claim_method`
  `AssertionError: 'claim verification method is missing' not found in ''`

- CLAIM-COVERAGE: `DocsChecks.test_claim_coverage`
  `AssertionError: 'no claim/procedure evidence ledger' not found in ''`

- STABLE-ANCHOR: `DocsChecks.test_stable_anchor`
  `AssertionError: 'stable guide anchor is missing' not found in ''`

- STABLE-URL: `DocsChecks.test_stable_url`
  `AssertionError: 'stable guide URL is missing' not found in ''`

- FENCED-ANCHOR: `DocsChecks.test_fenced_anchor_does_not_exist`
  `AssertionError: 'old' unexpectedly found in {'old'}`

- LEDGER-PRIVACY: `DocsChecks.test_json_escaped_private_value`
  `AssertionError: ValueError not raised`

- LEDGER-SCHEMA: `DocsChecks.test_ledger_rows_are_objects`
  `AssertionError: ValueError not raised`

- FRESH-PROCEDURE: `ProcedureLifecycle.test_reused_paths_refuse_before_launch`
  `AssertionError: 'new config' not found in 'Owned simulator launch failed; inspect private probe logs'`

- FAILED-LAUNCH-CLEANUP: `ProcedureLifecycle.test_failed_launch_stops_owned_server`
  `AssertionError: Lists differ: ['start', 'stop'] != ['start']`

- UNOWNED-CLEANUP: `ProcedureLifecycle.test_unowned_process_is_not_stopped`
  `AssertionError: Lists differ: ['start'] != ['start', 'stop']`

- CLEANUP-HONESTY: `ProcedureLifecycle.test_unowned_process_is_not_stopped`
  `AssertionError: 'No owned process was stopped' not found in 'Owned procedure server stopped.\n'`

## Restored suite

```text
.................................................
----------------------------------------------------------------------
Ran 49 tests in 0.170s

OK
```

Workflow action majors were checked against the official [checkout releases](https://github.com/actions/checkout/releases) and [setup-python releases](https://github.com/actions/setup-python/releases). The workflow uses read-only contents permission, does not retain checkout credentials, and reports a privacy skip when the external secret is unavailable.
