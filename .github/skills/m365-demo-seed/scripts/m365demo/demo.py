"""Demos in the workspace: demos/<name>/demo.json, the authored story and content (no secrets).

references/content.md documents the format; validate() is its executable form and reports
every problem with its path, for example `mail[0].messages[1].from: unknown person 'bob'`.
"""

import hashlib
import re

from .common import Blocked, Usage
from . import text, workspace

UPN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KEY = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
FILE_TYPES = ("docx", "xlsx", "pptx")
BAD_FILE_CHARS = re.compile(r'["*:<>?\\/|#%]')
BAD_CHANNEL_CHARS = re.compile(r"[~#%&*{}+/\\:<>?|'\",]")
BAD_SHEET_CHARS = re.compile(r"[:\\/?*\[\]]")
PEOPLE = (2, 8)
LIMITS = {"files": 20, "mail": 30, "chats": 20, "meetings": 20, "channels": 5, "prompts": 20}
KEY_RULE = "must be lowercase letters, digits and '-', starting with a letter"


def file(name):
    return workspace.demo_dir(name) / "demo.json"


def names():
    root = workspace.demos_dir()
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir()
                  if entry.is_dir() and workspace.NAME.match(entry.name) and (entry / "demo.json").is_file())


def latest():
    """The most recently changed demo's name, or None."""
    found = names()
    return max(found, key=lambda name: file(name).stat().st_mtime) if found else None


def load(name):
    doc = workspace.read_json(file(name))
    if doc is None:
        raise Usage("no-demo", f"There is no demo named {name!r}.", demos=names())
    if not isinstance(doc, dict):
        raise Blocked("invalid-demo", f"{file(name)} must hold a JSON object.")
    return doc


def sha256(name):
    return hashlib.sha256(file(name).read_bytes()).hexdigest()


def people(doc):
    """The demo users as [{key, upn, name, role}], UPNs lowercased."""
    found = []
    for person in doc.get("people") or []:
        if isinstance(person, dict) and isinstance(person.get("upn"), str) and UPN.match(person["upn"].strip()):
            found.append({**person, "upn": person["upn"].strip().lower()})
    return found


def admin_upn(doc):
    value = doc.get("adminUpn")
    return value.strip().lower() if isinstance(value, str) and UPN.match(value.strip()) else None


def by_key(doc):
    return {person["key"]: person for person in people(doc) if isinstance(person.get("key"), str)}


class Checker:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, path, message):
        self.errors.append(f"{path}: {message}")

    def warn(self, path, message):
        self.warnings.append(f"{path}: {message}")

    def mapping(self, value, path):
        if not isinstance(value, dict):
            self.error(path, "must be an object")
            return {}
        return value

    def items(self, parent, field, path, *, low=0, high=None):
        value = parent.get(field)
        where = f"{path}.{field}" if path else field
        if value is None:
            if low:
                self.error(where, f"is required (at least {low})")
            return []
        if not isinstance(value, list):
            self.error(where, "must be a list")
            return []
        if len(value) < low:
            self.error(where, f"needs at least {low}")
        if high is not None and len(value) > high:
            self.error(where, f"allows at most {high}")
        return value

    def string(self, parent, field, path, *, required=True, high=200, pattern=None, describe=None):
        value = parent.get(field)
        where = f"{path}.{field}" if path else field
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                self.error(where, "is required")
            return None
        if not isinstance(value, str):
            self.error(where, "must be text")
            return None
        if len(value) > high:
            self.error(where, f"is longer than {high} characters")
        if pattern is not None and not pattern.match(value):
            self.error(where, describe or "has an invalid format")
        return value

    def body(self, parent, field, path, files, *, required=True, high=20000):
        value = self.string(parent, field, path, required=required, high=high)
        if value:
            where = f"{path}.{field}"
            for key in text.references(value):
                if key not in files:
                    self.error(where, f"links to an unknown file {{{{file:{key}}}}}")
            for placeholder in text.unknown_placeholders(value):
                self.error(where, f"has an unknown placeholder {placeholder}; only {{{{file:KEY}}}} is supported")
        return value

    def person(self, parent, field, path, known, *, required=True):
        value = parent.get(field)
        where = f"{path}.{field}"
        if value is None:
            if required:
                self.error(where, "is required")
            return None
        if not isinstance(value, str) or value not in known:
            self.error(where, f"unknown person {value!r}")
            return None
        return value

    def persons(self, parent, field, path, known, *, low=0, high=PEOPLE[1], exclude=()):
        chosen = []
        for index, value in enumerate(self.items(parent, field, path, low=low, high=high)):
            where = f"{path}.{field}[{index}]"
            if not isinstance(value, str) or value not in known:
                self.error(where, f"unknown person {value!r}")
            elif value in chosen:
                self.error(where, f"lists {value!r} twice")
            elif value in exclude:
                self.error(where, f"{value!r} is already the sender or organizer")
            else:
                chosen.append(value)
        return chosen

    def unique_key(self, item, path, seen, label):
        key = self.string(item, "key", path, high=31, pattern=KEY, describe=KEY_RULE)
        if key:
            if key in seen:
                self.error(f"{path}.key", f"repeats the {label} key {key!r}")
            seen.add(key)
        return key


