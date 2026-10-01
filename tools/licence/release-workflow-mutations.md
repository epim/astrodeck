# Release workflow mutation evidence

Run 2026-10-01T23:04:42.164237+00:00

These tests inspect real workflow commands and dependency/upload order. No workflow is dispatched. Every mutation is killed by its named assertion; exact bytes are restored in finally and SHA-256 checked.

- ALL-TARGETS-BEFORE-PUBLISH: `ReleasePipeline.test_publication_waits_for_all_artifacts`
  `AssertionError: False is not true : publication must wait for source and every binary target`

- SOURCE-ARTIFACT-GATE: `ReleasePipeline.test_source_gate_precedes_payload_upload`
  `AssertionError: 1 != 0 : source archive must run one artifact gate`

- FROZEN-ARTIFACT-GATE: `ReleasePipeline.test_both_platform_artifacts_are_gated`
  `AssertionError: False is not true : both native-wheel and frozen artifact gates must execute`

- NATIVE-WHEEL-GATE: `ReleasePipeline.test_both_platform_artifacts_are_gated`
  `AssertionError: False is not true : both native-wheel and frozen artifact gates must execute`

- MATCHING-NATIVE-BUILD: `ReleasePipeline.test_four_native_targets`
  `AssertionError: 1 != 0`

- DSS2-FETCH-EXCLUSION: `ReleasePipeline.test_dss2_is_never_fetched_or_passed`
  `AssertionError: 'survey_pack fetch' unexpectedly found in "python -m pip install -e ./server[dev] PyYAML psutil setuptools wheel\npython -c import os,tomllib; from pathlib import Path; p=tomllib.loads(Path('server/pyproject.toml').read_text()); expected='v'+p['project']['version']; assert os.environ['GITHUB_REF_TYPE']=='tag' and os.environ['GITHUB_REF_NAME']==expected, 'release ref must be the matching version tag'\npython tools/privacy_scan.py --require\npython -m pytest -m selftest -q\npython -m pytest tests/test_supervisor.py tests/test_supervisor_protocol_parity.py -q\npython packaging/distribution_policy.py --check-package-data\npython deploy/reverse-proxy/check_build_contexts.py\npython -B -m unittest discover -s packaging/tests -p test_*.py\npython -B -m unittest discover -s server/tests -p test_release_build_contexts.py\npython -B -m unittest discover -s server/tests -p test_release_pipeline.py\npython -B -m unittest discover -s tools/licence -p test_release_gate.py\npython -B -m pytest --noconftest -o addopts= -q server/tests/test_build_binary_smoke.py server/tests/test_build_release*.py server/tests/test_release_strictness.py server/tests/test_licence_*.py server/tests/test_licensing.py server/tests/test_credits.py\npython -m pip install ./server cryptography>=50.0.0\nnpm ci\ncargo fetch --locked --manifest-path native/Cargo.toml\npython tools/licence/build_credits.py --out .probe/release/credits.generated.json\nnode tools/licence/build_ui_inventory.mjs --credits .probe/release/credits.generated.json --out-dir .probe/release/ui-dist --report .probe/release/ui-inventory.json\npython -c import shutil; shutil.copytree('.probe/release/ui-dist','ui/dist')\npython -m astrodeck.catalog.survey_pack fetch --order 3 --dest dist/dss2\npython scripts/build_release.py --strict --version ${GITHUB_REF_NAME#v} --out dist --allow-missing astap\npython tools/licence/audit_release.py --kind source-tar --artifact dist/astrodeck-${GITHUB_REF_NAME#v}.tar.gz --platform linux-x86_64 --ui-inventory .probe/release/ui-inventory.json --credits .probe/release/credits.generated.json --report .probe/release/source-gate.json\nmkdir sbom-src\ntar -xzf dist/astrodeck-${GITHUB_REF_NAME#v}.tar.gz -C sbom-src\npython -m pip install ./server pyinstaller>=6.0 maturin>=1.9,<2 psutil\npython packaging/build_native.py --out dist\npython -m pip install --no-deps --force-reinstall dist/*.whl\nnpm ci\npython tools/licence/build_credits.py --out .probe/release/credits.generated.json\nnode tools/licence/build_ui_inventory.mjs --credits .probe/release/credits.generated.json --out-dir .probe/release/ui-dist --report .probe/release/ui-inventory.json\npython -c import shutil; shutil.copytree('.probe/release/ui-dist','server/astrodeck/webui')\nNATIVE_WHEEL=$(python -c from pathlib import Path; xs=list(Path('dist').glob('*.whl')); assert len(xs)==1; print(xs[0].as_posix()))\npython packaging/build_binary.py --skip-ui --native-wheel $NATIVE_WHEEL\npython tools/licence/release_provenance.py --artifact ${{ matrix.path }} --build-dir .probe/release/pyi --report .probe/release/frozen-provenance.json\nNATIVE_WHEEL=$(python -c from pathlib import Path; xs=list(Path('dist').glob('*.whl')); assert len(xs)==1; print(xs[0].as_posix()))\npython tools/licence/audit_release.py --kind native-wheel --artifact $NATIVE_WHEEL --platform ${{ matrix.platform }} --credits .probe/release/credits.generated.json --report .probe/release/native-gate.json\npython tools/licence/audit_release.py --kind frozen --artifact ${{ matrix.path }} --platform ${{ matrix.platform }} --ui-inventory .probe/release/ui-inventory.json --credits .probe/release/credits.generated.json --build-provenance .probe/release/frozen-provenance.json --report .probe/release/frozen-gate.json\nmv ${{ matrix.path }} dist/${{ matrix.asset }}\npython -m pip install cryptography>=50.0.0\npython scripts/sign_release.py dist/astrodeck-${GITHUB_REF_NAME#v}.tar.gz\npython -c from pathlib import Path; import hashlib; xs=[p for p in Path('dist').iterdir() if p.is_file() and (p.suffix=='.whl' or p.name.startswith(('astrodeck-linux-','astrodeck-windows-','astrodeck-macos-')))]; assert len(xs)==8, 'expected four wheels and four executables'; [p.with_name(p.name+'.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\\n',encoding='utf-8') for p in xs]"`

- PAYLOAD-UPLOAD-MUST-SUCCEED: `ReleasePipeline.test_source_gate_precedes_payload_upload`
  `AssertionError: 'if' unexpectedly found in {'name': 'Retain accepted source release files', 'if': 'always()', 'uses': 'actions/upload-artifact@v4', 'with': {'name': 'accepted-source', 'if-no-files-found': 'error', 'path': 'dist/astrodeck-*.tar.gz\ndist/astrodeck-sbom.cdx.json\n'}} : payload upload must require prior gate success`

- REPORT-ISOLATION: `ReleasePipeline.test_diagnostics_cannot_be_published_as_payload`
  `AssertionError: 'accepted-*' != '*'`

## Restored suite

```text
........
----------------------------------------------------------------------
Ran 8 tests in 0.065s

OK
```
