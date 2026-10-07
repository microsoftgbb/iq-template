data "azuread_client_config" "current" {}

locals {
  # Published Microsoft application IDs, not tenant-specific object IDs.
  fabric_api_client_id = "00000009-0000-0000-c000-000000000000"
  authorized_clients = {
    azure_cli        = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
    power_bi_service = local.fabric_api_client_id
    power_bi_client  = "871c010f-5e61-4fb1-83ac-98610a7e9110"
  }
  delegated_scopes = toset([
    "Capacity.ReadWrite.All",
    "Connection.ReadWrite.All",
    "Item.Execute.All",
    "Item.ReadWrite.All",
    "Pipeline.Deploy",
    "Pipeline.ReadWrite.All",
    "Workspace.GitCommit.All",
    "Workspace.GitUpdate.All",
    "Workspace.ReadWrite.All",
  ])
}

# Read the shared Microsoft service principal; never own or delete it.
data "azuread_service_principal" "fabric" {
  client_id = local.fabric_api_client_id

  lifecycle {
    postcondition {
      condition     = length(setsubtract(local.delegated_scopes, toset(keys(self.oauth2_permission_scope_ids)))) == 0
      error_message = "The Power BI Service principal does not publish all required Fabric scopes. Verify the selected tenant and supported public-cloud APIs before bootstrapping."
    }
  }
}

resource "random_uuid" "access_scope" {}

resource "azuread_application" "fabric" {
  display_name            = "${var.project_name}-fabric-user-auth"
  description             = "Delegated user authentication for Fabric provisioning, Git bootstrap and promotion."
  sign_in_audience        = "AzureADMyOrg"
  owners                  = [data.azuread_client_config.current.object_id]
  prevent_duplicate_names = true

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      id                         = random_uuid.access_scope.result
      value                      = "access"
      type                       = "User"
      enabled                    = true
      admin_consent_display_name = "Access ${var.project_name} Fabric deployment"
      admin_consent_description  = "Allow delegated users to access Fabric deployment services for ${var.project_name}."
      user_consent_display_name  = "Access ${var.project_name} Fabric deployment"
      user_consent_description   = "Access Fabric deployment services for ${var.project_name} on your behalf."
    }
  }

  # Declare delegated permissions; an administrator grants consent separately.
  required_resource_access {
    resource_app_id = data.azuread_service_principal.fabric.client_id

    dynamic "resource_access" {
      for_each = local.delegated_scopes

      content {
        id   = data.azuread_service_principal.fabric.oauth2_permission_scope_ids[resource_access.value]
        type = "Scope"
      }
    }
  }

  lifecycle {
    # The dedicated URI resource owns this field after Entra allocates client_id.
    ignore_changes = [identifier_uris]
  }
}

resource "azuread_service_principal" "fabric" {
  client_id = azuread_application.fabric.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

resource "azuread_application_identifier_uri" "fabric" {
  application_id = azuread_application.fabric.id
  identifier_uri = "api://${azuread_application.fabric.client_id}"
}

resource "azuread_application_pre_authorized" "clients" {
  for_each = local.authorized_clients

  application_id       = azuread_application.fabric.id
  authorized_client_id = each.value
  permission_ids       = [random_uuid.access_scope.result]
}