def _people(check, doc):
    known = {}
    upns = set()
    admin = check.string(doc, "adminUpn", "", pattern=UPN, describe="must be a user principal name")
    admin = admin if admin and UPN.match(admin) else None
    for index, person in enumerate(check.items(doc, "people", "", low=PEOPLE[0], high=PEOPLE[1])):
        path = f"people[{index}]"
        person = check.mapping(person, path)
        key = check.string(person, "key", path, high=31, pattern=KEY, describe=KEY_RULE)
        upn = check.string(person, "upn", path, pattern=UPN, describe="must be a user principal name")
        upn = upn if upn and UPN.match(upn) else None
        check.string(person, "name", path, high=80)
        check.string(person, "role", path, required=False, high=80)
        if key:
            if key in known:
                check.error(f"{path}.key", f"repeats the person key {key!r}")
            known[key] = person
        if upn:
            if upn.lower() in upns:
                check.error(f"{path}.upn", f"repeats {upn}")
            upns.add(upn.lower())
            if admin and upn.lower() == admin.lower():
                check.error(f"{path}.upn", "is the administrator; demo users must be other accounts")
    domains = {upn.split("@", 1)[1] for upn in upns}
    if admin:
        domains.add(admin.lower().split("@", 1)[1])
    if len(domains) > 1:
        check.warn("people", f"spans several domains ({', '.join(sorted(domains))}); all must be in the deployment tenant")
    return known


def _docx(check, item, path):
    check.string(item, "title", path, required=False, high=200)
    total = 0
    for number, section in enumerate(check.items(item, "sections", path, low=1, high=30)):
        where = f"{path}.sections[{number}]"
        section = check.mapping(section, where)
        check.string(section, "heading", where, required=False, high=200)
        for field in ("paragraphs", "bullets"):
            for line, value in enumerate(check.items(section, field, where, high=40)):
                if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                    check.error(f"{where}.{field}[{line}]", "must be text of at most 4000 characters")
                total += 1
    if not total:
        check.error(f"{path}.sections", "needs at least one paragraph or bullet")


def _xlsx(check, item, path):
    sheet_names = set()
    for number, sheet in enumerate(check.items(item, "sheets", path, low=1, high=10)):
        where = f"{path}.sheets[{number}]"
        sheet = check.mapping(sheet, where)
        sheet_name = check.string(sheet, "name", where, high=31)
        if sheet_name:
            if BAD_SHEET_CHARS.search(sheet_name) or sheet_name.startswith("'") or sheet_name.endswith("'"):
                check.error(f"{where}.name", "cannot contain : \\ / ? * [ ] or start or end with '")
            if sheet_name.lower() in sheet_names:
                check.error(f"{where}.name", f"repeats the sheet name {sheet_name!r}")
            sheet_names.add(sheet_name.lower())
        for row_number, row in enumerate(check.items(sheet, "rows", where, low=1, high=500)):
            if not isinstance(row, list) or len(row) > 30:
                check.error(f"{where}.rows[{row_number}]", "must be a list of at most 30 cells")
                continue
            for cell in row:
                bad_type = cell is not None and not isinstance(cell, (str, int, float, bool))
                if bad_type or (isinstance(cell, str) and len(cell) > 1000):
                    check.error(f"{where}.rows[{row_number}]",
                                "cells must be text (up to 1000 characters), numbers, true/false or null")
                    break


