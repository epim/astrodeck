# Licence gate mutation evidence

Run 2026-10-01. Baseline and restored focused suites pass. Every changed file was restored from its exact in-memory bytes in a finally block and SHA-256 checked; no git checkout/reset was used.

The TARBALL-ROGUE-SDK fixture inserts a vendor DLL before the real release packager runs and proves that the member reaches the resulting archive. Mutating the gate makes that assertion fail.

## NARROW-COPYLEFT-OWNER

- Source: tools/licence_policy.py
- Selected assertion: server/tests/test_credits.py::test_reviewed_copyleft_components_remain_owner_decisions
- Result: KILLED; exact original bytes restored.

~~~text
E   AssertionError: reviewed copyleft component lost its owner gate
E   assert None
E    +  where None = Resolution(policies=[Policy(spdx='LGPL-3.0-or-later', requires=('license', 'notice', 'source and relinking review'), s...placement/relinking and reverse-engineering permissions for this exact distribution form.', copyleft=True)], flag=None).flag
~~~

## UNKNOWN-VIRTUAL-RUNTIME

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_unknown_virtual_runtime_cannot_inherit_vite_licence
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'unclassified build input' in 'UI component vite: missing lockfile or generated credit'
E    +  where 'UI component vite: missing lockfile or generated credit' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## TARBALL-ROGUE-SDK

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_unregistered_binary_in_real_built_tarball_is_rejected
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'unaccounted binary/data member' in ''
E    +  where '' = findings(({'manifest.json': b'{\r\n  "name": "astrodeck",\r\n  "version": "licence-audit",\r\n  "built_at": "2026-10-01T18:30:1... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## SOURCE-EMBEDDED-DATA

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_ascii_table_disguised_as_source_needs_review
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'source or embedded data has not been reviewed' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/catalog/unaccounted_survey.py': b'SURVEY = [(... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## ASSET-HASH

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_changed_known_binary_is_not_covered_by_vendor_name
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'asset bytes differ' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00changed', '... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## COMPANION-NOTICE

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_companion_notice_must_travel_in_archive
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'required companion notice is absent' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## BLOCKED-ARTWORK

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_owner_needed_asset_cannot_be_cleared_by_registration
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'requires an owner decision' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## ASSET-NOTICE

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_credit_name_alone_does_not_satisfy_asset_notice
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'required licence text is absent' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## CORRUPT-TEXT-POOL

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_asset_notice_body_must_resolve_and_match_hash
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'licence text reference is missing, truncated or corrupt' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
E   assert 'licence text reference is missing, truncated or corrupt' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
E   assert 'licence text reference is missing, truncated or corrupt' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar... redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}}', {'packages': {}}))
~~~

## STALE-UI-CREDITS

- Source: tools/licence/audit_artifact.py
- Selected assertion: server/tests/test_licence_artifact.py::test_stale_generated_credits_fail
- Result: KILLED; exact original bytes restored.

~~~text
E   assert 'stale generated credits' in ''
E    +  where '' = findings(({'server/astrodeck/__init__.py': b'"""Fixture."""\n', 'server/astrodeck/vendor/fixture/test.dll': b'MZ\x00test-librar...redistribute with this notice. Copyright Fixture. Permission to redistribute with this notice. "}} ', {'packages': {}}))
~~~

## PROJECT-VERSION

- Source: ui/src/credits.generated.json
- Selected assertion: server/tests/test_credits.py::test_project_credit_version_matches_current_manifest
- Result: KILLED; exact original bytes restored.

~~~text
E   AssertionError: credits project version is stale
E   assert '0.0.0-stale' == '0.3.39'
E
E     - 0.3.39
E     + 0.0.0-stale
~~~

## INPUT-FRESHNESS

- Source: tools/credits_registry.py
- Selected assertion: server/tests/test_credits.py::test_generated_registry_policy_and_text_inputs_are_current
- Result: KILLED; exact original bytes restored.

~~~text
E   AssertionError: generated credit inputs are stale: tools/credits_registry.py
E   assert not ['tools/credits_registry.py']
~~~

## DEV-RUNTIME-CREDIT

- Source: ui/src/credits.generated.json
- Selected assertion: server/tests/test_credits.py::test_compiler_runtime_contributors_are_credited
- Result: KILLED; exact original bytes restored.

~~~text
E   AssertionError: emitted compiler runtime has no credit: vite
E   assert 'vite' in {'2mass colour imagery (optional remote survey)': {'name': '2MASS colour imagery (optional remote survey)', 'notes': "...ITH software but never sold on its own, and any Reserved Font Name may not be used by a modified version.'], ...}, ...}
E    +  where 'vite' = _norm('vite')
~~~

## BUNDLED-COMPONENT-NOTICE

- Source: ui/src/credits.generated.json
- Selected assertion: server/tests/test_credits.py::test_reviewed_artifact_component_manifest_is_credited_with_exact_notices
- Result: KILLED; exact original bytes restored.

~~~text
E   AssertionError: observed binary component lost its complete notice
E   assert 'A. HISTORY OF THE SOFTWARE\n==========================\n\nPython was created in the early 1990s by Guido van Rossum a... The original license terms of\nthe HTML Library software distribution is included in the file\ndocs/license.html_lib.' in []
~~~

## DEPENDENCY-POLICY

- Source: tools/licence/audit_dependencies.py
- Selected assertion: server/tests/test_licence_dependencies.py::test_unreviewed_copyleft_or_unknown_graph_dependency_fails
- Result: KILLED; exact original bytes restored.

~~~text
E   AssertionError: assert 'injected: licence is outside the allowed policy' in []
E    +  where [] = <function review at 0x00000219655C2520>([{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}])
E    +    where <function review at 0x00000219655C2520> = gate.review
E    +    and   [{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}] = <function cargo_rows at 0x00000219655C2A20>({'packages': [{'license': 'GPL-3.0-only', 'name': 'injected', 'version': '1'}]})
E    +      where <function cargo_rows at 0x00000219655C2A20> = gate.cargo_rows
E   AssertionError: assert 'injected: licence is outside the allowed policy' in []
E    +  where [] = <function review at 0x00000219655C2520>([{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}])
E    +    where <function review at 0x00000219655C2520> = gate.review
E    +    and   [{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}] = <function cargo_rows at 0x00000219655C2A20>({'packages': [{'license': 'LGPL-2.1-only', 'name': 'injected', 'version': '1'}]})
E    +      where <function cargo_rows at 0x00000219655C2A20> = gate.cargo_rows
E   AssertionError: assert 'injected: licence is outside the allowed policy' in []
E    +  where [] = <function review at 0x00000219655C2520>([{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}])
E    +    where <function review at 0x00000219655C2520> = gate.review
E    +    and   [{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}] = <function cargo_rows at 0x00000219655C2A20>({'packages': [{'license': 'AGPL-3.0-only', 'name': 'injected', 'version': '1'}]})
E    +      where <function cargo_rows at 0x00000219655C2A20> = gate.cargo_rows
E   AssertionError: assert 'injected: licence is outside the allowed policy' in []
E    +  where [] = <function review at 0x00000219655C2520>([{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}])
E    +    where <function review at 0x00000219655C2520> = gate.review
E    +    and   [{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}] = <function cargo_rows at 0x00000219655C2A20>({'packages': [{'license': 'SSPL-1.0', 'name': 'injected', 'version': '1'}]})
E    +      where <function cargo_rows at 0x00000219655C2A20> = gate.cargo_rows
E   AssertionError: assert 'injected: licence is outside the allowed policy' in []
E    +  where [] = <function review at 0x00000219655C2520>([{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}])
E    +    where <function review at 0x00000219655C2520> = gate.review
E    +    and   [{'ecosystem': 'cargo', 'name': 'injected', 'scope': 'resolved-all-targets; see dependency-cargo-inventory.json for runtime/build paths', 'source': 'workspace', ...}] = <function cargo_rows at 0x00000219655C2A20>({'packages': [{'license': None, 'name': 'injected', 'version': '1'}]})
E    +      where <function cargo_rows at 0x00000219655C2A20> = gate.cargo_rows
~~~

## FROZEN-ROGUE-SDK

- Source: tools/licence/audit_frozen.py
- Selected assertion: server/tests/test_licence_frozen.py::test_new_vendor_binary_in_executable_is_rejected
- Result: KILLED; exact original bytes restored.

~~~text
E   AssertionError: assert 'astrodeck/vendor/rogue/foreign.dll: unaccounted executable member' in ['Microsoft runtime redistribution basis and complete native subcomponent coverage remain unverified', 'No release cle...ronment', 'Reviewed baseline omits the native engine (#630); audit its final linked wheel when packaging is corrected']
~~~

## FROZEN-PLAYERONE

- Source: tools/licence/audit_frozen.py
- Selected assertion: server/tests/test_licence_frozen.py::test_known_player_one_binary_is_still_owner_blocked
- Result: KILLED; exact original bytes restored.

~~~text
E   assert False
E    +  where False = any(<generator object test_known_player_one_binary_is_still_owner_blocked.<locals>.<genexpr> at 0x000001D1BD863D30>)
E   assert False
E    +  where False = any(<generator object test_known_player_one_binary_is_still_owner_blocked.<locals>.<genexpr> at 0x000001D1BD8F3780>)
~~~

Final verification:

~~~text
........................................................................ [ 98%]
.                                                                        [100%]
73 passed in 2.65s
~~~
