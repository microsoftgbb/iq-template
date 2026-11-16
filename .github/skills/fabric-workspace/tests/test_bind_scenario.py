"""BindScenario, run locally on the repository's ScenarioOntology parts against stand-ins for Fabric.

The notebook's cells run with stand-ins for notebookutils and requests; nothing calls Fabric. The stored forms replay
what Fabric returned for this binder's SQL endpoint form in a derived project's DEV (October 2026): CRLF line
endings; the expression rewritten to ALM tokens naming the pinned lakehouse, with every ONT_* pin kept; and only
ONT_PinnedAtUtc kept with the OneLake (AzureStorage.DataLake) expression an earlier binder wrote.
"""

import base64
import copy
import json
import re
import types
import unittest

from _support import LAKEHOUSE_ID, OTHER_ID, WORKSPACE, WORKSPACE_ID, run

ONTOLOGY = WORKSPACE / "ScenarioOntology.Ontology"
SQL_ID = "44444444-4444-4444-8444-444444444444"
ONTOLOGY_ID = "55555555-5555-4555-8555-555555555555"
OPERATION_ID = "66666666-6666-4666-8666-666666666666"
SQL_HOST = "example-endpoint.datawarehouse.fabric.microsoft.com"
WORKSPACE_NAME = "scenario-dev"
PINS = ("ONT_WorkspaceId", "ONT_ItemId", "ONT_ItemKind", "ONT_ItemName", "ONT_WorkspaceName", "ONT_SqlEndpoint",
        "ONT_SqlDatabase", "ONT_PinnedAtUtc")
LAKEHOUSE = {"workspaceId": WORKSPACE_ID, "id": LAKEHOUSE_ID, "type": "Lakehouse", "displayName": "Scenario",
             "properties": {"defaultSchema": "dbo",
                            "sqlEndpointProperties": {"connectionString": SQL_HOST, "id": SQL_ID,
                                                      "provisioningStatus": "Success"}}}


def modules(fabric=None):
    """notebookutils and requests as BindScenario imports them; `fabric` answers its REST calls."""
    context = {"currentWorkspaceId": WORKSPACE_ID, "currentWorkspaceName": WORKSPACE_NAME}
    notebookutils = types.SimpleNamespace(runtime=types.SimpleNamespace(context=context),
                                          credentials=types.SimpleNamespace(getToken=lambda audience: "user-token"))
    requests = types.ModuleType("requests")
    if fabric:
        requests.request = fabric.request
    return {"notebookutils": notebookutils, "requests": requests}


def binder():
    """The binder's parameter and helper cells, executed without its Fabric calls."""
    return run("BindScenario", modules(), count=2)[0]


def run_binder(fabric, parameters=None):
    """Every cell of BindScenario against `fabric`; returns the printed output."""
    return run("BindScenario", modules(fabric), parameters)[1]


def encode(path, text):
    return {"path": path, "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"), "payloadType": "InlineBase64"}


def text_of(parts, path):
    return base64.b64decode(parts[path]["payload"]).decode("utf-8")


def repository_parts():
    return {path.relative_to(ONTOLOGY).as_posix(): encode(path.relative_to(ONTOLOGY).as_posix(), path.read_text(encoding="utf-8"))
            for path in sorted(ONTOLOGY.rglob("*")) if path.is_file()}


def alm(workspace=WORKSPACE_ID, lakehouse=LAKEHOUSE_ID):
    return f'Sql.Database("ONT_ALM_SQL_ENDPOINT", "ONT_ALM_SQL_DATABASE-Lakehouse:{workspace}:{lakehouse}")'


def stored(parts, keep_pins=True):
    """The definition as Get Definition returns it: CRLF, ALM tokens and the pins Fabric kept."""
    result = {}
    for path in parts:
        text = text_of(parts, path)
        if path.startswith("tables/") and not keep_pins:
            text = re.sub(r"(?m)^\t\tannotation ONT_(?!PinnedAtUtc)\w+ = [^\n]*\n\n?", "", text)
        if path == "expressions.tmdl":
            text = text.replace(f'Sql.Database("{SQL_HOST}", "{SQL_ID}")', alm())
        result[path] = encode(path, text.replace("\r\n", "\n").replace("\n", "\r\n"))
    return result