def _pptx(check, item, path):
    for number, slide in enumerate(check.items(item, "slides", path, low=1, high=30)):
        where = f"{path}.slides[{number}]"
        slide = check.mapping(slide, where)
        check.string(slide, "title", where, high=120)
        for line, value in enumerate(check.items(slide, "bullets", where, high=8)):
            if not isinstance(value, str) or not value.strip() or len(value) > 300:
                check.error(f"{where}.bullets[{line}]", "must be text of at most 300 characters")


def _files(check, doc, known):
    keys = set()
    for index, item in enumerate(check.items(doc, "files", "", high=LIMITS["files"])):
        path = f"files[{index}]"
        item = check.mapping(item, path)
        check.unique_key(item, path, keys, "file")
        kind = item.get("type")
        if kind not in FILE_TYPES:
            check.error(f"{path}.type", f"must be one of {', '.join(FILE_TYPES)}")
        name = check.string(item, "name", path, high=120)
        if name:
            if BAD_FILE_CHARS.search(name) or name.strip() != name or name.endswith("."):
                check.error(f"{path}.name", 'cannot contain " * : < > ? / \\ | # % or start or end with a space or dot')
            if kind in FILE_TYPES and not name.lower().endswith("." + kind):
                check.error(f"{path}.name", f"must end with .{kind}")
        folder = check.string(item, "folder", path, required=False, high=120)
        if folder:
            segments = folder.split("/")
            if len(segments) > 3 or any(not s.strip() or s in (".", "..") or BAD_FILE_CHARS.search(s) for s in segments):
                check.error(f"{path}.folder", "must be up to three folder names separated by '/'")
        owner = check.person(item, "owner", path, known)
        check.persons(item, "sharedWith", path, known, exclude=(owner,) if owner else ())
        {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}.get(kind, lambda *_: None)(check, item, path)
    return keys


def _mail(check, doc, known, files):
    keys = set()
    for index, thread in enumerate(check.items(doc, "mail", "", high=LIMITS["mail"])):
        path = f"mail[{index}]"
        thread = check.mapping(thread, path)
        check.unique_key(thread, path, keys, "mail thread")
        check.string(thread, "subject", path, high=200)
        participants = set()
        for number, message in enumerate(check.items(thread, "messages", path, low=1, high=10)):
            where = f"{path}.messages[{number}]"
            message = check.mapping(message, where)
            sender = check.person(message, "from", where, known)
            check.body(message, "body", where, files)
            if number == 0:
                to = check.persons(message, "to", where, known, low=1, exclude=(sender,) if sender else ())
                cc = check.persons(message, "cc", where, known, exclude=tuple(to) + ((sender,) if sender else ()))
                participants.update(to + cc + ([sender] if sender else []))
            else:
                for field in ("to", "cc"):
                    if message.get(field) is not None:
                        check.error(f"{where}.{field}", "replies go to everyone on the thread (reply all); remove it")
                if sender and sender not in participants:
                    check.error(f"{where}.from", f"{sender!r} is not on this thread, so cannot reply to it")


