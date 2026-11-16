# The web app on Azure Container Apps: one Node.js container serves the React app and calls the Foundry prompt agents
# with each signed-in user's own Foundry token. Nothing here holds a secret: the registry is pulled with a
# user-assigned identity, and users sign in through a single-page application registration without a client secret.
locals {
  app_name       = "ca-${local.resource_prefix}"
  app_image_name = "app"
  # External ingress is <app>.<environment default domain>; known before the app exists, so the sign-in
  # registration can name its redirect URI without depending on the app.
  app_url                  = "https://${local.app_name}.${azurerm_container_app_environment.main.default_domain}"
  foundry_project_endpoint = "https://${azapi_resource.foundry.name}.services.ai.azure.com/api/projects/${azapi_resource.foundry_project.name}"
  # The published application behind https://ai.azure.com tokens (Azure Machine Learning Services).
  foundry_api_client_id = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
  # deploy.py's agent phase publishes one prompt agent per variant from its reviewed definition directory.
  agents = {
    with_iq    = { name = "${var.project_name}-with-iq", display_name = "Scenario assistant – with IQ", source = "agents/scenario-with-iq" }
    without_iq = { name = "${var.project_name}-without-iq", display_name = "Scenario assistant – without IQ", source = "agents/scenario-without-iq" }
  }
}

# Read the shared Microsoft service principal; never own or delete it.
data "azuread_service_principal" "foundry_api" {
  client_id = local.foundry_api_client_id

  lifecycle {
    postcondition {
      condition     = contains(keys(self.oauth2_permission_scope_ids), "user_impersonation")
      error_message = "The tenant's Azure Machine Learning Services principal, the resource behind https://ai.azure.com, does not publish user_impersonation. Verify the selected tenant."
    }
  }
}

# Users sign in to the app and get a token for Foundry; with Work IQ enabled, Foundry asks each user once for Work IQ
# consent through the link the app shows. No client secret, certificate or application permission; each user consents
# to the Foundry scope at sign-in, or an administrator grants it for everyone.
resource "azuread_application" "app" {
  display_name            = "${var.project_name}-app"
  description             = "Sign-in for the ${var.project_name} web app on Azure Container Apps: the user's own Foundry token, no client secret."
  sign_in_audience        = "AzureADMyOrg"
  owners                  = [data.azurerm_client_config.current.object_id]
  prevent_duplicate_names = true

  # MSAL's redirect bridge, redirect.html, receives every sign-in response, also from the hidden iframe in which MSAL
  # renews a token silently. A local run registers its own bridge through app_local_redirect_uris.
  single_page_application {
    redirect_uris = concat(["${local.app_url}/redirect.html"], var.app_local_redirect_uris)
  }

  required_resource_access {
    resource_app_id = data.azuread_service_principal.foundry_api.client_id

    resource_access {
      id   = data.azuread_service_principal.foundry_api.oauth2_permission_scope_ids["user_impersonation"]
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "app" {
  client_id = azuread_application.app.client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

resource "azurerm_container_registry" "app" {
  name                = "cr${local.compact_prefix}${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.container_registry_sku
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.resource_prefix}-app"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

# Pulling its own image and, in monitoring.tf, publishing its traces are the app identity's only Azure permissions.
# It calls Foundry with each user's token, never its identity.
resource "azurerm_role_assignment" "app_acr_pull" {
  scope                            = azurerm_container_registry.app.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.app.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.resource_prefix}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  logs_destination           = "log-analytics"
  tags                       = local.tags

  # The service creates every environment with this serverless profile; declaring it avoids a permanent diff.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# Created once deploy.py's app phase has built the image and set app_image_tag: the app never runs a placeholder.
resource "azurerm_container_app" "app" {
  count = var.app_image_tag == null ? 0 : 1

  name                         = local.app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.app.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled           = true
    target_port                = 8080
    transport                  = "auto"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # A warm replica: a cold start would add seconds to the first question of a session.
    min_replicas = var.app.min_replicas
    max_replicas = var.app.max_replicas

    container {
      name   = "web"
      image  = "${azurerm_container_registry.app.login_server}/${local.app_image_name}:${var.app_image_tag}"
      cpu    = var.app.cpu
      memory = var.app.memory

      env {
        name  = "PORT"
        value = "8080"
      }
      env {
        name  = "FOUNDRY_PROJECT_ENDPOINT"
        value = local.foundry_project_endpoint
      }
      env {
        name  = "ENTRA_TENANT_ID"
        value = data.azurerm_client_config.current.tenant_id
      }
      env {
        name  = "ENTRA_CLIENT_ID"
        value = azuread_application.app.client_id
      }
      # Tracing: the server sends its spans with this identity, the only use the app makes of it besides pulling.
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = local.telemetry_connection_string
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }
      env {
        name  = "APP_VERSION"
        value = var.app_image_tag
      }
      env {
        name  = "AGENT_WITH_IQ"
        value = local.agents.with_iq.name
      }
      env {
        name  = "AGENT_WITHOUT_IQ"
        value = local.agents.without_iq.name
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/healthz"
        interval_seconds        = 2
        failure_count_threshold = 30
      }
      readiness_probe {
        transport        = "HTTP"
        port             = 8080
        path             = "/healthz"
        interval_seconds = 10
      }
      liveness_probe {
        transport        = "HTTP"
        port             = 8080
        path             = "/healthz"
        interval_seconds = 30
      }
    }

    # One request per question stays open for up to 4 minutes; scale out before a replica holds too many.
    http_scale_rule {
      name                = "concurrent-questions"
      concurrent_requests = "20"
    }
  }

  depends_on = [azurerm_role_assignment.app_acr_pull, azurerm_role_assignment.telemetry_publisher]
}
