"""Shared paths and the notebook runner for the offline Fabric notebook tests.

Run them with `python3 -B -m unittest discover -s .github/skills/fabric-workspace/tests`. Each test executes a
workspace/ notebook cell by cell, as Fabric runs it, against in-memory stand-ins for the modules it imports;
nothing calls Fabric, Spark or OneLake.
"""

import contextlib
import io
import re
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
WORKSPACE = ROOT / "workspace"
WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
LAKEHOUSE_ID = "22222222-2222-4222-8222-222222222222"
OTHER_ID = "33333333-3333-4333-8333-333333333333"


def cells(name):
    """The code cells of workspace/<name>.Notebook in order, its parameters cell first."""
    source = (WORKSPACE / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
    return [chunk.split("# METADATA ********************")[0]
            for chunk in re.split(r"(?m)^# (?:PARAMETERS )?CELL \*+$", source)[1:]]


def run(name, modules, parameters=None, count=None):
    """Run the first `count` cells (all by default) with `modules` importable; return (namespace, printed output).

    `parameters` override the parameters cell's defaults, as Fabric injects a run's parameters after that cell.
    """
    namespace, output = {"__name__": name}, io.StringIO()
    with mock.patch.dict(sys.modules, modules), mock.patch("time.sleep"), contextlib.redirect_stdout(output):
        for index, cell in enumerate(cells(name)[:count]):
            exec(compile(cell, f"{name} cell {index}", "exec"), namespace)
            if index == 0:
                namespace.update(parameters or {})
    return namespace, output.getvalue()
