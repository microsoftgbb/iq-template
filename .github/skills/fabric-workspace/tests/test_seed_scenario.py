"""SeedScenario, run locally against in-memory stand-ins for Spark, Delta and OneLake.

The notebook's cells run with stand-ins for notebookutils, pyspark.sql and delta.tables; nothing calls Fabric or
Spark. The stand-ins implement only the calls the notebook makes, with the semantics it relies on: errorifexists
writes, Delta's insert-only merge on the key, and notebookutils.fs files that are never overwritten and need their
folder to exist.
"""

import copy
import re
import types
import unittest

from _support import LAKEHOUSE_ID, OTHER_ID, WORKSPACE_ID, run

LAKEHOUSE_PATH = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LAKEHOUSE_ID}"
TABLES = f"{LAKEHOUSE_PATH}/Tables/dbo"
FILES = f"{LAKEHOUSE_PATH}/Files"
# The default knowledge_folders entry, documents, indexes Files/documents.
STARTER = f"{FILES}/documents/starter.txt"
LEGACY = f"{FILES}/starter.txt"
PUMP_B = ("demo-asset-002", "demo-site-001", "Pump B", "Maintenance")


class Schema:
    """A Spark schema from the notebook's DDL: `name STRING, ...`."""

    def __init__(self, ddl):
        self.ddl, self.names = ddl, re.findall(r"(\w+) STRING", ddl)
        assert ", ".join(f"{name} STRING" for name in self.names) == ddl, ddl

    def simpleString(self):
        return "struct<" + ",".join(f"{name}:string" for name in self.names) + ">"


class Frame:
    def __init__(self, lake, schema, rows):
        self.lake, self.schema, self.rows = lake, schema, rows

    @property
    def write(self):
        return Writer(self)

    def alias(self, name):
        assert name == "source"
        return self


class Writer:
    def __init__(self, frame):
        self.frame, self.settings = frame, []

    def format(self, name):
        self.settings.append(("format", name))
        return self

    def mode(self, name):
        self.settings.append(("mode", name))
        return self

    def save(self, path):
        assert self.settings == [("format", "delta"), ("mode", "errorifexists")], self.settings
        lake = self.frame.lake
        if lake.exists(path):
            raise RuntimeError(f"[PATH_ALREADY_EXISTS] {path}")
        lake.tables[path] = {"ddl": self.frame.schema.ddl, "rows": list(self.frame.rows)}
        lake.log.append(("create", path))


class DeltaTable:
    """delta.tables.DeltaTable over the lake's tables: isDeltaTable, forPath and an insert-only merge."""

    def __init__(self, lake, path):
        self.lake, self.path = lake, path

    @staticmethod
    def isDeltaTable(spark, path):
        return path in spark.lake.tables

    @classmethod
    def forPath(cls, spark, path):
        assert path in spark.lake.tables, path
        return cls(spark.lake, path)

    def toDF(self):
        table = self.lake.tables[self.path]
        return Frame(self.lake, Schema(table["ddl"]), list(table["rows"]))

    def alias(self, name):
        assert name == "target"
        return self

    def merge(self, source, condition):
        return Merge(self, source, condition)


class Merge:
    def __init__(self, target, source, condition):
        self.target, self.source, self.inserting = target, source, False
        self.keys = tuple(re.findall(r"target\.(\w+) = source\.\1", condition))
        assert condition == " AND ".join(f"target.{key} = source.{key}" for key in self.keys), condition

    def whenNotMatchedInsertAll(self):
        self.inserting = True
        return self

    def execute(self):
        assert self.inserting, "only whenNotMatchedInsertAll merges are insert-only"
        table = self.target.lake.tables[self.target.path]
        positions = [Schema(table["ddl"]).names.index(key) for key in self.keys]
        present = {tuple(row[index] for index in positions) for row in table["rows"]}
        inserted = 0
        for row in self.source.rows:
            key = tuple(row[index] for index in positions)
            if key not in present:
                table["rows"].append(row)
                present.add(key)
                inserted += 1
        self.target.lake.log.append(("merge", self.target.path, self.keys, inserted))


class Lake:
    """One lakehouse in memory: notebookutils.fs over its files and folders, and its Delta tables by path."""

    def __init__(self):
        self.files, self.directories, self.tables, self.log = {}, set(), {}, []

    def exists(self, path):
        return path in self.files or path in self.directories or path in self.tables

    def mkdirs(self, path):
        self.directories.add(path)
        self.log.append(("mkdirs", path))
        return True

    def put(self, path, content, overwrite):
        assert path.rsplit("/", 1)[0] in self.directories, f"no folder for {path}"
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = content
        self.log.append(("put", path))
        return True


class Spark:
    def __init__(self, lake):
        self.lake = lake

    def createDataFrame(self, data, schema):
        schema = Schema(schema)
        rows = [tuple(row) for row in data]
        assert all(len(row) == len(schema.names) and all(type(value) is str for value in row) for row in rows), rows
        return Frame(self.lake, schema, rows)