def _chats(check, doc, known, files):
    keys = set()
    pairs = {}
    for index, chat in enumerate(check.items(doc, "chats", "", high=LIMITS["chats"])):
        path = f"chats[{index}]"
        chat = check.mapping(chat, path)
        check.unique_key(chat, path, keys, "chat")
        members = check.persons(chat, "members", path, known, low=2)
        topic = check.string(chat, "topic", path, required=len(members) > 2, high=200)
        if topic and len(members) == 2:
            check.warn(f"{path}.topic", "a one-on-one chat has no topic; it is ignored")
        if len(members) == 2:
            pair = frozenset(members)
            if pair in pairs:
                check.error(f"{path}.members", f"repeats the one-on-one chat of {pairs[pair]}; Teams keeps one chat "
                            "per pair, so merge their messages")
            pairs[pair] = path
        for number, message in enumerate(check.items(chat, "messages", path, low=1, high=50)):
            where = f"{path}.messages[{number}]"
            message = check.mapping(message, where)
            sender = check.person(message, "from", where, known)
            if sender and members and sender not in members:
                check.error(f"{where}.from", f"{sender!r} is not a member of this chat")
            check.body(message, "text", where, files, high=4000)


def _channel(check, doc, known, files):
    team = doc.get("channel")
    if team is None:
        return
    team = check.mapping(team, "channel")
    check.string(team, "team", "channel", high=100)
    check.string(team, "description", "channel", required=False, high=1000)
    owner = check.person(team, "owner", "channel", known)
    roster = set(check.persons(team, "members", "channel", known, exclude=(owner,) if owner else ()))
    if owner:
        roster.add(owner)
    names_seen = set()
    for index, channel in enumerate(check.items(team, "channels", "channel", low=1, high=LIMITS["channels"])):
        path = f"channel.channels[{index}]"
        channel = check.mapping(channel, path)
        name = check.string(channel, "name", path, high=50)
        if name:
            if BAD_CHANNEL_CHARS.search(name) or name.startswith(("_", ".")) or name.endswith("."):
                check.error(f"{path}.name", "cannot contain ~ # % & * { } + / \\ : < > ? | ' \" , or start with _ or .")
            if name.lower() in names_seen:
                check.error(f"{path}.name", f"repeats the channel {name!r}")
            names_seen.add(name.lower())
        check.string(channel, "description", path, required=False, high=1000)
        for number, post in enumerate(check.items(channel, "posts", path, low=1, high=30)):
            where = f"{path}.posts[{number}]"
            post = check.mapping(post, where)
            sender = check.person(post, "from", where, known)
            if sender and roster and sender not in roster:
                check.error(f"{where}.from", f"{sender!r} is not on the team (owner or members)")
            check.string(post, "subject", where, required=False, high=200)
            check.body(post, "text", where, files, high=4000)
            for reply_number, reply in enumerate(check.items(post, "replies", where, high=20)):
                at = f"{where}.replies[{reply_number}]"
                reply = check.mapping(reply, at)
                replier = check.person(reply, "from", at, known)
                if replier and roster and replier not in roster:
                    check.error(f"{at}.from", f"{replier!r} is not on the team (owner or members)")
                check.body(reply, "text", at, files, high=4000)


def _meetings(check, doc, known, files):
    keys = set()
    for index, meeting in enumerate(check.items(doc, "meetings", "", high=LIMITS["meetings"])):
        path = f"meetings[{index}]"
        meeting = check.mapping(meeting, path)
        check.unique_key(meeting, path, keys, "meeting")
        check.string(meeting, "subject", path, high=200)
        organizer = check.person(meeting, "organizer", path, known)
        attendees = check.persons(meeting, "attendees", path, known, low=1, exclude=(organizer,) if organizer else ())
        day = meeting.get("day")
        if not isinstance(day, int) or isinstance(day, bool) or not -20 <= day <= 20:
            check.error(f"{path}.day", "must be a whole number of business days from the seed day, -20 to 20")
        check.string(meeting, "start", path, high=5, pattern=CLOCK, describe="must be a 24-hour time like 09:30")
        minutes = meeting.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 15 <= minutes <= 240:
            check.error(f"{path}.minutes", "must be 15 to 240")
        check.body(meeting, "agenda", path, files, high=4000)
        allowed = set(attendees) | ({organizer} if organizer else set())
        for number, message in enumerate(check.items(meeting, "chat", path, high=20)):
            where = f"{path}.chat[{number}]"
            message = check.mapping(message, where)
            sender = check.person(message, "from", where, known)
            if sender and sender not in allowed:
                check.error(f"{where}.from", f"{sender!r} is not invited to this meeting")
            check.body(message, "text", where, files, high=4000)


