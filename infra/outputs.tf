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
  value       = local.foundry_project_endpoint
}

output "foundry_project_id" {
  description = "Foundry project Azure resource ID."
  value       = azapi_resource.foundry_project.id
}

output "model_deployments" {
  description = "Foundry model deployment names: agent for the prompt agents, chat for knowledge-base query planning and answer synthesis, embedding for the folder sources."
  value = {
    agent     = azapi_resource.agent_model.name
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
  description = "Foundry IQ knowledge base of approved documents for the agents' Foundry IQ tool, backed by Azure AI Search: only the attached knowledge_folders sources, never an ontology. Absent until knowledge setup."
  value       = one(restful_resource.knowledge_base[*].body.name)
}

output "knowledge_source_names" {
  description = "Indexed OneLake knowledge sources keyed by knowledge_folders key, attached to knowledge_base_name or not; empty until knowledge setup. Use includeReferenceSourceData in retrieval requests for document URLs."
  value       = { for key, source in restful_resource.knowledge_source : key => source.body.name }
}

output "ontology_check_knowledge_base_name" {
  description = "Check-only knowledge base holding the live ontology sources, which deploy.py verify queries with the deployer's own token; no agent uses it. Absent until an ontology is registered."
  value       = one(restful_resource.ontology_check_base[*].body.name)
}

output "knowledge_setup_pending" {
  description = "True until the bootstrapped DEV lakehouse GUID is supplied. False does not imply asynchronous ingestion has finished."
  value       = var.knowledge_lakehouse_id == null
}

output "knowledge_source_status_urls" {
  description = "Per knowledge_source_names key: GET with a Search token to inspect asynchronous ingestion and errors."
  value = {
    for key, source in restful_resource.knowledge_source :
    key => "${local.search_endpoint}/knowledgesources/${source.body.name}/status?api-version=${local.search_api_version}"
  }
}

output "knowledge_base_mcp_endpoint" {
  description = "MCP endpoint of knowledge_base_name for Foundry agents, with the Search API version in its query."
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

output "ontology_knowledge_sources" {
  description = "Live Fabric ontology sources registered in the check-only knowledge base, with DEV physical item IDs. Empty until explicitly configured."
  value = {
    for name, source in restful_resource.ontology_source : name => {
      knowledge_source_name = source.body.name
      workspace_id          = fabric_workspace.dev.id
      ontology_id           = data.fabric_ontology.knowledge[name].id
    }
  }
}

output "knowledge_base_requires_user_token" {
  description = "When true, retrieval from ontology_check_knowledge_base_name must include an end-user Search-audience token in x-ms-query-source-authorization; project managed identity alone cannot query Fabric ontologies. knowledge_base_name holds no ontology and never needs it."
  value       = length(var.knowledge_ontologies) > 0
}

output "fabric_iq_connections" {
  description = "Native Foundry Fabric IQ delegated-user connections keyed by ontology registration; the with-IQ agent's Fabric IQ tool uses the scenario registration's. They don't prove that a user has consented or may read the ontology."
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
    mcp_endpoint    = local.web_iq_mcp_endpoint
  }
}

output "public_context_knowledge_base_name" {
  description = "Foundry IQ knowledge base whose only source is Web IQ, for public context through the agents' Web IQ tool; absent until knowledge setup."
  value       = one(restful_resource.public_context_base[*].body.name)
}

output "public_context_knowledge_source_name" {
  description = "Web IQ MCP server knowledge source of the public-context knowledge base; absent until knowledge setup."
  value       = one(restful_resource.public_context_source[*].body.name)
}

output "foundry_agents" {
  description = "The prompt agents, keyed by variant: the Foundry agent name, the display name the runner sets as its description, and the reviewed definition directory it deploys. deploy.py's agent phase creates their versions; Terraform does not."
  value       = local.agents
}

output "agent_settings" {
  description = "Model settings the agent phase fills into every agent definition: the agent deployment, its model and the reasoning effort (null omits it)."
  value = {
    model_deployment = azapi_resource.agent_model.name
    model            = var.agent_model.name
    reasoning_effort = var.agent_reasoning_effort
  }
}

output "foundry_tool_connections" {
  description = "The agents' MCP tool connections besides Fabric IQ, with the server URL each tool must name: the Foundry IQ knowledge base and Web IQ's public-context knowledge base, each read as the project identity (null until knowledge setup), and Work IQ's MCP server, as each user through OAuth identity passthrough (null unless work_iq_enabled)."
  value = {
    knowledge_base = var.knowledge_lakehouse_id == null ? null : {
      name       = azapi_resource.knowledge_base_connection[0].name
      server_url = local.knowledge_base_mcp_endpoint
    }
    public_context = var.knowledge_lakehouse_id == null ? null : {
      name       = azapi_resource.public_context_connection[0].name
      server_url = local.public_context_mcp_endpoint
    }
    work_iq_mail = var.work_iq_enabled ? {
      name       = azapi_resource.work_iq_connection[0].name
      server_url = local.work_iq_mcp_endpoint
    } : null
  }
}

output "work_iq" {
  description = "The Work IQ route when work_iq_enabled is true: the registration that is the OAuth client of Foundry's work-iq connection (WorkIQAgent.Ask and offline_access, which an administrator grants at api_permissions_url) and Foundry's redirect URL registered on it. Registration is not consent, and Work IQ needs a Copilot Credits spending policy that covers its API, even for users with a Microsoft 365 Copilot licence. When work_iq_enabled is false, only { enabled = false }."
  # A conditional can't return two object shapes, so merge adds the route's details, which exist only with its
  # registration, to the flag.
  value = merge({ enabled = var.work_iq_enabled }, [
    for registration in azuread_application.work_iq : {
      client_id                   = registration.client_id
      service_principal_object_id = azuread_service_principal.work_iq[0].object_id
      tenant_id                   = data.azurerm_client_config.current.tenant_id
      connection_name             = azapi_resource.work_iq_connection[0].name
      mcp_endpoint                = local.work_iq_mcp_endpoint
      oauth_scopes                = local.work_iq_mcp_scopes
      oauth_redirect_url          = azapi_resource.work_iq_connection[0].output.properties.redirectUrl
      api_client_id               = local.work_iq_api_client_id
      delegated_scopes            = ["WorkIQAgent.Ask"]
      api_permissions_url         = "https://portal.azure.com/#@${data.azurerm_client_config.current.tenant_id}/view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/${registration.client_id}/isMSAApp~/false"
    }
  ]...)
}

output "container_registry" {
  description = "The registry deploy.py's app phase builds the web app image in, with ACR Tasks, and the image repository."
  value = {
    name         = azurerm_container_registry.app.name
    login_server = azurerm_container_registry.app.login_server
    repository   = local.app_image_name
  }
}

output "app" {
  description = "The web app on Azure Container Apps: its URL (null until the app phase sets app_image_tag), the image tag it runs and its sign-in registration, whose redirect URIs are the app's and any app_local_redirect_uris MSAL redirect bridge. Registration is not consent: each user consents to its one delegated scope, https://ai.azure.com/user_impersonation, at first sign-in, unless an administrator granted it tenant-wide for service_principal_object_id: deploy.py grants it as the assumed administrator, or with --manual-grants leaves it to api_permissions_url."
  value = {
    url                         = var.app_image_tag == null ? null : "https://${azurerm_container_app.app[0].ingress[0].fqdn}"
    name                        = local.app_name
    image_tag                   = var.app_image_tag
    client_id                   = azuread_application.app.client_id
    service_principal_object_id = azuread_service_principal.app.object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
    redirect_uris               = sort(tolist(azuread_application.app.single_page_application[0].redirect_uris))
    api_permissions_url         = "https://portal.azure.com/#@${data.azurerm_client_config.current.tenant_id}/view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/${azuread_application.app.client_id}/isMSAApp~/false"
  }
}

output "app_settings" {
  description = "The non-secret environment the app's server reads, for a local live run (npm run dev) against this deployment."
  value = {
    FOUNDRY_PROJECT_ENDPOINT = local.foundry_project_endpoint
    ENTRA_TENANT_ID          = data.azurerm_client_config.current.tenant_id
    ENTRA_CLIENT_ID          = azuread_application.app.client_id
    AGENT_WITH_IQ            = local.agents.with_iq.name
    AGENT_WITHOUT_IQ         = local.agents.without_iq.name
  }
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace (customer) ID holding the app's Container Apps logs and the Application Insights tables of the app's and the agents' traces."
  value       = azurerm_log_analytics_workspace.main.workspace_id
}

output "application_insights" {
  description = "Application Insights for the app's and the agents' traces: query it with app_id (az monitor app-insights query), or open the Foundry project's Traces view. Foundry's prompts, answers and tool content sit in the protected AppGenAIContent table, which only Privileged Monitoring Data Reader holders, such as the deployer, can read."
  value = {
    id     = azurerm_application_insights.main.id
    name   = azurerm_application_insights.main.name
    app_id = azurerm_application_insights.main.app_id
  }
}
