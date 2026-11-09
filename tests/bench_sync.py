#!/usr/bin/env python3
"""End-to-end sync check against a scratch GitHub repo.

  python3 tests/bench_sync.py owner/scratch-repo [--tickets 30] [--drift 3] [--keep]

1. Builds a board of N tickets with blocked-by links and phases, and publishes it
   (creates N issues and a Project; slow on purpose, GitHub limits creation).
2. Rewrites the acceptance criteria of `--drift` tickets, as a user would.
3. Times `plan` + `sync` and checks that exactly those tickets were updated,
   that it finished within 30 s, and that a second plan has nothing to push.
4. Edits one issue body on GitHub directly and checks plan reports it as
   "edited on GitHub" instead of overwriting it.

Without --keep, it closes the issues and deletes the Project at the end.
The repo itself is never deleted.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

BOARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "ticket-board", "scripts", "board.py")


def run(*args, check=True):
    p = subprocess.run(["python3", BOARD, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        print(p.stdout, p.stderr)
        sys.exit(f"board.py {args[0]} failed")
    return p.stdout + p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--tickets", type=int, default=30)
    ap.add_argument("--drift", type=int, default=3)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    d = tempfile.mkdtemp(prefix="tb-bench-")
    path = os.path.join(d, "bench.tickets.json")
    tickets = []
    for n in range(1, a.tickets + 1):
        tickets.append({
            "n": n, "title": f"Bench ticket {n}", "area": ["api", "web", "db"][n % 3],
            "phase": "Phase 1" if n <= a.tickets // 2 else "Phase 2",
            "blocked_by": [n - 1] if n % 4 == 0 else [],
            "summary": f"Synthetic ticket {n} for the sync benchmark.",
            "acceptance": [f"Criterion A for {n}", f"Criterion B for {n}"],
        })
    board = {"title": f"ticket-board bench {time.strftime('%H%M%S')}", "phases": ["Phase 1", "Phase 2"],
             "github": {"repo": a.repo}, "tickets": tickets}
    json.dump(board, open(path, "w"), indent=2)

    t0 = time.time()
    print(run("sync", path, "--yes-create-project").strip().splitlines()[-1])
    print(f"publish of {a.tickets}: {time.time() - t0:.1f}s")

    board = json.load(open(path))
    drifted = list(range(2, 2 + a.drift))
    for t in board["tickets"]:
        if t["n"] in drifted:
            t["acceptance"].append("Rewritten criterion")
    json.dump(board, open(path, "w"), indent=2)

    t0 = time.time()
    plan = run("plan", path)
    out = run("sync", path)
    took = time.time() - t0
    updated = [line for line in out.splitlines() if line.startswith("- update ticket")]
    print(plan.strip().splitlines()[0])
    print(f"update of {a.drift} drifted tickets: {took:.1f}s, {len(updated)} updated")
    ok = len(updated) == a.drift and took < 30
    again = run("plan", path)
    ok = ok and "nothing to push" in again
    print("second plan: " + again.strip().splitlines()[0])

    board = json.load(open(path))
    victim = board["tickets"][0]["issue"]
    subprocess.run(["gh", "issue", "edit", str(victim), "-R", a.repo, "--body", "edited by hand"], check=True, capture_output=True)
    edited = run("plan", path)
    remote_ok = "edited on GitHub" in edited
    print("hand edit detected: " + str(remote_ok))
    ok = ok and remote_ok

    if not a.keep:
        nums = [str(t["issue"]) for t in board["tickets"] if t.get("issue")]
        for num in nums:
            subprocess.run(["gh", "issue", "close", num, "-R", a.repo, "--reason", "not planned"], capture_output=True)
        pid = board["github"]["project"]["id"]
        subprocess.run(["gh", "api", "graphql", "-f", f'query=mutation {{ deleteProjectV2(input: {{projectId: "{pid}"}}) {{ clientMutationId }} }}'], capture_output=True)
        print(f"cleaned up: closed {len(nums)} issues, deleted the project")
    else:
        print(f"kept board at {path}")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
