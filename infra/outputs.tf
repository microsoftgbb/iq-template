output "fabric_user_auth" {
  description = "Prepared Fabric user-auth registration, delegated permission manifest and admin approval link. Consent and user sign-in remain separate."
  value       = module.fabric_user_auth.authentication
}

output "fabric_user_auth_tenant_id" {
  description = "Entra tenant for the prepared Fabric user-auth application."
  value       = module.fabric_user_auth.authentication.tenant_id
}

output "fabric_user_auth_scope" {
  description = "Application scope to use with az login after administrator consent."
  value       = module.fabric_user_auth.authentication.login_scope
}

output "resource_group_name" {
  description = "Resource group containing the Azure infrastructure."
  value       = azurerm_resource_group.main.name
}

output "service_locations" {
  description = "Explicit effective regions. Cross-region traffic, cost and data handling must be reviewed when overrides are used."
  value = {
    foundry = var.location
    fabric  = coalesce(var.fabric_location, var.location)
    search  = coalesce(var.search_location, var.location)
  }
}

output "fabric_capacity_id" {
  description = "Fabric capacity GUID used by both workspaces."
  value       = data.fabric_capacity.capacity.id
}

output "fabric_capacity_resource_id" {
  description = "Azure resource ID of the newly provisioned capacity."
  value       = azurerm_fabric_capacity.main.id
}

output "dev_workspace_id" {
  description = "DEV workspace GUID; import definitions with the Bootstrap deployment plan after platform setup."
  value       = fabric_workspace.dev.id
}

output "dev_workspace_name" {
  description = "Display name of the DEV bootstrap target."
  value       = fabric_workspace.dev.display_name
}

output "test_workspace_id" {
  description = "Initially empty TEST workspace."
  value       = fabric_workspace.test.id
}

output "test_workspace_name" {
  description = "Display name of the TEST item-promotion target."
  value       = fabric_workspace.test.display_name
}

output "github_repository_url" {
  description = "Repository containing the reviewed Fabric definitions, agent and app."
  value       = local.github_repository_url
}

output "github_connection_id" {
  description = "Opt-in GitHub credentials connection; null when Fabric Git synchronization is disabled."
  value       = var.fabric_git_enabled ? fabric_connection.github[0].id : null
}

output "github_connect_request" {
  description = "Opt-in Fabric Git Connect request; null when disabled. Terraform does not connect or synchronize Git."
  value = var.fabric_git_enabled ? {
    gitProviderDetails = {
      gitProviderType = "GitHub"
      ownerName       = lower(var.github_owner)
      repositoryName  = var.github_repository_name
      branchName      = var.github_branch_name
      directoryName   = var.github_directory_name
    }
    myGitCredentials = {
      source       = "ConfiguredConnection"
      connectionId = fabric_connection.github[0].id
    }
  } : null
}

output "deployment_pipeline_id" {
  description = "Native Fabric DEV/TEST deployment pipeline; no content is deployed by Terraform."
  value       = fabric_deployment_pipeline.main.id
}

output "deployment_pipeline_stage_ids" {
  description = "Native Fabric stage GUIDs keyed by stage name."
  value       = { for stage in fabric_deployment_pipeline.main.stages : stage.display_name => stage.id }
}

output "foundry_project_endpoint" {
  description = "Foundry project API endpoint."
  value       = "https://${azapi_resource.foundry.name}.services.ai.azure.com/api/projects/${azapi_resource.foundry_project.name}"
}

output "foundry_project_id" {
  description = "Foundry project Azure resource ID."
  value       = azapi_resource.foundry_project.id
}

output "model_deployments" {
  description = "Foundry model deployment names."
  value = {
    chat      = azapi_resource.chat_model.name
    embedding = azapi_resource.embedding_model.name
  }
}

output "search_endpoint" {
  description = "Azure AI Search endpoint."
  value       = local.search_endpoint
}

output "search_management_url" {
  description = "Search service ARM URL. Serverless (preview) cannot be created with a managed identity, so deploy.py PATCHes its system identity here after the platform apply."
  value       = "https://management.azure.com${azapi_resource.search.id}?api-version=${local.search_management_api_version}"
}

output "knowledge_base_name" {
  description = "Foundry IQ knowledge base, backed by Azure AI Search; absent until knowledge setup."
  value       = one(restful_resource.knowledge_base[*].body.name)
}

output "knowledge_setup_pending" {
  description = "True until the bootstrapped DEV lakehouse GUID is supplied. False does not imply asynchronous ingestion has finished."
  value       = var.knowledge_lakehouse_id == null
}

output "knowledge_source_status_url" {
  description = "GET with a Search token to inspect asynchronous ingestion and errors."
  value       = var.knowledge_lakehouse_id == null ? null : "${local.search_endpoint}/knowledgesources/${restful_resource.knowledge_source[0].body.name}/status?api-version=${local.search_api_version}"
}

output "knowledge_base_mcp_endpoint" {
  description = "Knowledge-base MCP endpoint for Foundry agents."
  value       = local.knowledge_base_mcp_endpoint
}

