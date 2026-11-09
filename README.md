# ticket-board

A Claude Code skill that turns a spec or a plan into tickets you can review,
then publishes them as GitHub issues on a GitHub Project and keeps them in sync
when you rewrite them.

- **Draft**: slices the spec into vertical tickets with explicit "blocked by"
  order, expand and contract steps for wide changes, and checkable acceptance
  criteria (optionally Gherkin).
- **Review**: renders a styled HTML board with filters. Mark each ticket "looks
  good" or "needs change", add a note, and copy the whole review back into chat.
- **Sync**: one `tickets.json` owns titles, bodies, phase and area; GitHub owns
  status. `board.py sync` reads GitHub in one request, diffs locally, and writes
  only the tickets that changed in one batched request. Edits made directly on
  GitHub are reported, never overwritten.

## Install

```
claude plugin marketplace add milenkotomic/claude-plugins
claude plugin install ticket-board@milenkotomic
```

Requires Python 3 and the GitHub CLI logged in with the `project` scope
(`gh auth refresh -s project`). Drafting and the review page work without `gh`.

## Use

In Claude Code: "make tickets from docs/specs/notes.md", review the page, paste
the review, then "publish the tickets to owner/repo". Later: "ticket 4 also
needs X, update GitHub".

The script works on its own too:

```
python3 skills/ticket-board/scripts/board.py check  notes.tickets.json
python3 skills/ticket-board/scripts/board.py render notes.tickets.json
python3 skills/ticket-board/scripts/board.py plan   notes.tickets.json
python3 skills/ticket-board/scripts/board.py sync   notes.tickets.json --yes-create-project
python3 skills/ticket-board/scripts/board.py diff   notes.tickets.json 4
```

File format and field ownership: [`references/format.md`](skills/ticket-board/references/format.md).
Slicing rules: [`references/slicing.md`](skills/ticket-board/references/slicing.md).

## Test

`tests/bench_sync.py owner/scratch-repo` publishes a 30-ticket board to a
scratch repo, rewrites 3 tickets, and checks that the update touches exactly
those 3 in under 30 seconds and that a hand edit on GitHub is detected. It
closes the issues and deletes the Project afterwards.

## License

MIT
