# Operator documentation checks

This directory records the source audit and simulator procedures for the user guide revision. The audit began at b79c271f51133cf35b5c1f927ffd7817417f99b0 and was rebased onto privacy fix 6e8dad9d66f66b78d5df7cc12872f49e1f626d6b before the recorded procedures.

Run from the repository root:

~~~text
python tools/docs/check_docs.py --require-privacy
python -m unittest discover -s tools/docs -p "test_*.py"
python tools/docs/mutation_check.py
~~~

The checker reads Markdown without executing examples or fetching links. It verifies local targets and anchors, the old guide URL/anchor inventory, quoted UI labels, claim-source references, UTF-8, house style (including a ban on money metaphors for things that do not involve money, such as "earned" hours or a time "budget") and the existing external privacy scanner. A source citation establishes traceability, not independent proof of behavior. Reviews and simulator evidence cover that distinction.

A quoted UI label is matched by its text anywhere in the cited source file, not by the recorded line: the line is kept only as a hint for resolving more than one hit and for diagnostics. An unrelated edit above a label no longer fails this check; only the label's own wording going missing, or becoming genuinely ambiguous, does (#660).

The UI-label ledgers register bold labels. The claim ledgers say whether a statement was source-traced or exercised. Dynamic labels may register their fixed prefix; surrounding prose explains the variable part. The old anchor inventory was extracted from the base revision and must not shrink when headings change.

The docs workflow has explicit documentation and checker path triggers. The main CI workflow ignores documentation changes; putting this gate there would skip precisely those changes. The independent privacy workflow remains the repository-wide gate. Forks without the privacy secret report a skip; local acceptance uses --require-privacy and the out-of-tree needle file.

probe_session.py starts a fresh private server and browser for manual procedure review. It refuses reused state or an occupied port, verifies process creation time, command and listener ownership through the existing simulator tooling, and stops its owned server on console exit. It blocks external browser traffic and survey imagery requests. It does not simulate successful HTTP responses or install dependencies. Use a private virtual environment and browser cache under .probe/docs.

See [executed procedure evidence](procedure-evidence.md), [structured observations](procedure-records.json), [setup review](setup-review.md), [operator review](operator-review.md) and [mutation evidence](mutation-evidence.md). Audit ledgers alone must not be described as a completed hardware, unattended-night or release installation test. Claude owns the additional fresh-context review before merge.
