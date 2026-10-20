"""The optional Work IQ check after a seed run: which prompts to ask, and what the answers cited.

A seed run proves the content was written, not that Work IQ answers from it: Microsoft 365 indexes
new content over minutes to hours. This module never calls a tenant. The agent asks Work IQ itself
(the Work IQ MCP server in GitHub Copilot, signed in as one of the demo users) and records the
answers here; a citation counts as seeded when its title names an item the run created.
"""

import re

from .common import Usage, utc_now
from . import content, demo, workspace

CHAT_FIRST = re.compile(r"copilot|chat", re.IGNORECASE)
PREFIX = re.compile(r"^(?:(?:re|fw|fwd|aw|wg|sv|vs|tr|rv)\s*:\s*)+", re.IGNORECASE)
MIN_PARTIAL = 8


def normal(title):
    """A title compared loosely: lowercase, single spaces, no RE:/FW: prefixes, no Office extension."""
    text = PREFIX.sub("", " ".join(str(title or "").split())).lower()
    return re.sub(r"\.(docx|xlsx|pptx)$", "", text)


def matches(title, names):
    """The seeded names a citation title refers to (exact, or one containing the other when long enough)."""
    cited = normal(title)
    if not cited:
        return []
    found = []
    for name in names:
        seeded = normal(name)
        if not seeded:
            continue
        shorter = min(len(seeded), len(cited))
        if cited == seeded or (shorter >= MIN_PARTIAL and (seeded in cited or cited in seeded)):
            found.append(name)
    return found


def seeded_names(doc, manifest_data):
    """Titles of what the run created: mail subjects, file names, meetings, chat topics, team and channels."""
    items = manifest_data.get("items") or {}

    def made(key):
        entry = items.get(key) or {}
        return any(entry.get(field) for field in content.CREATED_KEYS)

    names = []
    names += [t["subject"] for t in doc.get("mail") or [] if made(f"mail:{t['key']}")]
    names += [f["name"] for f in doc.get("files") or [] if made(f"file:{f['key']}")]
    names += [m["subject"] for m in doc.get("meetings") or [] if made(f"meeting:{m['key']}")]
    names += [c["topic"] for c in doc.get("chats") or [] if c.get("topic") and made(f"chat:{c['key']}")]
    team = doc.get("channel")
    if team and made("team"):
        names += [team["team"], *(channel["name"] for channel in team.get("channels") or [])]
    return sorted(set(names))


def prompts(run, count=3):
    """Up to `count` of the demo's prompts, Copilot Chat surfaces first, and the demo users to ask as."""
    doc = run.demo()
    if not doc:
        raise Usage("no-demo", f"{run.id} has no demo copy; it is not a seed run.")
    people = demo.by_key(doc)
    listed = (doc.get("guide") or {}).get("prompts") or []
    ordered = sorted(range(len(listed)), key=lambda i: (0 if CHAT_FIRST.search(listed[i].get("surface", "")) else 1, i))
    chosen = [{"number": i + 1, "surface": listed[i]["surface"], "as": people.get(listed[i]["as"], {}).get("upn"),
               "prompt": listed[i]["prompt"], "expect": listed[i].get("expect") or []}
              for i in ordered[:max(1, min(count, 20))]]
    return {"runId": run.id, "demoUsers": [p["upn"] for p in demo.people(doc)], "prompts": chosen,
            "next": ["Ask each prompt with the Work IQ MCP server, signed in as one of demoUsers (ideally the prompt's "
                     "`as`), and record the answers with `workiq record`."]}


def _results(path):
    data = workspace.read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list) or not data["answers"]:
        raise Usage("bad-results", f"{path} must hold {{\"askedAs\": UPN, \"answers\": [{{\"prompt\", \"answered\", "
                    "\"citations\": [{\"title\", \"url\"}]}]}.")
    answers = []
    for index, answer in enumerate(data["answers"]):
        if not isinstance(answer, dict) or not isinstance(answer.get("prompt"), str) or \
                not isinstance(answer.get("answered"), bool):
            raise Usage("bad-results", f"answers[{index}] needs a prompt (text) and answered (true or false).")
        citations = []
        for number, citation in enumerate(answer.get("citations") or []):
            if not isinstance(citation, dict) or not isinstance(citation.get("title"), str):
                raise Usage("bad-results", f"answers[{index}].citations[{number}] needs a title (text).")
            citations.append({"title": citation["title"][:300], "url": str(citation.get("url") or "")[:2000]})
        answers.append({"prompt": answer["prompt"][:2000], "answered": answer["answered"], "citations": citations})
    asked_as = data.get("askedAs")
    return (asked_as.strip().lower() if isinstance(asked_as, str) else None), answers


def record(run, results_path):
    """Score the agent's Work IQ answers against what the run seeded; keeps every check in workiq.json."""
    doc = run.demo()
    if not doc:
        raise Usage("no-demo", f"{run.id} has no demo copy; it is not a seed run.")
    asked_as, answers = _results(results_path)
    names = seeded_names(doc, content.Manifest(run.path("manifest.json")).data)
    expected = {p["prompt"]: p.get("expect") or [] for p in (doc.get("guide") or {}).get("prompts") or []}
    scored = []
    for answer in answers:
        seeded = sorted({name for c in answer["citations"] for name in matches(c["title"], names)})
        expect = expected.get(answer["prompt"], [])
        scored.append({**answer, "seededCitations": seeded,
                       "expectedCited": sorted({e for c in answer["citations"] for e in matches(c["title"], expect)})})
    with_seeded = sum(1 for a in scored if a["answered"] and a["seededCitations"])
    answered = sum(1 for a in scored if a["answered"])
    verdict = ("found-seeded-content" if with_seeded else
               "answered-without-seeded-citations" if answered else "not-found")
    people = {p["upn"] for p in demo.people(doc)}
    check = {"checkedAt": utc_now(), "askedAs": asked_as, "askedAsIsDemoUser": asked_as in people, "verdict": verdict,
             "prompts": len(scored), "answered": answered, "withSeededCitations": with_seeded, "answers": scored}
    path = run.path("workiq.json")
    history = workspace.read_json(path, default={}) or {}
    workspace.write_json(path, {"latest": check, "checks": [*(history.get("checks") or []), check]})
    summary = {key: check[key] for key in ("verdict", "prompts", "answered", "withSeededCitations", "askedAs",
                                           "askedAsIsDemoUser", "checkedAt")}
    return {"runId": run.id, "file": str(path), **summary,
            "answers": [{"prompt": a["prompt"], "answered": a["answered"], "seededCitations": a["seededCitations"]}
                        for a in scored]}


def latest(run):
    return (workspace.read_json(run.path("workiq.json"), default={}) or {}).get("latest")
