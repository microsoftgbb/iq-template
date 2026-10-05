resource "azurerm_search_service" "knowledge" {
  name                = "srch-${local.resource_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.search_sku
  replica_count       = 1
  partition_count     = 1
  semantic_search_sku = "standard"
  tags                = local.tags

  # Terraform uses an admin key; Foundry queries use the project managed identity.
  local_authentication_enabled = true
  authentication_failure_mode  = "http401WithBearerChallenge"

  identity {
    type = "SystemAssigned"
  }
}

resource "fabric_lakehouse" "knowledge" {
  display_name = "${replace(var.project_name, "-", "_")}_knowledge"
  description  = "OneLake files indexed by the Foundry IQ knowledge base."
  workspace_id = fabric_workspace.dev.id

  # Finish initial Git synchronization before adding the Terraform-owned item.
  depends_on = [fabric_workspace_git.dev]
}

# The OneLake indexer currently requires workspace Contributor, not Viewer.
resource "fabric_workspace_role_assignment" "search_onelake_contributor" {
  workspace_id = fabric_workspace.dev.id
  role         = "Contributor"
  principal = {
    id   = azurerm_search_service.knowledge.identity[0].principal_id
    type = "ServicePrincipal"
  }
}

# Fabric's provider has no file resource; bootstrap with the existing Azure CLI.
resource "terraform_data" "onelake_seed" {
  triggers_replace = {
    workspace_id = fabric_workspace.dev.id
    lakehouse_id = fabric_lakehouse.knowledge.id
    content_hash = filesha256("${path.module}/seed/starter.txt")
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    quiet       = true
    command     = <<-EOT
      set -eu
      az storage fs file upload \
        --account-name onelake \
        --blob-endpoint https://onelake.dfs.fabric.microsoft.com \
        --file-system "$WORKSPACE_ID" \
        --path "$LAKEHOUSE_ID/Files/starter.txt" \
        --source "$SOURCE_FILE" \
        --auth-mode login \
        --overwrite true \
        --content-type text/plain \
        --only-show-errors \
        --output none
    EOT
    environment = {
      WORKSPACE_ID                    = fabric_workspace.dev.id
      LAKEHOUSE_ID                    = fabric_lakehouse.knowledge.id
      SOURCE_FILE                     = abspath("${path.module}/seed/starter.txt")
      AZURE_STORAGE_CONNECTION_STRING = ""
      AZURE_STORAGE_KEY               = ""
      AZURE_STORAGE_SAS_TOKEN         = ""
    }
  }
}

resource "azurerm_role_assignment" "search_foundry_user" {
  scope                            = azapi_resource.foundry.id
  role_definition_name             = "Cognitive Services User"
  principal_id                     = azurerm_search_service.knowledge.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "project_search_reader" {
  scope                            = azurerm_search_service.knowledge.id
  role_definition_name             = "Search Index Data Reader"
  principal_id                     = azapi_resource.foundry_project.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "deployer_search_reader" {
  scope                = azurerm_search_service.knowledge.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "restful_resource" "knowledge_source" {
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
      lakehouseId       = fabric_lakehouse.knowledge.id
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
    replace_triggered_by = [
      azurerm_search_service.knowledge.id,
      fabric_workspace.dev.id,
      fabric_lakehouse.knowledge.id,
      azapi_resource.embedding_model.body,
    ]
  }

  depends_on = [
    terraform_data.onelake_seed,
    fabric_workspace_role_assignment.search_onelake_contributor,
    azurerm_role_assignment.search_foundry_user,
  ]
}

resource "restful_resource" "knowledge_base" {
  path            = "/knowledgebases/${local.knowledge_base_name}"
  check_existance = true

  body = {
    name               = local.knowledge_base_name
    description        = "Foundry IQ knowledge base for ${var.project_name}."
    outputMode         = "answerSynthesis"
    answerInstructions = "Answer using the retrieved documents and include citations. If the sources do not contain the answer, say you do not know."
    knowledgeSources   = [{ name = restful_resource.knowledge_source.body.name }]
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
    replace_triggered_by = [restful_resource.knowledge_source]
  }
}