def onelake_binding(bound_definition):
    """A definition an earlier binder bound through OneLake, as Fabric stored it: of the pins only ONT_PinnedAtUtc."""
    onelake = ("expression ScenarioLakehouse =\n\t\tlet\n\t\t\tSource = AzureStorage.DataLake("
               f'"https://onelake.dfs.fabric.microsoft.com/{WORKSPACE_ID}/{LAKEHOUSE_ID}", [HierarchicalNavigation=true])\n'
               "\t\tin\n\t\t\tSource\n\tlineageTag: afcdf5a6-abb9-4722-81d6-c540ac4b4433\n")
    bound = bound_definition(repository_parts(), LAKEHOUSE, WORKSPACE_NAME)
    return stored(dict(bound, **{"expressions.tmdl": encode("expressions.tmdl", onelake)}), keep_pins=False)


def replace_text(parts, path, old, new):
    text = text_of(parts, path)
    assert old in text, (path, old)
    return dict(parts, **{path: encode(path, text.replace(old, new))})


def pins(text):
    return dict(re.findall(r"(?m)^\t\tannotation (ONT_\w+) = (.*?)\r?$", text))


class Response:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        return self.body


class FakeFabric:
    """The Fabric REST API as BindScenario uses it, over one workspace's items and ontology definition."""

    def __init__(self, parts, logical_id=None, generation=2, refresh=None, keep_rules=True, keep_pins=True):
        self.parts = dict(parts)
        self.item = {"id": ONTOLOGY_ID, "type": "Ontology", "workspaceId": WORKSPACE_ID, "displayName": "ScenarioOntology",
                     "logicalId": logical_id or json.loads(text_of(parts, ".platform"))["config"]["logicalId"],
                     "properties": {"generation": generation}}
        self.items = [{"id": LAKEHOUSE_ID, "type": "Lakehouse", "displayName": "Scenario", "workspaceId": WORKSPACE_ID},
                      dict(self.item)]
        self.lakehouse = copy.deepcopy(LAKEHOUSE)
        self.refresh = refresh or {}
        self.keep_rules, self.keep_pins = keep_rules, keep_pins
        self.calls, self.updates, self.refreshes, self.edits = [], [], [], []
        self.throttle = 0
        self.status = "Succeeded"
        self.operation = None

    def request(self, method, url, headers=None, timeout=None, allow_redirects=None, params=None, json=None):
        assert url.startswith("https://api.fabric.microsoft.com/v1/") and headers == {"Authorization": "Bearer user-token"}
        assert allow_redirects is False and 0 < timeout <= 30
        path = url[len("https://api.fabric.microsoft.com"):]
        self.calls.append((method, path))
        if self.throttle:
            self.throttle -= 1
            return Response(429, {"errorCode": "TooManyRequests"}, {"Retry-After": "0"})
        workspace = f"/v1/workspaces/{WORKSPACE_ID}"
        if (method, path) == ("GET", f"{workspace}/items"):
            return Response(200, {"value": [item for item in self.items if item["type"] == params["type"]]})
        if (method, path) == ("GET", f"{workspace}/ontologies/{ONTOLOGY_ID}"):
            return Response(200, self.item)
        if (method, path) == ("POST", f"{workspace}/ontologies/{ONTOLOGY_ID}/getDefinition"):
            if self.edits:
                self.parts = self.edits.pop(0)(self.parts)
            return Response(200, {"definition": {"parts": list(self.parts.values())}})
        if (method, path) == ("GET", f"{workspace}/lakehouses/{LAKEHOUSE_ID}"):
            return Response(200, self.lakehouse)
        if (method, path) == ("POST", f"{workspace}/sqlEndpoints/{SQL_ID}/refreshMetadata"):
            self.refreshes.append(json)
            results = [{"tableName": f"dbo.{name}", "status": self.refresh.get(name, "Success")}
                       for name in json["tables"][0]["tableNames"]]
            return self.accepted({"value": results})
        if (method, path) == ("POST", f"{workspace}/ontologies/{ONTOLOGY_ID}/updateDefinition"):
            parts = {part["path"]: part for part in json["definition"]["parts"]}
            self.updates.append(parts)
            self.parts = {path: part for path, part in stored(parts, self.keep_pins).items()
                          if self.keep_rules or not path.startswith("rules/")}
            return self.accepted(None)
        if (method, path) == ("GET", f"/v1/operations/{OPERATION_ID}"):
            return Response(200, {"status": self.status})
        if (method, path) == ("GET", f"/v1/operations/{OPERATION_ID}/result"):
            return Response(200, self.operation)
        raise AssertionError(f"unexpected Fabric call {method} {path}")

    def accepted(self, result):
        self.operation = result
        return Response(202, None, {"x-ms-operation-id": OPERATION_ID, "Retry-After": "0"})


