"""Create the demo content in Microsoft 365: each item as its demo user, the team as the administrator.

Order: the team's group first (it needs minutes to become a team), then files (so later items can
link them), mail, chats, meetings and the channel posts. The run's manifest records each created
item before the next one, so an interrupted run resumes where it stopped and cleanup knows exactly
what exists. One failed item is recorded and the run continues; a cancellation stops it.
"""

import contextlib
from datetime import datetime, time as clock_time, timedelta, timezone
import re
import secrets
import urllib.parse

from .common import Stop, utc_now
from . import office, pace, text, workspace
from .graph import Graph, GraphError, add_to_group, odata_string, query, segment

DELIVERY = [0, 3, 5, 8, 12, 20, 30, 45, 60]
CHAT_PROPAGATION = [0, 2, 5, 10, 20, 30]
ROSTER_PROPAGATION = [0, 10, 20, 30, 45, 60, 90, 120, 120, 180]
TEAM_PROMOTION = [0, 10, 10, 20, 30, 30, 45, 60, 60, 90]
TEAM_READY = [0, 5, 10, 15, 20, 30, 30, 45, 60]
DRIVE_READY = [0, 15, 30, 60, 60]
MEETING_THREAD = re.compile(r"19:meeting_[^@/\s]+@thread\.v2")
# Manifest fields that mean an item exists in the tenant, at least partly.
CREATED_KEYS = ("itemId", "chatId", "eventId", "groupId", "mailNickname", "sent", "pending")


class Cancelled(Stop):
    code = 2


class Manifest:
    """manifest.json: every created item and its identifiers; the cleanup source and resume ledger."""

    def __init__(self, path, run_id=None, demo_name=None):
        self.path = path
        self.data = workspace.read_json(path) or {"version": 1, "runId": run_id, "demo": demo_name, "items": {}}

    def item(self, key):
        return self.data["items"].setdefault(key, {"status": "pending"})

    def save(self):
        self.data["updatedAt"] = utc_now()
        workspace.write_json(self.path, self.data)

    def counts(self):
        statuses = [entry.get("status") for entry in self.data["items"].values()]
        return {status: statuses.count(status) for status in sorted(set(statuses))}

    def created(self):
        """How many items exist in the tenant, at least partly (what a cleanup would have to delete)."""
        return sum(1 for entry in self.data["items"].values()
                   if not entry.get("deleted") and any(entry.get(key) for key in CREATED_KEYS))


def business_day(start, offset):
    """The date `offset` working days (Monday to Friday) from `start`; 0 is start itself (moved off a weekend)."""
    day = start
    while day.weekday() >= 5:
        day += timedelta(days=1)
    step = 1 if offset >= 0 else -1
    for _ in range(abs(offset)):
        day += timedelta(days=step)
        while day.weekday() >= 5:
            day += timedelta(days=step)
    return day


def meeting_thread(join_url):
    match = MEETING_THREAD.search(urllib.parse.unquote(join_url or ""))
    return match.group(0) if match else None


def merge_reply(quoted, new_html):
    """New text above the quoted thread of a createReplyAll draft, as Outlook places it."""
    block = f"<div>{new_html}</div>"
    if not quoted:
        return block
    for marker in ('<div id="appendonsend"', "<hr"):
        index = quoted.find(marker)
        if index >= 0:
            return quoted[:index] + block + quoted[index:]
    body = re.search(r"<body[^>]*>", quoted, re.IGNORECASE)
    if body:
        return quoted[:body.end()] + block + quoted[body.end():]
    return block + quoted


def units(doc):
    """Work units for progress: one per file, message, post, reply, meeting and meeting chat line."""
    channel = doc.get("channel") or {}
    posts = [post for item in channel.get("channels") or [] for post in item.get("posts") or []]
    return (len(doc.get("files") or []) + sum(len(t["messages"]) for t in doc.get("mail") or [])
            + sum(len(c["messages"]) for c in doc.get("chats") or [])
            + len(posts) + sum(len(p.get("replies") or []) for p in posts)
            + sum(1 + len(m.get("chat") or []) for m in doc.get("meetings") or []))


