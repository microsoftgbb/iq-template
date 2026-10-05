# Fabric item promotion with the CLI

Use the [Microsoft Fabric CLI](https://microsoft.github.io/fabric-cli/) to promote **versioned Fabric items**, not underlying data:

```text
DEV workspace -> GitHub workspace/ -> TEST -> same approved commit -> PROD
```

[`fab deploy`](https://microsoft.github.io/fabric-cli/commands/fs/deploy/) uses this folder's YAML configuration and runs [fabric-cicd](https://github.com/microsoft/fabric-cicd) internally. Install only the CLI; its dependencies include the library. No custom Python code or direct library integration is needed. Terraform creates infrastructure; this separate, optional step publishes items into existing workspaces.

## Prepare in your derived repository

- Use Python **3.10-3.13** and a Fabric identity allowed to create/update the selected item types in the target workspace (Contributor or higher, subject to item-specific requirements).
- Terraform supplies DEV and TEST. **PROD is not provisioned by this template**; provision it in your derived project before configuring its ID.
- Author items in DEV, commit them through Fabric Git integration, then review/merge them in GitHub. `workspace/` must contain real item definitions, including their `.platform` files, not just `.gitkeep`.

From the repository root (Bash/zsh):

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r cicd/requirements.txt
fab auth login --tenant "$(az account show --query tenantId -o tsv)"
```

Edit [`config.yml`](config.yml) with TEST's workspace GUID, **not** an Azure resource ID or DEV's workspace ID. Obtain it with `terraform -chdir=infra output -raw test_workspace_id`. Uncomment and fill in PROD only after provisioning it: the CLI validates every configured ID, including environments not being deployed.

Only include supported, Git-versioned items in `workspace/`. Keep deployment configuration here, outside the Fabric Git item directory. Workspace IDs are not credentials; never put tokens or passwords in YAML.

## Promote

Check out a reviewed commit in a clean working tree. The commands deploy the **local files**; they do not pull uncommitted changes from the live DEV workspace.

```sh
fab deploy --config cicd/config.yml --target_env TEST
```

Verify the application/items in TEST using your project's tests. After approval, deploy the **same commit** to PROD:

```sh
fab deploy --config cicd/config.yml --target_env PROD
```

These commands create/update real target items; they are not dry runs. Keep the interactive confirmation. **Orphan deletion is disabled** by the global `unpublish.skip: true`; removing an item from Git does not delete its deployed counterpart. Do not remove this setting without an explicit deletion policy.

## Environment-specific values

If items contain DEV-specific workspace IDs, connection references, URLs, or settings, customize [`parameter.yml.example`](parameter.yml.example), save it as `cicd/parameter.yml`, and uncomment `core.parameter` in `config.yml`. Keep the `TEST`/`PROD` names consistent. The example only replaces a DEV workspace ID; configure other mappings according to your actual items.

Review parameterization before promotion to avoid production items still referencing DEV resources. Configure credentials and data access separately in each environment. See the upstream [parameterization guide](https://microsoft.github.io/fabric-cicd/latest/how_to/parameterization/).

Add automated GitHub Actions workflows, project tests, and environment approvals in the **derived repository**. Use [federated authentication](https://microsoft.github.io/fabric-cli/examples/auth_examples/) for automation and grant its identity the appropriate Fabric permissions. No workflow here deploys automatically.
