# Optional Work IQ (preview) knowledge source, enabled with work_iq_enabled and work_iq_terms_accepted.
# At query time Azure AI Search exchanges the signed-in user's token for this customer-owned app for a
# delegated Work IQ token, authenticating as the app through a federated credential that trusts the Search
# identity: no secret exists. An administrator grants the app's WorkIQAgent.Ask consent separately.
# The source has its own knowledge base: a retrieve without the user's Work IQ token fails, and the Studio
# agent's Foundry IQ connector cannot send one, so the scenario knowledge base must never depend on it.

locals {
  # Work IQ, a Microsoft application; a Global Administrator creates its service principal once per tenant.
  work_iq_client_id   = "fdcc1f02-fc51-4226-8753-f668596af7f7"
  work_iq_scope       = "WorkIQAgent.Ask"
  azure_cli_client_id = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
  work_iq             = var.work_iq_enabled && var.knowledge_lakehouse_id != null
  # The federated credential, source and knowledge base wait for the Search identity like its other grants.
  work_iq_ready       = local.work_iq && local.search_identity_granted
  work_iq_source_name = "${var.project_name}-work-iq"
  work_iq_base_name   = "${var.project_name}-work-iq-knowledge"
}

data "azuread_client_config" "work_iq" {
  count = local.work_iq ? 1 : 0
}

# Read the shared Work IQ principal; never own or change it.
data "azuread_service_principal" "work_iq" {
  count     = local.work_iq ? 1 : 0
  client_id = local.work_iq_client_id

  lifecycle {
    postcondition {
      condition     = contains(keys(self.oauth2_permission_scope_ids), local.work_iq_scope)
      error_message = "The tenant's Work IQ service principal does not publish ${local.work_iq_scope}. A Global Administrator enables Work IQ for the tenant first."
    }
  }
}

resource "random_uuid" "work_iq_scope" {
  count = local.work_iq ? 1 : 0
}

resource "azuread_application" "work_iq" {
  count = local.work_iq ? 1 : 0

  display_name            = "${var.project_name}-work-iq"
  description             = "Work IQ (preview) knowledge source for ${var.project_name}: Azure AI Search queries Work IQ on behalf of the signed-in user through a federated credential. No secrets."
  sign_in_audience        = "AzureADMyOrg"
  owners                  = [data.azuread_client_config.work_iq[0].object_id]
  prevent_duplicate_names = true

  api {
    requested_access_token_version = 2

    # Work IQ knowledge sources require exactly this scope name.
    oauth2_permission_scope {
      id                         = random_uuid.work_iq_scope[0].result
      value                      = "access_as_user"
      type                       = "User"
      enabled                    = true
      admin_consent_display_name = "Query Work IQ for ${var.project_name}"
      admin_consent_description  = "Allow the ${var.project_name} Work IQ knowledge source to query Work IQ on behalf of the signed-in user."
      user_consent_display_name  = "Query Work IQ for ${var.project_name}"
      user_consent_description   = "Allow the ${var.project_name} Work IQ knowledge source to query Work IQ on your behalf."
    }
  }

  # Declare the delegated permission only; an administrator grants consent separately.
  required_resource_access {
    resource_app_id = local.work_iq_client_id

    resource_access {
      id   = data.azuread_service_principal.work_iq[0].oauth2_permission_scope_ids[local.work_iq_scope]
      type = "Scope"
    }
  }

  lifecycle {
    # The dedicated URI resource owns this field after Entra allocates client_id.
    ignore_changes = [identifier_uris]

    precondition {
      condition     = var.work_iq_terms_accepted
      error_message = "Review the Work IQ (preview) costs, data handling and preview actions before setting work_iq_terms_accepted = true (see docs/getting-started.md)."
    }
  }
}

resource "azuread_service_principal" "work_iq" {
  count = local.work_iq ? 1 : 0

  client_id = azuread_application.work_iq[0].client_id
  owners    = [data.azuread_client_config.work_iq[0].object_id]
}

resource "azuread_application_identifier_uri" "work_iq" {
  count = local.work_iq ? 1 : 0

  application_id = azuread_application.work_iq[0].id
  identifier_uri = "api://${azuread_application.work_iq[0].client_id}"
}

# deploy.py proves retrieval with the deploying user's own Azure CLI token for this app.
resource "azuread_application_pre_authorized" "work_iq_azure_cli" {
  count = local.work_iq ? 1 : 0

  application_id       = azuread_application.work_iq[0].id
  authorized_client_id = local.azure_cli_client_id
  permission_ids       = [random_uuid.work_iq_scope[0].result]
}

resource "azuread_application_federated_identity_credential" "work_iq_search" {
  count = local.work_iq_ready ? 1 : 0

  application_id = azuread_application.work_iq[0].id
  display_name   = "${azapi_resource.search.name}-identity"
  description    = "Lets the ${var.project_name} Search service identity authenticate as this app."
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://login.microsoftonline.com/${data.azuread_client_config.work_iq[0].tenant_id}/v2.0"
  subject        = local.search_principal_id

  lifecycle {
    precondition {
      condition     = local.search_principal_id != null
      error_message = local.search_identity_missing
    }
  }
}

resource "restful_resource" "work_iq_source" {
  count = local.work_iq_ready ? 1 : 0

  path            = "/knowledgesources/${local.work_iq_source_name}"
  check_existance = true
  force_new_attrs = ["name", "kind"]

  body = {
    name        = local.work_iq_source_name
    kind        = "workIQ"
    description = "Microsoft 365 work context (mail, meetings, chats and files) that the signed-in user can access, through Work IQ (preview)."
    workIQParameters = {
      entraAppAuthentication = {
        applicationId         = azuread_application.work_iq[0].client_id
        federatedCredentialId = azuread_application_federated_identity_credential.work_iq_search[0].credential_id
      }
    }
  }

  lifecycle {
    replace_triggered_by = [azapi_resource.search]
  }
}

resource "restful_resource" "work_iq_knowledge_base" {
  count = local.work_iq_ready ? 1 : 0

  path            = "/knowledgebases/${local.work_iq_base_name}"
  check_existance = true

  body = {
    name               = local.work_iq_base_name
    description        = "Work IQ (preview) knowledge base for ${var.project_name}, queried with the signed-in user's own Work IQ token."
    outputMode         = "answerSynthesis"
    answerInstructions = "Answer from the retrieved Work IQ results and include their citations. If they do not contain the answer, say you do not know."
    knowledgeSources   = [{ name = restful_resource.work_iq_source[count.index].body.name }]
    models = [{
      kind = "azureOpenAI"
      azureOpenAIParameters = {
        resourceUri  = local.openai_endpoint
        deploymentId = azapi_resource.chat_model.name
        modelName    = var.chat_model.name
      }
    }]
    retrievalReasoningEffort = { kind = "low" }
  }

  lifecycle {
    # Search refuses to delete a source while a knowledge base references it.
    replace_triggered_by = [restful_resource.work_iq_source[count.index]]
  }
}