class Seeder:
    def __init__(self, doc, people, admin, manifest, *, run_id, log, advance, cancelled, today=None):
        """people maps a person key to {key, upn, name, id, session (or None when they could not sign in)}."""
        self.doc, self.people, self.admin, self.manifest = doc, people, admin, manifest
        self.admin_graph = Graph(admin)
        self.run_id, self.log, self._advance, self._cancelled = run_id, log, advance, cancelled
        self.today = today or datetime.now(timezone.utc).date()
        self.links, self.failures, self._drives, self._on_roster = {}, [], set(), set()

    # --- shared -------------------------------------------------------------------------------
    def check_cancel(self):
        if self._cancelled():
            raise Cancelled("cancelled", "The run was cancelled.")

    @contextlib.contextmanager
    def work(self, key, count):
        """Track one item's progress units and record its failure; the remaining units count as done."""
        left = {"units": count}

        def tick(number=1):
            left["units"] -= number
            self._advance(number)

        entry = self.manifest.item(key)
        if entry.get("status") == "failed":
            entry["status"] = "retrying"
            entry.pop("error", None)
        try:
            yield entry, tick
        except Cancelled:
            raise
        except Stop as error:
            entry.update(status="failed", error=str(error))
            self.failures.append({"item": key, "error": str(error)})
            self.log(f"FAILED {key}: {error}")
        finally:
            self.manifest.save()
            if left["units"] > 0:
                self._advance(left["units"])

    def graph(self, key):
        person = self.people[key]
        if not person.get("session"):
            raise Stop("no-session", f"{person['upn']} could not sign in, so their content was skipped.")
        return Graph(person["session"])

    def upn(self, key):
        return self.people[key]["upn"]

    def member(self, key):
        return {"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["owner"],
                "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{self.people[key]['id']}')"}

    def html(self, value):
        return text.to_html(value, self.links)

    def post_message(self, graph, path, html, *, subject=None, schedule=CHAT_PROPAGATION):
        """Post a Teams message, waiting out membership propagation (403/404) after a chat or team is new."""
        body = {"body": {"contentType": "html", "content": html}}
        if subject:
            body["subject"] = subject
        for _, last in pace.waits(schedule):
            response = graph.call("POST", path, json_body=body)
            if response.ok:
                return response.json(default={}) or {}
            if response.status in (403, 404) and not last:
                continue
            raise GraphError("POST", path, response)
        return {}

    # --- files ----------------------------------------------------------------------------------
    def ensure_drive(self, key, graph):
        if key in self._drives:
            return
        for _, last in pace.waits(DRIVE_READY):
            response = graph.call("GET", "me/drive?$select=id")
            if response.ok:
                self._drives.add(key)
                return
            if response.status in (404, 423, 500, 503) and not last:
                continue
            raise GraphError("GET", "me/drive", response)

    def seed_files(self):
        title = self.doc.get("title") or "the demo"
        for item in self.doc.get("files") or []:
            self.check_cancel()
            key = f"file:{item['key']}"
            with self.work(key, 1) as (entry, tick):
                if entry.get("status") != "done":
                    graph = self.graph(item["owner"])
                    self.ensure_drive(item["owner"], graph)
                    data, media = office.build(item, author=self.people[item["owner"]].get("name") or "m365-demo-seed")
                    parts = [part for part in (item.get("folder") or "").split("/") if part] + [item["name"]]
                    uploaded = graph.put(f"me/drive/root:/{'/'.join(segment(part) for part in parts)}:/content", data,
                                         media)
                    entry.update(status="uploaded", owner=self.upn(item["owner"]), itemId=uploaded["id"],
                                 name=uploaded.get("name") or item["name"], webUrl=uploaded.get("webUrl"),
                                 path="/".join(parts))
                    self.manifest.save()
                    recipients = [{"email": self.upn(person)} for person in item.get("sharedWith") or []]
                    entry["sharedWith"] = [r["email"] for r in recipients]
                    if recipients:
                        graph.post(f"me/drive/items/{uploaded['id']}/invite", {
                            "recipients": recipients, "requireSignIn": True, "sendInvitation": True, "roles": ["write"],
                            "message": f"Sharing {item['name']} for {title}."})
                    entry.update(status="done", sharedWith=[r["email"] for r in recipients])
                    self.log(f"File {item['name']} uploaded by {self.upn(item['owner'])}"
                             f" and shared with {len(recipients)}.")
                self.links[item["key"]] = (entry.get("name") or item["name"], entry.get("webUrl"))
                tick()

    # --- mail -----------------------------------------------------------------------------------
    def find_message(self, graph, previous):
        """The previous message of a thread in this user's mailbox, waiting for its delivery."""
        if previous.get("internetMessageId"):
            path = "me/messages?" + query(filter=f"internetMessageId eq {odata_string(previous['internetMessageId'])}",
                                          select="id,receivedDateTime")
        else:
            path = "me/messages?" + query(filter=f"conversationId eq {odata_string(previous['conversationId'])}",
                                          select="id,receivedDateTime")
        for _, last in pace.waits(DELIVERY):
            found = graph.get(path).get("value") or []
            if found:
                return max(found, key=lambda message: message.get("receivedDateTime") or "")
        return None

    def mark_sent(self, entry):
        pending = entry.pop("pending")
        entry["sent"].append({key: pending.get(key) for key in ("index", "from", "internetMessageId",
                                                                 "conversationId")})
        self.manifest.save()

    def finish_pending(self, graph, entry, index):
        """True when an earlier attempt's draft for this message was sent (now, or before it was interrupted)."""
        pending = entry.get("pending")
        if not pending or pending.get("index") != index:
            return False
        if pending.get("internetMessageId"):
            path = "me/mailFolders/sentitems/messages?" + query(
                filter=f"internetMessageId eq {odata_string(pending['internetMessageId'])}", select="id")
            if graph.get(path).get("value"):
                self.mark_sent(entry)
                return True
        response = graph.call("POST", f"me/messages/{pending['id']}/send")
        if response.ok:
            self.mark_sent(entry)
            return True
        if response.status != 404:
            raise GraphError("POST", f"me/messages/{pending['id']}/send", response)
        if not pending.get("internetMessageId"):
            # Unverifiable in Sent Items: sending is what moves a draft away, so a missing draft was sent.
            self.mark_sent(entry)
            return True
        entry.pop("pending")
        self.manifest.save()
        return False

    def seed_mail(self):
        for thread in self.doc.get("mail") or []:
            self.check_cancel()
            key = f"mail:{thread['key']}"
            with self.work(key, len(thread["messages"])) as (entry, tick):
                sent = entry.setdefault("sent", [])
                entry["participants"] = sorted({self.upn(k) for m in thread["messages"]
                                                for k in [m["from"], *(m.get("to") or []), *(m.get("cc") or [])]})
                tick(len(sent))
                for index, message in enumerate(thread["messages"]):
                    if index < len(sent):
                        continue
                    self.check_cancel()
                    graph = self.graph(message["from"])
                    if self.finish_pending(graph, entry, index):
                        tick()
                        continue
                    body = self.html(message["body"])
                    if index == 0:
                        draft = graph.post("me/messages", {
                            "subject": thread["subject"], "body": {"contentType": "HTML", "content": body},
                            "toRecipients": [{"emailAddress": {"address": self.upn(k)}} for k in message["to"]],
                            "ccRecipients": [{"emailAddress": {"address": self.upn(k)}} for k in message.get("cc") or []]})
                    else:
                        found = self.find_message(graph, sent[-1])
                        if not found:
                            raise Stop("not-delivered", f"The previous message did not reach {self.upn(message['from'])} "
                                       "in time, so the reply was not sent.")
                        draft = graph.post(f"me/messages/{found['id']}/createReplyAll", {})
                        merged = merge_reply((draft.get("body") or {}).get("content") or "", body)
                        graph.patch(f"me/messages/{draft['id']}", {"body": {"contentType": "HTML", "content": merged}})
                    if not draft.get("internetMessageId"):
                        draft.update(graph.get(f"me/messages/{draft['id']}?$select=internetMessageId,conversationId"))
                    entry["pending"] = {"index": index, "id": draft["id"], "from": self.upn(message["from"]),
                                        "internetMessageId": draft.get("internetMessageId"),
                                        "conversationId": draft.get("conversationId")}
                    self.manifest.save()
                    graph.post(f"me/messages/{draft['id']}/send")
                    self.mark_sent(entry)
                    tick()
                entry["status"] = "done"
                self.log(f"Mail thread '{thread['subject']}': {len(sent)} message(s).")

    # --- chats ----------------------------------------------------------------------------------
    def seed_chats(self):
        for chat in self.doc.get("chats") or []:
            self.check_cancel()
            key = f"chat:{chat['key']}"
            with self.work(key, len(chat["messages"])) as (entry, tick):
                if not entry.get("chatId"):
                    creator = chat["messages"][0]["from"]
                    body = {"chatType": "oneOnOne" if len(chat["members"]) == 2 else "group",
                            "members": [self.member(k) for k in chat["members"]]}
                    if len(chat["members"]) > 2:
                        body["topic"] = chat["topic"]
                    created = self.graph(creator).post("chats", body)
                    entry.update(chatId=created["id"], chatType=body["chatType"], creator=self.upn(creator),
                                 members=[self.upn(k) for k in chat["members"]], posted=0, messages=[])
                    self.manifest.save()
                posted = entry.get("posted", 0)
                tick(posted)
                for index, message in enumerate(chat["messages"]):
                    if index < posted:
                        continue
                    self.check_cancel()
                    posted_message = self.post_message(self.graph(message["from"]), f"chats/{entry['chatId']}/messages",
                                                       self.html(message["text"]))
                    entry.setdefault("messages", []).append({"id": posted_message.get("id"),
                                                             "from": self.upn(message["from"])})
                    entry["posted"] = index + 1
                    self.manifest.save()
                    tick()
                entry["status"] = "done"
                self.log(f"Chat {chat.get('topic') or ' and '.join(entry['members'])}: {entry['posted']} message(s).")

    # --- meetings -------------------------------------------------------------------------------
    def seed_meetings(self):
        zone = self.doc.get("timezone") or "UTC"
        for meeting in self.doc.get("meetings") or []:
            self.check_cancel()
            key = f"meeting:{meeting['key']}"
            lines = meeting.get("chat") or []
            with self.work(key, 1 + len(lines)) as (entry, tick):
                if not entry.get("eventId"):
                    hours, minutes = (int(part) for part in meeting["start"].split(":"))
                    start = datetime.combine(business_day(self.today, meeting["day"]), clock_time(hours, minutes))
                    end = start + timedelta(minutes=meeting["minutes"])
                    event = self.graph(meeting["organizer"]).post("me/events", {
                        "subject": meeting["subject"], "body": {"contentType": "HTML", "content": self.html(meeting["agenda"])},
                        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone},
                        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone},
                        "attendees": [{"emailAddress": {"address": self.upn(k), "name": self.people[k].get("name")},
                                       "type": "required"} for k in meeting["attendees"]],
                        "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness",
                        "allowNewTimeProposals": True, "transactionId": f"{self.run_id}-{meeting['key']}"})
                    join = (event.get("onlineMeeting") or {}).get("joinUrl")
                    entry.update(status="created", organizer=self.upn(meeting["organizer"]), eventId=event["id"],
                                 iCalUId=event.get("iCalUId"), subject=meeting["subject"],
                                 attendees=[self.upn(k) for k in meeting["attendees"]], start=start.isoformat(),
                                 joinUrl=join, threadId=meeting_thread(join), chatPosted=0, chatMessages=[])
                    self.manifest.save()
                tick()
                posted = entry.get("chatPosted", 0)
                tick(posted)
                for index, line in enumerate(lines):
                    if index < posted or entry.get("chatStatus"):
                        continue
                    if not entry.get("threadId"):
                        entry["chatStatus"] = "skipped: the meeting has no Teams chat"
                        break
                    try:
                        posted_line = self.post_message(self.graph(line["from"]), f"chats/{entry['threadId']}/messages",
                                                        self.html(line["text"]))
                        entry.setdefault("chatMessages", []).append({"id": posted_line.get("id"),
                                                                     "from": self.upn(line["from"])})
                    except Cancelled:
                        raise
                    except Stop as error:
                        entry["chatStatus"] = f"skipped: {error}"
                        self.log(f"Meeting chat for '{meeting['subject']}' skipped (best effort): {error}")
                        break
                    entry["chatPosted"] = index + 1
                    self.manifest.save()
                    tick()
                entry["status"] = "done"
                self.log(f"Meeting '{meeting['subject']}' on {entry.get('start')} by {entry.get('organizer')}.")

    # --- team and channel -----------------------------------------------------------------------
    def find_team_group(self, nickname):
        path = "groups?" + query(filter=f"mailNickname eq {odata_string(nickname)}", select="id")
        found = self.admin_graph.get(path).get("value") or []
        return found[0]["id"] if found else None

    def team_group(self, entry, team):
        """Create the team's Microsoft 365 group as the administrator, or find the one an earlier attempt created."""
        if entry.get("groupId"):
            return
        if entry.get("mailNickname"):
            entry["groupId"] = self.find_team_group(entry["mailNickname"])
        else:
            prefix = re.sub(r"[^a-z0-9]", "", team["team"].lower())[:30] or "demoteam"
            entry.update(mailNickname=prefix + secrets.token_hex(3), displayName=team["team"])
            self.manifest.save()
        if not entry.get("groupId"):
            try:
                group = self.admin_graph.post("groups", {
                    "displayName": team["team"], "description": team.get("description") or team["team"],
                    "mailNickname": entry["mailNickname"], "mailEnabled": True, "securityEnabled": False,
                    "groupTypes": ["Unified"], "visibility": "Private",
                    "resourceBehaviorOptions": ["WelcomeEmailDisabled"]})
            except GraphError as error:
                if 400 <= error.status < 500:
                    entry.pop("mailNickname", None)  # refused, so nothing was created
                raise
            entry.update(groupId=group["id"], createdAt=utc_now())
            self.log(f"Created the group for the team '{team['team']}'.")
        entry["status"] = "group-created"
        self.manifest.save()

    def team_members(self, entry, team):
        added = entry.setdefault("membersAdded", [])
        for key in [team["owner"], *team.get("members", [])]:
            if key not in added:
                add_to_group(self.admin_graph, entry["groupId"], self.people[key]["id"])
                added.append(key)
                self.manifest.save()
        if not entry.get("ownerAdded"):
            add_to_group(self.admin_graph, entry["groupId"], self.people[team["owner"]]["id"], "owners")
            entry["ownerAdded"] = True
            self.manifest.save()

    def prepare_team(self):
        """Create the team's group and membership early, so the team is ready by the time posts start."""
        team = self.doc.get("channel")
        if not team:
            return
        with self.work("team", 0) as (entry, _):
            self.team_group(entry, team)
            self.team_members(entry, team)

    def promote(self, group_id, owner_id, created_at):
        for _, last in pace.waits(TEAM_READY):
            try:
                owners = {owner.get("id") for owner in self.admin_graph.pages(f"groups/{group_id}/owners?$select=id")}
            except GraphError as error:
                if error.status != 404 or last:
                    raise
                continue
            if owner_id in owners:
                break
        created = datetime.fromisoformat(created_at) if created_at else None
        if created:
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age < 30:
                pace.sleep(30 - age)
        settings = {"memberSettings": {"allowCreateUpdateChannels": True},
                    "messagingSettings": {"allowUserEditMessages": True, "allowUserDeleteMessages": True},
                    "funSettings": {"allowGiphy": True, "giphyContentRating": "strict"}}
        for _, last in pace.waits(TEAM_PROMOTION):
            response = self.admin_graph.call("PUT", f"groups/{group_id}/team", json_body=settings)
            if response.ok or response.status == 409:
                break
            if response.status in (403, 404, 429, 500, 502, 503, 504) and not last:
                continue
            raise GraphError("PUT", f"groups/{group_id}/team", response)
        for _, last in pace.waits(TEAM_READY):
            response = self.admin_graph.call("GET", f"teams/{group_id}?$select=id")
            if response.ok:
                return
            if response.status in (403, 404, 500, 502, 503, 504) and not last:
                continue
            raise GraphError("GET", f"teams/{group_id}", response)

    def roster(self, key):
        """Wait long for Teams to add a member only until their first post in the team succeeds."""
        return CHAT_PROPAGATION if key in self._on_roster else ROSTER_PROPAGATION

    def channel_id(self, group_id, channel):
        if channel["name"].lower() == "general":
            return self.admin_graph.get(f"teams/{group_id}/primaryChannel?$select=id")["id"]
        for existing in self.admin_graph.pages(f"teams/{group_id}/channels?$select=id,displayName"):
            if str(existing.get("displayName", "")).lower() == channel["name"].lower():
                return existing["id"]
        return self.admin_graph.post(f"teams/{group_id}/channels", {
            "displayName": channel["name"], "description": channel.get("description") or "",
            "membershipType": "standard"})["id"]

    def seed_channel(self):
        team = self.doc.get("channel")
        if not team:
            return
        posts = [post for channel in team["channels"] for post in channel["posts"]]
        total = len(posts) + sum(len(post.get("replies") or []) for post in posts)
        if not self.manifest.item("team").get("groupId"):
            self._advance(total)
            return
        with self.work("team", total) as (entry, tick):
            group_id = entry["groupId"]
            self.team_members(entry, team)
            if not entry.get("team"):
                self.promote(group_id, self.people[team["owner"]]["id"], entry.get("createdAt"))
                entry["team"] = True
                self.manifest.save()
                self.log(f"The team '{team['team']}' is ready.")
            records = entry.setdefault("channels", {})
            for channel in team["channels"]:
                record = records.setdefault(channel["name"], {"posts": []})
                if not record.get("id"):
                    for _, last in pace.waits(TEAM_READY):
                        try:
                            record["id"] = self.channel_id(group_id, channel)
                            break
                        except GraphError as error:
                            if error.status not in (403, 404) or last:
                                raise
                    self.manifest.save()
                base = f"teams/{group_id}/channels/{record['id']}/messages"
                for index, post in enumerate(channel["posts"]):
                    self.check_cancel()
                    if index >= len(record["posts"]):
                        message = self.post_message(self.graph(post["from"]), base, self.html(post["text"]),
                                                    subject=post.get("subject"), schedule=self.roster(post["from"]))
                        self._on_roster.add(post["from"])
                        record["posts"].append({"messageId": message.get("id"), "from": self.upn(post["from"]),
                                                "replies": 0})
                        self.manifest.save()
                    tick()
                    done = record["posts"][index]
                    tick(done["replies"])
                    for number, reply in enumerate(post.get("replies") or []):
                        if number < done["replies"]:
                            continue
                        self.check_cancel()
                        self.post_message(self.graph(reply["from"]), f"{base}/{done['messageId']}/replies",
                                          self.html(reply["text"]), schedule=self.roster(reply["from"]))
                        self._on_roster.add(reply["from"])
                        done["replies"] = number + 1
                        self.manifest.save()
                        tick()
            if not entry.get("adminRemoved") and self.admin.user_id:
                try:
                    for relation in ("owners", "members"):
                        ids = {item.get("id") for item in
                               self.admin_graph.pages(f"groups/{group_id}/{relation}?$select=id")}
                        if self.admin.user_id in ids and (relation == "members" or len(ids) > 1):
                            self.admin_graph.delete(f"groups/{group_id}/{relation}/{self.admin.user_id}/$ref")
                    entry["adminRemoved"] = True
                except GraphError as error:
                    entry["adminRemovalError"] = str(error)
                    self.log(f"WARNING: the administrator is still on the team '{team['team']}': {error}")
            entry["status"] = "done"
            self.log(f"Channel posts in '{team['team']}': {len(posts)} post(s).")
