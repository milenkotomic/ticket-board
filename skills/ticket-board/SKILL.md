---
name: ticket-board
description: Turn a spec, plan or feature description into reviewable tickets (vertical slices with blocked-by order and checkable acceptance criteria), show them as a styled HTML review board, then publish them as GitHub issues on a GitHub Project and keep both in sync after edits. Use when the user wants to break work into tickets, issues or a backlog, plan a feature as tasks, review or reorder drafted tickets, publish tickets to GitHub, or update published tickets after rewriting descriptions or acceptance criteria. Triggers include "make tickets from this spec", "break this into issues", "ticket board", "plan this as tickets", "publish the tickets", "sync the board", "update the issues", and in Spanish "arma los tickets", "divide esto en tickets", "crea los issues", "tablero de tickets", "publica los tickets", "actualiza los tickets", "sincroniza el tablero".
argument-hint: "[spec path | tickets.json path | sync]"
---

# Ticket board

One `tickets.json` is the source of truth for a board's content. From it the
skill renders a review page, and `board.py sync` publishes it to GitHub issues
on a GitHub Project and keeps them current. Every GitHub read and write goes
through the script, in a handful of batched requests; never edit published
issues one by one with `gh issue edit` or other per-issue calls, because that
is what makes updates take many minutes.

Script: `python3 ${CLAUDE_SKILL_DIR}/scripts/board.py <command> <tickets.json>`.
Commands: `check`, `render`, `plan`, `sync`, `diff N`. Run it with `-h` for flags.

## Defaults

| Setting | Default | Change it when |
|---|---|---|
| Board file | `<spec dir>/<spec name>.tickets.json`; from a chat plan, `docs/tickets/<slug>.tickets.json` in the repo | the user names a path |
| Review page | `<board file without .json>.html`, opened with `open` | the user asks for a shareable page: publish that HTML with the Artifact tool |
| Language of tickets | the language of the spec or the user's messages | the user asks |
| Publish target | GitHub repo in `github.repo`; ask for it on first publish | other trackers are not supported yet: stop after the review page and say so |

## Pick the mode

- **Draft**: no board file yet. Input is a spec file, a plan in chat, or both.
- **Revise**: the board exists and the user pastes review notes from the page or asks for changes.
- **Sync**: the user wants the board published or GitHub updated. Also the last step of Revise when the board is already published.

## Draft

1. Read the spec (or the plan in chat) fully, then read
   `${CLAUDE_SKILL_DIR}/references/slicing.md` and
   `${CLAUDE_SKILL_DIR}/references/format.md`.
2. If scope is unclear, ask at most three questions in one message (what is
   out of scope, phases or milestones, which repo the issues go to). Do not ask
   what the spec already answers.
3. Write the board file. Content fields only; leave `issue`, `synced`, `state`
   and `github.project` out.
4. `board.py check`, fix anything it reports, then `board.py render` and open
   the page. Tell the user how many tickets, which can start first, and that
   they can mark each ticket on the page and press "Copy review for chat".

## Revise

1. Apply the review notes to the board file with the Edit tool, ticket by
   ticket. Splitting a ticket: keep the original `n` for one piece, give new
   pieces new numbers (never reuse a number), and fix every `blocked_by` that
   pointed at it. If the original is already published, make it the `parent`
   of the pieces instead of deleting it.
2. `board.py check`, then `board.py render`. Summarise what changed in two or
   three lines.
3. If the board is published, go on to Sync.

## Sync

1. `board.py plan` first. It reads GitHub in one request and prints what would
   change. Show the user that plan.
2. First publish (no project yet), or any plan that creates issues: creating
   issues and a Project is outward-facing, so get the user's go-ahead, then run
   `board.py sync --yes-create-project` (the flag is only needed when the
   Project does not exist). Run it as its own Bash call, not chained with other
   commands.
3. Later updates where the plan only updates existing tickets: run
   `board.py sync` without asking again if the user already asked to update
   GitHub.
4. Tickets reported as "edited on GitHub": run `board.py diff N`, port the
   change into the board file (the GitHub edit is usually the newer intent),
   then sync. "Edited on both sides": show the diff and ask which to keep;
   after merging into the board file, `sync --force N`. Never use `--force`
   without that step.
5. Re-render the page after sync so it shows issue links and states. Report
   the Project URL and the plan's counts. If sync exits with errors, rerun it
   once (it is idempotent and skips what already succeeded) and report what
   still fails.

## Rules

- The board file is the only place ticket content is edited. Status, comments
  and assignees belong to GitHub.
- Never edit the bookkeeping fields (`issue`, `synced`, `state`,
  `github.project`, `github.synced_at`) by hand.
- Keep the user in the loop before anything is created on GitHub; updates to
  already-published tickets do not need a second confirmation.
- If `gh` is missing or not logged in, or lacks the `project` scope, say so
  and give the command (`gh auth refresh -s project`); do not work around it.
