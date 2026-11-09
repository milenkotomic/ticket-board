#!/usr/bin/env python3
"""ticket-board: one tickets.json, a review page, and a fast GitHub sync.

Usage:
  board.py check  <tickets.json>              validate the file, print a summary
  board.py render <tickets.json> [-o OUT]     write the review page (default: next to the JSON)
  board.py plan   <tickets.json>              read GitHub once, print what sync would do; writes nothing
  board.py sync   <tickets.json> [--force N,N] [--yes-create-project]
                                              apply the plan in a few batched requests
  board.py diff   <tickets.json> N            show GitHub's copy of ticket N against the local one

The JSON owns each issue's title, body and the Phase and Area fields. GitHub owns
Status. A ticket edited on GitHub since the last sync is reported, never
overwritten, unless it is named in --force.
"""
import argparse
import difflib
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, NoReturn

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "<sub>Managed by ticket-board. Edit the board's tickets.json and sync; edits made here are detected, not overwritten.</sub>"
CHUNK = 20          # aliases per GraphQL request
CREATE_CHUNK = 10   # issue creations per request, to stay clear of GitHub's content-creation limits


# ---------------------------------------------------------------- file, validation

def die(msg) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        die(f"{path} not found")
    except json.JSONDecodeError as e:
        die(f"{path} is not valid JSON: {e}")


def save(path, board):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(board, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def phases_of(board):
    order = list(board.get("phases") or [])
    for t in board["tickets"]:
        p = t.get("phase") or ""
        if p and p not in order:
            order.append(p)
    return order


def validate(board):
    errs = []
    if not isinstance(board.get("title"), str) or not board["title"].strip():
        errs.append("board needs a non-empty 'title'")
    tickets = board.get("tickets")
    if not isinstance(tickets, list) or not tickets:
        return errs + ["board needs a non-empty 'tickets' list"]
    ns = {}
    for i, t in enumerate(tickets):
        n = t.get("n")
        if not isinstance(n, int) or n < 1:
            errs.append(f"ticket at index {i}: 'n' must be a positive integer")
            continue
        if n in ns:
            errs.append(f"ticket {n}: duplicate 'n'")
        ns[n] = t
        if not str(t.get("title", "")).strip():
            errs.append(f"ticket {n}: empty title")
        if len(str(t.get("title", ""))) > 256:
            errs.append(f"ticket {n}: title over 256 characters")
        for key in ("acceptance", "blocked_by"):
            if key in t and not isinstance(t[key], list):
                errs.append(f"ticket {n}: '{key}' must be a list")
    declared = board.get("phases")
    for n, t in ns.items():
        if declared and t.get("phase") and t["phase"] not in declared:
            errs.append(f"ticket {n}: phase '{t['phase']}' is not in 'phases'")
        for b in t.get("blocked_by") or []:
            if b == n:
                errs.append(f"ticket {n}: blocked by itself")
            elif b not in ns:
                errs.append(f"ticket {n}: blocked by unknown ticket {b}")
        p = t.get("parent")
        if p is not None and (p == n or p not in ns):
            errs.append(f"ticket {n}: parent {p} is not another ticket on the board")
    # cycles in blocked_by
    state = {}

    def visit(n, stack):
        state[n] = 1
        for b in ns[n].get("blocked_by") or []:
            if b not in ns:
                continue
            if state.get(b) == 1:
                errs.append("blocked_by cycle: " + " -> ".join(str(x) for x in stack + [n, b]))
            elif not state.get(b):
                visit(b, stack + [n])
        state[n] = 2

    for n in ns:
        if not state.get(n):
            visit(n, [])
    gh = board.get("github") or {}
    if gh.get("repo") and not re.fullmatch(r"[\w.-]+/[\w.-]+", gh["repo"]):
        errs.append("github.repo must look like owner/name")
    return errs


def checked(path):
    board = load(path)
    errs = validate(board)
    if errs:
        print("tickets.json has problems:", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)
    return board


# ---------------------------------------------------------------- issue content and hashes

def norm(s):
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in s.strip().split("\n"))


