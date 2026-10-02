# Build-context mutation evidence

Run 2026-10-01T23:36:49.117904+00:00

Each fixture is local. No Docker daemon or network is used. Mutations are killed by the named assertion, then exact source bytes are restored in finally and SHA-256 checked.

- COPY-MISSING: `BuildContexts.test_missing_copy_fails`
  `AssertionError: 'COPY source is missing: absent.txt' not found in ['COPY source is excluded by dockerignore: absent.txt']`

- COPY-IGNORED: `BuildContexts.test_dockerignore_excluded_fails`
  `AssertionError: 'COPY source is excluded by dockerignore: relay' not found in []`

- COPY-STAGE: `BuildContexts.test_unknown_stage_fails`
  `AssertionError: 'COPY references an unknown or current stage' not found in []`

- COPY-UNSUPPORTED: `BuildContexts.test_unhandled_syntax_fails_closed`
  `AssertionError: 'unsupported COPY flag' not found in []`

- COPY-TAB: `BuildContexts.test_tab_delimited_copy_is_checked`
  `AssertionError: 'COPY source is missing: absent.txt' not found in []`

- IGNORE-PRECEDENCE: `BuildContexts.test_specific_ignore_overrides_context_ignore`
  `AssertionError: Lists differ: [] != ['COPY source is excluded by dockerignore: relay']`

- IGNORE-NEGATION: `BuildContexts.test_ignore_exception_and_zero_directory_glob`
  `AssertionError: True is not false`

- IGNORE-SEGMENT: `BuildContexts.test_ignore_wildcard_does_not_cross_directory`
  `AssertionError: False is not true`

- COPY-RECURSIVE: `BuildContexts.test_copy_recursive_glob_needs_review`
  `AssertionError: 'unsupported recursive path pattern' not found in []`

- RELAY-CONTEXT: `BuildContexts.test_real_compose_and_fly_contexts`
  `AssertionError: Lists differ: [] != [{'build': 'docker-compose.relay.yml:relay[176 chars]y']}]`

## Restored suite

```text
..............
----------------------------------------------------------------------
Ran 14 tests in 0.052s

OK
```
