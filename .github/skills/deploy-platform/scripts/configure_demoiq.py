#!/usr/bin/env python3
"""Turn on optional DemoIQ seeding (demoiq_enabled = true) in the local tfvars without deploying."""

import argparse
import os
from pathlib import Path
import re
import tempfile

SETTING = "demoiq_enabled"


def configure(text):
    """Return the tfvars text with exactly one `demoiq_enabled = true`, preserving everything else."""
    newline = "\r\n" if "\r\n" in text else "\n"
    assignments = re.findall(rf"(?m)^{SETTING}[ \t]*=", text)
    if len(assignments) > 1:
        raise ValueError(f"Expected at most one top-level {SETTING} assignment.")
    if not assignments:
        separator = newline if text and not text.endswith("\n") else ""
        return f"{text}{separator}{SETTING} = true{newline}"
    match = re.search(rf"(?m)^{SETTING}[ \t]*=[ \t]*(?P<value>true|false)[ \t]*(?:#[^\r\n]*)?\r?$", text)
    if not match:
        raise ValueError(f"{SETTING} has an unexpected value; review it without overwriting.")
    return text[:match.start("value")] + "true" + text[match.end("value"):]


def enable(infra, dry_run=False):
    """Update infra/terraform.tfvars in place, owner-only; return what happened."""
    infra = Path(infra)
    target = infra / "terraform.tfvars"
    if infra.is_symlink() or target.is_symlink():
        raise ValueError("Refusing to update symlinked configuration.")
    if any(path.name == "terraform.tfvars.json" or path.name.endswith((".auto.tfvars", ".auto.tfvars.json"))
           for path in infra.iterdir()):
        raise ValueError("Additional auto-loaded tfvars exist; review precedence before editing.")
    original = target.read_bytes()
    updated = configure(original.decode("utf-8")).encode("utf-8")
    if updated == original:
        return "unchanged"
    if dry_run:
        return "would enable"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=infra, prefix="terraform.tfvars.", suffix=".tmp",
                                         delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
        if target.is_symlink() or target.read_bytes() != original:
            raise ValueError("The configuration changed during preparation; review before retrying.")
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return "enabled"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    infra = Path(__file__).resolve().parents[4] / "infra"
    try:
        result = enable(infra, args.dry_run)
    except (OSError, ValueError, UnicodeDecodeError) as error:
        parser.exit(1, f"DemoIQ configuration failed: {error}\n")
    messages = {
        "unchanged": "DemoIQ is already enabled; no files changed.",
        "would enable": f"Would set {SETTING} = true; no files changed.",
        "enabled": f"Set {SETTING} = true in infra/terraform.tfvars with owner-only permissions.",
    }
    print(messages[result])
    if result == "enabled":
        print("No cloud changes made. Review the DemoIQ Terraform plan before applying.")


if __name__ == "__main__":
    main()