def modules(lake, workspace_name="scenario-dev", lakehouse_workspace=WORKSPACE_ID):
    """notebookutils, pyspark.sql and delta.tables as SeedScenario imports them."""
    spark = Spark(lake)

    def get(name, workspace):
        if (name, workspace) != ("Scenario", WORKSPACE_ID):
            raise LookupError(f"no lakehouse {name} in workspace {workspace}")
        return types.SimpleNamespace(id=LAKEHOUSE_ID, workspaceId=lakehouse_workspace, displayName=name)

    context = {"currentWorkspaceId": WORKSPACE_ID, "currentWorkspaceName": workspace_name}
    sql = types.SimpleNamespace(SparkSession=types.SimpleNamespace(builder=types.SimpleNamespace(getOrCreate=lambda: spark)))
    tables = types.SimpleNamespace(DeltaTable=DeltaTable)
    return {"notebookutils": types.SimpleNamespace(runtime=types.SimpleNamespace(context=context),
                                                   lakehouse=types.SimpleNamespace(get=get), fs=lake),
            "pyspark": types.SimpleNamespace(sql=sql), "pyspark.sql": sql,
            "delta": types.SimpleNamespace(tables=tables), "delta.tables": tables}


def seed(lake, parameters=None, **context):
    """Every cell of SeedScenario against `lake`; returns (namespace, printed output)."""
    return run("SeedScenario", modules(lake, **context), parameters)


def seeded():
    lake = Lake()
    seed(lake)
    return lake


class SeedTests(unittest.TestCase):
    def test_a_first_run_seeds_both_tables_and_the_documents_folder(self):
        lake = Lake()
        namespace, output = seed(lake)
        self.assertEqual(sorted(lake.tables), [f"{TABLES}/demo_assets", f"{TABLES}/demo_sites"])
        self.assertIn(PUMP_B, lake.tables[f"{TABLES}/demo_assets"]["rows"])
        self.assertIn(("demo-site-001", "North Works", "North"), lake.tables[f"{TABLES}/demo_sites"]["rows"])
        self.assertEqual(lake.files, {STARTER: namespace["starter_document"]})
        self.assertEqual([entry for entry in lake.log if entry[0] in ("mkdirs", "put")],
                         [("mkdirs", f"{FILES}/documents"), ("put", STARTER)])
        self.assertIn("Files/documents", namespace["starter_document"], "the document names the folder it is indexed from")
        self.assertIn("Created Files/documents/starter.txt.", output)
        self.assertNotIn("Left Files/starter.txt", output)

    def test_a_rerun_inserts_and_writes_nothing(self):
        lake = seeded()
        before, start = copy.deepcopy((lake.files, lake.directories, lake.tables)), len(lake.log)
        _, output = seed(lake)
        self.assertEqual((lake.files, lake.directories, lake.tables), before)
        self.assertEqual(lake.log[start:], [("merge", f"{TABLES}/demo_sites", ("site_id",), 0),
                                            ("merge", f"{TABLES}/demo_assets", ("asset_id",), 0)])
        self.assertIn("Preserving existing Files/documents/starter.txt.", output)

    def test_existing_rows_and_an_edited_document_are_kept_and_missing_rows_inserted(self):
        lake = seeded()
        lake.files[STARTER] = "edited in the lakehouse"
        assets = lake.tables[f"{TABLES}/demo_assets"]["rows"]
        assets[:] = [row for row in assets if row[0] != "demo-asset-001"]
        assets[assets.index(PUMP_B)] = PUMP_B[:3] + ("Operational",)
        seed(lake)
        self.assertEqual(lake.files, {STARTER: "edited in the lakehouse"})
        self.assertIn(("demo-asset-001", "demo-site-001", "Pump A", "Operational"), assets)
        self.assertIn(PUMP_B[:3] + ("Operational",), assets, "an existing row is never updated")
        self.assertEqual(len(assets), 3)

    def test_a_starter_left_in_the_files_root_is_untouched(self):
        lake = Lake()
        lake.directories.add(FILES)
        lake.files[LEGACY] = "an earlier starter document"
        namespace, output = seed(lake)
        self.assertEqual(lake.files, {LEGACY: "an earlier starter document", STARTER: namespace["starter_document"]})
        self.assertNotIn(LEGACY, [entry[1] for entry in lake.log])
        self.assertIn("Left Files/starter.txt in place", output)

    def test_nothing_is_written_outside_a_dev_or_test_lakehouse_or_over_an_incompatible_table(self):
        for parameters, context, message in (
            ({}, {"workspace_name": "scenario-prod"}, "restricted to -dev/-test workspaces"),
            ({"allow_other_workspaces": "true"}, {"workspace_name": "scenario-prod"}, "must be a Boolean"),
            ({}, {"lakehouse_workspace": OTHER_ID}, "outside the current workspace"),
        ):
            lake = Lake()
            with self.subTest(parameters=parameters, context=context), self.assertRaisesRegex(ValueError, message):
                seed(lake, parameters, **context)
            self.assertEqual(lake.log, [])
        lake = Lake()
        lake.directories.add(f"{TABLES}/demo_sites")
        with self.assertRaisesRegex(ValueError, "Refusing to replace a non-Delta path"):
            seed(lake)
        lake = seeded()
        lake.tables[f"{TABLES}/demo_sites"]["ddl"] = "site_id STRING, site_name STRING"
        start = len(lake.log)
        with self.assertRaisesRegex(ValueError, "Existing schema differs from the seed schema: demo_sites"):
            seed(lake)
        self.assertEqual(lake.log[start:], [])
        lake = Lake()
        seed(lake, {"allow_other_workspaces": True}, workspace_name="scenario-prod")
        self.assertIn(STARTER, lake.files)


if __name__ == "__main__":
    unittest.main()
