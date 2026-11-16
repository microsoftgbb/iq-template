# One workspace for the app's Container Apps logs (its value-free turn records and platform events) and for the
# Application Insights tables, kept for retention_days.
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.resource_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.telemetry.retention_days
  tags                = local.tags
}

# Tracing for the app and the agents in one resource. The app's server sends a span per request and, for each
# question, an invoke_agent span whose trace context Foundry continues in its server-side trace of the run, through
# the project connection below. Ingestion keys are disabled: the app and the Foundry project send with their managed
# identities and Monitoring Metrics Publisher, so nothing holds an ingestion secret.
resource "azurerm_application_insights" "main" {
  name                         = "appi-${local.resource_prefix}"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  workspace_id                 = azurerm_log_analytics_workspace.main.id
  application_type             = "web"
  local_authentication_enabled = false
  tags                         = local.tags

  # Destroyed after the component, so the cleanup below runs once Azure can no longer regenerate what it deletes.
  depends_on = [azapi_resource_action.remove_smart_detection]
}

# Azure gives every component a Failure Anomalies alert rule in its resource group. The rule notifies the subscription's
# Application Insights Smart Detection action group, which Azure creates in that group only if the subscription has none
# yet; otherwise it reuses the existing group, wherever it is. Terraform doesn't manage either, so either would block
# the resource group's deletion. These DELETEs run only at teardown, after the component is gone, as part of the
# reviewed destroy plan. They address this resource group only, never a shared action group elsewhere, and ARM
# answers 204 for one that doesn't exist.
resource "azapi_resource_action" "remove_smart_detection" {
  for_each = {
    action_group           = { type = "Microsoft.Insights/actionGroups@2023-01-01", path = "Microsoft.Insights/actionGroups/Application Insights Smart Detection" }
    failure_anomalies_rule = { type = "Microsoft.AlertsManagement/smartDetectorAlertRules@2021-04-01", path = "Microsoft.AlertsManagement/smartDetectorAlertRules/Failure Anomalies - appi-${local.resource_prefix}" }
  }

  type        = each.value.type
  resource_id = "${azurerm_resource_group.main.id}/providers/${each.value.path}"
  method      = "DELETE"
  when        = "destroy"
}

locals {
  # Without local authentication the connection string only addresses the resource: it can't ingest anything.
  telemetry_connection_string = nonsensitive(azurerm_application_insights.main.connection_string)
  telemetry_publishers = {
    app     = azurerm_user_assigned_identity.app.principal_id
    project = azapi_resource.foundry_project.identity[0].principal_id
  }
  # Workspace-centric queries need the role on the workspace; Foundry and Application Insights read through the component.
  telemetry_content_scopes = {
    application_insights = azurerm_application_insights.main.id
    workspace            = azurerm_log_analytics_workspace.main.id
  }
}

resource "azurerm_role_assignment" "telemetry_publisher" {
  for_each = local.telemetry_publishers

  scope                            = azurerm_application_insights.main.id
  role_definition_name             = "Monitoring Metrics Publisher"
  principal_id                     = each.value
  skip_service_principal_aad_check = true
}

# Foundry's traces hold prompts, answers, tool definitions and tool arguments and results: ontology rows, document and
# public-context passages and, with Work IQ, mail content. Application Insights writes that content only to
# AppGenAIContent. Protecting the table denies every standard read role, so only Privileged Monitoring Data Reader
# holders, the deployer below, can read it. The rest of each trace stays readable to the workspace's readers.
# protectionLevel exists from API version 2026-03-01; earlier versions accept the PATCH and ignore it.
resource "azapi_resource_action" "protect_genai_content" {
  type        = "Microsoft.OperationalInsights/workspaces/tables@2026-03-01"
  resource_id = "${azurerm_log_analytics_workspace.main.id}/tables/AppGenAIContent"
  method      = "PATCH"
  body        = { properties = { protectionLevel = "Protected" } }
}

resource "azurerm_role_assignment" "deployer_telemetry_content_reader" {
  for_each = local.telemetry_content_scopes

  scope        = each.value
  principal_id = data.azurerm_client_config.current.object_id

  # Privileged Monitoring Data Reader: read access to protected tables.
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/dbc9c667-e97f-4491-aee6-90b9cf960190"
}

# Foundry's server-side tracing: one AppInsights connection per project. The schema omits ProjectManagedIdentity for
# this category; Foundry's own samples use it, with the connection string to address the resource.
resource "azapi_resource" "telemetry_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "app-insights"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category      = "AppInsights"
      authType      = "ProjectManagedIdentity"
      target        = azurerm_application_insights.main.id
      isSharedToAll = false
      metadata = {
        ApiType                             = "Azure"
        ResourceId                          = azurerm_application_insights.main.id
        ApplicationInsightsConnectionString = local.telemetry_connection_string
      }
    }
  }

  # Protect AppGenAIContent first: tracing must never start writing prompts, tool results or mail into a table that
  # standard monitoring readers can still read.
  depends_on = [azurerm_role_assignment.telemetry_publisher, azapi_resource_action.protect_genai_content]
}
