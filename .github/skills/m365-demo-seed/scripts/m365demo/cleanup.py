"""Delete what a seed run created, from its manifest: recorded identifiers only, never a broad search.

As the administrator: the team (its Microsoft 365 group, with the channels and posts), group chats
and meeting chats. As each demo user: their files, their mail (by conversation, in every
participant's mailbox), their meetings (the organizer deletes the event, attendees remove their
cancelled copies) and their own messages in one-on-one chats - the chat itself is kept, because
Teams has one chat per pair of people and it may hold their earlier conversation.

Best-effort sweeps then remove notifications that seeding and cleanup caused - meeting invitations
and cancellations, and file share notices - matched by the seeded subject or file name in the
recipients' most recent mail.

Re-seeding a demo overwrites its files in place, so a file that another not-yet-cleaned run of the
same demo also recorded is kept for that run, as are notices that would match its content.
"""

import contextlib
import hashlib
import json
import time

from .common import Stop
from . import pace
from .content import CREATED_KEYS, Cancelled
from .graph import Graph, GraphError, odata_string, query

ORDER = ("team", "chat", "meeting", "file", "mail")
SWEEP_PAGES = 3
CHAT_DELETE_GAP = 1.0


def kind_of(key):
    return key.split(":", 1)[0]


def exists(entry):
    """True when the manifest entry holds something that may still be in the tenant."""
    return not entry.get("deleted") and any(entry.get(key) for key in CREATED_KEYS)


def shared_with(other_manifests):
    """What other not-yet-cleaned runs of the demo still use: {("file", owner, item id): run id, ...}."""
    shared = {}
    for run_id, data in other_manifests:
        for key, entry in (data.get("items") or {}).items():
            if not exists(entry):
                continue
            kind = kind_of(key)
            if kind == "file" and entry.get("itemId"):
                shared[("file", entry.get("owner"), entry["itemId"])] = run_id
                shared.setdefault(("name", base_name(entry.get("name"))), run_id)
            elif kind == "meeting" and entry.get("subject"):
                shared.setdefault(("subject", normal(entry["subject"])), run_id)
    return shared


def normal(text):
    return " ".join(str(text or "").split()).lower()


def base_name(name):
    name = str(name or "")
    return normal(name.rsplit(".", 1)[0] if "." in name else name)


