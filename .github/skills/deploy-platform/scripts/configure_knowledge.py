#!/usr/bin/env python3
"""Set verified DEV item IDs in the local tfvars without deploying.

Fills the starter's null/empty knowledge settings. A registration this helper
wrote earlier (one lakehouse and one ontology entry in its own format) is
replaced only with --replace: without it the helper exits with
REPLACEMENT_REQUIRED and describes the replacement, so the runner can ask for
approval first. Any other registration stays a manual review.
"""

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID

REPLACEMENT_REQUIRED = 3
GUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
KEY = r"[a-z](?:[a-z0-9]|-(?!-)){0,30}[a-z0-9]"
# The exact single-entry block configure() writes, with either line ending.
WRITTEN_BLOCK = (rf"\{{\r?\n  (?P<key>{KEY}) = \{{\r?\n    ontology_id = \"(?P<ontology>{GUID})\"\r?\n"
                 r'    description = "(?:[^"\\\r\n]|\\.)*"\r?\n  \}\r?\n\}')


class ReplacementRequired(ValueError):
    pass


def physical_id(value, logical_ids):
    if not re.fullmatch(GUID, value):
        raise ValueError("Supply physical DEV item GUIDs in hyphenated UUID format.")
    item_id = UUID(value)
    if item_id.int == 0 or str(item_id) in logical_ids:
        raise ValueError("A zero GUID or Git logical ID cannot be used as a physical DEV item ID.")
    return str(item_id)


def registration_name(value):
    if not re.fullmatch(KEY, value):
        raise ValueError("The registration must be 2-32 lowercase letters, digits or single hyphens, "
                         "starting with a letter and ending with a letter or digit.")
    return value


def description_text(value):
    if not value.strip() or value != value.strip() or any(character in value for character in "\r\n\0"):
        raise ValueError("The description must be one nonempty line without surrounding whitespace.")
    return value


def configure(text, lakehouse_id, ontology_id, registration, description, replace=False):
    """The updated tfvars text and a description of any earlier registration it replaces."""
    newline = "\r\n" if "\r\n" in text else "\n"
    block = newline.join((
        "{",
        f"  {registration} = {{",
        f'    ontology_id = "{ontology_id}"',
        f"    description = {json.dumps(description)}",
        "  }",
        "}",
    ))
    settings = (
        ("knowledge_lakehouse_id", rf'null|{re.escape(json.dumps(lakehouse_id))}', rf'"{GUID}"',
         json.dumps(lakehouse_id)),
        ("knowledge_ontologies", rf"\{{\}}|{re.escape(block)}", WRITTEN_BLOCK, block),
    )
    changes, replaced = [], []
    for name, starter, written, replacement in settings:
        if len(re.findall(rf"(?m)^{name}[ \t]*=", text)) != 1:
            raise ValueError(f"Expected exactly one top-level {name} assignment.")
        match = re.search(
            rf"(?m)^{name}[ \t]*=[ \t]*(?P<value>{starter}|{written})[ \t]*(?:#[^\r\n]*)?\r?$",
            text,
        )
        if not match:
            raise ValueError(
                f"{name} is already customized or points to different items. "
                "Review it without overwriting existing registrations."
            )
        if not re.fullmatch(starter, match["value"]):
            earlier = re.fullmatch(written, match["value"])
            if name == "knowledge_lakehouse_id":
                replaced.append(f"knowledge_lakehouse_id {match['value']} with {replacement}")
            else:
                replaced.append(f"knowledge_ontologies.{earlier['key']} (ontology {earlier['ontology']}) "
                                f"with {registration} (ontology {ontology_id})")
        changes.append((match.start("value"), match.end("value"), replacement))
    if replaced and not replace:
        raise ReplacementRequired("Replace the earlier knowledge registration in infra/terraform.tfvars: "
                                  + "; ".join(replaced) + ".")
    for start, end, replacement in sorted(changes, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, replaced


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lakehouse-id", required=True,
                        help="the scenario lakehouse whose Files the knowledge sources index")
    parser.add_argument("--ontology-id", required=True, help="the scenario ontology")
    parser.add_argument("--registration", required=True, help="the knowledge_ontologies key, such as scenario")
    parser.add_argument("--description", required=True, help="the questions the ontology answers")
    parser.add_argument("--replace", action="store_true",
                        help="replace a registration this helper wrote earlier (approve it first)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = Path(root) if root else Path(__file__).resolve().parents[4]
    infra = root / "infra"
    target = infra / "terraform.tfvars"
    temporary_path = None

    try:
        if infra.is_symlink() or target.is_symlink():
            raise ValueError("Refusing to update symlinked configuration.")
        if any(
            path.name == "terraform.tfvars.json"
            or path.name.endswith((".auto.tfvars", ".auto.tfvars.json"))
            for path in infra.iterdir()
        ):
            raise ValueError("Additional auto-loaded tfvars exist; review precedence before editing.")
        logical_ids = {
            str(UUID(json.loads(path.read_text(encoding="utf-8"))["config"]["logicalId"]))
            for path in (root / "workspace").glob("*/.platform")
        }
        if not logical_ids:
            raise ValueError("Workspace logical identities are missing.")
        lakehouse_id = physical_id(args.lakehouse_id, logical_ids)
        ontology_id = physical_id(args.ontology_id, logical_ids)
        if lakehouse_id == ontology_id:
            raise ValueError("The lakehouse and ontology must have different physical IDs.")
        registration = registration_name(args.registration)
        description = description_text(args.description)
        original = target.read_bytes()
        text, replaced = configure(original.decode("utf-8"), lakehouse_id, ontology_id, registration, description,
                                   args.replace)
        updated = text.encode("utf-8")
        if updated == original:
            print("The knowledge settings already match; no files changed.")
            return
        if args.dry_run:
            print("Would " + ("replace " + "; ".join(replaced) if replaced else "fill only the starter knowledge settings")
                  + "; no files changed.")
            return

        with tempfile.NamedTemporaryFile(
            mode="wb", dir=infra, prefix="terraform.tfvars.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
        if target.is_symlink() or target.read_bytes() != original:
            raise ValueError("The configuration changed during preparation; review before retrying.")
        os.replace(temporary_path, target)
        temporary_path = None
    except ReplacementRequired as required:
        # On stdout: the runner shows it as the approval it asks for.
        print(required)
        raise SystemExit(REPLACEMENT_REQUIRED) from None
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Knowledge configuration failed: {error}\n")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    print(("Replaced " + "; ".join(replaced) if replaced else "Updated only the starter knowledge settings")
          + " with owner-only permissions.")
    print("No cloud changes made. Review a new Terraform knowledge plan before applying.")


if __name__ == "__main__":
    main()
