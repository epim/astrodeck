# The site coordinates in git history — a decision, not a task

Backlog finding H. The working tree was redacted in `2f56d08`; **the history was
not**. This document is for the repo owner to decide against, because the answer
turns on one question that only they can answer.

## ANSWERED 2026-08-05: rewrite

The owner's answer: *"good chance it'll be public some day. but I might also
sell it."* Both branches point the same way, and the second one harder than the
first.

**A sale is the stronger argument, not the weaker one.** Going public is a
decision with a date attached — you can scrub the week before. A sale is not:
due diligence means a third party reads the repository, with its history, on
their schedule and often under an NDA that protects THEIR interests rather than
yours. And the buyer does not merely read it, they own it: the seller's home
coordinates end up permanently inside an asset someone else controls. There is
no "scrub it before we flip the switch" moment in an acquisition.

The cost also only grows. 302 of 787 commits are affected today. Every week of
work adds commits on top of the affected range without removing any.

So: **rewrite.** Preparation is done (see "Status" below); the irreversible push
is the owner's to make, because a second collaborator holds a clone.

## What is actually exposed

The developer's home coordinates to six decimal places (about 11 cm) and the
site label, in **prose inside spec and plan documents**. They were never in code,
tests, or config; the boilerplate that FORBIDS them is what spelled them out.

**This cannot be rotated.** A leaked key is replaced in an afternoon; a home
address is permanent. So scrubbing does not "fix" anything already read — the
only lever is limiting who can read it from here on. That is exactly why the
public/private question is the whole decision.

## Measured blast radius (2026-08-05)

| | |
|---|---|
| Commits introducing the coordinates | 9 |
| Commits introducing the site label | 16 |
| First affected commit | `43a08fe`, 2026-07-22 |
| Commits that a rewrite would rewrite | 302 |
| Commits left untouched (before it) | 485 of 787 |
| Repo visibility | **PRIVATE** since creation, 2026-06-16 |
| Forks | 0 |
| Collaborators | 2 (`epim`, `feralcreative`) |
| Tags | 3 (would need re-pointing) |
| Deployed release tarball | ships `server/` + `ui/` only — **no docs, rig is clean** |

Two weeks of history, no forks, one other person. This is the smallest this
problem will ever be.

Note the irony worth recording: commit `2f56d08`, the redaction itself, put both
values into history one more time as deleted lines. Redacting a file never
un-writes it.

## Option A — rewrite (the chosen path)

`git filter-repo --replace-text` needs a file listing what to replace. That file
must NOT be written by hand here: an earlier draft of this document pasted the
three literals into the code block below and **the new pre-commit hook refused
the commit** — the document explaining how to remove the values had put them
back in, which is the identical failure to the boilerplate that started all of
this. So generate it from the scanner, which is the one place they live:

```bash
pip install git-filter-repo
git clone --mirror git@github.com:epim/astrodeck.git astrodeck-scrub

# Generate replacements.txt from tools/privacy_scan.py's assembled constants.
# Never type them; never commit this file (write it outside the repo).
python - <<'PY' > /tmp/replacements.txt
import sys; sys.path.insert(0, "tools")
from privacy_scan import FORBIDDEN
names = ("<REDACTED-LAT>", "<REDACTED-LON>", "<REDACTED-SITE-LABEL>")
for value, name in zip(FORBIDDEN, names):
    print(f"{value}==>{name}")
PY

cd astrodeck-scrub
git filter-repo --replace-text /tmp/replacements.txt
git push --force --mirror
rm /tmp/replacements.txt
```

Then, and this step is the one people skip:

1. **Both collaborators must delete and re-clone.** A rewritten history plus an
   old local clone re-introduces the old commits on the next push.
2. **Force-pushing does NOT remove the old objects from GitHub.** They stay
   reachable by direct SHA URL until GitHub garbage-collects, which is not on a
   schedule you control. To actually remove them, open a GitHub Support ticket
   asking them to purge unreachable objects — or delete and recreate the repo
   from the scrubbed mirror, which is faster and certain.
3. Re-point the 3 tags (`filter-repo` rewrites them, but verify).
4. Accept that **every SHA from `43a08fe` onward changes.** Commit hashes cited
   in `docs/superpowers/backlog/`, in the audit findings file, and in my memory
   notes become dangling references. Nothing breaks functionally; the citations
   just stop resolving.

`ui/src/lib/__tests__/troubleshoot.test.ts` must keep the literals — it is the
guard asserting they never reach a troubleshooting export. Add it to
`filter-repo`'s path exclusions or restore it afterwards.

## Option B — accept it (recommended IF the repo stays private forever)

Do nothing to history. The exposure is two named collaborators on a private
repo, and a rewrite that invalidates 302 commits plus every SHA reference buys
nothing against that threat model. Record the decision here so it is not
re-litigated every time someone greps the repo.

If the repo's status ever changes, **rewrite before flipping the switch, not
after** — the moment it goes public is the moment forks and archives start.

## Do this regardless of A or B

A guard, so the values cannot come back. The redaction is worthless if the next
spec pastes the same boilerplate:

1. A CI step that greps the working tree for the three literals and fails,
   excluding the one test that legitimately contains them.
2. A `pre-commit` hook doing the same locally, so it is caught before it is
   committed rather than after it is pushed.

This is cheap, uncontroversial, and independent of the decision above. It is the
only part of this document that is unambiguously worth doing today.
