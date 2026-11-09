# tickets.json

One file per board. The skill and you edit the content fields; the script owns
the bookkeeping fields and rewrites them on every sync.

```json
{
  "title": "Login rate limiting",
  "spec": "docs/specs/rate-limit.md",
  "phases": ["Phase 1", "Phase 2"],
  "github": { "repo": "owner/name" },
  "tickets": [
    {
      "n": 1,
      "title": "Reject login after five failed attempts in a minute",
      "area": "api",
      "phase": "Phase 1",
      "blocked_by": [],
      "summary": "Token bucket per account in Redis. Out of scope: IP-based limits.",
      "acceptance": [
        "The sixth failed attempt within 60 s returns 429 with `Retry-After`",
        "A successful login resets the bucket"
      ],
      "gherkin": "Scenario: ...\n  Given ...\n  When ...\n  Then ..."
    }
  ]
}
```

## Content fields (edit these)

| Field | Required | Notes |
|---|---|---|
| `title` (board) | yes | Also the GitHub Project's title on first sync. |
| `spec` | no | Path or URL of the source spec, shown on the page. |
| `phases` | no | Display and Project order. Phases used by tickets but missing here are appended. |
| `github.repo` | to sync | `owner/name`. Issues go to this repo; the Project belongs to its owner. |
| `n` | yes | Stable ticket number, unique, never reused. Not the issue number. |
| `title` | yes | Issue title, at most 256 characters. |
| `area` | no | Component, repo or team; a text field on the Project and a filter on the page. |
| `phase` | no | Single-select field on the Project and a section on the page. |
| `blocked_by` | no | Ticket numbers (`n`), synced as GitHub "blocked by" links. |
| `parent` | no | Ticket number of an umbrella ticket, synced as a sub-issue link. |
| `summary` | no | Markdown: paragraphs, `-` lists, `code`, **bold**, links. |
| `acceptance` | no | List of strings, rendered as a list. |
| `gherkin` | no | One Gherkin block, plain text with newlines. |

## Bookkeeping fields (never edit by hand)

| Field | Owner | Meaning |
|---|---|---|
| `issue` | sync | GitHub issue number once created. Clear it only to recreate a deleted issue. |
| `synced.hash` | sync | Fingerprint of title, body, phase and area at the last successful push. How sync tells your edits from edits made on GitHub. |
| `state` | sync | Status pulled from the Project (`Todo`, `In Progress`, `Done`); closed issues count as `Done`. Unpublished tickets show as `Draft`. |
| `github.project` | sync | Project id, number and URL after the first sync. |
| `github.synced_at` | sync | Time of the last sync, UTC. |

## Who owns what on GitHub

- Title, body, Phase and Area: tickets.json. Sync pushes them.
- Status and closing: GitHub. Sync pulls them into `state`.
- Comments, assignees, labels, milestones: GitHub only. Sync never touches them.

The issue body is generated from `summary`, `acceptance` and `gherkin` and ends
with a short "managed by ticket-board" note. When someone edits the title or
body on GitHub, sync reports the ticket as "edited on GitHub" and leaves it
alone until the change is ported into tickets.json (see `board.py diff`) or
overridden with `--force N`.
