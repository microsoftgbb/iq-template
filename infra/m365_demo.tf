# Optional Microsoft 365 demo seeding (.github/skills/m365-demo-seed), enabled with
# m365_demo_enabled. Delegated only: the app has no application permissions and no credentials.
# Demo users sign in through it as a public client, and an administrator signs in for each
# seeding, cleanup or restore run; both act only within the consented scopes and their own roles.
# An administrator grants that consent separately (deploy.py's m365-demo phase relays the link).

locals {
  microsoft_graph_client_id = "00000003-0000-0000-c000-000000000000"
  m365_demo_delegated_scopes = toset([
    # Demo content, created and later deleted by each demo user in their own mailbox, calendar,
    # OneDrive, chats and team channel.
    "Calendars.ReadWrite",
    "Channel.ReadBasic.All",
    "ChannelMessage.Send",
    "Chat.Create",
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "Files.ReadWrite",
    "Mail.ReadWrite",
    "Mail.Send",
    "Team.ReadBasic.All",
    "User.Read",
    # As the signed-in administrator: the demo team and its membership, the temporary MFA and
    # Conditional Access changes for demo users and their restore, and deleting demo chats.
    "Chat.ManageDeletion.All",
    "Group.ReadWrite.All",
    "Policy.Read.All",
    "Policy.ReadWrite.AuthenticationMethod",
    "Policy.ReadWrite.ConditionalAccess",
    "User.Read.All",
    # Sign-in and token refresh for the length of a run.
    "offline_access",
    "openid",
  ])
}

data "azuread_client_config" "m365_demo" {
  count = var.m365_demo_enabled ? 1 : 0
}

# Read the shared Microsoft Graph principal; never own or change it.
data "azuread_service_principal" "microsoft_graph" {
  count     = var.m365_demo_enabled ? 1 : 0
  client_id = local.microsoft_graph_client_id

  lifecycle {
    postcondition {
      condition     = length(setsubtract(local.m365_demo_delegated_scopes, toset(keys(self.oauth2_permission_scope_ids)))) == 0
      error_message = "Microsoft Graph does not publish every delegated scope the Microsoft 365 demo app declares in this tenant."
    }
  }
}

resource "azuread_application" "m365_demo" {
  count = var.m365_demo_enabled ? 1 : 0

  display_name                   = "${var.project_name}-m365-demo-seed"
  description                    = "Microsoft 365 demo content seeding (m365-demo-seed skill). Delegated only: demo users and a signed-in administrator act within its consented scopes."
  sign_in_audience               = "AzureADMyOrg"
  owners                         = [data.azuread_client_config.m365_demo[0].object_id]
  prevent_duplicate_names        = true
  fallback_public_client_enabled = true

  # Declare delegated permissions only; an administrator grants consent separately.
  required_resource_access {
    resource_app_id = local.microsoft_graph_client_id

    dynamic "resource_access" {
      for_each = local.m365_demo_delegated_scopes

      content {
        id   = data.azuread_service_principal.microsoft_graph[0].oauth2_permission_scope_ids[resource_access.value]
        type = "Scope"
      }
    }
  }
}

resource "azuread_service_principal" "m365_demo" {
  count = var.m365_demo_enabled ? 1 : 0

  client_id = azuread_application.m365_demo[0].client_id
  owners    = [data.azuread_client_config.m365_demo[0].object_id]
}
