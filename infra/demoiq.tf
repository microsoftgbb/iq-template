# Optional DemoIQ Microsoft 365 demo seeding (.github/skills/demoiq-seed), enabled with
# demoiq_enabled. Delegated only: the app has no application permissions and no credentials.
# Demo users sign in through it as a public client, and an administrator signs in for each
# seeding or cleanup run; both act only within the consented scopes and their own roles.
# An administrator grants that consent separately (deploy.py's demoiq phase relays the link).

locals {
  microsoft_graph_client_id = "00000003-0000-0000-c000-000000000000"
  demoiq_delegated_scopes = toset([
    # Demo content, created and later cleaned up as each demo user.
    "Calendars.ReadWrite",
    "Channel.ReadBasic.All",
    "ChannelMessage.Read.All",
    "ChannelMessage.Send",
    "Chat.Create",
    "Chat.Read",
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "ExternalItem.Read.All",
    "Files.ReadWrite.All",
    "Group.ReadWrite.All",
    "Mail.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "OnlineMeetingTranscript.Read.All",
    "People.Read.All",
    "Sites.Read.All",
    "Team.ReadBasic.All",
    "User.Read",
    # Temporary tenant preparation, its restore, and cleanup, as the signed-in administrator.
    "ChannelMember.Read.All",
    "Chat.ManageDeletion.All",
    "Policy.Read.All",
    "Policy.ReadWrite.AuthenticationMethod",
    "Policy.ReadWrite.ConditionalAccess",
    "User.Read.All",
    # Sign-in and token refresh for the length of a run.
    "offline_access",
    "openid",
  ])
}

data "azuread_client_config" "demoiq" {
  count = var.demoiq_enabled ? 1 : 0
}

# Read the shared Microsoft Graph principal; never own or change it.
data "azuread_service_principal" "microsoft_graph" {
  count     = var.demoiq_enabled ? 1 : 0
  client_id = local.microsoft_graph_client_id

  lifecycle {
    postcondition {
      condition     = length(setsubtract(local.demoiq_delegated_scopes, toset(keys(self.oauth2_permission_scope_ids)))) == 0
      error_message = "Microsoft Graph does not publish every DemoIQ delegated scope in this tenant."
    }
  }
}

resource "azuread_application" "demoiq" {
  count = var.demoiq_enabled ? 1 : 0

  display_name                   = "${var.project_name}-demoiq-seeder"
  description                    = "DemoIQ Microsoft 365 demo seeding. Delegated only: demo users and a signed-in administrator act within its consented scopes."
  sign_in_audience               = "AzureADMyOrg"
  owners                         = [data.azuread_client_config.demoiq[0].object_id]
  prevent_duplicate_names        = true
  fallback_public_client_enabled = true

  # Declare delegated permissions only; an administrator grants consent separately.
  required_resource_access {
    resource_app_id = local.microsoft_graph_client_id

    dynamic "resource_access" {
      for_each = local.demoiq_delegated_scopes

      content {
        id   = data.azuread_service_principal.microsoft_graph[0].oauth2_permission_scope_ids[resource_access.value]
        type = "Scope"
      }
    }
  }
}

resource "azuread_service_principal" "demoiq" {
  count = var.demoiq_enabled ? 1 : 0

  client_id = azuread_application.demoiq[0].client_id
  owners    = [data.azuread_client_config.demoiq[0].object_id]
}
