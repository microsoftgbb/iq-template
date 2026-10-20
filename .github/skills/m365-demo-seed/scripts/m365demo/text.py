"""Authored text to HTML for mail, Teams and meetings.

Text is plain: blank lines separate paragraphs, lines starting with "- " form a bullet list,
**double asterisks** make bold, and {{file:KEY}} becomes a link to that seeded file.
"""

import html
import re

FILE_LINK = re.compile(r"\{\{file:([a-z][a-z0-9-]{0,30})\}\}")
ANY_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")
BOLD = re.compile(r"\*\*(.+?)\*\*")


def references(text):
    return FILE_LINK.findall(text or "")


def unknown_placeholders(text):
    return [match for match in ANY_PLACEHOLDER.findall(text or "") if not FILE_LINK.fullmatch(match)]


def _inline(text, links):
    parts, position = [], 0
    for match in FILE_LINK.finditer(text):
        parts.append(html.escape(text[position:match.start()], quote=False))
        name, url = links.get(match.group(1), (match.group(1), None))
        parts.append(f'<a href="{html.escape(url)}">{html.escape(name)}</a>' if url else html.escape(name))
        position = match.end()
    parts.append(html.escape(text[position:], quote=False))
    return BOLD.sub(r"<b>\1</b>", "".join(parts))


def to_html(text, links=None):
    """links maps a file key to (file name, web URL); unseeded files render as their name."""
    links = links or {}
    blocks = [block for block in re.split(r"\n\s*\n", (text or "").strip().replace("\r\n", "\n")) if block.strip()]
    rendered = []
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if all(line.lstrip().startswith(("- ", "* ")) for line in lines):
            rendered.append("<ul>" + "".join(f"<li>{_inline(line.lstrip()[2:], links)}</li>" for line in lines) + "</ul>")
        else:
            rendered.append("<p>" + "<br>".join(_inline(line, links) for line in lines) + "</p>")
    return "".join(rendered)


def to_plain(text, names=None):
    """The text with each {{file:KEY}} replaced by the file's name."""
    names = names or {}
    return FILE_LINK.sub(lambda match: names.get(match.group(1), match.group(1)), text or "")