output "knowledge_base_connection_id" {
  description = "Project connection resource ID for the knowledge-base MCP tool."
  value       = one(azapi_resource.knowledge_base_connection[*].id)
}

output "knowledge_lakehouse_id" {
  description = "Git-owned DEV lakehouse containing the knowledge source files; absent until knowledge setup."
  value       = one(data.fabric_lakehouse.knowledge[*].id)
}

output "knowledge_lakehouse_name" {
  description = "Display name of the DEV knowledge lakehouse."
  value       = one(data.fabric_lakehouse.knowledge[*].display_name)
}

output "knowledge_files_url" {
  description = "Authenticated OneLake Files endpoint for source documents."
  value       = var.knowledge_lakehouse_id == null ? null : "https://onelake.dfs.fabric.microsoft.com/${fabric_workspace.dev.id}/${data.fabric_lakehouse.knowledge[0].id}/Files"
}

output "knowledge_source_name" {
  description = "Indexed OneLake source; use includeReferenceSourceData in retrieval requests for document URLs."
  value       = one(restful_resource.knowledge_source[*].body.name)
}

output "ontology_knowledge_sources" {
  description = "Live Fabric ontology sources registered in the knowledge base, with DEV physical item IDs. Empty until explicitly configured."
  value = {
    for name, source in restful_resource.ontology_source : name => {
      knowledge_source_name = source.body.name
      workspace_id          = fabric_workspace.dev.id
      ontology_id           = data.fabric_ontology.knowledge[name].id
    }
  }
}

output "knowledge_base_requires_user_token" {
  description = "When true, retrieval must include an end-user Search-audience token in x-ms-query-source-authorization. Project managed identity alone cannot query Fabric ontologies."
  value       = length(var.knowledge_ontologies) > 0
}

output "fabric_iq_connections" {
  description = "Native Foundry Fabric IQ delegated-user connections keyed by ontology registration. These do not create agents or prove consuming-app authorization."
  value = {
    for name, connection in azapi_resource.fabric_iq_connection : name => {
      id         = connection.id
      name       = connection.name
      server_url = connection.body.properties.target
    }
  }
}

output "web_iq" {
  description = "Web IQ account and public service endpoints."
  value = {
    resource_id     = azapi_resource.web_iq.id
    management_url  = "https://management.azure.com${azapi_resource.web_iq.id}?api-version=2026-06-01-preview"
    portal_url      = "https://webiq.microsoft.ai/profiles"
    search_endpoint = "https://api.microsoft.ai/v3/search/web"
    mcp_endpoint    = "https://api.microsoft.ai/v3/mcp"
  }
}

output "studio_agent_schema_name" {
  description = "Prescribed schema name for the scenario's Copilot Studio agent, derived from project_name. PAC creates the agent; Terraform does not."
  value       = "iq_${replace(var.project_name, "-", "_")}_assistant"
}

output "studio_agent_display_name" {
  description = "Prescribed display name for the scenario's Copilot Studio agent and Managed Runtime app."
  value       = "${var.project_name} assistant"
}

output "m365_demo" {
  description = "Optional Microsoft 365 demo seeding handoff (null unless m365_demo_enabled): the delegated-only app, its declared scopes and the administrator consent link. Registration does not prove consent."
  value = var.m365_demo_enabled ? {
    tenant_id                   = data.azuread_client_config.m365_demo[0].tenant_id
    client_id                   = azuread_application.m365_demo[0].client_id
    service_principal_object_id = azuread_service_principal.m365_demo[0].object_id
    delegated_scopes            = sort(tolist(local.m365_demo_delegated_scopes))
    api_permissions_url         = "https://portal.azure.com/#@${data.azuread_client_config.m365_demo[0].tenant_id}/view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/${azuread_application.m365_demo[0].client_id}/isMSAApp~/false"
  } : null
}

output "work_iq" {
  description = "Optional Work IQ (preview) knowledge handoff (null unless work_iq_enabled at knowledge setup): the app whose user token a retrieve sends in x-ms-query-work-iq-source-authorization, its consent link and the Work IQ knowledge base. Registration does not prove consent, tenant enablement or billing; the Studio agent and app cannot query it yet."
  value = local.work_iq ? {
    tenant_id                   = data.azuread_client_config.work_iq[0].tenant_id
    client_id                   = azuread_application.work_iq[0].client_id
    service_principal_object_id = azuread_service_principal.work_iq[0].object_id
    user_scope                  = "${azuread_application_identifier_uri.work_iq[0].identifier_uri}/access_as_user"
    work_iq_app_id              = local.work_iq_client_id
    delegated_scopes            = [local.work_iq_scope]
    api_permissions_url         = "https://portal.azure.com/#@${data.azuread_client_config.work_iq[0].tenant_id}/view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/${azuread_application.work_iq[0].client_id}/isMSAApp~/false"
    knowledge_source_name       = local.work_iq_ready ? restful_resource.work_iq_source[0].body.name : null
    knowledge_base_name         = local.work_iq_ready ? restful_resource.work_iq_knowledge_base[0].body.name : null
  } : null

  depends_on = [azuread_application_pre_authorized.work_iq_azure_cli]
}