def issue_body(t):
    parts = []
    if t.get("summary"):
        parts.append(t["summary"].strip())
    acc = [a for a in t.get("acceptance") or [] if str(a).strip()]
    if acc:
        parts.append("## Acceptance criteria\n\n" + "\n".join(f"- {str(a).strip()}" for a in acc))
    if t.get("gherkin"):
        parts.append("```gherkin\n" + t["gherkin"].strip("\n") + "\n```")
    parts.append(MARK)
    return norm("\n\n".join(parts))


def fingerprint(title, body, phase, area):
    raw = "\x00".join([norm(title), norm(body), phase or "", (area or "").strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def local_hash(t):
    return fingerprint(t["title"], issue_body(t), t.get("phase", ""), t.get("area", ""))


# ---------------------------------------------------------------- GitHub GraphQL

class GH:
    def __init__(self):
        self.requests = 0

    def q(self, query, variables=None, allow_partial=False):
        payload = json.dumps({"query": query, "variables": variables or {}})
        for attempt in range(3):
            self.requests += 1
            p = subprocess.run(["gh", "api", "graphql", "--input", "-"], input=payload,
                               capture_output=True, text=True)
            try:
                out = json.loads(p.stdout) if p.stdout.strip() else {}
            except json.JSONDecodeError:
                out = {}
            errors = out.get("errors") or []
            text = (p.stderr or "") + json.dumps(errors)
            if "rate limit" in text.lower() and attempt < 2:
                wait = 30 * (attempt + 1)
                print(f"  GitHub rate limit, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if p.returncode != 0 and not out.get("data"):
                die("gh api graphql failed: " + (p.stderr.strip() or json.dumps(errors))[:800])
            if errors and not allow_partial:
                die("GitHub returned errors: " + json.dumps(errors)[:800])
            return out.get("data") or {}, errors
        die("gave up after repeated rate limiting")


ISSUE_FIELDS = """id number state title body url
  parent { number }
  blockedBy(first: 50) { nodes { number } }
  projectItems(first: 20) { nodes { id project { id }
    status: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
    phase: fieldValueByName(name: "Phase") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
    area: fieldValueByName(name: "Area") { ... on ProjectV2ItemFieldTextValue { text } } } }"""

PROJECT_FIELDS = """id number url title
  fields(first: 50) { nodes {
    ... on ProjectV2SingleSelectField { id name options { id name } }
    ... on ProjectV2Field { id name dataType } } }"""


def read_remote(gh, board):
    """One request: the repository, the project and every issue the board knows."""
    owner, name = board["github"]["repo"].split("/")
    proj = (board["github"].get("project") or {}).get("id")
    nums = sorted({t["issue"] for t in board["tickets"] if t.get("issue")})
    parts = [f"repo: repository(owner: $owner, name: $name) {{ id owner {{ id login }}"]
    parts += [f"  i{n}: issue(number: {n}) {{ {ISSUE_FIELDS} }}" for n in nums]
    parts.append("}")
    if proj:
        parts.append(f'project: node(id: "{proj}") {{ ... on ProjectV2 {{ {PROJECT_FIELDS} }} }}')
    query = "query($owner: String!, $name: String!) {\n" + "\n".join(parts) + "\n}"
    data, errors = gh.q(query, {"owner": owner, "name": name}, allow_partial=True)
    repo = data.get("repo")
    if not repo:
        die(f"cannot read repository {board['github']['repo']}: " + json.dumps(errors)[:400])
    issues = {n: repo.get(f"i{n}") for n in nums}
    return repo, data.get("project"), issues


def field_map(project):
    out = {}
    for f in (project or {}).get("fields", {}).get("nodes", []):
        if f and f.get("name"):
            out[f["name"]] = f
    return out


def item_in(issue, project_id):
    for it in (issue or {}).get("projectItems", {}).get("nodes", []):
        if it and it["project"]["id"] == project_id:
            return it
    return None


def remote_view(issue, project_id):
    it = item_in(issue, project_id) or {}
    phase = (it.get("phase") or {}).get("name", "")
    area = (it.get("area") or {}).get("text", "")
    status = (it.get("status") or {}).get("name", "")
    if issue and issue["state"] == "CLOSED":
        status = "Done"
    return {"title": issue["title"], "body": norm(issue["body"]), "phase": phase, "area": area,
            "status": status, "hash": fingerprint(issue["title"], issue["body"], phase, area)}


# ---------------------------------------------------------------- plan

def make_plan(board, repo, project, issues, force=()):
    pid = (project or {}).get("id")
    by_n = {t["n"]: t for t in board["tickets"]}
    plan: dict[str, Any] = {"create": [], "update": [], "same": [], "unchanged": [], "remote_edited": [],
            "conflict": [], "missing": [], "add_item": [], "edges_add": [], "edges_remove": [],
            "parent_set": [], "parent_remove": [], "status": []}
    for t in board["tickets"]:
        n = t["n"]
        if not t.get("issue"):
            plan["create"].append(n)
            continue
        issue = issues.get(t["issue"])
        if not issue:
            plan["missing"].append(n)
            continue
        r = remote_view(issue, pid)
        if not pid or not item_in(issue, pid):
            # Not on the board yet, so it has no Phase or Area: compare title and body only.
            r["hash"] = fingerprint(issue["title"], issue["body"], t.get("phase", ""), t.get("area", ""))
        lh, sh = local_hash(t), (t.get("synced") or {}).get("hash")
        if lh == r["hash"]:
            plan["same" if lh != sh else "unchanged"].append(n)
        elif r["hash"] == sh:
            plan["update"].append(n)
        elif n in force:
            plan["update"].append(n)
        elif lh == sh:
            plan["remote_edited"].append(n)
        else:
            plan["conflict"].append(n)
        if pid and not item_in(issue, pid):
            plan["add_item"].append(n)
        if r["status"] and r["status"] != t.get("state"):
            plan["status"].append((n, r["status"]))
    # Dependency edges and parents, only between issues that exist (or will after creation).
    issue_to_n = {t["issue"]: t["n"] for t in board["tickets"] if t.get("issue")}
    for t in board["tickets"]:
        n = t["n"]
        issue = issues.get(t.get("issue")) if t.get("issue") else None
        want = set(t.get("blocked_by") or [])
        have = set()
        if issue:
            have = {issue_to_n[x["number"]] for x in issue["blockedBy"]["nodes"] if x and x["number"] in issue_to_n}
        for b in sorted(want - have):
            plan["edges_add"].append((n, b))
        for b in sorted(have - want):
            plan["edges_remove"].append((n, b))
        want_p = t.get("parent")
        have_p = None
        if issue and issue.get("parent") and issue["parent"]["number"] in issue_to_n:
            have_p = issue_to_n[issue["parent"]["number"]]
        if want_p and want_p != have_p:
            plan["parent_set"].append((n, want_p))
        elif not want_p and have_p:
            plan["parent_remove"].append((n, have_p))
    plan["_by_n"] = by_n
    return plan


def print_plan(board, plan, project):
    def lst(ns):
        return ", ".join(f"#{n}" for n in ns)
    total = len(board["tickets"])
    head = []
    for key, label in (("unchanged", "unchanged"), ("same", "already match"), ("update", "to update"),
                       ("create", "to create"), ("remote_edited", "edited on GitHub"),
                       ("conflict", "edited on both sides"), ("missing", "issue not found")):
        if plan[key]:
            head.append(f"{len(plan[key])} {label}")
    print(f"{total} tickets: " + ", ".join(head))
    if not project:
        print(f"- create GitHub Project '{board['title']}' linked to {board['github']['repo']}")
    if plan["create"]:
        print(f"- create issues for tickets {lst(plan['create'])}")
    for n in plan["update"]:
        print(f"- update ticket #{n} (issue {board_issue(plan, n)})")
    for n in plan["remote_edited"]:
        print(f"- ticket #{n} was edited on GitHub only: run `diff` and port the change into tickets.json, or pass --force {n}")
    for n in plan["conflict"]:
        print(f"- ticket #{n} changed locally AND on GitHub: run `diff`, merge into tickets.json, then --force {n}")
    for n in plan["missing"]:
        print(f"- ticket #{n}: issue {board_issue(plan, n)} not found (deleted or transferred?). Clear its 'issue' to recreate it")
    if plan["edges_add"] or plan["edges_remove"]:
        print(f"- blocked-by links: {len(plan['edges_add'])} to add, {len(plan['edges_remove'])} to remove")
    if plan["parent_set"] or plan["parent_remove"]:
        print(f"- sub-issue links: {len(plan['parent_set'])} to set, {len(plan['parent_remove'])} to remove")
    if plan["status"]:
        print("- status from GitHub: " + ", ".join(f"#{n} {s}" for n, s in plan["status"]))
    if not any(plan[k] for k in ("create", "update", "edges_add", "edges_remove", "parent_set",
                                  "parent_remove", "add_item")) and project:
        print("nothing to push")


def board_issue(plan, n):
    t = plan["_by_n"][n]
    return f"#{t['issue']}" if t.get("issue") else "(none)"


# ---------------------------------------------------------------- apply

def batched(gh, ops, chunk=CHUNK):
    """ops: list of (alias, mutation_text). Returns {alias: result} and a list of error strings."""
    results, errs = {}, []
    for i in range(0, len(ops), chunk):
        part = ops[i:i + chunk]
        query = "mutation {\n" + "\n".join(f"  {a}: {m}" for a, m in part) + "\n}"
        data, errors = gh.q(query, allow_partial=True)
        results.update({k: v for k, v in (data or {}).items() if v is not None})
        errs += [e.get("message", str(e)) for e in errors]
    return results, errs


def s(v):
    return json.dumps(v, ensure_ascii=False)


def ensure_project(gh, board, repo, project):
    """Create the project and its Phase and Area fields when missing; add new phase options."""
    phases = phases_of(board)
    if not project:
        data, _ = gh.q("""mutation($owner: ID!, $repo: ID!, $title: String!) {
          createProjectV2(input: {ownerId: $owner, repositoryId: $repo, title: $title}) {
            projectV2 { id number url } } }""",
                      {"owner": repo["owner"]["id"], "repo": repo["id"], "title": board["title"]})
        p = data["createProjectV2"]["projectV2"]
        board["github"]["project"] = {"id": p["id"], "number": p["number"], "url": p["url"]}
        print(f"  created project {p['url']}")
        opts = ", ".join(f'{{name: {s(ph)}, color: GRAY, description: ""}}' for ph in phases) or ""
        ops = [("phase", f'createProjectV2Field(input: {{projectId: "{p["id"]}", dataType: SINGLE_SELECT, name: "Phase", singleSelectOptions: [{opts}]}}) {{ clientMutationId }}'),
               ("area", f'createProjectV2Field(input: {{projectId: "{p["id"]}", dataType: TEXT, name: "Area"}}) {{ clientMutationId }}')]
        if not phases:
            ops = ops[1:]
        _, errs = batched(gh, ops)
        if errs:
            print("  field creation errors: " + "; ".join(errs), file=sys.stderr)
    else:
        f = field_map(project).get("Phase")
        have = [o["name"] for o in (f or {}).get("options", [])]
        new = [ph for ph in phases if ph not in have]
        if f and new:
            opts = [f'{{id: "{o["id"]}", name: {s(o["name"])}, color: GRAY, description: ""}}' for o in f["options"]]
            opts += [f'{{name: {s(ph)}, color: GRAY, description: ""}}' for ph in new]
            gh.q(f'mutation {{ updateProjectV2Field(input: {{fieldId: "{f["id"]}", singleSelectOptions: [{", ".join(opts)}]}}) {{ clientMutationId }} }}')
            print("  added phase options: " + ", ".join(new))
    pid = board["github"]["project"]["id"]
    data, _ = gh.q(f'query {{ node(id: "{pid}") {{ ... on ProjectV2 {{ {PROJECT_FIELDS} }} }} }}')
    return data["node"]


def apply(gh, path, board, repo, project, issues, plan):
    project = ensure_project(gh, board, repo, project)
    pid = project["id"]
    fields = field_map(project)
    by_n = plan["_by_n"]
    errors = []

    # 1. Create issues, then save immediately so a later failure cannot cause duplicates.
    if plan["create"]:
        ops = [(f"c{n}", f'createIssue(input: {{repositoryId: "{repo["id"]}", title: {s(by_n[n]["title"])}, body: {s(issue_body(by_n[n]))}}}) {{ issue {{ id number url }} }}')
               for n in plan["create"]]
        res, errs = batched(gh, ops, CREATE_CHUNK)
        errors += errs
        for n in plan["create"]:
            r = res.get(f"c{n}")
            if r:
                by_n[n]["issue"] = r["issue"]["number"]
                issues[r["issue"]["number"]] = {"id": r["issue"]["id"], "number": r["issue"]["number"],
                                                "projectItems": {"nodes": []}, "blockedBy": {"nodes": []},
                                                "parent": None, "state": "OPEN"}
                by_n[n]["synced"] = {"hash": None}
        save(path, board)
        print(f"  created {sum(1 for n in plan['create'] if by_n[n].get('issue'))} issues")

    def iid(n):
        t = by_n[n]
        return (issues.get(t.get("issue")) or {}).get("id")

    # 2. Add new issues to the project.
    need_item = [n for n in plan["create"] + plan["add_item"] if iid(n)]
    items = {}
    if need_item:
        ops = [(f"a{n}", f'addProjectV2ItemById(input: {{projectId: "{pid}", contentId: "{iid(n)}"}}) {{ item {{ id }} }}') for n in need_item]
        res, errs = batched(gh, ops)
        errors += errs
        items = {n: res[f"a{n}"]["item"]["id"] for n in need_item if res.get(f"a{n}")}
    for t in board["tickets"]:
        it = item_in(issues.get(t.get("issue")), pid)
        if it and t["n"] not in items:
            items[t["n"]] = it["id"]

    # 3. Everything else in batched mutations.
    ops = []
    pf, af = fields.get("Phase"), fields.get("Area")
    phase_opt = {o["name"]: o["id"] for o in (pf or {}).get("options", [])}
    write_fields = set(plan["create"]) | set(plan["update"]) | set(plan["add_item"])
    for n in plan["update"]:
        t = by_n[n]
        ops.append((f"u{n}", f'updateIssue(input: {{id: "{iid(n)}", title: {s(t["title"])}, body: {s(issue_body(t))}}}) {{ clientMutationId }}'))
    for n in sorted(write_fields):
        t, item = by_n[n], items.get(n)
        if not item:
            continue
        if pf and t.get("phase") in phase_opt:
            ops.append((f"p{n}", f'updateProjectV2ItemFieldValue(input: {{projectId: "{pid}", itemId: "{item}", fieldId: "{pf["id"]}", value: {{singleSelectOptionId: "{phase_opt[t["phase"]]}"}}}}) {{ clientMutationId }}'))
        elif pf and not t.get("phase"):
            ops.append((f"p{n}", f'clearProjectV2ItemFieldValue(input: {{projectId: "{pid}", itemId: "{item}", fieldId: "{pf["id"]}"}}) {{ clientMutationId }}'))
        if af:
            ops.append((f"r{n}", f'updateProjectV2ItemFieldValue(input: {{projectId: "{pid}", itemId: "{item}", fieldId: "{af["id"]}", value: {{text: {s(t.get("area", ""))}}}}}) {{ clientMutationId }}'))
    sf = fields.get("Status")
    todo = next((o["id"] for o in (sf or {}).get("options", []) if o["name"].lower() in ("todo", "to do")), None)
    if sf and todo:
        for n in plan["create"]:
            if items.get(n):
                ops.append((f"t{n}", f'updateProjectV2ItemFieldValue(input: {{projectId: "{pid}", itemId: "{items[n]}", fieldId: "{sf["id"]}", value: {{singleSelectOptionId: "{todo}"}}}}) {{ clientMutationId }}'))
    for n, b in plan["edges_add"]:
        if iid(n) and iid(b):
            ops.append((f"e{n}_{b}", f'addBlockedBy(input: {{issueId: "{iid(n)}", blockingIssueId: "{iid(b)}"}}) {{ clientMutationId }}'))
    for n, b in plan["edges_remove"]:
        ops.append((f"x{n}_{b}", f'removeBlockedBy(input: {{issueId: "{iid(n)}", blockingIssueId: "{iid(b)}"}}) {{ clientMutationId }}'))
    for n, p in plan["parent_set"]:
        if iid(n) and iid(p):
            ops.append((f"s{n}", f'addSubIssue(input: {{issueId: "{iid(p)}", subIssueId: "{iid(n)}", replaceParent: true}}) {{ clientMutationId }}'))
    for n, p in plan["parent_remove"]:
        ops.append((f"z{n}", f'removeSubIssue(input: {{issueId: "{iid(p)}", subIssueId: "{iid(n)}"}}) {{ clientMutationId }}'))
    if ops:
        res, errs = batched(gh, ops)
        errors += errs
        failed = {a for a, _ in ops} - set(res)
    else:
        failed = set()

    # 4. Record what now matches GitHub, and pull Status.
    for n in set(plan["create"]) | set(plan["update"]) | set(plan["same"]):
        t = by_n[n]
        if t.get("issue") and f"u{n}" not in failed and f"p{n}" not in failed and f"r{n}" not in failed:
            t["synced"] = {"hash": local_hash(t)}
    for n, st in plan["status"]:
        by_n[n]["state"] = st
    for n in plan["create"]:
        by_n[n].setdefault("state", "Todo")
    board["github"]["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save(path, board)
    return errors, len(ops)


# ---------------------------------------------------------------- render

def md_inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
    return t


def md_block(text):
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + md_inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{md_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in (text or "").split("\n"):
        m = re.match(r"\s*[-*] (.*)", line)
        if m:
            if para:
                flush()
            items.append(m.group(1))
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "\n".join(out)


GHERKIN_KW = r"(Feature|Background|Scenario Outline|Scenario|Examples|Given|When|Then|And|But|Característica|Antecedentes|Esquema del escenario|Escenario|Ejemplos|Dado|Dada|Cuando|Entonces|Y|Pero)"


def gherkin_html(src):
    lines = []
    for line in src.strip("\n").split("\n"):
        e = html.escape(line, quote=False)
        # Strings first: the keyword pass adds markup with quotes of its own.
        e = re.sub(r"\"[^\"]*\"", lambda m: f'<span class="str">{m.group(0)}</span>', e)
        e = re.sub(r"^(\s*)" + GHERKIN_KW + r"\b", r'\1<span class="kw">\2</span>', e)
        lines.append(e)
    return "\n".join(lines)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "board"


def render(path, out):
    board = checked(path)
    tpl = open(os.path.join(HERE, "page.html"), encoding="utf-8").read()
    phases = phases_of(board) + ([""] if any(not t.get("phase") for t in board["tickets"]) else [])
    by_n = {t["n"]: t for t in board["tickets"]}
    gh = board.get("github") or {}
    repo_url = f"https://github.com/{gh['repo']}" if gh.get("repo") else ""
    done = {n for n, t in by_n.items() if t.get("state") == "Done"}
    sections = []
    for ph in phases:
        cards = []
        for t in sorted((t for t in board["tickets"] if (t.get("phase") or "") == ph), key=lambda t: t["n"]):
            n, st = t["n"], t.get("state") or "Draft"
            blockers = t.get("blocked_by") or []
            ready = st not in ("Done",) and all(b in done for b in blockers)
            chips = [f'<span class="pill st st-{slug(st)}">{html.escape(st)}</span>']
            if t.get("area"):
                chips.append(f'<span class="pill area">{html.escape(t["area"])}</span>')
            if t.get("issue") and repo_url:
                chips.append(f'<a class="pill issue" href="{repo_url}/issues/{t["issue"]}" target="_blank" rel="noopener">#{t["issue"]}</a>')
            if ready and blockers:
                chips.append('<span class="pill ready">ready</span>')
            meta = []
            if blockers:
                meta.append("Blocked by " + ", ".join(f'<a href="#t-{b}" class="dep{" ok" if b in done else ""}">{b}</a>' for b in blockers))
            if t.get("parent"):
                meta.append(f'Part of <a href="#t-{t["parent"]}" class="dep">{t["parent"]}</a>')
            unblocks = [m for m, u in by_n.items() if n in (u.get("blocked_by") or [])]
            if unblocks:
                meta.append("Unblocks " + ", ".join(f'<a href="#t-{m}" class="dep">{m}</a>' for m in unblocks))
            body = md_block(t.get("summary", ""))
            acc = [a for a in t.get("acceptance") or [] if str(a).strip()]
            if acc:
                body += '<h4>Acceptance criteria</h4><ul class="acc">' + "".join(f"<li>{md_inline(str(a))}</li>" for a in acc) + "</ul>"
            if t.get("gherkin"):
                body += f'<pre class="gherkin">{gherkin_html(t["gherkin"])}</pre>'
            cards.append(f'''<article class="card" id="t-{n}" data-n="{n}" data-phase="{html.escape(ph)}" data-state="{html.escape(st)}" data-area="{html.escape(t.get("area", ""))}">
  <header><span class="num">{n}</span><h3>{md_inline(t["title"])}</h3></header>
  <div class="chips">{"".join(chips)}</div>
  {f'<div class="meta">{" · ".join(meta)}</div>' if meta else ""}
  <div class="body">{body}</div>
  <div class="review"><button type="button" data-v="ok">Looks good</button><button type="button" data-v="chg">Needs change</button>
    <textarea placeholder="What should change?" rows="2"></textarea></div>
</article>''')
        label = ph or "No phase"
        sections.append(f'<section class="phase" data-phase="{html.escape(ph)}"><h2>{html.escape(label)} <span class="count">{len(cards)}</span></h2>\n' + "\n".join(cards) + "</section>")
    sub = []
    if board.get("spec"):
        sub.append(f"Spec: <code>{html.escape(board['spec'])}</code>")
    if repo_url:
        sub.append(f'Repo: <a href="{repo_url}" target="_blank" rel="noopener">{html.escape(gh["repo"])}</a>')
    if (gh.get("project") or {}).get("url"):
        sub.append(f'<a href="{gh["project"]["url"]}" target="_blank" rel="noopener">GitHub Project</a>')
    if gh.get("synced_at"):
        sub.append(f"Synced {html.escape(gh['synced_at'].replace('T', ' ').rstrip('Z'))} UTC")
    page = (tpl.replace("__TITLE__", html.escape(board["title"]))
               .replace("__SUB__", " · ".join(sub))
               .replace("__SECTIONS__", "\n".join(sections))
               .replace("__KEY__", "ticket-board:" + slug(board["title"]))
               .replace("__COUNT__", str(len(board["tickets"]))))
    out = out or os.path.splitext(path)[0] + ".html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(out)


# ---------------------------------------------------------------- commands

def cmd_check(path):
    board = checked(path)
    tickets = board["tickets"]
    print(f"{board['title']}: {len(tickets)} tickets, phases: {', '.join(phases_of(board)) or 'none'}")
    published = sum(1 for t in tickets if t.get("issue"))
    edited = sum(1 for t in tickets if t.get("issue") and local_hash(t) != (t.get("synced") or {}).get("hash"))
    edges = sum(len(t.get("blocked_by") or []) for t in tickets)
    print(f"{published} published, {edited} edited since last sync, {edges} blocked-by links")
    roots = [t["n"] for t in tickets if not t.get("blocked_by")]
    print("can start first: " + ", ".join(f"#{n}" for n in roots))
    if not (board.get("github") or {}).get("repo"):
        print("no github.repo set yet (needed only to sync)")


def need_repo(board):
    if not (board.get("github") or {}).get("repo"):
        die("set github.repo (owner/name) in tickets.json before planning a sync")


def cmd_plan(path, force=()):
    board = checked(path)
    need_repo(board)
    t0 = time.time()
    gh = GH()
    repo, project, issues = read_remote(gh, board)
    plan = make_plan(board, repo, project, issues, force)
    print_plan(board, plan, project)
    print(f"(read in {gh.requests} request, {time.time() - t0:.1f}s)")
    return board, gh, repo, project, issues, plan


def cmd_sync(path, force, yes_create):
    board = checked(path)
    need_repo(board)
    t0 = time.time()
    gh = GH()
    repo, project, issues = read_remote(gh, board)
    if not project and not yes_create:
        die("no GitHub Project yet: run plan, confirm with the user, then sync --yes-create-project")
    plan = make_plan(board, repo, project, issues, force)
    print_plan(board, plan, project)
    errors, nops = apply(gh, path, board, repo, project, issues, plan)
    print(f"done in {time.time() - t0:.1f}s, {gh.requests} GitHub requests")
    if errors:
        print(f"{len(errors)} errors (rerun sync to retry; it is idempotent):", file=sys.stderr)
        for e in errors[:15]:
            print("  - " + e, file=sys.stderr)
        sys.exit(2)


def cmd_diff(path, n):
    board = checked(path)
    need_repo(board)
    t = next((t for t in board["tickets"] if t["n"] == n), None)
    if not t or not t.get("issue"):
        die(f"ticket {n} has no issue yet")
    gh = GH()
    repo, project, issues = read_remote(gh, board)
    issue = issues.get(t["issue"])
    if not issue:
        die(f"issue #{t['issue']} not found")
    r = remote_view(issue, (project or {}).get("id"))
    local = [f"title: {t['title']}", f"phase: {t.get('phase', '')}", f"area: {t.get('area', '')}", ""] + issue_body(t).split("\n")
    remote = [f"title: {r['title']}", f"phase: {r['phase']}", f"area: {r['area']}", ""] + r["body"].split("\n")
    out = list(difflib.unified_diff(local, remote, f"tickets.json #{n}", f"GitHub #{t['issue']}", lineterm=""))
    print("\n".join(out) if out else "no differences")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "render", "plan", "sync", "diff"):
        p = sub.add_parser(name)
        p.add_argument("tickets")
        if name == "render":
            p.add_argument("-o", "--out")
        if name in ("plan", "sync"):
            p.add_argument("--force", default="", help="comma-separated ticket numbers to push over GitHub edits")
        if name == "sync":
            p.add_argument("--yes-create-project", action="store_true")
        if name == "diff":
            p.add_argument("n", type=int)
    a = ap.parse_args()
    force = tuple(int(x) for x in getattr(a, "force", "").split(",") if x.strip())
    if a.cmd == "check":
        cmd_check(a.tickets)
    elif a.cmd == "render":
        render(a.tickets, a.out)
    elif a.cmd == "plan":
        cmd_plan(a.tickets, force)
    elif a.cmd == "sync":
        cmd_sync(a.tickets, force, a.yes_create_project)
    elif a.cmd == "diff":
        cmd_diff(a.tickets, a.n)


if __name__ == "__main__":
    main()
