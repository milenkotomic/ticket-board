# Slicing a spec into tickets

Read this before drafting or re-slicing. The goal is a set of tickets a person
can pick up one at a time, each ending in something that runs and can be
reviewed, with the order between them written down instead of implied.

## Each ticket is a vertical slice

- A ticket delivers one behaviour end to end: the API, the storage, the UI or
  CLI surface and the test for that one behaviour. "Add the table" and "add the
  endpoint" as separate tickets are layers, not slices; merge them unless the
  layer is itself shippable (a migration other teams depend on, a shared
  library release).
- It can be merged on its own without breaking main. If it cannot, it is part
  of a bigger ticket or needs a flag.
- One to three days of work for one person. Bigger: split by behaviour
  (happy path first, then each edge case or variant). Smaller than half a day:
  fold it into its neighbour unless it unblocks others.
- The title says what changes for whom, in the language of the spec:
  "Reject login after five failed attempts", not "Rate limiter".

## Order is explicit

- `blocked_by` lists the tickets that must be merged first, and only those.
  "Would be nicer after" is not a blocker. Keep the graph shallow: if every
  ticket is blocked by the one before it, the slicing is serial and probably
  layered.
- Name at least one ticket nobody is blocked on so work can start in
  parallel. `board.py check` prints the tickets that can start first.
- Use `parent` only for a real umbrella: a ticket that is split later into
  pieces, or an epic-sized behaviour the user wants tracked as one.

## Wide changes use expand and contract

A rename, a schema change, a new API version or anything that touches many
callers becomes three tickets or more, never one:

1. **Expand**: add the new shape next to the old one; both work.
2. **Migrate**: move callers over, one ticket per caller group if they belong
   to different owners or repos. Each is blocked by expand.
3. **Contract**: remove the old shape. Blocked by every migrate ticket.

## Acceptance criteria are checkable

- Each item is a statement someone can verify by running something or reading
  one place: an input and the observable result. "Works well" and "is fast" are
  not criteria; "returns 429 with `Retry-After` after the fifth attempt within
  a minute" is.
- Three to six items per ticket. More means the ticket is two tickets.
- Use `gherkin` when the user or the spec already uses scenarios, or when a
  behaviour has several paths that read better as Given/When/Then. Write it in
  the language of the spec (Gherkin has Spanish keywords: Característica,
  Escenario, Dado, Cuando, Entonces, Y).
- Put what is out of scope in the summary when a reader would otherwise assume
  it is included.

## What not to make a ticket

- Investigation with no deliverable: fold it into the ticket that needs the
  answer, or make it a time-boxed spike whose result is a decision written in
  the spec.
- "Write tests" or "write docs" on their own: they belong to the slice they
  cover.
- Anything the spec does not ask for. Note it as an open question for the user
  instead.

## Self-review before showing the board

- Every behaviour in the spec maps to at least one ticket, and every ticket
  traces back to the spec.
- No ticket mixes two behaviours that could ship separately.
- `blocked_by` has no cycles (`check` catches these) and no "nice to have"
  edges.
- Each ticket's acceptance criteria are checkable as written.
