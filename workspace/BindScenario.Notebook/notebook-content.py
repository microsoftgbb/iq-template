# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Bind the scenario ontology
# Run after SeedScenario and ScenarioOntology deploy. This notebook does not seed
# data or create items. It binds only the sample ontology to its current workspace.
# Run as an authorized Fabric user; service-principal NotebookUtils tokens lack
# the full Fabric scopes needed for these definition/metadata operations.
# Serialize runs and authoring: updateDefinition replaces the item's definition.
# Review and commit the generated DEV binding through Fabric source control.

# PARAMETERS ********************

lakehouse_name = "Scenario"
ontology_name = "ScenarioOntology"
timeout_seconds = 900

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import base64
import copy
import json
import re
import time
from uuid import UUID

import notebookutils
import requests

if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
    raise ValueError("timeout_seconds must be an integer from 1 to 3600.")
if not all(isinstance(name, str) and name.strip() for name in (lakehouse_name, ontology_name)):
    raise ValueError("Supply lakehouse and ontology display names in the current workspace.")

workspace_id = str(UUID(notebookutils.runtime.context["currentWorkspaceId"]))
deadline = time.monotonic() + timeout_seconds
api_root = "https://api.fabric.microsoft.com"
sample_logical_id = "e6a99499-e3f5-4a9b-8288-3985c73a05e5"
table_names = ("demo_sites", "demo_assets")
binding_marker = "annotation BootstrapBinding = site-asset-v1"


def remaining_seconds():
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Ontology binding timed out; inspect Fabric operations before rerunning.")
    return remaining


def wait_for_poll(seconds):
    if seconds < 0:
        raise ValueError("A polling delay cannot be negative.")
    if seconds >= remaining_seconds():
        raise TimeoutError("The next Fabric poll exceeds the ontology binding timeout.")
    time.sleep(seconds)


def fabric_request(method, path, **kwargs):
    if not path.startswith("/v1/") or "://" in path:
        raise ValueError("Fabric requests must use a relative v1 API path.")
    while True:
        response = requests.request(
            method,
            f"{api_root}{path}",
            headers={"Authorization": f"Bearer {notebookutils.credentials.getToken('pbi')}"},
            timeout=min(30, remaining_seconds()),
            allow_redirects=False,
            **kwargs,
        )
        if response.status_code == 429:
            wait_for_poll(int(response.headers.get("Retry-After", "5")))
            continue
        if response.status_code not in (200, 201, 202, 204):
            raise RuntimeError(
                f"Fabric {method} {path} failed ({response.status_code}): {response.text[:1000]}"
            )
        return response


def fabric_operation(path, body=None, has_result=True):
    response = fabric_request("POST", path, json=body)
    if response.status_code == 202:
        operation_id = str(UUID(response.headers["x-ms-operation-id"]))
        print(f"Waiting for Fabric operation {operation_id}: {path}")
        while True:
            wait_for_poll(int(response.headers.get("Retry-After", "5")))
            response = fabric_request("GET", f"/v1/operations/{operation_id}")
            operation = response.json()
            if operation["status"] == "Succeeded":
                break
            if operation["status"] not in ("NotStarted", "Running"):
                raise RuntimeError(f"Fabric operation {operation_id} failed: {operation}")
        if has_result:
            response = fabric_request("GET", f"/v1/operations/{operation_id}/result")
    return response.json() if has_result else None


def find_item(item_type, display_name):
    matches = []
    params = {"type": item_type}
    seen_tokens = set()
    while True:
        page = fabric_request("GET", f"/v1/workspaces/{workspace_id}/items", params=params).json()
        matches.extend(item for item in page["value"] if item["displayName"] == display_name)
        token = page.get("continuationToken")
        if not token:
            break
        if token in seen_tokens:
            raise RuntimeError("Fabric item listing repeated its continuation token.")
        seen_tokens.add(token)
        params = {"type": item_type, "continuationToken": token}
    if len(matches) != 1:
        raise ValueError(f"Expected one {item_type} named {display_name} in the current workspace.")
    item = matches[0]
    if item["type"] != item_type or str(UUID(item["workspaceId"])) != workspace_id:
        raise ValueError("Fabric returned an item outside the requested workspace/type.")
    return str(UUID(item["id"]))


def get_definition(ontology_id):
    definition = fabric_operation(
        f"/v1/workspaces/{workspace_id}/ontologies/{ontology_id}/getDefinition?format=TMDL"
    )["definition"]
    parts = {part["path"]: part for part in definition["parts"]}
    if len(parts) != len(definition["parts"]):
        raise ValueError("The ontology definition contains duplicate part paths.")
    return parts


def part_text(parts, path):
    part = parts[path]
    if part["payloadType"] != "InlineBase64":
        raise ValueError(f"Unsupported ontology payload type: {path}")
    return base64.b64decode(part["payload"], validate=True).decode("utf-8")


