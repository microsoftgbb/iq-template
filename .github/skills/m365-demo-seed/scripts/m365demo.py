#!/usr/bin/env python3
"""m365-demo-seed: seed fictional Microsoft 365 content into this deployment's demo tenant.

Run from the repository root with Python 3.10 or newer (standard library only):
    python3 .github/skills/m365-demo-seed/scripts/m365demo.py status
Inspired by DemoIQ (https://aka.ms/demoiq).
"""

import json
from pathlib import Path
import sys

if sys.version_info < (3, 10):
    print(json.dumps({"ok": False, "reason": "python-too-old",
                      "message": f"Python 3.10 or newer is required; this is {sys.version.split()[0]}."}))
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m365demo.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
