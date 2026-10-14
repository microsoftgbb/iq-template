locals {
  # Serverless (preview) fails provisioning when a managed identity is requested, and its resource
  # provider answers HTTP 500 to identity writes and to every change once an identity exists.
  # Terraform creates it without one, deploy.py enables the system identity after the platform
  # apply, and a changed setting replaces the service instead of updating it in place.
  search_identity_deferred = var.search_sku == "serverless"
  # Grant the identity once it exists: at creation on dedicated tiers, at knowledge setup on serverless,
  # and never in the apply that creates or replaces a serverless service (deploy.py stages that plan).
  search_identity_granted = var.search_identity_ready && (!local.search_identity_deferred || var.knowledge_lakehouse_id != null)
  search_identity_missing = "The Search service has no system-assigned identity yet. deploy.py up enables it and stages plans that create or replace serverless Search. By hand: apply such a plan with -var search_identity_ready=false, PATCH {\"identity\":{\"type\":\"SystemAssigned\"}} to the search_management_url output (the preview answers HTTP 500), confirm identity.principalId with a GET, then plan again."
  search_settings = {
    semanticSearch = "standard"
    # Agentic retrieval has its own billing plan since the 2026-04-01 Search API.
    knowledgeRetrieval = "standard"
    # The project identity authenticates to Search; ontology queries also need a user token.
    # Local authentication stays on for the admin key that manages knowledge objects.
    disableLocalAuth = false
    authOptions      = { aadOrApiKey = { aadAuthFailureMode = "http401WithBearerChallenge" } }
  }
  search_endpoint     = azapi_resource.search.output.endpoint
  search_principal_id = azapi_resource.search.output.principal_id
}

resource "azapi_resource" "search" {
  type      = "Microsoft.Search/searchServices@${local.search_management_api_version}"
  name      = "srch-${local.resource_prefix}"
  parent_id = azurerm_resource_group.main.id
  location  = coalesce(var.search_location, var.location)
  tags      = local.tags

  dynamic "identity" {
    for_each = local.search_identity_deferred ? [] : ["SystemAssigned"]
    content {
      type = identity.value
    }
  }

  body = {
    sku        = { name = var.search_sku }
    properties = local.search_settings
  }

  # Serverless cannot switch tiers or, during its preview, change any setting in place.
  replace_triggers_external_values = {
    sku      = var.search_sku
    settings = local.search_identity_deferred ? jsonencode([local.search_settings, local.tags]) : null
  }

  response_export_values = {
    endpoint     = "properties.endpoint"
    principal_id = "identity.principalId"
  }

  lifecycle {
    # deploy.py owns enabling the serverless identity; never plan its removal.
    ignore_changes = [identity]
  }
}

data "fabric_lakehouse" "knowledge" {
  count = var.knowledge_lakehouse_id == null ? 0 : 1

  id           = var.knowledge_lakehouse_id
  workspace_id = fabric_workspace.dev.id
}

data "fabric_ontology" "knowledge" {
  for_each = var.knowledge_ontologies

  id           = each.value.ontology_id
  workspace_id = fabric_workspace.dev.id
}

# The OneLake indexer currently requires workspace Contributor, not Viewer.
resource "fabric_workspace_role_assignment" "search_onelake_contributor" {
  count = local.search_identity_granted ? 1 : 0

  workspace_id = fabric_workspace.dev.id
  role         = "Contributor"
  principal = {
    id   = local.search_principal_id
    type = "ServicePrincipal"
  }

  lifecycle {
    precondition {
      condition     = local.search_principal_id != null
      error_message = local.search_identity_missing
    }
  }
}

resource "azurerm_role_assignment" "search_foundry_user" {
  count = local.search_identity_granted ? 1 : 0

  scope                            = azapi_resource.foundry.id
  role_definition_name             = "Cognitive Services User"
  principal_id                     = local.search_principal_id
  skip_service_principal_aad_check = true

  lifecycle {
    precondition {
      condition     = local.search_principal_id != null
      error_message = local.search_identity_missing
    }
  }
}

resource "azurerm_role_assignment" "project_search_reader" {
  scope                            = azapi_resource.search.id
  role_definition_name             = "Search Index Data Reader"
  principal_id                     = azapi_resource.foundry_project.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "deployer_search_reader" {
  scope                = azapi_resource.search.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "restful_resource" "knowledge_source" {
  count = var.knowledge_lakehouse_id == null ? 0 : 1

  path            = "/knowledgesources/${local.knowledge_source_name}"
  check_existance = true
  # Rebuild the generated ingestion objects when source settings change.
  force_new_attrs = ["name", "kind", "description", "indexedOneLakeParameters"]

  body = {
    name        = local.knowledge_source_name
    kind        = "indexedOneLake"
    description = "Documents from the ${var.project_name} DEV knowledge lakehouse Files area."
    indexedOneLakeParameters = {
      fabricWorkspaceId = fabric_workspace.dev.id
      lakehouseId       = data.fabric_lakehouse.knowledge[count.index].id
      ingestionParameters = {
        networkAccessMode         = "public"
        contentExtractionMode     = "minimal"
        disableImageVerbalization = true
        ingestionSchedule         = { interval = "PT5M" }
        embeddingModel = {
          kind = "azureOpenAI"
          azureOpenAIParameters = {
            resourceUri  = local.openai_endpoint
            deploymentId = azapi_resource.embedding_model.name
            modelName    = var.embedding_model.name
          }
        }
      }
    }
  }

  lifecycle {
    # Search redacts this ID on GET; changes are tracked by the workspace trigger.
    ignore_changes = [body.indexedOneLakeParameters.fabricWorkspaceId]
    # Any planned Search change rebuilds the source; serverless changes are always replacements.
    replace_triggered_by = [
      azapi_resource.search,
      fabric_workspace.dev.id,
      azapi_resource.embedding_model.body,
    ]
  }

  depends_on = [
    fabric_workspace_role_assignment.search_onelake_contributor,
    azurerm_role_assignment.search_foundry_user,
  ]
}

resource "restful_resource" "knowledge_base" {
  count = var.knowledge_lakehouse_id == null ? 0 : 1

  path            = "/knowledgebases/${local.knowledge_base_name}"
  check_existance = true

  body = {
    name                  = local.knowledge_base_name
    description           = "Foundry IQ knowledge base for ${var.project_name}."
    outputMode            = "answerSynthesis"
    answerInstructions    = "Answer using retrieved source results and include citations. If the sources do not contain the answer, say you do not know. Never report unavailable or unauthorized sources as successfully queried."
    retrievalInstructions = "Use connected Fabric ontology sources for current entities, relationships, statuses and counts. Use indexed OneLake files for documentation and background. Bootstrap documents are not live table data; do not substitute them for a failed ontology query."
    knowledgeSources = concat(
      [{ name = restful_resource.knowledge_source[count.index].body.name }],
      [for source in restful_resource.ontology_source : { name = source.body.name }],
    )
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
    replace_triggered_by = [restful_resource.knowledge_source[count.index]]
  }
}

resource "restful_resource" "ontology_source" {
  for_each = var.knowledge_ontologies

  path            = "/knowledgesources/${var.project_name}-${each.key}-ontology"
  check_existance = true
  force_new_attrs = ["name", "kind"]

  body = {
    name        = "${var.project_name}-${each.key}-ontology"
    kind        = "fabricOntology"
    description = each.value.description
    fabricOntologyParameters = {
      workspaceId = fabric_workspace.dev.id
      ontologyId  = data.fabric_ontology.knowledge[each.key].id
    }
  }

  lifecycle {
    replace_triggered_by = [azapi_resource.search]
  }
}
