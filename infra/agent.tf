# Hosting for the Foundry hosted agent: the registry its images come from, telemetry, and the
# project connections Agent Service uses to pull images and export traces. deploy.py builds the
# images and publishes the toolbox and agent versions; those releases live outside Terraform state.

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.resource_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.resource_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

resource "azurerm_container_registry" "main" {
  name                = "cr${local.compact_prefix}${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.container_registry_sku
  admin_enabled       = false
  tags                = local.tags
}

# Agent Service pulls hosted-agent images with the project's managed identity.
resource "azurerm_role_assignment" "project_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azapi_resource.foundry_project.identity[0].principal_id
  skip_service_principal_aad_check = true
}

# The documented registry connection shape is absent from AzAPI's schema.
resource "azapi_resource" "registry_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "container-registry"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category      = "ContainerRegistry"
      authType      = "ManagedIdentity"
      target        = azurerm_container_registry.main.login_server
      isSharedToAll = true
      credentials = {
        clientId   = azapi_resource.foundry_project.identity[0].principal_id
        resourceId = azurerm_container_registry.main.id
      }
      metadata = { ResourceId = azurerm_container_registry.main.id }
    }
  }

  depends_on = [azurerm_role_assignment.project_acr_pull]
}

# Hosted agents export traces through the project's Application Insights connection.
resource "azapi_resource" "app_insights_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "app-insights"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category      = "AppInsights"
      authType      = "ApiKey"
      target        = azurerm_application_insights.main.id
      isSharedToAll = true
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.main.id
      }
    }
  }

  # Write-only: the connection string is sent but never stored in state.
  sensitive_body = {
    properties = {
      credentials = { key = azurerm_application_insights.main.connection_string }
    }
  }
}
