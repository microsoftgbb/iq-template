# The Container Apps environment, pull identity and browser sign-in for the scenario assistant
# web app. deploy.py builds its image and creates or updates the app itself, at the URL fixed here.

locals {
  # Published Microsoft application behind the https://ai.azure.com audience; not tenant-specific.
  foundry_api_client_id = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
  foundry_api_scope     = "https://ai.azure.com/user_impersonation"
  web_app_name          = "ca-${local.resource_prefix}-web"
  web_app_url           = "https://${local.web_app_name}.${azurerm_container_app_environment.main.default_domain}"
}

data "azuread_client_config" "current" {}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.resource_prefix}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags

  # The service creates every environment with this serverless profile; declaring it avoids a permanent diff.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_user_assigned_identity" "web" {
  name                = "id-${local.resource_prefix}-web"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

# The web app pulls only its image; it has no Foundry or data access of its own.
resource "azurerm_role_assignment" "web_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.web.principal_id
  skip_service_principal_aad_check = true
}

# Read the shared Microsoft service principal; never own or delete it.
data "azuread_service_principal" "foundry_api" {
  client_id = local.foundry_api_client_id

  lifecycle {
    postcondition {
      condition     = contains(keys(self.oauth2_permission_scope_ids), "user_impersonation")
      error_message = "The Foundry API principal does not publish user_impersonation. Verify the selected tenant and public-cloud API."
    }
  }
}

# Public single-page client: the browser signs users in with PKCE and holds no secret.
resource "azuread_application" "web" {
  display_name            = "${var.project_name}-web"
  description             = "Browser sign-in for the ${var.project_name} scenario assistant; calls the hosted agent as the user."
  sign_in_audience        = "AzureADMyOrg"
  owners                  = [data.azuread_client_config.current.object_id]
  prevent_duplicate_names = true

  single_page_application {
    redirect_uris = ["${local.web_app_url}/"]
  }

  # Declare the delegated permission; deploy.py grants tenant-wide consent as the administrator.
  required_resource_access {
    resource_app_id = data.azuread_service_principal.foundry_api.client_id

    resource_access {
      id   = data.azuread_service_principal.foundry_api.oauth2_permission_scope_ids["user_impersonation"]
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "web" {
  client_id = azuread_application.web.client_id
  owners    = [data.azuread_client_config.current.object_id]
}