class BindingCheck(unittest.TestCase):
    binder = binder()

    def bind(self, parts):
        return self.binder["bound_definition"](parts, LAKEHOUSE, WORKSPACE_NAME)

    def assert_bound_to(self, parts, lakehouse_id=LAKEHOUSE_ID, sql_id=SQL_ID):
        # The exact form the runner's binding check reads: the SQL endpoint host and GUID in a let ... in block.
        self.assertEqual(text_of(parts, "expressions.tmdl"),
                         "expression ScenarioLakehouse =\n\t\tlet\n"
                         f'\t\t\tdatabase = Sql.Database("{SQL_HOST}", "{sql_id}")\n'
                         f"\t\tin\n\t\t\tdatabase\n\tlineageTag: {self.binder['source_lineage_tag']}\n")
        for table in self.binder["table_names"]:
            text = text_of(parts, f"tables/{table}.tmdl").replace("\r\n", "\n")
            self.assertIn(f"\tpartition {table} = entity\n\t\tmode: directLake\n", text)
            self.assertIn(f"\t\t\tentityName: {table}\n\t\t\tschemaName: dbo\n", text)
            self.assertIn("\t\t\texpressionSource: ScenarioLakehouse", text)
            values = pins(text)
            self.assertEqual(sorted(values), sorted(PINS), table)
            self.assertRegex(values.pop("ONT_PinnedAtUtc"), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
            self.assertEqual(values, {"ONT_WorkspaceId": WORKSPACE_ID, "ONT_ItemId": lakehouse_id,
                                      "ONT_ItemKind": "Lakehouse", "ONT_ItemName": "Scenario",
                                      "ONT_WorkspaceName": WORKSPACE_NAME, "ONT_SqlEndpoint": SQL_HOST,
                                      "ONT_SqlDatabase": "Scenario"}, table)


class BindingTests(BindingCheck):
    def test_the_binder_targets_exactly_this_ontology(self):
        self.assertEqual(self.binder["ontology_logical_id"],
                         json.loads((ONTOLOGY / ".platform").read_text(encoding="utf-8"))["config"]["logicalId"])
        model = (ONTOLOGY / "model.tmdl").read_text(encoding="utf-8")
        self.assertEqual(list(self.binder["table_names"]), re.findall(r"(?m)^ref table (\S+)$", model))
        self.assertEqual(list(self.binder["entity_names"]), re.findall(r"(?m)^ref entity (\S+)$", model))
        self.assertEqual(self.binder["expression_name"], "ScenarioLakehouse")
        for entity in self.binder["entity_names"]:
            lines = (ONTOLOGY / "entities" / f"{entity}.tmdl").read_text(encoding="utf-8").splitlines()
            self.assertIn(f"\t{self.binder['binding_marker']}", lines, entity)
        # The repository keeps the ontology unbound: partitions, pins and the source expression are target-local.
        self.assertFalse((ONTOLOGY / "expressions.tmdl").exists())
        for table in self.binder["table_names"]:
            self.assertNotRegex((ONTOLOGY / "tables" / f"{table}.tmdl").read_text(encoding="utf-8"),
                                r"(?m)^\s*(?:partition|annotation ONT_)")

    def test_a_first_bind_reads_the_sql_endpoint_and_pins_every_table(self):
        original = repository_parts()
        bound = self.bind(original)
        self.assert_bound_to(bound)
        unrelated = [path for path in original if not path.startswith("tables/")]
        self.assertEqual({path: bound[path] for path in unrelated}, {path: original[path] for path in unrelated})
        self.assertEqual(self.bind(bound), bound)

    def test_the_stored_binding_with_its_pins_is_current(self):
        kept = stored(self.bind(repository_parts()))
        self.assertIn("\r\n", text_of(kept, "tables/demo_assets.tmdl"))
        self.assertEqual(sorted(pins(text_of(kept, "tables/demo_assets.tmdl"))), sorted(PINS))
        self.assertIn(alm(), text_of(kept, "expressions.tmdl"))
        self.assertEqual(self.bind(kept), kept)

    def test_a_missing_pin_fails_the_read_back(self):
        # Pins the graph cannot do without: a stored binding that lost any of them is pinned again, so the binder's
        # read-back check raises instead of reporting the ontology bound.
        dropped = stored(self.bind(repository_parts()), keep_pins=False)
        self.assertNotEqual(self.bind(dropped), dropped)
        kept = stored(self.bind(repository_parts()))
        for pin in PINS:
            with self.subTest(pin=pin):
                text = text_of(kept, "tables/demo_sites.tmdl")
                missing = dict(kept, **{"tables/demo_sites.tmdl": encode(
                    "tables/demo_sites.tmdl", re.sub(rf"(?m)^\t\tannotation {pin} = [^\r\n]*\r\n", "", text))})
                rebound = self.bind(missing)
                self.assertNotEqual(rebound, missing)
                self.assertEqual(sorted(pins(text_of(rebound, "tables/demo_sites.tmdl"))), sorted(PINS))
                self.assertEqual(rebound["tables/demo_assets.tmdl"], kept["tables/demo_assets.tmdl"])

    def test_an_earlier_onelake_binding_is_replaced_by_the_sql_endpoint(self):
        legacy = onelake_binding(self.binder["bound_definition"])
        self.assertEqual(sorted(pins(text_of(legacy, "tables/demo_assets.tmdl"))), ["ONT_PinnedAtUtc"])
        self.assert_bound_to(self.bind(legacy))

    def test_another_target_is_rebound(self):
        bound = self.bind(repository_parts())
        self.assert_bound_to(self.bind(replace_text(bound, "expressions.tmdl", SQL_ID, OTHER_ID)))
        kept = stored(bound)
        for foreign in (alm(lakehouse=OTHER_ID), alm(workspace=OTHER_ID)):
            with self.subTest(foreign=foreign):
                self.assert_bound_to(self.bind(replace_text(kept, "expressions.tmdl", alm(), foreign)))
        moved = replace_text(kept, "expressions.tmdl", alm(), alm(lakehouse=OTHER_ID))
        moved = replace_text(moved, "tables/demo_sites.tmdl", f"ONT_ItemId = {LAKEHOUSE_ID}", f"ONT_ItemId = {OTHER_ID}")
        self.assert_bound_to(self.bind(moved))

    def test_custom_sources_and_partitions_are_refused(self):
        kept = stored(self.bind(repository_parts()))
        for old, new in ((alm(), 'Odbc.DataSource("dsn=x")'), ("\t\t\tdatabase\r\n", "\t\t\tSource\r\n"),
                         ("ONT_ALM_SQL_DATABASE-Lakehouse:", "ONT_ALM_SQL_DATABASE-Warehouse:")):
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, "custom source connector"):
                self.bind(replace_text(kept, "expressions.tmdl", old, new))
        with self.assertRaisesRegex(ValueError, "unrecognized source expression"):
            self.bind(replace_text(kept, "expressions.tmdl", "\t\tin\r\n", "\t\t\tunused = 1,\r\n\t\tin\r\n"))
        with self.assertRaisesRegex(ValueError, "Unexpected expressionSource"):
            self.bind(replace_text(kept, "tables/demo_assets.tmdl", "expressionSource: ScenarioLakehouse",
                                   "expressionSource: OtherSource"))
        with self.assertRaisesRegex(ValueError, "custom partitions"):
            self.bind(replace_text(kept, "tables/demo_assets.tmdl", "partition demo_assets = entity",
                                   "partition custom = entity"))

    def test_every_entity_needs_the_binding_marker(self):
        unmarked = replace_text(repository_parts(), "entities/Asset.tmdl", "\tannotation BootstrapBinding = site-asset-v1\n", "")
        with self.assertRaisesRegex(ValueError, "Asset entity is missing its binding marker"):
            self.bind(unmarked)

    def test_a_lakehouse_in_another_workspace_is_refused(self):
        with self.assertRaisesRegex(ValueError, "outside the current workspace"):
            self.binder["bound_definition"](repository_parts(), dict(LAKEHOUSE, workspaceId=OTHER_ID), WORKSPACE_NAME)