def _guide(check, doc, known):
    guide = doc.get("guide")
    if guide is None:
        check.warn("guide", "has no Work IQ prompts; the presenter guide and the Work IQ check need them")
        return
    guide = check.mapping(guide, "guide")
    for index, prompt in enumerate(check.items(guide, "prompts", "guide", low=1, high=LIMITS["prompts"])):
        path = f"guide.prompts[{index}]"
        prompt = check.mapping(prompt, path)
        check.string(prompt, "surface", path, high=60)
        check.person(prompt, "as", path, known)
        check.string(prompt, "prompt", path, high=1000)
        for number, value in enumerate(check.items(prompt, "expect", path, high=10)):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                check.error(f"{path}.expect[{number}]", "must be text of at most 200 characters")


def validate(doc):
    """(errors, warnings) for a demo document."""
    if not isinstance(doc, dict):
        return ["demo: must hold a JSON object"], []
    check = Checker()
    if doc.get("version") != 1:
        check.error("version", "must be 1")
    check.string(doc, "title", "", high=120)
    check.string(doc, "summary", "", high=2000)
    check.string(doc, "timezone", "", required=False, high=64)
    known = _people(check, doc)
    files = _files(check, doc, known)
    _mail(check, doc, known, files)
    _chats(check, doc, known, files)
    _channel(check, doc, known, files)
    _meetings(check, doc, known, files)
    _guide(check, doc, known)
    if not any(doc.get(section) for section in ("files", "mail", "chats", "channel", "meetings")):
        check.error("demo", "has no content: add files, mail, chats, a channel or meetings")
    return check.errors, check.warnings


def counts(doc):
    channel = doc.get("channel") if isinstance(doc.get("channel"), dict) else {}
    channels = [item for item in channel.get("channels") or [] if isinstance(item, dict)]
    posts = [post for item in channels for post in item.get("posts") or [] if isinstance(post, dict)]

    def total(section, field):
        return sum(len(item.get(field) or []) for item in doc.get(section) or [] if isinstance(item, dict))

    return {"people": len(doc.get("people") or []), "files": len(doc.get("files") or []),
            "mailThreads": len(doc.get("mail") or []), "mailMessages": total("mail", "messages"),
            "chats": len(doc.get("chats") or []), "chatMessages": total("chats", "messages"),
            "channels": len(channels), "channelPosts": len(posts),
            "channelReplies": sum(len(post.get("replies") or []) for post in posts),
            "meetings": len(doc.get("meetings") or []), "meetingChatMessages": total("meetings", "chat"),
            "prompts": len(((doc.get("guide") or {}) if isinstance(doc.get("guide"), dict) else {}).get("prompts") or [])}


def report(name):
    """Validation result for a demo, with its hash and content counts."""
    doc = load(name)
    errors, warnings = validate(doc)
    return {"demo": name, "file": str(file(name)), "sha256": sha256(name), "valid": not errors,
            "errors": errors, "warnings": warnings, "counts": counts(doc)}


def load_valid(name):
    doc = load(name)
    errors, _ = validate(doc)
    if errors:
        raise Usage("invalid-demo", f"The demo {name!r} has {len(errors)} problem(s); fix them and validate again.",
                    errors=errors[:50])
    return doc


def listing():
    rows = []
    for name in names():
        try:
            doc = load(name)
            errors, _ = validate(doc)
            rows.append({"demo": name, "title": doc.get("title"), "valid": not errors, "counts": counts(doc),
                         "file": str(file(name))})
        except Blocked as stop:
            rows.append({"demo": name, "valid": False, "error": stop.message})
    return rows
