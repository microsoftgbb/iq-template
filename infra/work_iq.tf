# Work IQ for the agents' mail tool, only with work_iq_enabled. The tool calls the Work IQ MCP server directly through
# Foundry's work-iq connection (foundry.tf), signed in as each user with OAuth identity passthrough: the user consents
# once through the link the app shows, and Foundry keeps a token per user and connection. The registration below is
# that connection's OAuth client. It is a public client, with Foundry's redirect URL registered as such, so nothing
# holds a secret. Declaring WorkIQAgent.Ask is not consent: an administrator grants it. Work IQ bills each call as
# Copilot Credits, even for users with a Microsoft 365 Copilot licence, so it needs a spending policy covering its API.
locals {
  # Published Microsoft application ID of Work IQ; a Global Administrator provisions its principal once per tenant.
  work_iq_api_client_id = "fdcc1f02-fc51-4226-8753-f668596af7f7"
  # Work IQ's MCP server and the delegated scopes its OAuth connection requests; offline_access lets Foundry refresh
  # a user's token without asking again.
  work_iq_mcp_endpoint = "https://workiq.svc.cloud.microsoft/mcp"
  work_iq_mcp_scopes   = ["api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask", "offline_access"]
  entra_oauth_endpoint = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/oauth2/v2.0"
}

# Read the shared Microsoft service principals; never own or delete them. Without Work IQ, nothing reads them, so a
# tenant that never provisioned Work IQ deploys without it.
data "azuread_service_principal" "work_iq" {
  count = var.work_iq_enabled ? 1 : 0

  client_id = local.work_iq_api_client_id

  lifecycle {
    postcondition {
      condition     = contains(keys(self.oauth2_permission_scope_ids), "WorkIQAgent.Ask")
      error_message = "The tenant's Work IQ principal does not publish WorkIQAgent.Ask. Verify the selected tenant."
    }
  }
}

data "azuread_service_principal" "microsoft_graph" {
  count = var.work_iq_enabled ? 1 : 0

  client_id = "00000003-0000-0000-c000-000000000000"
}

resource "azuread_application" "work_iq" {
  count = var.work_iq_enabled ? 1 : 0

  display_name            = "${var.project_name}-work-iq"
  description             = "Work IQ for the ${var.project_name} agents: the OAuth client of Foundry's Work IQ connection, a public client without a secret, which signs each user in to Work IQ."
  sign_in_audience        = "AzureADMyOrg"
  owners                  = [data.azurerm_client_config.current.object_id]
  prevent_duplicate_names = true

  # Declare the delegated permissions; an administrator grants consent separately.
  required_resource_access {
    resource_app_id = data.azuread_service_principal.work_iq[0].client_id

    resource_access {
      id   = data.azuread_service_principal.work_iq[0].oauth2_permission_scope_ids["WorkIQAgent.Ask"]
      type = "Scope"
    }
  }

  required_resource_access {
    resource_app_id = data.azuread_service_principal.microsoft_graph[0].client_id

    resource_access {
      id   = data.azuread_service_principal.microsoft_graph[0].oauth2_permission_scope_ids["offline_access"]
      type = "Scope"
    }
  }

  lifecycle {
    # The redirect URI resource owns this field: Foundry allocates the URL after the connection exists.
    ignore_changes = [public_client]
  }
}

resource "azuread_service_principal" "work_iq" {
  count = var.work_iq_enabled ? 1 : 0

  client_id = azuread_application.work_iq[0].client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

# Foundry's consent service redeems each user's authorization code at this redirect URL. A public-client redirect
# needs no client secret.
resource "azuread_application_redirect_uris" "work_iq" {
  count = var.work_iq_enabled ? 1 : 0

  application_id = azuread_application.work_iq[0].id
  type           = "PublicClient"
  redirect_uris  = [azapi_resource.work_iq_connection[0].output.properties.redirectUrl]
}