def plan(manifest_data, shared):
    """The deletion plan for a run: one row per item that may still exist, with its action."""
    rows = []
    for key in sorted((manifest_data.get("items") or {}), key=lambda k: (ORDER.index(kind_of(k))
                                                                           if kind_of(k) in ORDER else 9, k)):
        entry = manifest_data["items"][key]
        if not exists(entry):
            continue
        kind = kind_of(key)
        row = {"item": key, "kind": kind}
        if kind == "team":
            row.update(action="delete the team and its group (administrator)", name=entry.get("displayName"))
        elif kind == "chat":
            if entry.get("chatType") == "oneOnOne":
                row.update(action=f"delete {len(entry.get('messages') or [])} seeded message(s), each as its sender; "
                                  "the one-on-one chat itself is kept", members=entry.get("members"))
            else:
                row.update(action="delete the group chat (administrator)", members=entry.get("members"))
        elif kind == "meeting":
            row.update(action="delete the meeting (organizer; attendees get a cancellation), remove attendees' "
                              "copies and the meeting chat", subject=entry.get("subject"),
                       organizer=entry.get("organizer"))
        elif kind == "file":
            row.update(name=entry.get("name"), owner=entry.get("owner"))
            other = shared.get(("file", entry.get("owner"), entry.get("itemId")))
            if other:
                row.update(action="keep", keptFor=other)
            else:
                row["action"] = "delete from the owner's OneDrive (recycle bin)"
        elif kind == "mail":
            row.update(action="delete the thread from each participant's mailbox", participants=entry.get("participants"),
                       messages=len(entry.get("sent") or []) + (1 if entry.get("pending") else 0))
        rows.append(row)
    return rows


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class Cleaner:
    def __init__(self, manifest, people, admin, *, shared, log, advance, cancelled, graph_for):
        """people maps a UPN to {upn, id}; graph_for(upn) returns that person's Graph client or raises Stop."""
        self.manifest, self.people, self.admin = manifest, people, admin
        self.admin_graph = Graph(admin)
        self.shared, self.log, self._advance, self._cancelled = shared, log, advance, cancelled
        self.graph_for = graph_for
        self.failures, self.kept = [], []
        self.counts = {kind: {"deleted": 0, "kept": 0, "failed": 0} for kind in ORDER}
        self.swept = {"meetingNotices": 0, "shareNotices": 0}
        self._last_chat_delete = 0.0

    def check_cancel(self):
        if self._cancelled():
            raise Cancelled("cancelled", "The cleanup was cancelled.")

    @contextlib.contextmanager
    def work(self, key):
        entry = self.manifest.item(key)
        kind = kind_of(key)
        try:
            yield entry
            if entry.get("kept"):
                self.counts[kind]["kept"] += 1
            else:
                self.counts[kind]["deleted"] += 1
            entry.update(deleted=True, deletedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            entry.pop("deleteError", None)
        except Cancelled:
            raise
        except Stop as error:
            self.counts[kind]["failed"] += 1
            entry["deleteError"] = str(error)
            self.failures.append({"item": key, "error": str(error)})
            self.log(f"NOT DELETED {key}: {error}")
        finally:
            self.manifest.save()
            self._advance(1)

    def items(self):
        rows = plan(self.manifest.data, self.shared)
        return [row["item"] for row in rows]

    def run(self):
        keys = self.items()
        for key in keys:
            self.check_cancel()
            handler = getattr(self, f"delete_{kind_of(key)}", None)
            if handler is None:
                continue
            with self.work(key) as entry:
                handler(key, entry)
        self.sweep()

    # --- administrator ---------------------------------------------------------------------
    def delete_chat_thread(self, chat_id):
        """Delete a chat as the administrator, at most one per second (the service's limit per tenant)."""
        wait = CHAT_DELETE_GAP - (time.monotonic() - self._last_chat_delete)
        if wait > 0:
            pace.sleep(wait)
        try:
            self.admin_graph.delete(f"chats/{chat_id}")
        finally:
            self._last_chat_delete = time.monotonic()

    def delete_team(self, key, entry):
        group_id = entry.get("groupId")
        if not group_id and entry.get("mailNickname"):
            path = "groups?" + query(filter=f"mailNickname eq {odata_string(entry['mailNickname'])}", select="id")
            found = self.admin_graph.get(path).get("value") or []
            group_id = found[0]["id"] if found else None
        if group_id:
            self.admin_graph.delete(f"groups/{group_id}")
            self.log(f"Deleted the team '{entry.get('displayName')}' and its group.")

    # --- chats -----------------------------------------------------------------------------
    def delete_chat(self, key, entry):
        if entry.get("chatType") != "oneOnOne":
            self.delete_chat_thread(entry["chatId"])
            self.log(f"Deleted the group chat {key}.")
            return
        for message in entry.get("messages") or []:
            if message.get("deleted"):
                continue
            if not message.get("id"):
                raise Stop("unknown-message", f"A message in {key} has no recorded id, so it was not deleted.")
            sender = self.people.get(message["from"])
            if not sender:
                raise Stop("unknown-person", f"{message['from']} is not one of the demo's people.")
            path = f"users/{sender['id']}/chats/{entry['chatId']}/messages/{message['id']}/softDelete"
            response = self.graph_for(message["from"]).call("POST", path, json_body={})
            if not response.ok and response.status != 404:
                raise GraphError("POST", path, response)
            message["deleted"] = True
            self.manifest.save()
        self.log(f"Deleted the seeded messages of {key}; the one-on-one chat itself is kept.")

    # --- meetings --------------------------------------------------------------------------
    def delete_meeting(self, key, entry):
        if not entry.get("eventDeleted"):
            self.graph_for(entry["organizer"]).delete(f"me/events/{entry['eventId']}")
            entry["eventDeleted"] = True
            self.manifest.save()
        if entry.get("iCalUId"):
            path = "me/events?" + query(filter=f"iCalUId eq {odata_string(entry['iCalUId'])}", select="id")
            for attendee in entry.get("attendees") or []:
                graph = self.graph_for(attendee)
                for copy in list(graph.pages(path)):
                    graph.delete(f"me/events/{copy['id']}")
        if entry.get("threadId") and not entry.get("chatDeleted"):
            self.delete_chat_thread(entry["threadId"])
            entry["chatDeleted"] = True
        self.log(f"Deleted the meeting '{entry.get('subject')}'.")

    # --- files -----------------------------------------------------------------------------
    def delete_file(self, key, entry):
        other = self.shared.get(("file", entry.get("owner"), entry.get("itemId")))
        if other:
            entry["kept"] = f"also seeded by {other}, which has not been cleaned up"
            self.kept.append({"item": key, "name": entry.get("name"), "keptFor": other})
            return
        self.graph_for(entry["owner"]).delete(f"me/drive/items/{entry['itemId']}")
        self.log(f"Deleted the file {entry.get('name')} from {entry['owner']}'s OneDrive.")

    # --- mail ------------------------------------------------------------------------------
    def delete_mail(self, key, entry):
        conversations = {sent.get("conversationId") for sent in entry.get("sent") or [] if sent.get("conversationId")}
        internet_ids = {sent.get("internetMessageId") for sent in entry.get("sent") or []
                        if sent.get("internetMessageId") and not sent.get("conversationId")}
        cleared = entry.setdefault("mailboxesCleared", [])
        for upn in entry.get("participants") or []:
            if upn in cleared:
                continue
            graph = self.graph_for(upn)
            filters = [f"conversationId eq {odata_string(c)}" for c in sorted(conversations)]
            filters += [f"internetMessageId eq {odata_string(i)}" for i in sorted(internet_ids)]
            for condition in filters:
                for message in list(graph.pages("me/messages?" + query(filter=condition, select="id"))):
                    graph.delete(f"me/messages/{message['id']}")
            cleared.append(upn)
            self.manifest.save()
        pending = entry.get("pending")
        if pending:
            self.graph_for(pending["from"]).delete(f"me/messages/{pending['id']}")
        self.log(f"Deleted the mail thread {key} from {len(cleared)} mailbox(es).")

    # --- notifications ---------------------------------------------------------------------
    def recent(self, graph):
        path = "me/messages?$select=id,subject&$top=100&$orderby=receivedDateTime%20desc"
        count = 0
        link = path
        while link and count < SWEEP_PAGES:
            page = graph.get(link)
            count += 1
            yield from page.get("value") or []
            link = page.get("@odata.nextLink")

    def sweep(self):
        """Best effort: notices about the deleted meetings and files; failures are logged, not fatal."""
        subjects, names = {}, {}
        for key, entry in (self.manifest.data.get("items") or {}).items():
            if not entry.get("deleted") or entry.get("kept") or entry.get("swept"):
                continue
            kind = kind_of(key)
            if kind == "meeting" and entry.get("subject") and ("subject", normal(entry["subject"])) not in self.shared:
                for upn in [entry.get("organizer"), *(entry.get("attendees") or [])]:
                    subjects.setdefault(upn, set()).add(normal(entry["subject"]))
                entry["swept"] = True
            elif kind == "file" and entry.get("name") and ("name", base_name(entry["name"])) not in self.shared:
                for upn in entry.get("sharedWith") or []:
                    names.setdefault(upn, set()).add(base_name(entry["name"]))
                entry["swept"] = True
        for upn in sorted(set(subjects) | set(names)):
            if upn not in self.people:
                continue
            try:
                graph = self.graph_for(upn)
                for message in list(self.recent(graph)):
                    subject = normal(message.get("subject"))
                    meeting = any(subject == s or subject.endswith(": " + s) for s in subjects.get(upn, ()))
                    share = any(n and n in subject and ("shared" in subject or "invit" in subject)
                                for n in names.get(upn, ()))
                    if meeting or share:
                        graph.delete(f"me/messages/{message['id']}")
                        self.swept["meetingNotices" if meeting else "shareNotices"] += 1
            except Stop as error:
                self.log(f"WARNING: notifications in {upn}'s mailbox were not swept: {error}")
        self.manifest.save()