class RunTests(BindingCheck):
    def test_a_run_refreshes_the_tables_then_binds_and_verifies_the_ontology(self):
        fabric = FakeFabric(repository_parts())
        output = run_binder(fabric)
        self.assertEqual(len(fabric.refreshes), 1)
        self.assertEqual({key: fabric.refreshes[0][key] for key in ("recreateTables", "tables")},
                         {"recreateTables": False, "tables": [{"schema": "dbo", "tableNames": ["demo_sites", "demo_assets"]}]})
        self.assertEqual(len(fabric.updates), 1)
        self.assert_bound_to(fabric.updates[0])
        self.assertIn(alm(), text_of(fabric.parts, "expressions.tmdl"))
        self.assertIn("Bound ScenarioOntology.", output)
        self.assertIn(f"Lakehouse GUID: {LAKEHOUSE_ID}\nOntology GUID: {ONTOLOGY_ID}\n", output)
        refreshed = fabric.calls.index(("POST", f"/v1/workspaces/{WORKSPACE_ID}/sqlEndpoints/{SQL_ID}/refreshMetadata"))
        updated = fabric.calls.index(("POST", f"/v1/workspaces/{WORKSPACE_ID}/ontologies/{ONTOLOGY_ID}/updateDefinition"))
        self.assertLess(refreshed, updated, "the SQL metadata is ready before the ontology binds to it")

    def test_a_rerun_leaves_a_bound_ontology_and_its_pins_untouched(self):
        kept = stored(self.bind(repository_parts()))
        fabric = FakeFabric(kept)
        self.assertIn("ScenarioOntology is already bound to the current workspace's lakehouse.", run_binder(fabric))
        self.assertEqual(fabric.updates, [])
        self.assertEqual(fabric.parts, kept, "ONT_PinnedAtUtc included")

    def test_an_earlier_onelake_binding_is_rebound_and_verified(self):
        fabric = FakeFabric(onelake_binding(self.binder["bound_definition"]))
        output = run_binder(fabric)
        self.assertEqual(len(fabric.updates), 1)
        self.assert_bound_to(fabric.updates[0])
        self.assertIn(alm(), text_of(fabric.parts, "expressions.tmdl"))
        self.assertIn("Bound ScenarioOntology.", output)

    def test_a_read_back_without_the_pins_fails(self):
        fabric = FakeFabric(repository_parts(), keep_pins=False)
        with self.assertRaisesRegex(RuntimeError, "without the expected target binding"):
            run_binder(fabric)
        self.assertEqual(len(fabric.updates), 1)

    def test_throttling_is_retried(self):
        fabric = FakeFabric(repository_parts())
        fabric.throttle = 2
        run_binder(fabric)
        self.assertEqual(fabric.calls[0], fabric.calls[2])
        self.assertEqual(len(fabric.updates), 1)

    def test_polling_stops_at_the_deadline_and_failed_operations_stop_the_run(self):
        fabric = FakeFabric(repository_parts())
        fabric.lakehouse["properties"]["sqlEndpointProperties"]["provisioningStatus"] = "InProgress"
        with self.assertRaisesRegex(TimeoutError, "exceeds the ontology binding timeout"):
            run_binder(fabric, {"timeout_seconds": 1})
        fabric = FakeFabric(repository_parts())
        fabric.status = "Failed"
        with self.assertRaisesRegex(RuntimeError, f"Fabric operation {OPERATION_ID} failed"):
            run_binder(fabric)
        self.assertEqual(fabric.updates, [])

    def test_only_the_reviewed_new_experience_ontology_is_bound(self):
        for fabric in (FakeFabric(repository_parts(), logical_id=LAKEHOUSE_ID), FakeFabric(repository_parts(), generation=1)):
            with self.subTest(item=fabric.item), self.assertRaisesRegex(ValueError, "new-experience ScenarioOntology"):
                run_binder(fabric)
            self.assertEqual(fabric.updates, [])

    def test_each_name_resolves_to_one_item_of_the_current_workspace(self):
        fabric = FakeFabric(repository_parts())
        fabric.items.append(dict(fabric.items[0], id=OTHER_ID))
        with self.assertRaisesRegex(ValueError, "Expected one Lakehouse named Scenario"):
            run_binder(fabric)
        self.assertEqual(fabric.updates, [])

    def test_a_failed_table_refresh_stops_the_run(self):
        fabric = FakeFabric(repository_parts(), refresh={"demo_assets": "Failure"})
        with self.assertRaisesRegex(RuntimeError, "SQL metadata is not ready for dbo.demo_assets"):
            run_binder(fabric)
        self.assertEqual(fabric.updates, [])

    def test_a_concurrent_edit_stops_the_update(self):
        fabric = FakeFabric(repository_parts())
        fabric.edits = [lambda parts: parts,
                        lambda parts: replace_text(parts, "entities/Site.tmdl", "An operating site", "A site")]
        with self.assertRaisesRegex(RuntimeError, "changed during binding"):
            run_binder(fabric)
        self.assertEqual(fabric.updates, [])

    def test_an_update_that_loses_business_rules_fails(self):
        rule = encode("rules/MaintenanceNeedsReview.tmdl", "/// A business rule a scenario author added.\n")
        fabric = FakeFabric(dict(repository_parts(), **{rule["path"]: rule}), keep_rules=False)
        with self.assertRaisesRegex(RuntimeError, "changed its business rules"):
            run_binder(fabric)


if __name__ == "__main__":
    unittest.main()
