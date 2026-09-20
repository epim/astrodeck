# AstroDeck project rules

## File a GitHub issue for every problem you discover

Any defect, security finding, or unexplained behaviour found while working on
this project gets a GitHub issue on `epim/astrodeck`, at the time it is found.
This includes problems found incidentally while doing something else, and
problems you caused yourself and then fixed.

This applies to **anything that required a human to touch the rig**. If the
operator had to intervene during a night, that intervention is a bug: the
system should have handled it, recovered from it, or refused to get into that
state. File it with what was happening, what you had to do by hand, and what
the system should have done instead.

Do not batch findings into a scratch list and move on. A finding that lives
only in a conversation is lost when the conversation ends.

Include in each issue: what was observed, the evidence (durable log paths,
timestamps, exact error text), the impact, and a suggested shape of a fix.
Where a finding is one instance of a recurring class, say so and name the
class. Where something is intermittent, say that explicitly, because it
changes how a fix must be validated.

Check `gh issue list` before filing so findings accumulate rather than
duplicate.

## Site privacy, absolute

The real site latitude, longitude and label must NEVER appear in code, tests,
docs, commit messages, issue bodies, or any program output. They live only in
`C:/Users/bear/.astrodeck/privacy-needles.txt` and in the GitHub secret
`ASTRODECK_PRIVACY_NEEDLES`. Never print them.

Before publishing anything outward (an issue, a doc, a pasted log), scan it
against the needles file. Report only which files matched, never the match:

    grep -l -F -f C:/Users/bear/.astrodeck/privacy-needles.txt <files>

A key-name redaction filter is not sufficient protection, because it cannot
withhold a value that a route computes and names itself. See issue #19.

## Secrets

Never open, cat or grep `C:/Users/bear/.fly/config.yml`. Use `flyctl` or `gh`.
If a Fly token is ever seen in plaintext, rotate it with `fly tokens`.

Never print or log `admin_token`, `device_token` or session cookies. Rig
scripts mint sessions locally and keep cookies in memory.

## Git

- Commit with explicit pathspecs: `git commit -F msg -- <paths>`. With
  parallel agents in one tree, a bare `git add` sweeps another agent's staged
  files.
- Subagents never commit.
- **Never push upstream** without the operator saying so explicitly.

## Style

No emojis in code, docs, commit messages or issue bodies.

Keep source files UTF-8 without a BOM. On Windows PowerShell 5.1, do not
round-trip source through `Get-Content` / `Set-Content`: the former can decode
UTF-8 as ANSI and the latter writes a BOM. Prefer the file-editing tools, or
`[System.IO.File]::ReadAllText` and `WriteAllText` with `UTF8Encoding($false)`.
Write control characters as source escapes, never as literal NUL bytes.

## The rig

`astrotown`, Windows, `C:\Users\James\AstroDeck`, venv at `venv\` (not
`.venv\`), server on port 8800.

- ssh lands in **PowerShell**. Use the call operator:
  `cd C:\Users\James\AstroDeck; & .\venv\Scripts\python.exe x.py`
- Inline Python over ssh gets mangled by PowerShell quoting. Ship script files.
- Detached processes: `Start-Process -WindowStyle Hidden` does NOT survive an
  ssh disconnect. `Invoke-CimMethod Win32_Process Create` does. Always verify
  survival from a fresh connection.
- The durable night log is `captures/logs/<night>.jsonl`. The `/api/logs` ring
  holds only 200 entries and will have rolled over.
- `POST /api/capture` is asynchronous: it returns `{"started": "capture"}` and
  the exposure then occupies the capture lane.
- Reconnect the whole rig with `POST /api/profiles/<id>/activate`, never with
  `POST /api/disconnect` plus `POST /api/connect/rig`.
