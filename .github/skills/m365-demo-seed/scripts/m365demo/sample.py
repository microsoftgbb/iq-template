"""The template's sample story, North Works / South Works, mapped onto 2 to 8 demo users.

It matches the fictional asset-operations scenario that workspace/SeedScenario.Notebook seeds:
North Works runs Pump A (operational) and Pump B (in maintenance); South Works runs Conveyor A.
Every site, asset, person's role and status is fictional. Content refers to role slots; with
fewer people than slots, slots wrap around and the result is tidied so nobody writes to
themselves and every chat still has two or more members.
"""

import re

from . import demo

ROLES = ("Operations Manager, North Works", "Maintenance Planner", "Reliability Engineer", "Site Lead, South Works",
         "Procurement Coordinator", "Shift Supervisor, North Works", "Production Analyst", "Safety Lead")
OPS, PLANNER, RELIABILITY, SOUTH, PROCUREMENT = range(5)


def _key(base, taken):
    key = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")[:24] or "person"
    if not key[0].isalpha():
        key = "p-" + key
    candidate, number = key, 2
    while candidate in taken:
        candidate, number = f"{key}-{number}", number + 1
    taken.add(candidate)
    return candidate


def _people(people):
    taken, result = set(), []
    for index, (upn, name) in enumerate(people):
        local = upn.split("@", 1)[0]
        display = name or re.sub(r"[._-]+", " ", local).title()
        result.append({"key": _key(display.split()[0] if display.split() else local, taken), "upn": upn.lower(),
                       "name": display, "role": ROLES[index % len(ROLES)]})
    return result