def set_part(parts, path, text):
    parts[path] = {
        "path": path,
        "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    }


def bound_definition(original, sql_host, sql_database):
    parts = copy.deepcopy(original)
    platform = json.loads(part_text(parts, ".platform"))
    if platform["metadata"]["type"] != "Ontology":
        raise ValueError("The selected item is not an ontology.")
    if str(UUID(platform["config"]["logicalId"])) != sample_logical_id:
        raise ValueError("BindScenario only manages the sample ontology logical ID.")
    if binding_marker not in (line.strip() for line in part_text(parts, "model.tmdl").splitlines()):
        raise ValueError("The ontology is missing its site-asset-v1 binding marker.")

    for table_name in table_names:
        path = f"tables/{table_name}.tmdl"
        text = part_text(parts, path)
        partitions = re.findall(r"(?m)^[ \t]+partition[ \t]+([^\r\n]+)", text)
        if partitions:
            expected = {
                "mode": "directLake",
                "entityName": table_name,
                "schemaName": "dbo",
                "expressionSource": "ScenarioLakehouse",
            }
            if len(partitions) != 1 or partitions[0].strip() not in (
                f"{table_name} = entity", f"'{table_name}' = entity"
            ):
                raise ValueError(f"Refusing to replace custom partitions in {path}.")
            for field, value in expected.items():
                values = re.findall(rf"(?m)^[ \t]+{field}:[ \t]+([^\r\n]+)", text)
                if [entry.strip().strip("'") for entry in values] != [value]:
                    raise ValueError(f"Unexpected {field} in {path}; review the binding manually.")
        else:
            text = (
                f"{text.rstrip()}\n\n"
                f"\tpartition {table_name} = entity\n"
                "\t\tmode: directLake\n"
                "\t\tsource\n"
                f"\t\t\tentityName: {table_name}\n"
                "\t\t\tschemaName: dbo\n"
                "\t\t\texpressionSource: ScenarioLakehouse\n"
            )
            set_part(parts, path, text)

    sql_database = str(UUID(sql_database))
    if not re.fullmatch(r"[A-Za-z0-9.-]+(?:,1433)?", sql_host):
        raise ValueError("The lakehouse returned an unexpected SQL endpoint hostname.")
    sql_call = f'Sql.Database("{sql_host}", "{sql_database}")'
    path = "expressions.tmdl"
    if path not in parts:
        set_part(
            parts,
            path,
            f"expression ScenarioLakehouse =\n\t\t{sql_call}\n"
            "\tlineageTag: afcdf5a6-abb9-4722-81d6-c540ac4b4433\n"
            f"\t{binding_marker}\n",
        )
    else:
        text = part_text(parts, path)
        expressions = re.findall(r"(?m)^expression[ \t]+([^=\r\n]+)=", text)
        if [name.strip().strip("'") for name in expressions] != ["ScenarioLakehouse"]:
            raise ValueError("BindScenario only manages the ScenarioLakehouse expression.")
        if binding_marker not in (line.strip() for line in text.splitlines()):
            raise ValueError("Refusing to overwrite an unmarked source expression.")
        pattern = r'Sql\.Database\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)'
        calls = re.findall(pattern, text)
        if len(calls) != 1:
            raise ValueError("Expected exactly one literal Sql.Database source expression.")
        if calls[0] != (sql_host, sql_database):
            set_part(parts, path, re.sub(pattern, lambda match: sql_call, text))
    return parts

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

lakehouse_id = find_item("Lakehouse", lakehouse_name)
ontology_id = find_item("Ontology", ontology_name)
original = get_definition(ontology_id)

while True:
    lakehouse = fabric_request(
        "GET", f"/v1/workspaces/{workspace_id}/lakehouses/{lakehouse_id}"
    ).json()
    if str(UUID(lakehouse["workspaceId"])) != workspace_id:
        raise ValueError("Refusing to bind to a different workspace.")
    if lakehouse["properties"].get("defaultSchema") != "dbo":
        raise ValueError("Scenario must be a schema-enabled lakehouse with default schema dbo.")
    sql_endpoint = lakehouse["properties"]["sqlEndpointProperties"]
    if sql_endpoint["provisioningStatus"] == "Success":
        break
    if sql_endpoint["provisioningStatus"] != "InProgress":
        raise RuntimeError(f"SQL endpoint provisioning failed: {sql_endpoint['provisioningStatus']}")
    wait_for_poll(5)

sql_id = str(UUID(sql_endpoint["id"]))
updated = bound_definition(original, sql_endpoint["connectionString"], sql_id)

sync = fabric_operation(
    f"/v1/workspaces/{workspace_id}/sqlEndpoints/{sql_id}/refreshMetadata",
    {
        "recreateTables": False,
        "tables": [{"schema": "dbo", "tableNames": list(table_names)}],
        "timeout": {"timeUnit": "Seconds", "value": int(remaining_seconds())},
    },
)
statuses = {}
for result in sync["value"]:
    name = result["tableName"].removeprefix("dbo.")
    if name in statuses or name not in table_names:
        raise RuntimeError(f"Unexpected SQL metadata refresh result: {result}")
    statuses[name] = result
if set(statuses) != set(table_names):
    raise RuntimeError(f"SQL metadata refresh did not report both seeded tables: {statuses}")
for name, result in statuses.items():
    if result["status"] == "Success" or (
        result["status"] == "NotRun" and result.get("lastSuccessfulSyncDateTime")
    ):
        print(f"SQL metadata ready: dbo.{name}")
    else:
        raise RuntimeError(f"SQL metadata is not ready for dbo.{name}: {result}")

if updated != original:
    if get_definition(ontology_id) != original:
        raise RuntimeError("The ontology changed during binding. Serialize authoring and rerun.")
    fabric_operation(
        f"/v1/workspaces/{workspace_id}/ontologies/{ontology_id}/updateDefinition",
        {"definition": {"format": "TMDL", "parts": list(updated.values())}},
        has_result=False,
    )
    verified = get_definition(ontology_id)
    if bound_definition(verified, sql_endpoint["connectionString"], sql_id) != verified:
        raise RuntimeError("The ontology update completed without the expected target binding.")
    print("Bound ScenarioOntology. Review and commit the generated DEV definition in Git.")
else:
    print("ScenarioOntology is already bound to the current workspace's lakehouse.")

print(f"Lakehouse GUID: {lakehouse_id}")
print(f"Ontology GUID: {ontology_id}")
print("Register this physical ontology GUID in knowledge_ontologies after checking a Fabric query.")
print("Foundry IQ ontology retrieval also requires the signed-in user's delegated token.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