def build(admin, people, title=None):
    """A complete demo.json document for these people: [(upn, display name or None)]."""
    persons = _people(people)
    count = len(persons)

    def slot(number):
        return persons[number % count]["key"]

    def first(number):
        return persons[number % count]["name"].split()[0]

    def others(owner_key):
        return [person["key"] for person in persons if person["key"] != owner_key]

    def tidy(sender, keys):
        chosen = []
        for key in keys:
            if key != sender and key not in chosen:
                chosen.append(key)
        return chosen or [next(key for key in others(sender))]

    files = [
        {"key": "asset-status", "type": "xlsx", "name": "North Works Asset Status.xlsx", "owner": slot(PLANNER),
         "folder": "Operations", "sheets": [
             {"name": "Assets", "rows": [
                 ["Site", "Region", "Asset", "Status", "Next action", "Owner"],
                 ["North Works", "North", "Pump A", "Operational", "Routine inspection on Friday", first(RELIABILITY)],
                 ["North Works", "North", "Pump B", "Maintenance", "Bearing and seal replacement", first(PLANNER)],
                 ["South Works", "South", "Conveyor A", "Operational", "Run extended shifts while Pump B is down",
                  first(SOUTH)]]},
             {"name": "Pump B parts", "rows": [
                 ["Part", "Quantity", "Supplier", "Expected", "Status"],
                 ["Drive-end bearing", 2, "Northline Industrial", "Tuesday", "Confirmed"],
                 ["Mechanical seal kit", 1, "Northline Industrial", "Wednesday", "Confirmed"],
                 ["Coupling insert", 1, "Harbor Supply", "Thursday", "Awaiting confirmation"]]}]},
        {"key": "pump-b-plan", "type": "docx", "name": "Pump B Maintenance Plan.docx", "owner": slot(RELIABILITY),
         "folder": "Operations", "title": "Pump B Maintenance Plan - North Works", "sections": [
             {"heading": "Purpose", "paragraphs": [
                 "Pump B at North Works shows rising vibration on the drive-end bearing. This plan replaces the bearing "
                 "and mechanical seal in a planned window instead of risking an unplanned stop."]},
             {"heading": "Window and scope", "bullets": [
                 "Planned outage: three shifts, starting Wednesday morning.",
                 "Replace the drive-end bearing and the mechanical seal; inspect the coupling.",
                 "Pump A stays in service; South Works covers part of the lost throughput with Conveyor A."]},
             {"heading": "Parts and contractor", "paragraphs": [
                 "Bearings and the seal kit are confirmed. The coupling insert awaits supplier confirmation; if it is "
                 "late we inspect and reuse the current insert. The alignment contractor is booked for Thursday."]},
             {"heading": "Return-to-service checks", "bullets": [
                 "Vibration below the alert threshold for two hours at normal load.",
                 "No seal leakage after the first shift.",
                 "Sign-off by the reliability engineer and the shift supervisor."]},
             {"heading": "Risks", "bullets": [
                 "Late coupling insert: mitigated by inspect-and-reuse.",
                 "South Works overtime limits: capacity confirmed for three shifts only."]}]},
        {"key": "weekly-review", "type": "pptx", "name": "Asset Operations Weekly Review.pptx", "owner": slot(OPS),
         "folder": "Operations", "slides": [
             {"title": "Asset operations weekly review", "bullets": ["North Works and South Works",
                                                                     "Focus: Pump B planned maintenance"]},
             {"title": "Site status", "bullets": ["North Works: Pump A operational, Pump B in maintenance",
                                                  "South Works: Conveyor A operational, extended shifts planned"]},
             {"title": "Pump B maintenance window", "bullets": ["Three shifts from Wednesday",
                                                                "Bearing and seal replacement, coupling inspection",
                                                                "Alignment contractor booked for Thursday"]},
             {"title": "Throughput plan", "bullets": ["North Works runs on Pump A at reduced rate",
                                                      "South Works absorbs about 15% of North Works volume",
                                                      "Overtime approved for three shifts"]},
             {"title": "Decisions needed", "bullets": ["Approve the maintenance window",
                                                       "Confirm the leadership update for Friday"]}]},
    ]
    for item in files:
        item["sharedWith"] = others(item["owner"])

    def thread(key, subject, sender, to, cc, body, replies):
        to = tidy(sender, to)
        cc = [k for k in dict.fromkeys(cc) if k != sender and k not in to]
        first_message = {"from": sender, "to": to, "body": body}
        if cc:
            first_message["cc"] = cc
        return {"key": key, "subject": subject,
                "messages": [first_message] + [{"from": who, "body": text} for who, text in replies]}

    mail = [
        thread("pump-b-window", "Pump B maintenance window: parts and contractor", slot(PLANNER), [slot(OPS)],
               [slot(PROCUREMENT)],
               f"Hi {first(OPS)},\n\nI'd like to take Pump B down for three shifts from Wednesday morning to replace the "
               "drive-end bearing and the mechanical seal. Vibration has been climbing for two weeks.\n\n"
               "- Bearings and seal kit: confirmed\n- Coupling insert: waiting on the supplier\n"
               "- Alignment contractor: booked for Thursday\n\n"
               "The parts list is in {{file:asset-status}}. Can you approve the window?",
               [(slot(PROCUREMENT), "The coupling insert should be confirmed by Thursday. If it slips, we can inspect and "
                                    "reuse the current one as the plan says."),
                (slot(OPS), "Approved. Let's confirm the throughput cover with South Works before Wednesday.")]),
        thread("throughput-cover", "Throughput while Pump B is down", slot(OPS), [slot(SOUTH)], [slot(RELIABILITY)],
               f"Hi {first(SOUTH)},\n\nWhile Pump B is in maintenance, North Works runs on Pump A at a reduced rate. Can "
               "South Works absorb part of our volume on Conveyor A for three shifts?",
               [(slot(SOUTH), "We can take about 15% of North Works volume for three shifts with overtime. Beyond that "
                              "we would need to move a planned stop."),
                (slot(RELIABILITY), "Three shifts matches the plan. The return-to-service checks are in "
                                    "{{file:pump-b-plan}}.")]),
        thread("leadership-update", "Leadership update draft: Pump B", slot(OPS),
               [slot(PLANNER), slot(RELIABILITY), slot(SOUTH)], [slot(PROCUREMENT)],
               "Team,\n\nHere is the draft for Friday's leadership update: {{file:weekly-review}}. Please flag anything "
               "that is out of date by Thursday noon.",
               [(slot(RELIABILITY), "Looks right. I added the return-to-service criteria on the window slide.")]),
    ]

    chats, seen_pairs = [], {}

    def chat(key, members, topic, messages):
        unique = list(dict.fromkeys(members))
        if len(unique) < 2:
            return
        body = [{"from": who, "text": text} for who, text in messages if who in unique]
        if len(unique) == 2:
            pair = frozenset(unique)
            if pair in seen_pairs:
                seen_pairs[pair]["messages"].extend(body)
                return
            entry = {"key": key, "members": unique, "messages": body}
            seen_pairs[pair] = entry
        else:
            entry = {"key": key, "members": unique, "topic": topic, "messages": body}
        chats.append(entry)

    chat("vibration", [slot(OPS), slot(RELIABILITY)], None, [
        (slot(RELIABILITY), "Pump B drive-end vibration was up again on the night shift."),
        (slot(OPS), "Is it still safe to run until Wednesday?"),
        (slot(RELIABILITY), "Yes, if we keep it below 80% load. I'll watch the trend each shift."),
        (slot(OPS), "Thanks. Flag it right away if it crosses the alert threshold.")])
    chat("parts", [slot(PLANNER), slot(PROCUREMENT), slot(RELIABILITY)], "Pump B parts", [
        (slot(PLANNER), "Quick check on the Pump B parts before Wednesday."),
        (slot(PROCUREMENT), "Bearings and the seal kit arrive Tuesday and Wednesday. The coupling insert is still open."),
        (slot(RELIABILITY), "If the insert is late, I'm fine inspecting and reusing the current one."),
        (slot(PLANNER), "Great, I'll note that in the plan.")])

    team = {"team": "North Works Operations", "description": "Day-to-day coordination for North Works.",
            "owner": slot(OPS), "members": others(slot(OPS)), "channels": [
                {"name": "Pump B maintenance", "description": "Planning and status for the Pump B outage.", "posts": [
                    {"from": slot(PLANNER), "subject": "Pump B outage starts Wednesday",
                     "text": "Pump B comes down Wednesday morning for three shifts. Plan: {{file:pump-b-plan}}.",
                     "replies": [{"from": slot(RELIABILITY), "text": "I'll be on site for the bearing change."},
                                 {"from": slot(OPS), "text": "Thanks all. South Works covers part of the volume."}]},
                    {"from": slot(RELIABILITY), "text": "Return-to-service checks are agreed: two hours below the "
                                                        "vibration threshold and no seal leakage after the first shift.",
                     "replies": [{"from": slot(PLANNER), "text": "Added to the plan."}]}]},
                {"name": "General", "posts": [
                    {"from": slot(OPS), "text": "Reminder: weekly operations sync covers the Pump B window and the "
                                                "throughput plan. Deck: {{file:weekly-review}}."}]}]}
    if count < 2 or not team["members"]:
        team = None

    def meeting(key, subject, organizer, attendees, day, start, minutes, agenda, messages):
        invited = tidy(organizer, attendees)
        allowed = set(invited) | {organizer}
        entry = {"key": key, "subject": subject, "organizer": organizer, "attendees": invited, "day": day,
                 "start": start, "minutes": minutes, "agenda": agenda}
        lines = [{"from": who, "text": text} for who, text in messages if who in allowed]
        if lines:
            entry["chat"] = lines
        return entry

    meetings = [
        meeting("weekly-sync", "Weekly operations sync", slot(OPS), others(slot(OPS)), -2, "10:00", 30,
                "- Site status for North Works and South Works\n- Pump B maintenance window\n- Throughput plan while "
                "Pump B is down\n- Leadership update for Friday\n\nDeck: {{file:weekly-review}}",
                [(slot(PLANNER), "Window confirmed for Wednesday, three shifts."),
                 (slot(SOUTH), "South Works can cover about 15% of the volume.")]),
        meeting("return-to-service", "Pump B return-to-service review", slot(RELIABILITY), [slot(OPS), slot(PLANNER)],
                3, "14:00", 45,
                "Review the bearing and seal work, the vibration readings and the sign-off before Pump B returns to "
                "service. Criteria: {{file:pump-b-plan}}.", []),
    ]

    prompts = [
        {"surface": "Microsoft 365 Copilot Chat", "as": slot(OPS),
         "prompt": "Where does the Pump B maintenance at North Works stand, and what decisions are waiting on me?",
         "expect": ["Pump B Maintenance Plan.docx", "Pump B maintenance window: parts and contractor"]},
        {"surface": "Outlook", "as": slot(PLANNER),
         "prompt": "Summarize the thread about the Pump B maintenance window and list any open questions.",
         "expect": ["Pump B maintenance window: parts and contractor"]},
        {"surface": "Teams", "as": slot(RELIABILITY),
         "prompt": "What did the team agree in the weekly operations sync about Pump B?",
         "expect": ["Weekly operations sync"]},
        {"surface": "Microsoft 365 Copilot Chat", "as": slot(SOUTH),
         "prompt": "What did I commit to for covering North Works volume while Pump B is down?",
         "expect": ["Throughput while Pump B is down"]},
        {"surface": "Excel", "as": slot(PLANNER),
         "prompt": "Which assets are not operational, and what is the next action for each?",
         "expect": ["North Works Asset Status.xlsx"]},
    ]

    doc = {"version": 1, "title": title or "Pump B maintenance at North Works",
           "summary": "The operations team runs two fictional plants. North Works, in the North region, has Pump A, "
                      "which is operational, and Pump B, which goes into planned maintenance. South Works, in the South "
                      "region, runs Conveyor A. Across mail, Teams chats and a channel, two meetings and three Office "
                      "files, the team plans Pump B's maintenance window, confirms parts and a contractor, agrees "
                      "throughput cover from South Works and prepares a leadership update. Everything is fictional.",
           "adminUpn": admin.lower(), "timezone": "UTC", "people": persons, "files": files, "mail": mail,
           "chats": chats, "meetings": meetings, "guide": {"prompts": prompts}}
    if team:
        doc["channel"] = team
    errors, _ = demo.validate(doc)
    if errors:
        raise AssertionError("the sample story is invalid: " + "; ".join(errors[:5]))
    return doc
